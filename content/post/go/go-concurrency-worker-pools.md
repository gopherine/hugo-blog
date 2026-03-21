---
title: 'Lesson 9: Worker Pools — Bounded concurrency or bust'
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 9
keywords:
  - Go
  - golang
  - concurrency
  - worker pool
  - goroutine pool
  - bounded concurrency
  - job channel
  - result channel
  - graceful drain
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-07-10T00:00:00.000Z'
---

Production Go codebases that run fine in staging will sometimes crater under real traffic. More often than not, the cause is unbounded goroutine creation. Some background job processor spins up a goroutine per message. The queue backs up. Suddenly there are 50,000 goroutines fighting for CPU, exhausting file descriptors, allocating gigabytes of stack space. The service falls over. The incident post-mortem says "we didn't expect this load." The real cause: no worker pool.

A worker pool is the single most important pattern for any Go service that processes work from a queue, a channel, or a batch. It's not complicated. But it needs to be done right — the graceful drain part especially.

## The Problem

The impulse is understandable: `for each item, go func() { process(item) }()`. It's one line. It parallelizes everything. In tests, it's blazing fast. In production with variable load, it's a time bomb.

```go
// WRONG — unbounded goroutine creation per message
func processMessages(broker MessageBroker) {
    for {
        msg, err := broker.Receive()
        if err != nil {
            log.Printf("receive error: %v", err)
            continue
        }
        go func(m Message) { // new goroutine for EVERY message
            if err := handleMessage(m); err != nil {
                log.Printf("handle error: %v", err)
            }
        }(msg)
    }
}
```

At 10 messages/second this is fine. At 10,000 messages/second — or during a queue buildup after a downstream outage — you're creating goroutines faster than they can drain. The runtime's scheduler gets overwhelmed. Memory climbs. The OOM killer shows up. I've seen this exact pattern take down a service that had been running for two years when a downstream dependency had a 3-minute outage.

The second mistake is ignoring results entirely, which masks processing errors:

```go
// ALSO WRONG — no result channel, errors silently dropped
func processAll(items []Item) {
    var wg sync.WaitGroup
    for _, item := range items {
        wg.Add(1)
        go func(it Item) {
            defer wg.Done()
            _ = process(it) // error thrown away
        }(item)
    }
    wg.Wait()
}
```

Errors disappear into the void. You think everything succeeded. It didn't.

## The Idiomatic Way

A worker pool has four components: a jobs channel, a results channel, a fixed set of worker goroutines, and a clean shutdown sequence. Here's the canonical form:

```go
// RIGHT — fixed worker pool with job and result channels
type Job struct {
    ID      int
    Payload string
}

type Result struct {
    JobID int
    Value string
    Err   error
}

func RunPool(ctx context.Context, numWorkers int, jobs <-chan Job) <-chan Result {
    results := make(chan Result, numWorkers)

    var wg sync.WaitGroup
    for i := 0; i < numWorkers; i++ {
        wg.Add(1)
        go func() {
            defer wg.Done()
            for {
                select {
                case job, ok := <-jobs:
                    if !ok {
                        return // channel closed, worker exits cleanly
                    }
                    val, err := process(ctx, job.Payload)
                    results <- Result{JobID: job.ID, Value: val, Err: err}
                case <-ctx.Done():
                    return
                }
            }
        }()
    }

    // Close results only after all workers are done
    go func() {
        wg.Wait()
        close(results)
    }()

    return results
}
```

The caller controls the `jobs` channel — they send work and then `close(jobs)` when done. Workers exit naturally when `jobs` is closed. The anonymous goroutine closes `results` after the `WaitGroup` reaches zero, which lets the caller range over `results` safely.

Now wire it up end-to-end with a clean caller:

```go
// RIGHT — complete usage with result collection and error handling
func main() {
    ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
    defer cancel()

    const numWorkers = 10
    jobsCh := make(chan Job, numWorkers*2) // small buffer to smooth bursts

    resultsCh := RunPool(ctx, numWorkers, jobsCh)

    // Feed jobs in a goroutine so we can collect results concurrently
    go func() {
        defer close(jobsCh)
        for i, payload := range loadPayloads() {
            select {
            case jobsCh <- Job{ID: i, Payload: payload}:
            case <-ctx.Done():
                return
            }
        }
    }()

    // Collect results — range exits when results is closed
    var errs []error
    for r := range resultsCh {
        if r.Err != nil {
            errs = append(errs, fmt.Errorf("job %d: %w", r.JobID, r.Err))
            continue
        }
        fmt.Printf("job %d → %s\n", r.JobID, r.Value)
    }

    if len(errs) > 0 {
        log.Printf("%d jobs failed", len(errs))
    }
}
```

