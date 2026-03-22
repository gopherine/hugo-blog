---
title: "Lesson 7: Async Channels — tokio::sync::mpsc"
author: Atharva Pandey
series: "Async Rust Masterclass"
lesson: 7
keywords: [Rust, rust, async, tokio, channels, mpsc, oneshot, broadcast, watch]
tags: [Rust tutorial, rust, async, tokio]
date: '2025-01-18T13:52:28.000Z'
---

The first real async service I built had a classic architecture: an HTTP handler receives a request, puts work on a queue, a background worker processes it, and the result gets sent back. In Go, this is channels all day. In async Rust, it's *also* channels — but you've got four different kinds to choose from, and picking the wrong one leads to subtle bugs.

This lesson covers all four of Tokio's channel types and when to reach for each one.

## The Four Channel Types

| Channel | Senders | Receivers | Buffered | Use Case |
|---------|---------|-----------|----------|----------|
| `mpsc` | Many | One | Yes | Work queues, command channels |
| `oneshot` | One | One | No | Request-response, single results |
| `broadcast` | Many | Many | Yes | Event broadcasting, pub-sub |
| `watch` | One | Many | No (latest only) | Config updates, state changes |

## mpsc — The Workhorse

Multi-producer, single-consumer. This is your default choice for most async communication:

```rust
use tokio::sync::mpsc;

#[tokio::main]
async fn main() {
    // Buffer size of 32 — sender blocks when full
    let (tx, mut rx) = mpsc::channel::<String>(32);

    // Multiple senders via clone
    let tx2 = tx.clone();

    tokio::spawn(async move {
        tx.send("from sender 1".to_string()).await.unwrap();
    });

    tokio::spawn(async move {
        tx2.send("from sender 2".to_string()).await.unwrap();
    });

    // Receive until all senders are dropped
    while let Some(msg) = rx.recv().await {
        println!("Got: {msg}");
    }

    println!("Channel closed — all senders dropped");
}
```

### Bounded vs Unbounded

```rust
use tokio::sync::mpsc;

#[tokio::main]
async fn main() {
    // Bounded: blocks sender when buffer is full
    let (tx, mut rx) = mpsc::channel::<i32>(10);

    // Unbounded: never blocks sender, can use unlimited memory
    let (utx, mut urx) = mpsc::unbounded_channel::<i32>();

    // Bounded send is async (might wait)
    tx.send(1).await.unwrap();

    // Unbounded send is sync (never waits)
    utx.send(1).unwrap(); // No .await needed!

    println!("bounded: {:?}", rx.recv().await);
    println!("unbounded: {:?}", urx.recv().await);
}
```

**Always prefer bounded channels.** Unbounded channels are a memory leak waiting to happen — if the consumer is slower than the producer, the buffer grows forever. Bounded channels provide natural backpressure.

The only time I use unbounded channels is when the sender is synchronous code that can't `.await` (like a Drop implementation or a signal handler).

### The Worker Pattern

```rust
use tokio::sync::mpsc;
use tokio::time::{sleep, Duration};

#[derive(Debug)]
enum Job {
    Process(String),
    Flush,
    Shutdown,
}

async fn worker(mut rx: mpsc::Receiver<Job>) {
    let mut batch = Vec::new();

    while let Some(job) = rx.recv().await {
        match job {
            Job::Process(data) => {
                batch.push(data);
                if batch.len() >= 5 {
                    println!("Auto-flushing batch of {}: {:?}", batch.len(), batch);
                    batch.clear();
                }
            }
            Job::Flush => {
                if !batch.is_empty() {
                    println!("Flushing batch of {}: {:?}", batch.len(), batch);
                    batch.clear();
                }
            }
            Job::Shutdown => {
                if !batch.is_empty() {
                    println!("Final flush of {}: {:?}", batch.len(), batch);
                }
                println!("Worker shutting down");
                break;
            }
        }
    }
}

#[tokio::main]
async fn main() {
    let (tx, rx) = mpsc::channel(100);

    let worker_handle = tokio::spawn(worker(rx));

    for i in 0..12 {
        tx.send(Job::Process(format!("item-{i}"))).await.unwrap();
    }
    tx.send(Job::Flush).await.unwrap();
    tx.send(Job::Shutdown).await.unwrap();

    worker_handle.await.unwrap();
}
```

