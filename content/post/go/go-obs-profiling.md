---
title: "Lesson 5: Profiling in Production — pprof is not just for development"
author: Atharva Pandey
series: "Go Observability in Production"
lesson: 5
keywords:
  - Go
  - golang
  - observability
  - pprof
  - profiling
  - CPU profiling
  - memory profiling
  - goroutine profiling
  - continuous profiling
  - Pyroscope
tags:
  - Go tutorial
  - golang
  - observability
date: '2024-12-01T00:00:00.000Z'
---

I used to think profiling was something you did when you had a performance problem: reproduce it locally, run pprof, stare at the flame graph, fix the hot path. A fire-fighting tool, not an always-on system.

Then we had a memory leak in production that we couldn't reproduce locally. The heap grew by about 50 MB per hour under real traffic patterns, causing an OOM every eight hours and a rolling restart across the fleet. Our staging environment used synthetic load that didn't trigger the leak. We had no profile data from when the leak was building — only from after the crash, when the heap had already been cleared.

We deployed continuous profiling. Three days later a similar leak happened in a different service. This time we had heap profiles from the entire growth period, timestamped and correlated to deployment markers. The fix took 20 minutes: a goroutine was holding a reference to a growing slice in a closure that was never garbage collected because the goroutine itself never exited.

## The Problem

The common mental model: pprof is a development tool you use with benchmarks. You run `go test -bench . -cpuprofile cpu.out`, look at it once, and move on. This model misses the cases where performance problems only manifest under real production traffic — specific user behavior, specific data shapes, cardinality that your synthetic load doesn't cover.

The other mistake is leaving pprof disabled in production out of concern for overhead:

```go
// people add this and then comment it out "for performance"
// import _ "net/http/pprof"
```

The pprof HTTP handler adds approximately 5% CPU overhead when actively sampling, and essentially zero overhead when idle. The default handlers are idle unless you hit the `/debug/pprof/` endpoint. Leaving them enabled costs nothing in normal operation and gives you everything when you need it.

The third problem is running a one-off profile at the wrong moment. If your service leaks memory over 8 hours, a 30-second heap profile taken 2 minutes after startup tells you nothing useful. You need continuous, time-series profiling data.

## The Idiomatic Way

**Step 1: Enable the pprof endpoints — and lock them down**

```go
package main

import (
    "net/http"
    _ "net/http/pprof"  // registers handlers on http.DefaultServeMux
    "os"
)

func main() {
    // serve pprof on a separate port, not your public API port
    // bind to localhost or an internal network interface only
    go func() {
        addr := os.Getenv("PPROF_ADDR")
        if addr == "" {
            addr = "localhost:6060"
        }
        if err := http.ListenAndServe(addr, nil); err != nil {
            slog.Error("pprof server failed", "err", err)
        }
    }()

    // your main API server on a different port
    http.ListenAndServe(":8080", apiRouter())
}
```

Never expose pprof on your public port. Bind it to localhost or an internal network range, then access it via `kubectl port-forward` or an internal load balancer. Exposing pprof to the internet allows any caller to CPU-starve your service by requesting a 30-second CPU profile.

**Step 2: Take targeted profiles**

The pprof HTTP endpoints accept query parameters that control duration and depth:

```bash
# CPU profile for 30 seconds
curl -o cpu.prof http://localhost:6060/debug/pprof/profile?seconds=30

# heap profile (current allocations)
curl -o heap.prof http://localhost:6060/debug/pprof/heap

# goroutine profile — shows all running goroutines and their stacks
curl -o goroutine.prof http://localhost:6060/debug/pprof/goroutine

# mutex contention — shows where goroutines block on mutexes
curl -o mutex.prof http://localhost:6060/debug/pprof/mutex

# analyze locally
go tool pprof -http=:8081 cpu.prof
```

The goroutine profile is underrated. When you suspect a goroutine leak, download it and look at the count over time:

```bash
# download goroutine profile every 60 seconds for 10 minutes
for i in $(seq 1 10); do
    curl -o "goroutine-$i.prof" http://localhost:6060/debug/pprof/goroutine?debug=1
    sleep 60
done

# check goroutine counts
grep "^goroutine" goroutine-*.prof
```

If the goroutine count grows monotonically, you have a leak.

**Step 3: Continuous profiling with Pyroscope**

For always-on profiling that gives you time-series data, integrate Pyroscope (or Grafana Alloy with the profiling feature):

