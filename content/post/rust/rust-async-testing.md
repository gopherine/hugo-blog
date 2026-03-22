---
title: "Lesson 19: Testing Async Code — Mocking time and I/O"
author: Atharva Pandey
series: "Async Rust Masterclass"
lesson: 19
keywords: [Rust, rust, async, tokio, testing, mock, time, tokio-test, async test]
tags: [Rust tutorial, rust, async, tokio]
date: '2025-02-10T11:28:56.000Z'
---

Async code is harder to test than sync code. Not because the logic is more complex, but because time, I/O, and concurrency introduce non-determinism. A test that passes 99 times and fails on the 100th is worse than a test that always fails — at least the always-failing test tells you something.

I've developed a set of patterns for testing async Rust that eliminate flakiness. The key insight: mock the things that make tests non-deterministic (time, network, randomness) and let everything else run for real.

## The Basics: #[tokio::test]

```rust
#[tokio::test]
async fn basic_async_test() {
    let result = async_add(2, 3).await;
    assert_eq!(result, 5);
}

async fn async_add(a: i32, b: i32) -> i32 {
    a + b
}
```

`#[tokio::test]` sets up a Tokio runtime for your test. By default, it uses the current-thread runtime, which is deterministic and fast.

### Multi-threaded tests

```rust
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn multi_threaded_test() {
    let handle = tokio::spawn(async { 42 });
    assert_eq!(handle.await.unwrap(), 42);
}
```

Use multi-threaded tests when you specifically need to test concurrent behavior across threads.

## Testing with Timeouts

Never let a test hang forever:

```rust
use tokio::time::{timeout, Duration};

#[tokio::test]
async fn test_with_timeout() {
    let result = timeout(Duration::from_secs(5), async {
        // Your test logic
        some_async_operation().await
    }).await;

    assert!(result.is_ok(), "Test timed out");
    assert_eq!(result.unwrap(), 42);
}

async fn some_async_operation() -> i32 {
    tokio::time::sleep(Duration::from_millis(100)).await;
    42
}
```

## Mocking Time with tokio::time::pause

This is the game-changer. `tokio::time::pause()` stops real time and lets you advance it manually:

```rust
use tokio::time::{sleep, pause, advance, Duration, Instant};

#[tokio::test]
async fn test_time_dependent_logic() {
    pause(); // Freeze time!

    let start = Instant::now();

    // This doesn't actually wait 10 seconds
    sleep(Duration::from_secs(10)).await;

    let elapsed = start.elapsed();
    // In paused time, the sleep completes "instantly"
    // but elapsed reports the virtual time that passed
    assert!(elapsed >= Duration::from_secs(10));
}

#[tokio::test]
async fn test_retry_backoff() {
    pause();

    let start = Instant::now();
    let result = retry_with_backoff(|| async {
        Err::<(), _>("still failing")
    }, 3).await;

    assert!(result.is_err());
    // Backoff: 100ms + 200ms + 400ms = 700ms virtual time
    assert!(start.elapsed() >= Duration::from_millis(700));
    // But the test ran in microseconds of real time!
}

async fn retry_with_backoff<F, Fut, T, E>(
    f: F,
    max_retries: u32,
) -> Result<T, E>
where
    F: Fn() -> Fut,
    Fut: std::future::Future<Output = Result<T, E>>,
{
    let mut delay = Duration::from_millis(100);
    for _ in 0..max_retries {
        match f().await {
            Ok(val) => return Ok(val),
            Err(e) => {
                sleep(delay).await;
                delay *= 2;
                if delay > Duration::from_secs(30) {
                    return Err(e);
                }
            }
        }
    }
    f().await
}
```

With paused time, a test that involves 30 seconds of backoff runs in milliseconds. No more `#[ignore]` on slow tests.

## Testing Channels and Message Passing

```rust
use tokio::sync::mpsc;
use tokio::time::{timeout, Duration};

async fn worker(mut rx: mpsc::Receiver<String>) -> Vec<String> {
    let mut results = Vec::new();
    while let Some(msg) = rx.recv().await {
        results.push(format!("processed: {msg}"));
    }
    results
}

#[tokio::test]
async fn test_worker_processes_all_messages() {
    let (tx, rx) = mpsc::channel(10);

    let handle = tokio::spawn(worker(rx));

    tx.send("hello".into()).await.unwrap();
    tx.send("world".into()).await.unwrap();
    drop(tx); // Close the channel

    let results = timeout(Duration::from_secs(1), handle)
        .await
        .expect("timed out")
        .expect("task panicked");

    assert_eq!(results, vec!["processed: hello", "processed: world"]);
}

#[tokio::test]
async fn test_worker_handles_empty_channel() {
    let (tx, rx) = mpsc::channel::<String>(10);
    drop(tx); // Immediately close

    let handle = tokio::spawn(worker(rx));
    let results = handle.await.unwrap();

    assert!(results.is_empty());
}
```