### try_send — Non-blocking Send

Sometimes you don't want to wait:

```rust
use tokio::sync::mpsc;

#[tokio::main]
async fn main() {
    let (tx, mut rx) = mpsc::channel(2); // Small buffer

    tx.send(1).await.unwrap();
    tx.send(2).await.unwrap();

    // Buffer is full — try_send returns immediately with an error
    match tx.try_send(3) {
        Ok(()) => println!("Sent"),
        Err(mpsc::error::TrySendError::Full(val)) => {
            println!("Channel full, couldn't send {val}");
        }
        Err(mpsc::error::TrySendError::Closed(val)) => {
            println!("Channel closed, couldn't send {val}");
        }
    }

    println!("Received: {:?}", rx.recv().await);
}
```

## oneshot — Request-Response

One sender, one receiver, one value. Perfect for getting a result back from another task:

```rust
use tokio::sync::{mpsc, oneshot};
use tokio::time::{sleep, Duration};

#[derive(Debug)]
struct DbQuery {
    sql: String,
    respond_to: oneshot::Sender<Vec<String>>,
}

async fn db_worker(mut rx: mpsc::Receiver<DbQuery>) {
    while let Some(query) = rx.recv().await {
        // Simulate database work
        sleep(Duration::from_millis(50)).await;
        let results = vec![format!("result for: {}", query.sql)];

        // Send the result back to the requester
        let _ = query.respond_to.send(results);
    }
}

#[tokio::main]
async fn main() {
    let (tx, rx) = mpsc::channel(32);

    tokio::spawn(db_worker(rx));

    // Make a query and get the result back
    let (resp_tx, resp_rx) = oneshot::channel();
    tx.send(DbQuery {
        sql: "SELECT * FROM users".to_string(),
        respond_to: resp_tx,
    }).await.unwrap();

    let results = resp_rx.await.unwrap();
    println!("Query results: {results:?}");

    // Make another query
    let (resp_tx, resp_rx) = oneshot::channel();
    tx.send(DbQuery {
        sql: "SELECT * FROM orders".to_string(),
        respond_to: resp_tx,
    }).await.unwrap();

    let results = resp_rx.await.unwrap();
    println!("Query results: {results:?}");
}
```

This pattern is everywhere — it's how you build an "actor" in Rust. The actor owns some state (like a database connection), processes messages from a channel, and sends results back via oneshot channels.

## broadcast — Fan-Out

Every receiver gets every message. Think event bus:

```rust
use tokio::sync::broadcast;
use tokio::time::{sleep, Duration};

#[derive(Clone, Debug)]
enum Event {
    UserCreated(String),
    OrderPlaced(u64),
    SystemAlert(String),
}

#[tokio::main]
async fn main() {
    let (tx, _) = broadcast::channel::<Event>(100);

    // Email service — only cares about user events
    let mut rx1 = tx.subscribe();
    tokio::spawn(async move {
        while let Ok(event) = rx1.recv().await {
            if let Event::UserCreated(name) = event {
                println!("[email] Sending welcome email to {name}");
            }
        }
    });

    // Analytics service — cares about everything
    let mut rx2 = tx.subscribe();
    tokio::spawn(async move {
        while let Ok(event) = rx2.recv().await {
            println!("[analytics] Event: {event:?}");
        }
    });

    // Alerting service
    let mut rx3 = tx.subscribe();
    tokio::spawn(async move {
        while let Ok(event) = rx3.recv().await {
            if let Event::SystemAlert(msg) = event {
                println!("[alert] ALERT: {msg}");
            }
        }
    });

    // Produce events
    sleep(Duration::from_millis(10)).await; // Let subscribers start

    tx.send(Event::UserCreated("Alice".into())).unwrap();
    tx.send(Event::OrderPlaced(42)).unwrap();
    tx.send(Event::SystemAlert("High CPU usage".into())).unwrap();

    sleep(Duration::from_millis(100)).await;
}
```

Broadcast channels have a fixed capacity. If a slow receiver falls behind, it gets a `RecvError::Lagged(n)` telling it how many messages it missed. This is a design choice — broadcast prioritizes throughput over reliability.

