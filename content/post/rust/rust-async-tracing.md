---
title: "Lesson 18: Tracing Async Code — Context propagation across .await"
author: Atharva Pandey
series: "Async Rust Masterclass"
lesson: 18
keywords: [Rust, rust, async, tokio, tracing, logging, spans, instrumentation, observability]
tags: [Rust tutorial, rust, async, tokio]
date: '2025-02-08T15:43:29.000Z'
---

Debugging async code with `println!` is like debugging a highway pileup by asking each car individually what happened. You get disconnected fragments — "I was going north," "I hit something," "I heard a crash" — but no coherent story. With 500 concurrent tasks all writing to stdout, good luck finding which log line belongs to which request.

The `tracing` crate solves this. It gives you structured, contextual logging that follows your request across tasks, `.await` points, and even service boundaries. I switched from `log` to `tracing` three years ago and haven't looked back.

## Setup

```toml
[dependencies]
tokio = { version = "1", features = ["full"] }
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter", "json"] }
```

## Basic Tracing

```rust
use tracing::{info, warn, error, debug, trace};

#[tokio::main]
async fn main() {
    // Initialize the subscriber (log output handler)
    tracing_subscriber::fmt()
        .with_max_level(tracing::Level::DEBUG)
        .init();

    info!("Server starting");
    debug!(port = 8080, host = "0.0.0.0", "Binding address");

    process_request(42).await;
}

async fn process_request(id: u32) {
    info!(request_id = id, "Processing request");

    match fetch_data(id).await {
        Ok(data) => info!(request_id = id, data_len = data.len(), "Request complete"),
        Err(e) => error!(request_id = id, error = %e, "Request failed"),
    }
}

async fn fetch_data(id: u32) -> Result<String, String> {
    debug!(id, "Fetching data");
    tokio::time::sleep(tokio::time::Duration::from_millis(50)).await;

    if id % 3 == 0 {
        warn!(id, "Slow query detected");
    }

    Ok(format!("data-{id}"))
}
```

Output:
```
2025-01-30T10:00:00.000Z  INFO server starting
2025-01-30T10:00:00.000Z DEBUG binding address port=8080 host="0.0.0.0"
2025-01-30T10:00:00.001Z  INFO processing request request_id=42
2025-01-30T10:00:00.001Z DEBUG fetching data id=42
2025-01-30T10:00:00.051Z  INFO request complete request_id=42 data_len=7
```

Notice: structured fields (`request_id = 42`) instead of string interpolation. This matters for log aggregation and searching.

## Spans: The Killer Feature

Spans are tracing's secret weapon. A span represents a *period of time* during which work happens. It carries context that's automatically attached to every event within it.

```rust
use tracing::{info, info_span, Instrument};
use tokio::time::{sleep, Duration};

async fn handle_request(request_id: u64) {
    let span = info_span!("request", id = request_id);

    async {
        info!("Started processing");

        let user = fetch_user(request_id).await;
        info!(user = %user, "User fetched");

        let result = compute_response(&user).await;
        info!(response_size = result.len(), "Response ready");
    }
    .instrument(span) // Attach the span to this future
    .await;
}

async fn fetch_user(id: u64) -> String {
    let span = info_span!("fetch_user", user_id = id);
    async {
        sleep(Duration::from_millis(50)).await;
        info!("Database query complete");
        format!("user-{id}")
    }
    .instrument(span)
    .await
}

async fn compute_response(user: &str) -> String {
    sleep(Duration::from_millis(20)).await;
    format!("Hello, {user}!")
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_max_level(tracing::Level::INFO)
        .init();

    // Process several requests concurrently
    let handles: Vec<_> = (1..=3)
        .map(|id| tokio::spawn(handle_request(id)))
        .collect();

    for h in handles {
        h.await.unwrap();
    }
}
```

Output looks like:
```
INFO request{id=1}: Started processing
INFO request{id=2}: Started processing
INFO request{id=3}: Started processing
INFO request{id=1}:fetch_user{user_id=1}: Database query complete
INFO request{id=1}: User fetched user="user-1"
...
```

Every log line shows the full span context. You can filter by `request_id` to see everything that happened for a single request, even when thousands of requests are interleaved.

## The #[instrument] Attribute

Manual span creation is verbose. The `#[instrument]` attribute does it automatically:

```rust
use tracing::{info, instrument};
use tokio::time::{sleep, Duration};

#[instrument(skip(password))]  // Don't log the password field!
async fn login(username: &str, password: &str) -> Result<String, String> {
    info!("Login attempt");

    let user = validate_credentials(username, password).await?;
    info!("Login successful");
    Ok(user)
}

#[instrument]
async fn validate_credentials(username: &str, _password: &str) -> Result<String, String> {
    sleep(Duration::from_millis(50)).await;
    if username == "admin" {
        Ok("admin-token".to_string())
    } else {
        Err("invalid credentials".to_string())
    }
}

#[instrument(name = "api_handler", fields(request_id = %uuid()))]
async fn handle_api_request(path: &str) -> String {
    info!("Handling request");
    sleep(Duration::from_millis(30)).await;
    format!("Response for {path}")
}

fn uuid() -> u64 {
    use std::sync::atomic::{AtomicU64, Ordering};
    static COUNTER: AtomicU64 = AtomicU64::new(1);
    COUNTER.fetch_add(1, Ordering::Relaxed)
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_max_level(tracing::Level::INFO)
        .init();

    let _ = login("admin", "secret123").await;
    let _ = handle_api_request("/api/users").await;
}
```

`#[instrument]` automatically:
- Creates a span named after the function
- Logs all arguments as span fields (unless `skip`ped)
- Enters the span for the duration of the function

## Subscriber Configuration

### Pretty output for development

```rust
fn init_dev_tracing() {
    tracing_subscriber::fmt()
        .pretty()
        .with_max_level(tracing::Level::DEBUG)
        .with_target(true)
        .with_thread_ids(true)
        .with_file(true)
        .with_line_number(true)
        .init();
}
```

### JSON output for production

```rust
fn init_prod_tracing() {
    tracing_subscriber::fmt()
        .json()                    // Machine-readable output
        .with_max_level(tracing::Level::INFO)
        .with_target(true)
        .with_current_span(true)
        .flatten_event(true)       // Flatten span fields into events
        .init();
}
```

### Environment-based filtering

```rust
use tracing_subscriber::EnvFilter;

fn init_filtered_tracing() {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| {
                    // Default: info for everything, debug for our crate
                    EnvFilter::new("info,my_app=debug,tower_http=debug")
                })
        )
        .init();
}
```

Set `RUST_LOG=debug` or `RUST_LOG=my_crate::module=trace` to control filtering at runtime.

## Tracing Across Task Boundaries

When you spawn a task, the span context doesn't automatically follow. You need to explicitly carry it:

```rust
use tracing::{info, info_span, Instrument};
use tokio::time::{sleep, Duration};

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_max_level(tracing::Level::INFO)
        .init();

    let span = info_span!("main_request", id = 42);
    let _enter = span.enter();

    info!("Starting request");

    // BAD: Spawned task loses the span context
    // tokio::spawn(async {
    //     info!("This log has no parent span!");
    // });

    // GOOD: Carry the span into the spawned task
    let span_clone = info_span!("background_work", parent_request = 42);
    tokio::spawn(
        async {
            sleep(Duration::from_millis(50)).await;
            info!("Background work done — with context!");
        }
        .instrument(span_clone)
    ).await.unwrap();
}
```

## Timing with Spans

Spans automatically track their duration when using the right subscriber:

```rust
use tracing::{info, info_span, Instrument};
use tokio::time::{sleep, Duration};

#[tracing::instrument]
async fn database_query(query: &str) -> Vec<String> {
    info!("Executing query");
    sleep(Duration::from_millis(150)).await;
    vec!["row1".into(), "row2".into()]
}

#[tracing::instrument]
async fn cache_lookup(key: &str) -> Option<String> {
    sleep(Duration::from_millis(5)).await;
    None
}

#[tracing::instrument]
async fn handle_request(user_id: u32) -> String {
    // Each function call creates a span with timing
    let cached = cache_lookup(&format!("user:{user_id}")).await;

    let data = match cached {
        Some(d) => d,
        None => {
            let rows = database_query("SELECT * FROM users WHERE id = ?").await;
            rows.join(", ")
        }
    };

    format!("Response: {data}")
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_max_level(tracing::Level::INFO)
        .with_span_events(
            tracing_subscriber::fmt::format::FmtSpan::CLOSE
        )  // Log when spans close (includes duration)
        .init();

    handle_request(1).await;
}
```

