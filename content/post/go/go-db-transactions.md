---
title: "Lesson 3: Transactions That Don't Bite — Begin, defer rollback, commit"
author: Atharva Pandey
series: "Go Database Patterns"
lesson: 3
keywords:
  - Go
  - golang
  - database
  - postgres
  - transactions
  - sql.Tx
  - savepoints
  - isolation levels
tags:
  - Go tutorial
  - golang
  - database
date: '2025-09-07T00:00:00.000Z'
---

Transactions are the part of database programming where "it's fine most of the time" really isn't good enough. A buggy SELECT just returns wrong data. A buggy transaction can leave your database in a half-written state — an order placed without inventory decremented, money debited without the transfer completing, a user created without their profile record. The bugs are subtle, often don't manifest in testing, and only show up in production when two things happen at the same time.

The good news: Go's transaction pattern is clean once you understand it, and there's a small set of rules that eliminate most of the ways to get it wrong.

## The Problem

The naive approach to transactions is to call `Begin`, do your work, and call `Commit`:

```go
// WRONG — no rollback on error, connection leak if Commit panics
func TransferFunds(db *sql.DB, fromID, toID int, amount int) error {
    tx, err := db.Begin()
    if err != nil {
        return err
    }

    _, err = tx.Exec("UPDATE accounts SET balance = balance - $1 WHERE id = $2", amount, fromID)
    if err != nil {
        // Oops — forgot to rollback. Transaction is now open, connection held.
        return err
    }

    _, err = tx.Exec("UPDATE accounts SET balance = balance + $1 WHERE id = $2", amount, toID)
    if err != nil {
        // Same problem here.
        return err
    }

    return tx.Commit()
}
```

If the second UPDATE fails and we return the error, the transaction is abandoned — but it's not rolled back. The connection returns to the pool still holding an open transaction. When the next caller picks up that connection and tries to do something, they're inside a transaction they didn't start. This is a subtle, nasty bug that's very hard to trace.

There's another version of this that looks defensive but isn't:

```go
// WRONG — rollback logic is duplicated and easy to miss
func TransferFunds(db *sql.DB, fromID, toID int, amount int) error {
    tx, err := db.Begin()
    if err != nil {
        return err
    }

    _, err = tx.Exec("UPDATE accounts SET balance = balance - $1 WHERE id = $2", amount, fromID)
    if err != nil {
        tx.Rollback() // manually handled — but...
        return err
    }

    _, err = tx.Exec("UPDATE accounts SET balance = balance + $1 WHERE id = $2", amount, toID)
    if err != nil {
        tx.Rollback() // duplicated everywhere
        return err
    }

    if err := tx.Commit(); err != nil {
        tx.Rollback() // What does rollback do after a failed commit? Unclear.
        return err
    }

    return nil
}
```

Every error path needs a manual `Rollback`. If you add a new operation in the middle, you have to remember to add another rollback branch. This code will drift — someone will add a third UPDATE and forget the rollback. It's a maintenance hazard.

## The Idiomatic Way

The pattern is `Begin` → `defer Rollback` → do work → `Commit`. The key insight is that `Rollback` after a successful `Commit` is a no-op in Postgres. So you can always defer it, and it only actually does anything if `Commit` was never reached:

```go
// RIGHT — defer rollback is the safety net, commit is the only happy path exit
func TransferFunds(db *sql.DB, ctx context.Context, fromID, toID int, amount int) error {
    tx, err := db.BeginTx(ctx, nil)
    if err != nil {
        return fmt.Errorf("begin transaction: %w", err)
    }
    defer tx.Rollback() // This is the safety net. After Commit succeeds, this is a no-op.

    _, err = tx.ExecContext(ctx,
        "UPDATE accounts SET balance = balance - $1 WHERE id = $2 AND balance >= $1",
        amount, fromID,
    )
    if err != nil {
        return fmt.Errorf("debit: %w", err) // defer fires → Rollback called
    }

    _, err = tx.ExecContext(ctx,
        "UPDATE accounts SET balance = balance + $1 WHERE id = $2",
        amount, toID,
    )
    if err != nil {
        return fmt.Errorf("credit: %w", err) // defer fires → Rollback called
    }

    if err := tx.Commit(); err != nil {
        return fmt.Errorf("commit: %w", err) // defer fires → Rollback called (no-op after failed commit)
    }

    return nil // defer fires → Rollback called → no-op because Commit succeeded
}
```

This is the pattern. One `defer tx.Rollback()`, no explicit rollback calls anywhere else. The logic is clear: the function either commits or the deferred rollback cleans up. You can add as many operations as you want in the middle and the safety net remains.

A helper that wraps this pattern makes it even cleaner for complex services:

```go
// RIGHT — generic transaction helper eliminates boilerplate
func WithTransaction(ctx context.Context, db *sql.DB, fn func(*sql.Tx) error) error {
    tx, err := db.BeginTx(ctx, nil)
    if err != nil {
        return fmt.Errorf("begin: %w", err)
    }
    defer tx.Rollback()

    if err := fn(tx); err != nil {
        return err
    }

    return tx.Commit()
}

// Usage — clean call sites, zero rollback boilerplate
func TransferFunds(db *sql.DB, ctx context.Context, fromID, toID, amount int) error {
    return WithTransaction(ctx, db, func(tx *sql.Tx) error {
        _, err := tx.ExecContext(ctx,
            "UPDATE accounts SET balance = balance - $1 WHERE id = $2",
            amount, fromID,
        )
        if err != nil {
            return fmt.Errorf("debit: %w", err)
        }

        _, err = tx.ExecContext(ctx,
            "UPDATE accounts SET balance = balance + $1 WHERE id = $2",
            amount, toID,
        )
        if err != nil {
            return fmt.Errorf("credit: %w", err)
        }

        return nil
    })
}
```

