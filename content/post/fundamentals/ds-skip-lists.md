---
title: "Lesson 11: Skip Lists — How Redis sorted sets work"
author: Atharva Pandey
series: "Data Structures for Production Engineers"
lesson: 11
keywords: [data structures, computer science, skip list, Redis, sorted set, probabilistic, concurrent]
tags: [fundamentals, data structures]
date: '2024-09-03T00:00:00.000Z'
---

Redis chose skip lists for its sorted set implementation, and that choice is more interesting than it first appears. When you have `ZADD`, `ZRANGE`, and `ZRANK` all needing to run at O(log n), you might reach for a balanced BST. But Redis chose a probabilistic alternative that's simpler to implement, easier to reason about in concurrent contexts, and performs comparably in practice.

Understanding skip lists taught me something important about engineering tradeoffs: sometimes "good enough with simpler code" beats "optimal but complex."

## How It Actually Works

A skip list is a layered linked list. At the bottom layer (level 0), every element is linked in sorted order — it's just a sorted linked list. At higher levels, elements are selectively promoted via coin flip (each element has a 50% chance of being promoted to the next level).

The result: a probabilistic data structure with O(log n) expected search, insert, and delete.

```go
package main

import (
    "fmt"
    "math/rand"
)

const maxLevel = 16
const probability = 0.5

type SkipNode struct {
    key     float64
    member  string
    forward []*SkipNode
}

type SkipList struct {
    head  *SkipNode
    level int
    size  int
}

func newNode(key float64, member string, level int) *SkipNode {
    return &SkipNode{
        key:     key,
        member:  member,
        forward: make([]*SkipNode, level+1),
    }
}

func NewSkipList() *SkipList {
    head := newNode(-1e18, "", maxLevel)
    return &SkipList{head: head, level: 0}
}

func (sl *SkipList) randomLevel() int {
    level := 0
    for level < maxLevel && rand.Float64() < probability {
        level++
    }
    return level
}

func (sl *SkipList) Insert(score float64, member string) {
    update := make([]*SkipNode, maxLevel+1)
    curr := sl.head

    // Find insertion point at each level
    for i := sl.level; i >= 0; i-- {
        for curr.forward[i] != nil &&
            (curr.forward[i].key < score ||
                (curr.forward[i].key == score && curr.forward[i].member < member)) {
            curr = curr.forward[i]
        }
        update[i] = curr
    }

    level := sl.randomLevel()
    if level > sl.level {
        for i := sl.level + 1; i <= level; i++ {
            update[i] = sl.head
        }
        sl.level = level
    }

    node := newNode(score, member, level)
    for i := 0; i <= level; i++ {
        node.forward[i] = update[i].forward[i]
        update[i].forward[i] = node
    }
    sl.size++
}

func (sl *SkipList) Search(score float64, member string) bool {
    curr := sl.head
    for i := sl.level; i >= 0; i-- {
        for curr.forward[i] != nil &&
            (curr.forward[i].key < score ||
                (curr.forward[i].key == score && curr.forward[i].member < member)) {
            curr = curr.forward[i]
        }
    }
    curr = curr.forward[0]
    return curr != nil && curr.key == score && curr.member == member
}

// RangeByScore returns all members with score in [min, max]
func (sl *SkipList) RangeByScore(min, max float64) []string {
    curr := sl.head
    for i := sl.level; i >= 0; i-- {
        for curr.forward[i] != nil && curr.forward[i].key < min {
            curr = curr.forward[i]
        }
    }
    curr = curr.forward[0]

    var result []string
    for curr != nil && curr.key <= max {
        result = append(result, curr.member)
        curr = curr.forward[0]
    }
    return result
}

func main() {
    sl := NewSkipList()

    // Leaderboard: score is points, member is player name
    sl.Insert(1500, "alice")
    sl.Insert(2300, "bob")
    sl.Insert(800, "charlie")
    sl.Insert(3100, "diana")
    sl.Insert(2300, "eve") // same score as bob

    fmt.Println("Search alice (1500):", sl.Search(1500, "alice")) // true
    fmt.Println("Search unknown:", sl.Search(9999, "nobody"))     // false

    top := sl.RangeByScore(2000, 9999)
    fmt.Println("Players with score >= 2000:", top)
}
```

The elegant part: search always starts from the top level (the "express lane") and drops down when it overshoots. The coin-flip promotion means about half the elements appear at level 1, a quarter at level 2, an eighth at level 3 — forming an implicit binary search structure without any rotation logic.

## When to Use It

Use a skip list when:
- You need a sorted data structure with O(log n) operations
- You want simpler implementation than a red-black tree or AVL tree
- You need range queries on a sorted set
- You're implementing a sorted set data structure (leaderboards, priority queues with ordered access)
- Concurrent access is important — skip lists have better lock-free properties than balanced BSTs

Redis specifically chose skip lists for sorted sets because:
1. Range operations (ZRANGE) are cache-friendly — just walk the bottom level
2. Rank operations (ZRANK) can be implemented with augmented skip lists
3. The implementation is simpler than a balanced BST
4. Memory locality is acceptable for in-memory workloads

## Production Example

The most common use of skip list semantics in production is the Redis sorted set. Let's model a real-time leaderboard — the kind gaming companies, trading platforms, and loyalty programs use:

