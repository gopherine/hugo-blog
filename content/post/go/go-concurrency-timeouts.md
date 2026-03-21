---
title: 'Lesson 13: Timeouts Everywhere — Unbounded waits are production bugs'
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 13
keywords:
  - Go
  - golang
  - concurrency
  - timeouts
  - context.WithTimeout
  - time.After
  - HTTP client timeout
  - database timeout
  - timeout stack
  - context deadline
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-08-26T00:00:00.000Z'
---

I have a strongly held opinion about timeouts: if you're making a network call, a database query, or waiting on any external resource without a timeout, you've written a production bug. It just hasn't fired yet. The network will eventually hang. The database will eventually have a slow query. The external API will eventually stop responding. And when it does, your goroutine will wait. And wait. And wait — holding a connection, a file descriptor, a slot in your worker pool — until the process runs out of resources or someone restarts it.

Every I/O call in a production Go service should have a timeout. Not most of them. Every one.

## The Problem

The default behavior for almost every I/O operation in Go is "wait forever." HTTP client? No timeout unless you set one. `sql.DB`? Queries run until the server closes the connection or you configure a timeout. Channel receive? Blocks until a message arrives or until heat death of the universe.

```go
// WRONG — HTTP client with no timeout
func fetchUser(id string) (*User, error) {
    resp, err := http.Get("https://user-service/users/" + id)
    // if user-service is slow or hanging, this goroutine is stuck indefinitely
    if err != nil {
        return nil, err
    }
    defer resp.Body.Close()
    var user User
    json.NewDecoder(resp.Body).Decode(&user)
    return &user, nil
}
```

The default `http.DefaultClient` has no timeout set. One slow upstream and you've got a goroutine stuck in a `read` syscall. If this endpoint is called frequently, you accumulate stuck goroutines. Your worker pool fills up. New requests queue up. The service cascades.

Here's the database version — equally dangerous:

```go
// WRONG — database query with no timeout
func getOrder(db *sql.DB, orderID int) (*Order, error) {
    row := db.QueryRow("SELECT * FROM orders WHERE id = $1", orderID)
    // if the database is under load and this query is slow, goroutine is stuck
    var order Order
    if err := row.Scan(&order.ID, &order.Total, &order.Status); err != nil {
        return nil, err
    }
    return &order, nil
}
```

And channel waiting without a timeout is just as bad:

```go
// WRONG — blocking channel receive with no timeout
func waitForResult(resultCh <-chan Result) Result {
    return <-resultCh // blocks forever if the sender panics or exits early
}
```

## The Idiomatic Way

Context is the Go mechanism for carrying deadlines through a call chain. Create a context with a timeout at the entry point, pass it to every downstream call, and let the deadline propagate automatically.

```go
// RIGHT — HTTP request with context deadline
func fetchUser(ctx context.Context, id string) (*User, error) {
    // If ctx already has a deadline, this uses the earlier of the two
    reqCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
    defer cancel()

    req, err := http.NewRequestWithContext(reqCtx, http.MethodGet,
        "https://user-service/users/"+id, nil)
    if err != nil {
        return nil, fmt.Errorf("build request: %w", err)
    }

    resp, err := http.DefaultClient.Do(req)
    if err != nil {
        return nil, fmt.Errorf("fetch user %s: %w", id, err)
    }
    defer resp.Body.Close()

    var user User
    if err := json.NewDecoder(resp.Body).Decode(&user); err != nil {
        return nil, fmt.Errorf("decode user: %w", err)
    }
    return &user, nil
}
```

Note that `http.DefaultClient` still has no transport-level timeout. For the context deadline to work, the HTTP client must respect it — and `http.NewRequestWithContext` does attach the context correctly. But I also recommend setting transport timeouts on the client itself as a defense-in-depth measure:

```go
// RIGHT — HTTP client with transport-level timeouts
var httpClient = &http.Client{
    Timeout: 10 * time.Second, // total request timeout including read
    Transport: &http.Transport{
        DialContext:           (&net.Dialer{Timeout: 3 * time.Second}).DialContext,
        TLSHandshakeTimeout:   3 * time.Second,
        ResponseHeaderTimeout: 5 * time.Second,
        IdleConnTimeout:       90 * time.Second,
        MaxIdleConns:          100,
        MaxIdleConnsPerHost:   10,
    },
}
```

These transport timeouts are belt-and-suspenders: the context deadline handles per-request timeouts, the transport timeouts handle pathological cases like a server that accepts the TCP connection but never sends response headers.

For database queries, pass the context:

```go
// RIGHT — database query with context deadline
func getOrder(ctx context.Context, db *sql.DB, orderID int) (*Order, error) {
    queryCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
    defer cancel()

    row := db.QueryRowContext(queryCtx,
        "SELECT id, total, status FROM orders WHERE id = $1", orderID)

    var order Order
    if err := row.Scan(&order.ID, &order.Total, &order.Status); err != nil {
        if errors.Is(err, context.DeadlineExceeded) {
            return nil, fmt.Errorf("order query timed out: %w", err)
        }
        return nil, fmt.Errorf("scan order: %w", err)
    }
    return &order, nil
}
```

