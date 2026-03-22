---
title: "Interview Patterns L11: Binary Tree Traversal — Four ways to walk a tree, four different answers"
author: "Atharva Pandey"
keywords:
  - binary tree traversal
  - inorder iterative
  - level order traversal
  - zigzag traversal
  - BFS tree
  - DFS tree
  - interview patterns
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 11
date: "2024-12-17T00:00:00.000Z"
---

The moment an interviewer draws a binary tree on the whiteboard, a clock starts. They are not just testing whether you know what inorder means. They are watching how you think about state, iteration, and the relationship between recursive and iterative logic. Four traversal orders — preorder, inorder, postorder, level order — each reveals something different about the tree. Knowing which one to reach for, and being able to implement it iteratively without hesitation, is what separates candidates who get offers from candidates who get "we'll be in touch."

I have failed to implement iterative inorder cleanly under pressure before. It felt embarrassing because I knew the recursive version in my sleep. The lesson I took from that was this: recursive is how you think about trees, iterative is how you show you understand the call stack. This lesson covers the traversals that come up most in interviews and the ones where the iterative version is the real test.

## The Pattern

Tree traversal is about controlling the order in which you visit nodes. Recursion handles the order implicitly via the call stack. When you go iterative, you make that stack explicit. The critical insight is that **a recursive DFS function is just a loop plus a stack**. Once you internalize that, iterative traversal becomes mechanical.

Level order (BFS) uses a queue instead of a stack and processes nodes layer by layer. Zigzag is level order with a direction toggle.

The three problems below build your intuition from the ground up.

## Problem 1 [Easy-Medium]: Binary Tree Inorder Traversal (Iterative)

**Problem:** Given the root of a binary tree, return the inorder traversal of its nodes' values **without using recursion**.

Inorder means: left subtree → current node → right subtree. For a BST, this yields sorted order — which is why inorder is asked constantly.

**Brute force — recursion:** Everyone can write this. It works, but it is not what the interviewer wants when they say "now do it iteratively."

```go
func inorderRecursive(root *TreeNode) []int {
    var result []int
    var dfs func(*TreeNode)
    dfs = func(node *TreeNode) {
        if node == nil {
            return
        }
        dfs(node.Left)
        result = append(result, node.Val)
        dfs(node.Right)
    }
    dfs(root)
    return result
}
```

**Building the iterative version step by step:**

The recursion pushes the current node onto the call stack, goes left, processes current, then goes right. We replicate this with an explicit stack.

Key insight: we want to go as far left as possible, pushing nodes as we go. When we can go no further left, we pop, record the value, then pivot to the right subtree and repeat.

```go
type TreeNode struct {
    Val   int
    Left  *TreeNode
    Right *TreeNode
}

func inorderIterative(root *TreeNode) []int {
    var result []int
    var stack []*TreeNode
    curr := root

    for curr != nil || len(stack) > 0 {
        // Phase 1: go as far left as possible, pushing along the way
        for curr != nil {
            stack = append(stack, curr)
            curr = curr.Left
        }
        // Phase 2: pop, record, pivot right
        curr = stack[len(stack)-1]
        stack = stack[:len(stack)-1]
        result = append(result, curr.Val)
        curr = curr.Right
    }
    return result
}
```

**Why it works:** The outer loop runs as long as there is either an unprocessed node in hand (`curr != nil`) or nodes waiting on the stack. The inner loop drills left. When the inner loop exhausts, we unwind one level, record the value, and restart the process for the right subtree. This exactly mirrors the call-stack behavior of the recursive version.

**Complexity:** O(n) time, O(h) space where h is tree height (O(log n) balanced, O(n) worst case skewed).

## Problem 2 [Medium]: Binary Tree Level Order Traversal

**Problem:** Given the root of a binary tree, return the level order traversal of its nodes' values as `[][]int` — each inner slice is one level.

**Brute force thinking:** You could use DFS and pass a depth parameter, appending values to the correct index in a slice-of-slices. It works but feels unnatural. The natural tool for level order is BFS with a queue.

**Building the BFS solution:**

The trick that transforms basic BFS into "level-aware" BFS is processing the queue in batches. Before starting each batch, capture the current queue length. That length is exactly the number of nodes on the current level.

