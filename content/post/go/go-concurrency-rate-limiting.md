---
title: 'Lesson 22: Rate Limiting and Load Shedding — Say no before you fall over'
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 22
keywords:
  - Go
  - golang
  - concurrency
  - rate limiting
  - load shedding
  - token bucket
  - golang.org/x/time/rate
  - HTTP 503
  - backpressure
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-12-10T00:00:00.000Z'
---

The most honest thing a server can do is say "no." Not crash, not time out after 30 seconds, not queue work indefinitely until memory explodes — just return a clean 503 and let the caller decide what to do. I spent a long time thinking rate limiting was about protecting *users* from themselves. Then I got paged at midnight because a misbehaving client hammered an endpoint, the service queued everything politely, RAM climbed to the ceiling, and the process died. The fix wasn't more memory. It was teaching the service to refuse work it couldn't handle.

Rate limiting and load shedding are two sides of the same coin. Rate limiting says "you can only ask me this many times per second." Load shedding says "I'm already overwhelmed — I'm dropping this regardless of who you are." Both require the service to have opinions about its own capacity. That's the mindset shift. Your service is not a passive recipient of traffic. It's an active participant that gets to say no.

## The Problem

The naive implementation looks harmless:

```go
// WRONG — no rate limiting, no load shedding
func handleRequest(w http.ResponseWriter, r *http.Request) {
    // Just do the work, always, no matter what
    result, err := expensiveDBQuery(r.Context(), r.URL.Query().Get("id"))
    if err != nil {
        http.Error(w, "internal error", http.StatusInternalServerError)
        return
    }
    json.NewEncoder(w).Encode(result)
}
```

This handler is completely honest about one thing: it has no idea what's happening around it. It doesn't know if there are 10 concurrent requests or 10,000. It doesn't know if the DB is at 90% capacity. It'll happily accept every request and attempt every query until something in the stack — the DB connection pool, the OS file descriptor limit, the heap — gives up and dies.

The failure mode is particularly ugly because it's gradual and then sudden. Latency creeps up. P99 goes from 50ms to 500ms. Then 5 seconds. Then requests start timing out on the client side. Then the DB connection pool exhausts. Then the whole service falls over. By then you have a cascade: the upstream retries, doubling the load, making recovery impossible without a full restart.

## The Idiomatic Way

Go's `golang.org/x/time/rate` package implements a token bucket, which is the right mental model. Imagine a bucket that fills with tokens at a steady rate. Each request consumes a token. If the bucket is empty, you wait or you get rejected. The bucket has a maximum capacity (burst) so short spikes can be absorbed.

```go
// RIGHT — per-service rate limiter with token bucket
import "golang.org/x/time/rate"

// 100 requests per second, burst of 20
var limiter = rate.NewLimiter(rate.Limit(100), 20)

func handleRequest(w http.ResponseWriter, r *http.Request) {
    // Non-blocking check — returns immediately
    if !limiter.Allow() {
        w.Header().Set("Retry-After", "1")
        http.Error(w, "rate limit exceeded", http.StatusTooManyRequests)
        return
    }

    result, err := expensiveDBQuery(r.Context(), r.URL.Query().Get("id"))
    if err != nil {
        http.Error(w, "internal error", http.StatusInternalServerError)
        return
    }
    json.NewEncoder(w).Encode(result)
}
```

`Allow()` is the non-blocking form — it either takes a token or returns false. `Wait(ctx)` is the blocking form — it waits until a token is available or the context cancels. `Reserve()` gives you back a `Reservation` you can inspect before committing. For HTTP handlers, `Allow()` is almost always what you want. You don't want to hold a goroutine hostage waiting for a rate limit slot.

Now, that's per-service rate limiting. But often you need per-client limiting — different buckets per IP or API key:

```go
// RIGHT — per-client rate limiting with cleanup
import (
    "sync"
    "time"
    "golang.org/x/time/rate"
)

type clientLimiter struct {
    limiter  *rate.Limiter
    lastSeen time.Time
}

type IPRateLimiter struct {
    mu      sync.Mutex
    clients map[string]*clientLimiter
    rate    rate.Limit
    burst   int
}

func NewIPRateLimiter(r rate.Limit, b int) *IPRateLimiter {
    rl := &IPRateLimiter{
        clients: make(map[string]*clientLimiter),
        rate:    r,
        burst:   b,
    }
    // Clean up stale entries every minute
    go rl.cleanupLoop()
    return rl
}

func (rl *IPRateLimiter) Allow(ip string) bool {
    rl.mu.Lock()
    defer rl.mu.Unlock()

    cl, ok := rl.clients[ip]
    if !ok {
        cl = &clientLimiter{
            limiter: rate.NewLimiter(rl.rate, rl.burst),
        }
        rl.clients[ip] = cl
    }
    cl.lastSeen = time.Now()
    return cl.limiter.Allow()
}

func (rl *IPRateLimiter) cleanupLoop() {
    ticker := time.NewTicker(time.Minute)
    defer ticker.Stop()
    for range ticker.C {
        rl.mu.Lock()
        for ip, cl := range rl.clients {
            if time.Since(cl.lastSeen) > 3*time.Minute {
                delete(rl.clients, ip)
            }
        }
        rl.mu.Unlock()
    }
}
```

