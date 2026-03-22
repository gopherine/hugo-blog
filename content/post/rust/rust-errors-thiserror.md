---
title: "Lesson 5: thiserror — Derive your way to clean errors"
author: Atharva Pandey
series: "Rust Error Handling at Scale"
lesson: 5
keywords: [Rust, rust, error handling, thiserror, derive macro, Error trait, custom errors]
tags: [Rust tutorial, rust, error-handling]
date: '2024-07-14T10:00:00.000Z'
---

After writing my third custom error type by hand — with the `Display` impl, the `Error` impl, the `From` impls — I thought, "there has to be a better way." There was. It's called `thiserror`, and it's probably the most widely-used error handling crate in the Rust ecosystem. David Tolnay wrote it, which means it's well-designed, well-maintained, and does exactly one thing with zero bloat.

## What thiserror Does

`thiserror` is a derive macro that generates `Display`, `Error`, and `From` implementations for your error types. It produces the exact same code you'd write by hand — no runtime cost, no extra dependencies at runtime (it's a proc-macro, so it's only a compile-time dependency).

Add it to your `Cargo.toml`:

```toml
[dependencies]
thiserror = "2"
```

## Before and After

Remember the `AppError` from lesson 4? Here's the manual version versus `thiserror`:

**Manual (from lesson 4):**

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
    fn from(e: io::Error) -> Self { AppError::IoError(e) }
}

impl From<ParseIntError> for AppError {
    fn from(e: ParseIntError) -> Self { AppError::InvalidPort(e) }
}
```

**With thiserror:**

```rust
use std::io;
use std::num::ParseIntError;
use thiserror::Error;

#[derive(Debug, Error)]
enum AppError {
    #[error("config not found: {0}")]
    ConfigNotFound(String),

