---
title: "Lesson 12: Production Deployment — Docker, graceful shutdown, observability"
author: Atharva Pandey
series: "Rust Web & API Development"
lesson: 12
keywords: [Rust, rust, web, API, axum, Docker, graceful shutdown, observability, metrics, tracing, deployment]
tags: [Rust tutorial, rust, web, api]
date: '2024-10-28T08:55:00.000Z'
---

Shipping to production is where the real education begins. Your local dev environment is a controlled fantasy — one instance, no load balancer, fast database on localhost, unlimited memory. Production is a hostile environment where your service gets killed mid-request, runs out of memory at 3am, and needs to tell you what went wrong without you SSH-ing into a container. This lesson is about surviving out there.

## Dockerfile: The Multi-Stage Build

Rust binaries are statically linked (or nearly so). A compiled Rust service can run in a scratch or distroless container with no runtime dependencies. This means tiny images — often under 20MB.

```dockerfile
# Stage 1: Build
FROM rust:1.82-bookworm AS builder

WORKDIR /app

# Cache dependencies by copying Cargo files first
COPY Cargo.toml Cargo.lock ./
RUN mkdir src && echo "fn main() {}" > src/main.rs
RUN cargo build --release && rm -rf src

# Build the actual application
COPY src ./src
COPY migrations ./migrations
RUN touch src/main.rs && cargo build --release

# Stage 2: Runtime
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/target/release/my-api /usr/local/bin/my-api
COPY --from=builder /app/migrations /app/migrations

ENV RUST_LOG=info
EXPOSE 3000

CMD ["my-api"]
```

The dependency caching trick is crucial. Rust compilation is slow, and re-downloading and compiling 200 crates every time you change a line of application code is painful. By copying just `Cargo.toml` and `Cargo.lock` first, building, then copying the source, Docker caches the dependency layer. Subsequent builds only recompile your code — usually under 30 seconds.

### Even Smaller: Using scratch

If you statically link with musl, you can use a `scratch` image — literally nothing but your binary.

```dockerfile
FROM rust:1.82-bookworm AS builder

RUN rustup target add x86_64-unknown-linux-musl
RUN apt-get update && apt-get install -y musl-tools

WORKDIR /app
COPY Cargo.toml Cargo.lock ./
RUN mkdir src && echo "fn main() {}" > src/main.rs
RUN cargo build --release --target x86_64-unknown-linux-musl && rm -rf src

COPY src ./src
COPY migrations ./migrations
RUN touch src/main.rs && cargo build --release --target x86_64-unknown-linux-musl

FROM scratch
COPY --from=builder /app/target/x86_64-unknown-linux-musl/release/my-api /my-api
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/

EXPOSE 3000
ENTRYPOINT ["/my-api"]
```

The resulting image is your binary plus CA certificates. Nothing else. No shell, no package manager, no attack surface. I use `debian-slim` for services that need to run migrations at startup (which requires the `migrations` directory), and `scratch` for services that connect to pre-migrated databases.

## Graceful Shutdown

When Kubernetes kills your pod, when you deploy a new version, when the orchestrator reschedules — your service receives a SIGTERM. You have a grace period (usually 30 seconds) before SIGKILL. In that window, you need to:

1. Stop accepting new connections
2. Finish processing in-flight requests
3. Close database connections cleanly
4. Flush any buffered logs or metrics

Axum supports graceful shutdown through `axum::serve`'s `.with_graceful_shutdown()`:

```rust
use tokio::signal;

#[tokio::main]
async fn main() {
    tracing_subscriber::registry()
        .with(tracing_subscriber::EnvFilter::new(
            std::env::var("RUST_LOG").unwrap_or_else(|_| "info".into()),
        ))
        .with(tracing_subscriber::fmt::layer().json())
        .init();

    let pool = setup_database().await;
    let state = AppState::new(pool.clone());
    let app = create_router(state);

    let listener = tokio::net::TcpListener::bind("0.0.0.0:3000")
        .await
        .unwrap();

    tracing::info!("listening on {}", listener.local_addr().unwrap());

    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await
        .unwrap();

    // After server stops, clean up
    tracing::info!("shutting down database connections");
    pool.close().await;
    tracing::info!("shutdown complete");
}

async fn shutdown_signal() {
    let ctrl_c = async {
        signal::ctrl_c()
            .await
            .expect("failed to install Ctrl+C handler");
    };

    #[cfg(unix)]
    let terminate = async {
        signal::unix::signal(signal::unix::SignalKind::terminate())
            .expect("failed to install signal handler")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => tracing::info!("received Ctrl+C"),
        _ = terminate => tracing::info!("received SIGTERM"),
    }
}
```

