---
title: "Lesson 9: Rate Limiting and Throttling — Protecting your service"
author: Atharva Pandey
series: "Rust Web & API Development"
lesson: 9
keywords: [Rust, rust, web, API, axum, rate limiting, throttling, token bucket, sliding window, tower]
tags: [Rust tutorial, rust, web, api]
date: '2024-10-18T13:10:00.000Z'
---

We launched a public API without rate limiting. Within a week, a single user was making 200 requests per second — not maliciously, just a badly written script with no backoff. Their traffic consumed 40% of our database connections and degraded performance for everyone else. We added rate limiting, their requests started getting 429s, they fixed their script, and everyone was happy. Should've been there from day one.

## Why Rate Limit

Three reasons, in order of importance:

1. **Availability.** One abusive client shouldn't degrade the experience for everyone else. Rate limiting is the most basic form of fairness.
2. **Security.** Brute-force attacks on login endpoints, credential stuffing, enumeration attacks — all rely on high request volumes. Rate limiting makes them impractical.
3. **Cost.** Every request costs CPU, memory, database connections, and potentially money (if you're calling paid APIs downstream). Unbounded request rates mean unbounded costs.

## Token Bucket Algorithm

The token bucket is the most common rate limiting algorithm. Picture a bucket that holds N tokens. Every request consumes one token. Tokens refill at a fixed rate. When the bucket is empty, requests are rejected until tokens refill.

```rust
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::Mutex;

#[derive(Clone)]
pub struct TokenBucket {
    inner: Arc<Mutex<TokenBucketInner>>,
}

struct TokenBucketInner {
    tokens: f64,
    max_tokens: f64,
    refill_rate: f64, // tokens per second
    last_refill: Instant,
}

impl TokenBucket {
    pub fn new(max_tokens: f64, refill_rate: f64) -> Self {
        Self {
            inner: Arc::new(Mutex::new(TokenBucketInner {
                tokens: max_tokens,
                max_tokens,
                refill_rate,
                last_refill: Instant::now(),
            })),
        }
    }

    pub async fn try_acquire(&self) -> bool {
        let mut inner = self.inner.lock().await;
        let now = Instant::now();
        let elapsed = now.duration_since(inner.last_refill).as_secs_f64();

        // Refill tokens based on elapsed time
        inner.tokens = (inner.tokens + elapsed * inner.refill_rate).min(inner.max_tokens);
        inner.last_refill = now;

        if inner.tokens >= 1.0 {
            inner.tokens -= 1.0;
            true
        } else {
            false
        }
    }

    pub async fn tokens_remaining(&self) -> f64 {
        let inner = self.inner.lock().await;
        inner.tokens
    }
}
```

This allows bursts up to `max_tokens` and sustains `refill_rate` requests per second. A bucket with `max_tokens=10` and `refill_rate=2` allows a burst of 10 requests, then sustains 2 per second.

## Per-Client Rate Limiting

You don't want a single global bucket — that would mean all clients share the same limit. You want per-client buckets, keyed by IP address, API key, or user ID.

```rust
use std::collections::HashMap;
use std::net::IpAddr;

#[derive(Clone)]
pub struct RateLimiter {
    buckets: Arc<Mutex<HashMap<String, TokenBucket>>>,
    max_tokens: f64,
    refill_rate: f64,
}

impl RateLimiter {
    pub fn new(max_tokens: f64, refill_rate: f64) -> Self {
        Self {
            buckets: Arc::new(Mutex::new(HashMap::new())),
            max_tokens,
            refill_rate,
        }
    }

    pub async fn check(&self, key: &str) -> RateLimitResult {
        let mut buckets = self.buckets.lock().await;

        let bucket = buckets
            .entry(key.to_string())
            .or_insert_with(|| TokenBucket::new(self.max_tokens, self.refill_rate));

        if bucket.try_acquire().await {
            let remaining = bucket.tokens_remaining().await;
            RateLimitResult::Allowed { remaining: remaining as u64 }
        } else {
            RateLimitResult::Limited
        }
    }
}

pub enum RateLimitResult {
    Allowed { remaining: u64 },
    Limited,
}
```

### Cleaning Up Stale Buckets

Without cleanup, the `HashMap` grows forever as new clients arrive. Run a periodic cleanup task:

```rust
impl RateLimiter {
    pub fn start_cleanup(self: Arc<Self>, interval: Duration) {
        tokio::spawn(async move {
            let mut ticker = tokio::time::interval(interval);
            loop {
                ticker.tick().await;
                let mut buckets = self.buckets.lock().await;
                let before = buckets.len();
                // Remove buckets that are full (haven't been used recently)
                buckets.retain(|_, bucket| {
                    // Keep buckets that have been used recently
                    // A full bucket means it hasn't been used since last refill
                    let inner = bucket.inner.try_lock();
                    match inner {
                        Ok(guard) => guard.tokens < guard.max_tokens,
                        Err(_) => true, // Keep if locked (in use)
                    }
                });
                let removed = before - buckets.len();
                if removed > 0 {
                    tracing::debug!("Cleaned up {} stale rate limit buckets", removed);
                }
            }
        });
    }
}
```

## Rate Limiting Middleware

Wire the rate limiter into an Axum middleware:

```rust
use axum::{
    extract::{ConnectInfo, State},
    http::{Request, StatusCode, HeaderValue},
    middleware::Next,
    response::{IntoResponse, Response},
    Json,
};
use std::net::SocketAddr;

pub async fn rate_limit_middleware(
    State(limiter): State<Arc<RateLimiter>>,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    request: Request<axum::body::Body>,
    next: Next,
) -> Result<Response, Response> {
    let key = addr.ip().to_string();

    match limiter.check(&key).await {
        RateLimitResult::Allowed { remaining } => {
            let mut response = next.run(request).await;

            // Add rate limit headers
            let headers = response.headers_mut();
            headers.insert(
                "X-RateLimit-Remaining",
                HeaderValue::from_str(&remaining.to_string()).unwrap(),
            );
            headers.insert(
                "X-RateLimit-Limit",
                HeaderValue::from_str(&limiter.max_tokens.to_string()).unwrap(),
            );

            Ok(response)
        }
        RateLimitResult::Limited => {
            let body = Json(serde_json::json!({
                "error": "rate_limited",
                "message": "Too many requests. Please slow down.",
            }));

            let mut response = (StatusCode::TOO_MANY_REQUESTS, body).into_response();
            response.headers_mut().insert(
                "Retry-After",
                HeaderValue::from_static("1"),
            );

            Err(response)
        }
    }
}
```

To use `ConnectInfo`, you need to enable it when serving:

```rust
use axum::extract::connect_info::ConnectInfo;

let app = Router::new()
    .route("/api/users", get(list_users))
    .layer(axum::middleware::from_fn_with_state(
        limiter.clone(),
        rate_limit_middleware,
    ))
    .with_state(state);

// Important: use into_make_service_with_connect_info
let listener = tokio::net::TcpListener::bind("0.0.0.0:3000").await.unwrap();
axum::serve(
    listener,
    app.into_make_service_with_connect_info::<SocketAddr>(),
)
.await
.unwrap();
```

## Tiered Rate Limits

Different endpoints need different limits. Login should be tightly limited (prevent brute force). A read-only list endpoint can be more generous.

```rust
use std::collections::HashMap as StdHashMap;

#[derive(Clone)]
struct TieredRateLimiter {
    tiers: StdHashMap<String, Arc<RateLimiter>>,
    default: Arc<RateLimiter>,
}

impl TieredRateLimiter {
    fn new() -> Self {
        let mut tiers = StdHashMap::new();

        // Strict: 5 requests per minute (login, password reset)
        tiers.insert(
            "auth".to_string(),
            Arc::new(RateLimiter::new(5.0, 5.0 / 60.0)),
        );

        // Normal: 60 requests per minute
        tiers.insert(
            "api".to_string(),
            Arc::new(RateLimiter::new(60.0, 1.0)),
        );

        // Generous: 300 requests per minute (read-only endpoints)
        tiers.insert(
            "read".to_string(),
            Arc::new(RateLimiter::new(300.0, 5.0)),
        );

        Self {
            tiers,
            default: Arc::new(RateLimiter::new(60.0, 1.0)),
        }
    }

    fn get_limiter(&self, tier: &str) -> Arc<RateLimiter> {
        self.tiers
            .get(tier)
            .cloned()
            .unwrap_or_else(|| self.default.clone())
    }
}
```

Apply different tiers to different route groups:

```rust
fn rate_limit_for_tier(
    tier: &'static str,
) -> impl Fn(State<TieredRateLimiter>, ConnectInfo<SocketAddr>, Request<axum::body::Body>, Next)
    -> std::pin::Pin<Box<dyn std::future::Future<Output = Result<Response, Response>> + Send>>
    + Clone
{
    move |State(limiter): State<TieredRateLimiter>,
          ConnectInfo(addr): ConnectInfo<SocketAddr>,
          request: Request<axum::body::Body>,
          next: Next| {
        let limiter = limiter.get_limiter(tier);
        Box::pin(async move {
            let key = addr.ip().to_string();
            match limiter.check(&key).await {
                RateLimitResult::Allowed { remaining } => {
                    let mut response = next.run(request).await;
                    response.headers_mut().insert(
                        "X-RateLimit-Remaining",
                        HeaderValue::from_str(&remaining.to_string()).unwrap(),
                    );
                    Ok(response)
                }
                RateLimitResult::Limited => {
                    Err((StatusCode::TOO_MANY_REQUESTS, "Too many requests").into_response())
                }
            }
        })
    }
}
```

Or more practically, just create separate middleware functions:

```rust
pub async fn rate_limit_auth(
    State(state): State<AppState>,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    request: Request<axum::body::Body>,
    next: Next,
) -> Result<Response, Response> {
    check_rate_limit(&state.auth_limiter, &addr.ip().to_string(), request, next).await
}

pub async fn rate_limit_api(
    State(state): State<AppState>,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    request: Request<axum::body::Body>,
    next: Next,
) -> Result<Response, Response> {
    check_rate_limit(&state.api_limiter, &addr.ip().to_string(), request, next).await
}

async fn check_rate_limit(
    limiter: &RateLimiter,
    key: &str,
    request: Request<axum::body::Body>,
    next: Next,
) -> Result<Response, Response> {
    match limiter.check(key).await {
        RateLimitResult::Allowed { remaining } => {
            let mut response = next.run(request).await;
            response.headers_mut().insert(
                "X-RateLimit-Remaining",
                HeaderValue::from_str(&remaining.to_string()).unwrap(),
            );
            Ok(response)
        }
        RateLimitResult::Limited => {
            let body = Json(serde_json::json!({
                "error": "rate_limited",
                "message": "Too many requests",
            }));
            let mut resp = (StatusCode::TOO_MANY_REQUESTS, body).into_response();
            resp.headers_mut().insert("Retry-After", HeaderValue::from_static("1"));
            Err(resp)
        }
    }
}

// Wire up
let auth_routes = Router::new()
    .route("/login", post(login))
    .route("/register", post(register))
    .layer(middleware::from_fn_with_state(state.clone(), rate_limit_auth));

let api_routes = Router::new()
    .route("/users", get(list_users))
    .route("/posts", get(list_posts))
    .layer(middleware::from_fn_with_state(state.clone(), rate_limit_api));
```

## Using tower-governor for Production

For production use, consider `tower-governor` — a Tower middleware that implements the Governor rate limiting library. It handles all the edge cases (cleanup, sliding windows, key extraction) that you'd otherwise build yourself.

```toml
[dependencies]
tower_governor = "0.4"
governor = "0.6"
```

```rust
use tower_governor::{
    governor::GovernorConfigBuilder,
    GovernorLayer,
};

let governor_conf = Arc::new(
    GovernorConfigBuilder::default()
        .per_second(2) // 2 requests per second
        .burst_size(10) // burst up to 10
        .finish()
        .unwrap(),
);

let app = Router::new()
    .route("/api/users", get(list_users))
    .layer(GovernorLayer {
        config: governor_conf,
    });
```

`tower-governor` automatically extracts client IPs, handles cleanup, and returns proper 429 responses with `Retry-After` headers. For most applications, this is all you need.

## Distributed Rate Limiting with Redis

In-memory rate limiting works for single-instance deployments. When you have multiple replicas behind a load balancer, each instance tracks limits independently — a client could get 100 requests per second by hitting 10 replicas at 10 req/s each.

For multi-instance deployments, use Redis:

```rust
use redis::AsyncCommands;

pub struct RedisRateLimiter {
    redis: redis::Client,
    max_requests: u64,
    window_seconds: u64,
}

impl RedisRateLimiter {
    pub fn new(redis_url: &str, max_requests: u64, window_seconds: u64) -> Self {
        Self {
            redis: redis::Client::open(redis_url).unwrap(),
            max_requests,
            window_seconds,
        }
    }

    pub async fn check(&self, key: &str) -> Result<RateLimitResult, AppError> {
        let mut conn = self.redis.get_multiplexed_async_connection().await
            .map_err(|_| AppError::internal("Redis connection failed"))?;

        let redis_key = format!("rate_limit:{}", key);

        // Atomic increment and expire
        let count: u64 = redis::pipe()
            .atomic()
            .incr(&redis_key, 1u64)
            .expire(&redis_key, self.window_seconds as i64)
            .ignore()
            .query_async(&mut conn)
            .await
            .map_err(|_| AppError::internal("Redis rate limit check failed"))?;

        if count <= self.max_requests {
            Ok(RateLimitResult::Allowed {
                remaining: self.max_requests - count,
            })
        } else {
            Ok(RateLimitResult::Limited)
        }
    }
}
```

This uses a fixed window — a simple counter that resets every N seconds. It's not perfectly smooth (a burst at the end of one window and the start of the next can temporarily exceed the limit), but it's simple and fast. For stricter requirements, implement a sliding window using Redis sorted sets.

## Rate Limit Headers

Follow the draft IETF standard for rate limit headers:

```
X-RateLimit-Limit: 100        // Max requests in window
X-RateLimit-Remaining: 42     // Remaining requests
X-RateLimit-Reset: 1697654400 // Unix timestamp when the window resets
Retry-After: 30               // Seconds to wait (only on 429)
```

These headers let well-behaved clients self-throttle before hitting limits. Good API design is about cooperation, not just enforcement.

## What to Rate Limit

Not everything needs the same treatment:

| Endpoint | Limit | Why |
|----------|-------|-----|
| `POST /login` | 5/min | Brute force prevention |
| `POST /register` | 3/min | Spam prevention |
| `POST /forgot-password` | 3/hour | Email bombing prevention |
| `GET /api/*` | 100/min | General API protection |
| `POST /api/*` | 30/min | Write operations cost more |
| `GET /health` | No limit | Load balancers need this |

Rate limiting is a balance. Too strict and legitimate users get frustrated. Too loose and it's meaningless. Start conservative, monitor your 429 rate, and adjust. If nobody ever gets a 429, your limits are too high. If 5% of requests get 429s, something is probably misconfigured.

Next: OpenAPI documentation — making your API self-documenting.
