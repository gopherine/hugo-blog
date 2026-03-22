---
title: "Lesson 7: Library vs Application Error Strategy — They're not the same"
author: Atharva Pandey
series: "Rust Error Handling at Scale"
lesson: 7
keywords: [Rust, rust, error handling, library errors, application errors, API design, error strategy]
tags: [Rust tutorial, rust, error-handling]
date: '2024-07-18T09:40:00.000Z'
---

I learned this the hard way. I was building a parsing library and used `anyhow::Error` as my error type in all public functions. A user opened an issue: "I can't match on your errors to handle different parse failures differently." They were right. I'd designed a library with application-level error handling, and it was a terrible experience for consumers. Took me a weekend to refactor everything to typed errors.

Libraries and applications have fundamentally different error handling requirements. Mix them up and you'll either frustrate your users or drown in unnecessary boilerplate.

## The Core Difference

**Libraries** produce errors that *other code* handles. The library author doesn't know how the error will be used — maybe the caller retries, maybe they log it, maybe they convert it to an HTTP response. Library errors need to be precise, typed, and inspectable.

**Applications** consume errors and make final decisions. The application is the end of the line — errors get logged, shown to users, or trigger recovery logic. Application errors need to be easy to create, easy to chain with context, and easy to display.

| Concern | Library | Application |
|---------|---------|-------------|
| Error type | Specific enum/struct | `anyhow::Error` or top-level enum |
| Dependencies | Minimal (prefer `thiserror`) | Whatever helps (`anyhow`, logging, etc.) |
| `Display` format | Lowercase, no period, composable | Full sentences, user-facing |
| Backtrace | Leave to caller | Capture when needed |
| Panic | Almost never | At startup if unrecoverable |
| `#[non_exhaustive]` | Yes, for public enums | Unnecessary |

## Library Error Design

### Rule 1: Typed, matchable errors

Your public API should return concrete error types, never `Box<dyn Error>` or `anyhow::Error`:

```rust
use std::fmt;

// Good: callers can match on these
#[derive(Debug)]
#[non_exhaustive]
pub enum ParseError {
    UnexpectedToken { line: usize, found: String, expected: String },
    UnterminatedString { line: usize },
    InvalidEscape { line: usize, sequence: String },
    MaxDepthExceeded { depth: usize, max: usize },
}

impl fmt::Display for ParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ParseError::UnexpectedToken { line, found, expected } => {
                write!(f, "unexpected token at line {}: found '{}', expected '{}'", line, found, expected)
            }
            ParseError::UnterminatedString { line } => {
                write!(f, "unterminated string at line {}", line)
            }
            ParseError::InvalidEscape { line, sequence } => {
                write!(f, "invalid escape sequence '{}' at line {}", sequence, line)
            }
            ParseError::MaxDepthExceeded { depth, max } => {
                write!(f, "nesting depth {} exceeds maximum {}", depth, max)
            }
        }
    }
}

impl std::error::Error for ParseError {}

// Callers can do this:
fn handle_parse(input: &str) {
    let result = parse(input);
    match result {
        Ok(data) => println!("Parsed {} items", data.len()),
        Err(ParseError::UnterminatedString { line }) => {
            eprintln!("Fix the string on line {}", line);
        }
        Err(ParseError::MaxDepthExceeded { max, .. }) => {
            eprintln!("Flatten your structure (max depth: {})", max);
        }
        Err(e) => eprintln!("Parse error: {}", e),
    }
}

fn parse(input: &str) -> Result<Vec<String>, ParseError> {
    if input.contains('"') && !input.ends_with('"') {
        return Err(ParseError::UnterminatedString { line: 1 });
    }
    Ok(input.lines().map(String::from).collect())
}

fn main() {
    handle_parse("hello \"world");
}
```

### Rule 2: Don't expose internal dependencies

If your library uses `serde`, `reqwest`, or any other dependency internally, don't leak their error types through your public API:

```rust
use thiserror::Error;
use std::io;

// Bad: leaking internal dependency errors
// pub enum MyError {
//     Serde(serde_json::Error),  // Now your users depend on the exact serde version
//     Reqwest(reqwest::Error),   // Same problem
// }

// Good: wrap with your own variant, expose only what matters
#[derive(Debug, Error)]
#[non_exhaustive]
pub enum MyError {
    #[error("serialization failed: {0}")]
    Serialization(String),  // String, not serde_json::Error

    #[error("network request failed: {0}")]
    Network(String),  // String, not reqwest::Error

    #[error("I/O error: {0}")]
    Io(#[from] io::Error),  // io::Error is fine — it's std
}

fn main() {
    let err = MyError::Serialization("invalid JSON at position 42".into());
    eprintln!("{}", err);
}
```

Standard library types are fine to expose. Third-party crate types are not — you'd be coupling your users to your dependency versions.

### Rule 3: Display messages should be lowercase and composable

Library error messages get embedded in larger context chains. If your message starts with a capital letter or ends with a period, it looks weird when composed:

```rust
use std::fmt;

#[derive(Debug)]
struct LibError(String);

impl fmt::Display for LibError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl std::error::Error for LibError {}

fn example() {
    // Good: "failed to load config: connection timed out"
    let _ = LibError("connection timed out".into());

    // Bad: "failed to load config: Connection timed out."
    //  (capital C, trailing period — looks wrong in a chain)
    let _ = LibError("Connection timed out.".into());
}

fn main() {
    example();
}
```

### Rule 4: Use #[non_exhaustive]

Always. If you ever add a new error variant (and you will), `#[non_exhaustive]` prevents breaking downstream code:

```rust
use std::fmt;

#[derive(Debug)]
#[non_exhaustive]
pub enum StorageError {
    NotFound { key: String },
    PermissionDenied { key: String },
    QuotaExceeded { used: u64, limit: u64 },
    // Adding ConnectionLost later won't break anyone
}

impl fmt::Display for StorageError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            StorageError::NotFound { key } => write!(f, "key '{}' not found", key),
            StorageError::PermissionDenied { key } => write!(f, "access denied for '{}'", key),
            StorageError::QuotaExceeded { used, limit } => {
                write!(f, "quota exceeded: {}/{} bytes", used, limit)
            }
        }
    }
}

impl std::error::Error for StorageError {}

fn main() {
    let err = StorageError::NotFound { key: "user:42".into() };
    // Callers MUST have a wildcard arm because of #[non_exhaustive]
    match err {
        StorageError::NotFound { ref key } => println!("Missing: {}", key),
        StorageError::PermissionDenied { .. } => println!("Access denied"),
        _ => println!("Storage error: {}", err),
    }
}
```

## Application Error Design

### Rule 1: Use anyhow for orchestration

Application code orchestrates library calls. You're reading files, parsing configs, making HTTP requests, querying databases. Each step can fail with a different error type. `anyhow` handles all of them:

```rust
use anyhow::{Context, Result};
use std::collections::HashMap;

fn load_app_config() -> Result<HashMap<String, String>> {
    let contents = std::fs::read_to_string("app.toml")
        .context("failed to read app config")?;

    let mut config = HashMap::new();
    for line in contents.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some((k, v)) = line.split_once('=') {
            config.insert(k.trim().to_string(), v.trim().to_string());
        }
    }
    Ok(config)
}

fn run() -> Result<()> {
    let config = load_app_config()?;
    let host = config.get("host").cloned().unwrap_or("localhost".into());
    println!("Host: {}", host);
    Ok(())
}

fn main() {
    if let Err(e) = run() {
        eprintln!("Fatal: {:#}", e);
        std::process::exit(1);
    }
}
```

### Rule 2: Have a top-level error type for HTTP/API boundaries

If you're building a web service, you need an error type that maps to HTTP status codes. This is one place where application code *does* benefit from typed errors:

```rust
use thiserror::Error;

#[derive(Debug, Error)]
enum ApiError {
    #[error("resource not found: {0}")]
    NotFound(String),

    #[error("bad request: {0}")]
    BadRequest(String),

    #[error("unauthorized")]
    Unauthorized,

    #[error("internal error: {0}")]
    Internal(#[from] anyhow::Error),
}

impl ApiError {
    fn status_code(&self) -> u16 {
        match self {
            ApiError::NotFound(_) => 404,
            ApiError::BadRequest(_) => 400,
            ApiError::Unauthorized => 401,
            ApiError::Internal(_) => 500,
        }
    }
}

fn handle_get_user(id: u64) -> Result<String, ApiError> {
    if id == 0 {
        return Err(ApiError::BadRequest("user ID must be positive".into()));
    }
    if id > 1000 {
        return Err(ApiError::NotFound(format!("user {}", id)));
    }
    Ok(format!("{{\"id\": {}, \"name\": \"User {}\"}}", id, id))
}

fn main() {
    for id in [42, 0, 9999] {
        match handle_get_user(id) {
            Ok(body) => println!("200: {}", body),
            Err(e) => println!("{}: {}", e.status_code(), e),
        }
    }
}
```

### Rule 3: Add context at every layer boundary

Every time an error crosses a layer boundary (database → service → handler), add context:

```rust
use anyhow::{Context, Result};

fn db_get_user(id: u64) -> Result<String> {
    if id == 0 {
        anyhow::bail!("no rows returned");
    }
    Ok(format!("user_{}", id))
}

fn service_get_user(id: u64) -> Result<String> {
    let user = db_get_user(id)
        .with_context(|| format!("failed to fetch user {} from database", id))?;
    Ok(user)
}

fn handler_get_user(id_param: &str) -> Result<String> {
    let id: u64 = id_param.parse()
        .with_context(|| format!("invalid user ID parameter: '{}'", id_param))?;

    let user = service_get_user(id)
        .context("user lookup failed")?;

    Ok(format!("{{\"user\": \"{}\"}}", user))
}

fn main() {
    match handler_get_user("0") {
        Ok(resp) => println!("{}", resp),
        Err(e) => {
            eprintln!("Error: {:#}", e);
            // Error: user lookup failed: failed to fetch user 0 from database: no rows returned
        }
    }
}
```

That error chain tells you exactly what happened, where, and why. You couldn't get that from a bare `io::Error`.

## The Hybrid Pattern

Most real Rust projects use both `thiserror` and `anyhow`. Here's the pattern:

```text
┌─────────────────────────────┐
│    main() / CLI / HTTP      │  ← anyhow::Result
├─────────────────────────────┤
│    Application services     │  ← anyhow::Result with .context()
├─────────────────────────────┤
│    Domain logic             │  ← thiserror enums (typed)
├─────────────────────────────┤
│    Infrastructure / IO      │  ← thiserror or std errors
└─────────────────────────────┘
```

Domain errors are typed because business logic needs to branch on them. Everything above the domain layer uses `anyhow` because it's just propagating and adding context. The `?` operator bridges the two seamlessly — `thiserror` types implement `std::error::Error`, so they convert into `anyhow::Error` automatically.

This isn't a theoretical framework. It's what most well-maintained Rust projects actually do. Spend your error-type budget where it matters (domain boundaries) and use `anyhow` everywhere else.