The cleanup loop is important. Without it, every unique IP you've ever seen stays in memory forever. That's a slow memory leak that'll bite you on busy services.

## In The Wild

Rate limiting handles steady-state traffic. Load shedding handles sudden spikes. The distinction matters. If you only have rate limiting, a thundering herd can still overwhelm you because the requests queue up and your goroutine count or connection pool saturates before the rate limiter kicks them out.

Load shedding uses a different heuristic: current system load. The simplest version uses a semaphore to limit concurrent in-flight requests:

```go
// RIGHT — load shedding via semaphore (concurrent request cap)
type LoadShedder struct {
    sem chan struct{}
}

func NewLoadShedder(maxConcurrent int) *LoadShedder {
    return &LoadShedder{
        sem: make(chan struct{}, maxConcurrent),
    }
}

func (ls *LoadShedder) Middleware(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        select {
        case ls.sem <- struct{}{}:
            defer func() { <-ls.sem }()
            next.ServeHTTP(w, r)
        default:
            // Can't acquire slot — shed the load
            w.Header().Set("Retry-After", "2")
            http.Error(w, "service overloaded", http.StatusServiceUnavailable)
        }
    })
}
```

The `select` with a `default` is key. We try to acquire a semaphore slot. If one's available, we take it and do the work. If not, we immediately return 503. We never block. We never queue. We shed the load and let the client handle the retry logic.

In production I combine both — rate limiting for sustained abuse, load shedding for spikes:

```go
// Production setup: rate limiter + load shedder as middleware stack
func main() {
    mux := http.NewServeMux()
    mux.HandleFunc("/api/", handleRequest)

    ipLimiter := NewIPRateLimiter(rate.Limit(10), 5) // 10 req/s per IP, burst 5
    shedder := NewLoadShedder(500)                   // max 500 concurrent

    handler := rateLimitMiddleware(ipLimiter,
        shedder.Middleware(mux),
    )

    srv := &http.Server{
        Addr:    ":8080",
        Handler: handler,
    }
    srv.ListenAndServe()
}

func rateLimitMiddleware(rl *IPRateLimiter, next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        ip := r.RemoteAddr // use X-Forwarded-For in real deployments
        if !rl.Allow(ip) {
            w.Header().Set("Retry-After", "1")
            http.Error(w, "too many requests", http.StatusTooManyRequests)
            return
        }
        next.ServeHTTP(w, r)
    })
}
```

## The Gotchas

**Rate limiting and backpressure are not the same thing.** Backpressure means slowing down the producer — making callers wait rather than rejecting them. It's appropriate for internal queues between goroutines, not for HTTP handlers. If you make HTTP handlers wait for rate limit slots, you're holding goroutines, file descriptors, and connection pool slots hostage. The pressure doesn't propagate cleanly. Just reject with 429/503 and put the retry logic in the client.

**`rate.Wait` in HTTP handlers is almost always wrong.** It looks appealing — "instead of rejecting, just make them wait." But waiting means the goroutine is blocked. Under load, you'll have hundreds of goroutines all blocking on `Wait`, consuming memory and connection pool slots, and your rate limiter becomes a queue rather than a safety valve. Use `Allow()` in handlers.

**Burst size matters more than you think.** A limiter of `rate.Limit(100)` with burst `1` is brutal — it allows exactly 100 req/s, perfectly evenly spaced. Real traffic doesn't arrive evenly. Set your burst to something reasonable — at least 5-10x your per-second rate for interactive APIs — or legitimate traffic gets rejected during momentary spikes.

**The Retry-After header is not optional.** When you return 429 or 503, include `Retry-After`. It tells the client how long to wait before retrying. Without it, well-behaved clients retry immediately, doubling your load. The value should be based on when the rate limiter will have tokens again — `limiter.Reserve().Delay()` gives you that.

**Global vs per-instance rate limits.** If you're running 10 replicas, a per-instance rate limit of 100 req/s means 1000 req/s total. That's fine if you want to limit per-server load. But if you want to limit a specific client to 100 req/s total across your cluster, you need a shared counter — Redis with INCR/EXPIRE is the typical solution. Local rate limiters can't coordinate across processes.

## Key Takeaway

Your service's most important feature is knowing when to say no. Rate limiting (token bucket, per-client) handles sustained abuse and prevents any single caller from monopolizing your resources. Load shedding (semaphore-based concurrent request cap) handles sudden spikes and prevents your goroutine pool and connection pool from saturating. Use `Allow()` in HTTP handlers — never `Wait()`. Return 429 for rate limits, 503 for load shedding, always with `Retry-After`. And remember: local rate limiters only work per-instance — if you need cluster-wide limits, you need a shared store.

The moment you add rate limiting and load shedding, your service stops being a victim of bad traffic and starts being a participant in its own survival. That's the shift that separates services that fall over from services that stay up.

---

← [Lesson 21: Supervisor Patterns](/post/go/go-concurrency-supervisor-patterns) | [Lesson 23: Distributed vs Local Concurrency →](/post/go/go-concurrency-distributed)
