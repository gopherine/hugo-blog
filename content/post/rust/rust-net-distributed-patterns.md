---
title: "Lesson 10: Distributed System Patterns — Consensus, CRDTs, and consistency"
author: Atharva Pandey
series: "Rust Networking & Distributed Systems"
lesson: 10
keywords: [Rust, rust, networking, distributed, consensus, CRDT, Raft, consistency, CAP theorem, eventual consistency]
tags: [Rust tutorial, rust, networking, distributed-systems]
date: '2025-06-04T11:08:00.000Z'
---

I once watched a team spend six months building a "distributed database" that was really just PostgreSQL with a cron job that copied rows between data centers. It worked until it didn't — conflicting writes, lost updates, and an incident where the same order was fulfilled twice from different warehouses. They learned the hard way that distributed systems aren't just "run it on multiple machines." They're a fundamentally different programming model with different guarantees, different failure modes, and different mental models.

This lesson won't make you a distributed systems expert — that takes years. But it'll give you the vocabulary, the patterns, and enough working Rust code to understand what's actually happening when people say things like "eventual consistency" or "Raft consensus."

## The CAP Theorem — Pick Your Trade-offs

You've probably heard this: in a distributed system, you can have at most two of three properties:

- **Consistency** — every read returns the most recent write.
- **Availability** — every request gets a response (not an error).
- **Partition tolerance** — the system works even when network messages between nodes are lost.

Since network partitions are inevitable in the real world, you're really choosing between CP (consistent but sometimes unavailable during partitions) and AP (always available but sometimes stale data).

But CAP is a blunt instrument. The real question is more nuanced: what kind of consistency do you need, for which operations, at what latency cost?

## Consistency Models in Practice

Here's a practical hierarchy, from strongest to weakest:

**Linearizability** — operations appear to happen atomically at some point between invocation and response. This is what single-threaded programs give you for free and what distributed systems struggle to provide. etcd and CockroachDB offer this.

**Sequential consistency** — operations happen in the order each client issued them, but different clients' operations might interleave differently than wall-clock order.

**Causal consistency** — if operation A causally precedes B, everyone sees A before B. Concurrent operations can appear in any order.

**Eventual consistency** — if you stop writing, eventually all replicas converge. DynamoDB, Cassandra, and most AP systems offer this.

Let's implement some of these.

## A Simple Replicated Key-Value Store

Start with the basics — a key-value store that replicates writes to multiple nodes.

