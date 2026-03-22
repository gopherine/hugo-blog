---
title: 'Lesson 1: How a Query Executes — Parser to Planner to Disk'
author: Atharva Pandey
series: "Database Internals for Backend Engineers"
lesson: 1
keywords:
  - database internals
  - query execution
  - query planner
  - SQL parser
  - backend engineering
tags:
  - fundamentals
  - databases
date: '2024-04-22T00:00:00.000Z'
---

Every time I call `db.QueryContext(ctx, "SELECT * FROM orders WHERE user_id = $1", userID)` in Go, I used to think the database just "found" the rows. It wasn't until I started debugging a production slowdown — a query that was fast for months and then suddenly wasn't — that I actually traced what happens between the moment my application hands off that SQL string and the moment rows come back. Understanding that pipeline changed how I write queries, design schemas, and diagnose performance problems.

## How It Actually Works

A SQL query goes through five distinct stages before you see a single row. Let me walk through each one.

**1. Parsing**

The database receives your query as a plain string. The parser tokenizes it and builds an Abstract Syntax Tree (AST). At this point, it validates syntax only — it does not know whether `orders` exists or whether `user_id` is a valid column. A syntax error here will fail fast.

**2. Analysis (Semantic Analysis)**

The analyzer resolves names against the system catalog. It checks that `orders` is a real table, that `user_id` is a column of the right type, that `$1` is compatible. It also resolves `*` into the actual column list. This stage produces a normalized query tree with all names resolved to object identifiers.

**3. Rewriting**

Postgres's rule system can rewrite queries before planning. Views are expanded here — a query against a view becomes a query against the underlying tables. Row-level security policies are injected as additional `WHERE` predicates at this stage.

**4. Planning and Optimization**

This is where the interesting decisions happen. The planner enumerates possible execution strategies, estimates the cost of each using table statistics (row counts, value distributions, index availability), and picks the cheapest plan. It decides whether to use an index or do a sequential scan, whether to use a hash join or a nested loop, and in what order to join multiple tables.

**5. Execution**

The executor walks the plan tree and pulls rows. Each node in the tree — a SeqScan, an IndexScan, a Hash Join, a Sort — is an operator that feeds rows upward to its parent. For a `SELECT`, rows bubble all the way up to the client. For a `DELETE` or `UPDATE`, the final node writes changes to heap pages and WAL.

Here is a simplified Go analogy of how the planner works conceptually:

```go
type Plan interface {
    Execute(ctx context.Context) ([]Row, error)
}

type SeqScan struct {
    Table  string
    Filter func(Row) bool
}

func (s *SeqScan) Execute(ctx context.Context) ([]Row, error) {
    // reads every page from disk, applies filter
    var result []Row
    for _, row := range readAllPages(s.Table) {
        if s.Filter(row) {
            result = append(result, row)
        }
    }
    return result, nil
}

type IndexScan struct {
    Index string
    Key   any
}

func (i *IndexScan) Execute(ctx context.Context) ([]Row, error) {
    // traverses B-tree, fetches matching heap pages
    ctids := lookupIndex(i.Index, i.Key)
    return fetchHeapPages(ctids), nil
}
```

The planner's job is to decide which `Plan` implementation to use. Get it wrong and you do 10 million row scans instead of a 3-level tree traversal.

## Why It Matters

Understanding this pipeline explains several things that surprised me early in my career:

- **Why `PREPARE` helps with repeated queries**: you skip parsing and analysis on every execution, and the plan can be cached.
- **Why query plans change after a schema change or data distribution shift**: the planner's statistics are stale, and it re-plans with old estimates.
- **Why `EXPLAIN` is indispensable**: it shows you the plan the planner chose, along with cost estimates and actual row counts.

When I was debugging that production slowdown, the query was against an `events` table that had grown from 1 million to 50 million rows over three months. The planner's statistics hadn't been refreshed, so it thought the table was still small and kept choosing a nested loop join that was efficient at 1 million rows but catastrophic at 50 million. Running `ANALYZE events` refreshed the statistics, the planner switched to a hash join, and the query dropped from 12 seconds to 80 milliseconds.

## Production Example

In Go, when you use `database/sql`, your query goes through all five stages on the database side. But you can give the planner better information by using prepared statements:

```go
// Prepare once, execute many times — skips parse + analyze on each call
stmt, err := db.PrepareContext(ctx, `
    SELECT id, created_at, total_cents
    FROM orders
    WHERE user_id = $1
      AND created_at > $2
    ORDER BY created_at DESC
    LIMIT 50
`)
if err != nil {
    return fmt.Errorf("prepare orders query: %w", err)
}
defer stmt.Close()

rows, err := stmt.QueryContext(ctx, userID, since)
```

On the Postgres side, you can inspect what the planner decided by prefixing with `EXPLAIN ANALYZE`:

```sql
EXPLAIN ANALYZE
SELECT id, created_at, total_cents
FROM orders
WHERE user_id = 42
  AND created_at > '2024-01-01'
ORDER BY created_at DESC
LIMIT 50;
```

The output will tell you whether the planner chose an index scan on `(user_id, created_at)` or fell back to a sequential scan with a filter, and crucially, how many rows it estimated versus how many it actually processed.

## The Tradeoffs

The planner uses statistics, not certainty. It estimates. Those estimates can be wrong when:

- Data is highly skewed (most rows have `user_id = 1`, but the query filters on `user_id = 99999` which has 3 rows — the planner may guess 500)
- Statistics are stale (table hasn't been `ANALYZE`d since bulk inserts)
- You have complex predicates with correlated columns
- You're querying across many joined tables (the search space for join ordering is exponential)

You can influence planning with hints via `pg_hint_plan`, by adjusting `work_mem` for sort and hash operations, or by rewriting queries to make the planner's job easier. But the first step is always understanding what the planner is seeing.

## Key Takeaway

A SQL query is not a direct instruction — it is a declaration of what you want. The database decides how to get it. The planner is one of the most sophisticated pieces of software in your stack, and it works best when you give it accurate statistics and clear predicates. Learn to read `EXPLAIN ANALYZE` output. It is the difference between guessing and knowing why a query is slow.

---

**Next:** [Lesson 2: B-Tree Indexes — O(n) to O(log n) in one CREATE INDEX](/post/fundamentals/db-btree-indexes/)
