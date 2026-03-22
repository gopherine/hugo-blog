---
title: "Lesson 5: Trees and BSTs — Why your database is a tree"
author: Atharva Pandey
series: "Data Structures for Production Engineers"
lesson: 5
keywords: [data structures, computer science, trees, binary search trees, BST, database index]
tags: [fundamentals, data structures]
date: '2024-06-13T00:00:00.000Z'
---

When you run `SELECT * FROM orders WHERE user_id = 42`, your database doesn't scan every row. It walks a tree. Understanding why databases chose trees over hash maps — and which kind of tree, and why — is one of those "oh, everything makes sense now" moments that changes how you design systems.

Let me start with binary search trees, get honest about their weaknesses, and set up the foundation for B-trees in the next lesson.

## How It Actually Works

A Binary Search Tree is a tree where each node has at most two children, and the invariant holds: everything in the left subtree is less than the node's value, everything in the right subtree is greater.

```go
package main

import "fmt"

type BSTNode struct {
    Key   int
    Value string
    Left  *BSTNode
    Right *BSTNode
}

type BST struct {
    Root *BSTNode
}

func (t *BST) Insert(key int, value string) {
    t.Root = insertNode(t.Root, key, value)
}

func insertNode(node *BSTNode, key int, value string) *BSTNode {
    if node == nil {
        return &BSTNode{Key: key, Value: value}
    }
    if key < node.Key {
        node.Left = insertNode(node.Left, key, value)
    } else if key > node.Key {
        node.Right = insertNode(node.Right, key, value)
    } else {
        node.Value = value // update existing
    }
    return node
}

func (t *BST) Search(key int) (string, bool) {
    node := t.Root
    for node != nil {
        if key == node.Key {
            return node.Value, true
        } else if key < node.Key {
            node = node.Left
        } else {
            node = node.Right
        }
    }
    return "", false
}

// InOrder traversal gives sorted output — a key property
func (t *BST) InOrder() []int {
    var result []int
    var traverse func(*BSTNode)
    traverse = func(n *BSTNode) {
        if n == nil { return }
        traverse(n.Left)
        result = append(result, n.Key)
        traverse(n.Right)
    }
    traverse(t.Root)
    return result
}

func main() {
    t := &BST{}
    for _, k := range []int{5, 3, 7, 1, 4, 6, 9} {
        t.Insert(k, fmt.Sprintf("val-%d", k))
    }

    if v, ok := t.Search(4); ok {
        fmt.Println("Found:", v)
    }

    fmt.Println("Sorted:", t.InOrder()) // [1 3 4 5 6 7 9]
}
```

The beautiful property here: in-order traversal gives you sorted output. This is why trees are so useful for databases — they natively support range queries. "Give me all users with ID between 100 and 200" is just a bounded in-order traversal.

## When to Use It

Use trees when you need:
- **Ordered data with O(log n) operations** — insertion, deletion, search all O(log n) in a balanced tree
- **Range queries** — "all keys between X and Y"
- **Ordered iteration** — elements in sorted order without sorting
- **Predecessor/successor queries** — "what's the next/previous key?"

Hash maps beat trees on raw point lookup (amortized O(1) vs O(log n)), but they can't do any of the above. This is why databases offer both hash indexes and B-tree indexes. Choose hash for equality lookups, B-tree for everything else.

## Production Example

The most important tree in production is the AVL tree or red-black tree — self-balancing BSTs. Go's standard library doesn't expose one directly, but they underpin many systems. Here's where balance matters critically:

```go
package main

import "fmt"

// Demonstrate why balance matters: degenerate BST becomes a linked list
func buildDegenerateBST(keys []int) *BSTNode {
    var root *BSTNode
    for _, k := range keys {
        root = insertNode(root, k, fmt.Sprintf("v%d", k))
    }
    return root
}

func treeHeight(node *BSTNode) int {
    if node == nil {
        return 0
    }
    left := treeHeight(node.Left)
    right := treeHeight(node.Right)
    if left > right {
        return left + 1
    }
    return right + 1
}

func main() {
    // Sorted insertion — worst case for naive BST
    sorted := []int{1, 2, 3, 4, 5, 6, 7, 8, 9, 10}
    degenerate := buildDegenerateBST(sorted)
    fmt.Printf("Sorted insert, height: %d (should be ~10, like a linked list)\n",
        treeHeight(degenerate))

    // Random insertion — closer to balanced
    random := []int{5, 2, 8, 1, 4, 7, 9, 3, 6, 10}
    balanced := buildDegenerateBST(random)
    fmt.Printf("Random insert, height: %d (should be ~4, like log2(10))\n",
        treeHeight(balanced))
}
```

