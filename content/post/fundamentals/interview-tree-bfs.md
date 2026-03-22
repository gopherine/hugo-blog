---
title: "Interview Patterns L15: Tree BFS Patterns — Level by level reveals structure"
author: "Atharva Pandey"
keywords:
  - tree BFS
  - right side view
  - connect next pointer
  - minimum depth
  - level order
  - interview patterns
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 15
date: "2025-02-21T00:00:00.000Z"
---

Level by level. That phrase shows up in maybe a third of binary tree interview problems, sometimes explicitly and sometimes disguised. "What would you see from the right side?" Level by level. "What is the minimum number of steps from root to a leaf?" Level by level. "Connect each node to its right neighbor on the same level?" Level by level.

BFS on trees feels simpler than BFS on graphs because trees have no cycles and no visited-set bookkeeping. But the interesting problems are not about the traversal itself — they are about what you do with the level structure that BFS naturally exposes. In this lesson, I focus on three problems that are each a variation on one theme: level order traversal plus one clever twist per problem.

## The Pattern

The foundation is the level order BFS template from L11:

```go
queue := []*TreeNode{root}
for len(queue) > 0 {
    levelSize := len(queue)
    for i := 0; i < levelSize; i++ {
        node := queue[0]
        queue = queue[1:]
        // process node
        if node.Left != nil { queue = append(queue, node.Left) }
        if node.Right != nil { queue = append(queue, node.Right) }
    }
}
```

Everything in this lesson builds on top of this skeleton. The question is what you do with `i`, `levelSize`, and the relative position of `node` within the level.

## Problem 1 [Medium]: Binary Tree Right Side View

**Problem:** Imagine standing to the right of a binary tree and looking left. Return the values of the nodes you can see, ordered from top to bottom.

Example:
```
    1
   / \
  2   3
   \
    5
```
Right side view: `[1, 3, 5]`

**Brute force:** Do level order BFS, collect all levels, take the last element of each level. O(n) time and space. This is actually not brute force — it is the correct approach. But many candidates over-engineer it.

**The clean solution:**

The last node processed in each level batch is visible from the right. We track `i` and record the node value when `i == levelSize - 1`.

```go
type TreeNode struct {
    Val   int
    Left  *TreeNode
    Right *TreeNode
}

func rightSideView(root *TreeNode) []int {
    if root == nil {
        return nil
    }
    var result []int
    queue := []*TreeNode{root}

    for len(queue) > 0 {
        levelSize := len(queue)
        for i := 0; i < levelSize; i++ {
            node := queue[0]
            queue = queue[1:]

            if i == levelSize-1 {
                result = append(result, node.Val)
            }
            if node.Left != nil {
                queue = append(queue, node.Left)
            }
            if node.Right != nil {
                queue = append(queue, node.Right)
            }
        }
    }
    return result
}
```

**Follow-up: left side view?** Change `i == levelSize-1` to `i == 0`. The BFS processes left children before right, so the first node in each level is the leftmost.

**Why BFS is better than DFS here:** You could do DFS and track the rightmost node at each depth (keeping a depth → value map). It works and is O(n). But BFS is more natural for level-based problems, and the code is simpler. Use BFS here unless the interviewer explicitly asks for a DFS approach.

**Complexity:** O(n) time, O(n) space (queue holds at most the widest level).

## Problem 2 [Hard]: Populating Next Right Pointers in Each Node

**Problem:** Each node has a `Next` pointer. Connect each node's `Next` to the node immediately to its right on the same level. Nodes at the rightmost position of a level should point to `nil`.

```go
type Node struct {
    Val   int
    Left  *Node
    Right *Node
    Next  *Node
}
```

**Brute force — BFS with queue:**

Level order BFS naturally groups nodes by level. For each level, connect consecutive nodes.

```go
func connect(root *Node) *Node {
    if root == nil {
        return nil
    }
    queue := []*Node{root}

    for len(queue) > 0 {
        levelSize := len(queue)
        for i := 0; i < levelSize; i++ {
            node := queue[0]
            queue = queue[1:]

            // Connect to next node in level (not the first node of next level)
            if i < levelSize-1 {
                node.Next = queue[0]
            }
            if node.Left != nil {
                queue = append(queue, node.Left)
            }
            if node.Right != nil {
                queue = append(queue, node.Right)
            }
        }
    }
    return root
}
```

This is O(n) time and O(n) space. The interviewer will then ask: **"Can you do it in O(1) extra space?"**

**Building the O(1) space solution using already-established Next pointers:**

Key insight: once we finish connecting level `L`, every node at level `L` is connected to its right neighbor via `Next`. We can use those connections to traverse level `L` and set up connections at level `L+1` — without a queue.

We treat each level as a linked list (connected via `Next`), and we use a "dummy head" trick for the next level to avoid edge cases when starting a new level.

