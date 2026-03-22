---
title: "Interview Patterns L16: Graph BFS — Shortest path in unweighted graphs"
author: "Atharva Pandey"
keywords:
  - graph BFS
  - number of islands
  - rotting oranges
  - shortest path binary matrix
  - unweighted shortest path
  - interview patterns
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 16
date: "2025-03-07T00:00:00.000Z"
---

The moment someone says "shortest path" in an interview, I mentally split the problem into two cases: weighted or unweighted? If unweighted — every edge costs the same, every step is distance 1 — BFS gives the shortest path guarantee for free. No Dijkstra needed. BFS explores nodes in order of increasing distance from the source, so the first time you reach a destination, that is definitionally the shortest path.

Graph BFS is slightly more involved than tree BFS because graphs can have cycles. You need a visited set. Everything else is the same queue-based expansion. The three problems in this lesson — Number of Islands, Rotting Oranges, Shortest Path in Binary Matrix — each add one new dimension to the BFS template. Master the template, understand what each problem adds, and you have covered the majority of FAANG graph BFS questions.

## The Pattern

Graph BFS template:

```go
visited := make(map[int]bool) // or a boolean grid for grid problems
queue := []int{start}
visited[start] = true
steps := 0

for len(queue) > 0 {
    levelSize := len(queue)
    for i := 0; i < levelSize; i++ {
        node := queue[0]
        queue = queue[1:]
        // process node
        for _, neighbor := range getNeighbors(node) {
            if !visited[neighbor] {
                visited[neighbor] = true
                queue = append(queue, neighbor)
            }
        }
    }
    steps++
}
```

For grid problems, "node" is a `(row, col)` coordinate and "neighbors" are the four (or eight) adjacent cells. Visited is a boolean grid of the same dimensions as the input.

## Problem 1 [Medium]: Number of Islands

**Problem:** Given an `m x n` grid of `'1'` (land) and `'0'` (water), count the number of islands. An island is surrounded by water and formed by connecting adjacent lands horizontally or vertically.

**Brute force / correct approach:**

There is no smarter approach than O(m×n). You must check every cell. The question is whether you use BFS or DFS to mark an island as fully visited once found.

This problem is not about shortest path — it is about counting connected components. BFS and DFS are equivalent here. I use BFS because it is the focus of this lesson.

**Building the BFS solution:**

Iterate over every cell. When you find an unvisited land cell, increment the island count and BFS from that cell to mark the entire island as visited.

```go
func numIslands(grid [][]byte) int {
    if len(grid) == 0 {
        return 0
    }
    rows, cols := len(grid), len(grid[0])
    count := 0
    dirs := [][2]int{{0, 1}, {0, -1}, {1, 0}, {-1, 0}}

    for r := 0; r < rows; r++ {
        for c := 0; c < cols; c++ {
            if grid[r][c] == '1' {
                count++
                // BFS to mark entire island
                queue := [][2]int{{r, c}}
                grid[r][c] = '0' // mark visited by overwriting

                for len(queue) > 0 {
                    pos := queue[0]
                    queue = queue[1:]
                    for _, d := range dirs {
                        nr, nc := pos[0]+d[0], pos[1]+d[1]
                        if nr >= 0 && nr < rows && nc >= 0 && nc < cols && grid[nr][nc] == '1' {
                            grid[nr][nc] = '0'
                            queue = append(queue, [2]int{nr, nc})
                        }
                    }
                }
            }
        }
    }
    return count
}
```

**Why overwrite `'1'` with `'0'` instead of a separate visited array:** The problem says we can modify the grid (or we can restore afterward). Overwriting is a common trick to avoid allocating a separate boolean grid. If the interviewer says you cannot modify the grid, allocate `visited [rows][cols]bool`.

**Complexity:** O(m×n) time — every cell is visited at most twice (once in the outer loop, once during BFS). O(min(m,n)) space for the queue in the worst case (a thin diagonal island).

## Problem 2 [Medium]: Rotting Oranges

**Problem:** A grid contains `0` (empty), `1` (fresh orange), `2` (rotten orange). Every minute, any fresh orange adjacent to a rotten orange becomes rotten. Return the minimum number of minutes until no fresh oranges remain. Return -1 if it is impossible.

**Why this is multi-source BFS:** Rotting spreads simultaneously from all rotten oranges. Single-source BFS would be wrong — you would process rotten oranges one at a time, not simultaneously.

**Key insight:** Add all initially rotten oranges to the queue at once before starting BFS. The level count then represents simultaneous spreading.

