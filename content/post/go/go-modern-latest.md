---
title: "Lesson 5: What's New in Go 1.25–1.26 — Swiss table maps, weak pointers, and the future"
author: Atharva Pandey
series: "Modern Go: 1.21 to 1.26"
lesson: 5
keywords:
  - Go 1.25
  - Go 1.26
  - Swiss table
  - weak pointers
  - unique package
  - go toolchain
  - golang future
  - runtime improvements
tags:
  - Go tutorial
  - golang
  - modern Go
date: "2025-03-02T00:00:00.000Z"
---

Every major Go release follows a rhythm: one or two headline language features, a handful of standard library additions, and a runtime improvement you probably do not notice directly but that makes your services run better in aggregate. Go 1.23 and 1.24 brought the iterator protocol, `go tool` improvements, and the `unique` package. Go 1.25 and 1.26 continued this pattern with Swiss table map internals, a production-ready weak pointer API, and improvements to the toolchain that affect how you build, ship, and profile Go binaries.

This lesson covers the changes that matter most for production Go code — what they are, when they help, and what to watch for when upgrading.

## The Problem

Maps in Go are a workhorse data structure, but their internal implementation had not changed significantly since Go's early days. For small maps and string keys, performance was fine. For large maps, maps with high deletion rates, or workloads doing millions of lookups per second, the hash collision handling and memory layout left measurable performance on the table. The Go team had known this for years. The fix required rewriting the map internals without changing the language surface area at all.

Separately, the Go garbage collector does not expose weak references — pointers that do not prevent an object from being collected. Weak references are useful for caches: you want to keep an object around if it is still referenced elsewhere, but not force it to stay alive. Before 1.24's `weak` package, Go developers hacked around this with `sync.Map`, manual TTL-based caches, or third-party libraries. Having this in the standard library with the right semantics closes a real gap.

## How It Works

**Swiss table maps (Go 1.24+)**

The runtime switched the internal map implementation to a Swiss table design, the same approach used by Abseil's `flat_hash_map` in C++ and Rust's `hashbrown`. The key ideas:

- Groups of 8 slots stored contiguously, with a separate 8-byte control word holding the top 7 bits of each key's hash plus a sentinel for empty/deleted slots.
- SIMD-accelerated probing on amd64: a single instruction compares all 8 control bytes simultaneously, finding matching slots or empty slots in one step.
- Better memory density and cache locality compared to the old chained-bucket approach.

From your code, nothing changes. Maps are still created with `make(map[K]V)`, accessed with `m[key]`, and iterated with `for k, v := range m`. The improvement is purely internal. Benchmarks from the Go team show 30–60% faster lookup and insert for medium-to-large maps, with smaller relative gains for tiny maps where the old implementation was already fast enough.

You get this automatically when you build with Go 1.24+. No code changes required.

**The `weak` package (Go 1.24)**

`weak.Pointer[T]` holds a weak reference to a value. It does not prevent garbage collection. You dereference it with `Value()`, which returns `nil` if the object was collected:

```go
import "weak"

type CachedResult struct {
    Data []byte
    // ...
}

type Cache struct {
    mu    sync.Mutex
    items map[string]weak.Pointer[CachedResult]
}

func (c *Cache) Get(key string) *CachedResult {
    c.mu.Lock()
    defer c.mu.Unlock()
    if wp, ok := c.items[key]; ok {
        if result := wp.Value(); result != nil {
            return result // still alive
        }
        delete(c.items, key) // was collected, clean up
    }
    return nil
}

func (c *Cache) Set(key string, result *CachedResult) {
    c.mu.Lock()
    defer c.mu.Unlock()
    c.items[key] = weak.Make(result)
}
```

The cache holds entries without preventing GC from reclaiming them under memory pressure. When memory is tight, the GC collects old results; the cache misses and recomputes. This is the correct behaviour for a memory-sensitive cache without an explicit eviction policy.

**The `unique` package (Go 1.23)**

`unique.Make[T]` interns a value — returns a canonical handle that is `==` comparable:

```go
import "unique"

h1 := unique.Make("hello")
h2 := unique.Make("hello")
fmt.Println(h1 == h2) // true, same handle

// Useful for interning strings in parsers, compilers, or protocol buffers
type Symbol struct{ h unique.Handle[string] }
```

The package maintains a global intern table with weak references — interned values are eligible for GC when no one holds a handle. It is the right tool for reducing memory use when you have many duplicate string or struct values.

**Go toolchain self-management (Go 1.21+)**

The `go` directive in `go.mod` now specifies the minimum required toolchain version, and Go will download the right toolchain automatically if you have a newer `go` binary and the module needs an older one. The `GOTOOLCHAIN` environment variable controls this behaviour. For teams managing multiple Go versions across projects, this removes a whole class of "it works on my machine" issues.

**Profile-guided optimization (PGO) improvements**

PGO — using a CPU profile from a running binary to guide compiler optimizations — was introduced in Go 1.20 and has improved in each subsequent release. In 1.24 and 1.25, the compiler uses PGO data for better devirtualization and inlining decisions. For CPU-bound services, a PGO build can yield 5–15% performance gains with no code changes.

Collecting a profile and applying it:

```go
// 1. Collect a CPU profile from production (30 seconds)
// 2. Save it as default.pgo in the main package directory
// 3. go build -pgo=auto ./...
```

The `auto` flag tells the compiler to look for `default.pgo` automatically.

## In Practice

**Upgrading to Go 1.24 in an existing service** is usually a `go.mod` bump and a `go build`:

```
go 1.24
```

Run `go mod tidy`, rebuild, run your tests. The Swiss table changes and GC improvements activate automatically. If your service does significant map work, run your benchmarks before and after — the improvement is often visible.

**For caches**, replace hand-rolled TTL maps with `weak.Pointer` where eviction-on-memory-pressure is the right semantics. This is a different model from TTL-based eviction: objects stay alive as long as *other* code references them, not for a fixed duration. Combine with a background cleanup goroutine that periodically scans for nil weak pointers and removes them from the map.

**For string-heavy workloads** — parsers, compilers, protocol handling — profile your heap before adopting `unique.Make`. If duplicate string allocations show up as significant in the heap profile, interning is worth adding.

## The Gotchas

**Swiss table map iteration order is still unspecified.** The implementation change did not change the language guarantee: map iteration order is random and may differ across runs, even for the same map contents. Code relying on stable map iteration order was already wrong — the new implementation may make it *more* obviously wrong by changing the order more frequently.

**Weak pointers require a pointer to a heap-allocated object.** You cannot create a weak pointer to a stack-allocated value or a field of a struct. `weak.Make` accepts only pointer types, and the value must be reachable from the heap. The compiler enforces this at compile time.

**PGO profiles go stale.** A profile collected from a binary built three months ago may not represent the current hot paths well. Refresh profiles regularly, especially after significant refactors. A stale profile is not harmful — the compiler treats it as advisory — but it may miss optimization opportunities or optimize code that is no longer hot.

**The `unique` intern table has a cost.** `unique.Make` acquires a lock on the global intern table. In extremely high-throughput scenarios (millions of interns per second from many goroutines), this can become a bottleneck. Profile before committing to it for hot paths.

## Key Takeaway

Go 1.23 through 1.26 represents the runtime growing up. Swiss table maps are a significant internal improvement that you get for free. Weak pointers and the `unique` package fill two genuine gaps in the standard library — caches that do not hold memory hostage and intern tables for deduplication. PGO closes the gap between Go's compilation speed and the runtime performance of more heavily optimizing compilers. Upgrade your `go.mod`, run your benchmarks, and collect a PGO profile if CPU is your bottleneck.

---

**Previous:** [Lesson 4: Structured Logging with slog](/post/go/go-modern-slog/)

---

🎓 **Course Complete!** You have finished *Modern Go: 1.21 to 1.26*. You now know the iterator protocol, enhanced HTTP routing, the loop variable fix, structured logging with slog, and the runtime improvements in 1.24–1.26. The modern Go standard library is richer than it has ever been — and it keeps getting better.
