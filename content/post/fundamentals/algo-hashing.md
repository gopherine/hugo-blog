---
title: "Lesson 5: Hashing and Consistent Hashing — How load balancers distribute traffic"
author: "Atharva Pandey"
keywords:
  - hashing
  - consistent hashing
  - load balancing
  - distributed systems
  - hash ring
  - production engineering
tags:
  - fundamentals
  - algorithms
series: "Algorithms That Matter in Production"
lesson: 5
date: "2024-06-17T00:00:00.000Z"
---

I did not think much about hashing until I had to explain why a cache cluster was unusable after we added two new nodes. We had doubled the cache hit rate over six months, added two boxes to handle the load, and immediately destroyed most of our cached data. Every key rehashed to a different server. Cache hit rate dropped from 85% to under 10%. We were hammering the database.

The problem was modulo hashing. The fix was consistent hashing. Understanding why this works requires understanding what hashing actually does.

## How It Works

A hash function maps arbitrary input to a fixed-size output — typically an integer or byte string. Good hash functions are deterministic (same input → same output), fast, and distribute outputs uniformly. They are not encryption; they are fingerprinting.

The basic pattern for hash-based routing:

```go
import (
    "hash/fnv"
)

// Simple modulo assignment — which server handles this key?
func serverIndex(key string, numServers int) int {
    h := fnv.New32a()
    h.Write([]byte(key))
    return int(h.Sum32()) % numServers
}
```

This works until `numServers` changes. If you go from 5 servers to 6, `key % 5` and `key % 6` almost never agree. Nearly every key maps to a different server. If those servers were caches, you just invalidated everything. If they were stateful services, you just routed sessions to the wrong servers.

**Consistent hashing** solves this by placing both servers and keys on a virtual ring (a circle with 2^32 or 2^64 positions). Each server occupies one or more positions on the ring. A key is assigned to the first server clockwise from its position. When you add or remove a server, only the keys that were assigned to that server need to move — roughly 1/N of all keys, where N is the number of servers.

```go
package consistenthash

import (
    "fmt"
    "hash/crc32"
    "sort"
    "sync"
)

type Ring struct {
    mu       sync.RWMutex
    replicas int              // virtual nodes per real node
    ring     map[uint32]string // hash -> server name
    sorted   []uint32          // sorted hash values for binary search
}

func New(replicas int) *Ring {
    return &Ring{
        replicas: replicas,
        ring:     make(map[uint32]string),
    }
}

func (r *Ring) hash(key string) uint32 {
    return crc32.ChecksumIEEE([]byte(key))
}

// Add adds a server to the ring with virtual nodes
func (r *Ring) Add(servers ...string) {
    r.mu.Lock()
    defer r.mu.Unlock()
    for _, server := range servers {
        for i := 0; i < r.replicas; i++ {
            virtualKey := fmt.Sprintf("%s#%d", server, i)
            h := r.hash(virtualKey)
            r.ring[h] = server
            r.sorted = append(r.sorted, h)
        }
    }
    sort.Slice(r.sorted, func(i, j int) bool {
        return r.sorted[i] < r.sorted[j]
    })
}

// Get returns the server responsible for a key
func (r *Ring) Get(key string) string {
    r.mu.RLock()
    defer r.mu.RUnlock()
    if len(r.ring) == 0 {
        return ""
    }
    h := r.hash(key)
    // Find the first virtual node at or after h on the ring
    idx := sort.Search(len(r.sorted), func(i int) bool {
        return r.sorted[i] >= h
    })
    // Wrap around: if past the last node, use the first
    if idx == len(r.sorted) {
        idx = 0
    }
    return r.ring[r.sorted[idx]]
}
```

The virtual nodes (replicas parameter) are critical. Without them, the ring is uneven — some real servers end up with far more keys than others. Adding 150–200 virtual nodes per real server gives good distribution. This is what Cassandra, Riak, and most distributed caches use.

## When You Need It

Consistent hashing matters any time you have a distributed cluster of stateful or cache nodes and you need to:

- Route requests to the same server for the same key (session affinity, cache locality)
- Add or remove nodes without restarting the world
- Avoid thundering herd after node changes

Simple use cases where it does not matter: stateless compute behind a load balancer where any server can handle any request. Round-robin or least-connections is fine there.

```go
// Demonstrating the difference in key remapping

func demonstrateModuloInstability() {
    keys := []string{"user:1001", "user:2002", "session:abc", "cart:xyz"}

    // Before: 3 servers
    fmt.Println("Before adding server:")
    for _, k := range keys {
        fmt.Printf("  %s -> server %d\n", k, serverIndex(k, 3))
    }

    // After: 4 servers — nearly everything moved
    fmt.Println("After adding server:")
    for _, k := range keys {
        fmt.Printf("  %s -> server %d\n", k, serverIndex(k, 4))
    }
}

func serverIndex(key string, n int) int {
    h := fnv.New32a()
    h.Write([]byte(key))
    return int(h.Sum32()) % n
}
```

Run this and you will see most keys point to a different server after adding one node. With consistent hashing, only ~25% of keys would move when going from 3 to 4 servers.

## Production Example

A caching layer in front of a recommendation engine stored precomputed personalization vectors keyed by user ID. The cache held ~40 million entries and was spread across 8 Redis nodes using consistent hashing.

During a planned maintenance window, we removed one node. With modulo hashing, this would have evicted 87.5% of cached vectors (7/8 of keys would move). With consistent hashing, only the keys that had been assigned to that node moved — approximately 12.5%. The other 87.5% remained on their original nodes and continued serving cache hits.

The routing layer looked like this:

```go
type RecommendationCache struct {
    ring    *Ring
    clients map[string]*redis.Client
}

func NewRecommendationCache(nodes []string) *RecommendationCache {
    ring := New(150) // 150 virtual nodes per server
    ring.Add(nodes...)
    clients := make(map[string]*redis.Client, len(nodes))
    for _, node := range nodes {
        clients[node] = redis.NewClient(&redis.Options{Addr: node})
    }
    return &RecommendationCache{ring: ring, clients: clients}
}

func (c *RecommendationCache) Get(ctx context.Context, userID string) ([]float32, error) {
    server := c.ring.Get(userID)
    client := c.clients[server]
    val, err := client.Get(ctx, "reco:"+userID).Bytes()
    if err != nil {
        return nil, err
    }
    return deserializeVector(val), nil
}

func (c *RecommendationCache) Set(ctx context.Context, userID string, vec []float32) error {
    server := c.ring.Get(userID)
    client := c.clients[server]
    return client.Set(ctx, "reco:"+userID, serializeVector(vec), 24*time.Hour).Err()
}
```

The same user always hits the same Redis node as long as the ring composition does not change. When it does change, only the affected fraction of users get a cache miss.

## The Tradeoffs

Virtual nodes solve distribution imbalance but add memory overhead. A ring with 10 servers and 200 virtual nodes per server has 2,000 entries to search. This is still tiny (binary search over 2,000 entries is about 11 comparisons), but it is a consideration for very large clusters.

Consistent hashing does not handle hotspots. If one key is requested 10x more than others (e.g., a celebrity user), the server assigned to that key gets 10x the load. Solutions include replication (store hot keys on multiple servers) or application-level caching in front of the distributed cache.

Also, the hashing function choice matters. CRC32 is fast and fine for routing. For security-sensitive scenarios (preventing adversarial key patterns from causing hotspots), use a cryptographic hash or add a random salt.

## Key Takeaway

Consistent hashing solves a specific problem: how to distribute keys across a changing set of servers while minimizing the fraction of keys that need to move when the set changes. It powers distributed caches, database sharding, and load balancers. If you are building anything that distributes work by key across a dynamic pool of nodes, consistent hashing is the right primitive to understand.

---

← [Lesson 4: Two Pointers and Sliding Window](/post/fundamentals/algo-two-pointers/) | [Course Index](/post/fundamentals/) | Next: [Lesson 6: BFS and DFS](/post/fundamentals/algo-bfs-dfs/) →