```rust
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;
use tokio::net::TcpListener;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

#[derive(Clone)]
struct KvNode {
    id: String,
    store: Arc<RwLock<HashMap<String, ValueEntry>>>,
    peers: Vec<String>, // addresses of other nodes
}

#[derive(Clone, Debug)]
struct ValueEntry {
    value: String,
    timestamp: u64, // Lamport timestamp for ordering
    node_id: String, // Which node wrote this
}

impl KvNode {
    fn new(id: &str, peers: Vec<String>) -> Self {
        Self {
            id: id.to_string(),
            store: Arc::new(RwLock::new(HashMap::new())),
            peers,
        }
    }

    async fn get(&self, key: &str) -> Option<String> {
        let store = self.store.read().await;
        store.get(key).map(|e| e.value.clone())
    }

    async fn set(&self, key: &str, value: &str, timestamp: u64) {
        let mut store = self.store.write().await;

        // Last-writer-wins using timestamps
        let should_write = match store.get(key) {
            Some(existing) => timestamp > existing.timestamp,
            None => true,
        };

        if should_write {
            store.insert(
                key.to_string(),
                ValueEntry {
                    value: value.to_string(),
                    timestamp,
                    node_id: self.id.clone(),
                },
            );
        }
    }

    async fn replicate_to_peers(
        &self,
        key: &str,
        value: &str,
        timestamp: u64,
    ) {
        for peer in &self.peers {
            let peer = peer.clone();
            let msg = format!("REPLICATE {} {} {}\n", key, value, timestamp);

            tokio::spawn(async move {
                match tokio::net::TcpStream::connect(&peer).await {
                    Ok(mut stream) => {
                        let _ = stream.write_all(msg.as_bytes()).await;
                    }
                    Err(e) => {
                        eprintln!("Failed to replicate to {peer}: {e}");
                    }
                }
            });
        }
    }
}

async fn run_node(
    node: KvNode,
    addr: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let listener = TcpListener::bind(addr).await?;
    println!("Node {} listening on {addr}", node.id);

    loop {
        let (stream, _) = listener.accept().await?;
        let node = node.clone();

        tokio::spawn(async move {
            let mut reader = BufReader::new(stream);
            let mut line = String::new();

            while reader.read_line(&mut line).await.unwrap_or(0) > 0 {
                let parts: Vec<&str> = line.trim().split_whitespace().collect();

                match parts.as_slice() {
                    ["GET", key] => {
                        let val = node.get(key).await;
                        let response = match val {
                            Some(v) => format!("OK {v}\n"),
                            None => "NOT_FOUND\n".to_string(),
                        };
                        if let Ok(writer) = reader.get_mut().write_all(
                            response.as_bytes()
                        ).await {
                            let _ = writer;
                        }
                    }
                    ["SET", key, value] => {
                        let ts = std::time::SystemTime::now()
                            .duration_since(std::time::UNIX_EPOCH)
                            .unwrap()
                            .as_millis() as u64;

                        node.set(key, value, ts).await;
                        node.replicate_to_peers(key, value, ts).await;

                        let _ = reader.get_mut()
                            .write_all(b"OK\n")
                            .await;
                    }
                    ["REPLICATE", key, value, ts] => {
                        if let Ok(timestamp) = ts.parse::<u64>() {
                            node.set(key, value, timestamp).await;
                        }
                    }
                    _ => {
                        let _ = reader.get_mut()
                            .write_all(b"ERR unknown command\n")
                            .await;
                    }
                }

                line.clear();
            }
        });
    }
}
```

This store uses **last-writer-wins** (LWW) conflict resolution with timestamps. It's the simplest approach and works well for many use cases, but it has a fundamental problem: if two clients write different values at nearly the same time, one write is silently discarded. The client that "loses" never knows.

## CRDTs — Conflict-Free Replicated Data Types

CRDTs are data structures designed so that replicas can be updated independently and concurrently without coordination, and they always converge to the same state when they sync up. No consensus protocol needed. No conflicts to resolve. It sounds too good to be true, and the trade-off is that CRDTs can only represent certain kinds of data.

### G-Counter (Grow-Only Counter)

The simplest CRDT. Each node maintains its own counter. The global count is the sum of all nodes' counters.

```rust
use std::collections::HashMap;

#[derive(Clone, Debug)]
struct GCounter {
    node_id: String,
    counts: HashMap<String, u64>,
}

impl GCounter {
    fn new(node_id: &str) -> Self {
        Self {
            node_id: node_id.to_string(),
            counts: HashMap::new(),
        }
    }

    fn increment(&mut self) {
        let count = self.counts.entry(self.node_id.clone()).or_insert(0);
        *count += 1;
    }

    fn value(&self) -> u64 {
        self.counts.values().sum()
    }

    /// Merge with another replica — take the max of each node's count
    fn merge(&mut self, other: &GCounter) {
        for (node, &count) in &other.counts {
            let entry = self.counts.entry(node.clone()).or_insert(0);
            *entry = (*entry).max(count);
        }
    }
}

fn gcounter_demo() {
    let mut node_a = GCounter::new("A");
    let mut node_b = GCounter::new("B");

    // Both increment independently
    node_a.increment();
    node_a.increment();
    node_a.increment();

    node_b.increment();
    node_b.increment();

    println!("Node A sees: {}", node_a.value()); // 3
    println!("Node B sees: {}", node_b.value()); // 2

    // Merge — both converge to the same value
    node_a.merge(&node_b);
    node_b.merge(&node_a);

    println!("After merge:");
    println!("Node A sees: {}", node_a.value()); // 5
    println!("Node B sees: {}", node_b.value()); // 5
}
```

The merge operation takes the maximum of each node's count. This is commutative, associative, and idempotent — meaning you can merge in any order, any number of times, and always get the same result. That's the CRDT magic.

### PN-Counter (Positive-Negative Counter)

