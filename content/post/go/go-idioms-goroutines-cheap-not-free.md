---
title: 'Go Idioms: Goroutines Are Cheap, Not Free'
author: Atharva Pandey
keywords:
  - Go
  - golang
  - goroutines
  - goroutine leak
  - concurrency
  - errgroup
  - memory
  - performance
tags:
  - Go tutorial
  - golang
date: '2025-08-11T00:00:00.000Z'
---
"cheap" is not "free." Every goroutine consumes memory, every running goroutine takes scheduler time, and every goroutine that you start and never stop is a leak. Goroutine leaks are one of the most common performance and correctness problems in production Go services, and they are subtle because they do not crash your program — they just slowly eat your memory until the process OOMs or the scheduler slows to a crawl.

## The Stack: Small But Growable

A goroutine starts with a 2KB stack. If the function needs more — through deep recursion or large local variables — the runtime grows the stack dynamically, up to a default maximum of 1GB (configurable with `GOMAXSTACK`). This growth happens automatically and transparently.

The practical implication: you do not need to worry about stack sizing for most goroutines. But a goroutine with a deeply recursive algorithm can use much more than 2KB, and a million goroutines each using 10KB is 10GB of memory. Cheap per goroutine can still mean expensive in aggregate.

## The Goroutine Leak

Here is the classic leak pattern:

```go
// WRONG — this goroutine leaks
func processRequest(input string) string {
    resultCh := make(chan string)

    go func() {
        result := expensiveComputation(input)
        resultCh <- result // blocks here if nobody reads
    }()

    select {
    case result := <-resultCh:
        return result
    case <-time.After(1 * time.Second):
        return "timeout"
    }
}
```

This looks reasonable. You start a goroutine to do work, use a select to wait for either the result or a timeout. The problem: when the timeout fires, `processRequest` returns. But the goroutine is still running, still trying to send on `resultCh`. Since nobody is reading from `resultCh`, the goroutine blocks on the send forever. It never exits.

Call `processRequest` under load — say, 1000 requests per second with occasional timeouts — and you are creating goroutines faster than they can exit. Memory climbs, the scheduler has more goroutines to manage, and eventually things get slow or the process dies.

The fix is to use a buffered channel so the goroutine can send even if nobody is listening, or use a context to signal cancellation:

```go
// RIGHT — buffered channel allows goroutine to exit
func processRequest(input string) string {
    resultCh := make(chan string, 1) // buffered with capacity 1

    go func() {
        result := expensiveComputation(input)
        resultCh <- result // does not block, there is room in the buffer
    }()

    select {
    case result := <-resultCh:
        return result
    case <-time.After(1 * time.Second):
        return "timeout"
    }
}
```

With a buffered channel of size 1, the goroutine can always send its result and exit, even if the caller has already returned due to timeout. The result sits in the buffer until the garbage collector cleans up the channel.

## Context-Based Cancellation: The Real Fix

The buffered channel approach stops the leak, but it does not stop the computation. If `expensiveComputation` takes 30 seconds, the goroutine still runs for 30 seconds after the timeout. In a real service, that means CPU and memory consumed for work that nobody will use.

The correct pattern is to pass a context into the computation so it can cancel itself:

```go
// RIGHT — context cancellation propagates into the work
func processRequest(ctx context.Context, input string) (string, error) {
    ctx, cancel := context.WithTimeout(ctx, 1*time.Second)
    defer cancel()

    resultCh := make(chan string, 1)
    errCh := make(chan error, 1)

    go func() {
        result, err := expensiveComputationWithContext(ctx, input)
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
        return "", err
    case <-ctx.Done():
        return "", ctx.Err()
    }
}
```

When the timeout fires, `cancel()` is called (via the deferred call when the function returns). The goroutine running `expensiveComputationWithContext` sees the context cancelled and stops early. Both the goroutine and the work stop together.

## Done Channels for Long-Running Workers

For persistent background goroutines — workers that process jobs from a queue — the pattern is a done channel or a context:

```go
// WRONG — worker runs forever with no way to stop it
func startWorker(jobs <-chan Job) {
    go func() {
        for job := range jobs {
            process(job)
        }
    }()
    // If jobs is never closed, this goroutine runs until process death
}

// RIGHT — context provides a clean shutdown path
func startWorker(ctx context.Context, jobs <-chan Job) {
    go func() {
        for {
            select {
            case job, ok := <-jobs:
                if !ok {
                    return // channel closed, exit cleanly
                }
                process(ctx, job)
            case <-ctx.Done():
                return // context cancelled, exit cleanly
            }
        }
    }()
}
```

