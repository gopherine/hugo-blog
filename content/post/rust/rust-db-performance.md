---
title: "Lesson 9: N+1 Queries, Indexes, and EXPLAIN — Database performance in Rust"
author: Atharva Pandey
series: "Rust Database Patterns"
lesson: 9
keywords: [Rust, rust, database, SQL, performance, N+1, indexes, EXPLAIN, postgres, optimization]
tags: [Rust tutorial, rust, database, postgres]
date: '2024-11-06T07:52:00.000Z'
---

A coworker asked me to look at an endpoint that was taking 8 seconds to return 50 orders. The table had 200K rows — not big by any standard. I opened the code, and the pattern was immediately obvious: fetch 50 orders, then for each order, fetch its items in a separate query. Fifty-one database round trips where one would do.

The N+1 query problem is the most common performance mistake in database-backed applications, and Rust doesn't magically prevent it. You need to know what to look for.

## The N+1 Problem

Here's the classic N+1 pattern in Rust:

```rust
// BAD: N+1 queries
async fn get_orders_with_items(pool: &PgPool) -> Result<Vec<OrderWithItems>, sqlx::Error> {
    // Query 1: fetch all orders
    let orders = sqlx::query_as!(Order, "SELECT * FROM orders LIMIT 50")
        .fetch_all(pool)
        .await?;

    let mut result = Vec::new();

    for order in orders {
        // Queries 2..51: one query per order
        let items = sqlx::query_as!(
            OrderItem,
            "SELECT * FROM order_items WHERE order_id = $1",
            order.id
        )
        .fetch_all(pool)
        .await?;

        result.push(OrderWithItems { order, items });
    }

    Ok(result)
}
```

For 50 orders, that's 51 queries. For 500 orders, 501 queries. Each query has network latency, planning time, and execution time. Even if each individual query takes 2ms, 501 of them takes a second. The problem scales linearly with result count.

## Fix 1: JOIN Everything

The simplest fix — one query, no round trips:

```rust
// GOOD: single query with JOIN
async fn get_orders_with_items(pool: &PgPool) -> Result<Vec<OrderWithItems>, sqlx::Error> {
    let rows = sqlx::query!(
        "SELECT
            o.id as order_id,
            o.customer_id,
            o.status,
            o.total,
            o.created_at,
            oi.id as item_id,
            oi.product_id,
            oi.quantity,
            oi.price
         FROM orders o
         JOIN order_items oi ON oi.order_id = o.id
         ORDER BY o.created_at DESC
         LIMIT 500"
    )
    .fetch_all(pool)
    .await?;

    // Group flat rows into nested structures
    let mut orders_map: std::collections::HashMap<Uuid, OrderWithItems> =
        std::collections::HashMap::new();

    for row in rows {
        let entry = orders_map.entry(row.order_id).or_insert_with(|| {
            OrderWithItems {
                order: Order {
                    id: row.order_id,
                    customer_id: row.customer_id,
                    status: row.status.clone(),
                    total: row.total,
                    created_at: row.created_at,
                },
                items: Vec::new(),
            }
        });

        entry.items.push(OrderItem {
            id: row.item_id,
            product_id: row.product_id,
            quantity: row.quantity,
            price: row.price,
        });
    }

    Ok(orders_map.into_values().collect())
}
```

One query. All the data. The grouping logic runs in your application, which is fine — Rust is fast at HashMap operations.

## Fix 2: Batch Loading with IN Clause

Sometimes a JOIN produces too many duplicated columns (if orders have many non-item fields). Batch loading is the middle ground — two queries instead of N+1:

```rust
// GOOD: 2 queries instead of N+1
async fn get_orders_with_items_batched(
    pool: &PgPool,
) -> Result<Vec<OrderWithItems>, sqlx::Error> {
    // Query 1: fetch orders
    let orders = sqlx::query_as!(
        Order,
        "SELECT * FROM orders ORDER BY created_at DESC LIMIT 50"
    )
    .fetch_all(pool)
    .await?;

    let order_ids: Vec<Uuid> = orders.iter().map(|o| o.id).collect();

    // Query 2: fetch ALL items for ALL orders in one shot
    let items = sqlx::query_as!(
        OrderItem,
        "SELECT * FROM order_items WHERE order_id = ANY($1)",
        &order_ids
    )
    .fetch_all(pool)
    .await?;

    // Group items by order_id
    let mut items_by_order: std::collections::HashMap<Uuid, Vec<OrderItem>> =
        std::collections::HashMap::new();
    for item in items {
        items_by_order.entry(item.order_id).or_default().push(item);
    }

    // Combine
    let result = orders
        .into_iter()
        .map(|order| {
            let items = items_by_order.remove(&order.id).unwrap_or_default();
            OrderWithItems { order, items }
        })
        .collect();

    Ok(result)
}
```

Two queries regardless of how many orders. This is the DataLoader pattern from the GraphQL world, and it works just as well outside GraphQL.

## Fix 3: Postgres JSON Aggregation

