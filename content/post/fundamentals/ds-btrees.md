---
title: "Lesson 6: B-Trees and B+ Trees — How every database index actually works"
author: Atharva Pandey
series: "Data Structures for Production Engineers"
lesson: 6
keywords: [data structures, computer science, B-tree, B+ tree, database index, PostgreSQL, MySQL]
tags: [fundamentals, data structures]
date: '2024-06-26T00:00:00.000Z'
---

Every time PostgreSQL, MySQL, SQLite, or MongoDB uses an index, it's almost certainly a B-tree or B+ tree underneath. Not a binary search tree — a B-tree. The difference matters more than most engineers realize, and it comes down to one thing: disk read costs.

I've seen engineers add indexes blindly to fix slow queries without understanding what an index actually is. When you understand B-trees, you understand why some indexes help more than others, why certain query patterns can't use indexes, and why index-heavy tables slow down on writes.

## How It Actually Works

A B-tree is a balanced, multi-way search tree. Unlike a BST where each node has at most 2 children, a B-tree node can have hundreds or thousands of children. Each node stores multiple keys and the pointers between them.

The core property: a B-tree of order `m` has nodes with up to `m-1` keys and `m` children. Every non-root node is at least half full. The tree is always height-balanced — all leaves are at the same level.

Why does this matter? Because a disk read fetches a full page (typically 4KB or 8KB) in a single I/O operation. If your BST nodes are 32 bytes each, a 4KB page read retrieves one node and wastes most of the page. If your B-tree nodes are 4KB each, a single page read gets you hundreds of keys to compare against — turning one disk seek into potentially several comparisons, but all in-memory comparisons, not I/O.

```go
package main

import (
    "fmt"
    "sort"
)

// A simplified B-tree for illustration (order 3: max 2 keys, max 3 children)
const order = 3

type BTreeNode struct {
    keys     []int
    children []*BTreeNode
    isLeaf   bool
}

func newNode(isLeaf bool) *BTreeNode {
    return &BTreeNode{isLeaf: isLeaf}
}

type BTree struct {
    root *BTreeNode
}

func (t *BTree) Search(key int) bool {
    return searchNode(t.root, key)
}

func searchNode(node *BTreeNode, key int) bool {
    if node == nil {
        return false
    }
    // Find the position where key would be in this node
    i := sort.SearchInts(node.keys, key)
    if i < len(node.keys) && node.keys[i] == key {
        return true
    }
    if node.isLeaf {
        return false
    }
    return searchNode(node.children[i], key)
}

// Insert simplified — full implementation requires split logic
func (t *BTree) Insert(key int) {
    if t.root == nil {
        t.root = newNode(true)
    }
    // For illustration: just show the concept
    t.root.keys = append(t.root.keys, key)
    sort.Ints(t.root.keys)
}

func main() {
    t := &BTree{}
    for _, k := range []int{10, 20, 30, 5, 15, 25, 35} {
        t.Insert(k)
    }
    fmt.Println("Search 15:", t.Search(15)) // true
    fmt.Println("Search 99:", t.Search(99)) // false
}
```

A real B-tree implementation requires node splitting when a node overflows, and merging/borrowing when nodes underflow on deletion. The full logic is complex — which is why you don't write B-tree implementations, you use databases that implement them for you. But understanding the structure tells you how to use them effectively.

## When to Use It

You're using B-trees every time you use a database index. The question is: when should you create an index, and what query patterns can exploit it?

**B-trees support:**
- Equality lookups: `WHERE id = 42`
- Range queries: `WHERE created_at BETWEEN '2024-01-01' AND '2024-03-01'`
- Prefix matches: `WHERE name LIKE 'Atharva%'` (but NOT `'%Atharva'`)
- Sorting: `ORDER BY indexed_column` (the tree is already sorted)
- Composite queries in prefix order: index on `(user_id, timestamp)` supports `WHERE user_id = X AND timestamp > Y`

