---
title: "Lesson 4: Or Patterns, @ Bindings, and Rest Patterns — The full syntax"
author: Atharva Pandey
series: "Rust Pattern Matching & Enums Deep Dive"
lesson: 4
keywords: [Rust, rust, pattern matching, or patterns, at bindings, rest patterns, pattern syntax]
tags: [Rust tutorial, rust, pattern-matching]
date: '2024-07-27T14:10:00.000Z'
---

I was reviewing a PR last year where someone had written twelve match arms for an enum — four groups of three variants that all did the same thing. Each group was identical code, copy-pasted three times. I left a one-line comment: "or patterns." The whole match collapsed from 36 lines to 12.

Most Rust developers learn the basics of `match` early and never dig into the full pattern syntax. That's a shame, because there are three features that eliminate a ton of redundancy: or patterns, `@` bindings, and rest patterns.

## Or Patterns: Matching Multiple Cases

The `|` operator lets you combine multiple patterns into a single arm. If *any* of the patterns match, the arm fires:

```rust
enum Day {
    Monday,
    Tuesday,
    Wednesday,
    Thursday,
    Friday,
    Saturday,
    Sunday,
}

fn is_weekend(day: &Day) -> bool {
    match day {
        Day::Saturday | Day::Sunday => true,
        _ => false,
    }
}

fn day_type(day: &Day) -> &'static str {
    match day {
        Day::Monday | Day::Tuesday | Day::Wednesday
        | Day::Thursday | Day::Friday => "weekday",
        Day::Saturday | Day::Sunday => "weekend",
    }
}

fn main() {
    println!("{}", is_weekend(&Day::Saturday)); // true
    println!("{}", is_weekend(&Day::Wednesday)); // false
    println!("{}", day_type(&Day::Friday)); // weekday
}
```

Simple enough. But or patterns get more interesting when combined with other features.

### Or Patterns With Destructuring

You can use `|` inside nested patterns, as long as all alternatives bind the same variables with the same types:

```rust
enum Expr {
    Literal(i64),
    Add(Box<Expr>, Box<Expr>),
    Sub(Box<Expr>, Box<Expr>),
    Mul(Box<Expr>, Box<Expr>),
    Neg(Box<Expr>),
}

fn count_operations(expr: &Expr) -> usize {
    match expr {
        Expr::Literal(_) => 0,
        Expr::Add(a, b) | Expr::Sub(a, b) | Expr::Mul(a, b) => {
            1 + count_operations(a) + count_operations(b)
        }
        Expr::Neg(inner) => 1 + count_operations(inner),
    }
}

fn main() {
    // (3 + 4) * 2
    let expr = Expr::Mul(
        Box::new(Expr::Add(
            Box::new(Expr::Literal(3)),
            Box::new(Expr::Literal(4)),
        )),
        Box::new(Expr::Literal(2)),
    );
    println!("Operations: {}", count_operations(&expr)); // 2
}
```

The first arm groups all binary operations together. All three variants bind `a` and `b` with the same types, so the or pattern works. When the logic is identical across variants, this cuts duplication dramatically.

### Or Patterns in `if let` and `matches!`

Or patterns aren't limited to `match`:

```rust
enum Command {
    Start,
    Stop,
    Pause,
    Resume,
    Status,
}

fn needs_running_system(cmd: &Command) -> bool {
    matches!(cmd, Command::Stop | Command::Pause | Command::Status)
}

fn main() {
    let cmd = Command::Pause;

    if let Command::Start | Command::Resume = cmd {
        println!("Starting up...");
    }

    // The matches! macro is the cleanest for boolean checks
    println!("Needs running: {}", needs_running_system(&cmd));
}
```

The `matches!` macro is syntactic sugar for `match value { pattern => true, _ => false }`. I use it constantly — it's the cleanest way to do pattern-based boolean checks.

## @ Bindings: Name What You Match

Sometimes you need to match a specific pattern *and* bind the whole value (or a subexpression) to a variable. That's what `@` does:

```rust
fn describe_age(age: u32) -> String {
    match age {
        n @ 0..=2 => format!("{} — infant", n),
        n @ 3..=12 => format!("{} — child", n),
        n @ 13..=17 => format!("{} — teenager", n),
        n @ 18..=64 => format!("{} — adult", n),
        n @ 65.. => format!("{} — senior", n),
    }
}

fn main() {
    for age in [0, 5, 15, 30, 70] {
        println!("{}", describe_age(age));
    }
}
```

