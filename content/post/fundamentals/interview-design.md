---
title: "Lesson 38: Design Problems — Build it from scratch in 30 minutes"
author: "Atharva Pandey"
keywords:
  - LRU cache
  - LFU cache
  - design twitter feed
  - design problems
  - hash map
  - doubly linked list
  - interview patterns
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 38
date: "2026-02-03T00:00:00.000Z"
---

Design problems in coding interviews are different from system design rounds. You are not sketching architecture at a whiteboard — you are implementing a concrete data structure from scratch, live, in 30 to 45 minutes. The interviewer cares about both correctness and your choices of underlying data structures. "Just use a map" is never a complete answer.

I find these problems particularly satisfying because the solutions are compact. Once you see the underlying pattern — that almost every caching and feed problem requires a hash map layered on top of an ordered structure — the implementations become variations on a theme.

## The Pattern

The canonical data structure behind LRU Cache, LFU Cache, and most feed/timeline designs is a **hash map + doubly linked list**. The map provides O(1) key lookup. The doubly linked list maintains order (recency, frequency) and allows O(1) node reordering when you have a direct pointer to the node.

This combination comes up so often that internalizing it pays dividends across multiple interview problems.

```go
type Node struct {
    key, val   int
    prev, next *Node
}

// DLL with sentinel head and tail nodes — eliminates nil checks
type List struct {
    head, tail *Node
    size       int
}

func newList() *List {
    head := &Node{}
    tail := &Node{}
    head.next = tail
    tail.prev = head
    return &List{head: head, tail: tail}
}

func (l *List) addToFront(node *Node) {
    node.next = l.head.next
    node.prev = l.head
    l.head.next.prev = node
    l.head.next = node
    l.size++
}

func (l *List) remove(node *Node) {
    node.prev.next = node.next
    node.next.prev = node.prev
    l.size--
}

func (l *List) removeLast() *Node {
    if l.size == 0 {
        return nil
    }
    last := l.tail.prev
    l.remove(last)
    return last
}
```

Sentinel nodes (`head` and `tail`) eliminate nil checks in `addToFront` and `remove`. This is the implementation detail that separates clean code from buggy code under pressure.

## Problem 1: LRU Cache

Design a Least Recently Used cache with O(1) `Get` and `Put`.

```go
type LRUCache struct {
    cap     int
    cache   map[int]*Node
    list    *List
}

func Constructor(capacity int) LRUCache {
    return LRUCache{
        cap:   capacity,
        cache: make(map[int]*Node),
        list:  newList(),
    }
}

func (c *LRUCache) Get(key int) int {
    node, ok := c.cache[key]
    if !ok {
        return -1
    }
    // Move to front: most recently used
    c.list.remove(node)
    c.list.addToFront(node)
    return node.val
}

func (c *LRUCache) Put(key, value int) {
    if node, ok := c.cache[key]; ok {
        node.val = value
        c.list.remove(node)
        c.list.addToFront(node)
        return
    }
    // Evict if at capacity
    if len(c.cache) == c.cap {
        evicted := c.list.removeLast()
        delete(c.cache, evicted.key)
    }
    node := &Node{key: key, val: value}
    c.list.addToFront(node)
    c.cache[key] = node
}
```

Every access (Get or Put) moves the node to the front of the list. The LRU victim is always at the back. Because the map stores pointers to nodes, both operations are O(1).

The node must store its own key so that when we evict it from the list, we know which key to delete from the map.

## Problem 2: LFU Cache

Design a Least Frequently Used cache with O(1) `Get` and `Put`. Ties in frequency are broken by LRU order.

LFU is harder than LRU because eviction depends on frequency, not just recency. The standard approach: maintain a map from frequency to a doubly linked list of nodes at that frequency. Track the current minimum frequency explicitly.

```go
type LFUCache struct {
    cap     int
    minFreq int
    keys    map[int]*LFUNode           // key -> node
    freqs   map[int]*LFUList           // freq -> doubly linked list of nodes
}

type LFUNode struct {
    key, val, freq int
    prev, next     *LFUNode
}

type LFUList struct {
    head, tail *LFUNode
    size       int
}

func newLFUList() *LFUList {
    head := &LFUNode{}
    tail := &LFUNode{}
    head.next = tail
    tail.prev = head
    return &LFUList{head: head, tail: tail}
}

func (l *LFUList) addToFront(node *LFUNode) {
    node.next = l.head.next
    node.prev = l.head
    l.head.next.prev = node
    l.head.next = node
    l.size++
}

func (l *LFUList) remove(node *LFUNode) {
    node.prev.next = node.next
    node.next.prev = node.prev
    l.size--
}

func (l *LFUList) removeLast() *LFUNode {
    if l.size == 0 {
        return nil
    }
    last := l.tail.prev
    l.remove(last)
    return last
}

func LFUConstructor(capacity int) LFUCache {
    return LFUCache{
        cap:   capacity,
        keys:  make(map[int]*LFUNode),
        freqs: make(map[int]*LFUList),
    }
}

func (c *LFUCache) increment(node *LFUNode) {
    oldFreq := node.freq
    c.freqs[oldFreq].remove(node)
    if c.freqs[oldFreq].size == 0 {
        delete(c.freqs, oldFreq)
        if c.minFreq == oldFreq {
            c.minFreq++
        }
    }
    node.freq++
    if c.freqs[node.freq] == nil {
        c.freqs[node.freq] = newLFUList()
    }
    c.freqs[node.freq].addToFront(node)
}

func (c *LFUCache) Get(key int) int {
    node, ok := c.keys[key]
    if !ok {
        return -1
    }
    c.increment(node)
    return node.val
}

func (c *LFUCache) Put(key, value int) {
    if c.cap == 0 {
        return
    }
    if node, ok := c.keys[key]; ok {
        node.val = value
        c.increment(node)
        return
    }
    if len(c.keys) == c.cap {
        list := c.freqs[c.minFreq]
        evicted := list.removeLast()
        if list.size == 0 {
            delete(c.freqs, c.minFreq)
        }
        delete(c.keys, evicted.key)
    }
    node := &LFUNode{key: key, val: value, freq: 1}
    c.keys[key] = node
    if c.freqs[1] == nil {
        c.freqs[1] = newLFUList()
    }
    c.freqs[1].addToFront(node)
    c.minFreq = 1
}
```

