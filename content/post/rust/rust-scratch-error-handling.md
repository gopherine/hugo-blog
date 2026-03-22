---
title: "Lesson 16: Result and the ? Operator — Errors as values"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 16
keywords: [Rust, rust, Result, error handling, question mark operator, unwrap, expect, custom errors]
tags: [Rust tutorial, rust, beginner]
date: '2024-04-01T08:45:00.000Z'
---

Exception-based error handling has a fundamental flaw: you can't tell by looking at a function signature whether it might throw. Go fixed this by returning `(value, error)` tuples, but then you're back to forgetting to check the error. Rust's `Result` type gets it right — errors are values, and the type system makes them impossible to ignore.

## The Result Type

```rust
// Built into the language:
// enum Result<T, E> {
//     Ok(T),
//     Err(E),
// }
```

`Result<T, E>` is either `Ok(T)` (success with a value) or `Err(E)` (failure with an error). You've seen `Option<T>` — `Result` is similar but carries error information when something goes wrong.

```rust
fn divide(a: f64, b: f64) -> Result<f64, String> {
    if b == 0.0 {
        Err(String::from("division by zero"))
    } else {
        Ok(a / b)
    }
}

fn main() {
    match divide(10.0, 3.0) {
        Ok(result) => println!("10 / 3 = {result:.4}"),
        Err(e) => println!("Error: {e}"),
    }

    match divide(10.0, 0.0) {
        Ok(result) => println!("10 / 0 = {result}"),
        Err(e) => println!("Error: {e}"),
    }
}
```

## The ? Operator — Error Propagation Made Beautiful

Here's a function that reads a number from a file without `?`:

```rust
use std::fs;

fn read_number_verbose(path: &str) -> Result<i32, String> {
    let content = match fs::read_to_string(path) {
        Ok(c) => c,
        Err(e) => return Err(format!("Failed to read file: {e}")),
    };

    let trimmed = content.trim();

    match trimmed.parse::<i32>() {
        Ok(n) => Ok(n),
        Err(e) => Err(format!("Failed to parse number: {e}")),
    }
}

fn main() {
    match read_number_verbose("number.txt") {
        Ok(n) => println!("Number: {n}"),
        Err(e) => println!("Error: {e}"),
    }
}
```

That's verbose. Now with `?`:

```rust
use std::fs;
use std::num::ParseIntError;

#[derive(Debug)]
enum ReadError {
    Io(std::io::Error),
    Parse(ParseIntError),
}

impl From<std::io::Error> for ReadError {
    fn from(e: std::io::Error) -> Self {
        ReadError::Io(e)
    }
}

impl From<ParseIntError> for ReadError {
    fn from(e: ParseIntError) -> Self {
        ReadError::Parse(e)
    }
}

fn read_number(path: &str) -> Result<i32, ReadError> {
    let content = fs::read_to_string(path)?;  // ? propagates the error
    let number = content.trim().parse::<i32>()?;
    Ok(number)
}

fn main() {
    match read_number("number.txt") {
        Ok(n) => println!("Number: {n}"),
        Err(e) => println!("Error: {e:?}"),
    }
}
```

The `?` operator does three things:
1. If the Result is `Ok`, unwrap the value and continue
2. If the Result is `Err`, convert the error using `From` and return early
3. This conversion is what makes `?` work across different error types

This is where Rust error handling gets elegant. `?` replaces pages of match statements with a single character. The error path is still explicit — the function signature tells you it can fail — but the happy path reads almost like pseudocode.

## Common Ways to Handle Results

### unwrap and expect

```rust
fn main() {
    // unwrap — panic on error
    let n: i32 = "42".parse().unwrap();
    println!("{n}");

    // expect — panic with a custom message
    let n: i32 = "42".parse().expect("should be a valid number");
    println!("{n}");

    // Both are fine in:
    // - Examples and prototypes
    // - Tests
    // - Cases where you've already validated the input
    // Never use them in library code or production error paths
}
```

### unwrap_or, unwrap_or_else, unwrap_or_default

```rust
fn main() {
    let port: u16 = "invalid".parse().unwrap_or(8080);
    println!("Port: {port}");  // 8080

    let port: u16 = "invalid".parse().unwrap_or_else(|e| {
        eprintln!("Parse error: {e}, using default");
        8080
    });
    println!("Port: {port}");

    let count: i32 = "invalid".parse().unwrap_or_default();
    println!("Count: {count}");  // 0 (i32's default)
}
```

### map and and_then

```rust
fn main() {
    // map — transform the Ok value
    let result: Result<i32, _> = "42".parse::<i32>().map(|n| n * 2);
    println!("{:?}", result);  // Ok(84)

    // and_then — chain fallible operations
    let result = "42"
        .parse::<i32>()
        .and_then(|n| {
            if n > 0 {
                Ok(n)
            } else {
                Err("must be positive".parse::<i32>().unwrap_err())
            }
        });
    println!("{:?}", result);
}
```

### map_err — Transform the Error

```rust
fn parse_port(s: &str) -> Result<u16, String> {
    s.parse::<u16>().map_err(|e| format!("Invalid port '{}': {}", s, e))
}

fn main() {
    println!("{:?}", parse_port("8080"));   // Ok(8080)
    println!("{:?}", parse_port("abc"));    // Err("Invalid port 'abc': ...")
    println!("{:?}", parse_port("99999"));  // Err("Invalid port '99999': ...")
}
```

