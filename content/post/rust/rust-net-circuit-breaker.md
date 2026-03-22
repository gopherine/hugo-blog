---
title: "Lesson 8: Circuit Breakers in Rust — Failing fast"
author: Atharva Pandey
series: "Rust Networking & Distributed Systems"
lesson: 8
keywords: [Rust, rust, networking, distributed, circuit breaker, resilience, fault tolerance, microservices]
tags: [Rust tutorial, rust, networking, distributed-systems]
date: '2025-05-29T07:35:00.000Z'
---

Picture this: your payment service depends on a fraud detection API that's completely down. Every request to it takes 30 seconds to timeout. Your payment service has 200 requests queued up, each holding a thread and a database connection while waiting for fraud detection to respond. Within minutes, you're out of connections, the payment service itself starts failing, and now the checkout service that depends on payments starts failing too. One dead service has cascaded into a full outage.

A circuit breaker would've prevented the entire cascade. It detects that fraud detection is failing, stops sending requests to it, and returns an error immediately — in microseconds instead of 30 seconds. The payment service stays healthy, maybe falls back to a less-strict fraud check, and everything else keeps running.

## The Circuit Breaker Pattern

The concept comes from electrical engineering — when too much current flows through a circuit, the breaker trips and cuts the connection to prevent damage. In software, when too many requests to a service fail, the circuit breaker "opens" and stops sending requests.

Three states:

- **Closed** — everything's fine. Requests flow through normally. The breaker counts failures.
- **Open** — too many failures. Requests are rejected immediately without calling the downstream service.
- **Half-Open** — after a timeout, the breaker lets a few test requests through. If they succeed, it closes. If they fail, it opens again.

## Building a Circuit Breaker from Scratch

Let's implement this properly. No crates — just Rust's standard concurrency primitives.

