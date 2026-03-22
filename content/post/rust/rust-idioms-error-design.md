---
title: "Lesson 12: Designing Error Types — thiserror vs anyhow"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 12
keywords: [Rust, rust, idiomatic, error handling, thiserror, anyhow, error types]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-04-29T12:18:00.000Z'
---

I've seen two extremes in Rust error handling. On one end: `Box<dyn std::error::Error>` everywhere, a stringly-typed mess where you can't distinguish a network timeout from a parse failure. On the other end: 47 custom error types with hand-written `Display` and `From` implementations, an over-engineered cathedral of boilerplate.

The right answer is somewhere in the middle. And the two crates that get you there are `thiserror` and `anyhow`.

---

## The Wrong Ways

### Wrong Way #1: String Errors

```rust
fn parse_config(input: &str) -> Result<u16, String> {
    let port: u16 = input.parse().map_err(|e| format!("bad port: {}", e))?;
    if port < 1024 {
        return Err("port must be >= 1024".to_string());
    }
    Ok(port)
}
```

The caller gets a `String`. What can they do with it? Print it. That's about it. They can't match on error variants, can't programmatically decide how to handle different failures, can't distinguish between "bad port" and "port too low." It's a dead end.

### Wrong Way #2: Box\<dyn Error\> for Everything

```rust
fn do_stuff() -> Result<(), Box<dyn std::error::Error>> {
    let content = std::fs::read_to_string("config.txt")?;
    let port: u16 = content.trim().parse()?;
    // caller gets... some error. Who knows what kind.
    Ok(())
}
```

Better than strings — at least the errors retain their original types. But the caller still can't easily distinguish error types without downcasting, which is fragile and verbose.

### Wrong Way #3: Manual Boilerplate

```rust
use std::fmt;
use std::io;
use std::num::ParseIntError;

#[derive(Debug)]
enum ConfigError {
    Io(io::Error),
    Parse(ParseIntError),
    Validation(String),
}

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ConfigError::Io(e) => write!(f, "IO error: {}", e),
            ConfigError::Parse(e) => write!(f, "parse error: {}", e),
            ConfigError::Validation(msg) => write!(f, "validation error: {}", msg),
        }
    }
}

impl std::error::Error for ConfigError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            ConfigError::Io(e) => Some(e),
            ConfigError::Parse(e) => Some(e),
            ConfigError::Validation(_) => None,
        }
    }
}

impl From<io::Error> for ConfigError {
    fn from(e: io::Error) -> Self { ConfigError::Io(e) }
}

impl From<ParseIntError> for ConfigError {
    fn from(e: ParseIntError) -> Self { ConfigError::Parse(e) }
}
```

This is correct and thorough. It's also 30+ lines of boilerplate for three error variants. Every new variant needs a `Display` arm, a `source` arm, and a `From` impl. Nobody wants to write this by hand.

---

## thiserror — The Library Way

`thiserror` generates all that boilerplate with a derive macro:

```rust
use thiserror::Error;

#[derive(Debug, Error)]
enum ConfigError {
    #[error("IO error reading config")]
    Io(#[from] std::io::Error),

    #[error("failed to parse port number")]
    Parse(#[from] std::num::ParseIntError),

    #[error("validation error: {0}")]
    Validation(String),

    #[error("port {port} is below minimum {min}")]
    PortTooLow { port: u16, min: u16 },
}

fn load_config(path: &str) -> Result<u16, ConfigError> {
    let content = std::fs::read_to_string(path)?; // auto-converts via From
    let port: u16 = content.trim().parse()?;       // auto-converts via From

    if port < 1024 {
        return Err(ConfigError::PortTooLow { port, min: 1024 });
    }

    Ok(port)
}

fn main() {
    match load_config("config.txt") {
        Ok(port) => println!("Port: {}", port),
        Err(ConfigError::Io(e)) => eprintln!("Can't read config: {}", e),
        Err(ConfigError::Parse(e)) => eprintln!("Config has bad number: {}", e),
        Err(ConfigError::PortTooLow { port, min }) => {
            eprintln!("Port {} is below minimum {}", port, min);
        }
        Err(e) => eprintln!("Config error: {}", e),
    }
}
```

What `thiserror` gives you:
- `#[error("...")]` generates the `Display` impl.
- `#[from]` generates the `From` impl (and marks it as the `source`).
- `{0}`, `{field}` interpolation in error messages.
- Proper `Error::source()` chain for debugging.

What it *doesn't* do: add any runtime cost. `thiserror` is purely a compile-time code generator. The generated code is exactly what you'd write by hand.

---

## anyhow — The Application Way

`anyhow` takes the opposite approach. Instead of structured error types, it gives you a single `anyhow::Error` type that wraps any error with optional context:

```rust
use anyhow::{Context, Result};

fn load_config(path: &str) -> Result<u16> {
    let content = std::fs::read_to_string(path)
        .with_context(|| format!("failed to read config from {}", path))?;

    let port: u16 = content.trim().parse()
        .context("config file does not contain a valid port number")?;

    anyhow::ensure!(port >= 1024, "port {} is below minimum 1024", port);

    Ok(port)
}

fn main() {
    match load_config("config.txt") {
        Ok(port) => println!("Port: {}", port),
        Err(e) => {
            eprintln!("Error: {}", e);
            // Print the full chain of context
            for cause in e.chain().skip(1) {
                eprintln!("  caused by: {}", cause);
            }
        }
    }
}
```

`anyhow::Result<T>` is short for `Result<T, anyhow::Error>`. The `.context()` and `.with_context()` methods add human-readable context to errors as they propagate up. The result is a chain of error messages that tells the full story:

```
Error: failed to read config from config.txt
  caused by: No such file or directory (os error 2)
```

---

