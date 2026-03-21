---
title: "Lesson 8: Rate Limiting — Say no before you break"
author: Atharva Pandey
series: "Go API and Service Design"
lesson: 8
keywords: [Go, golang, API, backend, rate limiting, token bucket, sliding window, throttling]
tags: [Go tutorial, golang, backend]
date: '2025-03-15T00:00:00.000Z'
---

A service I was running had no rate limiting. One night, a script that a client was testing started sending requests in a tight loop — about 4,000 requests per second. The database connection pool saturated within seconds. Other clients started seeing timeouts. By the time I woke up and deployed a fix, we had been degraded for forty minutes. A single `429 Too Many Requests` response would have stopped the script cold in seconds.

Rate limiting is the difference between a polite rejection and a cascading failure.

## The Problem

Without rate limiting, your service is only as stable as your most aggressive client. A single misbehaving caller — a buggy retry loop, a DDoS attempt, a misconfigured load test — can exhaust your resources and degrade the experience for every other caller. The problem is not that you want to be mean to clients. The problem is that you cannot serve anyone at all if you are serving one client infinitely.

Rate limiting is also about fairness. A freemium API where paying customers share infrastructure with free-tier users needs limits to ensure that heavy free-tier usage does not degrade the paid experience.

## The Idiomatic Way

The token bucket algorithm is the right choice for most HTTP APIs. Tokens accumulate at a fixed rate up to a maximum capacity. Each request consumes one token. Requests that arrive when the bucket is empty are rejected. This allows short bursts (the bucket can be full) while enforcing a long-term average rate.

Go's `golang.org/x/time/rate` package provides a production-quality token bucket implementation:

```go
package ratelimit

import (
    "context"
    "net/http"
    "sync"
    "time"

    "golang.org/x/time/rate"
)

// PerClientLimiter maintains a separate rate limiter per client identifier
type PerClientLimiter struct {
    mu      sync.Mutex
    clients map[string]*entry
    rate    rate.Limit // tokens per second
    burst   int        // maximum burst size
    ttl     time.Duration
}

type entry struct {
    limiter  *rate.Limiter
    lastSeen time.Time
}

func NewPerClientLimiter(r rate.Limit, burst int, ttl time.Duration) *PerClientLimiter {
    pcl := &PerClientLimiter{
        clients: make(map[string]*entry),
        rate:    r,
        burst:   burst,
        ttl:     ttl,
    }
    go pcl.cleanup()
    return pcl
}

func (pcl *PerClientLimiter) getLimiter(clientID string) *rate.Limiter {
    pcl.mu.Lock()
    defer pcl.mu.Unlock()

    e, ok := pcl.clients[clientID]
    if !ok {
        l := rate.NewLimiter(pcl.rate, pcl.burst)
        pcl.clients[clientID] = &entry{limiter: l, lastSeen: time.Now()}
        return l
    }
    e.lastSeen = time.Now()
    return e.limiter
}

// cleanup periodically removes limiters for clients not seen recently
func (pcl *PerClientLimiter) cleanup() {
    ticker := time.NewTicker(pcl.ttl)
    defer ticker.Stop()
    for range ticker.C {
        pcl.mu.Lock()
        for id, e := range pcl.clients {
            if time.Since(e.lastSeen) > pcl.ttl {
                delete(pcl.clients, id)
            }
        }
        pcl.mu.Unlock()
    }
}

// Middleware returns an http.Handler middleware that rate-limits by clientID.
// clientID is extracted by the provided function (e.g. from API key or IP).
func (pcl *PerClientLimiter) Middleware(clientID func(*http.Request) string) func(http.Handler) http.Handler {
    return func(next http.Handler) http.Handler {
        return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
            id := clientID(r)
            limiter := pcl.getLimiter(id)

            if !limiter.Allow() {
                w.Header().Set("Retry-After", "1")
                w.Header().Set("X-RateLimit-Limit", fmt.Sprintf("%d", pcl.burst))
                http.Error(w,
                    `{"error":"rate_limit_exceeded","message":"Too many requests. Please slow down."}`,
                    http.StatusTooManyRequests,
                )
                return
            }

            next.ServeHTTP(w, r)
        })
    }
}
```

Wire it up in your server initialisation:

