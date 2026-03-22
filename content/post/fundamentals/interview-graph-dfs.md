---
title: "Interview Patterns L17: Graph DFS — Explore everything, mark what you've seen"
author: "Atharva Pandey"
keywords:
  - graph DFS
  - clone graph
  - pacific atlantic water flow
  - course schedule
  - cycle detection
  - interview patterns
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 17
date: "2025-03-24T00:00:00.000Z"
---

Graph DFS is the tool you reach for when you need to explore every reachable node, not just the closest ones. Unlike BFS, which expands in rings of increasing distance, DFS commits to one direction until it cannot go further, then backtracks. This makes it naturally suited for problems where you need to visit entire connected regions, detect cycles, or trace paths between specific source and destination sets.

The three problems in this lesson each probe a different dimension of graph DFS. Cloning a graph tests your ability to handle shared references while building a new structure. Pacific Atlantic Water Flow introduces the reverse-DFS technique — instead of asking "where can this water flow?", you ask "which cells can reach each ocean?" Course Schedule uses DFS to detect cycles, the canonical prerequisite for topological ordering. Together they cover the non-trivial uses of graph DFS that come up in senior engineering interviews.

## The Pattern

Graph DFS template:

```go
func dfs(node int, visited map[int]bool, graph map[int][]int) {
    if visited[node] {
        return
    }
    visited[node] = true
    for _, neighbor := range graph[node] {
        dfs(neighbor, visited, graph)
    }
}
```

For graph problems (as opposed to tree problems), the visited set is non-negotiable. Without it, cycles turn DFS into an infinite loop. The visited set can be a `map[int]bool`, a boolean slice indexed by node number, or the node itself can be mutated to mark it visited (common in grid problems).

## Problem 1 [Medium]: Clone Graph

**Problem:** Given a reference to a node in a connected undirected graph, return a deep copy of the graph. Each node has a value and a list of neighbors.

```go
type GraphNode struct {
    Val       int
    Neighbors []*GraphNode
}
```

**Why this is tricky:** Graphs can have cycles and shared references. A naive recursive clone would either infinite-loop on cycles or create duplicate nodes when a node is reachable via multiple paths.

**Brute force / wrong attempt:** Recursively clone each node. When you encounter a neighbor, recursively clone it. This hits infinite recursion on cycles and creates duplicate copies of the same node.

**Building the correct solution — DFS with a clone map:**

Key insight: maintain a map from original node to its clone. Before recursing into a neighbor, check if we have already cloned it. If so, reuse the existing clone.

```go
func cloneGraph(node *GraphNode) *GraphNode {
    if node == nil {
        return nil
    }
    cloneMap := make(map[*GraphNode]*GraphNode)

    var dfs func(*GraphNode) *GraphNode
    dfs = func(n *GraphNode) *GraphNode {
        if clone, exists := cloneMap[n]; exists {
            return clone // already cloned, return existing copy
        }
        clone := &GraphNode{Val: n.Val}
        cloneMap[n] = clone // register BEFORE recursing to handle cycles

        for _, neighbor := range n.Neighbors {
            clone.Neighbors = append(clone.Neighbors, dfs(neighbor))
        }
        return clone
    }

    return dfs(node)
}
```

**Why register the clone BEFORE recursing:** If you register after the recursive loop, a cycle will cause infinite recursion. When DFS follows a cycle back to a node already being processed, `cloneMap[n]` must exist at that point. Registering the shell clone before the loop breaks the cycle.

**Tracing through a cycle:** Consider nodes A-B-C forming a triangle (A→B→C→A).
1. DFS(A): create clone A', register A→A', then recurse neighbors
2. DFS(B): create clone B', register B→B', recurse neighbors
3. DFS(C): create clone C', register C→C', recurse neighbors
4. DFS(A) again: A is in cloneMap → return A' immediately. No infinite loop.

**Complexity:** O(V+E) time and space where V is vertices and E is edges.

## Problem 2 [Hard]: Pacific Atlantic Water Flow

**Problem:** An island has two oceans: Pacific (top and left edges) and Atlantic (bottom and right edges). Water can flow from a cell to an adjacent cell with equal or lower height. Return all cells from which water can flow to both oceans.

**Brute force:** For each cell, DFS/BFS to check if water can reach both oceans. O((m×n)²) time — too slow for large grids.

**Building the reverse DFS approach:**

Instead of asking "can water flow from this cell to the ocean?", ask "which cells can water flow TO from the ocean?" We run DFS backwards — from ocean edges uphill (to equal or higher neighbors).

Two separate DFS passes:
1. From all Pacific-touching cells (top row + left column) → mark cells reachable by Pacific
2. From all Atlantic-touching cells (bottom row + right column) → mark cells reachable by Atlantic

Cells in both sets can flow to both oceans.

