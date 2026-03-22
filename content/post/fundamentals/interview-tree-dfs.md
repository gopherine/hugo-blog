---
title: "Interview Patterns L14: Tree DFS Patterns — Every path question is DFS"
author: "Atharva Pandey"
keywords:
  - tree DFS
  - maximum depth
  - path sum
  - lowest common ancestor
  - diameter of binary tree
  - interview patterns
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 14
date: "2025-01-30T00:00:00.000Z"
---

If a binary tree problem asks anything about paths — longest, shortest, sum along a path, common ancestor between two nodes — the solution is almost certainly DFS. Not because DFS is the only way, but because path problems require you to propagate information up from leaves to ancestors, and that is exactly what post-order DFS does. You compute the answer for children before combining it for the parent.

What I find interesting about this cluster of problems is how they reveal a single reusable DFS skeleton. Once you internalize the pattern — recurse left, recurse right, combine and return — you can adapt it to maximum depth, path sum, diameter, and LCA with only surface-level changes. The thinking cost drops dramatically once you stop treating each problem as novel and start asking "what do I return from each recursive call, and how do I combine it?"

## The Pattern

The core DFS skeleton for tree problems:

```go
func dfs(node *TreeNode) ReturnType {
    if node == nil {
        return baseCase
    }
    left := dfs(node.Left)
    right := dfs(node.Right)
    // Combine left, right, and current node's value
    // Update a global answer if needed
    return valueForParent
}
```

Two modes:
1. **Return-only:** the function returns what the parent needs; the answer is the return value of the root call
2. **Return + global:** the function returns what the parent needs, but also updates a global variable when the "interesting" combination is at the current node (e.g., a path passing through this node, not just below it)

Diameter and Path Sum II use the second mode. Maximum Depth and LCA use the first.

## Problem 1 [Easy]: Maximum Depth of Binary Tree

**Problem:** Find the maximum depth (number of nodes along the longest path from root to a leaf).

**Brute force:** This is already optimal. There is no way to determine the depth without visiting every node.

**Building the solution:**

Depth of a node = 1 + max(depth of left child, depth of right child). Base case: null node has depth 0.

```go
type TreeNode struct {
    Val   int
    Left  *TreeNode
    Right *TreeNode
}

func maxDepth(root *TreeNode) int {
    if root == nil {
        return 0
    }
    leftDepth := maxDepth(root.Left)
    rightDepth := maxDepth(root.Right)
    if leftDepth > rightDepth {
        return leftDepth + 1
    }
    return rightDepth + 1
}
```

**Why this is the right mental model:** Each call asks "what is the deepest I can go from this node?" Null means 0. A leaf means 1 (it adds 1 to the 0 from both null children). The root adds 1 to the deeper of its two subtrees.

**Complexity:** O(n) time (every node visited), O(h) space (recursion depth equals tree height).

This problem seems trivial but it is the foundation. Every harder problem in this lesson uses the same "return information up to the parent" structure.

## Problem 2 [Medium]: Path Sum II

**Problem:** Given a binary tree and a target sum, find all root-to-leaf paths where the path's node values sum to target. Return a list of all such paths.

**Brute force thinking:** Generate all root-to-leaf paths, compute each sum, filter by target. Works but builds all paths before filtering.

**Building the DFS + backtracking solution:**

We do a preorder DFS (visit current node before children), maintaining the current path. At each leaf, we check if the remaining target equals the leaf's value. If so, we found a valid path.

Critical: we backtrack the path after returning from a subtree.

```go
func pathSum(root *TreeNode, targetSum int) [][]int {
    var result [][]int
    var path []int

    var dfs func(node *TreeNode, remaining int)
    dfs = func(node *TreeNode, remaining int) {
        if node == nil {
            return
        }
        path = append(path, node.Val)
        remaining -= node.Val

        // Leaf check: no children and remaining sum is 0
        if node.Left == nil && node.Right == nil && remaining == 0 {
            // Append a copy of path, not path itself
            tmp := make([]int, len(path))
            copy(tmp, path)
            result = append(result, tmp)
        }

        dfs(node.Left, remaining)
        dfs(node.Right, remaining)

        // Backtrack: remove current node from path
        path = path[:len(path)-1]
    }

    dfs(root, targetSum)
    return result
}
```

**Why copy before appending to result:** Go slices share underlying arrays. If you append `path` directly, all result entries will point to the same backing array and will all end up containing the last state of `path`. Always copy.

**Why the leaf check uses `node.Left == nil && node.Right == nil`:** A path must end at a leaf. Without this check, you would count partial paths (ending at a non-leaf) if the partial sum matches.

**Complexity:** O(n) time to visit every node. Space: O(n) for results in the worst case (a skewed tree where every root-to-leaf path is valid), O(h) for the recursion stack.

