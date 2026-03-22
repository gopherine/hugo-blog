---
title: "Lesson 30: Bitmask DP — When the state is a set"
author: "Atharva Pandey"
keywords:
  - dynamic programming
  - bitmask DP
  - travelling salesman
  - TSP
  - partition to k equal sum subsets
  - minimum XOR sum
  - subset DP
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 30
date: "2025-10-17T00:00:00.000Z"
---

Every DP pattern we've covered has kept the state manageable: an index, a remaining budget, a mode. Bitmask DP enters the picture when the state is a *subset* of elements — specifically, which elements from a small set have been included or visited so far.

The representation is elegant: a bitmask of n bits, where bit i is 1 if element i is in the current subset and 0 otherwise. With n = 20, there are 2²⁰ ≈ 1 million possible subsets. That's the practical ceiling for bitmask DP — you'll see n ≤ 20 in problem constraints, and that's the signal.

The canonical problem is Travelling Salesman: find the minimum-cost tour visiting all n cities exactly once. With n = 20, brute force over all n! permutations is hopeless, but DP over all 2^n subsets × n endpoints is tractable.

## The Pattern

Bitmask DP follows this shape:

1. State: `dp[mask][i]` — the answer for a subset represented by `mask` where i is some "last chosen" element.
2. Transition: try adding each element j not yet in `mask` to extend the state.
3. `dp[mask | (1 << j)][j] = optimize(dp[mask][i], cost(i, j))`
4. Fill by increasing popcount of mask (or just iterate mask from 0 to 2^n, which works because adding an element always increases the mask value).

---

## Problem 1: Travelling Salesman Problem

**Classic / LeetCode 847 variant.** Given n cities and a distance matrix `dist[i][j]`, find the minimum-cost tour that starts at city 0, visits every city exactly once, and returns to city 0.

### Define the subproblem

`dp[mask][i]` = minimum distance to visit exactly the cities in `mask`, ending at city i, starting from city 0.

`mask` is a bitmask of n bits. Bit j is set if city j has been visited.

### Recurrence

From state (mask, i), extend to city j (not yet visited):

```
dp[mask | (1<<j)][j] = min(dp[mask | (1<<j)][j], dp[mask][i] + dist[i][j])
```

for each j where bit j is not set in mask.

Final answer: `min over all i of (dp[(1<<n)-1][i] + dist[i][0])`.

`(1<<n)-1` is the full mask with all n cities visited.

### Build bottom-up

```go
func tsp(dist [][]int) int {
    n := len(dist)
    const inf = 1<<31 - 1
    full := (1 << n) - 1

    dp := make([][]int, 1<<n)
    for i := range dp {
        dp[i] = make([]int, n)
        for j := range dp[i] {
            dp[i][j] = inf
        }
    }
    dp[1][0] = 0 // started at city 0, only city 0 visited (mask = 0b...001)

    for mask := 1; mask <= full; mask++ {
        for i := 0; i < n; i++ {
            if dp[mask][i] == inf {
                continue
            }
            // i must be in mask
            if mask&(1<<i) == 0 {
                continue
            }
            // try extending to city j
            for j := 0; j < n; j++ {
                if mask&(1<<j) != 0 {
                    continue // j already visited
                }
                nextMask := mask | (1 << j)
                newCost := dp[mask][i] + dist[i][j]
                if newCost < dp[nextMask][j] {
                    dp[nextMask][j] = newCost
                }
            }
        }
    }

    result := inf
    for i := 1; i < n; i++ {
        if dp[full][i] != inf {
            cost := dp[full][i] + dist[i][0]
            if cost < result {
                result = cost
            }
        }
    }
    return result
}
```

O(2^n × n²) time, O(2^n × n) space. For n = 20, that's about 20 million states with 20 transitions each — feasible in about 400 million operations, which is tight but passes with efficient code.

**Bit operations to know:**
- Test if bit j is set: `mask & (1 << j) != 0`
- Set bit j: `mask | (1 << j)`
- Clear bit j: `mask & ^(1 << j)`  (or `mask &^ (1 << j)` in Go)
- Iterate all set bits: `for mask != 0 { j := bits.TrailingZeros(uint(mask)); mask &^= (1 << j) }`

---

## Problem 2: Partition to K Equal Sum Subsets

**LeetCode 698.** Given an integer array nums and an integer k, return true if it is possible to divide the array into k non-empty subsets whose sums are all equal.

### The approach

Target sum per subset = totalSum / k. If totalSum isn't divisible by k, return false.

Use bitmask DP: `dp[mask]` = the sum accumulated in the "current partially filled bucket" when exactly the elements in `mask` have been assigned to any bucket.

We process elements one bucket at a time. When the current bucket reaches the target, we start a new one (reset current sum to 0).

### Define the subproblem

`dp[mask]` = the running sum of the current (partially filled) bucket when the elements corresponding to `mask` have been distributed across completed and current buckets.

If `dp[mask] == -1`, this mask is not reachable.

If `dp[mask] >= 0`, its value is the current bucket's sum mod target (since full buckets restart at 0).

### Recurrence

