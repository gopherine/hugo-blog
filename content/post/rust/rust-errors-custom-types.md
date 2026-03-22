---
title: "Lesson 4: Designing Custom Error Types — Your domain, your errors"
author: Atharva Pandey
series: "Rust Error Handling at Scale"
lesson: 4
keywords: [Rust, rust, error handling, custom errors, Error trait, From trait, enum errors]
tags: [Rust tutorial, rust, error-handling]
date: '2024-07-11T19:15:00.000Z'
---

I once worked on a project where every error was `Box<dyn std::error::Error>`. Debugging was miserable. When something failed in production, the logs said things like "invalid input" with zero context about *which* input, *where* in the pipeline, or *why* it was invalid. That's when I learned that good error types aren't an afterthought — they're part of your domain model.

## Why Custom Error Types Matter

Standard library errors like `io::Error` and `ParseIntError` are fine for what they describe. But your application has its own failure modes. A payment processor doesn't just fail with "IO error" — it fails with "card declined," "insufficient funds," "gateway timeout," "idempotency key conflict." These are domain-specific failures that deserve domain-specific types.

Custom error types buy you three things:

1. **Pattern matching** — callers can handle different failures differently
2. **Context** — you can attach domain information to errors
3. **Compiler enforcement** — when you add a new error variant, every `match` that isn't exhaustive will fail to compile

## The Error Trait

Before building custom types, you need to understand what Rust expects an error to be. The `std::error::Error` trait:

```rust
pub trait Error: Debug + Display {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        None
    }
}
```

Three requirements:
- Implement `Debug` (usually `#[derive(Debug)]`)
- Implement `Display` (the human-readable message)
- Optionally implement `source()` to chain errors

That's it. There's no special magic. Any type that satisfies these bounds is an error.

## Building Your First Custom Error

Here's the manual approach — no macros, no derive crates. You should know how to do this by hand before reaching for tools.

```rust
use std::fmt;
use std::io;
use std::num::ParseIntError;

#[derive(Debug)]
enum AppError {
    ConfigNotFound(String),
    InvalidPort(ParseIntError),
    IoError(io::Error),
    ValidationFailed { field: String, message: String },
}

impl fmt::Display for AppError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            AppError::ConfigNotFound(path) => {
                write!(f, "configuration file not found: {}", path)
            }
            AppError::InvalidPort(e) => {
                write!(f, "invalid port number: {}", e)
            }
            AppError::IoError(e) => {
                write!(f, "I/O error: {}", e)
            }
            AppError::ValidationFailed { field, message } => {
                write!(f, "validation failed for '{}': {}", field, message)
            }
        }
    }
}

impl std::error::Error for AppError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            AppError::InvalidPort(e) => Some(e),
            AppError::IoError(e) => Some(e),
            _ => None,
        }
    }
}

// From impls for ? operator support
impl From<io::Error> for AppError {
    fn from(e: io::Error) -> Self {
        AppError::IoError(e)
    }
}

impl From<ParseIntError> for AppError {
    fn from(e: ParseIntError) -> Self {
        AppError::InvalidPort(e)
    }
}
```

Yeah, it's a lot of boilerplate. I'm showing you the full version on purpose — every error handling crate generates exactly this code. When something goes wrong with `thiserror` or `snafu`, you need to know what's underneath.

## Using the Custom Error Type

