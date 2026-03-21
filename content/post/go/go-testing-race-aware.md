---
title: "Lesson 8: Race-Aware Tests — If it passes without -race, it hasn't passed"
author: Atharva Pandey
series: "Go Testing Masterclass"
lesson: 8
keywords:
  - Go
  - golang
  - testing
  - race detector
  - -race flag
  - data race
  - sync
  - concurrency testing
tags:
  - Go tutorial
  - golang
  - testing
date: '2025-01-20T00:00:00.000Z'
---

A test suite that passes without `-race` is not a clean bill of health. It's a test suite that hasn't checked one of the most insidious categories of bugs in Go: data races. I've shipped code that passed every test, passed code review, and then caused memory corruption in production because two goroutines were reading and writing a map concurrently. The race detector would have found it in under a second. We just never ran it.

The Go race detector is built into the toolchain. It instruments your code at compile time to track memory accesses and flag any unsynchronized reads and writes that happen concurrently. It's not a static analyzer — it detects actual races at runtime, during your test runs. The cost is 2-20x slower execution and 5-10x more memory. That cost is entirely acceptable in CI and during development.

## The Problem

Here's the class of bug the race detector finds that ordinary tests miss:

```go
// WRONG — map accessed from multiple goroutines without synchronization
type Cache struct {
    data map[string]string
}

func NewCache() *Cache {
    return &Cache{data: make(map[string]string)}
}

func (c *Cache) Set(key, value string) {
    c.data[key] = value // RACE: concurrent write
}

func (c *Cache) Get(key string) (string, bool) {
    v, ok := c.data[key] // RACE: concurrent read
    return v, ok
}
```

And a test that passes without `-race`:

```go
// WRONG — test that misses the race
func TestCache(t *testing.T) {
    c := NewCache()
    c.Set("foo", "bar")
    v, ok := c.Get("foo")
    if !ok || v != "bar" {
        t.Errorf("expected bar, got %q", v)
    }
    // Test passes! Single-threaded access, no race detected.
}
```

This test is green. But in production, when `Set` and `Get` are called from concurrent HTTP handlers, the map panics with a concurrent map read and write — or worse, silently corrupts data.

A subtler version is a race that's hard to trigger even with `-race` unless you write the test to exercise concurrency:

```go
// WRONG — tests that don't exercise the concurrent paths
type Counter struct {
    count int // not atomic, not locked
}

func (c *Counter) Increment() { c.count++ }
func (c *Counter) Value() int { return c.count }

func TestCounter(t *testing.T) {
    c := &Counter{}
    c.Increment()
    c.Increment()
    if c.Value() != 2 {
        t.Errorf("expected 2, got %d", c.Value())
    }
    // Passes. But Counter is not safe for concurrent use.
}
```

## The Idiomatic Way

Write tests that actually exercise concurrency, then run them with `-race`:

```go
// RIGHT — test that exercises the race condition
func TestCache_ConcurrentAccess(t *testing.T) {
    c := NewCache()
    var wg sync.WaitGroup

    // 100 goroutines writing
    for i := 0; i < 100; i++ {
        wg.Add(1)
        go func(i int) {
            defer wg.Done()
            c.Set(fmt.Sprintf("key-%d", i), fmt.Sprintf("val-%d", i))
        }(i)
    }

    // 100 goroutines reading concurrently
    for i := 0; i < 100; i++ {
        wg.Add(1)
        go func(i int) {
            defer wg.Done()
            c.Get(fmt.Sprintf("key-%d", i))
        }(i)
    }

    wg.Wait()
    // With -race, this detects the concurrent map access immediately.
    // Without -race, it might pass or might panic — undefined behavior.
}
```

Run it: `go test -race -run TestCache_ConcurrentAccess`. The race detector fires immediately and shows you the goroutines involved and the exact line of the conflicting accesses.

The fix:

```go
// RIGHT — synchronized cache
type Cache struct {
    mu   sync.RWMutex
    data map[string]string
}

func (c *Cache) Set(key, value string) {
    c.mu.Lock()
    defer c.mu.Unlock()
    c.data[key] = value
}

func (c *Cache) Get(key string) (string, bool) {
    c.mu.RLock()
    defer c.mu.RUnlock()
    v, ok := c.data[key]
    return v, ok
}
```

