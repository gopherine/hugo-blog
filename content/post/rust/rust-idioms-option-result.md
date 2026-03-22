---
title: "Lesson 3: Option and Result Are Your Control Flow — Stop using sentinel values"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 3
keywords: [Rust, rust, idiomatic, Option, Result, error handling, control flow]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-04-14T11:22:00.000Z'
---

I once inherited a C codebase where `-1` meant "not found," `0` meant "error," and `NULL` meant... well, it depended on the function. Sometimes it meant "empty," sometimes "uninitialized," sometimes "we ran out of memory." The codebase had roughly 40 unique sentinel values across different modules, and half the bugs were someone forgetting which magic number meant what.

Rust looked at that mess and said: "How about we just... don't."

---

## The Problem With Sentinel Values

Every language has dealt with the "absence of a value" problem. C uses `NULL` pointers and magic numbers. Java has `null` (and `NullPointerException` — the billion-dollar mistake). Go uses the zero value plus an error return. Python uses `None`.

They all share the same fundamental issue: **nothing in the type system forces you to handle the absent case**.

```c
// C: hope you remembered to check for NULL
char* user = find_user(db, "atharva");
printf("Name: %s\n", user); // segfault if NULL
```

```java
// Java: hope you remembered to check for null
String user = findUser("atharva");
System.out.println(user.length()); // NullPointerException
```

Rust's answer is `Option<T>` and `Result<T, E>`. They're enums — algebraic data types — and they force you to handle both cases before you can access the inner value.

---

## Option — A Value That Might Not Exist

`Option<T>` has exactly two variants:

```rust
enum Option<T> {
    Some(T),
    None,
}
```

That's it. A value is either there (`Some(value)`) or it isn't (`None`). And because it's a different type from `T`, you can't accidentally use `None` as if it were a real value.

```rust
fn find_user(name: &str) -> Option<String> {
    let users = vec!["atharva", "bob", "charlie"];
    if users.contains(&name) {
        Some(format!("User: {}", name))
    } else {
        None
    }
}

fn main() {
    let user = find_user("atharva");

    // You can't just use `user` as a String — it's an Option<String>
    // println!("{}", user); // ERROR: Option<String> doesn't implement Display

    // You MUST handle both cases
    match user {
        Some(name) => println!("Found: {}", name),
        None => println!("User not found"),
    }
}
```

The compiler won't let you forget. That's not a restriction — that's a *guarantee*.

---

## Result — An Operation That Might Fail

`Result<T, E>` is `Option`'s sibling for operations that can fail with an error:

```rust
enum Result<T, E> {
    Ok(T),
    Err(E),
}
```

Success gives you `Ok(value)`. Failure gives you `Err(error)`. Again, the type system forces you to deal with both.

```rust
use std::num::ParseIntError;

fn parse_age(input: &str) -> Result<u32, ParseIntError> {
    input.parse::<u32>()
}

fn main() {
    match parse_age("25") {
        Ok(age) => println!("Age: {}", age),
        Err(e) => println!("Invalid age: {}", e),
    }

    match parse_age("not_a_number") {
        Ok(age) => println!("Age: {}", age),
        Err(e) => println!("Invalid age: {}", e),
    }
}
```

---

## The Wrong Way: `.unwrap()` Everywhere

The most common anti-pattern I see from Rust beginners:

```rust
fn main() {
    let config = std::fs::read_to_string("config.toml").unwrap();
    let port: u16 = config.lines()
        .find(|l| l.starts_with("port"))
        .unwrap()
        .split('=')
        .nth(1)
        .unwrap()
        .trim()
        .parse()
        .unwrap();

    println!("Port: {}", port);
}
```

Four `.unwrap()` calls. Four places where your program will panic with a useless error message. This is the Rust equivalent of not checking for `NULL` — you've defeated the entire purpose of the type system.

**`.unwrap()` is for prototypes and tests. That's it.** In production code, handle the error or propagate it.

---

## The Idiomatic Way: The `?` Operator

Rust's `?` operator is beautiful. It propagates errors up the call stack without boilerplate:

```rust
use std::fs;
use std::io;

fn read_port_from_config() -> Result<u16, Box<dyn std::error::Error>> {
    let config = fs::read_to_string("config.toml")?;
    let line = config.lines()
        .find(|l| l.starts_with("port"))
        .ok_or("no port line found")?;
    let port: u16 = line.split('=')
        .nth(1)
        .ok_or("malformed port line")?
        .trim()
        .parse()?;
    Ok(port)
}

fn main() {
    match read_port_from_config() {
        Ok(port) => println!("Port: {}", port),
        Err(e) => eprintln!("Failed to read config: {}", e),
    }
}
```

Each `?` says: "If this is `Err`, return it from the function. If it's `Ok`, unwrap the value and continue." Clean, readable, and no hidden panics.

Notice `ok_or()` — it converts `Option<T>` to `Result<T, E>`, letting you use `?` with both types.

---

## Combinators: The Functional Toolkit

`Option` and `Result` come with a rich set of combinator methods. These are your primary tools for working with optional and fallible values.

### `map` — Transform the inner value

