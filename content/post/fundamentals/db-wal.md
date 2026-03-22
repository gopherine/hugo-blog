---
title: 'Lesson 3: Write-Ahead Log — How Databases Survive Crashes'
author: Atharva Pandey
series: "Database Internals for Backend Engineers"
lesson: 3
keywords:
  - database internals
  - write-ahead log
  - WAL
  - crash recovery
  - durability
  - backend engineering
tags:
  - fundamentals
  - databases
date: '2024-05-23T00:00:00.000Z'
---

Databases promise durability. When `INSERT` returns successfully, your data is safe — even if the server loses power a millisecond later. For a long time I accepted this as magic. Then I started reading about what actually happens when Postgres writes data, and the mechanism behind that promise is both elegant and counterintuitive: to make writes safe, you write them twice. The first write goes to a sequential log. The second write goes to the actual data file. And the log is what saves you when things go wrong.

## How It Actually Works

The Write-Ahead Log (WAL) is a sequential, append-only file on disk. Every change to database state — every inserted row, every updated value, every committed transaction — is recorded in the WAL before it is applied to the actual heap files (the data pages).

The rule is strict: **the WAL record must be flushed to durable storage before the operation is considered complete**. Only after the WAL is safely on disk can Postgres acknowledge the write to your application.

The reason this works is that sequential writes are much faster than random writes. Writing a WAL record means appending to the end of a file — the disk head (or SSD write pointer) moves in one direction. Writing to heap files means seeking to the specific page where that row lives — random I/O, much slower.

So the actual sequence for a committed INSERT is:

1. Write the change to the WAL buffer (in memory)
2. Flush the WAL buffer to disk (sequential write — fast)
3. Return success to the client
4. At some later point, write the change to the heap file (the "dirty page flush")

Step 4 is done lazily by Postgres's background writer and checkpoint process. The heap page may not be on disk for seconds or minutes after the INSERT returns. That's fine — because if the server crashes, recovery can replay the WAL to reconstruct exactly where the heap should be.

Here is a simplified Go representation of a WAL append:

```go
type WALRecord struct {
    LSN       uint64    // Log Sequence Number — monotonically increasing
    XID       uint32    // Transaction ID
    Operation string    // INSERT, UPDATE, DELETE, COMMIT
    TableOID  uint32
    TupleData []byte
}

type WAL struct {
    mu      sync.Mutex
    file    *os.File
    nextLSN uint64
}

func (w *WAL) Append(xid uint32, op string, data []byte) (uint64, error) {
    w.mu.Lock()
    defer w.mu.Unlock()

    rec := WALRecord{
        LSN:       w.nextLSN,
        XID:       xid,
        Operation: op,
        TupleData: data,
    }
    w.nextLSN++

    if err := binary.Write(w.file, binary.LittleEndian, rec); err != nil {
        return 0, err
    }
    // fsync ensures the OS page cache is flushed to physical storage
    return rec.LSN, w.file.Sync()
}
```

The Log Sequence Number (LSN) is monotonically increasing and uniquely identifies every WAL record. It is used for replication, point-in-time recovery, and to determine which pages need to be written to disk during a checkpoint.

## Why It Matters

The WAL is the reason Postgres can guarantee durability without writing every change directly to its final location. It also enables several other critical features:

**Crash recovery**: on startup after a crash, Postgres reads the WAL from the last checkpoint forward and replays any changes that hadn't been flushed to the heap. This is called REDO recovery.

**Replication**: streaming replication works by shipping WAL records to replica servers. The replica applies those records to its own copy of the heap, staying in sync with the primary.

**Point-in-time recovery (PITR)**: by archiving WAL files, you can restore a database to any point in time — not just from a base backup but from any moment between backups. This is how Postgres provides RPO (Recovery Point Objective) measured in seconds.

**Logical decoding**: tools like Debezium and pglogical read the WAL to stream changes to Kafka or other systems. This is change data capture (CDC).

## Production Example

`synchronous_commit` is a Postgres setting that controls when WAL flush happens relative to returning success to the client. It is one of the most important tuning knobs for write-heavy workloads:

```sql
-- Default: wait for WAL to be flushed to local disk before acknowledging
SET synchronous_commit = on;

-- Faster: return success after WAL is written to OS buffer, not flushed
-- Risk: up to wal_writer_delay (200ms) of data loss on crash
SET synchronous_commit = off;

-- For replication: wait until replica has confirmed WAL receipt
SET synchronous_commit = remote_apply;
```

In Go, you can set this per-transaction for operations where you can tolerate a small window of data loss in exchange for much higher throughput:

```go
// For high-volume analytics events where occasional loss is acceptable
tx, err := db.BeginTx(ctx, nil)
if err != nil {
    return err
}
defer tx.Rollback()

if _, err := tx.ExecContext(ctx, "SET LOCAL synchronous_commit = off"); err != nil {
    return err
}

if _, err := tx.ExecContext(ctx,
    "INSERT INTO page_views (user_id, url, created_at) VALUES ($1, $2, NOW())",
    userID, url,
); err != nil {
    return err
}

return tx.Commit()
```

For financial transactions, payment records, and anything where data loss is unacceptable, always use the default `synchronous_commit = on`.

## The Tradeoffs

WAL introduces overhead in a few ways:

**Write amplification**: every change is written twice — once to WAL, once eventually to heap. On SSD-heavy systems this matters less than it used to, but it is still real.

**WAL size and archiving**: WAL files accumulate. You need a strategy for archiving them (for PITR) and for removing old files that are no longer needed for recovery. `archive_command` in `postgresql.conf` handles archiving; `wal_keep_size` controls local retention.

**`fsync = off` is dangerous**: some guides suggest disabling `fsync` for performance in test environments. This bypasses the WAL durability guarantee entirely — the database will recover to an indeterminate state after a crash. Never disable it in production.

**Checkpoint distance**: the gap between the last checkpoint and the WAL tip determines crash recovery time. `max_wal_size` controls how large this gap can be. A larger value means fewer checkpoints (less I/O pressure) but longer recovery after a crash.

## Key Takeaway

The Write-Ahead Log is the foundation of database durability. By committing changes to a sequential log before touching heap pages, Postgres gets both speed and safety. The WAL enables crash recovery, streaming replication, and point-in-time restore. Understanding it explains why durability guarantees hold, when `synchronous_commit` is a valid tradeoff, and how replication fundamentally works.

---

**Previous:** [Lesson 2: B-Tree Indexes](/post/fundamentals/db-btree-indexes/) | **Next:** [Lesson 4: MVCC — How Postgres Handles Concurrent Reads and Writes](/post/fundamentals/db-mvcc/)
