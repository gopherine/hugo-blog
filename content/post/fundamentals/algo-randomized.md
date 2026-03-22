---
title: "Lesson 14: Randomized Algorithms — Reservoir sampling, HyperLogLog, probabilistic counting"
author: "Atharva Pandey"
keywords:
  - reservoir sampling
  - HyperLogLog
  - probabilistic algorithms
  - approximate counting
  - bloom filter
  - production engineering
tags:
  - fundamentals
  - algorithms
series: "Algorithms That Matter in Production"
lesson: 14
date: "2024-11-01T00:00:00.000Z"
---

There is a class of production problems where exact answers are either impossible or not worth the cost. You want to know approximately how many unique visitors hit your site today. You want to sample 1% of requests for tracing without reading every request into memory first. You want to check if a username is already taken without querying the database on every keystroke.

Randomized algorithms provide exact answers with known probability bounds, or approximate answers with bounded error, using a fraction of the memory or time that exact computation would require. I was skeptical of "probabilistic" algorithms for a long time — it seemed like trading correctness for efficiency. Then I learned what the error bounds actually are. A HyperLogLog cardinality estimate with 1.5% error using 12KB of memory is a better engineering choice than a perfect count using 100MB, for the vast majority of use cases.

## How It Works

**Reservoir sampling** solves a fundamental streaming problem: how do you select k items from a stream of n items with uniform probability, when you do not know n in advance and cannot store all n items?

The algorithm: keep a reservoir of k items. For the i-th item (where i > k), include it with probability k/i. If included, replace a randomly selected item in the reservoir.

```go
// Reservoir sampling — uniformly sample k items from a stream of unknown length
// Each item in the stream has equal probability of being in the final sample
import "math/rand"

func reservoirSample[T any](stream <-chan T, k int) []T {
    reservoir := make([]T, 0, k)
    i := 0

    for item := range stream {
        i++
        if len(reservoir) < k {
            reservoir = append(reservoir, item)
        } else {
            // Replace a random existing item with probability k/i
            j := rand.Intn(i)
            if j < k {
                reservoir[j] = item
            }
        }
    }
    return reservoir
}

// Synchronous version for slice input
func sampleK[T any](items []T, k int, rng *rand.Rand) []T {
    if k >= len(items) {
        result := make([]T, len(items))
        copy(result, items)
        return result
    }
    reservoir := make([]T, k)
    copy(reservoir, items[:k])
    for i := k; i < len(items); i++ {
        j := rng.Intn(i + 1)
        if j < k {
            reservoir[j] = items[i]
        }
    }
    return reservoir
}
```

The mathematical proof is elegant: after processing all n items, each item has been selected with probability exactly k/n. No item is favored, no randomness accumulates incorrectly.

**HyperLogLog** estimates the cardinality (number of distinct elements) of a multiset using O(log log n) bits per element — about 12KB for counts up to 10^18 with 1.5% error. The algorithm:

1. Hash each element to a uniformly distributed bit string
2. Track the maximum number of leading zeros seen across k buckets
3. Use harmonic mean to estimate cardinality from the leading-zero statistics

```go
// Simplified HyperLogLog — production use should use a vetted library
// (axiomhq/hyperloglog or DataDog/sketches-go)
import (
    "hash/fnv"
    "math"
    "math/bits"
)

const hllBuckets = 64 // m = 64 gives ~1.625m bytes memory, ~1.04% std error

type HyperLogLog struct {
    registers [hllBuckets]uint8
}

func (h *HyperLogLog) Add(item string) {
    hasher := fnv.New64a()
    hasher.Write([]byte(item))
    x := hasher.Sum64()

    // Use first log2(m) bits to select bucket
    bucketBits := uint(math.Log2(hllBuckets))
    bucket := x >> (64 - bucketBits)
    remaining := x << bucketBits

    // Count leading zeros in remaining bits + 1
    leadingZeros := uint8(bits.LeadingZeros64(remaining)) + 1

    // Track maximum
    if leadingZeros > h.registers[bucket] {
        h.registers[bucket] = leadingZeros
    }
}

func (h *HyperLogLog) Count() uint64 {
    // Harmonic mean of 2^register values
    sum := 0.0
    for _, r := range h.registers {
        sum += math.Pow(2, -float64(r))
    }
    alpha := 0.7213 / (1 + 1.079/float64(hllBuckets)) // bias correction constant
    estimate := alpha * float64(hllBuckets*hllBuckets) / sum
    return uint64(estimate)
}
```

**Bloom filter** checks set membership with no false negatives and a configurable false positive rate. Uses k hash functions and a bit array. If any bit is 0, the element is definitely not in the set. If all bits are 1, the element is probably in the set (with a known false positive probability).

```go
type BloomFilter struct {
    bits     []bool
    numHash  int
    size     uint
}

func NewBloomFilter(expectedItems int, falsePositiveRate float64) *BloomFilter {
    // Optimal size and hash count formulas
    m := uint(-float64(expectedItems) * math.Log(falsePositiveRate) / (math.Log(2) * math.Log(2)))
    k := int(float64(m) / float64(expectedItems) * math.Log(2))
    return &BloomFilter{bits: make([]bool, m), numHash: k, size: m}
}

func (bf *BloomFilter) indices(item string) []uint {
    // Use two hash functions to simulate k independent hashes
    h1 := fnv.New64a()
    h1.Write([]byte(item))
    h1val := h1.Sum64()

    h2 := fnv.New64()
    h2.Write([]byte(item))
    h2val := h2.Sum64()

    indices := make([]uint, bf.numHash)
    for i := 0; i < bf.numHash; i++ {
        indices[i] = uint(h1val+uint64(i)*h2val) % bf.size
    }
    return indices
}

func (bf *BloomFilter) Add(item string) {
    for _, idx := range bf.indices(item) {
        bf.bits[idx] = true
    }
}

// MightContain: false means definitely not in set; true means probably in set
func (bf *BloomFilter) MightContain(item string) bool {
    for _, idx := range bf.indices(item) {
        if !bf.bits[idx] {
            return false
        }
    }
    return true
}
```

