---
title: 'Lesson 4: MVCC — How Postgres Handles Concurrent Reads and Writes'
author: Atharva Pandey
series: "Database Internals for Backend Engineers"
lesson: 4
keywords:
  - database internals
  - MVCC
  - multiversion concurrency control
  - Postgres concurrency
  - transactions
  - backend engineering
tags:
  - fundamentals
  - databases
date: '2024-06-09T00:00:00.000Z'
---

One thing that puzzled me early on was how Postgres could let me read from a table while someone else was writing to it — without locking me out and without me seeing half-written data. Most systems I had worked with used explicit read locks, which meant readers and writers had to take turns. Postgres doesn't do that. Reads never block writes, and writes never block reads. The mechanism that makes this possible is called Multiversion Concurrency Control, or MVCC, and it works by keeping multiple versions of every row simultaneously.

## How It Actually Works

Every row in a Postgres heap has two hidden system columns: `xmin` and `xmax`.

- `xmin`: the transaction ID (XID) of the transaction that inserted this row version
- `xmax`: the transaction ID of the transaction that deleted or updated this row version (0 if the row is current)

When you `UPDATE` a row, Postgres does not modify the existing row in place. Instead it:
1. Creates a new row version with the updated values and sets its `xmin` to your transaction's XID
2. Sets the `xmax` of the old row version to your transaction's XID
3. Leaves both versions on disk

When you `DELETE` a row, Postgres just sets `xmax` on the existing row — the row data stays on disk until vacuum comes along to reclaim it.

This means at any given moment, a heap page may contain multiple versions of the same logical row.

When your transaction reads a row, the visibility check determines which version you see:

```
A row version is visible to transaction T if:
  xmin committed before T started
  AND (xmax is 0, OR xmax belongs to T itself, OR xmax committed after T started)
```

Here is that logic expressed in Go:

```go
type RowVersion struct {
    Data []byte
    Xmin uint32 // transaction that created this version
    Xmax uint32 // transaction that deleted/updated this version (0 = current)
}

type TransactionSnapshot struct {
    MyXID    uint32
    XminHorizon uint32 // oldest active transaction
    ActiveXIDs  map[uint32]bool
}

func (snap *TransactionSnapshot) IsVisible(row RowVersion) bool {
    // inserting transaction must be committed and before our snapshot
    if row.Xmin >= snap.MyXID || snap.ActiveXIDs[row.Xmin] {
        return false // not yet committed when we started
    }
    // if no deleter, row is alive
    if row.Xmax == 0 {
        return true
    }
    // row is deleted — visible only if the deleter started after us
    if row.Xmax >= snap.MyXID || snap.ActiveXIDs[row.Xmax] {
        return true // deleter wasn't committed when we started
    }
    return false // deleter committed before us — row is gone for us too
}
```

Each transaction gets a consistent snapshot at start time. It sees the world as it existed at that moment, regardless of what other transactions do afterward.

## Why It Matters

MVCC delivers the "readers don't block writers" guarantee that makes Postgres suitable for OLTP workloads. In a lock-based system, a long-running read (like a reporting query) would block writes on the same rows for its entire duration. With MVCC, the reporting query sees its snapshot version of rows, while concurrent writes create new versions alongside the old ones.

This also means:
- `SELECT` statements in Postgres **never acquire row locks** by default
- Long-running transactions hold onto old row versions, preventing vacuum from cleaning them up (the "transaction horizon" problem)
- `pg_dump` is just a long-running transaction — it takes a snapshot and reads consistent data even while the database is being written to

## Production Example

You can observe MVCC directly using the `xmin` and `xmax` system columns:

```sql
-- Create a row and check its system columns
INSERT INTO products (name, price_cents) VALUES ('Widget', 999);

SELECT xmin, xmax, name, price_cents FROM products WHERE name = 'Widget';
-- xmin: 12345, xmax: 0, name: Widget, price_cents: 999

-- Update the row in a transaction — check from another session before committing
BEGIN;
UPDATE products SET price_cents = 1099 WHERE name = 'Widget';
-- From another session: still see xmin: 12345, price_cents: 999
-- Both versions exist on disk right now
COMMIT;
-- Now the new version (xmin: 12346, price_cents: 1099) is visible
-- Old version (xmin: 12345) is now dead, eligible for vacuum
```

In Go, the pattern to be aware of is keeping transactions short to avoid holding a snapshot that prevents vacuum from cleaning dead rows:

```go
// Bad: holds an MVCC snapshot for the duration of slow processing
tx, _ := db.BeginTx(ctx, nil)
rows, _ := tx.QueryContext(ctx, "SELECT id FROM large_table")
for rows.Next() {
    // slow external API call — holding snapshot for minutes
    callExternalAPI(id)
}
tx.Commit()

// Better: collect IDs first, close transaction, then process
var ids []int64
func() {
    tx, _ := db.BeginTx(ctx, nil)
    defer tx.Commit()
    rows, _ := tx.QueryContext(ctx, "SELECT id FROM large_table")
    for rows.Next() {
        var id int64
        rows.Scan(&id)
        ids = append(ids, id)
    }
}()

for _, id := range ids {
    callExternalAPI(id) // transaction is done, no snapshot held
}
```

## The Tradeoffs

MVCC has a significant cost: **table bloat**. Dead row versions accumulate on heap pages. Old versions can only be reclaimed once no active transaction could possibly need to see them. The vacuum process does this cleanup, but if you have long-running transactions or idle-in-transaction sessions, old versions pile up.

Signs of MVCC-related bloat:
- `pg_stat_user_tables.n_dead_tup` climbing without dropping
- Table size growing despite no net increase in row count
- Queries slowing down because heap pages are full of dead rows and fewer live rows fit per page I/O

The fix is a combination of: keeping transactions short, tuning autovacuum to run more aggressively, and watching for idle connections holding snapshots. Lesson 10 in this series goes deep on vacuum.

MVCC also means that `UPDATE` is more expensive than in some other databases, because it writes a new row version rather than modifying in place. High-update-rate workloads (like counters or frequently-updated status fields) deserve careful design.

## Key Takeaway

MVCC is how Postgres achieves non-blocking reads and writes. By keeping multiple versions of rows and using transaction IDs to determine visibility, each transaction sees a consistent snapshot of the database at its start time. The cost is dead tuple accumulation that vacuum must clean up. Keep transactions short, watch dead tuple counts, and you get the full benefit of MVCC without the bloat.

---

**Previous:** [Lesson 3: Write-Ahead Log](/post/fundamentals/db-wal/) | **Next:** [Lesson 5: Transaction Isolation — Read Committed vs Serializable](/post/fundamentals/db-isolation-levels/)
