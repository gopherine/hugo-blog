---
title: "Lesson 5: Transactions and Error Rollback — Atomic operations"
author: Atharva Pandey
series: "Rust Database Patterns"
lesson: 5
keywords: [Rust, rust, database, SQL, transactions, rollback, postgres, ACID, atomicity]
tags: [Rust tutorial, rust, database, postgres]
date: '2024-10-28T10:05:00.000Z'
---

A payment service I worked on had a subtle bug: it deducted money from the user's wallet, then tried to create an order record. If the order insert failed — constraint violation, timeout, anything — the money was already gone. The user's balance was decremented but they had no order. We called these "ghost charges" internally, and customers called them something less polite.

The fix was embarrassingly simple: wrap both operations in a transaction.

## The Problem

Most meaningful database operations involve multiple queries. Transferring money? That's a debit from one account and a credit to another. Creating an order? That's inserting the order, inserting line items, updating inventory, and recording the payment. Registering a user? That's creating the user record, creating their default settings, and sending a welcome email trigger.

If any of these multi-step operations fails halfway through, you're left with inconsistent data. The money is debited but never credited. The order exists but the inventory isn't updated. The user is created but has no settings.

Transactions make a group of operations atomic — either all of them succeed, or none of them do.

## Transactions in SQLx

SQLx makes transactions straightforward:

```rust
use sqlx::PgPool;
use uuid::Uuid;

async fn transfer_funds(
    pool: &PgPool,
    from_account: Uuid,
    to_account: Uuid,
    amount: i64,
) -> Result<(), sqlx::Error> {
    let mut tx = pool.begin().await?;

    // Debit the sender
    let rows_affected = sqlx::query!(
        "UPDATE accounts SET balance = balance - $1 WHERE id = $2 AND balance >= $1",
        amount,
        from_account
    )
    .execute(&mut *tx)
    .await?
    .rows_affected();

    if rows_affected == 0 {
        // Either the account doesn't exist or insufficient funds.
        // The transaction will be rolled back when `tx` is dropped.
        return Err(sqlx::Error::RowNotFound);
    }

    // Credit the receiver
    sqlx::query!(
        "UPDATE accounts SET balance = balance + $1 WHERE id = $2",
        amount,
        to_account
    )
    .execute(&mut *tx)
    .await?;

    // Record the transfer
    sqlx::query!(
        "INSERT INTO transfers (from_account, to_account, amount) VALUES ($1, $2, $3)",
        from_account,
        to_account,
        amount
    )
    .execute(&mut *tx)
    .await?;

    // Commit — if this line is never reached, the transaction is rolled back
    tx.commit().await?;

    Ok(())
}
```

The critical behavior here: if `tx` is dropped without calling `commit()`, the transaction is automatically rolled back. This is Rust's ownership system doing exactly what it's good at — deterministic cleanup. If any of the queries return an `Err`, the `?` operator returns early, `tx` gets dropped, and the rollback happens.

No explicit `try/catch/finally`. No `defer rollback()`. No forgetting to clean up. The compiler handles it.

## Transactions in Diesel

Diesel's transaction API is even more concise:

```rust
use diesel::prelude::*;
use diesel::PgConnection;

fn transfer_funds(
    conn: &mut PgConnection,
    from_id: Uuid,
    to_id: Uuid,
    amount: i64,
) -> Result<(), diesel::result::Error> {
    conn.transaction(|conn| {
        // Debit sender
        let updated = diesel::update(
            accounts::table
                .filter(accounts::id.eq(from_id))
                .filter(accounts::balance.ge(amount))
        )
        .set(accounts::balance.eq(accounts::balance - amount))
        .execute(conn)?;

        if updated == 0 {
            return Err(diesel::result::Error::NotFound);
        }

        // Credit receiver
        diesel::update(accounts::table.filter(accounts::id.eq(to_id)))
            .set(accounts::balance.eq(accounts::balance + amount))
            .execute(conn)?;

        // Record the transfer
        diesel::insert_into(transfers::table)
            .values(&NewTransfer {
                from_account: from_id,
                to_account: to_id,
                amount,
            })
            .execute(conn)?;

        Ok(())
    })
}
```

The closure-based API is clean — if the closure returns `Err`, Diesel rolls back. If it returns `Ok`, Diesel commits. No manual commit/rollback calls.

