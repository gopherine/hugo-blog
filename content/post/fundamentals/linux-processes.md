---
title: 'Lesson 1: Processes and Threads — What Goroutines Map To'
author: Atharva Pandey
series: "Linux for Backend Engineers"
lesson: 1
keywords:
  - linux
  - processes
  - threads
  - goroutines
  - OS scheduler
  - backend engineering
tags:
  - fundamentals
  - linux
  - operating systems
date: '2024-04-25T00:00:00.000Z'
---

When I first started using Go seriously, I accepted goroutines as "lightweight threads" without really understanding what that meant. The Go runtime creates them, schedules them, and I launch them with `go`. Then I got curious: what does the OS actually see? When I run 10,000 goroutines, does the kernel manage 10,000 things? The answer is no — and understanding why requires understanding the difference between processes, kernel threads, and userspace threads. It also explains why goroutines scale so much better than Java threads or Python threads.

## How It Actually Works

**Process**: an independent execution environment with its own virtual address space, file descriptor table, signal handlers, and resources. Creating a process (`fork()`) copies the parent's address space. Each process has at least one thread.

**Kernel thread (OS thread)**: a unit of CPU scheduling managed by the kernel. The kernel sees threads, not processes — from the scheduler's perspective, a thread is the thing that gets CPU time. Creating a kernel thread (`clone()` with `CLONE_THREAD`) is cheaper than forking a process but still involves a syscall, kernel-managed stack (typically 8 MB per thread), and kernel scheduler tracking.

**Userspace thread (goroutine)**: managed entirely by the Go runtime, not the kernel. The kernel has no knowledge of individual goroutines. The runtime multiplexes goroutines onto a smaller pool of OS threads.

Go's scheduler is an M:N scheduler — M goroutines multiplexed onto N OS threads (where N ≈ `GOMAXPROCS`, defaulting to the number of CPU cores).

```
Goroutines (millions possible)
    ↓ scheduled by Go runtime
OS Threads (GOMAXPROCS — typically 8–16)
    ↓ scheduled by Linux kernel
CPU Cores
```

Here is a simplified view of what the Go runtime does when you write `go f()`:

```go
// Conceptually, the runtime does something like:
type G struct {          // goroutine descriptor
    stack    Stack       // 2KB initial, grows as needed
    status   GStatus     // running, runnable, waiting, ...
    m        *M          // OS thread currently running this G (if any)
    sched    gobuf        // saved registers for context switch
    goexit   uintptr
}

type M struct {          // OS thread (machine)
    g0       *G          // scheduling goroutine (stack for scheduler itself)
    curg     *G          // current goroutine
    p        *P          // logical processor — holds runqueue
    spinning bool
}

type P struct {          // logical processor
    runq     [256]*G     // local run queue (lock-free ring buffer)
    runnext  *G          // next goroutine to run (cache)
    mcache   *mcache     // per-P memory allocation cache
}
```

The runtime's scheduler runs goroutines cooperatively (at function calls, channel operations, syscalls) and preemptively (since Go 1.14, signals preempt long-running goroutines). When a goroutine blocks on a syscall, the runtime parks it and moves the OS thread to another goroutine — or creates a new OS thread if needed.

## Why It Matters

The practical consequences:

**Goroutine stack is 2KB vs. 8MB for OS threads**: you can create 100,000 goroutines in a few hundred megabytes of RAM. Creating 100,000 OS threads would require 800 GB of stack space alone.

**Goroutine context switch is cheap**: the runtime switches goroutines by saving a handful of registers (the `gobuf`). An OS thread context switch involves a kernel trap, saving the full CPU context, and potentially a TLB flush. Goroutine switches are measured in nanoseconds; OS thread switches in microseconds.

**Blocking syscalls are handled transparently**: when a goroutine makes a blocking syscall (like reading from a file), the runtime detaches the goroutine from the OS thread, runs other goroutines on that thread, and creates a new OS thread if needed. Your code writes blocking-style I/O; the runtime makes it non-blocking behind the scenes.

## Production Example

Understanding the process/thread model helps debug several common production issues:

```go
package main

import (
    "fmt"
    "runtime"
    "sync"
)

func main() {
    // Check number of OS threads in use
    fmt.Printf("GOMAXPROCS: %d\n", runtime.GOMAXPROCS(0))
    fmt.Printf("NumCPU: %d\n", runtime.NumCPU())
    fmt.Printf("NumGoroutine: %d\n", runtime.NumGoroutine())

    var wg sync.WaitGroup

    // Launch 10,000 goroutines — OS only sees ~8 threads
    for i := 0; i < 10_000; i++ {
        wg.Add(1)
        go func(n int) {
            defer wg.Done()
            // simulate work
            result := 0
            for j := 0; j < 1000; j++ {
                result += j
            }
            _ = result
        }(i)
    }

    wg.Wait()
    fmt.Printf("After: NumGoroutine: %d\n", runtime.NumGoroutine())
}
```

To check what the OS actually sees, run `ps -T -p <pid>` (threads per process) or `cat /proc/<pid>/status | grep Threads` while the program runs. You'll see O(GOMAXPROCS) threads, not O(10,000).

When goroutines are blocked on C code or blocking syscalls, the runtime creates new OS threads to keep other goroutines running. This is where the OS thread count can grow unexpectedly:

```go
// This can cause OS thread count to grow — each CGo call may need its own thread
// while the blocking C call executes
import "C"

func callBlockingC() {
    // runtime creates a new OS thread to service other goroutines
    // while this goroutine is stuck in C
    C.blocking_network_call()
}
```

Monitor OS thread count with the `runtime` package or `go tool pprof` goroutine profiles:

```go
// Useful in a health check endpoint
http.HandleFunc("/debug/runtime", func(w http.ResponseWriter, r *http.Request) {
    var ms runtime.MemStats
    runtime.ReadMemStats(&ms)
    fmt.Fprintf(w, "goroutines=%d\n", runtime.NumGoroutine())
    // runtime doesn't expose OS thread count directly — use pprof
})
```

## The Tradeoffs

**`runtime.LockOSThread()`**: some operations (CGo, certain system calls) must run on a specific OS thread. This pins a goroutine to an OS thread for its lifetime — that OS thread is no longer available for other goroutines. Use sparingly.

**GOMAXPROCS and CPU-bound work**: for CPU-bound work, set `GOMAXPROCS` to the number of available cores. For I/O-bound work, it matters less because goroutines spend most time waiting, not burning CPU.

**Too many goroutines with large stacks**: goroutines start with a 2KB stack but it grows dynamically. A goroutine with deep recursion can have a large stack. 100,000 goroutines each with a 1 MB stack is 100 GB. Profile goroutine stacks if memory grows unexpectedly.

**Goroutine leaks**: a goroutine blocked on a channel or waiting for a context that never fires is a leak. It doesn't consume CPU but it does consume memory (stack) and contributes to garbage collector overhead. Lesson 1 of the Go Concurrency Masterclass covers this in depth.

## Key Takeaway

Goroutines are userspace threads managed by the Go runtime, not the kernel. The runtime's M:N scheduler multiplexes many goroutines onto a small pool of OS threads equal to `GOMAXPROCS`. This gives goroutines cheap creation (2KB stack), cheap context switching (no kernel trap), and transparent handling of blocking I/O. The kernel sees a handful of threads; your program can have millions of goroutines. Understanding this model explains goroutine scaling, blocking behavior, and how to reason about resource consumption.

---

**Next:** [Lesson 2: Virtual Memory — 1GB RSS but Only 50MB is Real](/post/fundamentals/linux-virtual-memory/)
