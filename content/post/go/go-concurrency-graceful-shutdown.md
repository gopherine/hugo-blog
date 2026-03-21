---
title: 'Lesson 11: Graceful Shutdown — Stop accepting, finish what you started'
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 11
keywords:
  - Go
  - golang
  - concurrency
  - graceful shutdown
  - signal.NotifyContext
  - HTTP server shutdown
  - worker drain
  - context cancellation
  - SIGTERM
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-08-03T00:00:00.000Z'
---

Most Go services handle startup carefully and shutdown carelessly. The startup code has retries, health checks, dependency validation. The shutdown code is `os.Exit(0)` or — if the developer was feeling generous — nothing at all, just letting the process get killed. That's how you get dropped HTTP connections, half-written database records, uncommitted Kafka offsets, and on-call alerts at 3am.

Graceful shutdown has one principle: stop accepting new work, finish what you already started. That's it. But implementing it correctly requires knowing the shutdown order, wiring up OS signals properly, and draining workers before pulling the plug.

## The Problem

The simplest broken shutdown looks like this:

```go
// WRONG — kills the process immediately, in-flight requests dropped
func main() {
    mux := http.NewServeMux()
    mux.HandleFunc("/process", processHandler)

    srv := &http.Server{Addr: ":8080", Handler: mux}
    if err := srv.ListenAndServe(); err != nil {
        log.Fatal(err)
    }
}
```

When Kubernetes sends SIGTERM, or a human hits Ctrl+C, `ListenAndServe` doesn't return gracefully — the process dies instantly. Any in-flight HTTP request gets a connection reset. If `processHandler` was halfway through a database write, that transaction is now abandoned. If it was producing to Kafka, that message may be partially written depending on your producer configuration.

A slightly better but still wrong approach — catching the signal but not waiting for in-flight work:

```go
// STILL WRONG — catches signal, but shuts down before in-flight goroutines finish
func main() {
    mux := http.NewServeMux()
    mux.HandleFunc("/", handler)

    srv := &http.Server{Addr: ":8080", Handler: mux}

    sigCh := make(chan os.Signal, 1)
    signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

    go func() {
        if err := srv.ListenAndServe(); err != http.ErrServerClosed {
            log.Fatal(err)
        }
    }()

    <-sigCh
    // Server is shut down, but background workers are still running!
    srv.Shutdown(context.Background())
    // process exits, background goroutines killed mid-work
}
```

The HTTP server drains, but background goroutines — message consumers, scheduled jobs, cache warmers — get killed without warning. Their in-flight work is lost.

## The Idiomatic Way

The modern Go approach uses `signal.NotifyContext`, which creates a context that cancels on the specified signals. Pair it with `http.Server.Shutdown` and a `WaitGroup` for background workers.

```go
// RIGHT — full graceful shutdown for HTTP server + background workers
func main() {
    // This context is cancelled when SIGINT or SIGTERM is received
    ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
    defer stop()

    mux := http.NewServeMux()
    mux.HandleFunc("/process", processHandler)

    srv := &http.Server{
        Addr:         ":8080",
        Handler:      mux,
        ReadTimeout:  5 * time.Second,
        WriteTimeout: 10 * time.Second,
        IdleTimeout:  120 * time.Second,
    }

    // Track background workers
    var wg sync.WaitGroup

    // Start background worker
    wg.Add(1)
    go func() {
        defer wg.Done()
        runBackgroundWorker(ctx)
    }()

    // Start HTTP server
    serverErr := make(chan error, 1)
    go func() {
        log.Println("server starting on :8080")
        if err := srv.ListenAndServe(); err != http.ErrServerClosed {
            serverErr <- err
        }
    }()

    // Wait for shutdown signal or server error
    select {
    case <-ctx.Done():
        log.Println("shutdown signal received")
    case err := <-serverErr:
        log.Printf("server error: %v", err)
        stop() // cancel context so workers shut down too
    }

    // Phase 1: stop accepting new HTTP requests, drain in-flight ones
    shutdownCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
    defer cancel()
    if err := srv.Shutdown(shutdownCtx); err != nil {
        log.Printf("HTTP shutdown error: %v", err)
    }
    log.Println("HTTP server stopped")

    // Phase 2: wait for background workers to finish
    workerDone := make(chan struct{})
    go func() {
        wg.Wait()
        close(workerDone)
    }()

    select {
    case <-workerDone:
        log.Println("all workers stopped")
    case <-time.After(60 * time.Second):
        log.Println("timeout waiting for workers — forcing exit")
    }

    log.Println("shutdown complete")
}
```