The invariant to maintain: `minFreq` always holds the lowest frequency with at least one node. When a node is incremented, if its old frequency was `minFreq` and the list at `minFreq` is now empty, `minFreq++`. On insertion, `minFreq` resets to 1.

## Problem 3: Design Twitter Feed

Design a simplified Twitter where users can post tweets, follow/unfollow other users, and retrieve the 10 most recent tweets in their news feed (their own + followed users).

```go
import "container/heap"

type Twitter struct {
    timestamp int
    tweets    map[int][]Tweet    // userId -> list of tweets
    following map[int]map[int]bool // userId -> set of followed userIds
}

type Tweet struct {
    id        int
    timestamp int
}

func TwitterConstructor() Twitter {
    return Twitter{
        tweets:    make(map[int][]Tweet),
        following: make(map[int]map[int]bool),
    }
}

func (t *Twitter) PostTweet(userId, tweetId int) {
    t.timestamp++
    t.tweets[userId] = append(t.tweets[userId], Tweet{tweetId, t.timestamp})
}

func (t *Twitter) GetNewsFeed(userId int) []int {
    // Collect all candidate users: self + followed
    users := []int{userId}
    for uid := range t.following[userId] {
        users = append(users, uid)
    }

    // Max-heap of (timestamp, tweetId, userIdx, tweetIdx within user)
    type Entry struct {
        ts, tweetId, userIdx, tweetIdx int
    }
    h := &TweetHeap{}
    heap.Init(h)

    // Seed with the most recent tweet from each user
    for i, uid := range users {
        twts := t.tweets[uid]
        if len(twts) > 0 {
            last := len(twts) - 1
            heap.Push(h, Entry{twts[last].timestamp, twts[last].id, i, last})
        }
    }

    result := []int{}
    for h.Len() > 0 && len(result) < 10 {
        top := heap.Pop(h).(Entry)
        result = append(result, top.tweetId)
        if top.tweetIdx > 0 {
            uid := users[top.userIdx]
            prev := top.tweetIdx - 1
            twts := t.tweets[uid]
            heap.Push(h, Entry{twts[prev].timestamp, twts[prev].id, top.userIdx, prev})
        }
    }
    return result
}

func (t *Twitter) Follow(followerId, followeeId int) {
    if t.following[followerId] == nil {
        t.following[followerId] = make(map[int]bool)
    }
    t.following[followerId][followeeId] = true
}

func (t *Twitter) Unfollow(followerId, followeeId int) {
    delete(t.following[followerId], followeeId)
}

type TweetHeap []struct{ ts, tweetId, userIdx, tweetIdx int }
func (h TweetHeap) Len() int           { return len(h) }
func (h TweetHeap) Less(i, j int) bool { return h[i].ts > h[j].ts } // max-heap by timestamp
func (h TweetHeap) Swap(i, j int)      { h[i], h[j] = h[j], h[i] }
func (h *TweetHeap) Push(x any)        { *h = append(*h, x.(struct{ ts, tweetId, userIdx, tweetIdx int })) }
func (h *TweetHeap) Pop() any {
    old := *h; n := len(old); x := old[n-1]; *h = old[:n-1]; return x
}
```

The feed retrieval is the same pattern as Merge K Sorted Lists — a heap seeded with the most recent tweet from each user, then repeatedly popping the maximum and pushing the next tweet from the same user.

## How to Recognize This Pattern

Design problems at the coding interview level almost always require one combination:

1. **Hash map + doubly linked list** (LRU, LFU, any ordered cache)
2. **Hash map + heap** (top-K with dynamic updates, feed/timeline)
3. **Hash map + multiple hash maps with frequency tracking** (LFU variant)

The interview signal: whenever you hear "O(1) operations on a collection where order matters," think hash map plus an ordered structure that supports O(1) reordering — and the only standard data structure that gives O(1) insert/remove with a direct pointer is a doubly linked list.

## Key Takeaway

Every cache design problem is a hash map pointing into nodes in an ordered structure. Every feed problem is a merge K sorted lists problem with a max-heap. The data structures are always the same — the problem statement just describes the access pattern that determines which end you insert and evict from.

Implement the doubly linked list with sentinels every time. The sentinel pattern eliminates all null pointer checks and makes the insert/remove operations three to four lines each. Under interview pressure that simplicity is worth more than any cleverness.

---

**Series**: [Interview Patterns: Cracking FAANG+ in the AI Era](/series/interview-patterns-cracking-faang-in-the-ai-era/)

**Previous**: [Lesson 37: Concurrency Problems](/post/fundamentals/interview-concurrency/) — The questions Google loves

**Next**: [Lesson 39: Hard Composites](/post/fundamentals/interview-hard-composites/) — When one pattern isn't enough
