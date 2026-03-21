---
title: 'Lesson 21: Supervisor Patterns — Let it crash, then restart it'
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 21
keywords:
  - Go
  - golang
  - concurrency
  - supervisor
  - worker restart
  - goroutine lifecycle
  - health checks
  - fault tolerance
  - Erlang OTP
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-11-25T00:00:00.000Z'
---

Erlang got famous for the "let it crash" philosophy. The idea is that trying to handle every possible error in every possible place produces fragile, complicated code. It's often better to let a process crash cleanly and have a supervisor restart it. The supervisor knows how to bring the process back to a known-good state. The process itself just needs to do its job and fail fast when something's wrong.

Go isn't Erlang, and goroutines aren't processes. But the philosophy transfers. Long-running goroutines — workers, background processors, event consumers — should be supervised. When one crashes (panics) or exits unexpectedly, something needs to notice and restart it. Without supervision, a panicking worker silently disappears and your processing capacity quietly shrinks.

## The Problem

The most common pattern I see for "background workers" in Go services is fire-and-forget:

```go
// WRONG — goroutine launched once, never monitored
func startBackgroundWorker(db *sql.DB) {
    go func() {
        for {
            if err := processQueue(db); err != nil {
                log.Printf("worker error: %v", err)
                // keep going, hope it recovers...
            }
        }
    }()
}
```

This looks like it handles errors — there's a loop, there's logging. But it doesn't handle panics. If `processQueue` panics, the goroutine is gone. No restart, no alert, no trace in the logs beyond a stack dump that might scroll off your log aggregator before anyone notices. Your queue stops draining. Users wonder why their jobs aren't completing.

The second version adds panic recovery but still doesn't restart:

```go
// WRONG — catches the panic but doesn't restart
go func() {
    defer func() {
        if r := recover(); r != nil {
            log.Printf("worker panicked: %v", r)
            // goroutine exits here, nothing restarts it
        }
    }()
    for {
        processQueue(db)
    }
}()
```

You recovered from the panic — great. But the goroutine still exited. You've exchanged a crash dump for a silent death. Slightly better logging, same outcome.

## The Idiomatic Way

A supervisor function wraps a worker in a restart loop. The worker can crash, panic, return an error — the supervisor notices and starts it again:

```go
// RIGHT — supervisor with exponential backoff restart
func supervise(ctx context.Context, name string, worker func(ctx context.Context) error) {
    const (
        minBackoff = 100 * time.Millisecond
        maxBackoff = 30 * time.Second
    )
    backoff := minBackoff

    for {
        if err := ctx.Err(); err != nil {
            log.Printf("supervisor[%s]: context done, stopping: %v", name, err)
            return
        }

        err := runWorkerSafe(ctx, worker)
        if err == nil || errors.Is(err, context.Canceled) {
            log.Printf("supervisor[%s]: clean exit", name)
            return
        }

        log.Printf("supervisor[%s]: worker failed: %v — restarting in %v", name, err, backoff)
        select {
        case <-time.After(backoff):
        case <-ctx.Done():
            return
        }

        backoff = min(backoff*2, maxBackoff)
    }
}

// runWorkerSafe converts panics into errors so the supervisor can handle them
func runWorkerSafe(ctx context.Context, worker func(ctx context.Context) error) (err error) {
    defer func() {
        if r := recover(); r != nil {
            buf := make([]byte, 4096)
            n := runtime.Stack(buf, false)
            err = fmt.Errorf("panic: %v\n%s", r, buf[:n])
        }
    }()
    return worker(ctx)
}

func min(a, b time.Duration) time.Duration {
    if a < b {
        return a
    }
    return b
}
```

