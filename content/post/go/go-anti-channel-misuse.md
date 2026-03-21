---
title: "Lesson 4: Channel Misuse — You used a channel where a mutex would do"
author: Atharva Pandey
series: "Go Anti-Patterns & Code Smells"
lesson: 4
keywords:
  - Go
  - golang
  - anti-patterns
  - code quality
tags:
  - Go tutorial
  - golang
  - code quality
date: '2024-10-15T00:00:00.000Z'
---

Go's channels are genuinely great. They make certain concurrent programming patterns — pipelines, fan-out, fan-in, worker pools — natural and readable. Because they are idiomatic and distinctly Go-like, there is a tendency among Go developers to reach for them first whenever concurrency is involved. The result is code that uses channels to protect shared state, which is what mutexes are for, or code that passes one value through a channel with ceremony that could be replaced by a function call.

The Go proverb says "don't communicate by sharing memory; share memory by communicating." But the corollary — which is less often quoted — is that the right tool depends on what you are doing. Protecting a counter is sharing memory; use a mutex. Distributing work across goroutines is communicating; use a channel.

## The Problem

Using a channel to protect shared state when a mutex is simpler and more direct:

```go
// WRONG — channel used as a mutex replacement
type SafeCounter struct {
    ch chan struct{}
    n  int
}

func NewSafeCounter() *SafeCounter {
    c := &SafeCounter{ch: make(chan struct{}, 1)}
    c.ch <- struct{}{} // "unlock" — put token in channel
    return c
}

func (c *SafeCounter) Increment() {
    <-c.ch         // "lock" — take token
    c.n++
    c.ch <- struct{}{} // "unlock" — return token
}

func (c *SafeCounter) Value() int {
    <-c.ch
    defer func() { c.ch <- struct{}{} }()
    return c.n
}
```

This works, technically. It is also significantly slower than a mutex, allocates a goroutine scheduler data structure, and confuses every reader who expects a channel to be about communication. A mutex is not just idiomatic here — it is faster and clearer.

A second misuse: using a channel as a future or one-time synchronization point when `sync.WaitGroup` or a simple blocking call would do:

```go
// WRONG — channel used to wait for a single goroutine result
func processFile(path string) (Result, error) {
    resultCh := make(chan Result, 1)
    errCh    := make(chan error, 1)

    go func() {
        result, err := doProcess(path)
        if err != nil {
            errCh <- err
            return
        }
        resultCh <- result
    }()

    select {
    case result := <-resultCh:
        return result, nil
    case err := <-errCh:
        return Result{}, err
    }
}
```

There is no reason for the goroutine here at all — `doProcess` is called once, and the caller blocks immediately waiting for it. This is equivalent to calling `doProcess(path)` directly, with extra goroutine overhead and two channel allocations.

## The Idiomatic Way

Use a mutex for protecting shared state. Use `sync/atomic` for single integer counters. Use channels for distributing work or signaling between goroutines that are genuinely running concurrently:

```go
// RIGHT — mutex for shared state, atomic for counters
type SafeCounter struct {
    mu sync.Mutex
    n  int
}

func (c *SafeCounter) Increment() {
    c.mu.Lock()
    c.n++
    c.mu.Unlock()
}

func (c *SafeCounter) Value() int {
    c.mu.Lock()
    defer c.mu.Unlock()
    return c.n
}

// Or, for a single counter with no other shared state:
type AtomicCounter struct {
    n atomic.Int64
}

func (c *AtomicCounter) Increment() { c.n.Add(1) }
func (c *AtomicCounter) Value() int64 { return c.n.Load() }
```

For work distribution where channels are the right tool — a genuine producer-consumer pattern:

```go
// RIGHT — channels for genuine work distribution
func processFiles(paths []string, workers int) []Result {
    jobs := make(chan string, len(paths))
    results := make(chan Result, len(paths))

    // Start workers
    var wg sync.WaitGroup
    for i := 0; i < workers; i++ {
        wg.Add(1)
        go func() {
            defer wg.Done()
            for path := range jobs {
                result, err := doProcess(path)
                if err != nil {
                    results <- Result{Error: err, Path: path}
                    continue
                }
                results <- result
            }
        }()
    }

    // Send jobs
    for _, path := range paths {
        jobs <- path
    }
    close(jobs)

    // Collect results
    go func() {
        wg.Wait()
        close(results)
    }()

    var out []Result
    for r := range results {
        out = append(out, r)
    }
    return out
}
```

This is the genuine use case for channels: multiple goroutines are running concurrently, and work is being distributed and collected.

## In The Wild

**`sync.WaitGroup` for parallel fan-out.** When you want to run N tasks in parallel and wait for all of them to complete, `sync.WaitGroup` is almost always cleaner than a done channel:

```go
// RIGHT — WaitGroup for parallel tasks with no results
var wg sync.WaitGroup
for _, item := range items {
    wg.Add(1)
    go func(i Item) {
        defer wg.Done()
        process(i)
    }(item)
}
wg.Wait()
```

**`errgroup.Group` for parallel tasks with errors.** `golang.org/x/sync/errgroup` extends `WaitGroup` with error propagation and optional context cancellation:

```go
// RIGHT — errgroup for parallel tasks that can fail
g, ctx := errgroup.WithContext(context.Background())
for _, item := range items {
    item := item
    g.Go(func() error {
        return processWithContext(ctx, item)
    })
}
if err := g.Wait(); err != nil {
    return fmt.Errorf("parallel processing: %w", err)
}
```

**Unbuffered channels for signaling.** An unbuffered channel send blocks until the receiver is ready — this is a synchronization primitive, not a data transport. Use it for signaling events (shutdown, ready, done) rather than sending data that needs to be buffered:

```go
shutdown := make(chan struct{})
go func() {
    <-shutdown // block until shutdown signal
    cleanup()
}()
// ... later
close(shutdown) // signal shutdown to all receivers simultaneously
```

## The Gotchas

**Goroutine leaks from channels nobody reads.** A goroutine that is blocked on a channel send where no receiver will ever appear is leaked forever. This is the most common goroutine leak pattern. Always ensure either the sender or the receiver handles the case where the other side is gone — typically via a `select` with a `ctx.Done()` case.

**Deadlock from wrong buffer sizes.** A buffered channel with capacity N will deadlock when you try to send the N+1th item without a reader. Sizing the buffer correctly requires knowing how many items will be sent. When in doubt, use an unbuffered channel with explicit goroutines on both sides, which makes the backpressure explicit.

**Channel direction in function signatures.** When passing channels to functions, specify direction to make intent clear and catch errors at compile time:

```go
func producer(out chan<- int) { /* can only send */ }
func consumer(in <-chan int)  { /* can only receive */ }
```

**The channel-as-semaphore pattern.** A buffered channel of capacity N used to limit concurrency (send before starting work, receive when done) is a well-known pattern. It is one of the legitimate uses of a channel where a mutex would not be the right tool:

```go
sem := make(chan struct{}, maxConcurrent) // semaphore
for _, item := range items {
    sem <- struct{}{}
    go func(i Item) {
        defer func() { <-sem }()
        process(i)
    }(item)
}
```

## Key Takeaway

Channels are for communication between goroutines — distributing work, signaling events, streaming results. Mutexes are for protecting shared state that multiple goroutines access. Using a channel to protect a counter or to wait for a single function call is not idiomatic Go — it is a misapplication of a powerful tool that adds complexity without benefit. Reach for `sync.Mutex`, `sync.RWMutex`, `sync/atomic`, `sync.WaitGroup`, and `errgroup` first; reach for channels when you have a genuine producer-consumer or pipeline structure.

---

**Go Anti-Patterns & Code Smells**

Previous: [Lesson 3: Global Mutable State — The variable that breaks every test](/post/go/go-anti-global-state/)
Next: [Lesson 5: Ignoring Context — The cancellation nobody checked](/post/go/go-anti-ignoring-context/)
