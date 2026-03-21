---
title: "Lesson 7: CPU vs Memory Tradeoffs — Cache it or compute it, pick one"
author: Atharva Pandey
series: "Go Performance Engineering"
lesson: 7
keywords:
  - Go
  - golang
  - performance
  - CPU tradeoffs
  - memory tradeoffs
  - caching
  - memoization
  - sync.Pool
  - precomputation
tags:
  - Go tutorial
  - golang
  - performance
date: '2025-02-20T00:00:00.000Z'
---

Every performance optimization ultimately makes the same trade: you're giving up memory to gain CPU time, or giving up CPU time to reduce memory usage. There's no free lunch. The skill isn't knowing that the tradeoff exists — it's knowing which side of it you're on, and making the choice consciously rather than accidentally. I've optimized for memory when the bottleneck was CPU, and optimized for CPU when memory was the problem. Both directions are wrong when you pick them without data.

## The Problem

The hidden version of this tradeoff is precomputation: doing work once upfront and storing the result to avoid doing it again later. It sounds obviously good, but it has costs that are easy to undercount.

First, the memory cost: cached results take space. If you precompute a lookup table with a million entries, you're holding a million entries in memory forever. If those entries contain pointers, the GC scans them every cycle. If your process has other memory-sensitive components, the cache competes for resources.

Second, the stale data problem: cached results go out of date. If your computation depends on inputs that change, your cache needs an invalidation strategy, which adds code complexity that is itself a source of bugs.

Third, the cache miss problem: a cache that isn't warm is slower than no cache. Cold starts, low-traffic paths, and rare keys all pay both the lookup cost and the computation cost.

The opposite problem — recomputing things that could reasonably be cached — has its own failure mode. The classic example is hash computation:

```go
// COSTLY — recomputes the same hash for the same input on every request
type Router struct {
    routes map[string]Handler
}

func (r *Router) Match(path string) Handler {
    // If path matching involves regex compilation or heavy parsing,
    // doing it per-request is expensive
    for pattern, handler := range r.routes {
        if matchPattern(pattern, path) { // pattern compiled fresh every time
            return handler
        }
    }
    return nil
}
```

If `matchPattern` compiles a regex from `pattern` on every call, and you have 50 routes and 10,000 requests per second, you're compiling 500,000 regexes per second. Regex compilation in Go is expensive — on the order of microseconds per pattern. That's 500ms of CPU time per second spent on pure redundant work.

## The Idiomatic Way

The canonical Go solution for precomputation is compile-once-at-startup. Initialize expensive computed values during setup, not during request handling:

```go
// Compile patterns once, use many times
type Route struct {
    pattern *regexp.Regexp
    handler Handler
}

type Router struct {
    routes []Route
}

func NewRouter(specs map[string]Handler) *Router {
    routes := make([]Route, 0, len(specs))
    for pattern, handler := range specs {
        re := regexp.MustCompile(pattern) // panic on bad pattern at startup, not at request time
        routes = append(routes, Route{pattern: re, handler: handler})
    }
    return &Router{routes: routes}
}

func (r *Router) Match(path string) Handler {
    for _, route := range r.routes {
        if route.pattern.MatchString(path) {
            return route.handler
        }
    }
    return nil
}
```

Zero regex compilations at request time. The memory cost is one `*regexp.Regexp` per route — negligible. The CPU time per request is now just `MatchString` execution, which is much cheaper than compilation.

For results that depend on runtime inputs and are expensive to compute, `sync.Map` or a hand-rolled LRU with bounded size is appropriate. But my preferred pattern for bounded caching in Go is explicit size limits with eviction:

```go
// Simple LRU-ish cache using sync.Map for concurrent reads
// (For production use, prefer github.com/hashicorp/golang-lru)
type BoundedCache struct {
    mu    sync.RWMutex
    data  map[string]cacheEntry
    limit int
}

type cacheEntry struct {
    value    []byte
    accessed time.Time
}

func (c *BoundedCache) Get(key string) ([]byte, bool) {
    c.mu.RLock()
    e, ok := c.data[key]
    c.mu.RUnlock()
    if ok {
        c.mu.Lock()
        e.accessed = time.Now()
        c.data[key] = e
        c.mu.Unlock()
    }
    return e.value, ok
}

func (c *BoundedCache) Set(key string, value []byte) {
    c.mu.Lock()
    defer c.mu.Unlock()
    if len(c.data) >= c.limit {
        c.evictOldest()
    }
    c.data[key] = cacheEntry{value: value, accessed: time.Now()}
}
```

The explicit `limit` means memory is bounded. The eviction happens before the limit is exceeded. This is the pattern I reach for when `sync.Pool` isn't applicable (because `sync.Pool` objects can be reclaimed between GC cycles — unsuitable for caches where you want persistence).

