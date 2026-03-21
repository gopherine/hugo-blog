---
title: 'Lesson 14: errgroup for Structured Concurrency — All succeed or all cancel'
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 14
keywords:
  - Go
  - golang
  - concurrency
  - errgroup
  - structured concurrency
  - golang.org/x/sync
  - WithContext
  - SetLimit
  - API aggregation
  - error handling
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-09-02T00:00:00.000Z'
---

There's a pattern that comes up constantly in backend services: make N concurrent calls, collect all their results, and if any one of them fails, cancel the rest and return the error. This is the "all succeed or all cancel" pattern — and before `errgroup`, implementing it correctly required a non-trivial amount of boilerplate involving `WaitGroup`, error channels, and manual context cancellation. People got it wrong often enough that `errgroup` was created specifically to handle it.

`errgroup` is in `golang.org/x/sync/errgroup`. It's not in the standard library, but it's maintained by the Go team and I treat it as standard. If you're not using it for fan-out with error collection, you're probably reinventing it badly.

## The Problem

The manual approach to concurrent calls with error collection is verbose and fragile:

```go
// WRONG — manual approach, easy to get wrong
func fetchDashboard(userID string) (*Dashboard, error) {
    var (
        profile *Profile
        orders  []*Order
        notifs  []*Notification
        profileErr, ordersErr, notifsErr error
    )

    var wg sync.WaitGroup

    wg.Add(1)
    go func() {
        defer wg.Done()
        profile, profileErr = getProfile(userID)
    }()

    wg.Add(1)
    go func() {
        defer wg.Done()
        orders, ordersErr = getOrders(userID)
    }()

    wg.Add(1)
    go func() {
        defer wg.Done()
        notifs, notifsErr = getNotifications(userID)
    }()

    wg.Wait()

    // Which error do we return? What if multiple fail?
    if profileErr != nil {
        return nil, profileErr
    }
    if ordersErr != nil {
        return nil, ordersErr
    }
    if notifsErr != nil {
        return nil, notifsErr
    }

    return &Dashboard{Profile: profile, Orders: orders, Notifications: notifs}, nil
}
```

Four things are wrong here. First, if `getProfile` fails, `getOrders` and `getNotifications` keep running — wasting resources on work whose results we'll discard. Second, multiple errors can occur but we only surface one (and not necessarily the most meaningful one). Third, there's no context propagation — these calls can't be cancelled if the outer request is cancelled. Fourth, the error check waterfall at the end is a maintenance hazard — add a fourth call and you have to remember to add a fourth error check.

The error channel approach is better but still messy:

```go
// STILL WRONG — error channel version, still no cancellation propagation
func fetchDashboard(userID string) (*Dashboard, error) {
    errs := make(chan error, 3)
    // ... launch goroutines that send to errs ...
    var wg sync.WaitGroup
    // collect errors
    go func() {
        wg.Wait()
        close(errs)
    }()
    for err := range errs {
        if err != nil {
            return nil, err // other goroutines still running!
        }
    }
    // ...
}
```

Returning on the first error while other goroutines are still in flight — that's a goroutine leak.

## The Idiomatic Way

`errgroup.WithContext` is the correct tool. It gives you a group that:
- Runs goroutines concurrently
- Waits for all of them via `g.Wait()`
- Returns the first non-nil error
- Cancels the shared context when any goroutine returns an error

```go
// RIGHT — errgroup with shared context cancellation
import "golang.org/x/sync/errgroup"

func fetchDashboard(ctx context.Context, userID string) (*Dashboard, error) {
    g, gctx := errgroup.WithContext(ctx)

    var (
        profile *Profile
        orders  []*Order
        notifs  []*Notification
    )

    g.Go(func() error {
        var err error
        profile, err = getProfile(gctx, userID)
        return err
    })

    g.Go(func() error {
        var err error
        orders, err = getOrders(gctx, userID)
        return err
    })

    g.Go(func() error {
        var err error
        notifs, err = getNotifications(gctx, userID)
        return err
    })

    if err := g.Wait(); err != nil {
        return nil, fmt.Errorf("fetch dashboard for %s: %w", userID, err)
    }

    return &Dashboard{
        Profile:       profile,
        Orders:        orders,
        Notifications: notifs,
    }, nil
}
```

This is dramatically cleaner. `g.Go` launches each goroutine. `g.Wait()` blocks until all complete and returns the first non-nil error. The `gctx` context is cancelled the moment any goroutine returns an error — so `getOrders` and `getNotifications` will have their contexts cancelled and can exit early if `getProfile` fails. No manual `WaitGroup`, no error channels, no leaked goroutines.

Now add `SetLimit` for when you're fanning out over a slice — this is the errgroup version of a worker pool:

```go
// RIGHT — errgroup with concurrency limit for processing a slice
func enrichProducts(ctx context.Context, products []Product) ([]EnrichedProduct, error) {
    results := make([]EnrichedProduct, len(products))

    g, gctx := errgroup.WithContext(ctx)
    g.SetLimit(20) // at most 20 goroutines at a time

    for i, p := range products {
        i, p := i, p // capture for Go < 1.22
        g.Go(func() error {
            enriched, err := enrichProduct(gctx, p)
            if err != nil {
                return fmt.Errorf("enrich product %s: %w", p.ID, err)
            }
            results[i] = enriched // safe: each goroutine writes to a unique index
            return nil
        })
    }

    if err := g.Wait(); err != nil {
        return nil, err
    }
    return results, nil
}
```

