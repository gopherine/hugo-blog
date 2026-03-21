---
title: "Lesson 1: Sentinel vs Typed Errors — Know your error before you handle it"
author: Atharva Pandey
series: "Go Error Design at Scale"
lesson: 1
keywords:
  - Go
  - golang
  - error handling
  - sentinel errors
  - typed errors
  - errors.New
  - custom error types
tags:
  - Go tutorial
  - golang
date: '2024-12-14T00:00:00.000Z'
---

When I first started writing Go seriously, I treated errors like fire: try to avoid them, and when you can't, put them out as fast as possible with an `if err != nil { return err }`. It took a few production incidents — and a lot of reading other people's codebases — before I understood that errors aren't just signals. They're data. And how you design that data determines how well your callers can respond to failures.

This lesson is about the two fundamental error shapes in Go: sentinel errors and typed errors. You need both. The trick is knowing when each one belongs.

## The Problem

The naive approach is to just return `errors.New("not found")` everywhere and move on. The calling code then tries to figure out what went wrong by string-matching the message — which is fragile, untestable, and breaks the moment someone refactors a string.

```go
// WRONG — stringly typed error handling
func GetUser(id string) (*User, error) {
    u, err := db.QueryUser(id)
    if err != nil {
        return nil, errors.New("user not found")
    }
    return u, nil
}

// Caller does this:
func HandleGetUser(w http.ResponseWriter, r *http.Request) {
    user, err := GetUser(r.URL.Query().Get("id"))
    if err != nil {
        if err.Error() == "user not found" { // fragile string comparison
            http.Error(w, "404", http.StatusNotFound)
            return
        }
        http.Error(w, "500", http.StatusInternalServerError)
    }
}
```

This is a maintenance trap. There's no contract between the producer and consumer of that error. If someone changes the string, the handler silently breaks — no compiler warning, no test failure unless you happened to write an exact-string test.

The other common mistake is the opposite extreme: wrapping everything in a giant custom struct even when you just need a simple signal.

```go
// WRONG — over-engineered error type for a simple sentinel case
type UserNotFoundError struct {
    Message   string
    Timestamp time.Time
    RequestID string
}

func (e *UserNotFoundError) Error() string {
    return fmt.Sprintf("[%s] %s (req: %s)", e.Timestamp, e.Message, e.RequestID)
}

// Now every piece of code that checks "is it a not-found?" has to do a type
// assertion AND maintain awareness of this internal struct. Callers are coupled
// to your implementation details.
```

## The Idiomatic Way

Go gives you two clean tools: **sentinel errors** for identity-based matching, and **typed errors** for structured data extraction. Each solves a different problem.

**Sentinel errors** are package-level `var` declarations. They're comparable with `==` and (properly) with `errors.Is`. Use them when your caller only needs to ask "did this specific thing happen?" — not "give me details about what happened."

```go
// RIGHT — sentinel error for a simple, stable signal
var (
    ErrNotFound   = errors.New("not found")
    ErrUnauthorized = errors.New("unauthorized")
)

func GetUser(id string) (*User, error) {
    u, ok := cache[id]
    if !ok {
        return nil, ErrNotFound // return the sentinel directly
    }
    return u, nil
}

// Caller uses errors.Is — safe through wrapping chains
func HandleGetUser(w http.ResponseWriter, r *http.Request) {
    user, err := GetUser(r.URL.Query().Get("id"))
    if err != nil {
        if errors.Is(err, ErrNotFound) {
            http.Error(w, "user not found", http.StatusNotFound)
            return
        }
        http.Error(w, "internal error", http.StatusInternalServerError)
        return
    }
    json.NewEncoder(w).Encode(user)
}
```

**Typed errors** are custom structs or types that implement the `error` interface. Use them when your caller needs to extract structured information from the error — not just ask "what kind of error?" but "what were the details?"