A G-Counter can only grow. For a counter that supports both increment and decrement, use two G-Counters — one for increments, one for decrements.

```rust
#[derive(Clone, Debug)]
struct PNCounter {
    positive: GCounter,
    negative: GCounter,
}

impl PNCounter {
    fn new(node_id: &str) -> Self {
        Self {
            positive: GCounter::new(node_id),
            negative: GCounter::new(node_id),
        }
    }

    fn increment(&mut self) {
        self.positive.increment();
    }

    fn decrement(&mut self) {
        self.negative.increment();
    }

    fn value(&self) -> i64 {
        self.positive.value() as i64 - self.negative.value() as i64
    }

    fn merge(&mut self, other: &PNCounter) {
        self.positive.merge(&other.positive);
        self.negative.merge(&other.negative);
    }
}
```

### LWW-Register (Last-Writer-Wins Register)

For storing a single value where conflicts are resolved by timestamp:

```rust
#[derive(Clone, Debug)]
struct LWWRegister<T: Clone> {
    value: Option<T>,
    timestamp: u64,
}

impl<T: Clone> LWWRegister<T> {
    fn new() -> Self {
        Self {
            value: None,
            timestamp: 0,
        }
    }

    fn set(&mut self, value: T, timestamp: u64) {
        if timestamp > self.timestamp {
            self.value = Some(value);
            self.timestamp = timestamp;
        }
    }

    fn get(&self) -> Option<&T> {
        self.value.as_ref()
    }

    fn merge(&mut self, other: &LWWRegister<T>) {
        if other.timestamp > self.timestamp {
            self.value = other.value.clone();
            self.timestamp = other.timestamp;
        }
    }
}
```

### OR-Set (Observed-Remove Set)

Sets are trickier. If node A adds an element and node B concurrently removes it, what should happen? The OR-Set (Observed-Remove Set) tracks unique tags for each addition, so removes only affect the specific additions they've observed.

```rust
use std::collections::{HashMap, HashSet};
use uuid::Uuid;

#[derive(Clone, Debug)]
struct ORSet<T: Clone + Eq + std::hash::Hash> {
    node_id: String,
    /// Maps each element to its set of unique addition tags
    elements: HashMap<T, HashSet<String>>,
    /// Tombstones — tags that have been removed
    tombstones: HashSet<String>,
}

impl<T: Clone + Eq + std::hash::Hash> ORSet<T> {
    fn new(node_id: &str) -> Self {
        Self {
            node_id: node_id.to_string(),
            elements: HashMap::new(),
            tombstones: HashSet::new(),
        }
    }

    fn add(&mut self, element: T) {
        let tag = Uuid::new_v4().to_string();
        self.elements
            .entry(element)
            .or_insert_with(HashSet::new)
            .insert(tag);
    }

    fn remove(&mut self, element: &T) {
        if let Some(tags) = self.elements.remove(element) {
            // Move all tags to tombstones
            self.tombstones.extend(tags);
        }
    }

    fn contains(&self, element: &T) -> bool {
        self.elements.get(element).map_or(false, |tags| {
            tags.iter().any(|t| !self.tombstones.contains(t))
        })
    }

    fn values(&self) -> Vec<&T> {
        self.elements
            .iter()
            .filter(|(_, tags)| tags.iter().any(|t| !self.tombstones.contains(t)))
            .map(|(elem, _)| elem)
            .collect()
    }

    fn merge(&mut self, other: &ORSet<T>) {
        // Union of elements
        for (elem, tags) in &other.elements {
            let entry = self
                .elements
                .entry(elem.clone())
                .or_insert_with(HashSet::new);
            entry.extend(tags.iter().cloned());
        }

        // Union of tombstones
        self.tombstones.extend(other.tombstones.iter().cloned());

        // Clean up: remove elements where all tags are tombstoned
        self.elements.retain(|_, tags| {
            tags.retain(|t| !self.tombstones.contains(t));
            !tags.is_empty()
        });
    }
}
```

## Raft Consensus — A Simplified View

When you need strong consistency — when every node must agree on the order of operations — you need a consensus protocol. Raft is the most understandable one, designed explicitly to be easier to learn than Paxos.