When the shutdown signal fires, the server stops accepting new connections but continues processing in-flight requests. Once all active requests complete (or the Tokio runtime shuts down), the code after `axum::serve` runs — that's where you close database pools and flush buffers.

## Health Checks

Your orchestrator (Kubernetes, ECS, Nomad) needs to know if your service is healthy. Two endpoints:

```rust
use std::sync::atomic::{AtomicBool, Ordering};

static READY: AtomicBool = AtomicBool::new(false);

/// Liveness probe — is the process running?
/// Returns 200 as long as the server is up.
/// Kubernetes restarts the pod if this fails.
async fn health_live() -> StatusCode {
    StatusCode::OK
}

/// Readiness probe — can we serve traffic?
/// Returns 200 only when the database is connected and migrations are run.
/// Kubernetes removes the pod from the load balancer if this fails.
async fn health_ready(State(state): State<AppState>) -> StatusCode {
    if !READY.load(Ordering::Relaxed) {
        return StatusCode::SERVICE_UNAVAILABLE;
    }

    // Check database connectivity
    match sqlx::query("SELECT 1").execute(&state.db).await {
        Ok(_) => StatusCode::OK,
        Err(_) => StatusCode::SERVICE_UNAVAILABLE,
    }
}

// In main, after migrations:
sqlx::migrate!("./migrations").run(&pool).await.unwrap();
READY.store(true, Ordering::Relaxed);
```

Wire them up outside the auth middleware:

```rust
let app = Router::new()
    .route("/health/live", get(health_live))
    .route("/health/ready", get(health_ready))
    .nest("/api", api_routes) // api_routes has auth middleware
    .with_state(state);
```

In Kubernetes:

```yaml
livenessProbe:
  httpGet:
    path: /health/live
    port: 3000
  initialDelaySeconds: 5
  periodSeconds: 10

readinessProbe:
  httpGet:
    path: /health/ready
    port: 3000
  initialDelaySeconds: 10
  periodSeconds: 5
```

## Structured Logging

In production, you don't read log lines visually — they go into a log aggregator (Datadog, Grafana Loki, CloudWatch). Structured JSON logs are essential.

```rust
use tracing_subscriber::{
    fmt, layer::SubscriberExt, util::SubscriberInitExt, EnvFilter,
};

fn init_tracing() {
    let env_filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("info"));

    tracing_subscriber::registry()
        .with(env_filter)
        .with(
            fmt::layer()
                .json() // JSON output for production
                .with_target(true)
                .with_thread_ids(true)
                .with_file(true)
                .with_line_number(true)
        )
        .init();
}
```

This produces logs like:

```json
{
  "timestamp": "2024-10-28T08:55:00.000Z",
  "level": "INFO",
  "target": "my_api::handlers",
  "message": "user created",
  "user_id": 42,
  "email": "alice@example.com",
  "file": "src/handlers/users.rs",
  "line": 87,
  "threadId": 1
}
```

Use structured fields in your handlers:

```rust
async fn create_user(
    State(state): State<AppState>,
    Json(input): Json<CreateUserInput>,
) -> Result<(StatusCode, Json<User>), AppError> {
    let user = state.db.create_user(&input).await?;

    tracing::info!(
        user_id = user.id,
        email = %user.email,
        "user created"
    );

    Ok((StatusCode::CREATED, Json(user)))
}
```

## Metrics with Prometheus

Metrics tell you about your service's behavior over time — request rates, latency distributions, error rates, connection pool usage.

```toml
[dependencies]
metrics = "0.23"
metrics-exporter-prometheus = "0.15"
axum-prometheus = "0.7"
```

