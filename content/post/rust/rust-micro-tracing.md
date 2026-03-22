---
title: "Lesson 6: Distributed Tracing Across Services — Following requests"
author: Atharva Pandey
series: "Rust Microservices Patterns"
lesson: 6
keywords: [Rust, rust, microservices, distributed tracing, OpenTelemetry, Jaeger, spans, trace context, observability]
tags: [Rust tutorial, rust, microservices, architecture]
date: '2025-06-11T10:45:00.000Z'
---

"It's slow" is the most useless bug report in a microservices world. Slow where? The API gateway? The order service? The database query inside the payment service? The message queue between inventory and shipping? When a single user action touches five services and three databases, "it's slow" could mean anything.

I spent an entire afternoon once trying to track down a latency spike. Added timing logs to every service. Correlated timestamps across hosts. Manually stitched together the request flow from six different log streams. Found the culprit: a DNS resolution that was taking 800ms because of a misconfigured resolver — in a service I didn't even know was involved.

Distributed tracing would have shown me the problem in thirty seconds. A single view: request enters gateway (2ms), hits auth service (5ms), hits order service (3ms), order service calls inventory service... wait, there's an 800ms gap before the inventory response arrives. Click on that span, see the DNS resolution. Done.

## OpenTelemetry in Rust

OpenTelemetry (OTel) is the standard. It's vendor-neutral, well-supported in Rust, and works with every tracing backend — Jaeger, Zipkin, Grafana Tempo, Datadog, Honeycomb. You instrument once and switch backends without code changes.

The Rust OTel ecosystem uses the `tracing` crate as its foundation. If you're already using `tracing` for logs (and you should be), adding distributed tracing is mostly plumbing.

```toml
# Cargo.toml
[dependencies]
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter", "json"] }
tracing-opentelemetry = "0.27"
opentelemetry = "0.27"
opentelemetry_sdk = { version = "0.27", features = ["rt-tokio"] }
opentelemetry-otlp = { version = "0.27", features = ["tonic"] }
opentelemetry-semantic-conventions = "0.27"
axum = "0.7"
tower-http = { version = "0.6", features = ["trace"] }
tokio = { version = "1", features = ["full"] }
uuid = { version = "1", features = ["v4"] }
reqwest = { version = "0.12", features = ["json"] }
```

## Setting Up the Tracing Pipeline

This is the initialization code that every service runs at startup. I extract it into a shared crate so every service configures tracing identically.

```rust
// src/telemetry.rs

use opentelemetry::trace::TracerProvider;
use opentelemetry_otlp::WithExportConfig;
use opentelemetry_sdk::{
    runtime,
    trace::{self, Sampler},
    Resource,
};
use opentelemetry::KeyValue;
use opentelemetry_semantic_conventions::resource as semconv;
use tracing_subscriber::{
    fmt, layer::SubscriberExt, util::SubscriberInitExt, EnvFilter,
};

pub fn init_telemetry(
    service_name: &str,
    otlp_endpoint: &str,
) -> anyhow::Result<()> {
    // Configure the OTLP exporter
    let exporter = opentelemetry_otlp::SpanExporter::builder()
        .with_tonic()
        .with_endpoint(otlp_endpoint)
        .build()?;

    // Build the tracer provider
    let provider = opentelemetry_sdk::trace::TracerProvider::builder()
        .with_batch_exporter(exporter, runtime::Tokio)
        .with_sampler(Sampler::TraceIdRatioBased(0.1)) // Sample 10% in prod
        .with_resource(Resource::new(vec![
            KeyValue::new(semconv::SERVICE_NAME, service_name.to_string()),
            KeyValue::new(semconv::SERVICE_VERSION, env!("CARGO_PKG_VERSION").to_string()),
            KeyValue::new("deployment.environment", "production"),
        ]))
        .build();

    let tracer = provider.tracer(service_name.to_string());

    // Bridge tracing crate → OpenTelemetry
    let otel_layer = tracing_opentelemetry::layer().with_tracer(tracer);

    // JSON log output for structured logging
    let fmt_layer = fmt::layer()
        .json()
        .with_target(true)
        .with_thread_ids(true)
        .with_span_events(fmt::format::FmtSpan::CLOSE);

    // Wire it all together
    tracing_subscriber::registry()
        .with(EnvFilter::from_default_env()
            .add_directive("hyper=warn".parse()?)
            .add_directive("tower=info".parse()?))
        .with(otel_layer)
        .with(fmt_layer)
        .init();

    // Register the provider globally
    opentelemetry::global::set_tracer_provider(provider);

    Ok(())
}

/// Call this during graceful shutdown to flush pending spans.
pub fn shutdown_telemetry() {
    opentelemetry::global::shutdown_tracer_provider();
}
```

