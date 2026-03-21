---
title: "Lesson 26: Safe Background Jobs in Web Servers — Don''t spawn goroutines in handlers"
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 26
keywords:
  - Go
  - golang
  - concurrency
  - background jobs
  - goroutine leak
  - errgroup
  - graceful shutdown
  - web server
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2026-01-23T00:00:00.000Z'
---

The first time I saw `go func()` inside an HTTP handler, I thought it was clever. Fire off the slow work in the background, respond to the client fast, everybody wins. Then I watched what happened when we deployed a new version: the server started shutting down, half the background goroutines got killed mid-operation, we had partial writes in the database and no way to know which ones had completed. The "clever" optimization had created a correctness nightmare. The real problem wasn't the goroutine — it was that it was invisible to the server's shutdown lifecycle.

Web servers have a lifecycle: start, handle requests, shut down. Graceful shutdown means "finish what you're doing before you exit." If you spawn goroutines from handlers and don't track them, those goroutines are invisible to the shutdown logic. The server thinks it's done when the last request handler returns. Your background goroutines think they have all the time in the world. The OS disagrees, and kills the process.

## The Problem

The temptation is real, especially for "fire and forget" work:

```go
// WRONG — goroutine spawned in handler, invisible to shutdown
func handleCreateOrder(w http.ResponseWriter, r *http.Request) {
    order, err := parseOrder(r)
    if err != nil {
        http.Error(w, "bad request", http.StatusBadRequest)
        return
    }

    // Save to DB
    if err := db.CreateOrder(r.Context(), order); err != nil {
        http.Error(w, "internal error", http.StatusInternalServerError)
        return
    }

    // Respond to client immediately
    w.WriteHeader(http.StatusAccepted)
    json.NewEncoder(w).Encode(order)

    // Do async work — but who's watching this goroutine?
    go func() {
        sendConfirmationEmail(order)      // might fail silently
        updateInventory(order)            // might get killed mid-write
        triggerFulfillmentWebhook(order)  // never runs if server shuts down first
    }()
}
```

There are four problems here. First, the goroutine is invisible — the server has no idea it exists, so graceful shutdown won't wait for it. Second, if `sendConfirmationEmail` panics, the whole server crashes — goroutine panics that aren't recovered propagate to the runtime. Third, if the server shuts down while the goroutine is in `updateInventory`, you get a partial write. Fourth, there's no way to know if `triggerFulfillmentWebhook` ran successfully — if it didn't, nobody will retry it.

The other common mistake is using `server.RegisterOnShutdown` incorrectly:

```go
// WRONG — RegisterOnShutdown with no way to wait for goroutines
var backgroundTasks sync.WaitGroup

func handleRequest(w http.ResponseWriter, r *http.Request) {
    backgroundTasks.Add(1)
    go func() {
        defer backgroundTasks.Done()
        doBackgroundWork()
    }()
    w.WriteHeader(http.StatusAccepted)
}

func main() {
    srv := &http.Server{Addr: ":8080", Handler: mux}

    srv.RegisterOnShutdown(func() {
        backgroundTasks.Wait() // waits, but...
    })

    // Problem: Shutdown() waits for active connections, not for shutdown callbacks
    // to finish. The process might exit before backgroundTasks.Wait() returns.
}
```

`RegisterOnShutdown` callbacks run concurrently with the server draining connections, and `Shutdown` returns as soon as all connections close — not when the shutdown callbacks finish. So `backgroundTasks.Wait()` might still be running when `Shutdown` returns and your code calls `os.Exit`.

## The Idiomatic Way

The right approach is to own background work at the server level, not the handler level. Handlers communicate intent via a job queue; a separately-managed worker is responsible for doing the work and is tracked by the server's lifecycle.

```go
// RIGHT — job queue with lifecycle-managed workers
type BackgroundJobServer struct {
    queue  chan Job
    done   chan struct{}
    wg     sync.WaitGroup
}

func NewBackgroundJobServer(bufferSize int) *BackgroundJobServer {
    s := &BackgroundJobServer{
        queue: make(chan Job, bufferSize),
        done:  make(chan struct{}),
    }
    return s
}

func (s *BackgroundJobServer) Start(ctx context.Context, workers int) {
    for i := 0; i < workers; i++ {
        s.wg.Add(1)
        go func() {
            defer s.wg.Done()
            for {
                select {
                case job, ok := <-s.queue:
                    if !ok {
                        return // channel closed, worker exits
                    }
                    s.runJob(ctx, job)
                case <-ctx.Done():
                    return
                }
            }
        }()
    }
}

func (s *BackgroundJobServer) Enqueue(job Job) error {
    select {
    case s.queue <- job:
        return nil
    default:
        // Queue full — shed the load rather than blocking the handler
        return ErrQueueFull
    }
}

func (s *BackgroundJobServer) Shutdown(timeout time.Duration) {
    close(s.queue) // signal workers to drain and exit
    done := make(chan struct{})
    go func() {
        s.wg.Wait()
        close(done)
    }()
    select {
    case <-done:
        // All workers finished cleanly
    case <-time.After(timeout):
        // Workers didn't finish in time — log and move on
        slog.Warn("background workers did not finish within shutdown timeout")
    }
}

func (s *BackgroundJobServer) runJob(ctx context.Context, job Job) {
    defer func() {
        if r := recover(); r != nil {
            slog.Error("background job panicked", "job_id", job.ID, "panic", r)
        }
    }()
    if err := job.Execute(ctx); err != nil {
        slog.Error("background job failed", "job_id", job.ID, "error", err)
    }
}
```

