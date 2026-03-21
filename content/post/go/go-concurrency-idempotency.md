---
title: 'Lesson 27: Idempotency in Concurrent Systems — Assume everything runs twice'
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 27
keywords:
  - Go
  - golang
  - concurrency
  - idempotency
  - singleflight
  - at-least-once delivery
  - idempotency keys
  - database upserts
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2026-02-08T00:00:00.000Z'
---

Every distributed system I've worked on eventually ran something twice. A retry after a timeout. A duplicate webhook delivery. A job that got processed by two workers simultaneously because of a clock skew issue in the claim timeout logic. A user who double-clicked a submit button. Systems aren't gentle about this — they don't ask "are you sure?" before running your code again. They just run it. The question isn't whether your code will ever run twice. It's whether running it twice causes a problem.

Idempotency is the property that makes "running twice" safe. An idempotent operation produces the same result whether you run it once, twice, or a hundred times. It's not about preventing duplicates at the delivery layer — that's often impossible. It's about designing the processing layer so that processing a duplicate produces the same observable outcome as processing the original. Once you internalize this, a lot of distributed systems complexity becomes manageable.

## The Problem

The non-idempotent handler is the naive one:

```go
// WRONG — payment handler that charges twice on retry
func handleCreatePayment(w http.ResponseWriter, r *http.Request) {
    var req PaymentRequest
    json.NewDecoder(r.Body).Decode(&req)

    // If the client retries this request (network timeout, etc.),
    // we'll charge the customer again.
    charge, err := stripe.Charges.New(&stripe.ChargeParams{
        Amount:   stripe.Int64(req.Amount),
        Currency: stripe.String("usd"),
        Source:   stripe.String(req.CardToken),
    })
    if err != nil {
        http.Error(w, "payment failed", http.StatusInternalServerError)
        return
    }

    db.SavePayment(charge.ID, req.OrderID, req.Amount)
    json.NewEncoder(w).Encode(charge)
}
```

The client sends a payment request. The server processes it, charges the card, but the network drops before the response arrives. The client sees a timeout and retries. The server processes it again. The card gets charged twice. The customer calls support. The ops team manually issues a refund. The incident post-mortem mentions "the retry logic didn't account for idempotency."

This is a classic case, and it happens constantly. Not just with payments — with any mutation that's exposed over a network.

The concurrent in-process version is subtler:

```go
// WRONG — cache population without deduplication
var cache = make(map[string]*UserProfile)
var mu sync.RWMutex

func getProfile(ctx context.Context, userID string) (*UserProfile, error) {
    mu.RLock()
    if p, ok := cache[userID]; ok {
        mu.RUnlock()
        return p, nil
    }
    mu.RUnlock()

    // Multiple goroutines can reach here simultaneously for the same userID
    // They'll all hit the DB at once — thundering herd on cache miss
    profile, err := db.GetUserProfile(ctx, userID)
    if err != nil {
        return nil, err
    }

    mu.Lock()
    cache[userID] = profile
    mu.Unlock()

    return profile, nil
}
```

This isn't a correctness problem (the end result is the same — cache is populated), but it's a thundering herd problem. A burst of requests for an uncached user will all miss the cache simultaneously and all hit the database. Under load, this can overwhelm your database. The `singleflight` package solves this.

## The Idiomatic Way

For HTTP APIs, idempotency keys are the standard solution. The client generates a unique key for each logical operation and includes it in the request. The server uses this key to detect and suppress duplicates.

```go
// RIGHT — idempotency key pattern for payment handler
func handleCreatePayment(w http.ResponseWriter, r *http.Request) {
    idempotencyKey := r.Header.Get("Idempotency-Key")
    if idempotencyKey == "" {
        http.Error(w, "Idempotency-Key header required", http.StatusBadRequest)
        return
    }

    var req PaymentRequest
    json.NewDecoder(r.Body).Decode(&req)

    // Check if we've already processed this key
    existing, err := db.GetPaymentByIdempotencyKey(r.Context(), idempotencyKey)
    if err == nil && existing != nil {
        // Already processed — return the same response as last time
        w.Header().Set("X-Idempotent-Replayed", "true")
        json.NewEncoder(w).Encode(existing)
        return
    }

    // Process atomically — the upsert handles the race between concurrent
    // requests with the same idempotency key
    charge, err := stripe.Charges.New(&stripe.ChargeParams{
        Amount:      stripe.Int64(req.Amount),
        Currency:    stripe.String("usd"),
        Source:      stripe.String(req.CardToken),
        IdempotencyKey: stripe.String(idempotencyKey), // Stripe supports this natively
    })
    if err != nil {
        http.Error(w, "payment failed", http.StatusInternalServerError)
        return
    }

    // Use INSERT ... ON CONFLICT to handle concurrent requests with the same key
    if err := db.SavePaymentIdempotent(r.Context(), idempotencyKey, charge); err != nil {
        // If it's a conflict error, another request already saved it — fetch and return
        existing, _ = db.GetPaymentByIdempotencyKey(r.Context(), idempotencyKey)
        json.NewEncoder(w).Encode(existing)
        return
    }

    json.NewEncoder(w).Encode(charge)
}
```

For the thundering herd / cache stampede problem, `singleflight` is exactly right:

```go
// RIGHT — singleflight deduplicates concurrent identical calls
import "golang.org/x/sync/singleflight"

var (
    profileGroup singleflight.Group
    cache        sync.Map
)

func getProfile(ctx context.Context, userID string) (*UserProfile, error) {
    // Check cache first (sync.Map is safe for concurrent reads)
    if cached, ok := cache.Load(userID); ok {
        return cached.(*UserProfile), nil
    }

    // singleflight ensures only one DB call happens per userID,
    // regardless of how many goroutines call getProfile concurrently.
    // All callers for the same key block and get the same result.
    result, err, _ := profileGroup.Do(userID, func() (interface{}, error) {
        // Only one goroutine executes this for a given userID at a time
        profile, err := db.GetUserProfile(ctx, userID)
        if err != nil {
            return nil, err
        }
        cache.Store(userID, profile)
        return profile, nil
    })
    if err != nil {
        return nil, err
    }
    return result.(*UserProfile), nil
}
```

`singleflight.Do` takes a key and a function. If multiple goroutines call `Do` with the same key simultaneously, only one executes the function — the others block and get the same result when it completes. The third return value (the bool, ignored as `_` above) indicates whether the result was shared with another caller.

## In The Wild

Database upserts are the foundation of idempotent writes. Understand the difference between `INSERT`, `INSERT ... ON CONFLICT DO NOTHING`, and `INSERT ... ON CONFLICT DO UPDATE`:

```go
// RIGHT — idempotent job completion using database upsert
func markJobComplete(ctx context.Context, db *sql.DB, jobID string, result JobResult) error {
    // ON CONFLICT DO UPDATE is idempotent — running it twice sets the same values
    _, err := db.ExecContext(ctx, `
        INSERT INTO job_results (job_id, status, result_data, completed_at)
        VALUES ($1, 'completed', $2, NOW())
        ON CONFLICT (job_id)
        DO UPDATE SET
            status = EXCLUDED.status,
            result_data = EXCLUDED.result_data,
            -- Don't update completed_at on replay — keep the original timestamp
            completed_at = job_results.completed_at
    `, jobID, result.Data)
    return err
}

// At-least-once consumer: this can safely run for the same job twice
func processJobMessage(ctx context.Context, db *sql.DB, msg Message) error {
    job, err := parseJob(msg)
    if err != nil {
        return err // malformed — don't ack, let dead letter queue handle it
    }

    // Idempotency check at the start — fast path for duplicates
    if done, _ := isJobComplete(ctx, db, job.ID); done {
        return nil // already done, ack the duplicate
    }

    result, err := executeJob(ctx, job)
    if err != nil {
        return err // processing failed — don't ack, allow retry
    }

    // Upsert the result — safe to call twice
    return markJobComplete(ctx, db, job.ID, result)
}
```

The pattern here is: check if already done (fast path), do the work, upsert the result. Even if two workers run this simultaneously for the same job, the upsert handles the race. The work might run twice (wasteful but acceptable) but the result recorded in the database is consistent.

## The Gotchas

**Idempotency keys need expiration.** You can't store idempotency keys forever — that's a slow memory/storage leak. Set a TTL based on your retry window: if clients retry within 24 hours, keep keys for 48 hours. Clean up expired keys periodically.

**Singleflight suppresses errors too.** If the function passed to `singleflight.Do` returns an error, all waiting goroutines get that error. For transient errors, this is a problem — one bad DB query causes many callers to fail simultaneously rather than retrying individually. You can work around this by checking the error and not caching it, but be aware of the behavior.

**Idempotency is not the same as exactly-once.** Idempotent processing means "running twice produces the same result as running once." It doesn't prevent the operation from running twice — it just makes that safe. External systems you call (like payment processors) need to support idempotency keys of their own, otherwise the external call might still run twice even if your database state is consistent.

**The check-then-act race.** In the payment handler above, there's a window between "check if idempotency key exists" and "insert the result" where two concurrent requests with the same key can both pass the check and both try to process. The `ON CONFLICT DO NOTHING` (or `DO UPDATE`) in the database handles this — it's the actual serialization point. Never rely on the application-level check alone for correctness; rely on database constraints.

**`singleflight` vs a cache.** Singleflight deduplicates concurrent calls — once the call completes, the next call will go to the origin again. It's not a cache. Use singleflight to prevent thundering herds on cache misses; use a real cache (with TTL) to avoid hitting the origin on every request.

## Key Takeaway

Assume everything runs twice — retries, duplicate messages, concurrent workers, user double-clicks. Design your operations to be idempotent: require idempotency keys on mutating HTTP endpoints, use `INSERT ... ON CONFLICT` in your database writes, use `singleflight` to deduplicate concurrent identical calls within a process. Idempotency doesn't prevent duplicates — it makes handling them safe. The check-then-act pattern needs a database constraint as the final serialization point, not just an application-level check. Once your system handles "runs twice" gracefully, you gain the freedom to retry aggressively without fear — and aggressive retries are what make distributed systems resilient.

---

← [Lesson 26: Safe Background Jobs in Web Servers](/post/go/go-concurrency-safe-background-jobs) | [Lesson 28: Production Concurrency Architecture →](/post/go/go-concurrency-production-architecture)