For Postgres specifically, you can aggregate child rows into JSON directly in SQL:

```rust
#[derive(Debug, sqlx::FromRow)]
struct OrderWithJsonItems {
    id: Uuid,
    customer_id: Uuid,
    status: String,
    total: i64,
    created_at: NaiveDateTime,
    items: sqlx::types::Json<Vec<ItemData>>,
}

#[derive(Debug, serde::Deserialize, serde::Serialize)]
struct ItemData {
    id: Uuid,
    product_id: Uuid,
    quantity: i32,
    price: i64,
}

async fn get_orders_with_json_items(
    pool: &PgPool,
) -> Result<Vec<OrderWithJsonItems>, sqlx::Error> {
    sqlx::query_as!(
        OrderWithJsonItems,
        r#"SELECT
            o.id,
            o.customer_id,
            o.status,
            o.total,
            o.created_at,
            COALESCE(
                json_agg(
                    json_build_object(
                        'id', oi.id,
                        'product_id', oi.product_id,
                        'quantity', oi.quantity,
                        'price', oi.price
                    )
                ) FILTER (WHERE oi.id IS NOT NULL),
                '[]'
            ) as "items!: sqlx::types::Json<Vec<ItemData>>"
         FROM orders o
         LEFT JOIN order_items oi ON oi.order_id = o.id
         GROUP BY o.id
         ORDER BY o.created_at DESC
         LIMIT 50"#
    )
    .fetch_all(pool)
    .await
}
```

One query, nested result, no application-side grouping. The database does the aggregation. This is my preferred approach for read-heavy endpoints where the nested structure maps directly to an API response.

## Understanding EXPLAIN

You can't fix what you can't see. Postgres `EXPLAIN` shows you exactly how it plans to execute your query:

```rust
async fn explain_query(pool: &PgPool, customer_id: Uuid) -> Result<(), sqlx::Error> {
    let plan = sqlx::query_scalar!(
        "EXPLAIN ANALYZE SELECT * FROM orders WHERE customer_id = $1",
        customer_id
    )
    .fetch_all(pool)
    .await?;

    for line in plan {
        println!("{}", line.unwrap_or_default());
    }

    Ok(())
}
```

A typical output looks like:

```
Seq Scan on orders  (cost=0.00..4235.00 rows=50 width=64) (actual time=0.023..25.184 rows=47 loops=1)
  Filter: (customer_id = 'abc-123'::uuid)
  Rows Removed by Filter: 199953
Planning Time: 0.082 ms
Execution Time: 25.312 ms
```

That `Seq Scan` is a red flag. Postgres is reading every row in the table and filtering in memory. With 200K rows, it scans all of them to find 47 matches.

Add an index:

```sql
CREATE INDEX idx_orders_customer_id ON orders(customer_id);
```

Now EXPLAIN shows:

```
Index Scan using idx_orders_customer_id on orders  (cost=0.42..8.50 rows=50 width=64) (actual time=0.032..0.156 rows=47 loops=1)
  Index Cond: (customer_id = 'abc-123'::uuid)
Planning Time: 0.095 ms
Execution Time: 0.178 ms
```

From 25ms to 0.18ms. That's a 140x improvement from one `CREATE INDEX` statement. The index lets Postgres jump directly to the matching rows instead of scanning the entire table.

## Index Strategies

Not all indexes are created equal. Here are the patterns I use most:

### B-tree indexes (the default)

Good for equality and range queries:

```sql
-- Equality lookups
CREATE INDEX idx_users_email ON users(email);

-- Range queries
CREATE INDEX idx_orders_created ON orders(created_at);

-- Composite index for queries that filter on multiple columns
CREATE INDEX idx_orders_customer_status ON orders(customer_id, status);
```

Column order in composite indexes matters. The index on `(customer_id, status)` is useful for:
- `WHERE customer_id = X` — yes, uses the index
- `WHERE customer_id = X AND status = Y` — yes, uses the index
- `WHERE status = Y` — **no**, can't use the index (wrong column order)

Think of it like a phone book sorted by last name, then first name. You can look up by last name, or by last name + first name. But you can't efficiently look up by first name alone.

### Partial indexes

Only index rows that match a condition. Perfect for queries against a subset of data:

```sql
-- Only index active users — smaller index, faster lookups
CREATE INDEX idx_active_users ON users(email) WHERE active = true;

-- Only index recent orders — most queries are for recent data
CREATE INDEX idx_recent_orders ON orders(created_at)
    WHERE created_at > '2024-01-01';
```

### GIN indexes for full-text search

```sql
-- Text search
CREATE INDEX idx_posts_search ON posts USING gin(to_tsvector('english', title || ' ' || body));

-- JSONB field queries
CREATE INDEX idx_metadata ON events USING gin(metadata);
```

### Expression indexes

Index a computed value:

```sql
-- Case-insensitive email lookup
CREATE INDEX idx_users_lower_email ON users(LOWER(email));

-- Your query must use the same expression
-- WHERE LOWER(email) = LOWER($1)
```

