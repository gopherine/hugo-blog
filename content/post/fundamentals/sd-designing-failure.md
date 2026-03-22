---
title: "Lesson 14: Designing for Failure — Circuit Breakers, Bulkheads, Chaos"
author: "Atharva Pandey"
series: "System Design from First Principles"
lesson: 14
tags:
  - fundamentals
  - system design
keywords:
  - circuit breaker
  - bulkhead pattern
  - chaos engineering
  - resilience
  - fault tolerance
  - designing for failure
  - system design
date: "2024-11-04T00:00:00.000Z"
---

In distributed systems, failure is not an exceptional case — it's the default condition. Networks partition. Hard drives fail. Memory fills up. Dependencies have bugs. Every distributed system that's been running for more than a few years has experienced every kind of failure you can imagine, and many you can't. The engineers who build resilient systems aren't smarter than the ones who don't. They've just internalized a single principle: design for when things go wrong, not for when things go right.

## The Core Concept

**Cascading failures: the real threat**

A single service failure in a well-designed system is a minor incident. The same failure in a poorly designed system can take down everything. Cascading failures happen when a slow or failing service causes callers to exhaust their thread pools, which makes those callers slow, which causes their callers to exhaust their thread pools, and so on up the dependency chain until everything is timing out.

The mechanism: Service A depends on Service B. B starts returning slow responses (500ms instead of 20ms). A has a thread pool of 50 threads. Each thread holds a connection to B, waiting for a response. Within seconds, all 50 threads are occupied. New requests to A queue up. A's response time climbs. Service C, which depends on A, starts experiencing slow responses. C's threads fill up. The cascade continues.

The root cause isn't the failure in B — it's the unbounded waiting in A. Every resilience pattern in this lesson addresses some aspect of this failure mode.

**Timeouts: the floor**

Every network call needs a timeout. Without one, a single slow dependency holds a thread forever. This is the absolute minimum resilience requirement.

```go
ctx, cancel := context.WithTimeout(ctx, 500*time.Millisecond)
defer cancel()

resp, err := client.Call(ctx, req)
if err != nil {
    if ctx.Err() == context.DeadlineExceeded {
        // Timeout — treat as transient failure, apply fallback
    }
    return nil, err
}
```

Setting the right timeout is part science, part judgment. Too low: you time out on legitimately slow but successful requests. Too high: you hold threads too long during failures. Start with the 99th percentile latency of the dependency in normal operation, multiply by 2–3x.

## How to Design It

**Circuit Breaker**

Named after the electrical circuit breaker, this pattern stops calls to a failing service before they time out. The circuit breaker tracks recent failures. When the failure rate exceeds a threshold, the circuit "opens" and subsequent calls fail immediately without attempting the network call.

```
[CLOSED] → too many failures → [OPEN]
  ↑                               ↓
  ← success                 (after timeout)
                           [HALF-OPEN]
                            (allow one request)
                                 ↓
                    success → [CLOSED]
                    failure → [OPEN]
```

Three states:
- **Closed**: normal operation, calls go through, failures are counted
- **Open**: circuit is broken, calls fail immediately with a fallback response
- **Half-open**: after a recovery timeout, one request is allowed through; if it succeeds, the circuit closes

```go
type CircuitBreaker struct {
    mu           sync.Mutex
    state        string // "closed", "open", "half-open"
    failures     int
    lastFailure  time.Time
    threshold    int
    timeout      time.Duration
}

func (cb *CircuitBreaker) Call(fn func() error) error {
    cb.mu.Lock()
    if cb.state == "open" {
        if time.Since(cb.lastFailure) > cb.timeout {
            cb.state = "half-open"
        } else {
            cb.mu.Unlock()
            return ErrCircuitOpen // fail fast
        }
    }
    cb.mu.Unlock()

    err := fn()

    cb.mu.Lock()
    defer cb.mu.Unlock()
    if err != nil {
        cb.failures++
        cb.lastFailure = time.Now()
        if cb.failures >= cb.threshold || cb.state == "half-open" {
            cb.state = "open"
        }
    } else {
        cb.failures = 0
        cb.state = "closed"
    }
    return err
}
```

The benefit: instead of waiting 500ms per request for a timeout, you fail immediately. This frees threads and prevents cascading. Combined with a fallback (return cached data, a default response, or an error message), the caller stays responsive even while the dependency is down.

**Bulkhead**

In ship design, bulkheads are walls that partition the hull into watertight compartments. If one compartment floods, the others remain dry. Applied to software: isolate resources (thread pools, connection pools) so that a failure in one subsystem doesn't starve resources from another.

Without bulkheads: you have one shared thread pool of 100 threads. A slow payment service hogs 95 of them. Your user profile service also uses the same pool and can't get threads. Everything degrades.

With bulkheads: payment service gets a dedicated pool of 20 threads, user profile service gets another 20 threads. A payment service meltdown doesn't affect user profile.