```rust
use tokio::sync::broadcast;

#[tokio::main]
async fn main() {
    let (tx, mut rx) = broadcast::channel(2); // Tiny buffer

    tx.send(1).unwrap();
    tx.send(2).unwrap();
    tx.send(3).unwrap(); // This overwrites message 1

    match rx.recv().await {
        Ok(val) => println!("Got: {val}"),
        Err(broadcast::error::RecvError::Lagged(n)) => {
            println!("Missed {n} messages!");
        }
        Err(broadcast::error::RecvError::Closed) => {
            println!("Channel closed");
        }
    }
}
```

## watch — Latest Value Only

Watch channels only keep the most recent value. Receivers always see the current state, never a queue of past states:

```rust
use tokio::sync::watch;
use tokio::time::{sleep, Duration};

#[derive(Debug, Clone)]
struct Config {
    max_connections: u32,
    timeout_ms: u64,
}

#[tokio::main]
async fn main() {
    let initial = Config {
        max_connections: 100,
        timeout_ms: 5000,
    };
    let (tx, rx) = watch::channel(initial);

    // Worker that reacts to config changes
    let mut rx1 = rx.clone();
    tokio::spawn(async move {
        loop {
            // Wait for the value to change
            if rx1.changed().await.is_err() {
                break; // Sender dropped
            }
            let config = rx1.borrow().clone();
            println!("[worker] Config updated: {config:?}");
        }
    });

    // Another worker
    let mut rx2 = rx.clone();
    tokio::spawn(async move {
        loop {
            if rx2.changed().await.is_err() {
                break;
            }
            let config = rx2.borrow().clone();
            println!("[monitor] New timeout: {}ms", config.timeout_ms);
        }
    });

    sleep(Duration::from_millis(50)).await;

    // Update config
    tx.send(Config {
        max_connections: 200,
        timeout_ms: 3000,
    }).unwrap();

    sleep(Duration::from_millis(50)).await;

    tx.send(Config {
        max_connections: 200,
        timeout_ms: 1000,
    }).unwrap();

    sleep(Duration::from_millis(100)).await;
}
```

Watch is perfect for:
- Configuration that can change at runtime
- Shutdown signals (start as `false`, flip to `true`)
- Progress reporting (you only care about the latest value)

## Choosing the Right Channel

Decision tree:

1. **One response to one request?** → `oneshot`
2. **Latest value only, multiple readers?** → `watch`
3. **Every receiver needs every message?** → `broadcast`
4. **Everything else?** → `mpsc`

And within `mpsc`:
- **Default to bounded.** Pick a reasonable buffer size (32, 64, 128).
- **Use unbounded only when the sender can't await** (synchronous context, Drop impl).

## Anti-patterns

### Don't use channels when a mutex works

```rust
use std::sync::Arc;
use tokio::sync::Mutex;

// If you just need shared mutable state, a mutex is simpler:
async fn simple_counter() {
    let counter = Arc::new(Mutex::new(0u64));

    let mut handles = vec![];
    for _ in 0..10 {
        let counter = counter.clone();
        handles.push(tokio::spawn(async move {
            let mut lock = counter.lock().await;
            *lock += 1;
        }));
    }

    for h in handles {
        h.await.unwrap();
    }

    println!("Counter: {}", *counter.lock().await);
}

#[tokio::main]
async fn main() {
    simple_counter().await;
}
```

Don't build an actor with a channel just to wrap a `HashMap`. Sometimes a `Mutex<HashMap>` is the right answer.

### Don't use unbounded channels as "fire and forget"

```rust
// BAD: Unbounded channel grows without limit if consumer is slow
// let (tx, rx) = mpsc::unbounded_channel();

// GOOD: Bounded channel provides backpressure
// let (tx, rx) = mpsc::channel(100);
```

### Don't ignore send errors

```rust
use tokio::sync::mpsc;

#[tokio::main]
async fn main() {
    let (tx, rx) = mpsc::channel::<String>(10);
    drop(rx); // Receiver is gone

    // This silently discards the error — DON'T do this
    // let _ = tx.send("hello".to_string()).await;

    // Handle the error
    if tx.send("hello".to_string()).await.is_err() {
        println!("Receiver dropped — nobody is listening");
    }
}
```

Channels are the plumbing of async Rust applications. Get comfortable with all four types, and you'll be able to wire up any concurrent architecture you need. Next up: async mutexes and when you should (and shouldn't) use them.
