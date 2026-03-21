---
title: "Lesson 2: Stack vs Heap Intuition — The allocation you didn't know you made"
author: Atharva Pandey
series: "Go Performance Engineering"
lesson: 2
keywords:
  - Go
  - golang
  - performance
  - stack allocation
  - heap allocation
  - memory model
  - GC pressure
tags:
  - Go tutorial
  - golang
  - performance
date: '2024-07-16T00:00:00.000Z'
---

There's a class of performance bugs in Go that doesn't show up in code review, doesn't trigger the race detector, and doesn't cause test failures. It just makes your service slowly worse under load. The source is almost always the same: heap allocations happening in places you didn't intend, turning what should be fast stack operations into GC-visible objects that pile up until the collector has to stop and clean them up. Building an intuition for when Go allocates on the heap versus the stack was one of the single highest-leverage things I did to improve the services I work on.

## The Problem

Most developers who come from garbage-collected languages don't think about allocation at all — the runtime handles it, and you should trust it. That's reasonable advice until you're handling twenty thousand requests per second and your GC pause metrics are climbing. Then allocation discipline becomes the difference between a P99 of 10ms and 80ms.

The core model is this: every goroutine has its own stack, which starts small (around 2KB in modern Go) and grows as needed. Variables that live only within a function call — and whose addresses don't outlive that call — live on the stack. Variables whose lifetime extends beyond their declaring function, or whose address is passed somewhere the compiler can't track, escape to the heap.

The confusion comes from the fact that Go syntax doesn't tell you which is which. This compiles and runs identically whether `x` is on the stack or the heap:

```go
x := make([]int, 10)
```

Whether that slice's backing array escapes depends entirely on what you do with `x` afterwards. If you return it, pass it to a goroutine, store it in a struct that escapes — heap. If you use it locally and it goes out of scope — stack (for small enough sizes). The compiler decides. You influence it.

Here's a concrete example of an allocation you probably didn't realize was happening:

```go
// Hidden allocation #1: appending to nil slice in a loop
func buildIDs(n int) []string {
    var ids []string
    for i := 0; i < n; i++ {
        ids = append(ids, fmt.Sprintf("id-%d", i))
    }
    return ids
}
```

This has two allocation issues. First, `fmt.Sprintf` heap-allocates the formatted string. Second, every time `append` grows the slice beyond its capacity, it allocates a new backing array on the heap and copies the old one. For `n=1000`, you might have 10+ backing array allocations plus 1000 string allocations.

## The Idiomatic Way

Build a mental model around three rules: **values escape when their address is shared outward, when they're stored in interfaces, and when they're too large for the stack.** Everything else is noise until you have profiling data telling you otherwise.

For the slice example, pre-allocating capacity eliminates the intermediate allocations:

```go
// Pre-allocate: one backing array, not ten
func buildIDs(n int) []string {
    ids := make([]string, 0, n) // capacity hint avoids reallocations
    for i := 0; i < n; i++ {
        ids = append(ids, strconv.Itoa(i)) // no heap alloc per call for small ints
    }
    return ids
}
```

`strconv.Itoa` for small integers uses a pre-allocated buffer internally (Go 1.19+), avoiding the per-call allocation. For the general formatting case, `strconv.AppendInt` lets you build into an existing buffer:

```go
func buildIDsNoAlloc(n int) []string {
    ids := make([]string, n)
    var buf [20]byte // stack-allocated scratch buffer
    for i := 0; i < n; i++ {
        b := strconv.AppendInt(buf[:0], int64(i), 10)
        ids[i] = string(b) // one allocation per string, unavoidable
    }
    return ids
}
```

The `buf` array is on the stack — it's a fixed-size local that never escapes. We reuse it every iteration for the integer formatting, then convert to string once. One heap allocation per element, not two or three.

For understanding what the compiler actually decided, use the build flag alongside a benchmark:

```go
func BenchmarkBuildIDs(b *testing.B) {
    for i := 0; i < b.N; i++ {
        _ = buildIDs(100)
    }
}
```

```bash
$ go test -bench=BenchmarkBuildIDs -benchmem
BenchmarkBuildIDs-8    42315    27891 ns/op    6432 B/op    201 allocs/op

$ go test -bench=BenchmarkBuildIDsNoAlloc -benchmem
BenchmarkBuildIDsNoAlloc-8    89204    13412 ns/op    3216 B/op    100 allocs/op
```

The `-benchmem` flag is the most underused tool in the Go performance toolkit. It shows allocations per operation directly — no inference needed.

## In The Wild

I was profiling an HTTP middleware layer that was adding non-trivial latency to every request. The middleware wrapped a `ResponseWriter` to capture the status code:

```go
// Seemingly harmless wrapper
type statusRecorder struct {
    http.ResponseWriter
    status int
}

func (r *statusRecorder) WriteHeader(code int) {
    r.status = code
    r.ResponseWriter.WriteHeader(code)
}

func withLogging(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        rec := &statusRecorder{ResponseWriter: w} // heap allocation on every request
        next.ServeHTTP(rec, r)
        log.Printf("status: %d", rec.status)
    })
}
```

Every request created a `statusRecorder` on the heap. At ten thousand requests per second, that's ten thousand allocations per second, continuously. The fix was a `sync.Pool`:

```go
var recorderPool = sync.Pool{
    New: func() interface{} { return &statusRecorder{} },
}

func withLogging(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        rec := recorderPool.Get().(*statusRecorder)
        rec.ResponseWriter = w
        rec.status = http.StatusOK
        defer func() {
            log.Printf("status: %d", rec.status)
            rec.ResponseWriter = nil
            recorderPool.Put(rec)
        }()
        next.ServeHTTP(rec, r)
    })
}
```

GC object churn dropped immediately. Latency at the 95th percentile fell by about 12ms during peak traffic — entirely from eliminating one avoidable allocation per request.

## The Gotchas

**Stack size is not unlimited.** Go stacks grow dynamically up to a default max of 1GB, but growth itself has cost. If your goroutine's stack needs to grow, Go allocates a new, larger stack and copies everything. This is rare in practice but can become a factor if you have deep recursion or very large stack frames.

**Large local variables get suspicious.** The compiler may choose to heap-allocate very large local variables even if they don't technically escape, because placing them on the stack risks stack overflow or frequent growth. Arrays larger than roughly 64KB tend to escape regardless of how you use them.

**`make` with a non-constant size often escapes.** `make([]T, n)` where `n` is a runtime variable typically escapes to the heap because the compiler can't know the size at compile time for stack allocation. `make([]T, 10)` with a constant may stay on the stack if it doesn't escape for other reasons.

**The `any` type (alias for `interface{}`) is a heap pressure magnet.** Every concrete value assigned to `any` that is larger than a pointer gets boxed. This includes `int`, `string`, `bool` on many architectures. If you use `any` in a hot path, benchmark it.

## Key Takeaway

Stack and heap aren't implementation details you can ignore — they're the foundation of your program's allocation behavior, which is the foundation of your GC behavior, which is the foundation of your tail latency. You don't need to obsess over every variable. But you should know the three things that send a value to the heap — address shared outward, interface boxing, too large for the stack — and you should check `-benchmem` output before declaring a hot path "done." Most allocations I've eliminated in production weren't obvious until I measured them, and once measured, the fix was usually ten lines or fewer.

---

[← Lesson 1: Escape Analysis](/post/go/go-perf-escape-analysis) | [Course Index](/series/go-performance-engineering) | [Next → Lesson 3: Slice and Map Performance](/post/go/go-perf-slice-map)
