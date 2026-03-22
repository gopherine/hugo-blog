---
title: "Lesson 20: Production Async Architecture — Connection pools, retries, circuit breakers"
author: Atharva Pandey
series: "Async Rust Masterclass"
lesson: 20
keywords: [Rust, rust, async, tokio, production, connection pool, retry, circuit breaker, resilience]
tags: [Rust tutorial, rust, async, tokio]
date: '2025-02-12T17:09:33.000Z'
---

This is the lesson that ties everything together. Over the last 19 lessons, we've built up from mental models to executors, from channels to cancellation safety. Now it's time to put it all into a production-grade architecture.

I've run async Rust services handling tens of thousands of requests per second. The patterns in this lesson are the ones that survived contact with real traffic, real failures, and 3 AM pages. Nothing theoretical here — just battle-tested code.

## Connection Pool Pattern

Every production service talks to databases, caches, or external services. Creating a new connection per request is slow and wasteful. A connection pool maintains a set of reusable connections:

```rust
use std::sync::Arc;
use tokio::sync::{Semaphore, Mutex};
use tokio::time::{sleep, timeout, Duration};
use std::collections::VecDeque;

#[derive(Debug)]
struct Connection {
    id: u32,
    healthy: bool,
}

impl Connection {
    async fn new(id: u32) -> Result<Self, String> {
        // Simulate connection establishment
        sleep(Duration::from_millis(50)).await;
        Ok(Connection { id, healthy: true })
    }

    async fn execute(&mut self, query: &str) -> Result<String, String> {
        if !self.healthy {
            return Err("connection unhealthy".to_string());
        }
        sleep(Duration::from_millis(10)).await;
        Ok(format!("result from conn-{}: {query}", self.id))
    }

    async fn health_check(&mut self) -> bool {
        sleep(Duration::from_millis(5)).await;
        self.healthy
    }
}

struct Pool {
    connections: Mutex<VecDeque<Connection>>,
    semaphore: Arc<Semaphore>,
    max_size: usize,
    created: Mutex<u32>,
}

impl Pool {
    fn new(max_size: usize) -> Arc<Self> {
        Arc::new(Pool {
            connections: Mutex::new(VecDeque::new()),
            semaphore: Arc::new(Semaphore::new(max_size)),
            max_size,
            created: Mutex::new(0),
        })
    }

    async fn acquire(&self) -> Result<PooledConnection<'_>, String> {
        // Wait for a slot
        let permit = timeout(
            Duration::from_secs(5),
            self.semaphore.acquire(),
        )
        .await
        .map_err(|_| "timeout waiting for connection".to_string())?
        .map_err(|_| "pool closed".to_string())?;

        // Try to reuse an existing connection
        let mut conns = self.connections.lock().await;
        if let Some(mut conn) = conns.pop_front() {
            drop(conns);
            if conn.health_check().await {
                permit.forget(); // We manage the slot ourselves
                return Ok(PooledConnection {
                    conn: Some(conn),
                    pool: self,
                });
            }
            // Unhealthy — create a new one
        } else {
            drop(conns);
        }

        // Create a new connection
        let mut count = self.created.lock().await;
        *count += 1;
        let id = *count;
        drop(count);

        let conn = Connection::new(id).await?;
        permit.forget();
        Ok(PooledConnection {
            conn: Some(conn),
            pool: self,
        })
    }

    async fn release(&self, conn: Connection) {
        let mut conns = self.connections.lock().await;
        if conns.len() < self.max_size {
            conns.push_back(conn);
        }
        drop(conns);
        self.semaphore.add_permits(1);
    }
}

struct PooledConnection<'a> {
    conn: Option<Connection>,
    pool: &'a Pool,
}

impl<'a> PooledConnection<'a> {
    async fn execute(&mut self, query: &str) -> Result<String, String> {
        self.conn.as_mut().unwrap().execute(query).await
    }
}

impl<'a> Drop for PooledConnection<'a> {
    fn drop(&mut self) {
        if let Some(conn) = self.conn.take() {
            let pool_conns = &self.pool.connections;
            let sem = &self.pool.semaphore;
            // We can't await in Drop, so we spawn
            // In production, use a return channel instead
            sem.add_permits(1);
        }
    }
}

#[tokio::main]
async fn main() {
    let pool = Pool::new(5);

    let mut handles = vec![];
    for i in 0..20 {
        let pool = pool.clone();
        handles.push(tokio::spawn(async move {
            let mut conn = pool.acquire().await.unwrap();
            let result = conn.execute(&format!("SELECT {i}")).await.unwrap();
            println!("{result}");
            // Connection returned to pool when conn is dropped
            pool.release(conn.conn.take().unwrap()).await;
        }));
    }

    for h in handles {
        h.await.unwrap();
    }
}
```

