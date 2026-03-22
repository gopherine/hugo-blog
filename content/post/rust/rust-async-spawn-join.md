---
title: "Lesson 4: Spawning Tasks and JoinHandles — Concurrent work units"
author: Atharva Pandey
series: "Async Rust Masterclass"
lesson: 4
keywords: [Rust, rust, async, tokio, spawn, JoinHandle, join, concurrency, tasks]
tags: [Rust tutorial, rust, async, tokio]
date: '2025-01-12T08:44:19.000Z'
---

I remember the exact moment async Rust "clicked" for me. I was building an API aggregator that needed to call five different services. My first version awaited them sequentially — 2 seconds total. Then I spawned them as concurrent tasks — 400ms. Same work, 5x faster, and I didn't need to think about threads, locks, or shared state.

But spawning tasks isn't free, and `JoinHandle` has some sharp edges that nobody warned me about. This lesson covers the patterns you'll use every day.

## tokio::spawn — The Basics

`tokio::spawn` takes a future and runs it as an independent task on the Tokio runtime:

```rust
use tokio::time::{sleep, Duration};

#[tokio::main]
async fn main() {
    let handle = tokio::spawn(async {
        sleep(Duration::from_millis(100)).await;
        42
    });

    // The task runs concurrently with whatever else we do here
    println!("Task is running in the background...");

    // .await the handle to get the result
    let result = handle.await.unwrap();
    println!("Got: {result}");
}
```

A few critical things to understand:

1. **`spawn` returns immediately.** The future starts running (gets scheduled) right away.
2. **The return type is `JoinHandle<T>`.** Awaiting it gives you `Result<T, JoinError>`.
3. **`JoinError` means the task panicked or was cancelled.** It does *not* mean your async code returned an error — that's inside the `T`.

## The 'static Bound

This is where people hit their first wall:

```rust
#[tokio::main]
async fn main() {
    let data = String::from("hello");

    // This WON'T compile:
    // tokio::spawn(async {
    //     println!("{data}");
    // });

    // Because `data` is borrowed, but spawned tasks must be 'static.
    // The task might outlive the current scope.

    // Solution 1: Move ownership
    tokio::spawn(async move {
        println!("{data}");
    }).await.unwrap();

    // Solution 2: Clone first
    let data2 = String::from("world");
    let data_clone = data2.clone();
    tokio::spawn(async move {
        println!("{data_clone}");
    }).await.unwrap();

    // data2 is still available here
    println!("{data2}");
}
```

The `'static` bound exists because spawned tasks run independently — they might keep running after the function that spawned them returns. The compiler can't guarantee your references will still be valid.

This is one of those things that feels annoying but prevents real bugs. In Go, you'd just close over a reference in a goroutine and hope for the best. Rust makes you think about ownership upfront.

## JoinHandle: More Than Just .await

`JoinHandle` isn't just for getting results. It's also your cancellation mechanism:

```rust
use tokio::time::{sleep, Duration};

#[tokio::main]
async fn main() {
    let handle = tokio::spawn(async {
        loop {
            println!("Working...");
            sleep(Duration::from_millis(200)).await;
        }
    });

    // Let it run for a bit
    sleep(Duration::from_secs(1)).await;

    // Cancel the task by aborting it
    handle.abort();

    // Awaiting an aborted task gives JoinError
    match handle.await {
        Ok(_) => println!("Task completed normally"),
        Err(e) if e.is_cancelled() => println!("Task was cancelled"),
        Err(e) => println!("Task panicked: {e}"),
    }
}
```

**Dropping** a `JoinHandle` does *not* cancel the task. The task keeps running in the background — you just lose the ability to await its result or cancel it. This is a common source of "fire and forget" tasks that people didn't intend.

```rust
use tokio::time::{sleep, Duration};

#[tokio::main]
async fn main() {
    // This task runs to completion even though we drop the handle
    let _ = tokio::spawn(async {
        sleep(Duration::from_millis(100)).await;
        println!("I still ran!");
    });

    sleep(Duration::from_millis(200)).await;
}
```

## tokio::join! — Concurrent Awaiting

`join!` runs multiple futures concurrently and waits for all of them:

```rust
use tokio::time::{sleep, Duration, Instant};

async fn fetch_user(id: u32) -> String {
    sleep(Duration::from_millis(200)).await;
    format!("user-{id}")
}

async fn fetch_profile(id: u32) -> String {
    sleep(Duration::from_millis(300)).await;
    format!("profile-{id}")
}

async fn fetch_settings(id: u32) -> String {
    sleep(Duration::from_millis(150)).await;
    format!("settings-{id}")
}

#[tokio::main]
async fn main() {
    let start = Instant::now();

    let (user, profile, settings) = tokio::join!(
        fetch_user(1),
        fetch_profile(1),
        fetch_settings(1),
    );

    println!("{user}, {profile}, {settings}");
    println!("Took {:?}", start.elapsed()); // ~300ms, not 650ms
}
```

`join!` vs `spawn`: what's the difference?

- **`join!`** runs futures concurrently *on the current task*. They share the same task context. No `'static` bound required.
- **`spawn`** creates a new task that runs independently, possibly on a different thread. Requires `'static + Send`.

Use `join!` when you want concurrent I/O within a single logical operation. Use `spawn` when you want truly independent work (like handling separate connections).

## tokio::try_join! — Short-Circuit on Error

When all your futures return `Result`, `try_join!` stops as soon as one fails:

```rust
use tokio::time::{sleep, Duration};

async fn fetch_required_data() -> Result<String, String> {
    sleep(Duration::from_millis(200)).await;
    Ok("data".to_string())
}

async fn fetch_with_error() -> Result<String, String> {
    sleep(Duration::from_millis(100)).await;
    Err("service unavailable".to_string())
}

#[tokio::main]
async fn main() {
    let result = tokio::try_join!(
        fetch_required_data(),
        fetch_with_error(),
    );

    match result {
        Ok((data, other)) => println!("Got: {data}, {other}"),
        Err(e) => println!("Failed fast: {e}"),
        // Prints "Failed fast: service unavailable" after ~100ms
        // The other future is cancelled
    }
}
```

Important: `try_join!` **cancels** the remaining futures when one fails. This is usually what you want — why keep fetching data if one required piece already failed?

## JoinSet — Dynamic Task Management

When you don't know the number of tasks at compile time, `JoinSet` is your friend:

```rust
use tokio::task::JoinSet;
use tokio::time::{sleep, Duration};

async fn process_item(id: u32) -> String {
    sleep(Duration::from_millis(100 * id as u64)).await;
    format!("processed-{id}")
}

#[tokio::main]
async fn main() {
    let items = vec![1, 2, 3, 4, 5];
    let mut set = JoinSet::new();

    for id in items {
        set.spawn(process_item(id));
    }

    // Collect results as they complete (not in order!)
    while let Some(result) = set.join_next().await {
        match result {
            Ok(val) => println!("Completed: {val}"),
            Err(e) => eprintln!("Task failed: {e}"),
        }
    }

    println!("All tasks done");
}
```

`JoinSet` gives you:
- Dynamic task spawning
- Results as they arrive (not in spawn order)
- Automatic cleanup when the `JoinSet` is dropped (all tasks are aborted)

That last point is huge — it means `JoinSet` is a structured concurrency primitive. If you drop it, all tasks are cancelled. No leaked background work.

## Spawn Patterns for Real Applications

### Pattern: Fan-Out / Fan-In

```rust
use tokio::task::JoinSet;

async fn check_endpoint(url: String) -> (String, bool) {
    let healthy = tokio::time::sleep(tokio::time::Duration::from_millis(50))
        .await;
    // In reality: reqwest::get(&url).await.is_ok()
    (url, true)
}

#[tokio::main]
async fn main() {
    let endpoints = vec![
        "https://api1.example.com/health",
        "https://api2.example.com/health",
        "https://api3.example.com/health",
    ];

    let mut set = JoinSet::new();
    for url in endpoints {
        set.spawn(check_endpoint(url.to_string()));
    }

    let mut results = Vec::new();
    while let Some(Ok(result)) = set.join_next().await {
        results.push(result);
    }

    for (url, healthy) in &results {
        println!("{url}: {}", if *healthy { "UP" } else { "DOWN" });
    }
}
```

### Pattern: Bounded Concurrency

Don't spawn 10,000 tasks that all hit the same database. Use a semaphore (we'll cover this in depth in lesson 9):

```rust
use std::sync::Arc;
use tokio::sync::Semaphore;
use tokio::task::JoinSet;
use tokio::time::{sleep, Duration};

async fn process(id: u32) -> u32 {
    sleep(Duration::from_millis(100)).await;
    id * 2
}

#[tokio::main]
async fn main() {
    let semaphore = Arc::new(Semaphore::new(5)); // Max 5 concurrent
    let mut set = JoinSet::new();

    for i in 0..20 {
        let permit = semaphore.clone().acquire_owned().await.unwrap();
        set.spawn(async move {
            let result = process(i).await;
            drop(permit); // Release when done
            result
        });
    }

    while let Some(Ok(result)) = set.join_next().await {
        println!("Result: {result}");
    }
}
```

### Pattern: Spawn with Shared State

```rust
use std::sync::Arc;
use tokio::sync::Mutex;
use tokio::task::JoinSet;
use tokio::time::{sleep, Duration};

#[tokio::main]
async fn main() {
    let counter = Arc::new(Mutex::new(0u64));
    let mut set = JoinSet::new();

    for _ in 0..10 {
        let counter = counter.clone();
        set.spawn(async move {
            sleep(Duration::from_millis(50)).await;
            let mut lock = counter.lock().await;
            *lock += 1;
        });
    }

    while let Some(result) = set.join_next().await {
        result.unwrap();
    }

    println!("Counter: {}", *counter.lock().await); // 10
}
```

## Task Panics

If a spawned task panics, it doesn't crash the program. The panic is captured and returned via the `JoinHandle`:

```rust
#[tokio::main]
async fn main() {
    let handle = tokio::spawn(async {
        panic!("oops!");
    });

    match handle.await {
        Ok(_) => println!("Success"),
        Err(e) if e.is_panic() => {
            println!("Task panicked: {:?}", e.into_panic());
        }
        Err(e) => println!("Task error: {e}"),
    }

    println!("Main continues running fine");
}
```

This is different from threads, where an unjoined thread's panic is silently swallowed by default. Tokio tasks always give you a chance to handle failures.

## When to Spawn vs When to Join

My rule of thumb:

- **Use `join!`** when the futures are part of the same logical operation and you want the results together. Like fetching a user and their profile for a single API response.
- **Use `spawn`** when the work is independent. Like handling different HTTP requests, or producing items on a background queue.
- **Use `JoinSet`** when you have a dynamic number of independent tasks.

Don't over-spawn. Every spawned task has overhead (heap allocation, scheduling, the `'static` bound forces cloning). If you're spawning a task just to immediately `.await` it, you probably wanted `join!` instead.

Next lesson, we'll look at `tokio::select!` — the tool for racing futures against each other.
