---
title: "Lesson 12: Enums and Option — Null safety by design"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 12
keywords: [Rust, rust, enums, Option, Some, None, null safety, algebraic data types, sum types]
tags: [Rust tutorial, rust, beginner]
date: '2024-03-25T12:00:00.000Z'
---

Tony Hoare called null his "billion-dollar mistake." He invented it in 1965 and has publicly apologized for it multiple times since. Rust took him seriously. There is no null in Rust — and the replacement is so much better that going back to languages with null feels like giving up a superpower.

## Basic Enums

At their simplest, enums define a type that can be one of several variants:

```rust
#[derive(Debug)]
enum Direction {
    North,
    South,
    East,
    West,
}

fn describe(dir: &Direction) -> &str {
    match dir {
        Direction::North => "heading north",
        Direction::South => "heading south",
        Direction::East => "heading east",
        Direction::West => "heading west",
    }
}

fn main() {
    let d = Direction::North;
    println!("{:?}: {}", d, describe(&d));
}
```

If this is all enums did, they'd be equivalent to C enums. Mildly useful. Not exciting.

## Enums with Data

Here's where Rust enums become extraordinary. Each variant can carry different data:

```rust
#[derive(Debug)]
enum Shape {
    Circle(f64),                    // radius
    Rectangle(f64, f64),            // width, height
    Triangle { a: f64, b: f64, c: f64 }, // three sides
}

fn area(shape: &Shape) -> f64 {
    match shape {
        Shape::Circle(r) => std::f64::consts::PI * r * r,
        Shape::Rectangle(w, h) => w * h,
        Shape::Triangle { a, b, c } => {
            // Heron's formula
            let s = (a + b + c) / 2.0;
            (s * (s - a) * (s - b) * (s - c)).sqrt()
        }
    }
}

fn main() {
    let shapes = vec![
        Shape::Circle(5.0),
        Shape::Rectangle(4.0, 6.0),
        Shape::Triangle { a: 3.0, b: 4.0, c: 5.0 },
    ];

    for shape in &shapes {
        println!("{:?} -> area: {:.2}", shape, area(shape));
    }
}
```

This is a *sum type* (or *tagged union* or *algebraic data type*). A `Shape` is a Circle OR a Rectangle OR a Triangle. Not all three. Not none of them. Exactly one, with the appropriate data attached.

Try doing this cleanly in Java or C. You'd need an abstract base class, three subclasses, and a visitor pattern. Or you'd use a struct with a type field and optional fields, half of which are null at any given time. Rust enums are both safer and more concise.

## Option — The Null Killer

The most important enum in Rust:

```rust
// This is built into the language — you don't need to define it:
// enum Option<T> {
//     Some(T),
//     None,
// }
```

`Option<T>` means "maybe a T, maybe nothing." It replaces null from every other language.

```rust
fn find_first_even(numbers: &[i32]) -> Option<i32> {
    for &n in numbers {
        if n % 2 == 0 {
            return Some(n);
        }
    }
    None
}

fn main() {
    let nums = vec![1, 3, 5, 8, 11];

    match find_first_even(&nums) {
        Some(n) => println!("First even: {n}"),
        None => println!("No even numbers"),
    }

    let empty: Vec<i32> = vec![1, 3, 5];
    match find_first_even(&empty) {
        Some(n) => println!("First even: {n}"),
        None => println!("No even numbers"),
    }
}
```

Here's why this is better than null: **you can't forget to handle the missing case.** An `Option<i32>` is not an `i32`. You can't add two `Option<i32>` values. You can't call methods on `Option<String>` as if it were a `String`. You must explicitly check whether a value is present.

In Java, any reference can be null, and the compiler doesn't help you. The NullPointerException is the most common exception in Java for a reason. In Rust, if a function returns `Option<T>`, the type system forces you to handle the `None` case.

## Working with Option

### unwrap — The Quick and Dirty Way

```rust
fn main() {
    let x: Option<i32> = Some(42);
    let value = x.unwrap();  // get the value, panic if None
    println!("{value}");

    // let y: Option<i32> = None;
    // y.unwrap();  // PANIC: called unwrap on a None value
}
```

`unwrap()` panics on `None`. Use it for prototyping and tests, never in production code.

### unwrap_or and unwrap_or_else — Defaults

```rust
fn main() {
    let x: Option<i32> = None;

    let a = x.unwrap_or(0);          // default value
    let b = x.unwrap_or_else(|| {    // lazily computed default
        println!("Computing default...");
        42
    });

    println!("a: {a}, b: {b}");
}
```

### map — Transform the Inner Value

```rust
fn main() {
    let name: Option<String> = Some(String::from("alice"));

    let upper: Option<String> = name.map(|n| n.to_uppercase());
    println!("{:?}", upper);  // Some("ALICE")

    let none: Option<String> = None;
    let upper: Option<String> = none.map(|n| n.to_uppercase());
    println!("{:?}", upper);  // None — map does nothing on None
}
```

### and_then — Chain Operations That Return Option

