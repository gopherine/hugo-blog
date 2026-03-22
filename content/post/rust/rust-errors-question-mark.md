---
title: "Lesson 3: The ? Operator — Propagation made elegant"
author: Atharva Pandey
series: "Rust Error Handling at Scale"
lesson: 3
keywords: [Rust, rust, error handling, question mark operator, error propagation, From trait, try]
tags: [Rust tutorial, rust, error-handling]
date: '2024-07-09T14:30:00.000Z'
---

Before the `?` operator existed, Rust had `try!()` — a macro that did the same thing but looked ugly and nested poorly. When `?` landed in Rust 1.13, it was one of those rare language changes where everyone immediately agreed it was better. I remember refactoring an entire crate the same week, deleting dozens of `match` blocks and `try!()` calls, and the code just... breathed.

## What ? Actually Does

The `?` operator is syntactic sugar for early return on error. When you write this:

```rust
use std::fs;
use std::io;

fn read_config() -> Result<String, io::Error> {
    let contents = fs::read_to_string("config.toml")?;
    Ok(contents)
}

fn main() {
    match read_config() {
        Ok(c) => println!("{}", c),
        Err(e) => eprintln!("Error: {}", e),
    }
}
```

The compiler translates that `?` into roughly this:

```rust
use std::fs;
use std::io;

fn read_config() -> Result<String, io::Error> {
    let contents = match fs::read_to_string("config.toml") {
        Ok(val) => val,
        Err(e) => return Err(e.into()),
    };
    Ok(contents)
}

fn main() {
    match read_config() {
        Ok(c) => println!("{}", c),
        Err(e) => eprintln!("Error: {}", e),
    }
}
```

Two critical details here. First, on success, `?` unwraps the `Ok` value and gives it to you. Second, on failure, it calls `.into()` on the error and returns it from the function immediately. That `.into()` call is where the magic of automatic error conversion happens.

## Chaining ? for Clean Pipelines

Where `?` really shines is chaining multiple fallible operations. Without it, you'd have nested matches or a chain of `and_then` calls. With it:

```rust
use std::fs;
use std::io;

#[derive(Debug)]
struct ServerConfig {
    host: String,
    port: u16,
}

fn load_server_config(path: &str) -> Result<ServerConfig, Box<dyn std::error::Error>> {
    let contents = fs::read_to_string(path)?;
    let mut host = String::from("127.0.0.1");
    let mut port: u16 = 3000;

    for line in contents.lines() {
        let parts: Vec<&str> = line.splitn(2, '=').collect();
        if parts.len() != 2 {
            continue;
        }
        let key = parts[0].trim();
        let value = parts[1].trim();

        match key {
            "host" => host = value.to_string(),
            "port" => port = value.parse()?,
            _ => {}
        }
    }

    Ok(ServerConfig { host, port })
}

fn main() {
    match load_server_config("server.conf") {
        Ok(config) => println!("{:?}", config),
        Err(e) => eprintln!("Failed to load config: {}", e),
    }
}
```

Two different error types — `io::Error` from `read_to_string` and `ParseIntError` from `parse()` — both propagated with `?` into a single `Box<dyn std::error::Error>`. The `.into()` that `?` calls handles the conversion automatically because both types implement the `Error` trait.

## The From Trait: How ? Converts Errors

This is the mechanism you need to understand. When you write `result?` and the error type of `result` doesn't match the return type of your function, Rust calls `From::from()` to convert it. If there's no `From` impl, it won't compile.

```rust
use std::fmt;
use std::io;
use std::num::ParseIntError;

#[derive(Debug)]
enum ConfigError {
    Io(io::Error),
    Parse(ParseIntError),
}

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ConfigError::Io(e) => write!(f, "IO error: {}", e),
            ConfigError::Parse(e) => write!(f, "parse error: {}", e),
        }
    }
}

impl std::error::Error for ConfigError {}

// These From impls are what make ? work
impl From<io::Error> for ConfigError {
    fn from(e: io::Error) -> Self {
        ConfigError::Io(e)
    }
}

impl From<ParseIntError> for ConfigError {
    fn from(e: ParseIntError) -> Self {
        ConfigError::Parse(e)
    }
}

fn load_port(path: &str) -> Result<u16, ConfigError> {
    let contents = std::fs::read_to_string(path)?; // io::Error → ConfigError via From
    let port = contents.trim().parse::<u16>()?;     // ParseIntError → ConfigError via From
    Ok(port)
}

fn main() {
    match load_port("port.txt") {
        Ok(p) => println!("Port: {}", p),
        Err(e) => eprintln!("Error: {}", e),
    }
}
```

This is verbose, yes — writing `From` impls by hand gets old fast. That's exactly why `thiserror` exists (lesson 5). But understanding this mechanism is essential because it's what *every* error handling crate builds on.

## ? With Option

