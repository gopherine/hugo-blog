---
title: "Lesson 11: Timeouts, Deadlines, and Graceful Shutdown — Bounded operations"
author: Atharva Pandey
series: "Async Rust Masterclass"
lesson: 11
keywords: [Rust, rust, async, tokio, timeout, deadline, graceful shutdown, signals]
tags: [Rust tutorial, rust, async, tokio]
date: '2025-01-26T12:24:51.000Z'
---

Every production outage I've investigated boils down to one of two things: unbounded retries or missing timeouts. A function that "usually takes 50ms" eventually takes 30 seconds because the database is overloaded, and suddenly your entire service is frozen because every thread is waiting on that one slow call.

Timeouts aren't optional in production code. They're as important as error handling. And graceful shutdown — cleanly stopping your service when it's time to deploy — is what separates "my service runs in production" from "my service runs in production *well*."

## Basic Timeouts

```rust
use tokio::time::{timeout, Duration, sleep};

async fn slow_operation() -> String {
    sleep(Duration::from_secs(10)).await;
    "finally done".to_string()
}

#[tokio::main]
async fn main() {
    match timeout(Duration::from_secs(2), slow_operation()).await {
        Ok(result) => println!("Got: {result}"),
        Err(_) => println!("Timed out after 2 seconds"),
    }
}
```

`timeout` wraps any future and returns `Err(Elapsed)` if it doesn't complete in time. The inner future is **dropped** (cancelled) when the timeout fires — remember lesson 10 on cancellation safety.

## Deadline Pattern

Sometimes you want a single deadline for multiple operations, not individual timeouts:

```rust
use tokio::time::{timeout_at, Instant, Duration, sleep};

async fn step_one() -> String {
    sleep(Duration::from_millis(300)).await;
    "step1".to_string()
}

async fn step_two() -> String {
    sleep(Duration::from_millis(400)).await;
    "step2".to_string()
}

async fn step_three() -> String {
    sleep(Duration::from_millis(200)).await;
    "step3".to_string()
}

#[tokio::main]
async fn main() {
    // All three steps must complete within 800ms total
    let deadline = Instant::now() + Duration::from_millis(800);

    let result = async {
        let a = timeout_at(deadline, step_one()).await.map_err(|_| "step 1 timeout")?;
        let b = timeout_at(deadline, step_two()).await.map_err(|_| "step 2 timeout")?;
        let c = timeout_at(deadline, step_three()).await.map_err(|_| "step 3 timeout")?;
        Ok::<_, &str>((a, b, c))
    }.await;

    match result {
        Ok((a, b, c)) => println!("All done: {a}, {b}, {c}"),
        Err(step) => println!("Failed at: {step}"),
    }
    // Total: 900ms needed, 800ms budget → step 3 will fail
}
```

Using `timeout_at` with a shared deadline means if step one takes longer than expected, the remaining steps get less time. This is usually what you want — the caller cares about total latency, not per-step latency.

## Retry with Timeout

```rust
use tokio::time::{timeout, sleep, Duration, Instant};

async fn flaky_request() -> Result<String, String> {
    // Simulate a request that fails 70% of the time
    sleep(Duration::from_millis(50)).await;
    if rand_bool(0.7) {
        Err("connection refused".to_string())
    } else {
        Ok("success".to_string())
    }
}

// Simple deterministic "random" for demo
fn rand_bool(probability: f64) -> bool {
    use std::sync::atomic::{AtomicU64, Ordering};
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let n = COUNTER.fetch_add(1, Ordering::Relaxed);
    (n % 10) as f64 / 10.0 < probability
}

async fn retry_with_timeout(
    max_retries: u32,
    per_attempt_timeout: Duration,
    overall_timeout: Duration,
) -> Result<String, String> {
    let deadline = Instant::now() + overall_timeout;

    for attempt in 1..=max_retries {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err("overall timeout exceeded".to_string());
        }

        // Use the shorter of per-attempt timeout and remaining time
        let attempt_timeout = per_attempt_timeout.min(remaining);

        match timeout(attempt_timeout, flaky_request()).await {
            Ok(Ok(result)) => return Ok(result),
            Ok(Err(e)) => {
                println!("Attempt {attempt} failed: {e}");
                if attempt < max_retries {
                    let backoff = Duration::from_millis(100 * 2u64.pow(attempt - 1));
                    let wait = backoff.min(deadline.saturating_duration_since(Instant::now()));
                    sleep(wait).await;
                }
            }
            Err(_) => {
                println!("Attempt {attempt} timed out");
            }
        }
    }

    Err(format!("all {max_retries} attempts failed"))
}

#[tokio::main]
async fn main() {
    let result = retry_with_timeout(
        5,
        Duration::from_secs(1),
        Duration::from_secs(10),
    ).await;

    println!("Result: {result:?}");
}
```

