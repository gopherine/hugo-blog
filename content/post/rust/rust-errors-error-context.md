---
title: "Lesson 8: Adding Context — Error chains and backtraces"
author: Atharva Pandey
series: "Rust Error Handling at Scale"
lesson: 8
keywords: [Rust, rust, error handling, error context, error chain, backtrace, source, anyhow context]
tags: [Rust tutorial, rust, error-handling]
date: '2024-07-21T13:10:00.000Z'
---

Picture this: it's 2 AM, your pager goes off, and the log says `"connection refused"`. Connection to *what*? From *where*? During *which operation*? That single error message is technically correct and practically useless. I've been in that exact situation enough times to develop strong opinions about error context. Every error in a production system should answer three questions: what happened, where did it happen, and what was the system trying to do when it happened.

## The Error Chain Model

Rust's `std::error::Error` trait has a `source()` method that returns the underlying cause of an error:

```rust
pub trait Error: Debug + Display {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        None
    }
}
```

This creates a chain: your error wraps another error, which wraps another, all the way down to the root cause. Walking the chain gives you the full story.

```rust
use std::fmt;
use std::io;

#[derive(Debug)]
struct ConfigError {
    path: String,
    source: io::Error,
}

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "failed to load config from '{}'", self.path)
    }
}

impl std::error::Error for ConfigError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        Some(&self.source)
    }
}

#[derive(Debug)]
struct AppStartupError {
    phase: String,
    source: ConfigError,
}

impl fmt::Display for AppStartupError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "startup failed during {}", self.phase)
    }
}

impl std::error::Error for AppStartupError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        Some(&self.source)
    }
}

fn walk_error_chain(err: &dyn std::error::Error) {
    let mut current: Option<&dyn std::error::Error> = Some(err);
    let mut depth = 0;
    while let Some(e) = current {
        let indent = "  ".repeat(depth);
        if depth == 0 {
            eprintln!("{}error: {}", indent, e);
        } else {
            eprintln!("{}caused by: {}", indent, e);
        }
        current = e.source();
        depth += 1;
    }
}

fn main() {
    let io_err = io::Error::new(io::ErrorKind::NotFound, "no such file");
    let config_err = ConfigError {
        path: "/etc/myapp/config.toml".into(),
        source: io_err,
    };
    let startup_err = AppStartupError {
        phase: "configuration".into(),
        source: config_err,
    };

    walk_error_chain(&startup_err);
    // error: startup failed during configuration
    //   caused by: failed to load config from '/etc/myapp/config.toml'
    //     caused by: no such file
}
```

Each layer adds meaning. The root cause is "no such file." But the context tells you *which* file and *when* in the application lifecycle the failure occurred.

## Context with anyhow

Manually building error chains with custom types is tedious. `anyhow` makes context-adding trivial:

```rust
use anyhow::{Context, Result};
use std::collections::HashMap;

fn read_file(path: &str) -> Result<String> {
    std::fs::read_to_string(path)
        .with_context(|| format!("failed to read '{}'", path))
}

fn parse_config(contents: &str) -> Result<HashMap<String, String>> {
    let mut map = HashMap::new();
    for (i, line) in contents.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let (key, value) = line.split_once('=')
            .with_context(|| format!("invalid syntax at line {}: '{}'", i + 1, line))?;
        map.insert(key.trim().to_string(), value.trim().to_string());
    }
    Ok(map)
}

fn load_database_url() -> Result<String> {
    let config = read_file("database.conf")
        .context("loading database configuration")?;
    let parsed = parse_config(&config)
        .context("parsing database configuration")?;
    let url = parsed.get("url")
        .cloned()
        .ok_or_else(|| anyhow::anyhow!("missing 'url' key"))
        .context("extracting database URL")?;
    Ok(url)
}

fn main() {
    match load_database_url() {
        Ok(url) => println!("DB URL: {}", url),
        Err(e) => {
            // {:#} prints the full chain in one line
            eprintln!("Error: {:#}", e);

            // Or iterate for structured logging
            eprintln!("\nFull chain:");
            for (i, cause) in e.chain().enumerate() {
                eprintln!("  {}: {}", i, cause);
            }
        }
    }
}
```