```rust
use metrics_exporter_prometheus::PrometheusBuilder;
use axum_prometheus::PrometheusMetricLayer;

fn setup_metrics() -> PrometheusMetricLayer<'static> {
    let (prometheus_layer, metric_handle) = PrometheusMetricLayer::pair();

    // Serve metrics endpoint
    tokio::spawn(async move {
        let metrics_app = Router::new()
            .route("/metrics", get(|| async move { metric_handle.render() }));

        let listener = tokio::net::TcpListener::bind("0.0.0.0:9090")
            .await
            .unwrap();
        axum::serve(listener, metrics_app).await.unwrap();
    });

    prometheus_layer
}

#[tokio::main]
async fn main() {
    init_tracing();

    let prometheus_layer = setup_metrics();

    let app = Router::new()
        .nest("/api", api_routes)
        .route("/health/live", get(health_live))
        .layer(prometheus_layer)
        .with_state(state);

    // ...
}
```

This automatically tracks:

- `http_requests_total` — request count by method, path, status
- `http_requests_duration_seconds` — latency histogram by method, path, status

Add custom metrics for business events:

```rust
use metrics::{counter, histogram};

async fn create_order(
    State(state): State<AppState>,
    Json(input): Json<CreateOrderInput>,
) -> Result<Json<Order>, AppError> {
    let start = std::time::Instant::now();

    let order = state.db.create_order(&input).await?;

    counter!("orders_created_total", "product" => input.product_type).increment(1);
    histogram!("order_creation_duration_seconds").record(start.elapsed().as_secs_f64());

    Ok(Json(order))
}
```

Scrape the `/metrics` endpoint with Prometheus:

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'my-api'
    static_configs:
      - targets: ['my-api:9090']
    scrape_interval: 15s
```

## Distributed Tracing

When a request flows through multiple services, you need to trace it end-to-end. OpenTelemetry is the standard.

```toml
[dependencies]
opentelemetry = "0.24"
opentelemetry_sdk = { version = "0.24", features = ["rt-tokio"] }
opentelemetry-otlp = "0.17"
tracing-opentelemetry = "0.25"
```

```rust
use opentelemetry::global;
use opentelemetry_sdk::trace::TracerProvider;
use opentelemetry_otlp::WithExportConfig;

fn init_tracing_with_otel() {
    let otlp_exporter = opentelemetry_otlp::new_exporter()
        .tonic()
        .with_endpoint("http://jaeger:4317");

    let tracer_provider = TracerProvider::builder()
        .with_batch_exporter(
            opentelemetry_otlp::new_pipeline()
                .tracing()
                .with_exporter(otlp_exporter)
                .install_batch(opentelemetry_sdk::runtime::Tokio)
                .unwrap(),
            opentelemetry_sdk::runtime::Tokio,
        )
        .build();

    global::set_tracer_provider(tracer_provider.clone());

    let telemetry_layer = tracing_opentelemetry::layer()
        .with_tracer(tracer_provider.tracer("my-api"));

    tracing_subscriber::registry()
        .with(EnvFilter::new("info"))
        .with(fmt::layer().json())
        .with(telemetry_layer)
        .init();
}
```

Every `tracing::info_span!` and `tracing::info!` call now generates OpenTelemetry spans that flow to Jaeger (or any OTLP-compatible backend). Add the Tower trace layer, and every HTTP request automatically gets a span with method, path, status, and duration.

## Configuration Management

Don't hardcode configuration. Load it from environment variables with sensible defaults:

```rust
use serde::Deserialize;

#[derive(Deserialize, Clone)]
pub struct Config {
    #[serde(default = "default_port")]
    pub port: u16,

    pub database_url: String,

    #[serde(default = "default_max_connections")]
    pub db_max_connections: u32,

    pub jwt_secret: String,

    #[serde(default = "default_jwt_expiration")]
    pub jwt_expiration_hours: i64,

    pub redis_url: Option<String>,

    #[serde(default = "default_log_level")]
    pub rust_log: String,
}

fn default_port() -> u16 { 3000 }
fn default_max_connections() -> u32 { 20 }
fn default_jwt_expiration() -> i64 { 1 }
fn default_log_level() -> String { "info".to_string() }

