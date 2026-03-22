---
title: "Lesson 8: Design a URL Shortener — The Classic, Done Properly"
author: "Atharva Pandey"
series: "System Design from First Principles"
lesson: 8
tags:
  - fundamentals
  - system design
keywords:
  - URL shortener
  - system design interview
  - base62 encoding
  - distributed ID generation
  - Redis caching
  - system design
date: "2024-08-04T00:00:00.000Z"
---

The URL shortener is the "Hello World" of system design interviews. It appears deceptively simple: take a long URL, return a short one. But if you treat it superficially, you miss what the interviewer is actually testing: your ability to think through ID generation at scale, read-heavy caching, redirect semantics, analytics storage, and data modeling. Done well, the URL shortener problem touches nearly every fundamental we've covered so far.

## The Core Concept

A URL shortener has two primary operations:
1. **Shorten**: given a long URL, generate a short code and store the mapping
2. **Expand**: given a short code, look up the original URL and redirect the user

The short URL looks like `https://sho.rt/aB3xK7`. The path segment `aB3xK7` is the short code — typically 6–8 characters. Let's think about what that means at scale.

**Capacity estimation**

If we use Base62 encoding (a-z, A-Z, 0-9), a 7-character code gives us 62^7 ≈ 3.5 trillion unique combinations. For a service creating 100 million new URLs per day, that's enough unique codes for nearly 100 years. So 7 characters is a reasonable choice.

Read vs. write ratio: URL shorteners are extremely read-heavy. Once a URL is shortened, it might be clicked thousands or millions of times. A ratio of 100:1 reads to writes is conservative. At 100M URLs per day created and 10 billion redirects per day, we need to design primarily for read performance.

**301 vs 302 Redirects**

A detail that matters more than it seems:
- **301 Moved Permanently**: browsers cache this. The second time a user clicks the short link, the browser redirects directly to the destination without contacting your service. This reduces load but means you lose analytics for repeat visits.
- **302 Found (Temporary Redirect)**: browsers don't cache this. Every click goes through your service. More load, but you capture every click for analytics.

Which you choose depends on your product requirements. Most real URL shorteners use 302 so they can track click counts.

## How to Design It

**Component overview**

```
Client → [Load Balancer] → [API / Redirect Service]
                                    ↓
                          [Cache Layer: Redis]
                                    ↓ (miss)
                          [Database: PostgreSQL]
                                    ↓
                          [Analytics: Kafka → ClickHouse]
```

**ID Generation**

The short code is the critical piece. You have several options:

Option 1: **Random code + collision check**. Generate a random 7-char Base62 string. Check if it's in the database. If collision, regenerate. Works but DB round-trip per creation is expensive and collisions become frequent as the table fills.

Option 2: **Auto-increment ID + Base62 encode**. Use a database auto-increment primary key. Encode the integer to Base62.

```go
const base62Chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

func toBase62(n int64) string {
    if n == 0 {
        return string(base62Chars[0])
    }
    result := []byte{}
    for n > 0 {
        result = append([]byte{base62Chars[n%62]}, result...)
        n /= 62
    }
    return string(result)
}
// ID 1000000 → "4c92" (4 chars), ID 100000000000 → "fXSiqnV" (7 chars)
```

