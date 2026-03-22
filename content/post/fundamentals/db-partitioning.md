---
title: 'Lesson 9: Partitioning — Range, Hash, List and When Each Helps'
author: Atharva Pandey
series: "Database Internals for Backend Engineers"
lesson: 9
keywords:
  - database internals
  - table partitioning
  - range partitioning
  - hash partitioning
  - list partitioning
  - Postgres partitioning
  - backend engineering
tags:
  - fundamentals
  - databases
date: '2024-08-24T00:00:00.000Z'
---

I have worked with a few systems that had grown their main tables to 500 million rows or more. At that scale, even well-indexed queries start slowing down — not because the indexes are wrong, but because the index itself becomes large and the buffer cache can only hold so many pages. Vacuum struggles to keep up. `EXPLAIN` output looks fine but queries still feel sluggish. Partitioning is the architectural solution to this class of problem: instead of one big table, you have many smaller tables that look like one from the application's perspective.

## How It Actually Works

Postgres declarative partitioning (introduced in version 10, matured in versions 11–13) lets you define a parent table and attach child tables as partitions. Queries against the parent automatically route to the relevant partitions.

There are three partition strategies:

**Range Partitioning**: rows are assigned to partitions based on a range of values of the partition key. Most commonly used with timestamps.

```sql
CREATE TABLE events (
    id          bigserial,
    user_id     bigint NOT NULL,
    event_type  text NOT NULL,
    created_at  timestamptz NOT NULL,
    payload     jsonb
) PARTITION BY RANGE (created_at);

CREATE TABLE events_2024_01 PARTITION OF events
    FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');

CREATE TABLE events_2024_02 PARTITION OF events
    FOR VALUES FROM ('2024-02-01') TO ('2024-03-01');
-- ... and so on
```

**Hash Partitioning**: rows are assigned by computing a hash of the partition key, then using the modulus. Distributes rows evenly regardless of key values.

```sql
CREATE TABLE sessions (
    id      uuid DEFAULT gen_random_uuid(),
    user_id bigint NOT NULL,
    data    jsonb
) PARTITION BY HASH (user_id);

CREATE TABLE sessions_0 PARTITION OF sessions
    FOR VALUES WITH (MODULUS 4, REMAINDER 0);
CREATE TABLE sessions_1 PARTITION OF sessions
    FOR VALUES WITH (MODULUS 4, REMAINDER 1);
-- ... sessions_2, sessions_3
```

**List Partitioning**: rows are assigned based on explicit enumerated values of the partition key. Useful for known, bounded categories.

```sql
CREATE TABLE orders (
    id       bigserial,
    region   text NOT NULL,
    amount   numeric
) PARTITION BY LIST (region);

CREATE TABLE orders_us   PARTITION OF orders FOR VALUES IN ('US');
CREATE TABLE orders_eu   PARTITION OF orders FOR VALUES IN ('DE', 'FR', 'NL', 'GB');
CREATE TABLE orders_apac PARTITION OF orders FOR VALUES IN ('JP', 'AU', 'SG');
```

When a query includes a filter on the partition key, Postgres's partition pruning eliminates partitions that cannot contain matching rows. A query for `WHERE created_at BETWEEN '2024-03-01' AND '2024-03-31'` only scans `events_2024_03` — skipping all other partitions entirely.

## Why It Matters

Partitioning helps in three specific situations:

**Time-series data with retention policies**: if you partition by month and your retention is 12 months, dropping old data is `DROP TABLE events_2023_01` — instantaneous, compared to `DELETE FROM events WHERE created_at < '2024-01-01'` which is a full table scan + massive dead tuple cleanup.

**Index size**: each partition has its own indexes. A B-Tree index on `events_2024_03` covers only one month of data — much smaller than an index on the full table. Smaller indexes fit better in the buffer cache and traverse fewer levels.

**Vacuum efficiency**: autovacuum works on one partition at a time. Dead tuples from January don't slow down vacuum of March's data.

**Parallel query**: Postgres can scan multiple partitions in parallel, effectively increasing throughput for queries that touch many partitions.

## Production Example

In Go, partitioning is transparent to your application — you query the parent table as normal:

```go
// This query automatically routes to the correct partition
// No application-side changes needed
rows, err := db.QueryContext(ctx, `
    SELECT id, user_id, event_type, payload
    FROM events
    WHERE user_id = $1
      AND created_at >= $2
      AND created_at < $3
    ORDER BY created_at DESC
    LIMIT 100
`, userID, startTime, endTime)
```

But you do need to manage partition creation proactively. I use a background job that creates next month's partition in advance:

```go
func ensurePartitionExists(ctx context.Context, db *sql.DB, month time.Time) error {
    start := month.UTC().Truncate(24 * time.Hour).AddDate(0, 0, -month.Day()+1)
    end := start.AddDate(0, 1, 0)
    partName := fmt.Sprintf("events_%s", start.Format("2006_01"))

    _, err := db.ExecContext(ctx, fmt.Sprintf(`
        CREATE TABLE IF NOT EXISTS %s PARTITION OF events
            FOR VALUES FROM ('%s') TO ('%s')
    `, partName, start.Format(time.RFC3339), end.Format(time.RFC3339)))
    return err
}

// Run this on startup and as a monthly cron job
func (s *Scheduler) EnsureFuturePartitions(ctx context.Context) error {
    now := time.Now()
    for i := 0; i < 3; i++ { // create partitions 3 months ahead
        month := now.AddDate(0, i, 0)
        if err := ensurePartitionExists(ctx, s.db, month); err != nil {
            return fmt.Errorf("partition for %s: %w", month.Format("2006-01"), err)
        }
    }
    return nil
}
```

For dropping old partitions:

```go
func dropOldPartition(ctx context.Context, db *sql.DB, month time.Time) error {
    partName := fmt.Sprintf("events_%s", month.Format("2006_01"))
    _, err := db.ExecContext(ctx, fmt.Sprintf("DROP TABLE IF EXISTS %s", partName))
    return err
}
```

## The Tradeoffs

Partitioning is not free and is not always the right answer:

**Partition pruning requires the partition key in the query**: if you query `events` without filtering on `created_at`, Postgres scans all partitions. Worse than an unpartitioned table with a good index.

**Cross-partition queries are slower**: a `SELECT *` across all partitions hits every child table. The planner must plan N queries and merge results.

**Unique constraints must include the partition key**: you cannot have a `UNIQUE (id)` on a partitioned table — only `UNIQUE (id, created_at)`. This affects foreign keys too.

**Operational complexity**: you must manage partition creation and deletion. Forget to create next month's partition and inserts fail.

**Not a substitute for indexes**: partitioning reduces the search space; indexes traverse within that space. You still need indexes on each partition.

The right candidates for partitioning: tables with hundreds of millions of rows or more, time-series data with drop-based retention, and tables where queries are almost always scoped to a partition key range.

## Key Takeaway

Table partitioning splits a large logical table into smaller physical tables. Range partitioning by time is the most common pattern — it enables instant partition drops for retention, smaller per-partition indexes, and efficient time-scoped queries. Hash partitioning distributes rows evenly across a fixed number of partitions. List partitioning groups by enumerated values. The benefit requires that most queries filter on the partition key; without that, partitioning can make things worse.

---

**Previous:** [Lesson 8: Replication](/post/fundamentals/db-replication/) | **Next:** [Lesson 10: Vacuum and Bloat — Why Postgres Tables Grow](/post/fundamentals/db-vacuum-bloat/)
