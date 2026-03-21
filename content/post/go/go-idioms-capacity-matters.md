---
title: 'Go Idioms: Capacity Matters'
author: Atharva Pandey
keywords:
  - Go
  - golang
  - slices
  - capacity
  - performance
  - make
  - preallocate
  - benchmark
  - memory allocation
tags:
  - Go tutorial
  - golang
date: '2025-04-21T00:00:00.000Z'
---
s growth algorithm tries to minimize the number of reallocations by growing aggressively:

- For slices smaller than 256 elements, Go roughly **doubles** the capacity on each reallocation
- Beyond 256 elements, the growth factor tapers toward approximately **25%** per reallocation to avoid wasteful over-allocation

That means building a 1,000-element slice from scratch with repeated `append` calls will trigger roughly ten reallocations. Each reallocation copies all previously accumulated data. The total work done is proportional to O(n log n) rather than O(n).

## The Wrong Way: Growing From Zero

This is the pattern you will see written reflexively, especially by programmers coming from languages where this is the only option:

```go
// WRONG: triggers multiple allocations and copies
func collectIDs(users []User) []int {
    var ids []int
    for _, u := range users {
        ids = append(ids, u.ID)
    }
    return ids
}
```

If `users` has 10,000 elements, this loop triggers approximately 14 reallocations. The first allocation gives capacity 1, then 2, 4, 8, 16, 32, 64, 128, 256, then the 25% growth kicks in, and so on until capacity exceeds 10,000. Each reallocation copies everything accumulated so far.

## The Right Way: make([]T, 0, n)

When you know the final size (or a good upper bound), preallocate:

```go
// RIGHT: single allocation, no copies
func collectIDs(users []User) []int {
    ids := make([]int, 0, len(users))
    for _, u := range users {
        ids = append(ids, u.ID)
    }
    return ids
}
```

`make([]int, 0, len(users))` creates a slice with length 0 and capacity `len(users)`. `append` fills in elements without ever triggering a reallocation. The entire operation requires a single heap allocation.

Note the distinction between `make([]int, n)` and `make([]int, 0, n)`. The first gives you a slice of length `n` filled with zeros — useful if you are writing by index. The second gives you a zero-length slice with reserved capacity — useful if you are building with `append`. Mixing them up is a common mistake:

```go
// WRONG: creates 10 zeros then appends after them
ids := make([]int, len(users))
for _, u := range users {
    ids = append(ids, u.ID) // appends AFTER the 10 zeros!
}
// ids now has len(users)*2 elements — first half are zeros
```

## Benchmark: Seeing the Difference

Numbers are more convincing than prose. Here is a benchmark that measures the two approaches:

```go
package main

import (
    "testing"
)

func BenchmarkWithoutCapacity(b *testing.B) {
    input := make([]int, 10000)
    for i := range input {
        input[i] = i
    }
    b.ResetTimer()
    for n := 0; n < b.N; n++ {
        var result []int
        for _, v := range input {
            result = append(result, v*2)
        }
        _ = result
    }
}

func BenchmarkWithCapacity(b *testing.B) {
    input := make([]int, 10000)
    for i := range input {
        input[i] = i
    }
    b.ResetTimer()
    for n := 0; n < b.N; n++ {
        result := make([]int, 0, len(input))
        for _, v := range input {
            result = append(result, v*2)
        }
        _ = result
    }
}
```

Running this with `go test -bench=. -benchmem` produces output similar to:

```
BenchmarkWithoutCapacity-8    8412    139823 ns/op    357627 B/op    19 allocs/op
BenchmarkWithCapacity-8      22174     54021 ns/op     81920 B/op     1 allocs/op
```

The preallocated version runs roughly **2.6x faster** and uses **4x less memory** for a 10,000-element slice. More importantly, it drops from 19 allocations to 1. Under load, those 18 extra allocations per call accumulate into measurable GC pressure.

## Preallocating Maps