## Problem 3 [Hard]: Lowest Common Ancestor (LCA)

**Problem:** Given a binary tree and two nodes `p` and `q`, find their lowest common ancestor — the deepest node that has both `p` and `q` as descendants (a node is a descendant of itself).

**Brute force:** For each node, collect all descendants and check if both p and q are in the set. O(n²) time.

**Building the elegant DFS solution:**

Key insight: when searching for `p` and `q`, three cases cover all possibilities:
1. `p` is in left subtree, `q` is in right subtree (or vice versa) → current node is LCA
2. Current node is `p` or `q` → current node is LCA (since the other must be in its subtree per problem guarantee)
3. Both are in the same subtree → recurse into that subtree

The DFS returns non-nil when it finds p or q. The first node where both left and right recursion return non-nil is the LCA.

```go
func lowestCommonAncestor(root, p, q *TreeNode) *TreeNode {
    if root == nil {
        return nil
    }
    // If current node is p or q, it is (or contains) the LCA
    if root == p || root == q {
        return root
    }

    left := lowestCommonAncestor(root.Left, p, q)
    right := lowestCommonAncestor(root.Right, p, q)

    // Both sides found something: this node is the split point = LCA
    if left != nil && right != nil {
        return root
    }
    // One side found both nodes (they're in the same subtree)
    if left != nil {
        return left
    }
    return right
}
```

**Tracing through an example:**
```
        3
       / \
      5   1
     / \
    6   2
```
LCA of 6 and 2:
- At node 3: recurse left and right
- At node 1: both children return nil → return nil
- At node 5: left finds 6, right finds 2 → both non-nil → return node 5
- Node 3 gets left=node5, right=nil → returns node5

**Why this terminates cleanly:** The guarantee that p and q exist in the tree means we will always find the LCA. If one of them did not exist, we would need additional tracking.

**Complexity:** O(n) time, O(h) space.

## Problem 4 [Hard]: Diameter of Binary Tree

**Problem:** The diameter is the length of the longest path between any two nodes (number of edges). The path does not have to pass through the root.

**Key insight:** The diameter through any node = depth of left subtree + depth of right subtree. We want the maximum across all nodes.

**This is a "return + global" pattern problem:** The function returns depth (what the parent needs), but updates a global maximum with the diameter at the current node.

```go
func diameterOfBinaryTree(root *TreeNode) int {
    maxDiameter := 0

    var depth func(*TreeNode) int
    depth = func(node *TreeNode) int {
        if node == nil {
            return 0
        }
        leftDepth := depth(node.Left)
        rightDepth := depth(node.Right)

        // Diameter through this node: edges going left + edges going right
        diameterHere := leftDepth + rightDepth
        if diameterHere > maxDiameter {
            maxDiameter = diameterHere
        }

        // Return depth to parent: max depth of either subtree + 1 for this node
        if leftDepth > rightDepth {
            return leftDepth + 1
        }
        return rightDepth + 1
    }

    depth(root)
    return maxDiameter
}
```

**Why the diameter passes through the current node but depth goes up:** The parent needs to know "how deep can I go through this child?" — that is `max(leftDepth, rightDepth) + 1`. But the diameter for the current node uses both subtrees simultaneously, so it is `leftDepth + rightDepth`. These are two different questions and the DFS answers both in one pass.

**Complexity:** O(n) time, O(h) space. One pass through the tree.

## How to Recognize This Pattern

- "Find the longest/shortest path" → DFS, the path likely passes through a node (return + global)
- "Find all paths from root to leaf" → preorder DFS with backtracking
- "Find common ancestor" → postorder DFS, return non-nil signals where each node was found
- "Maximum/minimum depth" → postorder DFS, return the combined depth
- The path does NOT have to go through the root → you almost certainly need the "return + global" variant

The signal for "return + global" is the phrase **"any two nodes"** or **"any path"** rather than "root to leaf path." When the path can start and end anywhere, the interesting combination happens at the current node, not at the root.

## Key Takeaway

Path problems on trees are DFS problems. The pattern is: recurse to both children, combine their results, return what the parent needs. Most depth and path problems use postorder DFS (children before parent) because you need child information to compute the parent's answer. When the optimal solution passes through an arbitrary node (not just root-to-leaf), you need the "return + global" variant: return depth upward for the parent's use, while updating the global max/min using both subtrees at once. Once you have the skeleton memorized and understand these two modes, you can solve most tree path problems by filling in the specific combination logic.

---

**Previous:** [L13: Tree Construction](/post/fundamentals/interview-tree-construction/) | **Next:** [L15: Tree BFS Patterns — Level by level reveals structure](/post/fundamentals/interview-tree-bfs/)
