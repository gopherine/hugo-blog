---
title: "Lesson 7: Heaps and Priority Queues — Scheduling, top-K, and rate limiters"
author: Atharva Pandey
series: "Data Structures for Production Engineers"
lesson: 7
keywords: [data structures, computer science, heap, priority queue, min-heap, max-heap, scheduling]
tags: [fundamentals, data structures]
date: '2024-07-09T00:00:00.000Z'
---

Every time your operating system picks the next process to run, it's using a heap. Every time Kubernetes re-schedules a pod, it's using a priority queue. Every time you've written a "get top 10 most frequent items from a billion-row stream," the efficient solution involves a heap. These are workhorses of systems programming that look simple on paper and have genuinely tricky implementation details.

The heap is also one of my favorite data structures to explain because it demonstrates a beautiful property: you can implement a tree efficiently inside an array, using only arithmetic to find parent and child nodes.

## How It Actually Works

A min-heap is a complete binary tree where every parent is smaller than or equal to its children. The minimum element is always at the root. A max-heap is the same with the inequality flipped.

The clever part: store the tree level-by-level in an array. For a node at index `i`:
- Parent is at `(i-1) / 2`
- Left child is at `2*i + 1`
- Right child is at `2*i + 2`

No pointers. No tree nodes. Just arithmetic.

```go
package main

import (
    "container/heap"
    "fmt"
)

// MinHeap implements heap.Interface for integers
type MinHeap []int

func (h MinHeap) Len() int           { return len(h) }
func (h MinHeap) Less(i, j int) bool { return h[i] < h[j] }
func (h MinHeap) Swap(i, j int)      { h[i], h[j] = h[j], h[i] }

func (h *MinHeap) Push(x interface{}) {
    *h = append(*h, x.(int))
}

func (h *MinHeap) Pop() interface{} {
    old := *h
    n := len(old)
    x := old[n-1]
    *h = old[:n-1]
    return x
}

func main() {
    h := &MinHeap{5, 3, 8, 1, 4}
    heap.Init(h)

    heap.Push(h, 2)

    fmt.Println("Min element:", (*h)[0]) // always the minimum

    for h.Len() > 0 {
        fmt.Printf("%d ", heap.Pop(h).(int))
    }
    // Output: 1 2 3 4 5 8 — sorted!
}
```

The sift-up (after push) and sift-down (after pop) operations each take O(log n) — one comparison per level of the tree, and the tree height is log n because it's always complete. The genius of the array representation is that these operations have perfect cache locality — you're accessing consecutive memory, not chasing pointers across the heap.

## When to Use It

Use a heap/priority queue when:
- You need repeated access to the minimum or maximum element
- You're implementing a scheduler (always pick the highest-priority job next)
- You need top-K from a stream (maintain a K-size heap)
- You're implementing Dijkstra's shortest path
- You need a timer wheel or delayed task queue (always process the next-due event)

