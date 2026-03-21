---
title: 'Lesson 12: Leak Prevention — Every goroutine must have an exit'
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 12
keywords:
  - Go
  - golang
  - concurrency
  - goroutine leak
  - leak prevention
  - blocked send
  - blocked receive
  - ticker leak
  - goleak
  - runtime.NumGoroutine
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-08-15T00:00:00.000Z'
---

Goroutine leaks are the memory leaks of concurrent Go. They're slow, invisible, and tend to surface only under load — or worse, only after days of continuous running when the service has accumulated tens of thousands of stuck goroutines. I've debugged two production incidents that traced back to leaks in code that had been in production for months, completely unnoticed during normal load.

The root cause is almost always the same: someone started a goroutine and didn't give it a way to exit. Not a way to exit eventually — a way to exit in every possible code path.

## The Problem

The most classic goroutine leak is a blocked channel receive. You start a goroutine to listen for results, but the sender exits early due to an error and never sends — so the goroutine waits forever.

```go
// WRONG — goroutine leaks when fetch returns an error
func fetchAsync(url string) <-chan string {
    ch := make(chan string)
    go func() {
        resp, err := http.Get(url)
        if err != nil {
            return // exits without sending — channel never closed, receiver blocks forever
        }
        defer resp.Body.Close()
        body, _ := io.ReadAll(resp.Body)
        ch <- body
    }()
    return ch
}

// caller
func handler(w http.ResponseWriter, r *http.Request) {
    result := fetchAsync("https://api.example.com/data")
    // if fetchAsync leaked, this blocks forever
    w.Write([]byte(<-result))
}
```

Every failed HTTP request leaves one goroutine permanently blocked on `ch <- body` — no wait, it leaves the *caller* blocked on `<-result`, and the *goroutine* exits without signaling. Either way, someone is stuck. Run a few hundred of these requests with a flaky upstream and you've got hundreds of blocked goroutines. The HTTP server's connection pool fills up. New requests pile up. The service stops responding.

Blocked send is the mirror image:

```go
// WRONG — goroutine leaks when nobody reads from the channel
func startEventStream(events <-chan Event) {
    processed := make(chan Event) // unbuffered, no buffer
    go func() {
        for e := range events {
            processed <- e // blocks if nobody is reading
        }
    }()
    // processed is returned nowhere and read nowhere
    // goroutine is blocked on 'processed <- e' forever
}
```

Ticker leaks are particularly sneaky because they're not obviously channel-related:

```go
// WRONG — ticker never stopped, goroutine runs forever
func startHeartbeat(id string) {
    go func() {
        ticker := time.NewTicker(5 * time.Second)
        for {
            <-ticker.C
            sendHeartbeat(id)
        }
        // this loop has no exit condition
        // goroutine lives until process death
    }()
}
```

Call `startHeartbeat` for each connected client. Clients disconnect. The goroutines keep running. After 24 hours on a busy server, you have thousands of heartbeat goroutines pinging for clients that left hours ago.

## The Idiomatic Way

Every goroutine needs an exit path. The exit path is usually one of: the input channel closes, the context is cancelled, or an explicit done channel is closed.

```go
// RIGHT — leak-free fetch using a result struct and closed channel
func fetchAsync(ctx context.Context, url string) <-chan fetchResult {
    type fetchResult struct {
        body string
        err  error
    }
    ch := make(chan fetchResult, 1) // buffered: sender never blocks
    go func() {
        req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
        if err != nil {
            ch <- fetchResult{err: err}
            return
        }
        resp, err := http.DefaultClient.Do(req)
        if err != nil {
            ch <- fetchResult{err: err}
            return
        }
        defer resp.Body.Close()
        body, err := io.ReadAll(resp.Body)
        ch <- fetchResult{body: string(body), err: err}
    }()
    return ch
}
```

Two fixes here. First, the channel is buffered with capacity 1 — the goroutine can always send its result and exit, even if the caller has already abandoned the channel due to context cancellation. Second, every return path sends a value. The goroutine is guaranteed to exit.

For the ticker leak, the fix is to pass a context and stop the ticker in a select:

```go
// RIGHT — heartbeat goroutine exits when context is cancelled
func startHeartbeat(ctx context.Context, id string) {
    go func() {
        ticker := time.NewTicker(5 * time.Second)
        defer ticker.Stop() // always stop the ticker to free resources
        for {
            select {
            case <-ticker.C:
                sendHeartbeat(id)
            case <-ctx.Done():
                log.Printf("heartbeat %s stopping", id)
                return
            }
        }
    }()
}
```

When the client disconnects, cancel the context. The goroutine exits cleanly. The ticker is stopped. No leak.

Now let's talk about detecting leaks in tests — because "review the code" isn't enough. Use `goleak`:

```go
// RIGHT — using goleak to catch leaks in tests
import (
    "testing"
    "go.uber.org/goleak"
)

func TestFetchAsync(t *testing.T) {
    defer goleak.VerifyNone(t)

    ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
    defer cancel()

    ch := fetchAsync(ctx, "https://httpbin.org/get")
    select {
    case result := <-ch:
        if result.err != nil {
            t.Fatal(result.err)
        }
    case <-ctx.Done():
        t.Fatal("timeout")
    }
    // goleak.VerifyNone runs here — if any goroutines are still running
    // that weren't running before the test, the test fails
}
```

`goleak.VerifyNone` checks that no new goroutines are running at the end of the test compared to the start. It catches exactly the patterns I described — any goroutine that wasn't cleaned up will be flagged. I've added this to every package in our codebase that uses goroutines, and it has caught real bugs before they hit production.

For monitoring in production, `runtime.NumGoroutine()` gives you the current goroutine count. Expose it as a metric:

```go
// RIGHT — expose goroutine count as a Prometheus gauge
import (
    "runtime"
    "github.com/prometheus/client_golang/prometheus"
    "github.com/prometheus/client_golang/prometheus/promauto"
)

var goroutineCount = promauto.NewGaugeFunc(
    prometheus.GaugeOpts{
        Name: "go_goroutines_active",
        Help: "Number of goroutines currently active",
    },
    func() float64 {
        return float64(runtime.NumGoroutine())
    },
)
```

Graph it over time. A goroutine count that grows monotonically and never comes back down is a leak. A count that spikes under load and returns to baseline is normal. Once you see the metric, you'll never want to run without it.

## In The Wild

At a previous company we ran a WebSocket-based notification service. Each connected client had a goroutine reading from a personal event channel. When clients disconnected, the cleanup code called `conn.Close()` — but didn't cancel the goroutine's context. The goroutines kept running, blocked on `<-eventCh`, waiting for events for a client that was long gone.

The leak was invisible at first. The service restarted every night. But as we grew, the nightly restart wasn't enough to mask the accumulated leaks within a single day. Goroutine count climbed from ~500 at startup to ~12,000 by end of business. Memory usage followed. By the time we noticed, requests were taking 3–4 seconds due to scheduler pressure.

The fix was adding a `cancel` function to every connection and calling it in the disconnect handler:

```go
type Connection struct {
    conn   *websocket.Conn
    ctx    context.Context
    cancel context.CancelFunc
    events chan Event
}

func NewConnection(parentCtx context.Context, conn *websocket.Conn) *Connection {
    ctx, cancel := context.WithCancel(parentCtx)
    c := &Connection{conn: conn, ctx: ctx, cancel: cancel, events: make(chan Event, 32)}
    go c.readLoop()
    return c
}

func (c *Connection) readLoop() {
    defer c.cancel() // if readLoop exits for any reason, cancel context
    for {
        select {
        case event := <-c.events:
            if err := c.conn.WriteJSON(event); err != nil {
                return
            }
        case <-c.ctx.Done():
            return
        }
    }
}

func (c *Connection) Disconnect() {
    c.cancel() // signal all goroutines for this connection to exit
    c.conn.Close()
}
```

After the fix, goroutine count stayed flat after client disconnects. The incident never recurred.

## The Gotchas

**Goroutines started in `init()` or package-level vars.** These run for the lifetime of the process and can't be cancelled — which is usually fine, but it means `goleak` needs to know about them. Use `goleak.IgnoreTopFunction` to exclude known-good goroutines from leak checks.

**`time.After` in a goroutine select.** `time.After(d)` creates a timer that fires after `d` but is never garbage-collected until it fires. In a long-running goroutine that frequently selects on `time.After`, you accumulate timer objects. Use `time.NewTimer` with an explicit `t.Stop()` and `t.Reset()` instead.

**Goroutines that block on `sync.Mutex` or `sync.WaitGroup`.** These aren't channel leaks, but they're leaks. If a `WaitGroup` counter goes negative or never reaches zero because a goroutine panicked without calling `Done()`, the waiter blocks forever. Always use `defer wg.Done()` — not `wg.Done()` at the end — so that panics don't prevent it.

**HTTP response body not closed.** Not a goroutine leak per se, but reading from `resp.Body` in a goroutine and forgetting `defer resp.Body.Close()` leaks the underlying TCP connection. The connection eventually times out, but until it does, it holds a file descriptor.

## Key Takeaway

The rule is absolute: every goroutine you start must have at least one guaranteed exit path. Not a "maybe it exits when the channel closes" path — a guaranteed path that covers errors, context cancellation, and normal completion. Add `goleak` to your test suite to make leak detection automatic. Add `runtime.NumGoroutine()` to your metrics to catch leaks that only appear under production load. A goroutine that leaks in production is as serious as a nil pointer dereference — it just kills you slowly instead of immediately.

---

← [Previous: Graceful Shutdown](/post/go/go-concurrency-graceful-shutdown) | [Course Index](/post/go/) | [Next: Timeouts Everywhere](/post/go/go-concurrency-timeouts) →