    #[error("invalid port: {0}")]
    InvalidPort(#[from] ParseIntError),

    #[error("I/O error: {0}")]
    IoError(#[from] io::Error),

    #[error("validation failed for '{field}': {message}")]
    ValidationFailed { field: String, message: String },
}
```

That's it. Same behavior, fraction of the code. The `#[error("...")]` attribute generates `Display`. The `#[from]` attribute generates both `From` and `source()`. Everything is type-checked at compile time.

## The #[error] Attribute

This controls the `Display` implementation. You can use several interpolation styles:

```rust
use thiserror::Error;

#[derive(Debug, Error)]
enum MyError {
    // Positional fields — use {0}, {1}, etc.
    #[error("failed to read file at {0}")]
    FileRead(String),

    // Named fields — use {field_name}
    #[error("validation failed for '{field}': {reason}")]
    Validation { field: String, reason: String },

    // You can call methods on fields
    #[error("request failed with status {}", .status)]
    HttpError { status: u16, body: String },

    // Display of the inner type
    #[error("parse error: {0}")]
    Parse(#[from] std::num::ParseIntError),

    // Static messages work too
    #[error("operation timed out")]
    Timeout,
}
```

The format strings use the same syntax as `format!()`, so you can do padding, precision, alternate formatting — anything `Display` supports.

## The #[from] Attribute

`#[from]` generates a `From` implementation, which is what makes `?` work automatically:

```rust
use thiserror::Error;
use std::io;
use std::num::ParseIntError;

#[derive(Debug, Error)]
enum ConfigError {
    #[error("IO error: {0}")]
    Io(#[from] io::Error),

    #[error("parse error: {0}")]
    Parse(#[from] ParseIntError),
}

// Now ? converts automatically:
fn load_port(path: &str) -> Result<u16, ConfigError> {
    let contents = std::fs::read_to_string(path)?; // io::Error → ConfigError::Io
    let port = contents.trim().parse::<u16>()?;     // ParseIntError → ConfigError::Parse
    Ok(port)
}

fn main() {
    match load_port("port.txt") {
        Ok(p) => println!("Port: {}", p),
        Err(e) => eprintln!("{}", e),
    }
}
```

Important: `#[from]` also sets up the `source()` method automatically. When you use `#[from]`, calling `.source()` on the error will return the wrapped inner error. You don't need to do anything extra.

## The #[source] Attribute

Sometimes you want error chaining without automatic `From` conversion. Use `#[source]`:

```rust
use thiserror::Error;
use std::io;

#[derive(Debug, Error)]
enum AppError {
    #[error("failed to initialize database")]
    DbInit {
        #[source]
        cause: io::Error,
        connection_string: String,
    },
}

fn init_db(conn_str: &str) -> Result<(), AppError> {
    // Simulating a database connection failure
    let io_err = io::Error::new(io::ErrorKind::ConnectionRefused, "connection refused");
    Err(AppError::DbInit {
        cause: io_err,
        connection_string: conn_str.to_string(),
    })
}

fn main() {
    if let Err(e) = init_db("postgres://localhost/mydb") {
        eprintln!("Error: {}", e);
        // Walk the chain
        let mut source = std::error::Error::source(&e);
        while let Some(s) = source {
            eprintln!("  caused by: {}", s);
            source = s.source();
        }
    }
}
```

`#[source]` marks a field as the error's source (for the `source()` method) without generating `From`. Use this when you need to carry extra context alongside the wrapped error.

## Struct Errors with thiserror

`thiserror` works on structs too, not just enums:

```rust
use thiserror::Error;

#[derive(Debug, Error)]
#[error("parse error at line {line}, column {col}: {message}")]
struct ParseError {
    line: usize,
    col: usize,
    message: String,
}

fn parse_config(input: &str) -> Result<Vec<(String, String)>, ParseError> {
    let mut result = Vec::new();
    for (i, line) in input.lines().enumerate() {
        let parts: Vec<&str> = line.splitn(2, '=').collect();
        if parts.len() != 2 {
            return Err(ParseError {
                line: i + 1,
                col: 1,
                message: format!("expected KEY=VALUE, got '{}'", line),
            });
        }
        result.push((parts[0].trim().to_string(), parts[1].trim().to_string()));
    }
    Ok(result)
}

fn main() {
    let input = "host=localhost\nbad line\nport=8080";
    if let Err(e) = parse_config(input) {
        eprintln!("{}", e);
        // parse error at line 2, column 1: expected KEY=VALUE, got 'bad line'
    }
}
```

## Transparent Errors

Sometimes you want a newtype wrapper around an existing error that behaves like the inner error. The `#[error(transparent)]` attribute forwards both `Display` and `source()`:

```rust
use thiserror::Error;

#[derive(Debug, Error)]
#[error(transparent)]
struct MyIoError(#[from] std::io::Error);

// MyIoError's Display prints exactly what io::Error would print
// MyIoError's source() returns what io::Error's source() returns

fn read_file(path: &str) -> Result<String, MyIoError> {
    Ok(std::fs::read_to_string(path)?)
}

fn main() {
    if let Err(e) = read_file("nonexistent.txt") {
        eprintln!("{}", e); // prints the io::Error message directly
    }
}
```

This is useful when you want to wrap an error for type safety without changing its presentation.

## A Real-World Example: HTTP Service Errors

Here's a pattern I use in production services:

```rust
use thiserror::Error;
use std::num::ParseIntError;

// Domain layer
#[derive(Debug, Error)]
enum DomainError {
    #[error("user {user_id} not found")]
    UserNotFound { user_id: u64 },

    #[error("email '{email}' is already registered")]
    DuplicateEmail { email: String },

    #[error("invalid age: must be between 0 and 150, got {0}")]
    InvalidAge(u8),
}

// Service layer — wraps domain + infrastructure errors
#[derive(Debug, Error)]
enum ServiceError {
    #[error(transparent)]
    Domain(#[from] DomainError),

    #[error("database error: {0}")]
    Database(String),

    #[error("external API error: {message}")]
    ExternalApi {
        message: String,
        status_code: u16,
    },
}

// API layer — converts to HTTP responses
#[derive(Debug, Error)]
enum ApiError {
    #[error(transparent)]
    Service(#[from] ServiceError),

    #[error("invalid request: {0}")]
    BadRequest(String),

    #[error("parse error: {0}")]
    Parse(#[from] ParseIntError),
}

impl ApiError {
    fn status_code(&self) -> u16 {
        match self {
            ApiError::BadRequest(_) | ApiError::Parse(_) => 400,
            ApiError::Service(ServiceError::Domain(DomainError::UserNotFound { .. })) => 404,
            ApiError::Service(ServiceError::Domain(DomainError::DuplicateEmail { .. })) => 409,
            ApiError::Service(ServiceError::Domain(DomainError::InvalidAge(_))) => 422,
            ApiError::Service(ServiceError::ExternalApi { .. }) => 502,
            ApiError::Service(ServiceError::Database(_)) => 500,
        }
    }
}

fn find_user(id: u64) -> Result<String, ServiceError> {
    if id == 0 {
        Err(DomainError::UserNotFound { user_id: id })?
    }
    Ok(format!("User {}", id))
}

fn handle_request(id_str: &str) -> Result<String, ApiError> {
    let id: u64 = id_str.parse::<u64>().map_err(|_| {
        ApiError::BadRequest(format!("'{}' is not a valid user ID", id_str))
    })?;

    let user = find_user(id)?;
    Ok(user)
}

fn main() {
    for input in &["42", "0", "abc"] {
        match handle_request(input) {
            Ok(user) => println!("200: {}", user),
            Err(e) => println!("{}: {}", e.status_code(), e),
        }
    }
}
```

Each layer has its own error type. `#[from]` handles the conversions. The API layer maps errors to status codes by matching on the nested structure. Clean, typed, and the compiler catches any missing cases.

## When Not to Use thiserror

`thiserror` is perfect for libraries and well-structured applications. But there are cases where it's not the right tool:

- **Quick prototypes** — use `anyhow` instead (lesson 6)
- **When you need backtrace capture** — `thiserror` supports it, but `anyhow` makes it easier
- **When you genuinely don't care about error types** — again, `anyhow`

For anything you're shipping as a library that other people depend on, `thiserror` is the right default. It produces clean, idiomatic, zero-cost error types with minimal code. That's exactly what you want.
