---
title: "Lesson 7: Retry Strategies and Exponential Backoff — Resilient clients"
author: Atharva Pandey
series: "Rust Networking & Distributed Systems"
lesson: 7
keywords: [Rust, rust, networking, distributed, retry, backoff, exponential, resilience, jitter]
tags: [Rust tutorial, rust, networking, distributed-systems]
date: '2025-05-26T10:40:00.000Z'
---

Here's a scenario that's burned me more than once: a downstream service has a brief hiccup — maybe a pod is restarting, maybe there's a momentary network partition — and instead of gracefully retrying, my service immediately returns a 500 to every caller. The hiccup lasts 3 seconds. My P99 latency graph spikes. On-call gets paged. Everyone's unhappy. The fix? A retry loop with exponential backoff. Three lines of logic that would've made the entire incident invisible.

But retries done wrong are worse than no retries at all. Naive retry logic is how you turn a small problem into a cascading failure that takes down your entire system.

## The Naive Approach (Don't Do This)

```rust
// DON'T DO THIS IN PRODUCTION
async fn naive_retry<F, T, E>(f: F, max_retries: u32) -> Result<T, E>
where
    F: Fn() -> futures::future::BoxFuture<'static, Result<T, E>>,
{
    let mut last_err = None;
    for _ in 0..=max_retries {
        match f().await {
            Ok(val) => return Ok(val),
            Err(e) => last_err = Some(e),
        }
    }
    Err(last_err.unwrap())
}
```

What's wrong with this? No backoff. If the server returns a 503, you immediately hit it again, and again, and again. If 1,000 clients all do this simultaneously, you've just created a thundering herd that hammers the recovering server with 4,000 requests in under a second. Congratulations, you've turned a recovery into a DDoS.

## Exponential Backoff with Jitter

The correct approach has three components:

1. **Exponential backoff** — wait longer between each retry. 100ms, 200ms, 400ms, 800ms, etc.
2. **Jitter** — add randomness so clients don't all retry at the same instant.
3. **Maximum delay cap** — don't wait 17 minutes between retries.

```rust
use rand::Rng;
use std::time::Duration;

#[derive(Clone, Debug)]
pub struct BackoffConfig {
    pub initial_delay: Duration,
    pub max_delay: Duration,
    pub max_retries: u32,
    pub multiplier: f64,
}

impl Default for BackoffConfig {
    fn default() -> Self {
        Self {
            initial_delay: Duration::from_millis(100),
            max_delay: Duration::from_secs(30),
            max_retries: 5,
            multiplier: 2.0,
        }
    }
}

impl BackoffConfig {
    pub fn delay_for_attempt(&self, attempt: u32) -> Duration {
        let base = self.initial_delay.as_millis() as f64
            * self.multiplier.powi(attempt as i32);

        let capped = base.min(self.max_delay.as_millis() as f64);

        // Full jitter: random value between 0 and the calculated delay
        let jittered = rand::rng().random_range(0.0..=capped);

        Duration::from_millis(jittered as u64)
    }
}
```

Full jitter (random between 0 and the calculated delay) is generally better than equal jitter (half the delay plus random half). AWS published a great analysis on this — full jitter spreads retry traffic more evenly across time.

## A Complete Retry Function

```rust
use std::future::Future;
use std::time::Duration;

#[derive(Debug)]
pub enum RetryError<E> {
    /// All retries exhausted
    Exhausted { last_error: E, attempts: u32 },
    /// The error was not retryable
    NotRetryable(E),
}

pub async fn retry_with_backoff<F, Fut, T, E>(
    config: &BackoffConfig,
    is_retryable: impl Fn(&E) -> bool,
    operation: F,
) -> Result<T, RetryError<E>>
where
    F: Fn() -> Fut,
    Fut: Future<Output = Result<T, E>>,
{
    let mut last_error;

    // First attempt (attempt 0)
    match operation().await {
        Ok(val) => return Ok(val),
        Err(e) => {
            if !is_retryable(&e) {
                return Err(RetryError::NotRetryable(e));
            }
            last_error = e;
        }
    }

    for attempt in 1..=config.max_retries {
        let delay = config.delay_for_attempt(attempt - 1);
        tokio::time::sleep(delay).await;

        match operation().await {
            Ok(val) => return Ok(val),
            Err(e) => {
                if !is_retryable(&e) {
                    return Err(RetryError::NotRetryable(e));
                }
                last_error = e;
            }
        }
    }

    Err(RetryError::Exhausted {
        last_error,
        attempts: config.max_retries + 1,
    })
}
```

The `is_retryable` callback is critical. Not all errors should be retried. A 404 Not Found? Don't retry that — the resource doesn't exist. A 400 Bad Request? Your request is malformed; sending it again won't help. You retry on transient failures: 502, 503, 504, connection resets, timeouts.

## Practical HTTP Retry Client

Let's put it all together with reqwest:

```rust
use reqwest::{Client, Response, StatusCode};
use serde::de::DeserializeOwned;
use std::time::Duration;

#[derive(Debug, thiserror::Error)]
pub enum HttpError {
    #[error("Request failed: {0}")]
    Request(#[from] reqwest::Error),
    #[error("Server error: {status}")]
    ServerError { status: StatusCode, body: String },
    #[error("Client error: {status}")]
    ClientError { status: StatusCode, body: String },
}

impl HttpError {
    fn is_retryable(&self) -> bool {
        match self {
            // Network errors are usually transient
            HttpError::Request(e) => {
                e.is_timeout() || e.is_connect() || e.is_request()
            }
            // 5xx errors are retryable (server's problem)
            HttpError::ServerError { status, .. } => {
                matches!(
                    *status,
                    StatusCode::BAD_GATEWAY
                        | StatusCode::SERVICE_UNAVAILABLE
                        | StatusCode::GATEWAY_TIMEOUT
                        | StatusCode::TOO_MANY_REQUESTS
                )
            }
            // 4xx errors are not retryable (client's problem)
            HttpError::ClientError { .. } => false,
        }
    }
}

pub struct ResilientClient {
    client: Client,
    backoff: BackoffConfig,
}

impl ResilientClient {
    pub fn new() -> Self {
        Self {
            client: Client::builder()
                .timeout(Duration::from_secs(10))
                .connect_timeout(Duration::from_secs(5))
                .build()
                .expect("Failed to build client"),
            backoff: BackoffConfig::default(),
        }
    }

    pub fn with_backoff(mut self, config: BackoffConfig) -> Self {
        self.backoff = config;
        self
    }

    pub async fn get<T: DeserializeOwned>(
        &self,
        url: &str,
    ) -> Result<T, RetryError<HttpError>> {
        let client = &self.client;
        let url = url.to_string();

        retry_with_backoff(
            &self.backoff,
            |e: &HttpError| e.is_retryable(),
            || {
                let client = client.clone();
                let url = url.clone();
                async move {
                    let response = client.get(&url).send().await?;
                    handle_response(response).await
                }
            },
        )
        .await
    }

    pub async fn post<T: DeserializeOwned, B: serde::Serialize>(
        &self,
        url: &str,
        body: &B,
    ) -> Result<T, RetryError<HttpError>> {
        let client = &self.client;
        let url = url.to_string();
        let body_json = serde_json::to_value(body).unwrap();

        retry_with_backoff(
            &self.backoff,
            |e: &HttpError| e.is_retryable(),
            || {
                let client = client.clone();
                let url = url.clone();
                let body = body_json.clone();
                async move {
                    let response = client.post(&url).json(&body).send().await?;
                    handle_response(response).await
                }
            },
        )
        .await
    }
}

async fn handle_response<T: DeserializeOwned>(
    response: Response,
) -> Result<T, HttpError> {
    let status = response.status();

    if status.is_success() {
        let value = response.json().await?;
        Ok(value)
    } else if status.is_client_error() {
        let body = response.text().await.unwrap_or_default();
        Err(HttpError::ClientError { status, body })
    } else {
        let body = response.text().await.unwrap_or_default();
        Err(HttpError::ServerError { status, body })
    }
}
```

## Respecting Retry-After Headers

When a server sends 429 Too Many Requests, it often includes a `Retry-After` header telling you exactly when to come back. Ignoring this is rude at best and will get your IP blocked at worst.

```rust
use reqwest::Response;
use std::time::Duration;

fn extract_retry_after(response: &Response) -> Option<Duration> {
    let header = response.headers().get("retry-after")?;
    let value = header.to_str().ok()?;

    // Retry-After can be seconds or an HTTP date
    if let Ok(seconds) = value.parse::<u64>() {
        return Some(Duration::from_secs(seconds));
    }

    // Try parsing as HTTP date
    if let Ok(date) = httpdate::parse_http_date(value) {
        let now = std::time::SystemTime::now();
        if let Ok(duration) = date.duration_since(now) {
            return Some(duration);
        }
    }

    None
}

// Integrate into retry logic:
async fn retry_respecting_server<F, Fut, T>(
    config: &BackoffConfig,
    operation: F,
) -> Result<T, HttpError>
where
    F: Fn() -> Fut,
    Fut: Future<Output = Result<(T, Option<Duration>), HttpError>>,
{
    for attempt in 0..=config.max_retries {
        match operation().await {
            Ok((val, _)) => return Ok(val),
            Err(e) if !e.is_retryable() => return Err(e),
            Err(e) => {
                if attempt == config.max_retries {
                    return Err(e);
                }

                // Use server's Retry-After if available,
                // otherwise use our backoff calculation
                let delay = config.delay_for_attempt(attempt);
                tokio::time::sleep(delay).await;
            }
        }
    }

    unreachable!()
}
```

## Using the backon Crate

Writing retry logic from scratch is educational, but in production I usually reach for the **backon** crate. It's well-tested, composable, and handles edge cases I'd rather not think about.

```toml
[dependencies]
backon = "1"
tokio = { version = "1", features = ["full"] }
reqwest = { version = "0.12", features = ["json"] }
anyhow = "1"
```

