---
title: "Lesson 37: Concurrency Problems — The questions Google loves"
author: "Atharva Pandey"
keywords:
  - concurrency
  - goroutines
  - channels
  - mutex
  - print in order
  - print foobar alternately
  - building h2o
  - interview patterns
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 37
date: "2026-01-18T00:00:00.000Z"
---

Concurrency problems are the ones where an interviewer can tell immediately whether you actually understand concurrency or have just memorized solutions. They are also the problems where Go shines brightest — goroutines and channels are so expressive for synchronization that solutions which require dense mutex orchestration in Java become almost self-documenting in Go.

Google, in particular, loves these. I have heard this pattern described by multiple engineers who have been through their interview loops: "expect at least one problem where you need to coordinate goroutines." The underlying skill being tested is not just "can you prevent a race condition" — it is "do you understand which primitives to reach for, and can you reason about your solution's correctness?"

## The Pattern

Go gives you three primary concurrency primitives for interview problems:

- **Channels**: for passing values between goroutines and for signaling (a send is a signal; a receive blocks until signaled).
- **sync.WaitGroup**: for waiting until N goroutines complete.
- **sync.Mutex / sync.RWMutex**: for protecting shared state.

A fourth pattern worth knowing: **semaphore via buffered channel**. A buffered channel of capacity N acts as a semaphore — you can acquire (send) and release (receive). This idiom solves many classic concurrency problems cleanly.

```go
// Semaphore: allow at most N concurrent operations
sem := make(chan struct{}, N)
// Acquire
sem <- struct{}{}
// Release
<-sem
```

The most common mistake in concurrency interviews: reaching for `sync.Mutex` when a channel communicates the ordering constraint more directly. Mutexes protect state; channels communicate events. If your problem is about ordering or signaling, start with channels.

## Problem 1: Print in Order

Three functions `first`, `second`, and `third` must be called in order from three separate goroutines. Goroutines can be started in any order.

```go
type Foo struct {
    firstDone  chan struct{}
    secondDone chan struct{}
}

func NewFoo() *Foo {
    return &Foo{
        firstDone:  make(chan struct{}),
        secondDone: make(chan struct{}),
    }
}

func (f *Foo) First(printFirst func()) {
    printFirst()
    close(f.firstDone) // signal: first is done
}

func (f *Foo) Second(printSecond func()) {
    <-f.firstDone // wait for first
    printSecond()
    close(f.secondDone)
}

func (f *Foo) Third(printThird func()) {
    <-f.secondDone // wait for second
    printThird()
}
```

The key insight: `close` on a channel unblocks **all** goroutines waiting on that channel, not just one. This makes it perfect for signaling "event X has happened" to multiple potential waiters. A regular send only unblocks one receiver.

If you use a regular send instead of close (`f.firstDone <- struct{}{}`), you would need exactly one receiver. `close` is the right signal primitive when you want "broadcast" semantics.

## Problem 2: Print FooBar Alternately

Two goroutines: one prints "foo", one prints "bar". They must alternate: "foobarfoobarfoobar..." for n repetitions.

```go
type FooBar struct {
    n       int
    fooTurn chan struct{} // signal: foo's turn
    barTurn chan struct{} // signal: bar's turn
}

func NewFooBar(n int) *FooBar {
    fb := &FooBar{
        n:       n,
        fooTurn: make(chan struct{}, 1),
        barTurn: make(chan struct{}, 1),
    }
    fb.fooTurn <- struct{}{} // foo goes first
    return fb
}

func (fb *FooBar) Foo(printFoo func()) {
    for i := 0; i < fb.n; i++ {
        <-fb.fooTurn   // wait for our turn
        printFoo()
        fb.barTurn <- struct{}{} // signal bar
    }
}

func (fb *FooBar) Bar(printBar func()) {
    for i := 0; i < fb.n; i++ {
        <-fb.barTurn   // wait for our turn
        printBar()
        fb.fooTurn <- struct{}{} // signal foo
    }
}
```

Two buffered channels with capacity 1 act as a single "token" that passes back and forth. Only the goroutine holding the token can proceed. This is the classic alternating goroutine pattern.

Why buffered channels with capacity 1? Buffered sends do not block if there is space in the buffer. We seed `fooTurn` with one token. Foo grabs it, prints, drops a token into `barTurn`. Bar grabs it, prints, drops a token back into `fooTurn`. The token never accumulates (capacity 1 means the sender blocks if a token is already there), so the two goroutines alternate cleanly.

An alternative using `sync.Mutex` and `sync.Cond` works but is significantly more verbose. Channel-based solutions are idiomatic Go.

## Problem 3: Building H2O

Two types of goroutines: hydrogen and oxygen. You must release goroutines in groups where two hydrogens and one oxygen form a water molecule. No goroutine may pass the barrier before a complete molecule is formed.

