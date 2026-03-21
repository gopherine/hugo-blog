---
title: "Lesson 8: Production Error Architecture — Designing the error system for a real service"
author: Atharva Pandey
series: "Go Error Design at Scale"
lesson: 8
keywords:
  - Go
  - golang
  - error handling
  - production architecture
  - AppError
  - error hierarchy
  - error middleware
  - structured error logging
  - error codes
tags:
  - Go tutorial
  - golang
date: '2025-06-14T00:00:00.000Z'
---

This is the lesson where everything comes together. Over the previous seven lessons we've looked at sentinels and typed errors, wrapping strategy, error classification, where to log, how to translate at layer boundaries, and when panic is actually defensible. Now I want to show you what all of that looks like assembled into a complete, production-ready error system for a real API service.

I'm going to build the full error architecture for a hypothetical orders API. By the end you'll have a template you can adapt directly — the error type hierarchy, the middleware, the structured logging, and the client-facing error codes. This is the code I wish I'd had when I started building services in Go.

## The Problem

A service without a designed error system looks like this: scattered `errors.New` calls with different string conventions, inconsistent HTTP status codes, raw database errors in API responses sometimes, duplicate log lines everywhere, and a `500 Internal Server Error` for user mistakes because nobody translated the error before returning it.

```go
// WRONG — no coherent error system
func (h *Handler) CreateOrder(w http.ResponseWriter, r *http.Request) {
    var req struct {
        UserID string  `json:"user_id"`
        Amount float64 `json:"amount"`
    }
    if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
        log.Printf("decode error: %v", err) // log 1
        http.Error(w, err.Error(), 400)     // leaks parse details
        return
    }
    if req.Amount <= 0 {
        http.Error(w, "bad amount", 400) // no structure
        return
    }
    order, err := h.svc.CreateOrder(r.Context(), req.UserID, req.Amount)
    if err != nil {
        log.Printf("create order error: %v", err) // log 2 (maybe also logged in service)
        if strings.Contains(err.Error(), "not found") { // string matching
            http.Error(w, "user not found", 404)
            return
        }
        http.Error(w, "internal error", 500)
        return
    }
    json.NewEncoder(w).Encode(order)
}
```

Every handler looks slightly different. There's no contract. Clients can't reliably parse error responses. Operators can't count error rates by kind.

## The Idiomatic Way

Here's the full error type hierarchy I use. Everything builds on a single `AppError` struct:

```go
// errors/errors.go — the complete error system

package apperrors

import (
    "errors"
    "fmt"
    "net/http"
)

// ErrorKind classifies the error for routing decisions at boundaries
type ErrorKind string

const (
    KindValidation   ErrorKind = "VALIDATION_ERROR"
    KindNotFound     ErrorKind = "NOT_FOUND"
    KindUnauthorized ErrorKind = "UNAUTHORIZED"
    KindForbidden    ErrorKind = "FORBIDDEN"
    KindConflict     ErrorKind = "CONFLICT"
    KindTransient    ErrorKind = "TRANSIENT"
    KindInternal     ErrorKind = "INTERNAL"
)

// AppError is the single error type used throughout the service
type AppError struct {
    Kind    ErrorKind // used for routing at boundaries
    Code    string    // machine-readable code for clients ("ORDER_AMOUNT_INVALID")
    Message string    // human-readable, safe to return to clients
    Detail  string    // internal detail for logging only — never sent to clients
    Err     error     // underlying cause — for logging and chain walking
}

func (e *AppError) Error() string {
    if e.Detail != "" && e.Err != nil {
        return fmt.Sprintf("%s: %v", e.Detail, e.Err)
    }
    if e.Detail != "" {
        return e.Detail
    }
    return e.Message
}

func (e *AppError) Unwrap() error { return e.Err }

// HTTPStatus maps the error kind to an HTTP status code
func (e *AppError) HTTPStatus() int {
    switch e.Kind {
    case KindValidation:
        return http.StatusBadRequest
    case KindNotFound:
        return http.StatusNotFound
    case KindUnauthorized:
        return http.StatusUnauthorized
    case KindForbidden:
        return http.StatusForbidden
    case KindConflict:
        return http.StatusConflict
    case KindTransient:
        return http.StatusServiceUnavailable
    default:
        return http.StatusInternalServerError
    }
}

// IsUserFacing reports whether this error is safe to describe to the client
func (e *AppError) IsUserFacing() bool {
    switch e.Kind {
    case KindValidation, KindNotFound, KindUnauthorized, KindForbidden, KindConflict:
        return true
    default:
        return false
    }
}

// Constructors — one per kind, self-documenting at the call site
func NotFound(code, message, detail string) *AppError {
    return &AppError{Kind: KindNotFound, Code: code, Message: message, Detail: detail}
}

func Validation(code, message, detail string) *AppError {
    return &AppError{Kind: KindValidation, Code: code, Message: message, Detail: detail}
}

func Conflict(code, message, detail string) *AppError {
    return &AppError{Kind: KindConflict, Code: code, Message: message, Detail: detail}
}

func Transient(detail string, cause error) *AppError {
    return &AppError{
        Kind:    KindTransient,
        Code:    "SERVICE_UNAVAILABLE",
        Message: "service temporarily unavailable, please try again",
        Detail:  detail,
        Err:     cause,
    }
}

func Internal(detail string, cause error) *AppError {
    return &AppError{
        Kind:    KindInternal,
        Code:    "INTERNAL_ERROR",
        Message: "an unexpected error occurred",
        Detail:  detail,
        Err:     cause,
    }
}

// Sentinel errors — package-level for fast identity checks
var (
    ErrOrderNotFound = NotFound("ORDER_NOT_FOUND", "order not found", "order not found")
    ErrUserNotFound  = NotFound("USER_NOT_FOUND", "user not found", "user not found")
)
```

