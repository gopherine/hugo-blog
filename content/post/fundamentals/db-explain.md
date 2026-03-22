---
title: 'Lesson 6: Query Planning and EXPLAIN — Reading Execution Plans'
author: Atharva Pandey
series: "Database Internals for Backend Engineers"
lesson: 6
keywords:
  - database internals
  - EXPLAIN ANALYZE
  - query planning
  - execution plan
  - query optimization
  - backend engineering
tags:
  - fundamentals
  - databases
date: '2024-07-11T00:00:00.000Z'
---

The first time I ran `EXPLAIN ANALYZE` on a slow query, I stared at the output for five minutes and understood none of it. It looked like a compiler error message crossed with a financial report. Then a senior engineer walked me through it, and what had looked like noise resolved into a clear picture: this node was doing more work than the planner expected, this join strategy was wrong, this sort was spilling to disk. `EXPLAIN ANALYZE` is the most powerful tool in database performance work, and it takes an hour to learn but pays back every week for the rest of your career.

## How It Actually Works

`EXPLAIN` shows the query plan the planner chose — the tree of operations Postgres will execute. `EXPLAIN ANALYZE` actually runs the query and shows both the planner's estimates and what actually happened.

The output is a tree read from the bottom up. Each node represents one operation. Child nodes feed rows into their parent.

```sql
EXPLAIN ANALYZE
SELECT u.name, count(o.id) as order_count
FROM users u
JOIN orders o ON o.user_id = u.id
WHERE u.created_at > '2024-01-01'
GROUP BY u.id, u.name
ORDER BY order_count DESC
LIMIT 10;
```

Output (simplified):
```
Limit  (cost=1240.00..1240.03 rows=10 width=40) (actual time=45.2..45.2 rows=10 loops=1)
  -> Sort  (cost=1240.00..1242.50 rows=1000 width=40) (actual time=45.1..45.2 rows=10 loops=1)
        Sort Key: (count(o.id)) DESC
        Sort Method: top-N heapsort  Memory: 25kB
        -> HashAggregate  (cost=1200.00..1212.50 rows=1000 width=40) (actual time=44.8..45.0 rows=950 loops=1)
              Group Key: u.id
              -> Hash Join  (cost=400.00..1150.00 rows=10000 width=16) (actual time=5.2..38.0 rows=12000 loops=1)
                    Hash Cond: (o.user_id = u.id)
                    -> Seq Scan on orders o  (cost=0.00..600.00 rows=30000 width=8) (actual time=0.1..18.2 rows=30000 loops=1)
                    -> Hash  (cost=320.00..320.00 rows=6400 width=12) (actual time=4.9..4.9 rows=6400 loops=1)
                          Buckets: 8192  Batches: 1  Memory Usage: 380kB
                          -> Index Scan using idx_users_created_at on users u  (cost=0.43..320.00 rows=6400 width=12) (actual time=0.1..3.5 rows=6400 loops=1)
                                Index Cond: (created_at > '2024-01-01'::date)
```

The key numbers to read:

- `cost=X..Y`: planner's estimate. X is startup cost (before first row), Y is total cost. Units are arbitrary (proportional to page reads).
- `rows=N`: planner's estimated row count. Compare with `actual rows=N` to spot misfits.
- `actual time=X..Y`: real milliseconds. X to first row, Y to last row.
- `loops=N`: how many times this node executed. `actual time` is per-loop.

## Why It Matters

The gap between `rows=` (estimate) and `actual rows=` (reality) is your diagnostic signal. A large gap means the planner made decisions based on wrong assumptions. Common causes:

- Stale statistics: run `ANALYZE tablename`
- Highly skewed data: the planner assumes uniform distribution
- Correlated columns: filtering on `(city = 'NYC' AND state = 'NY')` — the planner doesn't know these are correlated
- Functions in WHERE clause: `WHERE lower(email) = $1` prevents index use unless you have a functional index

The node types to recognize:

| Node | Meaning |
|------|---------|
| `Seq Scan` | reads every row — expected for small tables or no useful index |
| `Index Scan` | uses a B-tree, fetches heap pages for each match |
| `Index Only Scan` | uses a B-tree, no heap fetch if all columns are in the index |
| `Bitmap Heap Scan` | collects ctids in a bitmap, then fetches heap pages in order |
| `Hash Join` | builds a hash table from one side, probes with the other |
| `Nested Loop` | for each outer row, looks up matching inner rows — good when outer is small |
| `Merge Join` | merges two sorted streams — requires sorted input |
| `Sort` | explicit sort — check if it spills to disk (`Sort Method: external merge`) |
| `HashAggregate` | in-memory aggregation using a hash table |

## Production Example

Here is a systematic Go function that fetches and logs the query plan for slow queries in development:

```go
func explainQuery(ctx context.Context, db *sql.DB, query string, args ...any) {
    explainSQL := "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query
    row := db.QueryRowContext(ctx, explainSQL, args...)

    var planJSON string
    if err := row.Scan(&planJSON); err != nil {
        log.Printf("EXPLAIN failed: %v", err)
        return
    }
    log.Printf("Query plan:\n%s", planJSON)
}

// In development middleware, log plans for queries over 100ms
func slowQueryMiddleware(db *sql.DB) func(context.Context, string, []any) {
    return func(ctx context.Context, query string, args []any) {
        start := time.Now()
        // ... execute query ...
        if time.Since(start) > 100*time.Millisecond {
            explainQuery(ctx, db, query, args...)
        }
    }
}
```

The `BUFFERS` option adds information about how many shared buffer hits vs disk reads occurred — invaluable for understanding whether a slow query is I/O-bound or CPU-bound:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE user_id = 42 ORDER BY created_at DESC LIMIT 20;

-- Output includes:
-- Buffers: shared hit=5 read=3
-- "hit" = page was in Postgres shared_buffers (fast)
-- "read" = page had to be fetched from disk or OS cache (slow)
```

A query with high `read=` counts is I/O-bound. Adding RAM (increasing `shared_buffers`) or a better index will help. A query with high `hit=` counts but still slow is CPU-bound — the bottleneck is computation, not I/O.

## The Tradeoffs

`EXPLAIN ANALYZE` actually executes the query. For destructive queries (`UPDATE`, `DELETE`), wrap them in a transaction you roll back:

```sql
BEGIN;
EXPLAIN ANALYZE UPDATE orders SET status = 'processed' WHERE status = 'pending';
ROLLBACK;
```

Over-relying on `EXPLAIN` output without understanding the underlying data distribution can mislead you. The planner's estimates are only as good as the statistics it has. Always run `ANALYZE` on tables that have had significant data changes before interpreting `EXPLAIN` output seriously.

Also be aware: a query that is fast in development (small dataset) may use a completely different plan in production (large dataset). The planner's cost thresholds for switching between strategies depend on table size.

## Key Takeaway

`EXPLAIN ANALYZE` is the X-ray of database performance. Learn to read it from the bottom up, compare estimated versus actual row counts, identify node types, and spot the difference between I/O-bound and CPU-bound slowdowns. It is not enough to know that a query is slow — you need to know which node in the plan is doing unexpected work and why. That's what `EXPLAIN ANALYZE` tells you.

---

**Previous:** [Lesson 5: Transaction Isolation](/post/fundamentals/db-isolation-levels/) | **Next:** [Lesson 7: Connection Pooling — What PgBouncer Actually Does](/post/fundamentals/db-connection-pooling/)
