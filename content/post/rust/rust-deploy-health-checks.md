---
title: "Lesson 5: Health Checks and Readiness Probes — Production liveness"
author: Atharva Pandey
series: "Rust Deployment & Operations"
lesson: 5
keywords: [Rust, rust, deployment, health checks, readiness probes, liveness, Kubernetes, load balancer]
tags: [Rust tutorial, rust, deployment, devops]
date: '2025-04-30T14:05:00.000Z'
---

A service I maintained once passed all its health checks while silently dropping 30% of incoming requests. The health endpoint returned 200 OK every time Kubernetes asked. The database connection pool was exhausted, the service couldn't process anything, but that little `/health` endpoint — which didn't touch the database — happily reported everything was fine.

That's when I learned the difference between a health check that checks health and one that just says "the process is running." They're very different things, and getting this wrong means your orchestrator keeps sending traffic to a broken instance instead of replacing it.

## Liveness vs Readiness: Two Different Questions

Kubernetes (and most modern orchestrators) ask two distinct questions about your service:

**Liveness: "Is this process fundamentally broken?"** If the answer is no, kill it and start a new one. Liveness failures mean "this instance is wedged and cannot recover on its own." Think deadlocks, infinite loops, corrupted state.

**Readiness: "Can this instance handle traffic right now?"** If the answer is no, stop sending it requests but don't kill it. Readiness failures are temporary — the service is still alive but busy, warming up, or waiting for a dependency. Once it's ready again, traffic resumes.

Getting these confused causes real problems. If your readiness probe is too aggressive, Kubernetes stops sending traffic during brief load spikes, making the remaining instances even more overloaded — a cascading failure. If your liveness probe checks dependencies, a database blip kills all your service instances simultaneously, creating an outage from what should have been a minor hiccup.

## Implementing Health Checks in Axum

Let's build a proper health check system. I'll use Axum, but the patterns apply to any framework:

```rust
use axum::{
    Router,
    routing::get,
    extract::State,
    http::StatusCode,
    response::IntoResponse,
    Json,
};
use serde::Serialize;
use sqlx::PgPool;
use std::sync::Arc;
use std::time::Instant;

#[derive(Clone)]
struct AppState {
    db: PgPool,
    started_at: Instant,
    ready: Arc<std::sync::atomic::AtomicBool>,
}

#[derive(Serialize)]
struct HealthResponse {
    status: &'static str,
    uptime_seconds: u64,
}

#[derive(Serialize)]
struct ReadinessResponse {
    status: &'static str,
    checks: ReadinessChecks,
}

#[derive(Serialize)]
struct ReadinessChecks {
    database: CheckResult,
}

#[derive(Serialize)]
struct CheckResult {
    status: &'static str,
    latency_ms: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}
```

### The Liveness Endpoint

Liveness should be cheap and fast. Don't check external dependencies — just confirm the process isn't stuck:

```rust
async fn liveness(State(state): State<AppState>) -> impl IntoResponse {
    let uptime = state.started_at.elapsed().as_secs();

    (StatusCode::OK, Json(HealthResponse {
        status: "alive",
        uptime_seconds: uptime,
    }))
}
```

That's it. If this endpoint responds, the process is alive. If it doesn't — because the event loop is blocked, the thread is deadlocked, or something truly catastrophic happened — Kubernetes will restart the pod. Simple.

Some people add a self-check here — like spawning a background task that updates a timestamp, and having liveness verify the timestamp is recent. That catches event-loop stalls:

```rust
use std::sync::atomic::{AtomicU64, Ordering};
use tokio::time::{interval, Duration};

struct AppState {
    db: PgPool,
    started_at: Instant,
    last_heartbeat: Arc<AtomicU64>,
    ready: Arc<std::sync::atomic::AtomicBool>,
}

async fn heartbeat_task(last_heartbeat: Arc<AtomicU64>) {
    let mut ticker = interval(Duration::from_secs(1));
    loop {
        ticker.tick().await;
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs();
        last_heartbeat.store(now, Ordering::Relaxed);
    }
}

async fn liveness(State(state): State<AppState>) -> impl IntoResponse {
    let last = state.last_heartbeat.load(Ordering::Relaxed);
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();

    if now - last > 5 {
        return (StatusCode::SERVICE_UNAVAILABLE, Json(HealthResponse {
            status: "stalled",
            uptime_seconds: state.started_at.elapsed().as_secs(),
        }));
    }

    (StatusCode::OK, Json(HealthResponse {
        status: "alive",
        uptime_seconds: state.started_at.elapsed().as_secs(),
    }))
}
```