Without `@`, you'd need to match the range and then reference the original value separately. With `@`, the variable `n` captures the value that matched the range. Both the range check and the binding happen in one place.

### @ With Enum Variants

This gets more useful with enums:

```rust
#[derive(Debug)]
enum Packet {
    Data { seq: u32, payload: Vec<u8> },
    Ack { seq: u32 },
    Heartbeat,
    Error { code: u16, message: String },
}

fn process_packet(packet: &Packet) {
    match packet {
        p @ Packet::Data { seq, .. } if *seq > 1000 => {
            println!("High-sequence data packet: {:?}", p);
        }
        Packet::Data { seq, payload } => {
            println!("Data seq={}, {} bytes", seq, payload.len());
        }
        Packet::Ack { seq } => {
            println!("Ack for seq={}", seq);
        }
        p @ Packet::Error { .. } => {
            // Bind the whole variant for logging, but don't destructure
            println!("Error packet: {:?}", p);
        }
        Packet::Heartbeat => {
            println!("Heartbeat");
        }
    }
}

fn main() {
    let packets = vec![
        Packet::Data { seq: 1, payload: vec![1, 2, 3] },
        Packet::Data { seq: 1500, payload: vec![4, 5] },
        Packet::Ack { seq: 1 },
        Packet::Error { code: 500, message: "internal".to_string() },
        Packet::Heartbeat,
    ];

    for p in &packets {
        process_packet(p);
    }
}
```

The `p @ Packet::Data { seq, .. }` pattern does three things: matches the `Data` variant, destructures `seq` out of it, and binds the entire `Packet::Data` to `p`. This is perfect for logging — you want the whole value for debug output but specific fields for logic.

### Nested @ Bindings

You can put `@` at any level of nesting:

```rust
#[derive(Debug)]
struct Point {
    x: i32,
    y: i32,
}

#[derive(Debug)]
enum Location {
    Known(Point),
    Unknown,
}

fn classify_location(loc: &Location) -> String {
    match loc {
        Location::Known(p @ Point { x: 0, y: 0 }) => {
            format!("At origin: {:?}", p)
        }
        Location::Known(p @ Point { x: 0, .. }) => {
            format!("On Y-axis: {:?}", p)
        }
        Location::Known(p @ Point { y: 0, .. }) => {
            format!("On X-axis: {:?}", p)
        }
        Location::Known(p) => {
            format!("At {:?}", p)
        }
        Location::Unknown => "Unknown location".to_string(),
    }
}

fn main() {
    let locations = vec![
        Location::Known(Point { x: 0, y: 0 }),
        Location::Known(Point { x: 0, y: 5 }),
        Location::Known(Point { x: 3, y: 0 }),
        Location::Known(Point { x: 3, y: 4 }),
        Location::Unknown,
    ];

    for loc in &locations {
        println!("{}", classify_location(loc));
    }
}
```

The `p @` binding captures the `Point` while the inner pattern matches specific field values. You get both the structural match and the bound variable.

## Rest Patterns: Ignore What You Don't Need

