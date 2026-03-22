---
title: "Lesson 5: tokio::select! — Racing futures"
author: Atharva Pandey
series: "Async Rust Masterclass"
lesson: 5
keywords: [Rust, rust, async, tokio, select, racing, futures, cancellation]
tags: [Rust tutorial, rust, async, tokio]
date: '2025-01-14T16:19:55.000Z'
---

A couple months ago I was building a WebSocket handler that needed to do three things simultaneously: read from the socket, check a shutdown signal, and send periodic heartbeats. With `join!`, I'd need all three to complete. But I didn't want them all to complete — I wanted to react to whichever one happened *first*.

That's what `select!` does. It races multiple futures and gives you the result of the winner. The losers are dropped.

## Basic select!

```rust
use tokio::time::{sleep, Duration};

#[tokio::main]
async fn main() {
    tokio::select! {
        _ = sleep(Duration::from_millis(100)) => {
            println!("Timer fired first");
        }
        _ = sleep(Duration::from_millis(200)) => {
            println!("This won't print");
        }
    }
}
```

`select!` polls all branches concurrently. When one completes, the others are **cancelled** (their futures are dropped). Only one branch ever executes.

## Real-World Pattern: Shutdown Signal

This is probably the most common use of `select!`:

```rust
use tokio::signal;
use tokio::time::{sleep, Duration};

async fn run_server() {
    loop {
        // Simulate handling requests
        sleep(Duration::from_millis(500)).await;
        println!("Handled a request");
    }
}

#[tokio::main]
async fn main() {
    tokio::select! {
        _ = run_server() => {
            println!("Server stopped on its own");
        }
        _ = signal::ctrl_c() => {
            println!("\nShutdown signal received, cleaning up...");
        }
    }

    println!("Goodbye");
}
```

The server loop runs until Ctrl+C is pressed. No complex threading, no channels, no flags — just race the server against the signal.

## Pattern Matching in select!

Each branch can pattern-match on the result:

```rust
use tokio::sync::mpsc;
use tokio::time::{sleep, Duration};

#[tokio::main]
async fn main() {
    let (tx, mut rx) = mpsc::channel::<String>(10);

    // Simulate sending a message after a delay
    tokio::spawn(async move {
        sleep(Duration::from_millis(150)).await;
        tx.send("hello".to_string()).await.unwrap();
    });

    tokio::select! {
        Some(msg) = rx.recv() => {
            println!("Got message: {msg}");
        }
        _ = sleep(Duration::from_secs(1)) => {
            println!("Timed out waiting for message");
        }
    }
}
```

If `rx.recv()` returns `None` (channel closed), that branch is disabled — it won't match. This is how you gracefully handle channel closure in a select loop.

## select! in a Loop

The real power shows up when you use `select!` in a loop:

```rust
use tokio::sync::mpsc;
use tokio::time::{interval, Duration};

async fn worker(mut rx: mpsc::Receiver<String>) {
    let mut heartbeat = interval(Duration::from_secs(5));
    let mut count = 0u64;

    loop {
        tokio::select! {
            Some(msg) = rx.recv() => {
                count += 1;
                println!("[{count}] Processing: {msg}");
            }
            _ = heartbeat.tick() => {
                println!("Heartbeat — processed {count} messages so far");
            }
        }
    }
}

#[tokio::main]
async fn main() {
    let (tx, rx) = mpsc::channel(100);

    let worker_handle = tokio::spawn(worker(rx));

    // Send some messages
    for i in 0..5 {
        tx.send(format!("task-{i}")).await.unwrap();
        tokio::time::sleep(Duration::from_millis(100)).await;
    }

    drop(tx); // Close the channel
    // Worker will exit when rx.recv() returns None and heartbeat isn't matched

    // Actually, the worker loop runs forever because heartbeat always ticks.
    // We'd need a shutdown mechanism — we'll handle that properly in lesson 11.
    worker_handle.abort();
}
```

## Biased Selection

By default, `select!` randomly picks which branch to poll first (to prevent starvation). If you want deterministic priority, use `biased`:

```rust
use tokio::sync::mpsc;
use tokio::time::{sleep, Duration};

#[tokio::main]
async fn main() {
    let (tx, mut rx) = mpsc::channel::<String>(100);
    let (shutdown_tx, mut shutdown_rx) = mpsc::channel::<()>(1);

    // Send shutdown after brief delay
    tokio::spawn(async move {
        sleep(Duration::from_millis(50)).await;
        shutdown_tx.send(()).await.unwrap();
    });

    // Send some messages
    let tx_clone = tx.clone();
    tokio::spawn(async move {
        for i in 0..100 {
            let _ = tx_clone.send(format!("msg-{i}")).await;
        }
    });

    sleep(Duration::from_millis(100)).await;

    loop {
        tokio::select! {
            biased;  // Check branches in order

            // Shutdown has highest priority
            _ = shutdown_rx.recv() => {
                println!("Shutting down");
                break;
            }
            Some(msg) = rx.recv() => {
                println!("Got: {msg}");
            }
            else => break,
        }
    }
}
```

With `biased`, shutdown is always checked first. Without it, the runtime might process more messages even after shutdown was signaled.

Use `biased` sparingly — it can cause starvation of lower-priority branches.

## The else Branch

