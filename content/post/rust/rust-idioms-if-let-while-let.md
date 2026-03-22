---
title: "Lesson 4: if let and while let — Concise pattern matching"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 4
keywords: [Rust, rust, idiomatic, if let, while let, pattern matching, destructuring]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-04-16T08:45:00.000Z'
---

There's a special kind of code smell that I call "match bloat" — when you write a six-line `match` statement to handle a single variant and throw an underscore wildcard on everything else. I wrote hundreds of these before discovering that Rust has a better way.

If you're writing `match` just to handle one case, you're doing too much work.

---

## The Problem: Match Bloat

Here's code I see all the time, especially from developers coming from the previous lesson on `Option` and `Result`:

```rust
fn print_config_value(config: &std::collections::HashMap<String, String>, key: &str) {
    let value = config.get(key);
    match value {
        Some(v) => println!("{}: {}", key, v),
        None => {} // do nothing
    }
}
```

Five lines to express: "if there's a value, print it." The `None => {}` arm is dead weight — it exists only because `match` forces exhaustiveness. And exhaustiveness is great! But not when one of the branches is literally "do nothing."

---

## The Idiomatic Way: `if let`

`if let` is syntactic sugar for a `match` with one interesting arm and a wildcard:

```rust
use std::collections::HashMap;

fn print_config_value(config: &HashMap<String, String>, key: &str) {
    if let Some(v) = config.get(key) {
        println!("{}: {}", key, v);
    }
}
```

Same behavior. Half the code. Zero dead-weight branches.

The general form is:

```rust
if let PATTERN = EXPRESSION {
    // runs if the pattern matches
}
```

And yes, you can add `else`:

```rust
fn get_greeting(name: Option<&str>) -> String {
    if let Some(n) = name {
        format!("Hello, {}!", n)
    } else {
        String::from("Hello, stranger!")
    }
}

fn main() {
    println!("{}", get_greeting(Some("Atharva")));
    println!("{}", get_greeting(None));
}
```

---

## When `if let` Shines

### Checking a single enum variant

```rust
enum Command {
    Quit,
    Echo(String),
    Move { x: i32, y: i32 },
    Color(u8, u8, u8),
}

fn handle(cmd: &Command) {
    // Only care about Echo? Use if let.
    if let Command::Echo(msg) = cmd {
        println!("Echo: {}", msg);
    }
}
```

### Conditional unwrapping with extra checks

```rust
fn process_positive(value: Option<i32>) {
    if let Some(n) = value {
        if n > 0 {
            println!("Processing positive: {}", n);
        }
    }
}
```

