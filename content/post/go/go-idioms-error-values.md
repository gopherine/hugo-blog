---
title: 'Go Idioms: Error Values, Not Exceptions'
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
date: '2025-07-28T00:00:00.000Z'
---
t happen, thrown up the call stack and caught somewhere above. Go rejects this model entirely. In Go, an error is a value, just like an integer or a string. You pass it around, you inspect it, you wrap it with context, and you check it right where it happens.

This forces a different relationship with failure. You can't ignore it without an explicit decision to do so. You can't let it silently propagate past ten function calls. Error handling is visible, which makes it honest.

## The Error Interface

Everything in Go's error system is built on a single interface:

```go
type error interface {
    Error() string
}
```

Any type that implements this interface is an error. That's it. The simplicity is intentional — it means you can make errors carry any information you want, as long as they know how to describe themselves as a string.

## Custom Error Types

The most basic custom error is a string wrapped in a type. The `errors.New` and `fmt.Errorf` functions cover the common cases, but when you need to carry structured data, you implement the interface yourself.

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

Now the caller can make decisions based on what kind of error occurred, not just what the error message says.

## Sentinel Errors

A sentinel error is a package-level error value that callers check against. The standard library uses them extensively: `io.EOF`, `sql.ErrNoRows`, `http.ErrNoCookie`. They're the simplest form of error that a caller can specifically handle.

```go
var (
    ErrNotFound    = errors.New("not found")
    ErrUnauthorized = errors.New("unauthorized")
    ErrInvalidInput = errors.New("invalid input")
)

func getItem(id string) (*Item, error) {
    if id == "" {
        return nil, ErrInvalidInput
    }
    item, ok := store[id]
    if !ok {
        return nil, ErrNotFound
    }
    return item, nil
}
```

Callers compare against the sentinel using `errors.Is`:

```go
item, err := getItem(id)
if errors.Is(err, ErrNotFound) {
    http.Error(w, "not found", http.StatusNotFound)
    return
}
if errors.Is(err, ErrInvalidInput) {
    http.Error(w, "bad request", http.StatusBadRequest)
    return
}
```

## Wrapping Errors with %w

When you add context to an error, use `%w` instead of `%v`. The `%w` verb wraps the original error in the new one, preserving the full chain. `%v` just formats the error as a string, discarding the type information.

```go
// WRONG — %v breaks the error chain
func processOrder(orderID string) error {
    if err := validateOrder(orderID); err != nil {
        return fmt.Errorf("order validation failed: %v", err)
        // Original error is now just a string, errors.Is/As can't inspect it
    }
    return nil
}

// RIGHT — %w preserves the chain
func processOrder(orderID string) error {
    if err := validateOrder(orderID); err != nil {
        return fmt.Errorf("processOrder %s: %w", orderID, err)
        // Caller can still use errors.Is(err, ErrInvalidInput) through the chain
    }
    return nil
}
```

The convention for the wrapper message is `"functionName: context"` — lowercase, no trailing period. It reads naturally when multiple layers stack up: `"processPayment: processOrder abc123: validateOrder: invalid input"`.

## errors.Is vs errors.As

These two functions handle the two common inspection cases.

`errors.Is` checks if any error in the chain matches a specific value. Use it for sentinel errors.

`errors.As` checks if any error in the chain matches a specific type, and if so, fills a pointer with that value. Use it for structured errors where you need the fields.

```go
// Building an error hierarchy for an API
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
    return e.Cause  // enables errors.Is/As to look deeper
}

// Somewhere deep in the call stack:
func chargeCard(amount int) error {
    if amount > maxCharge {
        return &APIError{
            StatusCode: 422,
            Code:       "CHARGE_LIMIT_EXCEEDED",
            Message:    fmt.Sprintf("amount %d exceeds maximum %d", amount, maxCharge),
        }
    }
    return nil
}

// HTTP handler at the top of the stack:
func handleCheckout(w http.ResponseWriter, r *http.Request) {
    err := processCheckout(r.Context())
    if err == nil {
        w.WriteHeader(http.StatusOK)
        return
    }

    var apiErr *APIError
    if errors.As(err, &apiErr) {
        // We got our structured error even if it was wrapped several layers deep
        w.Header().Set("Content-Type", "application/json")
        w.WriteHeader(apiErr.StatusCode)
        json.NewEncoder(w).Encode(map[string]string{
            "code":    apiErr.Code,
            "message": apiErr.Message,
        })
        return
    }

    // Unknown error — don't leak internals
    log.Printf("unexpected error during checkout: %v", err)
    http.Error(w, "internal server error", http.StatusInternalServerError)
}
```

The `Unwrap() error` method is what makes the chain work. When `errors.Is` or `errors.As` traverses the chain, it calls `Unwrap()` at each step until it finds what it's looking for or runs out of chain.

## Why Panic Is Not Error Handling

Go has `panic` and `recover`. New Go programmers sometimes use them as a substitute for exceptions, which is almost always wrong.

```go
// WRONG — using panic for ordinary error conditions
func parseConfig(path string) Config {
    data, err := os.ReadFile(path)
    if err != nil {
        panic(fmt.Sprintf("cannot read config: %v", err))
        // Caller now has to wrap this in a recover() just to get an error back
    }
    // ...
}
```

```go
// RIGHT — return an error, let the caller decide what to do
func parseConfig(path string) (Config, error) {
    data, err := os.ReadFile(path)
    if err != nil {
        return Config{}, fmt.Errorf("parseConfig: %w", err)
    }
    var cfg Config
    if err := json.Unmarshal(data, &cfg); err != nil {
        return Config{}, fmt.Errorf("parseConfig: invalid JSON in %s: %w", path, err)
    }
    return cfg, nil
}
```

Panic is for programmer errors — the kind that should never happen in a correct program. Index out of bounds. Nil pointer dereference when you guaranteed the pointer was valid. Calling a function with preconditions you violated. These are bugs, not errors.

For everything else — network failures, missing files, invalid input, database errors, timeouts — return an error. Give the caller the choice of how to respond.

## Errors Are Information

The deeper point about Go's error model is that it treats errors as first-class information about what went wrong. When you return a structured error type, you're documenting failure modes as explicitly as you document the happy path. When you wrap errors with context, you're building a narrative about where the failure occurred and why.

Languages with exceptions can obscure this. A stack trace tells you *where* something exploded, but a well-designed Go error chain tells you *what* failed, *why* it failed, and *where in the logic* the failure was detected — all in a form your code can programmatically inspect and act on.

That's worth a bit of verbosity.
