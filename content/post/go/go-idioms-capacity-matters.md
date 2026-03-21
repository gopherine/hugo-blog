---
title: 'Lesson 8: Capacity Matters — The allocation tax you''re paying without knowing'
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
series: "Idiomatic Go"
lesson: 8
date: '2025-04-21T00:00:00.000Z'
---

There's a one-line fix that will make your hot paths faster, use less memory, and reduce GC pressure. It costs you nothing in readability. Most Go programmers know about it. Far fewer actually do it consistently. The fix is telling Go how big a slice is going to be before you start filling it.

## The Problem

This is the pattern you reach for by reflex, especially if you've come from languages where dynamic arrays just grow as needed:

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

For a 10,000-element input, this loop triggers roughly 14 reallocations. Go's growth algorithm doubles capacity for slices under 256 elements, then shifts to approximately 25% growth per reallocation after that. So you start at capacity 1, go to 2, 4, 8, 16, 32, 64, 128, 256, then 320, 400, 512... until capacity finally exceeds 10,000. Every reallocation copies all previously accumulated data. You're doing O(n log n) work to produce an O(n) result.

The same hidden cost applies to maps:

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

Every time a Go map fills its load threshold it rehashes all existing keys. For 10,000 entries starting from zero, you're looking at 3-4 complete rehashes during population.

## The Idiomatic Way

When you know the final size — or a reasonable upper bound — just say so:

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

`make([]int, 0, len(users))` allocates a backing array big enough for `len(users)` elements upfront. Every `append` writes into already-reserved space. One heap allocation for the whole operation.

For maps:

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

The second argument to `make` for maps is a hint, not a guarantee — Go may allocate more or less — but it dramatically reduces the number of rehash events during initial population.

One important distinction: `make([]int, n)` and `make([]int, 0, n)` are different. The first gives you a slice of length `n` pre-filled with zeros, suitable for writing by index. The second gives you a zero-length slice with reserved capacity, suitable for building with `append`. Mixing them up is a common mistake:

```go
// WRONG: creates len(users) zeros then appends after them
ids := make([]int, len(users))
for _, u := range users {
    ids = append(ids, u.ID) // appends AFTER the zeros!
}
// ids now has len(users)*2 elements — first half are zeros
```

## In The Wild

Here's a benchmark that makes the numbers concrete:

```go
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

Running with `go test -bench=. -benchmem`:

```
BenchmarkWithoutCapacity-8    8412    139823 ns/op    357627 B/op    19 allocs/op
BenchmarkWithCapacity-8      22174     54021 ns/op     81920 B/op     1 allocs/op
```

2.6x faster. 4x less memory. 19 allocations reduced to 1. For a 10,000-element slice, that's the cost of three wrong characters (`var` instead of `make([]int, 0, n)`).

This isn't theoretical. Think about an API endpoint that formats search results into response DTOs. The search returns up to 100 results. At 10,000 requests per second without capacity hints, you're generating roughly 70,000 unnecessary allocations per second — directly increasing GC frequency and the tail latency that comes with it. The fix is two extra tokens in your `make` call.

## The Gotchas

**"I don't know the exact size."** You usually know more than you think. If you're filtering a slice and expecting roughly half the elements through, `len(input)/2` is a perfectly valid estimate. An underestimate still avoids most of the reallocations. Starting from zero is always the worst option.

**`make([]T, n)` vs `make([]T, 0, n)`.** I said this above but it's worth repeating because the bug is silent. If you `make([]T, n)` and then `append` to it, your first `n` elements will be zero values and your actual data starts at index `n`. The compiler won't warn you. Your tests might not catch it if the test data is all zeros.

**Maps and the hint being non-binding.** Go may round up your map size hint or allocate differently. You can't assume that `make(map[K]V, n)` means exactly `n` buckets — it's a best-effort hint. But "best effort" here is still much better than "no hint at all," especially for large initial populations.

## Key Takeaway

The rule is simple enough to become a habit in a week: every time you write a loop that appends to a slice and you know the input size, reach for `make([]T, 0, n)`. Every time you write a loop that inserts into a map, pass the expected entry count to `make`. The compiler and runtime can't infer loop counts from source structure, so you have to be explicit. It's one line of code per hot path, it's measurably faster, it uses measurably less memory, and it's something you can stop thinking about the moment it becomes automatic.

---

← [Lesson 7: Slices Are Views](/post/go/go-idioms-slices-are-views/) | [Course Index](#) | [Lesson 9: range Gotchas →](/post/go/go-idioms-range-gotchas/)
