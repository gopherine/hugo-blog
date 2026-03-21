---
title: "Lesson 3: Slice and Map Performance — The data structure tax"
author: Atharva Pandey
series: "Go Performance Engineering"
lesson: 3
keywords:
  - Go
  - golang
  - performance
  - slice performance
  - map performance
  - append
  - map iteration
  - data structures
tags:
  - Go tutorial
  - golang
  - performance
date: '2024-08-20T00:00:00.000Z'
---

Slices and maps are so convenient in Go that it's easy to forget they're not free. They have hidden costs — in allocations, in CPU cache misses, in GC scanning time — that only become visible when you push them into a hot path and watch your benchmarks light up. I've been burned by both, sometimes in embarrassing ways, and building intuition for when those costs matter has saved me more than one production incident.

## The Problem

The slice tax starts with growth. A slice in Go is a three-word header: a pointer to a backing array, a length, and a capacity. When you `append` past the capacity, Go allocates a new, larger backing array and copies everything over. The growth factor is roughly 2x for small slices, tapering off for larger ones. Each growth is a heap allocation and a memory copy. If you start with a nil slice and append a thousand elements one at a time, you get roughly ten allocation-and-copy cycles before you're done.

```go
// COSTLY — growth allocations and copies on every capacity breach
func collectResults(n int) []Result {
    var results []Result
    for i := 0; i < n; i++ {
        results = append(results, compute(i))
    }
    return results
}
```

The map tax is subtler. Maps in Go are hash tables with a complex internal structure involving buckets and overflow chains. Every insertion and lookup involves hashing the key, finding the bucket, and iterating the bucket chain. For small maps with simple keys this is fast. But maps have a few properties that can surprise you:

- Map iteration order is randomized on every run (by design).
- Maps cannot shrink. If you populate a map with a million entries and delete them all, the memory stays allocated.
- Iterating a map is slower than iterating a slice of comparable size because of pointer chasing and cache-unfriendly access patterns.

```go
// MAP ITERATION — cache-unfriendly, pointer chasing through bucket chains
func sumMap(m map[string]int) int {
    total := 0
    for _, v := range m {
        total += v
    }
    return total
}

// SLICE ITERATION — sequential memory, CPU prefetcher-friendly
func sumSlice(s []int) int {
    total := 0
    for _, v := range s {
        total += v
    }
    return total
}
```

For a thousand-element collection, `sumSlice` is typically 3–5x faster than `sumMap` for the same data. The difference is memory layout: slice elements are contiguous, so the CPU's prefetcher can load cache lines ahead of your iteration. Map buckets are scattered.

## The Idiomatic Way

For slices: **always pre-allocate when you know (or can estimate) the size.** This is the single most impactful micro-optimization I apply as a first pass on any data-building code.

```go
// PRE-ALLOCATED — zero growth allocations
func collectResults(n int) []Result {
    results := make([]Result, 0, n) // capacity hint
    for i := 0; i < n; i++ {
        results = append(results, compute(i))
    }
    return results
}
```

The `make([]Result, 0, n)` allocates exactly one backing array, sized for `n` elements. No growth cycles, no copies. For variable-size output where `n` is an upper bound, even an overestimate is usually better than starting from zero — the excess capacity costs a bit of memory but eliminates all the intermediate allocations.

For maps: **pre-size with a capacity hint.** Unlike slices, Go maps don't expose their current capacity directly, but `make(map[K]V, hint)` tells the runtime to pre-allocate enough buckets to hold approximately `hint` elements before the first rehash:

```go
// PRE-SIZED MAP — avoids bucket growth allocations during population
func buildIndex(records []Record) map[string]Record {
    index := make(map[string]Record, len(records))
    for _, r := range records {
        index[r.ID] = r
    }
    return index
}
```

Without the hint, inserting `len(records)` entries triggers multiple internal rehashes. With it, the map is typically built in a single pass with no rehashing. The benchmark difference for large maps can be 30–50% in build time.

For cases where you're iterating a map repeatedly and order doesn't matter, consider whether a slice of structs serves better:

```go
// For read-heavy workloads with no lookup requirement: use a slice
type Entry struct {
    Key   string
    Value int
}

// Much more cache-friendly for sequential processing
func processEntries(entries []Entry) {
    for _, e := range entries {
        handle(e.Key, e.Value)
    }
}
```

## In The Wild

I was working on a service that built lookup tables at startup from configuration files. The code looked like this:

```go
func loadConfig(path string) map[string]Config {
    data := parseFile(path)   // returns []RawConfig, typically ~50k entries
    result := make(map[string]Config) // no size hint
    for _, raw := range data {
        result[raw.Key] = transform(raw)
    }
    return result
}
```

Startup time was around 800ms. After adding a size hint — `make(map[string]Config, len(data))` — startup dropped to 480ms. One character change: adding `, len(data)`. No algorithm change, no structural change. Just telling the runtime how much space to prepare.

The second issue was memory: after loading, the service had a GC scan problem. Maps with pointer-valued types cause the GC to scan every key and value on every collection cycle. The config values contained strings (which are pointers internally), so the GC scanned all 50k entries every cycle. Moving to a struct with only value types where possible — replacing `string` pointers with interned integer IDs for the hot lookup path — cut GC scan time significantly.

```go
// BEFORE: GC scans all map entries because values contain pointers (strings)
type Config struct {
    Name    string
    Region  string
    Weight  int
}

// AFTER: hot lookup path uses integer IDs; strings stored separately
type ConfigCompact struct {
    NameID   uint32
    RegionID uint32
    Weight   int
}
```

GC pause duration at the 99th percentile fell by about 15ms under load. The string interning table was built once at startup and never modified, so it was cheap to maintain.

## The Gotchas

**`copy` doesn't resize the destination.** `copy(dst, src)` copies `min(len(dst), len(src))` elements. If `dst` is shorter than `src`, you silently lose data. Always ensure `len(dst) >= len(src)` or use `append(dst, src...)` instead.

**Slice of pointers vs slice of values.** A `[]Foo` stores values contiguously — fast iteration, GC scans the slice header only if `Foo` contains no pointers. A `[]*Foo` stores pointers — each lookup requires a pointer dereference (potential cache miss), and the GC must trace every pointer in the slice. Prefer value slices unless you need the indirection.

**Map key types matter for performance.** String keys require hashing the full string content. Integer keys are hashed much faster. For maps keyed by short strings that are used millions of times, consider whether you can intern the strings to integers for the hot path.

**Nil map reads are safe; writes panic.** `var m map[string]int; _ = m["key"]` returns the zero value without panicking. `m["key"] = 1` panics. This asymmetry trips up everyone at least once.

## Key Takeaway

Slices and maps are the workhorse data structures in Go, and their default behavior is tuned for correctness and convenience, not peak throughput. The data structure tax is real: grow cycles for slices, rehashing for maps, pointer chasing for both when the GC comes to scan. The remedies are almost always the same — pre-allocate when you know the size, prefer value types over pointer types for hot-path collections, and reach for `[]struct` instead of `map` when random access isn't required. These aren't premature optimizations; they're habits that cost you nothing in code clarity and save you from explaining slow startup times at 2 AM.

---

[← Lesson 2: Stack vs Heap Intuition](/post/go/go-perf-stack-heap) | [Course Index](/series/go-performance-engineering) | [Next → Lesson 4: String and Byte Conversions](/post/go/go-perf-string-bytes)