The `{:#}` format specifier on `anyhow::Error` prints the entire chain separated by colons. The `.chain()` iterator gives you each cause individually. Both are essential for debugging.

## Context with thiserror

For typed errors, `thiserror`'s `#[source]` and `#[from]` attributes build chains automatically:

```rust
use thiserror::Error;
use std::io;

#[derive(Debug, Error)]
enum StorageError {
    #[error("failed to read from disk")]
    DiskRead(#[source] io::Error),

    #[error("data corruption detected in block {block_id}")]
    Corruption {
        block_id: u64,
        #[source]
        cause: io::Error,
    },

    #[error("storage path not configured")]
    NotConfigured,
}

fn read_block(block_id: u64) -> Result<Vec<u8>, StorageError> {
    let path = format!("/data/blocks/{}.dat", block_id);
    std::fs::read(&path).map_err(|e| {
        if e.kind() == io::ErrorKind::InvalidData {
            StorageError::Corruption { block_id, cause: e }
        } else {
            StorageError::DiskRead(e)
        }
    })
}

fn main() {
    match read_block(42) {
        Ok(data) => println!("Read {} bytes", data.len()),
        Err(e) => {
            eprintln!("Error: {}", e);
            let mut source = std::error::Error::source(&e);
            while let Some(cause) = source {
                eprintln!("  caused by: {}", cause);
                source = cause.source();
            }
        }
    }
}
```

## Building a Context Helper

Sometimes you want context-adding without pulling in `anyhow`. Here's a lightweight pattern:

```rust
use std::fmt;

#[derive(Debug)]
struct WithContext<E> {
    context: String,
    source: E,
}

impl<E: fmt::Display> fmt::Display for WithContext<E> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.context)
    }
}

impl<E: std::error::Error + 'static> std::error::Error for WithContext<E> {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        Some(&self.source)
    }
}

trait ContextExt<T, E> {
    fn with_ctx(self, msg: impl Into<String>) -> Result<T, WithContext<E>>;
}

impl<T, E> ContextExt<T, E> for Result<T, E> {
    fn with_ctx(self, msg: impl Into<String>) -> Result<T, WithContext<E>> {
        self.map_err(|e| WithContext {
            context: msg.into(),
            source: e,
        })
    }
}

fn read_port() -> Result<u16, WithContext<std::num::ParseIntError>> {
    "not_a_number".parse::<u16>().with_ctx("parsing server port")
}

fn main() {
    match read_port() {
        Ok(p) => println!("Port: {}", p),
        Err(e) => {
            eprintln!("{}", e);
            if let Some(source) = std::error::Error::source(&e) {
                eprintln!("  caused by: {}", source);
            }
        }
    }
}
```

## Backtraces

Rust supports capturing backtraces in errors. Set `RUST_BACKTRACE=1` at runtime, and errors can capture where they were created.

With `anyhow`, backtraces are captured automatically when the environment variable is set:

```rust
use anyhow::{Context, Result};

fn deep_function() -> Result<()> {
    std::fs::read_to_string("/nonexistent/path")
        .context("deep_function failed")?;
    Ok(())
}

fn middle_function() -> Result<()> {
    deep_function().context("middle_function failed")
}

fn outer_function() -> Result<()> {
    middle_function().context("outer_function failed")
}

fn main() {
    // Run with RUST_BACKTRACE=1 to see backtraces
    if let Err(e) = outer_function() {
        eprintln!("Error: {:#}", e);
        eprintln!("\nBacktrace:\n{}", e.backtrace());
    }
}
```

For `thiserror`, you can add a `Backtrace` field:

```rust
use thiserror::Error;
use std::backtrace::Backtrace;

#[derive(Debug, Error)]
enum AppError {
    #[error("database query failed: {message}")]
    DatabaseError {
        message: String,
        backtrace: Backtrace,
    },
}

fn query_db() -> Result<String, AppError> {
    Err(AppError::DatabaseError {
        message: "connection pool exhausted".into(),
        backtrace: Backtrace::capture(),
    })
}

fn main() {
    // Set RUST_BACKTRACE=1 to get full backtraces
    if let Err(e) = query_db() {
        eprintln!("{}", e);
        match &e {
            AppError::DatabaseError { backtrace, .. } => {
                eprintln!("Backtrace:\n{}", backtrace);
            }
        }
    }
}
```

