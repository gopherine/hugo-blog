---
title: "Lesson 22: 1D DP Advanced — When the state space gets interesting"
author: "Atharva Pandey"
keywords:
  - dynamic programming
  - word break
  - longest increasing subsequence
  - coin change
  - 1D DP advanced
  - unbounded knapsack
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 22
date: "2025-06-27T00:00:00.000Z"
---

Climbing Stairs and House Robber have a comfortable property: `dp[i]` depends on just the previous one or two positions. Each step you take looks back a fixed distance. These are the training wheels problems.

Advanced 1D DP breaks that comfort. Word Break needs to look back up to the entire string. LIS needs to look back at every prior element. Coin Change loops over all denominations at every position. The look-back window is variable, sometimes unbounded. The dp array is still 1D — but you need a loop inside a loop.

This is where most people stall. They know the structure should be DP, they set up `dp[i]`, but they can't figure out how to populate it. The answer is almost always: you need an inner loop that iterates over what can transition into position i.

## The Pattern

Advanced 1D DP follows the same formula:

1. Define `dp[i]` clearly — what problem is solved when I'm at index i?
2. For each i, iterate over all j < i (or all choices) that could have led to i.
3. Apply the recurrence for each valid transition.

The outer loop fills the table. The inner loop considers all possible last decisions.

---

## Problem 1: Word Break

**LeetCode 139.** Given a string s and a dictionary of strings wordDict, return true if s can be segmented into a space-separated sequence of one or more dictionary words.

### Define the subproblem

`dp[i]` = true if the substring s[0..i-1] (first i characters) can be segmented using words from the dictionary.

### Recurrence

For each position i, check all possible last words. If there exists a j < i such that:
- `dp[j]` is true (the prefix of length j can be segmented), and
- `s[j..i-1]` is in the dictionary,

then `dp[i]` is true.

```
for j from 0 to i-1:
    if dp[j] == true and s[j:i] in wordDict:
        dp[i] = true
        break
```

Base case: `dp[0] = true` (empty string is always "segmented").

### Build bottom-up

```go
func wordBreak(s string, wordDict []string) bool {
    wordSet := make(map[string]bool)
    for _, w := range wordDict {
        wordSet[w] = true
    }

    n := len(s)
    dp := make([]bool, n+1)
    dp[0] = true

    for i := 1; i <= n; i++ {
        for j := 0; j < i; j++ {
            if dp[j] && wordSet[s[j:i]] {
                dp[i] = true
                break
            }
        }
    }
    return dp[n]
}
```

O(n²) time (dominated by the string slicing — each slice is O(n), so worst case is O(n³) unless you account for the dictionary max word length). In interviews, the O(n²) framing is usually accepted.

**Optimization:** if you know the maximum word length maxLen in the dictionary, restrict j to max(0, i-maxLen). This prunes useless inner iterations:

```go
func wordBreakOpt(s string, wordDict []string) bool {
    wordSet := make(map[string]bool)
    maxLen := 0
    for _, w := range wordDict {
        wordSet[w] = true
        if len(w) > maxLen {
            maxLen = len(w)
        }
    }

    n := len(s)
    dp := make([]bool, n+1)
    dp[0] = true

    for i := 1; i <= n; i++ {
        start := i - maxLen
        if start < 0 {
            start = 0
        }
        for j := start; j < i; j++ {
            if dp[j] && wordSet[s[j:i]] {
                dp[i] = true
                break
            }
        }
    }
    return dp[n]
}
```

---

## Problem 2: Longest Increasing Subsequence

**LeetCode 300.** Given an integer array nums, return the length of the longest strictly increasing subsequence.

### Define the subproblem

`dp[i]` = length of the longest increasing subsequence ending at index i (nums[i] is the last element).

The "ending at index i" phrasing is critical. It forces every valid subsequence to include nums[i], which gives us something concrete to extend from prior positions.

### Recurrence

For each i, scan all j < i. If nums[j] < nums[i], we can extend the LIS ending at j by appending nums[i]:

```
dp[i] = max(dp[i], dp[j] + 1) for all j < i where nums[j] < nums[i]
```

Base case: `dp[i] = 1` for all i (every single element is a subsequence of length 1).

Answer: `max(dp[0], dp[1], ..., dp[n-1])`.

### Build bottom-up

