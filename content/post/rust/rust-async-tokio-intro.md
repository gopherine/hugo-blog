---
title: "Lesson 3: Tokio — The runtime that powers async Rust"
author: Atharva Pandey
series: "Async Rust Masterclass"
lesson: 3
keywords: [Rust, rust, async, tokio, runtime, executor, reactor, multi-threaded]
tags: [Rust tutorial, rust, async, tokio]
date: '2025-01-10T11:05:33.000Z'
---

Every async Rust tutorial shows you `#[tokio::main]` in the first example and then moves on like that's totally self-explanatory. It's not. That macro hides a *lot* of important decisions, and if you don't understand what it's doing, you're going to make some costly mistakes.

I once spent an entire day debugging a deadlock that happened because I was using the single-threaded runtime without realizing it. One `#[tokio::main(flavor = "current_thread")]` vs the default, and my entire application's behavior changed. That shouldn't surprise anyone — but it surprised me.

## Why You Need a Runtime At All

Rust's standard library gives you `Future`, `Poll`, `Waker`, and... that's it. There's no built-in executor. No event loop. No async I/O. The language gives you the *abstraction*, but you bring your own *engine*.

This is a deliberate design choice. Rust targets everything from embedded systems to web servers. A microcontroller doesn't want Tokio's thread pool. A web server doesn't want a single-threaded event loop. By keeping the runtime out of `std`, Rust lets you pick the right tool.

In practice, Tokio has won the ecosystem. Most async libraries target Tokio. Most production Rust services run on Tokio. That's why this course uses it exclusively.

## Setting Up Tokio

```toml
# Cargo.toml
[dependencies]
tokio = { version = "1", features = ["full"] }
```

The `"full"` feature flag enables everything: multi-threaded runtime, I/O, timers, channels, sync primitives. For production, you might want to be selective:

```toml
tokio = { version = "1", features = ["rt-multi-thread", "macros", "time", "io-util", "net", "sync"] }
```

## The Two Runtime Flavors

Tokio gives you two runtime configurations, and choosing the wrong one will bite you.

### Multi-threaded (default)

```rust
#[tokio::main]
async fn main() {
    println!("Running on a thread pool!");
}

// This expands to:
fn main() {
    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .unwrap()
        .block_on(async {
            println!("Running on a thread pool!");
        })
}
```

The multi-threaded runtime creates a thread pool (default: one thread per CPU core). Tasks can be stolen between threads for load balancing. This is what you want for servers.

### Current-thread

```rust
#[tokio::main(flavor = "current_thread")]
async fn main() {
    println!("Running on a single thread!");
}

// Expands to:
fn main() {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap()
        .block_on(async {
            println!("Running on a single thread!");
        })
}
```

Everything runs on one thread. No work stealing, no thread synchronization overhead. Good for CLI tools, simple scripts, or when you know you're I/O-bound and don't need parallelism.

## Building the Runtime Manually

The macro is convenient, but sometimes you need more control:

```rust
use tokio::runtime::Runtime;
use std::time::Duration;

fn main() {
    let rt = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(4)              // Explicit thread count
        .thread_name("my-worker")       // Named threads for debugging
        .thread_stack_size(3 * 1024 * 1024) // 3MB stack
        .enable_all()
        .build()
        .unwrap();

    rt.block_on(async {
        println!("Custom runtime!");
        do_async_work().await;
    });

    // Runtime is dropped here — all tasks are cancelled
}

async fn do_async_work() {
    tokio::time::sleep(Duration::from_millis(100)).await;
    println!("Work done");
}
```

Why would you build manually? A few reasons:
- You want to control the number of worker threads
- You need to embed Tokio inside a larger synchronous application
- You're running multiple runtimes (rare, but it happens)
- You want named threads for better profiling

## Tokio's Architecture: Reactor + Executor

Tokio has two main components:

**The Reactor** handles I/O events. It wraps `epoll` (Linux), `kqueue` (macOS), or `IOCP` (Windows). When a socket becomes readable or a timer fires, the reactor wakes the appropriate task.

**The Executor** polls tasks that are ready to make progress. In multi-threaded mode, it uses a work-stealing scheduler — if one thread runs out of tasks, it steals from another thread's queue.

```
┌─────────────────────────────────────────────┐
│                  Tokio Runtime              │
│                                             │
│  ┌─────────────────────────────────────┐   │
│  │           Executor                   │   │
│  │  ┌─────────┐  ┌─────────┐          │   │
│  │  │ Worker 1 │  │ Worker 2 │  ...    │   │
│  │  │ [tasks]  │  │ [tasks]  │         │   │
│  │  └─────────┘  └─────────┘          │   │
│  │       ↕ work stealing ↕              │   │
│  └─────────────────────────────────────┘   │
│                    ↑ wake                   │
│  ┌─────────────────────────────────────┐   │
│  │     Reactor (epoll/kqueue/IOCP)     │   │
│  │     timers, sockets, signals        │   │
│  └─────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

## Common Tokio Patterns

### Running Multiple Async Operations

```rust
use tokio::time::{sleep, Duration, Instant};

async fn fetch_user() -> String {
    sleep(Duration::from_millis(200)).await;
    "alice".to_string()
}

async fn fetch_orders() -> Vec<String> {
    sleep(Duration::from_millis(300)).await;
    vec!["order-1".to_string(), "order-2".to_string()]
}

