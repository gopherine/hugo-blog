---
title: "Lesson 2: Result and Option — The foundation"
author: Atharva Pandey
series: "Rust Error Handling at Scale"
lesson: 2
keywords: [Rust, rust, error handling, Result, Option, combinators, map, and_then, unwrap_or]
tags: [Rust tutorial, rust, error-handling]
date: '2024-07-07T08:45:00.000Z'
---

When I first started writing Rust, I used `match` on every single `Result` and `Option`. My code looked like a staircase — match inside match inside match, indented halfway across the screen. Then a colleague showed me combinators, and suddenly the code read like prose instead of a tax form.

## Result: More Than Just Ok and Err

You already know `Result<T, E>` has two variants. But `Result` comes with a *huge* set of methods that let you transform, combine, and short-circuit without writing explicit `match` blocks everywhere.

### map — Transform the success value

`map` applies a function to the `Ok` value and leaves `Err` untouched. You use it when you want to transform a success without caring about failure yet.

```rust
fn parse_port(s: &str) -> Result<u16, std::num::ParseIntError> {
    s.parse::<u16>()
}

fn main() {
    let doubled: Result<u32, _> = parse_port("4000").map(|p| p as u32 * 2);
    println!("{:?}", doubled); // Ok(8000)

    let bad: Result<u32, _> = parse_port("abc").map(|p| p as u32 * 2);
    println!("{:?}", bad); // Err(invalid digit found in string)
}
```

The function inside `map` never runs if the `Result` is `Err`. That's the whole point — you chain transformations on the happy path and errors pass through untouched.

### map_err — Transform the error value

The mirror of `map`. Changes the error type without touching success:

```rust
use std::fmt;
use std::num::ParseIntError;

#[derive(Debug)]
struct ConfigError(String);

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "config error: {}", self.0)
    }
}

fn parse_port(s: &str) -> Result<u16, ConfigError> {
    s.parse::<u16>()
        .map_err(|e: ParseIntError| ConfigError(format!("invalid port '{}': {}", s, e)))
}

fn main() {
    match parse_port("not_a_number") {
        Ok(p) => println!("Port: {}", p),
        Err(e) => eprintln!("{}", e),
        // prints: config error: invalid port 'not_a_number': invalid digit found in string
    }
}
```

You'll use `map_err` *constantly* when converting between error types at module boundaries.

### and_then — Chain fallible operations

`and_then` is for when your transformation *itself* can fail. It's like `map`, but the closure returns a `Result`:

```rust
use std::num::ParseIntError;

fn parse_and_validate_port(s: &str) -> Result<u16, String> {
    s.parse::<u16>()
        .map_err(|e: ParseIntError| format!("parse error: {}", e))
        .and_then(|port| {
            if port >= 1024 {
                Ok(port)
            } else {
                Err(format!("port {} is privileged, use >= 1024", port))
            }
        })
}

fn main() {
    println!("{:?}", parse_and_validate_port("8080"));  // Ok(8080)
    println!("{:?}", parse_and_validate_port("80"));    // Err("port 80 is privileged...")
    println!("{:?}", parse_and_validate_port("abc"));   // Err("parse error: ...")
}
```

If you used `map` here, you'd get `Result<Result<u16, String>, String>` — a nested `Result`. `and_then` flattens it.

### unwrap_or, unwrap_or_else, unwrap_or_default

When you want to extract the value with a fallback:

```rust
fn main() {
    let port: u16 = "8080".parse().unwrap_or(3000);
    println!("{}", port); // 8080

    let port: u16 = "bad".parse().unwrap_or(3000);
    println!("{}", port); // 3000

    // unwrap_or_else takes a closure — useful when the default is expensive
    let port: u16 = "bad".parse().unwrap_or_else(|_| {
        eprintln!("falling back to default port");
        3000
    });
    println!("{}", port); // 3000

    // unwrap_or_default uses the type's Default impl
    let count: i32 = "bad".parse().unwrap_or_default();
    println!("{}", count); // 0
}
```

### or and or_else — Fallback Results

`or` provides an alternative `Result` if the first one is `Err`:

```rust
use std::env;
use std::fs;

fn read_config() -> Result<String, String> {
    // Try env var first, then file
    env::var("APP_CONFIG")
        .map_err(|e| format!("env error: {}", e))
        .or_else(|_| {
            fs::read_to_string("config.toml")
                .map_err(|e| format!("file error: {}", e))
        })
}

fn main() {
    match read_config() {
        Ok(config) => println!("Config loaded: {} bytes", config.len()),
        Err(e) => eprintln!("No config found: {}", e),
    }
}
```

## Option: The Same Toolkit, Different Semantics

`Option<T>` has almost the exact same set of combinators as `Result`. The difference is semantic — `Option` represents absence, not failure.

### The essentials

