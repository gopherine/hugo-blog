---
title: "Lesson 3: Middleware with Tower Layers — The composable middleware pattern"
author: Atharva Pandey
series: "Rust Web & API Development"
lesson: 3
keywords: [Rust, rust, web, API, axum, tower, middleware, layers, service, logging, cors]
tags: [Rust tutorial, rust, web, api]
date: '2024-10-05T08:30:00.000Z'
---

I once inherited a Node.js codebase with 23 Express middleware functions chained together. Half of them silently swallowed errors, three of them conflicted with each other, and nobody knew what order they ran in. When I started building services in Axum, the Tower middleware model felt like a revelation — not because it's easier (it's actually harder at first), but because it makes middleware *composable* and *type-checked*. You can't silently swallow errors when the type system forces you to handle them.

## What Tower Actually Is

Tower isn't an Axum thing. It's a standalone library that defines two core traits: `Service` and `Layer`. Everything in Axum's middleware stack is built on these traits, but so is Tonic (gRPC), Hyper (HTTP client/server), and dozens of other Rust networking libraries.

The mental model is simple. A `Service` takes a request and returns a response. A `Layer` wraps a `Service` to add behavior — logging, authentication, timeouts, whatever. Layers compose: you stack them, and each one wraps the one below it.

```
Request → [Layer 3] → [Layer 2] → [Layer 1] → Handler
                                                  ↓
Response ← [Layer 3] ← [Layer 2] ← [Layer 1] ← Result
```

The key insight: each layer sees both the request (going in) and the response (coming out). A logging layer can start a timer when the request arrives and log the duration when the response returns. An auth layer can short-circuit the chain by returning a 401 before the handler ever runs.

## Using Built-in Tower-HTTP Layers

Before writing custom middleware, know what's already available. The `tower-http` crate ships with production-ready layers for common needs.

```toml
[dependencies]
tower = "0.4"
tower-http = { version = "0.5", features = [
    "cors",
    "trace",
    "timeout",
    "limit",
    "compression-gzip",
    "request-id",
    "set-header",
] }
```

### Tracing / Logging

```rust
use tower_http::trace::TraceLayer;
use tracing::Level;

let app = Router::new()
    .route("/api/users", get(list_users))
    .layer(
        TraceLayer::new_for_http()
            .make_span_with(|request: &axum::http::Request<_>| {
                tracing::span!(
                    Level::INFO,
                    "http_request",
                    method = %request.method(),
                    uri = %request.uri(),
                )
            })
            .on_response(|response: &axum::http::Response<_>, latency: std::time::Duration, _span: &tracing::Span| {
                tracing::info!(
                    status = response.status().as_u16(),
                    latency = ?latency,
                    "response"
                );
            }),
    );
```

This gives you structured logging for every request — method, URI, status code, latency. It hooks into Rust's `tracing` ecosystem, so you get span-based context propagation for free.

### CORS

```rust
use tower_http::cors::{CorsLayer, Any};
use axum::http::{header, Method};

let cors = CorsLayer::new()
    .allow_origin(Any)
    .allow_methods([Method::GET, Method::POST, Method::PUT, Method::DELETE])
    .allow_headers([header::CONTENT_TYPE, header::AUTHORIZATION]);

let app = Router::new()
    .route("/api/users", get(list_users))
    .layer(cors);
```

For production, replace `Any` with specific origins:

```rust
use tower_http::cors::AllowOrigin;

let cors = CorsLayer::new()
    .allow_origin(AllowOrigin::list([
        "https://myapp.com".parse().unwrap(),
        "https://staging.myapp.com".parse().unwrap(),
    ]));
```

### Request Timeout

```rust
use tower_http::timeout::TimeoutLayer;
use std::time::Duration;

let app = Router::new()
    .route("/api/slow", get(slow_handler))
    .layer(TimeoutLayer::new(Duration::from_secs(30)));
```

If the handler doesn't respond within 30 seconds, the client gets a 408 Request Timeout. Simple, effective, prevents runaway requests from eating your server.

### Compression

```rust
use tower_http::compression::CompressionLayer;

let app = Router::new()
    .route("/api/large-data", get(large_data))
    .layer(CompressionLayer::new());
```

Automatically gzip-compresses responses when the client sends `Accept-Encoding: gzip`. Zero work in your handlers.

## Composing Multiple Layers

You'll typically want several layers active at once. Use `ServiceBuilder` to stack them:

```rust
use tower::ServiceBuilder;
use tower_http::{
    cors::CorsLayer,
    trace::TraceLayer,
    timeout::TimeoutLayer,
    compression::CompressionLayer,
};
use std::time::Duration;

let app = Router::new()
    .route("/api/users", get(list_users).post(create_user))
    .layer(
        ServiceBuilder::new()
            .layer(TraceLayer::new_for_http())
            .layer(TimeoutLayer::new(Duration::from_secs(30)))
            .layer(CorsLayer::permissive())
            .layer(CompressionLayer::new())
    );
```

**Order matters.** Layers are applied bottom-up in `ServiceBuilder` but execute top-down for requests. So in this example, the request flows through: Trace → Timeout → CORS → Compression → Handler. The trace layer wraps everything, so it measures the total time including all other middleware.

Wait — that's confusing. Let me be more precise. `ServiceBuilder` applies layers in declaration order — first declared wraps outermost. So `TraceLayer` is outermost (sees the request first), then `TimeoutLayer`, then `CorsLayer`, then `CompressionLayer` is innermost (closest to the handler).

With `.layer()` directly on Router (without `ServiceBuilder`), the *last* layer added is outermost. This is a common source of bugs:

```rust
// Without ServiceBuilder — LAST layer runs FIRST on request
let app = Router::new()
    .route("/", get(handler))
    .layer(CompressionLayer::new())     // runs 4th (innermost)
    .layer(CorsLayer::permissive())     // runs 3rd
    .layer(TimeoutLayer::new(dur))      // runs 2nd
    .layer(TraceLayer::new_for_http()); // runs 1st (outermost)
```

I always use `ServiceBuilder` because the ordering is more intuitive.

## Writing Custom Middleware

### The Easy Way: from_fn

For most custom middleware, `axum::middleware::from_fn` is all you need. It lets you write middleware as a plain async function.

```rust
use axum::{
    middleware::{self, Next},
    http::Request,
    response::Response,
};

async fn log_request_id(
    request: Request<axum::body::Body>,
    next: Next,
) -> Response {
    let request_id = request
        .headers()
        .get("x-request-id")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("unknown")
        .to_string();

    tracing::info!(request_id = %request_id, "processing request");

    let mut response = next.run(request).await;

    response.headers_mut().insert(
        "x-request-id",
        request_id.parse().unwrap(),
    );

    response
}

let app = Router::new()
    .route("/api/users", get(list_users))
    .layer(middleware::from_fn(log_request_id));
```

The `next.run(request)` call passes the request to the next layer (or the handler). You can do work before the call (on the request) and after the call (on the response). You can also short-circuit by returning a response without calling `next.run()`.

### Middleware That Needs State

If your middleware needs access to application state (say, a database pool for auth checks), use `from_fn_with_state`:

```rust
use axum::{
    extract::State,
    middleware::{self, Next},
    http::{Request, StatusCode},
    response::Response,
};

async fn require_api_key(
    State(state): State<AppState>,
    request: Request<axum::body::Body>,
    next: Next,
) -> Result<Response, StatusCode> {
    let api_key = request
        .headers()
        .get("x-api-key")
        .and_then(|v| v.to_str().ok());

    match api_key {
        Some(key) if state.valid_api_keys.contains(key) => {
            Ok(next.run(request).await)
        }
        _ => Err(StatusCode::UNAUTHORIZED),
    }
}

let app = Router::new()
    .route("/api/users", get(list_users))
    .layer(middleware::from_fn_with_state(state.clone(), require_api_key))
    .with_state(state);
```

### Inserting Data for Handlers via Extensions

A powerful pattern: middleware extracts data and makes it available to handlers via request extensions.

```rust
use axum::Extension;

#[derive(Clone)]
struct AuthenticatedUser {
    id: u64,
    email: String,
    role: String,
}

async fn auth_middleware(
    State(state): State<AppState>,
    mut request: Request<axum::body::Body>,
    next: Next,
) -> Result<Response, StatusCode> {
    let token = request
        .headers()
        .get("Authorization")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .ok_or(StatusCode::UNAUTHORIZED)?;

    // Validate token, look up user
    let user = validate_token(&state.db, token)
        .await
        .map_err(|_| StatusCode::UNAUTHORIZED)?;

    // Insert into extensions — handlers can extract this
    request.extensions_mut().insert(AuthenticatedUser {
        id: user.id,
        email: user.email,
        role: user.role,
    });

    Ok(next.run(request).await)
}

// Handler extracts the user set by middleware
async fn get_profile(Extension(user): Extension<AuthenticatedUser>) -> Json<serde_json::Value> {
    Json(json!({
        "id": user.id,
        "email": user.email,
        "role": user.role,
    }))
}
```