If the heartbeat task hasn't updated in 5 seconds, the tokio runtime is probably stalled. Kill it.

### The Readiness Endpoint

Readiness actually checks dependencies. Can we reach the database? Is the cache connected? Are we warmed up?

```rust
async fn readiness(State(state): State<AppState>) -> impl IntoResponse {
    // Quick flag check — allows manual drain
    if !state.ready.load(std::sync::atomic::Ordering::Relaxed) {
        return (StatusCode::SERVICE_UNAVAILABLE, Json(ReadinessResponse {
            status: "not_ready",
            checks: ReadinessChecks {
                database: CheckResult {
                    status: "skipped",
                    latency_ms: 0,
                    error: Some("service draining".to_string()),
                },
            },
        }));
    }

    // Check database
    let db_check = check_database(&state.db).await;

    let overall_status = if db_check.status == "ok" {
        StatusCode::OK
    } else {
        StatusCode::SERVICE_UNAVAILABLE
    };

    (overall_status, Json(ReadinessResponse {
        status: if overall_status == StatusCode::OK { "ready" } else { "not_ready" },
        checks: ReadinessChecks {
            database: db_check,
        },
    }))
}

async fn check_database(pool: &PgPool) -> CheckResult {
    let start = Instant::now();

    match sqlx::query("SELECT 1")
        .fetch_one(pool)
        .await
    {
        Ok(_) => CheckResult {
            status: "ok",
            latency_ms: start.elapsed().as_millis() as u64,
            error: None,
        },
        Err(e) => CheckResult {
            status: "error",
            latency_ms: start.elapsed().as_millis() as u64,
            error: Some(e.to_string()),
        },
    }
}
```

A few things to note here:

**The `ready` flag.** This atomic boolean lets you manually mark the service as not-ready during shutdown. When you receive SIGTERM, flip this to `false` before draining connections. Kubernetes stops sending new requests while you finish processing existing ones. We'll use this extensively in the graceful shutdown lesson.

**Timeouts on dependency checks.** The database check should have a timeout — if the DB takes 10 seconds to respond, your readiness probe shouldn't hang for 10 seconds:

```rust
async fn check_database(pool: &PgPool) -> CheckResult {
    let start = Instant::now();

    let result = tokio::time::timeout(
        Duration::from_secs(2),
        sqlx::query("SELECT 1").fetch_one(pool),
    ).await;

    match result {
        Ok(Ok(_)) => CheckResult {
            status: "ok",
            latency_ms: start.elapsed().as_millis() as u64,
            error: None,
        },
        Ok(Err(e)) => CheckResult {
            status: "error",
            latency_ms: start.elapsed().as_millis() as u64,
            error: Some(e.to_string()),
        },
        Err(_) => CheckResult {
            status: "timeout",
            latency_ms: start.elapsed().as_millis() as u64,
            error: Some("database check timed out".to_string()),
        },
    }
}
```

## Wiring Up the Router

```rust
fn app(state: AppState) -> Router {
    Router::new()
        .route("/livez", get(liveness))
        .route("/readyz", get(readiness))
        .route("/api/v1/users", get(list_users))
        // ... application routes
        .with_state(state)
}
```

I use `/livez` and `/readyz` — the `z` suffix is a Kubernetes convention from its own internal endpoints. Some teams use `/health/live` and `/health/ready`. Doesn't matter what you name them — just be consistent across services.

## Kubernetes Configuration

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: myservice
spec:
  replicas: 3
  template:
    spec:
      containers:
        - name: myservice
          image: myservice:latest
          ports:
            - containerPort: 3000
          livenessProbe:
            httpGet:
              path: /livez
              port: 3000
            initialDelaySeconds: 5
            periodSeconds: 10
            timeoutSeconds: 3
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /readyz
              port: 3000
            initialDelaySeconds: 3
            periodSeconds: 5
            timeoutSeconds: 2
            failureThreshold: 2
          startupProbe:
            httpGet:
              path: /livez
              port: 3000
            initialDelaySeconds: 1
            periodSeconds: 2
            failureThreshold: 15
