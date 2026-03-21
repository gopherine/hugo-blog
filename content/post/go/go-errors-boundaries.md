---
title: "Lesson 6: Error Boundaries Across Layers — Translate at the border, don't leak internals"
author: Atharva Pandey
series: "Go Error Design at Scale"
lesson: 6
keywords:
  - Go
  - golang
  - error handling
  - error boundaries
  - error translation
  - HTTP errors
  - gRPC errors
  - layer architecture
tags:
  - Go tutorial
  - golang
date: '2025-04-19T00:00:00.000Z'
---

One of the most subtle security issues I've encountered in Go APIs isn't an authentication bug or a missing authorization check — it's a SQL error message showing up in a JSON response. Something like `{"error": "pq: duplicate key value violates unique constraint \"users_email_key\""}`. The frontend is now showing your users your database schema. Not great.

This happens because somewhere between the repository and the HTTP response, someone got lazy with error translation. The raw database error bubbled all the way up and got written directly into the response. Error boundaries exist to prevent exactly this — they're the translation points between layers, where internal implementation details get converted to appropriate external representations.

## The Problem

The naive approach: pass errors through every layer unchanged, format them directly into the response at the handler.

```go
// WRONG — raw internal errors leak to the client
func (r *UserRepo) Create(ctx context.Context, u *User) error {
    _, err := r.db.ExecContext(ctx,
        `INSERT INTO users (email, password_hash) VALUES ($1, $2)`,
        u.Email, u.PasswordHash,
    )
    return err // returns raw postgres error: "pq: duplicate key..."
}

func (s *UserService) Register(ctx context.Context, req RegisterRequest) error {
    user := &User{Email: req.Email, PasswordHash: hash(req.Password)}
    return s.repo.Create(ctx, user) // passes through unchanged
}

func (h *Handler) Register(w http.ResponseWriter, r *http.Request) {
    var req RegisterRequest
    json.NewDecoder(r.Body).Decode(&req)

    if err := h.svc.Register(r.Context(), req); err != nil {
        // this writes the postgres error directly to the client
        http.Error(w, err.Error(), http.StatusInternalServerError)
        // client sees: "pq: duplicate key value violates unique constraint \"users_email_key\""
        return
    }
    w.WriteHeader(http.StatusCreated)
}
```

Beyond the security concern — leaking schema details — this also couples your clients to your database driver. If you switch from `lib/pq` to `pgx`, your error messages change, and anything parsing them breaks.

The second version of this mistake is translating in the wrong place — in the service or repository instead of at the actual transport boundary:

```go
// WRONG — translation in the service couples business logic to HTTP
func (s *UserService) Register(ctx context.Context, req RegisterRequest) (int, error) {
    // service now returns HTTP status codes — it knows about HTTP!
    if err := s.repo.Create(ctx, toUser(req)); err != nil {
        return http.StatusConflict, err // http package in business logic
    }
    return http.StatusCreated, nil
}
```

Now you can't reuse this service from a gRPC endpoint or a CLI tool without it still producing HTTP status codes.

## The Idiomatic Way

The pattern I use: each layer boundary has a **translate function** that converts the outgoing layer's error vocabulary to the incoming layer's error vocabulary. Repository errors become domain errors. Domain errors become transport errors. Nothing leaks through.

```go
// RIGHT — repository translates DB errors to domain errors
func (r *UserRepo) Create(ctx context.Context, u *User) error {
    _, err := r.db.ExecContext(ctx,
        `INSERT INTO users (email, password_hash) VALUES ($1, $2)`,
        u.Email, u.PasswordHash,
    )
    if err != nil {
        return translateDBError(err, "create user")
    }
    return nil
}

func translateDBError(err error, op string) error {
    if err == nil {
        return nil
    }
    // postgres-specific: check for constraint violations
    var pgErr *pgconn.PgError
    if errors.As(err, &pgErr) {
        switch pgErr.Code {
        case "23505": // unique_violation
            return &AppError{
                Kind:    KindConflict,
                Message: "resource already exists",
                Detail:  fmt.Sprintf("%s: unique constraint: %s", op, pgErr.ConstraintName),
            }
        case "23503": // foreign_key_violation
            return &AppError{
                Kind:    KindValidation,
                Message: "referenced resource does not exist",
                Detail:  fmt.Sprintf("%s: foreign key: %s", op, pgErr.ConstraintName),
            }
        case "57014": // query_canceled (context canceled)
            return fmt.Errorf("%s: %w", op, context.Canceled)
        }
    }
    if errors.Is(err, sql.ErrNoRows) {
        return &AppError{Kind: KindNotFound, Message: "not found", Detail: op}
    }
    // unknown DB error — wrap as internal, don't expose details
    return &AppError{Kind: KindInternal, Detail: fmt.Sprintf("%s: db error", op), Err: err}
}
```

The service passes domain errors through — it doesn't know or care about the transport:

