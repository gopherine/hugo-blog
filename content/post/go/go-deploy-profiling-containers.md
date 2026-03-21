---
title: 'Lesson 7: Profiling in Containers — pprof works in Kubernetes too'
author: Atharva Pandey
series: "Go Deployment & Operations"
lesson: 7
keywords:
  - Go
  - golang
  - pprof
  - profiling
  - Kubernetes
  - performance
  - CPU profile
  - memory profile
  - goroutine profile
tags:
  - Go tutorial
  - golang
  - deployment
  - devops
date: '2025-04-12T00:00:00.000Z'
---

The first time I needed to profile a Go service in production, I assumed I'd have to deploy a special build, reproduce the problem locally, or use some heavyweight APM product. Then I learned that Go's `net/http/pprof` package can serve live profiling data from a running process via HTTP — in a container, in Kubernetes, right now, without redeployment.

`pprof` is the built-in Go profiler. It can capture CPU profiles (what functions are consuming CPU time), heap profiles (what's allocated in memory and by whom), goroutine profiles (how many goroutines exist and what they're doing), and block/mutex profiles (where goroutines are waiting). All of this is available as HTTP endpoints that you can scrape with `go tool pprof` from your laptop.

## The Problem

The common misconception is that profiling requires special builds or redeployment:

```go
// The typical response when someone says "we have a memory leak in production":
// "We'll need to deploy a profiling build and reproduce it in staging"

// But your production binary already has profiling capability — it just needs
// the pprof HTTP handler registered.
```

The second problem is the security concern that keeps teams from enabling pprof in production. Exposing profiling data to the internet is genuinely dangerous — a profile can reveal internal paths, function names, and memory contents. The solution isn't to disable pprof; it's to expose it only on a separate internal port that's not accessible from outside the cluster.

## The Idiomatic Way

Register pprof on a separate internal port from your main server:

```go
package main

import (
    "log"
    "net"
    "net/http"
    _ "net/http/pprof" // registers pprof handlers on http.DefaultServeMux
    "os"
)

func main() {
    // Main server — serves public traffic
    mainMux := http.NewServeMux()
    mainMux.HandleFunc("/api/", apiHandler)
    mainMux.HandleFunc("/healthz", healthHandler)
    mainMux.HandleFunc("/readyz", readinessHandler)

    // Debug server — serves profiling data, only accessible internally
    // The blank import above registered pprof on http.DefaultServeMux
    debugServer := &http.Server{
        Addr:    ":6060",
        Handler: http.DefaultServeMux, // serves /debug/pprof/ endpoints
    }

    // Start debug server in background
    go func() {
        debugAddr := os.Getenv("DEBUG_ADDR")
        if debugAddr == "" {
            debugAddr = ":6060"
        }
        l, err := net.Listen("tcp", debugAddr)
        if err != nil {
            log.Printf("debug server: %v", err)
            return
        }
        // Only listen on localhost in production — not 0.0.0.0
        log.Printf("debug server listening on %s", l.Addr())
        if err := debugServer.Serve(l); err != http.ErrServerClosed {
            log.Printf("debug server error: %v", err)
        }
    }()

    log.Fatal(http.ListenAndServe(":8080", mainMux))
}
```

The pprof endpoints are now at `localhost:6060/debug/pprof/`. In Kubernetes, you access them via `kubectl port-forward`:

```bash
# Forward the debug port from a running pod to your laptop
kubectl port-forward pod/myapp-75d7b59f8c-xk9p2 6060:6060

# Now from a separate terminal:
# CPU profile — captures 30 seconds of CPU activity
go tool pprof http://localhost:6060/debug/pprof/profile?seconds=30

# Heap profile — snapshot of current heap allocations
go tool pprof http://localhost:6060/debug/pprof/heap

# Goroutine profile — stacks of all current goroutines
go tool pprof http://localhost:6060/debug/pprof/goroutine

# Blocking profile — where goroutines are blocked
go tool pprof http://localhost:6060/debug/pprof/block
```

Once inside `go tool pprof`, the commands are:
- `top10` — show top 10 functions by CPU time or memory
- `web` — open a flame graph in your browser (requires Graphviz)
- `list functionName` — annotate source code with profile data

For a richer UI, pass the `-http` flag:

```bash
go tool pprof -http=:8888 http://localhost:6060/debug/pprof/heap
# Opens a browser with flame graphs, graph view, and source annotation
```

