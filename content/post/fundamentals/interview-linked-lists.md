---
title: "Lesson 6: Linked Lists — Pointer Manipulation Is the Real Test"
author: "Atharva Pandey"
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 6
keywords:
  - interviews
  - algorithms
  - leetcode
  - FAANG
  - pattern
  - linked list
  - pointers
  - LRU cache
tags:
  - fundamentals
  - interviews
date: "2024-05-31T00:00:00.000Z"
---

Linked lists are where candidates reveal whether they actually understand pointers. You can read a hundred tutorials on reversing a linked list, but the first time you try to code it under pressure, you will likely lose a node, corrupt the list, or write an infinite loop. I know because it happened to me in a mock interview. The problem itself is not hard. The pointer mechanics are.

This lesson is about building the mental model that makes pointer operations feel mechanical rather than scary. Meta asks linked list questions constantly — their systems rely heavily on custom allocators and cache implementations, and linked lists are the natural test vehicle. Amazon uses the LRU cache variant to test both data structure design and implementation discipline.

## The Pattern

Linked list problems almost always reduce to pointer manipulation: rearranging `next` pointers to reverse, merge, or restructure a list. The key discipline is updating pointers in the right order so you never lose a reference. Draw the before/after state of the pointers on paper (or mentally) before you write a single line of code.

---

## Problem 1: Reverse Linked List

**The problem.** Given the head of a singly linked list, reverse the list and return the new head.

Input: `1 -> 2 -> 3 -> 4 -> 5` → `5 -> 4 -> 3 -> 2 -> 1`

```go
type ListNode struct {
    Val  int
    Next *ListNode
}
```

**Brute force.** Collect all values into a slice, then rebuild the list in reverse.

```go
func reverseListBrute(head *ListNode) *ListNode {
    vals := []int{}
    for n := head; n != nil; n = n.Next {
        vals = append(vals, n.Val)
    }
    dummy := &ListNode{}
    cur := dummy
    for i := len(vals) - 1; i >= 0; i-- {
        cur.Next = &ListNode{Val: vals[i]}
        cur = cur.Next
    }
    return dummy.Next
}
```

Time: O(n). Space: O(n). Correct but wastes space.

**Building toward optimal.** Reverse the pointers in place. Walk the list with three pointers: `prev` (starts nil), `curr` (starts at head), `next` (temporary to save the next node before we overwrite `curr.Next`). At each step:

1. Save `curr.Next` into `next` (don't lose the rest of the list)
2. Point `curr.Next` to `prev` (reverse the pointer)
3. Move `prev` to `curr`
4. Move `curr` to `next`

When `curr` is nil, `prev` is the new head.

```go
func reverseList(head *ListNode) *ListNode {
    var prev *ListNode
    curr := head

    for curr != nil {
        next := curr.Next  // save next before we overwrite
        curr.Next = prev   // reverse the pointer
        prev = curr        // advance prev
        curr = next        // advance curr
    }
    return prev
}
```

Time: O(n). Space: O(1).

**The mental model.** Think of it as flipping arrows one at a time. Before each flip, save where the arrow used to point. After all flips, the last node you processed (`prev`) is the new head. If you forget to save `next` before flipping, you've permanently lost the rest of the list — that's the bug that bites people under pressure.

**Recursive version** (worth knowing for interviews that ask for both):

```go
func reverseListRecursive(head *ListNode) *ListNode {
    if head == nil || head.Next == nil {
        return head
    }
    newHead := reverseListRecursive(head.Next)
    head.Next.Next = head // make the next node point back to current
    head.Next = nil       // current node now points to nothing
    return newHead
}
```

Time: O(n). Space: O(n) — recursion stack. The iterative version is preferred for large lists.

---

## Problem 2: Merge Two Sorted Lists

**The problem.** You are given the heads of two sorted linked lists. Merge them into one sorted list and return its head. Use the existing nodes, do not allocate new ones.

Input: `1 -> 2 -> 4`, `1 -> 3 -> 4` → `1 -> 1 -> 2 -> 3 -> 4 -> 4`

**Brute force.** Collect all values from both lists, sort them, build a new list.

```go
func mergeTwoListsBrute(list1 *ListNode, list2 *ListNode) *ListNode {
    vals := []int{}
    for n := list1; n != nil; n = n.Next {
        vals = append(vals, n.Val)
    }
    for n := list2; n != nil; n = n.Next {
        vals = append(vals, n.Val)
    }
    sort.Ints(vals)
    dummy := &ListNode{}
    cur := dummy
    for _, v := range vals {
        cur.Next = &ListNode{Val: v}
        cur = cur.Next
    }
    return dummy.Next
}
```

Time: O((m+n) log(m+n)). Space: O(m+n). We're throwing away the sorted structure and reallocating.

**Building toward optimal.** Both lists are already sorted — we just need to weave them together. Use a dummy head node to avoid special-casing the first element. Maintain a `current` pointer into the result list. At each step, compare the front elements of both lists, append the smaller one to `current`, and advance that list's pointer. When one list is exhausted, append the remainder of the other.

```go
func mergeTwoLists(list1 *ListNode, list2 *ListNode) *ListNode {
    dummy := &ListNode{} // sentinel head — no value, just a handle
    curr := dummy

    for list1 != nil && list2 != nil {
        if list1.Val <= list2.Val {
            curr.Next = list1
            list1 = list1.Next
        } else {
            curr.Next = list2
            list2 = list2.Next
        }
        curr = curr.Next
    }

    // attach the remaining list (at most one will be non-nil)
    if list1 != nil {
        curr.Next = list1
    } else {
        curr.Next = list2
    }

    return dummy.Next
}
```

Time: O(m+n). Space: O(1) — we reuse existing nodes.

**The dummy node pattern.** The dummy head is a standard linked list trick. Without it, you need to handle the first node of the result as a special case — which direction is `head`, which is `nil`, etc. The dummy node lets you always write `curr.Next = node` without checking if it's the first iteration. At the end, `dummy.Next` is the real head. Use this pattern in any problem where you're building a new list from scratch.

**How it extends.** Merge K Sorted Lists (a harder variant) uses this exact merge as a subroutine — either via repeated merging (O(nk log k) with a min-heap) or via divide-and-conquer where you merge pairs of lists recursively.

---

## Problem 3: LRU Cache

**The problem.** Design a data structure that follows the LRU (Least Recently Used) cache eviction policy. Implement `get(key)` and `put(key, value)`, both in O(1) time. When capacity is exceeded on a `put`, evict the least recently used item.

This is one of the most common system design-adjacent coding problems. I've seen it at Amazon, Meta, and Uber. It tests whether you can combine two data structures cleanly under time constraints.

**The insight.** We need O(1) access by key (hash map) and O(1) eviction of the LRU item (doubly linked list). The doubly linked list maintains order of use — most recently used at the head, least recently used at the tail. The hash map maps keys to their list nodes for O(1) access. On every get or put, move the accessed node to the head. On eviction, remove from the tail.

```go
type LRUNode struct {
    key, val   int
    prev, next *LRUNode
}

type LRUCache struct {
    capacity int
    cache    map[int]*LRUNode
    head     *LRUNode // dummy head (most recently used side)
    tail     *LRUNode // dummy tail (least recently used side)
}

func NewLRUCache(capacity int) LRUCache {
    head := &LRUNode{}
    tail := &LRUNode{}
    head.next = tail
    tail.prev = head
    return LRUCache{
        capacity: capacity,
        cache:    make(map[int]*LRUNode),
        head:     head,
        tail:     tail,
    }
}

// remove a node from its current position in the list
func (c *LRUCache) remove(node *LRUNode) {
    node.prev.next = node.next
    node.next.prev = node.prev
}

// insert a node right after the dummy head (mark as most recently used)
func (c *LRUCache) insertFront(node *LRUNode) {
    node.next = c.head.next
    node.prev = c.head
    c.head.next.prev = node
    c.head.next = node
}

func (c *LRUCache) Get(key int) int {
    if node, ok := c.cache[key]; ok {
        c.remove(node)
        c.insertFront(node) // mark as recently used
        return node.val
    }
    return -1
}

func (c *LRUCache) Put(key int, value int) {
    if node, ok := c.cache[key]; ok {
        node.val = value
        c.remove(node)
        c.insertFront(node)
        return
    }

    node := &LRUNode{key: key, val: value}
    c.cache[key] = node
    c.insertFront(node)

    if len(c.cache) > c.capacity {
        // evict the LRU node (just before the dummy tail)
        lru := c.tail.prev
        c.remove(lru)
        delete(c.cache, lru.key)
    }
}
```

Time: O(1) for both `get` and `put`. Space: O(capacity).

**Why doubly linked, not singly linked.** To remove a node in O(1), you need access to its predecessor. With a singly linked list, finding the predecessor requires an O(n) scan. The `prev` pointer in a doubly linked list gives you the predecessor in O(1). The dummy head and tail eliminate edge cases — the list is never truly empty, and you never have to check for nil before doing `node.prev.next = ...`.

**Why store the key in the node.** When you evict the LRU node from the tail, you need to delete its entry from the hash map. The only way to do that in O(1) is if the node knows its own key. This is the subtle design decision that people often miss — they store just the value in the node, then can't do the eviction efficiently.

---

## How to Recognize This Pattern

- "Reverse," "rotate," or "reorder" a linked list → pointer manipulation with prev/curr/next
- "Merge sorted lists" → dummy head, compare fronts, weave
- "O(1) get and evict by policy" → doubly linked list + hash map
- Problems involving list construction → always use a dummy head to simplify the first-insert case
- "Detect a cycle," "find the middle" → slow/fast pointer (Floyd's algorithm, a related technique)

Linked list problems test whether you can mentally track multiple pointer states simultaneously. The candidates who struggle are the ones who try to hold the entire state in their head instead of drawing the before/after pointer diagram first.

## Key Takeaway

Draw before you code. Linked list bugs come from losing a reference — you overwrote `curr.Next` before saving it, or you forgot to update `prev` when removing a node from the middle. Spend 30 seconds sketching the three-pointer diagram for reversal or the four-pointer manipulation for insertion/removal, and the code will follow naturally.

LRU Cache is the capstone: it combines a hash map (O(1) access) with a doubly linked list (O(1) ordering updates) and demands careful coordination between them. If you can implement it cleanly under interview pressure, you demonstrate both data structure knowledge and implementation discipline — two things interviewers are explicitly looking for.

---

*[← Lesson 5: Binary Search](/post/fundamentals/interview-binary-search/) | [Course Index](/series/interview-patterns-cracking-faang-in-the-ai-era/) | [Lesson 7: Recursion →](/post/fundamentals/interview-recursion/)*
