---
title: "Lesson 5: Service Mesh Integration — Istio, Linkerd, and Rust"
author: Atharva Pandey
series: "Rust Microservices Patterns"
lesson: 5
keywords: [Rust, rust, microservices, service mesh, Istio, Linkerd, sidecar proxy, mTLS, traffic management]
tags: [Rust tutorial, rust, microservices, architecture]
date: '2025-06-09T16:21:00.000Z'
---

I'll be honest — when someone first pitched "service mesh" to me, I thought it was over-engineered marketing. You're telling me I need a sidecar proxy bolted onto every pod, a control plane to manage those proxies, and custom CRDs to configure traffic routing... just to do what a load balancer and some retry logic could handle?

Then I ran a fleet of 20+ services in production. mTLS between everything? Doing that in application code is painful. Per-route retry policies? Circuit breaking with consistent configuration? Gradual traffic shifting for canary deploys? At that scale, doing it all in app code means doing it differently in every service, with different bugs in each implementation.

That's when the mesh clicked. It's not about adding complexity — it's about moving cross-cutting infrastructure concerns out of your application code and into a consistent, observable layer.

## What a Service Mesh Actually Does

A service mesh handles four things:

1. **Traffic management** — Routing, load balancing, retries, timeouts, circuit breaking
2. **Security** — Mutual TLS, authorization policies, certificate rotation
3. **Observability** — Metrics, traces, and access logs without code changes
4. **Resilience** — Fault injection, rate limiting, outlier detection

The key insight: your Rust service doesn't need to know about any of this. The sidecar proxy (Envoy in Istio, linkerd-proxy in Linkerd) intercepts all network traffic and applies these policies transparently.

## Making Rust Services Mesh-Ready

Your Rust services don't need a mesh-specific SDK. They just need to be good citizens: propagate headers, expose health endpoints, and emit structured logs.

```rust
// src/mesh/headers.rs

use axum::http::HeaderMap;

/// Headers that must be propagated for distributed tracing
/// and mesh routing to work correctly.
/// Both Istio and Linkerd rely on these.
pub const TRACE_HEADERS: &[&str] = &[
    "x-request-id",
    "x-b3-traceid",
    "x-b3-spanid",
    "x-b3-parentspanid",
    "x-b3-sampled",
    "x-b3-flags",
    "b3",
    // Istio-specific
    "x-envoy-attempt-count",
    "x-envoy-decorator-operation",
    // W3C Trace Context (modern standard)
    "traceparent",
    "tracestate",
];

/// Extract trace headers from an incoming request
/// so they can be forwarded to downstream calls.
pub fn extract_trace_headers(headers: &HeaderMap) -> HeaderMap {
    let mut trace_headers = HeaderMap::new();

    for &header_name in TRACE_HEADERS {
        if let Some(value) = headers.get(header_name) {
            if let Ok(name) = header_name.parse() {
                trace_headers.insert(name, value.clone());
            }
        }
    }

    trace_headers
}

/// Middleware that extracts and stores trace context
/// for use in downstream calls.
use axum::{extract::Request, middleware::Next, response::Response};
use std::sync::Arc;
use tokio::sync::RwLock;

#[derive(Clone, Default)]
pub struct TraceContext {
    pub headers: Arc<RwLock<HeaderMap>>,
}

pub async fn trace_propagation_middleware(
    mut request: Request,
    next: Next,
) -> Response {
    let trace_headers = extract_trace_headers(request.headers());

    let ctx = TraceContext {
        headers: Arc::new(RwLock::new(trace_headers)),
    };

    request.extensions_mut().insert(ctx);

    next.run(request).await
}
```

## Health Checks for the Mesh

Both Istio and Linkerd use health probes to manage traffic. Your service needs three endpoints:

```rust
// src/mesh/health.rs

use axum::{
    extract::State,
    http::StatusCode,
    response::IntoResponse,
    routing::get,
    Json, Router,
};
use serde::Serialize;
use std::sync::Arc;
use tokio::sync::RwLock;

#[derive(Clone)]
pub struct HealthState {
    /// Set to false during graceful shutdown
    pub ready: Arc<RwLock<bool>>,
    /// Tracks database connectivity, essential dependencies
    pub dependencies: Arc<RwLock<DependencyHealth>>,
}

#[derive(Clone, Serialize)]
pub struct DependencyHealth {
    pub database: bool,
    pub cache: bool,
    pub message_bus: bool,
}

impl HealthState {
    pub fn new() -> Self {
        Self {
            ready: Arc::new(RwLock::new(true)),
            dependencies: Arc::new(RwLock::new(DependencyHealth {
                database: true,
                cache: true,
                message_bus: true,
            })),
        }
    }
}

/// Liveness probe — is the process alive?
/// Return 200 unless the process is in a broken state.
/// The mesh uses this to decide whether to restart the pod.
async fn liveness() -> StatusCode {
    StatusCode::OK
}

/// Readiness probe — should this instance receive traffic?
/// Return 503 during startup, shutdown, or when dependencies are down.
/// The mesh uses this to remove the pod from load balancing.
async fn readiness(State(state): State<HealthState>) -> impl IntoResponse {
    let ready = *state.ready.read().await;
    let deps = state.dependencies.read().await;

    if ready && deps.database && deps.cache {
        (StatusCode::OK, Json(serde_json::json!({"status": "ready"})))
    } else {
        (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({
                "status": "not_ready",
                "database": deps.database,
                "cache": deps.cache,
                "message_bus": deps.message_bus,
            })),
        )
    }
}

/// Startup probe — has the service finished initializing?
/// Kubernetes won't send liveness/readiness checks until this passes.
async fn startup(State(state): State<HealthState>) -> StatusCode {
    let deps = state.dependencies.read().await;
    if deps.database {
        StatusCode::OK
    } else {
        StatusCode::SERVICE_UNAVAILABLE
    }
}

pub fn health_routes(state: HealthState) -> Router {
    Router::new()
        .route("/healthz", get(liveness))
        .route("/readyz", get(readiness))
        .route("/startupz", get(startup))
        .with_state(state)
}
```

## Graceful Shutdown

This is where people get bitten. When Kubernetes sends SIGTERM, your pod has a grace period (usually 30 seconds) to finish in-flight requests. But the mesh's sidecar proxy might still be routing traffic to you. You need to:

1. Stop accepting new connections
2. Mark yourself as not ready (so the mesh stops sending traffic)
3. Wait for in-flight requests to complete
4. Shut down

```rust
// src/mesh/shutdown.rs

use std::sync::Arc;
use tokio::sync::RwLock;
use tracing::info;

pub struct GracefulShutdown {
    health: super::health::HealthState,
}

impl GracefulShutdown {
    pub fn new(health: super::health::HealthState) -> Self {
        Self { health }
    }

    /// Call this when SIGTERM is received.
    /// The sequence matters — get it wrong and you'll drop requests.
    pub async fn initiate(&self) {
        info!("shutdown initiated — marking as not ready");

        // 1. Mark as not ready so the mesh stops sending new traffic
        {
            let mut ready = self.health.ready.write().await;
            *ready = false;
        }

        // 2. Wait for the mesh to notice and drain connections.
        //    Istio needs a few seconds to propagate the readiness change.
        //    This sleep prevents request drops during the propagation window.
        info!("waiting for mesh to drain connections...");
        tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;

        // 3. At this point, the Axum server's graceful_shutdown
        //    should handle completing in-flight requests.
        info!("ready for server shutdown");
    }
}

/// Wire up the shutdown signal.
pub async fn shutdown_signal(shutdown: Arc<GracefulShutdown>) {
    let ctrl_c = async {
        tokio::signal::ctrl_c()
            .await
            .expect("failed to install Ctrl+C handler");
    };

    #[cfg(unix)]
    let terminate = async {
        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("failed to install SIGTERM handler")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {},
        _ = terminate => {},
    }

    shutdown.initiate().await;
}
```

## Putting It All Together

```rust
// src/main.rs

use axum::Router;
use std::sync::Arc;
use tracing_subscriber::EnvFilter;

mod mesh;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env())
        .json() // JSON logs — mesh observability tools expect structured output
        .init();

    let health_state = mesh::health::HealthState::new();
    let shutdown = Arc::new(mesh::shutdown::GracefulShutdown::new(health_state.clone()));

    let app = Router::new()
        .merge(mesh::health::health_routes(health_state))
        .layer(axum::middleware::from_fn(
            mesh::headers::trace_propagation_middleware,
        ));
    // .merge(your_api_routes())

    let listener = tokio::net::TcpListener::bind("0.0.0.0:8080").await?;
    tracing::info!("server listening on 0.0.0.0:8080");

    axum::serve(listener, app)
        .with_graceful_shutdown(mesh::shutdown::shutdown_signal(shutdown))
        .await?;

    Ok(())
}
```

## Istio-Specific Configuration

Your Rust code is mesh-agnostic, but you'll need Kubernetes manifests to configure Istio's behavior:

```yaml
# k8s/virtual-service.yaml
# Controls routing, retries, and timeouts for your service.
apiVersion: networking.istio.io/v1beta1
kind: VirtualService
metadata:
  name: order-service
spec:
  hosts:
    - order-service
  http:
    - route:
        - destination:
            host: order-service
            subset: stable
          weight: 90
        - destination:
            host: order-service
            subset: canary
          weight: 10
      retries:
        attempts: 3
        perTryTimeout: 2s
        retryOn: 5xx,reset,connect-failure,retriable-4xx
      timeout: 10s

---
# k8s/destination-rule.yaml
# Defines subsets and connection pool settings.
apiVersion: networking.istio.io/v1beta1
kind: DestinationRule
metadata:
  name: order-service
spec:
  host: order-service
  trafficPolicy:
    connectionPool:
      tcp:
        maxConnections: 100
      http:
        h2UpgradePolicy: DEFAULT
        maxRequestsPerConnection: 100
    outlierDetection:
      consecutive5xxErrors: 5
      interval: 30s
      baseEjectionTime: 30s
      maxEjectionPercent: 50
  subsets:
    - name: stable
      labels:
        version: stable
    - name: canary
      labels:
        version: canary

---
# k8s/peer-authentication.yaml
# Enforce mTLS between all services.
apiVersion: security.istio.io/v1beta1
kind: PeerAuthentication
metadata:
  name: default
  namespace: production
spec:
  mtls:
    mode: STRICT
```

Notice what we didn't do in our Rust code: TLS configuration, retry logic, circuit breaking, canary traffic splitting. All of that is declarative YAML managed by platform engineers. Your Rust service just handles business logic.

## Linkerd vs. Istio — My Take

I've used both in production. Quick comparison:

**Linkerd** is simpler, lighter, and Rust-native (linkerd-proxy is written in Rust, which I appreciate). It installs in minutes, uses fewer resources, and the defaults are sane. It does mTLS, retries, metrics, and basic traffic splitting well.

**Istio** is more powerful and more complex. It has richer traffic management (fault injection, header-based routing, traffic mirroring), deeper security (fine-grained authorization policies, external auth), and a bigger ecosystem. It also uses more resources and has more configuration surface area to get wrong.

My rule: if you have fewer than 15 services, start with Linkerd. If you need advanced traffic management or have a platform team that can dedicate time to mesh operations, Istio is the better long-term investment.

## An HTTP Client That Plays Nice with the Mesh

When your Rust service calls other services through the mesh, use standard HTTP — the sidecar handles the rest. But you need to forward those trace headers:

```rust
// src/mesh/client.rs

use reqwest::{Client, Response};
use axum::http::HeaderMap;
use std::time::Duration;

pub struct MeshAwareClient {
    client: Client,
}

impl MeshAwareClient {
    pub fn new() -> Self {
        Self {
            client: Client::builder()
                // Don't set TLS config — the mesh handles mTLS
                // Use short timeouts — the mesh has its own retry/timeout layer
                .connect_timeout(Duration::from_secs(2))
                .timeout(Duration::from_secs(10))
                // Use HTTP/2 for efficiency through the sidecar
                .http2_prior_knowledge()
                .build()
                .expect("failed to build HTTP client"),
        }
    }

    pub async fn get(
        &self,
        url: &str,
        trace_headers: &HeaderMap,
    ) -> Result<Response, reqwest::Error> {
        let mut request = self.client.get(url);

        // Forward all trace headers
        for (key, value) in trace_headers.iter() {
            request = request.header(key.clone(), value.clone());
        }

        request.send().await
    }

    pub async fn post(
        &self,
        url: &str,
        body: impl serde::Serialize,
        trace_headers: &HeaderMap,
    ) -> Result<Response, reqwest::Error> {
        let mut request = self.client.post(url).json(&body);

        for (key, value) in trace_headers.iter() {
            request = request.header(key.clone(), value.clone());
        }

        request.send().await
    }
}
```

Key detail: don't configure TLS in your client. The mesh sidecar upgrades plaintext connections to mTLS transparently. If you configure TLS in your app *and* the mesh, you get double encryption — wasteful and sometimes broken.

## Observability for Free

One of the biggest wins of a mesh: you get golden signal metrics (latency, traffic, errors, saturation) for every service without writing a single line of instrumentation code. The sidecar proxy emits metrics for every request it handles.

But — and this is important — mesh metrics only cover the *network* layer. They'll tell you that a request to `/api/orders` took 500ms, but not that 450ms of that was a slow database query. You still need application-level tracing and metrics for the full picture. We'll cover that in Lesson 6.

## What I Got Wrong Early On

A few things I wish someone had told me:

**The 5-second sleep during shutdown isn't optional.** Without it, the mesh is still routing requests to your pod when it starts shutting down. Those requests get connection-refused errors. The sleep gives the mesh time to propagate the readiness change.

**Don't retry at both layers.** If your Rust code retries 3 times and the mesh retries 3 times, a single failure turns into 9 requests. Pick one layer to own retries. I let the mesh handle transient network retries and handle business-level retries in the app.

**Service-to-service calls go through `localhost`.** The mesh intercepts traffic at the pod network level. Your service calls `http://order-service:8080/api/orders` and the sidecar handles DNS resolution, load balancing, and mTLS. Don't try to be clever with direct pod IPs.

Next up — distributed tracing. Because when a request touches five services, you need to know exactly where it spent its time.