```rust
use std::sync::atomic::{AtomicU32, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum CircuitState {
    Closed,
    Open,
    HalfOpen,
}

#[derive(Clone)]
pub struct CircuitBreakerConfig {
    /// Number of consecutive failures before opening
    pub failure_threshold: u32,
    /// How long to stay open before trying half-open
    pub open_duration: Duration,
    /// Number of successes in half-open to close the circuit
    pub half_open_max_calls: u32,
}

impl Default for CircuitBreakerConfig {
    fn default() -> Self {
        Self {
            failure_threshold: 5,
            open_duration: Duration::from_secs(30),
            half_open_max_calls: 3,
        }
    }
}

pub struct CircuitBreaker {
    config: CircuitBreakerConfig,
    state: Mutex<CircuitState>,
    failure_count: AtomicU32,
    success_count: AtomicU32,
    last_failure_time: Mutex<Option<Instant>>,
    half_open_calls: AtomicU32,
}

#[derive(Debug, thiserror::Error)]
pub enum CircuitError<E> {
    #[error("Circuit is open — request rejected")]
    Open,
    #[error("Operation failed: {0}")]
    Inner(E),
}

impl CircuitBreaker {
    pub fn new(config: CircuitBreakerConfig) -> Arc<Self> {
        Arc::new(Self {
            config,
            state: Mutex::new(CircuitState::Closed),
            failure_count: AtomicU32::new(0),
            success_count: AtomicU32::new(0),
            last_failure_time: Mutex::new(None),
            half_open_calls: AtomicU32::new(0),
        })
    }

    pub fn state(&self) -> CircuitState {
        let mut state = self.state.lock().unwrap();

        // Check if we should transition from Open to HalfOpen
        if *state == CircuitState::Open {
            if let Some(last_failure) = *self.last_failure_time.lock().unwrap() {
                if last_failure.elapsed() >= self.config.open_duration {
                    *state = CircuitState::HalfOpen;
                    self.half_open_calls.store(0, Ordering::Relaxed);
                    self.success_count.store(0, Ordering::Relaxed);
                }
            }
        }

        *state
    }

    fn record_success(&self) {
        let mut state = self.state.lock().unwrap();

        match *state {
            CircuitState::Closed => {
                // Reset failure count on success
                self.failure_count.store(0, Ordering::Relaxed);
            }
            CircuitState::HalfOpen => {
                let successes = self.success_count.fetch_add(1, Ordering::Relaxed) + 1;
                if successes >= self.config.half_open_max_calls {
                    // Enough successes — close the circuit
                    *state = CircuitState::Closed;
                    self.failure_count.store(0, Ordering::Relaxed);
                    self.success_count.store(0, Ordering::Relaxed);
                    println!("Circuit CLOSED — service recovered");
                }
            }
            CircuitState::Open => {
                // Shouldn't happen, but handle gracefully
            }
        }
    }

    fn record_failure(&self) {
        let mut state = self.state.lock().unwrap();

        match *state {
            CircuitState::Closed => {
                let failures = self.failure_count.fetch_add(1, Ordering::Relaxed) + 1;
                if failures >= self.config.failure_threshold {
                    *state = CircuitState::Open;
                    *self.last_failure_time.lock().unwrap() = Some(Instant::now());
                    println!(
                        "Circuit OPENED after {failures} consecutive failures"
                    );
                }
            }
            CircuitState::HalfOpen => {
                // Any failure in half-open goes straight back to open
                *state = CircuitState::Open;
                *self.last_failure_time.lock().unwrap() = Some(Instant::now());
                println!("Circuit re-OPENED — half-open test failed");
            }
            CircuitState::Open => {}
        }
    }

    pub async fn call<F, Fut, T, E>(
        &self,
        operation: F,
    ) -> Result<T, CircuitError<E>>
    where
        F: FnOnce() -> Fut,
        Fut: std::future::Future<Output = Result<T, E>>,
    {
        let current_state = self.state();

        match current_state {
            CircuitState::Open => {
                return Err(CircuitError::Open);
            }
            CircuitState::HalfOpen => {
                // Only allow limited calls through
                let calls = self.half_open_calls.fetch_add(1, Ordering::Relaxed);
                if calls >= self.config.half_open_max_calls {
                    return Err(CircuitError::Open);
                }
            }
            CircuitState::Closed => {}
        }

        match operation().await {
            Ok(val) => {
                self.record_success();
                Ok(val)
            }
            Err(e) => {
                self.record_failure();
                Err(CircuitError::Inner(e))
            }
        }
    }
}
```

## Using the Circuit Breaker

```rust
use reqwest::Client;
use std::sync::Arc;
use std::time::Duration;

struct PaymentService {
    client: Client,
    fraud_check_breaker: Arc<CircuitBreaker>,
}

impl PaymentService {
    fn new() -> Self {
        Self {
            client: Client::builder()
                .timeout(Duration::from_secs(5))
                .build()
                .unwrap(),
            fraud_check_breaker: CircuitBreaker::new(CircuitBreakerConfig {
                failure_threshold: 3,
                open_duration: Duration::from_secs(60),
                half_open_max_calls: 2,
            }),
        }
    }

    async fn check_fraud(
        &self,
        transaction_id: &str,
    ) -> Result<bool, String> {
        let client = self.client.clone();
        let url = format!(
            "http://fraud-service/check/{transaction_id}"
        );

        let result = self
            .fraud_check_breaker
            .call(|| async {
                let resp = client
                    .get(&url)
                    .send()
                    .await
                    .map_err(|e| e.to_string())?;

                if resp.status().is_success() {
                    Ok(true) // Not fraudulent
                } else {
                    Err(format!("Fraud service returned {}", resp.status()))
                }
            })
            .await;

        match result {
            Ok(safe) => Ok(safe),
            Err(CircuitError::Open) => {
                // Fallback: allow the transaction but flag for manual review
                println!(
                    "Fraud service unavailable — flagging {transaction_id} for review"
                );
                Ok(true) // Allow, but log it
            }
            Err(CircuitError::Inner(e)) => Err(e),
        }
    }
}

#[tokio::main]
async fn main() {
    let service = PaymentService::new();

    // Simulate some requests
    for i in 0..10 {
        let tx_id = format!("tx-{i:04}");
        match service.check_fraud(&tx_id).await {
            Ok(true) => println!("{tx_id}: approved"),
            Ok(false) => println!("{tx_id}: rejected (fraud detected)"),
            Err(e) => println!("{tx_id}: error — {e}"),
        }

        tokio::time::sleep(Duration::from_millis(200)).await;
    }

    // Print final circuit state
    println!("\nCircuit state: {:?}", service.fraud_check_breaker.state());
}
```

