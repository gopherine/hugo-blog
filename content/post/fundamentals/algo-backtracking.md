---
title: "Lesson 10: Backtracking — Constraint satisfaction and config generation"
author: "Atharva Pandey"
keywords:
  - backtracking
  - constraint satisfaction
  - configuration generation
  - combinatorics
  - pruning
  - production engineering
tags:
  - fundamentals
  - algorithms
series: "Algorithms That Matter in Production"
lesson: 10
date: "2024-09-02T00:00:00.000Z"
---

Backtracking is the algorithmic equivalent of "try everything, but be smart about giving up early." It is a systematic way to explore a search space where you build a solution incrementally and abandon partial solutions as soon as you detect they cannot possibly lead to a valid result.

I first encountered backtracking outside of textbooks when building a scheduling system that needed to assign employees to shifts while respecting availability constraints, skill requirements, and labor regulations. The brute force approach — enumerate all possible assignments — was computationally equivalent to exploring all permutations, which was 20! for 20 employees. Backtracking with constraint pruning reduced the search space by several orders of magnitude.

## How It Works

The backtracking template:

1. If you have a complete valid solution, record it and return
2. For each possible next choice:
   a. If the choice is valid (does not violate constraints), make it
   b. Recurse
   c. Undo the choice (backtrack)

The "undo" step is what makes it backtracking rather than pure exhaustion. When a partial solution cannot be extended to a valid complete solution, you backtrack to the most recent decision point and try a different choice.

```go
// Generate all valid combinations of k numbers from 1 to n
// Classic backtracking structure
func combine(n, k int) [][]int {
    var result [][]int
    var path []int

    var backtrack func(start int)
    backtrack = func(start int) {
        if len(path) == k {
            combo := make([]int, k)
            copy(combo, path)
            result = append(result, combo)
            return
        }
        // Pruning: if remaining numbers aren't enough to fill path, skip
        remaining := k - len(path)
        for i := start; i <= n-remaining+1; i++ {
            path = append(path, i)
            backtrack(i + 1)
            path = path[:len(path)-1] // undo
        }
    }

    backtrack(1)
    return result
}
```

The pruning on `i <= n-remaining+1` is the key optimization. Without it, we would explore dead ends where there are not enough numbers left to fill the combination.

## When You Need It

Backtracking applies when:

- You need to find all valid configurations satisfying a set of constraints
- You need to determine if any valid configuration exists (decision problem)
- The search space is exponential but constraints prune it significantly
- Greedy and DP do not apply because choices interact in complex ways

Production scenarios where I have seen or used it:

- **Employee scheduling**: assign N employees to M shifts with availability, skill, and hour constraints
- **Feature flag validation**: ensure a set of feature flags does not violate mutual exclusion or dependency rules
- **Test case generation**: generate all valid input combinations for a configuration matrix
- **Resource allocation**: assign tasks to servers with memory, CPU, and affinity constraints
- **Permission system validation**: verify there is at least one valid permission assignment satisfying all role constraints

## Production Example

A feature flag system needed to validate that a proposed set of flag changes was coherent. Flags had dependencies (flag B requires flag A) and conflicts (flag C and flag D cannot both be on). Given a list of flags to enable, find a valid activation order or report that no valid order exists.

