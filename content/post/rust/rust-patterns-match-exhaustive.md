---
title: "Lesson 1: match — Exhaustive by design"
author: Atharva Pandey
series: "Rust Pattern Matching & Enums Deep Dive"
lesson: 1
keywords: [Rust, rust, pattern matching, enums, match expression, exhaustive matching, match arms]
tags: [Rust tutorial, rust, pattern-matching]
date: '2024-07-20T11:23:00.000Z'
---

I shipped a Python service once that had a `match` statement handling four message types. Three months later, the team added a fifth type. Nobody updated the match. The default branch silently swallowed the new messages, and we lost two days of analytics data before anyone noticed.

Rust's `match` wouldn't have let that happen.

## The Problem With Non-Exhaustive Matching

Most languages treat pattern matching (or switch/case) as a convenience. You list the cases you care about, slap a default at the bottom, and move on. The problem is that code evolves. New variants get added, old assumptions break, and that default case quietly covers up bugs.

This isn't hypothetical — it's the most common source of silent failures in any codebase I've worked on. A developer adds an enum variant in one file and has no way of knowing that seventeen other files need updating. Code review might catch it. Might not.

```rust
// C-style thinking in Rust — this won't compile
enum Direction {
    North,
    South,
    East,
    West,
}

fn describe(d: Direction) -> &'static str {
    match d {
        Direction::North => "heading up",
        Direction::South => "heading down",
        // Compiler error: non-exhaustive patterns
        // `East` and `West` not covered
    }
}
```

The compiler rejects this outright. Not a warning. Not a lint. A hard error.

## The Idiomatic Way

Rust's `match` is an *expression*, not a statement. It returns a value, and the compiler guarantees you've handled every possible case. This is exhaustiveness checking, and it's one of Rust's most underappreciated features.

```rust
enum TrafficLight {
    Red,
    Yellow,
    Green,
}

fn action(light: TrafficLight) -> &'static str {
    match light {
        TrafficLight::Red => "stop",
        TrafficLight::Yellow => "slow down",
        TrafficLight::Green => "go",
    }
}

fn main() {
    let light = TrafficLight::Green;
    println!("{}", action(light));
}
```

Every arm produces a value. Every variant is covered. The return types all agree. If you add `TrafficLight::FlashingRed` next month, the compiler will flag every `match` that doesn't handle it. That's not a nice-to-have — that's a guarantee that refactoring won't silently break things.

## Match Is an Expression

This trips up people coming from C-family languages. In Rust, `match` produces a value, which means you can bind it directly:

```rust
enum Coin {
    Penny,
    Nickel,
    Dime,
    Quarter,
}

fn value_in_cents(coin: Coin) -> u32 {
    match coin {
        Coin::Penny => 1,
        Coin::Nickel => 5,
        Coin::Dime => 10,
        Coin::Quarter => 25,
    }
}

fn main() {
    let coin = Coin::Quarter;
    let cents = value_in_cents(coin);
    println!("Value: {} cents", cents);

    // You can also use match inline
    let description = match coin {
        Coin::Penny => "a penny",
        Coin::Nickel => "a nickel",
        Coin::Dime => "a dime",
        Coin::Quarter => "a quarter",
    };
    println!("That's {}", description);
}
```

Because `match` is an expression, all arms must return the same type. The compiler enforces this. You can't accidentally return a `String` from one arm and an `i32` from another — that's a compile error.

## The Wildcard and When to Use It

Sometimes you genuinely don't care about every variant. Rust gives you the `_` wildcard for this:

```rust
enum HttpStatus {
    Ok,
    NotFound,
    InternalServerError,
    BadRequest,
    Unauthorized,
    Forbidden,
    ServiceUnavailable,
}

fn is_error(status: HttpStatus) -> bool {
    match status {
        HttpStatus::Ok => false,
        _ => true,
    }
}

fn main() {
    let status = HttpStatus::NotFound;
    println!("Is error: {}", is_error(status));
}
```

This compiles fine — `_` matches everything. But here's my strong opinion: **use wildcards sparingly.** Every time you write `_`, you're opting out of exhaustiveness checking. If someone adds `HttpStatus::Redirect` later, the wildcard silently catches it. Maybe that's what you want. Maybe it isn't.

