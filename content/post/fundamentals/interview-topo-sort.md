---
title: "Interview Patterns L18: Topological Sort — Order tasks with dependencies"
author: "Atharva Pandey"
keywords:
  - topological sort
  - course schedule II
  - alien dictionary
  - build order
  - Kahn's algorithm
  - DAG
  - interview patterns
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 18
date: "2025-04-14T00:00:00.000Z"
---

Topological sort comes up whenever there is an ordering constraint — "A must happen before B," "module X depends on module Y," "this course is a prerequisite for that one." The algorithm answers: given a directed acyclic graph (DAG) of dependencies, produce a linear ordering of all nodes such that every node appears after all its predecessors.

The word "topological" makes it sound academic. In practice, it is one of the most industrially relevant algorithms: build systems, package managers, spreadsheet recalculation engines, compiler dependency resolution, task schedulers — they all use topological sort or something equivalent. Interviewers ask it because it tests whether you can model real-world dependency problems as graphs and then solve them correctly.

There are two standard algorithms: DFS-based (the approach from L17 extended with a result stack) and Kahn's algorithm (BFS-based using in-degrees). I teach Kahn's because it is more intuitive, easier to verify correctness on the fly, and naturally detects cycles.

## The Pattern

**Kahn's Algorithm:**
1. Build the adjacency list and compute in-degree for every node
2. Add all nodes with in-degree 0 to a queue (they have no prerequisites, safe to start)
3. While queue is non-empty: dequeue a node, add it to the result, decrement in-degree of all its dependents, add any dependent with in-degree 0 to the queue
4. If the result contains all nodes → valid topological order. If not → cycle exists

In-degree 0 means "nothing needs to come before me." When we remove a node, we satisfy its dependents, potentially freeing them to be processed next.

## Problem 1 [Medium]: Course Schedule II

**Problem:** Return one valid ordering to take all `numCourses` courses given prerequisite pairs `[a, b]` (must take b before a). If impossible (cycle exists), return an empty array.

**Brute force:** Try every permutation, check if it satisfies all prerequisites. O(n!) — completely infeasible.

**Building Kahn's algorithm:**

```go
func findOrder(numCourses int, prerequisites [][]int) []int {
    // Build adjacency list and in-degree array
    graph := make([][]int, numCourses)
    inDegree := make([]int, numCourses)

    for _, pre := range prerequisites {
        course, prereq := pre[0], pre[1]
        graph[prereq] = append(graph[prereq], course)
        inDegree[course]++
    }

    // Seed queue with all nodes that have no prerequisites
    var queue []int
    for i := 0; i < numCourses; i++ {
        if inDegree[i] == 0 {
            queue = append(queue, i)
        }
    }

    var order []int
    for len(queue) > 0 {
        course := queue[0]
        queue = queue[1:]
        order = append(order, course)

        for _, next := range graph[course] {
            inDegree[next]--
            if inDegree[next] == 0 {
                queue = append(queue, next)
            }
        }
    }

    if len(order) == numCourses {
        return order
    }
    return []int{} // cycle detected: some courses never reached in-degree 0
}
```

**Why cycle detection is free:** If a cycle exists, the nodes in the cycle will never reach in-degree 0 (they depend on each other circularly). The BFS will exhaust the queue while those nodes remain unprocessed. `len(order) != numCourses` catches this.

**Why the direction of the edge matters:** `graph[prereq] = append(graph[prereq], course)` means "when prereq is taken, unlock course." The edge goes from prerequisite to dependent. In-degree of `course` is how many prerequisites it has.

**Complexity:** O(V+E) time, O(V+E) space.

## Problem 2 [Hard]: Alien Dictionary

**Problem:** Given a sorted list of words in an alien language's lexicographic order, deduce the order of characters in that language. Return any valid ordering, or "" if there is a contradiction.

**This is topological sort where you first build the graph from the word comparisons.**

**Building the graph:**

Compare adjacent pairs of words. Find the first character where they differ — that difference encodes a constraint: the character in the first word comes before the character in the second word in the alien alphabet.

Edge case: if word1 is a prefix of word2 but longer (`["abc", "ab"]`), the list is invalid — return "".