## Graceful Shutdown

This is the big one. A well-behaved service needs to:

1. Catch the shutdown signal (SIGTERM, Ctrl+C)
2. Stop accepting new work
3. Wait for in-flight work to complete (with a timeout)
4. Clean up resources
5. Exit

```rust
use tokio::signal;
use tokio::sync::{broadcast, mpsc};
use tokio::time::{sleep, timeout, Duration};

async fn handle_request(id: u32, mut shutdown: broadcast::Receiver<()>) {
    let work_time = Duration::from_millis(100 * (id as u64 % 5 + 1));

    tokio::select! {
        _ = sleep(work_time) => {
            println!("Request {id} completed");
        }
        _ = shutdown.recv() => {
            println!("Request {id} cancelled by shutdown");
        }
    }
}

async fn server(
    mut request_rx: mpsc::Receiver<u32>,
    shutdown: broadcast::Sender<()>,
) {
    let mut tasks = tokio::task::JoinSet::new();

    while let Some(id) = request_rx.recv().await {
        let shutdown_rx = shutdown.subscribe();
        tasks.spawn(handle_request(id, shutdown_rx));
    }

    println!("No more requests, waiting for in-flight tasks...");

    // Wait for all in-flight tasks with a timeout
    match timeout(Duration::from_secs(5), async {
        while let Some(result) = tasks.join_next().await {
            if let Err(e) = result {
                eprintln!("Task error: {e}");
            }
        }
    }).await {
        Ok(()) => println!("All tasks completed gracefully"),
        Err(_) => {
            println!("Shutdown timeout — aborting remaining tasks");
            tasks.abort_all();
        }
    }
}

#[tokio::main]
async fn main() {
    let (shutdown_tx, _) = broadcast::channel::<()>(1);
    let (request_tx, request_rx) = mpsc::channel::<u32>(100);

    // Start the server
    let shutdown_for_server = shutdown_tx.clone();
    let server_handle = tokio::spawn(server(request_rx, shutdown_for_server));

    // Simulate incoming requests
    let request_producer = tokio::spawn({
        let tx = request_tx.clone();
        async move {
            for i in 0..20 {
                if tx.send(i).await.is_err() {
                    break;
                }
                sleep(Duration::from_millis(50)).await;
            }
        }
    });

    // Wait for shutdown signal
    tokio::select! {
        _ = signal::ctrl_c() => {
            println!("\nShutdown signal received!");
        }
        _ = request_producer => {
            println!("All requests sent");
        }
    }

    // Initiate shutdown
    println!("Initiating graceful shutdown...");

    // 1. Stop accepting new requests
    drop(request_tx);

    // 2. Signal in-flight handlers to stop
    let _ = shutdown_tx.send(());

    // 3. Wait for server to finish
    server_handle.await.unwrap();

    println!("Server shut down cleanly");
}
```

## The CancellationToken Pattern

Tokio provides `CancellationToken` in `tokio-util`, which is purpose-built for shutdown coordination:

```rust
use tokio::time::{sleep, Duration};
use tokio_util::sync::CancellationToken;

async fn background_worker(name: &str, token: CancellationToken) {
    loop {
        tokio::select! {
            _ = token.cancelled() => {
                println!("[{name}] Received cancellation, cleaning up...");
                // Do cleanup
                sleep(Duration::from_millis(50)).await;
                println!("[{name}] Cleanup complete");
                return;
            }
            _ = sleep(Duration::from_millis(200)) => {
                println!("[{name}] Working...");
            }
        }
    }
}

#[tokio::main]
async fn main() {
    let token = CancellationToken::new();

    // Child tokens — cancelling the parent cancels all children
    let worker1_token = token.child_token();
    let worker2_token = token.child_token();

    let h1 = tokio::spawn(background_worker("worker-1", worker1_token));
    let h2 = tokio::spawn(background_worker("worker-2", worker2_token));

    // Let workers run for a bit
    sleep(Duration::from_secs(1)).await;

    // Cancel everything
    println!("Cancelling all workers...");
    token.cancel();

    h1.await.unwrap();
    h2.await.unwrap();
    println!("All workers stopped");
}
```

Add `tokio-util` to your dependencies:

```toml
[dependencies]
tokio-util = "0.7"
```

