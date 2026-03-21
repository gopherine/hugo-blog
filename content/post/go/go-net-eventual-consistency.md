---
title: 'Lesson 6: Eventual Consistency — Your data will be wrong, temporarily'
author: Atharva Pandey
series: "Go Networking & Distributed Systems"
lesson: 6
keywords:
  - Go
  - golang
  - eventual consistency
  - distributed systems
  - CAP theorem
  - cache invalidation
  - read-your-writes
  - consistency
tags:
  - Go tutorial
  - golang
  - networking
date: '2025-01-25T00:00:00.000Z'
---

I used to believe that eventual consistency was an exotic property of distributed databases that I'd only encounter at Google scale. Then I added a Redis cache to a simple CRUD service and immediately had a bug where users couldn't see their own updates. Welcome to eventual consistency — it shows up the moment you have two places that store the same data.

Eventual consistency means that if you stop writing to a system, all replicas will eventually converge to the same value. The word "eventually" can mean milliseconds or minutes, depending on the system. The hard part isn't the definition — it's designing application code that works correctly even when replicas haven't converged yet.

## The Problem

The classic cache inconsistency looks like this:

```go
// WRONG — classic cache-aside pattern with a race condition
func getUser(ctx context.Context, id string) (*User, error) {
    // Check cache first
    cached, err := redis.Get(ctx, "user:"+id).Result()
    if err == nil {
        var u User
        json.Unmarshal([]byte(cached), &u)
        return &u, nil
    }

    // Cache miss — read from database
    u, err := db.GetUser(ctx, id)
    if err != nil {
        return nil, err
    }

    // Store in cache
    data, _ := json.Marshal(u)
    redis.Set(ctx, "user:"+id, data, 5*time.Minute)
    return u, nil
}

func updateUser(ctx context.Context, u *User) error {
    // Update database
    if err := db.UpdateUser(ctx, u); err != nil {
        return err
    }
    // Delete cache entry
    redis.Del(ctx, "user:"+u.ID)
    return nil
}
```

This pattern — cache-aside with delete-on-write — looks correct. It mostly is. But there's a race: if `getUser` and `updateUser` run concurrently, the sequence can be: (1) getUser reads from DB, (2) updateUser writes DB and deletes cache, (3) getUser writes the stale value into cache. The cache now has the old value with a 5-minute TTL. Every read for the next five minutes returns stale data.

The deeper problem is that the application promises users read-your-writes consistency — you write an update and immediately see it — but the implementation doesn't guarantee that. The update writes to the primary database; the read might go to a replica that hasn't caught up, or to a cache that holds the pre-update value.

## The Idiomatic Way

For read-your-writes consistency, the key insight is tracking which user made which write and routing their subsequent reads appropriately:

```go
// version token encodes the logical time of a write
type WriteToken struct {
    UserID    string
    WrittenAt time.Time
}

// After a write, return a token to the client
func updateUser(ctx context.Context, u *User) (*WriteToken, error) {
    if err := db.UpdateUser(ctx, u); err != nil {
        return nil, err
    }
    // Invalidate cache
    redis.Del(ctx, "user:"+u.ID)
    return &WriteToken{UserID: u.ID, WrittenAt: time.Now()}, nil
}

// On reads, check if we need fresh data
func getUser(ctx context.Context, id string, token *WriteToken) (*User, error) {
    // If this user recently wrote, bypass cache to guarantee freshness.
    if token != nil && token.UserID == id && time.Since(token.WrittenAt) < 10*time.Second {
        return db.GetUser(ctx, id) // go directly to primary
    }

    // Normal cache-aside for other users' data
    cached, err := redis.Get(ctx, "user:"+id).Result()
    if err == nil {
        var u User
        if jsonErr := json.Unmarshal([]byte(cached), &u); jsonErr == nil {
            return &u, nil
        }
    }

    u, err := db.GetUser(ctx, id)
    if err != nil {
        return nil, err
    }
    data, _ := json.Marshal(u)
    redis.Set(ctx, "user:"+id, data, 5*time.Minute)
    return u, nil
}
```

The write token can be stored in the user's session. For 10 seconds after a write, their reads go to the primary database directly. After that window, they drop back to the cached path. This is a simple, pragmatic approach to read-your-writes without adding synchronization overhead to every request.

