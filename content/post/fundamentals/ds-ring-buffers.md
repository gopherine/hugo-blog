---
title: "Lesson 12: Ring Buffers — Fixed-size queues for real-time systems"
author: Atharva Pandey
series: "Data Structures for Production Engineers"
lesson: 12
keywords: [data structures, computer science, ring buffer, circular buffer, FIFO, lock-free, real-time]
tags: [fundamentals, data structures]
date: '2024-09-16T00:00:00.000Z'
---

The ring buffer is the data structure that makes real-time systems possible. Audio processing, network packet capture, kernel I/O buffers, metrics collection — anywhere you have a producer and a consumer that need to exchange data with zero allocation and bounded latency, you'll find a ring buffer.

It's also one of the most elegant structures in systems programming: a fixed-size array, two indices, and one invariant. Let me show you how it works and why it appears everywhere from Linux kernel drivers to Disruptor (the LMAX exchange's million-transactions-per-second queue).

## How It Actually Works

A ring buffer (circular buffer) is a fixed-size array where write and read positions wrap around when they reach the end. Think of it as a clock face — after position 11 comes position 0 again.

Two indices: `head` (next write position) and `tail` (next read position). When they're equal, the buffer is empty. When `(head + 1) % size == tail`, the buffer is full.

```go
package main

import (
    "errors"
    "fmt"
)

type RingBuffer[T any] struct {
    data []T
    head int // next write position
    tail int // next read position
    size int
    full bool
}

func NewRingBuffer[T any](capacity int) *RingBuffer[T] {
    return &RingBuffer[T]{
        data: make([]T, capacity),
        size: capacity,
    }
}

func (rb *RingBuffer[T]) Push(val T) error {
    if rb.full {
        return errors.New("buffer full")
    }
    rb.data[rb.head] = val
    rb.head = (rb.head + 1) % rb.size
    rb.full = rb.head == rb.tail
    return nil
}

func (rb *RingBuffer[T]) Pop() (T, error) {
    var zero T
    if rb.IsEmpty() {
        return zero, errors.New("buffer empty")
    }
    val := rb.data[rb.tail]
    rb.tail = (rb.tail + 1) % rb.size
    rb.full = false
    return val, nil
}

func (rb *RingBuffer[T]) IsEmpty() bool {
    return !rb.full && rb.head == rb.tail
}

func (rb *RingBuffer[T]) Len() int {
    if rb.full {
        return rb.size
    }
    if rb.head >= rb.tail {
        return rb.head - rb.tail
    }
    return rb.size - rb.tail + rb.head
}

func main() {
    rb := NewRingBuffer[int](4)

    rb.Push(1); rb.Push(2); rb.Push(3); rb.Push(4)
    fmt.Println("Full:", rb.full)

    v, _ := rb.Pop()
    fmt.Println("Popped:", v) // 1 — FIFO order

    rb.Push(5) // wraps around to position 0
    fmt.Println("Length:", rb.Len())

    for !rb.IsEmpty() {
        v, _ := rb.Pop()
        fmt.Printf("%d ", v)
    }
    // 2 3 4 5
}
```

The critical property: both `Push` and `Pop` are O(1) with zero allocation. There's no dynamic resizing, no copying, no GC pressure. The same backing array is reused forever.

## When to Use It

Use a ring buffer when:
- You need a bounded FIFO queue with no allocation overhead
- You have a real-time constraint (audio processing, network capture, metrics)
- Producer and consumer run at different speeds and you want to absorb bursts
- You want to drop old data when the buffer fills (sliding window over a stream)
- You need lock-free SPSC (single-producer, single-consumer) communication between goroutines

Don't use a ring buffer when:
- The queue size is truly unbounded (use a dynamic queue or channel)
- You need random access by index (use a slice)
- You need ordered iteration without consuming (use a deque)

## Production Example

The single-producer, single-consumer (SPSC) ring buffer is a gold standard for inter-goroutine communication without locks. With only one writer and one reader, you can use atomic operations instead of a mutex — eliminating lock contention entirely:

```go
package main

import (
    "fmt"
    "sync/atomic"
    "time"
    "unsafe"
)

// Lock-free SPSC ring buffer using atomic head/tail
// Only safe for exactly one goroutine writing and one goroutine reading
type SPSCBuffer struct {
    data []int64
    size uint64
    _    [56]byte  // padding to separate cache lines
    head uint64    // written by producer only
    _    [56]byte
    tail uint64    // written by consumer only
    _    [56]byte
}

func NewSPSCBuffer(size uint64) *SPSCBuffer {
    return &SPSCBuffer{
        data: make([]int64, size),
        size: size,
    }
}

func (b *SPSCBuffer) Push(val int64) bool {
    head := atomic.LoadUint64(&b.head)
    tail := atomic.LoadUint64(&b.tail)
    if head-tail >= b.size {
        return false // full
    }
    b.data[head%b.size] = val
    atomic.StoreUint64(&b.head, head+1)
    return true
}

func (b *SPSCBuffer) Pop() (int64, bool) {
    tail := atomic.LoadUint64(&b.tail)
    head := atomic.LoadUint64(&b.head)
    if tail == head {
        return 0, false // empty
    }
    val := b.data[tail%b.size]
    atomic.StoreUint64(&b.tail, tail+1)
    return val, true
}

// Demonstrate padding effect: false sharing vs isolated cache lines
var _ = unsafe.Sizeof(SPSCBuffer{}) // prevent import elision

func main() {
    buf := NewSPSCBuffer(1024)
    produced := 0
    consumed := 0
    done := make(chan struct{})

    // Producer goroutine
    go func() {
        for i := int64(0); i < 1_000_000; i++ {
            for !buf.Push(i) {
                // spin if full — in real code, use backoff
            }
            produced++
        }
    }()

    // Consumer goroutine
    go func() {
        for consumed < 1_000_000 {
            if _, ok := buf.Pop(); ok {
                consumed++
            }
        }
        close(done)
    }()

    start := time.Now()
    <-done
    elapsed := time.Since(start)

    fmt.Printf("Transferred 1M messages in %v\n", elapsed)
    fmt.Printf("Throughput: %.1f M msg/sec\n", 1.0/elapsed.Seconds())
}
```

