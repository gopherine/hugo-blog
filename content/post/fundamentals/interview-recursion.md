---
title: "Lesson 7: Recursion — Trust the Recursion, Define the Base Case"
author: "Atharva Pandey"
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 7
keywords:
  - interviews
  - algorithms
  - leetcode
  - FAANG
  - pattern
  - recursion
  - backtracking
  - subsets
  - permutations
tags:
  - fundamentals
  - interviews
date: "2024-09-07T00:00:00.000Z"
---

Recursion trips people up not because the concept is hard, but because they try to trace through it. "If I call `f(3)`, then `f(3)` calls `f(2)`, which calls `f(1)`..." and then they lose the thread. I used to do this. My interviewer at a Series B startup once watched me spend three minutes trying to mentally simulate a recursive call stack for a problem that had a two-line solution once I stopped simulating and started trusting.

The right mental model for recursion in interviews is: define what the function does in terms of a smaller version of itself, define the base case, and trust that it works. Don't simulate. This lesson starts with the simplest recursive problem and builds to the backtracking problems that show up in every FAANG loop — subsets and permutations.

## The Pattern

Recursion has two parts: the base case (when to stop) and the recursive case (how to reduce the problem). The recursive case must always move toward the base case — typically by working on a smaller input (n-1, left half of array, rest of list). If you can define "what does this function return for input of size n, given that it correctly handles size n-1?", you have the recurrence. Write it down, trust it, implement it.

---

## Problem 1: Climbing Stairs

**The problem.** You are climbing a staircase. It takes `n` steps to reach the top. Each time you can either climb 1 or 2 steps. How many distinct ways can you reach the top?

Input: `n = 5` → `8`

**Brute force / naive recursion.** Every step you take 1 or 2 stairs. Enumerate all paths.

```go
func climbStairsBrute(n int) int {
    if n <= 1 {
        return 1
    }
    return climbStairsBrute(n-1) + climbStairsBrute(n-2)
}
```

Time: O(2ⁿ) — exponential due to recomputation. Space: O(n) stack depth. This is correct but unusably slow for large n.

**Building toward optimal.** The recurrence `f(n) = f(n-1) + f(n-2)` is correct, but we recompute `f(n-1)` and `f(n-2)` dozens of times each. Add memoization — cache results the first time you compute them.

```go
func climbStairsMemo(n int) int {
    memo := make([]int, n+1)
    return climb(n, memo)
}

func climb(n int, memo []int) int {
    if n <= 1 {
        return 1
    }
    if memo[n] != 0 {
        return memo[n]
    }
    memo[n] = climb(n-1, memo) + climb(n-2, memo)
    return memo[n]
}
```

Time: O(n). Space: O(n). Each subproblem solved once.

**Iterative (bottom-up DP).** Often the cleanest form — avoid recursion overhead entirely.

```go
func climbStairs(n int) int {
    if n <= 1 {
        return 1
    }
    prev2, prev1 := 1, 1
    for i := 2; i <= n; i++ {
        curr := prev1 + prev2
        prev2 = prev1
        prev1 = curr
    }
    return prev1
}
```

Time: O(n). Space: O(1).

**The Fibonacci connection.** This is Fibonacci in disguise, and recognizing that immediately tells you the optimal solution. The recursion tree branches into two identical sub-trees — classic exponential blowup. Memoization collapses those branches. This problem introduces the DP concept that dominates the next tier of interview problems: overlapping subproblems and optimal substructure. Climbing Stairs is the entry point.

---

## Problem 2: Generate Subsets

**The problem.** Given an integer array `nums` with unique elements, return all possible subsets. The power set. Do not return duplicates.

Input: `nums = [1,2,3]`
Output: `[[], [1], [2], [1,2], [3], [1,3], [2,3], [1,2,3]]`

**Brute force.** Iterate over all 2ⁿ possible bitmasks. For each bitmask, include `nums[i]` if bit `i` is set.

```go
func subsetsBitmask(nums []int) [][]int {
    n := len(nums)
    result := [][]int{}
    for mask := 0; mask < (1 << n); mask++ {
        subset := []int{}
        for i := 0; i < n; i++ {
            if mask&(1<<i) != 0 {
                subset = append(subset, nums[i])
            }
        }
        result = append(result, subset)
    }
    return result
}
```

Time: O(2ⁿ · n). This is actually optimal in output size — there are 2ⁿ subsets and you must produce all of them. But the recursive version is cleaner and is what interviewers want to see.

**Building toward optimal recursion (backtracking).** Think of subset generation as a decision tree. For each element, you make two choices: include it or exclude it. Recurse on the remaining elements after each choice. The base case is when you've considered all elements — whatever is in your current subset is a valid result.

