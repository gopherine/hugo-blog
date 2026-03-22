---
title: "Lesson 1: Leader Election — Someone has to be in charge"
author: Atharva Pandey
keywords:
  - leader election
  - distributed systems
  - consensus
  - Zookeeper
  - etcd
  - distributed consensus
tags:
  - fundamentals
  - distributed systems
series: "Distributed Consensus"
lesson: 1
date: '2024-05-16T00:00:00.000Z'
---

I spent three days debugging a production incident where two nodes in our cluster both believed they were the primary. Each was accepting writes. Each was replicating to followers. Each was convinced the other was dead. By the time we noticed, we had diverged state that took two more days to reconcile. That incident made me obsessive about leader election — not as an academic concept, but as a concrete engineering problem with real failure modes.

The fundamental problem in distributed systems is coordination. When you have multiple nodes, someone needs to be in charge of making decisions. Who accepts the write? Who initiates the replication? Who triggers the failover? Without a clear answer to that question, you get the nightmare I just described: split-brain, where two nodes both think they're primary and you end up with two diverging copies of your data.

## How It Works

Leader election is the process by which a group of nodes agrees on exactly one node to act as the coordinator. The elected leader handles special responsibilities — typically writes, or initiating distributed transactions — while followers observe and stand ready to take over if the leader fails.

The challenge is deceptively simple to state: pick one node, everyone agrees on which one, and if that node fails, pick a new one. The difficulty is that in a distributed network, nodes can't perfectly observe each other's state. A node that appears dead might just be experiencing a network partition. A node that's slow to respond might be overloaded, not dead. Any algorithm for leader election has to make decisions under this uncertainty.

The three core requirements for a correct leader election algorithm are:

**Safety**: At most one leader at any given time. This is the hard requirement. Violating it causes split-brain.

**Liveness**: If the current leader fails, eventually a new leader gets elected. The system can't get stuck forever.

**Stability**: Once a leader is elected, it stays leader until it actually fails. Constant re-elections waste time and cause instability.

These three properties are in tension with each other, especially under network partitions.

## The Algorithm

The simplest approach to leader election is a **heartbeat and timeout** scheme. Each node broadcasts a heartbeat to all peers. If a node hasn't received a heartbeat from the current leader within some timeout window, it declares the leader dead and initiates an election. Whoever initiates the election proposes themselves as the new leader, and nodes vote. If a candidate receives votes from a majority of nodes, it becomes leader.

```
┌─────────────────────────────────────────────────────────────┐
│                    Leader Election Flow                      │
│                                                             │
│  Node A (Leader)          Node B           Node C           │
│      │                      │                │             │
│      │──── heartbeat ───────▶│                │             │
│      │──── heartbeat ────────────────────────▶│             │
│      │                      │                │             │
│   [crash]                   │                │             │
│                             │                │             │
│                    [timeout expires]          │             │
│                             │                │             │
│                             │── RequestVote ─▶│             │
│                             │◀── VoteGranted ─│             │
│                             │                │             │
│                    [majority achieved]        │             │
│                             │                │             │
│                    [Node B becomes leader]    │             │
└─────────────────────────────────────────────────────────────┘
```

The majority requirement is the critical safety mechanism. With three nodes, you need two votes. With five nodes, you need three. Because you can only have one majority partition in a split network, this prevents two nodes from simultaneously winning an election.

The problem with naive heartbeat-and-timeout is that it's sensitive to timing. If you set the timeout too low, you get frequent false positives — nodes declaring healthy leaders dead because of transient network latency spikes. If you set it too high, your failover time balloons. Real systems add **pre-vote phases**, **randomized election timeouts**, and **lease mechanisms** to make elections more stable.

## Production Example

etcd, the key-value store that Kubernetes uses for all its cluster state, implements leader election as a core primitive. But in practice, most applications don't talk to etcd directly for leader election — they use a higher-level abstraction built on top of it.

