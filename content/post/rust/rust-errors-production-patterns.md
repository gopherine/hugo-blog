---
title: "Lesson 10: Production Error Architecture — Logging, reporting, recovery"
author: Atharva Pandey
series: "Rust Error Handling at Scale"
lesson: 10
keywords: [Rust, rust, error handling, production, logging, tracing, error reporting, recovery, retry]
tags: [Rust tutorial, rust, error-handling]
date: '2024-07-28T11:30:00.000Z'
---

I shipped a Rust service to production once with great error types, proper `Result` propagation, context chains — the whole nine yards. And then I couldn't debug anything because every error got logged as a single flat string with no request ID, no trace correlation, and no distinction between "user sent bad input" and "our database is on fire." Having good error *types* is half the battle. The other half is what you *do* with those errors when they reach the top of your stack.

## The Error Handling Architecture

In production, errors flow through layers:

```text
Source (IO, parsing, external APIs)
  ↓ Result<T, E> with context
Domain logic (business rules, validation)
  ↓ typed errors + context
Service layer (orchestration)
  ↓ anyhow or typed errors
Boundary (HTTP handler, CLI, message consumer)
  ↓ log, report, respond
Observability (logs, metrics, alerting)
```

Each layer has a different job. Let's build the whole thing.

## Error Classification: Not All Errors Are Equal

The first thing you need in production is a way to classify errors by severity and action:

```rust
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ErrorKind {
    /// Client sent bad data — log at info, return 4xx
    BadRequest,
    /// Resource doesn't exist — log at info, return 404
    NotFound,
    /// Authentication/authorization failure — log at warn, return 401/403
    Unauthorized,
    /// Transient infrastructure issue — log at warn, maybe retry
    Transient,
    /// Bug in our code — log at error, alert, return 500
    Internal,
}

impl ErrorKind {
    fn status_code(&self) -> u16 {
        match self {
            ErrorKind::BadRequest => 400,
            ErrorKind::NotFound => 404,
            ErrorKind::Unauthorized => 401,
            ErrorKind::Transient => 503,
            ErrorKind::Internal => 500,
        }
    }

    fn should_alert(&self) -> bool {
        matches!(self, ErrorKind::Internal)
    }

    fn should_retry(&self) -> bool {
        matches!(self, ErrorKind::Transient)
    }

    fn log_level(&self) -> &'static str {
        match self {
            ErrorKind::BadRequest | ErrorKind::NotFound => "info",
            ErrorKind::Unauthorized | ErrorKind::Transient => "warn",
            ErrorKind::Internal => "error",
        }
    }
}

impl fmt::Display for ErrorKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ErrorKind::BadRequest => write!(f, "bad_request"),
            ErrorKind::NotFound => write!(f, "not_found"),
            ErrorKind::Unauthorized => write!(f, "unauthorized"),
            ErrorKind::Transient => write!(f, "transient"),
            ErrorKind::Internal => write!(f, "internal"),
        }
    }
}

fn main() {
    let kind = ErrorKind::Transient;
    println!("Status: {}, Alert: {}, Retry: {}, Level: {}",
        kind.status_code(), kind.should_alert(), kind.should_retry(), kind.log_level());
}
```

This classification drives everything downstream — logging level, alerting, HTTP response codes, retry logic.

## A Production Error Type

Here's the error type I actually use in production services:

```rust
use std::fmt;
use std::collections::HashMap;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ErrorKind {
    BadRequest,
    NotFound,
    Unauthorized,
    Transient,
    Internal,
}

impl ErrorKind {
    fn status_code(&self) -> u16 {
        match self {
            ErrorKind::BadRequest => 400,
            ErrorKind::NotFound => 404,
            ErrorKind::Unauthorized => 401,
            ErrorKind::Transient => 503,
            ErrorKind::Internal => 500,
        }
    }
}

impl fmt::Display for ErrorKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let s = match self {
            ErrorKind::BadRequest => "bad_request",
            ErrorKind::NotFound => "not_found",
            ErrorKind::Unauthorized => "unauthorized",
            ErrorKind::Transient => "transient",
            ErrorKind::Internal => "internal",
        };
        write!(f, "{}", s)
    }
}

#[derive(Debug)]
struct AppError {
    kind: ErrorKind,
    message: String,
    source: Option<Box<dyn std::error::Error + Send + Sync>>,
    metadata: HashMap<String, String>,
}

impl AppError {
    fn new(kind: ErrorKind, message: impl Into<String>) -> Self {
        AppError {
            kind,
            message: message.into(),
            source: None,
            metadata: HashMap::new(),
        }
    }

    fn with_source(mut self, source: impl std::error::Error + Send + Sync + 'static) -> Self {
        self.source = Some(Box::new(source));
        self
    }

    fn with_meta(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.metadata.insert(key.into(), value.into());
        self
    }

    fn bad_request(msg: impl Into<String>) -> Self {
        Self::new(ErrorKind::BadRequest, msg)
    }

    fn not_found(msg: impl Into<String>) -> Self {
        Self::new(ErrorKind::NotFound, msg)
    }

    fn internal(msg: impl Into<String>) -> Self {
        Self::new(ErrorKind::Internal, msg)
    }

    fn transient(msg: impl Into<String>) -> Self {
        Self::new(ErrorKind::Transient, msg)
    }
}

impl fmt::Display for AppError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "[{}] {}", self.kind, self.message)
    }
}

impl std::error::Error for AppError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        self.source.as_ref().map(|e| e.as_ref() as &(dyn std::error::Error + 'static))
    }
}

// Convert from common error types
impl From<std::io::Error> for AppError {
    fn from(e: std::io::Error) -> Self {
        let kind = match e.kind() {
            std::io::ErrorKind::NotFound => ErrorKind::NotFound,
            std::io::ErrorKind::PermissionDenied => ErrorKind::Unauthorized,
            std::io::ErrorKind::TimedOut | std::io::ErrorKind::ConnectionRefused => {
                ErrorKind::Transient
            }
            _ => ErrorKind::Internal,
        };
        AppError::new(kind, e.to_string()).with_source(e)
    }
}

impl From<std::num::ParseIntError> for AppError {
    fn from(e: std::num::ParseIntError) -> Self {
        AppError::bad_request(format!("invalid number: {}", e)).with_source(e)
    }
}

fn main() {
    let err = AppError::bad_request("invalid email format")
        .with_meta("field", "email")
        .with_meta("value", "not-an-email");

    println!("{}", err);
    println!("Status: {}", err.kind.status_code());
    println!("Metadata: {:?}", err.metadata);
}
```

The metadata map is critical for production debugging. When an error gets logged, those key-value pairs become searchable fields in your log aggregation system.

## Structured Error Logging

Flat log lines are almost useless at scale. You need structured logging with error context:

```rust
use std::collections::HashMap;
use std::fmt;
use std::time::SystemTime;

#[derive(Debug, Clone, Copy)]
enum ErrorKind {
    BadRequest,
    NotFound,
    Internal,
    Transient,
}

impl fmt::Display for ErrorKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ErrorKind::BadRequest => write!(f, "bad_request"),
            ErrorKind::NotFound => write!(f, "not_found"),
            ErrorKind::Internal => write!(f, "internal"),
            ErrorKind::Transient => write!(f, "transient"),
        }
    }
}

#[derive(Debug)]
struct AppError {
    kind: ErrorKind,
    message: String,
    metadata: HashMap<String, String>,
}

impl fmt::Display for AppError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "[{}] {}", self.kind, self.message)
    }
}

impl std::error::Error for AppError {}

impl AppError {
    fn new(kind: ErrorKind, msg: impl Into<String>) -> Self {
        AppError { kind, message: msg.into(), metadata: HashMap::new() }
    }

    fn with_meta(mut self, k: impl Into<String>, v: impl Into<String>) -> Self {
        self.metadata.insert(k.into(), v.into());
        self
    }
}

fn log_error(err: &AppError, request_id: &str) {
    // In production, you'd use tracing or slog. Here's the structure:
    let timestamp = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap()
        .as_secs();

    let mut fields = HashMap::new();
    fields.insert("timestamp".to_string(), timestamp.to_string());
    fields.insert("level".to_string(), match err.kind {
        ErrorKind::BadRequest | ErrorKind::NotFound => "info".to_string(),
        ErrorKind::Transient => "warn".to_string(),
        ErrorKind::Internal => "error".to_string(),
    });
    fields.insert("error_kind".to_string(), err.kind.to_string());
    fields.insert("message".to_string(), err.message.clone());
    fields.insert("request_id".to_string(), request_id.to_string());

    // Merge error metadata
    for (k, v) in &err.metadata {
        fields.insert(format!("error.{}", k), v.clone());
    }

    // In production: output as JSON for log aggregation
    // Here we just print it
    print!("{{");
    let entries: Vec<String> = fields.iter()
        .map(|(k, v)| format!("\"{}\": \"{}\"", k, v))
        .collect();
    print!("{}", entries.join(", "));
    println!("}}");
}

fn main() {
    let err = AppError::new(ErrorKind::NotFound, "user not found")
        .with_meta("user_id", "42")
        .with_meta("lookup_source", "database");

    log_error(&err, "req-abc-123");
}
```

## Error Responses: What the Client Sees

Never leak internal details to clients. Map your internal errors to safe, consistent responses:

```rust
use std::collections::HashMap;
use std::fmt;

#[derive(Debug, Clone, Copy)]
enum ErrorKind {
    BadRequest,
    NotFound,
    Unauthorized,
    Transient,
    Internal,
}

impl ErrorKind {
    fn status_code(&self) -> u16 {
        match self {
            ErrorKind::BadRequest => 400,
            ErrorKind::NotFound => 404,
            ErrorKind::Unauthorized => 401,
            ErrorKind::Transient => 503,
            ErrorKind::Internal => 500,
        }
    }
}

impl fmt::Display for ErrorKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ErrorKind::BadRequest => write!(f, "bad_request"),
            ErrorKind::NotFound => write!(f, "not_found"),
            ErrorKind::Unauthorized => write!(f, "unauthorized"),
            ErrorKind::Transient => write!(f, "transient"),
            ErrorKind::Internal => write!(f, "internal"),
        }
    }
}

#[derive(Debug)]
struct AppError {
    kind: ErrorKind,
    message: String,
    metadata: HashMap<String, String>,
}

impl fmt::Display for AppError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "[{}] {}", self.kind, self.message)
    }
}

impl std::error::Error for AppError {}

struct ErrorResponse {
    status: u16,
    body: String,
}

fn error_to_response(err: &AppError, request_id: &str) -> ErrorResponse {
    let (status, user_message) = match err.kind {
        ErrorKind::BadRequest => (400, err.message.clone()),
        ErrorKind::NotFound => (404, err.message.clone()),
        ErrorKind::Unauthorized => (401, "authentication required".to_string()),
        ErrorKind::Transient => (503, "service temporarily unavailable, please retry".to_string()),
        ErrorKind::Internal => {
            // NEVER expose internal error details to clients
            (500, "an internal error occurred".to_string())
        }
    };

    let body = format!(
        r#"{{"error": {{"code": "{}", "message": "{}", "request_id": "{}"}}}}"#,
        err.kind, user_message, request_id
    );

    ErrorResponse { status, body }
}

fn main() {
    // Internal error — message is hidden from client
    let internal_err = AppError {
        kind: ErrorKind::Internal,
        message: "database connection pool exhausted, 47 pending queries".into(),
        metadata: HashMap::new(),
    };

    let response = error_to_response(&internal_err, "req-xyz-789");
    println!("Status: {}", response.status);
    println!("Body: {}", response.body);
    // The client sees "an internal error occurred" — not the connection pool details

    println!();

    // Bad request — message IS shown to client
    let bad_req = AppError {
        kind: ErrorKind::BadRequest,
        message: "email field is required".into(),
        metadata: HashMap::new(),
    };

    let response = error_to_response(&bad_req, "req-abc-123");
    println!("Status: {}", response.status);
    println!("Body: {}", response.body);
}
```