```go
import "github.com/grafana/pyroscope-go"

func initProfiling(serviceName string) {
    pyroscope.Start(pyroscope.Config{
        ApplicationName: serviceName,
        ServerAddress:   "http://pyroscope:4040",
        Logger:          pyroscope.StandardLogger,
        ProfileTypes: []pyroscope.ProfileType{
            pyroscope.ProfileCPU,
            pyroscope.ProfileAllocObjects,
            pyroscope.ProfileAllocSpace,
            pyroscope.ProfileInuseObjects,
            pyroscope.ProfileInuseSpace,
            pyroscope.ProfileGoroutines,
        },
        // tag with deployment metadata for correlation
        Tags: map[string]string{
            "version": os.Getenv("APP_VERSION"),
            "region":  os.Getenv("AWS_REGION"),
        },
    })
}
```

Pyroscope samples the CPU profile at 100Hz and streams it continuously. You can query the flame graph for any time window, compare before and after a deploy, and correlate a memory growth event with the specific function that started allocating.

## In The Wild

The workflow I use in practice:

When alerted that heap usage is growing, I first look at the Pyroscope dashboard to see which function started allocating more after the last deployment. If it's a new deployment, I compare the flame graph from the hour before and the hour after.

For diagnosing goroutine leaks, I wrote a small monitoring goroutine that emits the goroutine count as a metric every 30 seconds:

```go
func monitorRuntimeMetrics(ctx context.Context) {
    ticker := time.NewTicker(30 * time.Second)
    defer ticker.Stop()

    for {
        select {
        case <-ctx.Done():
            return
        case <-ticker.C:
            var ms runtime.MemStats
            runtime.ReadMemStats(&ms)

            goroutineCount.Set(float64(runtime.NumGoroutine()))
            heapAllocBytes.Set(float64(ms.HeapAlloc))
            heapInuseBytes.Set(float64(ms.HeapInuse))
            gcPauseTotalNs.Set(float64(ms.PauseTotalNs))
            nextGCBytes.Set(float64(ms.NextGC))

            // alert threshold: if goroutines exceed 10,000, something is leaking
            if runtime.NumGoroutine() > 10000 {
                slog.Warn("goroutine count high — possible leak",
                    "count", runtime.NumGoroutine(),
                )
            }
        }
    }
}
```

When the goroutine metric spikes in Prometheus, I take a goroutine profile at that moment and look for goroutines blocked on the same operation — that's always where the leak is.

For CPU spikes during traffic bursts, the `trace` tool (distinct from distributed tracing) is the right instrument:

```go
import "runtime/trace"

// capture a 5-second execution trace
f, _ := os.Create("trace.out")
defer f.Close()
trace.Start(f)
time.Sleep(5 * time.Second)
trace.Stop()

// analyze
// go tool trace trace.out
```

The execution trace shows goroutine scheduling, syscall blocks, network waits, and GC stop-the-world events on a timeline with microsecond resolution. It answers the question pprof cannot: "why did my goroutine wait 200ms before getting scheduled?"

## The Gotchas

**`runtime.ReadMemStats` stops the world.** Calling it frequently (every second or more) introduces GC-like pauses. 30-second intervals are safe. Anything more frequent than 5 seconds starts to show up in latency distributions.

**Mutex and block profiling must be explicitly enabled.**

```go
// in main(), before starting your server
runtime.SetMutexProfileFraction(5)  // sample 1/5 mutex contentions
runtime.SetBlockProfileRate(1)      // sample all blocking events (expensive — use sparingly)
```

`SetBlockProfileRate(1)` is very expensive in production — it profiles every goroutine block. Use it briefly during an incident, not continuously. `SetMutexProfileFraction` at 5–10 is safe to leave on.

**Flame graphs show hot paths, not slow paths.** A CPU flame graph shows where CPU time is being spent. A function that's slow because it waits on I/O won't show up on a CPU profile because it's not consuming CPU while waiting. For I/O latency, use distributed traces. For CPU saturation, use pprof. They answer different questions.

**Profile samples are not deterministic across runs.** Don't compare absolute sample counts between profiles — compare the proportional distribution of where time is spent. A function that's 30% of samples in one profile and 5% in another has gotten faster; a function that holds steady at 10% while total throughput doubled is actually consuming twice as much absolute work.

## Key Takeaway

pprof endpoints have negligible idle cost — run them in production. Use them on demand for immediate diagnosis: CPU profile for hot paths, heap profile for memory growth, goroutine profile for leak detection. Add continuous profiling with Pyroscope for time-series flame graphs that let you compare before and after a deployment. Emit goroutine count and heap size as Prometheus metrics with thresholds to get alerted before a leak becomes an OOM. The execution trace tool (`runtime/trace`) is the right instrument when you need to understand scheduling latency, not just CPU consumption. Between these tools, almost any Go performance problem in production becomes diagnosable without a reproduction environment.

---

← [Previous: Correlation IDs](/post/go/go-obs-correlation-ids) | [Course Index](/post/go/) | [Next: Debugging Latency and Leaks →](/post/go/go-obs-latency-leaks)
