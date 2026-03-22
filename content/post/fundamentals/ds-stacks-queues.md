---
title: "Lesson 4: Stacks and Queues — The structures hiding in every system"
author: Atharva Pandey
series: "Data Structures for Production Engineers"
lesson: 4
keywords: [data structures, computer science, stacks, queues, LIFO, FIFO, deque]
tags: [fundamentals, data structures]
date: '2024-05-28T00:00:00.000Z'
---

Every time you make a function call, your program uses a stack. Every HTTP request in a web server sits in a queue. Every undo operation in your IDE is a stack. These structures are so fundamental they're baked into the hardware itself — the CPU has dedicated stack instructions.

Yet I consistently see engineers reach for generic slices or channels when a properly implemented stack or queue would be both clearer and faster. Let me show you what these structures actually are, why they're the shape they are, and where they show up in the systems you work on every day.

## How It Actually Works

A **stack** is Last-In-First-Out (LIFO). Push onto the top, pop from the top. Think of a stack of plates — you always take from the top.

A **queue** is First-In-First-Out (FIFO). Enqueue at the back, dequeue from the front. Think of a line at a coffee shop — first person in is first person served.

Both can be implemented on top of a slice:

```go
package main

import "fmt"

// Stack — LIFO
type Stack[T any] struct {
    data []T
}

func (s *Stack[T]) Push(v T) {
    s.data = append(s.data, v)
}

func (s *Stack[T]) Pop() (T, bool) {
    if len(s.data) == 0 {
        var zero T
        return zero, false
    }
    top := s.data[len(s.data)-1]
    s.data = s.data[:len(s.data)-1]
    return top, true
}

func (s *Stack[T]) Peek() (T, bool) {
    if len(s.data) == 0 {
        var zero T
        return zero, false
    }
    return s.data[len(s.data)-1], true
}

// Queue — FIFO using a ring buffer (see Lesson 12 for the full treatment)
type Queue[T any] struct {
    data []T
}

func (q *Queue[T]) Enqueue(v T) {
    q.data = append(q.data, v)
}

func (q *Queue[T]) Dequeue() (T, bool) {
    if len(q.data) == 0 {
        var zero T
        return zero, false
    }
    front := q.data[0]
    q.data = q.data[1:]
    return front, true
}

func main() {
    s := &Stack[int]{}
    s.Push(1); s.Push(2); s.Push(3)
    for {
        v, ok := s.Pop()
        if !ok { break }
        fmt.Println(v) // 3, 2, 1
    }

    q := &Queue[string]{}
    q.Enqueue("first"); q.Enqueue("second"); q.Enqueue("third")
    for {
        v, ok := q.Dequeue()
        if !ok { break }
        fmt.Println(v) // first, second, third
    }
}
```

One warning: the naive queue above has a subtle performance problem — `q.data[1:]` doesn't free memory, it just moves the slice header. Over time, the backing array grows without bound. For production queues, use a ring buffer (Lesson 12) or Go's `container/list`.

## When to Use It

**Stack use cases:**
- Function call management (the call stack — this is literally what the CPU does)
- Expression evaluation and parsing (calculator, JSON parser, HTML parser)
- Backtracking algorithms (DFS, undo/redo systems)
- Syntax validation (balanced parentheses, bracket matching)
- Browser history navigation

**Queue use cases:**
- Task queues and job scheduling
- BFS graph traversal
- Rate-limited request processing
- Event streams and message pipelines
- Buffering between producer and consumer

## Production Example

Here's a real pattern I've used: a depth-first search over a service dependency graph using an explicit stack rather than recursion. Recursive DFS blows the call stack for deep graphs; an explicit stack lets you handle arbitrary depth safely.

