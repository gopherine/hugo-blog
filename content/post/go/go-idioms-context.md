---
title: 'Go Idioms: context.Context'
author: Atharva Pandey
keywords:
  - Go
  - golang
  - context
  - context.Context
  - cancellation
  - timeout
  - deadline
  - goroutines
tags:
  - Go tutorial
  - golang
date: '2025-06-16T00:00:00.000Z'
---
t wired it up yet
ctx := context.TODO()
```

These two are functionally identical. The difference is semantic: `Background` is the intended long-term root; `TODO` is a marker that says "this code should have a real context eventually, but I'm adding one incrementally." You will see `context.TODO()` in codebases that were written before context was standard practice and are being migrated.

Do not use `context.Background()` deep inside your call stack. Use it at entry points — the `main` function, test functions, and the first point where a request enters your system (like an HTTP handler). From there, derive child contexts as needed.

## WithCancel, WithTimeout, WithDeadline

These three functions create child contexts with cancellation attached. They all return a context and a cancel function. Always defer the cancel function — calling it releases resources even if the context has not yet been used.

```go
// WithCancel — cancel manually
ctx, cancel := context.WithCancel(context.Background())
defer cancel() // always defer this

// WithTimeout — cancel after a duration
ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
defer cancel()

// WithDeadline — cancel at a specific time
deadline := time.Now().Add(5 * time.Second)
ctx, cancel := context.WithDeadline(context.Background(), deadline)
defer cancel()
```

`WithTimeout` is shorthand for `WithDeadline(parent, time.Now().Add(d))`. Use `WithTimeout` when you are thinking in durations ("this database call should finish in 2 seconds"), and `WithDeadline` when you have an absolute time ("this batch job must finish before midnight").

Here is a realistic example — a database call with a timeout:

```go
// WRONG — no timeout, query can hang forever
func getUserByID(db *sql.DB, userID string) (*User, error) {
    row := db.QueryRow("SELECT id, name, email FROM users WHERE id = $1", userID)
    // ...
}

// RIGHT — context with timeout propagated through the call
func getUserByID(ctx context.Context, db *sql.DB, userID string) (*User, error) {
    queryCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
    defer cancel()

    row := db.QueryRowContext(queryCtx, "SELECT id, name, email FROM users WHERE id = $1", userID)

    var u User
    if err := row.Scan(&u.ID, &u.Name, &u.Email); err != nil {
        if errors.Is(err, context.DeadlineExceeded) {
            return nil, fmt.Errorf("database query timed out: %w", err)
        }
        return nil, err
    }
    return &u, nil
}
```

Notice that the function accepts a `ctx context.Context` from the caller. The caller's context may already have a deadline shorter than 2 seconds — in that case, the `WithTimeout` call does not override it. A child context can only be more restrictive than its parent, never more permissive. If the parent has 500ms left, your 2-second timeout does not extend it.

## Context as the First Parameter

This is a Go convention that is very close to a rule: if a function accepts a context, it should be the first parameter, named `ctx`:

```go
// WRONG — context buried in the middle or last
func fetchData(userID string, ctx context.Context, options FetchOptions) ([]byte, error)

// WRONG — context on the struct instead of the function
type Service struct {
    ctx context.Context // don't do this
    db  *sql.DB
}

