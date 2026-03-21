---
title: "Lesson 29: sync.Cond — The coordination primitive nobody teaches"
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 29
keywords:
  - Go
  - golang
  - concurrency
  - sync.Cond
  - condition variable
  - goroutine coordination
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-10-01T00:00:00.000Z'
---

Every Go concurrency course covers goroutines, channels, mutexes, WaitGroups, and maybe semaphores. Very few touch `sync.Cond`. It's either treated as advanced or dismissed as unnecessary because "just use channels." But there's a whole class of coordination problem where channels are the wrong tool and `sync.Cond` is exactly right. Once you see the pattern, you'll recognize it everywhere — and you'll stop reaching for `time.Sleep` polling loops when you shouldn't.

This is a bonus lesson. Not because the topic is minor, but because it builds on mutexes (Lesson 6) and you really need to understand lock ownership before `sync.Cond` clicks.

## The Problem

Imagine you have a pool of workers consuming from a shared queue. The queue is empty. What do your consumers do while they wait?

The naive answer is: poll.

```go
// WRONG — busy-waiting burns CPU and introduces arbitrary latency
func consumer(queue *[]string, mu *sync.Mutex) {
    for {
        mu.Lock()
        if len(*queue) > 0 {
            item := (*queue)[0]
            *queue = (*queue)[1:]
            mu.Unlock()
            process(item)
        } else {
            mu.Unlock()
            time.Sleep(10 * time.Millisecond) // arbitrary delay
        }
    }
}
```

This is painful on multiple levels. You're burning CPU in a tight loop. The `time.Sleep` introduces up to 10ms of latency on every item even if the producer adds something immediately. And if you reduce the sleep to lower latency, you burn more CPU. There's no good value — you're trading one problem for another.

The other naive answer is: use a channel.

```go
// OK-ish — works, but falls apart with complex shared state
queue := make(chan string, 100)
```

Channels are great for the simple producer/consumer case. But what if the condition your goroutines need to wait on involves *multiple* pieces of shared state? What if you need consumers to wait only when the buffer is both non-empty *and* a priority flag is set? What if you need producers to wait when the buffer is full? You end up managing a tangle of channels to signal different conditions, and the code becomes harder to reason about than the original problem.

`sync.Cond` exists precisely for this: waiting until some shared state meets a condition, with instant wake-up when it does.

## The Idiomatic Way

A `sync.Cond` ties a condition variable to a `sync.Locker` (typically a `*sync.Mutex`). The three things you need to know:

- `c.Wait()` — atomically releases the lock and suspends the goroutine. When woken, it re-acquires the lock before returning. This is the key insight: Wait is never a simple sleep; it participates in your locking protocol.
- `c.Signal()` — wakes one goroutine that's sleeping in `Wait()`.
- `c.Broadcast()` — wakes all goroutines sleeping in `Wait()`.

Here's the same consumer, done right:

```go
// RIGHT — instant wake-up, no CPU waste
var (
    mu    sync.Mutex
    queue []string
    cond  = sync.NewCond(&mu)
)

func producer(items []string) {
    for _, item := range items {
        mu.Lock()
        queue = append(queue, item)
        cond.Signal() // wake one sleeping consumer
        mu.Unlock()
    }
}

func consumer(id int) {
    for {
        mu.Lock()
        for len(queue) == 0 { // NOTE: for loop, not if
            cond.Wait()
        }
        item := queue[0]
        queue = queue[1:]
        mu.Unlock()

        process(id, item)
    }
}
```

Notice the `for len(queue) == 0` — not `if`. This is non-negotiable. When a goroutine wakes from `Wait()`, the condition might *still* be false. Two reasons:

1. **Spurious wakeups** — the OS can wake threads without a Signal being sent. It's rare in practice but the Go runtime doesn't guarantee it won't happen.
2. **Lost race** — two consumers are sleeping, one item arrives, Signal wakes both (or Broadcast wakes both), and the first consumer takes the item. The second consumer wakes, re-checks the condition, and must go back to sleep.

The `for` loop handles both. Always. No exceptions.

## In The Wild

The most natural home for `sync.Cond` is a **bounded blocking queue** — a buffer where producers block when full and consumers block when empty. This is a classic threading textbook example, and `sync.Cond` is the tool it was designed for.

