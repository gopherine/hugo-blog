---
title: "Lesson 6: pprof Deep Dive — CPU, memory, goroutine — read all three"
author: Atharva Pandey
series: "Go Performance Engineering"
lesson: 6
keywords:
  - Go
  - golang
  - performance
  - pprof
  - CPU profiling
  - memory profiling
  - goroutine profiling
  - heap profile
tags:
  - Go tutorial
  - golang
  - performance
date: '2025-01-05T00:00:00.000Z'
---

When I first learned about `pprof`, I thought it was a single tool that told you "what's slow." It took me an embarrassingly long time to understand that it's actually a family of profilers — CPU, heap, allocs, goroutine, mutex, block — each answering a completely different question. Reading one profile and ignoring the others is like diagnosing engine trouble by only checking the oil. You might find something. You'll definitely miss something. The profilers are designed to work together, and once you start reading all three, performance diagnosis goes from guesswork to diagnosis.

## The Problem

Here's the scenario I've lived more than once: a service is slow, we add `net/http/pprof`, we grab a CPU profile, we stare at it, the hottest function is something we can't optimize, and we conclude "there's nothing to do here." Meanwhile, the service is leaking goroutines, memory is climbing, and every GC cycle is scanning twenty megabytes of live heap. The CPU profile wasn't lying — it was just answering the wrong question.

The three questions that matter:
- **CPU profile**: Where is the CPU time going? (sampling-based, wall clock)
- **Heap profile**: What is currently alive on the heap and who allocated it?
- **Goroutine profile**: How many goroutines are there and what are they all waiting on?

Getting all three and reading them as a system is how you actually understand a performance problem.

```go
// Minimal pprof server — add this to any long-running service
import (
    "net/http"
    _ "net/http/pprof" // registers /debug/pprof/* handlers as a side effect
)

func startDebugServer() {
    go func() {
        // Bind to localhost only — never expose this to the public internet
        http.ListenAndServe("localhost:6060", nil)
    }()
}
```

With this in place, the profiles are available at:
- `http://localhost:6060/debug/pprof/profile?seconds=30` — 30-second CPU profile
- `http://localhost:6060/debug/pprof/heap` — current heap snapshot
- `http://localhost:6060/debug/pprof/goroutine` — all goroutine stacks

## The Idiomatic Way

**Reading the CPU profile:**

```bash
$ go tool pprof http://localhost:6060/debug/pprof/profile?seconds=30
```

This runs for 30 seconds, sampling the program's call stacks at 100Hz, then drops you into an interactive shell. The commands I use every time:

- `top10` — shows the ten functions consuming the most CPU (flat and cumulative)
- `web` — opens a flame graph in the browser (requires Graphviz)
- `list FunctionName` — shows the source code with per-line sample counts

The key distinction is **flat** vs **cumulative** time. Flat time is time spent in that function's own instructions. Cumulative time includes all functions it called. A function with high cumulative but low flat is a coordinator — it doesn't do much work itself but calls things that do. A function with high flat time is a genuine hotspot.

```bash
(pprof) top10
Showing top 10 nodes out of 87 (cum >= 2.3s)
      flat  flat%   sum%        cum   cum%
     3.42s 34.20% 34.20%      3.42s 34.20%  runtime.cgocall
     1.28s 12.80% 47.00%      1.28s 12.80%  encoding/json.(*encodeState).marshal
     0.87s  8.70% 55.70%      2.15s 21.50%  net/http.(*Transport).roundTrip
```

**Reading the heap profile:**

