---
title: 'Go Idioms: Channels Are for Coordination'
author: Atharva Pandey
keywords:
  - Go
  - golang
  - channels
  - goroutines
  - fan-out
  - fan-in
  - concurrency
  - done channel
  - buffered channels
tags:
  - Go tutorial
  - golang
date: '2025-05-05T00:00:00.000Z'
---
s most recognizable features, and also one of the most misunderstood. The moment people learn about them, there's a strong temptation to reach for a channel every time two goroutines need to interact. That instinct is wrong about half the time. Channels are for coordination — for signaling events, distributing work, and collecting results. They are not a universal replacement for shared state.

Rob Pike's famous line from his 2012 talk sums it up: "Do not communicate by sharing memory; share memory by communicating." That's a guiding philosophy, not an absolute rule. Once you understand what channels are actually good at, you'll use them precisely and stop forcing them where a mutex would be cleaner.

## The Channel Axioms

Before diving into patterns, internalize the fundamental behaviors:

- **A send on an unbuffered channel blocks until a receiver is ready.**
- **A receive on an unbuffered channel blocks until a sender sends.**
- **Closing a channel broadcasts to all receivers** — any goroutine blocked on a receive from a closed channel unblocks immediately and gets the zero value.
- **Sending on a closed channel panics.**
- **A nil channel blocks forever** — useful for disabling a case in a `select`.

These aren't trivia. They're the mechanics you'll use to build every pattern in this article.

## Fan-Out / Fan-In

Fan-out means distributing work from one source to multiple goroutines. Fan-in means collecting results from multiple goroutines back into one channel. Together they form the backbone of any pipeline-style concurrent program.

```go
// WRONG — doing all the work sequentially when it could be parallel
func processURLs(urls []string) []Result {
    var results []Result
    for _, url := range urls {
        r := fetch(url)  // each fetch blocks until complete
        results = append(results, r)
    }
    return results
}
```

This works, but if `fetch` takes 200ms and you have 50 URLs, you've spent 10 seconds doing something that could take 200ms.

```go
// RIGHT — fan-out to workers, fan-in results
func processURLs(urls []string) []Result {
    jobs := make(chan string, len(urls))
    results := make(chan Result, len(urls))

    // Fan-out: launch workers
    const numWorkers = 10
    for i := 0; i < numWorkers; i++ {
        go func() {
            for url := range jobs {
                results <- fetch(url)
            }
        }()
    }

    // Send all jobs
    for _, url := range urls {
        jobs <- url
    }
    close(jobs) // signals workers that no more jobs are coming

    // Fan-in: collect results
    var out []Result
    for range urls {
        out = append(out, <-results)
    }
    return out
}
```

The `close(jobs)` call is the coordination signal. When workers range over `jobs`, they exit their loop as soon as the channel is closed and drained. You don't need a separate signal — the channel close does it.

## The Done Channel for Cancellation

A common coordination need is telling a goroutine to stop. The idiomatic way before `context` was added to the standard library was a `done` channel. It's still worth understanding because it reveals why `context.Context` works the way it does.

```go
// WRONG — no way to stop the goroutine
func startWorker() {
    go func() {
        for {
            doWork()
            time.Sleep(time.Second)
        }
    }()
}
// This goroutine runs forever. You've leaked it.
```

```go
// RIGHT — done channel signals the goroutine to stop
func startWorker(done <-chan struct{}) {
    go func() {
        for {
            select {
            case <-done:
                return
            default:
                doWork()
                time.Sleep(time.Second)
            }
        }
    }()
}

// Caller controls the lifetime
done := make(chan struct{})
startWorker(done)
// ... later
close(done) // broadcasts to all goroutines listening on done
```

`chan struct{}` uses zero bytes. It carries no data — it's a pure signal. And because `close` broadcasts, you can have a hundred goroutines all listening on the same `done` channel and stop them all with a single `close(done)` call.

In modern Go you'd use `context.WithCancel` instead, but internally it's doing exactly this.