## Building an EXPLAIN Helper

I keep a helper function in every project that lets me easily check query plans:

```rust
use sqlx::PgPool;

pub async fn explain_analyze(pool: &PgPool, query: &str) -> Result<String, sqlx::Error> {
    let plan_query = format!("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) {}", query);
    let rows = sqlx::query_scalar::<_, String>(&plan_query)
        .fetch_all(pool)
        .await?;

    Ok(rows.join("\n"))
}

// In tests:
#[sqlx::test(migrations = "./migrations")]
async fn verify_customer_orders_uses_index(pool: PgPool) {
    // Seed enough data that Postgres won't choose seq scan
    seed_large_dataset(&pool, 10_000).await;

    // Analyze the table so Postgres has accurate statistics
    sqlx::query("ANALYZE orders").execute(&pool).await.unwrap();

    let plan = explain_analyze(
        &pool,
        "SELECT * FROM orders WHERE customer_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'",
    )
    .await
    .unwrap();

    // Verify the query uses an index scan, not a seq scan
    assert!(
        plan.contains("Index Scan") || plan.contains("Index Only Scan"),
        "Expected index scan but got:\n{}",
        plan
    );
}
```

This is a test that verifies your indexes are actually being used. Most teams don't write these, and they find out about missing indexes when production slows down.

## Common Performance Antipatterns

### SELECT * when you only need a few columns

```rust
// BAD: fetches all columns including the 10KB `description` text
let orders = sqlx::query_as!(Order, "SELECT * FROM orders LIMIT 100")
    .fetch_all(pool).await?;

// GOOD: only fetch what the endpoint actually returns
let orders = sqlx::query!(
    "SELECT id, status, total, created_at FROM orders LIMIT 100"
)
.fetch_all(pool).await?;
```

Fetching unnecessary columns wastes network bandwidth, memory, and prevents Postgres from using index-only scans.

### Missing LIMIT on user-facing queries

```rust
// BAD: if someone has 50,000 orders, this returns all of them
let orders = sqlx::query_as!(Order,
    "SELECT * FROM orders WHERE customer_id = $1", customer_id)
    .fetch_all(pool).await?;

// GOOD: always paginate
let orders = sqlx::query_as!(Order,
    "SELECT * FROM orders WHERE customer_id = $1
     ORDER BY created_at DESC LIMIT $2 OFFSET $3",
    customer_id, page_size, offset)
    .fetch_all(pool).await?;
```

### Using OFFSET for deep pagination

OFFSET-based pagination gets slower as you go deeper because Postgres still fetches and discards the skipped rows:

```rust
// BAD: page 1000 with 20 items per page means Postgres reads 20,000 rows
"SELECT * FROM orders ORDER BY created_at DESC LIMIT 20 OFFSET 19980"

// GOOD: cursor-based pagination using the last seen value
"SELECT * FROM orders
 WHERE created_at < $1
 ORDER BY created_at DESC
 LIMIT 20"
```

Cursor pagination is O(1) regardless of which page you're on. The tradeoff is you can't jump to "page 500" directly — you can only move forward and backward. For most UIs (infinite scroll, "load more" buttons), that's fine.

### Not using connection pool metrics to detect slow queries

```rust
use std::time::Instant;

async fn timed_query<T>(
    pool: &PgPool,
    name: &str,
    future: impl std::future::Future<Output = Result<T, sqlx::Error>>,
) -> Result<T, sqlx::Error> {
    let start = Instant::now();
    let result = future.await;
    let elapsed = start.elapsed();

    if elapsed.as_millis() > 100 {
        eprintln!("SLOW QUERY [{}]: {}ms", name, elapsed.as_millis());
    }

    result
}

// Usage
let users = timed_query(
    &pool,
    "list_active_users",
    sqlx::query_as!(User, "SELECT * FROM users WHERE active = true")
        .fetch_all(&pool)
).await?;
```

In production, replace `eprintln!` with your metrics system. Set alerts for queries above your latency threshold.

## Batch Operations

When you need to process large datasets, don't load everything into memory:

```rust
use futures::TryStreamExt;

async fn process_all_orders(pool: &PgPool) -> Result<(), sqlx::Error> {
    let mut stream = sqlx::query_as!(
        Order,
        "SELECT * FROM orders WHERE status = 'pending' ORDER BY created_at"
    )
    .fetch(pool);

    let mut batch = Vec::with_capacity(100);

    while let Some(order) = stream.try_next().await? {
        batch.push(order);

        if batch.len() >= 100 {
            process_batch(&batch).await;
            batch.clear();
        }
    }

    // Process remaining items
    if !batch.is_empty() {
        process_batch(&batch).await;
    }

    Ok(())
}
```

Streaming plus batching gives you bounded memory usage regardless of dataset size.

## What's Next

We've focused entirely on Postgres so far. But not every data problem is a SQL problem. Lesson 10 covers Redis, MongoDB, and other non-relational stores in Rust — when to reach for them, and how to integrate them alongside your relational database.