impl Config {
    pub fn from_env() -> Self {
        envy::from_env::<Config>()
            .expect("Failed to load configuration from environment")
    }
}
```

```toml
[dependencies]
envy = "0.4"
```

In Docker Compose or Kubernetes, set the environment variables:

```yaml
# docker-compose.yml
services:
  api:
    image: my-api:latest
    environment:
      DATABASE_URL: postgres://user:pass@db:5432/myapp
      JWT_SECRET: your-256-bit-secret
      RUST_LOG: info
      PORT: 3000
    ports:
      - "3000:3000"
    depends_on:
      - db
```

## The Complete Production main.rs

Here's what a production-ready `main.rs` looks like when you put everything together:

```rust
use axum::Router;
use sqlx::postgres::PgPoolOptions;
use std::time::Duration;
use tracing_subscriber::{fmt, layer::SubscriberExt, util::SubscriberInitExt, EnvFilter};

mod config;
mod error;
mod handlers;
mod middleware;
mod models;
mod routes;

use config::Config;

#[tokio::main]
async fn main() {
    // Load configuration
    let config = Config::from_env();

    // Initialize structured logging
    tracing_subscriber::registry()
        .with(EnvFilter::new(&config.rust_log))
        .with(fmt::layer().json())
        .init();

    tracing::info!("starting application");

    // Database pool
    let pool = PgPoolOptions::new()
        .max_connections(config.db_max_connections)
        .min_connections(5)
        .acquire_timeout(Duration::from_secs(5))
        .idle_timeout(Duration::from_secs(600))
        .max_lifetime(Duration::from_secs(1800))
        .connect(&config.database_url)
        .await
        .expect("Failed to connect to database");

    tracing::info!("database connected");

    // Run migrations
    sqlx::migrate!("./migrations")
        .run(&pool)
        .await
        .expect("Failed to run migrations");

    tracing::info!("migrations complete");

    // Build application state
    let state = AppState::new(pool.clone(), config.clone());

    // Build router
    let app = routes::create_router(state);

    // Start server
    let addr = format!("0.0.0.0:{}", config.port);
    let listener = tokio::net::TcpListener::bind(&addr)
        .await
        .unwrap();

    tracing::info!(addr = %addr, "listening");

    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await
        .unwrap();

    // Cleanup
    tracing::info!("closing database connections");
    pool.close().await;
    tracing::info!("shutdown complete");
}

async fn shutdown_signal() {
    use tokio::signal;

    let ctrl_c = async {
        signal::ctrl_c().await.expect("failed to install handler");
    };

    #[cfg(unix)]
    let terminate = async {
        signal::unix::signal(signal::unix::SignalKind::terminate())
            .expect("failed to install handler")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => tracing::info!("ctrl-c received"),
        _ = terminate => tracing::info!("SIGTERM received"),
    }
}
```

## Production Checklist

Before you ship:

- [ ] Multi-stage Dockerfile with dependency caching
- [ ] Graceful shutdown handling (SIGTERM)
- [ ] Health check endpoints (liveness + readiness)
- [ ] Structured JSON logging
- [ ] Configuration from environment variables
- [ ] Database connection pooling with sensible limits
- [ ] Migrations run at startup
- [ ] Request timeout middleware (30s default)
- [ ] CORS configured for your actual domains
- [ ] Rate limiting on sensitive endpoints
- [ ] Prometheus metrics exposed
- [ ] Request tracing with correlation IDs
- [ ] Error responses that don't leak internals
- [ ] TLS termination (at load balancer or in-app)
- [ ] Non-root user in Dockerfile

Add to your Dockerfile:

```dockerfile
RUN adduser --disabled-password --gecos "" appuser
USER appuser
```

## Where to Go From Here

This course covered the core patterns for building production web services in Rust with Axum. You now have the foundation for routing, middleware, validation, authentication, database integration, real-time communication, API documentation, testing, and deployment.

The Rust web ecosystem is growing fast. Keep an eye on:

- **Loco** — A Rails-like framework built on Axum, good for rapid development
- **Shuttle** — A deployment platform purpose-built for Rust services
- **Pavex** — A new framework that uses compile-time dependency injection

But frameworks come and go. The patterns in this course — Tower middleware, typed extractors, compile-time SQL verification, structured observability — those are durable. They'll serve you regardless of which framework is popular next year.

Build something. Ship it. Watch it break. Fix it. That's how you actually learn this stuff.