The heap profile has two views: `inuse_space` (what's alive right now) and `alloc_space` (everything ever allocated since the process started). Start with `inuse_space` to understand current memory pressure:

```bash
$ go tool pprof -http=:8080 http://localhost:6060/debug/pprof/heap
```

The `-http` flag opens a browser UI with flame graphs, which are far easier to read than the text output for heap profiles. Look for the widest bars — they represent the most memory held. A function showing large `inuse_space` is where your live heap lives; that's your GC scanning cost and your memory usage.

**Reading the goroutine profile:**

```bash
$ curl http://localhost:6060/debug/pprof/goroutine?debug=2 > goroutines.txt
```

`debug=2` gives you the full stack trace for every goroutine. Open the file and look for:
1. An unexpectedly large goroutine count (anything above a few thousand usually warrants investigation)
2. Many goroutines blocked on the same operation (indicates contention or a goroutine leak)
3. Goroutines in `select` waiting on channels that may never receive (classic leak pattern)

## In The Wild

I was profiling an API gateway service that was showing elevated P95 latency during high traffic. The CPU profile showed `encoding/json.Marshal` as the top consumer at 28% flat time — reasonable for a gateway. The natural reaction would be to optimize JSON serialization. But before doing that, I pulled the heap profile.

The heap profile told a different story: 60% of live heap was owned by a caching layer that was supposed to be bounded at 10,000 entries. When I queried the goroutine profile, I found 2,300 goroutines — far more than our worker pool should have created. The goroutine dump showed hundreds blocked on `sync.Mutex.Lock` in the cache's eviction path.

The real problem was a lock contention bug in the cache's eviction routine. Because eviction was slow (blocked waiting for the lock), the cache grew unbounded. The growing heap increased GC scan time, which increased GC pause duration, which caused latency spikes. The CPU profile saw JSON marshaling as the "top" consumer simply because JSON was doing real work while the cache contention caused everything else to wait.

```go
// The lock contention pattern that caused the issue
type Cache struct {
    mu      sync.Mutex
    entries map[string]Entry
}

// evict held the lock for the entire LRU scan — O(n) under the lock
func (c *Cache) evict() {
    c.mu.Lock()
    defer c.mu.Unlock()
    // ... iterate all entries to find LRU candidate ...
}

// Fix: maintain a separate eviction data structure, shorten critical section
func (c *Cache) evict() {
    candidate := c.findEvictionCandidate() // outside the lock
    c.mu.Lock()
    delete(c.entries, candidate) // O(1) under the lock
    c.mu.Unlock()
}
```

The fix was a shorter critical section and a separate LRU list. Goroutine count dropped from 2,300 to ~200. Heap size stabilized. P95 latency halved — not because of any JSON optimization, but because the profiles together told the real story.

For thorough profiling sessions, I collect all three simultaneously during a load test:

```bash
# Run a 60-second CPU profile, heap snapshot, and goroutine dump
$ go tool pprof -http=:8081 http://localhost:6060/debug/pprof/profile?seconds=60 &
$ curl -o heap.prof http://localhost:6060/debug/pprof/heap
$ curl -o goroutines.txt "http://localhost:6060/debug/pprof/goroutine?debug=2"
```

## The Gotchas

**The heap profile shows allocations at the time of sampling, not the origin of the memory pressure.** If your service has a 30-second spike every 5 minutes, sampling the heap in between spikes will show you nothing. Time your profile collection to coincide with the symptom.

**CPU profiles can miss goroutines that are blocked.** The CPU sampler only captures goroutines that are actually executing on a CPU thread. A goroutine blocked on I/O, a channel, or a mutex doesn't show up in the CPU profile even if it's the root cause of your latency. The goroutine and block profiles fill this gap.

**`allocs` vs `heap` profile type.** The `allocs` profile (formerly called `alloc_space`) shows cumulative allocations — everything ever allocated. The `heap` profile in `inuse_space` mode shows what's live now. For understanding GC pressure, `inuse_space` is what matters. For finding where allocations come from in hot paths, `alloc_space` is more useful.

**Never expose `net/http/pprof` endpoints publicly.** The `/debug/pprof/` handlers expose your binary's structure, memory contents, and goroutine stacks. In production, either bind only to localhost, put it behind authentication, or use a separate internal network interface.

## Key Takeaway

`pprof` is not one tool — it's three distinct diagnoses that work as a system. The CPU profile tells you where time goes during execution. The heap profile tells you where memory is held and who holds it. The goroutine profile tells you what your goroutines are doing when they're not executing. A performance investigation that reads only one is incomplete. The most interesting performance bugs I've fixed were invisible in the CPU profile and obvious only when the heap and goroutine profiles were read alongside it. Start with all three; you can always narrow down from there.

---

[← Lesson 5: Benchmarking Done Right](/post/go/go-perf-benchmarking) | [Course Index](/series/go-performance-engineering) | [Next → Lesson 7: CPU vs Memory Tradeoffs](/post/go/go-perf-cpu-memory)
