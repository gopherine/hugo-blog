---
title: "Lesson 25: DP on Strings — Palindromes and partitions"
author: "Atharva Pandey"
keywords:
  - dynamic programming
  - palindromic substrings
  - palindrome partitioning
  - distinct subsequences
  - string DP
  - interval DP
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 25
date: "2025-08-09T00:00:00.000Z"
---

String DP has its own flavor that's distinct from the two-string comparison problems of the last lesson. Here, the state is typically a single string, but the subproblem is about a *range* within that string: "what's the answer for the substring s[i..j]?" That's interval DP applied to strings, and palindromes are its most natural setting.

The tricky part with palindrome problems is that you can approach them from the outside in (is s[i..j] a palindrome?) or the inside out (expand from a center). DP works from the outside in: small intervals first, then build up to larger ones. The expansion approach is often faster in practice but DP is more general and extends to partition problems naturally.

Distinct Subsequences rounds out the lesson as a counting problem — it looks like a 2D DP but operates on a single input string matched against a pattern. The mental model shift is subtle but worth understanding.

## The Pattern

String range DP follows this shape:

1. `dp[i][j]` answers a question about substring s[i..j].
2. Fill by increasing length: first answer for all substrings of length 1, then length 2, and so on.
3. The recurrence for `dp[i][j]` uses `dp[i+1][j]`, `dp[i][j-1]`, or `dp[i+1][j-1]` — shrinking the interval.

---

## Problem 1: Palindromic Substrings

**LeetCode 647.** Given a string s, return the number of palindromic substrings.

### Define the subproblem

`dp[i][j]` = true if s[i..j] is a palindrome.

### Recurrence

A string s[i..j] is a palindrome if:
- The outer characters match: `s[i] == s[j]`, AND
- The inner substring is also a palindrome: `dp[i+1][j-1]` is true (or the inner substring has length 0 or 1).

```
dp[i][j] = (s[i] == s[j]) && (j - i <= 2 || dp[i+1][j-1])
```

Base cases are embedded: substrings of length 1 are always palindromes; substrings of length 2 are palindromes iff both characters match.

### Build bottom-up

Fill by increasing length:

```go
func countSubstrings(s string) int {
    n := len(s)
    dp := make([][]bool, n)
    for i := range dp {
        dp[i] = make([]bool, n)
    }

    count := 0
    // Fill by length from 1 to n
    for length := 1; length <= n; length++ {
        for i := 0; i+length-1 < n; i++ {
            j := i + length - 1
            if s[i] == s[j] && (length <= 2 || dp[i+1][j-1]) {
                dp[i][j] = true
                count++
            }
        }
    }
    return count
}
```

O(n²) time and space. This table also answers "is s[i..j] a palindrome?" in O(1) after preprocessing, which is useful when palindrome checks are needed as a subroutine.

**Note:** The expand-around-center approach solves counting palindromes in O(n²) time and O(1) space, so in practice that's preferred. But the DP table is the foundation for the next problem.

---

## Problem 2: Palindrome Partitioning II

**LeetCode 132.** Given a string s, partition it such that every substring of the partition is a palindrome. Return the minimum number of cuts needed.

This combines the palindrome check from Problem 1 as a precomputed lookup with a 1D DP on cut positions.

### Define the subproblem

First, precompute `isPalin[i][j]` using the table from Problem 1.

Then: `cuts[i]` = minimum number of cuts for s[0..i].

### Recurrence

For each position i, scan all possible last palindromic segments ending at i. For each j <= i where s[j..i] is a palindrome:

- If j == 0: no cut needed — the whole prefix s[0..i] is a palindrome. `cuts[i] = 0`.
- Otherwise: make a cut before position j. `cuts[i] = min(cuts[i], cuts[j-1] + 1)`.

```
cuts[i] = min over all j <= i where isPalin[j][i]:
    if j == 0: 0
    else: cuts[j-1] + 1
```

### Build bottom-up

