---
title: "Lesson 26: DP on Trees — Post-order traversal meets memoization"
author: "Atharva Pandey"
keywords:
  - dynamic programming
  - tree DP
  - house robber III
  - binary tree maximum path sum
  - longest path different adjacent values
  - post-order traversal
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 26
date: "2025-08-20T00:00:00.000Z"
---

Tree DP is the pattern that catches people by surprise. You've been thinking of DP as filling a 1D or 2D table from left to right — a sequential, iterative process. Trees are recursive by nature. The "table" is implicit in the call stack.

The key insight: tree DP is just post-order traversal where each node computes its answer from its children's answers. There's no explicit table. The memoization (if you need it) is keyed by node pointer. The bottom-up order is inherently satisfied because post-order visits children before parents.

This changes the implementation style. Instead of a loop filling a dp array, you write a recursive function that returns one or more values up to the parent. The parent aggregates those values to compute its own answer.

## The Pattern

Tree DP follows this shape:

1. Write a recursive function `solve(node)` that computes what the current node contributes to the answer.
2. `solve` calls itself on the left and right children first (post-order).
3. The current node combines the children's results, computes its own answer, and returns up.
4. Often, the function needs to return multiple values: "the best answer if I include this node" and "the best answer if I exclude this node."

The second return value is where tree DP diverges from simple tree recursion. When decisions at one node constrain parent nodes (like in House Robber III), you must bubble up both options.

---

## Problem 1: House Robber III

**LeetCode 337.** The thief has found himself a new place — a binary tree of houses. Each node has a value. Adjacent nodes cannot both be robbed (no node and its direct children simultaneously). Return the maximum amount that can be robbed.

### Define the subproblem

For each node, we need two values:
- `rob`: maximum money if we rob this node (so we cannot rob its children).
- `skip`: maximum money if we skip this node (children are free to rob or skip).

### Recurrence

```
rob(node)  = node.Val + skip(left) + skip(right)
skip(node) = max(rob(left), skip(left)) + max(rob(right), skip(right))
```

The answer at the root is `max(rob(root), skip(root))`.

### Build bottom-up (post-order)

```go
type TreeNode struct {
    Val   int
    Left  *TreeNode
    Right *TreeNode
}

func rob(root *TreeNode) int {
    robVal, skipVal := dfs(root)
    if robVal > skipVal {
        return robVal
    }
    return skipVal
}

// returns (robThisNode, skipThisNode)
func dfs(node *TreeNode) (int, int) {
    if node == nil {
        return 0, 0
    }
    leftRob, leftSkip := dfs(node.Left)
    rightRob, rightSkip := dfs(node.Right)

    robThis := node.Val + leftSkip + rightSkip
    skipThis := max(leftRob, leftSkip) + max(rightRob, rightSkip)
    return robThis, skipThis
}

func max(a, b int) int {
    if a > b {
        return a
    }
    return b
}
```

O(n) time, O(h) space where h is the tree height. No memoization map needed because each node is visited exactly once.

**Why this works without a memo table:** in a tree, there's no overlapping subproblems between branches. Each subtree is disjoint from every other subtree. The DP structure is purely "parent depends on children," which post-order recursion handles exactly.

---

## Problem 2: Binary Tree Maximum Path Sum

**LeetCode 124.** A path in a binary tree is a sequence of nodes where each pair of adjacent nodes has an edge between them. A node can appear in the path at most once. The path does not need to pass through the root. Return the maximum path sum of any non-empty path.

