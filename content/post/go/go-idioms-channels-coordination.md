---
title: 'Lesson 16: Channels Are for Coordination — Stop using channels as fancy mutexes'
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
series: "Idiomatic Go"
lesson: 16
---

Channels are Go's most recognizable concurrency feature, and also one of the most misused. The moment engineers learn about them, there's a strong temptation to reach for a channel every time two goroutines need to interact. That instinct is wrong about half the time. Channels are for coordination — signaling events, distributing work, collecting results. They are not a universal replacement for shared state.

Rob Pike's line from his 2012 talk sums it up: "Do not communicate by sharing memory; share memory by communicating." That's a guiding philosophy, not an absolute rule.

## The Problem

The most common mistake I see is using a channel as a glorified lock — or worse, as a counter. It works, but it's solving the wrong problem with the wrong tool.

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

This works but it's overengineered. You're paying for goroutine scheduling and channel overhead to aggregate integers. There's no ownership transfer happening. There's no signaling. You're using a coordination primitive to do math.

A related antipattern is doing all work sequentially when it could be parallel:

```go
// WRONG — sequential when it could be parallel
func processURLs(urls []string) []Result {
    var results []Result
    for _, url := range urls {
        r := fetch(url) // each fetch blocks until complete
        results = append(results, r)
    }
    return results
}
```

If `fetch` takes 200ms and you have 50 URLs, you've spent 10 seconds doing something that could take 200ms.

## The Idiomatic Way

Use channels when you're actually coordinating — distributing work, signaling lifecycle events, building pipelines.

**For a counter, use atomic:**

```go
// RIGHT — atomic is simpler and faster for a counter
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

**For parallel work distribution, fan-out/fan-in is the right pattern:**

```go
// RIGHT — fan-out to workers, fan-in results
func processURLs(urls []string) []Result {
    jobs := make(chan string, len(urls))
    results := make(chan Result, len(urls))

    const numWorkers = 10
    for i := 0; i < numWorkers; i++ {
        go func() {
            for url := range jobs {
                results <- fetch(url)
            }
        }()
    }

    for _, url := range urls {
        jobs <- url
    }
    close(jobs) // signals workers that no more jobs are coming

    var out []Result
    for range urls {
        out = append(out, <-results)
    }
    return out
}
```

The `close(jobs)` call is the coordination signal. Workers ranging over `jobs` exit their loop automatically when the channel is closed and drained. No separate signal needed — the channel close does it.

**For goroutine cancellation, the done channel pattern:**

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

done := make(chan struct{})
startWorker(done)
// ... later
close(done) // broadcasts to all goroutines listening on done
```

`chan struct{}` uses zero bytes — pure signal, no data. And because `close` broadcasts, a hundred goroutines can all stop with a single `close(done)`. In modern Go you'd use `context.WithCancel`, but internally it's doing exactly this.

## In The Wild

Pipelines are where channels genuinely shine. Each stage reads from an input channel, transforms data, writes to an output channel. Stages compose naturally.

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
    for n := range square(generate(2, 3, 4, 5)) {
        fmt.Println(n) // 4, 9, 16, 25
    }
}
```

Each stage owns its goroutine and closes its output channel when done. The `range` on a channel stops automatically when the channel closes. This is channels at their most useful — not as a mutex, but as the connective tissue between concurrent stages.

## The Gotchas

**Forgetting to close.** If you forget `defer close(out)` in a pipeline stage, the downstream stage blocks forever waiting for values that never arrive. Always `defer close` when you're done sending.

**Buffered channels masking design problems.** A buffered channel decouples sender and receiver up to the buffer size. This is often used to "fix" deadlocks without understanding why the deadlock occurred. If your consumer is slow and the buffer fills, the block comes back anyway — just later, and harder to debug. Use buffered channels intentionally: buffer size should equal the number of in-flight jobs you're willing to queue.

**Large buffers hiding real backpressure.** A buffer of 1 is often enough to smooth over small timing differences. Large buffers are usually a sign that your producer and consumer are too far apart in speed, and you're hiding the problem rather than solving it.

## Key Takeaway

The mental model that unlocks channels: they're for *ownership transfer* and *event signaling*, not for protecting shared state. If you're passing data from one goroutine to another — use a channel. If you're signaling a lifecycle event (done, cancel, ready) — use a channel. If you're protecting a data structure that multiple goroutines read and write — use a mutex or atomic. The channel version of a counter doesn't transfer ownership; it aggregates. That's a job for `sync/atomic`. Get this distinction right and you'll stop force-fitting channels where they don't belong.

---

← [Previous: Goroutines Are Cheap, Not Free](/post/go/go-idioms-goroutines-cheap-not-free) | [Course Index](/post/go/) | [Next: select Is Elegant](/post/go/go-idioms-select) →
