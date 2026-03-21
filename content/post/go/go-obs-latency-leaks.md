---
title: "Lesson 6: Debugging Latency and Leaks — Your p99 is lying to you"
author: Atharva Pandey
series: "Go Observability in Production"
lesson: 6
keywords:
  - Go
  - golang
  - observability
  - latency
  - memory leaks
  - goroutine leaks
  - p99
  - tail latency
  - GC pressure
  - connection pool
tags:
  - Go tutorial
  - golang
  - observability
date: '2025-01-15T00:00:00.000Z'
---

We had a service whose p99 latency was 800ms. The p50 was 12ms. The SLA was 200ms at p99. All our dashboards showed the p99 breaching during peak traffic, but the service felt fine to most users. We added caching. We optimized the slow database query. We bumped the CPU allocation. The p99 barely moved.

Three weeks of tuning later, a colleague asked me to show him the raw histogram buckets. We looked at the actual distribution: 98% of requests were under 15ms. The remaining 2% were all clustered around 900ms, nearly a bimodal distribution. This was not a "slow" service — it was a service with two distinct response time populations, and the p99 was sampling from the slow population. The fix had nothing to do with the code on the hot path.

The slow population turned out to be requests that hit a connection pool contention window. When the pool was exhausted, requests waited for a connection to become available, and that wait was consistently 700–900ms. Increasing the pool size from 10 to 50 connections made the p99 drop to 18ms in four minutes.

That is what I mean when I say your p99 is lying to you. The percentile is accurate — but without the underlying distribution, it points you at the wrong problem.

## The Problem

Two categories of production problems that look similar from a distance but require entirely different tools:

**Latency problems** show up as elevated percentiles, user complaints about slowness, and SLA breaches. The root causes are varied: GC pressure, connection pool exhaustion, lock contention, noisy neighbor CPU scheduling, and TCP connection reuse failures.

**Leak problems** show up as gradual resource exhaustion — memory growing by a fixed amount per hour, goroutine count increasing by a few per minute, file descriptors approaching the OS limit. They manifest as OOM kills, EMFILE errors, and eventually complete service failures hours or days after deployment.

Both are invisible to naive monitoring. A p99 graph tells you there's a latency problem but not where. A heap metric tells you memory is growing but not which allocation is responsible.

```go
// these metrics tell you THAT something is wrong, not WHY
goroutineCount.Set(float64(runtime.NumGoroutine()))    // growing? but from where?
heapAllocBytes.Set(float64(ms.HeapAlloc))              // growing? but which type?
httpP99LatencyMs.Set(computeP99(latencySamples))       // high? but which code path?
```

You need the distribution under the percentile and the allocation stack under the heap number.

## The Idiomatic Way

**Diagnosing latency: histogram buckets and execution traces**

The first step is getting the distribution, not just the percentile. In Prometheus:

```promql
# see the raw bucket distribution for your HTTP handler
sum(rate(http_request_duration_seconds_bucket{handler="checkout"}[5m])) by (le)
```

Plot this as a heatmap in Grafana. If you see a bimodal distribution — two clusters of latency — that tells you immediately that something is intermittent, not uniformly slow. The fix for intermittent latency is almost always:
- A connection pool that's too small (requests wait for a connection)
- A lock that's occasionally contended (goroutines queue behind a mutex)
- A GC pause that's periodically stopping the world

For GC-induced latency spikes, the smoking gun is comparing your latency heatmap to your GC pause histogram:

```go
// track GC pause per cycle, not the total
var prevPauseTotalNs uint64

func recordGCMetrics() {
    var ms runtime.MemStats
    runtime.ReadMemStats(&ms)

    if ms.NumGC > 0 {
        // last GC pause duration
        lastPause := ms.PauseNs[(ms.NumGC+255)%256]
        gcLastPauseDuration.Set(float64(lastPause) / 1e9)
    }
}
```

If your latency spikes align with GC pauses, the fix is reducing allocation rate: pool objects with `sync.Pool`, avoid allocating in the hot path, and tune `GOGC` to trade memory for pause frequency.

**Diagnosing goroutine leaks: stack analysis**

A goroutine leak is typically one of three patterns:

```go
// Pattern 1: goroutine blocks forever on a channel that nobody writes to
func processItems(items []Item) {
    results := make(chan Result) // unbuffered
    for _, item := range items {
        go func(i Item) {
            results <- process(i) // blocks if nobody reads
        }(item)
    }
    // if we return early due to an error, the goroutines block forever
    for range items {
        r := <-results
        if r.Error != nil {
            return  // LEAK: remaining goroutines blocked on results <-
        }
    }
}

// Pattern 2: goroutine blocked on a ticker/timer with no exit condition
func startBackgroundWorker(cfg Config) {
    go func() {
        ticker := time.NewTicker(cfg.Interval)
        for range ticker.C { // no ctx.Done() — runs forever even after reload
            doWork()
        }
    }()
}

// Pattern 3: HTTP response body not closed — keeps connection open
func callAPI(url string) ([]byte, error) {
    resp, err := http.Get(url)
    if err != nil {
        return nil, err
    }
    // resp.Body.Close() missing — connection and its goroutine leak
    return io.ReadAll(resp.Body)
}
```

