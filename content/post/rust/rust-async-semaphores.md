---
title: "Lesson 9: Semaphores and Rate Limiting — Bounded async concurrency"
author: Atharva Pandey
series: "Async Rust Masterclass"
lesson: 9
keywords: [Rust, rust, async, tokio, semaphore, rate limiting, concurrency control, bounded]
tags: [Rust tutorial, rust, async, tokio]
date: '2025-01-22T15:08:36.000Z'
---

I once crashed a third-party API by spawning 10,000 concurrent requests from an async Rust service. The code was correct — every request completed (eventually). But the API's rate limiter kicked in after 50 concurrent connections, and we got IP-banned for two hours.

The fix was a single type: `tokio::sync::Semaphore`. Five lines of code turned "as fast as possible" into "at most N at a time."

## What's a Semaphore?

A semaphore is a counter with a maximum value. You acquire a permit before doing work, and release it when you're done. If all permits are taken, acquiring blocks (yields) until one becomes available.

Think of it as a parking lot with N spaces. Cars (tasks) wait at the entrance until a space opens up.

```rust
use std::sync::Arc;
use tokio::sync::Semaphore;
use tokio::time::{sleep, Duration, Instant};

#[tokio::main]
async fn main() {
    let semaphore = Arc::new(Semaphore::new(3)); // 3 concurrent tasks max
    let start = Instant::now();

    let mut handles = vec![];
    for i in 0..9 {
        let sem = semaphore.clone();
        handles.push(tokio::spawn(async move {
            // Wait for a permit
            let _permit = sem.acquire().await.unwrap();
            println!("[{:.1}s] Task {i} started",
                start.elapsed().as_secs_f64());

            // Simulate work
            sleep(Duration::from_millis(200)).await;

            println!("[{:.1}s] Task {i} done",
                start.elapsed().as_secs_f64());
            // permit is dropped here, releasing the slot
        }));
    }

    for h in handles {
        h.await.unwrap();
    }
}
```

You'll see tasks running in batches of 3. The first three start immediately, the next three start when the first three finish, and so on.

## OwnedSemaphorePermit — When You Need to Move

`acquire()` returns a permit that borrows the semaphore. If you need to move the permit into a spawned task, use `acquire_owned()`:

```rust
use std::sync::Arc;
use tokio::sync::Semaphore;
use tokio::time::{sleep, Duration};

#[tokio::main]
async fn main() {
    let sem = Arc::new(Semaphore::new(5));

    let mut handles = vec![];
    for i in 0..20 {
        // acquire_owned takes Arc<Semaphore> and gives you an owned permit
        let permit = sem.clone().acquire_owned().await.unwrap();
        handles.push(tokio::spawn(async move {
            sleep(Duration::from_millis(100)).await;
            println!("Task {i} done");
            drop(permit); // Explicitly release (or let it drop naturally)
        }));
    }

    for h in handles {
        h.await.unwrap();
    }
}
```

## try_acquire — Non-Blocking Check

Sometimes you want to try without waiting:

```rust
use tokio::sync::Semaphore;

#[tokio::main]
async fn main() {
    let sem = Semaphore::new(2);

    let p1 = sem.try_acquire().unwrap();
    let p2 = sem.try_acquire().unwrap();

    // No permits left
    match sem.try_acquire() {
        Ok(_) => println!("Got permit"),
        Err(_) => println!("No permits available — would have to wait"),
    }

    drop(p1);
    // Now one is available again
    let _p3 = sem.try_acquire().unwrap();
    println!("Got permit after release");

    drop(p2);
    drop(_p3);
}
```

## Pattern: Connection Pool Limiter

```rust
use std::sync::Arc;
use tokio::sync::Semaphore;
use tokio::time::{sleep, Duration};

struct DbPool {
    semaphore: Arc<Semaphore>,
    connection_string: String,
}

impl DbPool {
    fn new(max_connections: usize, connection_string: &str) -> Self {
        DbPool {
            semaphore: Arc::new(Semaphore::new(max_connections)),
            connection_string: connection_string.to_string(),
        }
    }

    async fn execute(&self, query: &str) -> Result<String, String> {
        // Wait for a connection slot
        let _permit = self.semaphore.acquire().await
            .map_err(|_| "Pool closed".to_string())?;

        // Simulate query execution
        sleep(Duration::from_millis(50)).await;
        Ok(format!("Result of: {query}"))

        // permit dropped → connection returned to pool
    }

    fn available_connections(&self) -> usize {
        self.semaphore.available_permits()
    }
}

#[tokio::main]
async fn main() {
    let pool = Arc::new(DbPool::new(5, "postgres://localhost/mydb"));

    println!("Available connections: {}", pool.available_connections());

    let mut handles = vec![];
    for i in 0..20 {
        let pool = pool.clone();
        handles.push(tokio::spawn(async move {
            let result = pool.execute(&format!("SELECT {i}")).await.unwrap();
            println!("{result}");
        }));
    }

    for h in handles {
        h.await.unwrap();
    }

    println!("Available connections: {}", pool.available_connections());
}
```

## Pattern: Rate Limiter (Token Bucket)

A semaphore can implement a simple token bucket rate limiter:

```rust
use std::sync::Arc;
use tokio::sync::Semaphore;
use tokio::time::{interval, Duration, Instant};

struct RateLimiter {
    semaphore: Arc<Semaphore>,
}

impl RateLimiter {
    fn new(permits_per_second: u32) -> Self {
        let sem = Arc::new(Semaphore::new(permits_per_second as usize));

        // Background task that replenishes permits
        let refill_sem = sem.clone();
        let rate = permits_per_second as usize;
        tokio::spawn(async move {
            let mut tick = interval(Duration::from_secs(1));
            loop {
                tick.tick().await;
                let current = refill_sem.available_permits();
                let to_add = rate.saturating_sub(current);
                if to_add > 0 {
                    refill_sem.add_permits(to_add);
                }
            }
        });

        RateLimiter { semaphore: sem }
    }

    async fn acquire(&self) {
        self.semaphore.acquire().await.unwrap().forget();
        // .forget() consumes the permit without returning it on drop
        // The refill task adds them back periodically
    }
}

#[tokio::main]
async fn main() {
    let limiter = RateLimiter::new(5); // 5 requests per second
    let start = Instant::now();

    for i in 0..15 {
        limiter.acquire().await;
        println!("[{:.1}s] Request {i} allowed",
            start.elapsed().as_secs_f64());
    }
}
```

## Pattern: Concurrent Web Scraper with Limits

```rust
use std::sync::Arc;
use tokio::sync::Semaphore;
use tokio::time::{sleep, Duration, Instant};

struct Scraper {
    max_concurrent: Arc<Semaphore>,
    max_per_domain: Arc<Semaphore>,
}

impl Scraper {
    fn new(max_concurrent: usize, max_per_domain: usize) -> Self {
        Scraper {
            max_concurrent: Arc::new(Semaphore::new(max_concurrent)),
            max_per_domain: Arc::new(Semaphore::new(max_per_domain)),
        }
    }

    async fn fetch(&self, url: &str) -> Result<String, String> {
        // Global concurrency limit
        let _global = self.max_concurrent.acquire().await
            .map_err(|_| "Closed".to_string())?;

        // Per-domain limit (simplified — in reality you'd have per-domain semaphores)
        let _domain = self.max_per_domain.acquire().await
            .map_err(|_| "Closed".to_string())?;

        // Simulate HTTP request
        sleep(Duration::from_millis(100)).await;
        Ok(format!("Content of {url}"))
    }
}

#[tokio::main]
async fn main() {
    let scraper = Arc::new(Scraper::new(10, 3));
    let start = Instant::now();

    let urls: Vec<String> = (0..30)
        .map(|i| format!("https://example.com/page/{i}"))
        .collect();

    let mut handles = vec![];
    for url in urls {
        let scraper = scraper.clone();
        handles.push(tokio::spawn(async move {
            match scraper.fetch(&url).await {
                Ok(content) => println!("[{:.1}s] Fetched: {url}",
                    Instant::now().duration_since(start).as_secs_f64()),
                Err(e) => eprintln!("Error fetching {url}: {e}"),
            }
        }));
    }

    for h in handles {
        h.await.unwrap();
    }

    println!("All done in {:.1}s", start.elapsed().as_secs_f64());
}
```

## Pattern: Batch Processing with Semaphore

Process items in parallel, but limit concurrency:

```rust
use std::sync::Arc;
use tokio::sync::Semaphore;
use tokio::task::JoinSet;
use tokio::time::{sleep, Duration};

async fn process_record(id: u32) -> Result<String, String> {
    sleep(Duration::from_millis(50)).await;
    if id % 7 == 0 {
        Err(format!("Record {id} failed"))
    } else {
        Ok(format!("Processed record {id}"))
    }
}

#[tokio::main]
async fn main() {
    let records: Vec<u32> = (1..=50).collect();
    let semaphore = Arc::new(Semaphore::new(10));
    let mut set = JoinSet::new();

    for id in records {
        let permit = semaphore.clone().acquire_owned().await.unwrap();
        set.spawn(async move {
            let result = process_record(id).await;
            drop(permit);
            (id, result)
        });
    }

    let mut successes = 0;
    let mut failures = 0;

    while let Some(Ok((id, result))) = set.join_next().await {
        match result {
            Ok(msg) => {
                successes += 1;
            }
            Err(msg) => {
                failures += 1;
                eprintln!("{msg}");
            }
        }
    }

    println!("Done: {successes} succeeded, {failures} failed");
}
```

## Semaphore vs Bounded Channel

Both limit concurrency, but differently:

**Semaphore:** "At most N things happening at the same time." The tasks can do anything — they just need a permit.

**Bounded channel:** "At most N items waiting to be processed." The channel introduces a producer-consumer relationship.

Use a semaphore when tasks are independent but you want to limit how many run in parallel. Use a bounded channel when you have a clear producer/consumer split.

## Closing a Semaphore

You can close a semaphore to prevent new permits from being acquired:

```rust
use tokio::sync::Semaphore;
use std::sync::Arc;

#[tokio::main]
async fn main() {
    let sem = Arc::new(Semaphore::new(5));

    // Close it — all pending and future acquire() calls will fail
    sem.close();

    match sem.acquire().await {
        Ok(_) => println!("Got permit"),
        Err(_) => println!("Semaphore closed!"),
    }
}
```

This is useful for graceful shutdown — close the semaphore so no new work starts, then wait for in-flight permits to be released.

Semaphores are one of those tools that seem simple but solve a huge class of problems. Rate limiting, connection pooling, batch processing, resource management — it's all just "at most N at a time." Next lesson tackles something trickier: cancellation safety and why `select!` can silently lose your data.