Two things I want to highlight: the `jobs` channel has a small buffer (`numWorkers * 2`). This lets the producer run slightly ahead of workers without blocking — it smooths over small timing differences. Don't make it huge; a huge buffer just hides backpressure. And the `select` in the job-feeding goroutine bails out if the context cancels — you don't want to block feeding jobs when the whole system is shutting down.

## In The Wild

We built a document-indexing service that needed to process user-uploaded PDFs through three stages: text extraction, embedding generation (via an external API), and vector store insertion. Each stage had different throughput characteristics — extraction was CPU-bound at ~50ms/doc, embedding was network-bound at ~300ms/doc, insertion was ~20ms/doc.

We ran separate worker pools per stage, sized to match their bottleneck:

```go
type Pipeline struct {
    extractWorkers int // CPU-bound: match GOMAXPROCS
    embedWorkers   int // Network-bound: much higher
    insertWorkers  int // Fast, small pool
}

func (p *Pipeline) Run(ctx context.Context, docs <-chan Document) <-chan IndexResult {
    // Stage 1: extract text
    extracted := make(chan ExtractedDoc, p.extractWorkers)
    go p.runExtractPool(ctx, docs, extracted, p.extractWorkers)

    // Stage 2: generate embeddings
    embedded := make(chan EmbeddedDoc, p.embedWorkers)
    go p.runEmbedPool(ctx, extracted, embedded, p.embedWorkers)

    // Stage 3: insert into vector store
    results := make(chan IndexResult, p.insertWorkers)
    go p.runInsertPool(ctx, embedded, results, p.insertWorkers)

    return results
}
```

Each pool is isolated — if the embedding API slows down, `embedded` fills up and `extracted` backs up, which eventually backs up `docs`. The system applies backpressure automatically rather than piling up unbounded work in memory.

## The Gotchas

**Sizing the pool wrong.** For CPU-bound work, `runtime.GOMAXPROCS(0)` is your upper bound — more workers just context-switch. For I/O-bound work (network, disk), you can go higher, but benchmark it. A pool of 100 for HTTP calls might be fine; a pool of 10,000 is rarely better than 200 and burns resources.

**Graceful drain under shutdown.** When you receive SIGTERM, you want to stop accepting new jobs but finish the ones in flight. The pattern: stop feeding the `jobs` channel, let workers drain it, wait for `WaitGroup` to reach zero. Do NOT `close(jobs)` while a goroutine might still be sending to it — that panics. Use `sync.Once` if multiple paths might trigger shutdown.

**Panics inside workers killing the pool.** An unrecovered panic in one goroutine takes down the whole program. Wrap worker bodies with `defer func() { if r := recover(); r != nil { log.Printf("worker panic: %v", r) } }()` if any of the work you're doing can panic.

**Blocking inside a worker on a full results channel.** If your results channel is unbuffered or its consumer is slow, workers will block on `results <- r`. Other workers continue filling up pending jobs. Eventually the jobs channel fills, the producer blocks, and you've got a deadlock. Make `results` buffered — at minimum `numWorkers` deep, so no worker ever blocks sending its result.

## Key Takeaway

The rule is simple: every service that processes work from a channel, queue, or stream needs a worker pool with a fixed, tuned size. "Unbounded goroutines" is not a scalability strategy — it's a deferred incident. Get the four components right (jobs channel, results channel, fixed workers, WaitGroup-gated results close), size the pool to match your workload type (CPU vs. I/O bound), and implement graceful drain so in-flight work completes on shutdown. That's it. A 40-line pool handles millions of jobs safely. The version without a pool falls over at a few thousand.

---

← [Previous: Fan-Out / Fan-In](/post/go/go-concurrency-fan-out-fan-in) | [Course Index](/post/go/) | [Next: Pipelines and Stage Isolation](/post/go/go-concurrency-pipelines) →