```go
package main

import "fmt"

type Graph struct {
    adjacency map[string][]string
}

func NewGraph() *Graph {
    return &Graph{adjacency: make(map[string][]string)}
}

func (g *Graph) AddEdge(from, to string) {
    g.adjacency[from] = append(g.adjacency[from], to)
}

// IterativeDFS finds all nodes reachable from start
func (g *Graph) IterativeDFS(start string) []string {
    visited := make(map[string]bool)
    order := []string{}

    stack := []string{start}

    for len(stack) > 0 {
        // Pop
        node := stack[len(stack)-1]
        stack = stack[:len(stack)-1]

        if visited[node] {
            continue
        }
        visited[node] = true
        order = append(order, node)

        for _, neighbor := range g.adjacency[node] {
            if !visited[neighbor] {
                stack = append(stack, neighbor)
            }
        }
    }
    return order
}

func main() {
    g := NewGraph()
    // Service dependency graph
    g.AddEdge("api-gateway", "user-service")
    g.AddEdge("api-gateway", "order-service")
    g.AddEdge("order-service", "inventory-service")
    g.AddEdge("order-service", "payment-service")
    g.AddEdge("payment-service", "fraud-detection")

    reachable := g.IterativeDFS("api-gateway")
    fmt.Println("Services reachable from api-gateway:")
    for _, s := range reachable {
        fmt.Println(" -", s)
    }
}
```

This pattern is directly applicable to dependency resolution (think Kubernetes reconciliation loops, Terraform plan/apply, package managers), circuit breaker cascade analysis, and service mesh topology exploration.

Now let's look at a queue pattern — a simple bounded work queue for background processing:

```go
package main

import (
    "fmt"
    "sync"
)

type Job struct {
    ID      int
    Payload string
}

type BoundedQueue struct {
    ch chan Job
    wg sync.WaitGroup
}

func NewBoundedQueue(capacity, workers int) *BoundedQueue {
    q := &BoundedQueue{ch: make(chan Job, capacity)}
    for i := 0; i < workers; i++ {
        q.wg.Add(1)
        go func(id int) {
            defer q.wg.Done()
            for job := range q.ch {
                fmt.Printf("worker %d processing job %d: %s\n", id, job.ID, job.Payload)
            }
        }(i)
    }
    return q
}

func (q *BoundedQueue) Submit(j Job) {
    q.ch <- j // blocks if queue is full — natural backpressure
}

func (q *BoundedQueue) Close() {
    close(q.ch)
    q.wg.Wait()
}

func main() {
    q := NewBoundedQueue(100, 4)
    for i := 0; i < 10; i++ {
        q.Submit(Job{ID: i, Payload: fmt.Sprintf("task-%d", i)})
    }
    q.Close()
}
```

Go channels are queues. Understanding that mental model makes concurrent Go code much clearer.

## The Tradeoffs

**Stacks and recursion depth.** Go's goroutine stack starts at 8KB and grows dynamically (up to 1GB by default). For most recursive algorithms this is fine. But if you're doing DFS on a graph with 100K nodes in the worst case (a degenerate path), the recursive version could grow the stack substantially. The iterative version using an explicit stack has predictable memory behavior.

**Queue with O(1) dequeue.** The naive slice queue above is O(n) for dequeue because of the copy-shift. For production use, Go's `container/ring` or a hand-rolled ring buffer (Lesson 12) gives you O(1) enqueue and dequeue. For most cases with moderate load, a channel-backed queue is the right Go idiom.

**Deques.** Sometimes you need both stack and queue semantics — insert and remove from either end. Go doesn't have a built-in deque, but `container/list` (doubly linked list) handles this. Just remember the cache-locality tradeoffs from Lesson 2.

## Key Takeaway

Stacks and queues are not academic exercises — they're the foundation of call stacks, work queues, event pipelines, and graph traversal. The key insight is this: **the shape of your data structure encodes the order guarantee you need**. When you need LIFO (backtracking, undo, DFS), use a stack. When you need FIFO (fairness, ordering, BFS), use a queue. Reaching for a generic slice without that mental framing means you're encoding ordering semantics in your application logic instead of the data structure — which is always messier.

---

*Previous: [Lesson 3: Hash Maps — O(1) with asterisks](/post/fundamentals/ds-hash-maps/)*

*Next: [Lesson 5: Trees and BSTs — Why your database is a tree](/post/fundamentals/ds-trees-bst/)*
