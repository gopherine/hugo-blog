---
title: "Lesson 2: Linked Lists — Almost never the right choice"
author: Atharva Pandey
series: "Data Structures for Production Engineers"
lesson: 2
keywords: [data structures, computer science, linked lists, pointers, memory]
tags: [fundamentals, data structures]
date: '2024-04-28T00:00:00.000Z'
---

Linked lists are the most over-taught data structure in computer science and the most under-used structure in production systems. I've reviewed hundreds of pull requests across distributed systems codebases, and I can count on one hand the times a linked list was genuinely the right call.

That said, understanding why linked lists are usually wrong teaches you something profound about how computers actually work. And there are a handful of situations where they're exactly right — and when those situations come up, you need to recognize them fast.

## How It Actually Works

A linked list stores elements as nodes, where each node holds a value and a pointer to the next node. There's no contiguous memory allocation — each node can live anywhere on the heap.

```go
package main

import "fmt"

type Node struct {
    Value int
    Next  *Node
}

type LinkedList struct {
    Head *Node
    Size int
}

func (l *LinkedList) Prepend(val int) {
    l.Head = &Node{Value: val, Next: l.Head}
    l.Size++
}

func (l *LinkedList) Delete(val int) bool {
    if l.Head == nil {
        return false
    }
    if l.Head.Value == val {
        l.Head = l.Head.Next
        l.Size--
        return true
    }
    curr := l.Head
    for curr.Next != nil {
        if curr.Next.Value == val {
            curr.Next = curr.Next.Next
            l.Size--
            return true
        }
        curr = curr.Next
    }
    return false
}

func main() {
    list := &LinkedList{}
    list.Prepend(3)
    list.Prepend(2)
    list.Prepend(1)

    for n := list.Head; n != nil; n = n.Next {
        fmt.Println(n.Value)
    }
}
```

Prepend is O(1) — you're just updating a pointer. Deletion at a known node (when you already have a pointer to the predecessor) is also O(1). These are the two things linked lists are genuinely good at.

Everything else is where they fall apart.

## When to Use It

The honest answer: almost never for application-level data structures. Slices beat linked lists in practice for most use cases because of cache efficiency. But there are real cases:

**Use a linked list when:**
- You have a very high rate of insertions and deletions at known positions, and the cost of copying (as in a slice) is genuinely prohibitive
- You're implementing an LRU cache — moving a node to the front is O(1) with a doubly linked list
- You're building an intrusive data structure inside a kernel or embedded system where you control allocation
- You need truly O(1) guaranteed prepend/append without amortization

**Don't use a linked list when:**
- You need random access by index (it's O(n))
- You're iterating over elements frequently (poor cache behavior)
- Your list is small (cache efficiency of slices wins below ~10K elements in most benchmarks)

## Production Example

The most legitimate use of linked lists in production is the doubly-linked list inside an LRU cache. Here's a stripped-down version of what Redis does internally:

```go
package main

import "fmt"

type LRUNode struct {
    Key   string
    Value string
    Prev  *LRUNode
    Next  *LRUNode
}

type LRUCache struct {
    capacity int
    cache    map[string]*LRUNode
    head     *LRUNode // most recently used
    tail     *LRUNode // least recently used
}

func NewLRUCache(capacity int) *LRUCache {
    head := &LRUNode{}
    tail := &LRUNode{}
    head.Next = tail
    tail.Prev = head
    return &LRUCache{
        capacity: capacity,
        cache:    make(map[string]*LRUNode),
        head:     head,
        tail:     tail,
    }
}

func (c *LRUCache) remove(node *LRUNode) {
    node.Prev.Next = node.Next
    node.Next.Prev = node.Prev
}

func (c *LRUCache) insertFront(node *LRUNode) {
    node.Next = c.head.Next
    node.Prev = c.head
    c.head.Next.Prev = node
    c.head.Next = node
}

func (c *LRUCache) Get(key string) (string, bool) {
    if node, ok := c.cache[key]; ok {
        c.remove(node)
        c.insertFront(node)
        return node.Value, true
    }
    return "", false
}

func (c *LRUCache) Put(key, value string) {
    if node, ok := c.cache[key]; ok {
        node.Value = value
        c.remove(node)
        c.insertFront(node)
        return
    }
    if len(c.cache) == c.capacity {
        lru := c.tail.Prev
        c.remove(lru)
        delete(c.cache, lru.Key)
    }
    node := &LRUNode{Key: key, Value: value}
    c.insertFront(node)
    c.cache[key] = node
}

func main() {
    cache := NewLRUCache(3)
    cache.Put("a", "1")
    cache.Put("b", "2")
    cache.Put("c", "3")
    cache.Get("a")       // "a" moves to front
    cache.Put("d", "4") // evicts "b"

    if _, ok := cache.Get("b"); !ok {
        fmt.Println("b was evicted (correct)")
    }
    if v, ok := cache.Get("a"); ok {
        fmt.Println("a is still present:", v)
    }
}
```

The key insight: the doubly-linked list gives O(1) moves because we can re-wire pointers without touching other nodes. Combined with a hash map for O(1) lookup, this is the classic O(1) LRU implementation. Neither a slice nor a tree gives you this combination cleanly.

## The Tradeoffs

**Cache performance is terrible.** Every `node.Next` dereference is potentially a cache miss. Each node is a separate heap allocation, scattered across memory. When you traverse a 10,000-element linked list, you're likely triggering thousands of L2/L3 cache misses. A slice of the same 10,000 integers would fit neatly into cache and be traversed in a fraction of the time.

```go
// This is deceptively slow for large lists
func sumList(head *Node) int64 {
    var total int64
    for n := head; n != nil; n = n.Next {
        total += int64(n.Value) // each n.Next may be a cache miss
    }
    return total
}

// This is much faster for the same data
func sumSlice(data []int) int64 {
    var total int64
    for _, v := range data { // sequential memory access, prefetcher loves it
        total += int64(v)
    }
    return total
}
```

**Memory overhead per element.** Each node in a doubly-linked list carries two pointers (16 bytes on 64-bit systems) plus whatever metadata the allocator adds. For a list of `int32` values (4 bytes), you're paying 5x overhead in pointers alone.

**GC pressure.** In Go, each node is a separate heap allocation that the garbage collector must track. A list of 100,000 nodes puts 100,000 objects on the GC's scan list. A slice holding 100,000 integers is a single allocation.

## Key Takeaway

Linked lists are not a bad data structure — they're a specialized one. The moment you need O(1) insertion or deletion at a position you already have a pointer to, and you don't need random access, a linked list is the right tool. LRU caches, certain scheduler queues, and some lock-free data structures genuinely need pointer-chased structures.

For everything else: use a slice. Your L1 cache will thank you.

---

*Previous: [Lesson 1: Arrays and Memory Layout](/post/fundamentals/ds-arrays-memory/)*

*Next: [Lesson 3: Hash Maps — O(1) with asterisks](/post/fundamentals/ds-hash-maps/)*
