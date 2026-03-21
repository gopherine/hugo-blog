---
title: 'Lesson 2: Retries with Exponential Backoff — Retry right or retry forever'
author: Atharva Pandey
series: "Go Networking & Distributed Systems"
lesson: 2
keywords:
  - Go
  - golang
  - retries
  - exponential backoff
  - jitter
  - resilience
  - HTTP
  - networking
tags:
  - Go tutorial
  - golang
  - networking
date: '2024-07-20T00:00:00.000Z'
---

I once worked on a service that retried failed HTTP requests in a tight loop with no backoff and no jitter. When our upstream had a brief outage, every instance of our service fired retries simultaneously, on the same cadence, forever. The upstream came back online, got immediately hammered by a synchronized retry storm from fifty instances, went down again, and the cycle repeated for forty minutes. The "retry" logic had turned a five-minute outage into a cascading failure.

Retries are not optional in distributed systems. Every network call can fail. Connections drop. Upstreams restart. Rate limiters kick in. The question isn't whether to retry — it's how to retry without making everything worse.

## The Problem

The most dangerous retry implementation looks innocent:

```go
// WRONG — synchronized retries with no backoff will thundering-herd your upstream
func fetchWithRetry(url string) (*http.Response, error) {
    for i := 0; i < 3; i++ {
        resp, err := http.Get(url)
        if err == nil && resp.StatusCode < 500 {
            return resp, nil
        }
        time.Sleep(time.Second) // fixed delay, everyone wakes up at the same time
    }
    return nil, fmt.Errorf("all retries failed")
}
```

Three problems in eight lines. Fixed delay means every client retries at the same instant — that's the thundering herd. No distinction between retryable and non-retryable errors — a 400 Bad Request will be retried three times for no reason. And the function swallows the last error, returning a useless message instead of the actual failure.

A subtler version of the same mistake: retry on all errors without checking if the request is idempotent. Retrying a POST that creates a payment record can charge a customer twice.

## The Idiomatic Way

Correct retry logic has four components: exponential base delay, random jitter, a retryability check, and a maximum attempt count. Here's the implementation I use as a starting point:

```go
package retry

import (
    "context"
    "fmt"
    "math/rand"
    "net/http"
    "time"
)

// Config controls retry behaviour.
type Config struct {
    MaxAttempts int
    BaseDelay   time.Duration
    MaxDelay    time.Duration
    // Retryable returns true if the error or response warrants a retry.
    Retryable func(resp *http.Response, err error) bool
}

// DefaultConfig is a reasonable starting point for idempotent HTTP calls.
var DefaultConfig = Config{
    MaxAttempts: 4,
    BaseDelay:   200 * time.Millisecond,
    MaxDelay:    10 * time.Second,
    Retryable: func(resp *http.Response, err error) bool {
        if err != nil {
            return true // network error — always retryable
        }
        // Retry on 429 (rate limited) and 5xx (server errors).
        // Never retry 4xx (client errors) — they won't get better.
        return resp.StatusCode == http.StatusTooManyRequests ||
            resp.StatusCode >= http.StatusInternalServerError
    },
}

// Do executes fn with retries according to cfg.
func Do(ctx context.Context, cfg Config, fn func(ctx context.Context) (*http.Response, error)) (*http.Response, error) {
    var (
        resp    *http.Response
        lastErr error
    )

    for attempt := 0; attempt < cfg.MaxAttempts; attempt++ {
        resp, lastErr = fn(ctx)

        if !cfg.Retryable(resp, lastErr) {
            return resp, lastErr
        }

        if attempt == cfg.MaxAttempts-1 {
            break // don't sleep after the last attempt
        }

        delay := backoffDelay(cfg.BaseDelay, cfg.MaxDelay, attempt)
        select {
        case <-ctx.Done():
            return nil, ctx.Err()
        case <-time.After(delay):
        }
    }

    if lastErr != nil {
        return nil, fmt.Errorf("after %d attempts: %w", cfg.MaxAttempts, lastErr)
    }
    return resp, nil
}

// backoffDelay returns an exponentially increasing delay with full jitter.
// Full jitter — random value in [0, cap] — produces better load distribution
// than equal jitter. See AWS's "Exponential Backoff and Jitter" article.
func backoffDelay(base, max time.Duration, attempt int) time.Duration {
    // Exponential: base * 2^attempt
    exp := base * (1 << attempt)
    if exp > max {
        exp = max
    }
    // Full jitter: uniform random in [0, exp]
    jitter := time.Duration(rand.Int63n(int64(exp)))
    return jitter
}
```