```go
func alienOrder(words []string) string {
    // Initialize graph with all unique characters
    graph := make(map[byte][]byte)
    inDegree := make(map[byte]int)
    for _, word := range words {
        for i := 0; i < len(word); i++ {
            if _, exists := graph[word[i]]; !exists {
                graph[word[i]] = []byte{}
                inDegree[word[i]] = 0
            }
        }
    }

    // Build edges from adjacent word comparisons
    for i := 0; i < len(words)-1; i++ {
        w1, w2 := words[i], words[i+1]
        minLen := len(w1)
        if len(w2) < minLen {
            minLen = len(w2)
        }
        // Check if w1 is a longer prefix of w2 — invalid ordering
        if len(w1) > len(w2) && w1[:minLen] == w2[:minLen] {
            return ""
        }
        for j := 0; j < minLen; j++ {
            if w1[j] != w2[j] {
                graph[w1[j]] = append(graph[w1[j]], w2[j])
                inDegree[w2[j]]++
                break // only the FIRST difference encodes an ordering constraint
            }
        }
    }

    // Kahn's algorithm on character graph
    var queue []byte
    for ch := range inDegree {
        if inDegree[ch] == 0 {
            queue = append(queue, ch)
        }
    }

    var result []byte
    for len(queue) > 0 {
        ch := queue[0]
        queue = queue[1:]
        result = append(result, ch)
        for _, next := range graph[ch] {
            inDegree[next]--
            if inDegree[next] == 0 {
                queue = append(queue, next)
            }
        }
    }

    if len(result) != len(graph) {
        return "" // cycle in character ordering
    }
    return string(result)
}
```

**Why break after first difference:** Only the first differing character between two adjacent words gives us a valid ordering constraint. Characters after the first difference could appear in any order relative to each other.

**Why the prefix check is necessary:** `["ab", "a"]` would suggest 'a' < 'a' in the alphabet after 'b', which is a contradiction. If word1 is longer than word2 and word2 is a prefix of word1, the sorted order is invalid — return "".

**Complexity:** O(C) where C is total number of characters across all words (for building the graph). Topological sort runs in O(U+E) where U is unique characters.

## Problem 3 [Medium]: Build Order (Bonus Variant)

**Problem:** You have a list of projects and a list of dependency pairs `(a, b)` meaning `b` depends on `a`. Find a build order such that all dependencies are satisfied. If impossible, return an error.

This is Course Schedule II reframed. The Go implementation is structurally identical; only the domain language changes. I include it to make the pattern recognition explicit.

```go
func buildOrder(projects []string, deps [][2]string) ([]string, error) {
    graph := make(map[string][]string)
    inDegree := make(map[string]int)

    for _, p := range projects {
        graph[p] = []string{}
        inDegree[p] = 0
    }

    for _, dep := range deps {
        before, after := dep[0], dep[1]
        graph[before] = append(graph[before], after)
        inDegree[after]++
    }

    var queue []string
    for p := range inDegree {
        if inDegree[p] == 0 {
            queue = append(queue, p)
        }
    }

    var order []string
    for len(queue) > 0 {
        p := queue[0]
        queue = queue[1:]
        order = append(order, p)
        for _, next := range graph[p] {
            inDegree[next]--
            if inDegree[next] == 0 {
                queue = append(queue, next)
            }
        }
    }

    if len(order) != len(projects) {
        return nil, fmt.Errorf("circular dependency detected")
    }
    return order, nil
}
```

**Note the symmetry with Course Schedule II:** Same algorithm, same complexity, same cycle detection. The domain is different (projects vs courses, strings vs ints) but the structure is identical. This is pattern recognition at work — you do not solve a new problem, you apply an existing template.

## How to Recognize This Pattern

- Problem mentions **prerequisites, dependencies, ordering constraints** → topological sort
- Problem asks "in what order should X be done?" → topological sort
- "Is it possible to complete all tasks?" → topological sort + cycle detection (answer: only if the DAG is acyclic)
- "Deduce an alphabet / ordering from comparisons" → build the graph from pairwise comparisons, then topological sort
- The result depends on **some nodes being completed before others** → topological sort

The alien dictionary graph construction is a separate pattern you need to recognize: whenever you are given sorted examples and asked to deduce a rule, compare adjacent examples to extract constraints.

## Key Takeaway

Topological sort via Kahn's algorithm is: build in-degree counts, seed the queue with in-degree-0 nodes, process and decrement, detect cycles by checking whether all nodes were processed. The algorithm is O(V+E) and the cycle detection is free. For dependency deduction problems like alien dictionary, the hard part is building the graph — once you have it, topological sort is mechanical. The common trap is getting edge directions wrong: the edge should go from prerequisite to dependent, not the other way. Spend ten seconds confirming your edge direction before writing the adjacency list construction, and you will avoid the most common bug in this family of problems.

---

**Previous:** [L17: Graph DFS](/post/fundamentals/interview-graph-dfs/) | **Next:** [L19: Union Find — Who belongs to whom?](/post/fundamentals/interview-union-find/)