The sampling rate is important. In production, tracing 100% of requests generates enormous volumes of data. 10% is a reasonable starting point. For debugging specific issues, you can temporarily bump it to 100% or use head-based sampling (always trace requests with a specific header).

## Instrumenting Axum Handlers

Here's where the rubber meets the road. Every incoming request should start a trace span, and every outgoing call should create a child span.

```rust
// src/handlers.rs

use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    Json,
};
use serde::{Deserialize, Serialize};
use tracing::{instrument, Span};
use uuid::Uuid;

#[derive(Clone)]
pub struct AppState {
    pub db: sqlx::PgPool,
    pub inventory_client: InventoryClient,
}

#[derive(Serialize)]
pub struct OrderResponse {
    pub id: String,
    pub status: String,
    pub items: Vec<ItemResponse>,
}

#[derive(Serialize)]
pub struct ItemResponse {
    pub product_id: String,
    pub quantity: u32,
    pub available: bool,
}

/// The #[instrument] macro creates a span automatically.
/// Every log within this function is attached to the span.
/// Downstream calls become child spans.
#[instrument(
    name = "create_order",
    skip(state, headers),
    fields(
        customer_id = %body.customer_id,
        item_count = body.items.len(),
        // These get filled in later
        order_id = tracing::field::Empty,
        total_cents = tracing::field::Empty,
    )
)]
pub async fn create_order(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<CreateOrderRequest>,
) -> Result<Json<OrderResponse>, (StatusCode, String)> {
    let order_id = Uuid::new_v4();

    // Record the order_id on the current span
    Span::current().record("order_id", &tracing::field::display(&order_id));

    // Check inventory — this creates a child span
    let availability = check_inventory(&state.inventory_client, &body.items, &headers)
        .await
        .map_err(|e| {
            tracing::error!(error = %e, "inventory check failed");
            (StatusCode::INTERNAL_SERVER_ERROR, e.to_string())
        })?;

    // Save to database — another child span
    let total_cents = save_order(&state.db, order_id, &body, &availability)
        .await
        .map_err(|e| {
            tracing::error!(error = %e, "database save failed");
            (StatusCode::INTERNAL_SERVER_ERROR, e.to_string())
        })?;

    Span::current().record("total_cents", total_cents);

    tracing::info!("order created successfully");

    Ok(Json(OrderResponse {
        id: order_id.to_string(),
        status: "pending".to_string(),
        items: availability,
    }))
}

#[derive(Deserialize)]
pub struct CreateOrderRequest {
    pub customer_id: String,
    pub items: Vec<OrderItemRequest>,
}

#[derive(Deserialize)]
pub struct OrderItemRequest {
    pub product_id: String,
    pub quantity: u32,
}

/// Each downstream call gets its own span.
/// The span captures timing, errors, and metadata.
#[instrument(
    name = "check_inventory",
    skip(client, headers),
    fields(item_count = items.len())
)]
async fn check_inventory(
    client: &InventoryClient,
    items: &[OrderItemRequest],
    headers: &HeaderMap,
) -> Result<Vec<ItemResponse>, anyhow::Error> {
    let product_ids: Vec<&str> = items.iter().map(|i| i.product_id.as_str()).collect();

    // This HTTP call to the inventory service will appear as
    // a child span in the trace
    let response = client.check_availability(&product_ids, headers).await?;

    let results: Vec<ItemResponse> = items.iter().map(|item| {
        let available = response
            .get(&item.product_id)
            .map(|qty| *qty >= item.quantity)
            .unwrap_or(false);

        ItemResponse {
            product_id: item.product_id.clone(),
            quantity: item.quantity,
            available,
        }
    }).collect();

    let unavailable_count = results.iter().filter(|r| !r.available).count();
    if unavailable_count > 0 {
        tracing::warn!(unavailable_count, "some items not available");
    }

    Ok(results)
}

#[instrument(
    name = "save_order",
    skip(db, body, availability),
    fields(order_id = %order_id)
)]
async fn save_order(
    db: &sqlx::PgPool,
    order_id: Uuid,
    body: &CreateOrderRequest,
    availability: &[ItemResponse],
) -> Result<i64, anyhow::Error> {
    let total_cents: i64 = 0; // Calculate from actual prices

    sqlx::query("INSERT INTO orders (id, customer_id, total_cents, status) VALUES ($1, $2, $3, 'pending')")
        .bind(order_id)
        .bind(&body.customer_id)
        .bind(total_cents)
        .execute(db)
        .await?;

    tracing::info!("order persisted to database");

    Ok(total_cents)
}
```

