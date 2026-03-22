---
title: "Lesson 2: Raft Consensus — The consensus algorithm you can actually understand"
author: Atharva Pandey
keywords:
  - Raft
  - consensus algorithm
  - distributed consensus
  - log replication
  - leader election
  - distributed systems
tags:
  - fundamentals
  - distributed systems
series: "Distributed Consensus"
lesson: 2
date: '2024-07-26T00:00:00.000Z'
---

When I first tried to understand Paxos — the original distributed consensus algorithm — I read the paper three times and still felt like I was missing something. I could follow each step individually, but I couldn't build a mental model of why it worked or what the invariants were. Raft was designed specifically to fix that. Its paper is literally titled "In Search of an Understandability: The Raft Consensus Algorithm." After reading it, I could explain it to someone else. That's the bar Raft was designed to clear, and it does.

Raft is the algorithm powering etcd, which powers Kubernetes. It's the algorithm powering CockroachDB, TiKV, and dozens of other production systems. Understanding it deeply is worth the investment.

## How It Works

Raft decomposes the consensus problem into three relatively independent subproblems: **leader election**, **log replication**, and **safety**. Each subproblem has clear rules. If you understand each rule and why it exists, the whole algorithm follows.

The fundamental state in Raft is the replicated log. Every change to the system is represented as an entry in this log. The log is append-only. The entire state of the system at any point is the result of replaying the log from the beginning. The job of consensus is to ensure that all nodes agree on the same log — same entries, same order.

Raft organizes time into **terms**. A term is a period of time identified by a monotonically increasing integer. Each term begins with an election. If a candidate wins the election, it serves as leader for the rest of the term. If no candidate wins (a split vote), the term ends with no leader and a new election begins immediately. Terms are the clock by which Raft nodes measure time.

```
┌─────────────────────────────────────────────────────────────────┐
│                         Raft Terms                              │
│                                                                 │
│  Term 1         Term 2     Term 3         Term 4               │
│  ┌──────────┐  ┌──────┐   ┌──────────┐  ┌──────────────────┐  │
│  │ Election │  │Split │   │ Election │  │     Normal       │  │
│  │ + Leader │  │ Vote │   │ + Leader │  │    Operation     │  │
│  └──────────┘  └──────┘   └──────────┘  └──────────────────┘  │
│                                                                 │
│  Terms are like logical clocks — they let nodes detect stale   │
│  information from previous leaders.                             │
└─────────────────────────────────────────────────────────────────┘
```

## The Algorithm

**Leader Election** in Raft works as follows. Each follower has an election timeout (randomized between 150ms–300ms). If a follower doesn't receive a heartbeat from a leader before the timeout fires, it increments its term, transitions to candidate state, votes for itself, and sends `RequestVote` RPCs to all other nodes. A node grants its vote if: (a) it hasn't voted in this term yet, and (b) the candidate's log is at least as up-to-date as its own. If the candidate receives votes from a majority, it becomes leader. The randomized timeout is what prevents all followers from starting elections simultaneously after a leader failure.

**Log Replication** is how the leader propagates entries to followers. When a client sends a write to the leader, the leader appends the entry to its local log with the current term number. It then sends `AppendEntries` RPCs to all followers in parallel. Each follower appends the entry to its log and responds. Once the leader has received acknowledgment from a majority (including itself), the entry is **committed**. The leader then applies the entry to its state machine and responds to the client. On the next `AppendEntries` RPC (which could be the next heartbeat), the leader informs followers of the new commit index, and they apply the entry to their state machines too.

```go
// Simplified Raft log entry
type LogEntry struct {
    Term    int    // which term this entry was created in
    Index   int    // position in the log
    Command []byte // the actual state machine command
}

// AppendEntries RPC — used for both log replication and heartbeats
type AppendEntriesArgs struct {
    Term         int        // leader's current term
    LeaderID     string     // so followers can redirect clients
    PrevLogIndex int        // index of log entry before new ones
    PrevLogTerm  int        // term of PrevLogIndex entry
    Entries      []LogEntry // entries to append (empty for heartbeat)
    LeaderCommit int        // leader's commitIndex
}
```