Now the handler just enqueues — it doesn't spawn anything:

```go
// RIGHT — handler enqueues, doesn't spawn
func handleCreateOrder(jobs *BackgroundJobServer) http.HandlerFunc {
    return func(w http.ResponseWriter, r *http.Request) {
        order, err := parseOrder(r)
        if err != nil {
            http.Error(w, "bad request", http.StatusBadRequest)
            return
        }

        if err := db.CreateOrder(r.Context(), order); err != nil {
            http.Error(w, "internal error", http.StatusInternalServerError)
            return
        }

        // Enqueue the follow-up work — handler doesn't own it
        if err := jobs.Enqueue(OrderFollowUpJob{Order: order}); err != nil {
            // Queue is full — that's okay, log it, maybe persist it for retry
            slog.Warn("could not enqueue order follow-up", "order_id", order.ID)
        }

        w.WriteHeader(http.StatusAccepted)
        json.NewEncoder(w).Encode(order)
    }
}
```

## In The Wild

For cases where you need structured concurrency within a request — multiple async operations that all need to succeed — `errgroup` is the right tool, used as middleware or explicitly in complex handlers:

```go
// RIGHT — errgroup for structured concurrent work within a request scope
import "golang.org/x/sync/errgroup"

func handleDashboard(w http.ResponseWriter, r *http.Request) {
    g, ctx := errgroup.WithContext(r.Context())

    var orders []Order
    var products []Product
    var metrics Metrics

    g.Go(func() error {
        var err error
        orders, err = db.GetRecentOrders(ctx, userID)
        return err
    })

    g.Go(func() error {
        var err error
        products, err = db.GetTopProducts(ctx)
        return err
    })

    g.Go(func() error {
        var err error
        metrics, err = analytics.GetDashboardMetrics(ctx, userID)
        return err
    })

    if err := g.Wait(); err != nil {
        http.Error(w, "internal error", http.StatusInternalServerError)
        return
    }

    render(w, DashboardPage{Orders: orders, Products: products, Metrics: metrics})
}
```

The `errgroup.WithContext` creates a context that gets cancelled the moment any goroutine returns an error — so if the orders query fails, the products and metrics queries get cancelled too. No wasted work, clean error propagation, and all goroutines are tracked through `g.Wait()`. The handler doesn't return until all three queries complete or one fails.

## The Gotchas

**Handler context cancels when the client disconnects.** The `r.Context()` passed to goroutines spawned in handlers gets cancelled when the HTTP response is sent or the client disconnects. If your background goroutine uses `r.Context()`, it'll be cancelled the moment the handler returns. For background work that should outlive the request, use the server's lifecycle context, not the request context.

**`server.RegisterOnShutdown` doesn't block `Shutdown`.** As I mentioned above — the shutdown callbacks run concurrently and `Shutdown` doesn't wait for them. If you use `RegisterOnShutdown` to wait for background goroutines, you need to hold up the main goroutine yourself until the wait completes. The safest pattern is to call `wg.Wait()` directly in your main shutdown sequence after `srv.Shutdown`.

**Queue full is not an error — it's a signal.** When `Enqueue` fails because the queue is full, that means your workers are behind. That's load shedding — the right response is to log it, maybe return a 202 to the client noting that the follow-up work might be delayed, and possibly persist the job to a durable store for retry. Don't block the handler waiting for queue space — that turns a background job problem into a request latency problem.

**Panics in goroutines kill the server.** A recovered panic in a goroutine spawned from a handler won't propagate — unless you don't recover it. Always wrap worker goroutine bodies in a `defer recover()`. I put this in the `runJob` function above — a single place where all background job execution happens, so I only need the recover logic once.

**The right answer is often a database-backed job queue.** If your background work needs to survive server crashes, the in-memory queue approach isn't enough. Jobs enqueued but not processed are lost when the process dies. For anything that must run exactly once — sending an email, charging a card, fulfilling an order — consider persisting the job to a database first, then processing it from there (as covered in Lesson 23).

## Key Takeaway

Never spawn goroutines directly in HTTP handlers. Handlers should be fast, synchronous, and context-aware. If you need to do work after responding — sending emails, updating caches, triggering webhooks — use an in-process job queue that's started and stopped as part of your server's lifecycle. Track all background goroutines through a WaitGroup so graceful shutdown can wait for them. Use `errgroup` for structured concurrent work within a request scope. Always recover panics in background workers. And remember: if the work needs to survive server restarts, it belongs in a database, not an in-memory channel.

---

← [Lesson 25: Observability for Concurrency](/post/go/go-concurrency-observability) | [Lesson 27: Idempotency in Concurrent Systems →](/post/go/go-concurrency-idempotency)