The context check inside `select` is critical. If the caller cancels the context — because a user disconnected, or a deadline passed — the retry loop stops immediately. Without it, the loop would keep sleeping and retrying even after the result is no longer needed.

Using this in practice:

```go
resp, err := retry.Do(ctx, retry.DefaultConfig, func(ctx context.Context) (*http.Response, error) {
    req, err := http.NewRequestWithContext(ctx, http.MethodGet, "https://api.example.com/data", nil)
    if err != nil {
        return nil, err
    }
    return httpClient.Do(req)
})
if err != nil {
    return fmt.Errorf("fetch data: %w", err)
}
defer resp.Body.Close()
```

Each attempt passes the context down to the request. If the upstream sends back a 429 with a `Retry-After` header, you can parse that header inside the `Retryable` function and use the server's suggested delay instead of your computed backoff.

## In The Wild

Handling `Retry-After` is a real-world requirement when talking to APIs that rate limit aggressively:

```go
func retryableWithHeader(resp *http.Response, err error) (bool, time.Duration) {
    if err != nil {
        return true, 0
    }
    if resp.StatusCode == http.StatusTooManyRequests {
        // Respect the server's requested delay.
        if ra := resp.Header.Get("Retry-After"); ra != "" {
            if secs, parseErr := strconv.Atoi(ra); parseErr == nil {
                return true, time.Duration(secs) * time.Second
            }
        }
        return true, 0 // fall back to computed backoff
    }
    return resp.StatusCode >= 500, 0
}
```

I've also found it useful to add observability to the retry loop. Each retry attempt is meaningful signal — it means something is degraded. Counting retries as a metric gives you early warning before errors breach your error budget:

```go
for attempt := 0; attempt < cfg.MaxAttempts; attempt++ {
    if attempt > 0 {
        retryCounter.WithLabelValues(strconv.Itoa(attempt)).Inc()
    }
    // ... rest of loop
}
```

When retry rate spikes, you know your upstream is struggling — often before your error rate metric picks it up, because the retries are masking the underlying failures from end users.

## The Gotchas

**Retrying non-idempotent operations.** GET, HEAD, and OPTIONS are safe to retry by definition. PUT and DELETE are idempotent in the HTTP spec. POST is not. If you must retry a POST, the upstream needs to support idempotency keys — a client-generated UUID sent in a header that the server uses to deduplicate requests. Without this, retried POSTs can create duplicate records.

**Wrapping errors loses retryability information.** If your `fn` function wraps the underlying error, the `Retryable` check needs to `errors.As` through the wrapper to find the original network error. Checking `err != nil` at the top level is usually sufficient, but if you're wrapping specific sentinel errors, be careful the retry logic can still see through the wrapper.

**Budget your retries globally.** Per-request retry logic is necessary but not sufficient. If one upstream starts failing and every incoming request retries four times, your service suddenly appears to handle 4x the traffic from the upstream's perspective. Combine per-request retries with a circuit breaker (covered in the next lesson) to cap the total pressure on a struggling downstream.

**Don't retry timeouts blindly.** A timeout could mean the request succeeded but the response was slow. Retrying a timed-out write operation can cause the operation to happen twice. Make sure your upstream is truly idempotent before retrying on context deadline exceeded.

## Key Takeaway

Exponential backoff with full jitter is the mechanical part — easy to implement correctly once you've seen it. The hard part is getting the semantics right: only retry idempotent operations, stop retrying when the context is done, and distinguish retryable server errors from non-retryable client errors. Naive retries don't reduce failures; they amplify them.

---

**Previous:** [Lesson 1: HTTP Client Internals](/post/go/go-net-http-client/)
**Next:** [Lesson 3: Circuit Breaking — Stop calling the service that's already down](/post/go/go-net-circuit-breaker/)