I have a personal rule: if an enum has fewer than ten variants, spell them all out. The explicitness is worth the extra lines.

## Matching on Integers and Other Types

`match` isn't limited to enums. You can match on integers, characters, booleans — anything that implements the right traits:

```rust
fn classify_age(age: u32) -> &'static str {
    match age {
        0 => "newborn",
        1..=12 => "child",
        13..=17 => "teenager",
        18..=64 => "adult",
        65..=u32::MAX => "senior",
    }
}

fn grade(score: u8) -> char {
    match score {
        90..=100 => 'A',
        80..=89 => 'B',
        70..=79 => 'C',
        60..=69 => 'D',
        0..=59 => 'F',
        _ => panic!("score out of range"),
    }
}

fn main() {
    println!("{}", classify_age(25));
    println!("{}", grade(87));
}
```

Notice the `..=` syntax for inclusive ranges. The compiler checks these for exhaustiveness too — if your ranges don't cover every possible `u32` value, it'll tell you.

## Multi-Arm Blocks

When an arm needs more than a single expression, use a block:

```rust
enum Command {
    Quit,
    Echo(String),
    Move { x: i32, y: i32 },
    Color(u8, u8, u8),
}

fn execute(cmd: Command) {
    match cmd {
        Command::Quit => {
            println!("Shutting down...");
            std::process::exit(0);
        }
        Command::Echo(msg) => {
            println!("ECHO: {}", msg);
        }
        Command::Move { x, y } => {
            println!("Moving to ({}, {})", x, y);
        }
        Command::Color(r, g, b) => {
            println!("Setting color to #{:02x}{:02x}{:02x}", r, g, b);
        }
    }
}

fn main() {
    execute(Command::Echo("hello".to_string()));
    execute(Command::Move { x: 10, y: 20 });
    execute(Command::Color(255, 128, 0));
}
```

The block returns the value of its last expression, just like any other block in Rust. If you're using `match` as a statement (not binding the result), the arms can return `()`.

## A Real-World Example: Parsing Configuration

Here's something closer to production code — parsing config values with proper error handling:

```rust
#[derive(Debug)]
enum ConfigValue {
    Text(String),
    Number(i64),
    Flag(bool),
    List(Vec<String>),
}

#[derive(Debug)]
enum ConfigError {
    MissingKey(String),
    TypeMismatch { key: String, expected: String },
}

fn get_port(config: &std::collections::HashMap<String, ConfigValue>) -> Result<u16, ConfigError> {
    match config.get("port") {
        None => Err(ConfigError::MissingKey("port".to_string())),
        Some(ConfigValue::Number(n)) => {
            if *n > 0 && *n <= 65535 {
                Ok(*n as u16)
            } else {
                Err(ConfigError::TypeMismatch {
                    key: "port".to_string(),
                    expected: "number between 1 and 65535".to_string(),
                })
            }
        }
        Some(_) => Err(ConfigError::TypeMismatch {
            key: "port".to_string(),
            expected: "number".to_string(),
        }),
    }
}

fn main() {
    let mut config = std::collections::HashMap::new();
    config.insert("port".to_string(), ConfigValue::Number(8080));

    match get_port(&config) {
        Ok(port) => println!("Server starting on port {}", port),
        Err(ConfigError::MissingKey(key)) => {
            eprintln!("Missing config key: {}", key);
        }
        Err(ConfigError::TypeMismatch { key, expected }) => {
            eprintln!("Config key '{}' should be {}", key, expected);
        }
    }
}
```

Notice how we're nesting patterns — matching on `Option<&ConfigValue>` first, then on the `ConfigValue` variant inside the `Some`. The compiler tracks all of it. You literally cannot forget a case.

## The Compiler Is Your Pair Programmer

The thing that took me the longest to internalize about Rust: the compiler isn't fighting you. When it rejects a non-exhaustive match, it's catching a bug that would have cost you hours in any other language. Every match arm you write is a contract — "I have thought about this case and know what should happen."

That's exhaustive matching. It's not glamorous, but it's the foundation everything else in this series builds on. Once you trust the compiler to enforce completeness, you start designing your types to leverage it. That's where the real power is.

Next up: destructuring. Because matching on variants is just the beginning — you can pull data apart in ways that make explicit field access look clunky.
