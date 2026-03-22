---
title: "Lesson 2: Sorting in Practice — When to sort and why TimSort won"
author: "Atharva Pandey"
keywords:
  - sorting algorithms
  - timsort
  - mergesort
  - quicksort
  - sort.Slice
  - production engineering
tags:
  - fundamentals
  - algorithms
series: "Algorithms That Matter in Production"
lesson: 2
date: "2024-05-01T00:00:00.000Z"
---

Sorting is one of those topics that feels like a solved problem until you actually need to care about it. Every language ships a standard sort. You call it, it works, you move on. But I have run into subtle production bugs caused by not understanding what the sort is actually doing — unstable sorts breaking tie-breaking logic, sorts on large datasets consuming unexpected memory, and sort comparators with subtle bugs that triggered Go's sort to panic.

Understanding why TimSort won the sorting algorithm wars is not trivia. It tells you what the people who built your language's runtime decided to optimize for.

## How It Works

The three sorting algorithms you actually encounter in production are mergesort, quicksort, and TimSort.

**Mergesort** divides the array in half recursively, sorts each half, then merges them. It is stable (equal elements keep their original order), O(n log n) in all cases, but requires O(n) extra memory for the merge step.

**Quicksort** picks a pivot, partitions around it, and recursively sorts each partition. Faster in practice than mergesort due to cache locality, but O(n²) in the worst case (sorted or nearly-sorted data with a bad pivot). Not stable.

**TimSort** is the algorithm Go's `sort.Slice` uses (along with Python's `sorted()`, Java's `Arrays.sort` for objects, and others). It is a hybrid: it finds already-sorted "runs" in real data, then merges them using a modified mergesort. The insight is that real-world data is rarely random — it contains partially sorted regions. TimSort exploits this to run in O(n) on already-sorted data and O(n log n) in the worst case.

```go
package main

import (
    "fmt"
    "sort"
)

type Order struct {
    ID       int
    Customer string
    Amount   float64
    Priority int
}

func main() {
    orders := []Order{
        {1, "alice", 150.0, 2},
        {2, "bob", 300.0, 1},
        {3, "alice", 75.0, 2},
        {4, "charlie", 500.0, 1},
    }

    // Sort by priority ascending, then by amount descending
    // sort.Slice is NOT stable — equal priorities may reorder
    sort.Slice(orders, func(i, j int) bool {
        if orders[i].Priority != orders[j].Priority {
            return orders[i].Priority < orders[j].Priority
        }
        return orders[i].Amount > orders[j].Amount
    })

    for _, o := range orders {
        fmt.Printf("ID=%d customer=%s priority=%d amount=%.0f\n",
            o.ID, o.Customer, o.Priority, o.Amount)
    }
}
```

## When You Need It

The decision is usually not which sorting algorithm to use — it is whether to sort at all, and what properties you need from the sort.

**Stable vs. unstable.** If you sort by column A and the user then sorts by column B, they expect rows with equal B values to stay in A order. This requires a stable sort. Go provides `sort.SliceStable` for this. Many UI table implementations get this wrong by using unstable sorts and then wondering why secondary sorting breaks.

**Pre-sorted data.** If your data arrives mostly sorted (e.g., time-series events with slight reordering), TimSort will handle it near O(n). Choosing a simpler algorithm like insertion sort for small arrays (< 16 elements) is also standard practice — the constant factors matter at that scale.

**Sorting once vs. sorting repeatedly.** If you sort the same data repeatedly, consider maintaining a sorted data structure (heap, balanced BST) instead. Go's `container/heap` provides a min/max heap in the standard library.

