---
title: "Lesson 3: Circuit Breaking — Stop calling the service that's already down"
author: Atharva Pandey
series: "Go Networking & Distributed Systems"
lesson: 3
keywords:
  - Go
  - golang
  - circuit breaker
  - resilience
  - distributed systems
  - fault tolerance
  - half-open
  - networking
tags:
  - Go tutorial
  - golang
  - networking
date: '2024-08-28T00:00:00.000Z'
---

There's a particular kind of production incident that goes like this: Service A calls Service B. Service B starts responding slowly because its database is under pressure. Service A's goroutines pile up waiting for responses. After a few minutes, Service A is out of memory, and now two services are down instead of one. The postmortem note says "cascading failure." The fix, which nobody implemented, is a circuit breaker.

The circuit breaker pattern comes from electrical engineering. When too much current flows through a circuit, the breaker trips — it opens the circuit and prevents more current from flowing until someone resets it. In software, the "current" is requests, and "tripping" means refusing to make calls to a failing downstream instead of piling up timeouts and consuming resources.

## The Problem

Without a circuit breaker, a slow or failing downstream saturates your connection pool and goroutine pool:

```go
// WRONG — no circuit breaker, each call can block for the full timeout
func getInventory(productID string) (*Inventory, error) {
    // If inventory service is down, every call blocks for 5 seconds.
    // 100 concurrent requests × 5 seconds = all goroutines occupied.
    resp, err := httpClient.Get("https://inventory.internal/products/" + productID)
    if err != nil {
        return nil, err
    }
    defer resp.Body.Close()
    // ...
}
```

This is the failure mode: the downstream is slow, not down. It returns responses, just slowly. Your service keeps trying, goroutines accumulate, memory fills up. A purely timeout-based approach doesn't help because the timeout is still consuming resources for its full duration per request.

The second failure mode is the retry amplification I described in the last lesson. Combine retries with no circuit breaking, and a 1x traffic load on the upstream becomes 4x in minutes.

## The Idiomatic Way

A circuit breaker has three states: Closed (normal operation, requests go through), Open (failure threshold exceeded, requests are rejected immediately), and Half-Open (probe state — let a few requests through to test if the downstream has recovered).

Here's a minimal but production-suitable implementation:

```go
package breaker

import (
    "errors"
    "sync"
    "time"
)

// ErrOpen is returned when the circuit is open and the call is rejected.
var ErrOpen = errors.New("circuit breaker open")

type state int

const (
    stateClosed   state = iota
    stateOpen
    stateHalfOpen
)

// Breaker is a simple three-state circuit breaker.
type Breaker struct {
    mu sync.Mutex

    state      state
    failures   int
    successes  int
    lastFailed time.Time

    // Configuration.
    maxFailures    int
    resetTimeout   time.Duration
    halfOpenProbes int
}

func New(maxFailures int, resetTimeout time.Duration) *Breaker {
    return &Breaker{
        maxFailures:    maxFailures,
        resetTimeout:   resetTimeout,
        halfOpenProbes: 1,
    }
}

// Do executes fn if the circuit allows it. Returns ErrOpen if the circuit
// is open and the call is rejected without executing fn.
func (b *Breaker) Do(fn func() error) error {
    b.mu.Lock()
    allowed, afterFn := b.allow()
    b.mu.Unlock()

    if !allowed {
        return ErrOpen
    }

    err := fn()

    b.mu.Lock()
    afterFn(err)
    b.mu.Unlock()

    return err
}

// allow returns whether the call should proceed and a callback to record the result.
// Must be called with b.mu held.
func (b *Breaker) allow() (bool, func(error)) {
    switch b.state {
    case stateClosed:
        return true, b.recordClosed

    case stateOpen:
        if time.Since(b.lastFailed) > b.resetTimeout {
            b.state = stateHalfOpen
            b.successes = 0
            return true, b.recordHalfOpen
        }
        return false, nil

    case stateHalfOpen:
        // Only allow one probe at a time in half-open state.
        return true, b.recordHalfOpen
    }
    return false, nil
}

func (b *Breaker) recordClosed(err error) {
    if err != nil {
        b.failures++
        b.lastFailed = time.Now()
        if b.failures >= b.maxFailures {
            b.state = stateOpen
            b.failures = 0
        }
    } else {
        b.failures = 0 // reset on success
    }
}

func (b *Breaker) recordHalfOpen(err error) {
    if err != nil {
        b.state = stateOpen
        b.lastFailed = time.Now()
    } else {
        b.successes++
        if b.successes >= b.halfOpenProbes {
            b.state = stateClosed
            b.failures = 0
        }
    }
}
```

