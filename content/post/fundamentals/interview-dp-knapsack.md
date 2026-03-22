---
title: "Lesson 27: Knapsack Patterns — Pick or skip, that's the whole pattern"
author: "Atharva Pandey"
keywords:
  - dynamic programming
  - knapsack
  - 0/1 knapsack
  - partition equal subset sum
  - target sum
  - subset sum
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 27
date: "2025-09-05T00:00:00.000Z"
---

Knapsack is one of those patterns that shows up in disguise constantly. You'll see problems framed as "partition this array," "find a subset with sum X," "assign +/- signs to get target T" — and underneath each one is the same fundamental structure: for each item, decide whether to include it or exclude it.

The standard 0/1 Knapsack is the reference problem. Every variant is a modification of it: different objective functions (count instead of max value), different constraints (exact sum instead of capacity), different item usage rules (unbounded instead of once). Once you internalize the base pattern, the variants fall into place.

The space optimization for knapsack (right-to-left traversal of the capacity/sum dimension) is one of the most commonly tested interview techniques. Know it cold.

## The Pattern

0/1 Knapsack DP follows this shape:

- State: `dp[i][w]` = answer considering the first i items with capacity/budget w.
- For each item i, two transitions: skip it (inherit `dp[i-1][w]`) or take it (use `dp[i-1][w - weight[i]] + value[i]`).
- Fill row by row (item by item), left to right within each row.
- Space optimize by doing a single 1D array traversed right to left.

---

## Problem 1: 0/1 Knapsack

**Classic problem.** Given n items each with a weight and value, and a knapsack of capacity W, maximize the total value without exceeding the weight capacity. Each item can be used at most once.

### Define the subproblem

`dp[i][w]` = maximum value achievable using the first i items with exactly w capacity available.

### Recurrence

For item i (0-indexed, weight `wt[i]`, value `val[i]`):

```
dp[i][w] = dp[i-1][w]                           // skip item i
if w >= wt[i]:
    dp[i][w] = max(dp[i][w], dp[i-1][w-wt[i]] + val[i])  // take item i
```

Base case: `dp[0][w] = 0` for all w.

### Build bottom-up

```go
func knapsack(weights []int, values []int, W int) int {
    n := len(weights)
    dp := make([][]int, n+1)
    for i := range dp {
        dp[i] = make([]int, W+1)
    }

    for i := 1; i <= n; i++ {
        wt, val := weights[i-1], values[i-1]
        for w := 0; w <= W; w++ {
            dp[i][w] = dp[i-1][w] // skip
            if w >= wt && dp[i-1][w-wt]+val > dp[i][w] {
                dp[i][w] = dp[i-1][w-wt] + val // take
            }
        }
    }
    return dp[n][W]
}
```

### Space optimization

