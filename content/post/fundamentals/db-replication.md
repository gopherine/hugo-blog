---
title: 'Lesson 8: Replication — Streaming, Logical, and Failover'
author: Atharva Pandey
series: "Database Internals for Backend Engineers"
lesson: 8
keywords:
  - database internals
  - Postgres replication
  - streaming replication
  - logical replication
  - failover
  - high availability
  - backend engineering
tags:
  - fundamentals
  - databases
date: '2024-08-09T00:00:00.000Z'
---

The first time I had to fail over a Postgres primary, it was 2 AM, the primary was not responding, and I genuinely did not know whether the replica was up to date or 10 minutes behind. We recovered, but that experience drove me to actually understand replication — not just "the replica gets the writes somehow" but exactly what data is shipped, when it arrives, and what happens when the primary dies. It turns out the mechanism is elegant and directly connected to the WAL we covered in Lesson 3.

## How It Actually Works

Postgres replication ships WAL records from the primary to replicas. This is not a logical "here's the SQL that was executed" stream — it is the physical binary changes to heap pages and index pages.

**Streaming Replication**

The primary runs a WAL sender process. Each replica runs a WAL receiver process. The replica connects to the primary's WAL sender, establishes a replication slot, and receives WAL records as they are produced.

The replica's recovery process applies those WAL records to its own local heap files, exactly replaying the operations the primary performed. The replica's data files are a byte-for-byte copy of the primary's.

The key metric is **replication lag**: how far behind in WAL is the replica compared to the primary. You can query this on the primary:

```sql
SELECT
    application_name,
    state,
    sent_lsn,
    write_lsn,
    flush_lsn,
    replay_lsn,
    (sent_lsn - replay_lsn) AS lag_bytes
FROM pg_stat_replication;
```

- `sent_lsn`: how much WAL the primary has sent to the replica
- `flush_lsn`: how much the replica has flushed to its own disk
- `replay_lsn`: how much the replica has actually applied to its heap pages

**Synchronous vs Asynchronous Replication**

By default, replication is asynchronous. The primary commits as soon as the WAL is flushed locally, without waiting for the replica to confirm receipt. If the primary dies before the replica catches up, committed transactions can be lost.

Synchronous replication makes the primary wait for at least one replica to confirm before acknowledging the commit:

```sql
-- On primary: require at least one replica to confirm WAL flush before commit
synchronous_standby_names = 'ANY 1 (replica1, replica2)'
synchronous_commit = remote_flush  -- wait for replica's WAL flush
```

This eliminates data loss at the cost of commit latency (add the network round-trip to every write).

**Logical Replication**

Streaming replication is physical — byte-level. Logical replication decodes WAL records into row-level changes (`INSERT`/`UPDATE`/`DELETE`) and ships those. This enables:

- Replicating to a different Postgres major version
- Replicating individual tables, not the whole database
- Replicating to non-Postgres destinations (Kafka via Debezium, another database)
- Zero-downtime major version upgrades

## Why It Matters

Replication serves two distinct purposes that are often conflated:

**High availability (HA)**: a warm standby ready to take over if the primary fails. Failover promotes the replica to primary.

**Read scaling**: route read-only queries to replicas to reduce load on the primary. This works when reads substantially outnumber writes and some replication lag is acceptable.

These require different configurations. HA prioritizes minimal failover time and data loss (use synchronous replication, keep replicas close). Read scaling prioritizes throughput (async replication is fine, replicas can be geographically distributed).

## Production Example

In Go, directing read queries to replicas requires your application to be aware of which connection is the primary and which are replicas. A common pattern uses separate connection pools:

```go
type DB struct {
    primary  *sql.DB   // reads + writes
    replicas []*sql.DB // reads only (may be slightly stale)
    rr       uint64    // round-robin counter
}

func (d *DB) Primary() *sql.DB {
    return d.primary
}

func (d *DB) Replica() *sql.DB {
    if len(d.replicas) == 0 {
        return d.primary // fall back to primary if no replicas
    }
    idx := atomic.AddUint64(&d.rr, 1) % uint64(len(d.replicas))
    return d.replicas[idx]
}

// Critical reads (after a write) go to primary
func (s *OrderService) CreateAndFetch(ctx context.Context, order Order) (Order, error) {
    if err := s.db.Primary().QueryRowContext(ctx, insertSQL, ...).Scan(&order.ID); err != nil {
        return Order{}, err
    }
    // Read from primary to avoid stale replica
    return s.fetchByID(ctx, s.db.Primary(), order.ID)
}

// Non-critical reads can use replicas
func (s *OrderService) ListOrders(ctx context.Context, userID int64) ([]Order, error) {
    return s.fetchOrders(ctx, s.db.Replica(), userID)
}
```

For failover automation, tools like Patroni or repmgr watch the primary's health and orchestrate promotion of a replica. In cloud environments, managed services (RDS, CloudSQL, AlloyDB) handle this automatically.

## The Tradeoffs

**Replication lag**: async replicas can be behind by milliseconds to seconds under normal load, or much more under heavy write load. Reads from replicas may return stale data. This is often acceptable (reading a user's profile 200ms after it was updated is fine) but sometimes it is not (reading a payment status immediately after processing it).

**Replication slots**: a replication slot holds WAL on the primary until the replica confirms it consumed that WAL. If a replica goes offline, WAL accumulates. If it accumulates faster than disk space allows, the primary runs out of disk and crashes. Monitor slot lag (`pg_replication_slots.pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn))`) carefully and drop slots for replicas that are gone.

**Failover is not instant**: even with automation, failover takes 10–60 seconds. During that time, write operations fail. Design applications to retry writes with backoff, and use read replicas to serve reads during primary downtime.

**Cascading replicas**: a replica can itself stream to downstream replicas, reducing load on the primary. Useful for read-heavy architectures.

## Key Takeaway

Postgres replication works by shipping WAL records to replicas, which apply them to maintain a synchronized copy. Streaming replication is physical and identical-copy; logical replication decodes to row changes enabling cross-version and cross-system replication. Use synchronous replication when data loss is unacceptable; async replication when throughput matters more. Route reads to replicas thoughtfully — always read from primary when staleness would be incorrect.

---

**Previous:** [Lesson 7: Connection Pooling](/post/fundamentals/db-connection-pooling/) | **Next:** [Lesson 9: Partitioning — Range, Hash, List and When Each Helps](/post/fundamentals/db-partitioning/)
