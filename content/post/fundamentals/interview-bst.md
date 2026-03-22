---
title: "Interview Patterns L12: BST Operations — Sorted order hides in every BST"
author: "Atharva Pandey"
keywords:
  - binary search tree
  - validate BST
  - kth smallest element
  - BST iterator
  - inorder traversal
  - interview patterns
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 12
date: "2024-12-29T00:00:00.000Z"
---

BST problems are deceptively simple on the surface. The property is easy to state: every node's left subtree contains only values less than it, every right subtree contains only values greater. You have known this since your data structures course. But FAANG interviewers do not ask you to recite the definition — they probe the edge cases, the constraints that cascade from parent to child rather than just between a node and its immediate children, and the elegant iterator pattern that wraps inorder traversal in an on-demand API.

The unifying insight for this entire lesson is one sentence: **inorder traversal of a BST produces values in sorted ascending order**. Every problem here is a variation on exploiting or validating that property.

## The Pattern

A BST's sorted structure means:
- To validate: you need min/max bounds that propagate, not just local comparison
- To find the kth smallest: inorder traversal and count
- To implement a lazy iterator: simulate inorder traversal with an explicit stack, yielding one element at a time

The mistake I see most often is checking `node.Val > node.Left.Val && node.Val < node.Right.Val` for validation. That passes local checks but misses cases like a right subtree containing a value that is less than an ancestor. The bound-propagation approach is correct and not harder to write once you understand it.

## Problem 1 [Medium]: Validate Binary Search Tree

**Problem:** Given the root of a binary tree, determine if it is a valid BST. A valid BST means: for every node, all nodes in its left subtree are strictly less, all nodes in its right subtree are strictly greater.

**Brute force / wrong approach:** Compare each node only to its direct children. This fails on trees like:

```
    5
   / \
  1   4
     / \
    3   6
```

Node 4's left child 3 is less than 4 (local check passes), but 3 is also less than 5 (the root), which means it is in the right subtree of 5 — invalid. Local checks cannot catch this.

**Building the correct solution — bound propagation:**

Every node inherits a valid range `(min, max)` from its ancestors. The root starts with `(-∞, +∞)`. When we go left, the current node's value becomes the new upper bound. When we go right, the current node's value becomes the new lower bound.

```go
import "math"

type TreeNode struct {
    Val   int
    Left  *TreeNode
    Right *TreeNode
}

func isValidBST(root *TreeNode) bool {
    return validate(root, math.MinInt64, math.MaxInt64)
}

func validate(node *TreeNode, min, max int) bool {
    if node == nil {
        return true
    }
    if node.Val <= min || node.Val >= max {
        return false
    }
    return validate(node.Left, min, node.Val) &&
        validate(node.Right, node.Val, max)
}
```

**Why this is correct:** When we descend left from a node with value `v`, the valid range for all descendants becomes `(min, v)` — they must all be less than `v`. When we descend right, the valid range becomes `(v, max)`. The bounds accumulate the entire history of ancestor constraints.

**Complexity:** O(n) time (visit every node once), O(h) space for the recursion stack.

**Edge case:** The problem says "strictly less" and "strictly greater." Using `<=` and `>=` in the check handles the case where equal values would make the BST invalid (most standard BST definitions forbid duplicates).

## Problem 2 [Medium]: Kth Smallest Element in a BST

**Problem:** Given a BST and an integer k, find the kth smallest value in the tree.

**Brute force:** Do inorder traversal, collect all values into a slice, return `slice[k-1]`. O(n) time, O(n) space. Works, but wastes space and time if k is small.

**Building the optimal solution — early exit:**

Since inorder traversal visits elements in ascending order, we can count as we go and stop as soon as we hit the kth element. No need to store everything.

```go
func kthSmallest(root *TreeNode, k int) int {
    var result int
    var count int
    var inorder func(*TreeNode)
    inorder = func(node *TreeNode) {
        if node == nil || count >= k {
            return
        }
        inorder(node.Left)
        count++
        if count == k {
            result = node.Val
            return
        }
        inorder(node.Right)
    }
    inorder(root)
    return result
}
```

**The iterative version (preferred in interviews):**