The core idea: one node is the **leader**. All writes go through the leader. The leader replicates writes to a majority of nodes before considering them committed. If the leader fails, the remaining nodes elect a new one.

Here's a simplified Raft node state machine:

```rust
use std::collections::HashMap;
use std::time::{Duration, Instant};
use rand::Rng;

#[derive(Debug, Clone, PartialEq)]
enum Role {
    Follower,
    Candidate,
    Leader,
}

#[derive(Debug, Clone)]
struct LogEntry {
    term: u64,
    command: String,
}

struct RaftNode {
    id: String,
    role: Role,
    current_term: u64,
    voted_for: Option<String>,
    log: Vec<LogEntry>,
    commit_index: usize,
    last_applied: usize,

    // Leader state
    next_index: HashMap<String, usize>,
    match_index: HashMap<String, usize>,

    // Timing
    election_timeout: Duration,
    last_heartbeat: Instant,

    // Cluster
    peers: Vec<String>,
}

impl RaftNode {
    fn new(id: &str, peers: Vec<String>) -> Self {
        let timeout = Duration::from_millis(
            rand::rng().random_range(150..=300)
        );

        Self {
            id: id.to_string(),
            role: Role::Follower,
            current_term: 0,
            voted_for: None,
            log: vec![],
            commit_index: 0,
            last_applied: 0,
            next_index: HashMap::new(),
            match_index: HashMap::new(),
            election_timeout: timeout,
            last_heartbeat: Instant::now(),
            peers,
        }
    }

    fn should_start_election(&self) -> bool {
        self.role != Role::Leader
            && self.last_heartbeat.elapsed() > self.election_timeout
    }

    fn start_election(&mut self) {
        self.current_term += 1;
        self.role = Role::Candidate;
        self.voted_for = Some(self.id.clone());
        self.last_heartbeat = Instant::now();
        // Randomize timeout to avoid split votes
        self.election_timeout = Duration::from_millis(
            rand::rng().random_range(150..=300)
        );

        println!(
            "Node {} starting election for term {}",
            self.id, self.current_term
        );
    }

    fn become_leader(&mut self) {
        self.role = Role::Leader;
        println!(
            "Node {} became leader for term {}",
            self.id, self.current_term
        );

        // Initialize leader state
        let next = self.log.len();
        for peer in &self.peers {
            self.next_index.insert(peer.clone(), next);
            self.match_index.insert(peer.clone(), 0);
        }
    }

    fn receive_heartbeat(&mut self, term: u64, leader_id: &str) {
        if term >= self.current_term {
            self.current_term = term;
            self.role = Role::Follower;
            self.voted_for = None;
            self.last_heartbeat = Instant::now();
        }
    }

    fn propose(&mut self, command: String) -> bool {
        if self.role != Role::Leader {
            return false; // Only leader accepts writes
        }

        self.log.push(LogEntry {
            term: self.current_term,
            command,
        });

        true
    }

    fn majority(&self) -> usize {
        (self.peers.len() + 1) / 2 + 1
    }
}
```

This is vastly simplified — a real Raft implementation handles AppendEntries RPCs, vote requests, log compaction (snapshotting), configuration changes, and a dozen edge cases. But the state machine gives you the mental model.

In production, you wouldn't implement Raft yourself. You'd use a library like **openraft** or build on top of etcd. But understanding the protocol helps you reason about its guarantees and limitations.

## Lamport Clocks and Vector Clocks

Distributed systems can't rely on wall clocks — machines' clocks drift, NTP can jump, and "what happened first" is a surprisingly hard question when events happen on different machines.

**Lamport clocks** give you a partial ordering of events:

```rust
use std::sync::atomic::{AtomicU64, Ordering};

struct LamportClock {
    counter: AtomicU64,
}

impl LamportClock {
    fn new() -> Self {
        Self {
            counter: AtomicU64::new(0),
        }
    }

    /// Tick the clock for a local event
    fn tick(&self) -> u64 {
        self.counter.fetch_add(1, Ordering::SeqCst) + 1
    }

    /// Update the clock when receiving a message
    fn receive(&self, received_timestamp: u64) -> u64 {
        loop {
            let current = self.counter.load(Ordering::SeqCst);
            let new_val = current.max(received_timestamp) + 1;
            match self.counter.compare_exchange(
                current,
                new_val,
                Ordering::SeqCst,
                Ordering::SeqCst,
            ) {
                Ok(_) => return new_val,
                Err(_) => continue,
            }
        }
    }
}
```

