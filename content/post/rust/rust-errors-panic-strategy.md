---
title: "Lesson 9: panic!, unwrap, expect — When crashing is correct"
author: Atharva Pandey
series: "Rust Error Handling at Scale"
lesson: 9
keywords: [Rust, rust, error handling, panic, unwrap, expect, catch_unwind, abort]
tags: [Rust tutorial, rust, error-handling]
date: '2024-07-24T07:55:00.000Z'
---

There's a pervasive myth in the Rust community that `unwrap()` is always bad. I've seen code reviews where people mechanically replace every `unwrap()` with a `match` or `.expect()`, even when the `unwrap()` was perfectly correct. The truth is more nuanced: `panic!` is a tool, and like any tool, the question isn't "should I ever use it?" but "when is it the right choice?"

## What panic! Actually Does

When you call `panic!()`, Rust does one of two things depending on your configuration:

**Unwinding (default):** The runtime walks up the stack, calling destructors (`Drop` impls) for every value, cleaning up resources. Then it terminates the thread. If the main thread panics, the whole process exits.

**Aborting:** The process immediately terminates. No destructors, no cleanup. Faster, smaller binaries.

```toml
# Cargo.toml — choose abort for release builds if you want smaller binaries
[profile.release]
panic = "abort"
```

```rust
fn main() {
    println!("Before panic");
    panic!("something went terribly wrong");
    // This line never executes
    // println!("After panic");
}
```

The key insight: **panic is for bugs, not for expected failures.** If a file might not exist, that's a `Result`. If an index should always be in bounds because you just checked it, a panic on out-of-bounds is correct — it means your logic is wrong.

## unwrap(): The "I'm Sure" Assertion

`unwrap()` calls `panic!` if the `Result` is `Err` or the `Option` is `None`:

```rust
fn main() {
    // This is fine — the literal "42" will always parse
    let n: i32 = "42".parse().unwrap();
    println!("{}", n);

    // This would panic — but the compiler can't stop you
    // let bad: i32 = "hello".parse().unwrap();
}
```

When is `unwrap()` acceptable?

**1. When failure is logically impossible:**

```rust
use std::net::Ipv4Addr;

fn main() {
    // This regex is a compile-time constant — if it doesn't parse, that's a bug
    let re = regex::Regex::new(r"^\d{4}-\d{2}-\d{2}$").unwrap();
    println!("Regex compiled: {}", re.is_match("2024-07-24"));

    // Hardcoded IP address — can't fail
    let localhost: Ipv4Addr = "127.0.0.1".parse().unwrap();
    println!("Localhost: {}", localhost);
}
```

**2. In tests:**

```rust
#[cfg(test)]
mod tests {
    #[test]
    fn test_parsing() {
        // unwrap in tests is fine — if it panics, the test fails with a clear message
        let result: i32 = "42".parse().unwrap();
        assert_eq!(result, 42);
    }
}

fn main() {}
```

**3. In prototype/example code:**

When you're sketching out an idea and error handling would obscure the point, `unwrap()` is fine. Just don't ship it.

## expect(): unwrap with a Message

`expect()` is `unwrap()` with a panic message. Always prefer `expect()` over `unwrap()` in code that might actually run in production:

```rust
use std::env;

fn main() {
    // Bad — panic message is "called `Result::unwrap()` on an `Err` value: NotPresent"
    // That tells you nothing about what you were trying to do
    // let url = env::var("DATABASE_URL").unwrap();

    // Good — panic message is "DATABASE_URL must be set: NotPresent"
    let url = env::var("DATABASE_URL")
        .expect("DATABASE_URL must be set");

    println!("Connecting to: {}", url);
}
```

The convention for `expect()` messages: describe *what should be true*, not *what went wrong*. Think of it as a failed assertion:

```rust
fn main() {
    let items = vec![1, 2, 3];

    // Bad: "failed to get first element"
    // Good: describes the invariant that should hold
    let first = items.first().expect("items list should never be empty");
    println!("First: {}", first);
}
```

## When panic! Is the Right Call

### Unrecoverable invariant violations

If your code reaches a state that should be impossible according to your logic, panic. Continuing with corrupt state is worse than crashing:

```rust
enum Direction {
    North,
    South,
    East,
    West,
}

fn opposite(dir: &Direction) -> Direction {
    match dir {
        Direction::North => Direction::South,
        Direction::South => Direction::North,
        Direction::East => Direction::West,
        Direction::West => Direction::East,
    }
}

fn move_player(x: &mut i32, y: &mut i32, dir: &Direction) {
    match dir {
        Direction::North => *y += 1,
        Direction::South => *y -= 1,
        Direction::East => *x += 1,
        Direction::West => *x -= 1,
    }
}

fn main() {
    let dir = Direction::North;
    let opp = opposite(&dir);
    let mut x = 0;
    let mut y = 0;
    move_player(&mut x, &mut y, &opp);
    println!("Position: ({}, {})", x, y);
}
```

### Startup configuration

If your application can't function without certain configuration, panicking at startup is better than limping along:

```rust
use std::env;
use std::collections::HashMap;

struct Config {
    database_url: String,
    port: u16,
    secret_key: String,
}

impl Config {
    fn from_env() -> Config {
        Config {
            database_url: env::var("DATABASE_URL")
                .expect("DATABASE_URL is required"),
            port: env::var("PORT")
                .unwrap_or_else(|_| String::from("8080"))
                .parse()
                .expect("PORT must be a valid u16"),
            secret_key: env::var("SECRET_KEY")
                .expect("SECRET_KEY is required"),
        }
    }
}

fn main() {
    // In a real app, you'd use this at startup.
    // If required env vars are missing, crash immediately
    // with a clear message — don't wait until the first request.
    println!("Config module loaded");

    // Demonstrate with a HashMap-based approach instead
    let mut env_map = HashMap::new();
    env_map.insert("DATABASE_URL", "postgres://localhost/db");
    env_map.insert("PORT", "3000");
    env_map.insert("SECRET_KEY", "supersecret");

    let db = env_map.get("DATABASE_URL").expect("DATABASE_URL required");
    let port: u16 = env_map.get("PORT").expect("PORT required")
        .parse().expect("PORT must be a number");
    println!("{}:{}", db, port);
}
```

