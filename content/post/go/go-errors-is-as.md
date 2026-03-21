---
title: "Lesson 2: errors.Is and errors.As — Matching errors through the wrapping chain"
author: Atharva Pandey
series: "Go Error Design at Scale"
lesson: 2
keywords:
  - Go
  - golang
  - error handling
  - errors.Is
  - errors.As
  - error wrapping
  - fmt.Errorf
tags:
  - Go tutorial
  - golang
date: '2025-01-09T00:00:00.000Z'
---

Go 1.13 shipped what I consider the most important error-handling improvement the language has had: `errors.Is`, `errors.As`, and the `%w` verb. Before that, wrapping errors with context meant breaking the ability to inspect them. You'd wrap with `fmt.Errorf("context: %v", err)` and then the original error was gone — you could log it, but you couldn't match it. Callers would resort to string matching or just give up and let every error map to a 500.

After 1.13, you can have both: rich context in the message and reliable matching in the handling code. But the API trips people up in specific ways that matter in production. Let's go through all of it.

## The Problem

The most common mistake is using `==` to compare errors that travel through multiple layers, or using type assertions (`.(*MyError)`) instead of `errors.As`. Both break the moment any intermediate layer adds context.

```go
// WRONG — direct == comparison breaks through wrapping
var ErrNotFound = errors.New("not found")

func repo() error {
    return ErrNotFound
}

func service() error {
    return fmt.Errorf("service: get user: %w", repo()) // wraps with %w
}

func handler() {
    err := service()
    if err == ErrNotFound { // false — wrapping breaks == comparison
        fmt.Println("handle not found")
    }
}
```

```go
// WRONG — type assertion breaks through wrapping
type ValidationError struct{ Field string }
func (e *ValidationError) Error() string { return "invalid: " + e.Field }

func validate() error {
    return &ValidationError{Field: "email"}
}

func process() error {
    return fmt.Errorf("process: %w", validate()) // wraps
}

func handle() {
    err := process()
    ve, ok := err.(*ValidationError) // ok is false — type assertion doesn't unwrap
    if ok {
        fmt.Println("field:", ve.Field) // never reached
    }
}
```

Both examples fail silently. The error passes through, doesn't match anything, and gets treated as an unknown 500 error. In a production service this hides the actual failure mode and makes debugging much harder.

## The Idiomatic Way

`errors.Is` and `errors.As` both walk the wrapping chain automatically. They call `Unwrap()` on each error in sequence until they find a match or run out of errors.

```go
// RIGHT — errors.Is walks the chain for sentinel matching
var ErrNotFound = errors.New("not found")

func repo() error {
    return ErrNotFound
}

func service() error {
    return fmt.Errorf("service: get user: %w", repo())
}

func domain() error {
    return fmt.Errorf("domain: fetch profile: %w", service())
}

func handle() {
    err := domain()
    // errors.Is unwraps through all three layers and finds ErrNotFound
    if errors.Is(err, ErrNotFound) {
        fmt.Println("not found — return 404")
        return
    }
    fmt.Println("unexpected error:", err)
}
```

```go
// RIGHT — errors.As walks the chain for typed error extraction
type ValidationError struct {
    Field   string
    Message string
}
func (e *ValidationError) Error() string {
    return fmt.Sprintf("%s: %s", e.Field, e.Message)
}

func validate(email string) error {
    if !strings.Contains(email, "@") {
        return &ValidationError{Field: "email", Message: "invalid format"}
    }
    return nil
}

func register(email string) error {
    if err := validate(email); err != nil {
        return fmt.Errorf("register user: %w", err)
    }
    return nil
}

func handleRegister(w http.ResponseWriter, r *http.Request) {
    email := r.FormValue("email")
    err := register(email)
    if err != nil {
        var ve *ValidationError
        // errors.As unwraps through the chain and sets ve if found
        if errors.As(err, &ve) {
            http.Error(w, ve.Message, http.StatusBadRequest)
            return
        }
        http.Error(w, "internal error", http.StatusInternalServerError)
        return
    }
    w.WriteHeader(http.StatusCreated)
}
```

The key distinction: `errors.Is` checks **identity** (is this specific error value in the chain?), `errors.As` checks **type** (is there an error of this type anywhere in the chain?).

## In The Wild

Here's a pattern I use in real services — a layered call chain where each layer adds context via `%w`, and the HTTP handler uses both `Is` and `As` to route to the right response:

```go
var (
    ErrNotFound    = errors.New("not found")
    ErrUnauthorized = errors.New("unauthorized")
)

type DBConstraintError struct {
    Constraint string
}
func (e *DBConstraintError) Error() string {
    return fmt.Sprintf("constraint violation: %s", e.Constraint)
}

// Repository layer
func (r *UserRepo) GetByEmail(ctx context.Context, email string) (*User, error) {
    var u User
    err := r.db.QueryRowContext(ctx,
        `SELECT id, email FROM users WHERE email = $1`, email,
    ).Scan(&u.ID, &u.Email)
    if errors.Is(err, sql.ErrNoRows) {
        return nil, ErrNotFound
    }
    if err != nil {
        return nil, fmt.Errorf("user repo get by email: %w", err)
    }
    return &u, nil
}

// Service layer
func (s *UserService) Profile(ctx context.Context, email string) (*Profile, error) {
    user, err := s.repo.GetByEmail(ctx, email)
    if err != nil {
        return nil, fmt.Errorf("user service profile: %w", err)
    }
    return toProfile(user), nil
}

// Handler layer
func (h *Handler) GetProfile(w http.ResponseWriter, r *http.Request) {
    email := r.URL.Query().Get("email")
    profile, err := h.svc.Profile(r.Context(), email)
    if err != nil {
        switch {
        case errors.Is(err, ErrNotFound):
            http.Error(w, "user not found", http.StatusNotFound)
        case errors.Is(err, ErrUnauthorized):
            http.Error(w, "unauthorized", http.StatusUnauthorized)
        default:
            var dce *DBConstraintError
            if errors.As(err, &dce) {
                // shouldn't happen on a read, but if it does, 500 with logging
                log.Printf("unexpected constraint error: %s", dce.Constraint)
            }
            http.Error(w, "internal error", http.StatusInternalServerError)
        }
        return
    }
    json.NewEncoder(w).Encode(profile)
}
```

Each layer adds its name as a prefix — "user repo get by email", "user service profile" — so the full error string tells you exactly where the failure originated. But the matching still works because everything flows through `%w`.

## The Gotchas

**`errors.Is` uses `==` by default, not `.Error()` string comparison.** Two different `errors.New("same message")` calls produce different sentinel values. They won't match each other with `errors.Is`. Sentinels only match themselves.

```go
var ErrA = errors.New("not found")
var ErrB = errors.New("not found") // same message, different value

errors.Is(ErrA, ErrB) // false — different pointers
errors.Is(ErrA, ErrA) // true
```

**You can implement a custom `Is` method to control matching.** This is useful when you want two structurally different errors to compare equal — for example, matching by error code rather than pointer identity.

```go
type AppError struct {
    Code    int
    Message string
}
func (e *AppError) Error() string { return e.Message }

// Custom Is: match if codes are equal
func (e *AppError) Is(target error) bool {
    var t *AppError
    if errors.As(target, &t) {
        return e.Code == t.Code
    }
    return false
}

ErrNotFound := &AppError{Code: 404, Message: "not found"}
wrapped := fmt.Errorf("layer: %w", &AppError{Code: 404, Message: "resource missing"})

errors.Is(wrapped, ErrNotFound) // true — codes match via custom Is
```

**`errors.As` modifies its second argument.** The target must be a non-nil pointer to either a type that implements `error`, or to any interface type. A common mistake is passing the error directly instead of a pointer to the target type.

```go
var ve *ValidationError

// WRONG — passing ve directly (nil pointer)
errors.As(err, ve) // panics: target must be a non-nil pointer

// RIGHT — passing &ve (pointer to pointer)
errors.As(err, &ve)
```

**Wrapping with `%v` instead of `%w` breaks the chain entirely.** If any intermediate layer uses `%v`, the unwrap chain stops there. `errors.Is` and `errors.As` will not find anything below that layer.

```go
inner := ErrNotFound
wrapped := fmt.Errorf("context: %v", inner) // uses %v, not %w
errors.Is(wrapped, ErrNotFound)              // false — chain broken
```

## Key Takeaway

Always use `errors.Is` to match sentinel errors — never `==`. Always use `errors.As` to extract typed errors — never type assertions on the error directly. Wrap with `%w` at every layer to keep the chain intact. And remember that `%v` is for logging-only wrapping — it produces a readable message but breaks inspection. These aren't stylistic preferences; they're the difference between an error system that works and one that silently swallows failures.

---

Previous: [Lesson 1: Sentinel vs Typed Errors](go-errors-sentinel-vs-typed.md) | Next: [Lesson 3: Wrapping Strategy — Every wrap should add context, never noise](go-errors-wrapping-strategy.md)