#[tokio::main]
async fn main() {
    let start = Instant::now();

    // Sequential: ~500ms
    let user = fetch_user().await;
    let orders = fetch_orders().await;
    println!("Sequential: {user}, {orders:?} in {:?}", start.elapsed());

    let start = Instant::now();

    // Concurrent with join!: ~300ms
    let (user, orders) = tokio::join!(fetch_user(), fetch_orders());
    println!("Concurrent: {user}, {orders:?} in {:?}", start.elapsed());
}
```

### The Blocking Escape Hatch

Sometimes you need to call synchronous, blocking code from async context. Never block the executor — use `spawn_blocking`:

```rust
use tokio::task;

#[tokio::main]
async fn main() {
    // BAD: This blocks an executor thread
    // let hash = expensive_hash("password");

    // GOOD: Run on a dedicated blocking thread pool
    let hash = task::spawn_blocking(|| {
        expensive_hash("password")
    }).await.unwrap();

    println!("Hash: {hash}");
}

fn expensive_hash(input: &str) -> String {
    // Simulating CPU-intensive work
    std::thread::sleep(std::time::Duration::from_millis(100));
    format!("hashed-{input}")
}
```

`spawn_blocking` runs your closure on a separate thread pool designed for blocking work. The executor threads stay free to poll other tasks.

### Nested Runtimes (Don't)

This is a common mistake:

```rust
#[tokio::main]
async fn main() {
    // This PANICS: "Cannot start a runtime from within a runtime"
    // let rt = tokio::runtime::Runtime::new().unwrap();
    // rt.block_on(async { ... });

    // If you need a handle to the current runtime:
    let handle = tokio::runtime::Handle::current();

    // Use it from a blocking context:
    task::spawn_blocking(move || {
        // We're on a blocking thread, no runtime here
        // But we can enter the runtime with the handle:
        handle.block_on(async {
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
            println!("Back in async land from blocking thread");
        });
    }).await.unwrap();
}

use tokio::task;
```

## Runtime Shutdown Behavior

This trips people up. When the runtime shuts down, all spawned tasks are **cancelled**, not completed:

```rust
use tokio::time::{sleep, Duration};

#[tokio::main]
async fn main() {
    tokio::spawn(async {
        sleep(Duration::from_secs(10)).await;
        println!("This will never print!");
    });

    // main returns immediately, runtime shuts down,
    // spawned task is cancelled
    println!("Main done");
}
```

If you want to wait for spawned tasks, you need to hold onto their `JoinHandle`s:

```rust
use tokio::time::{sleep, Duration};

#[tokio::main]
async fn main() {
    let handle = tokio::spawn(async {
        sleep(Duration::from_millis(100)).await;
        println!("Task completed!");
        42
    });

    let result = handle.await.unwrap();
    println!("Got: {result}");
}
```

## Practical: A Simple Async TCP Echo Server

Let's put it all together with something real:

```rust
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let listener = TcpListener::bind("127.0.0.1:8080").await?;
    println!("Listening on 127.0.0.1:8080");

    loop {
        let (mut socket, addr) = listener.accept().await?;
        println!("New connection from {addr}");

        // Spawn a task for each connection
        tokio::spawn(async move {
            let mut buf = [0u8; 1024];

            loop {
                let n = match socket.read(&mut buf).await {
                    Ok(0) => {
                        println!("{addr} disconnected");
                        return;
                    }
                    Ok(n) => n,
                    Err(e) => {
                        eprintln!("Read error from {addr}: {e}");
                        return;
                    }
                };

                if let Err(e) = socket.write_all(&buf[..n]).await {
                    eprintln!("Write error to {addr}: {e}");
                    return;
                }
            }
        });
    }
}
```

This handles thousands of concurrent connections without thousands of threads. Each connection is a lightweight task (a state machine) scheduled by Tokio's executor. The reactor watches all the sockets and wakes tasks when data arrives.

Compare this to the thread-per-connection model:

```rust
// Thread-per-connection (for comparison — don't do this)
use std::io::{Read, Write};
use std::net::TcpListener;

fn main() -> std::io::Result<()> {
    let listener = TcpListener::bind("127.0.0.1:8080")?;

    for stream in listener.incoming() {
        let mut stream = stream?;
        std::thread::spawn(move || {
            let mut buf = [0u8; 1024];
            loop {
                let n = stream.read(&mut buf).unwrap_or(0);
                if n == 0 { break; }
                stream.write_all(&buf[..n]).unwrap();
            }
        });
    }
    Ok(())
}
```

Same functionality, but each thread costs ~8MB of stack space. At 10,000 connections, that's 80GB just for stacks. Tokio tasks? A few hundred bytes each.

## Configuration Tips for Production

```rust
fn main() {
    let rt = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(num_cpus::get())  // Match CPU cores
        .max_blocking_threads(512)         // Cap blocking pool
        .enable_all()
        .build()
        .unwrap();

    rt.block_on(async {
        server_main().await;
    });
}

async fn server_main() {
    // Your actual application logic
    println!("Server starting...");
    // ...
}
```

Key tuning knobs:
- `worker_threads`: defaults to CPU count, usually fine
- `max_blocking_threads`: defaults to 512, increase if you have many blocking calls
- Consider `current_thread` for sidecar processes or CLI tools

Tokio is the engine under the hood. Now that you know how to configure and run it, we can start building real concurrent programs. Next lesson: spawning tasks and working with `JoinHandle`s.