**Vector clocks** give you more — they can tell you whether two events are causally related or concurrent:

```rust
use std::collections::HashMap;

#[derive(Clone, Debug)]
struct VectorClock {
    clocks: HashMap<String, u64>,
}

impl VectorClock {
    fn new() -> Self {
        Self {
            clocks: HashMap::new(),
        }
    }

    fn increment(&mut self, node_id: &str) {
        let counter = self.clocks.entry(node_id.to_string()).or_insert(0);
        *counter += 1;
    }

    fn merge(&mut self, other: &VectorClock) {
        for (node, &count) in &other.clocks {
            let entry = self.clocks.entry(node.clone()).or_insert(0);
            *entry = (*entry).max(count);
        }
    }

    /// Returns true if self happened before other
    fn happened_before(&self, other: &VectorClock) -> bool {
        let mut at_least_one_less = false;

        for (node, &count) in &other.clocks {
            let self_count = self.clocks.get(node).copied().unwrap_or(0);
            if self_count > count {
                return false; // self has a higher count somewhere
            }
            if self_count < count {
                at_least_one_less = true;
            }
        }

        // Check nodes that are in self but not in other
        for (node, &count) in &self.clocks {
            if !other.clocks.contains_key(node) && count > 0 {
                return false;
            }
        }

        at_least_one_less
    }

    /// Returns true if events are concurrent (neither happened before the other)
    fn concurrent_with(&self, other: &VectorClock) -> bool {
        !self.happened_before(other) && !other.happened_before(self)
    }
}

fn vector_clock_demo() {
    let mut vc_a = VectorClock::new();
    let mut vc_b = VectorClock::new();

    // Node A does something
    vc_a.increment("A");
    // A sends message to B — B merges and increments
    vc_b.merge(&vc_a);
    vc_b.increment("B");

    println!("A happened before B: {}", vc_a.happened_before(&vc_b)); // true

    // Now both do something concurrently
    vc_a.increment("A");
    vc_b.increment("B");

    println!(
        "A concurrent with B: {}",
        vc_a.concurrent_with(&vc_b)
    ); // true
}
```

Vector clocks are how DynamoDB detects conflicts — when two writes are concurrent (neither happened before the other), the system knows it needs conflict resolution.

## Putting It All Together

Here's how these patterns fit together in a real architecture:

- **CRDTs** for data that can tolerate eventual consistency — user presence indicators, view counts, shopping cart merges. No coordination needed, extremely low latency.
- **Consensus (Raft/Paxos)** for data that needs strong consistency — leader election, configuration management, financial transactions. Higher latency, but correctness guaranteed.
- **Vector clocks** for detecting conflicts in AP systems — when you can't use consensus but need to know when writes conflict.
- **Message queues** for decoupling services — events flow asynchronously, each service processes at its own pace.
- **Circuit breakers and retries** for handling the inevitable failures — network partitions, node crashes, slow services.

The art of distributed systems engineering is choosing the right consistency model for each piece of data, the right communication pattern for each interaction, and the right failure handling for each dependency. There's no universal answer — it depends on your business requirements, your latency budget, and how much complexity you're willing to manage.

## Course Wrap-Up

Over these 10 lessons, we've built up from raw TCP sockets to distributed system patterns. The progression was intentional — each layer builds on the one below it. HTTP sits on TCP. gRPC sits on HTTP/2. Retry logic wraps your HTTP calls. Circuit breakers wrap your retry logic. Message queues decouple your services. And distributed patterns like CRDTs and consensus determine how your data behaves across all of it.

Rust is uniquely well-suited for this kind of systems programming. The ownership model catches resource leaks at compile time. The type system makes invalid states unrepresentable. The async ecosystem gives you performance without sacrificing safety. And the error handling model forces you to think about failure paths — which is exactly the mindset you need for building systems that work across an unreliable network.

Build something. Break it. Fix it. That's how you learn this stuff.