`SetLimit(20)` means `g.Go` will block if 20 goroutines are already running. The caller doesn't need a separate jobs channel — `errgroup` handles the bounded concurrency internally. Writing to `results[i]` is safe here because each goroutine writes to a unique slice index (no two goroutines share the same `i`). This is one of the rare safe cases of "concurrent writes to a slice."

The index-based write trick only works when each item maps to a distinct index. For variable-length results (filtering, aggregating), collect into a mutex-protected slice or a channel:

```go
// RIGHT — errgroup with result collection via mutex when indices don't work
func searchAll(ctx context.Context, query string, sources []SearchSource) ([]Result, error) {
    var (
        mu      sync.Mutex
        results []Result
    )

    g, gctx := errgroup.WithContext(ctx)

    for _, src := range sources {
        src := src
        g.Go(func() error {
            r, err := src.Search(gctx, query)
            if err != nil {
                return fmt.Errorf("source %s: %w", src.Name(), err)
            }
            mu.Lock()
            results = append(results, r...)
            mu.Unlock()
            return nil
        })
    }

    if err := g.Wait(); err != nil {
        return nil, err
    }
    return results, nil
}
```

## In The Wild

The most compelling use case I've had for errgroup is a dashboard aggregation service that had to call seven different microservices — user profile, recent activity, notification count, subscription status, billing summary, recommendation feed, and A/B test assignments. These were all independent. They all needed to complete before the dashboard could render.

Before errgroup, the code was a tangle of channels and WaitGroups. After:

```go
func (s *DashboardService) GetDashboard(ctx context.Context, userID string) (*Dashboard, error) {
    ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
    defer cancel()

    g, gctx := errgroup.WithContext(ctx)

    var d Dashboard

    g.Go(func() error {
        var err error
        d.User, err = s.userSvc.GetUser(gctx, userID)
        return err
    })
    g.Go(func() error {
        var err error
        d.Activity, err = s.activitySvc.GetRecent(gctx, userID)
        return err
    })
    g.Go(func() error {
        var err error
        d.NotifCount, err = s.notifSvc.GetUnreadCount(gctx, userID)
        return err
    })
    g.Go(func() error {
        var err error
        d.Subscription, err = s.subSvc.GetStatus(gctx, userID)
        return err
    })
    g.Go(func() error {
        var err error
        d.Billing, err = s.billingSvc.GetSummary(gctx, userID)
        return err
    })
    g.Go(func() error {
        var err error
        d.Recommendations, err = s.recSvc.GetFeed(gctx, userID)
        return err
    })
    g.Go(func() error {
        var err error
        d.Experiments, err = s.abSvc.GetAssignments(gctx, userID)
        return err
    })

    if err := g.Wait(); err != nil {
        return nil, fmt.Errorf("dashboard for user %s: %w", userID, err)
    }
    return &d, nil
}
```

Seven concurrent calls, 5-second total timeout (via `context.WithTimeout`), one error returned if any fail, the others cancelled immediately. The p99 latency of this endpoint matches the slowest individual service call — not the sum of all seven. Before the rewrite it was doing these sequentially; the median latency dropped from ~1.4s to ~220ms.

## The Gotchas

**Writing to the same slice index from multiple goroutines.** The index-based write pattern is only safe when each goroutine has a unique index. If you're not sure, use a mutex or result channel instead. The race detector will catch this — run tests with `-race`.

**`errgroup.WithContext` cancels on the first error — make sure your goroutines respect it.** If a goroutine ignores the context and keeps running after another goroutine has failed, you lose the "cancel early" benefit. Pass `gctx` to every blocking call inside `g.Go` functions.

**Not wrapping errors with context.** When seven calls can fail, a bare `"connection refused"` in the error log is useless. Wrap with `fmt.Errorf("service X: %w", err)` so you know which call failed. The dashboard example above does this in the outer `fmt.Errorf` — for individual goroutines, wrap there too.

**Using `errgroup.Group` (without `WithContext`) when you need cancellation.** `errgroup.Group` exists for cases where you just want concurrent goroutines with error collection but don't need shared cancellation. It's fine for independent tasks that don't need to be cancelled if one fails. But if any failure should abort the others, you need `WithContext`.

**Closing over a loop variable in Go < 1.22.** The `i, p := i, p` capture before `g.Go` is mandatory in Go versions before 1.22. In Go 1.22+, loop variables are per-iteration and don't need this. Check your `go.mod` minimum version.

## Key Takeaway

`errgroup` is the right abstraction for "run N things concurrently, fail fast if any fail." It replaces the WaitGroup + error channel + manual context cancellation pattern with something that's impossible to get wrong. Use `WithContext` for shared cancellation, `SetLimit` for bounded concurrency over a slice, and always pass the group's context into every blocking call inside `g.Go`. The "all succeed or all cancel" contract makes error handling in concurrent code as clean as it gets in Go.

---

← [Previous: Timeouts Everywhere](/post/go/go-concurrency-timeouts) | [Course Index](/post/go/) | [Next: Semaphores](/post/go/go-concurrency-semaphores) →
