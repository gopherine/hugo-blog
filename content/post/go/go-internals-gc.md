---
title: "Lesson 5: GC Behavior and Tuning — GOGC and GOMEMLIMIT changed the game"
author: Atharva Pandey
series: "Go Memory Model & Internals"
lesson: 5
keywords: [Go, golang, internals, memory]
tags: [Go tutorial, golang, internals]
date: '2024-11-15T00:00:00.000Z'
---

For years, tuning Go's GC meant tweaking `GOGC` and hoping for the best. I operated on vibes and guesswork. Then Go 1.19 introduced `GOMEMLIMIT` — a hard memory limit that fundamentally changed how I reason about GC tuning. Suddenly I had a second axis of control that actually matched how production memory is constrained. This lesson is the mental model I wish I had from day one.

## The Problem

Go uses a concurrent, tri-color mark-and-sweep garbage collector. "Concurrent" means most of the GC work happens while your program is running, not in stop-the-world pauses. This is generally excellent, but the GC needs to be triggered somehow, and the default trigger can be surprising.

The classic problem is the "memory saw" pattern: your service allocates memory during a request burst, the GC runs, memory drops, then the cycle repeats. If the GC triggers too frequently, you burn CPU on collection. If it triggers too rarely, you hold too much live memory and risk OOM kills in container environments.

Most developers reach for `GOGC` and turn it up without fully understanding the trade-off. Since Go 1.19, the right tool is often `GOMEMLIMIT` instead — or a combination of both.

## The Idiomatic Way

Let's start with `GOGC`. The default value is 100, meaning: "trigger a GC cycle when the total heap size doubles from the size of live objects at the end of the last GC." So if you have 50MB of live objects after a collection, the next collection triggers when the heap reaches 100MB.

Setting `GOGC=200` means the heap can grow to 3x the live size before triggering. Less frequent GC, higher peak memory usage. `GOGC=50` means 1.5x — more frequent GC, lower peak memory.

```go
package main

import (
    "fmt"
    "runtime"
    "runtime/debug"
)

func printMemStats(label string) {
    var ms runtime.MemStats
    runtime.ReadMemStats(&ms)
    fmt.Printf("%s: HeapAlloc=%dMB HeapSys=%dMB NumGC=%d\n",
        label,
        ms.HeapAlloc/1024/1024,
        ms.HeapSys/1024/1024,
        ms.NumGC,
    )
}

func allocateMB(n int) [][]byte {
    chunks := make([][]byte, n)
    for i := range chunks {
        chunks[i] = make([]byte, 1024*1024) // 1MB each
    }
    return chunks
}

func main() {
    // See current GOGC
    current := debug.SetGCPercent(-1) // -1 reads without changing
    debug.SetGCPercent(current)
    fmt.Printf("Current GOGC: %d\n", current)

    printMemStats("before")

    // Allocate 50MB — GC will trigger based on GOGC
    data := allocateMB(50)
    printMemStats("after 50MB alloc")

    // Force a GC cycle
    runtime.GC()
    printMemStats("after forced GC")

    // data still referenced — won't be collected
    _ = data

    // Release reference — eligible for collection
    data = nil
    runtime.GC()
    printMemStats("after nil + GC")
}
```

Now `GOMEMLIMIT`, introduced in Go 1.19 via the `runtime/debug` package and the `GOMEMLIMIT` environment variable:

```go
package main

import (
    "fmt"
    "runtime"
    "runtime/debug"
)

func main() {
    // Set a memory limit of 100MB
    // This is the total memory Go's runtime may use
    limit := int64(100 * 1024 * 1024)
    debug.SetMemoryLimit(limit)

    // Or via environment variable: GOMEMLIMIT=100MiB

    var ms runtime.MemStats

    // The GC will now run more aggressively as we approach the limit
    // rather than purely relying on the GOGC ratio
    for i := 0; i < 5; i++ {
        data := make([]byte, 20*1024*1024) // 20MB
        runtime.ReadMemStats(&ms)
        fmt.Printf("Alloc %d: HeapAlloc=%dMB NumGC=%d\n",
            i+1,
            ms.HeapAlloc/1024/1024,
            ms.NumGC,
        )
        _ = data
    }

    // Read the currently set limit
    currentLimit := debug.SetMemoryLimit(-1)
    fmt.Printf("Memory limit: %dMB\n", currentLimit/1024/1024)
}
```