Since Rust 1.22, `?` works on `Option` too. In a function that returns `Option<T>`, using `?` on a `None` value returns `None` early:

```rust
fn find_middle_name(full_name: &str) -> Option<&str> {
    let mut parts = full_name.split_whitespace();
    let _first = parts.next()?;
    let middle = parts.next()?;
    let _last = parts.next()?;  // Must have at least 3 parts
    Some(middle)
}

fn main() {
    println!("{:?}", find_middle_name("Atharva Kumar Pandey")); // Some("Kumar")
    println!("{:?}", find_middle_name("Atharva Pandey"));       // None
    println!("{:?}", find_middle_name("Atharva"));              // None
}
```

Clean, readable, no nesting. But there's a catch — you can't mix `?` on `Result` and `Option` in the same function. If your function returns `Result`, using `?` on an `Option` won't compile. You need to convert first:

```rust
use std::collections::HashMap;

fn get_port(config: &HashMap<String, String>) -> Result<u16, String> {
    let port_str = config.get("port")
        .ok_or_else(|| String::from("missing 'port' key"))?;

    port_str.parse::<u16>()
        .map_err(|e| format!("invalid port: {}", e))
}

fn main() {
    let mut config = HashMap::new();
    config.insert("port".into(), "8080".into());

    match get_port(&config) {
        Ok(p) => println!("Port: {}", p),
        Err(e) => eprintln!("{}", e),
    }
}
```

## ? in main()

Since Rust 1.26, `main()` can return `Result`:

```rust
use std::fs;
use std::num::ParseIntError;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let contents = fs::read_to_string("number.txt")?;
    let n: i64 = contents.trim().parse()?;
    println!("Number is: {}", n);
    Ok(())
}
```

If `main` returns `Err`, Rust prints the error using the `Debug` format and exits with a non-zero status code. Handy for quick scripts and prototyping.

## The Termination Trait

Under the hood, `main()` returning `Result` works because of the `Termination` trait. You can implement it for your own types if you want custom exit behavior:

```rust
use std::process::ExitCode;

enum AppResult {
    Success,
    ConfigError(String),
    RuntimeError(String),
}

impl std::process::Termination for AppResult {
    fn report(self) -> ExitCode {
        match self {
            AppResult::Success => ExitCode::SUCCESS,
            AppResult::ConfigError(msg) => {
                eprintln!("Configuration error: {}", msg);
                ExitCode::from(2)
            }
            AppResult::RuntimeError(msg) => {
                eprintln!("Runtime error: {}", msg);
                ExitCode::from(1)
            }
        }
    }
}

fn main() -> AppResult {
    let config_value = match std::env::var("APP_MODE") {
        Ok(v) => v,
        Err(_) => return AppResult::ConfigError("APP_MODE not set".into()),
    };

    println!("Running in {} mode", config_value);
    AppResult::Success
}
```

## Common Pitfalls

**Mixing Result and Option with ?** — You'll hit this early on. The fix is always `.ok_or()` or `.ok_or_else()` to convert `Option` to `Result`, or `.ok()` to go the other direction.

**Forgetting that ? calls .into()** — If you're getting weird compilation errors about `From` not being implemented, check that there's a `From<SourceError> for YourError` impl. The compiler error message usually tells you exactly which conversion is missing.

**Using ? in closures** — `?` returns from the *enclosing function*, not from a closure. This bites people in iterator chains:

```rust
fn process_lines(input: &str) -> Result<Vec<i64>, std::num::ParseIntError> {
    // This works — ? inside the closure returns from the closure,
    // and collect() handles the Result
    input.lines()
        .map(|line| line.trim().parse::<i64>())
        .collect()
}

fn main() {
    match process_lines("1\n2\n3\n") {
        Ok(nums) => println!("{:?}", nums),
        Err(e) => eprintln!("Parse error: {}", e),
    }
}
```

Wait, actually `?` in a closure *does* return from the closure, not the outer function. The closure's return type becomes `Result<T, E>`, and then you use `collect()` to aggregate. But if you try to use `?` inside `for_each`, it won't work because `for_each` expects `()`, not `Result`. Stick with `map` + `collect` for fallible iterator processing.

## The Big Picture

`?` is arguably Rust's most important operator for practical error handling. It makes propagation so cheap syntactically that you never feel tempted to ignore errors. Compare:

```rust
// Go-style (if Rust didn't have ?)
let file = match fs::read_to_string(path) {
    Ok(f) => f,
    Err(e) => return Err(e.into()),
};
let port = match contents.parse::<u16>() {
    Ok(p) => p,
    Err(e) => return Err(e.into()),
};

// With ?
let file = fs::read_to_string(path)?;
let port = contents.parse::<u16>()?;
```

Same behavior, fraction of the noise. And because `?` calls `From::from()` automatically, your error types compose cleanly across crate boundaries. That's the foundation that everything else in this course builds on.
