---
title: "Lesson 36: Intervals — Sort by start, merge by end"
author: "Atharva Pandey"
keywords:
  - intervals
  - merge intervals
  - insert interval
  - meeting rooms
  - sweep line
  - interview patterns
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 36
date: "2026-01-05T00:00:00.000Z"
---

Interval problems show up everywhere — in calendar APIs, in resource scheduling, in genomics, in timeline visualizations. In interviews they appear in a small number of canonical forms, and every form reduces to the same underlying operation: sort by start time, then sweep left to right making decisions based on where the current interval's end overlaps with the next interval's start.

I have seen engineers panic at interval problems because the cases feel fiddly. Overlapping but not containing. Contained entirely. Adjacent but not touching. Once you drill the sort-and-sweep template into muscle memory, you handle all the cases in a single pass without tracking them explicitly.

## The Pattern

The fundamental operation: after sorting by start time, two adjacent intervals `[a, b]` and `[c, d]` (with `a <= c`) can be merged if and only if `c <= b`. The merged result is `[a, max(b, d)]`.

```
[a————b]
       [c———d]   → no overlap: c > b
[a————b]
    [c———d]      → overlap: merge to [a, max(b,d)]
[a—————————b]
    [c———d]      → contained: b already covers d, merge to [a, b]
```

The `max(b, d)` handles the contained case automatically — you do not need a special branch.

The canonical sweep loop:

```go
func mergeTemplate(intervals [][]int) [][]int {
    sort.Slice(intervals, func(i, j int) bool {
        return intervals[i][0] < intervals[j][0]
    })

    merged := [][]int{intervals[0]}

    for i := 1; i < len(intervals); i++ {
        last := merged[len(merged)-1]
        curr := intervals[i]

        if curr[0] <= last[1] {
            // Overlap: extend the end if needed
            if curr[1] > last[1] {
                last[1] = curr[1]
            }
        } else {
            // No overlap: start a new merged interval
            merged = append(merged, curr)
        }
    }
    return merged
}
```

Time: O(n log n) for sort, O(n) for the sweep. Space: O(n) for output.

## Problem 1: Merge Intervals

Given a list of intervals, merge all overlapping intervals.

```go
func merge(intervals [][]int) [][]int {
    if len(intervals) == 0 {
        return intervals
    }
    sort.Slice(intervals, func(i, j int) bool {
        return intervals[i][0] < intervals[j][0]
    })

    result := [][]int{intervals[0]}

    for i := 1; i < len(intervals); i++ {
        last := result[len(result)-1]
        curr := intervals[i]

        if curr[0] <= last[1] {
            if curr[1] > last[1] {
                last[1] = curr[1]
            }
        } else {
            result = append(result, []int{curr[0], curr[1]})
        }
    }
    return result
}
```

One subtlety in Go: when you do `last := result[len(result)-1]`, you get a slice header pointing into the same backing array. Modifying `last[1]` modifies the actual element in `result`. This is a feature here — you want in-place update. But if you append a new element with `append(result, curr)`, it may or may not point to the same backing array. The safe pattern for the new element is `[]int{curr[0], curr[1]}` to avoid accidental aliasing.

## Problem 2: Insert Interval

Given a sorted list of non-overlapping intervals and a new interval, insert it into the correct position and merge any overlaps.

```go
func insert(intervals [][]int, newInterval []int) [][]int {
    result := [][]int{}
    i := 0
    n := len(intervals)

    // Phase 1: add all intervals that end before newInterval starts
    for i < n && intervals[i][1] < newInterval[0] {
        result = append(result, intervals[i])
        i++
    }

    // Phase 2: merge all intervals that overlap with newInterval
    for i < n && intervals[i][0] <= newInterval[1] {
        if intervals[i][0] < newInterval[0] {
            newInterval[0] = intervals[i][0]
        }
        if intervals[i][1] > newInterval[1] {
            newInterval[1] = intervals[i][1]
        }
        i++
    }
    result = append(result, newInterval)

    // Phase 3: add all remaining intervals
    for i < n {
        result = append(result, intervals[i])
        i++
    }

    return result
}
```

Time: O(n). No sort needed — the list is already sorted. The three-phase structure is the clearest way to code this under pressure: intervals before, intervals overlapping, intervals after. Do not try to handle all three phases in one loop — you will get the conditions wrong.

