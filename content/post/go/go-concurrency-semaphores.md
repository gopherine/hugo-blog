---
title: 'Lesson 15: Semaphores for Concurrency Limits — A channel with a size is a semaphore'
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 15
keywords:
  - Go
  - golang
  - concurrency
  - semaphore
  - channel semaphore
  - rate limiting
  - connection pool
  - golang.org/x/sync
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-09-18T00:00:00.000Z'
---

There's a class of production bugs I see over and over — and the cause is almost always the same: nothing is telling the program to *slow down*. A spike in traffic arrives, every goroutine blasts outbound, and suddenly you've got five hundred simultaneous connections against a Postgres instance that's configured for a hundred. The database starts rejecting connections. The application throws errors. Everyone's paged at 2am.

The fix isn't complicated. It's a semaphore — a primitive that limits how many concurrent operations are in flight at once. Go doesn't ship a dedicated semaphore type, but it doesn't need to. A buffered channel of the right size *is* a semaphore, and that insight unlocks a whole class of resource-limiting patterns.

## The Problem

The naive approach to concurrent work is just — launch everything:

```go
// WRONG — no limit on simultaneous DB queries
func fetchAllUsers(ctx context.Context, db *sql.DB, ids []int) ([]*User, error) {
    results := make([]*User, len(ids))
    var wg sync.WaitGroup

    for i, id := range ids {
        wg.Add(1)
        go func(idx, userID int) {
            defer wg.Done()
            u, err := db.QueryRowContext(ctx, "SELECT * FROM users WHERE id = $1", userID).Scan(...)
            if err == nil {
                results[idx] = u
            }
        }(i, id)
    }
    wg.Wait()
    return results, nil
}
```

If `ids` has ten thousand entries, you've just fired ten thousand concurrent queries. Your connection pool hits its limit inside the first millisecond. Half the goroutines sit blocked waiting for a free connection, and the other half are competing for CPU scheduling. You haven't made things faster — you've made things chaotic.

The second mistake is using timeouts to paper over the lack of limits:

```go
// WRONG — timeouts are not a substitute for backpressure
ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
defer cancel()
// ... same unbounded goroutine launch
```

A timeout tells you when to give up. It doesn't tell your program to pace itself. These are different problems.

## The Idiomatic Way

A buffered channel is a semaphore. Acquiring the semaphore is a send. Releasing it is a receive. If the channel is full, the sender blocks — which is exactly the behavior you want.

```go
// RIGHT — channel-based semaphore limits concurrent DB queries
func fetchAllUsers(ctx context.Context, db *sql.DB, ids []int) ([]*User, error) {
    const maxConcurrent = 20
    sem := make(chan struct{}, maxConcurrent)

    results := make([]*User, len(ids))
    errs := make([]error, len(ids))
    var wg sync.WaitGroup

    for i, id := range ids {
        wg.Add(1)
        go func(idx, userID int) {
            defer wg.Done()

            sem <- struct{}{} // acquire
            defer func() { <-sem }() // release

            u, err := queryUser(ctx, db, userID)
            results[idx] = u
            errs[idx] = err
        }(i, id)
    }

    wg.Wait()

    // consolidate errors
    for _, err := range errs {
        if err != nil {
            return nil, err
        }
    }
    return results, nil
}
```

At most twenty queries run simultaneously. The rest wait their turn. The database never sees more than twenty connections from this code path. Simple, readable, and zero dependencies.

For more complex use cases — like weighted semaphores where some operations count for more than one slot — the standard library extended packages ship `golang.org/x/sync/semaphore`:

```go
// RIGHT — weighted semaphore from golang.org/x/sync
import "golang.org/x/sync/semaphore"

func processImages(ctx context.Context, paths []string) error {
    // each image processing job consumes 2 "slots" worth of resources
    const maxWeight = int64(10)
    sem := semaphore.NewWeighted(maxWeight)

    g, ctx := errgroup.WithContext(ctx)

    for _, p := range paths {
        path := p
        if err := sem.Acquire(ctx, 2); err != nil {
            return err
        }
        g.Go(func() error {
            defer sem.Release(2)
            return resizeImage(ctx, path)
        })
    }

    return g.Wait()
}
```

`semaphore.NewWeighted` is worth reaching for when operations have different costs — a small thumbnail and a raw 40MP TIFF shouldn't consume the same concurrency budget.

## In The Wild

**Limiting outbound HTTP client concurrency** is one of the most common production use cases. You have a downstream API with rate limits or connection caps, and you need your service to stay within them.

```go
type ThrottledClient struct {
    client *http.Client
    sem    chan struct{}
}

func NewThrottledClient(maxConcurrent int) *ThrottledClient {
    return &ThrottledClient{
        client: &http.Client{Timeout: 10 * time.Second},
        sem:    make(chan struct{}, maxConcurrent),
    }
}

func (c *ThrottledClient) Do(req *http.Request) (*http.Response, error) {
    c.sem <- struct{}{}
    defer func() { <-c.sem }()
    return c.client.Do(req)
}
```

Wrap `http.Client.Do` with a semaphore acquire/release and every caller that uses `ThrottledClient` automatically respects the concurrency limit. The callers don't need to know about the limit — it's enforced at the transport layer.

This pattern works for any resource with a capacity ceiling: Redis connection pools, S3 upload concurrency, gRPC stream limits, file descriptor budgets. Anywhere you'd say "no more than N of these at once," a buffered channel gives you the enforcement.

## The Gotchas

**Forgetting to release.** If you acquire the semaphore and then panic or return early without releasing, you've permanently reduced capacity. Always use `defer func() { <-sem }()` immediately after acquiring. The `defer` runs even when the function panics.

**Sizing the semaphore wrong.** A semaphore with `maxConcurrent = 1` is a mutex — all operations are serialized. That's correct in some cases but kills throughput in others. Pick a number that matches the downstream resource's actual capacity. If your DB pool has `MaxOpenConns = 25`, set your semaphore to 20–22 to leave headroom for health checks and other code paths.

**Context cancellation with the channel pattern.** The naive send `sem <- struct{}{}` blocks indefinitely. If the context is cancelled, you'll be stuck:

```go
// WRONG — ignores context cancellation while waiting to acquire
sem <- struct{}{}
```

```go
// RIGHT — respects context cancellation
select {
case sem <- struct{}{}:
    defer func() { <-sem }()
case <-ctx.Done():
    return ctx.Err()
}
```

This is the main reason to reach for `golang.org/x/sync/semaphore` in production — its `Acquire` method accepts a context and returns an error if the context is cancelled before the semaphore can be acquired.

**Semaphores don't queue fairly.** A channel semaphore doesn't guarantee first-in-first-out ordering. If ten goroutines are waiting, one of them gets picked when a slot opens — but which one is nondeterministic. For most use cases this doesn't matter. If fair ordering is a requirement, you need a different data structure.

## Key Takeaway

Any time you launch goroutines against a resource with finite capacity — a database, an HTTP API, a message queue, a file system — you need a concurrency limit. A buffered channel is the simplest way to enforce one: acquire with a send, release with a receive, and `defer` the release so it's never skipped. For context-aware limits or weighted operations, `golang.org/x/sync/semaphore` extends the pattern cleanly. The question isn't whether you need this — you do. The question is just whether you'll add it before or after the 2am incident.

---

← [Previous: Error Groups](/post/go/go-concurrency-errgroup) | [Course Index](/post/go/) | [Next: Atomic Operations](/post/go/go-concurrency-atomics) →