```go
func levelOrder(root *TreeNode) [][]int {
    if root == nil {
        return nil
    }
    var result [][]int
    queue := []*TreeNode{root}

    for len(queue) > 0 {
        levelSize := len(queue) // snapshot: nodes at this level
        level := make([]int, 0, levelSize)

        for i := 0; i < levelSize; i++ {
            node := queue[0]
            queue = queue[1:]
            level = append(level, node.Val)
            if node.Left != nil {
                queue = append(queue, node.Left)
            }
            if node.Right != nil {
                queue = append(queue, node.Right)
            }
        }
        result = append(result, level)
    }
    return result
}
```

**Why capturing `levelSize` matters:** Without the snapshot, newly added children would merge with the current level's processing. By fixing the batch size upfront, we guarantee we only process the nodes that were in the queue at the start of this level.

**Complexity:** O(n) time, O(n) space (the queue holds at most the widest level, which for a complete binary tree is n/2 nodes).

## Problem 3 [Hard]: Binary Tree Zigzag Level Order Traversal

**Problem:** Return the zigzag level order traversal: level 0 left-to-right, level 1 right-to-left, level 2 left-to-right, alternating.

**Brute force:** Do normal level order and reverse every other level's slice. It works. O(n) time. But is there a cleaner way?

**Building the optimal solution:**

One approach is to use a deque (double-ended queue) and append to the front or back depending on direction. Go does not have a built-in deque, but we can simulate it or simply use the direction flag to reverse the collected level before appending.

The cleanest interview solution: do normal BFS but toggle a `leftToRight` bool. When `leftToRight` is false, reverse the level slice before appending it to results.

```go
func zigzagLevelOrder(root *TreeNode) [][]int {
    if root == nil {
        return nil
    }
    var result [][]int
    queue := []*TreeNode{root}
    leftToRight := true

    for len(queue) > 0 {
        levelSize := len(queue)
        level := make([]int, levelSize)

        for i := 0; i < levelSize; i++ {
            node := queue[0]
            queue = queue[1:]

            // Place value at correct position based on direction
            if leftToRight {
                level[i] = node.Val
            } else {
                level[levelSize-1-i] = node.Val
            }

            if node.Left != nil {
                queue = append(queue, node.Left)
            }
            if node.Right != nil {
                queue = append(queue, node.Right)
            }
        }
        result = append(result, level)
        leftToRight = !leftToRight
    }
    return result
}
```

**Why pre-allocate with `make([]int, levelSize)`:** We know the level's size before we start filling it. Allocating with that size lets us write directly to index `i` or `levelSize-1-i` without any post-processing reversal. The direction toggle is free.

**Complexity:** O(n) time, O(n) space. Same as level order — we just write differently into the pre-allocated level slice.

## How to Recognize This Pattern

Ask yourself these questions when you see a binary tree problem:

1. **Does the answer depend on order within a level?** — BFS (level order or zigzag)
2. **Does the answer require seeing a node after both children?** — Postorder DFS
3. **Does the answer require sorted values (BST context)?** — Inorder
4. **Does the answer build up from root toward leaves?** — Preorder DFS
5. **Interviewer says "no recursion"?** — Explicit stack mirroring the recursive version

The dead giveaway for zigzag specifically is any mention of alternating direction, S-shaped traversal, or snake pattern.

Level order also appears disguised as: "group nodes by depth," "find all nodes at distance k from root," "average of each level," "minimum depth." All of these decompose to BFS with the level-size snapshot trick.

## Key Takeaway

Recursive tree traversal is intuitive. Iterative traversal is what interviewers use to test depth. The transformation is always the same: **the call stack becomes an explicit stack, and the recursion order determines when you push and when you record**. For inorder, drill left while pushing, then pop and record, then pivot right. For level order, BFS with a queue and a levelSize snapshot. For zigzag, level order with a pre-allocated slice and an index flip.

Master these three and you have covered 80% of what interviewers actually ask about tree traversal.

---

**Previous:** [L10: Sliding Window](#) | **Next:** [L12: BST Operations — Sorted order hides in every BST](/post/fundamentals/interview-bst/)
