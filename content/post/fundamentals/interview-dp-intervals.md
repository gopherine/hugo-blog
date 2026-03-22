---
title: "Lesson 28: Interval DP — Optimal strategy between boundaries"
author: "Atharva Pandey"
keywords:
  - dynamic programming
  - interval DP
  - burst balloons
  - matrix chain multiplication
  - minimum cost tree from leaf values
  - range DP
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 28
date: "2025-09-19T00:00:00.000Z"
---

Interval DP is the pattern that solves problems where you need to find the optimal way to process a contiguous segment, and the answer depends on how you choose to "split" or "last-process" within that segment. The classic examples — Burst Balloons, Matrix Chain Multiplication, Optimal BST — all share the same skeleton: try every possible "last operation" position k within [i, j], and combine the subproblems for [i, k] and [k, j].

The fill order is the key practical challenge. You can't fill left to right across the whole table because `dp[i][j]` depends on `dp[i][k]` and `dp[k][j]` — subintervals of different shapes. The correct order is by increasing interval length: fill all subintervals of length 1, then length 2, then length 3, and so on. When you reach length L, all subintervals of length < L are already filled.

## The Pattern

Interval DP follows this shape:

1. `dp[i][j]` = optimal answer for the subproblem on the segment [i, j].
2. For each interval [i, j], try all split points k where i <= k < j (or i <= k <= j, depending on the problem).
3. `dp[i][j] = optimize over k of (dp[i][k] + dp[k+1][j] + cost(i, j, k))`.
4. Fill by increasing length: `for length = 1 to n: for i = 0 to n-length: j = i + length - 1`.

---

## Problem 1: Burst Balloons

**LeetCode 312.** You have n balloons with numbers. When you burst balloon i, you earn `nums[i-1] * nums[i] * nums[i+1]` coins (boundaries are 1 if out of range). Return the maximum coins you can collect by bursting all balloons.

This problem has a clever twist: thinking about which balloon to burst *last* (not first) makes the subproblems independent.

### The key insight

If you think about which balloon to burst first, the subproblems are not independent: bursting one balloon changes the neighbors of remaining balloons. But if you think about which balloon in [i, j] to burst *last*, the boundaries i-1 and j+1 are always still present when the last balloon in [i, j] is burst.

### Define the subproblem

Add sentinel 1s at both ends: `nums = [1] + original + [1]`, so indices go from 0 to n+1.

`dp[i][j]` = maximum coins from bursting all balloons strictly between indices i and j (exclusive). The balloons at i and j are the boundaries and are never burst as part of this subproblem.

### Recurrence

For each possible last balloon k to burst in the open interval (i, j):

```
dp[i][j] = max over k in (i+1, j-1) of:
    dp[i][k] + nums[i] * nums[k] * nums[j] + dp[k][j]
```

`nums[i] * nums[k] * nums[j]` is the coins from bursting k last, when i and j are still intact as boundaries.

Base case: `dp[i][j] = 0` when j <= i+1 (no balloons between them).

### Build bottom-up

```go
func maxCoins(nums []int) int {
    // Add boundary sentinels
    arr := make([]int, len(nums)+2)
    arr[0] = 1
    arr[len(arr)-1] = 1
    for i, v := range nums {
        arr[i+1] = v
    }
    n := len(arr)

    dp := make([][]int, n)
    for i := range dp {
        dp[i] = make([]int, n)
    }

    // Fill by increasing length of the open interval (i, j)
    // length here is j - i, so we go from 2 (adjacent, no interior) upward
    for length := 2; length < n; length++ {
        for i := 0; i+length < n; i++ {
            j := i + length
            for k := i + 1; k < j; k++ {
                coins := dp[i][k] + arr[i]*arr[k]*arr[j] + dp[k][j]
                if coins > dp[i][j] {
                    dp[i][j] = coins
                }
            }
        }
    }
    return dp[0][n-1]
}
```

O(n³) time, O(n²) space. The sentinel array trick is essential — without it, boundary checks clutter the recurrence.

---

## Problem 2: Matrix Chain Multiplication

**Classic CS problem / LeetCode 1039 variant.** Given a chain of matrices where matrix i has dimensions `dims[i-1] × dims[i]`, find the minimum number of scalar multiplications needed to compute their product.

### Define the subproblem

`dp[i][j]` = minimum multiplications to compute the product of matrices i through j (inclusive, 1-indexed, so matrix i has dimensions `dims[i-1] × dims[i]`).

### Recurrence

For each split point k between i and j (the last matrix multiplication performed):

```
dp[i][j] = min over k in [i, j-1] of:
    dp[i][k] + dp[k+1][j] + dims[i-1] * dims[k] * dims[j]
```

`dims[i-1] * dims[k] * dims[j]` is the cost of multiplying the two resulting matrices.

Base case: `dp[i][i] = 0` (a single matrix needs no multiplication).

### Build bottom-up

