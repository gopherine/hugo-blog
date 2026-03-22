---
title: "Lesson 6: Graceful Shutdown — Draining connections cleanly"
author: Atharva Pandey
series: "Rust Deployment & Operations"
lesson: 6
keywords: [Rust, rust, deployment, graceful shutdown, SIGTERM, drain, connection draining, tokio, signal handling]
tags: [Rust tutorial, rust, deployment, devops]
date: '2025-05-02T19:45:00.000Z'
---

I deployed a new version of a payment service once, and for about 3 seconds during the rollout, a handful of transactions just... vanished. They weren't in the database. They weren't in the error logs. The old pods received the requests, started processing them, and then Kubernetes killed the pods before they finished. SIGKILL doesn't ask nicely — it just terminates the process. Those transactions were gone.

Graceful shutdown is the solution. When your service receives SIGTERM (Kubernetes's way of saying "please stop"), it should stop accepting new requests, finish processing in-flight requests, flush any buffered data, close connections cleanly, and *then* exit. Get this right and you get zero-downtime deployments. Get it wrong and you get data loss.

## How Kubernetes Shutdown Works

Understanding the sequence is critical:

1. Kubernetes sends SIGTERM to your process
2. Kubernetes simultaneously removes the pod from the Service's endpoint list
3. Your process has `terminationGracePeriodSeconds` (default 30 seconds) to exit
4. If your process hasn't exited by then, Kubernetes sends SIGKILL

There's a race condition in step 2. Removing the pod from endpoints takes time to propagate through kube-proxy and the cluster's network rules. For a few seconds after SIGTERM, the pod might still receive new requests. Your shutdown handler needs to account for this.

## The Basic Pattern

Here's the minimal graceful shutdown with Axum and tokio:

```rust
use axum::{Router, routing::get};
use std::net::SocketAddr;
use tokio::signal;
use tracing::info;

async fn shutdown_signal() {
    let ctrl_c = async {
        signal::ctrl_c()
            .await
            .expect("failed to install Ctrl+C handler");
    };

    #[cfg(unix)]
    let terminate = async {
        signal::unix::signal(signal::unix::SignalKind::terminate())
            .expect("failed to install SIGTERM handler")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => info!("received Ctrl+C"),
        _ = terminate => info!("received SIGTERM"),
    }
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt::init();

    let app = Router::new()
        .route("/health", get(|| async { "ok" }));

    let addr = SocketAddr::from(([0, 0, 0, 0], 3000));
    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();

    info!(%addr, "listening");

    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await
        .unwrap();

    info!("server shut down cleanly");
}
```

`axum::serve` with `with_graceful_shutdown` does the heavy lifting: when the signal fires, it stops accepting new connections and waits for existing connections to complete. This handles the most common case.

But real services have more than an HTTP server. You've got database connection pools, background workers, message consumers, OpenTelemetry exporters, and metrics flush buffers. All of these need orderly shutdown.

## Production-Grade Shutdown

Here's the pattern I use for services with multiple subsystems:

```rust
use axum::{Router, routing::get, extract::State};
use sqlx::PgPool;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;
use tokio::sync::watch;
use tracing::{info, warn, error};

#[derive(Clone)]
struct AppState {
    db: PgPool,
    ready: Arc<AtomicBool>,
    shutdown_tx: watch::Sender<bool>,
    shutdown_rx: watch::Receiver<bool>,
}

async fn shutdown_signal() {
    let ctrl_c = async {
        tokio::signal::ctrl_c().await.unwrap();
    };

    #[cfg(unix)]
    let terminate = async {
        tokio::signal::unix::signal(
            tokio::signal::unix::SignalKind::terminate(),
        )
        .unwrap()
        .recv()
        .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {},
        _ = terminate => {},
    }
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt::init();

    let db = PgPool::connect("postgres://localhost/mydb")
        .await
        .expect("failed to connect to database");

    let (shutdown_tx, shutdown_rx) = watch::channel(false);

    let state = AppState {
        db: db.clone(),
        ready: Arc::new(AtomicBool::new(true)),
        shutdown_tx,
        shutdown_rx: shutdown_rx.clone(),
    };

    // Start background workers
    let worker_handle = tokio::spawn(background_worker(
        db.clone(),
        shutdown_rx.clone(),
    ));

    // Build and start the server
    let app = Router::new()
        .route("/health", get(|| async { "ok" }))
        .with_state(state.clone());

    let listener = tokio::net::TcpListener::bind("0.0.0.0:3000")
        .await
        .unwrap();

    info!("listening on 0.0.0.0:3000");

    // Run server with graceful shutdown
    let server = axum::serve(listener, app)
        .with_graceful_shutdown(async {
            shutdown_signal().await;
            info!("shutdown signal received, starting graceful shutdown");

            // Phase 1: Mark as not ready (stop receiving new traffic)
            state.ready.store(false, Ordering::SeqCst);
            info!("marked as not ready");

            // Give Kubernetes time to propagate endpoint removal
            tokio::time::sleep(Duration::from_secs(5)).await;

            // Phase 2: Signal background workers to stop
            let _ = state.shutdown_tx.send(true);
            info!("signaled background workers to stop");
        });

    // Wait for the server to finish
    if let Err(e) = server.await {
        error!("server error: {}", e);
    }

    // Phase 3: Wait for background workers with a timeout
    info!("waiting for background workers to finish");
    match tokio::time::timeout(Duration::from_secs(10), worker_handle).await {
        Ok(Ok(())) => info!("background workers finished"),
        Ok(Err(e)) => error!("background worker panicked: {}", e),
        Err(_) => warn!("background workers didn't finish in time"),
    }

    // Phase 4: Close database pool
    info!("closing database pool");
    db.close().await;

    // Phase 5: Flush telemetry
    info!("shutdown complete");
}

async fn background_worker(
    db: PgPool,
    mut shutdown: watch::Receiver<bool>,
) {
    let mut interval = tokio::time::interval(Duration::from_secs(5));

    loop {
        tokio::select! {
            _ = interval.tick() => {
                // Do periodic work
                if let Err(e) = process_batch(&db).await {
                    warn!("batch processing error: {}", e);
                }
            }
            _ = shutdown.changed() => {
                if *shutdown.borrow() {
                    info!("background worker shutting down");
                    // Finish any in-progress work
                    break;
                }
            }
        }
    }
}

async fn process_batch(db: &PgPool) -> Result<(), sqlx::Error> {
    // process some batch work
    Ok(())
}
```

Let me walk through the shutdown phases.

### Phase 1: Mark as Not Ready

Flip the readiness flag immediately. The readiness probe from Lesson 5 starts returning 503. Kubernetes removes the pod from the endpoint list. But this takes time to propagate — up to a few seconds for kube-proxy to update iptables rules and for load balancers to notice.

### Phase 2: Sleep, Then Signal Workers

The 5-second sleep is deliberate. It gives the network time to stop routing new requests to this pod. Without it, you might reject requests that arrive during the propagation delay. This is the annoying part — you're waiting for an eventually-consistent distributed system to catch up.

After the sleep, signal all background workers to stop. The `watch` channel is perfect for this — it's cheap, clone-friendly, and multiple receivers can listen.

### Phase 3: Wait for Workers with a Timeout

Background workers might be mid-task. Give them a reasonable deadline to finish. If they don't finish in 10 seconds, log a warning and move on. You'd rather lose a single batch iteration than hang forever and get SIGKILLed.

### Phase 4: Close Resources

Database pools, Redis connections, file handles — close them in reverse initialization order. `sqlx::PgPool::close()` waits for active queries to complete, then closes all connections.

### Phase 5: Flush Telemetry

This is where OpenTelemetry's `TracerProvider::shutdown()` goes. If you skip this, the last batch of traces disappears.

## Handling In-Flight Requests

Axum's graceful shutdown handles HTTP connections, but what about long-running requests? If a request takes 60 seconds and your termination grace period is 30, it'll get killed.

Two approaches:

### Approach 1: Request Deadlines

Set a maximum request duration that's shorter than your grace period:

```rust
use axum::middleware;
use std::time::Duration;
use tower_http::timeout::TimeoutLayer;

let app = Router::new()
    .route("/api/process", get(process_handler))
    .layer(TimeoutLayer::new(Duration::from_secs(25)));
    // Grace period is 30s, so 25s max request duration
    // leaves 5s for cleanup
```

### Approach 2: Cancellation Tokens

For truly long-running operations, use `CancellationToken` from `tokio-util`:

```rust
use tokio_util::sync::CancellationToken;

#[derive(Clone)]
struct AppState {
    cancel: CancellationToken,
    // ... other fields
}

async fn long_running_handler(
    State(state): State<AppState>,
) -> impl IntoResponse {
    let result = tokio::select! {
        result = do_expensive_work() => result,
        _ = state.cancel.cancelled() => {
            return (StatusCode::SERVICE_UNAVAILABLE,
                    "service shutting down").into_response();
        }
    };

    Json(result).into_response()
}

// In shutdown handler:
// state.cancel.cancel();
```

The `CancellationToken` approach is more surgical. Instead of timing out the entire request, you cancel the expensive part and return a meaningful error to the client. The client can retry against a healthy instance.

## WebSocket and Streaming Connections

Long-lived connections like WebSockets need special attention. They won't naturally complete when you stop accepting new connections:

```rust
async fn websocket_handler(
    ws: axum::extract::WebSocketUpgrade,
    State(state): State<AppState>,
) -> impl IntoResponse {
    ws.on_upgrade(|socket| handle_websocket(socket, state))
}

async fn handle_websocket(
    mut socket: axum::extract::ws::WebSocket,
    state: AppState,
) {
    let mut shutdown = state.shutdown_rx.clone();

    loop {
        tokio::select! {
            msg = socket.recv() => {
                match msg {
                    Some(Ok(msg)) => {
                        // handle message
                    }
                    _ => break, // client disconnected
                }
            }
            _ = shutdown.changed() => {
                if *shutdown.borrow() {
                    // Send close frame
                    let _ = socket.send(
                        axum::extract::ws::Message::Close(None)
                    ).await;
                    break;
                }
            }
        }
    }
}
```

Send a WebSocket close frame before shutting down. Well-behaved clients will reconnect to a healthy instance.

## Kubernetes terminationGracePeriodSeconds

Match your grace period to your shutdown timeline:

```yaml
spec:
  terminationGracePeriodSeconds: 45
  containers:
    - name: myservice
      lifecycle:
        preStop:
          exec:
            command: ["sleep", "5"]
```

Timeline:
- t=0: SIGTERM received
- t=0-5: preStop hook (extra buffer for endpoint propagation)
- t=5: Your shutdown handler runs
- t=5-10: Wait for in-flight requests
- t=10-15: Drain background workers
- t=15-20: Close resources, flush telemetry
- t=45: SIGKILL if still running (shouldn't happen)

The `preStop` hook is another layer of defense against the endpoint propagation race. Some teams use it instead of the sleep in the shutdown handler — either works.

## Testing Graceful Shutdown

Don't wait for production to discover your shutdown handler doesn't work. Test it:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Instant;

    #[tokio::test]
    async fn test_graceful_shutdown() {
        let (shutdown_tx, shutdown_rx) = watch::channel(false);

        let state = AppState {
            ready: Arc::new(AtomicBool::new(true)),
            shutdown_tx: shutdown_tx.clone(),
            shutdown_rx,
            // ... test doubles for other fields
        };

        let app = Router::new()
            .route("/health", get(|| async { "ok" }))
            .with_state(state.clone());

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .unwrap();
        let addr = listener.local_addr().unwrap();

        let server = tokio::spawn(async move {
            axum::serve(listener, app)
                .with_graceful_shutdown(async move {
                    let mut rx = state.shutdown_rx.clone();
                    rx.changed().await.ok();
                })
                .await
                .unwrap();
        });

        // Verify service is running
        let resp = reqwest::get(format!("http://{}/health", addr))
            .await
            .unwrap();
        assert_eq!(resp.status(), 200);

        // Trigger shutdown
        let start = Instant::now();
        shutdown_tx.send(true).unwrap();

        // Server should stop
        server.await.unwrap();
        let elapsed = start.elapsed();

        // Should shut down quickly when there are no in-flight requests
        assert!(elapsed < Duration::from_secs(2));
    }
}
```

## The Shutdown Checklist

Every production Rust service should handle these on SIGTERM:

1. Mark as not-ready (readiness probe returns 503)
2. Wait for endpoint propagation (sleep 3-5 seconds)
3. Stop accepting new connections
4. Wait for in-flight HTTP requests to complete
5. Signal background workers to stop
6. Wait for background workers with a timeout
7. Close database/cache connections
8. Flush metrics and trace buffers
9. Exit with status code 0

Miss any of these and you'll have some interesting debugging sessions at 3 AM.

## What's Next

We've been hardcoding things like database URLs and port numbers. That works for examples, but real services need proper configuration management. Next lesson: configuration from environment variables, files, and feature flags — building a configuration system that works from local development through staging to production.
