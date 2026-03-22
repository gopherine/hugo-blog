---
title: "Lesson 4: Observability — tracing, metrics, OpenTelemetry"
author: Atharva Pandey
series: "Rust Deployment & Operations"
lesson: 4
keywords: [Rust, rust, deployment, observability, tracing, metrics, OpenTelemetry, logging, spans, Prometheus]
tags: [Rust tutorial, rust, deployment, devops]
date: '2025-04-27T08:22:00.000Z'
---

I once spent six hours debugging a production issue where requests were randomly timing out. No errors in the logs. CPU and memory looked fine. Response times were normal — except for the 2% that took 30 seconds. Without distributed tracing, I was blind. I ended up bisecting the problem by adding `println!` statements, deploying them one at a time, and watching CloudWatch. It was miserable.

That experience permanently changed how I build services. Observability isn't something you bolt on when things break. It's something you build in from day one, or you pay for it later — with your time, your sleep, and your sanity.

## The Three Pillars (and Why Rust Gets Two of Them Right)

Observability has three pillars: logs, metrics, and traces. Rust's ecosystem handles these through a unified foundation — the `tracing` crate — which is honestly one of the best observability libraries in any language.

### Why `tracing` Over `log`

The older `log` crate gives you leveled messages: `info!("request handled")`. That's fine for scripts. But for services, you need *structured* data — key-value pairs you can filter and aggregate — and *spans* that track the lifecycle of operations across async boundaries.

`tracing` does both:

```rust
use tracing::{info, instrument, warn};

#[instrument(skip(pool))]
async fn handle_request(
    user_id: u64,
    pool: &PgPool,
) -> Result<Response, AppError> {
    info!(user_id, "processing request");

    let user = get_user(pool, user_id).await?;

    if user.is_rate_limited() {
        warn!(user_id, limit = user.rate_limit, "user rate limited");
        return Ok(Response::too_many_requests());
    }

    let result = process(&user).await?;
    info!(user_id, items = result.len(), "request completed");

    Ok(Response::ok(result))
}
```

The `#[instrument]` macro creates a span for the entire function call. Every log statement inside it automatically includes the span's fields (`user_id` in this case). If `get_user` or `process` also have `#[instrument]`, their spans nest inside this one, creating a tree of operations you can follow through your system.

This is game-changing for async code. In a typical tokio runtime, dozens of requests are interleaved across a small number of threads. Without spans, log messages from different requests mix together into nonsense. With spans, every message carries its context.

## Setting Up tracing

Here's a complete setup for a production service:

```toml
# Cargo.toml
[dependencies]
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter", "json"] }
```

```rust
use tracing_subscriber::{fmt, EnvFilter, layer::SubscriberExt, util::SubscriberInitExt};

fn init_tracing() {
    let env_filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("info,hyper=warn,tower=warn"));

    tracing_subscriber::registry()
        .with(env_filter)
        .with(fmt::layer().json())
        .init();
}
```

This gives you:

- **Environment-based filtering.** Set `RUST_LOG=debug` to see everything, or `RUST_LOG=myservice=debug,sqlx=warn` to be selective. In production, `info` with noisy crates suppressed to `warn` is a good default.
- **JSON output.** Structured JSON logs are parseable by every log aggregation system — Datadog, Grafana Loki, CloudWatch, ELK. Never ship human-formatted logs to production.

For local development, swap JSON for pretty printing:

```rust
fn init_tracing() {
    let env_filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("debug"));

    let is_production = std::env::var("APP_ENV")
        .map(|v| v == "production")
        .unwrap_or(false);

    let registry = tracing_subscriber::registry().with(env_filter);

    if is_production {
        registry.with(fmt::layer().json()).init();
    } else {
        registry.with(fmt::layer().pretty()).init();
    }
}
```

## Metrics with Prometheus

For metrics, I use the `metrics` crate with Prometheus exposition:

```toml
[dependencies]
metrics = "0.23"
metrics-exporter-prometheus = "0.15"
```

```rust
use metrics::{counter, gauge, histogram};
use metrics_exporter_prometheus::PrometheusBuilder;
use std::time::Instant;

fn init_metrics() -> metrics_exporter_prometheus::PrometheusHandle {
    PrometheusBuilder::new()
        .install_recorder()
        .expect("failed to install Prometheus recorder")
}

async fn handle_request(req: Request) -> Response {
    let start = Instant::now();
    counter!("http_requests_total", "method" => req.method().to_string()).increment(1);

    let response = process_request(req).await;

    let duration = start.elapsed().as_secs_f64();
    histogram!("http_request_duration_seconds",
        "method" => req.method().to_string(),
        "status" => response.status().as_u16().to_string()
    ).record(duration);

    response
}
```

Expose the metrics endpoint:

```rust
use axum::{Router, routing::get};

async fn metrics_handler(
    handle: axum::extract::State<PrometheusHandle>,
) -> String {
    handle.render()
}

fn app(metrics_handle: PrometheusHandle) -> Router {
    Router::new()
        .route("/api/health", get(health))
        .route("/metrics", get(metrics_handler))
        .with_state(metrics_handle)
}
```

### What Metrics to Track

At minimum, every service should expose:

```rust
// Request metrics
counter!("http_requests_total", "method" => method, "path" => path, "status" => status);
histogram!("http_request_duration_seconds", "method" => method, "path" => path);

// Connection pool metrics (if using a database)
gauge!("db_connections_active").set(pool.size() as f64);
gauge!("db_connections_idle").set(pool.num_idle() as f64);

// Application-specific metrics
counter!("orders_processed_total", "type" => order_type);
histogram!("order_processing_duration_seconds");

// Runtime metrics
gauge!("process_uptime_seconds").set(uptime.as_secs_f64());
```