### Programmer errors, not user errors

```rust
fn process_chunk(data: &[u8], chunk_size: usize) -> Vec<Vec<u8>> {
    // chunk_size of 0 is a programmer error, not a runtime condition
    assert!(chunk_size > 0, "chunk_size must be positive, got 0");

    data.chunks(chunk_size)
        .map(|c| c.to_vec())
        .collect()
}

fn main() {
    let data = vec![1, 2, 3, 4, 5, 6, 7, 8];
    let chunks = process_chunk(&data, 3);
    for (i, chunk) in chunks.iter().enumerate() {
        println!("Chunk {}: {:?}", i, chunk);
    }
}
```

## When NOT to Panic

### User input

Never panic on user input. Users *will* send garbage. Handle it:

```rust
fn parse_age(input: &str) -> Result<u8, String> {
    let age: u8 = input.parse()
        .map_err(|_| format!("'{}' is not a valid age", input))?;

    if age > 150 {
        return Err(format!("age {} is unrealistic", age));
    }

    Ok(age)
}

fn main() {
    for input in &["25", "abc", "200", "0"] {
        match parse_age(input) {
            Ok(age) => println!("Age: {}", age),
            Err(e) => println!("Error: {}", e),
        }
    }
}
```

### Network/IO operations

Files go missing. Connections drop. Disks fill up. These are expected failures:

```rust
use std::io;
use std::fs;

fn read_config(path: &str) -> Result<String, io::Error> {
    // Don't: fs::read_to_string(path).unwrap()
    // Do:
    fs::read_to_string(path)
}

fn main() {
    match read_config("config.toml") {
        Ok(c) => println!("Config: {} bytes", c.len()),
        Err(e) => eprintln!("Could not read config: {}", e),
    }
}
```

### In library code

Libraries should almost never panic. Let the application decide how to handle failures:

```rust
// Bad library function
// pub fn parse(input: &str) -> Config {
//     serde_json::from_str(input).unwrap() // Panics on bad input!
// }

use std::fmt;

#[derive(Debug)]
struct ParseError(String);

impl fmt::Display for ParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "parse error: {}", self.0)
    }
}

impl std::error::Error for ParseError {}

// Good library function
fn parse(input: &str) -> Result<Vec<(String, String)>, ParseError> {
    let mut result = Vec::new();
    for line in input.lines() {
        let parts: Vec<&str> = line.splitn(2, '=').collect();
        if parts.len() != 2 {
            return Err(ParseError(format!("invalid line: '{}'", line)));
        }
        result.push((parts[0].to_string(), parts[1].to_string()));
    }
    Ok(result)
}

fn main() {
    match parse("key=value\nbad") {
        Ok(entries) => println!("{:?}", entries),
        Err(e) => eprintln!("{}", e),
    }
}
```

## catch_unwind: Panic Recovery

Sometimes you need to catch panics — typically at thread boundaries or FFI boundaries. `std::panic::catch_unwind` does this:

```rust
use std::panic;

fn risky_computation(input: i32) -> i32 {
    if input == 0 {
        panic!("division by zero in risky_computation");
    }
    100 / input
}

fn main() {
    let result = panic::catch_unwind(|| {
        risky_computation(0)
    });

    match result {
        Ok(value) => println!("Result: {}", value),
        Err(_) => println!("Computation panicked, using default"),
    }

    // The program continues running
    println!("Still alive!");

    // Normal case works fine
    let result = panic::catch_unwind(|| risky_computation(5));
    println!("Normal result: {:?}", result); // Ok(20)
}
```

`catch_unwind` only works with the unwinding panic strategy. If you've set `panic = "abort"` in `Cargo.toml`, panics will kill the process regardless.

Use cases for `catch_unwind`:
- Thread pool workers that shouldn't take down the pool when they panic
- FFI boundaries — don't let panics unwind through C code
- Test harnesses
- Plugin systems where untrusted code might panic

Don't use it as a general error handling mechanism. It's not `try/catch`.

## The Decision Framework

```text
Is it a programmer error / violated invariant?
  YES → panic! / assert! / unreachable!
  NO ↓

Is it expected at runtime (IO, user input, network)?
  YES → Result<T, E>
  NO ↓

Is the value logically guaranteed to exist?
  YES → unwrap() or expect() with a good message
  NO → Result or Option
```

## Configuring Panic Behavior

```toml
# Cargo.toml

[profile.dev]
# Default: unwind — good for debugging, runs destructors
panic = "unwind"

[profile.release]
# Option: abort — faster, smaller binaries, no catch_unwind
# Use this for services where you'd rather restart fast than try to recover
panic = "abort"
```

For server applications, `panic = "abort"` in release builds is often the right call. If something panics, you want the process to die immediately so your supervisor (systemd, Kubernetes, etc.) can restart it cleanly. Unwinding through half-constructed state is rarely better than a clean restart.

One last thing — `unwrap()` on `Option` and `Result` is greppable. You can search your codebase for `unwrap()` calls and audit them before a release. Some teams add a clippy lint to warn on `unwrap()` outside of tests. That's a reasonable policy as long as it doesn't become dogma. Sometimes `unwrap()` is the clearest, most correct thing to write.
