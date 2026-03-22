---
title: 'Lesson 2: B-Tree Indexes — O(n) to O(log n) in one CREATE INDEX'
author: Atharva Pandey
series: "Database Internals for Backend Engineers"
lesson: 2
keywords:
  - database internals
  - B-tree index
  - index scan
  - query optimization
  - backend engineering
tags:
  - fundamentals
  - databases
date: '2024-05-08T00:00:00.000Z'
---

I once joined a team that had a `users` table with 8 million rows. The `GET /users/{email}` endpoint was consistently timing out under load. When I ran `EXPLAIN ANALYZE` on the query, I saw it: `Seq Scan on users (cost=0.00..180000.00 rows=1 width=120) (actual rows=1 loops=1)`. It was reading every single row — all 8 million of them — to find one user by email. Adding a single index fixed it in under five minutes. That experience made me want to actually understand what an index is, not just that it makes things faster.

## How It Actually Works

A B-Tree (Balanced Tree) index is a separate data structure maintained alongside your table. Every time you insert, update, or delete a row, Postgres also updates any B-Tree indexes defined on that table's columns.

The structure looks like this:

- **Root node**: the top-level entry point, contains separator keys that point to child nodes
- **Internal nodes**: branch pages that route lookups toward leaf pages
- **Leaf nodes**: contain the actual indexed values plus a pointer (called a `ctid` in Postgres) to the heap page and row offset where the full row lives

For a query like `WHERE email = 'alice@example.com'`, Postgres:
1. Starts at the root node (always in memory or buffer cache)
2. Compares the search key against separator keys to pick a child
3. Descends through internal nodes — typically 2 to 4 levels for even very large tables
4. Reaches the leaf node, finds the `ctid` pointer
5. Fetches the actual row from the heap using that pointer

This traversal is `O(log n)`. For 8 million rows, that's roughly 23 comparisons instead of 8 million.

Here is a stripped-down Go simulation of how a B-Tree lookup works:

```go
type BTreeNode struct {
    Keys     []string
    Values   []int64 // heap ctids (simplified as row IDs)
    Children []*BTreeNode
    IsLeaf   bool
}

func (n *BTreeNode) Search(key string) (int64, bool) {
    if n.IsLeaf {
        for i, k := range n.Keys {
            if k == key {
                return n.Values[i], true
            }
        }
        return 0, false
    }

    // find the right child to descend into
    i := 0
    for i < len(n.Keys) && key > n.Keys[i] {
        i++
    }
    return n.Children[i].Search(key)
}
```

Each node corresponds to one 8KB page on disk. Because they're fixed-size, the tree stays balanced as data grows, and the height increases logarithmically rather than linearly.

## Why It Matters

Without an index, every query that filters on a column must read every row. This is a sequential scan. It's fast for small tables (a few thousand rows), but at millions of rows it becomes the primary source of latency in OLTP workloads.

The difference is not marginal. On that `users` table:

| Operation | Without index | With index on `email` |
|-----------|--------------|----------------------|
| Lookup by email | ~800ms | ~2ms |
| Reads from disk | 8,000,000 rows | ~4 leaf pages |

Indexes are also used for:
- **Range queries**: `WHERE created_at BETWEEN '2024-01-01' AND '2024-02-01'` — the B-Tree's sorted order means Postgres scans a contiguous range of leaf pages
- **ORDER BY**: if your sort column is indexed, Postgres can skip an explicit sort step
- **JOIN conditions**: the right side of a JOIN often benefits enormously from an index on the join key
- **Uniqueness enforcement**: `UNIQUE` constraints are implemented as B-Tree indexes

## Production Example

Composite indexes — indexes on multiple columns — are particularly powerful and frequently misunderstood. The key rule is **column order matters**: the index is only usable for queries that filter on a prefix of the indexed columns.

```sql
-- This composite index supports:
-- WHERE user_id = ?
-- WHERE user_id = ? AND created_at > ?
-- But NOT: WHERE created_at > ?  (user_id is missing from the filter)
CREATE INDEX idx_orders_user_created ON orders (user_id, created_at DESC);
```

In Go, when you're using a query like this, you want to make sure you always include `user_id` in the filter:

```go
// Good: uses the composite index fully
rows, err := db.QueryContext(ctx, `
    SELECT id, total_cents, created_at
    FROM orders
    WHERE user_id = $1
      AND created_at > $2
    ORDER BY created_at DESC
    LIMIT 20
`, userID, since)

// Bad: can't use the index because user_id is missing
rows, err := db.QueryContext(ctx, `
    SELECT id, total_cents, created_at
    FROM orders
    WHERE created_at > $1
    ORDER BY created_at DESC
    LIMIT 20
`, since)
```

You can also use partial indexes to index only a subset of rows, which keeps the index small and fast:

```sql
-- Only index unprocessed jobs — the hot path
CREATE INDEX idx_jobs_pending ON jobs (created_at)
WHERE status = 'pending';
```

## The Tradeoffs

Indexes are not free:

**Write overhead**: every `INSERT`, `UPDATE`, or `DELETE` must also update all relevant indexes. A table with 10 indexes takes 10x the write I/O compared to 1 index. For write-heavy tables (logs, events, telemetry), this matters.

**Storage**: indexes take disk space. A B-Tree index on a large text column can be almost as large as the table itself.

**Over-indexing**: I've seen tables with 15 indexes where only 3 were actually used. The unused indexes still cost write performance and vacuum time. Use `pg_stat_user_indexes` to find indexes with zero scans.

```sql
SELECT schemaname, tablename, indexname, idx_scan
FROM pg_stat_user_indexes
WHERE idx_scan = 0
ORDER BY schemaname, tablename;
```

**Index bloat**: after heavy updates and deletes, B-Tree pages can become partially empty. `REINDEX` or `VACUUM` reclaims this space.

The right mental model is: indexes are a read-optimized cache of column data. They pay for themselves when reads outnumber writes on indexed columns by a significant margin.

## Key Takeaway

A B-Tree index transforms a linear scan into a logarithmic traversal by maintaining a sorted, balanced tree structure alongside your table data. The cost is write overhead and storage. The benefit is query performance that scales with table growth instead of degrading. Add indexes where you filter, join, and sort — and audit unused indexes periodically to remove the ones that are only costing you writes.

---

**Previous:** [Lesson 1: How a Query Executes](/post/fundamentals/db-query-execution/) | **Next:** [Lesson 3: Write-Ahead Log — How Databases Survive Crashes](/post/fundamentals/db-wal/)
