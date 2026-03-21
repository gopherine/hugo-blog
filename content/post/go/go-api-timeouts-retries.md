---
title: "Lesson 7: Timeouts and Retries — Your client needs a deadline, your server needs a budget"
author: Atharva Pandey
series: "Go API and Service Design"
lesson: 7
keywords: [Go, golang, API, backend, timeouts, retries, context, deadline, circuit breaker]
tags: [Go tutorial, golang, backend]
date: '2025-02-01T00:00:00.000Z'
---

I once watched a service die in slow motion. An upstream dependency started responding slowly — not failing, just slow. Within minutes, all request goroutines were blocked waiting for the upstream. New requests kept arriving. Goroutines piled up. Memory climbed. Eventually the process was killed by the kernel. The upstream recovered in about thirty seconds. My service was down for twelve minutes. Every second of that outage was caused by the absence of a single line: a timeout.

## The Problem

Timeouts and retries are two sides of the same problem: how does your service behave when something it depends on is not responding normally?

Without timeouts, your service will block indefinitely waiting for slow dependencies. A single slow database connection or HTTP call can eventually exhaust your goroutine pool (in practice, your available memory, since goroutines are cheap but not free) and bring the whole service down.

Without retries, transient failures — a brief network hiccup, a 503 from a downstream service that recovers in 100ms — are surfaced as errors to your users even though the operation would have succeeded if attempted one more time.

The tension is that retries without timeouts are dangerous (you retry forever), and timeouts without retries are unfriendly (you surface every transient failure). The correct approach is both, carefully tuned.

## The Idiomatic Way

Go's `context` package is the mechanism for timeouts. Every function that does I/O should accept a `context.Context` as its first argument and honour cancellation. Here is the discipline applied consistently:

```go
package main

import (
    "context"
    "fmt"
    "net/http"
    "time"
)

// Server-side: attach a budget to every incoming request
func (s *Server) withRequestTimeout(timeout time.Duration) func(http.Handler) http.Handler {
    return func(next http.Handler) http.Handler {
        return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
            ctx, cancel := context.WithTimeout(r.Context(), timeout)
            defer cancel()
            next.ServeHTTP(w, r.WithContext(ctx))
        })
    }
}

// Client-side: every outbound call has a deadline
func (s *Server) fetchUserProfile(ctx context.Context, userID string) (*Profile, error) {
    // Derive a child context with a tighter deadline for this specific call
    callCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
    defer cancel()

    req, err := http.NewRequestWithContext(callCtx, http.MethodGet,
        fmt.Sprintf("https://profile-service/users/%s", userID), nil)
    if err != nil {
        return nil, err
    }

    resp, err := s.httpClient.Do(req)
    if err != nil {
        return nil, fmt.Errorf("profile fetch failed: %w", err)
    }
    defer resp.Body.Close()

    if resp.StatusCode != http.StatusOK {
        return nil, fmt.Errorf("profile service returned %d", resp.StatusCode)
    }

    var profile Profile
    if err := json.NewDecoder(resp.Body).Decode(&profile); err != nil {
        return nil, err
    }
    return &profile, nil
}
```

The `withRequestTimeout` middleware sets a budget for the entire request. Any downstream calls derive child contexts from it with their own tighter deadlines. When the outer context is cancelled (either by the client disconnecting or by the server-side budget expiring), all child contexts are cancelled automatically — no goroutine leaks.

## In The Wild

Retries need two things: a decision function (should I retry this error?) and a back-off strategy. Here is a reusable retry helper with exponential back-off and jitter:

```go
package retry

import (
    "context"
    "errors"
    "math"
    "math/rand"
    "net/http"
    "time"
)

type Config struct {
    MaxAttempts int
    BaseDelay   time.Duration
    MaxDelay    time.Duration
    // Retryable returns true if the error should be retried
    Retryable func(err error, statusCode int) bool
}

var DefaultConfig = Config{
    MaxAttempts: 3,
    BaseDelay:   100 * time.Millisecond,
    MaxDelay:    2 * time.Second,
    Retryable: func(err error, statusCode int) bool {
        if err != nil {
            // Retry on network errors but not on context cancellation
            return !errors.Is(err, context.Canceled) &&
                   !errors.Is(err, context.DeadlineExceeded)
        }
        // Retry on 429 and 5xx, but not on 4xx (client errors)
        return statusCode == http.StatusTooManyRequests ||
               (statusCode >= 500 && statusCode != http.StatusNotImplemented)
    },
}

// Do executes fn with retries according to cfg.
// fn should return (statusCode, error) — use 0 if no HTTP status is available.
func Do(ctx context.Context, cfg Config, fn func(ctx context.Context) (int, error)) error {
    var lastErr error
    for attempt := 0; attempt < cfg.MaxAttempts; attempt++ {
        if ctx.Err() != nil {
            return ctx.Err()
        }

        statusCode, err := fn(ctx)
        if err == nil && statusCode < 300 {
            return nil
        }

        lastErr = err
        if err == nil {
            lastErr = fmt.Errorf("received status %d", statusCode)
        }

        if !cfg.Retryable(err, statusCode) {
            return lastErr
        }

        if attempt < cfg.MaxAttempts-1 {
            delay := backoff(cfg.BaseDelay, cfg.MaxDelay, attempt)
            select {
            case <-time.After(delay):
            case <-ctx.Done():
                return ctx.Err()
            }
        }
    }
    return fmt.Errorf("after %d attempts: %w", cfg.MaxAttempts, lastErr)
}

// backoff computes exponential back-off with full jitter
func backoff(base, max time.Duration, attempt int) time.Duration {
    exp := math.Pow(2, float64(attempt))
    delay := time.Duration(float64(base) * exp)
    if delay > max {
        delay = max
    }
    // Full jitter: random duration between 0 and delay
    return time.Duration(rand.Int63n(int64(delay) + 1))
}
```

Usage is clean and explicit:

```go
var profile *Profile
err := retry.Do(ctx, retry.DefaultConfig, func(ctx context.Context) (int, error) {
    p, fetchErr := s.fetchUserProfile(ctx, userID)
    if fetchErr != nil {
        return 0, fetchErr
    }
    profile = p
    return http.StatusOK, nil
})
if err != nil {
    return nil, fmt.Errorf("could not fetch profile: %w", err)
}
```

The `select` inside the back-off respects context cancellation. If the outer request context expires while you are waiting between retries, the retry loop exits immediately instead of sleeping until the delay expires.

## The Gotchas

**Retrying non-idempotent operations is dangerous.** Only retry operations where it is safe to execute them more than once. `GET` is always safe. `POST` is only safe if the endpoint is idempotent (see Lesson 6). Never retry a payment creation without an idempotency key.

**Context cancellation is not an error to retry.** `context.Canceled` means the caller gave up. `context.DeadlineExceeded` means the budget expired. Retrying either of these is pointless — the context is already done and subsequent attempts will fail immediately with the same error.

**Set timeouts on the `http.Client`, not just the request context.** If you create an `http.Client` without setting `Timeout`, a context on the request is your only safety net. A better defence is to configure the transport directly:

```go
s.httpClient = &http.Client{
    Timeout: 10 * time.Second, // absolute maximum for any request
    Transport: &http.Transport{
        DialContext:           (&net.Dialer{Timeout: 3 * time.Second}).DialContext,
        TLSHandshakeTimeout:   3 * time.Second,
        ResponseHeaderTimeout: 5 * time.Second,
        IdleConnTimeout:       90 * time.Second,
        MaxIdleConns:          100,
        MaxIdleConnsPerHost:   10,
    },
}
```

`ResponseHeaderTimeout` is particularly valuable — it limits how long you wait for the first response byte. Without it, a server that accepts the connection and then hangs will block you for the full `Timeout`.

**Jitter is not optional for retry back-off.** If fifty instances of your service all fail at the same time and all retry with a deterministic back-off (1s, 2s, 4s), they will all hammer the recovering upstream simultaneously at each interval. Full jitter spreads the retries across time, reducing thundering-herd pressure on the dependency.

## Key Takeaway

Every outgoing I/O call needs a timeout. Every incoming request needs a budget. Go's context propagation makes this composable — derive child contexts, and cancellation flows automatically. Retries belong at the call site, not deep in your business logic, and they need three things: a cap on attempts, exponential back-off with jitter, and a sensible definition of what is retryable. Configure your `http.Client` transport explicitly. Never retry non-idempotent operations without an idempotency key. With these practices in place, transient failures become invisible to your users and slow dependencies can no longer bring your service down.

---

**Series: Go API and Service Design**

[← Lesson 6: Idempotency in APIs](/post/go/go-api-idempotency) | [Lesson 8: Rate Limiting →](/post/go/go-api-rate-limiting)
