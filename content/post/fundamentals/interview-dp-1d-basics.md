---
title: "Lesson 21: 1D DP Basics — If you can solve it recursively, you can DP it"
author: "Atharva Pandey"
keywords:
  - dynamic programming
  - climbing stairs
  - house robber
  - decode ways
  - 1D DP
  - memoization
  - tabulation
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 21
date: "2025-06-06T00:00:00.000Z"
---

Every time I bombed a DP problem in a mock interview, the pattern was the same: I stared at the problem, thought "this looks like DP," then froze because I couldn't immediately write the recurrence. The fix wasn't to memorize more recurrences. The fix was to stop trying to think bottom-up first.

Here's the approach that finally clicked: write the recursive solution first. Get it working. Then ask: "which subproblems am I solving multiple times?" Memoize those. Then, if you want to be clean about it, flip it into a bottom-up table. By the time you hit the bottom-up version, the recurrence is already obvious because you derived it from your own recursive code.

1D DP is the best place to drill this, because the state space is literally just one index. No matrices, no exotic states. Just "what do I know when I'm at position i?"

## The Pattern

A 1D DP problem has this shape:

1. You have an array or a sequence of length n.
2. The answer for position i depends only on earlier positions (i-1, i-2, maybe a few more).
3. You fill a dp array from left to right.

The mental model: `dp[i]` = "the answer to the problem if we only had the first i elements."

---

## Problem 1: Climbing Stairs

**LeetCode 70.** You are climbing a staircase with n steps. Each time you can climb 1 or 2 steps. How many distinct ways can you reach the top?

### Define the subproblem

`dp[i]` = number of distinct ways to reach step i.

### Recurrence

To reach step i, you either came from step i-1 (took 1 step) or step i-2 (took 2 steps). So:

```
dp[i] = dp[i-1] + dp[i-2]
```

Base cases: `dp[0] = 1` (one way to stand at the bottom), `dp[1] = 1`.

### Build bottom-up

```go
func climbStairs(n int) int {
    if n <= 1 {
        return 1
    }
    dp := make([]int, n+1)
    dp[0] = 1
    dp[1] = 1
    for i := 2; i <= n; i++ {
        dp[i] = dp[i-1] + dp[i-2]
    }
    return dp[n]
}
```

### Space optimization

We only ever look back two steps. Keep two variables instead of the whole array:

```go
func climbStairsOpt(n int) int {
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

O(1) space. This is Fibonacci with a renamed problem statement — and that's intentional. Climbing stairs is the on-ramp problem because the Fibonacci recurrence is one you likely already know.

---

## Problem 2: House Robber

**LeetCode 198.** You are a professional robber planning to rob houses along a street. Each house has a certain amount of money. You cannot rob two adjacent houses (triggers the alarm). Given an integer array nums, return the maximum amount you can rob tonight.

### Define the subproblem

`dp[i]` = maximum money you can rob from houses 0 through i.

### Recurrence

At house i, you have two choices:
- Skip house i: carry forward `dp[i-1]`
- Rob house i: you couldn't have robbed house i-1, so add `nums[i]` to `dp[i-2]`

```
dp[i] = max(dp[i-1], dp[i-2] + nums[i])
```

Base cases: `dp[0] = nums[0]`, `dp[1] = max(nums[0], nums[1])`.

### Build bottom-up

```go
func rob(nums []int) int {
    n := len(nums)
    if n == 1 {
        return nums[0]
    }
    dp := make([]int, n)
    dp[0] = nums[0]
    dp[1] = max(nums[0], nums[1])
    for i := 2; i < n; i++ {
        dp[i] = max(dp[i-1], dp[i-2]+nums[i])
    }
    return dp[n-1]
}

