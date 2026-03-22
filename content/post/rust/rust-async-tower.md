---
title: "Lesson 14: Tower — The service middleware pattern"
author: Atharva Pandey
series: "Async Rust Masterclass"
lesson: 14
keywords: [Rust, rust, async, tokio, tower, service, middleware, layer, retry, timeout]
tags: [Rust tutorial, rust, async, tokio]
date: '2025-01-31T19:18:37.000Z'
---

I avoided Tower for months. The trait bounds looked terrifying, the documentation assumed you already knew what you were doing, and I couldn't figure out why I'd want it when I could just write functions. Then I needed to add logging, retries, timeouts, and rate limiting to every HTTP handler in a service with 40 endpoints.

Writing those as middleware that composes? That's Tower's whole thing. And once it clicks, you'll never build a service without it.

## What Is Tower?

Tower is a library that defines a standard interface for request/response services. Think of it as the async equivalent of Unix pipes — small, composable pieces that you stack together.

The core is one trait:

```rust
pub trait Service<Request> {
    type Response;
    type Error;
    type Future: Future<Output = Result<Self::Response, Self::Error>>;

    fn poll_ready(&mut self, cx: &mut Context<'_>) -> Poll<Result<(), Self::Error>>;
    fn call(&mut self, req: Request) -> Self::Future;
}
```

A `Service` takes a `Request` and returns a `Future` that resolves to `Result<Response, Error>`. That's it.

`poll_ready` is for backpressure — it lets the service signal "I'm not ready to handle a request right now" (e.g., the rate limiter is full, the connection pool is exhausted).

## Setup

```toml
[dependencies]
tower = { version = "0.5", features = ["full"] }
tokio = { version = "1", features = ["full"] }
```

## Your First Service

```rust
use std::future::Future;
use std::pin::Pin;
use std::task::{Context, Poll};
use tower::Service;

#[derive(Clone)]
struct EchoService;

impl Service<String> for EchoService {
    type Response = String;
    type Error = std::convert::Infallible;
    type Future = Pin<Box<dyn Future<Output = Result<String, Self::Error>> + Send>>;

    fn poll_ready(&mut self, _cx: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
        Poll::Ready(Ok(())) // Always ready
    }

    fn call(&mut self, req: String) -> Self::Future {
        Box::pin(async move {
            Ok(format!("Echo: {req}"))
        })
    }
}

#[tokio::main]
async fn main() {
    let mut svc = EchoService;
    let response = svc.call("hello".to_string()).await.unwrap();
    println!("{response}");
}
```

That's a lot of boilerplate for an echo function. Tower provides `service_fn` for simple cases:

```rust
use tower::{service_fn, ServiceExt};

#[tokio::main]
async fn main() {
    let mut svc = service_fn(|req: String| async move {
        Ok::<_, std::convert::Infallible>(format!("Echo: {req}"))
    });

    let response = svc.call("hello".to_string()).await.unwrap();
    println!("{response}");
}
```

Much better.

## Layers — Middleware That Composes

A `Layer` wraps one service in another, adding behavior. This is where Tower gets powerful:

```rust
use std::future::Future;
use std::pin::Pin;
use std::task::{Context, Poll};
use std::time::Instant;
use tower::{Layer, Service};

// A middleware that logs request/response timing
#[derive(Clone)]
struct LoggingLayer;

impl<S> Layer<S> for LoggingLayer {
    type Service = LoggingService<S>;

    fn layer(&self, inner: S) -> Self::Service {
        LoggingService { inner }
    }
}

#[derive(Clone)]
struct LoggingService<S> {
    inner: S,
}

impl<S, Request> Service<Request> for LoggingService<S>
where
    S: Service<Request>,
    S::Future: Send + 'static,
    S::Response: std::fmt::Debug + Send + 'static,
    S::Error: std::fmt::Debug + Send + 'static,
    Request: std::fmt::Debug,
{
    type Response = S::Response;
    type Error = S::Error;
    type Future = Pin<Box<dyn Future<Output = Result<S::Response, S::Error>> + Send>>;

    fn poll_ready(&mut self, cx: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
        self.inner.poll_ready(cx)
    }

    fn call(&mut self, req: Request) -> Self::Future {
        println!(">> Request: {:?}", req);
        let start = Instant::now();
        let fut = self.inner.call(req);

        Box::pin(async move {
            let result = fut.await;
            let elapsed = start.elapsed();
            match &result {
                Ok(resp) => println!("<< Response: {:?} ({:?})", resp, elapsed),
                Err(err) => println!("<< Error: {:?} ({:?})", err, elapsed),
            }
            result
        })
    }
}

use tower::service_fn;

#[tokio::main]
async fn main() {
    let svc = service_fn(|req: String| async move {
        tokio::time::sleep(tokio::time::Duration::from_millis(50)).await;
        Ok::<_, std::convert::Infallible>(format!("Processed: {req}"))
    });

    let layer = LoggingLayer;
    let mut svc = layer.layer(svc);

    let resp = svc.call("hello".to_string()).await.unwrap();
    println!("Final: {resp}");
}
```

## Stacking Layers with ServiceBuilder

The real magic — composing multiple middleware:

```rust
use tower::{ServiceBuilder, service_fn, ServiceExt};
use std::time::Duration;

#[tokio::main]
async fn main() {
    let svc = service_fn(|req: String| async move {
        tokio::time::sleep(Duration::from_millis(50)).await;
        Ok::<_, Box<dyn std::error::Error + Send + Sync>>(format!("Result: {req}"))
    });

    let mut svc = ServiceBuilder::new()
        .timeout(Duration::from_secs(5))  // Timeout layer
        .concurrency_limit(10)             // Max concurrent requests
        .buffer(100)                       // Buffer up to 100 requests
        .service(svc);

    // ServiceExt provides .ready() which awaits poll_ready
    let svc = svc.ready().await.unwrap();
    let resp = svc.call("test".to_string()).await.unwrap();
    println!("{resp}");
}
```

Layers are applied bottom-to-top (the last layer in the builder wraps the inner service first). In the example above, the request flows: timeout → concurrency limit → buffer → your service.

## Built-in Tower Middleware

Tower comes with a bunch of useful middleware:

### Timeout

```rust
use tower::{ServiceBuilder, service_fn, ServiceExt};
use std::time::Duration;

#[tokio::main]
async fn main() {
    let svc = service_fn(|_req: ()| async {
        tokio::time::sleep(Duration::from_secs(10)).await;
        Ok::<_, std::convert::Infallible>("done")
    });

    let mut svc = ServiceBuilder::new()
        .timeout(Duration::from_secs(1))
        .service(svc);

    match svc.ready().await.unwrap().call(()).await {
        Ok(resp) => println!("Got: {resp}"),
        Err(e) => println!("Timed out: {e}"),
    }
}
```

### Rate Limiting

```rust
use tower::{ServiceBuilder, service_fn, ServiceExt};
use std::time::Duration;

#[tokio::main]
async fn main() {
    let svc = service_fn(|req: u32| async move {
        Ok::<_, std::convert::Infallible>(req * 2)
    });

    let mut svc = ServiceBuilder::new()
        .rate_limit(5, Duration::from_secs(1))  // 5 requests per second
        .service(svc);

    let start = std::time::Instant::now();
    for i in 0..10 {
        let svc = svc.ready().await.unwrap();
        let resp = svc.call(i).await.unwrap();
        println!("[{:.1}s] {i} -> {resp}", start.elapsed().as_secs_f64());
    }
}
```

### Retry

```rust
use tower::retry::Policy;
use std::future;

#[derive(Clone)]
struct RetryPolicy {
    max_retries: u32,
}

impl<E: std::fmt::Debug> Policy<String, String, E> for RetryPolicy {
    type Future = future::Ready<()>;

    fn retry(
        &mut self,
        _req: &mut String,
        result: &mut Result<String, E>,
    ) -> Option<Self::Future> {
        match result {
            Ok(_) => None, // Success — don't retry
            Err(e) => {
                if self.max_retries > 0 {
                    self.max_retries -= 1;
                    println!("Retrying... ({} left) error: {:?}", self.max_retries, e);
                    Some(future::ready(()))
                } else {
                    None // No more retries
                }
            }
        }
    }

    fn clone_request(&mut self, req: &String) -> Option<String> {
        Some(req.clone())
    }
}
```

