---
title: "Lesson 6: Swallowing Errors — The silent failure that cost us 3 hours"
author: Atharva Pandey
series: "Go Anti-Patterns & Code Smells"
lesson: 6
keywords:
  - Go
  - golang
  - anti-patterns
  - code quality
tags:
  - Go tutorial
  - golang
  - code quality
date: '2025-01-12T00:00:00.000Z'
---

We had a deployment where user preferences stopped being saved. New preferences were accepted by the API — the endpoint returned 200 — but nothing was written to the database. After three hours of debugging we found it: a `defer rows.Close()` inside a transaction helper that was discarding the error from `tx.Commit()`. The commit was failing silently, the defer returned without error, and the handler sent a success response. The only indication anything was wrong was a metrics counter nobody had set up an alert for.

Swallowing errors in Go is particularly dangerous because the language's error handling model depends on explicit return values. When you discard an error — by assigning it to the blank identifier, logging it without acting on it, or ignoring it in a deferred call — you remove it from the control flow. The calling code has no way to know something went wrong.

## The Problem

The blank identifier discard — the most visible form:

```go
// WRONG — error discarded with blank identifier
func saveUserPreferences(db *sql.DB, userID string, prefs Preferences) {
    data, _ := json.Marshal(prefs)   // marshal error swallowed
    _, _ = db.Exec(                  // database error swallowed
        "UPDATE users SET prefs = $1 WHERE id = $2",
        data, userID,
    )
    // function returns without any indication of failure
}
```

The caller thinks preferences were saved. They were not. The caller has no way to know.

Logging without returning is the subtler anti-pattern — it feels like you handled the error because you recorded it:

```go
// WRONG — logging is not error handling
func (s *OrderService) CancelOrder(ctx context.Context, orderID string) error {
    if err := s.db.UpdateStatus(ctx, orderID, "cancelled"); err != nil {
        log.Printf("error updating order status: %v", err)
        // forgot to return here — execution continues
    }

    // This runs even when UpdateStatus failed
    if err := s.mailer.SendCancellationEmail(ctx, orderID); err != nil {
        log.Printf("error sending email: %v", err)
    }

    return nil // always returns nil, even on failure
}
```

Errors in deferred calls are silently dropped unless you explicitly capture them:

```go
// WRONG — deferred close error is silently discarded
func writeFile(path string, content []byte) error {
    f, err := os.Create(path)
    if err != nil {
        return err
    }
    defer f.Close() // error from Close() is discarded

    _, err = f.Write(content)
    return err
    // If Write succeeds but Close fails (flush error), we return nil — the file may be corrupt
}
```

## The Idiomatic Way

Return every error to the caller and let the caller decide what to do. Wrap errors with context so the failure is understandable at every level:

```go
// RIGHT — all errors returned with context
func saveUserPreferences(ctx context.Context, db *sql.DB, userID string, prefs Preferences) error {
    data, err := json.Marshal(prefs)
    if err != nil {
        return fmt.Errorf("marshal preferences: %w", err)
    }

    _, err = db.ExecContext(ctx,
        "UPDATE users SET prefs = $1 WHERE id = $2",
        data, userID,
    )
    if err != nil {
        return fmt.Errorf("save preferences for user %s: %w", userID, err)
    }

    return nil
}
```

For the deferred close pattern, capture the error and merge it with the function's return error:

```go
// RIGHT — deferred close error is captured and returned
func writeFile(path string, content []byte) (retErr error) {
    f, err := os.Create(path)
    if err != nil {
        return fmt.Errorf("create file: %w", err)
    }
    defer func() {
        if closeErr := f.Close(); closeErr != nil && retErr == nil {
            retErr = fmt.Errorf("close file: %w", closeErr)
        }
    }()

    if _, err := f.Write(content); err != nil {
        return fmt.Errorf("write content: %w", err)
    }

    return nil
}
```

The named return value `retErr` lets the deferred function assign to the function's return value. The close error only overwrites `retErr` if `retErr` is nil — if `Write` already failed, the write error is more informative.