**B-trees don't help:**
- Full-text search (use inverted indexes or dedicated text search)
- Arbitrary `LIKE '%pattern%'` (can't use index for leading wildcard)
- Functions on indexed columns: `WHERE LOWER(email) = 'x'` — add a functional index instead

## Production Example

Here's a concrete demonstration of what a PostgreSQL B-tree index is actually doing when you run a range query. Let's model the page-access pattern:

```go
package main

import (
    "fmt"
    "math"
)

// Simulate the I/O cost difference between full scan and B-tree range query

type TableStats struct {
    rowCount   int
    pageSize   int // bytes
    rowSize    int // bytes
    indexOrder int // branching factor
}

func (s TableStats) totalPages() int {
    return int(math.Ceil(float64(s.rowCount*s.rowSize) / float64(s.pageSize)))
}

func (s TableStats) btreeHeight() int {
    // Height of B-tree = ceil(log_order(n))
    return int(math.Ceil(math.Log(float64(s.rowCount)) / math.Log(float64(s.indexOrder))))
}

func (s TableStats) seqScanPages(matchFraction float64) int {
    // Sequential scan reads all pages regardless
    return s.totalPages()
}

func (s TableStats) indexRangePages(matchFraction float64) int {
    // B-tree traversal: height traversal + leaf pages for matches
    matchRows := int(float64(s.rowCount) * matchFraction)
    rowsPerLeafPage := s.pageSize / 16 // index entry ~16 bytes
    leafPagesRead := int(math.Ceil(float64(matchRows) / float64(rowsPerLeafPage)))
    heapFetches := matchRows // worst case: one heap page per row

    return s.btreeHeight() + leafPagesRead + heapFetches
}

func main() {
    orders := TableStats{
        rowCount:   10_000_000, // 10M orders
        pageSize:   8192,       // 8KB PostgreSQL default
        rowSize:    200,        // bytes per row
        indexOrder: 200,        // realistic B-tree branching factor
    }

    fmt.Printf("Table: %d rows, %d total pages\n\n",
        orders.rowCount, orders.totalPages())

    fmt.Printf("B-tree height: %d levels\n", orders.btreeHeight())
    fmt.Println("(Each level = one disk seek to find the node)")
    fmt.Println()

    // Selective query (0.001% of rows)
    selectiveFrac := 0.00001
    fmt.Printf("Query matching %.4f%% of rows:\n", selectiveFrac*100)
    fmt.Printf("  Sequential scan: %d page reads\n",
        orders.seqScanPages(selectiveFrac))
    fmt.Printf("  Index range: ~%d page reads\n",
        orders.indexRangePages(selectiveFrac))

    // Non-selective query (10% of rows)
    broadFrac := 0.1
    fmt.Printf("\nQuery matching %.1f%% of rows:\n", broadFrac*100)
    fmt.Printf("  Sequential scan: %d page reads\n",
        orders.seqScanPages(broadFrac))
    fmt.Printf("  Index range: ~%d page reads\n",
        orders.indexRangePages(broadFrac))
    fmt.Println("  (Sequential scan wins here — this is why EXPLAIN matters)")
}
```

This is why PostgreSQL's query planner sometimes ignores your index and does a sequential scan instead — when a large fraction of rows match, the overhead of individual heap fetches from the index exceeds the cost of just reading all pages sequentially.

## The Tradeoffs

**B+ Trees vs B-Trees.** Most databases use B+ trees, not B-trees. The difference: in a B-tree, values are stored in internal nodes. In a B+ tree, values are only stored in leaf nodes, and all leaf nodes are linked in a doubly linked list. This makes range scans dramatically faster — once you find the start key, you just walk the leaf list rather than backtracking up the tree.

**Write amplification.** Every insert or update that changes an indexed column must update every index on that column. A table with 8 indexes and 1000 inserts/second is doing 8000 index writes per second, each potentially requiring a B-tree split and page rewrite. This is why write-heavy tables should have minimal indexes.

**Index bloat over time.** PostgreSQL B-trees don't automatically reclaim space when rows are deleted. Deleted entries are marked as "dead" and cleaned up by VACUUM. Under high delete/update rates, your indexes can become significantly larger than the live data, with degraded lookup performance. `REINDEX` or `VACUUM FULL` periodically reclaims this space.

**Cardinality and selectivity.** An index on a boolean column (true/false) is almost never useful — the planner would pick a sequential scan. Indexes work best on high-cardinality columns (user IDs, timestamps, UUIDs) where they filter out most of the table.

## Key Takeaway

B-trees are the answer to the question: "how do you build a BST for a world where disk reads are 1000x slower than memory reads?" The answer is: make each node huge, store many keys per node, minimize tree height, and chain leaf nodes for fast range scans. Understanding this explains why range queries use indexes efficiently, why leading-wildcard LIKE queries can't use B-tree indexes, and why you shouldn't add indexes to every column of every table.

When your DBA says "the query planner chose a sequential scan," they're saying "the B-tree overhead wasn't worth it for this query." Now you know why.

---

*Previous: [Lesson 5: Trees and BSTs — Why your database is a tree](/post/fundamentals/ds-trees-bst/)*

*Next: [Lesson 7: Heaps and Priority Queues — Scheduling, top-K, and rate limiters](/post/fundamentals/ds-heaps/)*