For the CPU-heavy case where you want to trade memory for computation speed, the lookup table is the oldest trick in the book:

```go
// Precomputed CRC32 table — computed once at init, used millions of times
var crcTable = crc32.MakeTable(crc32.IEEE) // 1KB, computed once

func checksumData(data []byte) uint32 {
    return crc32.Checksum(data, crcTable)
}

// For custom use cases: precompute a mapping for expensive operations
var encodingTable [256]byte

func init() {
    for i := range encodingTable {
        encodingTable[i] = computeEncoding(byte(i)) // expensive per-byte operation
    }
}

func encodeBuffer(buf []byte) {
    for i, b := range buf {
        buf[i] = encodingTable[b] // O(1) table lookup instead of O(?) computation
    }
}
```

The `init` function runs once at startup. The table is 256 bytes. The loop that uses it reduces an expensive operation to an array index. This pattern is universal — it appears in UTF-8 encoders, hash functions, compression codecs, and cryptography everywhere.

## In The Wild

I worked on a service that validated incoming data records against a set of business rules. Each rule was expressed as a struct with computed derived fields — things like pre-processed regular expressions, pre-sorted lists, and cached hash values. The original code recomputed these every time a rule was applied:

```go
// BEFORE — rule evaluation recomputed everything per record
func (r Rule) Validate(record Record) bool {
    // parsePattern compiled a regex from r.PatternStr every call
    pattern := parsePattern(r.PatternStr)
    // buildExpected allocated a new sorted list every call
    expected := buildExpected(r.AllowedValues)
    return pattern.MatchString(record.Value) && contains(expected, record.Category)
}
```

For 100,000 records and 20 rules, this function was called 2 million times per batch. The `parsePattern` and `buildExpected` calls were taking 65% of total CPU time.

The fix was to precompute the derived fields when the rule was constructed:

```go
// AFTER — precompute once at rule construction time
type Rule struct {
    PatternStr    string
    AllowedValues []string
    // Precomputed
    compiledPattern *regexp.Regexp
    sortedAllowed   []string
}

func NewRule(patternStr string, allowed []string) Rule {
    compiled := regexp.MustCompile(patternStr)
    sorted := make([]string, len(allowed))
    copy(sorted, allowed)
    sort.Strings(sorted)
    return Rule{
        PatternStr:      patternStr,
        AllowedValues:   allowed,
        compiledPattern: compiled,
        sortedAllowed:   sorted,
    }
}

func (r Rule) Validate(record Record) bool {
    i := sort.SearchStrings(r.sortedAllowed, record.Category)
    found := i < len(r.sortedAllowed) && r.sortedAllowed[i] == record.Category
    return r.compiledPattern.MatchString(record.Value) && found
}
```

Batch processing time dropped from 4.2 seconds to 0.8 seconds. The memory cost was one `*regexp.Regexp` and one sorted `[]string` per rule — negligible for 20 rules. The trade was obviously correct; we just hadn't made it consciously the first time.

## The Gotchas

**`sync.Pool` is not a cache.** `sync.Pool` objects can be dropped by the GC at any time — specifically, on every GC cycle before Go 1.13, and on select GC cycles after. Objects in the pool are not guaranteed to persist between requests. Use `sync.Pool` to reuse temporary buffers and objects within the lifetime of a single request or operation, not across requests.

**Cache invalidation is the hard part.** If you cache computed results from dynamic data and the data changes, your cache is a bug waiting to happen. The safest caches are either immutable (computed at startup, never changed) or have a TTL with acceptable stale tolerance.

**The memory cost of caches compounds.** Each cached value stays in heap memory. If the cached value contains pointers, the GC scans it every cycle. A cache of ten million small string-valued entries can add 50ms+ of GC scan time in a large heap. Sometimes the "fast" cache is slower end-to-end than recomputing because of this GC overhead.

**Profile before you cache.** Adding a cache to a code path that isn't a bottleneck adds complexity, memory usage, and a potential bug surface for zero performance gain. The `pprof` CPU profile will tell you whether the computation is actually expensive enough to warrant caching.

## Key Takeaway

The CPU vs memory tradeoff is fundamental, not optional. Every time you cache something, you're trading heap space and GC scan cost for faster repeated access. Every time you recompute, you're trading CPU cycles for simplicity and lower memory pressure. The question is never "should I cache this?" in the abstract — it's "does the profile show this computation is a bottleneck, and is the memory cost of caching it acceptable?" Make the tradeoff explicitly, with data, and revisit it when usage patterns change.

---

[← Lesson 6: pprof Deep Dive](/post/go/go-perf-pprof) | [Course Index](/series/go-performance-engineering) | [Next → Lesson 8: Avoiding Premature Optimization](/post/go/go-perf-premature-optimization)
