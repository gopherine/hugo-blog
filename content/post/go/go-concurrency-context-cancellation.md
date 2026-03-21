---
title: 'Lesson 2: Cancellation with context.Context — Every goroutine needs a kill switch'
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 2
keywords:
  - Go
  - golang
  - concurrency
  - context
  - context cancellation
  - WithCancel
  - WithTimeout
  - Done channel
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-04-13T00:00:00.000Z'
---

Without `context.Context`, you have goroutines running long after the user who triggered them gave up, HTTP connections burning server resources for requests nobody's waiting for, and database queries executing on behalf of clients that disconnected ten seconds ago. Context is the mechanism Go gives you to propagate "I changed my mind" — and most engineers don't use it until something in production embarrasses them into learning it properly.

## The Problem

Let's start with the most common mistake — blocking on a slow operation with no timeout:

```go
// WRONG — no timeout, no cancellation, blocks indefinitely
func GetUser(userID string) (*User, error) {
    row := db.QueryRow("SELECT * FROM users WHERE id = $1", userID)
    var u User
    if err := row.Scan(&u.ID, &u.Name, &u.Email); err != nil {
        return nil, err
    }
    return &u, nil
}
```

If the database goes away — network partition, overloaded replica, whatever — this function blocks until the OS-level TCP timeout fires. That can be minutes. Meanwhile, the goroutine that called it is stuck, the HTTP handler behind it is stuck, and more requests keep piling up behind that.

Here's the second flavor — propagating context but never checking if it's done:

```go
// WRONG — context accepted but ignored inside the loop
func ProcessItems(ctx context.Context, items []Item) error {
    for _, item := range items {
        // This could take forever if items is huge.
        // If ctx is cancelled, we don't notice until it's done.
        result, err := expensiveOperation(item)
        if err != nil {
            return err
        }
        saveResult(result)
    }
    return nil
}
```

Accepting `ctx` as a parameter without actually checking `ctx.Done()` is theater. It looks correct from the outside — the function signature suggests cancellation is supported — but it isn't. If the caller cancels, the loop runs to completion anyway.

## The Idiomatic Way

The core idiom is straightforward: pass context everywhere, check `ctx.Done()` at every blocking point or long-running loop iteration, and cancel contexts when you're done with them — always using `defer cancel()`.

```go
// RIGHT — context flows through, database query respects cancellation
func GetUser(ctx context.Context, userID string) (*User, error) {
    row := db.QueryRowContext(ctx, "SELECT * FROM users WHERE id = $1", userID)
    var u User
    if err := row.Scan(&u.ID, &u.Name, &u.Email); err != nil {
        return nil, err
    }
    return &u, nil
}

// Caller sets a deadline so the whole operation is bounded:
func handleGetUser(w http.ResponseWriter, r *http.Request) {
    ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
    defer cancel() // ALWAYS defer cancel — prevents context leak

    user, err := GetUser(ctx, r.URL.Query().Get("id"))
    if err != nil {
        if errors.Is(err, context.DeadlineExceeded) {
            http.Error(w, "upstream timeout", http.StatusGatewayTimeout)
            return
        }
        http.Error(w, "internal error", http.StatusInternalServerError)
        return
    }
    json.NewEncoder(w).Encode(user)
}
```

The `defer cancel()` after `WithTimeout` isn't optional. Even if the timeout fires and the context is already cancelled, calling `cancel()` releases the internal timer and the resources associated with the context tree node. Skip it and you leak — slowly, but surely.

For long loops, check the context on each iteration:

```go
// RIGHT — loop respects cancellation
func ProcessItems(ctx context.Context, items []Item) error {
    for _, item := range items {
        // Check before doing expensive work
        select {
        case <-ctx.Done():
            return ctx.Err()
        default:
        }

        result, err := expensiveOperation(item)
        if err != nil {
            return err
        }
        saveResult(result)
    }
    return nil
}
```

The `select` with `default` is a non-blocking check — it doesn't pause the loop, it just peeks at the done channel. If it's closed, we bail. If not, we proceed. This is the pattern.

