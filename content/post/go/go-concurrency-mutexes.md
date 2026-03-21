---
title: 'Lesson 6: Mutexes Done Right — The boring tool that actually works'
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 6
keywords:
  - Go
  - golang
  - concurrency
  - mutex
  - sync.Mutex
  - sync.RWMutex
  - critical section
  - lock
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-05-31T00:00:00.000Z'
---

Channels get the spotlight in Go talks and blog posts — they're the shiny, idiomatic, philosophically interesting tool. Mutexes feel like the C and Java baggage everyone was supposed to leave behind. But here's what I've learned after several years of writing concurrent Go: mutexes are often the *right* tool, especially when you're protecting shared state, and the problems you see in production are almost never "we should've used channels" — they're "we copied the mutex" or "we forgot to narrow the critical section." Learn to use mutexes properly and stop apologizing for them.

## The Problem

The most dangerous mutex mistake is also the most subtle:

```go
// WRONG — mutex copied, both copies have independent state
type SafeCounter struct {
    mu    sync.Mutex
    count int
}

func (c SafeCounter) Increment() { // value receiver — copies the struct!
    c.mu.Lock()
    defer c.mu.Unlock()
    c.count++
}

func (c SafeCounter) Value() int { // also a copy
    c.mu.Lock()
    defer c.mu.Unlock()
    return c.count
}
```

`sync.Mutex` must not be copied after first use. A value receiver makes a copy of the struct — including the mutex. Now you have two mutexes: one in the caller's struct, one in the method's local copy. They lock independently. Two goroutines can both think they hold the lock while touching `c.count` simultaneously. The race detector catches this, but only if you actually run with `-race`.

Here's the second problem — holding a lock longer than necessary:

```go
// WRONG — lock held during I/O, kills throughput
func (s *Server) handleRequest(r *Request) Response {
    s.mu.Lock()
    defer s.mu.Unlock()

    user := s.cache[r.UserID]
    if user == nil {
        // This HTTP call happens while holding the lock.
        // Every other goroutine is blocked for the duration of this call.
        user = fetchUserFromAPI(r.UserID) // could take 200ms
        s.cache[r.UserID] = user
    }
    return buildResponse(user, r)
}
```

The lock should protect the cache map — a tiny operation. Instead it's wrapping an HTTP call. Every concurrent request serializes behind that one slow call. Under any real load, this turns into a bottleneck that's hard to diagnose because your latency graph shows "everything is slow" rather than "lock contention on this one path."

## The Idiomatic Way

Rule one: pointer receivers for types with mutexes. Always.

```go
// RIGHT — pointer receiver, mutex is not copied
type SafeCounter struct {
    mu    sync.Mutex
    count int
}

func (c *SafeCounter) Increment() {
    c.mu.Lock()
    defer c.mu.Unlock()
    c.count++
}

func (c *SafeCounter) Value() int {
    c.mu.Lock()
    defer c.mu.Unlock()
    return c.count
}
```

If `go vet` sees a mutex copied, it'll tell you. Run `go vet` in CI. Don't skip it.

Rule two: narrow critical sections. Only hold the lock for the minimal operation on shared state:

```go
// RIGHT — lock only protects the map, not the I/O
func (s *Server) handleRequest(r *Request) Response {
    s.mu.RLock()
    user := s.cache[r.UserID]
    s.mu.RUnlock()

    if user == nil {
        // Fetch outside the lock — multiple goroutines can do this concurrently
        fetched := fetchUserFromAPI(r.UserID)

        s.mu.Lock()
        // Double-check — another goroutine might have populated it while we fetched
        if s.cache[r.UserID] == nil {
            s.cache[r.UserID] = fetched
        }
        user = s.cache[r.UserID]
        s.mu.Unlock()
    }

    return buildResponse(user, r)
}
```

Now the lock only protects map reads and writes — microseconds, not milliseconds. Concurrent requests fetch the API in parallel. The double-check after the write lock prevents redundant entries if two goroutines both missed the cache simultaneously. This is the standard "check, fetch outside lock, re-check, write" pattern.

`sync.RWMutex` is the right choice when reads vastly outnumber writes. It allows multiple concurrent readers, but a write locks out everyone:

```go
// RIGHT — RWMutex for read-heavy workloads
type Config struct {
    mu     sync.RWMutex
    values map[string]string
}

func (c *Config) Get(key string) (string, bool) {
    c.mu.RLock() // multiple goroutines can hold RLock simultaneously
    defer c.mu.RUnlock()
    v, ok := c.values[key]
    return v, ok
}

func (c *Config) Set(key, value string) {
    c.mu.Lock() // exclusive lock — blocks all readers and writers
    defer c.mu.Unlock()
    c.values[key] = value
}
```