**RED metrics** (Rate, Errors, Duration) for every external-facing endpoint. **USE metrics** (Utilization, Saturation, Errors) for every resource pool. This covers 90% of production debugging scenarios.

## OpenTelemetry: The Full Picture

If you're running multiple services, individual traces and metrics aren't enough. You need distributed tracing — the ability to follow a request from the API gateway through service A, into the message queue, out to service B, and into the database. That's OpenTelemetry.

```toml
[dependencies]
opentelemetry = "0.24"
opentelemetry_sdk = { version = "0.24", features = ["rt-tokio"] }
opentelemetry-otlp = { version = "0.17", features = ["tonic"] }
tracing-opentelemetry = "0.25"
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter"] }
```

```rust
use opentelemetry::trace::TracerProvider;
use opentelemetry_sdk::{trace as sdktrace, Resource};
use opentelemetry_otlp::WithExportConfig;
use tracing_opentelemetry::OpenTelemetryLayer;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt, EnvFilter, fmt};

fn init_otel_tracing() -> sdktrace::TracerProvider {
    let exporter = opentelemetry_otlp::SpanExporter::builder()
        .with_tonic()
        .with_endpoint("http://localhost:4317")
        .build()
        .expect("failed to create OTLP exporter");

    let provider = sdktrace::TracerProvider::builder()
        .with_batch_exporter(exporter)
        .with_resource(Resource::builder()
            .with_service_name("myservice")
            .build())
        .build();

    let tracer = provider.tracer("myservice");

    tracing_subscriber::registry()
        .with(EnvFilter::try_from_default_env()
            .unwrap_or_else(|_| EnvFilter::new("info")))
        .with(fmt::layer().json())
        .with(OpenTelemetryLayer::new(tracer))
        .init();

    provider
}

#[tokio::main]
async fn main() {
    let provider = init_otel_tracing();

    // ... run your application ...

    // Flush traces on shutdown
    provider.shutdown().expect("failed to shut down tracer provider");
}
```

The beauty of this setup is that your existing `#[instrument]` annotations and `info!`/`warn!` calls automatically become OpenTelemetry spans and events. You don't write OpenTelemetry-specific code in your business logic — `tracing` abstracts it away.

### Adding Trace Context to HTTP Requests

For distributed tracing to work, trace context must propagate across service boundaries. With Axum and reqwest:

```rust
use axum::middleware;
use opentelemetry::propagation::TextMapCompositePropagator;
use opentelemetry_sdk::propagation::TraceContextPropagator;
use reqwest::header::HeaderMap;

// Extract incoming trace context
async fn extract_trace_context(
    headers: axum::http::HeaderMap,
    next: middleware::Next,
    request: axum::extract::Request,
) -> axum::response::Response {
    // opentelemetry middleware handles this automatically
    // if you use tower-http's trace layer
    next.run(request).await
}

// Propagate context to outgoing requests
async fn call_downstream(url: &str) -> Result<String, reqwest::Error> {
    use tracing_opentelemetry::OpenTelemetrySpanExt;

    let mut headers = HeaderMap::new();
    let cx = tracing::Span::current().context();

    opentelemetry::global::get_text_map_propagator(|propagator| {
        propagator.inject_context(&cx, &mut opentelemetry_http::HeaderInjector(&mut headers));
    });

    reqwest::Client::new()
        .get(url)
        .headers(headers)
        .send()
        .await?
        .text()
        .await
}
```

## Putting It All Together

Here's a complete `main.rs` with logging, metrics, and optional OpenTelemetry:

```rust
use axum::{Router, routing::get, extract::State};
use metrics_exporter_prometheus::PrometheusHandle;
use std::net::SocketAddr;
use tracing::info;

mod observability;

struct AppState {
    metrics: PrometheusHandle,
}

async fn health() -> &'static str {
    "ok"
}

async fn metrics_handler(State(state): State<AppState>) -> String {
    state.metrics.render()
}

#[tokio::main]
async fn main() {
    // Initialize observability
    observability::init_tracing();
    let metrics_handle = observability::init_metrics();

    info!("starting service");

    let state = AppState {
        metrics: metrics_handle,
    };

    let app = Router::new()
        .route("/health", get(health))
        .route("/metrics", get(metrics_handler))
        .with_state(state);

    let addr = SocketAddr::from(([0, 0, 0, 0], 3000));
    info!(%addr, "listening");

    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}
```

## Common Mistakes

**Too many labels on metrics.** Every unique label combination creates a new time series. `http_requests_total{method, path, status, user_id}` — that `user_id` means one time series *per user*. With 100K users, that's 100K time series and your Prometheus instance is on fire. Keep cardinality low.

**Logging request/response bodies.** I've seen services that log every JSON body at `info` level. That's a great way to fill your disk in an hour and potentially leak sensitive data. Log IDs and metadata, not payloads.

**Not setting up tracing before anything else.** Initialize tracing as the very first thing in `main()`. Any logs before initialization are lost.

**Forgetting to flush on shutdown.** OpenTelemetry batches traces. If your service shuts down without flushing, the last batch of traces disappears. Always call `provider.shutdown()` in your shutdown handler — we'll cover this in Lesson 6.

## Next Up

Observability tells you what's happening. But how does your orchestrator know your service is *healthy*? In the next lesson, we'll build health checks and readiness probes — the endpoints that tell Kubernetes (or Docker, or your load balancer) whether your service should receive traffic.