Using this in a service:

```go
var inventoryBreaker = breaker.New(5, 30*time.Second)

func getInventory(productID string) (*Inventory, error) {
    var inv *Inventory

    err := inventoryBreaker.Do(func() error {
        resp, err := httpClient.Get("https://inventory.internal/products/" + productID)
        if err != nil {
            return err
        }
        defer resp.Body.Close()

        if resp.StatusCode >= 500 {
            return fmt.Errorf("inventory service error: %d", resp.StatusCode)
        }

        return json.NewDecoder(resp.Body).Decode(&inv)
    })

    if errors.Is(err, breaker.ErrOpen) {
        // Circuit is open — return cached data or a degraded response.
        return getCachedInventory(productID)
    }
    return inv, err
}
```

The key design decision is what to return when the circuit is open. Panicking or returning a hard error is one option, but a better approach — especially for read operations — is to return a cached or default response. The inventory service being down shouldn't prevent users from viewing products entirely; it should just mean the stock count might be slightly stale.

## In The Wild

Production circuit breakers need metrics. Without visibility into state transitions, you'll never know why the breaker tripped or how long it took to recover:

```go
// Record state transitions as counter events.
func (b *Breaker) transitionTo(next state) {
    prev := b.state
    b.state = next
    if prev != next {
        circuitBreakerTransitions.WithLabelValues(
            stateNames[prev],
            stateNames[next],
        ).Inc()
    }
}

var stateNames = map[state]string{
    stateClosed:   "closed",
    stateOpen:     "open",
    stateHalfOpen: "half_open",
}
```

I also expose the current state as a gauge metric that I plot on a dashboard alongside the upstream's latency p99. When the latency spike and the circuit opening appear on the same timeline, it becomes obvious what happened.

One refinement worth adding: count failures by error type. A 500 from the upstream should trip the breaker faster than a connection timeout. Timeouts might mean the upstream is slow but processing — retrying into a timeout storm trips your breaker for the wrong reason. Some teams use separate thresholds: 5 consecutive 5xx errors opens the circuit, but 20 consecutive timeouts are needed. The right thresholds depend on your SLOs.

## The Gotchas

**Per-instance vs. shared state.** My implementation above uses an in-process struct. If you have ten instances of your service, each one has an independent circuit breaker. One instance might have its breaker open while nine others continue hammering the failing upstream. For strongly coordinated circuit breaking, you need to share state — typically via a distributed cache or a service mesh feature like Istio's outlier detection. For most cases, per-instance is fine: the breaker still protects each instance from goroutine saturation, and all instances will trip eventually.

**Don't forget about the downstream's perspective.** When your circuit opens and you stop sending requests, your error rate drops — which looks good in your metrics. But the failing downstream gets a traffic reprieve, which can help it recover. This is a feature, not a bug. The reset timeout is the window you're giving the downstream to recover before you probe it again.

**Circuit breakers and retries need to cooperate.** If your retry logic runs inside the circuit breaker's `Do` function, retries count as additional calls for the breaker's failure tracking. Run the circuit breaker on the outside, retries on the inside: one "attempt" to the breaker equals the full retry sequence.

**Test the open state explicitly.** Most bugs I've seen in circuit breaker implementations are in the fallback path — the code that runs when the circuit is open. Load test your service with the downstream completely unavailable and verify the degraded response is actually served.

## Key Takeaway

A circuit breaker is the difference between a contained outage and a cascading failure. The mechanical implementation is straightforward. The hard work is deciding what happens when the circuit opens — what cached data, default response, or graceful degradation you serve instead. Build that fallback path first, test it in isolation, and then wire up the breaker.

---

**Previous:** [Lesson 2: Retries with Exponential Backoff](/post/go/go-net-retries/)
**Next:** [Lesson 4: Connection Pooling — One connection per request is a performance bug](/post/go/go-net-conn-pooling/)
