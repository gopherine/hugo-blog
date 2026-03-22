---
title: "Lesson 39: Hard Composites — When one pattern isn't enough"
author: "Atharva Pandey"
keywords:
  - median of two sorted arrays
  - sliding window maximum
  - alien dictionary
  - topological sort
  - trie
  - composite patterns
  - interview patterns
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 39
date: "2026-02-15T00:00:00.000Z"
---

There is a class of interview problem designed specifically to differentiate senior candidates. These are not problems where knowing one pattern is enough — they require you to recognize that two or three patterns need to compose, figure out the seam between them, and implement the composition cleanly under pressure. I call them hard composites.

The candidates who struggle here usually know the individual patterns. The gap is the synthesis. They apply binary search but miss that the search space itself requires a merge step. They build the trie but miss that the relationships between words encode a graph that needs topological sort. Practice the composites explicitly, not just their constituent patterns.

## The Pattern

Hard composite problems have two signatures:

1. **The naive approach using one pattern is too slow**, and the correct solution combines two patterns — typically one to build structure and one to query it.
2. **The problem involves two independent data sources or constraints** that must be reconciled, and each source requires its own algorithmic technique.

The discipline: when you see a hard problem, decompose it. Ask "what is the bottleneck in the naive solution?" and "what structure would make that bottleneck cheaper?" The answer often names a second pattern.

## Problem 1: Median of Two Sorted Arrays

Find the median of two sorted arrays with O(log(m+n)) time.

The naive approach: merge and find the middle. O(m+n). The hard part: getting to O(log(m+n)) requires binary search on a partition, not on the values themselves.

**Composition**: binary search + merge (conceptual)

```go
func findMedianSortedArrays(nums1 []int, nums2 []int) float64 {
    // Ensure nums1 is the smaller array to minimize binary search space
    if len(nums1) > len(nums2) {
        nums1, nums2 = nums2, nums1
    }
    m, n := len(nums1), len(nums2)
    total := m + n
    half := total / 2

    lo, hi := 0, m

    for {
        i := (lo + hi) / 2 // partition index in nums1
        j := half - i      // partition index in nums2

        // Elements just left and right of each partition
        nums1Left := math.MinInt64
        if i > 0 {
            nums1Left = nums1[i-1]
        }
        nums1Right := math.MaxInt64
        if i < m {
            nums1Right = nums1[i]
        }
        nums2Left := math.MinInt64
        if j > 0 {
            nums2Left = nums2[j-1]
        }
        nums2Right := math.MaxInt64
        if j < n {
            nums2Right = nums2[j]
        }

        if nums1Left <= nums2Right && nums2Left <= nums1Right {
            // Correct partition found
            if total%2 == 1 {
                return float64(min(nums1Right, nums2Right))
            }
            return float64(max(nums1Left, nums2Left)+min(nums1Right, nums2Right)) / 2.0
        } else if nums1Left > nums2Right {
            hi = i - 1 // move partition left in nums1
        } else {
            lo = i + 1 // move partition right in nums1
        }
    }
}

func min(a, b int) int {
    if a < b {
        return a
    }
    return b
}
func max(a, b int) int {
    if a > b {
        return a
    }
    return b
}
```

The composite insight: we are not binary searching for a value — we are binary searching for a **partition position** that satisfies a cross-array invariant. The invariant is that the left halves of both arrays together form exactly `half` elements, and `max(left sides) <= min(right sides)`.

This requires understanding what the median means structurally: it is the point where the merged array would be split in half. Binary search on the partition in the smaller array automatically determines the partition in the larger array. Time: O(log(min(m,n))).

## Problem 2: Sliding Window Maximum

Given an array and window size k, find the maximum value in each window.

The naive approach: scan each window. O(nk). The optimal approach requires recognizing that a monotonic deque (double-ended queue) maintains the running maximum across a sliding window in O(1) amortized.

**Composition**: sliding window + monotonic deque

```go
func maxSlidingWindow(nums []int, k int) []int {
    if len(nums) == 0 || k == 0 {
        return nil
    }

    // Deque stores indices of potentially useful elements
    // Invariant: elements in deque are in decreasing order of value
    dq := []int{}
    result := []int{}

    for i, num := range nums {
        // Remove elements outside the window
        for len(dq) > 0 && dq[0] < i-k+1 {
            dq = dq[1:]
        }
        // Remove smaller elements from the back — they can never be the max
        for len(dq) > 0 && nums[dq[len(dq)-1]] < num {
            dq = dq[:len(dq)-1]
        }
        dq = append(dq, i)

        // Window has reached size k: record the max
        if i >= k-1 {
            result = append(result, nums[dq[0]])
        }
    }
    return result
}
```

Time: O(n). Each element is added and removed from the deque at most once.