```go
func orangesRotting(grid [][]int) int {
    rows, cols := len(grid), len(grid[0])
    queue := [][2]int{}
    fresh := 0
    dirs := [][2]int{{0, 1}, {0, -1}, {1, 0}, {-1, 0}}

    // Collect all rotten oranges and count fresh ones
    for r := 0; r < rows; r++ {
        for c := 0; c < cols; c++ {
            if grid[r][c] == 2 {
                queue = append(queue, [2]int{r, c})
            } else if grid[r][c] == 1 {
                fresh++
            }
        }
    }

    if fresh == 0 {
        return 0
    }

    minutes := 0

    for len(queue) > 0 && fresh > 0 {
        levelSize := len(queue)
        for i := 0; i < levelSize; i++ {
            pos := queue[0]
            queue = queue[1:]
            for _, d := range dirs {
                nr, nc := pos[0]+d[0], pos[1]+d[1]
                if nr >= 0 && nr < rows && nc >= 0 && nc < cols && grid[nr][nc] == 1 {
                    grid[nr][nc] = 2
                    fresh--
                    queue = append(queue, [2]int{nr, nc})
                }
            }
        }
        minutes++
    }

    if fresh > 0 {
        return -1
    }
    return minutes
}
```

**Why increment minutes AFTER the level loop:** We increment minutes once per level, not once per node. Each level represents one minute of simultaneous spreading.

**The -1 case:** If there are fresh oranges unreachable from any rotten orange (e.g., surrounded by empty cells), the BFS will exhaust the queue while `fresh > 0`. The final `if fresh > 0` catches this.

**Complexity:** O(m×n) time, O(m×n) space for the queue in the worst case.

**Where multi-source BFS appears beyond this problem:** Anywhere you need the distance from the nearest of multiple sources — distance to nearest exit in a maze, distance to nearest 0 in a binary matrix, spread from multiple fire sources. The pattern is identical: seed the queue with all sources simultaneously.

## Problem 3 [Hard]: Shortest Path in Binary Matrix

**Problem:** Given an `n x n` binary matrix, find the length of the shortest clear path from the top-left `(0,0)` to the bottom-right `(n-1, n-1)`. A clear path uses only cells with value `0`, moves in 8 directions, and the path length is the number of cells visited. Return -1 if no clear path exists.

**Brute force:** DFS and track minimum path length. O(n²) nodes, each potentially visited many times. Exponential in the worst case without memoization.

**Building the BFS solution:**

BFS guarantees that the first time we reach `(n-1, n-1)`, we have taken the shortest path. We track the path length by starting at distance 1 (the starting cell itself counts) and incrementing by 1 with each BFS level.

```go
func shortestPathBinaryMatrix(grid [][]int) int {
    n := len(grid)
    if grid[0][0] == 1 || grid[n-1][n-1] == 1 {
        return -1
    }
    if n == 1 {
        return 1
    }

    dirs := [][2]int{
        {-1, -1}, {-1, 0}, {-1, 1},
        {0, -1}, {0, 1},
        {1, -1}, {1, 0}, {1, 1},
    }

    type State struct{ r, c, dist int }
    queue := []State{{0, 0, 1}}
    grid[0][0] = 1 // mark visited

    for len(queue) > 0 {
        curr := queue[0]
        queue = queue[1:]

        for _, d := range dirs {
            nr, nc := curr.r+d[0], curr.c+d[1]
            if nr < 0 || nr >= n || nc < 0 || nc >= n || grid[nr][nc] != 0 {
                continue
            }
            if nr == n-1 && nc == n-1 {
                return curr.dist + 1
            }
            grid[nr][nc] = 1 // mark visited
            queue = append(queue, State{nr, nc, curr.dist + 1})
        }
    }
    return -1
}
```

**Why carry `dist` in the state rather than using level batches:** For this problem, carrying distance in the state is equivalent to the level-batch approach and is slightly cleaner when each step is distance +1. Both work.

**Why mark visited when enqueuing, not when dequeuing:** If you mark visited on dequeue, the same cell can be enqueued multiple times before being dequeued (by different paths arriving at the same cell). Marking on enqueue prevents this and is more efficient. This is a subtle but important correctness point.

**Complexity:** O(n²) time (each cell visited at most once), O(n²) space for the queue.

## How to Recognize This Pattern

- "Minimum steps / minimum time / shortest path" in a grid or unweighted graph → BFS
- "How many connected groups?" → BFS or DFS, count the number of times you start a traversal from an unvisited node
- "Spread from multiple sources simultaneously" → multi-source BFS, seed queue with all sources upfront
- 8-directional movement vs 4-directional: 8 directions includes diagonals
- "If it is impossible, return -1" → track a count (e.g., `fresh` oranges) and check after BFS exhausts

The distinction between BFS and DFS for these problems: **BFS for shortest path, DFS for connectivity**. Both find all reachable nodes, but only BFS guarantees shortest path.

## Key Takeaway

Graph BFS in grids follows a consistent template: encode position as `(row, col)`, use a `dirs` slice for neighbors, mark visited when enqueuing, and process level by level when you need to count steps. Number of Islands shows the connected-components use of BFS — count how many times you need to start BFS from an unvisited source. Rotting Oranges shows multi-source BFS — seed the queue with all sources simultaneously before the loop starts. Shortest Path in Binary Matrix shows the canonical single-source shortest path use. Once the template is automatic, the challenge is recognizing which variant to apply. Ask: single source or multi-source? Just connectivity, or shortest distance too?

---

**Previous:** [L15: Tree BFS Patterns](/post/fundamentals/interview-tree-bfs/) | **Next:** [L17: Graph DFS — Explore everything, mark what you've seen](/post/fundamentals/interview-graph-dfs/)
