---
title: "Lesson 24: 2D DP Advanced — String comparison is always 2D DP"
author: "Atharva Pandey"
keywords:
  - dynamic programming
  - longest common subsequence
  - regular expression matching
  - wildcard matching
  - 2D DP advanced
  - string DP
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 24
date: "2025-07-24T00:00:00.000Z"
---

Edit Distance from the previous lesson is the template for a whole family of problems. Any time you're comparing two strings — finding common parts, matching patterns, counting transformations — you're drawing a 2D table where one string indexes the rows and the other indexes the columns.

The structure is always the same: `dp[i][j]` answers a question about the first i characters of one string and the first j characters of the other. The recurrence depends on whether the current characters match and what operations are allowed.

The problems in this lesson get progressively harder, with Regular Expression and Wildcard Matching adding the complexity of special characters that can match multiple things. The key is not to memorize those recurrences — it's to reason through "what can the current pattern character do?" and translate each possibility into a transition.

## The Pattern

All three problems follow this 2D DP shape:

- Rows index into string/pattern s1 (0 to m).
- Columns index into string/pattern s2 (0 to n).
- `dp[i][j]` is defined over the first i characters of s1 and first j characters of s2.
- Base cases handle empty prefixes (i=0 or j=0).
- The main recurrence handles character matches and special cases.

---

## Problem 1: Longest Common Subsequence

**LeetCode 1143.** Given two strings text1 and text2, return the length of their longest common subsequence. A subsequence is derived by deleting some characters without changing the order.

### Define the subproblem

`dp[i][j]` = length of LCS of text1[0..i-1] and text2[0..j-1].

### Recurrence

If the current characters match (`text1[i-1] == text2[j-1]`), include them:

```
dp[i][j] = dp[i-1][j-1] + 1
```

Otherwise, the LCS can't include both current characters, so drop one from either string and take the best:

```
dp[i][j] = max(dp[i-1][j], dp[i][j-1])
```

Base cases: `dp[0][j] = 0` and `dp[i][0] = 0` (LCS with an empty string is 0).

### Build bottom-up

```go
func longestCommonSubsequence(text1 string, text2 string) int {
    m, n := len(text1), len(text2)
    dp := make([][]int, m+1)
    for i := range dp {
        dp[i] = make([]int, n+1)
    }

    for i := 1; i <= m; i++ {
        for j := 1; j <= n; j++ {
            if text1[i-1] == text2[j-1] {
                dp[i][j] = dp[i-1][j-1] + 1
            } else {
                if dp[i-1][j] > dp[i][j-1] {
                    dp[i][j] = dp[i-1][j]
                } else {
                    dp[i][j] = dp[i][j-1]
                }
            }
        }
    }
    return dp[m][n]
}
```

### Space optimization

Same rolling-row trick as Edit Distance. Save the diagonal before overwriting:

```go
func longestCommonSubsequenceOpt(text1 string, text2 string) int {
    m, n := len(text1), len(text2)
    dp := make([]int, n+1)

    for i := 1; i <= m; i++ {
        prev := 0 // dp[i-1][0] = 0
        for j := 1; j <= n; j++ {
            temp := dp[j]
            if text1[i-1] == text2[j-1] {
                dp[j] = prev + 1
            } else {
                if dp[j] < dp[j-1] {
                    dp[j] = dp[j-1]
                }
            }
            prev = temp
        }
    }
    return dp[n]
}
```

LCS is the foundation. Edit Distance, Shortest Common Supersequence, and Minimum Insertions to make a string palindrome all reduce to or build on LCS.

---

## Problem 2: Regular Expression Matching

**LeetCode 10.** Implement regular expression matching with support for `.` and `*`. `.` matches any single character. `*` matches zero or more of the preceding element.

This is the hardest standard DP problem at FAANG interviews. The `*` makes it non-trivial because it can match zero occurrences — meaning the pattern `a*` can be completely removed.

### Define the subproblem

`dp[i][j]` = true if s[0..i-1] matches p[0..j-1].

### Recurrence

Case 1: Characters match directly. `p[j-1]` is a letter or `.`:

```
if p[j-1] == '.' || p[j-1] == s[i-1]:
    dp[i][j] = dp[i-1][j-1]
```

Case 2: `p[j-1]` is `*`. The `*` applies to `p[j-2]` (the character before it). Two sub-cases:

- **Zero occurrences** of `p[j-2]`: treat `p[j-2..j-1]` as empty. `dp[i][j] = dp[i][j-2]`.
- **One or more occurrences** of `p[j-2]`: valid only if `p[j-2]` matches `s[i-1]` (either same char or `.`). If so, `dp[i][j] = dp[i-1][j]` (we consumed one character from s, but p's `x*` can still match more).

```
if p[j-1] == '*':
    // zero occurrences of p[j-2]
    dp[i][j] = dp[i][j-2]
    // one or more occurrences
    if p[j-2] == '.' || p[j-2] == s[i-1]:
        dp[i][j] = dp[i][j] || dp[i-1][j]
```

Base cases:
- `dp[0][0] = true`: empty s matches empty p.
- `dp[0][j]`: empty s can match patterns like `a*`, `a*b*`, etc. Only true if p ends with `*` pairs.

```go
func isMatch(s string, p string) bool {
    m, n := len(s), len(p)
    dp := make([][]bool, m+1)
    for i := range dp {
        dp[i] = make([]bool, n+1)
    }
    dp[0][0] = true

    // empty string can match patterns like "a*", "a*b*c*"
    for j := 2; j <= n; j++ {
        if p[j-1] == '*' {
            dp[0][j] = dp[0][j-2]
        }
    }

    for i := 1; i <= m; i++ {
        for j := 1; j <= n; j++ {
            if p[j-1] == '*' {
                // zero occurrences
                dp[i][j] = dp[i][j-2]
                // one or more
                if p[j-2] == '.' || p[j-2] == s[i-1] {
                    dp[i][j] = dp[i][j] || dp[i-1][j]
                }
            } else if p[j-1] == '.' || p[j-1] == s[i-1] {
                dp[i][j] = dp[i-1][j-1]
            }
        }
    }
    return dp[m][n]
}
```

The insight that unlocks this: when you see `*`, always reason about "zero occurrences" first. That's `dp[i][j-2]`. Then ask "can it match one more character?" That's the `dp[i-1][j]` transition — we stayed at the same pattern position j (because `x*` can keep matching) but consumed one character from s.

---

## Problem 3: Wildcard Matching

**LeetCode 44.** Implement wildcard pattern matching. `?` matches any single character. `*` matches any sequence of characters (including empty).

Wildcard is simpler than Regex because `*` here doesn't depend on the preceding character. It always means "zero or more of anything."

### Define the subproblem

`dp[i][j]` = true if s[0..i-1] matches p[0..j-1].

### Recurrence

Case 1: Characters match. `p[j-1]` is a letter that equals `s[i-1]`, or `p[j-1]` is `?`:

```
dp[i][j] = dp[i-1][j-1]
```

Case 2: `p[j-1]` is `*`:

- **Zero characters**: `dp[i][j] = dp[i][j-1]` (skip the `*` in pattern).
- **One or more characters**: `dp[i][j] = dp[i-1][j]` (consume one character from s, `*` remains active).

```
if p[j-1] == '*':
    dp[i][j] = dp[i][j-1] || dp[i-1][j]
```

Base cases:
- `dp[0][0] = true`.
- `dp[0][j] = true` if all of p[0..j-1] are `*` characters. Once a non-`*` is found, everything to the right is false.

```go
func isMatchWildcard(s string, p string) bool {
    m, n := len(s), len(p)
    dp := make([][]bool, m+1)
    for i := range dp {
        dp[i] = make([]bool, n+1)
    }
    dp[0][0] = true

    for j := 1; j <= n; j++ {
        if p[j-1] == '*' {
            dp[0][j] = dp[0][j-1]
        }
    }

    for i := 1; i <= m; i++ {
        for j := 1; j <= n; j++ {
            if p[j-1] == '*' {
                dp[i][j] = dp[i][j-1] || dp[i-1][j]
            } else if p[j-1] == '?' || p[j-1] == s[i-1] {
                dp[i][j] = dp[i-1][j-1]
            }
        }
    }
    return dp[m][n]
}
```

### Comparing Regex vs Wildcard `*`

The key difference is in the `*` semantics:

- **Regex `*`**: depends on the character before it (`p[j-2]`). Zero occurrences → `dp[i][j-2]`. More occurrences → `dp[i-1][j]` (only if `p[j-2]` matches `s[i-1]`).
- **Wildcard `*`**: matches anything. Zero occurrences → `dp[i][j-1]`. More occurrences → `dp[i-1][j]` (always valid).

Both use the "more occurrences" transition as `dp[i-1][j]`, which is the elegant part: "I consumed one more character from s, but the `*` is still alive."

---

## How to Recognize This Pattern

- Two strings are involved, and you're asking about matching, common parts, or transformation.
- `dp[i][j]` is about the first i chars of one string and the first j chars of the other.
- Transitions depend on whether s[i-1] and p[j-1] match.
- Special characters in the pattern create branching in the recurrence.

String comparison problems almost never need more than 2D DP. When you see two strings and any of these keywords — "match," "common subsequence," "edit," "convert," "partition" — start drawing a 2D table.

---

## Key Takeaway

Regex and Wildcard Matching look intimidating but follow the same 2D DP template as LCS and Edit Distance. The only difference is reasoning carefully about what each special character can do.

For `*` in any pattern matching problem: always enumerate the cases (zero matches, one match, more than one match). The "more than one" case almost always resolves to `dp[i-1][j]`.

Draw the table for a small example before writing code. A 4×5 grid filled by hand will make the recurrence obvious.

---

*Previous: [Lesson 23: 2D DP Basics](/post/fundamentals/interview-dp-2d-basics/) · Next: [Lesson 25: DP on Strings](/post/fundamentals/interview-dp-strings/)*