The `..` pattern (which you've already seen in struct destructuring) works in more places than most people realize.

### In Tuple Patterns

```rust
fn first_and_last(data: &(i32, i32, i32, i32, i32)) -> (i32, i32) {
    let (first, .., last) = data;
    (*first, *last)
}

fn starts_with_zero(data: &(i32, i32, i32)) -> bool {
    matches!(data, (0, ..))
}

fn main() {
    let data = (1, 2, 3, 4, 5);
    let (first, last) = first_and_last(&data);
    println!("First: {}, Last: {}", first, last);

    println!("{}", starts_with_zero(&(0, 42, 99))); // true
    println!("{}", starts_with_zero(&(1, 0, 0)));   // false
}
```

`..` matches any number of elements in the middle. You can only use it once per tuple pattern — `(a, .., b, .., c)` is ambiguous and won't compile.

### In Struct Patterns

```rust
struct Connection {
    host: String,
    port: u16,
    timeout_ms: u64,
    pool_size: u32,
    tls: bool,
    retry_count: u8,
}

fn connection_label(conn: &Connection) -> String {
    let Connection { host, port, tls, .. } = conn;
    if *tls {
        format!("tls://{}:{}", host, port)
    } else {
        format!("tcp://{}:{}", host, port)
    }
}

fn main() {
    let conn = Connection {
        host: "db.example.com".to_string(),
        port: 5432,
        timeout_ms: 5000,
        pool_size: 10,
        tls: true,
        retry_count: 3,
    };
    println!("{}", connection_label(&conn));
}
```

The `..` in struct patterns is forgiving — when new fields get added to the struct, functions using `..` don't need to change. That's a double-edged sword. Sometimes you *want* the compiler to force you to handle a new field.

### In Slice Patterns

Slice patterns with `..` are available since Rust 1.42 and they're underused:

```rust
fn describe_slice(s: &[i32]) -> String {
    match s {
        [] => "empty".to_string(),
        [x] => format!("single element: {}", x),
        [first, second] => format!("pair: ({}, {})", first, second),
        [first, .., last] => {
            format!("{} elements, from {} to {}", s.len(), first, last)
        }
    }
}

fn starts_with_header(bytes: &[u8]) -> bool {
    matches!(bytes, [0xFF, 0xD8, 0xFF, ..])  // JPEG magic bytes
}

fn main() {
    println!("{}", describe_slice(&[]));
    println!("{}", describe_slice(&[42]));
    println!("{}", describe_slice(&[1, 2]));
    println!("{}", describe_slice(&[1, 2, 3, 4, 5]));

    let jpeg_header = vec![0xFF, 0xD8, 0xFF, 0xE0, 0x00];
    let not_jpeg = vec![0x89, 0x50, 0x4E, 0x47]; // PNG
    println!("JPEG: {}", starts_with_header(&jpeg_header));
    println!("JPEG: {}", starts_with_header(&not_jpeg));
}
```

Slice patterns with `..` are particularly powerful for protocol parsing, byte-level processing, and argument handling.

## Combining Everything

Here's a realistic example that uses or patterns, `@` bindings, and rest patterns together:

```rust
#[derive(Debug)]
enum LogLevel {
    Trace,
    Debug,
    Info,
    Warn,
    Error,
    Fatal,
}

#[derive(Debug)]
struct LogEntry {
    level: LogLevel,
    message: String,
    timestamp: u64,
    source: String,
}

fn should_alert(entry: &LogEntry) -> Option<String> {
    match entry {
        // Fatal always alerts, bind the whole entry for context
        e @ LogEntry { level: LogLevel::Fatal, .. } => {
            Some(format!("CRITICAL: {:?}", e))
        }

        // Error from specific sources alerts
        LogEntry {
            level: LogLevel::Error,
            source,
            message,
            ..
        } if source == "payment" || source == "auth" => {
            Some(format!("Alert from {}: {}", source, message))
        }

        // Warn or Error with specific keywords
        LogEntry {
            level: LogLevel::Warn | LogLevel::Error,
            message,
            ..
        } if message.contains("timeout") || message.contains("connection refused") => {
            Some(format!("Infrastructure issue: {}", message))
        }

        // Everything else — no alert
        _ => None,
    }
}

fn main() {
    let entries = vec![
        LogEntry {
            level: LogLevel::Fatal,
            message: "out of memory".to_string(),
            timestamp: 1000,
            source: "system".to_string(),
        },
        LogEntry {
            level: LogLevel::Error,
            message: "card declined".to_string(),
            timestamp: 1001,
            source: "payment".to_string(),
        },
        LogEntry {
            level: LogLevel::Warn,
            message: "connection refused to cache".to_string(),
            timestamp: 1002,
            source: "cache".to_string(),
        },
        LogEntry {
            level: LogLevel::Info,
            message: "user logged in".to_string(),
            timestamp: 1003,
            source: "auth".to_string(),
        },
    ];

    for entry in &entries {
        match should_alert(entry) {
            Some(alert) => println!("ALERT: {}", alert),
            None => println!("OK: {:?}", entry.message),
        }
    }
}
```

The or pattern `LogLevel::Warn | LogLevel::Error` inside the struct pattern is the key move here — it matches two log levels with a single arm, and the guard adds the keyword check. Without or patterns, you'd need two nearly identical arms.

## Pattern Syntax Quick Reference

Here's the quick reference I keep in my head:

- `_` — match anything, don't bind
- `x` — match anything, bind to `x`
- `A | B` — match `A` or `B`
- `x @ pattern` — match pattern, bind whole value to `x`
- `..` — match remaining fields or elements
- `ref x` — bind by reference
- `ref mut x` — bind by mutable reference
- `(a, b, ..)` — match tuple, ignore trailing elements
- `[first, .., last]` — match slice, ignore middle elements
- `Struct { field, .. }` — match struct, ignore other fields

You don't need to memorize all of this upfront. But knowing these exist means you'll recognize opportunities to simplify your code when they come up.

Next up: enums carrying data. We've been using them in examples already, but it's time to talk about how to *design* them — modeling real domains with algebraic data types.