```go
func connectO1Space(root *Node) *Node {
    if root == nil {
        return nil
    }
    curr := root // current level node we are linking from

    for curr != nil {
        dummy := &Node{} // sentinel head for next level
        tail := dummy    // tail of next level's linked list

        // Walk current level using Next pointers
        for curr != nil {
            if curr.Left != nil {
                tail.Next = curr.Left
                tail = tail.Next
            }
            if curr.Right != nil {
                tail.Next = curr.Right
                tail = tail.Next
            }
            curr = curr.Next // move right on current level
        }
        // Move down to next level
        curr = dummy.Next
    }
    return root
}
```

**Why the dummy/sentinel head:** When we start processing a new level, we do not know which node will be the head. The dummy node lets us always do `tail.Next = child` without a special case for the first child.

**Why this is O(1) space:** We only use a constant number of pointers (`curr`, `dummy`, `tail`). No queue. We leverage the `Next` pointers we are building as we go.

**Complexity:** O(n) time, O(1) extra space.

## Problem 3 [Medium]: Minimum Depth of Binary Tree

**Problem:** Find the minimum depth — the number of nodes along the shortest path from the root to the nearest leaf.

**This is trickier than it sounds.** The minimum depth is NOT simply `min(leftDepth, rightDepth) + 1`. Consider:

```
    1
     \
      2
```

`min(0, 1) + 1 = 1`, but the actual minimum depth is 2 because node 1 is not a leaf — it has a right child. A leaf must have no children.

**Brute force — DFS with the wrong formula:**

```go
// WRONG: this returns 1 for the example above
func minDepthWrong(root *TreeNode) int {
    if root == nil { return 0 }
    left := minDepthWrong(root.Left)
    right := minDepthWrong(root.Right)
    if left < right { return left + 1 }
    return right + 1
}
```

**Building the correct DFS solution:**

We must handle the case where a node has only one child. A node with only one child is NOT a leaf, so we must take the depth of the non-null child.

```go
func minDepthDFS(root *TreeNode) int {
    if root == nil {
        return 0
    }
    // Both children null: this is a leaf
    if root.Left == nil && root.Right == nil {
        return 1
    }
    // Only right child exists: must go right
    if root.Left == nil {
        return minDepthDFS(root.Right) + 1
    }
    // Only left child exists: must go left
    if root.Right == nil {
        return minDepthDFS(root.Left) + 1
    }
    // Both children exist: take the minimum
    l := minDepthDFS(root.Left)
    r := minDepthDFS(root.Right)
    if l < r {
        return l + 1
    }
    return r + 1
}
```

**Building the BFS solution — often better for "minimum" questions:**

For minimum depth, BFS has a key advantage: it finds the answer as soon as it reaches the first leaf. DFS always traverses the entire tree even if the shallowest leaf is on the left. BFS stops early.

```go
func minDepthBFS(root *TreeNode) int {
    if root == nil {
        return 0
    }
    queue := []*TreeNode{root}
    depth := 1

    for len(queue) > 0 {
        levelSize := len(queue)
        for i := 0; i < levelSize; i++ {
            node := queue[0]
            queue = queue[1:]

            // First leaf we encounter is at minimum depth
            if node.Left == nil && node.Right == nil {
                return depth
            }
            if node.Left != nil {
                queue = append(queue, node.Left)
            }
            if node.Right != nil {
                queue = append(queue, node.Right)
            }
        }
        depth++
    }
    return depth
}
```

**Why BFS wins for "minimum":** The first level-order node with no children is definitionally the shallowest leaf. BFS stops there. DFS might visit the entire right subtree before finding that the minimum is on the left. For balanced trees they are equivalent; for skewed trees BFS is far superior.

**Complexity:** O(n) worst case for both, but BFS best case is O(w × d) where w is the width and d is the minimum depth — potentially much better than O(n).

## How to Recognize This Pattern

- "What do you see from the side?" → BFS, take the first or last node per level
- "Connect nodes on the same level" → BFS with levelSize snapshot; O(1) space version uses already-built Next pointers
- "Minimum depth / minimum levels / shortest path to a leaf" → BFS, stop at first leaf
- "Average value at each depth / sum at each level" → BFS, aggregate per level
- Any problem involving **relative position within a level** (`i == 0`, `i == levelSize-1`) → BFS with the levelSize snapshot

The right-side view follow-up question is common: "What if the tree is not a complete binary tree and some levels have holes?" BFS handles this naturally because we only add non-null children. DFS with a depth-map also handles it, but BFS is cleaner.

## Key Takeaway

Tree BFS problems are all level order traversal plus one strategic twist. The key is the `levelSize` snapshot before the inner loop — it separates the current level from the next. With that snapshot, right side view is "take the last element," minimum depth is "stop at first leaf," and next pointer connection is "link consecutive nodes within the batch." For the next pointer problem, the O(1) space solution is the real interview test: use the Next pointers you just built to traverse the current level and wire up the next, with a dummy sentinel to avoid edge cases at the start of each level. That technique — building structure and using it immediately — is a pattern that comes up in linked list problems too.

---

**Previous:** [L14: Tree DFS Patterns](/post/fundamentals/interview-tree-dfs/) | **Next:** [L16: Graph BFS — Shortest path in unweighted graphs](/post/fundamentals/interview-graph-bfs/)