The monotonic deque maintains a window-scoped monotonically decreasing sequence. The front is always the index of the current window's maximum. When the window slides, we remove expired indices from the front. When a new element arrives, we pop smaller elements from the back (they can never be the max while this new, larger element is in the window).

This is the sliding window pattern (two pointers maintaining a window) composed with the monotonic stack insight (maintain decreasing order, pop when larger element arrives). The deque is the data structure that lets you apply both operations — pop from front and pop/push from back — in O(1).

## Problem 3: Alien Dictionary

Given a sorted list of words in an alien language, determine the order of characters in the alien alphabet.

**Composition**: trie (or direct comparison) to extract ordering constraints + topological sort (BFS/Kahn's) to linearize them

```go
func alienOrder(words []string) string {
    // Step 1: Build adjacency list for all unique characters
    graph := map[byte][]byte{}
    indegree := map[byte]int{}
    for _, word := range words {
        for i := 0; i < len(word); i++ {
            if _, ok := indegree[word[i]]; !ok {
                indegree[word[i]] = 0
                graph[word[i]] = []byte{}
            }
        }
    }

    // Step 2: Extract ordering constraints by comparing adjacent words
    for i := 0; i < len(words)-1; i++ {
        w1, w2 := words[i], words[i+1]
        minLen := len(w1)
        if len(w2) < minLen {
            minLen = len(w2)
        }
        // Invalid input: w1 is a prefix of w2 but w1 comes after w2
        if len(w1) > len(w2) && w1[:minLen] == w2[:minLen] {
            return ""
        }
        for j := 0; j < minLen; j++ {
            if w1[j] != w2[j] {
                // w1[j] comes before w2[j] in the alien alphabet
                graph[w1[j]] = append(graph[w1[j]], w2[j])
                indegree[w2[j]]++
                break // only the first differing character gives us ordering info
            }
        }
    }

    // Step 3: Topological sort via BFS (Kahn's algorithm)
    queue := []byte{}
    for ch, deg := range indegree {
        if deg == 0 {
            queue = append(queue, ch)
        }
    }
    // Sort queue for deterministic output (tiebreak lexicographically)
    sort.Slice(queue, func(i, j int) bool { return queue[i] < queue[j] })

    result := []byte{}
    for len(queue) > 0 {
        ch := queue[0]
        queue = queue[1:]
        result = append(result, ch)
        neighbors := graph[ch]
        sort.Slice(neighbors, func(i, j int) bool { return neighbors[i] < neighbors[j] })
        for _, next := range neighbors {
            indegree[next]--
            if indegree[next] == 0 {
                queue = append(queue, next)
            }
        }
    }

    if len(result) != len(indegree) {
        return "" // cycle detected: no valid ordering
    }
    return string(result)
}
```

The two-phase composition: first, extract directed edges from the input (each adjacent pair of words contributes at most one edge — the first character position where they differ). Second, linearize those edges with Kahn's topological sort. If the sort cannot include all characters, there is a cycle, meaning the input is contradictory.

The edge cases that trip people up: a word that is longer than the next word but shares the same prefix is invalid input (return empty string). Characters that appear in the words but have no ordering constraints relative to others get indegree 0 and go into the initial queue.

## How to Recognize This Pattern

Hard composites are identifiable by their constraints:

1. **O(log n) on unsorted or multiple-sorted inputs** — suspect binary search on a partition or on an implicit sorted property.
2. **Sliding window combined with "efficient range maximum/minimum"** — sliding window + monotonic deque. The deque replaces a segment tree for this specific query type.
3. **Ordering relationships extracted from sequence comparisons** — topological sort. The trie connection in Alien Dictionary: you can also build a trie of the words and traverse it to extract edges, though direct comparison is simpler.
4. **"What is the order" or "is this consistent"** on a set of constraints — graph + topological sort, possibly with cycle detection.

When a problem's time complexity requirement has `log` in it and the input is not a single sorted array, look for a binary search on a derived quantity — a partition, a count, an index — rather than on values directly.

## Key Takeaway

Hard composite problems are not fundamentally harder than their constituent patterns. They are harder because you need to see two patterns at once and figure out how they interface. The skill to develop is decomposition: given the bottleneck in the naive solution, what structure eliminates it, and how do I extract the input that structure needs?

Practice by solving each constituent pattern individually first, then re-reading hard composites and explicitly naming which patterns are composing. After a few cycles, the compositions start to feel predictable.

---

**Series**: [Interview Patterns: Cracking FAANG+ in the AI Era](/series/interview-patterns-cracking-faang-in-the-ai-era/)

**Previous**: [Lesson 38: Design Problems](/post/fundamentals/interview-design/) — Build it from scratch in 30 minutes

**Next**: [Lesson 40: Mock Interview Strategy](/post/fundamentals/interview-strategy/) — The 45-minute framework for any problem