The key difference: `GOGC` is a **ratio-based trigger** (heap can grow to X% of live size). `GOMEMLIMIT` is an **absolute ceiling** — the GC will run as aggressively as needed to stay under the limit, even temporarily disabling the `GOGC` ratio if necessary. This prevents OOM kills in container environments where memory is hard-capped.

A common production pattern is to set `GOGC=off` (disable ratio-based collection entirely) and rely purely on `GOMEMLIMIT`. This reduces GC frequency for memory-abundant services while still preventing OOM:

```go
package main

import (
    "fmt"
    "os"
    "runtime"
    "runtime/debug"
    "strconv"
)

func configureGC() {
    // Pattern: disable GOGC ratio, use memory limit instead
    // Good for services where latency matters more than memory efficiency
    // and the container has a known memory limit

    if limitStr := os.Getenv("GOMEMLIMIT"); limitStr != "" {
        // Already set via env — just disable GOGC ratio
        debug.SetGCPercent(-1) // -1 disables ratio-based collection
        fmt.Println("GC: ratio disabled, limit-only mode")
    } else {
        // Fallback: set a reasonable limit programmatically
        // Typically 90% of the container memory limit
        containerLimit := int64(512 * 1024 * 1024) // 512MB container
        debug.SetMemoryLimit(containerLimit * 9 / 10) // 90% = 460MB
        debug.SetGCPercent(-1)
        fmt.Printf("GC: limit set to %dMB, ratio disabled\n",
            containerLimit*9/10/1024/1024)
    }

    // Verify settings
    var ms runtime.MemStats
    runtime.ReadMemStats(&ms)
    gcPercent, _ := strconv.Atoi(os.Getenv("GOGC"))
    fmt.Printf("Initial heap: %dMB, GOGC env: %d\n",
        ms.HeapAlloc/1024/1024, gcPercent)
}

func main() {
    configureGC()
}
```

## In The Wild

In real services, I use `GOMEMLIMIT` to match my Kubernetes pod memory limit, leaving a 10-15% buffer for non-heap allocations (goroutine stacks, OS-level memory). Here's a pattern for observing GC behavior in a long-running service:

```go
package main

import (
    "fmt"
    "runtime"
    "runtime/debug"
    "time"
)

type GCStats struct {
    NumGC       uint32
    PauseTotal  time.Duration
    LastPause   time.Duration
    HeapAlloc   uint64
    HeapInUse   uint64
    StackInUse  uint64
    GCCPUFraction float64
}

func collectGCStats() GCStats {
    var ms runtime.MemStats
    runtime.ReadMemStats(&ms)

    var lastPause time.Duration
    if ms.NumGC > 0 {
        lastPause = time.Duration(ms.PauseNs[(ms.NumGC+255)%256])
    }

    return GCStats{
        NumGC:         ms.NumGC,
        PauseTotal:    time.Duration(ms.PauseTotalNs),
        LastPause:     lastPause,
        HeapAlloc:     ms.HeapAlloc,
        HeapInUse:     ms.HeapInuse,
        StackInUse:    ms.StackInuse,
        GCCPUFraction: ms.GCCPUFraction,
    }
}

func monitorGC(interval time.Duration, done <-chan struct{}) {
    ticker := time.NewTicker(interval)
    defer ticker.Stop()

    prev := collectGCStats()
    for {
        select {
        case <-ticker.C:
            curr := collectGCStats()
            if curr.NumGC > prev.NumGC {
                fmt.Printf(
                    "GC: cycles=%d lastPause=%v heapAlloc=%dMB gcCPU=%.2f%%\n",
                    curr.NumGC-prev.NumGC,
                    curr.LastPause,
                    curr.HeapAlloc/1024/1024,
                    curr.GCCPUFraction*100,
                )
            }
            prev = curr
        case <-done:
            return
        }
    }
}

func main() {
    // Set memory limit
    debug.SetMemoryLimit(200 * 1024 * 1024)

    done := make(chan struct{})
    go monitorGC(500*time.Millisecond, done)

    // Simulate workload
    for i := 0; i < 10; i++ {
        data := make([]byte, 30*1024*1024)
        time.Sleep(100 * time.Millisecond)
        _ = data
    }

    time.Sleep(time.Second)
    close(done)
}
```