This is my preferred pattern for auth. The middleware validates the token and resolves the user once. Every handler downstream just extracts `Extension<AuthenticatedUser>` — clean, no duplication.

## The Tower Service Trait (For When You Need It)

Most of the time, `from_fn` handles your needs. But sometimes you need the full power of the `Service` trait — for connection-level middleware, for middleware that needs `Clone`, or for middleware you want to publish as a library.

```rust
use tower::{Service, Layer};
use std::task::{Context, Poll};
use std::pin::Pin;
use std::future::Future;

#[derive(Clone)]
struct TimingLayer;

impl<S> Layer<S> for TimingLayer {
    type Service = TimingMiddleware<S>;

    fn layer(&self, inner: S) -> Self::Service {
        TimingMiddleware { inner }
    }
}

#[derive(Clone)]
struct TimingMiddleware<S> {
    inner: S,
}

impl<S, B> Service<Request<B>> for TimingMiddleware<S>
where
    S: Service<Request<B>, Response = Response> + Send + Clone + 'static,
    S::Future: Send + 'static,
    B: Send + 'static,
{
    type Response = S::Response;
    type Error = S::Error;
    type Future = Pin<Box<dyn Future<Output = Result<Self::Response, Self::Error>> + Send>>;

    fn poll_ready(&mut self, cx: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
        self.inner.poll_ready(cx)
    }

    fn call(&mut self, request: Request<B>) -> Self::Future {
        let mut inner = self.inner.clone();
        Box::pin(async move {
            let start = std::time::Instant::now();
            let response = inner.call(request).await?;
            let elapsed = start.elapsed();
            tracing::info!(elapsed = ?elapsed, "request processed");
            Ok(response)
        })
    }
}

// Usage
let app = Router::new()
    .route("/api/users", get(list_users))
    .layer(TimingLayer);
```

Yeah, it's verbose. That's the trade-off — you get full control over polling, cloning, and future types, but you write more boilerplate. For application-level middleware, stick with `from_fn`. Save the `Service` trait for library code.

## Applying Middleware Selectively

Not every route needs every middleware. Axum lets you apply layers to specific route groups.

```rust
let public_routes = Router::new()
    .route("/health", get(health))
    .route("/api/login", post(login));

let protected_routes = Router::new()
    .route("/api/users", get(list_users).post(create_user))
    .route("/api/users/:id", get(get_user))
    .layer(middleware::from_fn_with_state(state.clone(), require_auth));

let admin_routes = Router::new()
    .route("/admin/stats", get(admin_stats))
    .route("/admin/users/:id/ban", post(ban_user))
    .layer(middleware::from_fn_with_state(state.clone(), require_admin));

let app = Router::new()
    .merge(public_routes)
    .merge(protected_routes)
    .merge(admin_routes)
    .layer(TraceLayer::new_for_http()) // This applies to ALL routes
    .with_state(state);
```

Layers added to a specific `Router` only apply to that router's routes. Layers added after `.merge()` apply to everything. This gives you fine-grained control — public routes skip auth, admin routes get extra authorization, and all routes get logging.

## A Complete Middleware Stack

Here's the middleware stack I use for most production services:

```rust
use axum::{middleware, Router, routing::get};
use tower::ServiceBuilder;
use tower_http::{
    cors::CorsLayer,
    trace::TraceLayer,
    timeout::TimeoutLayer,
    compression::CompressionLayer,
    request_id::{MakeRequestUuid, SetRequestIdLayer, PropagateRequestIdLayer},
};
use std::time::Duration;

let app = Router::new()
    .nest("/api", api_routes)
    .layer(
        ServiceBuilder::new()
            // These run in order: first added = outermost
            .layer(SetRequestIdLayer::x_request_id(MakeRequestUuid))
            .layer(PropagateRequestIdLayer::x_request_id())
            .layer(TraceLayer::new_for_http())
            .layer(TimeoutLayer::new(Duration::from_secs(30)))
            .layer(CorsLayer::permissive()) // tighten for production
            .layer(CompressionLayer::new())
    )
    .with_state(state);
```

Request ID generation → propagation → tracing → timeout → CORS → compression. The request ID is generated first so the trace layer can include it in log spans. Timeout wraps everything after tracing so timeouts get logged. CORS and compression are inner layers because they only affect the response body.

This stack handles 90% of what you need. Custom auth middleware gets added at the route group level, not globally, because not every route needs authentication.

That's Tower middleware in Axum. It's more upfront work than `app.use()` in Express, but the type safety and composability pay off fast. Next lesson: request validation and error responses — making your API actually tell clients what went wrong.