```go
type BoundedQueue struct {
    mu       sync.Mutex
    notFull  *sync.Cond
    notEmpty *sync.Cond
    buf      []any
    cap      int
}

func NewBoundedQueue(cap int) *BoundedQueue {
    q := &BoundedQueue{cap: cap, buf: make([]any, 0, cap)}
    q.notFull = sync.NewCond(&q.mu)
    q.notEmpty = sync.NewCond(&q.mu)
    return q
}

// Push blocks until there is space in the buffer.
func (q *BoundedQueue) Push(item any) {
    q.mu.Lock()
    defer q.mu.Unlock()

    for len(q.buf) == q.cap {
        q.notFull.Wait() // wait until a consumer drains space
    }
    q.buf = append(q.buf, item)
    q.notEmpty.Signal() // tell one consumer something arrived
}

// Pop blocks until there is an item in the buffer.
func (q *BoundedQueue) Pop() any {
    q.mu.Lock()
    defer q.mu.Unlock()

    for len(q.buf) == 0 {
        q.notEmpty.Wait() // wait until a producer adds something
    }
    item := q.buf[0]
    q.buf = q.buf[1:]
    q.notFull.Signal() // tell one producer there is now space
    return item
}
```

Two separate condition variables, both sharing the same mutex. `notFull` is signaled by consumers (they drained an item, so a producer can proceed). `notEmpty` is signaled by producers (they added an item, so a consumer can proceed). This is precise — each goroutine only wakes the goroutines that are waiting on a condition it just changed.

You could implement this with channels, but you'd need a buffered channel of size `cap` and you'd lose the ability to cleanly separate "wait for space" from "wait for item" without awkward select gymnastics. With `sync.Cond` and shared state, the logic reads almost like the specification.

## The Gotchas

**1. You must hold the lock when calling Wait()**

`Wait()` needs to release the lock atomically. If you don't hold it, you have a race condition between the check and the wait — the producer can Signal before you sleep, and you'll sleep forever.

```go
// WRONG — lock not held, data race, possible deadlock
cond.Wait() // panics: sync: unlock of unlocked mutex (or worse)

// RIGHT
mu.Lock()
for !condition() {
    cond.Wait()
}
mu.Unlock()
```

**2. Signal and Broadcast don't need the lock — but it's safer if you hold it**

Technically you can call `Signal()` or `Broadcast()` without holding the mutex. But if you call it while not holding the lock, you can signal before the waiter calls `Wait()`, and the signal is lost. The safe pattern is always: acquire lock, change state, signal, release lock. This guarantees the waiter either sees the updated state before sleeping, or wakes up after the signal.

```go
// WRONG — can miss the signal if consumer hasn't called Wait() yet
go func() {
    data = computeResult()
    cond.Signal() // signaling without holding the lock
}()

// RIGHT — signal after updating state, under the lock
go func() {
    mu.Lock()
    data = computeResult()
    ready = true
    cond.Signal()
    mu.Unlock()
}()
```

**3. Signal vs Broadcast — picking wrong has consequences**

Use `Signal` when exactly one goroutine can make progress. Use `Broadcast` when the condition change means *all* waiters might be able to proceed — or when you're not sure which one should wake.

A common misuse is using `Signal` for shutdown:

```go
// WRONG — only wakes one goroutine, the rest sleep forever on shutdown
func (q *BoundedQueue) Close() {
    mu.Lock()
    closed = true
    q.notEmpty.Signal() // only wakes one consumer
    mu.Unlock()
}

// RIGHT — wake everyone so they can observe the closed state
func (q *BoundedQueue) Close() {
    mu.Lock()
    closed = true
    q.notEmpty.Broadcast()
    q.notFull.Broadcast()
    mu.Unlock()
}
```

Whenever you're changing state that affects the condition for *multiple* waiting goroutines simultaneously — shutdown, configuration reloads, clearing an error state — reach for `Broadcast`.

**4. Don't copy a sync.Cond**

Same rule as `sync.Mutex`. The moment you copy it, the copy has an independent internal state and your synchronization breaks silently. Always pass `*sync.Cond` or embed it in a struct and pass a pointer to the struct.

## Key Takeaway

`sync.Cond` is not a general replacement for channels. Channels are still the right tool for most producer/consumer pipelines in Go — they're composable, cancellable, and idiomatic. But `sync.Cond` solves a specific problem that channels handle awkwardly: **multiple goroutines waiting on shared mutable state, where you need precise control over who wakes up and why**.

The mental model is simple: Lock → check condition in a for loop → Wait if false → do work → Unlock. Signalers lock → change state → Signal or Broadcast → unlock. The symmetry is the point. Get that right and `sync.Cond` is one of the cleaner synchronization tools in the standard library.

You'll rarely need it. When you do, you'll be glad it's there.

---

← [Lesson 28: Production Architecture](/post/go/go-concurrency-production-architecture/) | [Course Index](/post/go/) | 🎓 Course Complete!