A more principled solution for cache invalidation is the write-through pattern combined with versioned cache keys:

```go
// Write-through: update cache atomically with the database write
func updateUserWithVersion(ctx context.Context, u *User) error {
    // Increment version in a transaction
    version, err := db.UpdateUserReturnVersion(ctx, u)
    if err != nil {
        return err
    }

    // Write to cache with the new version embedded in the key.
    // Old version keys expire naturally.
    cacheKey := fmt.Sprintf("user:%s:v%d", u.ID, version)
    data, _ := json.Marshal(u)
    if err := redis.Set(ctx, cacheKey, data, 10*time.Minute).Err(); err != nil {
        // Cache write failure is acceptable — we'll fall through to DB
        log.Printf("cache write failed: %v", err)
    }

    // Store the current version number under a stable key
    redis.Set(ctx, "user:"+u.ID+":version", version, 10*time.Minute)
    return nil
}

func getUserWithVersion(ctx context.Context, id string) (*User, error) {
    // Look up current version
    version, err := redis.Get(ctx, "user:"+id+":version").Int64()
    if err == nil {
        // Try versioned cache key
        cacheKey := fmt.Sprintf("user:%s:v%d", id, version)
        if cached, err := redis.Get(ctx, cacheKey).Result(); err == nil {
            var u User
            if json.Unmarshal([]byte(cached), &u) == nil {
                return &u, nil
            }
        }
    }

    // Fall through to database
    u, err := db.GetUser(ctx, id)
    if err != nil {
        return nil, err
    }
    return u, nil
}
```

## In The Wild

One of the most useful tools for managing eventual consistency is making the inconsistency visible in your APIs instead of hiding it. Return a version or `last_modified` timestamp on read responses:

```go
type UserResponse struct {
    User      User      `json:"user"`
    Version   int64     `json:"version"`
    FetchedAt time.Time `json:"fetched_at"`
}
```

Clients that care about consistency can use optimistic concurrency: send the version they read back on their update, and the server rejects the update if the version has changed in the interim:

```go
func handleUpdateUser(w http.ResponseWriter, r *http.Request) {
    var req struct {
        User    User  `json:"user"`
        Version int64 `json:"version"`
    }
    json.NewDecoder(r.Body).Decode(&req)

    updated, err := db.UpdateUserIfVersion(r.Context(), req.User, req.Version)
    if err != nil {
        http.Error(w, "conflict", http.StatusConflict) // 409
        return
    }
    json.NewEncoder(w).Encode(updated)
}
```

This pattern — optimistic concurrency control — sidesteps many consistency bugs by failing loudly instead of silently overwriting concurrent changes.

## The Gotchas

**Stale-while-revalidate confusion.** HTTP caching has a `stale-while-revalidate` directive that serves stale content and refreshes in the background. This is eventually consistent by design. Users sometimes report "I updated my profile and it still shows the old photo." The photo is coming from the CDN, which hasn't invalidated yet. Cache invalidation at the CDN layer is a separate problem from database consistency.

**Idempotency is your friend.** Eventual consistency frequently involves retrying operations. If retrying an operation twice produces different outcomes — like creating two orders — you have a correctness problem. Design all state-changing operations to be idempotent: processing the same event twice should have the same effect as processing it once. Use deduplication keys in your database.

**The inconsistency window matters.** "Eventual" consistency where replicas lag by 100ms is very different from replicas that lag by 30 seconds. Know your replication lag — expose it as a metric — and set your cache TTLs and write-token windows based on measured lag, not intuition.

## Key Takeaway

Eventual consistency isn't a failure mode to work around — it's a tradeoff you make deliberately. The moment you add a cache, a read replica, or an async message queue, you have eventual consistency. Accept this and design for it: use read-your-writes tokens for user-facing writes, use optimistic concurrency for conflict detection, and expose version metadata in your APIs so clients can reason about staleness.

---

**Previous:** [Lesson 5: Message Queues in Go](/post/go/go-net-message-queues/)
**Next:** [Lesson 7: Outbox Pattern — Reliable events without distributed transactions](/post/go/go-net-outbox/)