The fallback behavior is the interesting part. When the circuit is open, instead of returning an error, the payment service degrades gracefully — it allows the transaction but flags it for manual review. This is a business decision, not a technical one. Some services have no reasonable fallback and should return an error. Others can use cached data, default values, or degraded functionality.

## Combining Circuit Breakers with Retries

Circuit breakers and retries are complementary. Retries handle transient failures (brief blips). Circuit breakers handle sustained failures (dead services). The retry logic lives inside the circuit breaker.

```rust
use std::future::Future;
use std::time::Duration;

pub struct ResilientCaller {
    breaker: Arc<CircuitBreaker>,
    backoff: BackoffConfig,
}

impl ResilientCaller {
    pub fn new(
        breaker_config: CircuitBreakerConfig,
        backoff_config: BackoffConfig,
    ) -> Self {
        Self {
            breaker: CircuitBreaker::new(breaker_config),
            backoff: backoff_config,
        }
    }

    pub async fn call<F, Fut, T, E>(
        &self,
        is_retryable: impl Fn(&E) -> bool,
        operation: F,
    ) -> Result<T, CircuitError<E>>
    where
        F: Fn() -> Fut,
        Fut: Future<Output = Result<T, E>>,
        E: std::fmt::Display,
    {
        // The circuit breaker wraps the retry logic
        self.breaker
            .call(|| async {
                let mut last_error = None;

                for attempt in 0..=self.backoff.max_retries {
                    if attempt > 0 {
                        let delay = self.backoff.delay_for_attempt(attempt - 1);
                        tokio::time::sleep(delay).await;
                    }

                    match operation().await {
                        Ok(val) => return Ok(val),
                        Err(e) => {
                            if !is_retryable(&e) {
                                return Err(e);
                            }
                            eprintln!(
                                "Attempt {}/{}: {e}",
                                attempt + 1,
                                self.backoff.max_retries + 1
                            );
                            last_error = Some(e);
                        }
                    }
                }

                Err(last_error.unwrap())
            })
            .await
    }

    pub fn state(&self) -> CircuitState {
        self.breaker.state()
    }
}
```

The order matters: circuit breaker on the outside, retries on the inside. If you put retries outside the circuit breaker, retries would bypass the open circuit. The circuit breaker should see the final result of all retry attempts — if they all failed, that counts as one failure toward the threshold.

## Observability

A circuit breaker that trips silently is almost as bad as not having one. You need metrics and alerts.

```rust
use std::sync::atomic::{AtomicU64, Ordering};

pub struct CircuitBreakerMetrics {
    pub total_calls: AtomicU64,
    pub successful_calls: AtomicU64,
    pub failed_calls: AtomicU64,
    pub rejected_calls: AtomicU64, // Rejected because circuit was open
    pub state_changes: AtomicU64,
}

impl CircuitBreakerMetrics {
    pub fn new() -> Self {
        Self {
            total_calls: AtomicU64::new(0),
            successful_calls: AtomicU64::new(0),
            failed_calls: AtomicU64::new(0),
            rejected_calls: AtomicU64::new(0),
            state_changes: AtomicU64::new(0),
        }
    }

    pub fn report(&self) {
        let total = self.total_calls.load(Ordering::Relaxed);
        let success = self.successful_calls.load(Ordering::Relaxed);
        let failed = self.failed_calls.load(Ordering::Relaxed);
        let rejected = self.rejected_calls.load(Ordering::Relaxed);

        let success_rate = if total > 0 {
            (success as f64 / total as f64) * 100.0
        } else {
            100.0
        };

        println!("Circuit Breaker Report:");
        println!("  Total calls:    {total}");
        println!("  Successful:     {success}");
        println!("  Failed:         {failed}");
        println!("  Rejected (open): {rejected}");
        println!("  Success rate:   {success_rate:.1}%");
        println!(
            "  State changes:  {}",
            self.state_changes.load(Ordering::Relaxed)
        );
    }
}
```