Here's `WithCancel` for cases where you need manual control — not a timeout, but the ability to cancel from a parent when any sibling fails:

```go
// RIGHT — cancel all workers if any one of them fails
func RunPipeline(ctx context.Context, stages []Stage) error {
    ctx, cancel := context.WithCancel(ctx)
    defer cancel()

    errCh := make(chan error, len(stages))

    for _, stage := range stages {
        stage := stage // capture loop variable
        go func() {
            if err := stage.Run(ctx); err != nil {
                errCh <- err
                cancel() // signal all other goroutines to stop
            }
        }()
    }

    // Wait and collect first error
    for range stages {
        if err := <-errCh; err != nil {
            return err
        }
    }
    return nil
}
```

## In The Wild

A microservice I worked on called three downstream APIs to assemble a response. Originally, each call was independent — if one was slow, the others waited. We refactored to use `context.WithTimeout` at the handler level and fan out the three calls in parallel, each sharing that context.

```go
// RIGHT — parallel downstream calls that all respect the same deadline
func assembleResponse(ctx context.Context, userID string) (*Response, error) {
    ctx, cancel := context.WithTimeout(ctx, 500*time.Millisecond)
    defer cancel()

    var (
        profile   Profile
        prefs     Preferences
        recents   []Activity
        profileErr, prefsErr, recentsErr error
        wg sync.WaitGroup
    )

    wg.Add(3)
    go func() { defer wg.Done(); profile, profileErr = profileSvc.Get(ctx, userID) }()
    go func() { defer wg.Done(); prefs, prefsErr = prefsSvc.Get(ctx, userID) }()
    go func() { defer wg.Done(); recents, recentsErr = activitySvc.Recent(ctx, userID) }()
    wg.Wait()

    if profileErr != nil {
        return nil, fmt.Errorf("profile: %w", profileErr)
    }
    // prefs and recents are non-critical — log and continue
    if prefsErr != nil {
        log.Printf("prefs unavailable for %s: %v", userID, prefsErr)
    }
    return buildResponse(profile, prefs, recents), nil
}
```

When the 500ms deadline fires, all three in-flight HTTP calls get cancelled simultaneously — the ones that used `NewRequestWithContext` respect it immediately. p99 latency dropped by 40% on the problematic days because we stopped waiting for the one sluggish call to drag down the whole response.

## The Gotchas

**`context.TODO` and `context.Background` in application code.** `context.Background()` is the right root for main, tests, and top-level server setups — places where there genuinely is no parent context. `context.TODO` is supposed to be a placeholder that you replace with a real context — but I've watched it calcify into permanent code in production systems. If you find yourself reaching for `context.TODO` in a function that's called during request handling, stop and thread the real context through instead.

**Storing context in a struct.** The Go team explicitly discourages this. Context is a request-scoped value — it should live on the stack and flow through function parameters. Stuffing it into a struct makes it invisible to callers and leads to cancelled contexts being used long after the request ended.

**Ignoring the error from `ctx.Err()`.** When a context-aware function returns an error, check if it's `context.Canceled` or `context.DeadlineExceeded` before logging it as an internal error. A cancelled request isn't a 500 — it's a client decision. Logging it as an error inflates your error rate and degrades your alerting signal.

## Key Takeaway

Context isn't just a Go convention — it's the only standard mechanism the language gives you to thread cancellation signals through an entire call chain without coupling every layer to every other. Use `WithTimeout` at entry points (HTTP handlers, gRPC handlers, cron jobs), `WithCancel` when you need to cancel a group of goroutines programmatically, always defer cancel right after creating a derived context, and propagate it through every function that does I/O. The pattern is verbose, but that verbosity is intentional — it forces you to think about cancellation at every level, which is exactly the discipline that keeps production systems from falling over.

---

[← Lesson 1: Goroutine Lifecycle Management](/post/go/go-concurrency-goroutine-lifecycle) | [Course Index](/series/go-concurrency-masterclass) | [Next → Lesson 3: Channel Ownership Rules](/post/go/go-concurrency-channel-ownership)