```go
func NewServer() *Server {
    s := &Server{}

    // 10 requests per second, burst of 30
    limiter := ratelimit.NewPerClientLimiter(10, 30, 5*time.Minute)

    clientIDFn := func(r *http.Request) string {
        // Prefer API key, fall back to IP
        if key := r.Header.Get("X-API-Key"); key != "" {
            return "key:" + key
        }
        return "ip:" + realIP(r)
    }

    s.mux.Handle("/api/", limiter.Middleware(clientIDFn)(s.apiHandler()))
    return s
}

// realIP extracts the real client IP, respecting X-Forwarded-For from trusted proxies
func realIP(r *http.Request) string {
    if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
        // Take the leftmost (original client) IP
        parts := strings.Split(xff, ",")
        return strings.TrimSpace(parts[0])
    }
    host, _, _ := net.SplitHostPort(r.RemoteAddr)
    return host
}
```

## In The Wild

Production rate limiting often needs multiple tiers — a per-second burst limit and a per-hour sustained limit. Here is how to compose two limiters for tiered control:

```go
type TieredLimiter struct {
    burst    *rate.Limiter // short-term burst: 30 req/s, burst 50
    sustained *rate.Limiter // long-term: 1000 req/hour = ~0.278 req/s, burst 1000
}

func NewTieredLimiter() *TieredLimiter {
    return &TieredLimiter{
        burst:     rate.NewLimiter(30, 50),
        sustained: rate.NewLimiter(rate.Every(time.Hour/1000), 1000),
    }
}

func (t *TieredLimiter) Allow() bool {
    // Both limiters must allow the request
    return t.burst.Allow() && t.sustained.Allow()
}
```

The sustained limiter uses `rate.Every(time.Hour/1000)` which computes to approximately 0.000278 tokens per millisecond — equivalent to 1,000 requests per hour. Consuming from both limiters simultaneously means a client hitting the burst limit is still protected from draining their hourly budget too fast.

When you reject a request, include informative headers so well-behaved clients can adapt:

```go
func writeRateLimitError(w http.ResponseWriter, limiter *rate.Limiter) {
    reservation := limiter.ReserveN(time.Now(), 0)
    retryAfter := reservation.Delay().Seconds()

    w.Header().Set("X-RateLimit-Limit", "30")
    w.Header().Set("X-RateLimit-Remaining", "0")
    w.Header().Set("X-RateLimit-Reset", fmt.Sprintf("%d",
        time.Now().Add(time.Duration(retryAfter*float64(time.Second))).Unix()))
    w.Header().Set("Retry-After", fmt.Sprintf("%.0f", retryAfter))
    w.Header().Set("Content-Type", "application/json")
    w.WriteHeader(http.StatusTooManyRequests)
    fmt.Fprintf(w, `{"error":"rate_limit_exceeded","retry_after_seconds":%.1f}`, retryAfter)
}
```

## The Gotchas

**In-process rate limiting does not work across multiple instances.** A token bucket stored in memory is per-process. If you have five instances behind a load balancer, each has its own bucket. A client can effectively get five times your intended rate limit by round-robining across instances. For multi-instance rate limiting, use a shared store — Redis with a sliding window counter or a rate-limit sidecar like Envoy's global rate limiting service.

**Never trust `X-Forwarded-For` without validating the proxy.** If your service is directly exposed to the internet and someone sends `X-Forwarded-For: 1.2.3.4`, you will rate-limit the wrong IP. Only parse `X-Forwarded-For` if you know the request came from a trusted proxy (your load balancer's known IP range). Otherwise, use `r.RemoteAddr`.

**Rate limit by user identity, not just by IP.** Shared NAT environments — corporate offices, mobile carriers — can have thousands of users behind a single IP address. Rate limiting by IP in these environments unfairly penalises legitimate users. If your API supports authentication, rate limit by authenticated user ID or API key first, and use IP as a secondary fallback only for unauthenticated endpoints.

**`Allow()` vs `Wait()` vs `Reserve()`.** `Allow()` does not block and returns false immediately if the token is unavailable — use this in HTTP handlers. `Wait()` blocks until a token is available — use this for background workers where waiting is acceptable. `Reserve()` reserves a future token and returns when to use it — useful when you want to schedule work rather than reject it.

## Key Takeaway

Rate limiting is not about punishing users — it is about protecting your service's ability to serve everyone equally. Implement it at the edge of your API as middleware. Use token buckets for their burst-friendly behaviour. Key by API token or authenticated user ID rather than IP where possible. Return `429` with `Retry-After` and `X-RateLimit-*` headers so well-behaved clients can back off gracefully. For multi-instance deployments, move the counter to Redis. A service that can say "not right now" is a service that stays up.

---

**Series: Go API and Service Design**

[← Lesson 7: Timeouts and Retries](/post/go/go-api-timeouts-retries) | [Lesson 9: Config Management →](/post/go/go-api-config)