```rust
fn first_word_length(text: &str) -> Option<usize> {
    text.split_whitespace()
        .next()           // Option<&str>
        .map(|w| w.len()) // Option<usize>
}

fn main() {
    assert_eq!(first_word_length("hello world"), Some(5));
    assert_eq!(first_word_length(""), None);
}
```

### `and_then` — Chain operations that might fail

```rust
fn parse_even(s: &str) -> Option<i32> {
    s.parse::<i32>()
        .ok()                                    // Result → Option
        .and_then(|n| if n % 2 == 0 { Some(n) } else { None })
}

fn main() {
    assert_eq!(parse_even("4"), Some(4));
    assert_eq!(parse_even("3"), None);
    assert_eq!(parse_even("abc"), None);
}
```

### `unwrap_or` and `unwrap_or_else` — Provide defaults

```rust
fn get_username() -> Option<String> {
    std::env::var("USER").ok()
}

fn main() {
    let name = get_username().unwrap_or_else(|| String::from("anonymous"));
    println!("Hello, {}", name);

    // For cheap defaults, unwrap_or is fine:
    let count: i32 = "not_a_number".parse().unwrap_or(0);
    println!("Count: {}", count);
}
```

Use `unwrap_or` when the default is cheap. Use `unwrap_or_else` when the default requires computation — it takes a closure that's only called if the value is `None`/`Err`.

### `map_err` — Transform the error type

```rust
use std::num::ParseIntError;

#[derive(Debug)]
enum AppError {
    InvalidInput(String),
}

fn parse_port(s: &str) -> Result<u16, AppError> {
    s.parse::<u16>()
        .map_err(|e: ParseIntError| AppError::InvalidInput(e.to_string()))
}
```

---

## Option as an Iterator

Here's a trick that blew my mind: `Option` implements `IntoIterator`. `Some(x)` yields one element. `None` yields zero.

```rust
fn main() {
    let maybe_name: Option<&str> = Some("Atharva");
    let no_name: Option<&str> = None;

    let mut names = vec!["Bob", "Charlie"];
    names.extend(maybe_name); // Adds "Atharva"
    names.extend(no_name);    // Adds nothing
    println!("{:?}", names);  // ["Bob", "Charlie", "Atharva"]
}
```

This is incredibly useful when building collections with optional elements:

```rust
fn build_query(base: &str, limit: Option<u32>, offset: Option<u32>) -> String {
    let mut parts = vec![base.to_string()];
    parts.extend(limit.map(|l| format!("LIMIT {}", l)));
    parts.extend(offset.map(|o| format!("OFFSET {}", o)));
    parts.join(" ")
}

fn main() {
    let q = build_query("SELECT * FROM users", Some(10), None);
    println!("{}", q); // "SELECT * FROM users LIMIT 10"
}
```

---

## When to Use `expect` vs `unwrap`

If you *must* unwrap (because you genuinely know the value can't be `None`/`Err`), use `expect` with a message explaining *why*:

```rust
fn main() {
    let home = std::env::var("HOME")
        .expect("HOME environment variable must be set");

    // Better than: std::env::var("HOME").unwrap()
    // Because when it panics, you get a useful message
    println!("Home: {}", home);
}
```

But honestly? I'd still prefer to handle it properly:

```rust
fn main() {
    let home = match std::env::var("HOME") {
        Ok(h) => h,
        Err(_) => {
            eprintln!("Error: HOME environment variable not set");
            std::process::exit(1);
        }
    };
    println!("Home: {}", home);
}
```

---

## The Pattern I Use Most

In real application code, my functions almost always return `Result`. The main function handles errors at the top level:

```rust
use std::io;
use std::fs;

#[derive(Debug)]
struct Config {
    port: u16,
    host: String,
}

fn load_config(path: &str) -> Result<Config, Box<dyn std::error::Error>> {
    let content = fs::read_to_string(path)?;
    let mut port = 8080u16;
    let mut host = String::from("localhost");

    for line in content.lines() {
        let parts: Vec<&str> = line.splitn(2, '=').collect();
        if parts.len() != 2 { continue; }
        match parts[0].trim() {
            "port" => port = parts[1].trim().parse()?,
            "host" => host = parts[1].trim().to_string(),
            _ => {}
        }
    }

    Ok(Config { port, host })
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let config = load_config("config.toml")?;
    println!("Starting server on {}:{}", config.host, config.port);
    Ok(())
}

fn main() {
    if let Err(e) = run() {
        eprintln!("Error: {}", e);
        std::process::exit(1);
    }
}
```

This pattern — `run()` returns `Result`, `main()` handles the error — keeps error handling clean throughout the entire program.

---

## Key Takeaways

- `Option<T>` replaces null. `Result<T, E>` replaces exceptions. Both force you to handle the empty/error case.
- Use `?` to propagate errors up the call stack. It's Rust's replacement for try/catch.
- Use combinators (`map`, `and_then`, `unwrap_or`) instead of nested `match` statements.
- `.unwrap()` is for prototypes and tests. Use `.expect("reason")` if you must, but prefer proper error handling.
- `Option` is an iterator — use it with `extend`, `chain`, and `flat_map`.
