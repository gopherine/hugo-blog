---
title: "Interview Patterns L20: Shortest Path — When edges have weights"
author: "Atharva Pandey"
keywords:
  - shortest path
  - Dijkstra algorithm
  - Bellman-Ford algorithm
  - network delay time
  - cheapest flights
  - path with minimum effort
  - weighted graph
  - interview patterns
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 20
date: "2025-05-17T00:00:00.000Z"
---

In L16, I explained why BFS solves shortest path problems in unweighted graphs — every edge costs the same, so distance equals hop count, and BFS naturally explores in order of increasing hops. But the moment edges have different weights, BFS breaks. A path with two heavy edges can be longer than a path with ten light ones.

This is the lesson where we graduate to weighted shortest path. Two algorithms matter most for interviews: Dijkstra's (greedy, non-negative weights) and Bellman-Ford (dynamic programming, handles negative weights). A third problem shows a modified Dijkstra on a 2D grid. Understanding when to use each — and why the other would be wrong — is what separates candidates who have memorized code from candidates who actually understand the algorithms.

## The Pattern

**Dijkstra's algorithm:**
- Use a min-heap (priority queue) ordered by current best distance
- Initialize source distance to 0, all others to infinity
- Greedily process the node with the smallest known distance
- Relax its edges: update neighbor's distance if a shorter path is found
- Fails with negative weights (greedy assumption breaks)

**Bellman-Ford algorithm:**
- Run V-1 passes over all edges, relaxing each edge in every pass
- After V-1 passes, shortest paths are finalized (if no negative cycles)
- Can detect negative cycles (a path that keeps getting shorter)
- Use when: negative weights exist, or the problem adds a constraint like "at most k stops"

## Problem 1 [Medium]: Network Delay Time (Dijkstra)

**Problem:** There are n network nodes (1 to n). You have directed edges `[u, v, w]` meaning a signal from u reaches v in w time. Send a signal from node k. Return the time until all nodes receive the signal, or -1 if not all nodes are reachable.

**Answer:** the maximum shortest-path distance from k to any node.

**Brute force:** Bellman-Ford runs in O(V×E). Dijkstra is faster for non-negative weights.

**Building Dijkstra's algorithm:**

```go
import "container/heap"

// Min-heap of (distance, node) pairs
type Item struct {
    dist, node int
}
type PQ []Item

func (pq PQ) Len() int            { return len(pq) }
func (pq PQ) Less(i, j int) bool  { return pq[i].dist < pq[j].dist }
func (pq PQ) Swap(i, j int)       { pq[i], pq[j] = pq[j], pq[i] }
func (pq *PQ) Push(x interface{}) { *pq = append(*pq, x.(Item)) }
func (pq *PQ) Pop() interface{} {
    old := *pq
    n := len(old)
    x := old[n-1]
    *pq = old[:n-1]
    return x
}

func networkDelayTime(times [][]int, n int, k int) int {
    // Build adjacency list
    graph := make(map[int][][2]int) // node -> [(neighbor, weight)]
    for _, t := range times {
        u, v, w := t[0], t[1], t[2]
        graph[u] = append(graph[u], [2]int{v, w})
    }

    // Initialize distances to infinity
    const inf = 1<<31 - 1
    dist := make([]int, n+1)
    for i := range dist {
        dist[i] = inf
    }
    dist[k] = 0

    // Min-heap: start with source
    pq := &PQ{{dist: 0, node: k}}
    heap.Init(pq)

    for pq.Len() > 0 {
        curr := heap.Pop(pq).(Item)
        d, u := curr.dist, curr.node

        // If we have already found a shorter path to u, skip
        if d > dist[u] {
            continue
        }

        for _, edge := range graph[u] {
            v, w := edge[0], edge[1]
            newDist := dist[u] + w
            if newDist < dist[v] {
                dist[v] = newDist
                heap.Push(pq, Item{dist: newDist, node: v})
            }
        }
    }

    // Find max distance to any node
    maxDist := 0
    for i := 1; i <= n; i++ {
        if dist[i] == inf {
            return -1
        }
        if dist[i] > maxDist {
            maxDist = dist[i]
        }
    }
    return maxDist
}
```

