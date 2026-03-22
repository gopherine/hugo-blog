---
title: 'Lesson 2: Virtual Memory — 1GB RSS but Only 50MB is Real'
author: Atharva Pandey
series: "Linux for Backend Engineers"
lesson: 2
keywords:
  - linux
  - virtual memory
  - RSS
  - memory management
  - page table
  - backend engineering
tags:
  - fundamentals
  - linux
  - operating systems
date: '2024-05-12T00:00:00.000Z'
---

Early in my career I deployed a Go service and checked `top`. The `VIRT` column showed 1.2 GB. I nearly had a heart attack — our server had 4 GB of RAM and I thought the service was consuming nearly a third of it. A senior engineer laughed and told me to look at `RES` instead: 52 MB. I had no idea what the difference was. Understanding virtual memory is fundamental to reading memory metrics correctly, debugging out-of-memory kills, and understanding how processes interact with the kernel.

## How It Actually Works

Every process on Linux runs in its own virtual address space — a contiguous range of 64-bit addresses (0 to 128 TB in practice on x86-64) that the process believes it owns entirely. These virtual addresses are not physical RAM. They are mapped to physical RAM pages (4KB each) via a multi-level **page table** maintained by the kernel.

The CPU's Memory Management Unit (MMU) translates virtual addresses to physical addresses on every memory access using the page table. The Translation Lookaside Buffer (TLB) is a CPU cache for recent translations — cache misses trigger a page table walk.

Key concepts:

**Virtual address space (VIRT in `top`)**: the total range of addresses a process has mapped. This includes:
- The code segment (executable text)
- Data and BSS segments (global variables)
- Heap (dynamically allocated memory)
- Stack for each thread
- Memory-mapped files (shared libraries, `mmap()` calls)
- Anonymous mappings (goroutine stacks are often mmap'd)

A process can map 1 GB of address space without a single byte of physical RAM allocated — the pages just aren't backed yet.

**Resident Set Size (RSS / RES in `top`)**: the amount of physical RAM the process is currently using. Pages that have been written to or read from. This is the metric that actually matters for "how much RAM is this process using."

**Page faults**: when a process accesses a virtual address that isn't backed by physical memory yet, the CPU triggers a page fault. The kernel:
1. Finds or allocates a physical page
2. Updates the page table mapping
3. Resumes the process

**Copy-on-Write (CoW)**: when a process forks, the child gets a copy of the parent's page table but shares the same physical pages. Pages are only copied when either process writes to them. This is why `fork()` is cheap even for large processes.

Here is a Go program that demonstrates the VIRT vs RSS gap:

```go
package main

import (
    "fmt"
    "os"
    "runtime"
    "syscall"
)

func showMemStats() {
    var ms runtime.MemStats
    runtime.ReadMemStats(&ms)
    fmt.Printf("Go heap in use: %.1f MB\n", float64(ms.HeapInuse)/1024/1024)
    fmt.Printf("Go heap sys:    %.1f MB\n", float64(ms.HeapSys)/1024/1024)
    fmt.Printf("Go total sys:   %.1f MB\n", float64(ms.Sys)/1024/1024)

    // Read /proc/self/status for kernel view
    data, _ := os.ReadFile("/proc/self/status")
    fmt.Printf("\n/proc/self/status excerpt:\n%s", extractMemLines(string(data)))
}

func extractMemLines(status string) string {
    // In practice: parse VmRSS, VmSize, VmPeak from the file
    return status // simplified
}

func main() {
    fmt.Println("=== Before large allocation ===")
    showMemStats()

    // mmap 500MB — this reserves virtual address space but touches no physical pages
    mem, err := syscall.Mmap(-1, 0, 500*1024*1024,
        syscall.PROT_READ|syscall.PROT_WRITE,
        syscall.MAP_ANON|syscall.MAP_PRIVATE)
    if err != nil {
        panic(err)
    }

    fmt.Println("\n=== After mmap 500MB (virtual reserved, no physical pages) ===")
    showMemStats()
    // VIRT increases by ~500MB; RSS barely changes

    // Now touch every page — force physical allocation
    for i := 0; i < len(mem); i += 4096 {
        mem[i] = 1
    }

    fmt.Println("\n=== After touching all pages (physical RAM now allocated) ===")
    showMemStats()
    // Now RSS also increases by ~500MB
    syscall.Munmap(mem)
}
```

## Why It Matters

Reading memory metrics correctly:

| Metric | Meaning | When it matters |
|--------|---------|-----------------|
| `VIRT` | Virtual address space | Mostly noise for analysis |
| `RES` / `RSS` | Physical RAM in use | Primary memory consumption metric |
| `SHR` | Shared pages (libraries) | Shared across processes — not "extra" RAM |
| `%MEM` | RSS / total RAM | Useful for OOM risk assessment |

**OOM Killer**: the Linux kernel's Out of Memory killer activates when physical RAM (plus swap) is exhausted. It selects a process to kill based on `oom_score` (roughly: large RSS + low priority = higher score). Your Go service showing 1 GB VIRT but 50 MB RSS is not at risk. A service with 3.5 GB RSS on a 4 GB server is.

**Go's memory model**: Go's garbage collector returns memory to the OS (as of Go 1.12, MADV_FREE; as of 1.16, MADV_DONTNEED by default). This means RSS can drop after GC runs. `runtime.FreeOSMemory()` forces an immediate return.

## Production Example

When investigating memory leaks in production, these are the metrics to watch:

```go
// Expose memory stats via HTTP for debugging
func memStatsHandler(w http.ResponseWriter, r *http.Request) {
    var ms runtime.MemStats
    runtime.ReadMemStats(&ms)

    fmt.Fprintf(w, "HeapAlloc:   %.2f MB\n", float64(ms.HeapAlloc)/1024/1024)
    fmt.Fprintf(w, "HeapSys:     %.2f MB\n", float64(ms.HeapSys)/1024/1024)
    fmt.Fprintf(w, "HeapIdle:    %.2f MB\n", float64(ms.HeapIdle)/1024/1024)
    fmt.Fprintf(w, "HeapInuse:   %.2f MB\n", float64(ms.HeapInuse)/1024/1024)
    fmt.Fprintf(w, "HeapObjects: %d\n", ms.HeapObjects)
    fmt.Fprintf(w, "StackInuse:  %.2f MB\n", float64(ms.StackInuse)/1024/1024)
    fmt.Fprintf(w, "GoroutineN:  %d\n", runtime.NumGoroutine())
    fmt.Fprintf(w, "NumGC:       %d\n", ms.NumGC)
    fmt.Fprintf(w, "PauseTotalNs:%.2f ms\n", float64(ms.PauseTotalNs)/1e6)
}
```

A genuine memory leak in Go looks like `HeapAlloc` and `HeapObjects` growing continuously across GC cycles. A spike in `HeapInuse` that drops after GC is normal allocation pressure.

Reading `/proc/<pid>/smaps` shows exactly which virtual memory regions are mapped, their sizes, and how much RSS each contributes — useful for understanding where large virtual mappings come from:

```bash
# Find the largest virtual memory regions for your Go process
cat /proc/$(pgrep myservice)/smaps | awk '/^Size:/{size=$2} /^Rss:/{print size " -> " $2}' | sort -n | tail -20
```

## The Tradeoffs

**`GOGC` tuning**: the Go GC triggers when heap grows by `GOGC`% (default 100%) since the last collection. Lower `GOGC` means more frequent GC and lower RSS; higher `GOGC` means less frequent GC and higher RSS but lower CPU overhead. For memory-constrained environments, `GOGC=50` may be appropriate.

**`GOMEMLIMIT`** (Go 1.19+): sets a soft limit on the Go heap. The GC becomes more aggressive as usage approaches the limit. This prevents OOM kills without requiring precise `GOGC` tuning:

```go
import "runtime/debug"

func init() {
    // Soft limit at 512MB — GC aggressively keeps heap below this
    debug.SetMemoryLimit(512 * 1024 * 1024)
}
```

**Shared libraries inflate VIRT**: every shared library (`.so`) loaded by the process appears in its virtual address space. A typical Go binary statically links most dependencies, so VIRT is mostly actual allocations — but CGo-heavy programs load many `.so` files.

## Key Takeaway

Virtual memory lets every process believe it has a massive private address space. Physical RAM is only allocated when pages are actually accessed. `VIRT` tells you virtual address space reserved; `RSS` tells you physical RAM actually consumed. For Go services, watch `HeapAlloc` and `HeapObjects` across GC cycles to detect leaks. Set `GOMEMLIMIT` to prevent OOM kills. Ignore VIRT.

---

**Previous:** [Lesson 1: Processes and Threads](/post/fundamentals/linux-processes/) | **Next:** [Lesson 3: File Descriptors — Why Too Many Open Files Kills Your Server](/post/fundamentals/linux-file-descriptors/)
