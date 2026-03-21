---
title: 'Lesson 20: Profiling Contention — pprof knows where your goroutines sleep'
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 20
keywords:
  - Go
  - golang
  - concurrency
  - pprof
  - profiling
  - mutex profile
  - block profile
  - goroutine profile
  - go tool trace
  - contention
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-11-13T00:00:00.000Z'
---

You've added goroutines, you've got worker pools, you've written careful concurrent code — and the service is still slower than expected. Maybe goroutine count is climbing in your metrics dashboard. Maybe p99 latency has a long tail you can't explain. Maybe a throughput test plateaus at 40% of what you thought the hardware should support.

This is where guessing stops and profiling starts. Go ships world-class concurrency profiling tools in the standard library — mutex profiles, block profiles, goroutine dumps, and the execution tracer. Most engineers know about the CPU and memory profiler. Far fewer use the concurrency-specific profiles, which is a shame because they find the exact thing that's wrong in minutes instead of days.

## The Problem

The common reaction to concurrency slowdowns is to add more goroutines or increase worker pool sizes. That approach is guessing:

```go
// WRONG — throwing goroutines at the problem without understanding the bottleneck
func processRequests(reqs []Request) {
    // was 10, tried 50, then 100, throughput barely changed, ¯\_(ツ)_/¯
    const workers = 100
    jobs := make(chan Request, len(reqs))
    var wg sync.WaitGroup
    for i := 0; i < workers; i++ {
        wg.Add(1)
        go func() {
            defer wg.Done()
            for req := range jobs {
                handle(req)
            }
        }()
    }
    // ...
}
```

If `handle` internally acquires a heavily contended mutex, adding more workers makes contention *worse*, not better. More goroutines means more goroutines racing for the same lock. Throughput can actually decrease.

The second mistake is interpreting high goroutine count as "lots of work happening":

```go
// WRONG — goroutine count is a lagging indicator; goroutines might all be blocked
// What you see in your dashboard: goroutines=50000, CPU utilization=12%
// What's actually happening: 49000 goroutines blocked on a single mutex
```

CPU utilization and goroutine count tell you almost nothing about *why* your program is slow. The block and mutex profiles do.

## The Idiomatic Way

Enable the profiles and expose them via `net/http/pprof`:

```go
// RIGHT — enable concurrency profiles in your service
import (
    "net/http"
    _ "net/http/pprof" // registers pprof endpoints on http.DefaultServeMux
    "runtime"
)

func init() {
    // Enable the mutex and block profilers — they're off by default
    runtime.SetMutexProfileFraction(1)  // sample every mutex event (use 10-100 in prod)
    runtime.SetBlockProfileRate(1)       // sample every blocking event (use 10000 in prod)
}

func main() {
    // expose pprof on a separate port, never on your public-facing port
    go http.ListenAndServe("localhost:6060", nil)
    // ... rest of your service
}
```

`SetMutexProfileFraction(1)` records every mutex contention event. In production, use a fraction like 10 or 100 to reduce overhead — you're sampling, not recording everything. Same for `SetBlockProfileRate`: a rate of 10000 means "record one event per 10 microseconds of blocking."

Collecting and reading the profiles:

```bash
# Mutex profile — shows which mutexes are contended and where they're held
go tool pprof http://localhost:6060/debug/pprof/mutex

# Block profile — shows where goroutines block (channels, select, mutex, syscall)
go tool pprof http://localhost:6060/debug/pprof/block

# Goroutine dump — snapshot of every goroutine and its current stack
curl http://localhost:6060/debug/pprof/goroutine?debug=2 > goroutine.txt

# Execution trace — timeline of scheduler events, GC, goroutine start/stop
curl http://localhost:6060/debug/pprof/trace?seconds=5 > trace.out
go tool trace trace.out
```

In the pprof interactive shell, `top10` shows the ten locations with the most contention. `web` opens a flame graph in the browser. `list funcName` shows the annotated source code.

A real contention investigation looks like this:

```go
// You run: go tool pprof http://localhost:6060/debug/pprof/mutex
// Inside pprof:
// (pprof) top10
//
// Showing nodes accounting for 2.3s, 94.1% of 2.44s total
//   flat  flat%   sum%        cum   cum%
//   2.3s 94.1% 94.1%       2.3s 94.1%  sync.(*Mutex).Lock
//         0     0%  94.1%       2.3s 94.1%  main.(*Cache).Get
//
// (pprof) list Cache.Get

// You immediately see: your "Cache" is a global struct with a single RWMutex,
// Get acquires a write lock even for reads. That's your bottleneck.

// WRONG — using write lock for reads
func (c *Cache) Get(key string) (Value, bool) {
    c.mu.Lock() // write lock — blocks all other readers
    defer c.mu.Unlock()
    v, ok := c.data[key]
    return v, ok
}

// RIGHT — use RLock for reads
func (c *Cache) Get(key string) (Value, bool) {
    c.mu.RLock()
    defer c.mu.RUnlock()
    v, ok := c.data[key]
    return v, ok
}
```

That's a real class of bug the mutex profiler finds instantly.

## In The Wild

The goroutine profile is invaluable for debugging goroutine leaks — goroutines that were created and never cleaned up:

```go
// In a test or health check endpoint
func goroutineLeakCheck(t *testing.T) {
    before := runtime.NumGoroutine()

    // do stuff
    doSomeWork()

    time.Sleep(100 * time.Millisecond) // let goroutines settle
    after := runtime.NumGoroutine()

    if after > before+1 { // allow 1 for the goroutine running this check
        // Capture the dump for debugging
        buf := make([]byte, 1<<20)
        n := runtime.Stack(buf, true)
        t.Logf("goroutine dump:\n%s", buf[:n])
        t.Errorf("goroutine leak: before=%d after=%d", before, after)
    }
}
```

For a more complete solution, `go.uber.org/goleak` is worth adding to your test suite:

```go
func TestMain(m *testing.M) {
    goleak.VerifyTestMain(m)
}

// goleak will fail any test that leaves goroutines running after the test completes.
// The failure message includes the goroutine stack, so you know exactly what leaked.
```

`go tool trace` goes deeper than pprof — it gives you a timeline view of scheduler events. You can see individual goroutines being scheduled, paused, unblocked. For understanding why specific goroutines have high latency or why you're not getting expected parallelism, the trace view is invaluable. It's more work to interpret than pprof, but for hard problems it's the right tool.

## The Gotchas

**Profiling overhead in production.** `SetMutexProfileFraction(1)` and `SetBlockProfileRate(1)` add measurable overhead. Use fractions of 100 or higher in production. For point-in-time debugging, crank them up temporarily on one instance, collect your data, then dial them back.

**The pprof endpoint is a security risk.** Never expose `localhost:6060` on a public-facing interface. Stack traces and goroutine dumps can expose credentials, internal URLs, and other sensitive data. Use a separate internal port, or use firewall rules to restrict access. A common mistake is binding to `0.0.0.0:6060` instead of `localhost:6060`.

**Goroutine dumps at high concurrency are expensive.** Collecting a full goroutine dump with `debug=2` stops the world long enough to snapshot all goroutine stacks. On a service with hundreds of thousands of goroutines, this can cause a multi-second pause that cascades into timeouts. Use `debug=1` (abbreviated stacks) for large services, and collect dumps during off-peak hours.

**pprof shows cumulative time, not instantaneous state.** A mutex profile that shows function X at 50% doesn't mean X is holding a lock right now — it means X has accounted for 50% of lock contention *since the profiler was enabled* (or since process start). To see the current state of the world, the goroutine dump is more useful because it shows where every goroutine is *right now*.

## Key Takeaway

The four profiling tools for concurrent Go are: the mutex profile (which mutex is most contended), the block profile (where goroutines wait), the goroutine dump (what every goroutine is doing right now), and `go tool trace` (timeline of scheduler events). Enable the mutex and block profilers in every service — they're off by default and that default is wrong. When you see unexplained latency or sublinear throughput scaling, reach for the block profile first: it shows every place a goroutine stopped making progress and why. Use `goleak` in tests to catch goroutine leaks before they reach production. Guessing at concurrency problems wastes days; profiling finds them in minutes.

---

← [Previous: Ordering vs Throughput](/post/go/go-concurrency-ordering-throughput) | [Course Index](/post/go/) | [Next: Supervisor Patterns](/post/go/go-concurrency-supervisor-patterns) →
