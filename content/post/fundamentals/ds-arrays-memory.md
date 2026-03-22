---
title: "Lesson 1: Arrays and Memory Layout — Cache lines decide your performance"
author: Atharva Pandey
series: "Data Structures for Production Engineers"
lesson: 1
keywords: [data structures, computer science, arrays, memory layout, cache lines]
tags: [fundamentals, data structures]
date: '2024-04-15T00:00:00.000Z'
---

I spent two years writing Go services before I genuinely understood why iterating over a two-dimensional slice in the wrong order could tank my throughput by 5x. It wasn't a bug. It wasn't a bad algorithm. It was cache lines.

Arrays are the first data structure everyone learns and the last one most engineers actually understand. This is my attempt to fix that — not with theory, but with the reasoning that makes you a better systems engineer.

## How It Actually Works

An array is a contiguous block of memory. That's it. When you declare `[8]int64` in Go, you're reserving 64 bytes in a straight line — no pointers, no indirection, just raw memory addresses laid out sequentially.

```go
package main

import (
    "fmt"
    "unsafe"
)

func main() {
    arr := [4]int64{10, 20, 30, 40}

    for i := 0; i < 4; i++ {
        ptr := uintptr(unsafe.Pointer(&arr[0])) + uintptr(i)*8
        fmt.Printf("arr[%d] = %d, address = 0x%x\n", i, arr[i], ptr)
    }
}
// arr[0] = 10, address = 0xc0000b4000
// arr[1] = 20, address = 0xc0000b4008
// arr[2] = 30, address = 0xc0000b4010
// arr[3] = 40, address = 0xc0000b4018
```

Each element is exactly 8 bytes apart because `int64` is 8 bytes. This predictability is the whole point.

Now here's the thing your CS professor glossed over: your CPU doesn't read one value at a time from RAM. It reads in **cache lines** — chunks of 64 bytes on virtually every modern processor. When you touch `arr[0]`, the CPU fetches all of `arr[0]` through `arr[7]` (8 × 8 bytes = 64 bytes) into L1 cache in a single operation. The next 7 accesses are essentially free — they're already sitting in cache.

This is called **spatial locality**, and it's why arrays are fast even when O(n) algorithms seem "worse" than O(log n) alternatives.

## When to Use It

Use arrays (or slices backed by arrays) when:

- You know the size upfront or can bound it reasonably
- You're iterating sequentially — reading all elements, computing sums, filtering
- You're doing numeric work: matrix operations, time-series data, sensor readings
- You're implementing other data structures (the backing store for queues, hash tables, heaps)

Avoid arrays when:
- You need frequent insertions or deletions in the middle (O(n) shifts)
- You're building something inherently pointer-linked (trees, graphs)

## Production Example

Here's the cache line effect in practice. Consider two ways to sum a 2D matrix:

```go
package main

import (
    "fmt"
    "time"
)

const N = 4096

var matrix [N][N]int64

func sumRowMajor() int64 {
    var total int64
    for i := 0; i < N; i++ {
        for j := 0; j < N; j++ {
            total += matrix[i][j] // row by row — sequential access
        }
    }
    return total
}

func sumColumnMajor() int64 {
    var total int64
    for j := 0; j < N; j++ {
        for i := 0; i < N; i++ {
            total += matrix[i][j] // column by column — stride-N access
        }
    }
    return total
}

func main() {
    start := time.Now()
    sumRowMajor()
    fmt.Println("Row-major:", time.Since(start))

    start = time.Now()
    sumColumnMajor()
    fmt.Println("Column-major:", time.Since(start))
}
```

On my machine, row-major runs in roughly 20ms. Column-major takes 120ms on the same data. Same number of additions, same algorithmic complexity, 6x difference in wall time. The column-major version jumps 4096 elements on each access, blowing the cache every single time.

This matters in production. If you're building a metrics aggregation service that sums across time-series data, layout determines throughput — not the algorithm.

## The Tradeoffs

**Fixed size is a real constraint.** Go's built-in slices handle growth by allocating a new backing array and copying. When a slice doubles from capacity 512 to 1024, every element is copied. If you're appending in a hot path, pre-allocate:

```go
// Bad: triggers multiple reallocations
result := []int64{}
for _, v := range source {
    result = append(result, process(v))
}

// Good: single allocation
result := make([]int64, 0, len(source))
for _, v := range source {
    result = append(result, process(v))
}
```

**Middle insertions are expensive.** Inserting at index `i` requires shifting everything from `i` to `len-1` one position right — O(n) work. In practice, if you're doing a lot of middle insertions, you either need a different data structure or you should rethink your data model.

**False sharing in concurrent code.** If two goroutines write to different elements in the same cache line, they thrash each other's caches even though they're technically writing different memory. This is called false sharing and it's a source of subtle performance degradation in concurrent services.

```go
// Dangerous: counter[0] and counter[1] likely share a cache line
var counters [2]int64

// Better: pad to separate cache lines
type PaddedCounter struct {
    value int64
    _     [56]byte // pad to 64 bytes
}
var counters [2]PaddedCounter
```

## Key Takeaway

Arrays aren't interesting because they're simple. They're interesting because they're the only data structure where the hardware does you a favor — prefetching, cache lines, and spatial locality all conspire to make sequential access blazingly fast. Every other data structure you'll learn in this series trades away some of that locality for flexibility. Understanding what you're trading away starts here.

When a senior engineer tells you "just use a slice," they're usually right. But now you know *why* they're right, and you'll know the specific situations where it stops being true.

---

*Next: [Lesson 2: Linked Lists — Almost never the right choice](/post/fundamentals/ds-linked-lists/)*