Or combined with Rust 1.64+ `let-else` (we'll get to that):

```rust
fn find_port(config: &str) -> Option<u16> {
    for line in config.lines() {
        if let Some(rest) = line.strip_prefix("port=") {
            if let Ok(port) = rest.trim().parse::<u16>() {
                return Some(port);
            }
        }
    }
    None
}

fn main() {
    let config = "host=localhost\nport=8080\nlog=debug";
    println!("Port: {:?}", find_port(config));
}
```

### Method chaining results

```rust
fn first_even(numbers: &[i32]) -> Option<i32> {
    numbers.iter().find(|&&n| n % 2 == 0).copied()
}

fn main() {
    let nums = vec![1, 3, 4, 7, 8];
    if let Some(n) = first_even(&nums) {
        println!("First even: {}", n);
    }
}
```

---

## `while let` — Looping on a Pattern

`while let` is the loop version of `if let`. It keeps going as long as the pattern matches.

The classic use case: draining an iterator or channel.

```rust
fn main() {
    let mut stack = vec![1, 2, 3, 4, 5];

    // Pop elements until the stack is empty
    while let Some(top) = stack.pop() {
        println!("Popped: {}", top);
    }
    // Prints 5, 4, 3, 2, 1

    println!("Stack is now empty: {:?}", stack);
}
```

Without `while let`, this would be:

```rust
fn main() {
    let mut stack = vec![1, 2, 3, 4, 5];

    loop {
        match stack.pop() {
            Some(top) => println!("Popped: {}", top),
            None => break,
        }
    }
}
```

The `while let` version is obviously better.

### Processing a stream of results

```rust
use std::io::{self, BufRead};

fn read_numbers() -> Vec<i32> {
    let mut numbers = Vec::new();
    let stdin = io::stdin();
    let mut lines = stdin.lock().lines();

    // Keep reading until we get a non-number
    while let Some(Ok(line)) = lines.next() {
        if let Ok(n) = line.trim().parse::<i32>() {
            numbers.push(n);
        } else {
            break;
        }
    }
    numbers
}
```

Notice the nested pattern: `Some(Ok(line))` matches both the `Option` from the iterator and the `Result` from the line reading. Patterns compose beautifully.

---

## `let-else` — The Early Return Pattern

Stabilized in Rust 1.65, `let-else` is the missing piece that I'd been wanting for years. It handles the case where you want to destructure *or bail out*:

```rust
fn parse_header(line: &str) -> Option<(&str, &str)> {
    let Some((key, value)) = line.split_once(':') else {
        return None;
    };
    Some((key.trim(), value.trim()))
}

fn main() {
    println!("{:?}", parse_header("Content-Type: text/html"));
    println!("{:?}", parse_header("malformed"));
}
```

The pattern: `let PATTERN = EXPRESSION else { DIVERGE };`

The `else` block must *diverge* — return, break, continue, or panic. This is perfect for guard clauses:

```rust
fn process_user(input: &str) -> Result<(), String> {
    let Some(name) = input.split(',').next() else {
        return Err("missing name field".to_string());
    };

    let Ok(age) = input.split(',').nth(1).unwrap_or("").parse::<u32>() else {
        return Err("invalid age field".to_string());
    };

    println!("User: {} (age {})", name.trim(), age);
    Ok(())
}

fn main() {
    let _ = process_user("Atharva, 28");
    let _ = process_user("bad data");
}
```

Before `let-else`, you'd either nest `if let` blocks (pyramid of doom) or use a `match` with an early return. `let-else` keeps the happy path unindented — which is exactly what you want.

---

## Combining `if let` with Other Conditions

Rust lets you chain `if let` with `&&` in conditions (since Rust 1.64):

```rust
fn process(value: Option<i32>, threshold: i32) {
    if let Some(v) = value && v > threshold {
        println!("Value {} exceeds threshold {}", v, threshold);
    }
}

fn main() {
    process(Some(10), 5);   // prints
    process(Some(3), 5);    // doesn't print
    process(None, 5);       // doesn't print
}
```

**Note:** This is a nightly feature as of early 2024 — `let_chains`. It may be stabilized by the time you read this. Check the [tracking issue](https://github.com/rust-lang/rust/issues/53667). Until then, nest your conditions:

```rust
fn process(value: Option<i32>, threshold: i32) {
    if let Some(v) = value {
        if v > threshold {
            println!("Value {} exceeds threshold {}", v, threshold);
        }
    }
}
```

---

## `if let` vs `match` — When to Use Which

My rule is simple:

- **One or two arms?** Use `if let` (with optional `else`).
- **Three or more arms?** Use `match`.
- **Exhaustive handling needed?** Use `match` — the compiler checks you covered everything.

```rust
enum Status {
    Active,
    Inactive,
    Suspended(String),
}

fn describe(status: &Status) {
    // Good: match handles all variants explicitly
    match status {
        Status::Active => println!("Active"),
        Status::Inactive => println!("Inactive"),
        Status::Suspended(reason) => println!("Suspended: {}", reason),
    }
}

fn check_suspension(status: &Status) {
    // Good: if let for one variant
    if let Status::Suspended(reason) = status {
        println!("WARNING: Account suspended — {}", reason);
    }
}
```

If someone adds a new variant to `Status`, the `match` will force you to handle it (compiler error). The `if let` won't — which is fine when you only care about one case, but dangerous when you should care about all of them.

---

## A Real-World Example

Here's a pattern I use constantly — parsing structured text with `if let` chains:

```rust
#[derive(Debug)]
struct LogEntry {
    timestamp: String,
    level: String,
    message: String,
}

fn parse_log_line(line: &str) -> Option<LogEntry> {
    // Format: "[2024-01-15 10:30:00] ERROR: Something went wrong"
    let line = line.trim();

    let Some(rest) = line.strip_prefix('[') else {
        return None;
    };

    let Some((timestamp, rest)) = rest.split_once(']') else {
        return None;
    };

    let rest = rest.trim();

    let Some((level, message)) = rest.split_once(':') else {
        return None;
    };

    Some(LogEntry {
        timestamp: timestamp.to_string(),
        level: level.trim().to_string(),
        message: message.trim().to_string(),
    })
}

fn main() {
    let lines = vec![
        "[2024-01-15 10:30:00] ERROR: Database connection failed",
        "[2024-01-15 10:30:01] INFO: Retrying connection",
        "malformed line",
        "[2024-01-15 10:30:02] WARN: Connection restored",
    ];

    for line in lines {
        if let Some(entry) = parse_log_line(line) {
            println!("{:?}", entry);
        }
    }
}
```

Each `let-else` acts as a guard — if the pattern doesn't match, we bail with `None`. The happy path reads top to bottom with no nesting. This is idiomatic Rust parsing.

---

## Key Takeaways

- `if let` replaces single-arm `match` statements — use it when you care about one variant.
- `while let` loops until a pattern stops matching — perfect for draining collections and streams.
- `let-else` keeps the happy path unindented by diverging on pattern mismatch.
- Use `match` when you need exhaustive handling. Use `if let` when you don't.
- Patterns compose: `Some(Ok(value))` matches nested types cleanly.
- Don't fight `if let` into doing too much — if you have three branches, `match` is clearer.