```go
func matrixChainOrder(dims []int) int {
    // dims[i] = rows of matrix i+1; matrix i has dims[i] x dims[i+1]
    n := len(dims) - 1 // number of matrices
    dp := make([][]int, n+1)
    for i := range dp {
        dp[i] = make([]int, n+1)
    }
    // dp[i][i] = 0 by default

    for length := 2; length <= n; length++ {
        for i := 1; i+length-1 <= n; i++ {
            j := i + length - 1
            dp[i][j] = 1<<31 - 1
            for k := i; k < j; k++ {
                cost := dp[i][k] + dp[k+1][j] + dims[i-1]*dims[k]*dims[j]
                if cost < dp[i][j] {
                    dp[i][j] = cost
                }
            }
        }
    }
    return dp[1][n]
}
```

O(n³) time, O(n²) space. Matrix Chain is the textbook interval DP. The LeetCode version (Minimum Score Triangulation of Polygon, #1039) has an identical structure with triangles instead of matrices.

---

## Problem 3: Minimum Cost Tree From Leaf Values

**LeetCode 1130.** Given an array arr of positive integers, consider all binary trees such that each leaf corresponds to an element of arr (in order). The score of each non-leaf node is the product of the two largest leaf values in its subtrees. Return the minimum sum of non-leaf node values over all possible such trees.

This is a matrix-chain-style interval DP: the array is fixed in order, and we're choosing how to parenthesize it (how to split into left and right subtrees).

### Define the subproblem

`dp[i][j]` = minimum sum of non-leaf node values for a tree built from arr[i..j].

### Recurrence

For each split point k in [i, j-1], the root has two subtrees: [i, k] and [k+1, j]. The root's value is `maxLeaf(i, k) * maxLeaf(k+1, j)`. The maxLeaf values can be precomputed.

```
dp[i][j] = min over k in [i, j-1] of:
    dp[i][k] + dp[k+1][j] + maxLeaf[i][k] * maxLeaf[k+1][j]
```

Base case: `dp[i][i] = 0` (a single leaf has no non-leaf nodes).

### Build bottom-up

```go
func mctFromLeafValues(arr []int) int {
    n := len(arr)

    // Precompute maxLeaf[i][j] = max value in arr[i..j]
    maxLeaf := make([][]int, n)
    for i := range maxLeaf {
        maxLeaf[i] = make([]int, n)
        maxLeaf[i][i] = arr[i]
    }
    for length := 2; length <= n; length++ {
        for i := 0; i+length-1 < n; i++ {
            j := i + length - 1
            if arr[j] > maxLeaf[i][j-1] {
                maxLeaf[i][j] = arr[j]
            } else {
                maxLeaf[i][j] = maxLeaf[i][j-1]
            }
        }
    }

    // DP
    dp := make([][]int, n)
    for i := range dp {
        dp[i] = make([]int, n)
    }

    for length := 2; length <= n; length++ {
        for i := 0; i+length-1 < n; i++ {
            j := i + length - 1
            dp[i][j] = 1<<31 - 1
            for k := i; k < j; k++ {
                cost := dp[i][k] + dp[k+1][j] + maxLeaf[i][k]*maxLeaf[k+1][j]
                if cost < dp[i][j] {
                    dp[i][j] = cost
                }
            }
        }
    }
    return dp[0][n-1]
}
```

O(n³) time, O(n²) space.

**Note:** this problem also has an O(n) greedy solution using a monotonic stack, but the DP solution demonstrates the interval DP pattern more clearly and is expected in an interview if the interviewer is testing DP patterns.

---

## How to Recognize This Pattern

You're looking at interval DP when:

- The input is a sequence or array that you process in segments.
- The problem asks for optimal splitting, ordering, or parenthesization.
- The cost of processing [i, j] depends on which "last operation" or "split point" you choose within it.
- Subproblems are defined as intervals [i, j], not just prefixes dp[i].

Watch for keywords: "burst," "merge," "split," "chain," "parenthesization," "triangulation." Also watch for problems about trees built over a fixed-order leaf array — those are interval DP on the array.

The fill order diagnostic: if you find yourself wanting to compute dp[i][j] but it depends on dp[i][k] and dp[k][j] where k is between i and j, you need to fill by increasing length.

---

## Key Takeaway

Interval DP's mantra: **try every possible last operation**. The genius of Burst Balloons is the insight that "last burst" makes subproblems independent, while "first burst" doesn't.

All three problems have the same O(n³) time complexity from the triple nested loop. The code template is always: outer loop over lengths, middle loop over start positions, inner loop over split points k.

Precomputing auxiliary information (maxLeaf, balloon sentinels) before the main DP loop keeps the recurrence clean. Don't try to compute auxiliary values inline.

---

*Previous: [Lesson 27: Knapsack Patterns](/post/fundamentals/interview-dp-knapsack/) · Next: [Lesson 29: State Machine DP](/post/fundamentals/interview-dp-state-machine/)*