## Nested Transactions with Savepoints

Sometimes you want to attempt an operation within a transaction without rolling back the entire thing if it fails. That's what savepoints are for:

```rust
async fn create_order_with_optional_coupon(
    pool: &PgPool,
    user_id: Uuid,
    items: &[OrderItem],
    coupon_code: Option<&str>,
) -> Result<Order, sqlx::Error> {
    let mut tx = pool.begin().await?;

    // Create the order
    let order = sqlx::query_as!(
        Order,
        "INSERT INTO orders (user_id, status) VALUES ($1, 'pending') RETURNING *",
        user_id
    )
    .fetch_one(&mut *tx)
    .await?;

    // Insert line items
    for item in items {
        sqlx::query!(
            "INSERT INTO order_items (order_id, product_id, quantity, price)
             VALUES ($1, $2, $3, $4)",
            order.id,
            item.product_id,
            item.quantity,
            item.price
        )
        .execute(&mut *tx)
        .await?;
    }

    // Try to apply coupon — but don't fail the whole order if it doesn't work
    if let Some(code) = coupon_code {
        // Savepoint: creates a nested transaction
        let savepoint = tx.begin().await?;

        match apply_coupon(&mut *savepoint, order.id, code).await {
            Ok(_) => {
                savepoint.commit().await?;
            }
            Err(e) => {
                // Savepoint is rolled back (by being dropped), but the
                // outer transaction is still valid
                eprintln!("Coupon {} failed: {}, proceeding without discount", code, e);
            }
        }
    }

    tx.commit().await?;
    Ok(order)
}

async fn apply_coupon(
    conn: &mut sqlx::PgConnection,
    order_id: Uuid,
    code: &str,
) -> Result<(), sqlx::Error> {
    let coupon = sqlx::query!(
        "SELECT id, discount_pct FROM coupons WHERE code = $1 AND used = false",
        code
    )
    .fetch_one(&mut *conn)
    .await?;

    sqlx::query!(
        "UPDATE orders SET discount_pct = $1 WHERE id = $2",
        coupon.discount_pct,
        order_id
    )
    .execute(&mut *conn)
    .await?;

    sqlx::query!(
        "UPDATE coupons SET used = true WHERE id = $1",
        coupon.id
    )
    .execute(&mut *conn)
    .await?;

    Ok(())
}
```

This is a real pattern from production code. The order should succeed even if the coupon application fails — maybe the coupon is expired, maybe it's already used. Savepoints let you try and recover without aborting the entire transaction.

## Transaction Isolation Levels

Postgres supports four isolation levels, and picking the right one matters:

```rust
use sqlx::postgres::PgPool;

async fn read_committed_example(pool: &PgPool) -> Result<(), sqlx::Error> {
    // Default: READ COMMITTED
    // Each query sees the latest committed data
    let mut tx = pool.begin().await?;
    sqlx::query("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
        .execute(&mut *tx)
        .await?;
    // ... queries ...
    tx.commit().await
}

async fn repeatable_read_example(pool: &PgPool) -> Result<(), sqlx::Error> {
    // REPEATABLE READ: all queries in the transaction see the same snapshot
    let mut tx = pool.begin().await?;
    sqlx::query("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        .execute(&mut *tx)
        .await?;
    // ... queries ...
    tx.commit().await
}

async fn serializable_example(pool: &PgPool) -> Result<(), sqlx::Error> {
    // SERIALIZABLE: transactions behave as if they ran one at a time
    // Highest isolation, but can cause serialization failures that need retry
    let mut tx = pool.begin().await?;
    sqlx::query("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        .execute(&mut *tx)
        .await?;
    // ... queries ...
    tx.commit().await
}
```

My rules:

- **READ COMMITTED** (default): Good for 95% of cases. Simple, predictable, low overhead.
- **REPEATABLE READ**: Use when you need consistent reads across multiple queries in a transaction. Reports, balance calculations, anything where you read the same data twice and need the same answer.
- **SERIALIZABLE**: Use for financial operations, inventory management, anything where concurrent transactions must produce the same result as if they ran sequentially. Be prepared to retry on serialization failures.

## Handling Serialization Failures

When you use SERIALIZABLE isolation, Postgres might abort your transaction with a serialization error. You need retry logic:

```rust
use std::time::Duration;

async fn with_serializable_retry<F, T>(
    pool: &PgPool,
    max_retries: u32,
    operation: F,
) -> Result<T, sqlx::Error>
where
    F: Fn(&mut sqlx::Transaction<'_, sqlx::Postgres>) -> std::pin::Pin<
        Box<dyn std::future::Future<Output = Result<T, sqlx::Error>> + Send + '_>
    > + Send + Sync,
{
    let mut attempts = 0;

    loop {
        let mut tx = pool.begin().await?;
        sqlx::query("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            .execute(&mut *tx)
            .await?;

        match operation(&mut tx).await {
            Ok(result) => {
                tx.commit().await?;
                return Ok(result);
            }
            Err(sqlx::Error::Database(ref db_err))
                if db_err.code().as_deref() == Some("40001") =>
            {
                // Serialization failure — retry
                attempts += 1;
                if attempts >= max_retries {
                    return Err(sqlx::Error::Database(db_err.clone()));
                }
                // Brief backoff
                tokio::time::sleep(Duration::from_millis(10 * attempts as u64)).await;
                continue;
            }
            Err(e) => return Err(e),
        }
    }
}
```

The Postgres error code `40001` is "serialization_failure." When you see it, you retry the entire transaction from scratch — not just the failed query, because the entire transaction's view of the database might be stale.

## Advisory Locks for Application-Level Coordination

Sometimes you need coordination beyond what transaction isolation provides. Postgres advisory locks let you create application-level mutexes:

```rust
async fn process_payment_exclusively(
    pool: &PgPool,
    order_id: i64,
) -> Result<(), sqlx::Error> {
    let mut tx = pool.begin().await?;

    // Acquire an advisory lock keyed on the order ID
    // This blocks until the lock is available
    sqlx::query!("SELECT pg_advisory_xact_lock($1)", order_id)
        .execute(&mut *tx)
        .await?;

    // Now we have exclusive access for this order_id
    // No other transaction can hold this lock simultaneously

    let order = sqlx::query!(
        "SELECT * FROM orders WHERE id = $1 AND status = 'pending'",
        order_id
    )
    .fetch_optional(&mut *tx)
    .await?;

    if let Some(order) = order {
        // Process payment...
        sqlx::query!(
            "UPDATE orders SET status = 'paid' WHERE id = $1",
            order_id
        )
        .execute(&mut *tx)
        .await?;
    }

    tx.commit().await?;
    // Advisory lock is released when the transaction commits
    Ok(())
}
```

`pg_advisory_xact_lock` is transaction-scoped — the lock automatically releases when the transaction ends. This prevents double-processing of payments without requiring SERIALIZABLE isolation for the entire transaction.

## Common Pitfalls

**Holding transactions too long.** Every open transaction holds database resources and can block other operations. Don't do HTTP calls, file I/O, or any slow operation inside a transaction. Get in, do your database work, get out.

```rust
// BAD: HTTP call inside a transaction
let mut tx = pool.begin().await?;
sqlx::query!("UPDATE orders SET status = 'processing' WHERE id = $1", order_id)
    .execute(&mut *tx)
    .await?;
let result = http_client.post("https://payment.api/charge").send().await?; // SLOW!
sqlx::query!("UPDATE orders SET status = 'paid' WHERE id = $1", order_id)
    .execute(&mut *tx)
    .await?;
tx.commit().await?;

// GOOD: minimize transaction scope
let result = http_client.post("https://payment.api/charge").send().await?;
let mut tx = pool.begin().await?;
sqlx::query!("UPDATE orders SET status = 'paid', payment_ref = $1 WHERE id = $2",
    result.reference, order_id)
    .execute(&mut *tx)
    .await?;
tx.commit().await?;
```

**Forgetting that `?` triggers rollback.** This is actually a feature, not a bug — but you need to be aware of it. If you use `?` on a non-database operation inside a transaction block, the transaction rolls back on any error, not just database errors.

**Not retrying serialization failures.** If you use SERIALIZABLE and don't retry on error code 40001, your users will see random failures under concurrent load.

## What's Next

Transactions give you atomicity at the database level. But how do you organize your database access code so it doesn't leak into every corner of your application? Lesson 6 covers the Repository Pattern in Rust — abstracting persistence behind clean interfaces.