## Retry Logic

Transient errors should be retried. Here's a simple retry mechanism:

```rust
use std::fmt;
use std::thread;
use std::time::Duration;

#[derive(Debug, Clone, Copy, PartialEq)]
enum ErrorKind {
    Transient,
    Permanent,
}

#[derive(Debug)]
struct ServiceError {
    kind: ErrorKind,
    message: String,
}

impl fmt::Display for ServiceError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl std::error::Error for ServiceError {}

fn retry_with_backoff<T, F>(
    operation_name: &str,
    max_retries: u32,
    mut operation: F,
) -> Result<T, ServiceError>
where
    F: FnMut() -> Result<T, ServiceError>,
{
    let mut last_error = None;

    for attempt in 0..=max_retries {
        match operation() {
            Ok(value) => {
                if attempt > 0 {
                    println!(
                        "[{}] succeeded after {} retries",
                        operation_name, attempt
                    );
                }
                return Ok(value);
            }
            Err(e) if e.kind == ErrorKind::Transient && attempt < max_retries => {
                let delay = Duration::from_millis(100 * 2u64.pow(attempt));
                println!(
                    "[{}] transient error (attempt {}): {}, retrying in {:?}",
                    operation_name,
                    attempt + 1,
                    e,
                    delay
                );
                thread::sleep(delay);
                last_error = Some(e);
            }
            Err(e) => {
                return Err(e);
            }
        }
    }

    Err(last_error.unwrap())
}

fn flaky_api_call(call_count: &mut u32) -> Result<String, ServiceError> {
    *call_count += 1;
    if *call_count < 3 {
        Err(ServiceError {
            kind: ErrorKind::Transient,
            message: format!("connection timeout (attempt {})", call_count),
        })
    } else {
        Ok("success!".to_string())
    }
}

fn main() {
    let mut call_count = 0u32;
    let result = retry_with_backoff("api_call", 5, || flaky_api_call(&mut call_count));
    println!("Result: {:?}", result);
}
```

## Graceful Degradation

Sometimes the right response to an error isn't to fail — it's to degrade gracefully:

```rust
use std::collections::HashMap;

#[derive(Debug)]
struct UserProfile {
    name: String,
    email: String,
    avatar_url: Option<String>,
    recent_orders: Vec<String>,
}

fn fetch_avatar(user_id: u64) -> Result<String, String> {
    // Simulating failure
    Err(format!("avatar service timeout for user {}", user_id))
}

fn fetch_recent_orders(user_id: u64) -> Result<Vec<String>, String> {
    // Simulating failure
    Err(format!("order service unavailable for user {}", user_id))
}

fn get_user_profile(user_id: u64) -> Result<UserProfile, String> {
    // Core data — MUST succeed
    let users: HashMap<u64, (&str, &str)> = HashMap::from([
        (1, ("Atharva", "atharva@example.com")),
    ]);

    let (name, email) = users.get(&user_id)
        .ok_or_else(|| format!("user {} not found", user_id))?;

    // Optional enrichment — failures are degraded, not fatal
    let avatar_url = match fetch_avatar(user_id) {
        Ok(url) => Some(url),
        Err(e) => {
            eprintln!("[WARN] avatar fetch failed, degrading: {}", e);
            None
        }
    };

    let recent_orders = match fetch_recent_orders(user_id) {
        Ok(orders) => orders,
        Err(e) => {
            eprintln!("[WARN] order fetch failed, degrading: {}", e);
            Vec::new() // Empty list instead of failing
        }
    };

    Ok(UserProfile {
        name: name.to_string(),
        email: email.to_string(),
        avatar_url,
        recent_orders,
    })
}

fn main() {
    match get_user_profile(1) {
        Ok(profile) => {
            println!("Name: {}", profile.name);
            println!("Email: {}", profile.email);
            println!("Avatar: {:?}", profile.avatar_url);
            println!("Orders: {:?}", profile.recent_orders);
        }
        Err(e) => eprintln!("Failed to load profile: {}", e),
    }
}
```

