---
title: "Lesson 3: Caching — The Hardest Easy Problem in CS"
author: "Atharva Pandey"
series: "System Design from First Principles"
lesson: 3
tags:
  - fundamentals
  - system design
keywords:
  - caching
  - cache invalidation
  - Redis
  - eviction policy
  - write-through
  - cache-aside
  - system design
date: "2024-05-20T00:00:00.000Z"
---

Phil Karlton supposedly said: "There are only two hard things in Computer Science: cache invalidation and naming things." He was joking, but also completely serious. Caching is the answer to nearly every "make it faster" problem in system design. It's also the source of some of the most insidious bugs in production: stale data served with confidence, cache stampedes that take down your database, memory explosions from an unbounded cache. The concept is simple. Getting it right is not.

## The Core Concept

A cache is a faster, smaller storage layer that sits in front of a slower, larger one. When a request comes in, you check the cache first. If the data is there (a cache hit), you return it immediately without touching the underlying store. If it's not there (a cache miss), you fetch from the underlying store, optionally store it in the cache, and return it.

The fundamental trade-off: caches are fast because they're small. They're small because they're expensive (RAM vs. disk). Since they're small, they can't hold everything, which means you have to decide what to store and what to evict when you're full.

**Why caching works: temporal and spatial locality**

Most reads in real systems aren't uniformly distributed. A small percentage of data accounts for a large percentage of reads — this is the 80/20 rule applied to data access. A Twitter profile for Elon Musk is read millions of times. A profile for someone who signed up in 2012 and never tweeted gets read almost never. A cache that holds the top 1% of accessed items can serve the majority of traffic without touching the database.

**Cache eviction policies**

When the cache is full and a new item needs to be stored, something has to be evicted. The common policies:

- **LRU (Least Recently Used)**: evict the item that was accessed least recently. Works well for most general-purpose caches. The intuition: if you haven't used something recently, you probably won't need it soon.
- **LFU (Least Frequently Used)**: evict the item that has been accessed least often overall. Better for cases where popularity is stable over time. Worse for bursty access patterns.
- **FIFO (First In First Out)**: evict the oldest item. Simple but doesn't account for access patterns.
- **TTL (Time To Live)**: each item has an expiry time. Not an eviction policy per se, but a freshness policy — items are removed when they expire, regardless of whether the cache is full.

Redis supports LRU, LFU, and TTL. Memcached uses LRU only. In practice, LRU with TTL covers most use cases.

## How to Design It

**Cache-aside (lazy loading)**

The application code is responsible for the cache. On a read, check the cache. On a miss, fetch from DB and populate the cache.

```go
func GetUser(ctx context.Context, cache *redis.Client, db *sql.DB, userID string) (*User, error) {
    // 1. Check cache
    val, err := cache.Get(ctx, "user:"+userID).Result()
    if err == nil {
        var user User
        json.Unmarshal([]byte(val), &user)
        return &user, nil
    }

    // 2. Cache miss — fetch from DB
    user, err := fetchUserFromDB(ctx, db, userID)
    if err != nil {
        return nil, err
    }

    // 3. Populate cache with TTL
    data, _ := json.Marshal(user)
    cache.Set(ctx, "user:"+userID, data, 5*time.Minute)

    return user, nil
}
```

On a write, invalidate the cache key (or update it). The simplest strategy is to just delete the key — the next read will repopulate it.

Cache-aside is the most common pattern. Its weakness: on startup or after a cache flush, there's a burst of cache misses that hammer the DB (the "cold start" problem).

**Write-through**

On every write, update both the DB and the cache synchronously. The cache is always warm. The cost: every write is slower (two writes). Also, you may cache data that's never read — wasted memory.

**Write-behind (write-back)**

Write to the cache only, and asynchronously flush to the DB later. This is fast but risky: if the cache crashes before the flush, you lose writes. Used only when write performance is critical and some data loss is acceptable (or you have a durable write-ahead log).

**The cache stampede problem**

Suppose 10,000 users are all reading the same key that just expired. They all miss the cache simultaneously. They all go to the DB. The DB gets 10,000 queries at once. This is a cache stampede (also called thundering herd).

Solutions:
- **Probabilistic early expiration**: slightly before a key expires, randomly let one request repopulate it while others still get the cached value. The "XFetch" algorithm implements this.
- **Locking**: when a key misses, acquire a distributed lock, fetch from DB, set cache, release lock. Other goroutines wait or serve a slightly stale value.
- **Background refresh**: refresh keys before they expire, in the background, without blocking reads.

**What should you cache?**

Cache things that are: expensive to compute or fetch, read more often than written, and tolerate some staleness. User profiles, product listings, aggregate counts, rendered HTML fragments — these are good candidates. Never cache: per-user personalized data that changes per-request, financial balances (must be authoritative), anything that requires real-time accuracy.

## Real-World Example

Consider a social media feed. Each user's feed is an aggregated, sorted list of posts from people they follow. Computing it on every request requires reading follow relationships, fetching posts, sorting and ranking them — expensive. But the feed doesn't change every millisecond.

The approach: cache the feed per user with a short TTL (30–60 seconds). The user sees data that's at most a minute old. When a new post arrives, you can either wait for TTL expiry (acceptable for feeds, not for DMs) or proactively invalidate/update the cache entries for all followers (expensive if a user has millions of followers — more on this in Lesson 10 on News Feeds).

Another example: GitHub caches repository metadata (star counts, fork counts) in Redis with a TTL. The count you see might be slightly stale, but computing the real count on every page load would require expensive DB aggregations across millions of rows.

## Interview Tips

When you propose caching in an interview, the follow-up will almost always be: "How do you handle cache invalidation?" Have an answer ready.

The safest answer for most cases: short TTL + cache-aside + delete-on-write. For simple key-value caches like user profiles, this is almost always sufficient. The TTL bounds staleness, and deleting on write ensures that writes are visible within one TTL window.

Be careful about saying "I'll cache everything." It invites questions like: what's your eviction policy? How large is your cache? What's the cost of a cold start? What's your hit rate target?

A 90% cache hit rate means 10% of requests go to the DB. If your DB can handle 1,000 req/s and your total traffic is 10,000 req/s, a 90% hit rate is fine. A 95% hit rate doubles your effective DB headroom. That last 5% is often disproportionately hard to cache (long-tail queries, personalized results).

Always think about: what's the worst case when the cache is empty? If your DB can't handle full traffic without the cache, you have a hidden dependency. Design accordingly.

## Key Takeaway

Caching works because data access is not uniform — a small amount of data accounts for most reads. The three core patterns are cache-aside (read through on miss, delete on write), write-through (synchronous write to both), and write-behind (async flush). LRU with TTL covers most use cases. The hard parts are: preventing stampedes on popular expiring keys, deciding what to cache without ballooning memory, and ensuring that writes don't serve stale data past your acceptable window. Cache invalidation is genuinely hard — but thinking through the edge cases is what separates a superficial answer from a real design.

---

**Previous:** [Lesson 2: Load Balancing](/post/fundamentals/sd-load-balancing/)
**Next:** [Lesson 4: Database Scaling — Read Replicas, Sharding, and When Each Helps](/post/fundamentals/sd-database-scaling/)
