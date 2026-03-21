---
title: 'Lesson 24: Testing Concurrent Code — Flaky tests mean flaky design'
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 24
keywords:
  - Go
  - golang
  - concurrency
  - testing
  - race detector
  - goleak
  - deterministic tests
  - flaky tests
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-12-30T00:00:00.000Z'
---

A flaky test is a lie your codebase tells you. It says "this sometimes works and sometimes doesn't" and if you're honest with yourself, you already know what that means: there's a race condition somewhere, and the test is just unlucky enough to expose it occasionally. I used to mark flaky tests with `t.Skip("flaky, fix later")` and move on. I stopped doing that when a race condition that a flaky test was hinting at caused a double-charge bug in production. Now I treat every flaky test as a production incident waiting to happen.

Testing concurrent Go code is genuinely hard — harder than testing sequential code. The execution order is nondeterministic. A bug might only appear under specific scheduling conditions you can't reliably reproduce. The tools you'd normally use (assertions, return values) don't map cleanly onto goroutines. But Go has great tools for this if you know where to look, and the discipline to use them will catch bugs that no amount of code review will find.

## The Problem

The classic mistake is testing concurrent code with `time.Sleep` to "wait for things to settle":

```go
// WRONG — sleep-based synchronization in tests
func TestWorkerProcessesJobs(t *testing.T) {
    queue := make(chan Job, 10)
    results := make(chan Result, 10)

    go startWorker(queue, results)

    queue <- Job{ID: 1, Payload: "test"}

    time.Sleep(100 * time.Millisecond) // 🤞 hope the worker finishes in time

    if len(results) == 0 {
        t.Fatal("expected a result")
    }
}
```

This test has several problems. On a loaded CI machine, 100ms might not be enough, so it fails randomly. In a fast environment, 100ms is wasted time in every test run. And it doesn't actually assert anything useful — `len(results) == 0` just tells you something arrived, not what. Most importantly, `time.Sleep` creates a window where a race condition can exist without the test seeing it. The sleep masks timing issues rather than surfacing them.

```go
// WRONG — global state mutated by goroutines, no synchronization
var processed []string

func TestConcurrentProcessor(t *testing.T) {
    processed = nil // reset global state

    for i := 0; i < 5; i++ {
        go func(id int) {
            processed = append(processed, fmt.Sprintf("job-%d", id))
        }(i)
    }

    time.Sleep(50 * time.Millisecond)

    if len(processed) != 5 {
        t.Errorf("expected 5, got %d", len(processed))
    }
}
```

This is a data race on `processed`. Multiple goroutines append to it concurrently without synchronization. The race detector will catch it. But even without the race, the test is unreliable — some goroutines might not have started yet when the sleep ends.

## The Idiomatic Way

The first rule is: **synchronize in the test the same way you'd synchronize in production code**. If production code uses a WaitGroup, your test should use one too. If it uses channels, use them.

```go
// RIGHT — WaitGroup-based synchronization, no sleeps
func TestWorkerProcessesJobs(t *testing.T) {
    queue := make(chan Job, 10)
    results := make(chan Result, 10)

    var wg sync.WaitGroup
    wg.Add(1)
    go func() {
        defer wg.Done()
        startWorker(queue, results)
    }()

    queue <- Job{ID: 1, Payload: "test"}
    close(queue) // signal the worker to stop

    wg.Wait() // wait for the worker to finish, not a timer

    select {
    case result := <-results:
        if result.JobID != 1 {
            t.Errorf("expected job 1, got %d", result.JobID)
        }
    default:
        t.Fatal("expected a result, got none")
    }
}
```

The second rule: **use the race detector always**. Run your tests with `-race`. Not just occasionally. Not just in CI. Always. Add it to your `go test` invocation in every Makefile, every CI step, every local dev alias.

```bash
# In your Makefile or CI config
go test -race -timeout 30s ./...
```

The race detector adds about 5-10x overhead but catches races that are impossible to find with code review. It instruments memory accesses at compile time and reports races with exact goroutine stacks. When it fires, it gives you enough information to fix the bug.

The third rule: **check for goroutine leaks**. `goleak` is a library that checks at the end of your test that all goroutines started during the test have exited. Goroutine leaks are silent in production — they only show up as slowly growing memory or goroutine counts. `goleak` makes them test failures.

```go
// RIGHT — using goleak to catch goroutine leaks in tests
import "go.uber.org/goleak"

func TestMain(m *testing.M) {
    // Checks for leaked goroutines after every test
    goleak.VerifyTestMain(m)
}

func TestWorkerNoLeak(t *testing.T) {
    defer goleak.VerifyNone(t) // or use TestMain for all tests

    ctx, cancel := context.WithCancel(context.Background())
    jobs := make(chan Job)

    go worker(ctx, jobs) // starts a goroutine

    // Do some work
    jobs <- Job{ID: 1}

    cancel()             // signal the worker to stop
    close(jobs)          // also close the channel

    // goleak will verify the worker goroutine actually exited
}
```