```go
func lengthOfLIS(nums []int) int {
    n := len(nums)
    dp := make([]int, n)
    for i := range dp {
        dp[i] = 1
    }

    best := 1
    for i := 1; i < n; i++ {
        for j := 0; j < i; j++ {
            if nums[j] < nums[i] {
                if dp[j]+1 > dp[i] {
                    dp[i] = dp[j] + 1
                }
            }
        }
        if dp[i] > best {
            best = dp[i]
        }
    }
    return best
}
```

O(n²) time, O(n) space.

### O(n log n) approach

For completeness — in a real interview, the O(n²) solution is often enough, but the follow-up is always "can you do better?" The trick is to maintain a "patience sorting" array tails where tails[k] is the smallest tail element of all increasing subsequences of length k+1:

```go
func lengthOfLISFast(nums []int) int {
    tails := []int{}
    for _, num := range nums {
        lo, hi := 0, len(tails)
        for lo < hi {
            mid := (lo + hi) / 2
            if tails[mid] < num {
                lo = mid + 1
            } else {
                hi = mid
            }
        }
        if lo == len(tails) {
            tails = append(tails, num)
        } else {
            tails[lo] = num
        }
    }
    return len(tails)
}
```

The binary search replaces the inner loop. tails is not the actual LIS — it's a virtual structure that tells us the LIS length. Don't try to reconstruct the subsequence from tails directly.

---

## Problem 3: Coin Change

**LeetCode 322.** You are given coins of different denominations and a total amount. Return the minimum number of coins needed to make up that amount. Return -1 if it is not possible.

This is the classic unbounded knapsack. Each coin can be used unlimited times. The state is the remaining amount, not an index into the coins array.

### Define the subproblem

`dp[a]` = minimum number of coins to make amount a.

### Recurrence

For each amount a, try every coin denomination c. If c <= a and we can make amount a-c (i.e., dp[a-c] != infinity), then:

```
dp[a] = min(dp[a], dp[a-c] + 1) for each coin c where c <= a
```

Base case: `dp[0] = 0`. Initialize all other `dp[a] = infinity` (or amount+1 as a sentinel).

### Build bottom-up

```go
func coinChange(coins []int, amount int) int {
    const inf = 1<<31 - 1
    dp := make([]int, amount+1)
    for i := 1; i <= amount; i++ {
        dp[i] = inf
    }
    dp[0] = 0

    for a := 1; a <= amount; a++ {
        for _, c := range coins {
            if c <= a && dp[a-c] != inf {
                if dp[a-c]+1 < dp[a] {
                    dp[a] = dp[a-c] + 1
                }
            }
        }
    }

    if dp[amount] == inf {
        return -1
    }
    return dp[amount]
}
```

O(amount × len(coins)) time, O(amount) space. No further space optimization is meaningful here — the entire dp array is the state.

**Why outer loop is amount, inner is coins:** We're building up solutions for each target amount from 0 to amount. For each amount, we ask "which coin can I use last?" This is the standard approach for unbounded knapsack. (If the coin could only be used once, the loops would be structured differently — we'd iterate coins in the outer loop to enforce "used at most once." More on that in the Knapsack Patterns lesson.)

---

## How to Recognize This Pattern

You're looking at advanced 1D DP when:

- The problem is still over a 1D sequence or a single integer, but `dp[i]` depends on **multiple earlier positions**, not just i-1 or i-2.
- The problem says "segmentation," "partition," "minimum coins," "longest subsequence" — all requiring an inner loop over choices.
- You find yourself writing `for i ... { for j < i ... }` or `for amount ... { for each coin ... }`.

The dead giveaway is that you need to iterate backward through all previous states to populate the current one.

---

## Key Takeaway

Advanced 1D DP is still 1D — but the transition requires an inner loop. Word Break looks back through all split points. LIS looks back at all smaller elements. Coin Change tries all denominations.

The framework is identical to basic 1D DP: define `dp[i]`, write the recurrence, fill left to right. The only difference is that "left to right" requires an extra loop inside.

When you see a problem where the "last choice" could be any of many options, that's your cue to write an inner loop.

---

*Previous: [Lesson 21: 1D DP Basics](/post/fundamentals/interview-dp-1d-basics/) · Next: [Lesson 23: 2D DP Basics](/post/fundamentals/interview-dp-2d-basics/)*