**Why the `if d > dist[u] { continue }` check:** We might push the same node to the heap multiple times with different distances (as we find shorter paths). When we pop a node with a stale distance, we skip it. This is called "lazy deletion" and avoids the need for a decrease-key operation.

**Why the greedy assumption holds:** When we pop a node from the min-heap, we have the globally shortest path to it (given non-negative weights). Any future path to that node through unprocessed nodes would have to go through at least one more non-negative edge, so it cannot be shorter. Negative weights break this — they could make a "longer" looking path cheaper after adding a negative edge.

**Complexity:** O((V+E) log V) with a binary heap.

## Problem 2 [Medium]: Cheapest Flights Within K Stops (Bellman-Ford)

**Problem:** There are n cities and flight routes `[from, to, price]`. Find the cheapest price from src to dst with at most k stops (at most k+1 edges). Return -1 if impossible.

**Why not Dijkstra:** Dijkstra finds globally shortest paths, but this problem adds a constraint: at most k intermediate stops. Dijkstra has no natural way to enforce an edge-count constraint. Bellman-Ford's pass-based structure does.

**Building the k-stop Bellman-Ford:**

Bellman-Ford's i-th pass computes shortest paths using at most i edges. We run exactly k+1 passes (k stops = k+1 edges).

```go
func findCheapestPrice(n int, flights [][]int, src int, dst int, k int) int {
    const inf = 1<<31 - 1
    // prices[i] = cheapest price to reach node i
    prices := make([]int, n)
    for i := range prices {
        prices[i] = inf
    }
    prices[src] = 0

    // Run k+1 passes (k stops = k+1 edges)
    for i := 0; i <= k; i++ {
        // CRITICAL: work on a copy to avoid using updates from the same pass
        tmp := make([]int, n)
        copy(tmp, prices)

        for _, flight := range flights {
            from, to, price := flight[0], flight[1], flight[2]
            if prices[from] == inf {
                continue
            }
            if prices[from]+price < tmp[to] {
                tmp[to] = prices[from] + price
            }
        }
        prices = tmp
    }

    if prices[dst] == inf {
        return -1
    }
    return prices[dst]
}
```

**Why copy into `tmp` instead of updating `prices` in-place:** If we update `prices` in-place during a pass, a node updated early in the pass could be used by edges processed later in the same pass, effectively allowing more than one hop per pass. The copy ensures each pass only extends paths by exactly one edge.

**Tracing the logic:** After pass 0 (src → neighbors), `prices[v]` = cheapest direct flight from src to v. After pass 1, `prices[v]` = cheapest path with at most 2 edges. After pass k, at most k+1 edges. We want paths with at most k+1 edges, so k+1 passes starting at index 0 (0 to k inclusive) gives us exactly that.

**Complexity:** O(k × E) time. Much faster than full Bellman-Ford (O(V×E)) because we stop at k+1 passes.

## Problem 3 [Hard]: Path With Minimum Effort

**Problem:** In a 2D grid, the "effort" of a route is the maximum absolute difference in heights between consecutive cells. Find the path from top-left to bottom-right that minimizes this effort.

**Why this is a shortest-path problem in disguise:** The "cost" of a path is the maximum edge weight along it, not the sum. We want to minimize the maximum. This is called a minimax path.

**Building the modified Dijkstra:**

Replace "total distance" with "maximum effort so far." The heap orders by minimum current maximum effort. Relaxation updates when a path with lower maximum effort is found.

