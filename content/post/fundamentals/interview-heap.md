---
title: "Lesson 32: Heap Patterns — Keep the top K without sorting everything"
author: "Atharva Pandey"
keywords:
  - heap
  - priority queue
  - top k elements
  - merge k sorted lists
  - median data stream
  - task scheduler
  - interview patterns
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 32
date: "2025-11-11T00:00:00.000Z"
---

The first time I saw a "find the K largest elements" problem in an interview, I sorted the array and returned the last K. Correct answer, wrong approach. Sorting costs O(n log n). A heap does it in O(n log K). When n is a billion and K is ten, that difference matters enormously — and the interviewer knows it.

Heaps feel mystical until you internalize one thing: a heap is not a sorted array. It is a partially ordered tree that guarantees one thing — you can get the minimum (or maximum) element in O(1) and remove it in O(log n). That partial ordering is enough to solve an entire class of problems that would otherwise require full sorting.

## The Pattern

Go's standard library gives you `container/heap`, which is an interface rather than a concrete type. You implement it for your element type. For interview purposes, knowing the interface cold is table stakes.

```go
import "container/heap"

// MinHeap of integers
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
```

For a max-heap, flip the `Less` comparison: `h[i] > h[j]`.

The two core heap patterns:

- **Top K largest**: use a min-heap of size K. For each element, if it is larger than the heap's minimum, pop the minimum and push the new element. At the end, the heap contains the K largest.
- **Top K smallest**: use a max-heap of size K. Same logic in reverse.

The insight: a min-heap of size K is a filter. It keeps the K largest seen so far by evicting the smallest whenever the heap overflows.

## Problem 1: Merge K Sorted Lists

Given an array of K sorted linked lists, merge them into one sorted list.

The naive approach concatenates everything and sorts — O(n log n) where n is total elements. The heap approach processes elements in order by always pulling the current minimum across all list heads.

```go
type ListNode struct {
    Val  int
    Next *ListNode
}

type nodeHeap []*ListNode

func (h nodeHeap) Len() int           { return len(h) }
func (h nodeHeap) Less(i, j int) bool { return h[i].Val < h[j].Val }
func (h nodeHeap) Swap(i, j int)      { h[i], h[j] = h[j], h[i] }
func (h *nodeHeap) Push(x any)        { *h = append(*h, x.(*ListNode)) }
func (h *nodeHeap) Pop() any {
    old := *h
    n := len(old)
    x := old[n-1]
    *h = old[:n-1]
    return x
}

func mergeKLists(lists []*ListNode) *ListNode {
    h := &nodeHeap{}
    heap.Init(h)

    // Seed heap with the head of each list
    for _, node := range lists {
        if node != nil {
            heap.Push(h, node)
        }
    }

    dummy := &ListNode{}
    cur := dummy

    for h.Len() > 0 {
        smallest := heap.Pop(h).(*ListNode)
        cur.Next = smallest
        cur = cur.Next
        if smallest.Next != nil {
            heap.Push(h, smallest.Next)
        }
    }
    return dummy.Next
}
```

Time: O(n log K) where n is total nodes. The heap never grows beyond K elements. This is the canonical heap-as-merge-cursor pattern.

## Problem 2: Find Median from Data Stream

Design a data structure that supports adding numbers and retrieving the current median in O(log n) for insert and O(1) for median.

This is the "two heaps" pattern — one of my favorite interview tricks because it looks magical until you understand the invariant.

```go
type MedianFinder struct {
    low  *MaxHeap // lower half, max-heap
    high *MinHeap // upper half, min-heap
}

type MaxHeap []int
func (h MaxHeap) Len() int           { return len(h) }
func (h MaxHeap) Less(i, j int) bool { return h[i] > h[j] } // max at top
func (h MaxHeap) Swap(i, j int)      { h[i], h[j] = h[j], h[i] }
func (h *MaxHeap) Push(x any)        { *h = append(*h, x.(int)) }
func (h *MaxHeap) Pop() any {
    old := *h; n := len(old); x := old[n-1]; *h = old[:n-1]; return x
}

type MinHeap []int
func (h MinHeap) Len() int           { return len(h) }
func (h MinHeap) Less(i, j int) bool { return h[i] < h[j] }
func (h MinHeap) Swap(i, j int)      { h[i], h[j] = h[j], h[i] }
func (h *MinHeap) Push(x any)        { *h = append(*h, x.(int)) }
func (h *MinHeap) Pop() any {
    old := *h; n := len(old); x := old[n-1]; *h = old[:n-1]; return x
}

func Constructor() MedianFinder {
    lo, hi := &MaxHeap{}, &MinHeap{}
    heap.Init(lo)
    heap.Init(hi)
    return MedianFinder{low: lo, high: hi}
}

func (m *MedianFinder) AddNum(num int) {
    // Always push to low first
    heap.Push(m.low, num)

    // Balance: low's max must not exceed high's min
    if m.high.Len() > 0 && (*m.low)[0] > (*m.high)[0] {
        heap.Push(m.high, heap.Pop(m.low))
    }

    // Keep sizes balanced: low can have at most one extra element
    if m.low.Len() > m.high.Len()+1 {
        heap.Push(m.high, heap.Pop(m.low))
    } else if m.high.Len() > m.low.Len() {
        heap.Push(m.low, heap.Pop(m.high))
    }
}

func (m *MedianFinder) FindMedian() float64 {
    if m.low.Len() > m.high.Len() {
        return float64((*m.low)[0])
    }
    return float64((*m.low)[0]+(*m.high)[0]) / 2.0
}
```