Don't use a heap when:
- You need arbitrary sorted access or range queries (use a BST or B-tree)
- You need to search by value rather than by priority (O(n) in a heap)
- You need FIFO among equal-priority items and that matters to correctness (heaps don't guarantee FIFO among equals)

## Production Example

The top-K problem appears constantly in production: "What are the 10 most-called endpoints in the last hour?" "What are the 5 slowest database queries?" "What are the top 100 most active users?"

The naive approach (sort everything, take first K) is O(n log n). With a fixed-size heap, you can do it in O(n log k), which is dramatically faster when K is small:

```go
package main

import (
    "container/heap"
    "fmt"
)

type QueryStat struct {
    Endpoint string
    P99Ms    float64
}

// MinHeap of QueryStat by P99 latency — for top-K, use min-heap of size K
type SlowQueryHeap []QueryStat

func (h SlowQueryHeap) Len() int           { return len(h) }
func (h SlowQueryHeap) Less(i, j int) bool { return h[i].P99Ms < h[j].P99Ms }
func (h SlowQueryHeap) Swap(i, j int)      { h[i], h[j] = h[j], h[i] }
func (h *SlowQueryHeap) Push(x interface{}) {
    *h = append(*h, x.(QueryStat))
}
func (h *SlowQueryHeap) Pop() interface{} {
    old := *h
    n := len(old)
    x := old[n-1]
    *h = old[:n-1]
    return x
}

// TopKSlowest returns the K slowest endpoints from a stream
// Uses a min-heap of size K: pop the minimum when over capacity
// Result: heap contains K largest values
func TopKSlowest(stats []QueryStat, k int) []QueryStat {
    h := &SlowQueryHeap{}
    heap.Init(h)

    for _, s := range stats {
        heap.Push(h, s)
        if h.Len() > k {
            heap.Pop(h) // remove the fastest from our "top K slowest" set
        }
    }

    result := make([]QueryStat, h.Len())
    for i := len(result) - 1; i >= 0; i-- {
        result[i] = heap.Pop(h).(QueryStat)
    }
    return result
}

func main() {
    stats := []QueryStat{
        {"/api/users", 12.5},
        {"/api/orders", 340.2},
        {"/api/payments", 89.1},
        {"/health", 0.8},
        {"/api/search", 450.9},
        {"/api/recommendations", 210.4},
        {"/api/inventory", 55.3},
        {"/api/shipping", 178.6},
    }

    top3 := TopKSlowest(stats, 3)
    fmt.Println("Top 3 slowest endpoints:")
    for _, s := range top3 {
        fmt.Printf("  %s: %.1fms p99\n", s.Endpoint, s.P99Ms)
    }
}
```

Now let's look at a timer/scheduled task implementation — this is the pattern behind cron-like systems and connection timeout management:

```go
package main

import (
    "container/heap"
    "fmt"
    "time"
)

type Task struct {
    RunAt   time.Time
    Name    string
    index   int
}

type TaskQueue []*Task

func (tq TaskQueue) Len() int            { return len(tq) }
func (tq TaskQueue) Less(i, j int) bool  { return tq[i].RunAt.Before(tq[j].RunAt) }
func (tq TaskQueue) Swap(i, j int) {
    tq[i], tq[j] = tq[j], tq[i]
    tq[i].index, tq[j].index = i, j
}
func (tq *TaskQueue) Push(x interface{}) {
    t := x.(*Task)
    t.index = len(*tq)
    *tq = append(*tq, t)
}
func (tq *TaskQueue) Pop() interface{} {
    old := *tq
    n := len(old)
    t := old[n-1]
    *tq = old[:n-1]
    return t
}

func main() {
    tq := &TaskQueue{}
    heap.Init(tq)

    now := time.Now()
    heap.Push(tq, &Task{RunAt: now.Add(5 * time.Second), Name: "cleanup-job"})
    heap.Push(tq, &Task{RunAt: now.Add(1 * time.Second), Name: "health-check"})
    heap.Push(tq, &Task{RunAt: now.Add(3 * time.Second), Name: "metrics-flush"})

    fmt.Println("Tasks in execution order:")
    for tq.Len() > 0 {
        t := heap.Pop(tq).(*Task)
        fmt.Printf("  %s (in %.0fs)\n", t.Name, t.RunAt.Sub(now).Seconds())
    }
    // health-check (1s), metrics-flush (3s), cleanup-job (5s)
}
```

This is essentially how Kubernetes' work queue scheduler works — every node in the queue has a priority, and the heap always surfaces the next item to process.

## The Tradeoffs

**No efficient search.** A heap only guarantees O(1) access to the min/max. Finding an arbitrary element requires O(n) linear search. If you need to update priorities of elements you've already inserted (Dijkstra's does this), you need to track array indices and call `heap.Fix` — which is O(log n) but requires the extra bookkeeping.

**Not stable.** Items with equal priority can come out in any order. If FIFO ordering among equals is a correctness requirement, add a sequence number as a secondary sort key.

**Heap sort is elegant but cache-unfriendly.** Heap sort runs in O(n log n) and sorts in place with no extra memory, which sounds great. In practice, it has poor cache locality because it accesses elements far apart in the array during sift-down operations. Introsort (what Go's `sort.Slice` uses) consistently outperforms heap sort on modern hardware.

**Memory layout is excellent.** Because a heap is array-backed, it's cache-friendly for the push/pop operations that only access parents and children — which are close in index space. Compare this to a linked-list priority queue, where every node access could be a cache miss.

## Key Takeaway

The heap is the right data structure whenever you need repeated access to an extreme value (min or max) with dynamic insertions. The array-backed tree representation is one of computer science's elegant space-time tradeoffs — zero pointer overhead, cache-friendly access, O(log n) operations. In production, heaps show up in schedulers, top-K queries, event timers, Dijkstra's algorithm, and any system that needs to continuously process the "most urgent" item from a changing set.

If you've been using a sorted slice and repeatedly re-sorting it when items arrive, switch to a heap. You're trading O(n log n) per update for O(log n).

---

*Previous: [Lesson 6: B-Trees and B+ Trees — How every database index actually works](/post/fundamentals/ds-btrees/)*

*Next: [Lesson 8: Graphs — You're solving graph problems without knowing it](/post/fundamentals/ds-graphs/)*