```go
package main

import (
    "fmt"
    "math/rand"
)

// Augmented skip list that tracks subtree sizes for O(log n) rank queries
// This is how Redis implements ZRANK

type RankedNode struct {
    score   float64
    member  string
    forward []*RankedNode
    span    []int // number of nodes skipped at each level
}

type RankedSkipList struct {
    head  *RankedNode
    level int
    size  int
}

func newRankedNode(score float64, member string, level int) *RankedNode {
    return &RankedNode{
        score:   score,
        member:  member,
        forward: make([]*RankedNode, level+1),
        span:    make([]int, level+1),
    }
}

func NewRankedSkipList() *RankedSkipList {
    head := newRankedNode(-1e18, "", maxLevel)
    return &RankedSkipList{head: head, level: 0}
}

func (sl *RankedSkipList) randomLevel() int {
    level := 0
    for level < maxLevel && rand.Float64() < 0.5 {
        level++
    }
    return level
}

func (sl *RankedSkipList) Add(score float64, member string) {
    update := make([]*RankedNode, maxLevel+1)
    rank := make([]int, maxLevel+1)
    curr := sl.head

    for i := sl.level; i >= 0; i-- {
        if i == sl.level {
            rank[i] = 0
        } else {
            rank[i] = rank[i+1]
        }
        for curr.forward[i] != nil &&
            (curr.forward[i].score < score ||
                (curr.forward[i].score == score && curr.forward[i].member < member)) {
            rank[i] += curr.span[i]
            curr = curr.forward[i]
        }
        update[i] = curr
    }

    level := sl.randomLevel()
    if level > sl.level {
        for i := sl.level + 1; i <= level; i++ {
            update[i] = sl.head
            update[i].span[i] = sl.size
        }
        sl.level = level
    }

    node := newRankedNode(score, member, level)
    for i := 0; i <= level; i++ {
        node.forward[i] = update[i].forward[i]
        node.span[i] = update[i].span[i] - (rank[0] - rank[i])
        update[i].forward[i] = node
        update[i].span[i] = (rank[0] - rank[i]) + 1
    }
    for i := level + 1; i <= sl.level; i++ {
        update[i].span[i]++
    }
    sl.size++
}

func (sl *RankedSkipList) GetRank(score float64, member string) int {
    rank := 0
    curr := sl.head
    for i := sl.level; i >= 0; i-- {
        for curr.forward[i] != nil &&
            (curr.forward[i].score < score ||
                (curr.forward[i].score == score && curr.forward[i].member <= member)) {
            rank += curr.span[i]
            curr = curr.forward[i]
        }
        if curr != sl.head && curr.score == score && curr.member == member {
            return rank
        }
    }
    return -1
}

func main() {
    sl := NewRankedSkipList()

    players := []struct {
        name  string
        score float64
    }{
        {"alice", 9850}, {"bob", 7200}, {"charlie", 11300},
        {"diana", 8800}, {"eve", 11300}, {"frank", 6100},
    }
    for _, p := range players {
        sl.Add(p.score, p.name)
    }

    fmt.Printf("charlie's rank: %d\n", sl.GetRank(11300, "charlie"))
    fmt.Printf("alice's rank: %d\n", sl.GetRank(9850, "alice"))
    fmt.Printf("frank's rank: %d\n", sl.GetRank(6100, "frank"))
}
```

## The Tradeoffs

**Probabilistic, not guaranteed.** A balanced BST guarantees O(log n) in the worst case. A skip list guarantees O(log n) *expected*. In the extremely unlikely case that every element gets promoted to maximum level, performance degrades. In practice, the probability of this is astronomically small — but it's a real distinction when you need hard guarantees.

**Memory overhead.** Each node carries forward pointers for each level it occupies. On average, a node has 2 forward pointers (levels 0 and 1), but the worst case is `maxLevel` pointers. For 16 levels, that's 16 pointers per node — 128 bytes of overhead on 64-bit systems. Compare this to a red-black tree node with 3 pointers (parent, left, right) — 24 bytes.

**Cache behavior.** Level-0 traversal (range queries) walks nodes in order — similar cache behavior to a sorted linked list. The express-lane traversal does skip large distances in the array, potentially causing cache misses at each level drop. Red-black trees have similar pointer-chasing behavior, so this is roughly equivalent.

**Simplicity is real.** I've implemented both red-black trees and skip lists. The red-black tree required managing four rotation cases, color flipping, and careful invariant preservation on deletion. The skip list required one insertion algorithm and one deletion algorithm with no case analysis. The code is half the length and has been correct on the first attempt far more often.

## Key Takeaway

Skip lists are one of computer science's elegant "good enough" solutions. They achieve O(log n) operations through randomization rather than strict invariant maintenance, which makes them simpler to implement and easier to reason about in concurrent settings. Redis chose them over balanced BSTs for its sorted set, and the leaderboard functionality millions of applications depend on runs on this probabilistic structure.

When someone tells you that Redis sorted sets are fast, part of the reason is: the skip list's level-0 traversal for range queries is just walking a sorted linked list — predictable, simple, and fast.

---

*Previous: [Lesson 10: Bloom Filters — Probably yes, definitely no](/post/fundamentals/ds-bloom-filters/)*

*Next: [Lesson 12: Ring Buffers — Fixed-size queues for real-time systems](/post/fundamentals/ds-ring-buffers/)*
