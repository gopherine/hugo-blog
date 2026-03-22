---
title: "Lesson 8: Async Mutexes — tokio::sync::Mutex vs std"
author: Atharva Pandey
series: "Async Rust Masterclass"
lesson: 8
keywords: [Rust, rust, async, tokio, mutex, sync, RwLock, shared state, concurrency]
tags: [Rust tutorial, rust, async, tokio]
date: '2025-01-20T07:15:44.000Z'
---

"Should I use `tokio::sync::Mutex` or `std::sync::Mutex` in async code?" I've seen this question in every Rust Discord server, every forum, every team Slack. And the answer most people give — "always use the async one in async code" — is wrong.

The real answer depends on how long you hold the lock and whether you need to `.await` while holding it. Get this wrong and you'll either deadlock your runtime or tank your performance.

## The Two Mutexes

```rust
// Standard library mutex — blocks the OS thread
let std_mutex = std::sync::Mutex::new(0);

// Tokio's async mutex — suspends the task
let tokio_mutex = tokio::sync::Mutex::new(0);
```

Key difference: when `std::sync::Mutex` can't acquire the lock, it blocks the thread. When `tokio::sync::Mutex` can't acquire the lock, it yields the task (returns `Poll::Pending`) so other tasks can run on that thread.

## When to Use Which

Here's my decision framework:

**Use `std::sync::Mutex` when:**
- The critical section is short (no `.await` inside)
- You're just reading or updating a value
- Performance matters (std is faster)

**Use `tokio::sync::Mutex` when:**
- You need to `.await` while holding the lock
- The critical section might take a while
- You need the lock to be `Send` across await points

```rust
use std::sync::Arc;
use tokio::time::{sleep, Duration};

// GOOD: std mutex for quick operations
async fn with_std_mutex() {
    let data = Arc::new(std::sync::Mutex::new(Vec::new()));

    let mut handles = vec![];
    for i in 0..10 {
        let data = data.clone();
        handles.push(tokio::spawn(async move {
            // Lock, push, unlock — microseconds
            data.lock().unwrap().push(i);
        }));
    }

    for h in handles {
        h.await.unwrap();
    }

    println!("std: {:?}", data.lock().unwrap());
}

// GOOD: tokio mutex when awaiting inside the lock
async fn with_tokio_mutex() {
    let data = Arc::new(tokio::sync::Mutex::new(Vec::new()));

    let mut handles = vec![];
    for i in 0..10 {
        let data = data.clone();
        handles.push(tokio::spawn(async move {
            let mut lock = data.lock().await;
            // We need to await while holding the lock
            sleep(Duration::from_millis(10)).await;
            lock.push(i);
            // Lock is held across an await point — only safe with tokio Mutex
        }));
    }

    for h in handles {
        h.await.unwrap();
    }

    println!("tokio: {:?}", data.lock().await);
}

#[tokio::main]
async fn main() {
    with_std_mutex().await;
    with_tokio_mutex().await;
}
```

## The Real Danger: std::Mutex Across .await

This is the most common mistake:

```rust
use std::sync::{Arc, Mutex};
use tokio::time::{sleep, Duration};

// BAD — DO NOT DO THIS
async fn dangerous() {
    let data = Arc::new(Mutex::new(0));

    let data_clone = data.clone();
    tokio::spawn(async move {
        let mut lock = data_clone.lock().unwrap();
        // If we await here, we're holding a std mutex across a yield point.
        // This blocks the executor thread if another task on the same thread
        // tries to acquire the lock.
        sleep(Duration::from_millis(100)).await; // BAD!
        *lock += 1;
    });
}
```

The compiler actually catches this in many cases — `std::sync::MutexGuard` is `!Send`, so you can't hold it across an `.await` in a multi-threaded runtime. But there are cases where it slips through (single-threaded runtime, or `unsafe` shenanigans).

## Practical Patterns

### Pattern: Shared Cache

