---
title: "Lesson 8: Dynamic Programming Intuition — Memoization, not memorization"
author: "Atharva Pandey"
keywords:
  - dynamic programming
  - memoization
  - tabulation
  - overlapping subproblems
  - optimal substructure
  - production engineering
tags:
  - fundamentals
  - algorithms
series: "Algorithms That Matter in Production"
lesson: 8
date: "2024-08-02T00:00:00.000Z"
---

Dynamic programming has an intimidating reputation. The name sounds academic, the problems in textbooks often involve sequences and matrices, and the "aha moment" is notoriously hard to force. I spent a long time treating DP as an interview preparation topic rather than a tool I would actually use. That changed when I built a pricing engine and realized I had been reimplementing DP badly — without knowing it — by computing the same values over and over in nested function calls.

The insight is simple: if your function is called with the same inputs multiple times and always returns the same result, cache the results. That is memoization. DP is just the principled application of this idea to problems with overlapping subproblems.

## How It Works

Dynamic programming applies when a problem has two properties:

1. **Optimal substructure**: the optimal solution is built from optimal solutions to subproblems
2. **Overlapping subproblems**: the same subproblems are solved multiple times in a naive recursive approach

The two implementation styles:

**Top-down (memoization)**: write the recursive solution, add a cache. Natural to write, easier to reason about correctness.

**Bottom-up (tabulation)**: fill a table iteratively from base cases up to the full problem. Often faster (no recursion overhead), better memory behavior.

```go
// Classic example: Fibonacci to illustrate the caching pattern
// Naive recursive: O(2^n) due to recomputation
func fibNaive(n int) int {
    if n <= 1 {
        return n
    }
    return fibNaive(n-1) + fibNaive(n-2)
}

// Top-down with memoization: O(n) time, O(n) space
func fibMemo(n int, memo map[int]int) int {
    if n <= 1 {
        return n
    }
    if v, ok := memo[n]; ok {
        return v
    }
    result := fibMemo(n-1, memo) + fibMemo(n-2, memo)
    memo[n] = result
    return result
}

// Bottom-up tabulation: O(n) time, O(1) space
func fibDP(n int) int {
    if n <= 1 {
        return n
    }
    prev2, prev1 := 0, 1
    for i := 2; i <= n; i++ {
        prev2, prev1 = prev1, prev2+prev1
    }
    return prev1
}
```

The Fibonacci example is toy. The real value is recognizing when your production code is doing the same unnecessary recomputation.

## When You Need It

Signs that DP applies:

- A recursive function that could be called with the same arguments from multiple paths
- "What is the minimum/maximum cost to achieve X?" questions
- "How many ways can X be done?" questions
- Any problem where you are choosing among options and choices affect future options

In production systems, DP tends to appear in:

- **Pricing and discount engines**: apply the best combination of discounts to an order
- **Scheduling**: assign jobs to time slots minimizing cost
- **Text processing**: spell correction, fuzzy matching, diff computation
- **Resource allocation**: fit tasks into capacity optimally
- **Route planning with constraints**: shortest path with additional conditions

```go
// Edit distance (Levenshtein) — used in spell check, fuzzy search, diff tools
// Answers: minimum single-character edits (insert, delete, replace) to transform s into t
func editDistance(s, t string) int {
    m, n := len(s), len(t)
    // dp[i][j] = edit distance between s[:i] and t[:j]
    dp := make([][]int, m+1)
    for i := range dp {
        dp[i] = make([]int, n+1)
    }
    // Base cases: transform empty string to prefix of t (all inserts)
    for j := 0; j <= n; j++ {
        dp[0][j] = j
    }
    // Base cases: transform prefix of s to empty string (all deletes)
    for i := 0; i <= m; i++ {
        dp[i][0] = i
    }
    for i := 1; i <= m; i++ {
        for j := 1; j <= n; j++ {
            if s[i-1] == t[j-1] {
                dp[i][j] = dp[i-1][j-1] // no edit needed
            } else {
                dp[i][j] = 1 + min(
                    dp[i-1][j],   // delete from s
                    dp[i][j-1],   // insert into s
                    dp[i-1][j-1], // replace in s
                )
            }
        }
    }
    return dp[m][n]
}

func min(a, b, c int) int {
    if a < b {
        if a < c {
            return a
        }
        return c
    }
    if b < c {
        return b
    }
    return c
}
```