func max(a, b int) int {
    if a > b {
        return a
    }
    return b
}
```

### Space optimization

Same look-back pattern: only need the previous two values.

```go
func robOpt(nums []int) int {
    n := len(nums)
    if n == 1 {
        return nums[0]
    }
    prev2 := nums[0]
    prev1 := max(nums[0], nums[1])
    for i := 2; i < n; i++ {
        curr := max(prev1, prev2+nums[i])
        prev2 = prev1
        prev1 = curr
    }
    return prev1
}
```

**The insight here:** the recurrence is just formalizing the "pick or skip" decision. When you see a problem where you walk through a sequence and at each element you make a binary choice (take it or leave it), you're probably looking at a House Robber variant.

---

## Problem 3: Decode Ways

**LeetCode 91.** A message is encoded with a mapping: 'A' = 1, 'B' = 2, ..., 'Z' = 26. Given a string of digits, return the number of ways to decode it.

This is the hardest of the three because the transitions are conditional. A digit might be decodable as a single character, as a two-character pair, or both — but only if certain conditions hold (e.g., "06" cannot decode to 'F' because leading zeros are invalid).

### Define the subproblem

`dp[i]` = number of ways to decode the substring s[0..i-1] (first i characters).

### Recurrence

For each position i (1-indexed), consider:

1. **Single digit decode:** take s[i-1] alone. Valid if s[i-1] != '0'. If valid, add `dp[i-1]`.
2. **Two digit decode:** take s[i-2..i-1] together. Valid if i >= 2 and the two digits form a number between 10 and 26. If valid, add `dp[i-2]`.

```
dp[i] = 0
if s[i-1] != '0': dp[i] += dp[i-1]
if i >= 2 and 10 <= int(s[i-2:i]) <= 26: dp[i] += dp[i-2]
```

Base cases: `dp[0] = 1` (empty prefix has one way to decode: do nothing), `dp[1] = 1` if `s[0] != '0'` else 0.

### Build bottom-up

```go
func numDecodings(s string) int {
    n := len(s)
    dp := make([]int, n+1)
    dp[0] = 1
    if s[0] != '0' {
        dp[1] = 1
    }

    for i := 2; i <= n; i++ {
        // single digit
        if s[i-1] != '0' {
            dp[i] += dp[i-1]
        }
        // two digits
        twoDigit := (int(s[i-2]-'0') * 10) + int(s[i-1]-'0')
        if twoDigit >= 10 && twoDigit <= 26 {
            dp[i] += dp[i-2]
        }
    }
    return dp[n]
}
```

### Space optimization

Same two-variable trick:

```go
func numDecodingsOpt(s string) int {
    n := len(s)
    prev2 := 1
    prev1 := 0
    if s[0] != '0' {
        prev1 = 1
    }

    for i := 2; i <= n; i++ {
        curr := 0
        if s[i-1] != '0' {
            curr += prev1
        }
        twoDigit := (int(s[i-2]-'0') * 10) + int(s[i-1]-'0')
        if twoDigit >= 10 && twoDigit <= 26 {
            curr += prev2
        }
        prev2 = prev1
        prev1 = curr
    }
    return prev1
}
```

**The tricky parts:** The off-by-one indexing (dp is 1-indexed, s is 0-indexed) trips people up. Walk through "226" by hand before an interview: dp = [1,1,2,3]. Three ways: "2|2|6", "22|6", "2|26".

---

## How to Recognize This Pattern

- The problem gives you a sequence and asks for a count, maximum, or minimum.
- The answer at position i depends only on a constant number of previous positions.
- You can state a recursive definition naturally: "the best I can do at position i is..."

Watch for these keywords in problem statements: "distinct ways," "maximum sum," "number of ways to decode/parse." They almost always signal 1D DP.

The hardest part is defining `dp[i]` precisely. Spend 30 seconds on that before writing anything else. If your definition is wrong, your recurrence will be wrong, and the whole thing falls apart.

---

## Key Takeaway

1D DP is about filling a single array where each cell depends on a small window of previous cells. The code is almost always a for loop over the array, two base cases, and one line that implements the recurrence.

The sequence for every DP problem: **recursive sketch → identify overlapping subproblems → define dp[i] → write recurrence → code bottom-up → optimize space**.

Don't jump to the table. Start with "what would I do recursively?" and let the recurrence emerge.

---

*Previous: [Lesson 20: Greedy Patterns](/post/fundamentals/interview-greedy/) · Next: [Lesson 22: 1D DP Advanced](/post/fundamentals/interview-dp-1d-advanced/)*