With `FmtSpan::CLOSE`, you get automatic timing:
```
INFO handle_request{user_id=1}: Processing
INFO handle_request{user_id=1}:cache_lookup{key="user:1"}: ...
INFO handle_request{user_id=1}:cache_lookup{key="user:1"}: close time.busy=5ms
INFO handle_request{user_id=1}:database_query{query="SELECT..."}: ...
INFO handle_request{user_id=1}:database_query{query="SELECT..."}: close time.busy=150ms
INFO handle_request{user_id=1}: close time.busy=155ms
```

## Custom Span Fields

Add dynamic context as processing progresses:

```rust
use tracing::{info, Span};

#[tracing::instrument(fields(items_processed = tracing::field::Empty))]
async fn batch_process(items: Vec<String>) {
    let mut count = 0;

    for item in &items {
        // Simulate processing
        tokio::time::sleep(tokio::time::Duration::from_millis(10)).await;
        count += 1;
    }

    // Record the final count on the span
    Span::current().record("items_processed", count);
    info!("Batch complete");
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_max_level(tracing::Level::INFO)
        .init();

    batch_process(vec!["a".into(), "b".into(), "c".into()]).await;
}
```

## Error Tracing

```rust
use tracing::{error, info, warn, instrument};

#[derive(Debug)]
struct AppError {
    code: u32,
    message: String,
}

impl std::fmt::Display for AppError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "[{}] {}", self.code, self.message)
    }
}

#[instrument(err)]  // Automatically logs errors at ERROR level
async fn risky_operation(id: u32) -> Result<String, AppError> {
    if id % 2 == 0 {
        Ok(format!("success-{id}"))
    } else {
        Err(AppError {
            code: 500,
            message: format!("Failed for {id}"),
        })
    }
}

#[instrument(ret)]  // Automatically logs the return value
async fn compute(x: u32) -> u32 {
    x * x
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_max_level(tracing::Level::INFO)
        .init();

    let _ = risky_operation(1).await;
    let _ = risky_operation(2).await;
    let _ = compute(7).await;
}
```

## Practical: Fully Instrumented Service

```rust
use tracing::{info, error, instrument, Instrument, info_span};
use tokio::sync::mpsc;
use tokio::time::{sleep, Duration};

#[derive(Debug)]
struct Request {
    id: u64,
    path: String,
}

#[instrument(skip(rx))]
async fn request_handler(mut rx: mpsc::Receiver<Request>) {
    while let Some(req) = rx.recv().await {
        let span = info_span!("handle", request_id = req.id, path = %req.path);

        tokio::spawn(
            async move {
                info!("Processing started");

                let auth = authenticate(&req).await;
                if let Err(e) = auth {
                    error!(error = %e, "Authentication failed");
                    return;
                }

                let result = process(&req).await;
                match result {
                    Ok(response) => {
                        info!(response_size = response.len(), "Request completed");
                    }
                    Err(e) => {
                        error!(error = %e, "Processing failed");
                    }
                }
            }
            .instrument(span)
        );
    }
}

#[instrument(skip(req), fields(user = tracing::field::Empty))]
async fn authenticate(req: &Request) -> Result<(), String> {
    sleep(Duration::from_millis(10)).await;
    tracing::Span::current().record("user", "atharva");
    info!("Authenticated");
    Ok(())
}

#[instrument(skip(req))]
async fn process(req: &Request) -> Result<String, String> {
    sleep(Duration::from_millis(50)).await;
    Ok(format!("Response for {}", req.path))
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_max_level(tracing::Level::INFO)
        .with_span_events(tracing_subscriber::fmt::format::FmtSpan::CLOSE)
        .init();

    let (tx, rx) = mpsc::channel(100);

    tokio::spawn(request_handler(rx));

    for i in 1..=5 {
        tx.send(Request {
            id: i,
            path: format!("/api/users/{i}"),
        }).await.unwrap();
    }

    sleep(Duration::from_secs(1)).await;
}
```

Tracing isn't optional for production async services. It's the difference between "something is slow" and "request abc123 spent 450ms in the database query at line 87." Set it up early, instrument everything, and your future self will thank you.

Next: testing async code — because async introduces its own unique testing challenges.