```go
func subsets(nums []int) [][]int {
    result := [][]int{}

    var backtrack func(start int, current []int)
    backtrack = func(start int, current []int) {
        // every state is a valid subset — add a copy
        snapshot := make([]int, len(current))
        copy(snapshot, current)
        result = append(result, snapshot)

        for i := start; i < len(nums); i++ {
            current = append(current, nums[i])   // choose
            backtrack(i+1, current)               // explore
            current = current[:len(current)-1]    // unchoose
        }
    }

    backtrack(0, []int{})
    return result
}
```

Time: O(2ⁿ · n) — 2ⁿ subsets, each taking up to O(n) to copy. Space: O(n) recursion depth.

**The choose/explore/unchoose template.** This is the backtracking skeleton. You choose an element (append), explore what comes after (recurse), then unchoose (remove from slice). The `start` parameter prevents revisiting elements to the left — crucial for avoiding duplicates. `i` starts at `start`, not 0, in the inner loop.

The `copy` before appending to `result` is a Go-specific gotcha. Slices share underlying arrays. If you append `current` directly, all results will point to the same underlying array and get mutated. Always take a snapshot.

---

## Problem 3: Permutations

**The problem.** Given an array `nums` of distinct integers, return all possible permutations.

Input: `[1,2,3]`
Output: `[[1,2,3],[1,3,2],[2,1,3],[2,3,1],[3,1,2],[3,2,1]]`

**The difference from subsets.** Subsets are about inclusion/exclusion — order doesn't matter, and you never revisit a position. Permutations are about ordering — every element appears exactly once, and you need every arrangement. The recursive structure is different.

**Building toward optimal.** At each position in the permutation, any unused element can go there. Track which elements are already used with a boolean slice. When the current permutation has all n elements, record it.

```go
func permute(nums []int) [][]int {
    result := [][]int{}
    used := make([]bool, len(nums))

    var backtrack func(current []int)
    backtrack = func(current []int) {
        if len(current) == len(nums) {
            snapshot := make([]int, len(current))
            copy(snapshot, current)
            result = append(result, snapshot)
            return
        }

        for i := 0; i < len(nums); i++ {
            if used[i] {
                continue // skip already-used elements
            }
            used[i] = true
            current = append(current, nums[i])   // choose
            backtrack(current)                     // explore
            current = current[:len(current)-1]    // unchoose
            used[i] = false
        }
    }

    backtrack([]int{})
    return result
}
```

Time: O(n! · n) — n! permutations, each taking O(n) to copy. Space: O(n) recursion depth plus the `used` array.

**Contrasting with subsets.** In subsets, `i` starts at `start` (passed down) because we want combinations, not permutations — order doesn't matter, and we never go backward. In permutations, `i` always starts at 0 because any unused element can go in any position. The `used` array replaces the `start` index as the mechanism for preventing revisits.

**Alternative: swap-based permutation.** Swap element `start` with each element at index `start..n-1`, recurse on `start+1`, then swap back. This avoids the `used` array and generates permutations in-place:

```go
func permuteSwap(nums []int) [][]int {
    result := [][]int{}

    var backtrack func(start int)
    backtrack = func(start int) {
        if start == len(nums) {
            snapshot := make([]int, len(nums))
            copy(snapshot, nums)
            result = append(result, snapshot)
            return
        }
        for i := start; i < len(nums); i++ {
            nums[start], nums[i] = nums[i], nums[start] // swap in
            backtrack(start + 1)
            nums[start], nums[i] = nums[i], nums[start] // swap back
        }
    }

    backtrack(0)
    return result
}
```

Both approaches are correct. I prefer the `used` array version for clarity, and the swap version when asked for in-place generation.

---

## How to Recognize This Pattern

- "How many ways to..." with small overlapping subproblems → memoized recursion or DP
- "Generate all..." with combinations → backtracking with `start` index
- "Generate all..." with arrangements → backtracking with `used` array
- The recursive case is clearly "f(n) depends on f(n-1) and f(n-2)" → trust the recursion, add memo
- Exponential brute force that computes the same subproblem repeatedly → memoize

When the problem says "all possible" or "every combination/permutation/subset," backtracking is almost always the intended approach. The question becomes: are you choosing (subsets/combinations) or arranging (permutations)?

## Key Takeaway

Recursion in interviews has three forms. Counting/optimization with overlapping subproblems: write the recurrence, memoize, optionally convert to DP. Combination generation (subsets, combinations): backtrack with a `start` index to avoid revisiting. Permutation generation: backtrack with a `used` array to allow any unused element at each position.

The choose/explore/unchoose skeleton is the same in all cases. The difference is in how you control what gets chosen. Master that skeleton and every backtracking problem becomes a variation on a theme.

---

*[← Lesson 6: Linked Lists](/post/fundamentals/interview-linked-lists/) | [Course Index](/series/interview-patterns-cracking-faang-in-the-ai-era/) | [Lesson 8: Sorting →](/post/fundamentals/interview-sorting/)*
