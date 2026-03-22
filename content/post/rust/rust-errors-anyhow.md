---
title: "Lesson 6: anyhow — When you don't care about the type"
author: Atharva Pandey
series: "Rust Error Handling at Scale"
lesson: 6
keywords: [Rust, rust, error handling, anyhow, dynamic errors, context, backtrace]
tags: [Rust tutorial, rust, error-handling]
date: '2024-07-16T16:20:00.000Z'
---

There's a dirty secret in Rust error handling: half the time, you don't actually need typed errors. You need errors that are easy to create, easy to chain, and easy to print. That's `anyhow`. Same author as `thiserror` (David Tolnay), completely different use case. Where `thiserror` is for defining precise error types, `anyhow` is for *using* errors without ceremony.

## The Problem anyhow Solves

Without `anyhow`, when you have a function that can fail in multiple unrelated ways, you either:

1. Create a custom enum with `From` impls for everything (tedious for application code)
2. Use `Box<dyn std::error::Error>` (works but clunky)
3. Return `String` as your error (don't)

```rust
// Without anyhow — this gets old fast
fn setup() -> Result<(), Box<dyn std::error::Error>> {
    let config = std::fs::read_to_string("config.toml")?;
    let port: u16 = config.trim().parse()?;
    if port < 1024 {
        return Err("port must be >= 1024".into()); // .into() on a &str, gross
    }
    println!("Running on port {}", port);
    Ok(())
}

fn main() {
    if let Err(e) = setup() {
        eprintln!("Setup failed: {}", e);
    }
}
```

`Box<dyn Error>` works but it's ugly, and you lose the ability to easily add context.

## Enter anyhow

Add to `Cargo.toml`:

```toml
[dependencies]
anyhow = "1"
```

Now:

```rust
use anyhow::{Result, Context};

fn setup() -> Result<()> {
    let config = std::fs::read_to_string("config.toml")
        .context("failed to read config file")?;
    let port: u16 = config.trim().parse()
        .context("config file doesn't contain a valid port number")?;

    anyhow::ensure!(port >= 1024, "port {} is privileged, use >= 1024", port);

    println!("Running on port {}", port);
    Ok(())
}

fn main() {
    if let Err(e) = setup() {
        eprintln!("Error: {:#}", e);
        // Error: failed to read config file: No such file or directory (os error 2)
    }
}
```

`anyhow::Result<T>` is just `Result<T, anyhow::Error>`. The `anyhow::Error` type wraps any error that implements `std::error::Error`, so `?` works on everything without writing `From` impls.

## Context: The Killer Feature

The `.context()` method is why I reach for `anyhow`. It wraps the original error with a human-readable message that explains *what you were doing* when the error happened:

```rust
use anyhow::{Result, Context};
use std::fs;

fn read_user_config(username: &str) -> Result<String> {
    let path = format!("/home/{}/.config/myapp/config.toml", username);
    let contents = fs::read_to_string(&path)
        .with_context(|| format!("failed to read config for user '{}'", username))?;
    Ok(contents)
}

fn initialize_app(username: &str) -> Result<()> {
    let config = read_user_config(username)
        .context("app initialization failed")?;
    println!("Config: {} bytes", config.len());
    Ok(())
}

fn main() {
    if let Err(e) = initialize_app("atharva") {
        // Print the full chain with {:#}
        eprintln!("Error: {:#}", e);
        // Error: app initialization failed: failed to read config for user 'atharva':
        //   No such file or directory (os error 2)

        // Or print each cause on its own line
        eprintln!("\nDetailed:");
        for cause in e.chain() {
            eprintln!("  - {}", cause);
        }
    }
}
```

Notice `.context()` vs `.with_context()` — the latter takes a closure, so the context message is only constructed if there's actually an error. Use `.with_context()` when the message involves formatting or allocation.

## Creating Errors from Scratch

`anyhow` provides several ways to create errors without wrapping an existing one:

```rust
use anyhow::{anyhow, bail, ensure, Result};

fn validate_name(name: &str) -> Result<()> {
    // bail! — return an error immediately
    if name.is_empty() {
        bail!("name cannot be empty");
    }

    // ensure! — assert-like macro that returns Err on false
    ensure!(name.len() <= 100, "name too long: {} chars (max 100)", name.len());
    ensure!(
        name.chars().all(|c| c.is_alphanumeric() || c == ' ' || c == '-'),
        "name contains invalid characters: '{}'",
        name
    );

    Ok(())
}

fn process(name: &str) -> Result<String> {
    validate_name(name)?;

    // anyhow! — create an error value without returning
    let _example = anyhow!("this creates an error but doesn't return it");

    Ok(format!("Hello, {}!", name))
}

fn main() {
    for name in &["Atharva", "", "a]b[c", "Valid Name"] {
        match process(name) {
            Ok(greeting) => println!("{}", greeting),
            Err(e) => eprintln!("Invalid: {}", e),
        }
    }
}
```

`bail!` is the one I use most. It's equivalent to `return Err(anyhow!("..."))` but reads better.

## Downcasting: Getting the Original Error Back

Sometimes you *do* need to inspect the underlying error type. `anyhow::Error` supports downcasting:

```rust
use anyhow::{Result, Context};
use std::io;

fn read_file(path: &str) -> Result<String> {
    std::fs::read_to_string(path)
        .context("failed to read file")
}

fn main() {
    let result = read_file("nonexistent.txt");

    if let Err(ref e) = result {
        // Try to get the underlying io::Error
        if let Some(io_err) = e.downcast_ref::<io::Error>() {
            match io_err.kind() {
                io::ErrorKind::NotFound => {
                    println!("File not found — creating default");
                }
                io::ErrorKind::PermissionDenied => {
                    println!("Permission denied — check file permissions");
                }
                _ => {
                    println!("IO error: {}", io_err);
                }
            }
        } else {
            println!("Other error: {}", e);
        }
    }
}
```

This works, but if you find yourself downcasting a lot, that's a signal you should be using typed errors (`thiserror`) instead. Downcasting should be the exception, not the pattern.

## anyhow in main()

`anyhow` works great as the return type of `main()`:

```rust
use anyhow::{Result, Context};

fn run() -> Result<()> {
    let port: u16 = std::env::var("PORT")
        .context("PORT environment variable not set")?
        .parse()
        .context("PORT is not a valid number")?;

    println!("Starting server on port {}", port);
    Ok(())
}

fn main() -> Result<()> {
    run()
}
```

When `main()` returns `Err`, `anyhow` prints the error with its full chain using the `Debug` format, which includes all the context messages.

## anyhow vs thiserror: The Decision Framework

This question comes up constantly, so here's how I think about it:

**Use thiserror when:**
- You're writing a library
- Callers need to match on specific error variants
- You want the compiler to enforce exhaustive error handling
- Your errors are part of a public API

**Use anyhow when:**
- You're writing application code (binaries, CLIs, servers)
- You just need errors to propagate and print nicely
- You want to add context to errors from different sources
- You don't need callers to programmatically inspect error types

**Use both when:**
- Your library defines errors with `thiserror`
- Your application's `main()` and top-level functions use `anyhow`
- Service layers might use `thiserror` for domain errors and `anyhow` for orchestration

```rust
// In your library crate — thiserror
use thiserror::Error;

#[derive(Debug, Error)]
pub enum ParseError {
    #[error("invalid syntax at line {line}")]
    InvalidSyntax { line: usize },

    #[error("unexpected token: {0}")]
    UnexpectedToken(String),
}

pub fn parse(input: &str) -> Result<Vec<String>, ParseError> {
    if input.is_empty() {
        return Err(ParseError::InvalidSyntax { line: 1 });
    }
    Ok(input.lines().map(String::from).collect())
}
```

```rust
// In your application crate — anyhow
use anyhow::{Result, Context};

// Assuming `mylib` provides `parse` from above
fn process_file(path: &str) -> Result<()> {
    let contents = std::fs::read_to_string(path)
        .with_context(|| format!("failed to read {}", path))?;

    // parse() returns Result<_, ParseError>
    // anyhow accepts it via ? because ParseError implements std::error::Error
    // let data = mylib::parse(&contents)
    //     .context("failed to parse config")?;

    println!("Processed {} lines", contents.lines().count());
    Ok(())
}

fn main() -> Result<()> {
    process_file("input.txt")
}
```

## A Complete CLI Example

Here's a realistic CLI tool that uses `anyhow` throughout:

```rust
use anyhow::{bail, ensure, Context, Result};
use std::collections::HashMap;
use std::fs;

#[derive(Debug)]
struct Config {
    entries: HashMap<String, String>,
}

fn parse_config(contents: &str) -> Result<Config> {
    let mut entries = HashMap::new();

    for (i, line) in contents.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }

        let parts: Vec<&str> = line.splitn(2, '=').collect();
        ensure!(
            parts.len() == 2,
            "invalid config at line {}: expected KEY=VALUE, got '{}'",
            i + 1,
            line
        );

        let key = parts[0].trim().to_string();
        let value = parts[1].trim().to_string();

        if entries.contains_key(&key) {
            bail!("duplicate key '{}' at line {}", key, i + 1);
        }

        entries.insert(key, value);
    }

    Ok(Config { entries })
}

fn load_config(path: &str) -> Result<Config> {
    let contents = fs::read_to_string(path)
        .with_context(|| format!("cannot read config from '{}'", path))?;

    parse_config(&contents)
        .with_context(|| format!("failed to parse '{}'", path))
}

fn get_required<'a>(config: &'a Config, key: &str) -> Result<&'a str> {
    config.entries.get(key)
        .map(|s| s.as_str())
        .ok_or_else(|| anyhow::anyhow!("missing required config key: '{}'", key))
}

fn run() -> Result<()> {
    let config = load_config("app.conf")?;

    let host = get_required(&config, "host")?;
    let port_str = get_required(&config, "port")?;
    let port: u16 = port_str.parse()
        .with_context(|| format!("invalid port value: '{}'", port_str))?;

    println!("Starting server at {}:{}", host, port);
    Ok(())
}

fn main() {
    if let Err(e) = run() {
        eprintln!("Error: {:#}", e);
        std::process::exit(1);
    }
}
```

Every error has context. Every failure message tells you *what went wrong* and *where*. No custom error types needed — for application code like this, `anyhow` is the right tool. Save `thiserror` for your libraries and domain boundaries.
