---
title: "Lesson 23: 2D DP Basics — Two dimensions, one table"
author: "Atharva Pandey"
keywords:
  - dynamic programming
  - unique paths
  - minimum path sum
  - edit distance
  - 2D DP
  - grid DP
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 23
date: "2025-07-13T00:00:00.000Z"
---

1D DP was a single row — you filled it left to right and you were done. 2D DP extends that to a full table. You fill it row by row, and each cell depends on cells above it, to its left, or diagonally above-left. The shape of that dependency is the shape of the problem.

I find 2D DP more intuitive than 1D once I got comfortable with the table visualization. Unique Paths is a perfect first problem because you can literally draw the grid and fill it in by hand in 30 seconds. Edit Distance is the crown jewel — once you understand why the three transitions correspond to the three edit operations, you'll never forget the recurrence.

## The Pattern

A 2D DP problem has this shape:

1. The state requires two indices: typically (i, j) representing a position in a grid, or (i, j) representing prefixes of two strings.
2. `dp[i][j]` depends on `dp[i-1][j]`, `dp[i][j-1]`, `dp[i-1][j-1]`, or some combination.
3. You fill the table row by row, left to right.

The mental model: `dp[i][j]` = "the answer to the problem restricted to the first i rows and j columns (or first i characters of string A and first j characters of string B)."

---

## Problem 1: Unique Paths

**LeetCode 62.** A robot starts at the top-left corner of an m × n grid and must reach the bottom-right corner. The robot can only move right or down. How many distinct paths are there?

### Define the subproblem

`dp[i][j]` = number of distinct paths to reach cell (i, j).

### Recurrence

To reach cell (i, j), the robot either came from above (i-1, j) or from the left (i, j-1):

```
dp[i][j] = dp[i-1][j] + dp[i][j-1]
```

Base cases: `dp[0][j] = 1` for all j (only one way to travel along the top row: go all right). `dp[i][0] = 1` for all i (only one way along the left column: go all down).

### Build bottom-up

```go
func uniquePaths(m int, n int) int {
    dp := make([][]int, m)
    for i := range dp {
        dp[i] = make([]int, n)
        dp[i][0] = 1
    }
    for j := 0; j < n; j++ {
        dp[0][j] = 1
    }

    for i := 1; i < m; i++ {
        for j := 1; j < n; j++ {
            dp[i][j] = dp[i-1][j] + dp[i][j-1]
        }
    }
    return dp[m-1][n-1]
}
```

### Space optimization

When filling row i, you only need row i-1. So keep a single 1D array and update it in place:

```go
func uniquePathsOpt(m int, n int) int {
    dp := make([]int, n)
    for j := range dp {
        dp[j] = 1
    }
    for i := 1; i < m; i++ {
        for j := 1; j < n; j++ {
            dp[j] += dp[j-1]
        }
    }
    return dp[n-1]
}
```

`dp[j]` is implicitly the value from row i-1 before the inner loop overwrites it. `dp[j-1]` is already the updated value for column j-1 in the current row. This works because of the specific dependency structure (up and left only).

---

## Problem 2: Minimum Path Sum

**LeetCode 64.** Given an m × n grid filled with non-negative numbers, find a path from top-left to bottom-right that minimizes the sum of all numbers along the path. You can only move right or down.

### Define the subproblem

`dp[i][j]` = minimum sum to reach cell (i, j).

### Recurrence

Same movement options as Unique Paths, but now we take the minimum:

```
dp[i][j] = grid[i][j] + min(dp[i-1][j], dp[i][j-1])
```

For the top row (i = 0): `dp[0][j] = dp[0][j-1] + grid[0][j]` (can only come from the left).
For the left column (j = 0): `dp[i][0] = dp[i-1][0] + grid[i][0]` (can only come from above).

### Build bottom-up

```go
func minPathSum(grid [][]int) int {
    m, n := len(grid), len(grid[0])
    dp := make([][]int, m)
    for i := range dp {
        dp[i] = make([]int, n)
    }

    dp[0][0] = grid[0][0]
    for j := 1; j < n; j++ {
        dp[0][j] = dp[0][j-1] + grid[0][j]
    }
    for i := 1; i < m; i++ {
        dp[i][0] = dp[i-1][0] + grid[i][0]
    }

    for i := 1; i < m; i++ {
        for j := 1; j < n; j++ {
            up := dp[i-1][j]
            left := dp[i][j-1]
            if up < left {
                dp[i][j] = grid[i][j] + up
            } else {
                dp[i][j] = grid[i][j] + left
            }
        }
    }
    return dp[m-1][n-1]
}
```

### Space optimization

Same 1D rolling array technique:

```go
func minPathSumOpt(grid [][]int) int {
    m, n := len(grid), len(grid[0])
    dp := make([]int, n)
    dp[0] = grid[0][0]
    for j := 1; j < n; j++ {
        dp[j] = dp[j-1] + grid[0][j]
    }

    for i := 1; i < m; i++ {
        dp[0] += grid[i][0]
        for j := 1; j < n; j++ {
            up := dp[j]     // not yet updated for row i
            left := dp[j-1] // already updated for row i
            if up < left {
                dp[j] = grid[i][j] + up
            } else {
                dp[j] = grid[i][j] + left
            }
        }
    }
    return dp[n-1]
}
```

