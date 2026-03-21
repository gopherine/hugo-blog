---
title: 'Lesson 1: Goroutine Lifecycle Management — Who owns this goroutine?'
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 1
keywords:
  - Go
  - golang
  - concurrency
  - goroutine lifecycle
  - goroutine leak
  - fire and forget
  - ownership
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-04-06T00:00:00.000Z'
---

Nobody tells you that the first goroutine you "fire and forget" in production is the one that eventually takes down your service at 3 AM. You start it, it runs, and everything looks fine — until requests pile up, memory climbs, and your dashboards turn red because five hundred goroutines are blocked waiting on a channel that'll never receive another value. The problem isn't that goroutines are dangerous. It's that nobody taught you to think about *who owns them*.

## The Problem

Here's code I've seen in nearly every codebase I've touched early in my Go career — including my own:

```go
// WRONG — fire and forget with no exit guarantee
func StartWorker(db *sql.DB) {
    go func() {
        for {
            processNextJob(db)
            time.Sleep(5 * time.Second)
        }
    }()
}
```

The function returns immediately. The goroutine runs forever. There's no way to stop it. No way to know if it's still running. No way to wait for it to finish before your program exits. If `processNextJob` panics, the whole program crashes. If your service gets a shutdown signal, the goroutine just keeps going until the OS kills the process mid-transaction.

This is the "fire and forget" anti-pattern, and it's everywhere. The insidious part is that it *works* — right up until it doesn't.

Here's a subtler version — the kind that causes memory leaks rather than corruption:

```go
// WRONG — goroutine leaks when ctx is cancelled before the send
func fetchData(ctx context.Context, url string) <-chan Result {
    ch := make(chan Result) // unbuffered!
    go func() {
        resp, err := http.Get(url)
        // If the caller already gave up (ctx done), nobody reads from ch.
        // This goroutine is now blocked forever.
        ch <- Result{resp, err}
    }()
    return ch
}
```

The goroutine sends into `ch`. If the caller cancels the context and stops reading, the send blocks forever. The goroutine leaks. Over time, thousands of these pile up — especially under load, when timeouts are common.

## The Idiomatic Way

The fix starts with a mental model, not a code change: **every goroutine needs an owner, and the owner is responsible for its lifecycle**. That means the owner starts it, has a way to stop it, and can wait for it to finish.

```go
// RIGHT — explicit ownership with done channel and WaitGroup
type Worker struct {
    db   *sql.DB
    stop chan struct{}
    wg   sync.WaitGroup
}

func NewWorker(db *sql.DB) *Worker {
    return &Worker{
        db:   db,
        stop: make(chan struct{}),
    }
}

func (w *Worker) Start() {
    w.wg.Add(1)
    go func() {
        defer w.wg.Done()
        for {
            select {
            case <-w.stop:
                return
            default:
                processNextJob(w.db)
                // Use a timer instead of time.Sleep so we can interrupt it
                select {
                case <-time.After(5 * time.Second):
                case <-w.stop:
                    return
                }
            }
        }
    }()
}

func (w *Worker) Stop() {
    close(w.stop)
    w.wg.Wait()
}
```

Notice a few things. First, `wg.Add(1)` happens *before* the `go` statement — never inside the goroutine. If the scheduler runs slowly and `Stop()` is called before the goroutine increments the counter, `wg.Wait()` returns immediately and you have a goroutine still running. Second, `defer w.wg.Done()` is the very first statement in the goroutine body — so it fires even if a panic unwinds the stack (assuming you have a recover somewhere above). Third, the sleep uses a `select` so shutdown doesn't take up to 5 seconds.

For the leaky fetch example, the fix is a buffered channel or a select with the context:

```go
// RIGHT — goroutine can always complete even if caller cancels
func fetchData(ctx context.Context, url string) <-chan Result {
    ch := make(chan Result, 1) // buffered — send never blocks
    go func() {
        req, _ := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
        resp, err := http.DefaultClient.Do(req)
        ch <- Result{resp, err} // always succeeds, buffer absorbs it
    }()
    return ch
}
```

The goroutine completes in bounded time regardless of whether anyone reads from `ch`. The request itself respects context cancellation via `NewRequestWithContext`, so you're not burning a connection either.

## In The Wild

At a previous job, we had a metrics aggregation service. Every incoming request spawned a goroutine to flush metrics to a downstream sink. The goroutine called an HTTP endpoint with a 30-second timeout. Under normal traffic — fine. During a downstream outage, every single goroutine hit its 30-second timeout. But because requests kept coming in, new goroutines kept starting. We had no ceiling, no backpressure, no way to stop them. Memory doubled in about 90 seconds.

The production fix was three things: a semaphore to cap concurrent goroutines, context propagation from the top-level request, and a `WaitGroup` on shutdown so in-flight work could drain cleanly before the process exited.

```go
// RIGHT — bounded concurrency with proper lifecycle
type MetricsFlusher struct {
    sem chan struct{} // acts as a semaphore
    wg  sync.WaitGroup
}

func NewMetricsFlusher(maxConcurrent int) *MetricsFlusher {
    return &MetricsFlusher{
        sem: make(chan struct{}, maxConcurrent),
    }
}

func (f *MetricsFlusher) Flush(ctx context.Context, data []Metric) {
    f.sem <- struct{}{} // acquire
    f.wg.Add(1)
    go func() {
        defer func() {
            <-f.sem // release
            f.wg.Done()
        }()
        flushToSink(ctx, data)
    }()
}

func (f *MetricsFlusher) Drain() {
    f.wg.Wait()
}
```

When the service got a SIGTERM, we called `Drain()` before exiting. Metrics stopped dropping mid-flight. Oncall alerts stopped firing.

## The Gotchas

**Calling `wg.Add` inside the goroutine.** I see this constantly. The race is subtle — `wg.Wait()` can return before the goroutine even starts if the calling goroutine gets scheduled first. Always add before launching.

**Panics bypass `Done`.** If your goroutine panics and you don't have a `recover`, it takes down the whole program — but if you *do* recover somewhere without a `defer wg.Done()`, the `WaitGroup` counter is permanently off and `Wait()` blocks forever. Use `defer wg.Done()` as the very first line, every time.

**Closing a nil channel blocks forever; closing a closed channel panics.** Neither error is obvious from the callsite. If you're using channels for lifecycle signaling, be explicit about initialization and close them exactly once — usually from the owner, never from the worker.

## Key Takeaway

The ownership model isn't bureaucracy — it's the thing that makes your concurrent code debuggable at 3 AM. Before you write `go func()`, ask yourself three questions: who's going to stop this, how will I know when it's actually done, and what happens if it panics? If you can't answer all three, you're writing a leak, not a feature. The runtime won't stop you. Your code reviewer might not catch it. Production will.

---

[Course Index](/series/go-concurrency-masterclass) | [Next → Lesson 2: Cancellation with context.Context](/post/go/go-concurrency-context-cancellation)