// RIGHT — context is first, named ctx
func fetchData(ctx context.Context, userID string, options FetchOptions) ([]byte, error)
```

Storing a context on a struct is a specific antipattern that the Go documentation explicitly calls out. Contexts are request-scoped — they represent the lifetime of one operation. A struct that stores a database connection or a logger represents a long-lived service. Those two lifetimes should not be mixed. When you need a context, accept it as a parameter.

## The Done() Channel Pattern

Contexts signal cancellation through a channel returned by `ctx.Done()`. When the context is cancelled (by a cancel call, timeout, or deadline), the channel is closed. Closed channels can be selected on and received from indefinitely, which makes them perfect for signaling:

```go
func processItems(ctx context.Context, items []Item) error {
    for _, item := range items {
        // Check for cancellation before each unit of work
        select {
        case <-ctx.Done():
            return ctx.Err() // context.Canceled or context.DeadlineExceeded
        default:
            // continue processing
        }

        if err := processItem(ctx, item); err != nil {
            return err
        }
    }
    return nil
}
```

The `select` with a `default` case is non-blocking. It checks if the context is done; if it is, it returns immediately. If not, it falls through to the default and continues. Use this at the top of loops that might run for a long time.

For goroutines, the pattern looks like this:

```go
func startWorker(ctx context.Context, work <-chan Task) {
    for {
        select {
        case task, ok := <-work:
            if !ok {
                return // channel closed
            }
            processTask(ctx, task)
        case <-ctx.Done():
            return // context cancelled, stop the worker
        }
    }
}
```

This is how you prevent goroutine leaks. Without the `ctx.Done()` case, if the context is cancelled, the goroutine blocks on `<-work` forever. With it, the goroutine stops cleanly when the context is done.

## Cancellation Propagation in HTTP Servers

The Go HTTP server automatically cancels the request context when the client disconnects. This means if you propagate the request context through your call stack, all the downstream work will be cancelled automatically:

```go
func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    ctx := r.Context() // already has cancellation tied to the HTTP request

    // Add a deadline for the entire handler
    ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
    defer cancel()

    user, err := h.userService.GetUser(ctx, r.PathValue("id"))
    if err != nil {
        if errors.Is(err, context.Canceled) {
            // Client disconnected — no need to write a response
            return
        }
        http.Error(w, "internal error", http.StatusInternalServerError)
        return
    }

    // Fetch recommendations in parallel
    recCtx, recCancel := context.WithTimeout(ctx, 3*time.Second)
    defer recCancel()

    recs, err := h.recService.GetRecommendations(recCtx, user.ID)
    if err != nil {
        // Recommendation failure is non-fatal — serve the user without recs
        recs = nil
    }

    json.NewEncoder(w).Encode(buildResponse(user, recs))
}
```

If the client closes the browser tab mid-request, `r.Context()` gets cancelled. That cancellation propagates to `ctx`, which propagates to every `QueryRowContext`, `http.NewRequestWithContext`, and downstream `select { case <-ctx.Done() }` in your entire call chain. Expensive work stops. Resources are released.

## context.Value — Use Sparingly

Contexts can carry request-scoped values via `context.WithValue`. This is useful for things like request IDs and authenticated user identifiers — data that is relevant throughout the call stack for a single request but that you do not want to thread through every function signature.

```go
type contextKey string

const requestIDKey contextKey = "requestID"

// Setting a value
func withRequestID(ctx context.Context, id string) context.Context {
    return context.WithValue(ctx, requestIDKey, id)
}

// Reading a value
func getRequestID(ctx context.Context) string {
    id, ok := ctx.Value(requestIDKey).(string)
    if !ok {
        return ""
    }
    return id
}
```

Use an unexported custom type for your context keys, never a string or built-in type directly. If two packages both use the string `"userID"` as a key, they will collide. A `contextKey` type defined in your package is unique.

The caution: context values are not typed until you extract them with a type assertion. They bypass the compiler's type system. Do not use `context.Value` for data that is required for core business logic — use function parameters for that. Reserve `context.Value` for cross-cutting concerns: request IDs, trace IDs, authenticated principals, feature flags for the request. If you find yourself putting database results or computed values into the context, step back and reconsider.

## The Shape of a Well-Contextual API

After all this, here is what a function that uses context correctly looks like:

```go
// Context first, all other params after, returns error as last value
func (s *OrderService) CreateOrder(ctx context.Context, userID string, items []OrderItem) (*Order, error) {
    // Respect incoming deadline, add own if needed
    ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
    defer cancel()

    // Pass context to all I/O operations
    user, err := s.users.GetUser(ctx, userID)
    if err != nil {
        return nil, fmt.Errorf("fetching user: %w", err)
    }

    order, err := s.orders.Insert(ctx, user.ID, items)
    if err != nil {
        return nil, fmt.Errorf("inserting order: %w", err)
    }

    // Pass context to downstream services
    if err := s.inventory.Reserve(ctx, items); err != nil {
        return nil, fmt.Errorf("reserving inventory: %w", err)
    }

    return order, nil
}
```

Context as first parameter. Derive a child context when you need to add a timeout. Pass it to every function that does I/O. Check `ctx.Err()` or use `errors.Is(err, context.DeadlineExceeded)` to handle cancellations gracefully.

This is the pattern the entire standard library and every well-written Go service follows. Once it is in your muscle memory, you will never want to write blocking code without it again.