This problem is harder because a path can "bend" through a node — it can include both the left and right subtrees of a node. But the path you report upward to the parent can only go in one direction (you can't bend twice).

### Define the subproblem

For each node, compute:
- The maximum "gain" that can be extended upward: either go left, go right, or just take the node itself (whichever is highest). Negative gains are dropped (take 0 instead).
- The maximum path sum passing through this node as the bend point: `node.Val + leftGain + rightGain`.

### Recurrence

```
leftGain  = max(0, maxGain(left))
rightGain = max(0, maxGain(right))

pathThroughNode = node.Val + leftGain + rightGain
global_max = max(global_max, pathThroughNode)

return node.Val + max(leftGain, rightGain)  // can only extend in one direction
```

### Build bottom-up

```go
func maxPathSum(root *TreeNode) int {
    globalMax := -1 << 31 // minimum int
    var dfsPath func(node *TreeNode) int
    dfsPath = func(node *TreeNode) int {
        if node == nil {
            return 0
        }
        leftGain := dfsPath(node.Left)
        rightGain := dfsPath(node.Right)
        if leftGain < 0 {
            leftGain = 0
        }
        if rightGain < 0 {
            rightGain = 0
        }

        pathSum := node.Val + leftGain + rightGain
        if pathSum > globalMax {
            globalMax = pathSum
        }

        // return max gain going in one direction
        if leftGain > rightGain {
            return node.Val + leftGain
        }
        return node.Val + rightGain
    }
    dfsPath(root)
    return globalMax
}
```

The `globalMax` variable accumulates the best answer seen across all nodes. This is a common pattern in tree DP: the recursive function returns what the parent needs to continue the path, but the actual answer is tracked in a side variable updated during the traversal.

**Why the return value isn't the answer:** the function returns the best single-direction gain because the parent can only attach to one arm. The two-arm (bend) configuration is evaluated at each node and stored in `globalMax`, but it cannot be extended further.

---

## Problem 3: Longest Path with Different Adjacent Values

**LeetCode 2246.** Given a tree with n nodes (not binary, any number of children) and an array of values, return the longest path where each consecutive pair of nodes has different values.

### Define the subproblem

For each node, compute the longest valid downward path starting from this node. Similar to the max path sum problem, the "bend" through a node can combine its two longest valid child-paths.

For each node u:
- `longest1` = length of the longest valid path going down through one child.
- `longest2` = length of the second longest valid path going down through a different child.
- A path bending through u has length `longest1 + longest2 + 1`.
- The function returns the longest single-arm path for the parent to extend.

### Recurrence

```
for each child v of u:
    childLen = longestPath(v)
    if value[v] != value[u]:
        extend this arm by 1
    else:
        this arm is broken (cannot extend through here)

track longest1 and longest2 arms
global_max = max(global_max, longest1 + longest2 + 1)
return longest1 + 1
```

### Build bottom-up

```go
func longestPath(parent []int, s string) int {
    n := len(s)
    children := make([][]int, n)
    for i := 1; i < n; i++ {
        children[parent[i]] = append(children[parent[i]], i)
    }

    globalMax := 1
    var dfsLP func(u int) int
    dfsLP = func(u int) int {
        longest1, longest2 := 0, 0
        for _, v := range children[u] {
            childLen := dfsLP(v)
            if s[v] != s[u] {
                if childLen > longest1 {
                    longest2 = longest1
                    longest1 = childLen
                } else if childLen > longest2 {
                    longest2 = childLen
                }
            }
        }
        pathLen := longest1 + longest2 + 1
        if pathLen > globalMax {
            globalMax = pathLen
        }
        return longest1 + 1
    }
    dfsLP(0)
    return globalMax
}
```

O(n) time. The pattern of tracking top-2 child contributions to find the best "bend" at each node is reusable across many tree DP problems.

---

## How to Recognize This Pattern

- The problem is on a tree and asks for something about paths, subtrees, or node selections.
- The answer at a node depends on the answers of its children (classic post-order structure).
- You need to make a decision at each node that may constrain the parent (rob/skip, which arm to extend).
- The function might need to return multiple values: what the parent needs to continue, and what gets recorded as a potential answer.

When you see "adjacent nodes" constraints, think House Robber III. When you see "path" problems on trees, think about the bend: report a single arm upward, track both arms as a candidate answer.

---

## Key Takeaway

Tree DP = post-order DFS + multiple return values. There's no explicit table. The children's results are the "previous states," and the current node combines them.

The most important skill is designing the right return type. Ask: "what does the parent need to know about this subtree?" That's what you return. "What should I track as a global answer?" That lives in a closure variable or a pointer.

Post-order traversal inherently gives you bottom-up order for free. You never need to worry about fill order on trees.

---

*Previous: [Lesson 25: DP on Strings](/post/fundamentals/interview-dp-strings/) · Next: [Lesson 27: Knapsack Patterns](/post/fundamentals/interview-dp-knapsack/)*