```rust
use backon::{ExponentialBuilder, Retryable};
use reqwest::Client;
use std::time::Duration;

#[tokio::main]
async fn main() -> Result<(), anyhow::Error> {
    let client = Client::new();

    let response = (|| async {
        let resp = client
            .get("https://httpbin.org/status/200")
            .send()
            .await?;

        if resp.status().is_server_error() {
            anyhow::bail!("Server error: {}", resp.status());
        }

        Ok(resp.text().await?)
    })
    .retry(
        ExponentialBuilder::default()
            .with_min_delay(Duration::from_millis(100))
            .with_max_delay(Duration::from_secs(10))
            .with_max_times(5)
            .with_jitter(),
    )
    .notify(|err, dur| {
        eprintln!("Retrying after {dur:?} due to: {err}");
    })
    .await?;

    println!("Success: {response}");
    Ok(())
}
```

The `.notify()` callback is great for observability — hook it up to your metrics system and you'll know exactly how often retries are happening and how long they're taking.

## Retry Budgets

Individual retry configs are fine for simple services, but in a microservices architecture, you need to think about **retry budgets**. If service A retries 3 times, and it calls service B which also retries 3 times, and B calls service C which retries 3 times — a single failure at C generates up to 27 requests. This is retry amplification, and it can bring down entire clusters.

```rust
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

/// Token-bucket style retry budget
pub struct RetryBudget {
    /// Tokens available for retries
    tokens: AtomicU64,
    /// Maximum tokens
    max_tokens: u64,
    /// Tokens added per second
    refill_rate: f64,
    /// Last refill time
    last_refill: std::sync::Mutex<Instant>,
}

impl RetryBudget {
    pub fn new(max_tokens: u64, refill_rate: f64) -> Arc<Self> {
        Arc::new(Self {
            tokens: AtomicU64::new(max_tokens),
            max_tokens,
            refill_rate,
            last_refill: std::sync::Mutex::new(Instant::now()),
        })
    }

    fn refill(&self) {
        let mut last = self.last_refill.lock().unwrap();
        let elapsed = last.elapsed();
        let new_tokens = (elapsed.as_secs_f64() * self.refill_rate) as u64;

        if new_tokens > 0 {
            let current = self.tokens.load(Ordering::Relaxed);
            let refilled = (current + new_tokens).min(self.max_tokens);
            self.tokens.store(refilled, Ordering::Relaxed);
            *last = Instant::now();
        }
    }

    /// Try to acquire a retry token. Returns false if budget exhausted.
    pub fn try_acquire(&self) -> bool {
        self.refill();

        loop {
            let current = self.tokens.load(Ordering::Relaxed);
            if current == 0 {
                return false;
            }
            match self.tokens.compare_exchange_weak(
                current,
                current - 1,
                Ordering::Relaxed,
                Ordering::Relaxed,
            ) {
                Ok(_) => return true,
                Err(_) => continue,
            }
        }
    }

    /// Record a successful request (deposits a token).
    pub fn record_success(&self) {
        let current = self.tokens.load(Ordering::Relaxed);
        if current < self.max_tokens {
            self.tokens.fetch_add(1, Ordering::Relaxed);
        }
    }
}

// Usage:
// let budget = RetryBudget::new(100, 10.0); // 100 max tokens, 10/sec refill
//
// if budget.try_acquire() {
//     // OK to retry
// } else {
//     // Budget exhausted — fail immediately
//     return Err("retry budget exhausted");
// }
```

The idea: the entire service shares a budget of retry tokens. Successful requests deposit tokens, retries withdraw them. When the budget runs dry, the service stops retrying and fails fast. This prevents retry storms from amplifying failures.

## Idempotency and Retries

One more thing that most retry tutorials skip over — **you should only retry idempotent operations**. Retrying a POST that creates a resource can result in duplicates. Retrying a payment can charge someone twice.

The safest approach is to use an idempotency key:

```rust
use uuid::Uuid;

async fn create_order_with_retry(
    client: &ResilientClient,
    order: &Order,
) -> Result<OrderResponse, RetryError<HttpError>> {
    // Generate once, reuse across retries
    let idempotency_key = Uuid::new_v4().to_string();

    retry_with_backoff(
        &client.backoff,
        |e: &HttpError| e.is_retryable(),
        || {
            let client = client.client.clone();
            let key = idempotency_key.clone();
            let body = serde_json::to_value(order).unwrap();
            async move {
                let response = client
                    .post("https://api.example.com/orders")
                    .header("Idempotency-Key", &key)
                    .json(&body)
                    .send()
                    .await?;
                handle_response(response).await
            }
        },
    )
    .await
}
```

The server uses the idempotency key to deduplicate requests. If it receives the same key twice, it returns the result of the first request instead of creating a second order. Stripe popularized this pattern and it's become the standard for financial APIs.

## What's Next

Retries handle transient failures, but what about a dependency that's consistently failing? Retrying a dead service just wastes resources and adds latency. That's where circuit breakers come in — they detect sustained failures and stop calling the broken service entirely, giving it time to recover. That's next.
