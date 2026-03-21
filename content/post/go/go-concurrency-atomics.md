---
title: 'Lesson 16: Atomic Operations — Lock-free when you can, mutex when you must'
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 16
keywords:
  - Go
  - golang
  - concurrency
  - atomic
  - sync/atomic
  - lock-free
  - CompareAndSwap
  - atomic.Value
  - mutex
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-09-29T00:00:00.000Z'
---

The first time I looked at `sync/atomic`, it felt like a niche tool for systems programmers writing lock-free data structures. Turns out it's one of the most practically useful packages in the standard library — and most Go developers reach for it way too late, after they've already built something with a mutex and then profiled it into submission.

Atomic operations are CPU-level instructions that read or modify memory as a single, indivisible unit. There's no scheduler window between the read and the write. That means multiple goroutines can operate on the same memory location without a lock — and without any blocking. When your shared state is a counter, a flag, or a single configuration value, atomics are almost always the right tool.

## The Problem

The obvious mistake is a data race on a plain integer:

```go
// WRONG — data race, will corrupt the counter
var counter int

func increment() {
    counter++ // not atomic: read → add → write, three separate operations
}
```

Run this with `-race` and it screams at you. The fix most people reach for first is a mutex:

```go
// WORKS but overkill for a simple counter
var (
    mu      sync.Mutex
    counter int
)

func increment() {
    mu.Lock()
    counter++
    mu.Unlock()
}
```

A mutex is correct here, but it's heavier than necessary. Every lock and unlock involves a syscall path when there's contention. For a high-frequency counter that dozens of goroutines are incrementing on the hot path — a metrics counter in a web server, say — this adds up.

A second pattern I see constantly is storing configuration that can be reloaded at runtime:

```go
// WRONG — reading this config map while another goroutine replaces it is a data race
var globalConfig map[string]string

func reloadConfig(newCfg map[string]string) {
    globalConfig = newCfg // non-atomic pointer swap
}

func getConfigValue(key string) string {
    return globalConfig[key] // might read a half-initialized map
}
```

The pointer swap looks harmless but isn't. On 64-bit architectures the pointer write is usually a single instruction, but the Go memory model makes no guarantee about it being atomic at the language level. You need explicit atomic semantics.

## The Idiomatic Way

Go 1.19 introduced typed atomic types in `sync/atomic` that are much nicer to work with than the old function-based API. Use them:

```go
// RIGHT — sync/atomic.Int64 is safe, readable, and fast
var counter atomic.Int64

func increment() {
    counter.Add(1)
}

func getCount() int64 {
    return counter.Load()
}
```

No lock. No unlock. The `Add` method maps directly to a hardware atomic-add instruction. Reads with `Load` are similarly atomic. This is the standard pattern for counters, gauges, and any monotonically increasing value.

For the config reload case, `atomic.Value` is the right tool:

```go
// RIGHT — atomic.Value for lock-free config hot reload
type Config struct {
    MaxConnections int
    Timeout        time.Duration
    Features       map[string]bool
}

var liveConfig atomic.Value

func init() {
    liveConfig.Store(&Config{
        MaxConnections: 100,
        Timeout:        5 * time.Second,
    })
}

func reloadConfig(newCfg *Config) {
    liveConfig.Store(newCfg) // atomic pointer swap
}

func currentConfig() *Config {
    return liveConfig.Load().(*Config)
}
```

`atomic.Value` stores an `interface{}` and guarantees that `Store` and `Load` are atomic with respect to each other. The contract: all values stored must be the same concrete type. If you store a `*Config`, every subsequent store must also be a `*Config`. Violating this panics at runtime — deliberately, to catch type confusion bugs.

`CompareAndSwap` (CAS) is the third atomic primitive worth knowing. It's the foundation of most lock-free algorithms:

```go
// RIGHT — CAS for optimistic lock-free state transition
var state atomic.Int32

const (
    stateIdle    int32 = 0
    stateRunning int32 = 1
)

// TryStart returns true if this goroutine won the race to start
func TryStart() bool {
    return state.CompareAndSwap(stateIdle, stateRunning)
}

func Stop() {
    state.Store(stateIdle)
}
```

`CompareAndSwap` does this atomically: "if the current value is X, set it to Y and return true; otherwise return false." Exactly one goroutine wins when multiple call `TryStart` simultaneously — the rest see `false` and back off. This is how you implement once-only initialization, lock-free queues, and optimistic concurrency without ever calling `Lock`.

## In The Wild

A pattern I use in production metrics collection: per-endpoint request counters that are read by a background reporter goroutine every 60 seconds.

```go
type EndpointMetrics struct {
    Requests atomic.Int64
    Errors   atomic.Int64
    TotalMS  atomic.Int64
}

func (m *EndpointMetrics) Record(durationMS int64, err error) {
    m.Requests.Add(1)
    m.TotalMS.Add(durationMS)
    if err != nil {
        m.Errors.Add(1)
    }
}

func (m *EndpointMetrics) Snapshot() (reqs, errs, totalMS int64) {
    return m.Requests.Load(), m.Errors.Load(), m.TotalMS.Load()
}
```

Zero lock contention on the hot path — every HTTP request handler updates counters independently. The reporter goroutine reads the snapshot without ever blocking a handler. If I'd used a mutex around all three fields, every request would be competing for the same lock across the entire fleet.

## The Gotchas

**Atomics are not a replacement for mutexes on composite state.** This is the critical misunderstanding. Atomics make individual operations atomic. They say nothing about consistency *across* multiple operations.

```go
// WRONG — two atomic operations are not atomic together
func transfer(from, to *atomic.Int64, amount int64) {
    from.Add(-amount) // atomic
    to.Add(amount)    // atomic — but there's a window between these two where the total is wrong
}
```

Between the two `Add` calls, another goroutine reading both accounts sees an inconsistent state: money has left `from` but hasn't arrived in `to` yet. For operations that must be consistent across multiple fields, you need a mutex that covers all of them.

**`atomic.Value` panics on type mismatch.** If you `Store` a `*Config` and then later (in some error path) `Store` a `Config` (not a pointer), the runtime panics. Establish the type on first store and never deviate.

**64-bit atomics on 32-bit architectures require alignment.** In practice, if you're using the `atomic.Int64` type (Go 1.19+) rather than the old `atomic.AddInt64(&x, 1)` form on a plain `int64`, you're insulated from alignment issues. The typed atomics handle it for you. But if you're on a codebase that still uses the old function API on raw `int64` fields, misalignment on 32-bit systems causes panics. This is a good reason to migrate to the typed API.

**Don't do too much work between CAS loops.** A `CompareAndSwap` that fails retries in a loop. If the work between the load and the swap is expensive or slow, you're spinning in a tight loop burning CPU. CAS is suited for cheap in-memory operations, not for anything involving I/O.

## Key Takeaway

The decision tree is straightforward: single value, independent update — use `sync/atomic`. Multiple related fields that must be consistent together — use a mutex. Configuration that gets swapped out wholesale — use `atomic.Value`. CAS for optimistic state transitions where only one goroutine should "win." Atomics don't block, don't involve the scheduler, and don't require unlock discipline. For high-frequency counters and hot-reloaded config, they're almost always faster and simpler than a mutex. Just don't fall into the trap of thinking two atomic operations together are atomic — that's the one mistake that produces the hardest bugs to debug.

---

← [Previous: Semaphores](/post/go/go-concurrency-semaphores) | [Course Index](/post/go/) | [Next: Go Scheduler Behavior](/post/go/go-concurrency-scheduler) →