```go
type Flag struct {
    Name     string
    Requires []string // must be enabled before this one
    Excludes []string // cannot be on at the same time
}

type FlagState struct {
    enabled map[string]bool
    order   []string
}

func (s *FlagState) canEnable(flag Flag, allFlags map[string]Flag) bool {
    // Check that all required flags are already enabled
    for _, req := range flag.Requires {
        if !s.enabled[req] {
            return false
        }
    }
    // Check that no excluded flags are enabled
    for _, excl := range flag.Excludes {
        if s.enabled[excl] {
            return false
        }
    }
    return true
}

// Find a valid activation order for the given set of flags
func findValidOrder(toEnable []string, allFlags map[string]Flag) ([]string, bool) {
    state := &FlagState{
        enabled: make(map[string]bool),
    }
    inOrder := make(map[string]bool)

    var backtrack func(remaining []string) bool
    backtrack = func(remaining []string) bool {
        if len(remaining) == 0 {
            return true // all flags placed
        }
        for i, name := range remaining {
            flag := allFlags[name]
            if state.canEnable(flag, allFlags) {
                // Make choice: enable this flag next
                state.enabled[name] = true
                state.order = append(state.order, name)
                inOrder[name] = true

                next := make([]string, 0, len(remaining)-1)
                for j, r := range remaining {
                    if j != i {
                        next = append(next, r)
                    }
                }

                if backtrack(next) {
                    return true
                }

                // Undo: backtrack
                state.enabled[name] = false
                state.order = state.order[:len(state.order)-1]
                delete(inOrder, name)
            }
        }
        return false // no valid ordering found
    }

    if backtrack(toEnable) {
        return state.order, true
    }
    return nil, false
}
```

For small flag sets (under 20 flags per change set), this runs in milliseconds even without aggressive pruning. The validation ran at deploy time, catching invalid configurations before they reached production.

A more complex production example: generating all valid test configurations for a compatibility matrix.

```go
// Configuration matrix: each dimension has allowed values
// Constraints: some combinations are invalid
type ConfigDimension struct {
    Name   string
    Values []string
}

type ConfigConstraint struct {
    // Constraint: if DimA=ValA, then DimB cannot be ValB
    DimA, ValA string
    DimB, ValB string
}

func generateValidConfigs(
    dims []ConfigDimension,
    constraints []ConfigConstraint,
) []map[string]string {

    var results []map[string]string
    current := make(map[string]string)

    isValid := func() bool {
        for _, c := range constraints {
            if current[c.DimA] == c.ValA && current[c.DimB] == c.ValB {
                return false
            }
        }
        return true
    }

    var backtrack func(idx int)
    backtrack = func(idx int) {
        if idx == len(dims) {
            config := make(map[string]string, len(current))
            for k, v := range current {
                config[k] = v
            }
            results = append(results, config)
            return
        }
        dim := dims[idx]
        for _, val := range dim.Values {
            current[dim.Name] = val
            if isValid() { // prune if constraint already violated
                backtrack(idx + 1)
            }
        }
        delete(current, dim.Name)
    }

    backtrack(0)
    return results
}
```

The constraint check after each assignment prunes entire subtrees. For a 5-dimension problem with 4 values each (1024 total combinations), if 20% of partial assignments violate constraints, backtracking might only evaluate 200–300 full configurations instead of 1024.

## The Tradeoffs

Backtracking is still exponential in the worst case. Effective pruning is the difference between a practical algorithm and an impractical one. The quality of your pruning determines whether the algorithm completes in milliseconds or takes hours.

For constraint satisfaction problems at scale (thousands of variables), specialized CSP solvers (like those using arc consistency, forward checking, and constraint propagation) significantly outperform naive backtracking. Go does not have a mature CSP library, but OR-Tools (via a Go wrapper) handles these cases.

Recursive backtracking uses O(depth) stack space. For large search depths, iterative implementations with an explicit stack are safer and sometimes faster due to reduced function call overhead.

Also: backtracking does not inherently parallelize well. The search tree's branches are not independent — early decisions affect which branches are valid later. Parallel backtracking requires careful partitioning of the search space and a shared mechanism to stop when a solution is found.

## Key Takeaway

Backtracking is systematic exhaustion with early termination. It works on problems where you build solutions incrementally and can detect infeasibility before completing the solution. The critical optimization is pruning: the more aggressively you detect invalid partial solutions, the more of the search tree you skip. In production, backtracking appears in configuration validation, scheduling with constraints, and test case generation — anywhere you need to enumerate or validate complex combinations.

---

← [Lesson 9: Greedy Algorithms](/post/fundamentals/algo-greedy/) | [Course Index](/post/fundamentals/) | Next: [Lesson 11: String Algorithms](/post/fundamentals/algo-string/) →