The invariant: `low` holds the smaller half and `high` holds the larger half. `low` is a max-heap (its top is the largest of the small half). `high` is a min-heap (its top is the smallest of the large half). The median is either the top of `low` (odd total) or the average of both tops (even total).

## Problem 3: Task Scheduler

Given a list of tasks (letters) and a cooling period `n`, find the minimum number of intervals to finish all tasks, where identical tasks must be separated by at least `n` intervals.

```go
func leastInterval(tasks []byte, n int) int {
    freq := [26]int{}
    for _, t := range tasks {
        freq[t-'A']++
    }

    // Max-heap of frequencies
    h := &MaxIntHeap{}
    heap.Init(h)
    for _, f := range freq {
        if f > 0 {
            heap.Push(h, f)
        }
    }

    time := 0
    // queue holds [remaining_count, available_at_time]
    type entry struct{ count, available int }
    queue := []entry{}

    for h.Len() > 0 || len(queue) > 0 {
        time++

        if h.Len() > 0 {
            cnt := heap.Pop(h).(int) - 1
            if cnt > 0 {
                queue = append(queue, entry{cnt, time + n})
            }
        }

        // Re-add tasks whose cooling period has expired
        if len(queue) > 0 && queue[0].available == time {
            heap.Push(h, queue[0].count)
            queue = queue[1:]
        }
    }
    return time
}

type MaxIntHeap []int
func (h MaxIntHeap) Len() int           { return len(h) }
func (h MaxIntHeap) Less(i, j int) bool { return h[i] > h[j] }
func (h MaxIntHeap) Swap(i, j int)      { h[i], h[j] = h[j], h[i] }
func (h *MaxIntHeap) Push(x any)        { *h = append(*h, x.(int)) }
func (h *MaxIntHeap) Pop() any {
    old := *h; n := len(old); x := old[n-1]; *h = old[:n-1]; return x
}
```

The greedy insight: always execute the most frequent remaining task (greedy choice). The heap gives you the most frequent task at each step. Tasks that are cooling are stored in a queue ordered by when they become available again.

## How to Recognize This Pattern

Heap problems have a few clear markers:

1. **"Top K," "K largest," "K smallest," "K most frequent"** — you need a bounded heap of size K.
2. **Multiple sorted sequences being merged** — you need a heap seeded with the heads of each sequence, acting as a priority merge cursor.
3. **Streaming data where you need running statistics (median, top K)** — two-heap or bounded heap.
4. **Greedy scheduling where you always process the "best" remaining item** — heap as a priority queue for the greedy choice.

The two-heap trick specifically: if a problem asks you to maintain something that splits neatly into two halves (lower half / upper half, or processed / pending), suspect two heaps.

For the task scheduler family of problems: if you need to simulate time passing with constraints on when items can be re-processed, a heap combined with a cooldown queue is the standard approach.

## Key Takeaway

The heap's power is partial ordering. You do not need full sorted order to answer "what is the current minimum/maximum" efficiently. That partial order, maintained cheaply at O(log n) per insertion, is enough to solve the top-K family, the merge family, and the streaming statistics family.

Know the Go `container/heap` interface by heart. The boilerplate is always the same; only the `Less` function changes. In an interview, write the five methods in under two minutes and move straight to the algorithm.

---

**Series**: [Interview Patterns: Cracking FAANG+ in the AI Era](/series/interview-patterns-cracking-faang-in-the-ai-era/)

**Previous**: [Lesson 31: Monotonic Stack](/post/fundamentals/interview-monotonic-stack/) — The next greater element trick

**Next**: [Lesson 33: Trie](/post/fundamentals/interview-trie/) — When you need prefix matching