The `ctx.Done()` case ensures the goroutine exits when you want it to — on server shutdown, on test teardown, or when the parent operation is cancelled. The `!ok` check handles the case where the jobs channel is closed explicitly.

In your `main` function or server setup, this looks like:

```go
func main() {
    ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
    defer cancel()

    jobs := make(chan Job, 100)
    startWorker(ctx, jobs)

    // ... rest of server setup ...

    <-ctx.Done() // wait for Ctrl-C or SIGTERM
    // When ctx is cancelled, the worker exits cleanly
}
```

## errgroup for Bounded Fan-Out

A common pattern is starting a fixed number of goroutines to process a batch of work, waiting for all of them, and collecting any errors. The naive version is tedious to write correctly:

```go
// WRONG — common mistakes: ignoring errors, not waiting properly
func processBatch(items []Item) {
    for _, item := range items {
        go func(i Item) {
            process(i) // errors silently discarded
        }(item)
    }
    // no WaitGroup, function returns before goroutines finish
}

// Better but still verbose with sync.WaitGroup
func processBatch(items []Item) error {
    var wg sync.WaitGroup
    errCh := make(chan error, len(items))

    for _, item := range items {
        wg.Add(1)
        go func(i Item) {
            defer wg.Done()
            if err := process(i); err != nil {
                errCh <- err
            }
        }(item)
    }

    wg.Wait()
    close(errCh)

    for err := range errCh {
        return err // return first error
    }
    return nil
}
```

The idiomatic solution is `golang.org/x/sync/errgroup`:

```go
// RIGHT — errgroup handles WaitGroup, error collection, and context cancellation
import "golang.org/x/sync/errgroup"

func processBatch(ctx context.Context, items []Item) error {
    g, ctx := errgroup.WithContext(ctx)

    for _, item := range items {
        item := item // capture loop variable (pre-Go 1.22)
        g.Go(func() error {
            return process(ctx, item)
        })
    }

    return g.Wait() // waits for all goroutines, returns first non-nil error
}
```

`errgroup.WithContext` also cancels the context when the first error occurs, so other goroutines in the group can detect the failure and stop early.

For truly bounded concurrency — you want at most N goroutines running at a time — use `errgroup.SetLimit`:

```go
func processBatch(ctx context.Context, items []Item) error {
    g, ctx := errgroup.WithContext(ctx)
    g.SetLimit(10) // at most 10 goroutines at a time

    for _, item := range items {
        item := item
        g.Go(func() error {
            return process(ctx, item)
        })
    }

    return g.Wait()
}
```

`SetLimit` blocks `g.Go` when the limit is reached. Items are processed in batches of 10, and the call to `g.Go` automatically waits for a slot to open up. This is the right tool when you are hitting a rate-limited API or doing database operations where you do not want to open 10,000 connections at once.

## Detecting Leaks in Production

The Go runtime exposes goroutine counts via the `runtime` package and the pprof endpoints:

```go
import "runtime"

func logGoroutineCount() {
    ticker := time.NewTicker(30 * time.Second)
    for range ticker.C {
        log.Printf("goroutine count: %d", runtime.NumGoroutine())
    }
}
```

If `runtime.NumGoroutine()` climbs monotonically over time, you have a leak. Use the pprof endpoint to see what they are waiting on:

```go
import _ "net/http/pprof"

// In main:
go http.ListenAndServe(":6060", nil)
```

Then hit `http://localhost:6060/debug/pprof/goroutine?debug=2` to see a full goroutine dump with stack traces. Leaked goroutines typically show up blocked on a channel send or receive.

The `goleak` package (github.com/uber-go/goleak) is a testing tool that fails a test if goroutines are left running after it completes:

```go
func TestProcessRequest(t *testing.T) {
    defer goleak.VerifyNone(t)

    result := processRequest("some input")
    // If processRequest leaks a goroutine, goleak will catch it
}
```

Adding `goleak` to your test suite for concurrent code is one of the highest-ROI things you can do. It catches leaks at development time before they show up in production metrics.

## The Mental Model

Think of goroutines like open file descriptors. Files are cheap to open — your system supports thousands of them. But a process that opens files and never closes them will eventually exhaust the limit. Goroutines are the same: cheap to create, but every one you start must have a clear exit condition.

Before spawning a goroutine, ask: "What will cause this goroutine to exit?" If the answer is "nothing" or "I'm not sure," fix the design before the goroutine becomes a leak. The patterns are not complex: context cancellation, done channels, `errgroup`. They become second nature quickly, and the production stability improvement is significant.