Problem: sequential IDs are predictable. Anyone can enumerate all URLs by incrementing the ID. For a public service, this may be fine (short URLs aren't secrets). For security-sensitive use, use random codes.

Option 3: **Distributed ID generator + Base62**. Use a Snowflake-style ID generator (covered in many designs): a 64-bit ID composed of timestamp + machine ID + sequence number. Globally unique, roughly sequential, no DB coordination needed. Encode to Base62.

Option 4: **Hash of long URL**. MD5/SHA256 of the long URL, take first 7 characters of Base62 encoding. Same long URL always produces the same short code (deduplication). Collision probability is low but not zero — requires a check.

**Database schema**

```sql
CREATE TABLE urls (
    id          BIGSERIAL PRIMARY KEY,
    short_code  VARCHAR(10) NOT NULL UNIQUE,
    long_url    TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    expires_at  TIMESTAMPTZ,
    user_id     BIGINT REFERENCES users(id),
    click_count BIGINT DEFAULT 0
);

CREATE INDEX idx_short_code ON urls(short_code);
```

**Caching strategy**

The redirect path is read-heavy. Every click should ideally be served from cache. Redis is ideal: store `short_code → long_url` with a TTL matching the URL's expiry (or a long TTL if the URL never expires).

```go
func Redirect(ctx context.Context, code string, rdb *redis.Client, db *sql.DB) (string, error) {
    // 1. Check cache
    longURL, err := rdb.Get(ctx, "url:"+code).Result()
    if err == nil {
        return longURL, nil
    }

    // 2. DB lookup
    err = db.QueryRowContext(ctx,
        "SELECT long_url FROM urls WHERE short_code = $1 AND (expires_at IS NULL OR expires_at > NOW())",
        code,
    ).Scan(&longURL)
    if err == sql.ErrNoRows {
        return "", ErrNotFound
    }
    if err != nil {
        return "", err
    }

    // 3. Populate cache
    rdb.Set(ctx, "url:"+code, longURL, 24*time.Hour)
    return longURL, nil
}
```

**Analytics**

Click counting in real-time to the DB would create a write bottleneck. Every redirect causing a write is 10 billion writes per day. Instead: publish click events to Kafka asynchronously. A consumer batch-updates click counts periodically (every minute). Or use a purpose-built analytics store like ClickHouse that handles high-volume time-series data.

```
Redirect Service → Kafka (click_events topic) → Analytics Consumer → ClickHouse
```

The redirect response is not blocked by analytics — it responds immediately after the Redis lookup.

## Real-World Example

Bitly processes billions of redirects per day. Their architecture uses multiple layers: an in-process LRU cache (hot codes), a Redis layer, and a backend database. They encode rich analytics: click location, device, referrer, time. Their URL expiry and custom domains feature require more complex routing logic at the redirect layer.

TinyURL (the original) is simpler — no analytics, no accounts, just a long-lived mapping. Its simplicity means it's extremely reliable and has been running for over 20 years.

Twitter's t.co shortener wraps every URL in tweets. It adds click tracking and malware scanning. Every shortened URL is resolved through Twitter's servers — this is why `t.co` links always show in URLs. It uses 302 redirects for complete click tracking.

## Interview Tips

Interviewers often push on these specific areas:

**Unique constraint on short_code**: always mention that `short_code` needs a unique index. Without it, two concurrent creation requests could generate the same code and you'd have a race condition.

**Custom short codes**: users want `https://sho.rt/my-brand`. This is just another code in the same table, written directly by the user. You need to check availability before accepting.

**URL expiry**: add an `expires_at` column. The redirect service checks it. A background job (cron or TTL-based in Redis) cleans up expired entries.

**Abuse prevention**: short URL services are abused to hide malicious links. Mention URL validation (check the destination isn't a known malicious domain), and rate limiting on creation (Lesson 7).

**Scale to 100B URLs**: at this scale, the database needs sharding. The shard key is the short code itself, or a hash of it. The in-memory cache size matters — Redis needs enough RAM to hold the working set of active URLs.

## Key Takeaway

The URL shortener tests your ability to combine multiple fundamentals: ID generation strategy, caching hierarchy for a heavily read workload, redirect semantics (301 vs 302), and decoupled analytics. The core insight is that reads massively outnumber writes — design for the redirect path first, and make sure it almost never touches the database. Async analytics via a message queue decouples the redirect latency from the write latency. Every design decision (Base62 length, cache TTL, redirect code) has a reasoning rooted in the usage patterns — always show that reasoning.

---

**Previous:** [Lesson 7: Rate Limiting](/post/fundamentals/sd-rate-limiting/)
**Next:** [Lesson 9: Design a Chat System — WebSocket, Presence, Message Ordering](/post/fundamentals/sd-chat-system/)
