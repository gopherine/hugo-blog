---
title: "Lesson 1: Escape Analysis — Where your data lives matters more than how you write it"
author: Atharva Pandey
series: "Go Performance Engineering"
lesson: 1
keywords:
  - Go
  - golang
  - performance
  - escape analysis
  - stack allocation
  - heap allocation
  - memory optimization
tags:
  - Go tutorial
  - golang
  - performance
date: '2024-06-20T00:00:00.000Z'
---

I spent the first two years of writing Go completely unaware that the compiler was making decisions about my code that I never asked for — and that those decisions were quietly shaping the performance profile of everything I shipped. Escape analysis is the mechanism behind all of it. Once I understood it, I started reading code differently. Not just "does this work?" but "where does this data live, and did I give the compiler a chance to put it somewhere fast?"

## The Problem

When you allocate a variable in Go, it ends up in one of two places: the stack or the heap. The stack is fast — allocation is just a pointer increment, and deallocation is free when the function returns. The heap requires the garbage collector's involvement: allocating, tracking, scanning, and eventually collecting the memory. The GC is good, but it isn't free, and under load the difference between a function that heap-allocates and one that doesn't can be measured in milliseconds per request.

Here's the thing nobody told me early on: **you don't choose where your variables live.** The compiler does. It runs a pass called escape analysis to decide whether a variable can safely live on the stack (and be freed automatically when the function returns) or whether it must "escape" to the heap because its lifetime outlives the stack frame.

The silent killer is code that looks innocent but forces heap allocation:

```go
// This looks fine — but where does Request live?
func handleRequest(id int) *Request {
    req := Request{ID: id, Timestamp: time.Now()}
    return &req
}
```

By returning a pointer to `req`, we've told the compiler: "this value needs to outlive this stack frame." The compiler has no choice but to allocate `req` on the heap. Every call to `handleRequest` is a heap allocation. In a hot path handling thousands of requests per second, that's thousands of GC-visible objects created and collected continuously.

## The Idiomatic Way

The first tool is `go build -gcflags='-m'`. It tells you exactly what the compiler decided and why:

```bash
$ go build -gcflags='-m' ./...
./main.go:8:2: moved to heap: req
./main.go:12:14: &req escapes to heap
```

That output is a map of your allocations. Every line that says "moved to heap" or "escapes to heap" is a place where the GC has to do work. Not all of them are problems — sometimes heap allocation is unavoidable and correct — but every one is worth understanding.

The idiomatic fix depends on the pattern. For the request example, the caller can own the allocation:

```go
// Caller owns the memory — stays on stack if it doesn't escape further
func fillRequest(req *Request, id int) {
    req.ID = id
    req.Timestamp = time.Now()
}

func handleRequest(id int) {
    var req Request          // stack-allocated
    fillRequest(&req, id)    // pass pointer in, not out
    process(req)             // pass by value or pointer — stays stack if process doesn't store it
}
```

Now `req` lives on `handleRequest`'s stack frame. When the function returns, that memory is gone — no GC involvement. The key insight is direction: pointers going *in* to a function are fine; pointers coming *out* of a function often escape.

The second pattern that causes silent escapes is interfaces:

```go
// COSTLY — interface boxing causes heap allocation
func logValue(v interface{}) {
    fmt.Println(v)
}

func main() {
    n := 42
    logValue(n) // n escapes to heap — boxed into interface{}
}
```

Any time a concrete value is assigned to an interface, the compiler must create a heap-allocated "box" to hold it (for values larger than a pointer). `fmt.Println` accepts `...interface{}`, which is why it's one of the biggest sources of allocations in hot paths. In benchmarks I've written, replacing a single `fmt.Sprintf` call in a tight loop with a pre-built string or a `strings.Builder` approach cut allocations by more than half.

## In The Wild

In a high-throughput event processing pipeline I worked on, every event object was being returned as a pointer from a constructor. The code looked clean and conventional:

```go
func NewEvent(kind string, payload []byte) *Event {
    return &Event{Kind: kind, Payload: payload, Time: time.Now()}
}
```

After running `go build -gcflags='-m'` and then a heap profile with `pprof`, I found this function was responsible for about 40% of all heap allocations. The fix was a `sync.Pool` for reuse combined with a value-return pattern for the cases where the event didn't escape the processing goroutine:

```go
var eventPool = sync.Pool{
    New: func() interface{} { return &Event{} },
}

func acquireEvent(kind string, payload []byte) *Event {
    e := eventPool.Get().(*Event)
    e.Kind = kind
    e.Payload = payload
    e.Time = time.Now()
    return e
}

func releaseEvent(e *Event) {
    e.Kind = ""
    e.Payload = nil
    eventPool.Put(e)
}
```

GC pause duration dropped by roughly 30% under peak load because we stopped creating tens of thousands of short-lived heap objects per second. The `sync.Pool` lets the GC reclaim objects between GC cycles, but avoids repeated allocation in the common case.

## The Gotchas

**More `-m` flags give more detail.** `-gcflags='-m -m'` (two `-m` flags) prints the full escape analysis reasoning, not just the conclusion. It's verbose but invaluable when you can't figure out *why* something escapes.

**Closures capture variables by reference.** A variable referenced inside a closure that outlives the function will escape:

```go
func makeCounter() func() int {
    count := 0           // escapes to heap — closure captures it
    return func() int {
        count++
        return count
    }
}
```

`count` escapes because the returned closure holds a reference to it. This is correct behavior — it's just worth knowing you're paying for a heap allocation every time you call `makeCounter`.

**Interface conversions in loops are expensive.** If you're converting values to `interface{}` inside a hot loop — for logging, for generic processing — benchmark it. The boxing cost compounds fast.

**`go vet` won't warn you.** Escape analysis results are not surfaced as warnings by default. You have to ask for them explicitly with the build flags. Make it part of your profiling habit, not an afterthought.

## Key Takeaway

Escape analysis is the compiler's answer to the question "does this variable outlive its function?" Your job as a performance-conscious Go developer is to help the compiler answer "no" as often as possible — by keeping pointers pointing inward rather than outward, avoiding unnecessary interface boxing in hot paths, and using the `-gcflags='-m'` output as a first-pass diagnostic before reaching for `pprof`. Where your data lives matters more than how clean your code looks — but with practice, the two don't have to conflict.

---

[Course Index](/series/go-performance-engineering) | [Next → Lesson 2: Stack vs Heap Intuition](/post/go/go-perf-stack-heap)