```go
func pacificAtlantic(heights [][]int) [][]int {
    if len(heights) == 0 {
        return nil
    }
    rows, cols := len(heights), len(heights[0])
    pacific := make([][]bool, rows)
    atlantic := make([][]bool, rows)
    for i := range pacific {
        pacific[i] = make([]bool, cols)
        atlantic[i] = make([]bool, cols)
    }

    dirs := [][2]int{{0, 1}, {0, -1}, {1, 0}, {-1, 0}}

    var dfs func(r, c int, visited [][]bool)
    dfs = func(r, c int, visited [][]bool) {
        visited[r][c] = true
        for _, d := range dirs {
            nr, nc := r+d[0], c+d[1]
            if nr < 0 || nr >= rows || nc < 0 || nc >= cols {
                continue
            }
            if visited[nr][nc] {
                continue
            }
            // Reverse flow: only visit if neighbor is >= current (water flows downhill,
            // so going backwards means we go uphill or flat)
            if heights[nr][nc] >= heights[r][c] {
                dfs(nr, nc, visited)
            }
        }
    }

    // Seed Pacific from top row and left column
    for c := 0; c < cols; c++ {
        if !pacific[0][c] {
            dfs(0, c, pacific)
        }
    }
    for r := 0; r < rows; r++ {
        if !pacific[r][0] {
            dfs(r, 0, pacific)
        }
    }

    // Seed Atlantic from bottom row and right column
    for c := 0; c < cols; c++ {
        if !atlantic[rows-1][c] {
            dfs(rows-1, c, atlantic)
        }
    }
    for r := 0; r < rows; r++ {
        if !atlantic[r][cols-1] {
            dfs(r, cols-1, atlantic)
        }
    }

    // Collect cells reachable from both oceans
    var result [][]int
    for r := 0; r < rows; r++ {
        for c := 0; c < cols; c++ {
            if pacific[r][c] && atlantic[r][c] {
                result = append(result, []int{r, c})
            }
        }
    }
    return result
}
```

**Why reverse direction is key:** Forward DFS from each cell would re-explore most of the grid for every starting cell. Reverse DFS from ocean boundaries runs at most twice (once per ocean) across the entire grid, giving O(m×n) total.

**The `>=` condition:** In reverse flow, we are asking "from this ocean-touching cell, which uphill cells can eventually drain here?" So we move to neighbors that are at least as high (water would flow from them to us, not the other way).

**Complexity:** O(m×n) time and space.

## Problem 3 [Medium/Hard]: Course Schedule (Cycle Detection)

**Problem:** There are `numCourses` courses (0 to numCourses-1). Some have prerequisites: `[a, b]` means you must take b before a. Return true if you can finish all courses (i.e., no circular prerequisites exist).

**This is cycle detection in a directed graph.**

**Brute force:** Try every possible ordering and check if it works. Exponential time.

**Building the DFS cycle detection solution:**

DFS for cycles uses three states per node, not just two:
- `0` = unvisited
- `1` = currently being visited (in the current DFS path)
- `2` = fully visited (processed, no cycle found from here)

A cycle exists if we reach a node that is currently in the DFS path (state 1).

```go
func canFinish(numCourses int, prerequisites [][]int) bool {
    // Build adjacency list
    graph := make([][]int, numCourses)
    for _, pre := range prerequisites {
        // pre[0] depends on pre[1]: edge pre[1] -> pre[0]
        graph[pre[1]] = append(graph[pre[1]], pre[0])
    }

    // 0=unvisited, 1=in progress, 2=done
    state := make([]int, numCourses)

    var hasCycle func(node int) bool
    hasCycle = func(node int) bool {
        if state[node] == 1 {
            return true // back edge: cycle detected
        }
        if state[node] == 2 {
            return false // already fully processed: safe
        }
        state[node] = 1 // mark as in progress
        for _, neighbor := range graph[node] {
            if hasCycle(neighbor) {
                return true
            }
        }
        state[node] = 2 // mark as done
        return false
    }

    for i := 0; i < numCourses; i++ {
        if state[i] == 0 {
            if hasCycle(i) {
                return false
            }
        }
    }
    return true
}
```

**Why three states instead of two:** With two states (visited/unvisited), you cannot distinguish between "this node was visited in a previous DFS call (no cycle through it)" and "this node is on the current DFS path (cycle!)". The third state `2` ("done") prevents false positives when a node is reachable from multiple other nodes.

**Example with false positive if using two states:**
```
A → B → C
D → B
```
When DFS from D reaches B, B is already visited from A's DFS. With two states, you would incorrectly flag a cycle. With three states, B is state 2 (done), so we immediately return false (no cycle).

**Complexity:** O(V+E) time, O(V) space for the state array and recursion stack.

## How to Recognize This Pattern

- "Clone / deep copy" a graph → DFS with a "visited+clone map", register before recursing
- "Reachability from multiple source regions" → reverse DFS from the boundaries/sources, then intersect
- "Detect circular dependency / cycle in directed graph" → DFS with three-state coloring (unvisited, in-progress, done)
- "Can all nodes be processed in order?" → cycle detection (if no cycle, topological sort exists, see L18)

The multi-state technique from Course Schedule is the foundation for L18's topological sort. DFS cycle detection and topological ordering are two sides of the same coin.

## Key Takeaway

Graph DFS problems center on three techniques: clone with a memoization map (break cycle via pre-registration), reverse DFS from boundaries (flip the direction to avoid redundant work), and three-state cycle detection (distinguish "in current path" from "previously completed"). For cloning, the map both breaks cycles and prevents duplicate nodes. For Pacific Atlantic, reversing the flow direction turns an O(n²) problem into O(n). For cycle detection, the third state is what makes the algorithm correct — two states are insufficient for directed graphs with diamond-shaped dependencies. Internalize these three patterns and you have the DFS toolkit for the hardest graph questions at FAANG.

---

**Previous:** [L16: Graph BFS](/post/fundamentals/interview-graph-bfs/) | **Next:** [L18: Topological Sort — Order tasks with dependencies](/post/fundamentals/interview-topo-sort/)
