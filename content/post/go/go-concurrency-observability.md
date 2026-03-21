---
title: "Lesson 25: Observability for Concurrency — You can''t fix what you can''t see"
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 25
keywords:
  - Go
  - golang
  - concurrency
  - observability
  - pprof
  - goroutine metrics
  - mutex contention
  - structured logging
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2026-01-15T00:00:00.000Z'
---

The goroutine leak doesn't announce itself. It just slowly inflates your memory graph — 50MB, 80MB, 150MB — until either your alerting fires or the OOM killer shows up. Then you're staring at a core dump or (if you're lucky) a live process trying to figure out which of the 10,000 goroutines running in your service is the culprit. I've been there. It's not fun. The difference between spending 20 minutes diagnosing a leak and spending 4 hours diagnosing a leak is almost entirely whether you invested in observability before the incident.

Concurrent Go programs have failure modes that don't show up in CPU or memory charts alone. Goroutine count. Mutex contention time. Channel queue depth. Blocked goroutine stacks. These are the signals that tell you what's *actually happening* inside your concurrent system. And Go has first-class tooling for all of it — you just have to wire it up.

## The Problem

The typical service exposes nothing about its own concurrency health:

```go
// WRONG — service with no concurrency observability
func main() {
    mux := http.NewServeMux()
    mux.HandleFunc("/api/orders", handleOrders)
    mux.HandleFunc("/api/products", handleProducts)

    // That's it. No goroutine metrics, no pprof, no mutex tracing.
    // Good luck figuring out why this is slow under load.
    http.ListenAndServe(":8080", mux)
}
```

When something goes wrong with this service — goroutines leak, a mutex becomes a bottleneck, a channel fills up and blocks producers — you have essentially nothing to work with. You can look at CPU and memory from your infrastructure metrics, but those tell you *that* something is wrong, not *what* or *why*.

The other failure mode is logging in goroutines with no context — so when something goes wrong, you have hundreds of log lines with no way to correlate them:

```go
// WRONG — goroutine logging with no correlation
func processOrder(order Order) {
    go func() {
        log.Printf("processing order")        // which order? which goroutine?
        result, err := chargeCustomer(order)
        if err != nil {
            log.Printf("charge failed: %v", err) // no order ID, no trace ID
        }
        log.Printf("done")                    // meaningless without context
    }()
}
```

When you have 50 concurrent orders being processed and one fails, you can't correlate the "charge failed" log line with the order it belongs to. Every log line is an island.

## The Idiomatic Way

Start with `pprof`. It's built into the standard library and provides goroutine dumps, heap profiles, CPU profiles, mutex contention, and blocking profiles. You should expose it in every production service — ideally on a separate internal port, not your public API port.

```go
// RIGHT — pprof and goroutine metrics exposed on internal port
import (
    "expvar"
    "net/http"
    _ "net/http/pprof" // registers handlers on http.DefaultServeMux
    "runtime"
    "time"
)

func startInternalServer() {
    // Expose goroutine count as an expvar metric
    expvar.Publish("goroutines", expvar.Func(func() interface{} {
        return runtime.NumGoroutine()
    }))

    // pprof handlers are auto-registered by the import above
    // GET /debug/pprof/goroutine?debug=2 — full goroutine dump with stacks
    // GET /debug/pprof/heap             — heap profile
    // GET /debug/pprof/mutex            — mutex contention
    // GET /debug/pprof/block            — blocking profile
    go http.ListenAndServe(":6060", nil) // internal port only
}
```

That `_ "net/http/pprof"` import side-effectfully registers all the pprof handlers. Once this is running, you can do `curl localhost:6060/debug/pprof/goroutine?debug=2` to get a full dump of every goroutine with its stack trace. This is how you find leaks — look for goroutines that are all stuck at the same stack frame.

For proactive monitoring, emit goroutine count as a metric:

```go
// RIGHT — goroutine count and blocking metrics emitted to Prometheus
import "github.com/prometheus/client_golang/prometheus"

var (
    goroutineGauge = prometheus.NewGauge(prometheus.GaugeOpts{
        Name: "go_goroutines_total",
        Help: "Number of goroutines currently running",
    })

    workerQueueDepth = prometheus.NewGaugeVec(prometheus.GaugeOpts{
        Name: "worker_queue_depth",
        Help: "Number of items waiting in worker queues",
    }, []string{"queue_name"})
)

func init() {
    prometheus.MustRegister(goroutineGauge, workerQueueDepth)
}

// Collect metrics in a background goroutine
func startMetricsCollector(ctx context.Context, queues map[string]chan Job) {
    ticker := time.NewTicker(5 * time.Second)
    defer ticker.Stop()

    for {
        select {
        case <-ctx.Done():
            return
        case <-ticker.C:
            goroutineGauge.Set(float64(runtime.NumGoroutine()))
            for name, q := range queues {
                workerQueueDepth.WithLabelValues(name).Set(float64(len(q)))
            }
        }
    }
}
```

Now you can alert: "goroutine count above 5000 for 5 minutes" is a goroutine leak. "Queue depth above 80% capacity for 1 minute" is a worker bottleneck. These are actionable signals you wouldn't have without this instrumentation.

## In The Wild

Mutex contention is one of the sneakiest concurrency performance problems. A sync.Mutex inside a hot path turns your concurrent program into a serial one at exactly the wrong moment. The mutex profiler finds it for you:

```go
// RIGHT — enabling mutex and block profiling for production diagnosis
func init() {
    // Enable mutex profiling — samples 1/10 of mutex contention events
    // Lower rate = less overhead, higher rate = more detailed profile
    runtime.SetMutexProfileFraction(10)

    // Enable blocking profiling — samples goroutines blocked on channels, sleeps, etc.
    runtime.SetBlockProfileRate(1000) // sample every 1ms of blocking time
}
```

Once these are enabled, `GET /debug/pprof/mutex` shows you which mutexes are most contended and where they're being locked. I've used this to find a cache mutex that was responsible for 40% of p99 latency — the cache hit rate was high, but every hot request was serialized through one mutex protecting the whole map. Switching to `sync.Map` for that specific cache dropped p99 by 35%.

Structured logging in goroutines needs correlation IDs to be useful:

```go
// RIGHT — structured logging with correlation context in goroutines
import "log/slog"

func processOrderAsync(ctx context.Context, order Order) {
    // Extract the trace/correlation ID from the context before the goroutine starts
    // Context is safe to pass to goroutines — it's immutable
    logger := slog.With(
        "order_id", order.ID,
        "user_id", order.UserID,
        "trace_id", traceIDFromContext(ctx),
    )

    go func() {
        logger.Info("starting order processing")

        result, err := chargeCustomer(ctx, order)
        if err != nil {
            logger.Error("charge failed",
                "error", err,
                "amount", order.Amount,
            )
            return
        }

        logger.Info("order processed successfully",
            "charge_id", result.ChargeID,
        )
    }()
}

func traceIDFromContext(ctx context.Context) string {
    if id, ok := ctx.Value(traceIDKey{}).(string); ok {
        return id
    }
    return "unknown"
}
```

Now every log line from the goroutine has the order ID and trace ID. When something goes wrong, you can filter by order ID and see the complete story in order.

## The Gotchas

**`runtime.NumGoroutine()` includes runtime goroutines.** A fresh Go program starts with several goroutines for the GC, finalizer, etc. Your baseline will be around 4-6 goroutines, not 0. Set your leak alert threshold above that baseline — something like "goroutine count exceeds 200% of the 1-hour average" is more reliable than a fixed threshold.

**pprof on a public port is a security hole.** The pprof endpoint exposes goroutine stacks, which can contain request data, tokens, and other sensitive information. Always put it on an internal port that's not accessible from the public internet. If you run on Kubernetes, use a separate `Service` for the debug port that's only accessible within the cluster.

**Block profiling has overhead.** `runtime.SetBlockProfileRate(1)` samples every nanosecond of blocking — extremely detailed but significant overhead. In production, use a higher rate like 1000 (sample per millisecond of blocking) or enable it only when investigating a specific issue.

**`len(ch)` is a snapshot.** The queue depth you get from `len(queue)` is accurate at the moment you read it, but channels are concurrent data structures — by the time you log it or send it to Prometheus, the value may have changed. This is fine for trending (alerting on sustained depth) but not for precise accounting.

**Log context, not data.** Structured logging with correlation IDs is about finding log lines that belong together, not logging entire request payloads. Logging full request/response bodies is a great way to accidentally log PII, blow up your log storage costs, and degrade performance (log writes are IO). Log IDs, sizes, durations, and outcomes — not content.

## Key Takeaway

Observability for concurrent code means three things: metrics for trends (goroutine count, queue depth, mutex contention), profiles for diagnosis (pprof goroutine dumps, mutex and blocking profiles), and structured logging with correlation IDs for incident investigation. All three are built into or easily added to Go. Wire them up before you need them — pprof in development, metrics in production, correlation IDs from day one. The cost is minimal. The payoff during an incident is enormous. Every minute you spend setting this up is worth ten minutes of blind debugging at 3 AM.

---

← [Lesson 24: Testing Concurrent Code](/post/go/go-concurrency-testing) | [Lesson 26: Safe Background Jobs in Web Servers →](/post/go/go-concurrency-safe-background-jobs)
