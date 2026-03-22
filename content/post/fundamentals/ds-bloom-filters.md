---
title: "Lesson 10: Bloom Filters — Probably yes, definitely no"
author: Atharva Pandey
series: "Data Structures for Production Engineers"
lesson: 10
keywords: [data structures, computer science, bloom filter, probabilistic, false positive, hashing]
tags: [fundamentals, data structures]
date: '2024-08-19T00:00:00.000Z'
---

There's a beautiful data structure that will tell you one of two things: "definitely not in the set" or "probably in the set." That asymmetry — where false negatives are impossible but false positives are allowed — turns out to be useful in an enormous number of production scenarios.

Bloom filters use a fraction of the memory of a hash set, and the math behind their false positive rate is surprisingly elegant. Once you understand them, you'll see why databases, CDNs, and distributed caches reach for them constantly.

## How It Actually Works

A bloom filter is a bit array of size `m`, combined with `k` independent hash functions. Each hash function maps a key to one of the `m` bit positions.

**Insert**: hash the key with all `k` functions, set those `k` bits to 1.

**Query**: hash the key with all `k` functions, check if all `k` bits are 1. If any bit is 0, the key is definitely not in the set. If all bits are 1, the key *probably* is in the set.

The "probably" qualifier comes from hash collisions: a query key might map to `k` bits that were all set by different previous insertions, producing a false positive.

```go
package main

import (
    "fmt"
    "hash/fnv"
    "math"
)

type BloomFilter struct {
    bits    []bool
    m       uint // number of bits
    k       uint // number of hash functions
    count   int
}

func NewBloomFilter(expectedItems int, falsePositiveRate float64) *BloomFilter {
    // Optimal m and k formulas:
    // m = -(n * ln(p)) / (ln(2)^2)
    // k = (m/n) * ln(2)
    n := float64(expectedItems)
    p := falsePositiveRate

    m := uint(math.Ceil(-(n * math.Log(p)) / (math.Log(2) * math.Log(2))))
    k := uint(math.Ceil((float64(m) / n) * math.Log(2)))

    return &BloomFilter{
        bits: make([]bool, m),
        m:    m,
        k:    k,
    }
}

func (bf *BloomFilter) positions(item string) []uint {
    positions := make([]uint, bf.k)
    for i := uint(0); i < bf.k; i++ {
        h := fnv.New64a()
        // Different seeds via two hash functions: h1(x) + i*h2(x)
        h.Write([]byte(item))
        h.Write([]byte{byte(i), byte(i >> 8)})
        positions[i] = uint(h.Sum64()) % bf.m
    }
    return positions
}

func (bf *BloomFilter) Add(item string) {
    for _, pos := range bf.positions(item) {
        bf.bits[pos] = true
    }
    bf.count++
}

func (bf *BloomFilter) MightContain(item string) bool {
    for _, pos := range bf.positions(item) {
        if !bf.bits[pos] {
            return false // definitely not present
        }
    }
    return true // probably present
}

func (bf *BloomFilter) FalsePositiveRate() float64 {
    // Actual FPR: (1 - e^(-k*n/m))^k
    k := float64(bf.k)
    n := float64(bf.count)
    m := float64(bf.m)
    return math.Pow(1-math.Exp(-k*n/m), k)
}

func main() {
    // 10K items, target 1% false positive rate
    bf := NewBloomFilter(10_000, 0.01)

    fmt.Printf("Bit array size: %d bits (%.1f KB)\n", bf.m, float64(bf.m)/8/1024)
    fmt.Printf("Hash functions: %d\n", bf.k)

    // Add 10K items
    for i := 0; i < 10_000; i++ {
        bf.Add(fmt.Sprintf("user:%d", i))
    }

    // Test membership
    fmt.Println("\nMembership checks:")
    fmt.Println("user:5000 (inserted):", bf.MightContain("user:5000")) // true (correct)
    fmt.Println("user:99999 (not inserted):", bf.MightContain("user:99999")) // probably false
    fmt.Printf("Estimated false positive rate: %.2f%%\n", bf.FalsePositiveRate()*100)
}
```

The math works out beautifully. For 10K items with 1% target FPR, the filter uses about 12KB. A hash set with 10K string keys would use megabytes.

## When to Use It