```rust
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::Mutex;
use tokio::time::{sleep, Duration};

#[derive(Clone)]
struct Cache {
    data: Arc<Mutex<HashMap<String, String>>>,
}

impl Cache {
    fn new() -> Self {
        Cache {
            data: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    async fn get(&self, key: &str) -> Option<String> {
        self.data.lock().await.get(key).cloned()
    }

    async fn set(&self, key: String, value: String) {
        self.data.lock().await.insert(key, value);
    }

    async fn get_or_fetch(&self, key: &str) -> String {
        // Check cache first
        {
            let cache = self.data.lock().await;
            if let Some(val) = cache.get(key) {
                return val.clone();
            }
        } // Lock is dropped here!

        // Fetch the value (don't hold the lock during I/O!)
        let value = fetch_from_db(key).await;

        // Store in cache
        self.data.lock().await.insert(key.to_string(), value.clone());

        value
    }
}

async fn fetch_from_db(key: &str) -> String {
    sleep(Duration::from_millis(100)).await;
    format!("value-for-{key}")
}

#[tokio::main]
async fn main() {
    let cache = Cache::new();

    let mut handles = vec![];
    for i in 0..5 {
        let cache = cache.clone();
        handles.push(tokio::spawn(async move {
            let key = format!("key-{}", i % 3);
            let val = cache.get_or_fetch(&key).await;
            println!("Got: {key} = {val}");
        }));
    }

    for h in handles {
        h.await.unwrap();
    }
}
```

Notice how `get_or_fetch` drops the lock before doing I/O, then reacquires it. This is the pattern — hold the lock for as little time as possible.

### Pattern: Minimize Lock Scope with std::sync::Mutex

```rust
use std::sync::{Arc, Mutex};

#[derive(Clone)]
struct Stats {
    inner: Arc<Mutex<StatsInner>>,
}

struct StatsInner {
    request_count: u64,
    error_count: u64,
    total_latency_ms: u64,
}

impl Stats {
    fn new() -> Self {
        Stats {
            inner: Arc::new(Mutex::new(StatsInner {
                request_count: 0,
                error_count: 0,
                total_latency_ms: 0,
            })),
        }
    }

    fn record_request(&self, latency_ms: u64) {
        let mut stats = self.inner.lock().unwrap();
        stats.request_count += 1;
        stats.total_latency_ms += latency_ms;
    }

    fn record_error(&self) {
        self.inner.lock().unwrap().error_count += 1;
    }

    fn snapshot(&self) -> (u64, u64, f64) {
        let stats = self.inner.lock().unwrap();
        let avg = if stats.request_count > 0 {
            stats.total_latency_ms as f64 / stats.request_count as f64
        } else {
            0.0
        };
        (stats.request_count, stats.error_count, avg)
    }
}

#[tokio::main]
async fn main() {
    let stats = Stats::new();

    let mut handles = vec![];
    for i in 0..100 {
        let stats = stats.clone();
        handles.push(tokio::spawn(async move {
            // Simulate request processing
            tokio::time::sleep(tokio::time::Duration::from_millis(1)).await;

            // Recording is synchronous — std mutex is fine
            stats.record_request(i % 50);
            if i % 10 == 0 {
                stats.record_error();
            }
        }));
    }

    for h in handles {
        h.await.unwrap();
    }

    let (requests, errors, avg_latency) = stats.snapshot();
    println!("Requests: {requests}, Errors: {errors}, Avg latency: {avg_latency:.1}ms");
}
```

This uses `std::sync::Mutex` because we never hold the lock across an `.await`. The lock/unlock cycle is nanoseconds. No reason to pay for the async overhead.

## RwLock — Multiple Readers, One Writer

When reads vastly outnumber writes:

```rust
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;

struct ConfigStore {
    data: Arc<RwLock<HashMap<String, String>>>,
}

impl ConfigStore {
    fn new() -> Self {
        ConfigStore {
            data: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    async fn get(&self, key: &str) -> Option<String> {
        // Multiple tasks can read simultaneously
        self.data.read().await.get(key).cloned()
    }

    async fn set(&self, key: String, value: String) {
        // Only one writer at a time, blocks all readers
        self.data.write().await.insert(key, value);
    }
}

#[tokio::main]
async fn main() {
    let store = ConfigStore::new();
    store.set("db_host".into(), "localhost".into()).await;
    store.set("db_port".into(), "5432".into()).await;

    // Many concurrent readers
    let store = Arc::new(store);
    let mut handles = vec![];
    for _ in 0..10 {
        let store = store.clone();
        handles.push(tokio::spawn(async move {
            let host = store.get("db_host").await;
            println!("db_host = {host:?}");
        }));
    }

    for h in handles {
        h.await.unwrap();
    }
}
```

Same rules apply — prefer `std::sync::RwLock` for short critical sections without `.await`. Use `tokio::sync::RwLock` when you need to await while holding the lock.

## The "Actor Instead of Mutex" Pattern

Sometimes the cleanest solution isn't a mutex at all — it's a dedicated task that owns the state:

```rust
use tokio::sync::{mpsc, oneshot};
use std::collections::HashMap;

enum CacheCommand {
    Get {
        key: String,
        respond_to: oneshot::Sender<Option<String>>,
    },
    Set {
        key: String,
        value: String,
    },
}

async fn cache_actor(mut rx: mpsc::Receiver<CacheCommand>) {
    let mut store = HashMap::new();

    while let Some(cmd) = rx.recv().await {
        match cmd {
            CacheCommand::Get { key, respond_to } => {
                let _ = respond_to.send(store.get(&key).cloned());
            }
            CacheCommand::Set { key, value } => {
                store.insert(key, value);
            }
        }
    }
}

#[derive(Clone)]
struct CacheHandle {
    tx: mpsc::Sender<CacheCommand>,
}

impl CacheHandle {
    fn new() -> Self {
        let (tx, rx) = mpsc::channel(256);
        tokio::spawn(cache_actor(rx));
        CacheHandle { tx }
    }

    async fn get(&self, key: &str) -> Option<String> {
        let (respond_to, rx) = oneshot::channel();
        self.tx.send(CacheCommand::Get {
            key: key.to_string(),
            respond_to,
        }).await.unwrap();
        rx.await.unwrap()
    }

    async fn set(&self, key: String, value: String) {
        self.tx.send(CacheCommand::Set { key, value }).await.unwrap();
    }
}

#[tokio::main]
async fn main() {
    let cache = CacheHandle::new();

    cache.set("name".into(), "Atharva".into()).await;
    let name = cache.get("name").await;
    println!("name = {name:?}");
}
```

No mutex, no lock contention, no deadlock risk. The tradeoff is the overhead of channel communication versus the simplicity of a lock. For high-contention scenarios, actors often win. For low-contention, a mutex is simpler and faster.

## Performance Comparison

Quick mental model for performance:

1. **`std::sync::Mutex`** — fastest, no async overhead, nanosecond lock/unlock
2. **`tokio::sync::Mutex`** — slower, but cooperative (doesn't block the executor)
3. **Actor pattern** — most overhead per operation, but zero lock contention
4. **`std::sync::RwLock`** — good for read-heavy, but writer starvation is possible
5. **`tokio::sync::RwLock`** — async-friendly read-heavy access

My rule: start with `std::sync::Mutex`. Switch to `tokio::sync::Mutex` only if you need `.await` inside the lock. Consider actors only if contention is measurably high.

Don't optimize what you haven't measured. A `std::sync::Mutex` held for 100 nanoseconds is never your bottleneck, even in async code.

Next lesson: semaphores and rate limiting — because sometimes the problem isn't *protecting* shared state, it's *limiting* concurrent access to a resource.