```go
type H2O struct {
    hSem chan struct{} // semaphore: allows up to 2 hydrogen at a time
    oSem chan struct{} // semaphore: allows up to 1 oxygen at a time
    wg   sync.WaitGroup
}

func NewH2O() *H2O {
    return &H2O{
        hSem: make(chan struct{}, 2), // 2 H per molecule
        oSem: make(chan struct{}, 1), // 1 O per molecule
    }
}

func (h *H2O) Hydrogen(releaseHydrogen func()) {
    h.hSem <- struct{}{} // acquire H slot
    releaseHydrogen()
    // Coordinate molecule completion
    h.wg.Done()
}

func (h *H2O) Oxygen(releaseOxygen func()) {
    h.oSem <- struct{}{} // acquire O slot
    releaseOxygen()
    // Wait for 2 H + 1 O to complete, then reset
    h.wg.Wait()
    // Drain the semaphores to reset for the next molecule
    <-h.hSem
    <-h.hSem
    <-h.oSem
}
```

This naive approach has a race in coordination. The cleaner version uses a barrier:

```go
type H2OBarrier struct {
    hSem    chan struct{}
    oSem    chan struct{}
    barrier chan struct{} // released when a full molecule is assembled
}

func NewH2OBarrier() *H2OBarrier {
    return &H2OBarrier{
        hSem:    make(chan struct{}, 2),
        oSem:    make(chan struct{}, 1),
        barrier: make(chan struct{}),
    }
}

func (h *H2OBarrier) Hydrogen(releaseHydrogen func()) {
    h.hSem <- struct{}{} // I am one of the two H
    <-h.barrier          // wait for full molecule
    releaseHydrogen()
}

func (h *H2OBarrier) Oxygen(releaseOxygen func()) {
    h.oSem <- struct{}{}  // I am the O
    // I am the assembler: wait until 2 H slots are also filled
    h.hSem <- struct{}{}
    h.hSem <- struct{}{}
    // Full molecule assembled: release all three
    h.barrier <- struct{}{}
    h.barrier <- struct{}{}
    releaseOxygen()
    // Reset
    <-h.hSem
    <-h.hSem
    <-h.oSem
}
```

The conceptual approach is sound but the implementation needs care. The interview-ready version I reach for uses `semaphore` package semantics clearly:

```go
import "sync"

type H2OSync struct {
    mu      sync.Mutex
    hCount  int
    oCount  int
    hReady  chan struct{}
    oReady  chan struct{}
}

func NewH2OSync() *H2OSync {
    return &H2OSync{
        hReady: make(chan struct{}),
        oReady: make(chan struct{}),
    }
}

func (h *H2OSync) Hydrogen(releaseHydrogen func()) {
    h.mu.Lock()
    h.hCount++
    if h.hCount >= 2 && h.oCount >= 1 {
        h.hCount -= 2
        h.oCount--
        h.mu.Unlock()
        releaseHydrogen()
        h.hReady <- struct{}{} // wake a second H
        h.oReady <- struct{}{} // wake the O
        return
    }
    h.mu.Unlock()
    <-h.hReady
    releaseHydrogen()
}

func (h *H2OSync) Oxygen(releaseOxygen func()) {
    h.mu.Lock()
    h.oCount++
    if h.hCount >= 2 && h.oCount >= 1 {
        h.hCount -= 2
        h.oCount--
        h.mu.Unlock()
        <-h.hReady // consume H slot
        releaseOxygen()
        h.oReady <- struct{}{}
        return
    }
    h.mu.Unlock()
    <-h.oReady
    releaseOxygen()
}
```

H2O is one of those problems where the first solution you sketch will have a subtle race. Talk through it out loud. Interviewers expect you to find the issue and fix it — that thought process is the actual test.

## How to Recognize This Pattern

Concurrency interview problems cluster around a few themes:

1. **Ordering constraints**: function A must run before B before C. Use channels for signaling (`close` for broadcast, sends for single-receiver).
2. **Alternating / turn-taking**: two (or more) goroutines must take turns. Two buffered channels passing a single token back and forth.
3. **Barrier / molecule assembly**: N of type A and M of type B must rendezvous before any can proceed. Semaphores (buffered channels) with a barrier signal.
4. **Rate limiting / bounded concurrency**: at most K goroutines running simultaneously. Buffered channel of capacity K as a semaphore.

The mental model: channels are pipes that block unless there is room or a message. That blocking behavior is the synchronization mechanism. When you need "wait until something is ready," that is a channel receive. When you need "signal that something is ready," that is a channel send or close.

## Key Takeaway

Go's concurrency model is a gift in interviews. The primitives — channel, goroutine, close, buffered send — compose cleanly. Learn the four templates: ordered signaling with close, alternation with a token-passing buffered channel, semaphore with buffered channel capacity, and barrier with explicit release.

When you sketch a concurrency solution, immediately ask yourself: "what happens if goroutine X runs faster than expected, or slower?" That question surfaces most deadlocks and races before you even finish writing.

---

**Series**: [Interview Patterns: Cracking FAANG+ in the AI Era](/series/interview-patterns-cracking-faang-in-the-ai-era/)

**Previous**: [Lesson 36: Intervals](/post/fundamentals/interview-intervals/) — Sort by start, merge by end

**Next**: [Lesson 38: Design Problems](/post/fundamentals/interview-design/) — Build it from scratch in 30 minutes