This is exactly the problem databases faced: user IDs are often sequential. If you used a naive BST as a database index and inserted rows with IDs 1, 2, 3, 4... you'd get a linked list with O(n) lookup. This is why real databases use self-balancing trees (AVL, red-black) or B-trees that rebalance on every insert.

In Go's standard library, `sync.Map` uses a hash map internally, but the `golang.org/x/exp/maps` ecosystem and many production libraries (like `google/btree`) provide balanced tree implementations for use cases that need ordering.

Here's a practical application — an event scheduler that needs to find the next event by time:

```go
package main

import (
    "fmt"
    "time"
)

type Event struct {
    Timestamp time.Time
    Name      string
}

// In production, use a proper sorted structure like a heap or B-tree.
// This shows the BST query pattern for range lookups.
type EventIndex struct {
    root *eventNode
}

type eventNode struct {
    event       Event
    left, right *eventNode
}

func (idx *EventIndex) Insert(e Event) {
    idx.root = insertEvent(idx.root, e)
}

func insertEvent(n *eventNode, e Event) *eventNode {
    if n == nil {
        return &eventNode{event: e}
    }
    if e.Timestamp.Before(n.event.Timestamp) {
        n.left = insertEvent(n.left, e)
    } else {
        n.right = insertEvent(n.right, e)
    }
    return n
}

// Range query: events between start and end
func (idx *EventIndex) Range(start, end time.Time) []Event {
    var results []Event
    rangeQuery(idx.root, start, end, &results)
    return results
}

func rangeQuery(n *eventNode, start, end time.Time, results *[]Event) {
    if n == nil {
        return
    }
    if n.event.Timestamp.After(start) {
        rangeQuery(n.left, start, end, results)
    }
    if !n.event.Timestamp.Before(start) && !n.event.Timestamp.After(end) {
        *results = append(*results, n.event)
    }
    if n.event.Timestamp.Before(end) {
        rangeQuery(n.right, start, end, results)
    }
}

func main() {
    idx := &EventIndex{}
    base := time.Now()
    idx.Insert(Event{base.Add(1 * time.Minute), "user_signup"})
    idx.Insert(Event{base.Add(3 * time.Minute), "payment_processed"})
    idx.Insert(Event{base.Add(5 * time.Minute), "order_shipped"})
    idx.Insert(Event{base.Add(2 * time.Minute), "email_sent"})

    results := idx.Range(base, base.Add(4*time.Minute))
    fmt.Printf("Events in first 4 minutes: %d\n", len(results))
    for _, e := range results {
        fmt.Println(" -", e.Name)
    }
}
```

The range query prunes entire subtrees: if the current node's timestamp is after our end, we never visit the right subtree. This pruning is why databases can answer range queries in O(log n + k) rather than O(n).

## The Tradeoffs

**Balance is everything.** An unbalanced BST is just a slow linked list. Production-grade trees (AVL, red-black) add rotation logic on every insert and delete to maintain O(log n) height. The overhead is small but real — about 1-2 pointer operations per level.

**Cache behavior is poor for deep trees.** Each level of the tree is a pointer dereference to a potentially random memory location. A tree of height 20 (for a million nodes) means up to 20 cache misses per lookup. This is why databases don't use BSTs for disk-based indexes — they use B-trees with large nodes that fit multiple keys (covered in Lesson 6).

**Concurrency is hard.** A shared BST requires locking on writes. Fine-grained locking (per-node) is complex to get right. For concurrent use in Go, channel-coordinated access or `sync.RWMutex` on the whole tree is usually the right starting point.

## Key Takeaway

Binary search trees are the conceptual foundation for understanding every database index. The core insight is that trees encode ordering information structurally — the shape of the tree tells you the relative order of every element. This enables range queries, ordered iteration, and predecessor/successor lookups that hash maps fundamentally cannot provide. The limitation of BSTs is that balance is not guaranteed, which is why every real production system uses a variant that self-balances. B-trees take this one step further for disk access — that's the next lesson.

---

*Previous: [Lesson 4: Stacks and Queues — The structures hiding in every system](/post/fundamentals/ds-stacks-queues/)*

*Next: [Lesson 6: B-Trees and B+ Trees — How every database index actually works](/post/fundamentals/ds-btrees/)*