```go
import "container/heap"

type EffortItem struct {
    effort, r, c int
}
type EffortPQ []EffortItem

func (pq EffortPQ) Len() int            { return len(pq) }
func (pq EffortPQ) Less(i, j int) bool  { return pq[i].effort < pq[j].effort }
func (pq EffortPQ) Swap(i, j int)       { pq[i], pq[j] = pq[j], pq[i] }
func (pq *EffortPQ) Push(x interface{}) { *pq = append(*pq, x.(EffortItem)) }
func (pq *EffortPQ) Pop() interface{} {
    old := *pq
    n := len(old)
    x := old[n-1]
    *pq = old[:n-1]
    return x
}

func minimumEffortPath(heights [][]int) int {
    rows, cols := len(heights), len(heights[0])
    const inf = 1<<31 - 1
    effort := make([][]int, rows)
    for i := range effort {
        effort[i] = make([]int, cols)
        for j := range effort[i] {
            effort[i][j] = inf
        }
    }
    effort[0][0] = 0

    dirs := [][2]int{{0, 1}, {0, -1}, {1, 0}, {-1, 0}}
    pq := &EffortPQ{{effort: 0, r: 0, c: 0}}
    heap.Init(pq)

    for pq.Len() > 0 {
        curr := heap.Pop(pq).(EffortItem)
        e, r, c := curr.effort, curr.r, curr.c

        if r == rows-1 && c == cols-1 {
            return e
        }
        if e > effort[r][c] {
            continue // stale entry
        }

        for _, d := range dirs {
            nr, nc := r+d[0], c+d[1]
            if nr < 0 || nr >= rows || nc < 0 || nc >= cols {
                continue
            }
            diff := heights[nr][nc] - heights[r][c]
            if diff < 0 {
                diff = -diff
            }
            // New effort: max of current path's max effort and this edge's cost
            newEffort := e
            if diff > newEffort {
                newEffort = diff
            }
            if newEffort < effort[nr][nc] {
                effort[nr][nc] = newEffort
                heap.Push(pq, EffortItem{effort: newEffort, r: nr, c: nc})
            }
        }
    }
    return effort[rows-1][cols-1]
}
```

**Why modified Dijkstra works here:** The "minimax" objective still satisfies the greedy property: when we pop a cell from the min-effort heap, we have found the globally minimum-maximum-effort path to that cell. No future path to it (through higher-effort edges) can improve on that.

**Complexity:** O(m×n × log(m×n)) — same as Dijkstra on a graph with m×n nodes.

## How to Recognize This Pattern

- "Shortest path, minimum cost, cheapest route" with **weighted edges** → Dijkstra (non-negative weights)
- "Shortest path with negative weights" or "shortest path with at most K edges" → Bellman-Ford
- "Minimize the maximum edge weight along a path" (minimax) → Dijkstra with max instead of sum
- "Minimize the total weight along a path" → Dijkstra with sum (standard)
- If BFS worked in the unweighted version → use Dijkstra in the weighted version

The choice between Dijkstra and Bellman-Ford:
- **Dijkstra:** faster (O((V+E)logV)), requires non-negative weights
- **Bellman-Ford:** slower (O(V×E)), handles negative weights, naturally supports "at most k edges" by running exactly k passes

## Key Takeaway

When edges have weights, BFS fails and you need Dijkstra or Bellman-Ford. Dijkstra is the greedy algorithm using a min-heap: always process the globally closest unprocessed node, relax its edges, use lazy deletion to skip stale heap entries. Bellman-Ford is the DP algorithm: iterate V-1 times over all edges, each pass extends paths by one edge — run only k+1 passes to enforce a k-stop constraint. For minimax paths, Dijkstra still works if you replace sum-of-weights with max-of-weights in the relaxation step. The heap always orders by the quantity you are minimizing, whether that is total cost, number of stops, or maximum edge weight along the path.

This completes Phase 2 of the series. We have covered tree traversal, BST operations, tree construction, tree DFS, tree BFS, graph BFS, graph DFS, topological sort, union find, and weighted shortest path — the full trees and graphs toolkit for FAANG+ interviews.

---

**Previous:** [L19: Union Find](/post/fundamentals/interview-union-find/) | **Up next:** Phase 3 — Dynamic Programming