## Building a Real Middleware: Request ID

Here's a practical middleware that adds a request ID to every request:

```rust
use std::future::Future;
use std::pin::Pin;
use std::sync::atomic::{AtomicU64, Ordering};
use std::task::{Context, Poll};
use tower::{Layer, Service};

static REQUEST_COUNTER: AtomicU64 = AtomicU64::new(1);

#[derive(Debug)]
struct TracedRequest<T> {
    request_id: u64,
    inner: T,
}

#[derive(Clone)]
struct RequestIdLayer;

impl<S> Layer<S> for RequestIdLayer {
    type Service = RequestIdService<S>;

    fn layer(&self, inner: S) -> Self::Service {
        RequestIdService { inner }
    }
}

#[derive(Clone)]
struct RequestIdService<S> {
    inner: S,
}

impl<S, Request> Service<Request> for RequestIdService<S>
where
    S: Service<TracedRequest<Request>>,
    S::Future: Send + 'static,
{
    type Response = S::Response;
    type Error = S::Error;
    type Future = S::Future;

    fn poll_ready(&mut self, cx: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
        self.inner.poll_ready(cx)
    }

    fn call(&mut self, req: Request) -> Self::Future {
        let request_id = REQUEST_COUNTER.fetch_add(1, Ordering::Relaxed);
        let traced = TracedRequest {
            request_id,
            inner: req,
        };
        self.inner.call(traced)
    }
}

use tower::service_fn;

#[tokio::main]
async fn main() {
    let svc = service_fn(|req: TracedRequest<String>| async move {
        println!("[req-{}] Processing: {}", req.request_id, req.inner);
        Ok::<_, std::convert::Infallible>(format!("Done (req-{})", req.request_id))
    });

    let layer = RequestIdLayer;
    let mut svc = layer.layer(svc);

    for msg in ["hello", "world", "tower"] {
        let resp = svc.call(msg.to_string()).await.unwrap();
        println!("Response: {resp}");
    }
}
```

## Tower with Axum

Tower really shines with web frameworks like Axum (which is built on Tower):

```rust
use axum::{Router, routing::get, middleware, extract::Request, response::Response};
use std::time::Instant;
use tower::ServiceBuilder;
use std::time::Duration;

async fn handler() -> &'static str {
    "Hello, Tower!"
}

async fn logging_middleware(
    req: Request,
    next: middleware::Next,
) -> Response {
    let method = req.method().clone();
    let uri = req.uri().clone();
    let start = Instant::now();

    let response = next.run(req).await;

    println!("{method} {uri} -> {} ({:?})",
        response.status(), start.elapsed());

    response
}

// This shows the concept — you'd run this with a real Axum server
fn build_router() -> Router {
    Router::new()
        .route("/", get(handler))
        .layer(
            ServiceBuilder::new()
                .timeout(Duration::from_secs(10))
                .concurrency_limit(100)
                .layer(middleware::from_fn(logging_middleware))
        )
}
```

## When to Reach for Tower

Tower adds complexity. Here's when it's worth it:

**Use Tower when:**
- You have cross-cutting concerns (logging, metrics, auth, retries) across many services
- You're building infrastructure (proxies, load balancers, API gateways)
- You want composable, testable middleware
- You're using Axum, Tonic, or other Tower-based frameworks

**Skip Tower when:**
- You have one or two endpoints
- Your middleware needs are simple (just use function wrappers)
- The trait bounds are fighting you more than helping

Tower isn't for every project, but for services with real middleware needs, it's indispensable. The composability pays for itself the moment you need to add rate limiting to 30 endpoints and realize it's a one-line change.

Next lesson: backpressure — the art of saying "slow down" before your system falls over.
