---
title: 'Lesson 15: Goroutines Are Cheap, Not Free — 2KB that can eat your server'
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
series: "Idiomatic Go"
lesson: 15
---

"Goroutines are cheap" is something you read in every Go introduction. It's true. A goroutine starts with a 2KB stack and the runtime handles scheduling. Spinning up a thousand of them is trivial. The part the introductions leave out is that "cheap" is not "free," and goroutines that you start and never stop are a leak — one that doesn't crash your program, just slowly eats your memory and degrades your scheduler until something gives.

## The Problem

Here's the classic goroutine leak, and it's easy to write without realizing it:

```go
// WRONG — this goroutine leaks on every timeout
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

This looks reasonable: start a goroutine, wait for the result or a timeout. The problem: when the timeout fires, `processRequest` returns. The goroutine is still running. When `expensiveComputation` finishes, it tries to send on `resultCh`. Nobody is reading from it. The goroutine blocks on the send — forever. It never exits.

Under load with occasional timeouts — say a database that gets slow — you're creating leaked goroutines faster than any complete. Memory climbs. The scheduler has an ever-growing list of blocked goroutines to manage. Things get slow, then die.

The insidious part: this doesn't show up in basic testing. You need load testing or a production incident to see it, and by then you're doing a 2am postmortem.

## The Idiomatic Way

The immediate fix is a buffered channel. The goroutine can always send its result and exit, regardless of whether the caller is still listening:

```go
// RIGHT — buffered channel lets goroutine exit even after timeout
func processRequest(input string) string {
    resultCh := make(chan string, 1) // capacity 1

    go func() {
        result := expensiveComputation(input)
        resultCh <- result // never blocks — buffer absorbs the value
    }()

    select {
    case result := <-resultCh:
        return result
    case <-time.After(1 * time.Second):
        return "timeout"
    }
}
```

But this only stops the goroutine from leaking — `expensiveComputation` still runs to completion even after the caller has moved on. If it takes 30 seconds, you're burning CPU for 30 seconds on work nobody needs. The real fix is context cancellation:

```go
// BEST — context stops both the leak and the wasted work
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

When the timeout fires, `cancel()` is called via the defer. The goroutine running `expensiveComputationWithContext` sees the context cancelled and stops early. Both the goroutine and the computation stop together.

For fan-out — processing a batch of items concurrently — reach for `errgroup` instead of managing `sync.WaitGroup` manually:

```go
import "golang.org/x/sync/errgroup"

func processBatch(ctx context.Context, items []Item) error {
    g, ctx := errgroup.WithContext(ctx)
    g.SetLimit(10) // at most 10 goroutines at a time

    for _, item := range items {
        item := item // capture for Go < 1.22
        g.Go(func() error {
            return process(ctx, item)
        })
    }

    return g.Wait() // blocks until all done, returns first error
}
```

`errgroup` handles the WaitGroup bookkeeping, collects errors, and cancels the shared context when the first error occurs. `SetLimit` prevents spinning up 10,000 goroutines for a 10,000-item batch. This is the pattern to use whenever you're doing bounded concurrent work.

## In The Wild

For long-running background workers, always provide a way to stop them:

```go
// WRONG — no shutdown path
func startWorker(jobs <-chan Job) {
    go func() {
        for job := range jobs {
            process(job) // runs until process death
        }
    }()
}

// RIGHT — context provides clean shutdown
func startWorker(ctx context.Context, jobs <-chan Job) {
    go func() {
        for {
            select {
            case job, ok := <-jobs:
                if !ok {
                    return
                }
                process(ctx, job)
            case <-ctx.Done():
                return
            }
        }
    }()
}
```

In `main`, hook this to OS signals:

```go
func main() {
    ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
    defer cancel()

    jobs := make(chan Job, 100)
    startWorker(ctx, jobs)

    <-ctx.Done() // block until Ctrl-C or SIGTERM
    // worker exits cleanly when ctx is cancelled
}
```

This is the pattern that makes Go services restart gracefully instead of leaving half-processed jobs and zombie goroutines behind.

## The Gotchas

**Goroutine counts don't decline on their own if you have leaks.** Monitor `runtime.NumGoroutine()` in production. If it climbs monotonically over hours, you have a leak. Wire it to a metric or log it periodically:

```go
go func() {
    ticker := time.NewTicker(30 * time.Second)
    for range ticker.C {
        log.Printf("goroutines: %d", runtime.NumGoroutine())
    }
}()
```

And enable pprof in non-production builds so you can dump goroutine stacks to see what they're blocked on: `http://localhost:6060/debug/pprof/goroutine?debug=2`.

**The loop variable capture bug** (pre-Go 1.22). Before Go 1.22, goroutines launched inside a loop all share the same loop variable. By the time the goroutines run, the loop has advanced and they all see the last value:

```go
// WRONG in Go < 1.22
for _, item := range items {
    go func() {
        process(item) // all goroutines may see the same item
    }()
}

// RIGHT in Go < 1.22
for _, item := range items {
    item := item // new variable per iteration
    go func() {
        process(item)
    }()
}
```

Go 1.22 fixed this — loop variables are now per-iteration. But if you're on an older version or reading code that runs on older versions, this is a real bug.

**Use `goleak` in tests.** The `uber-go/goleak` package fails a test if any goroutines are still running after the test completes. It catches leaks at development time, which is the right time to catch them:

```go
func TestProcessRequest(t *testing.T) {
    defer goleak.VerifyNone(t)
    result, _ := processRequest(context.Background(), "input")
    _ = result
}
```

If your function leaks a goroutine, this test fails with a stack trace pointing at the leaked goroutine. Add it to any test that involves concurrency.

## Key Takeaway

Think of goroutines like open file descriptors: cheap per unit, but every one you start must have a clear exit condition. Before spawning a goroutine, ask "what will cause this to stop?" If the answer is "nothing" or "I'm not sure," fix the design. The exit conditions aren't complex — context cancellation, done channels, closed input channels. They become muscle memory quickly. The production stability payoff is significant: services that don't slowly leak goroutines are services that stay up.

---

← [Lesson 14: context.Context](/post/go/go-idioms-context) | [Course Index](#) | [Lesson 16: Channels for Coordination](/post/go/go-idioms-channels-coordination) →