## Propagating Context Across Services

When your order service calls the inventory service, the trace context needs to travel with the HTTP request. This is how spans in different services get linked into a single trace.

```rust
// src/tracing_client.rs

use opentelemetry::global;
use opentelemetry::trace::TraceContextExt;
use reqwest::{Client, Response};
use std::collections::HashMap;
use std::time::Duration;
use tracing::instrument;
use tracing_opentelemetry::OpenTelemetrySpanExt;

#[derive(Clone)]
pub struct InventoryClient {
    client: Client,
    base_url: String,
}

impl InventoryClient {
    pub fn new(base_url: &str) -> Self {
        Self {
            client: Client::builder()
                .timeout(Duration::from_secs(5))
                .build()
                .unwrap(),
            base_url: base_url.to_string(),
        }
    }

    #[instrument(
        name = "inventory_client.check_availability",
        skip(self, upstream_headers),
        fields(product_count = product_ids.len())
    )]
    pub async fn check_availability(
        &self,
        product_ids: &[&str],
        upstream_headers: &axum::http::HeaderMap,
    ) -> Result<HashMap<String, u32>, anyhow::Error> {
        let url = format!("{}/api/inventory/check", self.base_url);

        let mut request = self.client
            .post(&url)
            .json(&serde_json::json!({ "product_ids": product_ids }));

        // Inject trace context into outgoing request headers.
        // This is what connects spans across service boundaries.
        let cx = tracing::Span::current().context();
        let mut injector = HeaderInjector::new();
        global::get_text_map_propagator(|propagator| {
            propagator.inject_context(&cx, &mut injector);
        });

        for (key, value) in injector.headers {
            if let (Ok(name), Ok(val)) = (
                key.parse::<reqwest::header::HeaderName>(),
                value.parse::<reqwest::header::HeaderValue>(),
            ) {
                request = request.header(name, val);
            }
        }

        // Also forward mesh-specific headers from upstream
        for &header_name in &["x-request-id", "x-b3-traceid", "x-b3-spanid"] {
            if let Some(value) = upstream_headers.get(header_name) {
                if let Ok(name) = header_name.parse::<reqwest::header::HeaderName>() {
                    request = request.header(name, value.to_str().unwrap_or_default());
                }
            }
        }

        let response = request.send().await?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            tracing::error!(
                status = %status,
                body = %body,
                "inventory service returned error"
            );
            anyhow::bail!("inventory service error: {}", status);
        }

        let result: HashMap<String, u32> = response.json().await?;
        Ok(result)
    }
}

/// Helper to inject OTel context into HTTP headers
struct HeaderInjector {
    headers: Vec<(String, String)>,
}

impl HeaderInjector {
    fn new() -> Self {
        Self {
            headers: Vec::new(),
        }
    }
}

impl opentelemetry::propagation::Injector for HeaderInjector {
    fn set(&mut self, key: &str, value: String) {
        self.headers.push((key.to_string(), value));
    }
}
```

## Custom Span Attributes for Business Context

Generic spans like "HTTP POST /api/orders" are useless for debugging business problems. Add domain-specific attributes:

```rust
// src/span_enrichment.rs

use tracing::Span;

/// Add business context to spans.
/// When you're looking at a trace in Jaeger, these fields
/// let you filter and search by business concepts, not just
/// HTTP paths.
pub fn enrich_order_span(
    span: &Span,
    order_id: &str,
    customer_id: &str,
    order_total_cents: i64,
    item_count: usize,
) {
    span.record("order.id", order_id);
    span.record("order.customer_id", customer_id);
    span.record("order.total_cents", order_total_cents);
    span.record("order.item_count", item_count as i64);
}

/// Example: trace a database query with the actual SQL (sanitized)
/// and result count.
#[tracing::instrument(
    name = "db.query",
    skip(pool),
    fields(
        db.system = "postgresql",
        db.statement = %query_name,
        db.result_count = tracing::field::Empty,
    )
)]
pub async fn traced_query<T>(
    pool: &sqlx::PgPool,
    query_name: &str,
    query: sqlx::query::Query<'_, sqlx::Postgres, sqlx::postgres::PgArguments>,
) -> Result<Vec<T>, sqlx::Error>
where
    T: for<'r> sqlx::FromRow<'r, sqlx::postgres::PgRow> + Send + Unpin,
{
    let rows: Vec<T> = query
        .fetch_all(pool)
        .await?
        .into_iter()
        .collect();

    Span::current().record("db.result_count", rows.len() as i64);

    Ok(rows)
}
```

