---
title: "Lesson 3: Hash Maps — O(1) with asterisks"
author: Atharva Pandey
series: "Data Structures for Production Engineers"
lesson: 3
keywords: [data structures, computer science, hash maps, hash tables, collision, load factor]
tags: [fundamentals, data structures]
date: '2024-05-15T00:00:00.000Z'
---

Hash maps are the workhorse of production software. Nearly every caching layer, session store, deduplication system, and configuration lookup you've ever written relies on one. They're fast, they're flexible, and they're genuinely O(1) — with some asterisks that matter enormously in production.

I want to walk through how they actually work, because the "O(1) lookup" claim hides a lot of complexity that shows up in the worst possible moments: under load, with adversarial input, or when your hash function is subtly wrong.

## How It Actually Works

A hash map (or hash table) maps keys to values using three components: a hash function, a backing array, and a collision resolution strategy.

The flow: take a key, run it through a hash function to get an integer, take that integer modulo the array length to get a bucket index, store the key-value pair at that index.

```go
package main

import "fmt"

// A toy hash map for illustration — don't use this in production
type Entry struct {
    Key   string
    Value int
}

type HashMap struct {
    buckets [][]Entry
    size    int
    count   int
}

func NewHashMap(size int) *HashMap {
    return &HashMap{
        buckets: make([][]Entry, size),
        size:    size,
    }
}

func (h *HashMap) hash(key string) int {
    hash := 0
    for _, c := range key {
        hash = (hash*31 + int(c)) % h.size
    }
    return hash
}

func (h *HashMap) Set(key string, value int) {
    idx := h.hash(key)
    for i, e := range h.buckets[idx] {
        if e.Key == key {
            h.buckets[idx][i].Value = value
            return
        }
    }
    h.buckets[idx] = append(h.buckets[idx], Entry{key, value})
    h.count++
}

func (h *HashMap) Get(key string) (int, bool) {
    idx := h.hash(key)
    for _, e := range h.buckets[idx] {
        if e.Key == key {
            return e.Value, true
        }
    }
    return 0, false
}

func main() {
    m := NewHashMap(16)
    m.Set("requests", 1024)
    m.Set("errors", 3)

    if v, ok := m.Get("requests"); ok {
        fmt.Println("requests:", v)
    }
}
```

This uses **separate chaining** — each bucket holds a list of entries that hashed to the same index. When two keys land in the same bucket, that's a collision. A good hash function distributes keys uniformly so collisions are rare.

Go's built-in `map` uses a more sophisticated version: open addressing with 8-element buckets and incremental rehashing. But the fundamentals are the same.

## When to Use It

Hash maps are the right tool for:
- Frequency counting (word counts, request rates, error tallies)
- Deduplication (seen-set for idempotency checks)
- Lookup by string key (user sessions, feature flags, config values)
- Caching (memoization, HTTP response caches)
- Grouping or partitioning data

They're the wrong tool when:
- You need ordered iteration (use a sorted structure)
- You need range queries ("all keys between A and Z") — use a B-tree
- Memory is extremely constrained — consider a bloom filter for existence checks

## Production Example

Rate limiting is one of the most common production uses of hash maps. Here's a token bucket rate limiter backed by a simple map:

```go
package main

import (
    "fmt"
    "sync"
    "time"
)

type Bucket struct {
    tokens    float64
    lastRefil time.Time
}

type RateLimiter struct {
    mu       sync.Mutex
    buckets  map[string]*Bucket
    rate     float64 // tokens per second
    capacity float64
}

func NewRateLimiter(rate, capacity float64) *RateLimiter {
    return &RateLimiter{
        buckets:  make(map[string]*Bucket),
        rate:     rate,
        capacity: capacity,
    }
}

func (rl *RateLimiter) Allow(key string) bool {
    rl.mu.Lock()
    defer rl.mu.Unlock()

    now := time.Now()
    b, ok := rl.buckets[key]
    if !ok {
        b = &Bucket{tokens: rl.capacity, lastRefil: now}
        rl.buckets[key] = b
    }

    elapsed := now.Sub(b.lastRefil).Seconds()
    b.tokens = min(rl.capacity, b.tokens+elapsed*rl.rate)
    b.lastRefil = now

    if b.tokens >= 1.0 {
        b.tokens--
        return true
    }
    return false
}

func min(a, b float64) float64 {
    if a < b {
        return a
    }
    return b
}

func main() {
    rl := NewRateLimiter(10, 10) // 10 req/s, burst of 10

    for i := 0; i < 15; i++ {
        allowed := rl.Allow("user:123")
        fmt.Printf("Request %2d: allowed=%v\n", i+1, allowed)
    }
}
```

The map lookup `rl.buckets[key]` is O(1) amortized. A per-user rate limiter with 10 million active users still does that lookup in roughly constant time. No sorted structure required, no binary search, just hash and retrieve.

## The Tradeoffs

**The asterisks on O(1).** Hash map operations are O(1) *amortized*. When the map exceeds its load factor (typically 6.5 elements per bucket in Go), it doubles in size and rehashes every element. That individual operation is O(n). In a latency-sensitive service, this can spike your p99. Pre-size your maps if you know the rough cardinality:

```go
// Triggers multiple resizes if populated naively
m := make(map[string]int)

// Single allocation, no resize during population
m := make(map[string]int, 10_000)
```

**Hash collisions under adversarial input.** If an attacker can control your keys and knows your hash function, they can craft inputs that all land in the same bucket, degrading lookups to O(n). Go mitigates this by seeding its hash function with randomness at process start — you get different bucket distributions each run. Never use a hash map to index user-controlled strings with a non-randomized hash function.

**Iteration order is undefined.** Go deliberately randomizes map iteration order. If you're relying on consistent map iteration for business logic, you have a latent bug. Sort your keys explicitly before iteration when order matters.

```go
import "sort"

keys := make([]string, 0, len(m))
for k := range m {
    keys = append(keys, k)
}
sort.Strings(keys)
for _, k := range keys {
    fmt.Println(k, m[k])
}
```

**Memory layout is not friendly.** Go's map is a complex structure with pointer-chased buckets. You won't get the same cache line efficiency as a slice. For read-heavy workloads over small fixed key sets, a sorted slice with binary search can actually outperform a map because of better cache behavior.

## Key Takeaway

Hash maps are probably the most useful data structure in your toolbox, but "O(1)" is a marketing claim without the full context. Understand your load factor, pre-size your maps, and know what happens during rehash. In production, the difference between an amortized O(1) operation and one that occasionally triggers a full rehash can be the difference between a flat latency profile and mysterious p99 spikes.

When someone asks you why Redis is fast, a big part of the answer is: very well-implemented hash tables.

---

*Previous: [Lesson 2: Linked Lists — Almost never the right choice](/post/fundamentals/ds-linked-lists/)*

*Next: [Lesson 4: Stacks and Queues — The structures hiding in every system](/post/fundamentals/ds-stacks-queues/)*