```rust
fn parse_port(s: &str) -> Option<u16> {
    s.parse().ok()
}

fn validate_port(port: u16) -> Option<u16> {
    if port >= 1024 {
        Some(port)
    } else {
        None  // reject privileged ports
    }
}

fn main() {
    let result = parse_port("8080")
        .and_then(validate_port);
    println!("Valid port: {:?}", result);  // Some(8080)

    let result = parse_port("80")
        .and_then(validate_port);
    println!("Valid port: {:?}", result);  // None (privileged)

    let result = parse_port("abc")
        .and_then(validate_port);
    println!("Valid port: {:?}", result);  // None (parse failure)
}
```

### if let — When You Only Care About Some

```rust
fn main() {
    let config_value: Option<String> = Some(String::from("production"));

    if let Some(env) = config_value {
        println!("Environment: {env}");
    }

    let missing: Option<String> = None;
    if let Some(env) = missing {
        println!("This won't print: {env}");
    } else {
        println!("No environment set, using default");
    }
}
```

## Enums in Practice

### Representing State Machines

```rust
#[derive(Debug)]
enum ConnectionState {
    Disconnected,
    Connecting { attempt: u32 },
    Connected { address: String },
    Failed { message: String },
}

fn handle_state(state: &ConnectionState) {
    match state {
        ConnectionState::Disconnected => {
            println!("Not connected. Attempting connection...");
        }
        ConnectionState::Connecting { attempt } => {
            println!("Connection attempt #{attempt}...");
        }
        ConnectionState::Connected { address } => {
            println!("Connected to {address}");
        }
        ConnectionState::Failed { message } => {
            println!("FAILED: {message}");
        }
    }
}

fn main() {
    let states = vec![
        ConnectionState::Disconnected,
        ConnectionState::Connecting { attempt: 1 },
        ConnectionState::Connecting { attempt: 2 },
        ConnectionState::Connected {
            address: String::from("192.168.1.1:8080"),
        },
        ConnectionState::Failed {
            message: String::from("Connection reset"),
        },
    ];

    for state in &states {
        handle_state(state);
    }
}
```

This is how I model state in every Rust project. The enum makes impossible states unrepresentable. You can't have a "Connected" state without an address. You can't have a "Failed" state without a message. The type system enforces your domain model.

### Command Pattern

```rust
#[derive(Debug)]
enum Command {
    Quit,
    Echo(String),
    Move { x: i32, y: i32 },
    Color(u8, u8, u8),
}

fn execute(cmd: &Command) {
    match cmd {
        Command::Quit => println!("Quitting"),
        Command::Echo(msg) => println!("Echo: {msg}"),
        Command::Move { x, y } => println!("Moving to ({x}, {y})"),
        Command::Color(r, g, b) => println!("Color: rgb({r}, {g}, {b})"),
    }
}

fn main() {
    let commands = vec![
        Command::Echo(String::from("hello")),
        Command::Move { x: 10, y: 20 },
        Command::Color(255, 128, 0),
        Command::Quit,
    ];

    for cmd in &commands {
        execute(cmd);
    }
}
```

### Recursive Types with Box

Enums can be recursive if you use `Box`:

```rust
#[derive(Debug)]
enum Expr {
    Num(f64),
    Add(Box<Expr>, Box<Expr>),
    Mul(Box<Expr>, Box<Expr>),
}

fn compute(expr: &Expr) -> f64 {
    match expr {
        Expr::Num(n) => *n,
        Expr::Add(a, b) => compute(a) + compute(b),
        Expr::Mul(a, b) => compute(a) * compute(b),
    }
}

fn main() {
    // (2 + 3) * 4
    let expr = Expr::Mul(
        Box::new(Expr::Add(
            Box::new(Expr::Num(2.0)),
            Box::new(Expr::Num(3.0)),
        )),
        Box::new(Expr::Num(4.0)),
    );

    println!("{:?} = {}", expr, compute(&expr));
}
```

`Box` is a heap-allocated pointer. Without it, the enum would have infinite size (Expr contains Expr contains Expr...). The Box gives it a fixed size by adding a level of indirection.

## Option Methods Cheat Sheet

| Method | When to use |
|--------|-------------|
| `unwrap()` | Prototyping only — panics on None |
| `expect("msg")` | Same as unwrap but with a custom panic message |
| `unwrap_or(default)` | Provide a fallback value |
| `unwrap_or_default()` | Use the type's Default implementation |
| `map(f)` | Transform the inner value |
| `and_then(f)` | Chain operations that return Option |
| `or(other)` | Use another Option as fallback |
| `filter(predicate)` | Keep Some only if predicate passes |
| `is_some()` / `is_none()` | Boolean check |

Enums are, in my opinion, the single most powerful feature in Rust's type system. They let you model your domain precisely, make impossible states unrepresentable, and eliminate null pointer bugs entirely. Once you get comfortable with them, you'll miss them in every other language.

Next lesson: pattern matching with `match` — the tool that makes enums truly sing.
