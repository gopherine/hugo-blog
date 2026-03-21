---
title: "Lesson 6: Memory Alignment and Struct Padding — Field order affects struct size"
author: Atharva Pandey
series: "Go Memory Model & Internals"
lesson: 6
keywords: [Go, golang, internals, memory]
tags: [Go tutorial, golang, internals]
date: '2025-01-08T00:00:00.000Z'
---

I once reviewed a PR where someone had defined a struct with a bool field between two int64 fields, resulting in a 24-byte struct instead of the 17 bytes you might naively calculate. When I pointed it out, the response was "the compiler should handle that." It doesn't. Go respects the field order you give it, and it inserts padding to satisfy alignment requirements. Knowing the rules means you write efficient structs by default.

## The Problem

CPUs access memory most efficiently when values are at addresses that are multiples of their size. A 4-byte `int32` should sit at an address divisible by 4. An 8-byte `int64` should sit at an address divisible by 8. When a field in a struct would fall at a misaligned address, the compiler inserts **padding** bytes before it.

This padding is invisible in your source code but very real in memory. It means two structs with identical fields but different field orders can have dramatically different sizes. In programs that allocate millions of these structs — or use them in arrays — the difference is meaningful.

The two consequences worth caring about:
1. **Memory waste**: padding bytes are allocated but carry no useful data
2. **Cache efficiency**: a smaller struct fits more items in a cache line (64 bytes on x86), improving iteration performance

## The Idiomatic Way

Go's alignment rules are straightforward:

- Each type has an alignment requirement equal to its size (up to 8 bytes on 64-bit systems)
- `bool`, `int8`, `uint8`, `byte`: alignment 1
- `int16`, `uint16`: alignment 2
- `int32`, `uint32`, `float32`: alignment 4
- `int64`, `uint64`, `float64`, pointer, `uintptr`: alignment 8
- Structs: alignment equal to their largest field's alignment
- Arrays: alignment equal to the element type's alignment

The compiler adds padding before a field to bring its offset to a multiple of its alignment requirement. It also adds padding at the end of the struct so that array elements are properly aligned.

```go
package main

import (
    "fmt"
    "unsafe"
)

// BAD ordering: 24 bytes due to padding
type BadLayout struct {
    Active bool    // 1 byte at offset 0
    // 7 bytes padding (to align Count to 8)
    Count  int64   // 8 bytes at offset 8
    Score  float32 // 4 bytes at offset 16
    // 4 bytes padding (to align struct size to 8)
}

// GOOD ordering: 16 bytes, no wasted padding
type GoodLayout struct {
    Count  int64   // 8 bytes at offset 0
    Score  float32 // 4 bytes at offset 8
    Active bool    // 1 byte at offset 12
    // 3 bytes padding (struct alignment is 8, size must be multiple of 8)
}

// OPTIMAL: pack bool with other small types together
type OptimalLayout struct {
    Count  int64   // 8 bytes at offset 0
    Score  float32 // 4 bytes at offset 8
    Active bool    // 1 byte at offset 12
    Flags  byte    // 1 byte at offset 13
    // 2 bytes padding
}

func printLayout(name string, size, align uintptr) {
    fmt.Printf("%-20s size=%d  align=%d\n", name, size, align)
}

func main() {
    printLayout("BadLayout",     unsafe.Sizeof(BadLayout{}),     unsafe.Alignof(BadLayout{}))
    printLayout("GoodLayout",    unsafe.Sizeof(GoodLayout{}),    unsafe.Alignof(GoodLayout{}))
    printLayout("OptimalLayout", unsafe.Sizeof(OptimalLayout{}), unsafe.Alignof(OptimalLayout{}))

    // Inspect field offsets
    var g GoodLayout
    fmt.Printf("\nGoodLayout field offsets:\n")
    fmt.Printf("  Count:  %d\n", unsafe.Offsetof(g.Count))
    fmt.Printf("  Score:  %d\n", unsafe.Offsetof(g.Score))
    fmt.Printf("  Active: %d\n", unsafe.Offsetof(g.Active))
}
```