---

## Problem 3: Edit Distance

**LeetCode 72.** Given two strings word1 and word2, return the minimum number of operations (insert, delete, replace) to convert word1 into word2.

This is where 2D DP becomes genuinely elegant. The three operations map directly to three cells in the DP table.

### Define the subproblem

`dp[i][j]` = minimum edit distance to convert word1[0..i-1] (first i characters) to word2[0..j-1] (first j characters).

### Recurrence

For each pair (i, j):

- If `word1[i-1] == word2[j-1]`: the characters match — no operation needed. `dp[i][j] = dp[i-1][j-1]`.
- Otherwise, take the minimum of three operations:
  - **Replace** word1[i-1] with word2[j-1]: costs 1 + `dp[i-1][j-1]`
  - **Delete** word1[i-1]: costs 1 + `dp[i-1][j]`
  - **Insert** word2[j-1] into word1: costs 1 + `dp[i][j-1]`

```
if word1[i-1] == word2[j-1]:
    dp[i][j] = dp[i-1][j-1]
else:
    dp[i][j] = 1 + min(dp[i-1][j-1], dp[i-1][j], dp[i][j-1])
```

Base cases:
- `dp[i][0] = i`: converting word1[0..i-1] to empty string requires i deletions.
- `dp[0][j] = j`: converting empty string to word2[0..j-1] requires j insertions.

### Build bottom-up

```go
func minDistance(word1 string, word2 string) int {
    m, n := len(word1), len(word2)
    dp := make([][]int, m+1)
    for i := range dp {
        dp[i] = make([]int, n+1)
        dp[i][0] = i
    }
    for j := 0; j <= n; j++ {
        dp[0][j] = j
    }

    for i := 1; i <= m; i++ {
        for j := 1; j <= n; j++ {
            if word1[i-1] == word2[j-1] {
                dp[i][j] = dp[i-1][j-1]
            } else {
                replace := dp[i-1][j-1]
                delete_ := dp[i-1][j]
                insert := dp[i][j-1]
                best := replace
                if delete_ < best {
                    best = delete_
                }
                if insert < best {
                    best = insert
                }
                dp[i][j] = 1 + best
            }
        }
    }
    return dp[m][n]
}
```

### Space optimization

Each row only needs the previous row. Use a 1D array, but be careful: `dp[i-1][j-1]` (the diagonal) must be saved before overwriting:

```go
func minDistanceOpt(word1 string, word2 string) int {
    m, n := len(word1), len(word2)
    dp := make([]int, n+1)
    for j := 0; j <= n; j++ {
        dp[j] = j
    }

    for i := 1; i <= m; i++ {
        prev := dp[0] // dp[i-1][j-1] for j=0 is dp[i-1][-1] which doesn't exist; start prev = i-1
        dp[0] = i
        for j := 1; j <= n; j++ {
            temp := dp[j] // save dp[i-1][j] before overwrite; this becomes next iteration's diagonal
            if word1[i-1] == word2[j-1] {
                dp[j] = prev
            } else {
                best := prev // replace: dp[i-1][j-1]
                if dp[j] < best {
                    best = dp[j] // delete: dp[i-1][j]
                }
                if dp[j-1] < best {
                    best = dp[j-1] // insert: dp[i][j-1]
                }
                dp[j] = 1 + best
            }
            prev = temp
        }
    }
    return dp[n]
}
```

The `prev` variable tracks the diagonal (`dp[i-1][j-1]`) because after we update `dp[j]`, that slot is overwritten and the diagonal for the next column is gone. Save it before the update, use it inside.

---

## How to Recognize This Pattern

- The problem involves a grid with movement (Unique Paths, Minimum Path Sum).
- The problem involves two strings and you're comparing or transforming one into the other (Edit Distance, and the entire next lesson).
- `dp[i][j]` is the answer "restricted to the top-left corner of the table."
- The transitions come from a fixed set of neighbors: up, left, diagonal.

The boundary conditions (first row and first column) are almost always just linear initializations — filling in the degenerate cases where one of the dimensions is zero.

---

## Key Takeaway

2D DP is just 1D DP with an extra index. The table fills row by row. The dependency structure (which neighbors feed into dp[i][j]) is determined by the allowed operations: movement directions in grid problems, character match/insert/delete in string problems.

Draw the table. Write the base cases first. Then fill the interior. Edit Distance is the most important 2D DP problem to internalize because the three-way min and the diagonal dependency appear in many string comparison problems.

---

*Previous: [Lesson 22: 1D DP Advanced](/post/fundamentals/interview-dp-1d-advanced/) · Next: [Lesson 24: 2D DP Advanced](/post/fundamentals/interview-dp-2d-advanced/)*