## Trait-Based Mocking

The most reliable pattern for testing async code: define traits for external dependencies and provide mock implementations:

```rust
use async_trait::async_trait;
use std::collections::HashMap;
use std::sync::Mutex;

// The trait
#[async_trait]
trait UserRepository: Send + Sync {
    async fn find_by_id(&self, id: u64) -> Result<Option<User>, DbError>;
    async fn save(&self, user: &User) -> Result<(), DbError>;
}

#[derive(Debug, Clone, PartialEq)]
struct User {
    id: u64,
    name: String,
    email: String,
}

#[derive(Debug)]
struct DbError(String);

// The real implementation (for production)
struct PostgresUserRepo {
    // pool: sqlx::PgPool,
}

// The mock implementation (for tests)
struct MockUserRepo {
    users: Mutex<HashMap<u64, User>>,
    find_error: Mutex<Option<String>>,
}

impl MockUserRepo {
    fn new() -> Self {
        MockUserRepo {
            users: Mutex::new(HashMap::new()),
            find_error: Mutex::new(None),
        }
    }

    fn with_user(self, user: User) -> Self {
        self.users.lock().unwrap().insert(user.id, user);
        self
    }

    fn with_find_error(self, error: &str) -> Self {
        *self.find_error.lock().unwrap() = Some(error.to_string());
        self
    }
}

#[async_trait]
impl UserRepository for MockUserRepo {
    async fn find_by_id(&self, id: u64) -> Result<Option<User>, DbError> {
        if let Some(err) = self.find_error.lock().unwrap().as_ref() {
            return Err(DbError(err.clone()));
        }
        Ok(self.users.lock().unwrap().get(&id).cloned())
    }

    async fn save(&self, user: &User) -> Result<(), DbError> {
        self.users.lock().unwrap().insert(user.id, user.clone());
        Ok(())
    }
}

// The service under test
struct UserService<R: UserRepository> {
    repo: R,
}

impl<R: UserRepository> UserService<R> {
    async fn get_user(&self, id: u64) -> Result<User, String> {
        self.repo
            .find_by_id(id)
            .await
            .map_err(|e| format!("DB error: {:?}", e))?
            .ok_or_else(|| format!("User {id} not found"))
    }

    async fn update_email(&self, id: u64, new_email: &str) -> Result<(), String> {
        let mut user = self.get_user(id).await?;
        user.email = new_email.to_string();
        self.repo.save(&user).await.map_err(|e| format!("{:?}", e))
    }
}

#[tokio::test]
async fn test_get_existing_user() {
    let repo = MockUserRepo::new().with_user(User {
        id: 1,
        name: "Alice".into(),
        email: "alice@test.com".into(),
    });

    let service = UserService { repo };
    let user = service.get_user(1).await.unwrap();

    assert_eq!(user.name, "Alice");
    assert_eq!(user.email, "alice@test.com");
}

#[tokio::test]
async fn test_get_nonexistent_user() {
    let repo = MockUserRepo::new();
    let service = UserService { repo };

    let err = service.get_user(999).await.unwrap_err();
    assert!(err.contains("not found"));
}

#[tokio::test]
async fn test_get_user_db_error() {
    let repo = MockUserRepo::new()
        .with_find_error("connection refused");

    let service = UserService { repo };

    let err = service.get_user(1).await.unwrap_err();
    assert!(err.contains("DB error"));
}

#[tokio::test]
async fn test_update_email() {
    let repo = MockUserRepo::new().with_user(User {
        id: 1,
        name: "Alice".into(),
        email: "old@test.com".into(),
    });

    let service = UserService { repo };
    service.update_email(1, "new@test.com").await.unwrap();

    let user = service.get_user(1).await.unwrap();
    assert_eq!(user.email, "new@test.com");
}
```

## Testing Concurrent Behavior

```rust
use std::sync::Arc;
use std::sync::atomic::{AtomicU32, Ordering};
use tokio::sync::Semaphore;
use tokio::time::{sleep, Duration};

struct RateLimitedClient {
    semaphore: Arc<Semaphore>,
    call_count: Arc<AtomicU32>,
}

impl RateLimitedClient {
    fn new(max_concurrent: usize) -> Self {
        RateLimitedClient {
            semaphore: Arc::new(Semaphore::new(max_concurrent)),
            call_count: Arc::new(AtomicU32::new(0)),
        }
    }

    async fn request(&self) -> String {
        let _permit = self.semaphore.acquire().await.unwrap();
        self.call_count.fetch_add(1, Ordering::SeqCst);
        sleep(Duration::from_millis(50)).await;
        "ok".to_string()
    }

    fn active_count(&self) -> usize {
        let max = 5; // Must match construction
        max - self.semaphore.available_permits()
    }
}

#[tokio::test]
async fn test_rate_limiting_respects_max_concurrent() {
    tokio::time::pause();

    let client = Arc::new(RateLimitedClient::new(3));

    // Spawn 10 concurrent requests
    let mut handles = vec![];
    for _ in 0..10 {
        let client = client.clone();
        handles.push(tokio::spawn(async move {
            client.request().await
        }));
    }

    // Let everything complete
    for h in handles {
        h.await.unwrap();
    }

    // All 10 should have completed
    assert_eq!(client.call_count.load(Ordering::SeqCst), 10);
}
```