For channel selects with a timeout, use `time.After` — but use it carefully:

```go
// RIGHT — channel receive with explicit timeout
func waitForResult(ctx context.Context, resultCh <-chan Result) (Result, error) {
    select {
    case r, ok := <-resultCh:
        if !ok {
            return Result{}, errors.New("result channel closed")
        }
        return r, nil
    case <-ctx.Done():
        return Result{}, fmt.Errorf("wait for result: %w", ctx.Err())
    case <-time.After(5 * time.Second):
        return Result{}, errors.New("result not received within 5s")
    }
}
```

Here I have both `ctx.Done()` and `time.After` — the context deadline might be longer than 5 seconds (or there might be no deadline), so the explicit `time.After` adds a local backstop. Choose one or the other based on whether the timeout should be request-scoped (use the context) or fixed per-operation (use `time.After`). Don't redundantly duplicate the same timeout value in both.

## In The Wild

The concept of a "timeout stack" is something I started applying after a particularly painful incident. The idea: every layer of the call stack has a timeout, and they decrease as you go deeper. The outermost layer has the most time; inner layers have progressively less.

```go
// Request-level timeout: 10 seconds total
func handleCheckout(w http.ResponseWriter, r *http.Request) {
    ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
    defer cancel()

    order, err := processCheckout(ctx, r)
    // ...
}

// Business logic: 8 seconds budget
func processCheckout(ctx context.Context, r *http.Request) (*Order, error) {
    // If the parent's deadline is already < 8s, this uses the parent's deadline
    ctx, cancel := context.WithTimeout(ctx, 8*time.Second)
    defer cancel()

    user, err := fetchUser(ctx, userIDFromRequest(r))   // 2s internal timeout
    if err != nil {
        return nil, err
    }
    inventory, err := checkInventory(ctx, r.PostForm)   // 2s internal timeout
    if err != nil {
        return nil, err
    }
    return createOrder(ctx, user, inventory)             // 3s internal timeout
}
```

The outer handler gives 10 seconds. The business logic function takes 8 seconds of that. Each sub-call has its own budget. If any sub-call exceeds its budget, it cancels and the error propagates up. The critical property: `context.WithTimeout` always uses the earlier of the parent's deadline and the new timeout. So if the parent's 10s expires, all children get cancelled immediately — you never wait longer than the outermost timeout.

The metric to track: timeout rate by endpoint and by downstream call. If your user-service calls are timing out at 0.01%, that's fine. If it spikes to 5%, you've got a problem with user-service — and you caught it because you had timeouts and were measuring them. Without timeouts, you'd just see "requests are slow" with no clarity on where.

## The Gotchas

**`time.After` in a long-running select creating timer leaks.** If your goroutine loops and calls `time.After(d)` in each iteration, each call allocates a `Timer` object that isn't garbage collected until `d` elapses. In a tight loop, this accumulates. Use `time.NewTimer` with `t.Reset()` instead.

```go
// WRONG — timer leak in a loop
for {
    select {
    case msg := <-ch:
        handle(msg)
    case <-time.After(5 * time.Second): // new timer every iteration
        log.Println("idle timeout")
        return
    }
}

// RIGHT — single timer, reset each iteration
timer := time.NewTimer(5 * time.Second)
defer timer.Stop()
for {
    select {
    case msg := <-ch:
        if !timer.Stop() {
            <-timer.C
        }
        timer.Reset(5 * time.Second)
        handle(msg)
    case <-timer.C:
        log.Println("idle timeout")
        return
    }
}
```

**Not checking whether context deadline was exceeded in error handling.** `context.DeadlineExceeded` and `context.Canceled` are distinct. Your caller might need to distinguish "we took too long" from "the user cancelled the request." Use `errors.Is(err, context.DeadlineExceeded)` to check specifically.

**Setting the same timeout everywhere.** A 30-second timeout on every call isn't a timeout stack — it's theater. Timeouts should reflect what's reasonable for that specific operation. A DNS lookup should be milliseconds. A database query should be single-digit seconds. A report generation endpoint might be 30 seconds. Calibrate based on your p99 latency metrics, not on a default value you picked once.

**Forgetting `defer cancel()` after `context.WithTimeout`.** If you call `context.WithTimeout` and don't `defer cancel()`, the timer goroutine runs until the deadline — potentially 30+ seconds after the function returns. It's a small leak, but in a high-throughput service it adds up.

## Key Takeaway

Unbounded waits are production bugs. Every network call, database query, and channel receive in your service should have a timeout — either via `context.WithTimeout` on the calling context, or via transport-level configuration on the client. Build a timeout stack: outer layers give more time, inner layers take a defined slice of that budget. Measure your timeout rates in production. When a dependency slows down or stops responding, your services should fail fast with a clear error, not pile up stuck goroutines until the process dies. Timeouts are not pessimism about your dependencies — they're the engineering discipline that keeps your service alive when your dependencies misbehave.

---

← [Previous: Leak Prevention](/post/go/go-concurrency-leak-prevention) | [Course Index](/post/go/) | [Next: errgroup for Structured Concurrency](/post/go/go-concurrency-errgroup) →