The padding between `head` and `tail` is critical. Both are written by different goroutines. Without padding, they'd share a cache line, and each write would invalidate the other goroutine's cache — false sharing from Lesson 1. With 56 bytes of padding between them (filling out a 64-byte cache line), each goroutine has its own cache line for its index.

Now let's look at a sliding window use case — keeping the last N metrics without allocating:

```go
package main

import "fmt"

// SlidingWindow maintains the last N values and computes statistics
type SlidingWindow struct {
    data  []float64
    head  int
    count int
    size  int
}

func NewSlidingWindow(size int) *SlidingWindow {
    return &SlidingWindow{
        data: make([]float64, size),
        size: size,
    }
}

func (sw *SlidingWindow) Add(val float64) {
    sw.data[sw.head] = val
    sw.head = (sw.head + 1) % sw.size
    if sw.count < sw.size {
        sw.count++
    }
}

func (sw *SlidingWindow) Average() float64 {
    if sw.count == 0 {
        return 0
    }
    sum := 0.0
    for i := 0; i < sw.count; i++ {
        sum += sw.data[i]
    }
    return sum / float64(sw.count)
}

func (sw *SlidingWindow) Max() float64 {
    if sw.count == 0 {
        return 0
    }
    max := sw.data[0]
    for i := 1; i < sw.count; i++ {
        if sw.data[i] > max {
            max = sw.data[i]
        }
    }
    return max
}

func main() {
    // Keep last 5 latency measurements
    window := NewSlidingWindow(5)

    latencies := []float64{12, 15, 8, 200, 11, 9, 13, 14}
    for _, l := range latencies {
        window.Add(l)
        fmt.Printf("Added %.0fms — avg: %.1fms, max: %.0fms\n",
            l, window.Average(), window.Max())
    }
}
```

This pattern appears in rate limiters, circuit breakers, and monitoring agents — compute rolling statistics over a stream without unbounded memory growth.

## The Tradeoffs

**Fixed size means you must make a decision about overflow.** When the buffer fills, you have two choices: block the producer (backpressure) or overwrite the oldest entry (lossy). Which is right depends on your application. A network packet capture tool should drop new packets when full. An audio buffer should block the producer and wait for the consumer to catch up.

**Power-of-two sizes are faster.** Modulo arithmetic (`head % size`) can be replaced with bitwise AND (`head & (size-1)`) when size is a power of two. This is a meaningful optimization in tight loops:

```go
// Slower: general modulo
idx := head % size

// Faster: bitwise mask (requires size to be a power of two)
const size = 1024 // must be power of 2
mask := uint64(size - 1)
idx := head & mask
```

**The SPSC assumption is fragile.** Lock-free SPSC ring buffers are only safe with exactly one producer and one consumer. If two goroutines both call `Push` concurrently, they'll race on `head`. For MPMC (multiple-producer, multiple-consumer), you need a more complex design — the LMAX Disruptor pattern, for example, uses a sequence-based claim mechanism.

**Memory visibility ordering.** The atomic stores and loads in the SPSC example provide the memory ordering guarantees that make cross-goroutine communication safe. A plain assignment to `head` without atomics would be a data race on most architectures because of CPU reordering and cache coherency delays.

## Key Takeaway

The ring buffer is the data structure of real-time systems. Its properties — O(1) operations, zero allocation, predictable memory usage — are exactly what's needed when you can't afford to pause for garbage collection or block on a mutex. The SPSC variant is one of the fastest inter-thread communication mechanisms available, and the false-sharing-aware padding trick is a prime example of how hardware knowledge translates directly to performance.

Everywhere you see "circular buffer," "ring buffer," or "fixed-size queue" in systems code — from Linux kernel drivers to Disruptor to audio frameworks — this is the structure underneath.

---

*Previous: [Lesson 11: Skip Lists — How Redis sorted sets work](/post/fundamentals/ds-skip-lists/)*

---

## Course Complete

You've made it through all 12 lessons in **Data Structures for Production Engineers**. Here's what we covered:

| Lesson | Topic | Key Insight |
|--------|-------|-------------|
| 1 | Arrays | Cache lines decide performance |
| 2 | Linked Lists | Almost never the right choice |
| 3 | Hash Maps | O(1) with asterisks |
| 4 | Stacks & Queues | Hiding in every system |
| 5 | Trees & BSTs | Why databases use trees |
| 6 | B-Trees | How indexes actually work |
| 7 | Heaps | Scheduling and top-K |
| 8 | Graphs | You use them daily |
| 9 | Tries | Prefix matching at scale |
| 10 | Bloom Filters | Probably yes, definitely no |
| 11 | Skip Lists | How Redis sorted sets work |
| 12 | Ring Buffers | Real-time systems foundation |

The goal was never to teach you algorithms in isolation — it was to show you the *why* behind systems you already use. Every data structure here appears in production software you depend on every day. Recognizing the structure behind a system is the first step to understanding and improving it.