## In The Wild

Continuous profiling — capturing profiles automatically and storing them for comparison — is the production-grade approach. You catch regressions before users notice them. The open-source `pyroscope` project provides this for Go with minimal instrumentation:

```go
import "github.com/grafana/pyroscope-go"

func main() {
    if os.Getenv("PYROSCOPE_SERVER_ADDRESS") != "" {
        profiler, err := pyroscope.Start(pyroscope.Config{
            ApplicationName: "myapp",
            ServerAddress:   os.Getenv("PYROSCOPE_SERVER_ADDRESS"),
            ProfileTypes: []pyroscope.ProfileType{
                pyroscope.ProfileCPU,
                pyroscope.ProfileAllocObjects,
                pyroscope.ProfileAllocSpace,
                pyroscope.ProfileInuseObjects,
                pyroscope.ProfileInuseSpace,
            },
        })
        if err != nil {
            log.Printf("pyroscope: %v", err)
        }
        defer profiler.Stop()
    }

    // ... rest of main
}
```

With Pyroscope running, every deploy gets a continuous flame graph timeline. You can compare the CPU profile before and after a deploy and see exactly which functions changed.

For capturing profiles on specific endpoints to diagnose a latency spike, I use a script that captures everything at once:

```bash
#!/bin/bash
# capture-profiles.sh — capture all pprof profiles from a running pod
POD=${1:?usage: $0 <pod-name>}
OUTDIR="profiles-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUTDIR"

kubectl port-forward "pod/$POD" 6060:6060 &
PF_PID=$!
sleep 2  # wait for port-forward to establish

echo "Capturing profiles..."
curl -s "http://localhost:6060/debug/pprof/heap"         > "$OUTDIR/heap.pb.gz"
curl -s "http://localhost:6060/debug/pprof/goroutine"    > "$OUTDIR/goroutine.pb.gz"
curl -s "http://localhost:6060/debug/pprof/mutex"        > "$OUTDIR/mutex.pb.gz"
curl -s "http://localhost:6060/debug/pprof/profile?seconds=30" > "$OUTDIR/cpu.pb.gz"

kill $PF_PID
echo "Profiles saved to $OUTDIR/"
echo "Analyze with: go tool pprof -http=:8888 $OUTDIR/cpu.pb.gz"
```

## The Gotchas

**Heap profile shows allocations, not live objects.** The default heap profile (`/debug/pprof/heap`) captures `inuse_space` — currently live allocations. Pass `?gc=1` to trigger a GC first for a cleaner picture: `http://localhost:6060/debug/pprof/heap?gc=1`. The `allocs` profile (`/debug/pprof/allocs`) shows cumulative allocations since startup, which is better for finding allocation hotspots.

**Block and mutex profiles are off by default.** The block profiler measures where goroutines block on channel and sync operations. The mutex profiler measures mutex contention. Both are off by default because they add overhead. Enable them at startup with a fraction (1 = sample everything):

```go
import "runtime"

runtime.SetBlockProfileRate(1)    // enable block profiling
runtime.SetMutexProfileFraction(1) // enable mutex profiling
```

In production, use a smaller fraction like `runtime.SetMutexProfileFraction(10)` to sample 1 in 10 mutex contention events.

**Port-forward drops connections.** `kubectl port-forward` drops after idle timeout or network interruption. For long CPU profiles (30-60 seconds), this is usually fine. For anything longer, consider running the profile from within the cluster using an ephemeral debug container.

**Never expose the debug port publicly.** The pprof endpoints serve function names, file paths, and memory contents from your application. Bind to `127.0.0.1` in the container and use `kubectl port-forward` for access. Adding a Kubernetes NetworkPolicy that blocks ingress on port 6060 from outside the namespace is a good defense-in-depth measure.

## Key Takeaway

Go's pprof is always available — the `net/http/pprof` import and an internal HTTP server is all you need. Expose it on a separate port bound to localhost, access it via `kubectl port-forward`, and analyze it with `go tool pprof -http=:8888`. For production insight without manual intervention, add continuous profiling via Pyroscope or a similar system. Profile in production, where the real workload is — staging profiles are useful but never tell the whole story.

---

**Previous:** [Lesson 6: Race Detector in CI](/post/go/go-deploy-race-ci/)
**Next:** [Lesson 8: Zero-Downtime Deploys — Rolling updates without dropping requests](/post/go/go-deploy-zero-downtime/)