Without `goleak`, you'd never know that `worker` ignores the context and keeps running after the test ends. With `goleak`, the test fails and tells you exactly which goroutine leaked.

## In The Wild

For testing code that uses timers and time-based logic, replace real timers with controllable clocks. The standard pattern is an interface:

```go
// RIGHT — clock interface for deterministic timer testing
type Clock interface {
    Now() time.Time
    After(d time.Duration) <-chan time.Time
    NewTicker(d time.Duration) *time.Ticker
}

type realClock struct{}
func (realClock) Now() time.Time                        { return time.Now() }
func (realClock) After(d time.Duration) <-chan time.Time { return time.After(d) }
func (realClock) NewTicker(d time.Duration) *time.Ticker { return time.NewTicker(d) }

// In tests, use a fake clock you can control
type fakeClock struct {
    now     time.Time
    mu      sync.Mutex
    waiters []waiter
}

type waiter struct {
    until time.Time
    ch    chan time.Time
}

func (fc *fakeClock) Advance(d time.Duration) {
    fc.mu.Lock()
    defer fc.mu.Unlock()
    fc.now = fc.now.Add(d)
    for _, w := range fc.waiters {
        if !fc.now.Before(w.until) {
            w.ch <- fc.now
        }
    }
}

func TestRetryBackoffDeterministic(t *testing.T) {
    clock := &fakeClock{now: time.Date(2025, 1, 1, 0, 0, 0, 0, time.UTC)}
    retrier := NewRetrier(clock, 3, 100*time.Millisecond) // uses the clock interface

    var attempts int
    err := retrier.Do(context.Background(), func() error {
        attempts++
        if attempts < 3 {
            return errors.New("transient error")
        }
        return nil
    })

    // Advance time to trigger backoff timers without sleeping
    clock.Advance(100 * time.Millisecond)
    clock.Advance(200 * time.Millisecond)

    if err != nil {
        t.Fatalf("expected success, got %v", err)
    }
    if attempts != 3 {
        t.Errorf("expected 3 attempts, got %d", attempts)
    }
}
```

This test runs in microseconds and is completely deterministic. No sleeps, no timing dependencies, no flakiness.

## The Gotchas

**`t.Parallel()` and shared state.** Calling `t.Parallel()` on tests that share mutable global state is a great way to introduce races. Either make your tests fully isolated (each creates its own dependencies) or don't parallelize tests that touch shared state. The race detector will catch this — if you run `go test -race` and it fires on a parallel test, you have shared state.

**Closing a channel is a broadcast, not a one-to-one message.** When testing channel-based code, remember that `close(ch)` unblocks all goroutines waiting on `ch`. If your worker loop uses `for range ch` and your test closes the channel, all workers stop. If the test closes the channel before all workers have processed their work, you might get incomplete results. Plan the shutdown sequence of your test the same way you'd plan it in production.

**Test timeouts, not sleeps.** If you genuinely need to wait for something asynchronous, use a channel with a `select` and a timeout, not a sleep:

```go
// RIGHT — waiting with a timeout instead of sleeping
select {
case result := <-results:
    // got it
case <-time.After(5 * time.Second):
    t.Fatal("timed out waiting for result")
}
```

This gives you a fast test when things work correctly and a clear failure message when they don't. A sleep gives you a slow test regardless and often lets races slip through.

**The race detector doesn't catch all bugs.** It catches data races — concurrent unsynchronized memory accesses. It won't catch logical races (where goroutine ordering affects correctness but each memory access is individually safe), deadlocks in progress, or goroutine leaks. You need `goleak` and careful test design for those.

**`TestMain` with goleak needs an allowlist.** Some libraries (grpc, opencensus) start background goroutines that never stop. `goleak.VerifyTestMain` will flag these as leaks. Use `goleak.IgnoreTopFunction` to allowlist known-good background goroutines rather than disabling goroutine leak checking entirely.

## Key Takeaway

Flaky tests are not a test problem — they're a design problem. They surface race conditions that exist in your production code. Fix them: replace `time.Sleep` with proper synchronization, run with `-race` always, use `goleak` to catch goroutine leaks, and use a clock interface to make timer-based logic deterministic. These aren't nice-to-haves. A concurrent system without race detection and leak checking is flying blind. The investment pays off every time the race detector catches a bug before it goes to production — which, in my experience, is several times a year even in well-reviewed codebases.

---

← [Lesson 23: Distributed vs Local Concurrency](/post/go/go-concurrency-distributed) | [Lesson 25: Observability for Concurrency →](/post/go/go-concurrency-observability)