The `PrevLogIndex` and `PrevLogTerm` fields are Raft's consistency check. A follower only appends new entries if its log already contains an entry at `PrevLogIndex` with term `PrevLogTerm`. This ensures the logs are consistent up to the point where new entries are being appended.

**The Safety property** is the one that makes the whole thing correct: a leader must have all committed entries. Raft enforces this through the vote-granting rule. A node only grants a vote to a candidate whose log is at least as up-to-date as its own. "More up-to-date" means: higher last-entry term, or same last-entry term but longer log. Because committed entries were written to a majority of nodes, any candidate that wins a majority of votes must have received votes from at least one node that has the committed entry. So the winner is guaranteed to have it.

## Production Example

Here's a concrete example of how log replication works in a three-node cluster when a follower is lagging behind:

```
Initial state (all caught up):
  Leader:     [1:set x=1] [1:set y=2] [2:set x=3]  (committed=3)
  Follower B: [1:set x=1] [1:set y=2] [2:set x=3]  (committed=3)
  Follower C: [1:set x=1] [1:set y=2]               (committed=2, lagging)

New write arrives: set z=4 (term 2)

Leader appends locally:
  Leader:     [...] [2:set x=3] [2:set z=4]

AppendEntries to Follower B:
  PrevLogIndex=3, PrevLogTerm=2, Entries=[{2, 4, set z=4}]
  B checks: log[3] exists and has term 2? Yes. Append. Reply success.

AppendEntries to Follower C:
  PrevLogIndex=3, PrevLogTerm=2, Entries=[{2, 4, set z=4}]
  C checks: log[3] exists and has term 2? No, C only has index 2.
  Reply: failure, conflictIndex=3

Leader retries C with decremented nextIndex:
  PrevLogIndex=2, PrevLogTerm=1, Entries=[{2, 3, set x=3}, {2, 4, set z=4}]
  C checks: log[2] exists and has term 1? Yes. Append both. Reply success.

Leader receives majority (itself + B): commit index advances to 4.
```

In production systems running etcd, you'll see this catch-up happen during rolling restarts, temporary network partitions, or when a pod is rescheduled onto a slow node. The leader tracks `nextIndex[]` for each follower and automatically backs off until it finds the point of divergence.

## The Tradeoffs

**Performance under normal operation**: Raft requires two round trips to commit a write — the leader gets the entry to a majority, then the commit notification reaches followers. In practice, you can pipeline multiple entries and batch `AppendEntries` RPCs to improve throughput significantly. etcd and CockroachDB both do this.

**Read scalability**: By default, reads go through the leader too. The leader must confirm it's still leader (by getting a quorum acknowledgment or checking lease freshness) before responding with a read, to ensure it's not returning stale data to a client during a partition where a new leader has been elected. Linearizable reads are expensive. Systems like etcd support a `Serializable` (slightly stale) read that skips the quorum check for better read throughput.

**Network partitions and availability**: If a five-node cluster loses two nodes, the remaining three can still make progress (majority = 3). If it loses three nodes, the cluster becomes unavailable for writes — it can't achieve majority. This is the CAP tradeoff baked into consensus: Raft chooses consistency over availability during partitions.

**Membership changes**: Adding or removing nodes while the cluster is running is one of the hardest parts of implementing Raft correctly. Raft uses joint consensus or single-server changes to handle this safely. Most production implementations (etcd included) serialize all membership changes through the log itself.

## Key Takeaway

Raft makes consensus understandable by decomposing it into three clean problems: leader election (randomized timeouts + majority voting), log replication (AppendEntries with a consistency check), and safety (vote-granting rules that ensure leaders have all committed entries). The terms mechanism acts as a logical clock, letting nodes detect and ignore stale messages from old leaders. If you're running Kubernetes, you're already depending on Raft — every etcd write goes through this protocol. Understanding it tells you why etcd requires an odd number of members, why split-brain is impossible in a correctly functioning cluster, and why writes slow down or stall when nodes are unreachable.

---

**Previous:** [Lesson 1: Leader Election — Someone has to be in charge](/post/fundamentals/consensus-leader-election/)
**Next:** [Lesson 3: Paxos and Beyond — When Raft isn't enough](/post/fundamentals/consensus-paxos/)