```rust
use std::collections::HashMap;
use std::fmt;
use std::io;
use std::num::ParseIntError;

#[derive(Debug)]
enum AppError {
    ConfigNotFound(String),
    InvalidPort(ParseIntError),
    IoError(io::Error),
    ValidationFailed { field: String, message: String },
}

impl fmt::Display for AppError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            AppError::ConfigNotFound(path) => write!(f, "config not found: {}", path),
            AppError::InvalidPort(e) => write!(f, "invalid port: {}", e),
            AppError::IoError(e) => write!(f, "I/O error: {}", e),
            AppError::ValidationFailed { field, message } => {
                write!(f, "validation failed for '{}': {}", field, message)
            }
        }
    }
}

impl std::error::Error for AppError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            AppError::InvalidPort(e) => Some(e),
            AppError::IoError(e) => Some(e),
            _ => None,
        }
    }
}

impl From<io::Error> for AppError {
    fn from(e: io::Error) -> Self {
        AppError::IoError(e)
    }
}

impl From<ParseIntError> for AppError {
    fn from(e: ParseIntError) -> Self {
        AppError::InvalidPort(e)
    }
}

struct AppConfig {
    host: String,
    port: u16,
}

fn load_config(env: &HashMap<String, String>) -> Result<AppConfig, AppError> {
    let host = env.get("HOST")
        .ok_or_else(|| AppError::ValidationFailed {
            field: "HOST".into(),
            message: "required environment variable not set".into(),
        })?
        .clone();

    if host.is_empty() {
        return Err(AppError::ValidationFailed {
            field: "HOST".into(),
            message: "cannot be empty".into(),
        });
    }

    let port_str = env.get("PORT")
        .ok_or_else(|| AppError::ConfigNotFound("PORT".into()))?;

    let port: u16 = port_str.parse()?; // From<ParseIntError> kicks in

    Ok(AppConfig { host, port })
}

fn main() {
    let mut env = HashMap::new();
    env.insert("HOST".into(), "localhost".into());
    env.insert("PORT".into(), "8080".into());

    match load_config(&env) {
        Ok(config) => println!("{}:{}", config.host, config.port),
        Err(AppError::ValidationFailed { field, message }) => {
            eprintln!("Bad config: {} — {}", field, message);
        }
        Err(AppError::ConfigNotFound(key)) => {
            eprintln!("Missing: {}", key);
        }
        Err(e) => {
            eprintln!("Unexpected error: {}", e);
        }
    }
}
```

See how the caller can match on specific variants? That's the payoff. You're not parsing error message strings — you're matching on typed, structured data.

## Design Principles for Error Types

After building error types for a few production systems, here's what I've landed on:

### One error enum per module, not per function

Don't create a new error type for every function. Create one per logical module or subsystem. A `DatabaseError` for your database layer. An `AuthError` for authentication. An `ApiError` for your HTTP handlers.

```rust
// Good: one enum for the whole database module
#[derive(Debug)]
enum DatabaseError {
    ConnectionFailed(String),
    QueryFailed { query: String, cause: String },
    NotFound { table: String, id: i64 },
    ConstraintViolation(String),
}

// Bad: separate types for each function
// struct ConnectionError(String);
// struct QueryError(String);
// struct NotFoundError(String);
// This doesn't compose well
```

### Carry context, not just messages

The best error types attach structured data, not formatted strings:

```rust
use std::fmt;

// Bad — all context is baked into a string
#[derive(Debug)]
struct BadError(String);

impl fmt::Display for BadError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

// Good — structured data that callers can inspect
#[derive(Debug)]
enum OrderError {
    NotFound { order_id: u64 },
    AlreadyShipped { order_id: u64, shipped_at: String },
    PaymentFailed { order_id: u64, reason: String, retry_after_secs: u64 },
}

impl fmt::Display for OrderError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            OrderError::NotFound { order_id } => {
                write!(f, "order {} not found", order_id)
            }
            OrderError::AlreadyShipped { order_id, shipped_at } => {
                write!(f, "order {} already shipped at {}", order_id, shipped_at)
            }
            OrderError::PaymentFailed { order_id, reason, .. } => {
                write!(f, "payment failed for order {}: {}", order_id, reason)
            }
        }
    }
}

impl std::error::Error for OrderError {}
```

Now callers can extract `retry_after_secs` to implement backoff, or pull `order_id` for logging. You can't do that with a flat string.

### Keep Display user-facing, Debug developer-facing

