---
title: "Lesson 19: Context — Passing deadlines and cancellation through your code"
author: Atharva Pandey
series: "Go from Scratch"
lesson: 19
keywords: [Go, golang, beginner, context, WithCancel, WithTimeout, WithValue, Done channel, cancellation, deadline]
tags: [Go tutorial, golang, beginner]
date: '2024-04-12T00:00:00.000Z'
---

There's a moment in every Go developer's journey when they realize that starting an operation isn't the hard part — stopping it cleanly is. What happens when a user cancels a request? What happens when a database query takes 30 seconds instead of 300 milliseconds? Without a way to communicate "stop what you're doing," those goroutines keep running, consuming resources for something that no longer matters.

That's the problem `context` solves. It gives you a standard way to carry a cancellation signal, a deadline, and a small amount of request-scoped data through your entire call stack. Once you understand it, you'll see why the Go standard library passes it as the first parameter to almost every function that does I/O.

---

## The Basics

### `context.Background()` and `context.TODO()`

Every context starts from one of two roots:

- `context.Background()` is the top-level, empty context. Use it at the start of a program, in `main()`, or at the root of an incoming request.
- `context.TODO()` is also an empty context, but it signals intent: "I know I need a context here, and I'll hook it up properly later." It's a placeholder you leave in code under active development.

```go
package main

import (
    "context"
    "fmt"
)

func doWork(ctx context.Context) {
    fmt.Println("Starting work")
    // ... eventually we'll check ctx here
}

func main() {
    ctx := context.Background()
    doWork(ctx)
}
```

On their own, `Background()` and `TODO()` never cancel, never expire, and carry no values. They're starting points. You derive useful contexts from them using the `With*` functions.

### `context.WithCancel` — manual cancellation

`WithCancel` creates a child context and a `cancel` function. When you call `cancel()`, the child context (and all contexts derived from it) are cancelled.

```go
package main

import (
    "context"
    "fmt"
    "time"
)

func worker(ctx context.Context, id int) {
    for {
        select {
        case <-ctx.Done():
            fmt.Printf("Worker %d stopping: %v\n", id, ctx.Err())
            return
        default:
            fmt.Printf("Worker %d doing work...\n", id)
            time.Sleep(200 * time.Millisecond)
        }
    }
}

func main() {
    ctx, cancel := context.WithCancel(context.Background())
    defer cancel() // always call cancel, even if you cancel early

    go worker(ctx, 1)
    go worker(ctx, 2)

    time.Sleep(600 * time.Millisecond)
    fmt.Println("Cancelling...")
    cancel()
    time.Sleep(100 * time.Millisecond) // give workers time to print their stop message
}
```

`ctx.Done()` returns a channel that receives a value when the context is cancelled. `ctx.Err()` returns the reason: `context.Canceled` for manual cancellation, `context.DeadlineExceeded` for timeouts.

**Always call `cancel()`.** If you don't, you leak resources. The `defer cancel()` right after `WithCancel` is the idiomatic pattern — even if you call `cancel()` earlier in the function, calling it again is a no-op.

### `context.WithTimeout` — automatic deadline

`WithTimeout` creates a context that cancels automatically after a duration. This is the most common pattern for outgoing HTTP calls, database queries, and anything else that shouldn't run forever.

```go
package main

import (
    "context"
    "fmt"
    "time"
)

func fetchData(ctx context.Context) error {
    // Simulate a slow operation
    select {
    case <-time.After(2 * time.Second):
        fmt.Println("Data fetched!")
        return nil
    case <-ctx.Done():
        return fmt.Errorf("fetchData: %w", ctx.Err())
    }
}

func main() {
    ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
    defer cancel()

    if err := fetchData(ctx); err != nil {
        fmt.Println("Error:", err) // Error: fetchData: context deadline exceeded
    }
}
```

`WithDeadline` is the same idea but you pass an absolute `time.Time` instead of a duration. Under the hood, `WithTimeout(ctx, d)` is just `WithDeadline(ctx, time.Now().Add(d))`.

### `context.WithValue` — carrying request-scoped data

`WithValue` lets you attach a key-value pair to a context. Every function that receives the context can retrieve the value with `ctx.Value(key)`.

