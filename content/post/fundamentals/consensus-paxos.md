---
title: "Lesson 3: Paxos and Beyond — When Raft isn't enough"
author: Atharva Pandey
keywords:
  - Paxos
  - Multi-Paxos
  - distributed consensus
  - Byzantine fault tolerance
  - Viewstamped Replication
  - consensus algorithms
tags:
  - fundamentals
  - distributed systems
series: "Distributed Consensus"
lesson: 3
date: '2024-10-04T00:00:00.000Z'
---

After spending time with Raft, I found myself curious about the algorithm it was designed to replace. Paxos has a reputation: brilliant, correct, nearly impossible to implement correctly, and even harder to extend to practical systems. Leslie Lamport published the original Paxos paper in 1989, got it rejected, submitted a revised version in 1998, and it became the theoretical foundation for a generation of distributed systems. Chubby (Google's distributed lock service), Zookeeper (the coordination service), and the precursor to Spanner all descended from Paxos thinking. Understanding why Raft was necessary requires understanding what Paxos gets right and where it falls short in practice.

## How It Works

Basic Paxos is a protocol for getting a group of nodes to agree on a **single value**. Not a log. Not a sequence of values. One value. That's an important distinction. Real systems need to agree on sequences of values (a sequence of log entries), which requires extending Basic Paxos into Multi-Paxos — an extension that the original paper doesn't fully specify.

Basic Paxos has two roles: **proposers** (nodes that want to propose a value) and **acceptors** (nodes that vote on proposals). A quorum is any majority of acceptors. The protocol runs in two phases.

**Phase 1 (Prepare)**: A proposer picks a proposal number `n` (must be higher than any it has used before) and sends a `Prepare(n)` message to a quorum of acceptors. Each acceptor responds with a **promise** to never accept proposals numbered lower than `n`, along with the highest-numbered proposal it has already accepted (if any).

**Phase 2 (Accept)**: If the proposer receives promises from a quorum, it sends `Accept(n, v)` where `v` is either its own proposed value, or — critically — the value from the highest-numbered accepted proposal it heard about in Phase 1 (whichever is higher). Each acceptor accepts the proposal unless it has since made a promise to a higher number. Once a quorum accepts, the value is chosen.

```
Proposer P                Acceptors A1, A2, A3
    │
    │──── Prepare(5) ─────────────────▶ A1, A2, A3
    │
    │◀─── Promise(5, null) ────────────── A1
    │◀─── Promise(5, null) ────────────── A2
    │◀─── Promise(5, null) ────────────── A3
    │   (quorum received, no prior accepted value)
    │
    │──── Accept(5, "my_value") ───────▶ A1, A2, A3
    │
    │◀─── Accepted ────────────────────── A1
    │◀─── Accepted ────────────────────── A2
    │  (quorum accepted — "my_value" is chosen)
```

The subtle correctness property: if a value was previously accepted by any acceptor, the new proposer is forced to use that value (from the Phase 1 responses), preserving the already-chosen value even across leader failures.

## The Algorithm

The transition from Basic Paxos to **Multi-Paxos** is where implementations diverge. In Basic Paxos, every single value agreement requires two round trips. For a log-replication system processing thousands of writes per second, this is prohibitively expensive. Multi-Paxos optimizes this by electing a distinguished proposer (the leader) and skipping Phase 1 for subsequent log entries once the leader has established its authority. This is where Multi-Paxos starts to look a lot like Raft — with a stable leader, a heartbeat mechanism, and sequential log entries.

The difference is that Paxos doesn't specify how the leader is elected, how log gaps are filled, how membership changes are handled, or how state is transferred to new nodes. Each implementation makes different choices. Google's Chubby makes one set of choices. Apache Zookeeper's ZAB (Zookeeper Atomic Broadcast) makes another. Spanner's Paxos implementation makes yet another. This is why "implementing Paxos" in practice means implementing a lot of things Paxos doesn't tell you how to do.

**Flexible Paxos** is a more recent refinement worth knowing. Classic Paxos requires a majority quorum for both phases. Flexible Paxos observes that Phase 1 and Phase 2 quorums only need to overlap — they don't each need to be a majority. If you use a larger quorum for Phase 2 (acceptance), you can use a smaller quorum for Phase 1 (prepare). For example, in a five-node cluster, you could require Phase 1 quorum of 2 and Phase 2 quorum of 4. This allows you to tailor the algorithm to your read/write patterns.

```
Classic Paxos (5 nodes): Phase1=3, Phase2=3 (must overlap)
Flexible Paxos example:  Phase1=2, Phase2=4 (still overlap at min 1)
                         Phase1=4, Phase2=2 (overlap maintained)
```

## Production Example

Spanner, Google's globally distributed relational database, uses a variant of Multi-Paxos for replication within each shard group. What makes Spanner interesting for our discussion is how it handles the leader lease mechanism to enable fast local reads.

In Spanner, each Paxos group has a leader that holds a time-bounded lease (typically 10 seconds). The leader can serve reads locally without consulting the quorum because it knows it's the only leader during the lease period. When the lease expires, the leader must renew it via another Paxos round before it can continue serving reads. This is the lease-based optimization applied to Paxos — the same concept we saw in leader election, applied specifically to read performance.

The other major extension in Spanner is TrueTime — Google's API for getting a time interval (earliest, latest) rather than a point timestamp. Spanner uses TrueTime to assign commit timestamps to transactions, and it waits until `TrueTime.now().latest` has passed the commit timestamp before declaring a transaction committed externally visible. This is how Spanner achieves external consistency (linearizability) globally, across data centers, without requiring tight clock synchronization.

For systems outside Google, **etcd** using Raft is the practical choice. But understanding that Raft is essentially a fully-specified, implementation-complete version of Multi-Paxos tells you why: Raft fills in all the gaps Paxos leaves unspecified, which is exactly what production systems need.

## The Tradeoffs

**Paxos vs Raft in practice**: If you're building a new system that needs replicated state machine consensus, use Raft. The implementations are mature (etcd, Consul, CockroachDB), the spec is complete, and the behavior is predictable. Paxos is the theoretical foundation, but its underspecification makes it a research choice, not an implementation choice, for most teams.

**Byzantine fault tolerance**: Both Paxos and Raft assume **crash fault tolerance** — nodes either work correctly or crash. They do not handle **Byzantine faults**, where nodes can behave arbitrarily (send incorrect data, lie about their state, selectively respond). If you need to tolerate Byzantine faults — say, in a blockchain or a system where nodes are operated by untrusted parties — you need PBFT, Tendermint, or HotStuff. These algorithms require 3f+1 nodes to tolerate f Byzantine faults (compared to 2f+1 for crash faults), which makes them significantly more expensive.

**Geo-distribution**: Standard Raft/Paxos with a single leader means every write crosses the network to the leader's data center. For globally distributed systems, this creates unavoidable latency. Spanner addresses this with Paxos groups per shard plus TrueTime. CockroachDB uses a similar sharded Raft approach. The tradeoff is complexity: you now have consensus happening at multiple layers of the system.

**Epaxos and leaderless variants**: EPaxos (Egalitarian Paxos) is a leaderless variant that allows any node to commit non-conflicting commands in one round trip. The cost is that conflicting commands require an additional phase, and the protocol is significantly more complex. It's been implemented in some research systems but has seen limited production adoption. The lesson here is that "leaderless" sounds appealing but the implementation complexity is substantial.

## Key Takeaway

Paxos is the theoretical bedrock of distributed consensus — everything else builds on or borrows from it. Basic Paxos gets you agreement on a single value in two phases with quorum voting. Multi-Paxos extends this to log replication but leaves critical implementation decisions unspecified, which is why Raft was necessary. For practical systems, Raft is Paxos made complete and understandable. Go beyond Raft when you need: Byzantine fault tolerance (use PBFT/Tendermint), geo-distributed low-latency writes (use sharded Paxos groups like Spanner), or theoretical flexibility in quorum sizes (use Flexible Paxos). The right algorithm depends on your threat model (crash faults vs Byzantine), your latency requirements (single-region vs geo-distributed), and your ability to operate the complexity.

---

**Previous:** [Lesson 2: Raft Consensus — The consensus algorithm you can actually understand](/post/fundamentals/consensus-raft/)

---

🎓 **Course Complete!** You've finished "Distributed Consensus." From the basics of leader election through Raft's clean decomposition of the consensus problem and finally to Paxos and its descendants — you now have a solid mental model of how distributed systems agree on state, why split-brain happens, and what the real tradeoffs are between different consensus algorithms.