In practice, use `bb8`, `deadpool`, or `sqlx`'s built-in pool. But understanding the mechanics matters.

## Retry with Exponential Backoff and Jitter

```rust
use tokio::time::{sleep, Duration};
use std::future::Future;

#[derive(Clone)]
struct RetryConfig {
    max_retries: u32,
    initial_delay: Duration,
    max_delay: Duration,
    jitter: bool,
}

impl Default for RetryConfig {
    fn default() -> Self {
        RetryConfig {
            max_retries: 3,
            initial_delay: Duration::from_millis(100),
            max_delay: Duration::from_secs(10),
            jitter: true,
        }
    }
}

async fn retry<F, Fut, T, E>(
    config: &RetryConfig,
    operation: F,
) -> Result<T, E>
where
    F: Fn() -> Fut,
    Fut: Future<Output = Result<T, E>>,
    E: std::fmt::Display,
{
    let mut delay = config.initial_delay;

    for attempt in 0..=config.max_retries {
        match operation().await {
            Ok(val) => return Ok(val),
            Err(e) => {
                if attempt == config.max_retries {
                    eprintln!("All {} retries exhausted: {e}", config.max_retries);
                    return Err(e);
                }

                let jittered_delay = if config.jitter {
                    // Simple jitter: 50% to 150% of delay
                    let jitter_factor = 0.5 + (attempt as f64 * 0.1 % 1.0);
                    Duration::from_millis(
                        (delay.as_millis() as f64 * jitter_factor) as u64
                    )
                } else {
                    delay
                };

                eprintln!(
                    "Attempt {} failed: {e}. Retrying in {:?}",
                    attempt + 1,
                    jittered_delay
                );

                sleep(jittered_delay).await;
                delay = (delay * 2).min(config.max_delay);
            }
        }
    }

    unreachable!()
}

// Usage
use std::sync::atomic::{AtomicU32, Ordering};

#[tokio::main]
async fn main() {
    let attempt_count = AtomicU32::new(0);

    let result = retry(
        &RetryConfig::default(),
        || {
            let attempt = attempt_count.fetch_add(1, Ordering::Relaxed);
            async move {
                if attempt < 2 {
                    Err(format!("transient error on attempt {attempt}"))
                } else {
                    Ok("success!")
                }
            }
        },
    ).await;

    println!("Result: {result:?}");
}
```

## Circuit Breaker

A circuit breaker prevents cascading failures by stopping calls to a failing service:

```rust
use std::sync::Arc;
use tokio::sync::Mutex;
use tokio::time::{sleep, Duration, Instant};
use std::future::Future;

#[derive(Debug, Clone, Copy, PartialEq)]
enum CircuitState {
    Closed,     // Normal operation
    Open,       // Failing — reject all requests
    HalfOpen,   // Testing — allow one request through
}

struct CircuitBreaker {
    state: Mutex<CircuitState>,
    failure_count: Mutex<u32>,
    failure_threshold: u32,
    recovery_timeout: Duration,
    last_failure: Mutex<Option<Instant>>,
}

impl CircuitBreaker {
    fn new(failure_threshold: u32, recovery_timeout: Duration) -> Arc<Self> {
        Arc::new(CircuitBreaker {
            state: Mutex::new(CircuitState::Closed),
            failure_count: Mutex::new(0),
            failure_threshold,
            recovery_timeout,
            last_failure: Mutex::new(None),
        })
    }

    async fn call<F, Fut, T, E>(&self, operation: F) -> Result<T, CircuitError<E>>
    where
        F: FnOnce() -> Fut,
        Fut: Future<Output = Result<T, E>>,
    {
        // Check if we should allow the request
        let state = self.current_state().await;

        match state {
            CircuitState::Open => {
                return Err(CircuitError::Open);
            }
            CircuitState::HalfOpen | CircuitState::Closed => {
                // Allow the request
            }
        }

        match operation().await {
            Ok(val) => {
                self.on_success().await;
                Ok(val)
            }
            Err(e) => {
                self.on_failure().await;
                Err(CircuitError::Inner(e))
            }
        }
    }

    async fn current_state(&self) -> CircuitState {
        let mut state = self.state.lock().await;
        if *state == CircuitState::Open {
            let last = self.last_failure.lock().await;
            if let Some(last_failure) = *last {
                if last_failure.elapsed() >= self.recovery_timeout {
                    *state = CircuitState::HalfOpen;
                }
            }
        }
        *state
    }

    async fn on_success(&self) {
        let mut state = self.state.lock().await;
        *self.failure_count.lock().await = 0;
        *state = CircuitState::Closed;
    }

    async fn on_failure(&self) {
        let mut count = self.failure_count.lock().await;
        *count += 1;
        *self.last_failure.lock().await = Some(Instant::now());

        if *count >= self.failure_threshold {
            let mut state = self.state.lock().await;
            *state = CircuitState::Open;
            println!("Circuit OPENED after {} failures", count);
        }
    }
}

#[derive(Debug)]
enum CircuitError<E> {
    Open,       // Circuit is open — request rejected
    Inner(E),   // Actual operation error
}

impl<E: std::fmt::Display> std::fmt::Display for CircuitError<E> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CircuitError::Open => write!(f, "circuit breaker is open"),
            CircuitError::Inner(e) => write!(f, "{e}"),
        }
    }
}

#[tokio::main]
async fn main() {
    let breaker = CircuitBreaker::new(3, Duration::from_secs(5));
    let call_count = std::sync::atomic::AtomicU32::new(0);

    for i in 0..10 {
        let result = breaker.call(|| {
            let n = call_count.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            async move {
                if n < 4 {
                    Err(format!("service unavailable (call {n})"))
                } else {
                    Ok(format!("success (call {n})"))
                }
            }
        }).await;

        match result {
            Ok(val) => println!("[{i}] {val}"),
            Err(CircuitError::Open) => println!("[{i}] REJECTED — circuit open"),
            Err(CircuitError::Inner(e)) => println!("[{i}] Error: {e}"),
        }

        sleep(Duration::from_millis(100)).await;
    }

    // Wait for recovery timeout
    println!("\nWaiting for circuit to half-open...");
    sleep(Duration::from_secs(6)).await;

    let result = breaker.call(|| async {
        Ok::<_, String>("recovered!".to_string())
    }).await;
    println!("After recovery: {result:?}");
}
```

## Health Check Pattern

