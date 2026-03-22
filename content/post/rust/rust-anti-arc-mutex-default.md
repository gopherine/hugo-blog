---
title: "Lesson 7: Arc<Mutex<T>> as Default — Reach for channels first"
author: Atharva Pandey
series: "Rust Anti-Patterns & Code Smells"
lesson: 7
keywords: [Rust, rust, anti-patterns, code quality, Arc, Mutex, channels, concurrency, shared state]
tags: [Rust tutorial, rust, anti-patterns, code-quality]
date: '2025-04-16T20:15:00.000Z'
---

There's a specific moment in every Rust developer's journey where they discover `Arc<Mutex<T>>` and start putting everything in it. I've been that developer. I had a web service that needed to share a cache between request handlers, and my first instinct was `Arc<Mutex<HashMap<String, CachedItem>>>`. It worked. Then traffic went up, contention went up, tail latency went up, and I spent a weekend profiling lock contention that shouldn't have existed in the first place — because most of my "shared mutable state" could have been restructured as message passing.

`Arc<Mutex<T>>` is not wrong. It's a legitimate synchronization primitive. But it's the goto of concurrent Rust — it works, it's easy to reach for, and it papers over design problems that would be better solved differently.

## The Smell

Here's what `Arc<Mutex<T>>` abuse looks like:

```rust
use std::sync::{Arc, Mutex};
use std::collections::HashMap;

struct AppState {
    users: Arc<Mutex<HashMap<String, User>>>,
    sessions: Arc<Mutex<HashMap<String, Session>>>,
    metrics: Arc<Mutex<MetricsCollector>>,
    rate_limiter: Arc<Mutex<RateLimiter>>,
    cache: Arc<Mutex<LruCache<String, CachedResponse>>>,
    job_queue: Arc<Mutex<VecDeque<Job>>>,
}

// Every handler locks the entire map to do one operation
async fn get_user(state: &AppState, user_id: &str) -> Option<User> {
    let users = state.users.lock().unwrap(); // lock the entire map
    users.get(user_id).cloned()              // clone to release lock
}

async fn add_job(state: &AppState, job: Job) {
    let mut queue = state.job_queue.lock().unwrap();
    queue.push_back(job);
    // lock released here — but anyone waiting has been blocked
}
```

Six `Arc<Mutex<_>>` fields. Every operation acquires a lock, does something quick, and releases it. Under load, threads stack up waiting for locks. Worse, if you ever accidentally hold two locks at once, you risk deadlock:

```rust
// Deadlock waiting to happen
async fn process_and_record(state: &AppState, user_id: &str) {
    let users = state.users.lock().unwrap();
    let mut metrics = state.metrics.lock().unwrap(); // if another thread locks these in opposite order: deadlock
    if let Some(user) = users.get(user_id) {
        metrics.record("user_accessed");
    }
}
```

## Why It's Actually Bad

**Lock contention kills performance.** When one thread holds a `Mutex`, every other thread that needs it blocks. For a web server handling thousands of concurrent requests, a single mutex protecting a shared HashMap means requests are serialized — exactly the opposite of what you want from async Rust.

**Deadlocks.** The moment you hold two locks simultaneously, you need to ensure consistent ordering across your entire codebase. This is impossible to enforce without discipline, and the compiler won't help you — Rust prevents data races, not deadlocks.

**Poisoned locks.** If a thread panics while holding a `Mutex`, the lock becomes poisoned. Every subsequent `.lock()` returns an `Err`. You now have to decide whether to recover, propagate, or ignore — adding error handling complexity that wouldn't exist with a different concurrency model.

**It hides the data flow.** When everything is behind `Arc<Mutex<_>>`, you can't see how data moves through your system by reading function signatures. Any function that has access to the shared state can read or write anything at any time. There's no structure to the concurrency — it's a free-for-all.

**Holding locks across await points.** This is the async-specific landmine. If you hold a `MutexGuard` across an `.await`, you might hold the lock for the entire duration of an I/O operation — seconds, potentially. Even with `tokio::sync::Mutex`, which is designed for async, holding locks across awaits should be a deliberate choice, not an accident.

## The Fix

### Alternative 1: Channels for producer-consumer patterns

If one part of your system produces work and another consumes it, use a channel:

```rust
use tokio::sync::mpsc;

#[derive(Debug)]
enum MetricsEvent {
    RequestReceived { path: String, method: String },
    RequestCompleted { path: String, duration_ms: u64 },
    Error { code: u16, message: String },
}

// Metrics collector owns its data — no shared state
struct MetricsCollector {
    rx: mpsc::Receiver<MetricsEvent>,
    counters: HashMap<String, u64>,
}

impl MetricsCollector {
    fn new() -> (mpsc::Sender<MetricsEvent>, Self) {
        let (tx, rx) = mpsc::channel(1000);
        (tx, MetricsCollector {
            rx,
            counters: HashMap::new(),
        })
    }

    async fn run(mut self) {
        while let Some(event) = self.rx.recv().await {
            match event {
                MetricsEvent::RequestReceived { path, .. } => {
                    *self.counters.entry(path).or_insert(0) += 1;
                }
                MetricsEvent::RequestCompleted { duration_ms, .. } => {
                    *self.counters.entry("total_duration_ms".into()).or_insert(0) += duration_ms;
                }
                MetricsEvent::Error { code, .. } => {
                    *self.counters.entry(format!("errors_{code}")).or_insert(0) += 1;
                }
            }
        }
    }
}

// Handlers just send events — no locking, no blocking
async fn handle_request(metrics_tx: &mpsc::Sender<MetricsEvent>, path: &str) {
    metrics_tx.send(MetricsEvent::RequestReceived {
        path: path.to_string(),
        method: "GET".to_string(),
    }).await.ok();

    // ... handle the request ...
}
```