## Custom Error Types

For real projects, you'll define your own error types:

```rust
use std::fmt;
use std::num::ParseIntError;

#[derive(Debug)]
enum AppError {
    NotFound(String),
    ParseFailure(ParseIntError),
    InvalidInput(String),
}

impl fmt::Display for AppError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            AppError::NotFound(name) => write!(f, "not found: {name}"),
            AppError::ParseFailure(e) => write!(f, "parse error: {e}"),
            AppError::InvalidInput(msg) => write!(f, "invalid input: {msg}"),
        }
    }
}

impl From<ParseIntError> for AppError {
    fn from(e: ParseIntError) -> Self {
        AppError::ParseFailure(e)
    }
}

fn find_user(id: &str) -> Result<String, AppError> {
    let id_num: i32 = id.parse()?;  // uses From<ParseIntError>

    if id_num <= 0 {
        return Err(AppError::InvalidInput(
            format!("user ID must be positive, got {id_num}")
        ));
    }

    match id_num {
        1 => Ok(String::from("Alice")),
        2 => Ok(String::from("Bob")),
        _ => Err(AppError::NotFound(format!("user #{id_num}"))),
    }
}

fn main() {
    for id in &["1", "2", "3", "-1", "abc"] {
        match find_user(id) {
            Ok(name) => println!("Found: {name}"),
            Err(e) => println!("Error for '{id}': {e}"),
        }
    }
}
```

## The panic! Escape Hatch

`panic!` crashes the program immediately. It's for unrecoverable errors — bugs, violated invariants, situations where continuing would be worse than stopping.

```rust
fn main() {
    // Explicit panic
    // panic!("something went terribly wrong");

    // Implicit panics
    let v = vec![1, 2, 3];
    // let x = v[99];  // index out of bounds — panic

    // Use panic for programming errors, not for expected failures
    // "File not found" → Result
    // "Index out of bounds on a vector I just created" → panic (it's a bug)
}
```

Rule of thumb: if the error is something the *user* or *environment* caused (file not found, network timeout, invalid input), use `Result`. If the error is something the *programmer* caused (impossible state, broken invariant), `panic!` is appropriate.

## Chaining Fallible Operations

A realistic example — parsing a config string:

```rust
#[derive(Debug)]
struct ServerConfig {
    host: String,
    port: u16,
    workers: usize,
}

fn parse_config(input: &str) -> Result<ServerConfig, String> {
    let mut host = None;
    let mut port = None;
    let mut workers = None;

    for line in input.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }

        let (key, value) = line.split_once('=')
            .ok_or_else(|| format!("Invalid line: '{line}'"))?;

        let key = key.trim();
        let value = value.trim();

        match key {
            "host" => host = Some(value.to_string()),
            "port" => {
                port = Some(value.parse::<u16>()
                    .map_err(|e| format!("Invalid port '{value}': {e}"))?);
            }
            "workers" => {
                workers = Some(value.parse::<usize>()
                    .map_err(|e| format!("Invalid workers '{value}': {e}"))?);
            }
            _ => return Err(format!("Unknown key: '{key}'")),
        }
    }

    Ok(ServerConfig {
        host: host.ok_or("Missing 'host' field")?,
        port: port.ok_or("Missing 'port' field")?,
        workers: workers.ok_or("Missing 'workers' field")?,
    })
}

fn main() {
    let config_str = r#"
        # Server config
        host = 0.0.0.0
        port = 8080
        workers = 4
    "#;

    match parse_config(config_str) {
        Ok(config) => println!("Config: {:#?}", config),
        Err(e) => println!("Config error: {e}"),
    }

    let bad_config = "host = localhost\nport = abc";
    match parse_config(bad_config) {
        Ok(config) => println!("Config: {:#?}", config),
        Err(e) => println!("Config error: {e}"),
    }
}
```

Notice `.ok_or()` and `.ok_or_else()` — they convert `Option<T>` to `Result<T, E>`. This is how you bridge between "missing value" (Option) and "error" (Result).

## Result in main()

You can make `main()` return a Result:

```rust
use std::fs;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let content = fs::read_to_string("example.txt")?;
    let number: i32 = content.trim().parse()?;
    println!("Number: {number}");
    Ok(())
}
```

`Box<dyn std::error::Error>` is a trait object that can hold any error type. It's convenient for quick programs and examples. For real applications, use a concrete error type.

## My Error Handling Philosophy

1. **Use `Result` for expected failures.** File I/O, network, parsing, validation — anything that can fail in normal operation.
2. **Use `panic!` for programming errors.** Broken invariants, impossible states. If it happens, the code has a bug.
3. **Never use `unwrap()` in library code.** Libraries should return Results and let the caller decide.
4. **`unwrap()` is fine in tests.** A test that panics fails the test. That's the desired behavior.
5. **Use `?` aggressively.** It makes the happy path readable while still handling errors correctly.
6. **Define custom error types for libraries.** String errors are fine for applications, but libraries should have structured error types.

Next: modules — organizing your code into logical units.