When computing row i, we only look at row i-1. Use a single 1D array and traverse right to left so that `dp[w-wt]` still holds the i-1 value (hasn't been overwritten yet by row i):

```go
func knapsackOpt(weights []int, values []int, W int) int {
    dp := make([]int, W+1)

    for i, wt := range weights {
        val := values[i]
        for w := W; w >= wt; w-- {
            if dp[w-wt]+val > dp[w] {
                dp[w] = dp[w-wt] + val
            }
        }
    }
    return dp[W]
}
```

Right-to-left traversal is the canonical 0/1 knapsack optimization. If you traverse left to right in the optimized version, you'd be computing unbounded knapsack (each item usable multiple times). Direction of traversal controls item usage count — memorize this distinction.

---

## Problem 2: Partition Equal Subset Sum

**LeetCode 416.** Given an integer array nums, return true if you can partition it into two subsets with equal sum.

This is a direct reduction to 0/1 knapsack: can we find a subset that sums to `totalSum / 2`?

### Define the subproblem

If `totalSum` is odd, return false immediately (can't split evenly).

`dp[w]` = true if a subset of the numbers seen so far sums to exactly w.

### Recurrence

For each number num, update dp from right to left (0/1 knapsack pattern):

```
for w from target down to num:
    dp[w] = dp[w] || dp[w - num]
```

Base case: `dp[0] = true` (empty subset sums to 0).

### Build bottom-up

```go
func canPartition(nums []int) bool {
    total := 0
    for _, n := range nums {
        total += n
    }
    if total%2 != 0 {
        return false
    }
    target := total / 2

    dp := make([]bool, target+1)
    dp[0] = true

    for _, num := range nums {
        for w := target; w >= num; w-- {
            dp[w] = dp[w] || dp[w-num]
        }
    }
    return dp[target]
}
```

O(n × target) time, O(target) space. The early termination trick: if `dp[target]` becomes true during processing, you can return immediately. In Go, add a check after the inner loop:

```go
for _, num := range nums {
    for w := target; w >= num; w-- {
        dp[w] = dp[w] || dp[w-num]
    }
    if dp[target] {
        return true
    }
}
```

The reduction from "equal partition" to "subset sum to target" is a standard move. Anytime you see "partition into two parts with equal [sum/product/XOR]", compute the total, divide by two, and run subset sum.

---

## Problem 3: Target Sum

**LeetCode 494.** Given an integer array nums and an integer target, assign + or - to each number. Return the number of ways to assign signs such that the sum equals target.

This looks like a combinatorics problem but it's knapsack in disguise. The key transformation: let P be the sum of numbers assigned +, N be the sum of numbers assigned -. Then:

```
P + N = total
P - N = target
=> P = (total + target) / 2
```

So the problem reduces to: count subsets with sum exactly `(total + target) / 2`. If `(total + target)` is odd or negative, return 0.

### Define the subproblem

`dp[w]` = number of subsets that sum to exactly w.

### Recurrence

For each num, update right to left (0/1 knapsack, counting variant):

```
dp[w] += dp[w - num]
```

Base case: `dp[0] = 1`.

### Build bottom-up

```go
func findTargetSumWays(nums []int, target int) int {
    total := 0
    for _, n := range nums {
        total += n
    }

    // (total + target) must be even and non-negative
    if (total+target)%2 != 0 || total+target < 0 {
        return 0
    }
    if target < -total || target > total {
        return 0
    }

    subsetSum := (total + target) / 2
    dp := make([]int, subsetSum+1)
    dp[0] = 1

    for _, num := range nums {
        for w := subsetSum; w >= num; w-- {
            dp[w] += dp[w-num]
        }
    }
    return dp[subsetSum]
}
```

O(n × subsetSum) time, O(subsetSum) space.

**The transformation is the hard part.** Once you see P = (total + target) / 2, the rest is boilerplate subset-sum counting. Practice deriving this transformation: it appears in multiple FAANG-level problems.

**Edge cases to handle:**
- `target` is negative: flip the sign. Assign - to the same set as before, and the count is the same.
- `total + target` is odd: impossible. No valid assignment.
- Any `num > subsetSum` in the array: it can't be included in the positive subset — it won't contribute to valid assignments (this is naturally handled since the inner loop condition `w >= num` will skip it).

---

## How to Recognize This Pattern

You're looking at knapsack when:

- You have a collection of items and must decide to include or exclude each one.
- The constraint is a total (weight, sum, count) and you want to maximize, minimize, or count valid selections.
- The problem says "subset," "partition," "assign signs," "select elements."

**0/1 vs unbounded:** if each item can be used at most once, traverse right to left in the space-optimized version. If items can be reused (Coin Change), traverse left to right.

**Max/min vs count:** max/min problems use `max(dp[w], dp[w-wt]+val)`. Count problems use `dp[w] += dp[w-wt]`. The structure is identical; only the operation changes.

---

## Key Takeaway

Knapsack is pick-or-skip applied to a budget. The 2D recurrence is always: `dp[i][w] = skip(dp[i-1][w]) or take(dp[i-1][w-cost] + gain)`. The 1D optimization requires right-to-left traversal per item.

The three reductions to know cold:
1. Partition equal subset → subset sum to total/2.
2. Target sum → subset sum to (total + target)/2.
3. Coin Change → unbounded knapsack, left-to-right traversal.

Master these three reductions and you can handle the majority of interview knapsack variants on sight.

---

*Previous: [Lesson 26: DP on Trees](/post/fundamentals/interview-dp-trees/) · Next: [Lesson 28: Interval DP](/post/fundamentals/interview-dp-intervals/)*