In production, these counters would feed into Prometheus or your metrics system of choice. Alert on:
- Circuit opening (immediate notification)
- High rejection rate (sustained open circuit)
- Circuit staying open longer than expected (the service isn't recovering)

## Multiple Circuit Breakers

Real services talk to multiple downstream dependencies. Each one needs its own circuit breaker.

```rust
use std::collections::HashMap;
use std::sync::Arc;

pub struct CircuitBreakerRegistry {
    breakers: Arc<tokio::sync::RwLock<HashMap<String, Arc<CircuitBreaker>>>>,
    default_config: CircuitBreakerConfig,
}

impl CircuitBreakerRegistry {
    pub fn new(default_config: CircuitBreakerConfig) -> Self {
        Self {
            breakers: Arc::new(tokio::sync::RwLock::new(HashMap::new())),
            default_config,
        }
    }

    pub async fn get_or_create(&self, name: &str) -> Arc<CircuitBreaker> {
        // Check if exists
        {
            let breakers = self.breakers.read().await;
            if let Some(cb) = breakers.get(name) {
                return cb.clone();
            }
        }

        // Create new
        let mut breakers = self.breakers.write().await;
        let cb = breakers
            .entry(name.to_string())
            .or_insert_with(|| CircuitBreaker::new(self.default_config.clone()));
        cb.clone()
    }

    pub async fn status_report(&self) -> Vec<(String, CircuitState)> {
        let breakers = self.breakers.read().await;
        breakers
            .iter()
            .map(|(name, cb)| (name.clone(), cb.state()))
            .collect()
    }
}

// Usage:
// let registry = CircuitBreakerRegistry::new(CircuitBreakerConfig::default());
// let fraud_cb = registry.get_or_create("fraud-service").await;
// let inventory_cb = registry.get_or_create("inventory-service").await;
// let shipping_cb = registry.get_or_create("shipping-service").await;
```

This gives you a single place to check the health of all your dependencies. The status report can power a health dashboard or be exposed as an admin endpoint.

## Sliding Window vs Count-Based

Our implementation uses a simple consecutive-failure count. In practice, a sliding window approach is often better — "5 failures out of the last 20 requests" instead of "5 failures in a row."

```rust
use std::collections::VecDeque;
use std::time::Instant;

pub struct SlidingWindowBreaker {
    window: Mutex<VecDeque<(Instant, bool)>>, // (timestamp, success?)
    window_size: usize,
    failure_rate_threshold: f64, // 0.0 to 1.0
    min_calls: usize, // Minimum calls before evaluating
    // ... other fields
}

impl SlidingWindowBreaker {
    fn evaluate(&self) -> bool {
        let window = self.window.lock().unwrap();

        if window.len() < self.min_calls {
            return false; // Not enough data
        }

        let failures = window.iter().filter(|(_, success)| !success).count();
        let rate = failures as f64 / window.len() as f64;

        rate >= self.failure_rate_threshold
    }

    fn record(&self, success: bool) {
        let mut window = self.window.lock().unwrap();
        window.push_back((Instant::now(), success));

        while window.len() > self.window_size {
            window.pop_front();
        }
    }
}
```

The sliding window approach is more nuanced. A single flaky request doesn't trip the breaker. But if 50% of requests fail over a window of 20, something's clearly wrong.

## What's Next

We've covered the core resilience patterns — retries handle blips, circuit breakers handle sustained failures. But most distributed systems need more than just HTTP calls between services. They need asynchronous, decoupled communication. Next up: message queues in Rust — NATS, Kafka, and RabbitMQ.
