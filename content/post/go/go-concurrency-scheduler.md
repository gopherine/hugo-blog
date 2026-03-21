---
title: 'Lesson 17: Go Scheduler Behavior — M:N scheduling is not magic'
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 17
keywords:
  - Go
  - golang
  - concurrency
  - Go scheduler
  - goroutine scheduler
  - GOMAXPROCS
  - G M P model
  - preemption
  - work stealing
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-10-08T00:00:00.000Z'
---

Most Go developers write concurrent code for years without thinking about the scheduler. That's by design — the scheduler is supposed to be invisible. But eventually you'll hit a situation where goroutines aren't running when you expect them to, CPU cores are idle while goroutines pile up, or a CPU-bound workload is somehow slower with more goroutines. At that point you need a mental model of what's actually happening under the hood.

Go uses M:N scheduling — M goroutines multiplexed onto N OS threads. You get to create millions of goroutines; the runtime maps them onto a small pool of threads. Understanding why this works, and when it breaks down, makes you a better concurrent programmer.

## The Problem

The most common scheduler-related mistake is assuming that "more goroutines means more parallelism":

```go
// WRONG — spinning up goroutines equal to work items on a CPU-bound task
func computeHashes(data [][]byte) [][]byte {
    results := make([][]byte, len(data))
    var wg sync.WaitGroup

    for i, d := range data {
        wg.Add(1)
        go func(idx int, input []byte) {
            defer wg.Done()
            results[idx] = sha256sum(input) // pure CPU work
        }(i, d)
    }

    wg.Wait()
    return results
}
```

If `data` has a million entries, you've queued a million goroutines. The scheduler has to manage all of them — context-switching, stack allocation, queue management. For I/O-bound work, this is fine because most goroutines are parked waiting for I/O and don't consume CPU. For pure CPU-bound work, you're adding scheduling overhead on top of the actual computation. The sweet spot is `GOMAXPROCS` goroutines — one per available CPU thread.

The second mistake is writing a tight compute loop with no preemption points:

```go
// WRONG — tight loop that holds a P hostage (pre-Go 1.14)
func spinForever() {
    for {
        // no function calls, no channel ops, no syscalls
        x := heavyMathWithNoFunctionCalls()
        _ = x
    }
}
```

Before Go 1.14, this goroutine would never yield. It holds its OS thread (M) and processor (P) indefinitely, starving other goroutines that need to run. Even post-1.14 asynchronous preemption, understanding why this was a problem helps you write more cooperative code.

## The Idiomatic Way

Understanding the G-M-P model first:

- **G (Goroutine):** a lightweight execution context with its own stack, starting at 2KB
- **M (Machine):** an OS thread — the actual thing that runs on a CPU core
- **P (Processor):** a scheduling context, a local run queue of Gs. There are exactly `GOMAXPROCS` Ps

Every M needs a P to run goroutines. A P has a local queue of Gs it wants to run. When a G blocks on a syscall, its M is detached from the P so the P can pick up another M (or create one) and keep running other Gs. This is why Go can handle thousands of blocking I/O operations — each blocked G surrenders its M and P.

For CPU-bound work, limit goroutines to the number of Ps:

```go
// RIGHT — worker pool sized to GOMAXPROCS for CPU-bound work
func computeHashes(data [][]byte) [][]byte {
    results := make([][]byte, len(data))
    jobs := make(chan int, len(data))

    numWorkers := runtime.GOMAXPROCS(0) // 0 means "query, don't set"
    var wg sync.WaitGroup

    for w := 0; w < numWorkers; w++ {
        wg.Add(1)
        go func() {
            defer wg.Done()
            for idx := range jobs {
                results[idx] = sha256sum(data[idx])
            }
        }()
    }

    for i := range data {
        jobs <- i
    }
    close(jobs)

    wg.Wait()
    return results
}
```

`runtime.GOMAXPROCS(0)` returns the current value without changing it. You get exactly as many workers as there are CPU threads available. No scheduling overhead, maximum parallelism.

For long-running CPU-bound goroutines, add explicit yield points:

```go
// RIGHT — yield periodically so other goroutines get scheduled
func longCompute(data []int) int {
    sum := 0
    for i, v := range data {
        sum += expensiveOp(v)
        if i%1000 == 0 {
            runtime.Gosched() // voluntarily yield the processor
        }
    }
    return sum
}
```

`runtime.Gosched()` is a yield — it puts the current goroutine at the back of the run queue and lets the scheduler run something else. Use it in long loops that don't have natural I/O or channel operations. In practice, Go 1.14+ asynchronous preemption handles this for you in most cases, but explicit yields document intent and remain important in tight assembly-like loops.

## In The Wild

`GOMAXPROCS` tuning is a real production lever. The default is `runtime.NumCPU()` — all available CPUs. But in containerized environments, `NumCPU()` often reports the host machine's CPU count, not the container's CPU limit. A container with a 2-CPU limit running with `GOMAXPROCS=64` wastes memory on unused P structures and causes excessive context switching.

```go
// Kubernetes-aware GOMAXPROCS using automaxprocs
import _ "go.uber.org/automaxprocs"

// That's it — import the package and it reads the cgroup CPU quota
// and sets GOMAXPROCS appropriately at init time
```

`go.uber.org/automaxprocs` reads `/sys/fs/cgroup` CPU quotas and sets `GOMAXPROCS` to the container's actual CPU allocation. I add this import to every service that runs in Kubernetes. It's a one-liner that's prevented several performance regressions.

The work-stealing scheduler means you rarely need to worry about load balancing between Ps. When a P's local run queue is empty, it steals half the queue from another P. This keeps all Ps busy automatically. But it also means the order in which goroutines run is intentionally unpredictable — don't write tests that depend on goroutine execution order.

## The Gotchas

**`runtime.LockOSThread()` and forgetting to unlock.** Some APIs — OpenGL, certain CGo libraries — require that all calls happen on the same OS thread. `runtime.LockOSThread()` pins a goroutine to its M. If that goroutine exits without calling `runtime.UnlockOSThread()`, that M is permanently consumed. Leak enough of them and your program starves of OS threads.

```go
// RIGHT — always unlock, even on panics
func runOnLockedThread(fn func()) {
    runtime.LockOSThread()
    defer runtime.UnlockOSThread()
    fn()
}
```

**Blocking syscalls absorb Ms.** The scheduler handles blocking I/O by parking the G and detaching the M from the P. But there's a limit — `runtime.GOMAXPROCS` threads are the normal pool, but the runtime can create additional threads for blocked syscalls up to a hard limit (default 10,000). If you have thousands of goroutines all blocked in long-running CGo calls simultaneously, you can exhaust the thread limit.

**Setting `GOMAXPROCS(1)` doesn't make code race-free.** A single P means only one goroutine runs at a time, but the scheduler can still switch between goroutines at any preemption point. Two goroutines can still interleave their operations. `GOMAXPROCS(1)` eliminates true parallelism but doesn't eliminate concurrency bugs.

**Goroutine stacks start small but grow.** Each goroutine starts with a 2KB stack that grows dynamically as needed (up to 1GB by default). The growth is automatic, but it isn't free. Hot paths that call deeply recursive functions in new goroutines cause more stack copying. This is rarely a problem in practice but explains why benchmarks of goroutine-heavy code sometimes show unexpected allocations.

## Key Takeaway

The Go scheduler is a cooperative-plus-preemptive M:N scheduler built around the G-M-P model. Goroutines are cheap to create because they don't map 1:1 to OS threads — Ps multiplex Gs onto Ms. For I/O-bound work, create as many goroutines as you want; blocked Gs release their P. For CPU-bound work, more goroutines than `GOMAXPROCS` adds overhead without adding parallelism — size your worker pools to `runtime.GOMAXPROCS(0)`. In containerized deployments, set `GOMAXPROCS` to match the container's CPU limit, not the host's. Once you have this model in your head, scheduler behavior stops being mysterious.

---

← [Previous: Atomic Operations](/post/go/go-concurrency-atomics) | [Course Index](/post/go/) | [Next: Backpressure Design](/post/go/go-concurrency-backpressure) →