`CancellationToken` advantages over broadcast channels:
- No buffer size concerns
- Child tokens for hierarchical cancellation
- `.cancelled()` is cancellation-safe
- Can be checked synchronously with `.is_cancelled()`

## Production Shutdown Pattern

Here's the pattern I use in every production service:

```rust
use tokio::signal;
use tokio::time::{sleep, timeout, Duration};
use tokio_util::sync::CancellationToken;

struct App {
    shutdown: CancellationToken,
}

impl App {
    fn new() -> Self {
        App {
            shutdown: CancellationToken::new(),
        }
    }

    async fn run(&self) {
        let mut tasks = tokio::task::JoinSet::new();

        // Start background workers
        let token = self.shutdown.child_token();
        tasks.spawn(async move {
            Self::http_server(token).await;
        });

        let token = self.shutdown.child_token();
        tasks.spawn(async move {
            Self::background_processor(token).await;
        });

        let token = self.shutdown.child_token();
        tasks.spawn(async move {
            Self::metrics_reporter(token).await;
        });

        // Wait for shutdown signal
        signal::ctrl_c().await.unwrap();
        println!("Shutdown signal received");

        // Cancel all workers
        self.shutdown.cancel();

        // Wait for graceful completion
        let drain = timeout(Duration::from_secs(30), async {
            while let Some(result) = tasks.join_next().await {
                match result {
                    Ok(()) => {}
                    Err(e) => eprintln!("Task error during shutdown: {e}"),
                }
            }
        });

        if drain.await.is_err() {
            eprintln!("Shutdown timed out after 30s — force killing");
            tasks.abort_all();
        }

        println!("Shutdown complete");
    }

    async fn http_server(shutdown: CancellationToken) {
        println!("HTTP server starting...");
        loop {
            tokio::select! {
                _ = shutdown.cancelled() => {
                    println!("HTTP server: draining connections...");
                    sleep(Duration::from_millis(100)).await;
                    println!("HTTP server: stopped");
                    return;
                }
                _ = sleep(Duration::from_millis(500)) => {
                    println!("HTTP server: handled a request");
                }
            }
        }
    }

    async fn background_processor(shutdown: CancellationToken) {
        println!("Background processor starting...");
        loop {
            tokio::select! {
                _ = shutdown.cancelled() => {
                    println!("Processor: finishing current batch...");
                    sleep(Duration::from_millis(200)).await;
                    println!("Processor: stopped");
                    return;
                }
                _ = sleep(Duration::from_millis(300)) => {
                    println!("Processor: processed a batch");
                }
            }
        }
    }

    async fn metrics_reporter(shutdown: CancellationToken) {
        println!("Metrics reporter starting...");
        loop {
            tokio::select! {
                _ = shutdown.cancelled() => {
                    println!("Metrics: final flush...");
                    println!("Metrics: stopped");
                    return;
                }
                _ = sleep(Duration::from_secs(1)) => {
                    println!("Metrics: reported");
                }
            }
        }
    }
}

#[tokio::main]
async fn main() {
    let app = App::new();
    app.run().await;
}
```

## Timeout Gotchas

A few things that catch people:

**1. Timeout doesn't mean the inner future stops immediately.** It means the inner future is *dropped*. If Drop does cleanup work, that still happens synchronously.

**2. Nested timeouts take the shortest.** `timeout(5s, timeout(10s, work))` effectively has a 5-second timeout.

**3. Zero-duration timeouts still yield.** `timeout(Duration::ZERO, work)` doesn't run synchronously — it schedules the timeout, yields, and the timeout might fire before the work gets polled.

**4. `sleep` is not a timer.** `sleep(Duration::from_secs(5))` creates a new timer every time. In a loop, use `interval` instead:

```rust
use tokio::time::{interval, sleep, Duration};

#[tokio::main]
async fn main() {
    // BAD: drift accumulates
    // loop {
    //     do_work().await;
    //     sleep(Duration::from_secs(1)).await; // 1s AFTER work completes
    // }

    // GOOD: consistent intervals
    let mut ticker = interval(Duration::from_secs(1));
    for _ in 0..5 {
        ticker.tick().await; // Every 1s from the START of each period
        println!("tick at {:?}", std::time::Instant::now());
    }
}
```

Every network call, every database query, every external API request in your production code should have a timeout. No exceptions. The default timeout should be aggressive — 5 seconds is usually plenty. If something legitimately needs 30 seconds, make that explicit in the code with a comment explaining why.

Next lesson: async I/O — files, sockets, and DNS.