## In The Wild

**Savepoints** are the "nested transaction" mechanism in Postgres. Go's `database/sql` doesn't have native savepoint support, but you can use `tx.Exec("SAVEPOINT name")` directly:

```go
// RIGHT — savepoints for partial rollback within a transaction
func CreateOrderWithLineItems(ctx context.Context, db *sql.DB, order Order, items []LineItem) error {
    return WithTransaction(ctx, db, func(tx *sql.Tx) error {
        var orderID int
        err := tx.QueryRowContext(ctx,
            "INSERT INTO orders (user_id, total) VALUES ($1, $2) RETURNING id",
            order.UserID, order.Total,
        ).Scan(&orderID)
        if err != nil {
            return fmt.Errorf("insert order: %w", err)
        }

        for i, item := range items {
            savepointName := fmt.Sprintf("item_%d", i)

            if _, err := tx.ExecContext(ctx, "SAVEPOINT "+savepointName); err != nil {
                return fmt.Errorf("savepoint: %w", err)
            }

            _, err := tx.ExecContext(ctx,
                "INSERT INTO line_items (order_id, product_id, qty, price) VALUES ($1, $2, $3, $4)",
                orderID, item.ProductID, item.Qty, item.Price,
            )
            if err != nil {
                // Roll back just this item, not the whole order
                if _, rbErr := tx.ExecContext(ctx, "ROLLBACK TO SAVEPOINT "+savepointName); rbErr != nil {
                    return fmt.Errorf("rollback to savepoint: %w", rbErr)
                }
                log.Printf("skipped item %d: %v", item.ProductID, err)
                continue
            }

            if _, err := tx.ExecContext(ctx, "RELEASE SAVEPOINT "+savepointName); err != nil {
                return fmt.Errorf("release savepoint: %w", err)
            }
        }

        return nil
    })
}
```

**Isolation levels** matter for concurrent reads. The default in Postgres is `READ COMMITTED` — you see committed data from other transactions as it arrives. For a financial summary that must be consistent across multiple SELECTs in one transaction, you want `REPEATABLE READ`:

```go
// RIGHT — setting isolation level for consistent reads
func GetAccountSummary(ctx context.Context, db *sql.DB, userID int) (*Summary, error) {
    tx, err := db.BeginTx(ctx, &sql.TxOptions{
        Isolation: sql.LevelRepeatableRead,
        ReadOnly:  true, // Postgres optimization for read-only transactions
    })
    if err != nil {
        return nil, err
    }
    defer tx.Rollback()

    var s Summary
    // Multiple queries here will see a consistent snapshot —
    // even if another transaction commits between them
    if err := tx.QueryRowContext(ctx,
        "SELECT balance FROM accounts WHERE user_id = $1", userID,
    ).Scan(&s.Balance); err != nil {
        return nil, err
    }

    rows, err := tx.QueryContext(ctx,
        "SELECT amount, created_at FROM transactions WHERE user_id = $1 ORDER BY created_at DESC LIMIT 10",
        userID,
    )
    if err != nil {
        return nil, err
    }
    defer rows.Close()

    for rows.Next() {
        var t Transaction
        if err := rows.Scan(&t.Amount, &t.CreatedAt); err != nil {
            return nil, err
        }
        s.RecentTransactions = append(s.RecentTransactions, t)
    }

    return &s, tx.Commit()
}
```

## The Gotchas

**Long-running transactions are production incidents waiting to happen.** A transaction that stays open for seconds (or minutes) holds row locks that block every other writer on those rows. I've seen a transaction held open while waiting for an HTTP API call — the API was slow, the transaction held a lock, and every other request trying to update that row queued up. Within 30 seconds, the connection pool was full and the service was down. Never do I/O (HTTP calls, file system operations, anything that might block) inside a transaction.

**Rollback errors.** `tx.Rollback()` can return an error — connection dropped, transaction already committed, etc. When you use `defer tx.Rollback()`, you're ignoring that error. For most cases this is fine — if the rollback fails, Postgres will clean up when the connection closes. But if you need to audit rollback failures, assign the deferred call:

```go
defer func() {
    if err := tx.Rollback(); err != nil && !errors.Is(err, sql.ErrTxDone) {
        log.Printf("rollback failed: %v", err)
    }
}()
```

`sql.ErrTxDone` is returned when the transaction has already been committed or rolled back — that's the normal case after a successful commit, so you can ignore it.

**Serializable isolation and retry loops.** If you use `sql.LevelSerializable`, Postgres may return a serialization failure error (`40001`). You need to retry the entire transaction from scratch on this error. Serializable isolation is the strongest guarantee but it's also the most expensive — use it only when you genuinely need it.

## Key Takeaway

The transaction pattern is `Begin` → `defer Rollback` → work → `Commit`. Deferred rollback is the safety net, not an error path — after a successful commit it's a no-op. Wrap this pattern in a `WithTransaction` helper so every call site is clean. Use `BeginTx` with a context so transactions have a timeout. Never do I/O inside a transaction. Keep transactions short — seconds at most, not minutes. Get these right and transactions become one of the most reliable parts of your codebase.

---

← [Lesson 2: Connection Pool Tuning](/post/go/go-db-connection-pools) | [Lesson 4: The Repository Pattern →](/post/go/go-db-repository-pattern)
