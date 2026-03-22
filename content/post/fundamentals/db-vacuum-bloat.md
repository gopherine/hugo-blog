---
title: 'Lesson 10: Vacuum and Bloat — Why Postgres Tables Grow'
author: Atharva Pandey
series: "Database Internals for Backend Engineers"
lesson: 10
keywords:
  - database internals
  - Postgres vacuum
  - table bloat
  - autovacuum
  - dead tuples
  - MVCC
  - backend engineering
tags:
  - fundamentals
  - databases
date: '2024-09-09T00:00:00.000Z'
---

I watched a Postgres instance run out of disk space on a 500 GB SSD. The database had 50 GB of actual data. The other 450 GB was table bloat — dead row versions from MVCC that vacuum had failed to clean up. A batch job had been running long-running transactions for weeks, holding a transaction horizon that prevented vacuum from reclaiming anything. By the time we noticed, the disk was nearly full and autovacuum was struggling to catch up. Understanding why this happens — and how to prevent it — is one of the most important operational skills for running Postgres in production.

## How It Actually Works

Recall from Lesson 4: MVCC works by keeping old row versions on disk after updates and deletes. An `UPDATE` creates a new row version and marks the old one dead with an `xmax`. A `DELETE` marks the row dead without creating a new version. Neither operation removes data from disk immediately.

The role of **vacuum** is to:
1. Scan heap pages and find dead row versions
2. Mark them as free space (so future inserts can reuse those pages)
3. Update the **Free Space Map (FSM)** and **Visibility Map (VM)**
4. Advance the **transaction horizon** so old XIDs can be recycled
5. Update statistics for the planner

What vacuum does NOT do (by default): return free space to the operating system. Pages that were freed remain allocated to the table. This is table bloat — the table file on disk is large, but many pages are mostly empty. Future inserts reuse those pages, so the bloat doesn't grow forever — but it also doesn't shrink unless you run `VACUUM FULL` or `pg_repack`.

**Autovacuum** is a background process that triggers vacuum automatically when a table's dead tuple count exceeds a threshold:

```
autovacuum fires when:
  n_dead_tup > autovacuum_vacuum_threshold + autovacuum_vacuum_scale_factor × n_live_tup
  default: 50 + 0.2 × table_size
```

For a table with 10 million rows, autovacuum fires after 2 million dead tuples accumulate. That 20% threshold was reasonable in 2005 with smaller datasets. For modern tables, it is often too conservative.

## Why It Matters

Bloat has two distinct costs:

**Disk space**: tables can grow to 2–10× their logical data size. This wastes storage and costs money.

**Query performance**: more bloat means more pages to scan. A sequential scan of a bloated table reads all those half-empty pages, even the ones with no live rows. Index bloat means B-Tree traversals read more pages to reach valid data.

**Transaction ID wraparound**: Postgres uses 32-bit transaction IDs. After roughly 2 billion transactions, XIDs wrap around. Postgres goes into "transaction freeze" mode — the entire cluster becomes read-only until you vacuum. This is catastrophic and completely avoidable. Autovacuum monitors for this and aggressively vacuums tables with old XIDs (`relfrozenxid` in `pg_class`).

## Production Example

Monitoring dead tuples and bloat should be part of your standard observability:

```sql
-- Tables with high dead tuple counts
SELECT
    schemaname,
    relname AS tablename,
    n_live_tup,
    n_dead_tup,
    round(100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0), 1) AS dead_pct,
    last_autovacuum,
    last_autoanalyze
FROM pg_stat_user_tables
WHERE n_dead_tup > 100000
ORDER BY n_dead_tup DESC
LIMIT 20;

-- Check for long-running transactions holding the vacuum horizon
SELECT
    pid,
    now() - xact_start AS txn_age,
    state,
    query
FROM pg_stat_activity
WHERE xact_start IS NOT NULL
ORDER BY xact_start ASC
LIMIT 5;
```

For write-heavy tables, tuning autovacuum per-table is far more effective than changing global settings:

```sql
-- Aggressive autovacuum for a high-churn events table
ALTER TABLE events SET (
    autovacuum_vacuum_scale_factor = 0.01,  -- fire at 1% dead tuples instead of 20%
    autovacuum_vacuum_threshold = 1000,      -- minimum 1000 dead tuples
    autovacuum_analyze_scale_factor = 0.005, -- keep statistics fresh
    autovacuum_vacuum_cost_delay = 2         -- less throttling (default 2ms, was 20ms)
);
```

In Go, proactively closing idle transactions is critical:

```go
// Always set a statement timeout to kill runaway queries
db.ExecContext(ctx, "SET statement_timeout = '30s'")

// Always set idle_in_transaction_session_timeout
// This kills sessions that started a transaction but forgot to commit/rollback
db.ExecContext(ctx, "SET idle_in_transaction_session_timeout = '60s'")
```

Or set these in the connection string:
```go
dsn := "postgres://user:pass@host/db?statement_timeout=30000&idle_in_transaction_session_timeout=60000"
```

For tables that have become heavily bloated and autovacuum can't keep up, `pg_repack` reclaims disk space without an exclusive lock (unlike `VACUUM FULL`):

```bash
# Repack a bloated table online
pg_repack --table orders --dbname myapp
```

## The Tradeoffs

**`VACUUM FULL`**: reclaims disk space and returns it to the OS by rewriting the entire table. Requires an exclusive lock for the duration — your table is unavailable for writes. For large tables, this can take hours. Use `pg_repack` instead in production.

**Aggressive autovacuum**: running autovacuum more aggressively increases I/O. On I/O-constrained instances, this can impact query performance. Balance `autovacuum_vacuum_cost_delay` (throttle on I/O) against `autovacuum_vacuum_cost_limit` (how much work before throttling).

**Partitioning as a vacuum aid**: partitioned tables vacuum each partition independently. A busy monthly partition is vacuumed without affecting other months. For time-series data with drop-based retention (Lesson 9), partitioning eliminates the vacuum problem entirely for old data — you just drop the partition.

**Long-running transactions are the enemy**: a single transaction open for hours holds the MVCC horizon at that point in time. Nothing older than that transaction can be vacuumed. Set `idle_in_transaction_session_timeout` to terminate forgotten transactions automatically.

## Key Takeaway

Postgres tables grow because MVCC keeps dead row versions until vacuum reclaims them. Autovacuum handles this automatically, but its defaults are conservative and its effectiveness depends on no long-running transactions blocking the vacuum horizon. Monitor dead tuple counts, tune autovacuum per-table for write-heavy workloads, set idle-in-transaction timeouts to prevent horizon stalls, and prefer `pg_repack` over `VACUUM FULL` for online bloat reclamation.

---

**Previous:** [Lesson 9: Partitioning](/post/fundamentals/db-partitioning/)

---

🎓 **Course Complete — Database Internals for Backend Engineers**

You've covered the full lifecycle of data in Postgres: how queries execute, how B-Tree indexes accelerate lookups, how WAL ensures durability, how MVCC enables non-blocking concurrency, how isolation levels protect correctness, how to read execution plans, how connections are pooled, how replication works, how partitioning manages scale, and how vacuum keeps the engine clean. This is the foundation for every performance investigation, architecture decision, and operational challenge you will face with Postgres.
