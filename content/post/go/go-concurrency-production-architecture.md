---
title: 'Lesson 28: Production Concurrency Architecture — Putting it all together'
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 28
keywords:
  - Go
  - golang
  - concurrency
  - production architecture
  - worker pool
  - retry
  - graceful shutdown
  - metrics
  - cancellation
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2026-02-20T00:00:00.000Z'
---

This is the lesson I wish existed when I started. Not "here's how channels work" or "here's a simple worker pool" — but the full picture: how you take everything you've learned about goroutines, channels, contexts, rate limiting, idempotency, observability, and graceful shutdown and assemble it into a service that actually belongs in production. One that handles failures, recovers gracefully, doesn't leak resources, and gives you the visibility to understand what it's doing.

I'm going to build a concrete example: an order processing service. It receives order events (from an HTTP endpoint or a message queue), processes them with a worker pool, retries transient failures, records metrics, and shuts down cleanly when it receives a signal. This isn't toy code. It's close to what I'd write for a real service. By the end you'll have a template you can adapt.

## The Problem

A service assembled without thinking about the full lifecycle looks like this:

```go
// WRONG — naive service with none of the production concerns addressed
func main() {
    // No signal handling
    // No graceful shutdown
    // No metrics
    // No rate limiting
    // No idempotency
    // No retry logic
    // No goroutine leak prevention

    jobs := make(chan Job)

    go func() {
        http.HandleFunc("/orders", func(w http.ResponseWriter, r *http.Request) {
            var job Job
            json.NewDecoder(r.Body).Decode(&job)
            jobs <- job // blocks if queue is full — holds goroutine
            w.WriteHeader(http.StatusAccepted)
        })
        http.ListenAndServe(":8080", nil)
    }()

    for job := range jobs {
        processOrder(job) // serial, no worker pool, no retry
    }
}
```

This service is fragile in every dimension. It can't shut down cleanly. It doesn't know if it's processing 10 orders per second or 10,000. It retries nothing. It reports nothing. If `processOrder` panics, the whole service dies. And the HTTP handler blocks waiting for the queue to have space, which is terrible for latency.

## The Idiomatic Way

Let me build this piece by piece, then show the full assembly.

**The worker pool with retry:**

```go
// RIGHT — worker pool with exponential backoff retry
type Worker struct {
    id      int
    jobs    <-chan Job
    results chan<- JobResult
    metrics *Metrics
}

func (w *Worker) Run(ctx context.Context) {
    for {
        select {
        case job, ok := <-w.jobs:
            if !ok {
                return // channel closed, clean exit
            }
            w.processWithRetry(ctx, job)
        case <-ctx.Done():
            return
        }
    }
}

func (w *Worker) processWithRetry(ctx context.Context, job Job) {
    const maxAttempts = 3
    backoff := 100 * time.Millisecond

    for attempt := 1; attempt <= maxAttempts; attempt++ {
        start := time.Now()
        err := processOrder(ctx, job)
        duration := time.Since(start)

        if err == nil {
            w.metrics.JobSuccess(duration)
            return
        }

        if !isTransient(err) {
            // Permanent failure — don't retry
            w.metrics.JobFailure("permanent")
            slog.Error("permanent job failure",
                "worker_id", w.id,
                "job_id", job.ID,
                "error", err,
            )
            return
        }

        if attempt == maxAttempts {
            w.metrics.JobFailure("exhausted_retries")
            slog.Error("job failed after all retries",
                "worker_id", w.id,
                "job_id", job.ID,
                "attempts", attempt,
            )
            return
        }

        // Jittered exponential backoff
        jitter := time.Duration(rand.Int63n(int64(backoff / 2)))
        sleep := backoff + jitter
        slog.Warn("job failed, retrying",
            "worker_id", w.id,
            "job_id", job.ID,
            "attempt", attempt,
            "backoff_ms", sleep.Milliseconds(),
        )

        select {
        case <-time.After(sleep):
            backoff *= 2
        case <-ctx.Done():
            return // context cancelled during backoff — clean exit
        }
    }
}
```

