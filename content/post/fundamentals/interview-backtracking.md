---
title: "Lesson 34: Backtracking — Try everything, undo what doesn't work"
author: "Atharva Pandey"
keywords:
  - backtracking
  - permutations
  - n-queens
  - sudoku solver
  - recursion
  - interview patterns
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 34
date: "2025-12-11T00:00:00.000Z"
---

Backtracking scared me for a long time. The problems looked like they needed some clever mathematical insight — some observation that would magically reduce the search space. Then I realized the actual technique is almost mechanical: build a candidate solution incrementally, check constraints at each step, and undo your last choice if the current path cannot lead anywhere valid. That's it. The art is in recognizing when to prune.

Every backtracking solution I have ever written follows the same skeleton. Once that skeleton is internalized, the remaining work is problem-specific constraint checking. The code almost writes itself.

## The Pattern

The universal backtracking template:

```go
func backtrack(state State, choices []Choice) {
    if isComplete(state) {
        record(state)
        return
    }
    for _, choice := range choices {
        if isValid(state, choice) {
            apply(state, choice)           // make the choice
            backtrack(state, nextChoices)  // recurse
            undo(state, choice)            // unmake the choice
        }
    }
}
```

The three moves are always: **apply, recurse, undo**. The undo is what distinguishes backtracking from plain recursion — you restore state so that the next branch starts from the same clean slate.

Pruning happens in `isValid`. The more work you do in `isValid` to eliminate invalid paths early, the faster your solution runs in practice. In the worst case backtracking is exponential, but good pruning can make it fast enough for the problem sizes used in interviews.

## Problem 1: Permutations

Given an array of distinct integers, return all permutations.

```go
func permute(nums []int) [][]int {
    result := [][]int{}
    used := make([]bool, len(nums))
    current := []int{}

    var backtrack func()
    backtrack = func() {
        if len(current) == len(nums) {
            // Record a copy — slices are references in Go
            perm := make([]int, len(current))
            copy(perm, current)
            result = append(result, perm)
            return
        }
        for i, num := range nums {
            if used[i] {
                continue
            }
            // Apply
            used[i] = true
            current = append(current, num)
            // Recurse
            backtrack()
            // Undo
            current = current[:len(current)-1]
            used[i] = false
        }
    }

    backtrack()
    return result
}
```

Time: O(n × n!) — there are n! permutations and each takes O(n) to copy. Space: O(n) for the recursion stack plus O(n × n!) for results.

Notice the Go-specific gotcha: when you append the completed permutation to `result`, you must `copy` it into a new slice. If you append `current` directly, all results will end up pointing to the same backing array and you will get garbage.

For permutations with duplicates (a common follow-up), sort first and add a guard: `if i > 0 && nums[i] == nums[i-1] && !used[i-1] { continue }`. This skips duplicate choices at the same recursion depth.

## Problem 2: N-Queens

Place N queens on an N×N chessboard so that no two queens attack each other. Return all valid board configurations.

Queens attack along rows, columns, and diagonals. The key pruning insight: place exactly one queen per row. Use sets to track occupied columns and diagonals.

```go
func solveNQueens(n int) [][]string {
    result := [][]string{}
    queens := make([]int, n) // queens[row] = column
    for i := range queens {
        queens[i] = -1
    }
    cols := map[int]bool{}
    diag1 := map[int]bool{} // row - col (top-left to bottom-right)
    diag2 := map[int]bool{} // row + col (top-right to bottom-left)

    var backtrack func(row int)
    backtrack = func(row int) {
        if row == n {
            board := buildBoard(queens, n)
            result = append(result, board)
            return
        }
        for col := 0; col < n; col++ {
            if cols[col] || diag1[row-col] || diag2[row+col] {
                continue // this cell is under attack
            }
            // Apply
            queens[row] = col
            cols[col] = true
            diag1[row-col] = true
            diag2[row+col] = true
            // Recurse
            backtrack(row + 1)
            // Undo
            queens[row] = -1
            cols[col] = false
            diag1[row-col] = false
            diag2[row+col] = false
        }
    }

    backtrack(0)
    return result
}

func buildBoard(queens []int, n int) []string {
    board := make([]string, n)
    for row, col := range queens {
        line := make([]byte, n)
        for j := range line {
            line[j] = '.'
        }
        line[col] = 'Q'
        board[row] = string(line)
    }
    return board
}
```

