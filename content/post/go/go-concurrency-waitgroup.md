---
title: 'Lesson 5: sync.WaitGroup — Wait for everyone, then move on'
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 5
keywords:
  - Go
  - golang
  - concurrency
  - sync.WaitGroup
  - WaitGroup
  - errgroup
  - goroutine synchronization
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-05-21T00:00:00.000Z'
---

`sync.WaitGroup` is one of the first concurrency primitives you reach for in Go, and also one of the first you misuse. The API looks deceptively simple — three methods, twenty minutes of reading, you think you've got it. Then you hit a negative counter panic in production, or you find out your `Wait()` returned while goroutines were still running, and you spend an afternoon learning the rules you thought you already knew.

## The Problem

The most common mistake — and the one that's hardest to catch in testing:

```go
// WRONG — Add inside the goroutine races with Wait
func processAll(items []string) {
    var wg sync.WaitGroup

    for _, item := range items {
        item := item
        go func() {
            wg.Add(1)          // TOO LATE — race with Wait
            defer wg.Done()
            process(item)
        }()
    }

    wg.Wait()
}
```

If the scheduler runs all goroutines *after* `wg.Wait()` is reached, `Wait` sees a counter of 0 and returns immediately — before any work is done. In practice, this usually "works" because goroutines tend to get scheduled before `Wait` is reached on a busy system. Tests pass. Production passes. Until load changes, or you run on a different machine, or Go's scheduler version changes, and suddenly you're returning results from zero processing.

Here's the second flavor — the negative counter panic:

```go
// WRONG — calling Done more times than Add
func dispatch(tasks []Task) {
    var wg sync.WaitGroup
    wg.Add(1) // added once for the whole loop — wrong

    for _, task := range tasks {
        task := task
        go func() {
            defer wg.Done() // called len(tasks) times — panic!
            execute(task)
        }()
    }

    wg.Wait()
}
```

`wg.Add(1)` once, `wg.Done()` N times. After the first `Done`, the counter is 0. After the second, it's -1 — immediate panic: `sync: negative WaitGroup counter`. This is usually a typo or a misread of the docs, but the panic message is unhelpful enough that tracking it down takes time.

## The Idiomatic Way

The rules are short:

1. Call `wg.Add(n)` *before* launching the goroutines, in the same goroutine as the `Wait`.
2. Call `wg.Done()` via `defer` as the first statement in the goroutine body.
3. Add matches Done — one Add per goroutine, one Done per goroutine.

```go
// RIGHT — Add before go, Done in defer, counts match
func processAll(items []string) {
    var wg sync.WaitGroup

    wg.Add(len(items)) // set the count before any goroutine starts
    for _, item := range items {
        item := item
        go func() {
            defer wg.Done() // first line — runs even on panic
            process(item)
        }()
    }

    wg.Wait()
}
```

This is safe because `wg.Add` happens in the same goroutine as the loop — no scheduling races. `Wait()` can't return prematurely because the counter is already set to `len(items)` before the first goroutine is started.

For dynamic workloads where you don't know the count upfront, add before each launch — but still before `go`:

```go
// RIGHT — Add before each launch when count isn't known upfront
func processStream(ctx context.Context, items <-chan string) {
    var wg sync.WaitGroup

    for {
        select {
        case item, ok := <-items:
            if !ok {
                wg.Wait() // channel closed — wait for all in-flight work
                return
            }
            item := item
            wg.Add(1) // add BEFORE go — right here
            go func() {
                defer wg.Done()
                process(item)
            }()
        case <-ctx.Done():
            wg.Wait()
            return
        }
    }
}
```

Now here's where `WaitGroup` starts to show its limits. It only answers "are we done yet?" — it doesn't collect errors. For that, `golang.org/x/sync/errgroup` is almost always the better choice:

```go
// RIGHT — errgroup for parallel work with error collection
import "golang.org/x/sync/errgroup"

func fetchAll(ctx context.Context, ids []string) ([]User, error) {
    g, ctx := errgroup.WithContext(ctx)
    users := make([]User, len(ids))

    for i, id := range ids {
        i, id := i, id // capture loop variables
        g.Go(func() error {
            u, err := fetchUser(ctx, id)
            if err != nil {
                return fmt.Errorf("user %s: %w", id, err)
            }
            users[i] = u // safe — each goroutine writes to a distinct index
            return nil
        })
    }

    if err := g.Wait(); err != nil {
        return nil, err
    }
    return users, nil
}
```

`g.Wait()` returns the first non-nil error. The context derived from `errgroup.WithContext` is cancelled when any goroutine returns an error — so all other goroutines that check `ctx.Done()` stop early. You get both error propagation and cancellation for the cost of an import.

`errgroup` with a concurrency limit is the full production pattern:

```go
// RIGHT — errgroup with bounded concurrency
func crawl(ctx context.Context, urls []string) error {
    g, ctx := errgroup.WithContext(ctx)
    g.SetLimit(10) // at most 10 goroutines at a time

    for _, url := range urls {
        url := url
        g.Go(func() error {
            return fetch(ctx, url)
        })
    }

    return g.Wait()
}
```

`SetLimit` blocks `g.Go` when the limit is reached — exactly like a semaphore, but built into the group. No extra channel, no manual tracking.

## In The Wild

We had a batch job that processed user records to generate weekly digest emails. It used a `WaitGroup` and sent errors to a channel for collection. The pattern worked — but collecting errors from a buffered channel afterward was awkward, and we had to size the error channel correctly or risk blocking goroutines.

Switching to `errgroup` removed about 30 lines of boilerplate and made the error handling story clear:

```go
// RIGHT — production batch job with errgroup
func generateDigests(ctx context.Context, userIDs []string) error {
    g, ctx := errgroup.WithContext(ctx)
    g.SetLimit(50) // don't overwhelm the email API

    for _, uid := range userIDs {
        uid := uid
        g.Go(func() error {
            user, err := userStore.Get(ctx, uid)
            if err != nil {
                // Non-critical — log but don't abort the whole batch
                log.Printf("skip user %s: %v", uid, err)
                return nil
            }
            return emailer.SendDigest(ctx, user)
        })
    }

    return g.Wait()
}
```

When the email API started rate-limiting, the `ctx` cancellation from the first failed goroutine propagated to all in-flight ones. The batch stopped cleanly. We logged the error, incremented a metric, and the next run picked up from where we left off — because we tracked completion separately from the goroutines.

## The Gotchas

**Passing a WaitGroup by value.** `sync.WaitGroup` contains internal state that must not be copied — if you pass it by value, the copy and the original have separate counters. `wg.Done()` on the copy decrements the copy's counter; `wg.Wait()` on the original waits forever. Pass by pointer, or better, don't pass it at all — close over it.

**Reusing a WaitGroup before Wait returns.** You can reuse a `WaitGroup` after `Wait` returns — the counter is back to zero. But if you call `Add` before `Wait` finishes, it's a race condition. The docs say you can reuse if "all previous Wait calls have returned," which in practice means: wait for `Wait` to return, then add again. Don't interleave reuse with ongoing waits.

**Using WaitGroup when you need error propagation.** `WaitGroup` is just a counter. If your goroutines can fail, you need something to collect those failures. Sending errors to a buffered channel and draining it after `Wait` works — but `errgroup` does the same thing with less ceremony. Don't reinvent it.

## Key Takeaway

`sync.WaitGroup` has three rules and if you follow them, it never surprises you: Add before go, Done in defer, counts match. But for anything more than "fire off work and wait," reach for `errgroup` — it handles the error case that `WaitGroup` forces you to duct-tape yourself. The two-line switch from `var wg sync.WaitGroup` to `g, ctx := errgroup.WithContext(ctx)` pays for itself the first time something in your concurrent work fails and you don't have to debug a goroutine that swallowed the error.

---

[← Lesson 4: Buffered vs Unbuffered Channels](/post/go/go-concurrency-buffered-vs-unbuffered) | [Course Index](/series/go-concurrency-masterclass) | [Next → Lesson 6: Mutexes Done Right](/post/go/go-concurrency-mutexes)