```go
// Service — pure domain logic, no transport knowledge
func (s *UserService) Register(ctx context.Context, req RegisterRequest) (*User, error) {
    if err := validateRegisterRequest(req); err != nil {
        return nil, err // already an AppError{Kind: KindValidation}
    }

    user := &User{
        Email:        req.Email,
        PasswordHash: bcrypt.Hash(req.Password),
    }

    if err := s.repo.Create(ctx, user); err != nil {
        return nil, err // already translated by repo
    }

    return user, nil
}
```

The handler translates domain errors to HTTP:

```go
// Handler — translates domain errors to HTTP responses
func (h *Handler) Register(w http.ResponseWriter, r *http.Request) {
    var req RegisterRequest
    if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
        writeError(w, &AppError{Kind: KindValidation, Message: "invalid request body"})
        return
    }

    user, err := h.svc.Register(r.Context(), req)
    if err != nil {
        writeError(w, err)
        return
    }

    w.WriteHeader(http.StatusCreated)
    json.NewEncoder(w).Encode(user)
}

func writeError(w http.ResponseWriter, err error) {
    var ae *AppError
    if !errors.As(err, &ae) {
        w.WriteHeader(http.StatusInternalServerError)
        json.NewEncoder(w).Encode(map[string]string{"error": "internal server error"})
        return
    }

    w.Header().Set("Content-Type", "application/json")
    w.WriteHeader(kindToHTTPStatus(ae.Kind))
    // only expose ae.Message to the client — never ae.Detail or ae.Err
    json.NewEncoder(w).Encode(map[string]string{
        "error": ae.Message,
        "code":  ae.Code,
    })
}
```

## In The Wild

The same pattern works for gRPC — just swap the mapping function:

```go
// gRPC server — translates domain errors to gRPC status codes
import "google.golang.org/grpc/codes"
import "google.golang.org/grpc/status"

func domainErrToGRPC(err error) error {
    if err == nil {
        return nil
    }
    var ae *AppError
    if !errors.As(err, &ae) {
        return status.Error(codes.Internal, "internal error")
    }
    switch ae.Kind {
    case KindNotFound:
        return status.Error(codes.NotFound, ae.Message)
    case KindUnauthorized:
        return status.Error(codes.PermissionDenied, ae.Message)
    case KindValidation:
        return status.Error(codes.InvalidArgument, ae.Message)
    case KindConflict:
        return status.Error(codes.AlreadyExists, ae.Message)
    case KindTransient:
        return status.Error(codes.Unavailable, ae.Message)
    default:
        return status.Error(codes.Internal, "internal error")
    }
}

func (s *UserGRPCServer) Register(ctx context.Context, req *pb.RegisterRequest) (*pb.RegisterResponse, error) {
    user, err := s.svc.Register(ctx, fromProtoRegister(req))
    if err != nil {
        return nil, domainErrToGRPC(err) // translate at the gRPC boundary
    }
    return toProtoUser(user), nil
}
```

The service code is identical whether it's called from HTTP or gRPC — it only knows about domain errors.

## The Gotchas

**Every external dependency needs its own translation function.** Postgres errors, Redis errors, Stripe API errors, S3 errors — each has its own error vocabulary. Write a `translateXErr` function per client and call it at the client wrapper boundary, not in the service.

**Don't let `*sql.DB` or driver types appear anywhere above the repository layer.** If your service imports `database/sql` or `lib/pq`, you've broken the boundary. Domain code shouldn't know the data is stored in postgres.

**Translate in the right direction only.** The repository translates up (DB → domain). The handler translates up (domain → HTTP). There's no translation going down — inputs are validated separately before they become domain objects.

**Don't be clever with error wrapping at boundaries.** When you translate, create a new error — don't wrap the old one with `%w` and attach it to a new type. If the internal cause needs to be preserved for logging, store it as `Err` on your `AppError` struct, but don't let it be accessible via `errors.As` to the transport layer. The whole point of a boundary is that what's inside stays inside.

```go
// WRONG — wrapping internal error lets callers see DB details via errors.As
return &AppError{Kind: KindConflict, Err: pgErr} // pgErr accessible via errors.As

// RIGHT — log the detail, don't propagate the original
logger.Debug("constraint violation", "constraint", pgErr.ConstraintName)
return &AppError{Kind: KindConflict, Message: "already exists", Detail: pgErr.ConstraintName}
// Detail is for logging only — not returned in responses
```

## Key Takeaway

Each layer boundary is a translation point. The repository converts driver errors to domain errors. The handler converts domain errors to transport codes. Nothing internal escapes to the outside. Write a dedicated translate function per external dependency. Keep transport-specific concepts — HTTP codes, gRPC status codes — out of your service layer completely. The service layer should be reusable across transports; if it imports `net/http`, you've already violated the boundary.

---

Previous: [Lesson 5: Logging vs Returning](go-errors-logging-vs-returning.md) | Next: [Lesson 7: Panic, Recover, and When They're Actually Justified — Panic is not error handling](go-errors-panic-recover.md)