**The rate-limited job intake:**

```go
// RIGHT — HTTP handler with rate limiting and load shedding
type Intake struct {
    queue   chan Job
    limiter *rate.Limiter
    sem     chan struct{} // load shedder semaphore
}

func NewIntake(queueSize, maxConcurrent int, rateLimit rate.Limit) *Intake {
    return &Intake{
        queue:   make(chan Job, queueSize),
        limiter: rate.NewLimiter(rateLimit, int(rateLimit)),
        sem:     make(chan struct{}, maxConcurrent),
    }
}

func (i *Intake) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    // Rate limiting
    if !i.limiter.Allow() {
        w.Header().Set("Retry-After", "1")
        http.Error(w, "rate limit exceeded", http.StatusTooManyRequests)
        return
    }

    // Load shedding
    select {
    case i.sem <- struct{}{}:
        defer func() { <-i.sem }()
    default:
        w.Header().Set("Retry-After", "2")
        http.Error(w, "service overloaded", http.StatusServiceUnavailable)
        return
    }

    // Idempotency check
    idempotencyKey := r.Header.Get("Idempotency-Key")
    if idempotencyKey == "" {
        http.Error(w, "Idempotency-Key header required", http.StatusBadRequest)
        return
    }

    var job Job
    if err := json.NewDecoder(r.Body).Decode(&job); err != nil {
        http.Error(w, "bad request", http.StatusBadRequest)
        return
    }
    job.IdempotencyKey = idempotencyKey

    // Non-blocking enqueue
    select {
    case i.queue <- job:
        w.WriteHeader(http.StatusAccepted)
        json.NewEncoder(w).Encode(map[string]string{"status": "queued", "id": job.ID})
    default:
        w.Header().Set("Retry-After", "1")
        http.Error(w, "queue full", http.StatusServiceUnavailable)
    }
}
```

## In The Wild

Now the full assembly — main.go that wires it all together with signal handling and graceful shutdown:

```go
// RIGHT — full production service assembly
func main() {
    // Structured logger from day one
    slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
        Level: slog.LevelInfo,
    })))

    // Metrics — goroutine count, queue depth, job rates
    metrics := NewMetrics()
    prometheus.MustRegister(metrics)

    // Enable profiling on internal port
    runtime.SetMutexProfileFraction(10)
    runtime.SetBlockProfileRate(1000)
    go func() {
        slog.Info("debug server starting", "addr", ":6060")
        http.ListenAndServe(":6060", nil) // pprof registers on DefaultServeMux
    }()

    // Root context — cancelled on shutdown signal
    ctx, cancel := signal.NotifyContext(context.Background(),
        syscall.SIGINT, syscall.SIGTERM,
    )
    defer cancel()

    // Job intake
    intake := NewIntake(
        1000,              // queue buffer
        200,               // max concurrent HTTP requests
        rate.Limit(500),   // 500 req/s rate limit
    )

    // Worker pool
    const numWorkers = 20
    var wg sync.WaitGroup
    for i := 0; i < numWorkers; i++ {
        wg.Add(1)
        w := &Worker{id: i, jobs: intake.queue, metrics: metrics}
        go func() {
            defer wg.Done()
            w.Run(ctx)
        }()
    }

    // Metrics collection goroutine
    go func() {
        ticker := time.NewTicker(5 * time.Second)
        defer ticker.Stop()
        for {
            select {
            case <-ctx.Done():
                return
            case <-ticker.C:
                metrics.GoroutineCount.Set(float64(runtime.NumGoroutine()))
                metrics.QueueDepth.Set(float64(len(intake.queue)))
            }
        }
    }()

    // HTTP server
    mux := http.NewServeMux()
    mux.Handle("/orders", intake)
    mux.Handle("/metrics", promhttp.Handler())
    mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
        w.WriteHeader(http.StatusOK)
    })

    srv := &http.Server{
        Addr:         ":8080",
        Handler:      mux,
        ReadTimeout:  10 * time.Second,
        WriteTimeout: 30 * time.Second,
        IdleTimeout:  60 * time.Second,
    }

    // Start server
    go func() {
        slog.Info("HTTP server starting", "addr", srv.Addr)
        if err := srv.ListenAndServe(); !errors.Is(err, http.ErrServerClosed) {
            slog.Error("HTTP server error", "error", err)
            cancel() // trigger shutdown if server fails
        }
    }()

    // Wait for shutdown signal
    <-ctx.Done()
    slog.Info("shutdown signal received, draining...")

    // Phase 1: stop accepting new requests
    shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 30*time.Second)
    defer shutdownCancel()

    if err := srv.Shutdown(shutdownCtx); err != nil {
        slog.Error("HTTP server shutdown error", "error", err)
    }
    slog.Info("HTTP server stopped")

    // Phase 2: close the job queue, wait for workers to drain
    close(intake.queue)
    workerDone := make(chan struct{})
    go func() {
        wg.Wait()
        close(workerDone)
    }()

    select {
    case <-workerDone:
        slog.Info("all workers finished cleanly")
    case <-time.After(60 * time.Second):
        slog.Warn("workers did not finish within timeout, forcing shutdown")
    }

    slog.Info("shutdown complete")
}
```