Time: O(n!) in the worst case, but the column and diagonal constraints prune heavily. Space: O(n) stack depth, O(n) for the constraint sets.

The diagonal encoding is the elegant part. `row - col` is constant along a top-left-to-bottom-right diagonal, and `row + col` is constant along the other diagonal. Track these two values and constraint checking drops to O(1).

## Problem 3: Sudoku Solver

Fill in the blanks of a partially completed 9×9 Sudoku grid.

```go
func solveSudoku(board [][]byte) {
    solve(board)
}

func solve(board [][]byte) bool {
    for r := 0; r < 9; r++ {
        for c := 0; c < 9; c++ {
            if board[r][c] != '.' {
                continue
            }
            for d := byte('1'); d <= '9'; d++ {
                if isValidPlacement(board, r, c, d) {
                    board[r][c] = d
                    if solve(board) {
                        return true // found a solution, propagate up
                    }
                    board[r][c] = '.' // undo
                }
            }
            return false // no digit worked: dead end, backtrack
        }
    }
    return true // no empty cells remain
}

func isValidPlacement(board [][]byte, row, col int, d byte) bool {
    boxRow, boxCol := (row/3)*3, (col/3)*3

    for i := 0; i < 9; i++ {
        if board[row][i] == d { // row conflict
            return false
        }
        if board[i][col] == d { // column conflict
            return false
        }
        if board[boxRow+i/3][boxCol+i%3] == d { // 3×3 box conflict
            return false
        }
    }
    return true
}
```

This is backtracking with early termination: `solve` returns a bool and propagates `true` as soon as any complete solution is found. The moment we return `false`, the caller knows to undo its last placement and try the next digit.

The `board[boxRow+i/3][boxCol+i%3]` indexing for the 3×3 box is worth memorizing. With `i` from 0 to 8, `i/3` gives the row within the box (0, 0, 0, 1, 1, 1, 2, 2, 2) and `i%3` gives the column within the box (0, 1, 2, 0, 1, 2, 0, 1, 2).

A more efficient version pre-computes which digits are valid for each empty cell ("constraint propagation"), but the version above passes all LeetCode test cases and is the right complexity for an interview.

## How to Recognize This Pattern

Backtracking fits when you need to:

1. **Generate all combinations, permutations, or subsets** of a set.
2. **Solve constraint satisfaction problems** (Sudoku, N-Queens, crossword filling).
3. **Find paths in a graph or grid that satisfy conditions**, where you mark nodes as visited and unmark on the way back.
4. **Explore decisions where you do not know in advance which choices are valid** and must try options, discover dead ends, and retreat.

The key distinguishing feature from plain DFS: **you undo choices**. In DFS on a graph you typically track visited nodes in a set and never unmark them (you want to find if a path exists). In backtracking you unmark them so the same node can appear in different candidate paths.

The performance signal: if the constraints allow heavy pruning (N-Queens, Sudoku), backtracking is efficient. If constraints are loose and the search space is large, you might need DP instead.

## Key Takeaway

Backtracking is systematic trial and error with an undo button. The template is always the same: apply a choice, recurse, undo the choice. The problem-specific work lives in two places — what choices are available at each step, and what makes a choice valid. Get the template automatic and backtracking problems become straightforward.

The Go-specific reminder: always copy slices before adding them to your results. Appending the same slice reference is the most common source of wrong answers in Go backtracking code.

---

**Series**: [Interview Patterns: Cracking FAANG+ in the AI Era](/series/interview-patterns-cracking-faang-in-the-ai-era/)

**Previous**: [Lesson 33: Trie](/post/fundamentals/interview-trie/) — When you need prefix matching

**Next**: [Lesson 35: Greedy](/post/fundamentals/interview-greedy/) — Local optimal leads to global optimal (sometimes)