```rust
use std::sync::Arc;
use tokio::sync::RwLock;
use tokio::time::{interval, Duration};

#[derive(Debug, Clone)]
struct HealthStatus {
    database: bool,
    cache: bool,
    external_api: bool,
}

impl HealthStatus {
    fn is_healthy(&self) -> bool {
        self.database && self.cache
        // external_api is optional — degraded mode is OK
    }

    fn is_degraded(&self) -> bool {
        self.is_healthy() && !self.external_api
    }
}

struct HealthChecker {
    status: Arc<RwLock<HealthStatus>>,
}

impl HealthChecker {
    fn new() -> Self {
        HealthChecker {
            status: Arc::new(RwLock::new(HealthStatus {
                database: true,
                cache: true,
                external_api: true,
            })),
        }
    }

    fn start_background_checks(&self) {
        let status = self.status.clone();

        tokio::spawn(async move {
            let mut ticker = interval(Duration::from_secs(10));

            loop {
                ticker.tick().await;

                let db_ok = check_database().await;
                let cache_ok = check_cache().await;
                let api_ok = check_external_api().await;

                let mut s = status.write().await;
                s.database = db_ok;
                s.cache = cache_ok;
                s.external_api = api_ok;

                if !s.is_healthy() {
                    eprintln!("UNHEALTHY: {:?}", *s);
                } else if s.is_degraded() {
                    eprintln!("DEGRADED: {:?}", *s);
                }
            }
        });
    }

    async fn get_status(&self) -> HealthStatus {
        self.status.read().await.clone()
    }
}

async fn check_database() -> bool {
    tokio::time::sleep(Duration::from_millis(10)).await;
    true // Simulate check
}

async fn check_cache() -> bool {
    tokio::time::sleep(Duration::from_millis(5)).await;
    true
}

async fn check_external_api() -> bool {
    tokio::time::sleep(Duration::from_millis(20)).await;
    true
}

#[tokio::main]
async fn main() {
    let checker = HealthChecker::new();
    checker.start_background_checks();

    // Simulate checking health from HTTP handler
    for _ in 0..3 {
        let status = checker.get_status().await;
        println!("Health: {:?} (healthy={}, degraded={})",
            status, status.is_healthy(), status.is_degraded());
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
}
```

## Putting It All Together: A Resilient Service Client

```rust
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use tokio::sync::Semaphore;
use tokio::time::{sleep, timeout, Duration, Instant};

/// A production-ready service client with:
/// - Connection pooling (via semaphore)
/// - Retries with backoff
/// - Circuit breaker
/// - Timeouts on every operation
/// - Metrics
struct ResilientClient {
    name: String,
    concurrency: Arc<Semaphore>,
    request_timeout: Duration,
    total_requests: AtomicU64,
    failed_requests: AtomicU64,
    circuit_failures: std::sync::Mutex<u32>,
    circuit_open_until: std::sync::Mutex<Option<Instant>>,
}

impl ResilientClient {
    fn new(name: &str, max_concurrent: usize, request_timeout: Duration) -> Arc<Self> {
        Arc::new(ResilientClient {
            name: name.to_string(),
            concurrency: Arc::new(Semaphore::new(max_concurrent)),
            request_timeout,
            total_requests: AtomicU64::new(0),
            failed_requests: AtomicU64::new(0),
            circuit_failures: std::sync::Mutex::new(0),
            circuit_open_until: std::sync::Mutex::new(None),
        })
    }

    fn is_circuit_open(&self) -> bool {
        let open_until = self.circuit_open_until.lock().unwrap();
        match *open_until {
            Some(until) => Instant::now() < until,
            None => false,
        }
    }

    fn record_success(&self) {
        *self.circuit_failures.lock().unwrap() = 0;
        *self.circuit_open_until.lock().unwrap() = None;
    }

    fn record_failure(&self) {
        self.failed_requests.fetch_add(1, Ordering::Relaxed);
        let mut failures = self.circuit_failures.lock().unwrap();
        *failures += 1;
        if *failures >= 5 {
            *self.circuit_open_until.lock().unwrap() =
                Some(Instant::now() + Duration::from_secs(30));
            eprintln!("[{}] Circuit opened!", self.name);
        }
    }

    async fn request<F, Fut, T>(
        &self,
        operation: F,
    ) -> Result<T, String>
    where
        F: Fn() -> Fut,
        Fut: std::future::Future<Output = Result<T, String>>,
    {
        self.total_requests.fetch_add(1, Ordering::Relaxed);

        // Circuit breaker check
        if self.is_circuit_open() {
            return Err(format!("[{}] circuit open", self.name));
        }

        // Concurrency limit
        let _permit = timeout(
            Duration::from_secs(5),
            self.concurrency.acquire(),
        )
        .await
        .map_err(|_| format!("[{}] concurrency timeout", self.name))?
        .map_err(|_| format!("[{}] semaphore closed", self.name))?;

        // Retry loop
        let mut delay = Duration::from_millis(100);
        let max_retries = 3;

        for attempt in 0..=max_retries {
            match timeout(self.request_timeout, operation()).await {
                Ok(Ok(val)) => {
                    self.record_success();
                    return Ok(val);
                }
                Ok(Err(e)) => {
                    if attempt == max_retries {
                        self.record_failure();
                        return Err(format!("[{}] all retries failed: {e}", self.name));
                    }
                    eprintln!("[{}] attempt {} failed: {e}", self.name, attempt + 1);
                    sleep(delay).await;
                    delay *= 2;
                }
                Err(_) => {
                    if attempt == max_retries {
                        self.record_failure();
                        return Err(format!("[{}] request timeout", self.name));
                    }
                    eprintln!("[{}] attempt {} timed out", self.name, attempt + 1);
                    sleep(delay).await;
                    delay *= 2;
                }
            }
        }

        unreachable!()
    }

    fn stats(&self) -> (u64, u64) {
        (
            self.total_requests.load(Ordering::Relaxed),
            self.failed_requests.load(Ordering::Relaxed),
        )
    }
}

#[tokio::main]
async fn main() {
    let client = ResilientClient::new("payment-service", 10, Duration::from_secs(2));

    let call_num = AtomicU64::new(0);
    let mut handles = vec![];

    for _ in 0..15 {
        let client = client.clone();
        let call_num = &call_num;
        handles.push(tokio::spawn({
            let n = call_num.fetch_add(1, Ordering::Relaxed);
            let client = client.clone();
            async move {
                let result = client.request(|| async move {
                    sleep(Duration::from_millis(50)).await;
                    if n % 4 == 0 {
                        Err("payment gateway error".to_string())
                    } else {
                        Ok(format!("payment-{n} processed"))
                    }
                }).await;

                match result {
                    Ok(val) => println!("OK: {val}"),
                    Err(e) => println!("ERR: {e}"),
                }
            }
        }));
    }

    for h in handles {
        h.await.unwrap();
    }

    let (total, failed) = client.stats();
    println!("\nStats: {total} total, {failed} failed");
}
```

