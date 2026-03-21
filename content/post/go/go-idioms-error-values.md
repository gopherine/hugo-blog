---
title: 'Lesson 23: Error Values, Not Exceptions — Errors you can actually inspect'
author: Atharva Pandey
keywords:
  - Go
  - golang
  - error handling
  - errors.Is
  - errors.As
  - error wrapping
  - sentinel errors
  - custom error types
  - panic
tags:
  - Go tutorial
  - golang
series: "Idiomatic Go"
lesson: 23
date: '2025-07-28T00:00:00.000Z'
---

In most languages, errors are events — they get thrown, they propagate up the call stack, and you catch them somewhere above. Go rejects this model entirely. In Go, an error is a value, just like an integer or a string. You pass it around, inspect it, wrap it with context, and check it right where it happens. Ignore it and your code doesn't crash loudly; it quietly does the wrong thing, and you'll find out at the worst possible moment.

## The Problem

The worst error handling in Go looks like this: errors that carry no structure and can't be distinguished by the caller except by matching strings.

```go
// WRONG — error messages with no structure, impossible to inspect programmatically
func fetchUser(id int) (*User, error) {
    if id <= 0 {
        return nil, errors.New("invalid user id")
    }
    user, err := db.Query(id)
    if err != nil {
        return nil, fmt.Errorf("database error: %v", err)  // %v loses the original error
    }
    return user, nil
}

// Caller has no way to distinguish "invalid id" from "db down" except string matching
```

The `%v` verb is the subtle killer here. It formats the original error as a string and discards its type. If the database returns `sql.ErrNoRows`, you've thrown that information away. The caller can't call `errors.Is` against it. They're stuck doing fragile string comparisons or treating every error as fatal.

The second failure mode is wrapping without substance. `fmt.Errorf("error: %w", err)` adds noise and no context. Every layer should explain what operation failed and with what inputs.

## The Idiomatic Way

Errors should carry structure. Build types that implement the `error` interface and carry data the caller can use to make decisions:

```go
// RIGHT — structured error types the caller can inspect
type ValidationError struct {
    Field   string
    Message string
}

func (e *ValidationError) Error() string {
    return fmt.Sprintf("validation failed on %s: %s", e.Field, e.Message)
}

type NotFoundError struct {
    Resource string
    ID       int
}

func (e *NotFoundError) Error() string {
    return fmt.Sprintf("%s with id %d not found", e.Resource, e.ID)
}

func fetchUser(id int) (*User, error) {
    if id <= 0 {
        return nil, &ValidationError{Field: "id", Message: "must be positive"}
    }
    user, err := db.QueryUser(id)
    if errors.Is(err, sql.ErrNoRows) {
        return nil, &NotFoundError{Resource: "user", ID: id}
    }
    if err != nil {
        return nil, fmt.Errorf("fetchUser: %w", err)  // %w preserves the chain
    }
    return user, nil
}
```

For simpler cases where you just need a distinguishable signal, sentinel errors are the right tool — package-level error values that callers check against. The standard library uses them everywhere: `io.EOF`, `sql.ErrNoRows`, `http.ErrNoCookie`.

```go
var (
    ErrNotFound     = errors.New("not found")
    ErrUnauthorized = errors.New("unauthorized")
    ErrInvalidInput = errors.New("invalid input")
)
```

Use `errors.Is` to check sentinels — never `==`. `errors.Is` walks the entire error chain, so wrapping doesn't break it:

```go
item, err := getItem(id)
if errors.Is(err, ErrNotFound) {
    http.Error(w, "not found", http.StatusNotFound)
    return
}
```

Use `errors.As` when you need data from the error, not just a match:

```go
var apiErr *APIError
if errors.As(err, &apiErr) {
    w.WriteHeader(apiErr.StatusCode)
    json.NewEncoder(w).Encode(map[string]string{
        "code":    apiErr.Code,
        "message": apiErr.Message,
    })
    return
}
```

`errors.As` also walks the chain. Implement `Unwrap() error` on your custom types to make the chain traversable:

```go
type APIError struct {
    StatusCode int
    Code       string
    Message    string
    Cause      error
}

func (e *APIError) Error() string {
    return fmt.Sprintf("[%d %s] %s", e.StatusCode, e.Code, e.Message)
}

func (e *APIError) Unwrap() error {
    return e.Cause
}
```

The wrapping convention for `%w`: `"functionName: context"` — lowercase, no trailing period. It reads naturally when layers stack up: `"processPayment: processOrder abc123: validateOrder: invalid input"`.

## In The Wild

An HTTP checkout handler that needs to surface structured errors from several layers down the call stack:

```go
// WRONG — using %v and throwing away the type
func processOrder(orderID string) error {
    if err := validateOrder(orderID); err != nil {
        return fmt.Errorf("order validation failed: %v", err)
        // errors.Is/As can no longer inspect the original
    }
    return nil
}
```

```go
// RIGHT — %w preserves the chain all the way up
func processOrder(orderID string) error {
    if err := validateOrder(orderID); err != nil {
        return fmt.Errorf("processOrder %s: %w", orderID, err)
    }
    return nil
}

func handleCheckout(w http.ResponseWriter, r *http.Request) {
    err := processCheckout(r.Context())
    if err == nil {
        w.WriteHeader(http.StatusOK)
        return
    }

    var apiErr *APIError
    if errors.As(err, &apiErr) {
        w.Header().Set("Content-Type", "application/json")
        w.WriteHeader(apiErr.StatusCode)
        json.NewEncoder(w).Encode(map[string]string{
            "code":    apiErr.Code,
            "message": apiErr.Message,
        })
        return
    }

    log.Printf("unexpected error during checkout: %v", err)
    http.Error(w, "internal server error", http.StatusInternalServerError)
}
```

The structured error travels from `chargeCard` through `processOrder` through `processCheckout` wrapped in context at each layer — and the HTTP handler at the top can still extract the `StatusCode` and `Code` fields. That's the chain working as designed.

## The Gotchas

**`panic` is not error handling.** New Go developers sometimes use `panic` as a substitute for exceptions. It's almost always wrong. Panic is for programmer errors — index out of bounds, nil pointer dereference when you guaranteed the pointer was valid, calling a function with violated preconditions. For everything else — network failures, missing files, invalid input, timeouts — return an error and give the caller the choice of how to respond.

**Logging and returning.** Pick one. If you log the error, handle it. If you return it, let the caller log it. Doing both means every error gets logged twice and your logs become noise.

**Wrapping without substance.** `fmt.Errorf("error: %w", err)` adds noise. Include the operation name and the relevant inputs at each layer. "fetchUser 42: database error" builds a useful narrative; "error" does not.

## Key Takeaway

Go's error model treats errors as first-class information, not invisible exceptions. When you build structured error types, you're documenting failure modes as explicitly as you document the happy path. When you wrap errors with `%w`, you're building a narrative: what failed, why it failed, and where in the call stack the failure was detected — all in a form your code can programmatically inspect. That's worth the verbosity. Languages with exceptions tell you *where* something exploded via a stack trace; a well-designed Go error chain tells you *what* failed and *why*, in a form your monitoring, your handlers, and your tests can all act on.

---

← [Lesson 22: Small Packages Win](/post/go/go-idioms-small-packages) | [Course Index](/post/go/) | [Lesson 24: Prefer Plain Structs](/post/go/go-idioms-plain-structs) →