## Buffered vs Unbuffered: The Real Tradeoff

Buffered channels are often used to "fix" deadlocks without understanding why the deadlock occurred in the first place. Understand the actual tradeoff.

An **unbuffered channel** provides synchronization: the sender and receiver must meet. This is a guarantee — when a send completes, you know the receiver got the value.

A **buffered channel** decouples sender and receiver up to the buffer size. The sender can proceed without waiting for a receiver — until the buffer fills.

```go
// WRONG — using a buffered channel to paper over a design problem
func sendNotification(ch chan string, msg string) {
    ch <- msg // "works" because buffer absorbs it, but you've lost backpressure
}

// If the consumer is slow and the buffer fills, this blocks anyway.
// Worse, you might not notice the problem until load increases.
```

```go
// RIGHT — use buffered channels intentionally
// Buffer size = number of in-flight jobs you're willing to queue
jobs := make(chan Job, 100) // accept up to 100 queued jobs before blocking callers

// Or use buffered for "fire and forget" results where you know the count
results := make(chan Result, len(inputs)) // exactly one result per input
```

A buffer of 1 is often enough to smooth over small timing differences between goroutines. Large buffers are usually a sign that your producer and consumer are too far apart in speed.

## When Channels Are Overkill

This is the part people skip. Sometimes channels are the wrong tool entirely.

Suppose you want to count events across goroutines:

```go
// WRONG — using a channel as a counter
func countWithChannel(n int) int {
    ch := make(chan int, n)
    for i := 0; i < n; i++ {
        go func() {
            ch <- 1
        }()
    }
    total := 0
    for i := 0; i < n; i++ {
        total += <-ch
    }
    return total
}
```

This works but it's overengineered. You're using a channel to aggregate values that could just be an atomic integer. You're paying for goroutine scheduling and channel overhead for no benefit.

```go
// RIGHT — atomic is simpler and faster for a counter
import "sync/atomic"

func countWithAtomic(n int) int64 {
    var count int64
    var wg sync.WaitGroup
    for i := 0; i < n; i++ {
        wg.Add(1)
        go func() {
            defer wg.Done()
            atomic.AddInt64(&count, 1)
        }()
    }
    wg.Wait()
    return count
}
```

The rule of thumb: if you're passing ownership of data or signaling an event between goroutines, use a channel. If you're protecting shared state that multiple goroutines read and write, use a mutex or atomic. The channel version of the counter doesn't pass ownership — it aggregates. That's a mutex job.

## A Real Pattern: Pipeline Stages

Pipelines are where channels shine. Each stage reads from an input channel, transforms data, and writes to an output channel. Stages are connected by channels, and the whole thing is inherently concurrent.

```go
func generate(nums ...int) <-chan int {
    out := make(chan int)
    go func() {
        defer close(out)
        for _, n := range nums {
            out <- n
        }
    }()
    return out
}

func square(in <-chan int) <-chan int {
    out := make(chan int)
    go func() {
        defer close(out)
        for n := range in {
            out <- n * n
        }
    }()
    return out
}

func main() {
    // Pipeline: generate → square → print
    for n := range square(generate(2, 3, 4, 5)) {
        fmt.Println(n) // 4, 9, 16, 25
    }
}
```

Each stage owns its goroutine and closes its output channel when done. The `range` on a channel automatically stops when the channel is closed. This composability — where each stage is a function returning a channel — is channels at their most elegant.

Notice `defer close(out)` in each stage. This is critical. If you forget to close, the downstream stage blocks forever waiting for more values that never arrive.

## Channels Are Not Free

One more thing worth saying: channels have overhead. They involve goroutine scheduling, memory allocation, and synchronization primitives under the hood. For hot paths processing millions of items per second, measure before assuming a channel-based design is fast enough.

The right mental model: use channels when the coordination semantics are what you need — signaling, work distribution, pipeline composition. When you just need thread-safe state, reach for `sync.Mutex` or `sync/atomic`. The expressiveness of channels is a feature, not an excuse to use them everywhere.
