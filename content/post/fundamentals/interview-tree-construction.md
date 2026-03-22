---
title: "Interview Patterns L13: Tree Construction — Build trees from traversal arrays"
author: "Atharva Pandey"
keywords:
  - tree construction
  - build tree preorder inorder
  - serialize deserialize binary tree
  - reconstruct tree
  - interview patterns
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 13
date: "2025-01-16T00:00:00.000Z"
---

Construction problems break the mold. Most tree problems ask you to read a tree and return something — a traversal, a value, a boolean. Construction problems ask you to create a tree from some compact representation. That reversal in direction exposes a different set of thinking skills: can you reason backwards from output to structure? Can you identify what information each traversal encoding uniquely determines?

I think of tree construction as a test of how well you understand what information each traversal preserves and destroys. Inorder alone cannot reconstruct a tree. Preorder alone cannot either. But together they contain exactly enough information. Serialize/deserialize is a different angle: design your own encoding so reconstruction is unambiguous. Both problems show up at Google, Meta, and Amazon with surprising regularity.

## The Pattern

Every tree construction problem reduces to two questions:
1. **Where is the root?** The first element of preorder is always the root. For serialization, you decide.
2. **Where does the left subtree end and the right subtree begin?** In the combined preorder+inorder problem, the root's position in the inorder array draws that boundary. In serialization, null markers do the same job.

Once you find the root and split the remaining elements into left and right groups, you recurse. The recursion handles itself if you set up the split correctly.

## Problem 1 [Medium]: Construct Binary Tree from Preorder and Inorder Traversal

**Problem:** Given `preorder` and `inorder` traversal arrays of a binary tree (with unique values), reconstruct and return the root.

Example:
```
preorder = [3,9,20,15,7]
inorder  = [9,3,15,20,7]

Result:
    3
   / \
  9  20
    /  \
   15   7
```

**Brute force:** For each recursive call, search linearly through the inorder array to find the root's position. O(n²) time because of repeated linear scans.

**Building the optimal solution:**

**Key insight 1:** `preorder[0]` is always the root of the current subtree.

**Key insight 2:** The root's position in the inorder array (`inorderIdx`) splits the tree: everything to the left is the left subtree, everything to the right is the right subtree.

**Key insight 3:** The number of nodes in the left subtree (`leftSize = inorderIdx - inorderStart`) tells us exactly how many elements in preorder belong to the left subtree.

```go
type TreeNode struct {
    Val   int
    Left  *TreeNode
    Right *TreeNode
}

func buildTree(preorder []int, inorder []int) *TreeNode {
    // Map value -> index in inorder for O(1) lookup
    inorderMap := make(map[int]int, len(inorder))
    for i, v := range inorder {
        inorderMap[v] = i
    }

    var build func(preStart, preEnd, inStart, inEnd int) *TreeNode
    build = func(preStart, preEnd, inStart, inEnd int) *TreeNode {
        if preStart > preEnd {
            return nil
        }
        rootVal := preorder[preStart]
        root := &TreeNode{Val: rootVal}

        inIdx := inorderMap[rootVal]
        leftSize := inIdx - inStart

        root.Left = build(
            preStart+1, preStart+leftSize,
            inStart, inIdx-1,
        )
        root.Right = build(
            preStart+leftSize+1, preEnd,
            inIdx+1, inEnd,
        )
        return root
    }

    return build(0, len(preorder)-1, 0, len(inorder)-1)
}
```

**Tracing through the example:**
- `preorder[0] = 3` → root is 3
- 3 is at index 1 in inorder → left subtree has 1 node (index 0, value 9)
- Left subtree: `preorder[1..1]`, `inorder[0..0]` → single node 9
- Right subtree: `preorder[2..4]`, `inorder[2..4]` → root is 20, and so on recursively

**Complexity:** O(n) time with the hashmap (vs O(n²) without), O(n) space for the map and recursion stack.

**Common mistake:** Getting the index arithmetic wrong. Write out the index ranges explicitly before coding. The formula `leftSize = inIdx - inStart` is the one that trips people up. Practice tracing through a small example to verify.

## Problem 2 [Hard]: Serialize and Deserialize Binary Tree

**Problem:** Design an algorithm to serialize a binary tree to a string and deserialize that string back to the tree. You decide the format.

**Why this is hard:** You need to choose an encoding where null positions are unambiguous. Inorder alone is insufficient (many trees share the same inorder). Preorder with null markers is the standard clean solution.

**Brute force / naive approach:** Store preorder + inorder separately, then reconstruct as in Problem 1. Works but requires two arrays and the tree must have unique values.

