---
title: "Lesson 8: Sorting in Interviews — Can You Do Better Than O(n log n)?"
author: "Atharva Pandey"
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 8
keywords:
  - interviews
  - algorithms
  - leetcode
  - FAANG
  - pattern
  - sorting
  - merge sort
  - quickselect
  - meeting rooms
tags:
  - fundamentals
  - interviews
date: "2024-10-09T00:00:00.000Z"
---

Most candidates treat sorting as a black box — call `sort.Ints` and move on. That works until an interviewer says "can you do this without sorting?" or "implement this yourself" or "what's the worst-case time?" At that point, not understanding sorting costs you the offer.

I don't mean you need to memorize every sorting algorithm. You need three things: understand merge sort well enough to implement it (divide-and-conquer is a pattern you'll use in other contexts); know QuickSelect for kth-element problems (it's O(n) average and comes up constantly); and know when sorting is all you need and when the comparison lower bound of O(n log n) is a limit you can break. This lesson teaches all three.

## The Pattern

Sorting is not just about ordering data — it's about imposing structure that makes other operations fast. After sorting, binary search finds in O(log n). After sorting, two pointers find pairs in O(n). After sorting, greedy interval scheduling works correctly. The question to ask is always: "If the input were sorted, would this problem be easy?" If yes, sort it (O(n log n) once) and solve the simpler version.

---

## Problem 1: Merge Sort — Implement It

**The problem.** Implement merge sort on an integer array. Return the sorted array.

This appears not as a standalone problem but as a prerequisite — interviewers will ask you to implement it to probe your understanding of divide-and-conquer and stable sorting.

**The idea.** Divide the array in half recursively until each subarray has one element (trivially sorted). Then merge adjacent sorted subarrays together. The merge step is the core: given two sorted subarrays, produce one sorted merged array in O(n).

```go
func mergeSort(nums []int) []int {
    if len(nums) <= 1 {
        return nums
    }

    mid := len(nums) / 2
    left := mergeSort(nums[:mid])
    right := mergeSort(nums[mid:])
    return merge(left, right)
}

func merge(left, right []int) []int {
    result := make([]int, 0, len(left)+len(right))
    i, j := 0, 0

    for i < len(left) && j < len(right) {
        if left[i] <= right[j] {
            result = append(result, left[i])
            i++
        } else {
            result = append(result, right[j])
            j++
        }
    }

    result = append(result, left[i:]...)
    result = append(result, right[j:]...)
    return result
}
```

Time: O(n log n) — log n levels of recursion, O(n) work per level. Space: O(n) — auxiliary space for merged subarrays.

**Why merge sort matters beyond sorting.** The divide-and-conquer structure is the same one used in binary search, closest pair of points, count inversions, and external sorting (sorting data that doesn't fit in memory). Understanding how to split a problem in half, solve each half, and combine the results is one of the most important thinking tools in computer science. Merge sort is the canonical example.

**Count inversions variant.** An inversion is a pair `(i, j)` where `i < j` but `nums[i] > nums[j]`. Counting inversions naively is O(n²). With merge sort, count them during the merge step: when you pick an element from the right subarray over the left, the number of inversions added equals the remaining elements in the left subarray. This modification runs in O(n log n). If an interviewer asks "how many swaps would bubble sort make?", they're asking for count inversions.

---

## Problem 2: Kth Largest Element (QuickSelect)

**The problem.** Given an integer array `nums` and an integer `k`, return the kth largest element. Not the kth distinct element — the kth largest in sorted order.

Input: `nums = [3,2,1,5,6,4]`, `k = 2` → `5`

**Brute force.** Sort the array, return `nums[n-k]`.

```go
func findKthLargestBrute(nums []int, k int) int {
    sort.Ints(nums)
    return nums[len(nums)-k]
}
```

Time: O(n log n). Space: O(1) or O(log n) depending on the sort.

**Building toward O(n) average: QuickSelect.** Based on the partition step of quicksort. Pick a pivot, partition the array so everything to the left is smaller than the pivot and everything to the right is larger. After partitioning, the pivot is in its final sorted position. If that position is the one we want (n-k for kth largest), we're done. Otherwise, recurse into only the relevant half.

```go
func findKthLargest(nums []int, k int) int {
    // we want the element at position n-k in sorted order
    target := len(nums) - k
    return quickSelect(nums, 0, len(nums)-1, target)
}

func quickSelect(nums []int, left, right, target int) int {
    if left == right {
        return nums[left]
    }

    pivotIdx := partition(nums, left, right)

    if pivotIdx == target {
        return nums[pivotIdx]
    } else if pivotIdx < target {
        return quickSelect(nums, pivotIdx+1, right, target)
    } else {
        return quickSelect(nums, left, pivotIdx-1, target)
    }
}

func partition(nums []int, left, right int) int {
    // choose last element as pivot (Lomuto partition scheme)
    pivot := nums[right]
    i := left // i marks the boundary: everything left of i is <= pivot

    for j := left; j < right; j++ {
        if nums[j] <= pivot {
            nums[i], nums[j] = nums[j], nums[i]
            i++
        }
    }
    // place pivot in its correct position
    nums[i], nums[right] = nums[right], nums[i]
    return i
}
```

Time: O(n) average, O(n²) worst case. Space: O(log n) average recursion depth.

**The worst-case caveat.** If you always pick the min or max as pivot (bad luck or adversarial input), every partition only eliminates one element — giving O(n²). In practice, randomizing the pivot selection fixes this. For interviews, it's worth mentioning: "I'd use a random pivot in production to avoid adversarial inputs."

```go
func partitionRandom(nums []int, left, right int) int {
    // pick a random pivot and swap it to the end
    pivotIdx := left + rand.Intn(right-left+1)
    nums[pivotIdx], nums[right] = nums[right], nums[pivotIdx]
    return partition(nums, left, right)
}
```

**When to use QuickSelect vs a heap.** QuickSelect gives O(n) average for a single kth-element query. A min-heap of size k gives O(n log k) and is better when k is small relative to n or when you're processing a stream. Know both; say which you'd use and why.

---

## Problem 3: Meeting Rooms II

**The problem.** Given an array of meeting time intervals where `intervals[i] = [start_i, end_i]`, return the minimum number of conference rooms required.

Input: `[[0,30],[5,10],[15,20]]` → `2`
Input: `[[7,10],[2,4]]` → `1`

**Brute force.** Simulate each minute of time, track how many meetings are active.

```go
func minMeetingRoomsBrute(intervals [][]int) int {
    if len(intervals) == 0 {
        return 0
    }
    maxEnd := 0
    for _, iv := range intervals {
        if iv[1] > maxEnd {
            maxEnd = iv[1]
        }
    }
    rooms := 0
    for t := 0; t <= maxEnd; t++ {
        active := 0
        for _, iv := range intervals {
            if iv[0] <= t && t < iv[1] {
                active++
            }
        }
        if active > rooms {
            rooms = active
        }
    }
    return rooms
}
```

Time: O(T·n) where T is the max end time. Terrible for large time values.

**Building toward optimal.** The key insight: the number of rooms needed at any moment equals the number of meetings that have started but not yet ended. Sort meetings by start time. Use a min-heap to track end times of ongoing meetings. When a new meeting starts, check if any ongoing meeting has ended (the heap's minimum). If yes, reuse that room (pop the heap). If no, open a new room (push without popping). The heap size at the end is the answer.

```go
import "container/heap"

type MinHeap []int
func (h MinHeap) Len() int           { return len(h) }
func (h MinHeap) Less(i, j int) bool { return h[i] < h[j] }
func (h MinHeap) Swap(i, j int)      { h[i], h[j] = h[j], h[i] }
func (h *MinHeap) Push(x interface{}) { *h = append(*h, x.(int)) }
func (h *MinHeap) Pop() interface{} {
    old := *h
    n := len(old)
    x := old[n-1]
    *h = old[:n-1]
    return x
}

func minMeetingRooms(intervals [][]int) int {
    if len(intervals) == 0 {
        return 0
    }

    // sort by start time
    sort.Slice(intervals, func(i, j int) bool {
        return intervals[i][0] < intervals[j][0]
    })

    endTimes := &MinHeap{}
    heap.Init(endTimes)

    for _, iv := range intervals {
        start, end := iv[0], iv[1]

        if endTimes.Len() > 0 && (*endTimes)[0] <= start {
            // a room freed up — reuse it
            heap.Pop(endTimes)
        }
        // schedule this meeting (new or reused room)
        heap.Push(endTimes, end)
    }

    return endTimes.Len()
}
```

Time: O(n log n) — sorting plus heap operations. Space: O(n) — heap holds at most n end times.

**The two-pointer alternative.** Separate starts and ends into two sorted arrays. Walk them with two pointers. When `starts[i] < ends[j]`, a new meeting begins before the earliest ongoing one ends — open a room, advance `i`. Otherwise, a room frees up — close a room, advance `j`. The maximum "open rooms" is the answer.

```go
func minMeetingRoomsTwoPointers(intervals [][]int) int {
    n := len(intervals)
    starts := make([]int, n)
    ends := make([]int, n)
    for i, iv := range intervals {
        starts[i] = iv[0]
        ends[i] = iv[1]
    }
    sort.Ints(starts)
    sort.Ints(ends)

    rooms, maxRooms := 0, 0
    j := 0
    for i := 0; i < n; i++ {
        if starts[i] < ends[j] {
            rooms++ // new meeting starts before earliest end
        } else {
            j++ // a meeting ends, reuse its room
        }
        if rooms > maxRooms {
            maxRooms = rooms
        }
    }
    return maxRooms
}
```

Time: O(n log n). Space: O(n). Arguably cleaner than the heap version once you see it.

---

## How to Recognize This Pattern

- "Sort an array" + any follow-up → implement merge sort to show you know it
- "kth largest/smallest in unsorted array" → QuickSelect (O(n) avg) or heap (O(n log k))
- "Minimum rooms / containers / workers for overlapping intervals" → sort + heap tracking ends
- "Maximum overlap at any point" → same as meeting rooms
- "Can you do better than O(n log n)?" → usually means the answer range is bounded (bucket sort, counting sort) or the problem is structured enough for a linear algorithm

When the problem involves intervals, sort by start time first. Almost every interval problem becomes tractable after that one step.

## Key Takeaway

Sorting is not a primitive — it's a technique. Merge sort teaches divide-and-conquer and stable sort semantics. QuickSelect teaches partition-based selection and average-case O(n). Meeting rooms teaches how sorting transforms a complex temporal query into a simple greedy sweep.

When you're stuck on an array problem, ask: "What does this array look like if it's sorted?" The structure that appears often suggests the algorithm that solves it.

---

*[← Lesson 7: Recursion](/post/fundamentals/interview-recursion/) | [Course Index](/series/interview-patterns-cracking-faang-in-the-ai-era/) | [Lesson 9: Bit Manipulation →](/post/fundamentals/interview-bit-manipulation/)*