```rust
fn main() {
    let numbers = vec![10, 20, 30];

    // map — transform if present
    let first_doubled: Option<i32> = numbers.first().map(|n| n * 2);
    println!("{:?}", first_doubled); // Some(20)

    // and_then — chain operations that might return None
    let result = numbers.first()
        .and_then(|n| if *n > 5 { Some(n * 2) } else { None });
    println!("{:?}", result); // Some(20)

    // unwrap_or
    let empty: Vec<i32> = vec![];
    let val = empty.first().copied().unwrap_or(0);
    println!("{}", val); // 0

    // filter — keep the value only if it passes a predicate
    let big: Option<&i32> = numbers.first().filter(|n| **n > 100);
    println!("{:?}", big); // None
}
```

### ok_or — Converting Option to Result

This is a bridge you'll cross all the time. You have an `Option` but need a `Result` because absence *is* an error in your context:

```rust
use std::collections::HashMap;

fn get_user_email(users: &HashMap<u64, String>, id: u64) -> Result<&String, String> {
    users.get(&id)
        .ok_or(format!("user {} not found", id))
}

fn main() {
    let mut users = HashMap::new();
    users.insert(1, String::from("atharva@example.com"));

    println!("{:?}", get_user_email(&users, 1));  // Ok("atharva@example.com")
    println!("{:?}", get_user_email(&users, 99)); // Err("user 99 not found")
}
```

`ok_or_else` takes a closure instead — use it when constructing the error is expensive.

### Going the other direction: Result to Option

Sometimes you don't care *why* something failed, just whether it succeeded:

```rust
fn main() {
    // .ok() drops the error
    let maybe_port: Option<u16> = "8080".parse::<u16>().ok();
    println!("{:?}", maybe_port); // Some(8080)

    // .err() drops the success
    let maybe_error = "bad".parse::<u16>().err();
    println!("{:?}", maybe_error); // Some(invalid digit...)
}
```

## Combining Multiple Results

Real code often needs to run several fallible operations and combine their results.

### The collect trick

You can collect an iterator of `Result`s into a `Result` of a collection. If any element is `Err`, the whole thing short-circuits:

```rust
fn parse_all_ports(inputs: &[&str]) -> Result<Vec<u16>, std::num::ParseIntError> {
    inputs.iter()
        .map(|s| s.parse::<u16>())
        .collect()
}

fn main() {
    println!("{:?}", parse_all_ports(&["80", "443", "8080"]));
    // Ok([80, 443, 8080])

    println!("{:?}", parse_all_ports(&["80", "abc", "8080"]));
    // Err(invalid digit found in string)
}
```

This is one of those things that feels like magic the first time you see it. The `FromIterator` impl for `Result` handles the short-circuiting.

### Zip-style with tuples

When you need multiple independent results and want all of them:

```rust
fn main() {
    let host: Result<String, String> = Ok(String::from("localhost"));
    let port: Result<u16, String> = Ok(8080);

    // Using and_then to combine
    let addr = host.and_then(|h| {
        port.map(|p| format!("{}:{}", h, p))
    });

    println!("{:?}", addr); // Ok("localhost:8080")
}
```

## Patterns You'll Actually Use

Here's a real-world-ish function that chains combinators together. This is what idiomatic Rust error handling looks like once you get comfortable:

```rust
use std::collections::HashMap;
use std::num::ParseIntError;

#[derive(Debug)]
struct AppConfig {
    host: String,
    port: u16,
    workers: usize,
}

fn load_config(env: &HashMap<String, String>) -> Result<AppConfig, String> {
    let host = env.get("HOST")
        .cloned()
        .unwrap_or_else(|| String::from("127.0.0.1"));

    let port = env.get("PORT")
        .ok_or("PORT not set")?
        .parse::<u16>()
        .map_err(|e: ParseIntError| format!("invalid PORT: {}", e))?;

    let workers = env.get("WORKERS")
        .map(|w| w.parse::<usize>())
        .transpose()
        .map_err(|e: ParseIntError| format!("invalid WORKERS: {}", e))?
        .unwrap_or(4);

    Ok(AppConfig { host, port, workers })
}

fn main() {
    let mut env = HashMap::new();
    env.insert("PORT".into(), "8080".into());
    env.insert("WORKERS".into(), "8".into());

    match load_config(&env) {
        Ok(config) => println!("{:#?}", config),
        Err(e) => eprintln!("Config error: {}", e),
    }
}
```

Notice the `transpose()` call — that converts `Option<Result<T, E>>` into `Result<Option<T>, E>`. Incredibly useful when you have an optional field that needs parsing.

## When to Use Which Combinator

A quick reference I wish I'd had when starting out:

- **I want to transform the success value** → `map`
- **I want to transform the error value** → `map_err`
- **My transformation can also fail** → `and_then`
- **I want a default on failure** → `unwrap_or` / `unwrap_or_else`
- **I want to try an alternative** → `or_else`
- **I want to convert Option to Result** → `ok_or` / `ok_or_else`
- **I want to convert Result to Option** → `.ok()` / `.err()`
- **I want to flip Option<Result> to Result<Option>** → `transpose`

Master these and you'll rarely need to write explicit `match` blocks. Your code will be shorter, more expressive, and the error handling won't obscure the business logic. That's the whole game.