`supervise` does three things: it wraps the worker with panic recovery, it implements exponential backoff between restarts (so a worker that fails instantly doesn't spin at 100% CPU), and it respects context cancellation for clean shutdown. Starting a supervised worker looks like this:

```go
func main() {
    ctx, cancel := context.WithCancel(context.Background())
    defer cancel()

    var wg sync.WaitGroup

    wg.Add(1)
    go func() {
        defer wg.Done()
        supervise(ctx, "queue-processor", processQueueWorker)
    }()

    wg.Add(1)
    go func() {
        defer wg.Done()
        supervise(ctx, "metrics-flusher", flushMetricsWorker)
    }()

    // handle signals for graceful shutdown
    sig := make(chan os.Signal, 1)
    signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
    <-sig
    cancel()
    wg.Wait()
}
```

Cancel the context, all supervisors exit their restart loops after their current workers drain, `wg.Wait()` returns, process exits cleanly. This is the pattern I use in every service with background workers.

## In The Wild

For a pool of supervised workers — where you want N workers running at all times — a slightly more structured supervisor handles the lifecycle:

```go
// RIGHT — supervised worker pool with health tracking
type WorkerPool struct {
    name       string
    size       int
    workerFn   func(ctx context.Context, id int) error
    restarts   atomic.Int64
    ctx        context.Context
    cancel     context.CancelFunc
    wg         sync.WaitGroup
}

func NewWorkerPool(name string, size int, fn func(ctx context.Context, id int) error) *WorkerPool {
    ctx, cancel := context.WithCancel(context.Background())
    return &WorkerPool{
        name:     name,
        size:     size,
        workerFn: fn,
        ctx:      ctx,
        cancel:   cancel,
    }
}

func (p *WorkerPool) Start() {
    for i := 0; i < p.size; i++ {
        workerID := i
        p.wg.Add(1)
        go func() {
            defer p.wg.Done()
            name := fmt.Sprintf("%s/worker-%d", p.name, workerID)
            supervise(p.ctx, name, func(ctx context.Context) error {
                return p.workerFn(ctx, workerID)
            })
        }()
    }
}

func (p *WorkerPool) Stop() {
    p.cancel()
    p.wg.Wait()
}

func (p *WorkerPool) Restarts() int64 {
    return p.restarts.Load()
}
```

Expose `Restarts()` as a health metric. If your restart counter is climbing, something is wrong with a worker — panics, downstream connectivity, OOM. A rising restart rate is a signal that's worth alerting on before it becomes a user-visible outage.

For health checks, expose a simple endpoint that reports whether all workers are running:

```go
func (p *WorkerPool) HealthHandler(w http.ResponseWriter, r *http.Request) {
    restarts := p.Restarts()
    if restarts > 100 { // threshold depends on your SLO
        w.WriteHeader(http.StatusServiceUnavailable)
        fmt.Fprintf(w, `{"healthy":false,"restarts":%d}`, restarts)
        return
    }
    fmt.Fprintf(w, `{"healthy":true,"restarts":%d}`, restarts)
}
```

Kubernetes liveness probes hitting this endpoint will restart the pod if the workers are thrashing. That's the right escalation path: supervisor handles transient failures, orchestrator handles persistent ones.

## The Gotchas

**Infinite restart loops on configuration errors.** If a worker fails immediately because of a bad environment variable or a missing dependency, the supervisor will restart it in a tight loop (moderated by backoff). This is better than a single crash, but you want your alerts to fire on restarts so you catch configuration bugs before they become incidents. Always log the reason for the restart, and set a maximum restart count if fast-failing the whole process is preferable to a degraded restart loop:

```go
// Consider adding a max-restarts policy for fast-fail on persistent errors
if restartCount > maxRestarts {
    log.Printf("supervisor[%s]: exceeded max restarts (%d), giving up", name, maxRestarts)
    cancel() // shut down the whole supervisor
    return
}
```

**Isolation between workers.** Workers in a pool should not share mutable state without synchronization. A worker panicking due to a data race can corrupt shared state that other workers rely on, making recovery impossible even after restart. Design worker functions to be independent — they read from shared sources (database, queue) but maintain their own local state. Shared, protected state (a cache, a counter) should be accessed through synchronization primitives, not through direct field access.

**Panic recovery doesn't help with goroutine leaks inside the worker.** If your worker spawns its own goroutines and those goroutines are blocking on channels when the worker panics, the inner goroutines are now leaked. The supervisor restarts the worker, which starts a new set of inner goroutines — and the old leaked ones are still there. Workers need to propagate context cancellation to everything they spawn, so cleanup happens on exit:

```go
// RIGHT — worker passes context to everything it spawns
func myWorker(ctx context.Context) error {
    // pass ctx to all sub-goroutines and sub-calls
    result, err := fetchFromDB(ctx, query)
    if err != nil {
        return fmt.Errorf("db fetch: %w", err)
    }
    return processResult(ctx, result)
}
```

**The supervisor doesn't fix a fundamentally broken worker.** Supervision handles transient failures — network blips, temporary resource exhaustion, the occasional nil pointer. It doesn't fix a worker that's intrinsically broken. If `processQueueWorker` has a bug that causes it to panic on 30% of inputs, the supervisor will restart it faithfully and it'll panic again. Supervision buys you resilience against the environment; it doesn't substitute for fixing the code.

## Key Takeaway

Supervised goroutines — wrapped in a restart loop with panic recovery and exponential backoff — are more reliable than bare goroutines. Erlang taught the industry that "let it crash, then restart it" produces simpler, more resilient systems than trying to handle every failure in place. The Go equivalent is `runWorkerSafe` to convert panics to errors, a restart loop with backoff to handle transient failures, and context cancellation for clean shutdown. Expose restart counts as a health metric and alert on them — a rising restart rate is an early warning signal. Workers should be isolated from each other and must propagate context cancellation to everything they spawn, so the supervisor can actually clean up on restart.

---

← [Previous: Profiling Contention](/post/go/go-concurrency-contention-profiling) | [Course Index](/post/go/) | [Next: Rate Limiting Patterns](/post/go/go-concurrency-rate-limiting) →
