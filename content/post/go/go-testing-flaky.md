---
title: "Lesson 9: Flaky Test Control — A flaky test is worse than no test"
author: Atharva Pandey
series: "Go Testing Masterclass"
lesson: 9
keywords:
  - Go
  - golang
  - testing
  - flaky tests
  - test reliability
  - timing
  - non-determinism
  - test isolation
tags:
  - Go tutorial
  - golang
  - testing
date: '2025-02-15T00:00:00.000Z'
---

A flaky test is a test that sometimes passes and sometimes fails with no code change in between. It sounds like a minor annoyance. It's actually corrosive. Once your team learns that the test suite sometimes fails for "no reason," they start merging on red. They start dismissing failures. They lose trust in the test suite as a signal. A single reliably-failing test is informative; a dozen flaky tests teach your team to ignore failures. I've seen this destroy test suite culture on multiple teams.

Flakiness has a finite set of causes. Learn to recognize them and you can eliminate most flakiness before it ships.

## The Problem

The most common source of flakiness I've seen is time-dependent logic:

```go
// WRONG — sleeping to wait for an async operation
func TestAsyncProcessor(t *testing.T) {
    p := NewProcessor()
    p.Start()
    p.Submit(Task{ID: "t1", Data: "hello"})

    time.Sleep(100 * time.Millisecond) // hope it's done by now

    result, ok := p.GetResult("t1")
    if !ok {
        t.Fatal("result not found") // fails on slow machines or under load
    }
    if result.Output != "processed: hello" {
        t.Errorf("unexpected output: %s", result.Output)
    }
}
```

On a fast developer machine, 100ms is plenty. In CI under load, the test runner might be starved for CPU and the async work hasn't finished. The test fails intermittently. The `time.Sleep` duration is arbitrary, and there's no right value — any fixed value will fail eventually.

Another common cause is map iteration order:

```go
// WRONG — relying on map iteration order
func TestGetTags(t *testing.T) {
    tags := GetTags(articleID) // returns map[string]string

    var keys []string
    for k := range tags {
        keys = append(keys, k)
    }
    // Map iteration order is random in Go — this is flaky
    if keys[0] != "category" {
        t.Errorf("expected first key to be category, got %s", keys[0])
    }
}
```

Go randomizes map iteration order deliberately. Any test that depends on the order of map keys is guaranteed to be flaky.

A third cause is shared global state between tests:

```go
// WRONG — tests polluting global state
var globalRegistry = make(map[string]Handler)

func TestRegisterHandler(t *testing.T) {
    Register("ping", PingHandler)
    // If another test runs before this and leaves state in globalRegistry,
    // or if this test runs in parallel with another that modifies the same key,
    // behaviour is undefined.
    h, ok := Lookup("ping")
    if !ok {
        t.Error("handler not found")
    }
    _ = h
}
```

## The Idiomatic Way

For async operations, use polling with a timeout instead of sleeping:

```go
// RIGHT — polling with deadline instead of sleeping
func TestAsyncProcessor(t *testing.T) {
    p := NewProcessor()
    p.Start()
    defer p.Stop()
    p.Submit(Task{ID: "t1", Data: "hello"})

    // Poll until result is ready or deadline exceeded
    deadline := time.Now().Add(5 * time.Second)
    for time.Now().Before(deadline) {
        result, ok := p.GetResult("t1")
        if ok {
            if result.Output != "processed: hello" {
                t.Errorf("unexpected output: %s", result.Output)
            }
            return // success
        }
        time.Sleep(10 * time.Millisecond)
    }
    t.Fatal("timed out waiting for result after 5 seconds")
}
```

Better still: if your processor supports it, signal completion through a channel:

```go
// RIGHT — channel signaling instead of polling
func TestAsyncProcessor_WithDone(t *testing.T) {
    p := NewProcessor()
    p.Start()
    defer p.Stop()

    done := make(chan Result, 1)
    p.SubmitWithCallback(Task{ID: "t1", Data: "hello"}, func(r Result) {
        done <- r
    })

    select {
    case result := <-done:
        if result.Output != "processed: hello" {
            t.Errorf("unexpected output: %s", result.Output)
        }
    case <-time.After(5 * time.Second):
        t.Fatal("timed out waiting for result")
    }
}
```

For map ordering, sort before comparing:

```go
// RIGHT — sort map keys before asserting order
func TestGetTags(t *testing.T) {
    tags := GetTags(articleID)

    keys := make([]string, 0, len(tags))
    for k := range tags {
        keys = append(keys, k)
    }
    sort.Strings(keys)

    want := []string{"author", "category", "topic"}
    if !reflect.DeepEqual(keys, want) {
        t.Errorf("keys: got %v, want %v", keys, want)
    }
}
```

For global state, use `t.Cleanup` to restore it:

```go
// RIGHT — isolating global state with cleanup
func TestRegisterHandler(t *testing.T) {
    // Save and restore the global registry for each test
    original := cloneRegistry(globalRegistry)
    t.Cleanup(func() {
        globalRegistry = original
    })

    Register("ping", PingHandler)
    h, ok := Lookup("ping")
    if !ok {
        t.Error("handler not found")
    }
    _ = h
}
```

Or, better, redesign to avoid global state:

```go
// RIGHT — injected registry instead of global
type Registry struct {
    mu       sync.RWMutex
    handlers map[string]Handler
}

func NewRegistry() *Registry {
    return &Registry{handlers: make(map[string]Handler)}
}

func (r *Registry) Register(name string, h Handler) {
    r.mu.Lock()
    defer r.mu.Unlock()
    r.handlers[name] = h
}

func TestRegistry(t *testing.T) {
    reg := NewRegistry() // fresh instance per test — no shared state
    reg.Register("ping", PingHandler)
    h, ok := reg.Lookup("ping")
    if !ok {
        t.Error("handler not found")
    }
    _ = h
}
```

## In The Wild

One pattern I've found invaluable for diagnosing flaky tests is running them with `-count`:

```bash
# Run the test 100 times to surface intermittent failures
go test -run TestAsyncProcessor -count=100 -timeout 5m
```

If a test is flaky, running it 100 times reliably reproduces the failure. Once you can reproduce it reliably, you can investigate.

For tests that depend on external services (a real database, a real cache), I also use a `requireReady` helper:

```go
func requireServiceReady(t *testing.T, addr string) {
    t.Helper()
    deadline := time.Now().Add(10 * time.Second)
    for time.Now().Before(deadline) {
        conn, err := net.DialTimeout("tcp", addr, time.Second)
        if err == nil {
            conn.Close()
            return
        }
        time.Sleep(100 * time.Millisecond)
    }
    t.Fatalf("service at %s not ready after 10 seconds", addr)
}

func TestWithRealDB(t *testing.T) {
    dsn := os.Getenv("TEST_DATABASE_URL")
    if dsn == "" {
        t.Skip("no test database configured")
    }

    // Don't assume the DB is ready — wait for it
    requireServiceReady(t, extractHostPort(dsn))

    db, err := sql.Open("postgres", dsn)
    // ...
}
```

This eliminates flakiness that comes from tests starting before dependent services are fully ready.

## The Gotchas

**`t.Parallel()` without proper isolation is a major flakiness source.** Parallel subtests share the same process memory. If they mutate global state, read from shared resources without synchronization, or depend on execution order, they'll be flaky under parallel execution in ways that are hard to reproduce in isolation. Always ensure parallel subtests are fully independent.

**Network-dependent tests.** Tests that make real HTTP calls to external services will flake when those services are unavailable, slow, or return unexpected data. Either mock the external service, use `httptest.NewServer` to serve canned responses, or record and replay responses with a tool like `go-vcr`.

**File system state.** Tests that create, read, and delete files in `os.TempDir()` can interact if they don't use unique temporary directories. Use `t.TempDir()` — it creates a unique temp directory and cleans it up after the test.

**`init()` functions and package-level `var` initializers.** These run before any test and can't be reset. If your package-level state is mutable and tests modify it, you'll have ordering-dependent failures. Audit package-level mutable state in test packages and either make it immutable or reset it in `TestMain` or `t.Cleanup`.

## Key Takeaway

Flaky tests aren't just annoying — they're a trust problem. Once your team learns to dismiss red CI, you've lost the primary value of automated testing: confidence that green means good. Treat flakiness as a bug. When you find a flaky test, diagnose and fix it before it normalizes "ignore the failures" culture on your team. The causes are finite and learnable. Sleep-based waiting, map iteration order, shared global state, and missing synchronization cover 90% of flakiness I've encountered. Eliminate them systematically.

---

[Course Index](/series/go-testing-masterclass) | [← Lesson 8](/post/go/go-testing-race-aware) | [Next → Lesson 10: Test Architecture](/post/go/go-testing-architecture)
