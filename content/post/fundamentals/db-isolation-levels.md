---
title: 'Lesson 5: Transaction Isolation — Read Committed vs Serializable'
author: Atharva Pandey
series: "Database Internals for Backend Engineers"
lesson: 5
keywords:
  - database internals
  - transaction isolation
  - read committed
  - serializable
  - phantom reads
  - dirty reads
  - backend engineering
tags:
  - fundamentals
  - databases
date: '2024-06-23T00:00:00.000Z'
---

I shipped a bug once that allowed a user to spend the same gift card balance twice. Two requests arrived nearly simultaneously, both read the same balance, both decided the balance was sufficient, both deducted it, and both succeeded. The database did exactly what I asked. The problem was what I asked for: I assumed reads were consistent across statements within a transaction, but I was running at the default isolation level. Understanding the four isolation levels — and what each one actually protects you from — is not academic. It is the difference between shipping correct financial code and shipping race conditions.

## How It Actually Works

The SQL standard defines four isolation levels, each protecting against a different class of anomaly:

| Level | Dirty Read | Non-Repeatable Read | Phantom Read | Serialization Anomaly |
|-------|-----------|--------------------|--------------|-----------------------|
| Read Uncommitted | Possible | Possible | Possible | Possible |
| Read Committed | Not possible | Possible | Possible | Possible |
| Repeatable Read | Not possible | Not possible | Possible* | Possible |
| Serializable | Not possible | Not possible | Not possible | Not possible |

*Postgres's Repeatable Read also prevents phantom reads due to snapshot semantics.

**Dirty Read**: you read data written by an uncommitted transaction. If that transaction rolls back, you saw data that never officially existed.

**Non-Repeatable Read**: you read a row, another transaction modifies and commits it, you read the same row again and get a different value. Two reads of the same row within one transaction return different results.

**Phantom Read**: you run a query that returns a set of rows matching a condition, another transaction inserts a row that also matches, you rerun the query and get a different set. The "extra" row is the phantom.

**Serialization Anomaly**: the outcome of concurrent transactions is inconsistent with any possible serial (one-at-a-time) execution. This includes write skew — the bug in my gift card story.

**Read Committed** (Postgres default): each statement in your transaction gets a fresh snapshot. You never see uncommitted data, but if another transaction commits between your two `SELECT` statements, your second `SELECT` sees that committed change.

**Repeatable Read**: your entire transaction works from the snapshot taken at its start. Other transactions' commits are invisible to you for the duration.

**Serializable**: Postgres uses Serializable Snapshot Isolation (SSI), tracking read/write dependencies between concurrent transactions. If a cycle is detected (meaning the transactions can't be ordered serially without producing the actual result), one transaction is aborted with a serialization failure.

## Why It Matters

The gift card bug was a classic write skew under Read Committed:

```
Transaction A                 Transaction B
READ balance = 100            READ balance = 100
(balance >= 50? yes)          (balance >= 50? yes)
DEDUCT 50 → balance = 50      DEDUCT 75 → balance = 25
COMMIT                        COMMIT
Final balance: 25 (not -25, because A wrote 50 and B wrote 25 independently)
```

Wait — actually this produces balance = 25 only if A and B read before either writes. But if both read 100 and both deduct independently, you can end up with a balance that should have gone negative. The exact outcome depends on the update pattern. The safe fix is either a row lock (`SELECT ... FOR UPDATE`) or Serializable isolation.

## Production Example

Here is the gift card deduction written correctly at three different isolation levels:

```go
// Option 1: SELECT FOR UPDATE at Read Committed
// Locks the row, serializes concurrent deductions
func deductBalance(ctx context.Context, db *sql.DB, giftCardID int64, amountCents int) error {
    tx, err := db.BeginTx(ctx, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
    if err != nil {
        return err
    }
    defer tx.Rollback()

    var balanceCents int
    err = tx.QueryRowContext(ctx,
        "SELECT balance_cents FROM gift_cards WHERE id = $1 FOR UPDATE",
        giftCardID,
    ).Scan(&balanceCents)
    if err != nil {
        return err
    }

    if balanceCents < amountCents {
        return ErrInsufficientBalance
    }

    _, err = tx.ExecContext(ctx,
        "UPDATE gift_cards SET balance_cents = balance_cents - $1 WHERE id = $2",
        amountCents, giftCardID,
    )
    if err != nil {
        return err
    }
    return tx.Commit()
}

// Option 2: Serializable isolation — no explicit lock needed
// Postgres detects the write skew and aborts one transaction
func deductBalanceSerializable(ctx context.Context, db *sql.DB, giftCardID int64, amountCents int) error {
    for retries := 0; retries < 3; retries++ {
        err := attemptDeduct(ctx, db, giftCardID, amountCents)
        if err == nil {
            return nil
        }
        // Check if it's a serialization failure — retry is correct
        var pgErr *pgconn.PgError
        if errors.As(err, &pgErr) && pgErr.Code == "40001" {
            continue // serialization_failure — retry
        }
        return err // real error — don't retry
    }
    return ErrTooManyRetries
}
```

The key difference: `SELECT FOR UPDATE` adds an explicit lock and is fast but requires careful lock ordering to avoid deadlocks. Serializable isolation is cleaner but requires retry logic for serialization failures.

For reads where consistency matters (generating an invoice, producing a financial report), Repeatable Read protects you from seeing different values for the same row:

```go
tx, err := db.BeginTx(ctx, &sql.TxOptions{Isolation: sql.LevelRepeatableRead})
// All reads within this transaction see the same snapshot
// Another transaction's commits won't affect what you see
```

## The Tradeoffs

Higher isolation levels cost more:

**Read Committed**: low overhead, one snapshot per statement. The default is the right choice for most operations — user profile updates, product catalog reads, session management.

**Repeatable Read**: one snapshot per transaction. Slightly more overhead, but the main cost is that long transactions prevent vacuum from cleaning up old versions that your snapshot still needs.

**Serializable**: SSI tracks dependency graphs between transactions. For high-concurrency workloads, serialization failures can cascade. Retry logic is mandatory. I recommend Serializable for financial operations, inventory deductions, and anywhere write skew would be incorrect.

One practical rule I follow: if correctness requires that what you read determines what you write, you need at least `SELECT FOR UPDATE` or Serializable isolation. If you are just reading data to display it, Read Committed is almost always fine.

## Key Takeaway

Transaction isolation levels are not a dial you turn up for "more safety" — they're specific protections against specific anomalies. Read Committed is the right default for most reads and simple writes. Repeatable Read gives you a stable snapshot for complex read operations. Serializable prevents write skew and is the right choice for financial correctness. Learn which anomaly each level protects against, and choose the level that matches your operation — not blindly the lowest or highest.

---

**Previous:** [Lesson 4: MVCC](/post/fundamentals/db-mvcc/) | **Next:** [Lesson 6: Query Planning and EXPLAIN — Reading Execution Plans](/post/fundamentals/db-explain/)
