---
title: "Lesson 15: CAP Theorem in Practice — What It Actually Means for Your System"
author: "Atharva Pandey"
series: "System Design from First Principles"
lesson: 15
tags:
  - fundamentals
  - system design
keywords:
  - CAP theorem
  - consistency
  - availability
  - partition tolerance
  - eventual consistency
  - PACELC
  - system design
date: "2024-11-19T00:00:00.000Z"
---

CAP theorem is probably the most cited and most misunderstood concept in distributed systems interviews. Candidates memorize "you can only pick two of consistency, availability, and partition tolerance" and then either over-apply it (treating every design decision as a CAP trade-off) or under-apply it (never relating it to actual design choices). The theorem is real and important, but the way it's usually taught in 30-second summaries strips out the nuance that makes it actually useful. This final lesson clears that up.

## The Core Concept

CAP theorem (Brewer's theorem, 2000) states: in a distributed data store, during a network partition, you must choose between consistency and availability.

The three properties:
- **Consistency (C)**: every read receives the most recent write or an error. All nodes see the same data at the same time.
- **Availability (A)**: every request receives a (non-error) response — though not necessarily the most recent write.
- **Partition Tolerance (P)**: the system continues operating despite arbitrary network partitions (nodes that can't communicate with each other).

The most important thing to understand: **partition tolerance is not optional in real distributed systems**. Network partitions happen. Networks are unreliable. If your system spans multiple machines — even in the same datacenter — you will experience partitions. You can't choose to "not have" partition tolerance. So the real choice is: when a partition occurs, do you sacrifice consistency or availability?

**CP systems** (consistent, partition-tolerant): during a partition, refuse to answer (return an error) rather than return potentially stale data. HBase, ZooKeeper, MongoDB (in default config). Banks, payment systems — correctness is more important than constant availability.

**AP systems** (available, partition-tolerant): during a partition, continue answering requests using whatever data is available, potentially returning stale values. Cassandra, DynamoDB, CouchDB. Social feeds, product catalogs — users would rather see slightly stale data than get an error.

**Why "CA" is a fiction**: a system that claims to offer both consistency and availability without partition tolerance simply hasn't been deployed across the network. A single-node relational database is "CA" because there are no partitions — but the moment you add a second node for replication, you have a distributed system subject to partitions.

## How to Design It

**Consistency models: a spectrum**

CAP's consistency is "strong consistency" — the strictest model. But there's a spectrum:

- **Strong consistency (linearizability)**: every operation appears to take effect atomically at some point between its invocation and completion. Every reader sees the effect of every write that completed before their read. The most expensive to implement. Required for: financial ledgers, inventory counts, distributed locks.

- **Sequential consistency**: operations are ordered consistently across all nodes, but not necessarily in real-time. Slightly weaker and slightly cheaper.

- **Causal consistency**: operations that are causally related are seen in the right order. If A happened because of B, everyone sees B before A. Independent operations may be seen in any order. Used in: version vectors in distributed KV stores.

- **Eventual consistency**: given no new updates, all nodes will eventually converge to the same value. The "eventually" could be milliseconds or seconds. The weakest model but the most available. Used in: DNS, CDN caches, shopping cart totals, social media counters.

**PACELC: a better model**

The CAP theorem only describes behavior during partitions. But partitions are relatively rare — most of the time, your system is operating normally. The PACELC model extends CAP:

- When there is a **P**artition: choose between **A**vailability and **C**onsistency (as in CAP)
- **E**lse (normal operation): choose between **L**atency and **C**onsistency

This is more useful because it captures the everyday trade-off: do you want fast (low latency) reads that might be slightly stale, or slower reads that are always consistent?

Cassandra is PA/EL: during a partition, it favors availability; during normal operation, it favors low latency over consistency (reads from a single replica by default, not waiting for all replicas to agree).

DynamoDB strong consistency mode is PC/EC: during a partition, it favors consistency; in normal operation, consistent reads are slightly slower (must check multiple replicas).

**Replication and consistency levels**

Cassandra's replication factor and consistency levels make the trade-off explicit and tunable:

```
Replication factor: 3 (data written to 3 nodes)

Write consistency levels:
  ANY      → weakest, highest availability (accept if one node gets it)
  QUORUM   → majority of nodes must confirm (2 of 3)
  ALL      → all replicas must confirm (strongest, lowest availability)

Read consistency levels:
  ONE      → fastest, read from nearest replica (stale risk)
  QUORUM   → majority agreement required
  ALL      → all replicas must agree
```

The rule of thumb for strong consistency: `W + R > N` where W = write quorum, R = read quorum, N = replication factor. If you write to 2 nodes and read from 2 nodes (with 3 total), you're guaranteed to read at least one node that has the latest write. This is QUORUM + QUORUM with N=3: 2+2=4 > 3.

```
N=3, W=2, R=2: 2+2=4 > 3 → strong consistency
N=3, W=1, R=1: 1+1=2 < 3 → eventual consistency
```

## Real-World Examples

**DNS is AP**: when you update a DNS record, changes propagate eventually. Different resolvers around the world see different values for hours. DNS prioritizes availability (always return some answer) over consistency (return the current answer).

**Bank account balances are CP**: you cannot read an inconsistent balance. If a partition occurs, the system returns an error or routes to the authoritative node. A slightly stale balance is not acceptable — it could allow overdrafts.

**Amazon's shopping cart is AP**: Amazon famously designed their shopping cart to always accept additions (available) even during partitions. The cart might show slightly stale data (if you added an item on one device that another device hasn't seen yet). The reasoning: the cost of failing to add an item to a cart is higher than the cost of showing a slightly stale cart.

**ZooKeeper is CP**: it provides a strongly consistent coordination service used for distributed locks, leader election, and configuration management. During a partition, a ZooKeeper client that can't reach a quorum will block or return an error — never a stale value. This is correct because a distributed lock that returns "you have the lock" when you don't is catastrophic.

**Spanner (Google) chooses CP with global availability**: Google's Spanner is globally distributed and strongly consistent, using TrueTime (GPS and atomic clock synchronization) to implement external consistency across global datacenters. They achieve this by accepting higher latency for writes (must commit across datacenters). It's not CA — during a partition, Spanner pauses rather than serve stale data.

## Interview Tips

When CAP comes up in an interview, don't just recite the definition. Apply it:

"For this component, I'm choosing an AP design because user feeds can tolerate staleness of a few seconds. Seeing a post that's 3 seconds old is fine. Getting a 503 error is not. I'll use eventual consistency with Cassandra and set READ consistency to ONE for maximum read availability."

"For this component — account balance — I need CP. I'll use PostgreSQL with synchronous replication to a read replica, or a distributed database like CockroachDB that provides serializable isolation. If a partition occurs, I'd rather return an error than serve a stale balance."

"What's the consistency model of your cache?" The cache is eventually consistent with the database by design (TTL-based expiry). This is acceptable for user profile data but not for financial data.

Common mistake: thinking that choosing AP means "my system is unreliable." It means you've made a deliberate, correct decision that freshness is not a strict requirement for this component. Most consumer-facing data (feeds, profiles, listings) can tolerate eventual consistency. Most financial and coordination data cannot.

"How does CAP relate to distributed transactions?" Distributed transactions across shards are CP: they may block or fail during a partition to maintain consistency. This is why distributed transactions are slow and why teams avoid them where possible — using compensating transactions (sagas) instead, which are AP but require application-level rollback logic.

## Key Takeaway

CAP theorem's real message is: partition tolerance is not optional, so the choice is between consistency and availability when a partition occurs. Most consumer data benefits from AP designs with eventual consistency — the user experience tolerates stale data better than errors. Financial, coordination, and inventory data requires CP — correctness is non-negotiable. The PACELC model extends this to the normal case: even without partitions, you're trading latency for consistency on every read. The practical tool is the W+R > N quorum formula for distributed databases like Cassandra: tune your consistency level to match the required correctness for each operation. Understanding CAP isn't about memorizing a rule — it's about having a principled framework for making consistency trade-offs explicit, intentional, and documented.

---

**Previous:** [Lesson 14: Designing for Failure](/post/fundamentals/sd-designing-failure/)

---

## Course Complete

You've made it through all 15 lessons of **System Design from First Principles**. Here's the full map of what we covered:

**Foundations**
1. [How the Internet Works](/post/fundamentals/sd-internet-works/) — DNS, TCP, HTTP, TLS
2. [Load Balancing](/post/fundamentals/sd-load-balancing/) — L4 vs L7, algorithms
3. [Caching](/post/fundamentals/sd-caching/) — patterns, eviction, invalidation
4. [Database Scaling](/post/fundamentals/sd-database-scaling/) — read replicas, sharding
5. [Message Queues](/post/fundamentals/sd-message-queues/) — delivery semantics, fan-out
6. [CDNs](/post/fundamentals/sd-cdns/) — edge caching, cache-control
7. [Rate Limiting](/post/fundamentals/sd-rate-limiting/) — token bucket, sliding window, distributed

**Classic Designs**
8. [URL Shortener](/post/fundamentals/sd-url-shortener/) — ID generation, caching hierarchy
9. [Chat System](/post/fundamentals/sd-chat-system/) — WebSocket, presence, ordering
10. [News Feed](/post/fundamentals/sd-news-feed/) — fan-out on write vs read
11. [Notification System](/post/fundamentals/sd-notifications/) — priority, dedup, channels
12. [Search Engine](/post/fundamentals/sd-search-engine/) — inverted index, ranking
13. [Payment System](/post/fundamentals/sd-payment-system/) — idempotency, double-entry

**Advanced Reliability**
14. [Designing for Failure](/post/fundamentals/sd-designing-failure/) — circuit breakers, bulkheads
15. [CAP Theorem in Practice](/post/fundamentals/sd-cap-theorem/) — consistency trade-offs

The through line: every design decision is a trade-off. The best system designers don't know more patterns — they understand the trade-offs more deeply and can apply them to the specific constraints of the problem in front of them. That's the skill the interviews are testing, and it's the skill this series aimed to build.