Bloom filters shine when:
- You need to avoid expensive lookups (disk, network) for items you've definitely never seen
- False positives are acceptable (you'll verify with the real store on a hit)
- Memory is constrained but you need fast set membership
- You're building a "negative cache" — items known to not exist

Real production uses:
- **Databases**: check if a key exists before reading from disk (Cassandra, RocksDB, LevelDB all do this)
- **CDN caches**: before forwarding a request to origin, check if the content is definitely not cached
- **Distributed caches**: avoid stampedes by quickly filtering non-existent keys before hitting the database
- **Web crawlers**: don't re-crawl URLs you've already visited
- **Malware detection**: check file hashes against a known-bad set without storing every hash

Don't use bloom filters when:
- False positives are not acceptable (use a hash set)
- You need to delete elements (standard bloom filters don't support deletion; counting bloom filters do, at higher memory cost)
- The set is small enough to fit in a hash map anyway

## Production Example

The classic production scenario: protecting a database from cache-miss storms on non-existent keys. Without a bloom filter, every request for a non-existent user_id hits your cache (miss), then hits your database (also miss) — useless I/O at scale.

```go
package main

import (
    "fmt"
    "time"
)

// Simulate a cache + database lookup protected by a bloom filter
type DataStore struct {
    bloom    *BloomFilter
    cache    map[string]string // in-memory cache
    db       map[string]string // "database"
    dbHits   int
    cacheMisses int
}

func NewDataStore() *DataStore {
    ds := &DataStore{
        bloom: NewBloomFilter(100_000, 0.01),
        cache: make(map[string]string),
        db:    make(map[string]string),
    }
    // Pre-populate database with 100K users
    for i := 0; i < 100_000; i++ {
        key := fmt.Sprintf("user:%d", i)
        ds.db[key] = fmt.Sprintf("data-for-%d", i)
        ds.bloom.Add(key) // seed bloom filter with all known keys
    }
    return ds
}

func (ds *DataStore) Get(key string) (string, bool) {
    // Fast path: bloom filter says definitely not present
    if !ds.bloom.MightContain(key) {
        return "", false // no DB hit needed
    }

    // Check cache
    if v, ok := ds.cache[key]; ok {
        return v, true
    }
    ds.cacheMisses++

    // Cache miss — check DB
    if v, ok := ds.db[key]; ok {
        ds.dbHits++
        ds.cache[key] = v
        return v, true
    }

    // Key exists in bloom filter index but not in DB — false positive
    return "", false
}

func main() {
    ds := NewDataStore()

    start := time.Now()

    // Simulate 100K requests: 50K for valid users, 50K for non-existent
    validHits := 0
    bloomBlocked := 0

    for i := 0; i < 50_000; i++ {
        if _, ok := ds.Get(fmt.Sprintf("user:%d", i)); ok {
            validHits++
        }
    }

    for i := 100_000; i < 150_000; i++ {
        // Non-existent keys — bloom filter should block most of these
        if !ds.bloom.MightContain(fmt.Sprintf("user:%d", i)) {
            bloomBlocked++
        }
    }

    fmt.Printf("Duration: %v\n", time.Since(start))
    fmt.Printf("Valid hits: %d\n", validHits)
    fmt.Printf("Non-existent keys blocked by bloom filter: %d/50000\n", bloomBlocked)
    fmt.Printf("DB hits: %d\n", ds.dbHits)
    fmt.Printf("Estimated false positives: %d\n", 50_000-bloomBlocked)
    fmt.Printf("False positive rate: %.2f%%\n", ds.bloom.FalsePositiveRate()*100)
}
```

The bloom filter absorbs almost all of the "definitely not in database" queries before they become cache misses or database reads. At scale — millions of requests per second — this is a significant reduction in infrastructure load.

## The Tradeoffs

**False positive rate grows as the filter fills.** If you add more items than your initial estimate, the false positive rate climbs. Size your bloom filter with some headroom, or use a scalable bloom filter (a series of filters that expand automatically).

**No deletion in standard bloom filters.** Setting bits to 0 would invalidate other items that share those bits. Counting bloom filters maintain a count per bit instead of a boolean, allowing decrements, but they use 4x-8x more memory.

**All hash functions matter.** The quality of your false positive rate depends on hash independence. Using k independent hash functions is theoretically ideal but expensive. In practice, double hashing (gi(x) = h1(x) + i*h2(x)) gives statistically good results with just two hash functions.

**The filter is opaque.** You can't enumerate the elements in a bloom filter or tell which elements caused a particular bit to be set. It's a membership oracle, not a set.

## Key Takeaway

Bloom filters embody a powerful engineering tradeoff: trade a small, bounded error rate for massive memory savings and constant-time membership tests. The constraint — "definitely no, probably yes" — is the right semantic for many production scenarios where the cost of a false positive is just an extra lookup, not a correctness violation.

Cassandra, RocksDB, Google Bigtable, and Chrome's Safe Browsing API all use bloom filters. The next time you see a cache or storage engine with "probabilistic" in its description, you're almost certainly looking at a bloom filter.

---

*Previous: [Lesson 9: Tries — Prefix matching, autocomplete, routing tables](/post/fundamentals/ds-tries/)*

*Next: [Lesson 11: Skip Lists — How Redis sorted sets work](/post/fundamentals/ds-skip-lists/)*