The same principle applies to maps. Go maps grow dynamically, but each growth requires re-hashing all existing keys — an expensive operation.

```go
// WRONG: map grows and re-hashes as you insert
func indexByID(users []User) map[int]User {
    index := make(map[int]User)
    for _, u := range users {
        index[u.ID] = u
    }
    return index
}
```

```go
// RIGHT: preallocate with a size hint
func indexByID(users []User) map[int]User {
    index := make(map[int]User, len(users))
    for _, u := range users {
        index[u.ID] = u
    }
    return index
}
```

The second argument to `make` for maps is a *hint*, not a guarantee — Go may still allocate more or less than requested — but it significantly reduces the number of re-hash events during initial population. For a map that will hold 10,000 entries, the difference between no hint and an accurate hint is often 3-4 re-hashes avoided.

## When You Do Not Know the Size

Sometimes you genuinely do not know the final size. In those cases, a reasonable estimate is better than zero. If you are filtering a slice and expecting the output to be roughly half the input, using `len(input)/2` as the capacity is a reasonable guess:

```go
func filterActive(users []User) []User {
    // We expect roughly half to be active — good enough estimate
    result := make([]User, 0, len(users)/2)
    for _, u := range users {
        if u.Active {
            result = append(result, u)
        }
    }
    return result
}
```

If the estimate is off, Go handles it gracefully — it will allocate more capacity as needed. You just lose the benefit of perfectly avoiding reallocations. An underestimate is still much better than starting from zero, especially for large inputs.

## The Growth Algorithm in Detail

Understanding the growth algorithm helps you reason about worst-case allocations. The actual implementation in the Go runtime (as of Go 1.21) works roughly like this:

For a slice of size `n` that needs to grow:
- If `n < 256`: new capacity = `n * 2`
- If `n >= 256`: new capacity grows by approximately `(n + 3*256) / 4` on each growth, converging toward 25% growth per reallocation

```go
// Illustrating the growth pattern (approximate)
func demoGrowth() {
    var s []int
    prev := 0
    for i := 0; i < 2000; i++ {
        s = append(s, i)
        if cap(s) != prev {
            fmt.Printf("len=%d, cap=%d\n", len(s), cap(s))
            prev = cap(s)
        }
    }
}
```

Running this prints a sequence like `1, 2, 4, 8, 16, 32, 64, 128, 256, 320, 400, 512, ...` — you can see the shift from doubling to slower growth happening around 256.

## Real Production Impact

This is not theoretical micro-optimization. In a production service that handles thousands of requests per second, a hot path that builds a response slice from a database query result might execute millions of times per day. If that path triggers ten unnecessary allocations per call, you are looking at tens of millions of unnecessary allocations per day. Each allocation puts pressure on the GC, which pauses goroutines during mark phases.

The fix is one line of code. The cost of not fixing it is latency spikes under load.

A concrete scenario: an API endpoint that formats a list of search results into a response DTO. The search returns up to 100 results. Without capacity:

```go
// WRONG: up to 7 reallocations per request
var results []ResultDTO
for _, item := range searchResults {
    results = append(results, toDTO(item))
}
```

With capacity:

```go
// RIGHT: one allocation per request, always
results := make([]ResultDTO, 0, len(searchResults))
for _, item := range searchResults {
    results = append(results, toDTO(item))
}
```

At 10,000 requests per second, the difference is approximately 70,000 fewer allocations per second. That directly reduces GC frequency and the tail latency that comes with it.

## Putting It Together

The rule is simple: when you know the size, say so. `make([]T, 0, n)` for slices you build with `append`. `make(map[K]V, n)` for maps you populate in a loop. The compiler and runtime cannot infer your intent from loop structure, so you have to be explicit.

This is one of those idioms that requires almost zero thought once it becomes habit. You see a `for` loop that appends, you reach for `make` with a capacity. Over time it becomes automatic, and your code's memory profile improves measurably.