The rule of thumb for ordering fields: **largest to smallest**. Put your 8-byte fields first, then 4-byte, then 2-byte, then 1-byte. This naturally minimizes padding because each field's starting offset is already a multiple of its alignment.

```go
package main

import (
    "fmt"
    "unsafe"
)

// Real-world example: a network packet header
// BAD: 48 bytes
type PacketHeaderBad struct {
    SrcPort  uint16 // 2 bytes at 0
    // 6 bytes padding
    Seq      uint64 // 8 bytes at 8
    DstPort  uint16 // 2 bytes at 16
    // 6 bytes padding
    Ack      uint64 // 8 bytes at 24
    Flags    byte   // 1 byte at 32
    // 7 bytes padding
    Window   uint64 // 8 bytes at 40
}

// GOOD: 32 bytes — saves 16 bytes per packet header
type PacketHeaderGood struct {
    Seq      uint64 // 8 bytes at 0
    Ack      uint64 // 8 bytes at 8
    Window   uint64 // 8 bytes at 16
    SrcPort  uint16 // 2 bytes at 24
    DstPort  uint16 // 2 bytes at 26
    Flags    byte   // 1 byte at 28
    // 3 bytes padding
}

func main() {
    fmt.Printf("PacketHeaderBad:  %d bytes\n", unsafe.Sizeof(PacketHeaderBad{}))
    fmt.Printf("PacketHeaderGood: %d bytes\n", unsafe.Sizeof(PacketHeaderGood{}))
    // PacketHeaderBad:  48 bytes
    // PacketHeaderGood: 32 bytes
    // 33% reduction in memory for a high-frequency struct
}
```

## In The Wild

This matters most for structs allocated in large quantities — items in slices, keys/values in maps, goroutine-local state. Let's measure the real impact:

```go
package main

import (
    "fmt"
    "runtime"
    "time"
)

type EventBad struct {
    Timestamp int64   // 8
    Type      byte    // 1
    // 7 padding
    UserID    int64   // 8
    Payload   byte    // 1
    // 7 padding
    SequenceN int64   // 8
    // total: 40 bytes
}

type EventGood struct {
    Timestamp int64 // 8 at 0
    UserID    int64 // 8 at 8
    SequenceN int64 // 8 at 16
    Type      byte  // 1 at 24
    Payload   byte  // 1 at 25
    // 6 padding
    // total: 32 bytes
}

func benchmarkSlice[T any](name string, constructor func(i int) T, n int) {
    var before, after runtime.MemStats
    runtime.GC()
    runtime.ReadMemStats(&before)

    start := time.Now()
    data := make([]T, n)
    for i := range data {
        data[i] = constructor(i)
    }
    elapsed := time.Since(start)

    runtime.ReadMemStats(&after)
    allocated := after.TotalAlloc - before.TotalAlloc

    fmt.Printf("%-20s n=%d alloc=%dMB time=%v\n",
        name, n, allocated/1024/1024, elapsed)
    _ = data
}

func main() {
    const N = 1_000_000

    benchmarkSlice("EventBad (40B)", func(i int) EventBad {
        return EventBad{Timestamp: int64(i), UserID: int64(i), SequenceN: int64(i)}
    }, N)

    benchmarkSlice("EventGood (32B)", func(i int) EventGood {
        return EventGood{Timestamp: int64(i), UserID: int64(i), SequenceN: int64(i)}
    }, N)
    // EventGood uses 80% of EventBad's memory — 20% savings just from field reordering
}
```

Another place alignment matters: **false sharing in concurrent programs**. When two goroutines write to adjacent fields in a struct, they may write to the same CPU cache line (64 bytes), causing the cache line to bounce between cores. The solution is to pad hot fields to cache line boundaries:

```go
package main

import (
    "fmt"
    "runtime"
    "sync"
    "sync/atomic"
    "time"
    "unsafe"
)

const cacheLineSize = 64

// Without padding: Counter fields may share a cache line
type CounterNoPad struct {
    hits   int64
    misses int64
}

// With padding: each counter occupies its own cache line
type CounterPadded struct {
    hits    int64
    _       [cacheLineSize - unsafe.Sizeof(int64(0))]byte
    misses  int64
    _       [cacheLineSize - unsafe.Sizeof(int64(0))]byte
}

func benchmarkCounter(name string, hits, misses *int64, n int) time.Duration {
    var wg sync.WaitGroup
    wg.Add(2)
    start := time.Now()

    go func() {
        defer wg.Done()
        for i := 0; i < n; i++ {
            atomic.AddInt64(hits, 1)
        }
    }()
    go func() {
        defer wg.Done()
        for i := 0; i < n; i++ {
            atomic.AddInt64(misses, 1)
        }
    }()

    wg.Wait()
    elapsed := time.Since(start)
    fmt.Printf("%-25s: hits=%d misses=%d time=%v\n", name, *hits, *misses, elapsed)
    return elapsed
}

func main() {
    runtime.GOMAXPROCS(2)
    const N = 10_000_000

    c1 := &CounterNoPad{}
    benchmarkCounter("CounterNoPad", &c1.hits, &c1.misses, N)

    c2 := &CounterPadded{}
    benchmarkCounter("CounterPadded", &c2.hits, &c2.misses, N)
    // CounterPadded is typically 2-4x faster due to no false sharing
}
```

## The Gotchas

**Embedded structs and alignment**: when you embed a struct, its alignment requirement is promoted to the outer struct. This can cause surprising padding:

```go
package main

import (
    "fmt"
    "unsafe"
)

type Header struct {
    Magic   uint32
    Version uint16
    Flags   uint16
} // 8 bytes, align 4

type MessageBad struct {
    ID     int64   // 8 at 0
    Header         // embedded: 8 at 8
    Seq    int64   // 8 at 16
    Short  int16   // 2 at 24
    // 6 bytes padding
} // 32 bytes

type MessageGood struct {
    ID    int64  // 8 at 0
    Seq   int64  // 8 at 8
    Header       // embedded: 8 at 16
    Short int16  // 2 at 24
    // 6 bytes padding
} // 32 bytes — same in this case, but embedding position matters for larger structs

func main() {
    fmt.Println(unsafe.Sizeof(MessageBad{}))
    fmt.Println(unsafe.Sizeof(MessageGood{}))
}
```

**The `fieldalignment` linter**: the Go tools ecosystem has a `fieldalignment` linter in `golang.org/x/tools/go/analysis/passes/fieldalignment` that automatically reports suboptimally ordered structs and suggests the optimal ordering. Run it as part of your CI pipeline for large codebases:

```bash
go install golang.org/x/tools/go/analysis/passes/fieldalignment/cmd/fieldalignment@latest
fieldalignment ./...
# Output: struct with N pointer bytes could be M (fix with fieldalignment -fix)
```

**Zero-size fields**: a `struct{}` has zero size, but if it's the last field in a struct, Go may add padding to ensure the struct's size is at least 1 and pointers past the end are valid. This is a subtle edge case that matters if you use zero-size types as map values or embed them for type safety.

## Key Takeaway

Go does not reorder struct fields for you — it respects the order you write and inserts padding bytes to satisfy alignment requirements. The alignment requirement of a type equals its size (up to 8 bytes). A struct's total size must be a multiple of its largest field's alignment.

The practical approach:
- Order fields from largest to smallest to minimize padding: 8-byte fields first, then 4-byte, then 2-byte, then 1-byte
- Use `unsafe.Sizeof` and `unsafe.Offsetof` to inspect actual sizes and padding
- Run the `fieldalignment` linter on large codebases to catch wasteful layouts automatically
- For high-contention concurrent data, pad hot fields to 64-byte cache line boundaries to prevent false sharing
- For serialized formats (binary protocols, shared memory), explicit field ordering is not just a performance concern — it's a correctness requirement

---

**Series: Go Memory Model & Internals**

[← Lesson 5: GC Behavior and Tuning](../go-internals-gc) | [Lesson 7: Values Copy vs Share →](../go-internals-copy-share)