Now the repository, service, and handler layers:

```go
// repository/orders.go

func (r *OrderRepo) GetByID(ctx context.Context, id string) (*Order, error) {
    var o Order
    err := r.db.QueryRowContext(ctx,
        `SELECT id, user_id, amount, status FROM orders WHERE id=$1`, id,
    ).Scan(&o.ID, &o.UserID, &o.Amount, &o.Status)

    if errors.Is(err, sql.ErrNoRows) {
        return nil, apperrors.ErrOrderNotFound
    }
    if err != nil {
        return nil, translateDBError(err, fmt.Sprintf("get order %s", id))
    }
    return &o, nil
}

func (r *OrderRepo) Create(ctx context.Context, o *Order) error {
    _, err := r.db.ExecContext(ctx,
        `INSERT INTO orders (id, user_id, amount, status) VALUES ($1, $2, $3, $4)`,
        o.ID, o.UserID, o.Amount, o.Status,
    )
    if err != nil {
        return translateDBError(err, fmt.Sprintf("create order %s", o.ID))
    }
    return nil
}

func translateDBError(err error, op string) error {
    var pgErr *pgconn.PgError
    if errors.As(err, &pgErr) {
        switch pgErr.Code {
        case "23505":
            return apperrors.Conflict("DUPLICATE_ORDER",
                "this order already exists",
                fmt.Sprintf("%s: constraint %s", op, pgErr.ConstraintName))
        }
    }
    if isConnectionError(err) {
        return apperrors.Transient(op, err)
    }
    return apperrors.Internal(op, err)
}
```

```go
// service/orders.go

type CreateOrderRequest struct {
    UserID string
    Amount float64
    Items  []OrderItem
}

func (s *OrderService) CreateOrder(ctx context.Context, req CreateOrderRequest) (*Order, error) {
    // domain validation — returns Validation errors
    if err := validateCreateOrder(req); err != nil {
        return nil, err
    }

    // user must exist
    user, err := s.users.GetByID(ctx, req.UserID)
    if err != nil {
        return nil, err // already classified by user repo
    }

    if !user.CanPlaceOrders {
        return nil, apperrors.Forbidden("ACCOUNT_SUSPENDED",
            "your account is not allowed to place orders",
            fmt.Sprintf("user %s account suspended", req.UserID))
    }

    order := &Order{
        ID:     newOrderID(),
        UserID: req.UserID,
        Amount: req.Amount,
        Status: "pending",
    }

    if err := s.orders.Create(ctx, order); err != nil {
        return nil, err
    }

    return order, nil
}

func validateCreateOrder(req CreateOrderRequest) error {
    if req.UserID == "" {
        return apperrors.Validation("MISSING_USER_ID", "user_id is required",
            "create order: empty user_id")
    }
    if req.Amount <= 0 {
        return apperrors.Validation("INVALID_AMOUNT",
            fmt.Sprintf("amount must be positive, got %.2f", req.Amount),
            fmt.Sprintf("create order: invalid amount %.2f", req.Amount))
    }
    if len(req.Items) == 0 {
        return apperrors.Validation("NO_ITEMS", "order must contain at least one item",
            "create order: empty items")
    }
    return nil
}
```

## In The Wild

Here's the middleware and handler that tie everything together. This is the code that runs on every request:

```go
// middleware/error.go

type ErrorResponse struct {
    Error   string `json:"error"`             // human-readable
    Code    string `json:"code"`              // machine-readable, for client logic
    TraceID string `json:"trace_id,omitempty"` // correlate with logs
}

// ErrorMiddleware wraps handlers with panic recovery and response normalization
func ErrorMiddleware(logger *slog.Logger) func(http.Handler) http.Handler {
    return func(next http.Handler) http.Handler {
        return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
            defer func() {
                if rec := recover(); rec != nil {
                    buf := make([]byte, 4096)
                    n := runtime.Stack(buf, false)
                    logger.Error("panic in handler",
                        "panic", fmt.Sprintf("%v", rec),
                        "stack", string(buf[:n]),
                        "path", r.URL.Path,
                        "trace_id", traceID(r.Context()),
                    )
                    writeErrorResponse(w, r, apperrors.Internal("panic", nil), logger)
                }
            }()
            next.ServeHTTP(w, r)
        })
    }
}

func writeErrorResponse(w http.ResponseWriter, r *http.Request, err error, logger *slog.Logger) {
    tid := traceID(r.Context())

    var ae *apperrors.AppError
    if !errors.As(err, &ae) {
        // unclassified — always log
        logger.Error("unclassified error",
            "trace_id", tid,
            "error", err.Error(),
            "path", r.URL.Path,
        )
        w.Header().Set("Content-Type", "application/json")
        w.WriteHeader(http.StatusInternalServerError)
        json.NewEncoder(w).Encode(ErrorResponse{
            Error:   "an unexpected error occurred",
            Code:    "INTERNAL_ERROR",
            TraceID: tid,
        })
        return
    }

    // Log internal and transient errors — these are operational problems
    if !ae.IsUserFacing() {
        logger.Error("service error",
            "trace_id", tid,
            "kind", ae.Kind,
            "code", ae.Code,
            "detail", ae.Detail,
            "cause", fmt.Sprintf("%v", ae.Err),
            "path", r.URL.Path,
            "method", r.Method,
        )
    }

    w.Header().Set("Content-Type", "application/json")
    w.WriteHeader(ae.HTTPStatus())
    json.NewEncoder(w).Encode(ErrorResponse{
        Error:   ae.Message,
        Code:    ae.Code,
        TraceID: tid,
    })
}

// handler/orders.go

func (h *OrderHandler) CreateOrder(w http.ResponseWriter, r *http.Request) {
    var req struct {
        UserID string       `json:"user_id"`
        Amount float64      `json:"amount"`
        Items  []OrderItem  `json:"items"`
    }
    if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
        writeErrorResponse(w, r, apperrors.Validation("INVALID_JSON",
            "request body is not valid JSON", fmt.Sprintf("decode: %v", err)), h.logger)
        return
    }

    order, err := h.svc.CreateOrder(r.Context(), service.CreateOrderRequest{
        UserID: req.UserID,
        Amount: req.Amount,
        Items:  req.Items,
    })
    if err != nil {
        writeErrorResponse(w, r, err, h.logger)
        return
    }

    w.Header().Set("Content-Type", "application/json")
    w.WriteHeader(http.StatusCreated)
    json.NewEncoder(w).Encode(order)
}
```

## The Gotchas

**Keep error codes stable across releases.** Once a client is checking `if err.code === "ORDER_NOT_FOUND"`, changing that string is a breaking API change. Treat error codes like versioned API contract items — document them, don't change them without a migration path.

**Don't make the error type hierarchy too deep.** One `AppError` with a `Kind` field and a `Code` string is sufficient for most services. Resist the urge to create `ValidationError`, `NotFoundError`, `ConflictError` as separate types. The kind discrimination is already there in the `Kind` field — multiple types just mean multiple `errors.As` chains to maintain.

**Test the error mapping explicitly.** Write a table test that verifies each error kind maps to the expected HTTP status, response body, and log behavior. This is the contract your clients and operators depend on.

```go
func TestErrorMapping(t *testing.T) {
    tests := []struct {
        err        error
        wantStatus int
        wantCode   string
        wantLogged bool
    }{
        {
            err:        apperrors.Validation("INVALID_AMOUNT", "amount must be positive", ""),
            wantStatus: http.StatusBadRequest,
            wantCode:   "INVALID_AMOUNT",
            wantLogged: false, // user error — don't log
        },
        {
            err:        apperrors.ErrOrderNotFound,
            wantStatus: http.StatusNotFound,
            wantCode:   "ORDER_NOT_FOUND",
            wantLogged: false,
        },
        {
            err:        apperrors.Internal("db timeout", io.ErrUnexpectedEOF),
            wantStatus: http.StatusInternalServerError,
            wantCode:   "INTERNAL_ERROR",
            wantLogged: true, // operator error — do log
        },
    }
    // ... run tests against writeErrorResponse
}
```

## Key Takeaway

A production error system has three parts that work together: a single error type with a `Kind` field for routing, a `Code` string for client consumption, and `Message`/`Detail` split between what's safe for clients and what's for your logs. Layer constructors create errors with the right kind. Repositories translate driver errors at their boundary. Services create domain errors for business rule violations. The middleware logs internal errors and maps all errors to structured HTTP responses. That's the complete pattern — and it's designed to stay coherent as your service grows from one handler to fifty.

---

Previous: [Lesson 7: Panic, Recover, and When They're Actually Justified](go-errors-panic-recover.md)

---

You've reached the end of the Go Error Design at Scale series. You now have the full picture: from the difference between sentinels and typed errors, through wrapping strategy, error classification, where to log, boundary translation, panic handling, and finally the complete production architecture. Take the patterns from Lesson 8 and adapt them to your service. The error system is worth getting right — it's the connective tissue of every layer in your codebase.
