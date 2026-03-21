---
title: "Lesson 8: Fan-Out / Fan-In — Distribute, collect, don''t leak"
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 8
keywords:
  - Go
  - golang
  - concurrency
  - fan-out
  - fan-in
  - goroutines
  - done channel
  - worker pattern
  - channel merge
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-06-24T00:00:00.000Z'
---

There's a moment in every Go developer's journey where the serial loop stops being acceptable. You've got 200 URLs to fetch, 500 records to transform, 50 API calls to make — and you're doing them one at a time. The fix feels obvious: spin up goroutines. But spin them up without a plan and you've traded one problem for three.

Fan-out / fan-in is the pattern that makes parallel work manageable. Fan-out means distributing a stream of work across multiple goroutines. Fan-in means collecting their results into a single channel. Simple in concept, surprisingly easy to get wrong in practice.

## The Problem

The naive approach to parallelism is "just add a goroutine per item." Works fine in tests. Explodes in production.

```go
// WRONG — unbounded goroutines, results lost, no cancellation
func fetchAll(urls []string) []string {
    var results []string
    for _, url := range urls {
        go func(u string) {
            body, _ := http.Get(u)
            // WHERE does this result go?
            _ = body
        }(url)
    }
    // returns before goroutines finish — results is always empty
    return results
}
```

Two catastrophic problems here. First, the function returns before any goroutine finishes — you get nothing. Second, if `urls` has 10,000 entries, you've just launched 10,000 goroutines simultaneously. Each goroutine holds a stack (starts at 2–8 KB), has a TCP connection open, and the scheduler now has to juggle all of them. You'll saturate your file descriptor limit and memory long before you saturate the remote server's bandwidth.

Even when developers fix the "goroutines aren't joined" issue with a `WaitGroup`, they often miss result collection entirely:

```go
// STILL WRONG — joined, but results are racy and incomplete
func fetchAll(urls []string) []string {
    var results []string
    var wg sync.WaitGroup
    for _, url := range urls {
        wg.Add(1)
        go func(u string) {
            defer wg.Done()
            resp, _ := http.Get(u)
            defer resp.Body.Close()
            body, _ := io.ReadAll(resp.Body)
            results = append(results, string(body)) // DATA RACE
        }(url)
    }
    wg.Wait()
    return results
}
```

`results = append(results, ...)` from multiple goroutines without a lock is a data race. The race detector will catch this in testing, but I've seen code like this slip through because the team skipped `-race` in CI.

## The Idiomatic Way

Fan-out / fan-in solves both problems — bounded concurrency and safe result collection — with channels.

```go
// RIGHT — fan-out to a fixed pool, fan-in through a results channel
func fetchAll(urls []string) []string {
    jobs := make(chan string, len(urls))
    results := make(chan string, len(urls))

    // Fan-out: fixed number of workers
    const numWorkers = 20
    var wg sync.WaitGroup
    for i := 0; i < numWorkers; i++ {
        wg.Add(1)
        go func() {
            defer wg.Done()
            for url := range jobs {
                resp, err := http.Get(url)
                if err != nil {
                    results <- ""
                    continue
                }
                body, _ := io.ReadAll(resp.Body)
                resp.Body.Close()
                results <- string(body)
            }
        }()
    }

    // Send all jobs
    for _, url := range urls {
        jobs <- url
    }
    close(jobs) // signals workers: no more work coming

    // Close results once all workers are done
    go func() {
        wg.Wait()
        close(results)
    }()

    // Fan-in: collect from the single results channel
    var out []string
    for r := range results {
        out = append(out, r)
    }
    return out
}
```

The pattern has three distinct parts. A `jobs` channel carries the input. A pool of `numWorkers` goroutines reads from `jobs` and writes to `results`. A goroutine waits for all workers to finish, then closes `results` — which unblocks the `range results` loop in the main function. The key insight is that `close(jobs)` broadcasts to all workers at once: they all drain the remaining jobs and exit their `for range` loop cleanly. No sentinel values, no extra signaling.

Now add the done channel for cancellation — because production code needs a way out:

```go
// RIGHT — with cancellation via done channel
func fetchAllWithCancel(ctx context.Context, urls []string) ([]string, error) {
    jobs := make(chan string, len(urls))
    results := make(chan string, len(urls))

    var wg sync.WaitGroup
    const numWorkers = 20
    for i := 0; i < numWorkers; i++ {
        wg.Add(1)
        go func() {
            defer wg.Done()
            for {
                select {
                case url, ok := <-jobs:
                    if !ok {
                        return
                    }
                    req, _ := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
                    resp, err := http.DefaultClient.Do(req)
                    if err != nil {
                        results <- ""
                        continue
                    }
                    body, _ := io.ReadAll(resp.Body)
                    resp.Body.Close()
                    results <- string(body)
                case <-ctx.Done():
                    return
                }
            }
        }()
    }

    for _, url := range urls {
        select {
        case jobs <- url:
        case <-ctx.Done():
            close(jobs)
            return nil, ctx.Err()
        }
    }
    close(jobs)

    go func() {
        wg.Wait()
        close(results)
    }()

    var out []string
    for r := range results {
        out = append(out, r)
    }
    return out, nil
}
```

The `select` in the job-sending loop is critical — if the context cancels while you're still queuing work, you stop immediately instead of blocking on a full `jobs` channel.

## In The Wild

At a previous job we had an image-processing pipeline that resized uploaded photos to four dimensions — thumbnail, small, medium, large. The original code did this sequentially per upload. A 1MB source image took roughly 800ms total. With fan-out, each resize ran in a separate goroutine and the total dropped to ~220ms — limited by the slowest (largest) resize, not the sum of all four.

```go
type ResizeJob struct {
    Source []byte
    Width  int
    Label  string
}

type ResizeResult struct {
    Label string
    Data  []byte
    Err   error
}

func resizeVariants(ctx context.Context, src []byte) (map[string][]byte, error) {
    jobs := []ResizeJob{
        {src, 80, "thumbnail"},
        {src, 320, "small"},
        {src, 640, "medium"},
        {src, 1280, "large"},
    }

    results := make(chan ResizeResult, len(jobs))
    var wg sync.WaitGroup

    for _, j := range jobs {
        wg.Add(1)
        j := j // capture
        go func() {
            defer wg.Done()
            data, err := resizeImage(ctx, j.Source, j.Width)
            results <- ResizeResult{Label: j.Label, Data: data, Err: err}
        }()
    }

    wg.Wait()
    close(results)

    out := make(map[string][]byte)
    for r := range results {
        if r.Err != nil {
            return nil, fmt.Errorf("resize %s: %w", r.Label, r.Err)
        }
        out[r.Label] = r.Data
    }
    return out, nil
}
```

Since the number of jobs is known and small (exactly 4), I skip the worker pool and just fan out directly — one goroutine per job. When the set is bounded and small, this is fine. When it's unbounded (user-uploaded files, API batches), you need the pool.

## The Gotchas

**Forgetting `close(jobs)`** is probably the most common fan-out bug. Workers sit in `for url := range jobs` forever — goroutine leak. Always `close` the jobs channel after queuing all work.

**Closing results before all writers are done** is the mirror bug. If you `close(results)` while a worker is still trying to send, you get a panic: "send on closed channel." The goroutine that does `wg.Wait(); close(results)` must be the only entity that closes the channel, and it must wait for all writers.

**Not draining results when you return early.** If your fan-in loop returns early (say, you found what you needed), workers are blocked trying to send to a full `results` channel — goroutine leak. Either drain the channel in a goroutine or use a buffered channel large enough to absorb all remaining results.

**Loop variable capture** in Go versions before 1.22. The classic `for _, url := range urls { go func() { use(url) }() }` bug where all goroutines see the last value of `url`. Either pass `url` as a parameter or assign `url := url` before the goroutine. Go 1.22+ fixed loop variable semantics, but plenty of codebases still target older versions.

## Key Takeaway

Fan-out / fan-in is the right shape for "do N things in parallel, collect all results." The pattern has three non-negotiable rules: use a fixed number of workers (not one goroutine per item), close the jobs channel to signal workers when input is exhausted, and close the results channel only after all workers have finished writing. Violate any of these and you get either a panic, a goroutine leak, or missing results. Get them right and you've got a pattern you can drop into almost any parallel workload.

---

← [Previous: Race Conditions](/post/go/go-concurrency-race-conditions) | [Course Index](/post/go/) | [Next: Worker Pools](/post/go/go-concurrency-worker-pools) →