For each mask, try adding each element not yet in mask:

```
newSum = dp[mask] + nums[j]
if newSum <= target:
    dp[mask | (1<<j)] = newSum % target
```

Start: `dp[0] = 0`. End: `dp[full] == 0` (all elements assigned, all buckets complete).

### Build bottom-up

```go
func canPartitionKSubsets(nums []int, k int) bool {
    total := 0
    for _, n := range nums {
        total += n
    }
    if total%k != 0 {
        return false
    }
    target := total / k

    n := len(nums)
    full := (1 << n) - 1
    dp := make([]int, 1<<n)
    for i := range dp {
        dp[i] = -1
    }
    dp[0] = 0

    for mask := 0; mask < full; mask++ {
        if dp[mask] == -1 {
            continue
        }
        for j := 0; j < n; j++ {
            if mask&(1<<j) != 0 {
                continue // already used
            }
            if dp[mask]+nums[j] > target {
                continue // exceeds current bucket
            }
            nextMask := mask | (1 << j)
            if dp[nextMask] == -1 {
                dp[nextMask] = (dp[mask] + nums[j]) % target
            }
        }
    }
    return dp[full] == 0
}
```

O(2^n × n) time, O(2^n) space. The `% target` trick: when the current bucket sum equals target, it resets to 0 for the next bucket. So `dp[mask]` always holds the current bucket's running sum modulo target, which is sufficient to determine valid transitions.

**Optimization:** sort nums in descending order first. Large elements constrain placement earlier, pruning many invalid paths.

---

## Problem 3: Minimum XOR Sum of Two Arrays

**LeetCode 1879.** Given two integer arrays nums1 and nums2 of equal length n, assign each element of nums2 to an element of nums1 such that the XOR sum is minimized. Each element of nums2 must be assigned to exactly one element of nums1.

This is a minimum-cost assignment problem. With n ≤ 14, bitmask DP over all subsets of nums2 is tractable.

### Define the subproblem

Process nums1 elements left to right. The bitmask tracks which elements of nums2 have been assigned so far.

`dp[mask]` = minimum XOR sum when assigning elements of nums2 corresponding to `mask` to the first popcount(mask) elements of nums1.

If we've assigned k elements from nums2 (k = popcount(mask)), then we've processed nums1[0..k-1].

### Recurrence

From `dp[mask]` (k elements assigned, k = popcount(mask)):

```
dp[mask | (1<<j)] = min(dp[mask | (1<<j)], dp[mask] + (nums1[k] XOR nums2[j]))
```

for each j not in mask, where k = popcount(mask).

### Build bottom-up

```go
import "math/bits"

func minimumXORSum(nums1 []int, nums2 []int) int {
    n := len(nums1)
    const inf = 1<<31 - 1
    dp := make([]int, 1<<n)
    for i := range dp {
        dp[i] = inf
    }
    dp[0] = 0

    for mask := 0; mask < (1 << n); mask++ {
        if dp[mask] == inf {
            continue
        }
        k := bits.OnesCount(uint(mask)) // index into nums1
        if k == n {
            continue
        }
        for j := 0; j < n; j++ {
            if mask&(1<<j) != 0 {
                continue
            }
            nextMask := mask | (1 << j)
            cost := dp[mask] + (nums1[k] ^ nums2[j])
            if cost < dp[nextMask] {
                dp[nextMask] = cost
            }
        }
    }
    return dp[(1<<n)-1]
}
```

O(2^n × n) time, O(2^n) space. `bits.OnesCount` (from the `math/bits` package in Go) counts set bits in O(1) on modern hardware. This converts between "which nums2 elements have been used" and "which nums1 element we're currently assigning."

---

## How to Recognize This Pattern

You're looking at bitmask DP when:

- The problem involves n ≤ 20 items and asks about all possible **subsets** or **permutations** of those items.
- The state needs to encode "which elements have been visited/used."
- The constraint says n ≤ 15 or n ≤ 20 (the bitmask size constraint).

Watch for: "visit all nodes exactly once," "assign each element exactly once," "partition into k groups." If n is small (say, ≤ 20) and the problem involves tracking membership across an entire set, bitmask DP is the intended approach.

**When bitmask DP won't work:** if n > 20 or 25, the 2^n state space is too large. You'd need a different approach (approximation, heuristic, or reformulation).

---

## Key Takeaway

Bitmask DP encodes "which elements are in the current set" as an integer's binary representation. Transitions set or clear individual bits. The state space is 2^n × (last element or other dimension), and you iterate masks in ascending order to ensure all sub-masks are computed before the masks that contain them.

The three operations to memorize: test a bit (`mask & (1<<j)`), set a bit (`mask | (1<<j)`), count set bits (`bits.OnesCount(uint(mask))`). With those three, you can implement any bitmask DP.

This is the final DP pattern in this series. From Climbing Stairs to TSP — all of it is the same core idea: define subproblems, identify transitions, fill in order. The patterns just vary in what the state looks like.

---

*Previous: [Lesson 29: State Machine DP](/post/fundamentals/interview-dp-state-machine/)*