## The Decision: thiserror vs anyhow

Here's my straightforward rule:

**Libraries → `thiserror`**. Your callers need to match on error variants and handle them programmatically. Structured errors are part of your public API.

**Applications → `anyhow`**. You're the end consumer of errors. You mostly just need to print them, log them, or convert them to HTTP responses. Context matters more than structure.

**Both in the same project?** Absolutely. Libraries define errors with `thiserror`. Applications use `anyhow` for their own error handling and convert library errors with `?` or `.context()`.

```rust
// In a library crate:
use thiserror::Error;

#[derive(Debug, Error)]
pub enum DatabaseError {
    #[error("connection failed: {0}")]
    Connection(String),
    #[error("query failed: {0}")]
    Query(String),
    #[error("row not found")]
    NotFound,
}

// In an application crate:
use anyhow::{Context, Result};

fn handle_request(user_id: i64) -> Result<String> {
    let user = find_user(user_id)
        .context("failed to look up user for request")?;
    Ok(format!("Hello, {}", user))
}

fn find_user(id: i64) -> Result<String, DatabaseError> {
    // ... database lookup
    Err(DatabaseError::NotFound)
}
```

---

## Designing Good Error Enums

A few principles I follow:

### 1. Group by failure mode, not by source

```rust
use thiserror::Error;

// BAD: organized by where the error came from
#[derive(Debug, Error)]
enum BadError {
    #[error("IO error")]
    Io(#[from] std::io::Error),
    #[error("JSON error")]
    Json(#[from] serde_json::Error),
    #[error("HTTP error")]
    Http(#[from] reqwest::Error),
}

// GOOD: organized by what went wrong
#[derive(Debug, Error)]
enum ApiError {
    #[error("failed to load configuration: {0}")]
    ConfigLoad(#[source] std::io::Error),
    #[error("invalid response from upstream: {0}")]
    BadResponse(String),
    #[error("request timed out after {0}ms")]
    Timeout(u64),
    #[error("authentication failed")]
    Unauthorized,
}
```

The caller cares about *what went wrong*, not which crate produced the error.

### 2. Include relevant data in error variants

```rust
use thiserror::Error;

#[derive(Debug, Error)]
enum ValidationError {
    #[error("field '{field}' is required")]
    Required { field: String },

    #[error("field '{field}' must be at most {max} characters (got {actual})")]
    TooLong { field: String, max: usize, actual: usize },

    #[error("invalid email format: '{value}'")]
    InvalidEmail { value: String },
}
```

Error messages should contain enough information to diagnose the problem without digging through logs.

### 3. Don't over-granularize

```rust
// TOO MANY variants — do you really need to distinguish all of these?
#[derive(Debug)]
enum OverEngineered {
    FileNotFound,
    FilePermissionDenied,
    FileAlreadyExists,
    FileTooLarge,
    FileCorrupted,
    FileIsDirectory,
    // ...20 more
}

// Better: group into meaningful categories
use thiserror::Error;

#[derive(Debug, Error)]
enum StorageError {
    #[error("file not found: {path}")]
    NotFound { path: String },

    #[error("access denied: {path}")]
    AccessDenied { path: String },

    #[error("storage I/O error")]
    Io(#[from] std::io::Error),
}
```

If your callers won't handle `FileIsDirectory` differently from `FileCorrupted`, don't make them separate variants. Merge them into a general variant with a descriptive message.

---

## Error Context Chaining

Whether you use `thiserror` or `anyhow`, context chaining is crucial. A bare "No such file or directory" is useless. "Failed to load user preferences from ~/.config/myapp/prefs.toml: No such file or directory" tells you exactly what happened.

With `anyhow`:

```rust
use anyhow::{Context, Result};

fn load_preferences() -> Result<String> {
    let path = dirs_next::config_dir()
        .map(|p| p.join("myapp/prefs.toml"))
        .unwrap_or_else(|| "prefs.toml".into());

    std::fs::read_to_string(&path)
        .with_context(|| format!("failed to load preferences from {}", path.display()))
}
```

With `thiserror`:

```rust
use thiserror::Error;
use std::path::PathBuf;

#[derive(Debug, Error)]
#[error("failed to load preferences from {path}")]
struct PreferencesError {
    path: PathBuf,
    #[source]
    cause: std::io::Error,
}
```

Both approaches produce clear error chains. Pick the one that fits your crate's architecture.

---

## The `#[from]` vs `#[source]` Distinction

In `thiserror`:

- `#[from]` generates a `From` impl AND marks it as the `source`. Use when you want automatic `?` conversion.
- `#[source]` marks a field as the source without generating `From`. Use when you want error chaining without automatic conversion (or when two variants would produce conflicting `From` impls).

```rust
use thiserror::Error;

#[derive(Debug, Error)]
enum ProcessError {
    // #[from] — auto-converts io::Error to ProcessError via ?
    #[error("failed to read input")]
    ReadError(#[from] std::io::Error),

    // #[source] — preserves the error chain but no auto-conversion
    #[error("failed to write output to {path}")]
    WriteError {
        path: String,
        #[source]
        cause: std::io::Error,
    },
}
```

You can only have one `#[from]` per source error type (otherwise you'd have conflicting `From` impls). If two variants wrap `io::Error`, make one `#[from]` and the other `#[source]`.

---

## Key Takeaways

- **Libraries**: use `thiserror` for structured, matchable error types.
- **Applications**: use `anyhow` for ergonomic error handling with context.
- Group error variants by failure mode, not by source crate.
- Include relevant data in error variants — field names, paths, values.
- Don't over-granularize — merge variants that callers won't distinguish.
- Always add context to errors as they propagate. "What failed" is as important as "why it failed."
- `#[from]` gives you automatic `?` conversion. `#[source]` gives you error chaining without it.
