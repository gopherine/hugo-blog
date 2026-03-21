---
title: 'Lesson 18: sync.Mutex Is Often Simpler — Not everything needs a channel'
author: Atharva Pandey
keywords:
  - Go
  - golang
  - sync.Mutex
  - sync.RWMutex
  - sync.Map
  - mutex
  - concurrency
  - shared state
tags:
  - Go tutorial
  - golang
date: '2025-10-20T00:00:00.000Z'
series: "Idiomatic Go"
lesson: 18
---

After you absorb the Go concurrency philosophy — share memory by communicating — there's a temptation to reach for channels every time two goroutines need to share data. Resist that. Channels are for coordination and ownership transfer. For shared mutable state that multiple goroutines read and write, a mutex is usually clearer, simpler, and faster. Using a channel where a mutex belongs is one of those things that looks idiomatic but isn't.

Rob Pike addressed this directly. The message wasn't "always use channels." It was: think about what you're actually doing. Passing ownership? Channel. Protecting access to persisted, mutable state? Mutex.

## The Problem

The most obvious version: unprotected shared state causes data races.

```go
// WRONG — no protection, data race
type Counter struct {
    count int
}

func (c *Counter) Increment() {
    c.count++ // read-modify-write: not atomic, not safe
}

func (c *Counter) Value() int {
    return c.count
}
```

Run `go test -race` on this and the race detector catches it immediately. The `++` operation is three steps — read, increment, write — and two goroutines doing it simultaneously corrupt the value silently.

The more subtle version: holding a lock for too long.

```go
// WRONG — holding the lock during slow I/O
func (s *Store) SaveToFile(path string) error {
    s.mu.Lock()
    defer s.mu.Unlock()

    data, _ := json.Marshal(s.data)    // fast, fine
    return os.WriteFile(path, data, 0644) // slow — blocks everything on s
}
```

Every goroutine trying to access `s` is now queued behind a file write. You've serialized your entire program through a disk operation.

## The Idiomatic Way

The canonical mutex pattern: lock, defer unlock, do the work.

```go
// RIGHT — protecting shared state with a mutex
type Counter struct {
    mu    sync.Mutex
    count int
}

func (c *Counter) Increment() {
    c.mu.Lock()
    defer c.mu.Unlock()
    c.count++
}

func (c *Counter) Value() int {
    c.mu.Lock()
    defer c.mu.Unlock()
    return c.count
}
```

The `defer` ensures the mutex is released even if the function panics. Never hold a mutex across a return without a defer — you'll eventually miss a return path and deadlock.

For the I/O case, snapshot under the lock and release before the slow operation:

```go
// RIGHT — snapshot the data under the lock, then I/O outside
func (s *Store) SaveToFile(path string) error {
    s.mu.Lock()
    snapshot, err := json.Marshal(s.data)
    s.mu.Unlock() // explicit unlock — done with protected state

    if err != nil {
        return fmt.Errorf("SaveToFile: marshaling: %w", err)
    }
    return os.WriteFile(path, snapshot, 0644)
}
```

When reads heavily outnumber writes, `sync.RWMutex` lets multiple goroutines hold a read lock simultaneously:

```go
type Cache struct {
    mu    sync.RWMutex
    items map[string]string
}

func (c *Cache) Get(key string) (string, bool) {
    c.mu.RLock()
    defer c.mu.RUnlock()
    val, ok := c.items[key]
    return val, ok
}

func (c *Cache) Set(key, value string) {
    c.mu.Lock()
    defer c.mu.Unlock()
    c.items[key] = value
}
```

`RLock/RUnlock` for reads; `Lock/Unlock` for writes. Multiple goroutines can hold `RLock` simultaneously. A `Lock` waits for all readers to finish. Don't over-optimize: `RWMutex` has higher per-operation overhead than `Mutex`. It pays off when reads genuinely dominate and there's real contention.

## In The Wild

Here's a concrete example of where mutex is obviously the right call — a rate limiter:

```go
type RateLimiter struct {
    mu       sync.Mutex
    requests map[string]int
    window   time.Time
}

func (rl *RateLimiter) Allow(clientID string) bool {
    rl.mu.Lock()
    defer rl.mu.Unlock()
    // check and update rl.requests
    // ...
}
```

This protects shared state (`requests` map) that multiple goroutines query and mutate. There's no ownership transfer. There's no event signaling. It's a mutex job, full stop. Compare that to a pipeline that passes data between goroutines — that's a channel job. The distinction is about what the operation *means*, not just whether there's concurrent access.

`sync.Map` is worth knowing about too, but it's not a general-purpose `map` replacement. It's optimized for two specific patterns: write-once-read-many, or goroutines operating on disjoint key sets. For everything else, `map` + `RWMutex` is clearer and type-safe.

## The Gotchas

**Copying a mutex.** A mutex must not be copied after first use. Copying a struct that contains a mutex copies the internal state, causing undefined behavior. Always pass mutex-containing structs by pointer. The `go vet` tool catches this with the `copylocks` checker — run it in CI.

```go
// WRONG — passed by value copies the mutex
func processCounter(c Counter) { c.Increment() }

// RIGHT — pointer receiver
func processCounter(c *Counter) { c.Increment() }
```

**Recursive locking.** Go's `sync.Mutex` is not reentrant. If a function holding a lock calls another function that tries to acquire the same lock, it deadlocks. The pattern to avoid this: exported methods acquire the lock; unexported helper methods assume the caller holds it.

```go
// RIGHT — helpers operate without locking; callers hold the lock
func (s *Store) Delete(key string) {
    s.mu.Lock()
    defer s.mu.Unlock()
    s.deleteInternal(key) // assumes lock is held
}

func (s *Store) deleteInternal(key string) {
    // no lock here
    delete(s.data, key)
}
```

**Wide critical sections.** Any slow operation inside a lock — network call, file I/O, channel operation — serializes every other goroutine waiting for that lock. Snapshot the data, release the lock, do the slow work. Keep critical sections as narrow as possible.

## Key Takeaway

Mutexes get a bad reputation for being "low-level" compared to channels, but in Go they're straightforward. Lock, defer unlock, do the work. The race detector catches mistakes. Keep critical sections narrow. Never copy a mutex. That's the whole story. The engineers I've seen write the cleanest concurrent Go are the ones who don't feel the need to channel-ify everything — they pick the right tool for the job. Protecting a map? Mutex. Distributing work to goroutines? Channel. The decision framework isn't complicated once you understand what each primitive is actually for.

---

← [Previous: select Is Elegant](/post/go/go-idioms-select) | [Course Index](/post/go/) | [Next: Table-Driven Tests](/post/go/go-idioms-table-driven-tests) →