```go
// RIGHT — typed error when callers need structured data
type ValidationError struct {
    Field   string
    Message string
}

func (e *ValidationError) Error() string {
    return fmt.Sprintf("validation failed on %q: %s", e.Field, e.Message)
}

func CreateUser(req CreateUserRequest) error {
    if req.Email == "" {
        return &ValidationError{Field: "email", Message: "is required"}
    }
    if !strings.Contains(req.Email, "@") {
        return &ValidationError{Field: "email", Message: "must be a valid address"}
    }
    return nil
}

// Caller uses errors.As to extract the typed value
func HandleCreateUser(w http.ResponseWriter, r *http.Request) {
    var req CreateUserRequest
    json.NewDecoder(r.Body).Decode(&req)

    if err := CreateUser(req); err != nil {
        var ve *ValidationError
        if errors.As(err, &ve) {
            // we have the field name and message — can return structured JSON
            w.WriteHeader(http.StatusBadRequest)
            json.NewEncoder(w).Encode(map[string]string{
                "field":   ve.Field,
                "message": ve.Message,
            })
            return
        }
        http.Error(w, "internal error", http.StatusInternalServerError)
        return
    }
    w.WriteHeader(http.StatusCreated)
}
```

## In The Wild

The standard library uses both patterns deliberately. `database/sql` exports `sql.ErrNoRows` as a sentinel — callers just need to know "there was no row," no additional detail is meaningful. The standard `os` package exports `*PathError` as a typed error — callers might need to know which path failed and what the underlying OS error was.

```go
// sql.ErrNoRows — classic sentinel usage
func GetOrderByID(ctx context.Context, db *sql.DB, id string) (*Order, error) {
    var o Order
    err := db.QueryRowContext(ctx, `SELECT id, amount FROM orders WHERE id = $1`, id).
        Scan(&o.ID, &o.Amount)
    if err != nil {
        if errors.Is(err, sql.ErrNoRows) {
            return nil, ErrNotFound // translate to your domain sentinel
        }
        return nil, fmt.Errorf("query order %s: %w", id, err)
    }
    return &o, nil
}

// os.PathError — typed error, callers can extract Op and Path
func ReadConfig(path string) ([]byte, error) {
    data, err := os.ReadFile(path)
    if err != nil {
        var pe *os.PathError
        if errors.As(err, &pe) {
            return nil, fmt.Errorf("config file missing at %s: %w", pe.Path, err)
        }
        return nil, fmt.Errorf("read config: %w", err)
    }
    return data, nil
}
```

Notice the translation happening in `GetOrderByID`. The database returns `sql.ErrNoRows` — a stdlib concern. The function converts it to `ErrNotFound` — a domain concern. That's intentional, and we'll dig deeper into that pattern in Lesson 6.

## The Gotchas

**Don't export typed errors as concrete types if you can avoid it.** Once callers import and type-assert against your `*MyError`, you're locked into that struct's fields forever. If you must, keep the struct stable and lean.

**Sentinel errors aren't safe to compare with `==` after wrapping.** This trips up a lot of people. If someone wraps your sentinel with `fmt.Errorf("some context: %w", ErrNotFound)`, the resulting error is not `== ErrNotFound` anymore. But it is matched by `errors.Is`. Always use `errors.Is` for sentinel matching — never raw `==`.

```go
// WRONG — breaks when errors are wrapped
wrapped := fmt.Errorf("db layer: %w", ErrNotFound)
if wrapped == ErrNotFound { // false! wrapping breaks ==
    fmt.Println("not found")
}

// RIGHT — works through the wrapping chain
if errors.Is(wrapped, ErrNotFound) { // true
    fmt.Println("not found")
}
```

**Typed errors should be returned as pointer receivers.** If your `ValidationError` has methods on `*ValidationError`, returning a `ValidationError` value (not a pointer) means `errors.As` won't match a `*ValidationError` target. Always return `&ValidationError{...}`, not `ValidationError{...}`.

**Don't create a typed error just to carry a message.** If the only method on your type is `Error() string` and you're not extracting any fields, a sentinel is simpler and just as useful.

## Key Takeaway

Use **sentinel errors** when your caller needs to ask "did this specific thing happen?" — identity only, no data. Use **typed errors** when your caller needs to ask "what were the details of this failure?" — data extraction, structured responses. Use `errors.Is` for sentinels and `errors.As` for typed errors. Never string-match error messages. And always translate library/driver errors to your domain's error vocabulary at the layer boundary.

---

Next: [Lesson 2: errors.Is and errors.As — Matching errors through the wrapping chain](go-errors-is-as.md)