## Production Example

A tiered discount engine needed to apply the best combination of discount rules to a shopping cart. Rules had types: percentage off, fixed amount off, and "buy N get M free." They could not always all be combined — some were mutually exclusive. The goal was to maximize customer savings while respecting constraints.

This is the knapsack problem — a classic DP application.

```go
type DiscountRule struct {
    ID          string
    Description string
    Savings     float64
    ExcludesWith []string // rule IDs this cannot combine with
}

// Maximum savings from non-conflicting rules
// This is a variant of the weighted independent set problem
func maxSavings(rules []DiscountRule) float64 {
    n := len(rules)
    // Build conflict map
    conflicts := make(map[string]map[string]bool)
    for _, r := range rules {
        conflicts[r.ID] = make(map[string]bool)
        for _, excl := range r.ExcludesWith {
            conflicts[r.ID][excl] = true
        }
    }

    // dp[mask] = max savings achievable with the subset of rules indicated by mask
    dp := make([]float64, 1<<n)
    chosen := make([][]int, 1<<n) // track which rules were chosen

    for mask := 1; mask < (1 << n); mask++ {
        // Try adding each rule to smaller subsets
        for i := 0; i < n; i++ {
            if mask&(1<<i) == 0 {
                continue
            }
            subMask := mask ^ (1 << i)
            // Check if rule i conflicts with any rule already in subMask
            valid := true
            for j := 0; j < n; j++ {
                if subMask&(1<<j) != 0 && conflicts[rules[i].ID][rules[j].ID] {
                    valid = false
                    break
                }
            }
            if valid {
                candidate := dp[subMask] + rules[i].Savings
                if candidate > dp[mask] {
                    dp[mask] = candidate
                    chosen[mask] = append(chosen[subMask], i)
                }
            }
        }
    }

    best := 0.0
    for mask := 1; mask < (1 << n); mask++ {
        if dp[mask] > best {
            best = dp[mask]
        }
    }
    return best
}
```

For small rule counts (up to ~20 rules), the bitmask DP approach is clean and fast. For larger rule sets, a greedy approximation or integer programming solver is more appropriate — the exponential space becomes impractical past around 25 rules.

The edit distance function powered the "did you mean?" search suggestion feature. Given a misspelled product query, we computed edit distance against the top 10,000 product names (precomputed offline) and returned the closest matches. A single edit distance computation for two strings of length ~30 takes microseconds. Over 10,000 products, this is under 10ms.

```go
// Fuzzy product search using edit distance
func findClosestProducts(query string, catalog []string, maxDist int) []string {
    var matches []string
    for _, name := range catalog {
        if editDistance(query, name) <= maxDist {
            matches = append(matches, name)
        }
    }
    return matches
}
```

## The Tradeoffs

DP trades memory for time. A tabulation table for edit distance between strings of length m and n requires O(m*n) space. For very long strings (documents), this is impractical. Space-optimized variants exist — edit distance can be computed in O(min(m,n)) space by only keeping two rows of the table at a time.

The bitmask approach for rule combinations is O(2^n * n) — fine for n < 20, impractical for n > 30. Recognize this scaling cliff before applying it.

Top-down memoization risks stack overflow for very deep recursion. Go's goroutine stacks grow dynamically (starting at 8KB, growing to 1GB), so this is less of a concern than in other languages, but deeply recursive DP over graphs with thousands of nodes can still be slow due to function call overhead.

Also: DP requires pure functions. The cached result is only valid if the same inputs always produce the same output. If your "subproblem" depends on mutable global state, the memoization is incorrect.

## Key Takeaway

Dynamic programming is not a mystery technique. It is caching applied to recursive problems with overlapping subproblems. The skill is recognizing when your problem has this structure: "I am computing the same thing from multiple places, and the result depends only on the inputs." When you see that, add a cache. Then consider whether bottom-up tabulation gives you better memory characteristics. The pattern shows up in text processing, pricing engines, scheduling, and any optimization problem where you must choose among options with interdependencies.

---

← [Lesson 7: Shortest Path](/post/fundamentals/algo-shortest-path/) | [Course Index](/post/fundamentals/) | Next: [Lesson 9: Greedy Algorithms](/post/fundamentals/algo-greedy/) →