```go
type BulkheadPool struct {
    sem chan struct{}
}

func NewBulkheadPool(maxConcurrent int) *BulkheadPool {
    return &BulkheadPool{sem: make(chan struct{}, maxConcurrent)}
}

func (b *BulkheadPool) Execute(ctx context.Context, fn func() error) error {
    select {
    case b.sem <- struct{}{}: // acquire slot
        defer func() { <-b.sem }() // release on done
        return fn()
    case <-ctx.Done():
        return ErrBulkheadFull // reject immediately rather than wait
    }
}
```

**Retry with exponential backoff and jitter**

When a call fails transiently, retry — but not immediately, and not forever.

- **Exponential backoff**: wait 100ms, then 200ms, then 400ms... Each retry waits twice as long. Prevents overwhelming a recovering service.
- **Jitter**: add randomness to the backoff. Without it, all callers that experienced failures at the same time will retry at the same time, creating a thundering herd.
- **Maximum retries**: cap at 3–5 attempts. Don't retry indefinitely.

```go
func RetryWithBackoff(ctx context.Context, maxRetries int, fn func() error) error {
    for attempt := 0; attempt <= maxRetries; attempt++ {
        err := fn()
        if err == nil {
            return nil
        }
        if attempt == maxRetries {
            return err
        }
        // Exponential backoff with jitter
        base := time.Duration(100*math.Pow(2, float64(attempt))) * time.Millisecond
        jitter := time.Duration(rand.Int63n(int64(base / 2)))
        select {
        case <-time.After(base + jitter):
        case <-ctx.Done():
            return ctx.Err()
        }
    }
    return nil
}
```

Important: only retry on transient errors (timeouts, network errors, 503s). Never retry on 400 Bad Request or 404 — these won't improve with retries.

**Fallbacks and graceful degradation**

When a dependency is unavailable, what do you do? Options:
- Return cached data (slightly stale is better than nothing)
- Return a default response ("recommendations unavailable")
- Degrade functionality (show the page without personalized elements)
- Queue for later (return a 202 Accepted, process asynchronously when service recovers)

The worst option: return an error that propagates up the entire call stack, killing the whole page because one widget's API was down.

**Chaos Engineering**

Netflix invented "chaos engineering" by literally injecting random failures into production: randomly killing virtual machines (the Chaos Monkey), simulating entire region failures (Chaos Kong). The principle: if you don't test failure, your failure handling code rots. The first time a real outage happens shouldn't be the first time your circuit breakers are exercised.

Chaos engineering in practice: start in staging/pre-prod. Inject latency (make service B respond in 5 seconds instead of 20ms). Observe how service A behaves. Are circuit breakers triggering? Are fallbacks working? Are error rates acceptable? Fix the gaps, then gradually move to production during low-traffic windows with defined abort criteria.

## Real-World Example

Netflix's resilience libraries (Hystrix, now Resilience4j) are the canonical implementation of circuit breakers and bulkheads in microservices. Every service-to-service call at Netflix goes through these patterns. Their chaos engineering program (Simian Army) continuously validates that the resilience mechanisms work.

Amazon's dependency on the Dependency Management Service they call "Danger Zone" — any service that can take out a critical path if it fails gets special treatment: graceful degradation paths are required, tested, and monitored separately.

Google's SRE practices (documented in the SRE book) formalize error budgets: each service is allowed a certain amount of downtime per quarter. When the budget is exhausted, new feature development stops and reliability work takes priority. This aligns incentives — teams that ship fast and break things face real consequences.

## Interview Tips

"How do you handle a dependent service being slow?" — Timeouts first. Circuit breaker to fail fast after a pattern of failures. Bulkhead to limit blast radius. Retry with backoff for transient failures. Fallback for when all else fails.

"How do you set your circuit breaker threshold?" — Based on historical error rates. In normal operation, what's the expected error rate (usually < 1%)? Set the open threshold at something like 50% errors in a 10-second window. Too sensitive and it trips on normal variance; too insensitive and it doesn't help.

"What's the difference between a circuit breaker and a retry?" — Complementary, not alternatives. Retry handles individual transient failures. Circuit breaker handles systematic failures in a dependency. You retry first; if the failure rate is high enough, the circuit breaker stops you from retrying and wastes resources.

"How do you test resilience?" — Chaos engineering: inject failures in lower environments and validate that the system degrades gracefully. Automated failure injection in CI/CD pipelines.

## Key Takeaway

Distributed systems fail constantly. Cascading failures happen when slow dependencies hold threads, starving resources from other operations. Timeouts are the minimum — every network call must have one. Circuit breakers fail fast when a dependency is in trouble, preventing resource exhaustion. Bulkheads isolate resource pools so one dependency's failures don't affect others. Retry with exponential backoff and jitter handles transient failures without creating thundering herds. Graceful degradation means returning something useful (cached data, defaults) rather than propagating errors. Chaos engineering validates that all of this actually works under real failure conditions. Design for failure from day one — retrofitting resilience into an existing system is significantly harder than building it in.

---

**Previous:** [Lesson 13: Design a Payment System](/post/fundamentals/sd-payment-system/)
**Next:** [Lesson 15: CAP Theorem in Practice — What It Actually Means for Your System](/post/fundamentals/sd-cap-theorem/)