`Display` should produce messages suitable for end users or logs. `Debug` (which `#[derive(Debug)]` gives you) is for developers. Don't put stack traces or internal details in `Display`.

### Use #[non_exhaustive] for public error types

If your error type is part of a public API, mark it `#[non_exhaustive]` so you can add variants without breaking downstream code:

```rust
#[derive(Debug)]
#[non_exhaustive]
pub enum StorageError {
    NotFound(String),
    PermissionDenied(String),
    Corrupted(String),
    // Adding a new variant here won't break downstream callers
    // because they're forced to have a _ arm
}
```

## Struct Errors vs Enum Errors

Enums are for when a function can fail in multiple distinguishable ways. But sometimes there's only one kind of failure, and a struct makes more sense:

```rust
use std::fmt;

#[derive(Debug)]
struct ParseConfigError {
    line: usize,
    column: usize,
    message: String,
}

impl fmt::Display for ParseConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "parse error at {}:{}: {}", self.line, self.column, self.message)
    }
}

impl std::error::Error for ParseConfigError {}

fn parse_config(input: &str) -> Result<Vec<(String, String)>, ParseConfigError> {
    let mut entries = Vec::new();

    for (i, line) in input.lines().enumerate() {
        let parts: Vec<&str> = line.splitn(2, '=').collect();
        if parts.len() != 2 {
            return Err(ParseConfigError {
                line: i + 1,
                column: 1,
                message: format!("expected KEY=VALUE, got '{}'", line),
            });
        }
        entries.push((parts[0].trim().to_string(), parts[1].trim().to_string()));
    }

    Ok(entries)
}

fn main() {
    let input = "host=localhost\nbadline\nport=8080";
    match parse_config(input) {
        Ok(entries) => {
            for (k, v) in entries {
                println!("{} = {}", k, v);
            }
        }
        Err(e) => eprintln!("{}", e),
    }
}
```

## Error Type Composition Across Layers

In a real application, you'll have errors at different layers that need to compose:

```rust
use std::fmt;
use std::num::ParseIntError;

// Database layer
#[derive(Debug)]
enum DbError {
    ConnectionFailed(String),
    QueryFailed(String),
}

impl fmt::Display for DbError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            DbError::ConnectionFailed(s) => write!(f, "db connection failed: {}", s),
            DbError::QueryFailed(s) => write!(f, "query failed: {}", s),
        }
    }
}

impl std::error::Error for DbError {}

// Service layer wraps database errors
#[derive(Debug)]
enum ServiceError {
    Database(DbError),
    InvalidInput(String),
    ParseError(ParseIntError),
}

impl fmt::Display for ServiceError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ServiceError::Database(e) => write!(f, "service error: {}", e),
            ServiceError::InvalidInput(msg) => write!(f, "invalid input: {}", msg),
            ServiceError::ParseError(e) => write!(f, "parse error: {}", e),
        }
    }
}

impl std::error::Error for ServiceError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            ServiceError::Database(e) => Some(e),
            ServiceError::ParseError(e) => Some(e),
            _ => None,
        }
    }
}

impl From<DbError> for ServiceError {
    fn from(e: DbError) -> Self {
        ServiceError::Database(e)
    }
}

impl From<ParseIntError> for ServiceError {
    fn from(e: ParseIntError) -> Self {
        ServiceError::ParseError(e)
    }
}

fn main() {
    let err = ServiceError::Database(DbError::ConnectionFailed("timeout".into()));
    println!("{}", err);
    // service error: db connection failed: timeout

    // Walk the error chain
    let mut current: Option<&dyn std::error::Error> = Some(&err);
    while let Some(e) = current {
        println!("  caused by: {}", e);
        current = e.source();
    }
}
```

Each layer wraps the errors below it. The `source()` method creates a chain you can walk for debugging. This is the manual version — `thiserror` (next lesson) generates all of this from a couple of derive attributes.

The boilerplate is real. But now you understand exactly what those derive macros produce, and when something breaks, you'll know where to look.