No locks. No contention. No deadlocks. The metrics collector owns its data exclusively and processes events sequentially. Senders can fire events concurrently without blocking each other.

### Alternative 2: RwLock for read-heavy workloads

If your shared state is read much more often than it's written, use `RwLock` instead of `Mutex`:

```rust
use std::sync::RwLock;
use std::sync::Arc;

struct ConfigStore {
    config: Arc<RwLock<AppConfig>>,
}

impl ConfigStore {
    // Multiple readers can proceed concurrently
    fn get(&self, key: &str) -> Option<String> {
        let config = self.config.read().unwrap();
        config.get(key).cloned()
    }

    // Writes block readers, but are rare
    fn update(&self, key: &str, value: String) {
        let mut config = self.config.write().unwrap();
        config.set(key, value);
    }
}
```

For a config store that's read thousands of times per second and updated once an hour, `RwLock` eliminates virtually all contention. Readers never block each other.

### Alternative 3: Concurrent data structures

For concurrent maps and sets, use purpose-built concurrent data structures instead of wrapping standard collections in a Mutex:

```rust
use dashmap::DashMap;

// Instead of Arc<Mutex<HashMap<String, User>>>
let users: DashMap<String, User> = DashMap::new();

// Concurrent reads and writes without external locking
users.insert("alice".to_string(), alice);
if let Some(user) = users.get("alice") {
    println!("Found: {:?}", user.value());
}
```

`DashMap` uses fine-grained locking internally — it shards the map and locks individual shards. This means operations on different keys can proceed concurrently, which is exactly what you want for a concurrent cache.

### Alternative 4: Actor pattern for complex state

If your shared state has complex invariants — things that need to be true across multiple fields simultaneously — use the actor pattern:

```rust
use tokio::sync::{mpsc, oneshot};

enum CacheCommand {
    Get {
        key: String,
        reply: oneshot::Sender<Option<String>>,
    },
    Set {
        key: String,
        value: String,
    },
    Invalidate {
        key: String,
    },
    Stats {
        reply: oneshot::Sender<CacheStats>,
    },
}

struct CacheActor {
    data: HashMap<String, String>,
    hits: u64,
    misses: u64,
    rx: mpsc::Receiver<CacheCommand>,
}

impl CacheActor {
    async fn run(mut self) {
        while let Some(cmd) = self.rx.recv().await {
            match cmd {
                CacheCommand::Get { key, reply } => {
                    let result = self.data.get(&key).cloned();
                    if result.is_some() { self.hits += 1; } else { self.misses += 1; }
                    let _ = reply.send(result);
                }
                CacheCommand::Set { key, value } => {
                    self.data.insert(key, value);
                }
                CacheCommand::Invalidate { key } => {
                    self.data.remove(&key);
                }
                CacheCommand::Stats { reply } => {
                    let _ = reply.send(CacheStats {
                        size: self.data.len(),
                        hits: self.hits,
                        misses: self.misses,
                    });
                }
            }
        }
    }
}

// Handle to the actor — cheap to clone, no locks
#[derive(Clone)]
struct CacheHandle {
    tx: mpsc::Sender<CacheCommand>,
}

impl CacheHandle {
    async fn get(&self, key: &str) -> Option<String> {
        let (reply_tx, reply_rx) = oneshot::channel();
        self.tx.send(CacheCommand::Get {
            key: key.to_string(),
            reply: reply_tx,
        }).await.ok()?;
        reply_rx.await.ok()?
    }

    async fn set(&self, key: String, value: String) {
        let _ = self.tx.send(CacheCommand::Set { key, value }).await;
    }
}
```

The actor owns all its state. The handle is a thin wrapper around a channel sender. You can clone the handle freely and hand it to any number of tasks. No locks, no contention, no deadlocks, and the invariants (hits + misses = total lookups) are maintained automatically because there's only one thread mutating the state.

## When Arc<Mutex<T>> Is Actually Fine

I don't want to be absolutist about this. Mutex has its place:

- **Startup/shutdown.** One-time initialization or cleanup where contention is impossible.
- **Low-contention state.** A config value that's read once per request and updated once per hour. Use `RwLock`, but even `Mutex` would be fine.
- **Small critical sections.** If you lock, do an O(1) operation, and unlock — and the lock is rarely contended — Mutex is simpler than setting up channels.
- **When the mental model of channels doesn't fit.** Some problems are genuinely "shared mutable state" and channels would be unnatural.

## The Decision Framework

When you need concurrent access to shared state, ask:

1. **Is it producer-consumer?** Use a channel (`mpsc`, `broadcast`, `watch`).
2. **Is it read-heavy?** Use `RwLock` or a concurrent data structure like `DashMap`.
3. **Does it have complex invariants?** Use the actor pattern.
4. **Is it simple, low-contention, and short-lived?** `Mutex` is fine.
5. **Is it a counter or flag?** Use atomics (`AtomicU64`, `AtomicBool`).

The goal isn't to avoid `Arc<Mutex<T>>` entirely — it's to stop reaching for it as the default. Rust's concurrency story is rich. Mutexes are one tool in a large toolbox. Make sure you've considered the others before grabbing the first thing you see.