## The Gotchas

**Shutdown order matters.** Always stop the input source first (HTTP server stops accepting), then drain the queue, then wait for workers. If you wait for workers before stopping the HTTP server, new jobs keep arriving and the workers never drain. Shutdown is a pipeline: seal the input, drain the buffer, stop the processors.

**The worker count is a knob, not a constant.** 20 workers hardcoded into main works for a prototype. In production, expose it as a configuration variable and tune it based on your workload profile. CPU-bound work benefits from `runtime.NumCPU()` workers. IO-bound work (waiting on DB, HTTP) can use far more — 100 or more — because most workers are blocked waiting on IO, not consuming CPU.

**Context cancellation is not instantaneous.** When you cancel the root context, workers don't stop immediately — they check `ctx.Done()` at the next `select`. If your `processOrder` function makes a long DB call without propagating the context, the worker might be stuck in that call for its full timeout even after shutdown is requested. This is why every function in your call chain must accept and respect a context. It's not optional boilerplate.

**The WaitGroup and channel close must be coordinated.** Closing `intake.queue` signals workers to drain remaining jobs and exit. But if a worker is between the `case job, ok := <-w.jobs` and actually doing the work when you close the channel, the job is still in flight. The WaitGroup ensures you wait for all of that to complete. Never `os.Exit` after `Shutdown` without waiting for the WaitGroup.

**Prometheus metrics need registration before use.** Call `prometheus.MustRegister` at startup, not inside handlers. Double-registration panics. Use `prometheus.MustRegister` in `init()` or early in `main()`, and use descriptive metric names with the service name as a prefix (`orders_jobs_processed_total`, not `processed_total`).

## Key Takeaway

A production-grade concurrent service is not any one pattern — it's the composition of all of them. Rate limiting protects the input. A buffered queue decouples intake from processing. A worker pool provides concurrency. Retry with jitter handles transient failures. Context propagation enables clean cancellation. A phased shutdown (stop input, drain queue, wait for workers) ensures no work is lost. Metrics and pprof give you visibility. Structured logging with IDs gives you debuggability. Idempotency keys make retries safe.

None of these are individually complicated. The art is in assembling them correctly and understanding how they interact. I've tried to give you a template that gets the interactions right — not just the happy path, but the shutdown path, the error paths, and the overload paths. Take this template, adapt the `processOrder` logic to your domain, tune the worker count and queue size to your load profile, and you'll have a service that behaves well under pressure.

That's the whole course. Go build something.

---

← [Lesson 27: Idempotency in Concurrent Systems](/post/go/go-concurrency-idempotency) | [Course Index](/series/go-concurrency-masterclass) | 🎓 Course Complete!