Note: `std::backtrace::Backtrace` requires nightly or Rust 1.65+ (stabilized). It only captures a real backtrace when `RUST_BACKTRACE=1` is set — otherwise it captures nothing, so there's minimal performance cost.

## Patterns for Production Context

### Include operation details, not just error messages

```rust
use anyhow::{Context, Result};

fn process_order(order_id: u64, customer_id: u64) -> Result<()> {
    validate_inventory(order_id)
        .with_context(|| format!(
            "processing order {} for customer {}: inventory validation failed",
            order_id, customer_id
        ))?;

    charge_payment(order_id, customer_id)
        .with_context(|| format!(
            "processing order {} for customer {}: payment charging failed",
            order_id, customer_id
        ))?;

    Ok(())
}

fn validate_inventory(order_id: u64) -> Result<()> {
    if order_id % 2 == 0 {
        anyhow::bail!("item out of stock for order {}", order_id);
    }
    Ok(())
}

fn charge_payment(_order_id: u64, _customer_id: u64) -> Result<()> {
    Ok(())
}

fn main() {
    if let Err(e) = process_order(42, 7) {
        eprintln!("{:#}", e);
    }
}
```

### Structured error reporting

```rust
use anyhow::Result;

fn report_error(err: &anyhow::Error) {
    // Primary message
    eprintln!("[ERROR] {}", err);

    // Cause chain
    let causes: Vec<String> = err.chain().skip(1).map(|c| c.to_string()).collect();
    if !causes.is_empty() {
        eprintln!("[CAUSE] {}", causes.join(" -> "));
    }

    // For JSON logging / alerting systems
    let error_json = serde_json::json!({
        "error": err.to_string(),
        "causes": causes,
        "chain_length": err.chain().count(),
    });
    eprintln!("[JSON] {}", error_json);
}

fn failing_operation() -> Result<()> {
    let inner: Result<()> = Err(anyhow::anyhow!("connection refused"));
    inner.map_err(|e| e.context("database connection failed"))
         .map_err(|e| e.context("user service initialization"))?;
    Ok(())
}

fn main() {
    if let Err(e) = failing_operation() {
        report_error(&e);
    }
}
```

(You'd need `serde_json` in your dependencies for the JSON part — but the pattern works with any serialization approach.)

## Context Anti-Patterns

**Don't repeat the error message in the context:**

```rust
use anyhow::{Context, Result};

fn bad_example() -> Result<String> {
    // Bad — redundant information
    std::fs::read_to_string("config.toml")
        .context("I/O error reading file")
    // The io::Error already says what the I/O error is!
}

fn good_example() -> Result<String> {
    // Good — adds *new* information
    std::fs::read_to_string("config.toml")
        .context("loading application configuration")
    // Now we know WHAT we were doing, and the source tells us WHY it failed
}

fn main() {
    let _ = bad_example();
    let _ = good_example();
}
```

**Don't add context at every single line:**

```rust
use anyhow::{Context, Result};

fn over_contexted() -> Result<String> {
    // Too much — context on trivial operations adds noise
    let path = std::env::var("CONFIG_PATH")
        .context("reading CONFIG_PATH env var")?;
    let contents = std::fs::read_to_string(&path)
        .with_context(|| format!("reading file at {}", path))?;
    let trimmed = contents.trim().to_string();
    Ok(trimmed)
}

fn right_amount() -> Result<String> {
    // Better — context at the meaningful boundary
    let path = std::env::var("CONFIG_PATH")
        .context("CONFIG_PATH not set")?;
    let contents = std::fs::read_to_string(&path)
        .with_context(|| format!("reading config from '{}'", path))?;
    Ok(contents.trim().to_string())
}

fn main() {
    let _ = over_contexted();
    let _ = right_amount();
}
```

Add context at layer boundaries and at points where you have meaningful information to add. Not at every `?`. The error chain should read like a story, not a stack trace.
