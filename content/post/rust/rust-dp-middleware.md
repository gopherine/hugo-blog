---
title: "Lesson 8: Middleware / Chain of Responsibility — Tower-style"
author: Atharva Pandey
series: "Rust Design Patterns"
lesson: 8
keywords: [Rust, rust, design patterns, middleware, chain of responsibility, tower, service trait, layered architecture]
tags: [Rust tutorial, rust, design-patterns, architecture]
date: '2025-10-16T07:15:00.000Z'
---

If you've built anything with Express, Koa, or ASP.NET, you know middleware. A request comes in, passes through a chain of handlers — logging, auth, rate limiting, CORS — and eventually reaches your application logic. Each handler can modify the request, short-circuit the chain, or pass it along.

The Chain of Responsibility pattern from the GoF book is basically the same thing. The difference is branding.

What makes this pattern interesting in Rust is `tower` — the crate that defines how middleware works across the entire Rust async ecosystem. Axum, Tonic, Hyper — they all use Tower's `Service` trait. Understanding it unlocks the middleware patterns in all of these frameworks.

## The Basic Idea

Before Tower, let's build middleware from scratch to understand the mechanics:

```rust
pub type Request = String;
pub type Response = String;

pub type Handler = Box<dyn Fn(Request) -> Response>;
pub type Middleware = Box<dyn Fn(Request, &Handler) -> Response>;

pub struct Pipeline {
    middlewares: Vec<Middleware>,
    handler: Handler,
}

impl Pipeline {
    pub fn new(handler: impl Fn(Request) -> Response + 'static) -> Self {
        Self {
            middlewares: Vec::new(),
            handler: Box::new(handler),
        }
    }

    pub fn wrap(mut self, middleware: impl Fn(Request, &Handler) -> Response + 'static) -> Self {
        self.middlewares.push(Box::new(middleware));
        self
    }

    pub fn execute(&self, request: Request) -> Response {
        // Build the chain from inside out
        let mut current: Box<dyn Fn(Request) -> Response + '_> =
            Box::new(|req| (self.handler)(req));

        for middleware in self.middlewares.iter().rev() {
            let prev = current;
            current = Box::new(move |req| {
                middleware(req, &prev)
            });
        }

        current(request)
    }
}

fn main() {
    let pipeline = Pipeline::new(|req| {
        format!("Handled: {}", req)
    })
    .wrap(|req, next| {
        println!("[LOG] Incoming: {}", req);
        let resp = next(req);
        println!("[LOG] Outgoing: {}", resp);
        resp
    })
    .wrap(|req, next| {
        if req.contains("blocked") {
            return "403 Forbidden".to_string();
        }
        next(req)
    });

    println!("{}", pipeline.execute("hello".into()));
    println!("{}", pipeline.execute("blocked-request".into()));
}
```

This is the essence: each middleware gets the request and a reference to the next handler in the chain. It can inspect, modify, short-circuit, or delegate.

## The Tower Service Trait

Tower takes this concept and makes it generic, async, and composable. Here's the core trait, simplified:

```rust
pub trait Service<Request> {
    type Response;
    type Error;

    fn call(&mut self, req: Request) -> Result<Self::Response, Self::Error>;
}
```

The real Tower trait returns a `Future` and has a `poll_ready` method for backpressure, but this captures the essential shape. A `Service` takes a request type and produces a response or error. That's it.

Let's implement it:

```rust
// Our request/response types
#[derive(Debug, Clone)]
pub struct HttpRequest {
    pub method: String,
    pub path: String,
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
}

#[derive(Debug)]
pub struct HttpResponse {
    pub status: u16,
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
}

impl HttpResponse {
    pub fn ok(body: impl Into<Vec<u8>>) -> Self {
        Self {
            status: 200,
            headers: vec![],
            body: body.into(),
        }
    }

    pub fn error(status: u16, message: &str) -> Self {
        Self {
            status,
            headers: vec![],
            body: message.as_bytes().to_vec(),
        }
    }
}

// A concrete service — our application handler
pub struct AppService;

impl Service<HttpRequest> for AppService {
    type Response = HttpResponse;
    type Error = std::convert::Infallible;

    fn call(&mut self, req: HttpRequest) -> Result<HttpResponse, Self::Error> {
        Ok(HttpResponse::ok(format!("Hello from {}", req.path)))
    }
}
```

