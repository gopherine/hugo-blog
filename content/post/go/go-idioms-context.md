---
title: 'Lesson 14: context.Context — The parameter every function should take first'
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
series: "Idiomatic Go"
lesson: 14
---

Without `context.Context`, a function that makes a database call, fires an HTTP request, or runs a long computation has no way to be told to stop. The caller times out, the user closes the browser tab, the load balancer kills the connection — and your function keeps running, consuming CPU and holding database connections, doing work that nobody will ever see. `context.Context` is how Go solves this.

## The Problem

A function with no context cannot be cancelled. It runs until it finishes, or until the process dies. In an HTTP server handling hundreds of requests per second, this accumulates fast:

```go
// WRONG — no timeout, query can hang forever
func getUserByID(db *sql.DB, userID string) (*User, error) {
    row := db.QueryRow("SELECT id, name, email FROM users WHERE id = $1", userID)
    var u User
    if err := row.Scan(&u.ID, &u.Name, &u.Email); err != nil {
        return nil, err
    }
    return &u, nil
}
```

If the database is slow or unreachable, this call blocks the goroutine serving the request. Goroutines pile up. Memory climbs. The whole service degrades.

The context-less signature also fails to compose. If a calling function has a 500ms deadline and this function takes 2 seconds, there is no way to propagate that constraint inward. Every layer has to set its own independent timeout, they don't compose, and you end up with a system that can technically time out at each layer but never actually cancels in-flight work.

## The Idiomatic Way

Accept `context.Context` as the first parameter, named `ctx`. Always. Pass it to every function that does I/O. Derive child contexts when you need to add tighter deadlines.

```go
// RIGHT — context propagates cancellation and deadlines
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

A child context can only be more restrictive than its parent — never more permissive. If the caller's context has 300ms remaining and you create a 2-second child context, the child still expires at 300ms. Deadlines propagate downward automatically.

At the top of the call stack, create a root context with `context.Background()`:

```go
// Entry points: main, test functions, HTTP handlers, message queue consumers
ctx := context.Background()

// Add a deadline for the entire handler
ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
defer cancel()
```

Never store a context in a struct. Contexts are request-scoped — they represent one operation's lifetime. A struct that holds a database connection is long-lived. Mixing those lifetimes causes subtle bugs where a cancelled context leaks into unrelated work:

```go
// WRONG — context on the struct
type Service struct {
    ctx context.Context // don't do this
    db  *sql.DB
}

// RIGHT — context as a parameter
func (s *Service) GetUser(ctx context.Context, id string) (*User, error)
```

For goroutines that need to stop when cancelled, check `ctx.Done()` in your select:

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
            return // context cancelled — stop the worker
        }
    }
}
```

Without the `ctx.Done()` case, a goroutine blocked on `<-work` will never exit when the context is cancelled. That's a goroutine leak.

## In The Wild

In an HTTP handler, the request context is already wired to client disconnection:

```go
func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    ctx := r.Context() // cancelled automatically when client disconnects
    ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
    defer cancel()

    user, err := h.userService.GetUser(ctx, r.PathValue("id"))
    if err != nil {
        if errors.Is(err, context.Canceled) {
            return // client disconnected, no response needed
        }
        http.Error(w, "internal error", http.StatusInternalServerError)
        return
    }

    recCtx, recCancel := context.WithTimeout(ctx, 3*time.Second)
    defer recCancel()

    recs, err := h.recService.GetRecommendations(recCtx, user.ID)
    if err != nil {
        recs = nil // non-fatal, serve user without recommendations
    }

    json.NewEncoder(w).Encode(buildResponse(user, recs))
}
```

When a user closes the browser tab mid-request, `r.Context()` is cancelled. That cancellation propagates into `GetUser`, into `GetRecommendations`, into every `QueryRowContext` in the entire call chain. Expensive work stops. Database connections are returned to the pool. This is the whole point.

## The Gotchas

**Always defer cancel.** Every `context.WithCancel`, `context.WithTimeout`, and `context.WithDeadline` returns a cancel function. If you don't call it, the context and its resources leak until the parent context is cancelled. Always:

```go
ctx, cancel := context.WithTimeout(parent, 5*time.Second)
defer cancel() // this line is not optional
```

**`context.Value` is not a general-purpose bag.** Contexts can carry values via `context.WithValue`, which is useful for request IDs, trace IDs, and authenticated principals. It's not for business logic data or database results. Values bypass the type system (you extract them with type assertions), and any function in the call chain can accidentally overwrite a key. Use unexported custom types as keys to prevent collisions between packages:

```go
type contextKey string
const requestIDKey contextKey = "requestID"

// This key is unique to this package — no collision with other packages
ctx = context.WithValue(ctx, requestIDKey, id)
```

**`context.TODO()` vs `context.Background()`.** They're functionally identical. `TODO` is a marker meaning "this should have a real context eventually." Use `Background()` for intentional long-lived roots; use `TODO()` when you're migrating old code incrementally and haven't wired up a real context yet. If you see `TODO()` deep in a call stack, that's a todo worth addressing.

## Key Takeaway

`context.Context` as the first parameter is not a suggestion in Go — it's how the entire standard library and every serious production service is written. The payoff is real: cancellations propagate automatically through your entire call stack, timeouts compose correctly, and client disconnections stop work that would otherwise run to completion for no reason. Thread context through every function that does I/O from day one. Retrofitting it later is tedious, and every day you don't have it is a day your service is doing unnecessary work.

---

← [Lesson 13: iota for Enums](/post/go/go-idioms-iota-enums) | [Course Index](#) | [Lesson 15: Goroutines Are Cheap, Not Free](/post/go/go-idioms-goroutines-cheap-not-free) →
