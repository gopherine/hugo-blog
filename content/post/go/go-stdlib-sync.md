---
title: 'Lesson 6: sync Package Complete Guide — Mutex, Once, Pool, Map — when to use each'
author: Atharva Pandey
series: "Go Standard Library Mastery"
lesson: 6
keywords:
  - Go
  - golang
  - sync
  - Mutex
  - RWMutex
  - sync.Once
  - sync.Pool
  - sync.Map
  - concurrency
  - stdlib
tags:
  - Go tutorial
  - golang
  - stdlib
date: '2025-03-22T00:00:00.000Z'
---

The `sync` package is where Go's concurrency tools live when channels aren't the right answer. `sync.Mutex`, `sync.RWMutex`, `sync.WaitGroup`, `sync.Once`, `sync.Pool`, `sync.Map` — each one solves a specific problem, and using the wrong one is a reliable way to introduce bugs or performance regressions. I've misused all of them at various points.

The general guidance is channels for communication, mutexes for protecting shared state. But within the mutex family, the choice between Mutex, RWMutex, and sync.Map has performance implications that matter in hot paths. And sync.Pool is frequently misunderstood — it's not a general-purpose object pool; it's a GC-aware buffer recycler.

## The Problem

The most common mistake: using sync.Map when a regular map with a mutex is clearer and faster:

```go
// WRONG for most cases — sync.Map has poor read performance
// when the key set changes frequently
var cache sync.Map

func get(key string) (Value, bool) {
    v, ok := cache.Load(key)
    if !ok {
        return Value{}, false
    }
    return v.(Value), true
}

func set(key string, val Value) {
    cache.Store(key, val)
}
```

`sync.Map` is optimized for two specific cases: entries are only written once and read many times, or keys are disjoint across goroutines. For a general-purpose cache with frequent writes and reads, a regular map protected by `sync.RWMutex` is faster and cleaner. The type assertions in `sync.Map` also lose compile-time type safety.

The second common mistake: using `sync.Mutex` for a read-heavy workload:

```go
// SLOW — uses exclusive lock even for reads
type MetricsRegistry struct {
    mu      sync.Mutex
    metrics map[string]float64
}

func (r *MetricsRegistry) Get(name string) float64 {
    r.mu.Lock()
    defer r.mu.Unlock()
    return r.metrics[name]
}
```

If 99% of operations are reads and 1% are writes, a `sync.Mutex` serializes every read against every other read. `sync.RWMutex` allows concurrent reads and only requires exclusive access for writes.

## The Idiomatic Way

`sync.RWMutex` for read-heavy shared state:

```go
// Cache with concurrent reads and exclusive writes
type Cache struct {
    mu    sync.RWMutex
    items map[string]Item
}

func (c *Cache) Get(key string) (Item, bool) {
    c.mu.RLock()         // multiple goroutines can hold RLock simultaneously
    defer c.mu.RUnlock()
    item, ok := c.items[key]
    return item, ok
}

func (c *Cache) Set(key string, item Item) {
    c.mu.Lock()          // exclusive — no readers or other writers
    defer c.mu.Unlock()
    c.items[key] = item
}

func (c *Cache) Delete(key string) {
    c.mu.Lock()
    defer c.mu.Unlock()
    delete(c.items, key)
}
```

`sync.Once` for initialization that must happen exactly once regardless of concurrent callers:

```go
// Database connection initialized exactly once, even with concurrent callers
type DBClient struct {
    once   sync.Once
    db     *sql.DB
    initErr error
}

func (c *DBClient) DB() (*sql.DB, error) {
    c.once.Do(func() {
        db, err := sql.Open("postgres", os.Getenv("DATABASE_URL"))
        if err != nil {
            c.initErr = err
            return
        }
        if err := db.Ping(); err != nil {
            db.Close()
            c.initErr = err
            return
        }
        c.db = db
    })
    return c.db, c.initErr
}
```

One subtlety: if the function passed to `Once.Do` panics, the Once is still considered done. Future calls to `Do` will not retry. If initialization can fail and you want to retry, `sync.Once` is not the right tool — use an `atomic.Pointer` with a compare-and-swap instead.

`sync.Pool` for reusing temporary objects to reduce allocator pressure:

```go
// Pool for reusing byte buffers — reduces GC pressure in hot paths
var bufPool = sync.Pool{
    New: func() any {
        // Allocate a buffer with reasonable initial capacity.
        buf := make([]byte, 0, 4096)
        return &buf
    },
}

func encodeEvent(e Event) ([]byte, error) {
    // Get a buffer from the pool
    bufp := bufPool.Get().(*[]byte)
    buf := (*bufp)[:0] // reset length, keep capacity

    // Use the buffer
    buf = append(buf, `{"id":`...)
    buf = strconv.AppendInt(buf, int64(e.ID), 10)
    buf = append(buf, `,"type":"`...)
    buf = append(buf, e.Type...)
    buf = append(buf, `"}`...)

    // Copy the result before returning buffer to pool
    result := make([]byte, len(buf))
    copy(result, buf)

    // Return to pool
    *bufp = buf
    bufPool.Put(bufp)

    return result, nil
}
```

The critical rule with `sync.Pool`: the pool can evict objects at any time — specifically, on each GC cycle. Don't store objects with important state in a pool. Don't use a pool as a cache. Pool objects are for temporary use and will be silently discarded.

`sync.Map` in its correct use case — a registry populated at startup and read heavily at runtime:

```go
// Handler registry: written once at startup, read millions of times
var handlers sync.Map

func registerHandler(path string, h http.Handler) {
    handlers.Store(path, h)
}

func getHandler(path string) (http.Handler, bool) {
    v, ok := handlers.Load(path)
    if !ok {
        return nil, false
    }
    return v.(http.Handler), true
}
```

## In The Wild

`WaitGroup` for fan-out work with error collection is a common pattern, and the `errgroup` package from `golang.org/x/sync` does it better than a raw `WaitGroup` for most cases:

```go
import "golang.org/x/sync/errgroup"

func fetchAll(ctx context.Context, urls []string) ([][]byte, error) {
    g, ctx := errgroup.WithContext(ctx)
    results := make([][]byte, len(urls))

    for i, url := range urls {
        i, url := i, url // capture loop variables
        g.Go(func() error {
            resp, err := http.Get(url)
            if err != nil {
                return err
            }
            defer resp.Body.Close()
            results[i], err = io.ReadAll(resp.Body)
            return err
        })
    }

    // Wait for all goroutines. Returns the first non-nil error.
    if err := g.Wait(); err != nil {
        return nil, err
    }
    return results, nil
}
```

Writing to `results[i]` is safe here because each goroutine writes to a distinct index — no two goroutines share the same `i`.

## The Gotchas

**Never copy a mutex.** `sync.Mutex` and `sync.RWMutex` must not be copied after first use. If a struct containing a mutex is copied, the copy has its own mutex in an independent state. The original can be locked while the copy is unlocked — no mutual exclusion. Use pointer receivers for all methods on mutex-containing structs: `func (c *Cache) Get(...)`.

**`sync.Once` ignores panics but records the attempt.** If your `Once.Do` function panics, the panic propagates to the caller, but the `Once` records that `Do` was called. Subsequent calls to `Do` do nothing. If the initialization panicked, the resource is neither initialized nor retried.

**`sync.Pool` and the GC.** Objects in `sync.Pool` are eligible for collection on any GC cycle. The pool provides no guarantee about object lifetime. Never use `sync.Pool` as a connection pool or a cache.

**Lock granularity.** A single mutex protecting a large struct means all concurrent goroutines contend on that one lock. Consider splitting the state into smaller pieces, each protected by its own mutex. Or use sharding: a slice of N mutexes where each key maps to a shard via `hash(key) % N`.

## Key Takeaway

`sync.Mutex` for exclusive access; `sync.RWMutex` for read-heavy state; `sync.Once` for one-time initialization; `sync.Pool` for temporary object reuse under GC pressure; `sync.Map` only for write-once, read-many registries. Never copy a mutex. Never use `sync.Pool` as a cache.

---

**Previous:** [Lesson 5: os and filepath](/post/go/go-stdlib-os-filepath/)
**Next:** [Lesson 7: context Internals — How cancellation propagates under the hood](/post/go/go-stdlib-context/)