## Building Middleware Layers

A Tower-style middleware wraps an inner service:

```rust
pub struct LoggingMiddleware<S> {
    inner: S,
    prefix: String,
}

impl<S> LoggingMiddleware<S> {
    pub fn new(inner: S, prefix: impl Into<String>) -> Self {
        Self {
            inner,
            prefix: prefix.into(),
        }
    }
}

impl<S> Service<HttpRequest> for LoggingMiddleware<S>
where
    S: Service<HttpRequest, Response = HttpResponse>,
    S::Error: std::fmt::Debug,
{
    type Response = HttpResponse;
    type Error = S::Error;

    fn call(&mut self, req: HttpRequest) -> Result<HttpResponse, Self::Error> {
        let start = std::time::Instant::now();
        println!("[{}] {} {}", self.prefix, req.method, req.path);

        let response = self.inner.call(req)?;

        println!(
            "[{}] {} in {:?}",
            self.prefix,
            response.status,
            start.elapsed()
        );

        Ok(response)
    }
}
```

Authentication middleware that can short-circuit:

```rust
pub struct AuthMiddleware<S> {
    inner: S,
    api_keys: Vec<String>,
}

impl<S> AuthMiddleware<S> {
    pub fn new(inner: S, api_keys: Vec<String>) -> Self {
        Self { inner, api_keys }
    }

    fn extract_api_key(req: &HttpRequest) -> Option<&str> {
        req.headers
            .iter()
            .find(|(k, _)| k.to_lowercase() == "authorization")
            .map(|(_, v)| v.as_str())
    }
}

impl<S> Service<HttpRequest> for AuthMiddleware<S>
where
    S: Service<HttpRequest, Response = HttpResponse>,
    S::Error: std::fmt::Debug,
{
    type Response = HttpResponse;
    type Error = S::Error;

    fn call(&mut self, req: HttpRequest) -> Result<HttpResponse, Self::Error> {
        match Self::extract_api_key(&req) {
            Some(key) if self.api_keys.iter().any(|k| k == key) => {
                self.inner.call(req)
            }
            Some(_) => Ok(HttpResponse::error(403, "Invalid API key")),
            None => Ok(HttpResponse::error(401, "Missing Authorization header")),
        }
    }
}
```

Rate limiting:

```rust
use std::collections::HashMap;
use std::time::{Duration, Instant};

pub struct RateLimitMiddleware<S> {
    inner: S,
    requests: HashMap<String, Vec<Instant>>,
    max_requests: usize,
    window: Duration,
}

impl<S> RateLimitMiddleware<S> {
    pub fn new(inner: S, max_requests: usize, window: Duration) -> Self {
        Self {
            inner,
            requests: HashMap::new(),
            max_requests,
            window,
        }
    }

    fn client_id(req: &HttpRequest) -> String {
        req.headers
            .iter()
            .find(|(k, _)| k == "X-Client-Id")
            .map(|(_, v)| v.clone())
            .unwrap_or_else(|| "anonymous".into())
    }
}

impl<S> Service<HttpRequest> for RateLimitMiddleware<S>
where
    S: Service<HttpRequest, Response = HttpResponse>,
    S::Error: std::fmt::Debug,
{
    type Response = HttpResponse;
    type Error = S::Error;

    fn call(&mut self, req: HttpRequest) -> Result<HttpResponse, Self::Error> {
        let client = Self::client_id(&req);
        let now = Instant::now();
        let cutoff = now - self.window;

        let timestamps = self.requests.entry(client).or_default();
        timestamps.retain(|t| *t > cutoff);

        if timestamps.len() >= self.max_requests {
            return Ok(HttpResponse::error(429, "Rate limit exceeded"));
        }

        timestamps.push(now);
        self.inner.call(req)
    }
}
```

## Composing the Stack

Now stack them:

```rust
fn main() {
    let app = AppService;

    // Wrap inside-out: rate limit -> auth -> logging -> app
    let app = LoggingMiddleware::new(app, "HTTP");
    let app = AuthMiddleware::new(
        app,
        vec!["secret-key-123".into(), "dev-key-456".into()],
    );
    let mut app = RateLimitMiddleware::new(
        app,
        100,
        Duration::from_secs(60),
    );

    // Requests flow: RateLimit -> Auth -> Logging -> App
    let req = HttpRequest {
        method: "GET".into(),
        path: "/api/users".into(),
        headers: vec![
            ("Authorization".into(), "secret-key-123".into()),
            ("X-Client-Id".into(), "client-42".into()),
        ],
        body: vec![],
    };

    let response = app.call(req).unwrap();
    println!("Status: {}", response.status);
}
```

The type of `app` is `RateLimitMiddleware<AuthMiddleware<LoggingMiddleware<AppService>>>`. Verbose? Yes. But the compiler knows the entire chain at compile time. Every call through the middleware stack can be inlined. There's no `Vec<Box<dyn Middleware>>` — it's all static dispatch.

## The Layer Pattern

In Tower, you don't construct middleware directly — you use `Layer`, which is essentially a factory for middleware:

```rust
pub trait Layer<S> {
    type Service;
    fn layer(&self, inner: S) -> Self::Service;
}

pub struct LoggingLayer {
    prefix: String,
}

impl LoggingLayer {
    pub fn new(prefix: impl Into<String>) -> Self {
        Self {
            prefix: prefix.into(),
        }
    }
}

impl<S> Layer<S> for LoggingLayer {
    type Service = LoggingMiddleware<S>;

    fn layer(&self, inner: S) -> LoggingMiddleware<S> {
        LoggingMiddleware::new(inner, self.prefix.clone())
    }
}

pub struct AuthLayer {
    api_keys: Vec<String>,
}

impl<S> Layer<S> for AuthLayer {
    type Service = AuthMiddleware<S>;

    fn layer(&self, inner: S) -> AuthMiddleware<S> {
        AuthMiddleware::new(inner, self.api_keys.clone())
    }
}
```

Layers let you define middleware *configuration* separately from *application*. You can store layers, pass them around, and apply them to different services:

```rust
fn build_stack(app: AppService) -> impl Service<HttpRequest, Response = HttpResponse> {
    let logging = LoggingLayer::new("API");
    let auth = AuthLayer {
        api_keys: vec!["key-1".into()],
    };

    let app = logging.layer(app);
    let app = auth.layer(app);
    app
}
```

This is exactly how Axum's `Router::layer()` works under the hood. When you call `.layer(CorsLayer::new())`, you're adding a Tower Layer that wraps your entire router in a CORS middleware service.

## Error Handling in Middleware

One tricky part — how do you handle different error types across middleware layers? The clean approach is a common error type:

```rust
#[derive(Debug)]
pub enum MiddlewareError {
    Auth(String),
    RateLimit,
    Internal(String),
    Service(Box<dyn std::error::Error>),
}

impl<S> Service<HttpRequest> for AuthMiddleware<S>
where
    S: Service<HttpRequest, Response = HttpResponse, Error = MiddlewareError>,
{
    type Response = HttpResponse;
    type Error = MiddlewareError;

    fn call(&mut self, req: HttpRequest) -> Result<HttpResponse, Self::Error> {
        match Self::extract_api_key(&req) {
            Some(key) if self.api_keys.iter().any(|k| k == key) => {
                self.inner.call(req)
            }
            _ => Err(MiddlewareError::Auth("unauthorized".into())),
        }
    }
}
```

Or — and this is what I prefer — handle errors *as responses*. HTTP middleware should return HTTP responses, not Rust errors. A 401 isn't an error in the middleware sense; it's a valid response that short-circuits the chain. Reserve `Result::Err` for actual infrastructure failures.

## Why This Beats Traditional Chain of Responsibility

The GoF Chain of Responsibility has handlers that either process a request or pass it to the next handler. Tower's Service model is strictly better because:

1. **Middleware sees both request AND response.** Logging, timing, and response transformation are natural.
2. **The chain is type-safe.** You can't accidentally compose incompatible services.
3. **Backpressure is built in.** `poll_ready` lets services signal when they're overloaded.
4. **It composes.** A stack of middleware is itself a `Service`, so you can nest and reuse stacks.

If you're building anything with Axum, Hyper, or Tonic, you're already using this pattern. Understanding the `Service` and `Layer` traits means you can write custom middleware for any framework in the Tower ecosystem. One pattern, entire ecosystem.