For errors you genuinely cannot propagate — background goroutines, fire-and-forget operations — log them with enough context to be actionable, and count them in metrics:

```go
// RIGHT — background error handling with logging and metrics
go func() {
    if err := s.cache.Warm(context.Background()); err != nil {
        s.logger.Error("cache warm failed",
            "error", err,
            "component", "cache",
        )
        s.metrics.IncrementCounter("cache.warm.failure")
        // do NOT swallow — at minimum log + metric so it shows up in alerts
    }
}()
```

## In The Wild

**`errgroup` for parallel error collection.** When running multiple operations concurrently, `errgroup` collects errors from all goroutines rather than requiring you to manually aggregate:

```go
g, ctx := errgroup.WithContext(context.Background())

g.Go(func() error {
    return step1(ctx)
})
g.Go(func() error {
    return step2(ctx)
})

if err := g.Wait(); err != nil {
    return fmt.Errorf("parallel steps: %w", err) // first error from any goroutine
}
```

**Sentinel errors and `errors.Is`.** When callers need to distinguish between error types, define sentinel errors and use `fmt.Errorf("...: %w", err)` to wrap them so `errors.Is` works through the chain:

```go
var ErrNotFound = errors.New("not found")

func getUser(db *sql.DB, id string) (*User, error) {
    var u User
    err := db.QueryRow("SELECT ...").Scan(&u)
    if errors.Is(err, sql.ErrNoRows) {
        return nil, fmt.Errorf("user %s: %w", id, ErrNotFound)
    }
    if err != nil {
        return nil, fmt.Errorf("query user: %w", err)
    }
    return &u, nil
}
```

**Transaction commit errors.** The specific case from my story: always check the error from `tx.Commit()`. Deferring only `tx.Rollback()` and not capturing the `Commit()` error is a common pattern that hides silent failures:

```go
// RIGHT — explicit commit error handling
tx, err := db.BeginTx(ctx, nil)
if err != nil {
    return err
}
defer tx.Rollback() // rollback is a no-op if tx is already committed

// ... operations ...

if err := tx.Commit(); err != nil {
    return fmt.Errorf("commit transaction: %w", err)
}
return nil
```

## The Gotchas

**`linters` that catch swallowed errors.** `errcheck` is a linter that flags every unchecked error in your codebase. Integrate it in CI. The output is noisy at first — especially in test files — but the findings in production code are almost always real bugs. `golangci-lint` includes it in its default set.

**Retry vs fail.** Some errors are transient — network timeouts, temporary database unavailability. Some are permanent — constraint violations, invalid input. Do not retry permanent errors; you will just fail repeatedly and waste time. Use `errors.Is` to distinguish and only retry errors that make sense to retry.

**Panic in goroutines.** An unrecovered panic in a goroutine crashes the entire process. A goroutine that panics on an unexpected nil pointer is effectively an unhandled error. Add a recover wrapper to long-running background goroutines and convert panics to logged errors or metrics:

```go
func safeGo(name string, fn func()) {
    go func() {
        defer func() {
            if r := recover(); r != nil {
                log.Printf("panic in goroutine %s: %v\n%s", name, r, debug.Stack())
            }
        }()
        fn()
    }()
}
```

## Key Takeaway

Every error that crosses a function boundary is a message from the runtime to your code about what went wrong. Discarding it with `_`, logging it without returning, or letting it fall through deferred cleanup breaks the error propagation chain. The caller cannot handle a problem it does not know about. Wrap errors with context, return them to the caller, and handle or log only at the top of the call stack — not in the middle of it.

---

**Go Anti-Patterns & Code Smells**

Previous: [Lesson 5: Ignoring Context — The cancellation nobody checked](/post/go/go-anti-ignoring-context/)
Next: [Lesson 7: Panic as Error Handling — Panic is for bugs, not business logic](/post/go/go-anti-panic-misuse/)