```go
func minCut(s string) int {
    n := len(s)

    // Precompute palindrome table
    isPalin := make([][]bool, n)
    for i := range isPalin {
        isPalin[i] = make([]bool, n)
    }
    for length := 1; length <= n; length++ {
        for i := 0; i+length-1 < n; i++ {
            j := i + length - 1
            if s[i] == s[j] && (length <= 2 || isPalin[i+1][j-1]) {
                isPalin[i][j] = true
            }
        }
    }

    // DP for minimum cuts
    cuts := make([]int, n)
    for i := 0; i < n; i++ {
        if isPalin[0][i] {
            cuts[i] = 0
            continue
        }
        cuts[i] = i // worst case: i cuts (cut after each character)
        for j := 1; j <= i; j++ {
            if isPalin[j][i] {
                if cuts[j-1]+1 < cuts[i] {
                    cuts[i] = cuts[j-1] + 1
                }
            }
        }
    }
    return cuts[n-1]
}
```

O(n²) time and space. The two-phase approach (precompute palindromes, then DP on cuts) is the standard interview answer. Trying to interleave both in one pass makes the code much harder to reason about.

---

## Problem 3: Distinct Subsequences

**LeetCode 115.** Given two strings s and t, return the number of distinct subsequences of s that equal t.

This is a counting problem. How many ways can you select characters from s (in order) to spell out t?

### Define the subproblem

`dp[i][j]` = number of distinct subsequences of s[0..i-1] that equal t[0..j-1].

### Recurrence

For each (i, j):

- **Don't use s[i-1]** to match t[j-1]: the count is at least `dp[i-1][j]` (same t prefix, one shorter s prefix).
- **Use s[i-1]** to match t[j-1]: only valid if `s[i-1] == t[j-1]`. Contributes `dp[i-1][j-1]` additional ways.

```
dp[i][j] = dp[i-1][j]
if s[i-1] == t[j-1]:
    dp[i][j] += dp[i-1][j-1]
```

Base cases:
- `dp[i][0] = 1` for all i: one way to match the empty string t from any prefix of s (by selecting nothing).
- `dp[0][j] = 0` for j > 0: no way to match a non-empty t from an empty s.

### Build bottom-up

```go
func numDistinct(s string, t string) int {
    m, n := len(s), len(t)
    dp := make([][]int, m+1)
    for i := range dp {
        dp[i] = make([]int, n+1)
        dp[i][0] = 1
    }

    for i := 1; i <= m; i++ {
        for j := 1; j <= n; j++ {
            dp[i][j] = dp[i-1][j] // skip s[i-1]
            if s[i-1] == t[j-1] {
                dp[i][j] += dp[i-1][j-1] // use s[i-1]
            }
        }
    }
    return dp[m][n]
}
```

### Space optimization

Each row i depends only on row i-1. Process j from right to left to use a single array in place:

```go
func numDistinctOpt(s string, t string) int {
    n := len(t)
    dp := make([]int, n+1)
    dp[0] = 1

    for _, sc := range s {
        // traverse right to left to avoid using updated values
        for j := n; j >= 1; j-- {
            if sc == rune(t[j-1]) {
                dp[j] += dp[j-1]
            }
        }
    }
    return dp[n]
}
```

The right-to-left traversal ensures that when we update `dp[j]`, `dp[j-1]` still holds the value from the previous row (before any s[i] was considered). This is the same trick used in 0/1 knapsack.

---

## How to Recognize This Pattern

- The problem is about a single string and asks about palindromes, partitions, or subsequence counts.
- The answer for a substring s[i..j] is built from answers for smaller substrings.
- Fill order matters: smaller substrings before larger ones (for interval DP), or left-to-right with a palindrome lookup (for partitioning).

The palindrome table `isPalin[i][j]` is a subroutine that appears in many problems. Build it once and use it as a lookup.

---

## Key Takeaway

String range DP works on intervals within a single string. Build the table by increasing interval length. Palindrome problems use the "shrink from outside" recurrence. Partition problems use the palindrome table as a subroutine to a 1D cut DP.

Distinct Subsequences looks like 2D DP on two strings but it's really a counting problem where you choose whether to "use" each character in s. The right-to-left optimization collapses it to 1D.

---

*Previous: [Lesson 24: 2D DP Advanced](/post/fundamentals/interview-dp-2d-advanced/) · Next: [Lesson 26: DP on Trees](/post/fundamentals/interview-dp-trees/)*