## The Gotchas

**`GOMEMLIMIT` includes all Go memory, not just heap**: the limit covers the heap, goroutine stacks, and runtime metadata. If you set it equal to your container limit with no headroom, you can still OOM because the OS and non-Go libraries also need memory. Always leave 10-20% headroom.

**`GOGC=-1` with no `GOMEMLIMIT` is dangerous**: if you disable ratio-based collection and forget to set a memory limit, the GC will basically never run (until the runtime forces it), and your service will eat all available memory. Always pair them.

**GC pauses are not zero with concurrent collection**: the "stop-the-world" pauses in modern Go are very short (sub-millisecond for most programs), but they're not zero. Stack scanning still requires a brief STW phase. For latency-sensitive services, you should measure p99/p999 latency, not just averages:

```go
package main

import (
    "fmt"
    "runtime"
    "sort"
    "time"
)

func measureGCPauses(n int) []time.Duration {
    var ms runtime.MemStats
    runtime.ReadMemStats(&ms)
    startGC := ms.NumGC

    // Trigger n GC cycles
    for i := 0; i < n; i++ {
        runtime.GC()
    }

    runtime.ReadMemStats(&ms)
    pauses := make([]time.Duration, 0, ms.NumGC-startGC)
    for i := startGC; i < ms.NumGC && i < startGC+256; i++ {
        idx := i % 256
        if ms.PauseNs[idx] > 0 {
            pauses = append(pauses, time.Duration(ms.PauseNs[idx]))
        }
    }
    return pauses
}

func percentile(durations []time.Duration, p float64) time.Duration {
    if len(durations) == 0 {
        return 0
    }
    sort.Slice(durations, func(i, j int) bool {
        return durations[i] < durations[j]
    })
    idx := int(float64(len(durations)) * p / 100)
    if idx >= len(durations) {
        idx = len(durations) - 1
    }
    return durations[idx]
}

func main() {
    pauses := measureGCPauses(20)
    fmt.Printf("p50 GC pause: %v\n", percentile(pauses, 50))
    fmt.Printf("p99 GC pause: %v\n", percentile(pauses, 99))
    fmt.Printf("max GC pause: %v\n", percentile(pauses, 100))
}
```

## Key Takeaway

Go's GC uses two distinct tuning levers. `GOGC` controls the ratio between live heap size and the total heap size that triggers collection — it trades memory for CPU (higher GOGC = less frequent GC = more memory used). `GOMEMLIMIT` sets an absolute ceiling — the GC will run as aggressively as needed to stay under it, acting as a safety net against OOM.

For production services running in containers:
- Set `GOMEMLIMIT` to ~85-90% of your container memory limit
- Consider `GOGC=off` for latency-sensitive services with sufficient memory
- Monitor `GCCPUFraction` — if the GC is consuming more than 5% of CPU, you have an allocation problem worth addressing at the code level
- Use `runtime.ReadMemStats` to observe GC behavior; export key metrics to your APM system
- Remember that the best GC tuning is reducing allocations in the first place (Lesson 4 is your friend)

---

**Series: Go Memory Model & Internals**

[← Lesson 4: Escape Analysis Deep Dive](../go-internals-escape-analysis) | [Lesson 6: Memory Alignment and Struct Padding →](../go-internals-alignment)