## Error Recording

When things go wrong, you want the trace to capture *why*. Not just "status 500" — the actual error chain.

```rust
// src/error_tracing.rs

use tracing::Span;

/// Record an error on the current span.
/// This shows up as a red span in Jaeger/Tempo,
/// making failures immediately visible in the trace waterfall.
pub fn record_error(error: &dyn std::error::Error) {
    let span = Span::current();

    span.record("otel.status_code", "ERROR");
    span.record("otel.status_message", &tracing::field::display(error));

    // Walk the error chain
    let mut source = error.source();
    let mut depth = 0;
    while let Some(cause) = source {
        tracing::error!(
            error.cause_depth = depth,
            error.cause = %cause,
            "error chain"
        );
        source = cause.source();
        depth += 1;
    }
}

/// Middleware that automatically records HTTP errors on the trace span.
use axum::{extract::Request, middleware::Next, response::Response};

pub async fn error_recording_middleware(
    request: Request,
    next: Next,
) -> Response {
    let response = next.run(request).await;

    if response.status().is_server_error() {
        let span = Span::current();
        span.record("otel.status_code", "ERROR");
        span.record(
            "otel.status_message",
            &format!("HTTP {}", response.status().as_u16()),
        );
    }

    response
}
```

## What a Trace Looks Like

When everything is wired up, a single trace for creating an order looks like this:

```
[gateway] POST /api/orders                    ████████████████████████ 450ms
  [order-service] create_order                  ██████████████████████ 440ms
    [order-service] check_inventory               ██████████████ 280ms
      [inventory-service] POST /api/check           ████████████ 270ms
        [inventory-service] db.query                  ██████ 120ms
    [order-service] save_order                                   ████ 80ms
      [order-service] db.query                                    ███ 60ms
    [order-service] publish_event                                      █ 5ms
```

At a glance: the inventory service's database query is the bottleneck — 120ms out of 450ms total. Without tracing, you'd be guessing.

## Sampling Strategies

Don't trace everything in production. You'll drown in data and your tracing backend will drown in costs.

```rust
// src/sampling.rs

use opentelemetry_sdk::trace::Sampler;

/// Sampling strategies for different environments.
pub fn sampler_for_env(env: &str) -> Sampler {
    match env {
        "development" => {
            // Trace everything in dev
            Sampler::AlwaysOn
        }
        "staging" => {
            // Trace 50% in staging
            Sampler::TraceIdRatioBased(0.5)
        }
        "production" => {
            // Trace 10% normally.
            // For debugging specific issues, use head-based sampling:
            // send x-force-trace: true header to always trace a request.
            Sampler::TraceIdRatioBased(0.1)
        }
        _ => Sampler::AlwaysOff,
    }
}
```

One more trick: always trace errors. Even if you're sampling 10%, any request that results in a 5xx should be fully traced. Most tracing backends support tail-based sampling for this — the collector sees the full trace and decides to keep it if any span has an error.

## Common Pitfalls

**Not propagating context.** If even one service in the chain doesn't forward trace headers, the trace breaks in half. You get two separate partial traces instead of one complete picture. That inventory client code with the `HeaderInjector`? It's not optional.

**Over-instrumenting.** You don't need a span for every function call. Focus on I/O boundaries — HTTP calls, database queries, message queue operations. A trace with 500 spans for a single request is as useless as no trace at all.

**Ignoring cardinality.** Don't put user IDs or request bodies in span *names*. Put them in span *attributes*. High-cardinality span names blow up your tracing backend's index.

**Forgetting to flush on shutdown.** `shutdown_telemetry()` isn't just cleanup — it flushes all pending spans. Skip it and you'll lose the last few seconds of trace data every time a pod restarts. In production, those are often the most interesting spans (the ones right before the crash).

Next lesson — testing microservices. Because none of this matters if you can't verify it works.