```go
package main

import (
    "context"
    "fmt"
)

type contextKey string

const requestIDKey contextKey = "requestID"

func handleRequest(ctx context.Context) {
    if id, ok := ctx.Value(requestIDKey).(string); ok {
        fmt.Println("Handling request:", id)
    }
}

func main() {
    ctx := context.WithValue(context.Background(), requestIDKey, "req-abc-123")
    handleRequest(ctx)
}
```

A few rules that will save you from subtle bugs:

1. **Use a custom unexported type for your keys**, not bare strings or integers. If two packages both use the string `"userID"` as a key, they'll overwrite each other silently. Your own type prevents that collision.
2. **Store only request-scoped data** — things like a request ID, a trace ID, or authentication information that needs to flow through many layers. Don't use context values as a backdoor to pass function parameters; that makes your code hard to read and test.
3. **Context values have no type safety.** `ctx.Value(key)` returns `any`, so you need a type assertion. The assertion can fail if the value isn't there, so always use the two-value form: `val, ok := ctx.Value(key).(string)`.

### Always pass context as the first parameter

By convention, context is always the first argument of a function, and it's always named `ctx`:

```go
func QueryUser(ctx context.Context, id int) (*User, error) { ... }
func SendEmail(ctx context.Context, to, subject, body string) error { ... }
func FetchWeather(ctx context.Context, city string) (*Weather, error) { ... }
```

Don't store a context in a struct field (with very few exceptions like long-lived background jobs). A context is tied to a specific operation — an incoming request, a single function call chain. Storing it in a struct makes it outlive its intended scope.

### The `Done()` channel

`ctx.Done()` is a channel that is closed when the context is cancelled or its deadline passes. Closing a channel (rather than sending a value) is how Go signals "this is over" to potentially many goroutines at once — every goroutine blocking on `<-ctx.Done()` will unblock simultaneously.

```go
func streamData(ctx context.Context) {
    for {
        select {
        case <-ctx.Done():
            // clean up and exit
            return
        default:
            // do the next unit of work
        }
    }
}
```

Check `ctx.Done()` in your loops and between expensive steps. You don't need to check it after every single operation — just at natural yield points where it's safe to stop.

---

## Try It Yourself

Write a function `countdown(ctx context.Context, from int)` that prints numbers from `from` down to 0, pausing 300ms between each. Call it with a `WithTimeout` context of 1 second. What number does it stop at? Then call it with 2 seconds and observe the full countdown.

As a bonus, add a `WithValue` that carries a name, and make the function print "Countdown started by: {name}" at the top.

---

## Common Mistakes

**Not calling `cancel()`**

Every `WithCancel`, `WithTimeout`, and `WithDeadline` allocates internal resources. If you never call `cancel()`, those resources leak for the lifetime of the parent context. Use `defer cancel()` immediately after creating the context.

**Using `context.Background()` deep inside a function**

If a function creates its own `Background()` context instead of using the context passed in, it becomes impossible to cancel from the outside. Always thread the incoming context through, or derive a child context from it.

**Using bare strings as context keys**

```go
// WRONG — key collisions are possible
ctx = context.WithValue(ctx, "userID", 42)

// RIGHT — use your own type
type ctxKey string
const userIDKey ctxKey = "userID"
ctx = context.WithValue(ctx, userIDKey, 42)
```

**Passing a cancelled context to cleanup code**

If you're running cleanup logic after a context is cancelled, don't pass the cancelled context to your cleanup functions — they'll fail immediately. Create a fresh `Background()` context (with its own short timeout) for cleanup operations.

---

## Key Takeaway

`context` is how Go programs communicate "stop" signals and deadlines across function boundaries. Start with `context.Background()` at your entry points, derive child contexts with `WithCancel`, `WithTimeout`, or `WithValue`, always call `cancel()` with a defer, always pass context as the first parameter, and check `ctx.Done()` in long-running loops. Use `WithValue` sparingly and only for request-scoped metadata — never as a substitute for proper function parameters.

---

*[Course Index: Go from Scratch](/series/go-from-scratch) | [← Lesson 18: The sync Package](/post/go/go-scratch-sync) | [Lesson 20: Testing in Go →](/post/go/go-scratch-testing)*
