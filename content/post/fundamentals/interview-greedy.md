---
title: "Lesson 35: Greedy — Local optimal leads to global optimal (sometimes)"
author: "Atharva Pandey"
keywords:
  - greedy algorithm
  - jump game
  - non-overlapping intervals
  - partition labels
  - interview patterns
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 35
date: "2025-12-23T00:00:00.000Z"
---

Greedy algorithms have a brutal failure mode in interviews: the solution looks obvious, you implement it in fifteen minutes, and then the interviewer asks "does this always work?" and you have no good answer. I have been on both sides of that question. Greedy is powerful when the problem has the right structure, and dangerously wrong when it does not.

The discipline is learning to tell the difference. Most greedy interview problems are structured so that a correct greedy choice exists — the challenge is identifying what that choice is and, if asked, arguing why locally optimal decisions accumulate to a globally optimal result.

## The Pattern

A greedy algorithm makes the locally optimal choice at each step. No look-ahead, no backtracking, no reconsidering. For this to produce a globally optimal result, the problem must have two properties:

1. **Greedy choice property**: a globally optimal solution can be constructed by always making the locally optimal choice.
2. **Optimal substructure**: after making the greedy choice, the remaining subproblem has the same structure as the original.

These properties are why DP and greedy feel related — both require optimal substructure. The difference is that DP explores all subproblem solutions, while greedy commits to one without reconsidering.

For interview purposes, you typically prove correctness via an exchange argument: "assume the optimal solution disagrees with my greedy choice at step k. I can swap the optimal solution's choice with my greedy choice and the result does not get worse. Therefore greedy is at least as good as optimal."

## Problem 1: Jump Game I / II

**Jump Game I**: given an array where each element is your maximum jump distance, can you reach the last index?

```go
func canJump(nums []int) bool {
    maxReach := 0
    for i := 0; i < len(nums); i++ {
        if i > maxReach {
            return false // we can't even reach this index
        }
        if i+nums[i] > maxReach {
            maxReach = i + nums[i]
        }
    }
    return true
}
```

Greedy choice: always track the farthest index reachable from any index visited so far. If the current index exceeds `maxReach`, we are stuck. Time: O(n). Space: O(1).

**Jump Game II**: find the minimum number of jumps to reach the last index.

```go
func jump(nums []int) int {
    jumps := 0
    currentEnd := 0  // farthest index reachable with `jumps` jumps
    farthest := 0    // farthest index reachable from any index in current range

    for i := 0; i < len(nums)-1; i++ {
        if i+nums[i] > farthest {
            farthest = i + nums[i]
        }
        if i == currentEnd {
            jumps++
            currentEnd = farthest
        }
    }
    return jumps
}
```

The insight: you are implicitly grouping indices into levels — all indices reachable in exactly k jumps form one level. When you exhaust the current level (`i == currentEnd`), you must jump, extending to the farthest index reachable from that level. This is essentially BFS without the queue overhead.

Time: O(n). One pass, constant space. The greedy choice — always extend to the farthest possible index at each "level boundary" — provably minimizes jumps.

## Problem 2: Non-overlapping Intervals

Given an array of intervals, find the minimum number of intervals to remove so the rest do not overlap.

This is equivalent to finding the maximum number of non-overlapping intervals, then subtracting from total. The greedy choice: always keep the interval with the **earliest ending time** among those that fit.

```go
func eraseOverlapIntervals(intervals [][]int) int {
    if len(intervals) == 0 {
        return 0
    }
    // Sort by end time
    sort.Slice(intervals, func(i, j int) bool {
        return intervals[i][1] < intervals[j][1]
    })

    kept := 1
    lastEnd := intervals[0][1]

    for i := 1; i < len(intervals); i++ {
        if intervals[i][0] >= lastEnd {
            // This interval starts after the last kept one ends — keep it
            kept++
            lastEnd = intervals[i][1]
        }
        // Otherwise it overlaps — skip it (remove it)
    }
    return len(intervals) - kept
}
```

Time: O(n log n) for sort, O(n) for the sweep. Space: O(1).

Why sort by end time, not start time? If you sort by start time, you might greedily take a long interval that blocks many future intervals. Sorting by end time and always taking the next compatible interval maximizes the count — this is the classic activity selection argument.

The exchange argument: suppose the optimal solution does not take the interval with the earliest end time at some step. Call that interval A (earliest end) and the optimal's choice B (some other non-overlapping interval). We can replace B with A — A ends no later than B, so it cannot block any interval that B would not also block. The result is at least as good.

## Problem 3: Partition Labels

Given a string `s`, partition it into as many parts as possible so that each letter appears in at most one part. Return the sizes of the parts.

```go
func partitionLabels(s string) []int {
    // Record the last occurrence of each character
    last := [26]int{}
    for i, ch := range s {
        last[ch-'a'] = i
    }

    result := []int{}
    start, end := 0, 0

    for i, ch := range s {
        if last[ch-'a'] > end {
            end = last[ch-'a'] // extend current partition's required range
        }
        if i == end {
            // We've reached the end of this partition
            result = append(result, end-start+1)
            start = end + 1
        }
    }
    return result
}
```

Time: O(n). Space: O(1) — the `last` array is fixed at 26.

The greedy choice: as you scan left to right, maintain the farthest index `end` that must be included in the current partition (because some character at or before `i` has its last occurrence at `end`). When you reach `i == end`, you have found the smallest valid cut.

This is closely related to the merge intervals pattern. You are essentially computing the union of intervals `[first_occurrence, last_occurrence]` for each character encountered, and cutting wherever a union ends.

## How to Recognize This Pattern

Greedy problems tend to share surface features:

1. **"Maximum/minimum number of..."** combined with a simple ordering — often solvable with one sweep after a sort.
2. **Scheduling or interval problems** — sorting by start or end time and scanning once covers most of them.
3. **The problem feels like it should be DP but the recurrence has only one choice that matters** — suspect greedy.
4. **"Can you reach X?"** style reachability questions with local progress — think jump game.

The danger signal: if you cannot articulate why the greedy choice is safe ("taking the interval with the earliest end is always as good as any other choice"), slow down. Counter-examples are often simple. The classic trap is the knapsack problem — items with the best ratio of value/weight do not always produce the optimal result for bounded knapsack, so greedy fails there.

When in doubt, try a few examples. If every greedy choice you make feels irreversible but obviously correct, you are probably on solid ground. If you feel like you are "getting lucky" on examples, consider whether DP is more appropriate.

## Key Takeaway

Greedy algorithms are fast and elegant when they apply. The pattern that covers 80% of greedy interview problems: sort the input by the right key, scan once, make a local decision at each step, and trust that the decisions accumulate correctly.

The "right key" varies: end time for interval problems, farthest reach for jump problems, last occurrence for partition problems. Identifying the sort key is usually the hard part. Once you have it, the code is often just ten lines.

Always be prepared to justify your greedy choice. "It feels right" is not an answer. Know the exchange argument at least informally.

---

**Series**: [Interview Patterns: Cracking FAANG+ in the AI Era](/series/interview-patterns-cracking-faang-in-the-ai-era/)

**Previous**: [Lesson 34: Backtracking](/post/fundamentals/interview-backtracking/) — Try everything, undo what doesn't work

**Next**: [Lesson 36: Intervals](/post/fundamentals/interview-intervals/) — Sort by start, merge by end