```go
// When you need the top-K items from a stream without sorting everything
import "container/heap"

type MinHeap []int

func (h MinHeap) Len() int           { return len(h) }
func (h MinHeap) Less(i, j int) bool { return h[i] < h[j] }
func (h MinHeap) Swap(i, j int)      { h[i], h[j] = h[j], h[i] }
func (h *MinHeap) Push(x any)        { *h = append(*h, x.(int)) }
func (h *MinHeap) Pop() any {
    old := *h
    n := len(old)
    x := old[n-1]
    *h = old[:n-1]
    return x
}

// Keep the top K largest values seen so far — O(n log k)
func topK(stream []int, k int) []int {
    h := &MinHeap{}
    heap.Init(h)
    for _, v := range stream {
        heap.Push(h, v)
        if h.Len() > k {
            heap.Pop(h) // remove the smallest
        }
    }
    return []int(*h)
}
```

## Production Example

I built a leaderboard service that needed to return the top 100 players from a pool of ~500,000 by score, refreshed every 30 seconds. The naive implementation sorted all 500,000 on every request.

Sorting 500,000 structs in Go with `sort.Slice` takes roughly 200–400ms depending on the comparator cost. That was too slow for a 30-second refresh, but it was catastrophically too slow when a load spike caused multiple goroutines to each trigger a sort simultaneously.

The fix was a two-level approach: use a heap to maintain the top-K during ingestion, and only do a full sort for administrative queries.

```go
type Player struct {
    ID    string
    Score int64
}

type PlayerHeap []Player

func (h PlayerHeap) Len() int           { return len(h) }
func (h PlayerHeap) Less(i, j int) bool { return h[i].Score < h[j].Score } // min-heap by score
func (h PlayerHeap) Swap(i, j int)      { h[i], h[j] = h[j], h[i] }
func (h *PlayerHeap) Push(x any)        { *h = append(*h, x.(Player)) }
func (h *PlayerHeap) Pop() any {
    old := *h
    n := len(old)
    x := old[n-1]
    *h = old[:n-1]
    return x
}

type Leaderboard struct {
    topK int
    heap *PlayerHeap
}

func NewLeaderboard(k int) *Leaderboard {
    h := &PlayerHeap{}
    heap.Init(h)
    return &Leaderboard{topK: k, heap: h}
}

func (lb *Leaderboard) Record(p Player) {
    heap.Push(lb.heap, p)
    if lb.heap.Len() > lb.topK {
        heap.Pop(lb.heap) // evict the lowest scorer
    }
}

func (lb *Leaderboard) Top() []Player {
    result := make([]Player, lb.heap.Len())
    copy(result, []Player(*lb.heap))
    // sort descending for presentation
    sort.Slice(result, func(i, j int) bool {
        return result[i].Score > result[j].Score
    })
    return result
}
```

Maintaining the heap during ingestion is O(log k) per insert, where k=100. Reading the top 100 requires one sort of 100 elements — trivial. We eliminated the 200–400ms sort and replaced it with sub-millisecond heap operations.

## The Tradeoffs

`sort.Slice` is fast but not stable. `sort.SliceStable` preserves relative order of equal elements but has slightly higher overhead. In most cases the difference is negligible, but at millions of records it matters.

Custom comparators with side effects are a footgun. Go's sort will panic if your comparator returns inconsistent results (i.e., `less(a, b)` and `less(b, a)` are both true). I have seen this happen when the comparator reads from a map that was concurrently modified during the sort.

Sorting is not free memory-wise either. In-place sorts like quicksort use O(log n) stack space for recursion. Mergesort uses O(n). If you are sorting within a memory-constrained goroutine, this matters.

## Key Takeaway

Sort when you need ordered output, but do not sort blindly. For top-K problems, a heap beats a full sort by a large margin. For data that arrives mostly ordered, TimSort's exploitation of existing runs gives you near-linear performance. And always check whether you need stability — forgetting that one property causes confusing, hard-to-reproduce bugs in multi-column sort UIs and ranking systems.

---

← [Lesson 1: Big-O Thinking](/post/fundamentals/algo-big-o/) | [Course Index](/post/fundamentals/) | Next: [Lesson 3: Binary Search](/post/fundamentals/algo-binary-search/) →
