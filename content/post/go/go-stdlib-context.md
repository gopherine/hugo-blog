---
title: 'Lesson 7: context Internals — How cancellation propagates under the hood'
author: Atharva Pandey
series: "Go Standard Library Mastery"
lesson: 7
keywords:
  - Go
  - golang
  - context
  - cancellation
  - deadline
  - timeout
  - context.Context
  - stdlib
tags:
  - Go tutorial
  - golang
  - stdlib
date: '2025-05-22T00:00:00.000Z'
---

`context.Context` is the most important type in Go's standard library that most people use by convention without fully understanding. You pass it as the first argument to functions. You check `ctx.Done()` in goroutines. You create derived contexts with `context.WithTimeout`. It works — until it doesn't, and you have a goroutine that doesn't cancel when the parent context does, or a context that leaks forever because you forgot to call the cancel function.

Understanding how `context` actually works internally — the parent-child tree, the cancellation propagation mechanism, the value chain — makes the difference between correct context usage and cargo-culted context usage.

## The Problem

The most common mistake: ignoring the cancel function:

```go
// WRONG — cancel function is never called, goroutines and memory leak
func handleRequest(w http.ResponseWriter, r *http.Request) {
    ctx, _ := context.WithTimeout(r.Context(), 5*time.Second)
    // _ discards the cancel function — the timeout will fire eventually,
    // but until then, the context's internal goroutine holds memory.
    result, err := slowDatabaseQuery(ctx)
    // ...
}
```

`context.WithTimeout` (and `WithCancel`, `WithDeadline`) creates a new goroutine internally that waits for either the parent to cancel, the timer to fire, or the cancel function to be called. If you never call cancel, that goroutine keeps running until the timeout fires. In a high-throughput service, this accumulates thousands of leaked goroutines.

The second mistake: storing contexts in structs:

```go
// WRONG — context should not be stored in a struct
type Service struct {
    ctx context.Context // BAD: context is request-scoped, not service-scoped
    db  *sql.DB
}
```

A context is request-scoped — it carries the deadline and cancellation of one specific request. Storing it in a struct means the struct lives beyond the request, but the context inside it may have already been cancelled. Pass context as a function argument, not a struct field.

## The Idiomatic Way

Always call cancel, always immediately with defer:

```go
func handleRequest(w http.ResponseWriter, r *http.Request) {
    // WithTimeout: creates a child context that cancels after 5 seconds
    // OR when the parent (r.Context()) cancels — whichever comes first.
    ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
    defer cancel() // ALWAYS defer cancel immediately after creating the context

    result, err := slowDatabaseQuery(ctx)
    if err != nil {
        if errors.Is(err, context.DeadlineExceeded) {
            http.Error(w, "request timeout", http.StatusGatewayTimeout)
            return
        }
        http.Error(w, "internal error", http.StatusInternalServerError)
        return
    }
    json.NewEncoder(w).Encode(result)
}
```

The `defer cancel()` pattern is the correct idiom. Calling cancel multiple times is safe — the cancel function is idempotent. Deferring it ensures it's called even if the function returns early due to an error.

Understanding how cancellation propagates. The `context` package maintains a tree of contexts. Each `WithCancel`, `WithTimeout`, or `WithDeadline` call creates a child node in the tree. When a parent is cancelled, all children in the subtree are cancelled atomically via a channel close:

```go
// This is a simplified version of how context.WithCancel works internally:
type cancelCtx struct {
    parent Context
    done   chan struct{} // closed when this context is cancelled
    err    error
    mu     sync.Mutex
    children map[*cancelCtx]struct{} // child contexts registered here
}

func (c *cancelCtx) Done() <-chan struct{} {
    return c.done
}

func (c *cancelCtx) cancel(err error) {
    c.mu.Lock()
    if c.err != nil { // already cancelled
        c.mu.Unlock()
        return
    }
    c.err = err
    close(c.done) // closing the channel wakes up all goroutines selecting on it
    for child := range c.children {
        child.cancel(err) // propagate to all children
    }
    c.mu.Unlock()
}
```

This explains why selecting on `ctx.Done()` works for cancellation — the channel is closed (not sent to) when the context is cancelled, which unblocks all readers simultaneously.

Checking context cancellation in a long-running operation:

```go
func processItems(ctx context.Context, items []Item) error {
    for i, item := range items {
        // Check context cancellation at the start of each iteration.
        // This is the correct pattern for loops that shouldn't hold up cancellation.
        select {
        case <-ctx.Done():
            return fmt.Errorf("cancelled after %d items: %w", i, ctx.Err())
        default:
        }

        if err := processItem(ctx, item); err != nil {
            return fmt.Errorf("item %d: %w", err)
        }
    }
    return nil
}
```

The non-blocking select with a `default` case checks if the context is done without blocking. If the context is not yet cancelled, the loop continues.

## In The Wild

Context values — `context.WithValue` — are frequently misused for passing optional dependencies. The correct use is request-scoped metadata: trace IDs, user IDs from authentication middleware, request IDs for logging:

```go
type contextKey string

const (
    keyTraceID contextKey = "trace_id"
    keyUserID  contextKey = "user_id"
)

// Middleware adds the trace ID to the context
func withTraceID(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        traceID := r.Header.Get("X-Trace-ID")
        if traceID == "" {
            traceID = newTraceID()
        }
        ctx := context.WithValue(r.Context(), keyTraceID, traceID)
        next.ServeHTTP(w, r.WithContext(ctx))
    })
}

// Helper to extract trace ID — type-safe, with sensible fallback
func TraceID(ctx context.Context) string {
    if id, ok := ctx.Value(keyTraceID).(string); ok {
        return id
    }
    return "unknown"
}
```

The unexported `contextKey` type prevents collisions with other packages' context keys. Every package should define its own unexported key type.

Context values should carry request metadata, not function dependencies. Database connections, loggers, and other services should be passed as explicit function arguments or method receivers — not via context. The test for "should this go in context?" is: is this data that travels with the request (trace ID, user ID, request deadline)? If yes, context. If no, explicit parameter.

## The Gotchas

**`context.Background()` never cancels.** It's the root of the context tree, used in `main`, in tests, and at the top of call trees that have no cancellation requirement. Never use it inside a request handler — use `r.Context()` instead, which is cancelled when the client disconnects.

**`context.TODO()` is a placeholder, not a real context.** Use it during development when you know a function needs a context but you haven't wired one through yet. It's a signal to yourself (and code reviewers) that this is incomplete. Don't ship `context.TODO()` in production code.

**Cancellation is cooperative.** Cancelling a context doesn't interrupt a goroutine — it closes the `Done` channel. The goroutine must check `ctx.Done()` for cancellation to take effect. A goroutine that never checks the context will run to completion even after cancellation. Design long-running functions to check the context at regular intervals.

**Context values are not type-safe in the general case.** `ctx.Value(key)` returns `any`. You type-assert to the expected type, but if someone stored a different type under the same key, you get a nil after the assertion. Use unexported key types and provide typed accessor functions to enforce the contract.

## Key Takeaway

Every context created with `WithCancel`, `WithTimeout`, or `WithDeadline` must have its cancel function called — defer it immediately. Pass contexts as function arguments, never store them in structs. Use context values only for request-scoped metadata, not for dependency injection. Understand that cancellation propagates by closing a channel, so goroutines must check `ctx.Done()` cooperatively.

---

**Previous:** [Lesson 6: sync Package Complete Guide](/post/go/go-stdlib-sync/)
**Next:** [Lesson 8: strings and bytes Builders — Stop concatenating in loops](/post/go/go-stdlib-strings-bytes/)