The goroutine profile from pprof shows you the stack of every goroutine. A leak is visible as many goroutines with the same stack trace:

```bash
curl -s http://localhost:6060/debug/pprof/goroutine?debug=2 | \
    grep -A 5 "goroutine [0-9]* \[chan receive\]" | \
    sort | uniq -c | sort -rn | head -20
```

If you see hundreds of goroutines blocked on `chan receive` with the same stack trace, that's your leak.

**Diagnosing memory leaks: allocation profiles**

```bash
# take two heap profiles 5 minutes apart
curl -o heap1.prof http://localhost:6060/debug/pprof/heap
sleep 300
curl -o heap2.prof http://localhost:6060/debug/pprof/heap

# diff them to see what grew
go tool pprof -http=:8081 -diff_base heap1.prof heap2.prof
```

The diff profile shows you which allocation sites grew between the two snapshots. Sort by `inuse_space` (memory in use, not total allocated) and look for anything growing that shouldn't be.

## In The Wild

A real debugging session for a connection pool leak I traced:

The service had a metric showing `db_connections_active` growing slowly, reaching the pool max of 25 after about 2 hours of traffic. Requests would then queue for available connections, causing the p99 latency spike.

The goroutine profile showed dozens of goroutines blocked on `(*sql.DB).QueryContext` waiting for a connection. The heap profile showed large allocations in the database query result scanning code.

The root cause: a function that scanned query results was calling `rows.Next()` but was missing `rows.Close()` in an error path:

```go
// WRONG — rows not closed on early return
func getUserOrders(ctx context.Context, userID int64) ([]Order, error) {
    rows, err := db.QueryContext(ctx, "SELECT * FROM orders WHERE user_id = $1", userID)
    if err != nil {
        return nil, err
    }
    // rows.Close() missing — if Scan fails, we return but the rows (and connection) stay open

    var orders []Order
    for rows.Next() {
        var o Order
        if err := rows.Scan(&o.ID, &o.Status, &o.Amount); err != nil {
            return nil, err  // connection not returned to pool
        }
        orders = append(orders, o)
    }
    return orders, rows.Err()
}

// RIGHT — defer rows.Close() immediately after checking err
func getUserOrders(ctx context.Context, userID int64) ([]Order, error) {
    rows, err := db.QueryContext(ctx, "SELECT * FROM orders WHERE user_id = $1", userID)
    if err != nil {
        return nil, err
    }
    defer rows.Close()  // always runs, even on early return

    var orders []Order
    for rows.Next() {
        var o Order
        if err := rows.Scan(&o.ID, &o.Status, &o.Amount); err != nil {
            return nil, err
        }
        orders = append(orders, o)
    }
    return orders, rows.Err()
}
```

The fix was one line. The connection pool returned to zero utilization within seconds of the deploy.

## The Gotchas

**`sync.Pool` objects are released by GC.** `sync.Pool` is for reducing allocator pressure, not for maintaining a pool of resources. Put `[]byte` buffers in it, not database connections or HTTP clients. A GC cycle can drain the pool entirely, causing a burst of allocations right after.

**`GOGC=off` is not a fix for GC pause problems.** Disabling GC means the heap grows without bound until your process OOMs. You can tune `GOGC` upward (e.g., `GOGC=200` doubles the heap headroom before a GC cycle) to reduce GC frequency at the cost of higher memory usage. In Go 1.19+, `GOMEMLIMIT` is a better lever — it sets an absolute memory ceiling and tells the GC to run more frequently before reaching it.

**Latency percentiles require enough samples.** A p99 computed from 100 requests is statistically meaningless — you're looking at one sample with wide confidence intervals. Your histogram must aggregate over at least 1000 requests in the window. For low-traffic endpoints, use a longer window (10 minutes instead of 5).

**Context cancellation does not stop goroutines.** Canceling a context does not kill goroutines that are not checking `ctx.Done()`. Your goroutines must explicitly respect cancellation:

```go
func processLargeDataset(ctx context.Context, items []Item) error {
    for _, item := range items {
        select {
        case <-ctx.Done():
            return ctx.Err()  // exit the goroutine on cancellation
        default:
        }
        if err := processItem(ctx, item); err != nil {
            return err
        }
    }
    return nil
}
```

Without this pattern, goroutines spawned by a cancelled request continue running after the HTTP response has been sent, consuming CPU and holding resources.

## Key Takeaway

Your p99 is only useful when you can see the underlying distribution. Histogram heatmaps in Grafana reveal bimodal latency patterns that a single percentile hides — and bimodal latency almost always means intermittent resource contention, not slow code on the hot path. For goroutine leaks, the goroutine pprof profile with `debug=2` gives you every blocked stack trace; look for duplicate stacks at scale. For memory leaks, diff two heap profiles 5 minutes apart and sort by `inuse_space`. The fix is almost always a missing `defer rows.Close()`, a channel nobody drains, or a goroutine that never checks `ctx.Done()`. The tools are already in the standard library — you just have to use them before the incident, not during it.

---

← [Previous: Profiling in Production](/post/go/go-obs-profiling) | [Course Index](/post/go/) | [Next: The Complete Observability Stack →](/post/go/go-obs-complete-stack)