When reads are 99% of accesses and writes are rare (config reloads, for example), `RWMutex` lets all readers proceed in parallel. Writes are rare enough that the exclusive lock cost is negligible.

When do you reach for a mutex over a channel? My heuristic: if you're protecting shared state (a map, a counter, a cache), use a mutex. If you're coordinating work between goroutines (passing ownership of values, signaling events), use a channel. Trying to protect a shared map with a channel is awkward — you end up with a goroutine that serializes all access through receives, which is more complex and often slower than a mutex.

```go
// RIGHT — mutex for protecting a shared cache (simpler than channel-based serialization)
type UserCache struct {
    mu    sync.RWMutex
    store map[string]*User
}

func (c *UserCache) Get(id string) (*User, bool) {
    c.mu.RLock()
    defer c.mu.RUnlock()
    u, ok := c.store[id]
    return u, ok
}

func (c *UserCache) Set(id string, u *User) {
    c.mu.Lock()
    defer c.mu.Unlock()
    c.store[id] = u
}

func (c *UserCache) Delete(id string) {
    c.mu.Lock()
    defer c.mu.Unlock()
    delete(c.store, id)
}
```

Clean, obvious, correct. No goroutine managing a select loop. No channel to size. Just a mutex and a map.

## In The Wild

A rate limiter in a previous codebase used a channel-based token bucket — a goroutine that refilled the channel on a ticker, and callers that received from it to consume a token. The logic was fine, but it had a subtle performance problem: the refill goroutine and all callers were competing for the channel, and under high throughput the scheduler overhead was measurable.

We rewrote it with a mutex:

```go
// RIGHT — mutex-based token bucket, simpler and faster under load
type RateLimiter struct {
    mu       sync.Mutex
    tokens   float64
    maxTokens float64
    refillRate float64 // tokens per nanosecond
    lastRefill time.Time
}

func NewRateLimiter(rps float64) *RateLimiter {
    return &RateLimiter{
        tokens:     rps,
        maxTokens:  rps,
        refillRate: rps / float64(time.Second),
        lastRefill: time.Now(),
    }
}

func (r *RateLimiter) Allow() bool {
    r.mu.Lock()
    defer r.mu.Unlock()

    now := time.Now()
    elapsed := now.Sub(r.lastRefill)
    r.tokens = min(r.maxTokens, r.tokens+float64(elapsed)*r.refillRate)
    r.lastRefill = now

    if r.tokens >= 1 {
        r.tokens--
        return true
    }
    return false
}
```

No goroutines. No channels. Just a mutex, some arithmetic, and time. Benchmarks showed it was 3x faster than the channel version at high concurrency. The critical section is tiny — a few arithmetic operations and a time comparison.

## The Gotchas

**Forgetting `defer Unlock` and returning early.** Without `defer`, every early return path has to manually call `Unlock`. Miss one — say, in an error path — and you've deadlocked. Always `defer mu.Unlock()` immediately after `mu.Lock()`. The performance cost of defer in a hot path is real but small; the cost of a missed Unlock is infinite.

**Using `sync.Mutex` where `sync.Once` is better.** If you're using a mutex to guard one-time initialization (`if !initialized { ... }`), use `sync.Once` instead. It has the right semantics, it's well-understood, and it avoids a class of bugs where the double-check pattern is implemented incorrectly.

**Locking at the wrong granularity.** Embedding a mutex in a struct that gets passed by value (see the problem section), or having a single global mutex for a large map when you could shard — both granularity mistakes. Sharding is overkill for most apps, but if you profile and find lock contention on a large concurrent map, a sharded structure with per-shard mutexes is worth considering before reaching for `sync.Map`.

## Key Takeaway

The mutex is unglamorous, but it's correct, fast, and widely understood — and in Go, "boring and correct" beats "clever and subtle" every time. Use pointer receivers without exception, defer Unlock immediately after Lock, narrow your critical sections to pure state operations, and use `RWMutex` when your read-to-write ratio is high. If you catch yourself building a goroutine to serialize access to shared state so you don't have to use a mutex, ask yourself honestly whether channels are actually simpler there — usually they're not.

---

[← Lesson 5: sync.WaitGroup](/post/go/go-concurrency-waitgroup) | [Course Index](/series/go-concurrency-masterclass) | [Next → Lesson 7: Race Conditions and the Go Memory Model](/post/go/go-concurrency-race-conditions)