```

Let me explain these numbers:

**`startupProbe`**: This runs during startup only. It gives the service up to 30 seconds (15 failures × 2-second period) to become live. Without this, the liveness probe might kill a slow-starting service before it finishes initializing. Once the startup probe succeeds, it's disabled and liveness/readiness take over.

**`livenessProbe`**: Checks every 10 seconds, with a 3-second timeout. After 3 consecutive failures (30 seconds of being unresponsive), Kubernetes kills the pod. These numbers are conservative — you don't want a brief load spike to trigger a restart.

**`readinessProbe`**: Checks every 5 seconds, more aggressively than liveness. After 2 failures (10 seconds), traffic stops flowing to this pod. Readiness recovers automatically — once the probe passes again, traffic resumes.

## Startup Probes and Initialization

Rust services typically start fast — sub-second for a basic HTTP server. But some services need to warm up caches, run migrations, or establish connection pools. For these, use a startup probe and staged initialization:

```rust
#[tokio::main]
async fn main() {
    init_tracing();

    let db = PgPool::connect(&database_url).await.unwrap();
    sqlx::migrate!().run(&db).await.unwrap();

    let ready = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let state = AppState {
        db: db.clone(),
        started_at: Instant::now(),
        ready: ready.clone(),
        last_heartbeat: Arc::new(AtomicU64::new(0)),
    };

    // Start heartbeat
    tokio::spawn(heartbeat_task(state.last_heartbeat.clone()));

    // Start the server (liveness works immediately)
    let app = app(state);
    let listener = tokio::net::TcpListener::bind("0.0.0.0:3000").await.unwrap();

    // Warm up in the background
    let warm_ready = ready.clone();
    tokio::spawn(async move {
        warm_caches(&db).await;
        warm_ready.store(true, std::sync::atomic::Ordering::Relaxed);
        tracing::info!("service ready");
    });

    axum::serve(listener, app).await.unwrap();
}
```

The liveness probe passes immediately — the process is running and responding. The readiness probe fails until the cache warming completes and `ready` flips to `true`. Kubernetes won't send traffic until the service is genuinely ready.

## A Separate Health Port

Some teams expose health checks on a different port from application traffic. This prevents health checks from competing with application requests under load, and it lets you apply different network policies:

```rust
#[tokio::main]
async fn main() {
    let state = build_state().await;

    // Application server
    let app = Router::new()
        .route("/api/v1/users", get(list_users))
        .with_state(state.clone());

    // Health server on a separate port
    let health = Router::new()
        .route("/livez", get(liveness))
        .route("/readyz", get(readiness))
        .with_state(state);

    let app_listener = tokio::net::TcpListener::bind("0.0.0.0:3000").await.unwrap();
    let health_listener = tokio::net::TcpListener::bind("0.0.0.0:9090").await.unwrap();

    tokio::join!(
        axum::serve(app_listener, app),
        axum::serve(health_listener, health),
    );
}
```

Is this worth the complexity? For high-traffic services, yes. If a burst of traffic saturates your connection pool and queues up requests, you want the health server to still respond promptly so Kubernetes makes accurate decisions.

## Don't Over-Check

I've seen readiness probes that check the database, Redis, three external APIs, and an S3 bucket. If any one of those fails, the service reports not-ready. The problem? If Redis goes down for 5 seconds, *every* instance simultaneously becomes not-ready, and now there are zero healthy instances behind the load balancer. You've turned a minor dependency hiccup into a total outage.

Rule of thumb: only check dependencies that your service *cannot function without*. If you can serve cached data when Redis is down, don't include Redis in your readiness check. If you can queue writes when the database is slow, maybe the DB check should only fail on connection errors, not slow responses.

**Liveness checks:** Never check external dependencies. Only check if the process itself is healthy.

**Readiness checks:** Check only critical dependencies, with reasonable timeouts and thresholds.

## Next Up

Health checks tell your orchestrator when to route traffic — but what happens when the orchestrator decides to *stop* your service? In the next lesson, we'll build graceful shutdown: catching SIGTERM, stopping new request acceptance, draining in-flight requests, and shutting down cleanly. These two systems — health checks and graceful shutdown — work together to give you zero-downtime deployments.