For the counter:

```go
// RIGHT — atomic counter
type Counter struct {
    count int64
}

func (c *Counter) Increment() { atomic.AddInt64(&c.count, 1) }
func (c *Counter) Value() int64 { return atomic.LoadInt64(&c.count) }

func TestCounter_ConcurrentIncrement(t *testing.T) {
    c := &Counter{}
    var wg sync.WaitGroup

    for i := 0; i < 1000; i++ {
        wg.Add(1)
        go func() {
            defer wg.Done()
            c.Increment()
        }()
    }
    wg.Wait()

    if got := c.Value(); got != 1000 {
        t.Errorf("expected 1000, got %d", got)
    }
}
```

This test, run with `-race`, confirms both that the final count is correct and that no unsynchronized accesses occurred.

## In The Wild

In CI, I always run two test commands:

```bash
# Fast pass — no race detector, for quick feedback
go test ./...

# Race pass — slower, catches concurrency bugs
go test -race ./...
```

For packages that I know are heavily concurrent, I sometimes add a dedicated race stress test:

```go
func TestWorkerPool_UnderLoad(t *testing.T) {
    if testing.Short() {
        t.Skip("skipping stress test in short mode")
    }

    pool := NewWorkerPool(10)
    pool.Start()
    defer pool.Stop()

    var (
        mu        sync.Mutex
        processed int
    )

    const jobs = 10_000
    var wg sync.WaitGroup
    for i := 0; i < jobs; i++ {
        wg.Add(1)
        i := i
        pool.Submit(func() {
            defer wg.Done()
            // Simulate work
            time.Sleep(time.Microsecond)
            mu.Lock()
            processed++
            mu.Unlock()
        })
    }
    wg.Wait()

    if processed != jobs {
        t.Errorf("expected %d processed, got %d", jobs, processed)
    }
}
```

Running this under `-race` exercises every synchronization point in the worker pool implementation. Any missed lock, any channel access without proper coordination, surfaces immediately.

The race detector also catches races in test setup code itself — a common source of flakiness:

```go
// WRONG — t.Parallel() subtests sharing a loop variable
func TestProcessItems(t *testing.T) {
    items := []string{"a", "b", "c"}
    for _, item := range items {
        item := item // REQUIRED in Go < 1.22
        t.Run(item, func(t *testing.T) {
            t.Parallel()
            // Without "item := item", all subtests would use the last value
            result := Process(item)
            if result == "" {
                t.Errorf("empty result for item %q", item)
            }
        })
    }
}
```

Running this without `item := item` under `-race` will flag the race between the loop advancing `item` and the goroutines reading it.

## The Gotchas

**The race detector doesn't find all races.** It only detects races that actually *execute* during the test run. If a code path is never exercised, the race in that path is invisible to the detector. Write tests that exercise concurrent paths explicitly.

**False negatives under timing.** The detector is conservative — it only reports confirmed races. But timing-dependent races (ones that require a specific goroutine interleaving) might not appear unless the test creates the right contention. Stress tests and iteration (`-count=100`) increase the chance of triggering them.

**Performance impact.** With `-race`, tests run 2-20x slower. Don't gate your development workflow on the race pass for large test suites. Run it in CI and periodically locally, not on every file save.

**Races in `init()` and global state.** Global variables accessed from multiple goroutines (including from `TestMain` setting up shared state) are race candidates. The detector finds these too.

## Key Takeaway

The `-race` flag is not optional. It's part of correctness testing for any code that uses goroutines. A test that passes without `-race` on concurrent code is a test with a blindfold on. Make it a CI requirement. Write tests that actually exercise concurrent paths — not just "launch ten goroutines and wait" but tests that create the specific contention your code will face under load. The race detector is one of the most powerful correctness tools in the Go toolchain. Use it.

---

[Course Index](/series/go-testing-masterclass) | [← Lesson 7](/post/go/go-testing-golden-files) | [Next → Lesson 9: Flaky Test Control](/post/go/go-testing-flaky)