The core operation (fetching user data) must succeed. But enrichment data (avatar, recent orders) can fail without killing the request. Log the degradation, return partial data, let the frontend handle the missing pieces.

## Error Metrics

Track errors as metrics, not just log lines. Counts by error kind over time tell you things logs can't:

```rust
use std::collections::HashMap;
use std::sync::Mutex;

// In production, use prometheus or metrics crate
struct ErrorMetrics {
    counts: Mutex<HashMap<String, u64>>,
}

impl ErrorMetrics {
    fn new() -> Self {
        ErrorMetrics {
            counts: Mutex::new(HashMap::new()),
        }
    }

    fn record(&self, kind: &str, operation: &str) {
        let key = format!("{}:{}", kind, operation);
        let mut counts = self.counts.lock().unwrap();
        *counts.entry(key).or_insert(0) += 1;
    }

    fn report(&self) {
        let counts = self.counts.lock().unwrap();
        println!("\n=== Error Metrics ===");
        for (key, count) in counts.iter() {
            println!("  {} = {}", key, count);
        }
    }
}

fn main() {
    let metrics = ErrorMetrics::new();

    // Simulate some errors
    metrics.record("bad_request", "create_user");
    metrics.record("bad_request", "create_user");
    metrics.record("transient", "fetch_orders");
    metrics.record("internal", "process_payment");
    metrics.record("not_found", "get_user");
    metrics.record("not_found", "get_user");
    metrics.record("not_found", "get_user");

    metrics.report();
    // In production, these would be Prometheus counters:
    // app_errors_total{kind="bad_request", operation="create_user"} 2
    // app_errors_total{kind="transient", operation="fetch_orders"} 1
    // etc.
}
```

## Putting It All Together

Here's the complete flow for a request handler in a production service:

```rust
use std::collections::HashMap;
use std::fmt;

#[derive(Debug, Clone, Copy)]
enum ErrorKind {
    BadRequest,
    NotFound,
    Internal,
}

impl fmt::Display for ErrorKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ErrorKind::BadRequest => write!(f, "bad_request"),
            ErrorKind::NotFound => write!(f, "not_found"),
            ErrorKind::Internal => write!(f, "internal"),
        }
    }
}

impl ErrorKind {
    fn status_code(&self) -> u16 {
        match self {
            ErrorKind::BadRequest => 400,
            ErrorKind::NotFound => 404,
            ErrorKind::Internal => 500,
        }
    }
}

#[derive(Debug)]
struct AppError {
    kind: ErrorKind,
    message: String,
    internal_message: Option<String>,
    metadata: HashMap<String, String>,
}

impl AppError {
    fn bad_request(msg: impl Into<String>) -> Self {
        AppError {
            kind: ErrorKind::BadRequest,
            message: msg.into(),
            internal_message: None,
            metadata: HashMap::new(),
        }
    }

    fn not_found(msg: impl Into<String>) -> Self {
        AppError {
            kind: ErrorKind::NotFound,
            message: msg.into(),
            internal_message: None,
            metadata: HashMap::new(),
        }
    }

    fn internal(public_msg: impl Into<String>, internal_msg: impl Into<String>) -> Self {
        AppError {
            kind: ErrorKind::Internal,
            message: public_msg.into(),
            internal_message: Some(internal_msg.into()),
            metadata: HashMap::new(),
        }
    }

    fn with_meta(mut self, k: impl Into<String>, v: impl Into<String>) -> Self {
        self.metadata.insert(k.into(), v.into());
        self
    }
}

impl fmt::Display for AppError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl std::error::Error for AppError {}

struct Response {
    status: u16,
    body: String,
}

fn handle_get_user(user_id_param: &str) -> Result<Response, AppError> {
    // 1. Parse and validate input
    let user_id: u64 = user_id_param.parse()
        .map_err(|_| AppError::bad_request(format!("invalid user ID: '{}'", user_id_param))
            .with_meta("param", user_id_param.to_string()))?;

    // 2. Business logic
    if user_id > 1000 {
        return Err(AppError::not_found(format!("user {} does not exist", user_id))
            .with_meta("user_id", user_id.to_string()));
    }

    // 3. Simulate database call that might fail
    if user_id == 13 {
        return Err(AppError::internal(
            "unable to process request",
            "connection pool exhausted: 0/10 connections available",
        ).with_meta("user_id", user_id.to_string()));
    }

    Ok(Response {
        status: 200,
        body: format!(r#"{{"id": {}, "name": "User {}"}}"#, user_id, user_id),
    })
}

fn process_request(path: &str, request_id: &str) {
    let user_id_param = path.trim_start_matches("/users/");

    match handle_get_user(user_id_param) {
        Ok(resp) => {
            println!("[{}] {} -> {}", request_id, path, resp.status);
            println!("  Body: {}", resp.body);
        }
        Err(e) => {
            // Log at appropriate level
            match e.kind {
                ErrorKind::BadRequest | ErrorKind::NotFound => {
                    println!("[{}] INFO  {} -> {}: {}",
                        request_id, path, e.kind.status_code(), e);
                }
                ErrorKind::Internal => {
                    // Log internal details for debugging
                    println!("[{}] ERROR {} -> {}: {} (internal: {:?})",
                        request_id, path, e.kind.status_code(), e, e.internal_message);
                    // In production: increment error counter, maybe page on-call
                }
            }

            // Log metadata for searchability
            if !e.metadata.is_empty() {
                println!("  Metadata: {:?}", e.metadata);
            }

            // Client response hides internals
            let client_msg = match e.kind {
                ErrorKind::Internal => "an internal error occurred".to_string(),
                _ => e.message.clone(),
            };
            println!("  Response: {} {}", e.kind.status_code(), client_msg);
        }
    }
}

fn main() {
    println!("=== Production Error Handling Demo ===\n");

    process_request("/users/42", "req-001");
    println!();
    process_request("/users/abc", "req-002");
    println!();
    process_request("/users/9999", "req-003");
    println!();
    process_request("/users/13", "req-004");
}
```

## The Checklist

Before shipping error handling to production, run through this:

1. **Every error has a kind/classification** — drives logging level, response code, alerting
2. **Internal details never leak to clients** — 500 responses get generic messages
3. **Errors carry structured metadata** — searchable in log aggregation
4. **Request IDs flow through the entire error chain** — correlate logs to requests
5. **Transient errors trigger retries** — with exponential backoff
6. **Non-critical failures degrade gracefully** — partial data beats no data
7. **Error counts are tracked as metrics** — dashboards and alerts, not just logs
8. **Display is for users, Debug is for developers** — keep them separate

Error handling isn't glamorous work. Nobody's going to tweet about your error types. But when it's 2 AM and something breaks, the difference between "connection refused" and "[req-abc-123] order service connection refused during payment processing for order 7842, attempt 3/3, degrading to cached pricing" is the difference between a fifteen-minute fix and a three-hour investigation. Build the second kind.
