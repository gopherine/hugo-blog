---
title: "Lesson 4: Database Scaling — Read Replicas, Sharding, and When Each Helps"
author: "Atharva Pandey"
series: "System Design from First Principles"
lesson: 4
tags:
  - fundamentals
  - system design
keywords:
  - database scaling
  - read replicas
  - sharding
  - vertical scaling
  - horizontal scaling
  - consistent hashing
  - system design
date: "2024-06-05T00:00:00.000Z"
---

Your startup ships, gains traction, and one day your database becomes the bottleneck. Queries slow down. CPU spikes. The single machine hosting your Postgres instance can't keep up. This is one of the most predictable problems in engineering, yet I've seen teams reach it completely unprepared because they'd never thought carefully about how databases scale. The right solution depends entirely on whether you're read-heavy or write-heavy, whether your data is relational or not, and whether your workload is even or spiky.

## The Core Concept

A database has two scarce resources: compute (CPU for query processing) and I/O (disk reads/writes). Scaling means either increasing the capacity of one machine (vertical scaling) or distributing the load across multiple machines (horizontal scaling).

**Vertical Scaling: The First Move**

Before anything else, scale up. Move from a 4-core machine to a 32-core machine. Add RAM so your working set fits in memory (avoiding disk reads). Move to SSDs. This is cheap operationally — no code changes, no data migration. It's also capped: there's a biggest machine you can buy, and it's expensive.

For most startups, vertical scaling plus good indexing takes you further than you'd expect. An unindexed query on a table of 10 million rows that adds an index can go from 5,000ms to 2ms. That's more impactful than any architectural change.

**Read Replicas: Scaling Reads**

The most common pattern for read-heavy workloads. A primary (write) instance handles all writes. One or more replicas receive a stream of changes (via WAL streaming in Postgres) and apply them asynchronously. Reads can be directed to replicas.

```
Writes ──→ Primary DB ──→ Replica 1 (async replication)
                      └──→ Replica 2 (async replication)
Reads ──→ Replica 1 or Replica 2
```

The trade-off is replication lag. Changes written to the primary take a small amount of time to appear on replicas — typically milliseconds, but potentially seconds under heavy load. If a user just updated their profile and immediately reads it back, they might hit a replica and see the old value. This is called "read-your-writes" inconsistency.

Solutions:
- **Read after write**: after a write, route the next read for that user to the primary for a short window (or always route authenticated reads for the affected resource to the primary)
- **Synchronous replication**: replicas confirm writes before the primary acknowledges them. Zero lag, but slower writes and you can't have many replicas.
- **Version tracking**: the client sends the timestamp of the last write; the read path checks if any replica is caught up enough to serve it.

Read replicas scale reads linearly. Want to handle 10x reads? Add 9 more replicas. But they do nothing for write bottlenecks.

**Database Sharding: Scaling Writes**

Sharding splits data across multiple database instances. Each instance (shard) holds a subset of the data. When a write comes in, you route it to the correct shard. When a read comes in, you route it to the correct shard (or fan out to multiple shards and merge results).

```
User IDs 0–9M     → Shard A
User IDs 10M–19M  → Shard B
User IDs 20M–29M  → Shard C
```

This is range-based sharding. Simple, but can create hot spots — if IDs are monotonically increasing, all new users land on shard C while A and B are idle.

Hash-based sharding distributes by `hash(user_id) % num_shards`. More even distribution, but makes range queries across shards expensive.

Consistent hashing (as we discussed in Lesson 2) is often better: it distributes load evenly and minimizes data migration when shards are added or removed.

**The Sharding Tax**

Sharding is powerful but comes with serious complexity costs:

- **Cross-shard queries**: "get all orders placed in the last hour across all users" requires querying all shards and merging results. This is expensive and often requires a separate analytics store.
- **Cross-shard transactions**: ACID guarantees across shards are extremely hard. You need distributed transactions (2PC) or you accept eventual consistency.
- **Schema changes**: running a migration across 20 shards requires coordination and potentially significant downtime risk.
- **Operational overhead**: 20 databases to monitor, backup, and tune instead of one.

The advice I've heard from engineers who've lived through sharding a production database: shard as late as possible, and design your sharding key very carefully from the start (even if you don't shard yet).

## How to Design It

The decision tree:

1. Is the bottleneck reads or writes?
2. Reads → add read replicas + caching. Very often this is enough.
3. Writes → look at the write pattern. Is it one hot table? Can you partition it? Can you batch writes?
4. If writes truly outpace a single primary → sharding, or consider a different data store (Cassandra, DynamoDB) designed for horizontal write scaling.

For the shard key, choose something that:
- Has high cardinality (many unique values)
- Is accessed with roughly equal frequency across values
- Appears in the vast majority of queries (so you rarely need to fan out)
- Doesn't create hotspots over time (avoid using timestamps as shard keys)

In a multi-tenant SaaS app, tenant ID is often a natural shard key. All data for one tenant lives on one shard, eliminating cross-shard queries for single-tenant operations.

```go
// Routing layer in Go
func getShardForUser(userID int64, numShards int) int {
    h := fnv.New32a()
    binary.Write(h, binary.BigEndian, userID)
    return int(h.Sum32()) % numShards
}
```

## Real-World Example

Instagram started with a single PostgreSQL instance. As they grew, they added read replicas for their read-heavy photo feed workload. Eventually they moved to a sharded Postgres setup, sharding on user ID. Their engineers wrote about the operational complexity — managing schema migrations across hundreds of shards required custom tooling and a lot of careful planning.

Pinterest scaled to thousands of MySQL shards. Their sharding key was user ID. A board belongs to a user, a pin belongs to a board — everything traces back to user ID, so cross-shard queries were rare.

Stripe uses sharding with tenant isolation — each merchant's data lives in a shard, which also provides good security isolation.

## Interview Tips

When your interviewer asks "how do you scale your database," don't jump straight to sharding. Walk through the progression: vertical scaling → indexes → query optimization → caching → read replicas → then sharding. This shows you understand that sharding is a last resort with serious costs.

Be ready to discuss the sharding key. If you say "I'll shard by user ID," follow up with: what are the access patterns? Are there queries that need data from multiple users? What happens when a user becomes extremely popular (hot shard)?

Interviewers often ask about the read-your-writes problem specifically. Have a concrete answer — routing writes and the subsequent read to the primary for a short window is usually the right one.

"What about NoSQL?" is a common pivot. The honest answer: document stores like MongoDB and wide-column stores like Cassandra are designed to shard horizontally from the start. You trade ACID transactions and relational query power for easier horizontal scale. It's not better or worse — it's a different set of trade-offs that match different workloads.

## Key Takeaway

Database scaling follows a clear progression: scale up first, then add read replicas for read-heavy loads, then shard if writes are the bottleneck. Read replicas introduce replication lag — understand how your application handles read-your-writes consistency. Sharding solves write bottlenecks but imposes a significant complexity tax: cross-shard queries, distributed transactions, and operational overhead. Design your shard key to minimize cross-shard operations, avoid hotspots, and you'll buy yourself years of headroom. Reach for sharding only when you've exhausted simpler options.

---

**Previous:** [Lesson 3: Caching](/post/fundamentals/sd-caching/)
**Next:** [Lesson 5: Message Queues — Decoupling Services Without Losing Messages](/post/fundamentals/sd-message-queues/)