```go
func kthSmallestIterative(root *TreeNode, k int) int {
    var stack []*TreeNode
    curr := root

    for curr != nil || len(stack) > 0 {
        for curr != nil {
            stack = append(stack, curr)
            curr = curr.Left
        }
        curr = stack[len(stack)-1]
        stack = stack[:len(stack)-1]
        k--
        if k == 0 {
            return curr.Val
        }
        curr = curr.Right
    }
    return -1 // k larger than tree size, should not happen per constraints
}
```

**Why stop early:** If k is 1, we only traverse to the leftmost node. If the tree has a million nodes and k is 1, the iterative version exits after visiting O(h) nodes instead of O(n). The complexity for the optimal case is O(h + k).

**Follow-up interviewers love to ask:** "If the BST is modified frequently and kthSmallest is called often, how do you optimize?" Answer: augment each node with the size of its left subtree. Then you can binary-search your way to the kth element in O(h) without traversal.

## Problem 3 [Hard]: BST Iterator

**Problem:** Implement a `BSTIterator` class that iterates over a BST in ascending order:
- `BSTIterator(root)` — initializes the iterator
- `Next() int` — returns the next smallest number
- `HasNext() bool` — returns true if there is a next element

**Constraint:** `Next()` and `HasNext()` should run in average O(1) time and use O(h) memory (not O(n)).

**Brute force:** Collect all values inorder into a slice in the constructor. `Next()` pops from the front. O(n) space — violates the constraint.

**Building the O(h) solution — controlled inorder simulation:**

We simulate the iterative inorder traversal but pause after each node is yielded. The stack holds the "pending" path from the current position to the leftmost unvisited node.

```go
type BSTIterator struct {
    stack []*TreeNode
}

func Constructor(root *TreeNode) BSTIterator {
    it := BSTIterator{}
    it.pushLeft(root)
    return it
}

// pushLeft pushes all nodes from node down to its leftmost leaf
func (it *BSTIterator) pushLeft(node *TreeNode) {
    for node != nil {
        it.stack = append(it.stack, node)
        node = node.Left
    }
}

func (it *BSTIterator) Next() int {
    // Pop the top of the stack — this is the next smallest
    node := it.stack[len(it.stack)-1]
    it.stack = it.stack[:len(it.stack)-1]
    // If the node has a right subtree, push its leftmost path
    it.pushLeft(node.Right)
    return node.Val
}

func (it *BSTIterator) HasNext() bool {
    return len(it.stack) > 0
}
```

**Why O(h) space:** The stack at any point holds at most one path from the root to a leaf — that is O(h) nodes. We never store the entire tree.

**Why amortized O(1) per call:** Each node is pushed exactly once and popped exactly once. Across n calls to `Next()`, total work is O(n), giving amortized O(1) per call. A single call might do O(h) work if it triggers a long `pushLeft`, but averaged over all calls, it is O(1).

**Where this pattern appears beyond interviews:** This iterator design is how databases implement index range scans lazily. They maintain a cursor position and advance it on demand, which is exactly what `pushLeft` and the stack accomplish here.

## How to Recognize This Pattern

- Problem involves a BST and asks about **ordering, ranking, or range** — inorder is almost certainly the tool
- Problem asks **"is this a valid BST?"** — bound propagation, not local comparison
- Problem needs **lazy streaming** of sorted elements — the BST iterator pattern with explicit stack
- Problem asks about the **kth element** in a BST — inorder with early termination
- Follow-up asks about **frequent modifications + frequent queries** — augmented BST with subtree sizes

The red flag that tells you local comparison is wrong: when the problem says "for all nodes in the subtree" rather than "for the immediate children." Whenever you hear "all nodes," you need propagating bounds.

## Key Takeaway

BST problems are inorder problems in disguise. The sorted property is the entire game. For validation, the property must hold globally — use bound propagation, not local checks. For ranking, inorder gives you elements in sorted order and early exit saves work. For streaming, the iterative inorder pattern is a lazy iterator with O(h) overhead. These three problems represent the canonical BST interview questions and they share exactly one underlying idea: inorder traversal of a BST is sorted order, and everything follows from there.

---

**Previous:** [L11: Binary Tree Traversal](/post/fundamentals/interview-tree-traversal/) | **Next:** [L13: Tree Construction — Build trees from traversal arrays](/post/fundamentals/interview-tree-construction/)