**Building the elegant solution — preorder with null markers:**

We serialize using preorder DFS, writing `#` for null nodes. This encodes the tree's complete structure including the shape of null leaves. Deserialization reads back in preorder, consuming the string token by token.

```go
import (
    "strconv"
    "strings"
)

type Codec struct{}

// Serialize encodes the tree using preorder DFS with "#" for nulls
func (c *Codec) serialize(root *TreeNode) string {
    var sb strings.Builder
    var dfs func(*TreeNode)
    dfs = func(node *TreeNode) {
        if node == nil {
            sb.WriteString("#,")
            return
        }
        sb.WriteString(strconv.Itoa(node.Val))
        sb.WriteByte(',')
        dfs(node.Left)
        dfs(node.Right)
    }
    dfs(root)
    return sb.String()
}

// Deserialize decodes the preorder+null-marker string back to a tree
func (c *Codec) deserialize(data string) *TreeNode {
    tokens := strings.Split(data, ",")
    idx := 0

    var build func() *TreeNode
    build = func() *TreeNode {
        if idx >= len(tokens) || tokens[idx] == "#" {
            idx++
            return nil
        }
        val, _ := strconv.Atoi(tokens[idx])
        idx++
        node := &TreeNode{Val: val}
        node.Left = build()
        node.Right = build()
        return node
    }
    return build()
}
```

**Why preorder with nulls is self-contained:**

Consider the tree:
```
    1
   / \
  2   3
```

Serialized: `1,2,#,#,3,#,#,`

When deserializing, we read `1` → root. Then recursively build the left subtree: read `2` → node. Then its left: read `#` → nil. Then its right: read `#` → nil. Back to root's right: read `3` → node. And so on. The null markers tell us exactly when each subtree ends. No ambiguity.

**Why inorder alone fails:** The tree `[1, null, 2]` and the tree `[1, 2, null]` with `[2, 1]` both have the same inorder... wait, actually inorder of `[1, null, 2]` is `[1,2]` and inorder of `[2, 1]` would be `[2,1]` — different. But the deeper issue is that for a general binary tree (not a BST), inorder alone does not tell you where the root is. You need preorder or postorder to identify roots.

**Complexity:** O(n) time and O(n) space for both serialize and deserialize. The string length is O(n) — each node contributes its value plus a comma, each null contributes two characters.

**Alternative you should mention in an interview:** Level order serialization with null markers (BFS-style). It is how LeetCode encodes trees in problem statements. The logic is a bit more involved during deserialization but produces more human-readable output.

```go
// BFS serialize — worth knowing but preorder DFS is cleaner to implement
func serializeBFS(root *TreeNode) string {
    if root == nil {
        return "#"
    }
    var sb strings.Builder
    queue := []*TreeNode{root}
    for len(queue) > 0 {
        node := queue[0]
        queue = queue[1:]
        if node == nil {
            sb.WriteString("#,")
        } else {
            sb.WriteString(strconv.Itoa(node.Val))
            sb.WriteByte(',')
            queue = append(queue, node.Left)
            queue = append(queue, node.Right)
        }
    }
    return sb.String()
}
```

## How to Recognize This Pattern

- Problem gives you **one or two traversal arrays** and asks to reconstruct → preorder+inorder construction, use index arithmetic and a hashmap
- Problem asks you to **store a tree and recreate it exactly** → serialize/deserialize, preorder with null markers is cleanest
- Problem says **unique values** → hashmap is safe for index lookup
- Problem says values may repeat → you cannot use a value-to-index map; you must use the structural encoding (null markers) rather than value-based splitting

The distinguishing question: **does the encoding need to handle duplicates?** If yes, structure-based encoding (null markers) is the only safe approach. If no, value-based splitting with a hashmap is cleaner.

## Key Takeaway

Tree construction is about identifying the root and partitioning what remains. Preorder's first element is always the root. The root's position in inorder divides left from right. Together they reconstruct any tree with unique values in O(n) with a hashmap. For serialize/deserialize, preorder DFS with null markers is the most interview-friendly solution: it is short, handles all edge cases including empty trees and skewed trees, and the null markers make the structure self-describing. Write these twice until the index arithmetic and the token-consumption pattern feel automatic — under interview pressure, you want mechanics to be reflexive.

---

**Previous:** [L12: BST Operations](/post/fundamentals/interview-bst/) | **Next:** [L14: Tree DFS Patterns — Every path question is DFS](/post/fundamentals/interview-tree-dfs/)