The `client-go` leader election library for Kubernetes is a great concrete example. It works by having candidates compete to create and hold a lock resource (a `Lease` object in Kubernetes). The current leader continually renews this lease. If a candidate fails to renew the lease within the lease duration, other candidates can acquire it.

```go
import (
    "context"
    "time"
    "k8s.io/client-go/tools/leaderelection"
    "k8s.io/client-go/tools/leaderelection/resourcelock"
)

func runWithLeaderElection(ctx context.Context, client kubernetes.Interface, id string) {
    lock := &resourcelock.LeaseLock{
        LeaseMeta: metav1.ObjectMeta{
            Name:      "my-controller-lock",
            Namespace: "default",
        },
        Client: client.CoordinationV1(),
        LockConfig: resourcelock.ResourceLockConfig{
            Identity: id, // unique per pod, e.g., pod name + UID
        },
    }

    leaderelection.RunOrDie(ctx, leaderelection.LeaderElectionConfig{
        Lock:            lock,
        LeaseDuration:   15 * time.Second,
        RenewDeadline:   10 * time.Second,
        RetryPeriod:     2 * time.Second,
        Callbacks: leaderelection.LeaderCallbacks{
            OnStartedLeading: func(ctx context.Context) {
                // This runs only on the leader
                runController(ctx)
            },
            OnStoppedLeading: func() {
                // Called when this node loses leadership
                log.Fatal("lost leadership, restarting")
            },
            OnNewLeader: func(identity string) {
                log.Printf("new leader elected: %s", identity)
            },
        },
    })
}
```

The `LeaseDuration` (15s) is how long the lock is valid before another candidate can steal it. `RenewDeadline` (10s) is how long the current leader has to successfully renew before it gives up. `RetryPeriod` (2s) is how often candidates attempt to acquire the lock. These values are tuning knobs between failover speed and false-positive elections.

## The Tradeoffs

**Timeout tuning is hard**: Too short and you have false elections during network hiccups. Too long and your failover time is painful. A common production configuration is leaseDuration ≈ 30s, renewDeadline ≈ 15s for latency-tolerant workloads; 15s/10s for more sensitive ones. Don't go below 5s/3s without very good reason.

**Clock skew causes bugs**: Lease-based leader election depends on clocks. If nodes have significantly different clock values, a node might believe a lease has expired when the leader hasn't even reached its deadline. Running NTP (or better, a high-accuracy time service like Google's TrueTime API) on all cluster nodes isn't optional.

**Split-brain on partition**: If a network partition splits your three-node cluster into a group of two and a group of one, the majority partition (two nodes) can elect a new leader, while the minority partition (one node) continues to believe it's the leader until its lease expires. During that overlap window, you have two leaders. This is why you always run with an odd number of nodes (3, 5, 7) and why you need your application to handle the case of a leader that's been cut off from the majority.

**Fencing tokens**: After a leader election completes, the new leader should use a fencing token — a monotonically increasing number — that it attaches to all writes. Storage systems that support conditional writes can reject writes from a stale leader whose token is lower than the current one. This is how you prevent a zombie leader from corrupting state even if it's unaware it's been replaced.

## Key Takeaway

Leader election is not just a distributed systems theory problem — it's a concrete operational challenge with real failure modes. The core insight is that safety (at most one leader) requires a majority quorum, and liveness (eventually someone leads) requires timeout-based detection of failures. Every real implementation is a tuning exercise between these competing pressures. Run an odd number of nodes, tune your lease durations thoughtfully, use fencing tokens to protect against stale leaders, and make sure your application logic can tolerate brief periods of no leader during transitions. The incident I started with — split-brain with two primaries — was ultimately a missing fencing token on the storage layer. The election algorithm was correct; the write path didn't enforce the invariant.

---

**Next:** [Lesson 2: Raft Consensus — The consensus algorithm you can actually understand](/post/fundamentals/consensus-raft/)
