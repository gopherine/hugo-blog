---
title: 'Go Idioms: sync.Mutex Is Often Simpler'
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
---
ve absorbed the Go concurrency philosophy — share memory by communicating — you might be tempted to reach for channels every time two goroutines need to share data. Resist that. Channels are for coordination and ownership transfer. For shared mutable state that multiple goroutines need to read and write, a mutex is usually clearer, simpler, and faster.

Rob Pike himself addressed this in his 2012 Go Concurrency Patterns talk. The message wasn't "always use channels." It was: "think about what you're actually doing." If you're passing ownership of data, use a channel. If you're protecting access to state that persists and changes over time, use a mutex. The right tool depends on the problem.

## sync.Mutex: The Basics

A `sync.Mutex` has two methods: `Lock` and `Unlock`. Only one goroutine can hold the lock at a time. Everything else blocks until the lock is released.

```go
// WRONG — no protection, data race
type Counter struct {
    count int
}

func (c *Counter) Increment() {
    c.count++  // read-modify-write: not atomic, not safe
}

func (c *Counter) Value() int {
    return c.count
}
```

Run `go test -race` on code like this and the race detector will catch it immediately. The `++` operation is not atomic — it's a read, an increment, and a write. Two goroutines doing this simultaneously will corrupt the value.

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

The pattern is always: lock, defer unlock, then do the work. The `defer` ensures the mutex is released even if the function panics. Never hold a mutex across a `return` without a `defer` — you'll eventually miss a return path and deadlock.

## The Lock/Defer Unlock Pattern

The defer-based unlock is the canonical Go pattern for a reason. It's safe against early returns and panics, and it makes the critical section visually obvious.

```go
// WRONG — manual unlock, prone to missed returns
func (s *Store) Get(key string) (string, bool) {
    s.mu.Lock()
    val, ok := s.data[key]
    if !ok {
        s.mu.Unlock()  // easy to forget in every branch
        return "", false
    }
    s.mu.Unlock()
    return val, ok
}
```

Add another early return and you'll forget an `Unlock`. Then you'll have a deadlock that only shows up under specific conditions.

```go
// RIGHT — defer handles all exit paths
func (s *Store) Get(key string) (string, bool) {
    s.mu.Lock()
    defer s.mu.Unlock()
    val, ok := s.data[key]
    return val, ok
}
```

One lock, one defer, done. However, be mindful of how wide your critical section is. If you hold a lock while doing I/O or other slow operations, you're serializing your entire program through that bottleneck. Keep critical sections as narrow as possible.

```go
// WRONG — holding the lock during slow I/O
func (s *Store) SaveToFile(path string) error {
    s.mu.Lock()
    defer s.mu.Unlock()

    data, _ := json.Marshal(s.data)  // fast, fine inside lock
    return os.WriteFile(path, data, 0644)  // slow I/O — blocks all other operations on s
}

// RIGHT — snapshot the data under the lock, then do I/O outside
func (s *Store) SaveToFile(path string) error {
    s.mu.Lock()
    snapshot, err := json.Marshal(s.data)
    s.mu.Unlock()  // explicit unlock — we're done with the protected state

    if err != nil {
        return fmt.Errorf("SaveToFile: marshaling: %w", err)
    }
    return os.WriteFile(path, snapshot, 0644)
}
```

## sync.RWMutex: When Reads Dominate

`sync.Mutex` is exclusive: every operation — read or write — requires the exclusive lock. If your workload is mostly reads with occasional writes, this is unnecessarily restrictive. `sync.RWMutex` allows multiple concurrent readers, but only one writer at a time.

```go
type Cache struct {
    mu    sync.RWMutex
    items map[string]string
}

// Read operation — use RLock/RUnlock
func (c *Cache) Get(key string) (string, bool) {
    c.mu.RLock()
    defer c.mu.RUnlock()
    val, ok := c.items[key]
    return val, ok
}

// Write operation — use Lock/Unlock (exclusive)
func (c *Cache) Set(key, value string) {
    c.mu.Lock()
    defer c.mu.Unlock()
    c.items[key] = value
}
```

The rule: use `RLock/RUnlock` for read-only access, `Lock/Unlock` for writes. Multiple goroutines can hold `RLock` simultaneously. A `Lock` call waits for all existing readers to finish and blocks new readers until the write completes.

```go
// WRONG — using a full Mutex when reads dominate
type Config struct {
    mu     sync.Mutex  // every read blocks all other reads
    values map[string]string
}

// RIGHT — use RWMutex when reads are frequent
type Config struct {
    mu     sync.RWMutex
    values map[string]string
}
```

