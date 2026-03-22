---
title: "Lesson 7: Rate Limiting — Token Bucket, Sliding Window, Distributed"
author: "Atharva Pandey"
series: "System Design from First Principles"
lesson: 7
tags:
  - fundamentals
  - system design
keywords:
  - rate limiting
  - token bucket
  - sliding window
  - fixed window
  - leaky bucket
  - distributed rate limiting
  - system design
date: "2024-07-21T00:00:00.000Z"
---

Without rate limiting, a single misbehaving client can consume all your resources and deny service to everyone else. A bug in a client that retries in a tight loop. A competitor scraping your API. A DDoS attack. A feature that accidentally calls your endpoint ten times per button click. Rate limiting is the mechanism that prevents any of these from taking down your service — it's the first line of defense between the internet and your origin.

## The Core Concept

Rate limiting constrains how many requests a client (identified by API key, IP address, user ID, or some combination) can make in a given time window. Requests within the limit are processed normally. Requests that exceed the limit receive a `429 Too Many Requests` response.

The challenge is in the algorithm. Different algorithms offer different trade-offs around burst behavior, memory usage, and accuracy.

**Fixed Window Counter**

The simplest approach: divide time into fixed windows (e.g., each minute). Count requests per window per client. If the count exceeds the limit, reject.

```
Window 1 (12:00:00–12:00:59): user_A made 95 requests, limit is 100 → OK
Window 2 (12:01:00–12:01:59): user_A made 95 requests → OK
```

The problem: a client can make 100 requests at 12:00:59 and another 100 at 12:01:00. Within one second, they've made 200 requests, but neither window registers a violation. This "window boundary burst" can be double the intended limit.

**Sliding Window Log**

Track a log of timestamps for each request. On a new request, remove timestamps older than one window ago, then count remaining entries. If below limit, allow and add the new timestamp.

Accurate, but memory-intensive: you store a timestamp per request per client. At high volume, this doesn't scale.

**Sliding Window Counter (approximate)**

A practical compromise. Keep the current window count and the previous window count. Estimate the count over the sliding window using a weighted average:

```
estimated_count = prev_count × (1 - elapsed_fraction) + curr_count
```

If the window is 60 seconds and 30 seconds have elapsed in the current window:
```
estimated_count = prev_count × 0.5 + curr_count
```

This is an approximation but it's memory-efficient and handles the boundary burst problem well.

**Token Bucket**

Each client gets a bucket with a maximum capacity of N tokens. Tokens are added at a rate of R per second (up to the maximum). Each request consumes one token. If the bucket is empty, the request is rejected.

```go
type TokenBucket struct {
    capacity   float64
    tokens     float64
    rate       float64 // tokens per second
    lastRefill time.Time
    mu         sync.Mutex
}

func (tb *TokenBucket) Allow() bool {
    tb.mu.Lock()
    defer tb.mu.Unlock()

    now := time.Now()
    elapsed := now.Sub(tb.lastRefill).Seconds()
    tb.tokens = min(tb.capacity, tb.tokens+elapsed*tb.rate)
    tb.lastRefill = now

    if tb.tokens >= 1 {
        tb.tokens--
        return true
    }
    return false
}
```

Token bucket allows bursts up to the bucket capacity. If a client has been idle, they accumulate tokens and can burst. If they're continuously active, they're limited to the average rate. This models real-world usage well — occasional burst users get a better experience than strictly rate-limited users.

**Leaky Bucket**

Requests go into a queue (the bucket). The queue drains at a fixed rate. If the queue is full, excess requests are dropped. This smooths out bursts — the output is always at a steady rate, regardless of input spikes. Used for traffic shaping (outgoing request throttling) more than inbound rate limiting.

## How to Design It

**In-process vs. Distributed**

A single-process rate limiter (like the token bucket above) works fine for a single server. But if you have a fleet of 50 API servers, each with its own in-process rate limiter, a client can make 100 requests per minute per server — 5,000 total. The rate limit is meaningless.

Distributed rate limiting uses a shared store (Redis) to maintain counters across all instances:

```go
func AllowRequest(ctx context.Context, rdb *redis.Client, clientID string, limit int) (bool, error) {
    key := fmt.Sprintf("rate:%s:%d", clientID, time.Now().Unix()/60) // per-minute bucket

    count, err := rdb.Incr(ctx, key).Result()
    if err != nil {
        return false, err
    }

    if count == 1 {
        // First request in this window — set TTL
        rdb.Expire(ctx, key, 2*time.Minute)
    }

    return count <= int64(limit), nil
}
```

Redis's atomic `INCR` ensures no race conditions. The key includes the current minute, so the counter resets naturally. This implements fixed window, which is fine for most use cases.

For sliding window in Redis, use a sorted set with timestamps as scores:

```go
func AllowSlidingWindow(ctx context.Context, rdb *redis.Client, clientID string, limit int, window time.Duration) (bool, error) {
    now := time.Now()
    windowStart := now.Add(-window).UnixMilli()
    key := "rate:" + clientID

    pipe := rdb.TxPipeline()
    pipe.ZRemRangeByScore(ctx, key, "0", fmt.Sprint(windowStart)) // remove old
    pipe.ZAdd(ctx, key, redis.Z{Score: float64(now.UnixMilli()), Member: now.UnixNano()})
    pipe.ZCard(ctx, key)
    pipe.Expire(ctx, key, window+time.Second)

    cmds, _ := pipe.Exec(ctx)
    count := cmds[2].(*redis.IntCmd).Val()
    return count <= int64(limit), nil
}
```

**What to rate limit on**

- **IP address**: blunt but effective against unsophisticated attacks. Problematic behind NAT (corporate networks share one IP).
- **API key / OAuth token**: best for authenticated APIs. Ties to a specific client/account.
- **User ID**: for authenticated endpoints.
- **Combination**: global IP limit as a circuit breaker + per-API-key limit for quotas.

**Returning useful information**

A 429 with no explanation is frustrating. Add headers that tell the client how to back off:

```
HTTP/1.1 429 Too Many Requests
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1720912800
Retry-After: 42
```

## Real-World Example

Stripe's API rate limiting is widely cited as a model. They use a token bucket per API key, with different limits per endpoint (the charge endpoint has stricter limits than the list endpoint). They return standard headers so clients can implement proper backoff. Burst capacity lets a batch operation work without artificially slowing down. And they distinguish between test-mode keys and live-mode keys.

GitHub's REST API uses a fixed window: 5,000 requests per hour per authenticated user. GraphQL is metered by "points" per query (complex queries cost more points), which is a more sophisticated model that accounts for the varying cost of operations.

## Interview Tips

The canonical rate limiting question: "Design a rate limiter for an API that handles 10M requests per day across 1,000 servers." The key insight is that you need centralized state (Redis) to have meaningful rate limits across a fleet.

"What happens if Redis goes down?" This is the reliability vs. correctness trade-off. Options: fail open (allow all requests — risky during an attack), fail closed (reject all requests — causes downtime), or fall back to per-instance limiting (less accurate but keeps the service up). Most production systems choose fail open with alerts.

"How do you handle the boundary burst problem?" Mention sliding window counter as the practical fix.

"What about rate limiting at the API gateway?" Many teams implement rate limiting in a gateway (Kong, AWS API Gateway) rather than in application code. This centralizes policy and offloads the logic from services. It's a valid architectural choice — mention it as an alternative.

## Key Takeaway

Rate limiting protects your service from overload, abuse, and buggy clients. Fixed window counters are simple but allow double-the-limit bursts at window boundaries. Token bucket is the most practical: it allows natural burst behavior while enforcing average limits. Distributed rate limiting uses Redis atomic operations to maintain shared state across a fleet of servers. Return helpful headers so clients can implement graceful backoff. The algorithm choice matters less than having a working distributed implementation — and knowing the failure modes of whatever you choose.

---

**Previous:** [Lesson 6: CDNs](/post/fundamentals/sd-cdns/)
**Next:** [Lesson 8: Design a URL Shortener — The Classic, Done Properly](/post/fundamentals/sd-url-shortener/)