The shutdown has two explicit phases. Phase 1: stop the HTTP server. This stops accepting new connections and waits for in-flight requests to complete, up to the `shutdownCtx` timeout. Phase 2: wait for background workers. The `ctx` passed to `runBackgroundWorker` was already cancelled when the signal arrived, so the worker is already winding down — we're just waiting for it to finish cleanly.

The timeout on Phase 2 matters. You don't want a stuck worker to prevent shutdown indefinitely. 60 seconds is generous for most workloads; tune it to your actual drain time.

Here's how `runBackgroundWorker` should look — always checking context:

```go
// RIGHT — worker that respects context cancellation for clean drain
func runBackgroundWorker(ctx context.Context) {
    log.Println("worker started")
    for {
        select {
        case <-ctx.Done():
            log.Println("worker draining final batch...")
            // finish current unit of work if needed, then exit
            return
        default:
        }

        msg, err := fetchNextMessage(ctx)
        if err != nil {
            if ctx.Err() != nil {
                return // context cancelled, clean exit
            }
            log.Printf("fetch error: %v", err)
            time.Sleep(time.Second)
            continue
        }

        // Process message — use ctx so in-flight I/O also cancels
        if err := processMessage(ctx, msg); err != nil {
            log.Printf("process error: %v", err)
        }
    }
}
```

The worker checks `ctx.Done()` at the top of each loop iteration. Calls to `fetchNextMessage` and `processMessage` also receive `ctx`, so their network calls and database queries respect the cancellation too.

## In The Wild

The shutdown order problem is subtle and bites teams that have multiple interdependent components. Here's a real scenario: an HTTP server that reads from a Redis queue, processes jobs, writes results to PostgreSQL.

Wrong order: shut down the database first. Now the HTTP server and workers are still running but can't write results. They log errors, retry, fail. Chaos.

Right order:

```go
func shutdown(ctx context.Context, srv *http.Server, workers *WorkerPool, db *sql.DB, redis *redis.Client) {
    // 1. Stop accepting new HTTP traffic
    httpCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
    defer cancel()
    srv.Shutdown(httpCtx)

    // 2. Stop the worker pool from picking up new jobs
    workers.Stop()

    // 3. Wait for in-flight jobs to complete (they need db + redis)
    workers.Wait(30 * time.Second)

    // 4. Close data stores — all writers are done
    db.Close()
    redis.Close()

    log.Println("clean shutdown complete")
}
```

The principle: shut down in reverse dependency order. HTTP first (it depends on workers), workers second (they depend on datastores), datastores last. Reversing this order means some component tries to use a dependency that's already gone.

## The Gotchas

**Not setting a timeout on `srv.Shutdown`.** `srv.Shutdown(context.Background())` will wait forever if a client is holding a connection open (looking at you, HTTP/2 long-polls). Always pass a context with a deadline.

**Calling `stop()` from `signal.NotifyContext` after you're done with it.** `stop()` releases the signal binding in the OS. If you `defer stop()` and don't call it until process exit, signals received after the initial one won't be handled. Call `stop()` as soon as you've received the signal, before starting shutdown logic.

**Starting new goroutines after shutdown begins.** This is sneaky. A request handler that's still in-flight might spawn a background goroutine for "fire and forget" work. That goroutine now outlives the controlled shutdown. The fix: any fire-and-forget goroutine should receive the main context and exit when it's cancelled.

**Ignoring the HTTP server's error return.** If `srv.ListenAndServe()` returns something other than `http.ErrServerClosed`, that's a real error — port already in use, TLS config invalid, etc. Don't ignore it. The pattern `if err != http.ErrServerClosed { log.Fatal(err) }` is idiomatic.

## Key Takeaway

Graceful shutdown is not optional for production services. The shape is always the same: catch the OS signal with `signal.NotifyContext`, stop accepting new work (HTTP server shutdown), wait for in-flight work to finish (worker `WaitGroup`), shut down dependencies in reverse order. Put timeouts on every wait — not because you expect workers to hang, but because when they do, you want the process to exit anyway rather than get stuck. Kubernetes will SIGKILL you after its `terminationGracePeriodSeconds` regardless; make sure your in-flight work is done before that timer runs out.

---

← [Previous: Pipelines and Stage Isolation](/post/go/go-concurrency-pipelines) | [Course Index](/post/go/) | [Next: Leak Prevention](/post/go/go-concurrency-leak-prevention) →