Don't over-optimize: if your critical section is tiny and called infrequently, the difference between `Mutex` and `RWMutex` won't matter. `RWMutex` has higher overhead per operation than `Mutex`. It pays off when reads truly outnumber writes by a significant margin and the critical section has enough contention to matter.

## Common Mistakes

**Copying a mutex.** A mutex must not be copied after first use. If you copy a struct that contains a mutex, you copy the mutex's internal state, which leads to undefined behavior.

```go
// WRONG — copying a Counter copies the mutex
func processCounter(c Counter) {  // passed by value — mutex is copied!
    c.Increment()
}

// RIGHT — always pass mutex-containing structs by pointer
func processCounter(c *Counter) {
    c.Increment()
}
```

The `go vet` tool catches this with the `copylocks` checker. Run it as part of your CI pipeline.

**Locking too wide.** Holding a lock while doing anything slow — network calls, file I/O, channel operations — serializes your program. Other goroutines pile up waiting. Snapshot the data under the lock and release it before the slow operation, as shown earlier.

**Recursive locking.** Go's `sync.Mutex` is not reentrant. If a function holding a lock calls another function that tries to acquire the same lock, it deadlocks. Design your API so that exported methods acquire the lock, and unexported helper methods operate without it (assuming the caller holds it).

```go
// WRONG — unexported helper re-acquires the lock
func (s *Store) Delete(key string) {
    s.mu.Lock()
    defer s.mu.Unlock()
    s.deleteInternal(key)  // deadlock if deleteInternal also locks
}

// RIGHT — unexported helpers don't lock; exported methods do
func (s *Store) Delete(key string) {
    s.mu.Lock()
    defer s.mu.Unlock()
    s.deleteInternal(key)  // operates assuming lock is held
}

func (s *Store) deleteInternal(key string) {
    // No lock here — caller must hold it
    delete(s.data, key)
    s.onDelete(key)
}
```

## sync.Map: For Specific Use Cases

`sync.Map` is a specialized concurrent map built into the standard library. It's not a replacement for `map` + `RWMutex` in general. It's optimized for two specific scenarios: when the map is written once and read many times, or when goroutines operate on disjoint sets of keys.

```go
// sync.Map usage
var m sync.Map

// Store a value
m.Store("key", "value")

// Load a value
val, ok := m.Load("key")
if ok {
    fmt.Println(val.(string))
}

// Load or store atomically
actual, loaded := m.LoadOrStore("key", "default")
fmt.Println(actual, loaded)

// Delete
m.Delete("key")

// Range over all entries
m.Range(func(key, value any) bool {
    fmt.Println(key, value)
    return true  // return false to stop iteration
})
```

The type signature is `interface{}` (or `any`), so you lose compile-time type safety. For most general-purpose concurrent maps, `map[K]V` protected by a `sync.RWMutex` is clearer and safer. Use `sync.Map` when you've profiled and confirmed it's faster for your specific access pattern, or when you're implementing a global registry that's written during initialization and read-only thereafter.

## Mutex vs Channels: The Decision

Here's a practical heuristic:

- You're protecting a data structure that multiple goroutines read and write → **mutex**
- You're passing data from one goroutine to another (transfer of ownership) → **channel**
- You're signaling an event (done, cancel, ready) → **channel**
- You're implementing a worker pool → **channels for work distribution, possibly mutex for shared state inside workers**

```go
// This is naturally a mutex job
type RateLimiter struct {
    mu       sync.Mutex
    requests map[string]int
    window   time.Time
}

func (rl *RateLimiter) Allow(clientID string) bool {
    rl.mu.Lock()
    defer rl.mu.Unlock()
    // check and update rl.requests
    ...
}

// This is naturally a channel job
func pipeline(input <-chan Item) <-chan Result {
    output := make(chan Result)
    go func() {
        defer close(output)
        for item := range input {
            output <- transform(item)
        }
    }()
    return output
}
```

The rate limiter protects shared state (`requests` map) that multiple goroutines query and mutate. A mutex is the right fit. The pipeline passes data from one goroutine to another — channels are the natural model.

Mutexes get a bad reputation for being "low-level" or "error-prone" in comparison to channels, but in Go they're straightforward to use correctly. Lock, defer unlock, do the work. The race detector catches mistakes. Keep critical sections narrow. Don't copy mutexes. That's the whole story.
