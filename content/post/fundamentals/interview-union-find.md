---
title: "Interview Patterns L19: Union Find — Who belongs to whom?"
author: "Atharva Pandey"
keywords:
  - union find
  - disjoint set union
  - DSU
  - number of connected components
  - redundant connection
  - accounts merge
  - interview patterns
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 19
date: "2025-04-30T00:00:00.000Z"
---

Union Find is one of those data structures that most people never implement until they need it in an interview, and then they discover it is both elegant and surprisingly short. The core idea is deceptively simple: maintain a "parent" array where each element points to its group's representative. Two operations — `Find` (who is the root of this group?) and `Union` (merge two groups) — are all you need.

What makes Union Find powerful compared to BFS/DFS for connectivity questions is its incremental nature. You can add edges one at a time and answer "are these two nodes connected?" in nearly O(1) time. BFS/DFS would need to re-traverse the graph for each query. For problems where edges are added dynamically and you need real-time connectivity answers, Union Find is the only reasonable tool.

The three problems here — counting components, detecting redundant edges (cycle detection), and merging accounts — demonstrate the three most common Union Find interview patterns.

## The Pattern

The complete Union Find implementation with path compression and union by rank:

```go
type UnionFind struct {
    parent []int
    rank   []int
    count  int // number of connected components
}

func NewUnionFind(n int) *UnionFind {
    parent := make([]int, n)
    rank := make([]int, n)
    for i := range parent {
        parent[i] = i // each node is its own parent initially
    }
    return &UnionFind{parent: parent, rank: rank, count: n}
}

// Find returns the root representative of x's component
// Path compression: make every node point directly to root
func (uf *UnionFind) Find(x int) int {
    if uf.parent[x] != x {
        uf.parent[x] = uf.Find(uf.parent[x]) // path compression
    }
    return uf.parent[x]
}

// Union merges the components containing x and y
// Returns true if they were in different components (a new merge happened)
func (uf *UnionFind) Union(x, y int) bool {
    px, py := uf.Find(x), uf.Find(y)
    if px == py {
        return false // already in the same component
    }
    // Union by rank: attach smaller tree under larger tree
    if uf.rank[px] < uf.rank[py] {
        uf.parent[px] = py
    } else if uf.rank[px] > uf.rank[py] {
        uf.parent[py] = px
    } else {
        uf.parent[py] = px
        uf.rank[px]++
    }
    uf.count--
    return true
}
```

With path compression and union by rank, `Find` and `Union` both run in amortized O(α(n)) time — practically O(1) for any real-world input size.

## Problem 1 [Easy-Medium]: Number of Connected Components in an Undirected Graph

**Problem:** Given `n` nodes (0 to n-1) and a list of edges, return the number of connected components.

**Brute force:** BFS/DFS from each unvisited node, count how many times you start. O(V+E). Totally valid.

**Building the Union Find solution:**

Start with n components (each node is its own component). For each edge, union the two endpoints. If they were already in the same component, the union does nothing. After processing all edges, `uf.count` is the answer.

```go
func countComponents(n int, edges [][]int) int {
    uf := NewUnionFind(n)
    for _, edge := range edges {
        uf.Union(edge[0], edge[1])
    }
    return uf.count
}
```

That is the entire solution. Three lines of logic on top of the Union Find struct.

**Why Union Find over BFS here:** For this specific problem, BFS and Union Find have the same asymptotic complexity. The Union Find solution is shorter and sets you up for the follow-up: "What if edges are added one at a time and you need to report component count after each addition?" With BFS you would re-traverse the graph each time. With Union Find you do `union` + `return uf.count` per edge — O(α(n)) per query.

**Complexity:** O(n + E × α(n)) which is effectively O(n + E).

## Problem 2 [Medium]: Redundant Connection

**Problem:** A tree of n nodes has had one extra edge added to it, creating exactly one cycle. Find the edge that, when removed, makes the graph a tree. If multiple answers exist, return the edge that appears last in the input.

**Brute force:** Try removing each edge, check if the remaining graph is a tree. O(E × (V+E)).

**Building the Union Find solution:**

Process edges in order. When we try to union two nodes that are already in the same component, we have found the redundant edge — it created the cycle.

```go
func findRedundantConnection(edges [][]int) []int {
    n := len(edges) // n nodes, numbered 1 to n
    uf := NewUnionFind(n + 1) // +1 for 1-indexed nodes

    for _, edge := range edges {
        if !uf.Union(edge[0], edge[1]) {
            // Union returned false: they were already connected → this edge is redundant
            return edge
        }
    }
    return nil
}
```