The overlap condition in phase 2 is `intervals[i][0] <= newInterval[1]`. If an existing interval starts at or before the new interval ends, they overlap. We expand `newInterval` to cover both and continue.

## Problem 3: Meeting Rooms II

Given a list of meeting time intervals, find the minimum number of conference rooms required.

This is the sweep-line variant of the interval pattern. Instead of merging, you track how many intervals are active at any point in time.

```go
func minMeetingRooms(intervals [][]int) int {
    n := len(intervals)
    if n == 0 {
        return 0
    }

    starts := make([]int, n)
    ends := make([]int, n)

    for i, iv := range intervals {
        starts[i] = iv[0]
        ends[i] = iv[1]
    }

    sort.Ints(starts)
    sort.Ints(ends)

    rooms := 0
    endPtr := 0

    for i := 0; i < n; i++ {
        if starts[i] < ends[endPtr] {
            rooms++ // a meeting started before the earliest-ending one finished
        } else {
            endPtr++ // reuse the room that just became free
        }
    }
    return rooms
}
```

Time: O(n log n). Space: O(n).

The two-pointer trick: sort starts and ends separately. Walk through starts in order. For each new meeting, check if the earliest-ending current meeting has finished (by comparing against `ends[endPtr]`). If yes, reuse that room (`endPtr++`). If no, allocate a new room. The value of `rooms` at the end is the maximum number of simultaneous meetings.

An alternative: use a min-heap of end times. Push each meeting's end time. For each new meeting, if the heap's minimum is less than or equal to the new meeting's start time, pop it (reuse the room) and push the new end time. Otherwise just push the new end time. The heap size is the answer. Both approaches are O(n log n); the two-pointer version is slightly simpler to code.

```go
func minMeetingRoomsHeap(intervals [][]int) int {
    sort.Slice(intervals, func(i, j int) bool {
        return intervals[i][0] < intervals[j][0]
    })

    h := &MinIntHeap{}
    heap.Init(h)

    for _, iv := range intervals {
        if h.Len() > 0 && (*h)[0] <= iv[0] {
            heap.Pop(h) // room freed
        }
        heap.Push(h, iv[1])
    }
    return h.Len()
}

type MinIntHeap []int
func (h MinIntHeap) Len() int           { return len(h) }
func (h MinIntHeap) Less(i, j int) bool { return h[i] < h[j] }
func (h MinIntHeap) Swap(i, j int)      { h[i], h[j] = h[j], h[i] }
func (h *MinIntHeap) Push(x any)        { *h = append(*h, x.(int)) }
func (h *MinIntHeap) Pop() any {
    old := *h; n := len(old); x := old[n-1]; *h = old[:n-1]; return x
}
```

## How to Recognize This Pattern

Interval problems are identifiable by their inputs. If the problem gives you pairs `[start, end]` or asks about time ranges, overlaps, coverage, or scheduling, you are looking at an interval problem.

The decision tree:

- **Need to merge overlapping ranges?** Sort by start, sweep and merge by end.
- **Need to insert one interval into a sorted list?** Three-phase linear scan.
- **Need the maximum overlap at any point (rooms, bandwidth)?** Sort starts and ends separately, two-pointer sweep, or sort by start and use a min-heap of ends.
- **Need to count non-overlapping intervals?** Sort by end, greedy selection (same as Non-overlapping Intervals from the previous lesson).

Common mistakes: forgetting to sort before sweeping; using `<` instead of `<=` in the overlap check (missing intervals that share exactly one endpoint); modifying intervals in the input when you should be building a new list.

## Key Takeaway

Interval problems have two moves: sort by start time, then sweep left to right. The merge condition `curr.start <= last.end` and the update `last.end = max(last.end, curr.end)` are the heart of every merge problem. Once those two lines are reflexive, the rest is edge case handling.

Meeting Rooms II introduces the sweep-line variant: instead of merging, you count how deep the stack goes. Sort starts and ends separately, walk through starts, decide whether you need a new room or can reuse an existing one. The maximum depth is your answer.

---

**Series**: [Interview Patterns: Cracking FAANG+ in the AI Era](/series/interview-patterns-cracking-faang-in-the-ai-era/)

**Previous**: [Lesson 35: Greedy](/post/fundamentals/interview-greedy/) — Local optimal leads to global optimal (sometimes)

**Next**: [Lesson 37: Concurrency Problems](/post/fundamentals/interview-concurrency/) — The questions Google loves