The `else` branch runs when all other branches are disabled (their patterns didn't match or their futures returned a "disabled" value like `None` from a closed channel):

```rust
use tokio::sync::mpsc;

#[tokio::main]
async fn main() {
    let (_tx, mut rx) = mpsc::channel::<String>(10);
    // tx is dropped immediately, so channel is closed

    tokio::select! {
        Some(msg) = rx.recv() => {
            println!("Got: {msg}");
        }
        else => {
            println!("All channels closed, nothing to do");
        }
    }
}
```

## Borrowing in select!

Unlike `spawn`, `select!` doesn't require `'static`. You can borrow local data:

```rust
use tokio::time::{sleep, Duration};

#[tokio::main]
async fn main() {
    let mut data = vec![1, 2, 3];

    tokio::select! {
        _ = sleep(Duration::from_millis(100)) => {
            // Can borrow local data — no 'static needed
            data.push(4);
            println!("Data: {data:?}");
        }
        _ = sleep(Duration::from_millis(200)) => {
            data.push(5);
        }
    }

    // data is still usable after select!
    println!("Final: {data:?}");
}
```

This is one of the big advantages of `select!` over `spawn` — you don't need to clone or `Arc` everything.

## Cancellation Safety — The Hidden Danger

Here's the thing nobody tells beginners: **cancellation in `select!` can lose data**.

```rust
use tokio::sync::mpsc;
use tokio::time::{sleep, Duration};

#[tokio::main]
async fn main() {
    let (tx, mut rx) = mpsc::channel::<u32>(10);

    tokio::spawn(async move {
        for i in 0..5 {
            tx.send(i).await.unwrap();
            sleep(Duration::from_millis(50)).await;
        }
    });

    loop {
        tokio::select! {
            val = rx.recv() => {
                match val {
                    Some(v) => println!("Got: {v}"),
                    None => break,
                }
            }
            _ = sleep(Duration::from_millis(200)) => {
                println!("Timeout!");
                // WARNING: if rx.recv() had partially completed
                // (read a value internally), that value is now lost!
                // This particular case is safe because mpsc::recv IS
                // cancellation-safe, but not all futures are.
            }
        }
    }
}
```

We'll dedicate all of lesson 10 to cancellation safety because it's that important. For now, know that `select!` drops the losing futures, and if those futures had done partial work, that work might be lost.

**Cancellation-safe** futures from Tokio (safe to use in `select!`):
- `mpsc::Receiver::recv()`
- `oneshot::Receiver::recv()`
- `TcpListener::accept()`
- `tokio::time::sleep()`

**NOT cancellation-safe** (dangerous in `select!`):
- `AsyncReadExt::read()` (might have partially filled the buffer)
- Custom futures that do partial state mutation before yielding

## Preconditions with if

You can conditionally disable branches:

```rust
use tokio::sync::mpsc;
use tokio::time::{sleep, Duration};

#[tokio::main]
async fn main() {
    let (tx, mut rx) = mpsc::channel::<String>(10);
    let mut paused = false;

    tokio::spawn(async move {
        for i in 0..3 {
            tx.send(format!("msg-{i}")).await.unwrap();
            sleep(Duration::from_millis(100)).await;
        }
    });

    for _ in 0..5 {
        tokio::select! {
            Some(msg) = rx.recv(), if !paused => {
                println!("Processing: {msg}");
                if msg == "msg-1" {
                    paused = true;
                    println!("Pausing message processing");
                }
            }
            _ = sleep(Duration::from_millis(300)) => {
                println!("Tick (paused={paused})");
                paused = false; // Resume
            }
        }
    }
}
```

## Complete Example: A Mini Task Runner

```rust
use tokio::sync::mpsc;
use tokio::time::{sleep, interval, Duration, Instant};

#[derive(Debug)]
enum Command {
    Run(String),
    Status,
    Shutdown,
}

async fn task_runner(mut cmd_rx: mpsc::Receiver<Command>) {
    let mut completed = 0u64;
    let mut status_interval = interval(Duration::from_secs(2));
    let start = Instant::now();

    loop {
        tokio::select! {
            Some(cmd) = cmd_rx.recv() => {
                match cmd {
                    Command::Run(name) => {
                        println!("[{:.1}s] Running task: {name}",
                            start.elapsed().as_secs_f64());
                        sleep(Duration::from_millis(100)).await;
                        completed += 1;
                        println!("[{:.1}s] Completed: {name}",
                            start.elapsed().as_secs_f64());
                    }
                    Command::Status => {
                        println!("[{:.1}s] Status: {completed} tasks completed",
                            start.elapsed().as_secs_f64());
                    }
                    Command::Shutdown => {
                        println!("[{:.1}s] Shutting down after {completed} tasks",
                            start.elapsed().as_secs_f64());
                        break;
                    }
                }
            }
            _ = status_interval.tick() => {
                println!("[{:.1}s] Periodic status: {completed} completed",
                    start.elapsed().as_secs_f64());
            }
        }
    }
}

#[tokio::main]
async fn main() {
    let (tx, rx) = mpsc::channel(32);

    let runner = tokio::spawn(task_runner(rx));

    tx.send(Command::Run("build".into())).await.unwrap();
    tx.send(Command::Run("test".into())).await.unwrap();
    tx.send(Command::Status).await.unwrap();
    sleep(Duration::from_millis(500)).await;
    tx.send(Command::Run("deploy".into())).await.unwrap();
    tx.send(Command::Shutdown).await.unwrap();

    runner.await.unwrap();
}
```

`select!` is the swiss army knife of async Rust. You'll use it for timeouts, shutdown signals, multiplexing channels, heartbeats, and pretty much any scenario where you need to react to the first of several events. Master it, and you can build anything.

Next up: streams — async iterators for when you want a *sequence* of values from a future.