**Why this finds the last redundant edge automatically:** We process edges in order. The first edge that fails to merge two new components (returns false from Union) is the one that closes a cycle. Since we process left to right, if there are multiple such edges, we return the one that appears last in the input — which matches the problem requirement.

**Why this is cycle detection:** In a tree, every edge connects two previously disconnected components. Adding n-1 such edges to n nodes produces a spanning tree with no cycles. The nth edge (our extra edge) must connect two nodes already in the same component — that is a cycle.

**Complexity:** O(E × α(n)) effectively O(E).

## Problem 3 [Hard]: Accounts Merge

**Problem:** Each account is a list where the first element is a name, followed by email addresses. Two accounts belong to the same person if they share at least one common email. Merge all accounts belonging to the same person. Return the merged accounts sorted by email within each account.

**This is Union Find over emails, not integers.**

**Why this is hard:** The union key is strings (emails), not integers. Accounts that need to be merged might not share emails directly — they can be transitively connected through shared intermediate accounts. You need to handle the mapping from emails to account owners.

**Building the solution:**

Two passes:
1. For each account, union all its emails together (and map each email to the account owner's name)
2. Group emails by their root representative, sort each group, prepend the name

```go
import "sort"

// String-keyed Union Find
type EmailUF struct {
    parent map[string]string
}

func NewEmailUF() *EmailUF {
    return &EmailUF{parent: make(map[string]string)}
}

func (uf *EmailUF) Find(x string) string {
    if _, exists := uf.parent[x]; !exists {
        uf.parent[x] = x
    }
    if uf.parent[x] != x {
        uf.parent[x] = uf.Find(uf.parent[x])
    }
    return uf.parent[x]
}

func (uf *EmailUF) Union(x, y string) {
    px, py := uf.Find(x), uf.Find(y)
    if px != py {
        uf.parent[px] = py
    }
}

func accountsMerge(accounts [][]string) [][]string {
    uf := NewEmailUF()
    emailToName := make(map[string]string)

    // Pass 1: union all emails within the same account
    for _, account := range accounts {
        name := account[0]
        for i := 1; i < len(account); i++ {
            email := account[i]
            emailToName[email] = name
            // Union all emails in this account with the first email
            uf.Union(account[1], email)
        }
    }

    // Pass 2: group emails by root representative
    rootToEmails := make(map[string][]string)
    for email := range emailToName {
        root := uf.Find(email)
        rootToEmails[root] = append(rootToEmails[root], email)
    }

    // Build result: sort emails within each group, prepend name
    var result [][]string
    for root, emails := range rootToEmails {
        sort.Strings(emails)
        account := append([]string{emailToName[root]}, emails...)
        result = append(result, account)
    }
    return result
}
```

**Why union with `account[1]` (first email) rather than with each consecutive pair:** Unioning every email with the first email guarantees all emails in the account end up in the same component. Unioning consecutive pairs would also work (transitivity), but unioning with the first is simpler to reason about.

**Why path compression still works with string keys:** The string map version of Find uses the same recursive path compression logic — we just use strings as keys instead of array indices.

**Complexity:** O(E log E) where E is the total number of emails — dominated by the sort step. The Union Find operations are nearly O(E).

## How to Recognize This Pattern

- "How many connected components?" → Union Find (or BFS/DFS; UF is better if edges are added dynamically)
- "Does adding this edge create a cycle?" → Union Find: if both endpoints have the same root, adding the edge creates a cycle
- "Merge groups that share a common member" → Union Find over the member identifiers
- "Group elements by transitive relationships" → Union Find
- The key clue: **"connected"**, **"same group"**, **"belong together"**, **"reachable from each other"** — all suggest Union Find

Union Find vs BFS/DFS: use Union Find when you need **incremental connectivity updates** or when the problem structure maps naturally to "union two groups, then query." Use BFS/DFS when you need to find **paths** or when the graph is static and you need to visit nodes in a specific order.

## Key Takeaway

Union Find maintains group membership via a parent array. `Find` returns the root representative (with path compression so it flattens the tree). `Union` merges two groups (with union by rank so the tree stays balanced). With both optimizations, every operation runs in amortized O(α(n)) — practically constant. The three problems here show the three ways UF appears in interviews: counting components (trivial wrapper), cycle detection (redundant edge is the one where both endpoints already share a root), and merging records with shared identifiers (string-keyed UF). Get the base implementation memorized cold — it is short enough to write in under two minutes — and the problem-specific logic is always just a few lines on top.

---

**Previous:** [L18: Topological Sort](/post/fundamentals/interview-topo-sort/) | **Next:** [L20: Shortest Path — When edges have weights](/post/fundamentals/interview-shortest-path/)
