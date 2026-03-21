---
title: "Lesson 6: Real-World Examples — Generics that survived code review"
author: Atharva Pandey
series: "Go Generics Masterclass"
lesson: 6
keywords:
  - Go
  - golang
  - generics
  - cache wrapper
  - retry helper
  - result type
  - concurrent map
  - batch processor
  - production patterns
tags:
  - Go tutorial
  - golang
  - generics
date: '2024-10-02T00:00:00.000Z'
---

Lessons 1 through 5 were mostly about principles. This one is about code. Specifically, the five generic implementations I've used in real production systems that my teammates didn't complain about. Each one passed code review, made it into production, and held up over time.

The pattern across all of them is consistent: the algorithm is identical regardless of the type, the duplication without generics would have been mechanical and ongoing, and the generic version is genuinely easier to read than the alternative.

## The Problem

Without well-designed generic utilities, teams end up with one of two things: a sprawling internal package full of type-specific helpers that nobody fully remembers, or a heavy dependency on a third-party package for every slice and map operation. Neither ages well.

The goal is a small, stable set of production-grade utilities that your team actually uses and trusts. Here's what that looks like.

## The Idiomatic Way

**1. A typed in-memory cache with TTL**

This one came from a service that needed to cache both API responses and database lookups. Without generics, we had `userCache` and `productCache` with identical logic and separate types.

```go
// RIGHT — generic TTL cache
type entry[T any] struct {
    value     T
    expiresAt time.Time
}

type Cache[K comparable, V any] struct {
    mu    sync.RWMutex
    items map[K]entry[V]
    ttl   time.Duration
}

func NewCache[K comparable, V any](ttl time.Duration) *Cache[K, V] {
    return &Cache[K, V]{
        items: make(map[K]entry[V]),
        ttl:   ttl,
    }
}

func (c *Cache[K, V]) Set(key K, value V) {
    c.mu.Lock()
    defer c.mu.Unlock()
    c.items[key] = entry[V]{value: value, expiresAt: time.Now().Add(c.ttl)}
}

func (c *Cache[K, V]) Get(key K) (V, bool) {
    c.mu.RLock()
    defer c.mu.RUnlock()
    e, ok := c.items[key]
    if !ok || time.Now().After(e.expiresAt) {
        var zero V
        return zero, false
    }
    return e.value, true
}

func (c *Cache[K, V]) Delete(key K) {
    c.mu.Lock()
    defer c.mu.Unlock()
    delete(c.items, key)
}
```

Usage is clean and type-safe at the call site:

```go
userCache := NewCache[UserID, User](5 * time.Minute)
userCache.Set(42, user)
if u, ok := userCache.Get(42); ok {
    // u is typed as User — no casting
}
```

**2. A retry helper with exponential backoff**

Retry logic is identical regardless of what you're retrying. The only thing that varies is the operation's return type.

```go
// RIGHT — generic retry with exponential backoff
type RetryConfig struct {
    MaxAttempts int
    BaseDelay   time.Duration
    MaxDelay    time.Duration
}

func Retry[T any](ctx context.Context, cfg RetryConfig, op func() (T, error)) (T, error) {
    var zero T
    delay := cfg.BaseDelay

    for attempt := 0; attempt < cfg.MaxAttempts; attempt++ {
        result, err := op()
        if err == nil {
            return result, nil
        }

        if attempt == cfg.MaxAttempts-1 {
            return zero, fmt.Errorf("after %d attempts: %w", cfg.MaxAttempts, err)
        }

        select {
        case <-ctx.Done():
            return zero, ctx.Err()
        case <-time.After(delay):
        }

        delay *= 2
        if delay > cfg.MaxDelay {
            delay = cfg.MaxDelay
        }
    }
    return zero, fmt.Errorf("retry exhausted")
}
```

Usage in a service that calls an external API:

```go
cfg := RetryConfig{MaxAttempts: 3, BaseDelay: 100 * time.Millisecond, MaxDelay: 2 * time.Second}

user, err := Retry(ctx, cfg, func() (User, error) {
    return externalAPI.GetUser(userID)
})

order, err := Retry(ctx, cfg, func() (Order, error) {
    return paymentService.CreateOrder(orderReq)
})
```

No casting. The returned `user` is a `User` and the returned `order` is an `Order` — the compiler enforces this.

**3. A concurrent map with type-safe access**

Go's built-in `map` requires external synchronization. The `sync.Map` type exists but is untyped. This generic wrapper gives you a typed concurrent map:

```go
// RIGHT — typed concurrent map
type SyncMap[K comparable, V any] struct {
    mu sync.RWMutex
    m  map[K]V
}

func NewSyncMap[K comparable, V any]() *SyncMap[K, V] {
    return &SyncMap[K, V]{m: make(map[K]V)}
}

func (s *SyncMap[K, V]) Store(key K, value V) {
    s.mu.Lock()
    defer s.mu.Unlock()
    s.m[key] = value
}

func (s *SyncMap[K, V]) Load(key K) (V, bool) {
    s.mu.RLock()
    defer s.mu.RUnlock()
    v, ok := s.m[key]
    return v, ok
}

func (s *SyncMap[K, V]) LoadOrStore(key K, value V) (actual V, loaded bool) {
    s.mu.Lock()
    defer s.mu.Unlock()
    if existing, ok := s.m[key]; ok {
        return existing, true
    }
    s.m[key] = value
    return value, false
}

func (s *SyncMap[K, V]) Delete(key K) {
    s.mu.Lock()
    defer s.mu.Unlock()
    delete(s.m, key)
}

func (s *SyncMap[K, V]) Range(f func(K, V) bool) {
    s.mu.RLock()
    defer s.mu.RUnlock()
    for k, v := range s.m {
        if !f(k, v) {
            break
        }
    }
}
```

**4. A batch processor**

This one came from a data pipeline. We needed to process large slices in chunks, with a consistent pattern: split input into batches, process each batch, collect results or errors.

```go
// RIGHT — generic batch processor
type BatchResult[T any] struct {
    Results []T
    Errors  []error
}

func ProcessBatch[In, Out any](
    ctx context.Context,
    items []In,
    batchSize int,
    process func(ctx context.Context, batch []In) ([]Out, error),
) BatchResult[Out] {
    var result BatchResult[Out]

    for i := 0; i < len(items); i += batchSize {
        end := i + batchSize
        if end > len(items) {
            end = len(items)
        }
        batch := items[i:end]

        if ctx.Err() != nil {
            result.Errors = append(result.Errors, ctx.Err())
            break
        }

        out, err := process(ctx, batch)
        if err != nil {
            result.Errors = append(result.Errors, err)
            continue
        }
        result.Results = append(result.Results, out...)
    }

    return result
}
```

Used in an order processing pipeline:

```go
result := ProcessBatch(ctx, orderIDs, 100, func(ctx context.Context, batch []OrderID) ([]Order, error) {
    return db.GetOrdersByIDs(ctx, batch)
})

if len(result.Errors) > 0 {
    // handle partial failures
}
// result.Results is []Order — typed, no casting
```

## In The Wild

These five patterns — cache, retry, sync map, batch processor, and the `Result[T]` from Lesson 3 — cover maybe seventy percent of the generic utility code I've needed in production services. The reason they work is that each one passes a simple test: if I replaced `T` with a specific type, would the implementation change? No. The TTL logic, the retry backoff, the mutex locking — none of that depends on what `T` is.

The patterns that didn't make this list are the ones that failed the test. A "generic validator" that tried to express validation rules generically — failed, because validation logic is deeply type-specific. A "generic event bus" — failed, because routing and handling behavior varies by event type. For both of those, interfaces were the right tool.

## The Gotchas

**Gotcha 1: The cache and sync map need eviction logic for production use.**

The `Cache` implementation above doesn't evict expired entries — they accumulate until they're accessed. For production, you'd add a background goroutine that scans and removes stale entries, or use a library like `patrickmn/go-cache` that handles this for you. Generic utilities still need production hardening.

**Gotcha 2: The retry helper needs jitter.**

Without jitter, all callers on the same retry cycle hit the dependency at the same time after a failure, causing a thundering herd. Add jitter to the delay:

```go
jitter := time.Duration(rand.Int63n(int64(delay / 2)))
time.After(delay + jitter)
```

Small detail, big impact in production.

**Gotcha 3: `BatchResult` losing partial success info is a design choice.**

The current design collects all errors and all results. If you need to know which input items failed, you'd need to correlate by index or return a richer type. Know your requirements before committing to the signature.

**Gotcha 4: Generic types produce longer type names in logs and profiles.**

`*Cache[github.com/myco/service.UserID, github.com/myco/service.User]` is verbose in profiling output. Give your types short, clear names and consider adding a `String()` method if they appear in logs.

## Key Takeaway

Real-world generics are infrastructure-level utilities: caches, retriers, batch processors, typed concurrent data structures. They share a common trait — the algorithm is completely independent of the concrete type. Write them carefully, test them thoroughly, and they'll serve your entire codebase. Write them hastily, and they become the confusing generics your teammates work around.

---

*[← Lesson 5: Anti-Patterns](/post/go/go-generics-anti-patterns/) | [Course Index](/series/go-generics-masterclass/) | [Next → Lesson 7: Refactoring Concrete to Generic](/post/go/go-generics-refactoring/)*