## Production Checklist

Before shipping an async Rust service, I check:

1. **Every external call has a timeout.** No exceptions.
2. **Retries have backoff and jitter.** Thundering herd kills services.
3. **Circuit breakers on critical dependencies.** Don't let one failure cascade.
4. **Bounded concurrency everywhere.** Semaphores or bounded channels.
5. **Graceful shutdown is implemented.** Handle SIGTERM, drain connections.
6. **Tracing is configured.** Structured logs, spans across async boundaries.
7. **Health checks exist.** Both liveness and readiness probes.
8. **Metrics are exported.** Request count, latency histograms, error rates.
9. **No unbounded channels.** Every channel has a capacity.
10. **No blocking calls on the executor.** `spawn_blocking` for CPU work and legacy code.

## The Course Recap

We started with mental models and ended with production patterns. Along the way:

- **Lessons 1-2:** Futures are lazy state machines, not threads
- **Lessons 3-5:** Tokio, spawning, and select! — the core tools
- **Lessons 6-9:** Data flow — streams, channels, mutexes, semaphores
- **Lessons 10-11:** Safety — cancellation, timeouts, graceful shutdown
- **Lessons 12-15:** Real I/O — files, HTTP, Tower, backpressure
- **Lessons 16-17:** Internals — executors and Pin
- **Lessons 18-19:** Observability and testing
- **Lesson 20:** Production architecture

Async Rust has a steep learning curve. But once you've climbed it, you have a systems programming language with zero-cost async abstractions, fearless concurrency, and the performance to back it all up. No garbage collector pauses, no hidden thread pools, no runtime surprises.

The code you write is the code that runs. And now you know exactly how it runs.