## Testing Shutdown and Cancellation

```rust
use tokio::sync::mpsc;
use tokio::time::{sleep, Duration};
use tokio_util::sync::CancellationToken;

async fn long_running_worker(
    token: CancellationToken,
    tx: mpsc::Sender<String>,
) {
    loop {
        tokio::select! {
            _ = token.cancelled() => {
                tx.send("shutting down".into()).await.unwrap();
                return;
            }
            _ = sleep(Duration::from_millis(100)) => {
                if tx.send("heartbeat".into()).await.is_err() {
                    return;
                }
            }
        }
    }
}

#[tokio::test]
async fn test_worker_responds_to_cancellation() {
    let token = CancellationToken::new();
    let (tx, mut rx) = mpsc::channel(10);

    let handle = tokio::spawn(long_running_worker(token.clone(), tx));

    // Wait for at least one heartbeat
    let msg = tokio::time::timeout(Duration::from_secs(1), rx.recv())
        .await
        .expect("timeout")
        .expect("channel closed");
    assert_eq!(msg, "heartbeat");

    // Cancel the worker
    token.cancel();

    // Should get the shutdown message
    let msg = tokio::time::timeout(Duration::from_secs(1), rx.recv())
        .await
        .expect("timeout")
        .expect("channel closed");
    assert_eq!(msg, "shutting down");

    handle.await.unwrap();
}

#[tokio::test]
async fn test_worker_stops_when_channel_closes() {
    let token = CancellationToken::new();
    let (tx, rx) = mpsc::channel(10);

    let handle = tokio::spawn(long_running_worker(token, tx));

    // Drop the receiver — worker should stop
    drop(rx);

    // Worker should complete without hanging
    let result = tokio::time::timeout(Duration::from_secs(1), handle).await;
    assert!(result.is_ok(), "Worker didn't stop after channel closed");
}
```

## Test Helpers

Build reusable test utilities:

```rust
#[cfg(test)]
mod test_utils {
    use tokio::sync::mpsc;
    use tokio::time::{timeout, Duration};

    /// Collect all items from a channel with a timeout
    pub async fn collect_with_timeout<T>(
        mut rx: mpsc::Receiver<T>,
        dur: Duration,
    ) -> Vec<T> {
        let mut items = Vec::new();
        let _ = timeout(dur, async {
            while let Some(item) = rx.recv().await {
                items.push(item);
            }
        }).await;
        items
    }

    /// Assert that an async operation completes within a duration
    pub async fn assert_completes_within<F, T>(future: F, dur: Duration) -> T
    where
        F: std::future::Future<Output = T>,
    {
        timeout(dur, future)
            .await
            .expect("Operation did not complete within expected time")
    }

    /// Assert that an async operation does NOT complete within a duration
    pub async fn assert_does_not_complete<F, T>(future: F, dur: Duration)
    where
        F: std::future::Future<Output = T>,
    {
        let result = timeout(dur, future).await;
        assert!(result.is_err(), "Operation completed when it shouldn't have");
    }
}

#[cfg(test)]
mod tests {
    use super::test_utils::*;
    use tokio::sync::mpsc;
    use tokio::time::Duration;

    #[tokio::test]
    async fn test_collect_helper() {
        let (tx, rx) = mpsc::channel(10);

        tokio::spawn(async move {
            tx.send(1).await.unwrap();
            tx.send(2).await.unwrap();
            tx.send(3).await.unwrap();
        });

        let items = collect_with_timeout(rx, Duration::from_secs(1)).await;
        assert_eq!(items, vec![1, 2, 3]);
    }
}
```

## Test Checklist

For every async test I write, I check:

1. **Timeout:** Does the test have a maximum duration? A hanging test is worse than a failing one.
2. **Determinism:** Does the test depend on real time? If so, use `tokio::time::pause()`.
3. **Cleanup:** Are all spawned tasks awaited or aborted? Leaked tasks can affect other tests.
4. **Error paths:** Am I testing the failure cases, not just the happy path?
5. **Concurrency:** If the code is concurrent, am I testing the concurrent behavior?

Testing async code takes more thought than sync code, but the patterns are well-established. Mock what's non-deterministic, control time, use traits for dependencies, and always set timeouts. Your CI will thank you.

Last lesson next: production async patterns — connection pools, retries, circuit breakers, and everything you need to run async Rust in production.