## When You Need It

- **Reservoir sampling**: distributed tracing (sample 1% of requests), A/B test assignment from streams, analytics sampling
- **HyperLogLog**: unique visitor counts, distinct query counting in databases, network traffic cardinality estimation
- **Bloom filter**: checking cache membership before a database query, spam detection, safe browsing lists (Chrome uses a Bloom filter to check URLs locally before querying Google's servers)
- **Count-Min Sketch**: tracking top-N most frequent items in a stream without storing all items
- **Skip lists**: probabilistic balanced search tree used in Redis sorted sets

## Production Example

An analytics service tracked unique user IDs per campaign per day. With 50 million users and 10,000 campaigns, storing exact sets was not feasible — 50M IDs × 8 bytes × 10,000 campaigns = 4TB just for one day's data.

HyperLogLog reduced this to under 12KB per (campaign, day) pair with ~1.5% error. At 10,000 campaigns, that is 120MB — three orders of magnitude smaller, with error that was well within the acceptable range for marketing analytics.

```go
type CampaignAnalytics struct {
    mu   sync.RWMutex
    hlls map[string]*HyperLogLog // key: "campaignID:date"
}

func NewCampaignAnalytics() *CampaignAnalytics {
    return &CampaignAnalytics{
        hlls: make(map[string]*HyperLogLog),
    }
}

func (ca *CampaignAnalytics) RecordVisit(campaignID, userID, date string) {
    key := campaignID + ":" + date
    ca.mu.Lock()
    if _, ok := ca.hlls[key]; !ok {
        ca.hlls[key] = &HyperLogLog{}
    }
    ca.hlls[key].Add(userID)
    ca.mu.Unlock()
}

func (ca *CampaignAnalytics) UniqueVisitors(campaignID, date string) uint64 {
    key := campaignID + ":" + date
    ca.mu.RLock()
    defer ca.mu.RUnlock()
    if hll, ok := ca.hlls[key]; ok {
        return hll.Count()
    }
    return 0
}
```

Distributed tracing used reservoir sampling to limit trace volume. Every service sampled at most 100 requests per second for full tracing, regardless of actual throughput:

```go
type TraceSampler struct {
    mu        sync.Mutex
    reservoir []*TraceSpan
    k         int
    seen      int
    rng       *rand.Rand
}

func NewTraceSampler(k int) *TraceSampler {
    return &TraceSampler{
        reservoir: make([]*TraceSpan, 0, k),
        k:         k,
        rng:       rand.New(rand.NewSource(time.Now().UnixNano())),
    }
}

func (ts *TraceSampler) Consider(span *TraceSpan) bool {
    ts.mu.Lock()
    defer ts.mu.Unlock()
    ts.seen++
    if len(ts.reservoir) < ts.k {
        ts.reservoir = append(ts.reservoir, span)
        return true
    }
    j := ts.rng.Intn(ts.seen)
    if j < ts.k {
        ts.reservoir[j] = span
        return true
    }
    return false
}
```

## The Tradeoffs

Probabilistic algorithms trade exactness for efficiency, with known error bounds. The critical discipline is knowing and accepting those bounds before using them. A Bloom filter with a 1% false positive rate means 1 in 100 non-members will incorrectly be treated as members. For cache membership (worst case: unnecessary DB query), this is fine. For security access control, it is not.

HyperLogLog cannot delete elements once added (you can work around this with Cuckoo filters, which support deletion). Bloom filters are also append-only in their standard form.

Reservoir sampling requires a truly random number generator. Using `math/rand` with a fixed seed produces the same "random" sequence on every run — not suitable for security-sensitive sampling or any case where predictable sampling would be a problem. Use `crypto/rand` or a CSPRNG-seeded `math/rand` source.

Finally, probabilistic data structures are difficult to test for correctness. Unit tests with fixed seeds verify that the algorithm runs without errors, but verifying the statistical properties requires statistical tests over many runs. When using these in production, also monitor the actual error rate against ground truth for a sample of queries to validate that the implementation is behaving as expected.

## Key Takeaway

Randomized algorithms are engineering tools, not compromises. Reservoir sampling gives you statistically uniform samples from streams without knowing their length. HyperLogLog counts distinct elements within 1.5% error using 12KB. Bloom filters eliminate unnecessary database lookups with configurable false positive rates. The skill is recognizing which problems have acceptable approximate answers, understanding the error bounds, and choosing the right probabilistic structure for the job.

---

← [Lesson 13: Cryptographic Primitives](/post/fundamentals/algo-crypto/) | [Course Index](/post/fundamentals/)

---

**Course complete.** You have covered the algorithms that actually show up in production systems — from Big-O thinking and sorting to cryptographic primitives and probabilistic data structures. The common thread across all 14 lessons: algorithms are not abstract puzzles. They are tools with specific performance characteristics, failure modes, and tradeoffs. Knowing which one to reach for, and when not to use one at all, is what separates engineers who build systems that hold up from those who rebuild the same system six months later.
