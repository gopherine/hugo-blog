---
title: "Lesson 5: Ignoring Context — The cancellation nobody checked"
author: Atharva Pandey
series: "Go Anti-Patterns & Code Smells"
lesson: 5
keywords:
  - Go
  - golang
  - anti-patterns
  - code quality
tags:
  - Go tutorial
  - golang
  - code quality
date: '2024-11-22T00:00:00.000Z'
---

I spent a Tuesday afternoon tracking down why our service continued doing expensive database work after clients had long since disconnected. An HTTP client with a 3-second timeout would cancel the request, but our handler would still run three downstream database queries that together took 8 seconds. The handler was checking for errors but never checking the context. By the time it finished, the client had retried twice, and we were now running three copies of the same 8-second work simultaneously.

Context propagation is one of those things in Go where the plumbing is mostly correct — people pass `ctx` as the first argument to functions, they pass `r.Context()` into service calls — but the actual checking is often missing. Passing a context that nobody ever checks is theater. The cancellation signal goes nowhere.

## The Problem

Accepting a context parameter but never checking it for cancellation:

```go
// WRONG — context accepted but never checked
func (s *OrderService) ProcessOrder(ctx context.Context, orderID string) error {
    // Step 1: validate
    order, err := s.db.FindOrder(ctx, orderID)
    if err != nil {
        return err
    }

    // Step 2: charge payment — but what if ctx is already cancelled?
    if err := s.payments.Charge(order.UserID, order.Amount); err != nil {
        return err
    }

    // Step 3: send confirmation email — client disconnected 5 seconds ago
    if err := s.mailer.SendConfirmation(order.UserEmail); err != nil {
        return err
    }

    // Step 4: update inventory — wasted work, client already gave up
    return s.inventory.Decrement(ctx, order.Items)
}
```

Here, `s.db.FindOrder` passes the context and will respect cancellation. But `s.payments.Charge` and `s.mailer.SendConfirmation` do not accept a context at all — so when the HTTP client cancels after 3 seconds, `ProcessOrder` continues running steps 2 and 3 regardless.

A second mistake is creating a new background context instead of propagating the one you received:

```go
// WRONG — creating a new context loses cancellation from the caller
func (s *UserService) GetUser(ctx context.Context, id string) (*User, error) {
    // ctx carries the caller's deadline and cancellation
    // but we throw it away and use Background instead
    return s.db.QueryUser(context.Background(), id) // won't be cancelled
}
```

The caller's deadline — from an HTTP request timeout or a parent context with `WithTimeout` — is silently discarded. Your database query will run to completion even after the caller gives up.

## The Idiomatic Way

Check `ctx.Done()` at each significant step, especially before expensive operations:

```go
// RIGHT — context checked between steps
func (s *OrderService) ProcessOrder(ctx context.Context, orderID string) error {
    order, err := s.db.FindOrder(ctx, orderID)
    if err != nil {
        return err
    }

    // Check before the expensive payment call
    if err := ctx.Err(); err != nil {
        return fmt.Errorf("order processing cancelled before payment: %w", err)
    }

    if err := s.payments.Charge(ctx, order.UserID, order.Amount); err != nil {
        return err
    }

    if err := ctx.Err(); err != nil {
        return fmt.Errorf("order processing cancelled before email: %w", err)
    }

    if err := s.mailer.SendConfirmation(ctx, order.UserEmail); err != nil {
        return err
    }

    return s.inventory.Decrement(ctx, order.Items)
}
```

`ctx.Err()` returns `context.Canceled` or `context.DeadlineExceeded` if the context is done, nil otherwise. This is cheaper than a channel select and appropriate for sequential steps.

For long-running loops or processing pipelines, check in the loop body:

```go
// RIGHT — context check in a processing loop
func (s *ReportService) GenerateReport(ctx context.Context, userIDs []string) ([]Report, error) {
    reports := make([]Report, 0, len(userIDs))

    for _, id := range userIDs {
        // Check cancellation before each iteration
        select {
        case <-ctx.Done():
            return nil, fmt.Errorf("report generation cancelled after %d users: %w", len(reports), ctx.Err())
        default:
        }

        report, err := s.buildReport(ctx, id)
        if err != nil {
            return nil, err
        }
        reports = append(reports, report)
    }

    return reports, nil
}
```

Always propagate the context from the caller — never create `context.Background()` inside a function that received a context:

```go
// RIGHT — propagate the received context
func (s *UserService) GetUser(ctx context.Context, id string) (*User, error) {
    return s.db.QueryUser(ctx, id) // ctx, not context.Background()
}
```

## In The Wild

**HTTP handler context.** `r.Context()` is cancelled when the client disconnects or the `http.Server`'s `WriteTimeout` fires. Pass it to every downstream call. If your handler calls a service that calls a repository that makes a database query, `ctx` should flow all the way to the `sql.DB.QueryContext` call. A context that is passed to service layer but not to the database layer gives you partial cancellation.

**`context.WithTimeout` for bounded operations.** Impose deadlines on work whose duration you own:

```go
// RIGHT — impose a deadline on a sub-operation
func (s *SearchService) Search(ctx context.Context, query string) ([]Result, error) {
    // Ensure search never takes more than 2 seconds regardless of caller's deadline
    ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
    defer cancel()

    return s.index.Query(ctx, query)
}
```

`context.WithTimeout` derives from the parent — if the parent already has a shorter deadline, the shorter one wins. The derived context adds an additional constraint.

**goroutines and context.** When you launch a goroutine inside a context-aware function, pass the context into it:

```go
// RIGHT — context passed to goroutine
func (s *Service) DoAsync(ctx context.Context, id string) {
    go func() {
        if err := s.worker.Process(ctx, id); err != nil {
            // handle cancellation or error
            if !errors.Is(err, context.Canceled) {
                s.logger.Error("async processing failed", "error", err)
            }
        }
    }()
}
```

## The Gotchas

**Post-cancellation cleanup.** Some operations should complete even after cancellation — writing to a log, incrementing a metrics counter, sending a final status update. For these, derive a new background context, do not use the cancelled one:

```go
func cleanup(ctx context.Context, id string) {
    // ctx may be cancelled — use a new context for cleanup
    cleanupCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
    defer cancel()
    s.auditLog.Write(cleanupCtx, id, "cancelled")
}
```

**`context.Value` is not a bag of globals.** Storing large objects, dependencies, or mutable state in context values is a misuse of the API. Context values are for request-scoped data that crosses API boundaries — trace IDs, authenticated users, request IDs. Dependencies belong in struct fields.

**Detecting context cancellation in `select`.** When a goroutine is waiting on multiple channels, include `ctx.Done()` in the `select`:

```go
select {
case result := <-resultCh:
    return result, nil
case <-ctx.Done():
    return nil, ctx.Err()
}
```

Without the `ctx.Done()` case, a goroutine blocked on `resultCh` will be leaked if the context is cancelled before `resultCh` receives a value.

## Key Takeaway

Passing a context and checking a context are two different things. Passing context through the call chain is the mechanical part — most Go developers do this. Checking `ctx.Err()` between significant operations, and responding to `ctx.Done()` in blocking calls, is the part that actually makes cancellation work. A context that flows through every layer but is never checked is decoration, not behaviour.

---

**Go Anti-Patterns & Code Smells**

Previous: [Lesson 4: Channel Misuse — You used a channel where a mutex would do](/post/go/go-anti-channel-misuse/)
Next: [Lesson 6: Swallowing Errors — The silent failure that cost us 3 hours](/post/go/go-anti-swallowing-errors/)
