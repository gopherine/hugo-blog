---
title: "Lesson 5: std::fmt — Formatting internals"
author: Atharva Pandey
series: "Rust Standard Library Mastery"
lesson: 5
keywords: [Rust, rust, standard library, fmt, Display, Debug, formatting, format strings]
tags: [Rust tutorial, rust, stdlib]
date: '2024-09-24T11:05:00.000Z'
---

I once shipped a monitoring dashboard where all the latency values showed up as "Duration { secs: 0, nanos: 234000000 }" because I'd used `{:?}` instead of implementing `Display`. That's the kind of thing that makes you sit down and actually learn how formatting works.

## Display vs. Debug

These two traits are the foundation of everything in `std::fmt`. Every time you use `println!`, `format!`, or `write!`, you're invoking one of them.

- **`Display`** (`{}`) — user-facing output. Clean, readable. You implement this yourself.
- **`Debug`** (`{:?}`) — developer-facing output. Can be auto-derived. Shows structure.

```rust
use std::fmt;

struct Temperature {
    celsius: f64,
}

// Debug can be derived automatically
#[derive(Debug)]
struct TemperatureDebug {
    celsius: f64,
}

// Display must be implemented manually
impl fmt::Display for Temperature {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:.1}°C", self.celsius)
    }
}

fn main() {
    let temp = Temperature { celsius: 23.456 };
    let temp_d = TemperatureDebug { celsius: 23.456 };

    println!("Display: {temp}");      // 23.5°C
    println!("Debug:   {temp_d:?}");  // TemperatureDebug { celsius: 23.456 }
}
```

The rule is simple: if a human will see it, implement `Display`. If a developer will see it (logs, debug output, error messages during development), `Debug` is fine.

## Format Specifier Syntax

The full syntax inside `{}` is more powerful than most people realize:

```
{[argument][:[fill][align][sign][#][0][width][.precision][type]}
```

That looks dense, so here it is in practice:

```rust
fn main() {
    let name = "Rust";
    let pi = 3.14159265358979;
    let num = 42;

    // Width — minimum field width
    println!("[{:>10}]", name);   // [      Rust]  right-aligned
    println!("[{:<10}]", name);   // [Rust      ]  left-aligned
    println!("[{:^10}]", name);   // [   Rust   ]  center-aligned

    // Fill character
    println!("[{:*>10}]", name);  // [******Rust]
    println!("[{:-<10}]", name);  // [Rust------]
    println!("[{:=^10}]", name);  // [===Rust===]

    // Precision for floats
    println!("{:.2}", pi);        // 3.14
    println!("{:.5}", pi);        // 3.14159
    println!("{:.0}", pi);        // 3

    // Width + precision
    println!("[{:>10.3}]", pi);   // [     3.142]

    // Integer formatting
    println!("Decimal:  {}", num);      // 42
    println!("Binary:   {:b}", num);    // 101010
    println!("Octal:    {:o}", num);    // 52
    println!("Hex low:  {:x}", num);    // 2a
    println!("Hex up:   {:X}", num);    // 2A

    // # flag adds prefix for alternate format
    println!("Hex:      {:#x}", num);   // 0x2a
    println!("Binary:   {:#b}", num);   // 0b101010
    println!("Octal:    {:#o}", num);   // 0o52

    // Zero-padding
    println!("Padded:   {:06}", num);   // 000042
    println!("Hex pad:  {:08x}", num);  // 0000002a

    // Sign
    println!("Positive: {:+}", 42);     // +42
    println!("Negative: {:+}", -42);    // -42
}
```

### Dynamic Width and Precision

You can pull width and precision from variables using `$`:

```rust
fn print_table(headers: &[&str], rows: &[Vec<String>]) {
    // Calculate column widths
    let widths: Vec<usize> = headers.iter().enumerate().map(|(i, h)| {
        let max_data = rows.iter()
            .map(|row| row.get(i).map(|s| s.len()).unwrap_or(0))
            .max()
            .unwrap_or(0);
        h.len().max(max_data)
    }).collect();

    // Print headers
    for (i, header) in headers.iter().enumerate() {
        print!("| {:width$} ", header, width = widths[i]);
    }
    println!("|");

    // Print separator
    for width in &widths {
        print!("|{:-<w$}--", "", w = width);
    }
    println!("|");

    // Print rows
    for row in rows {
        for (i, cell) in row.iter().enumerate() {
            print!("| {:width$} ", cell, width = widths[i]);
        }
        println!("|");
    }
}

fn main() {
    let headers = vec!["Name", "Score", "Grade"];
    let rows = vec![
        vec!["Alice".into(), "95".into(), "A".into()],
        vec!["Bob".into(), "87".into(), "B+".into()],
        vec!["Charlie".into(), "92".into(), "A-".into()],
    ];

    print_table(&headers, &rows);

    // Dynamic precision
    for precision in 1..=6 {
        println!("pi to {precision} places: {:.prec$}", std::f64::consts::PI, prec = precision);
    }
}
```

## Implementing Display for Complex Types

Here's where it gets interesting. Real-world types need thoughtful `Display` implementations.

```rust
use std::fmt;

#[derive(Debug)]
struct HttpResponse {
    status: u16,
    headers: Vec<(String, String)>,
    body: String,
}

impl fmt::Display for HttpResponse {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        writeln!(f, "HTTP/1.1 {}", self.status)?;
        for (key, value) in &self.headers {
            writeln!(f, "{key}: {value}")?;
        }
        writeln!(f)?;
        write!(f, "{}", self.body)
    }
}

#[derive(Debug)]
struct Duration {
    total_seconds: u64,
}

impl fmt::Display for Duration {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let hours = self.total_seconds / 3600;
        let minutes = (self.total_seconds % 3600) / 60;
        let seconds = self.total_seconds % 60;

        if hours > 0 {
            write!(f, "{hours}h {minutes}m {seconds}s")
        } else if minutes > 0 {
            write!(f, "{minutes}m {seconds}s")
        } else {
            write!(f, "{seconds}s")
        }
    }
}

#[derive(Debug)]
struct ByteSize(u64);

impl fmt::Display for ByteSize {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let bytes = self.0;
        if bytes >= 1_073_741_824 {
            write!(f, "{:.2} GB", bytes as f64 / 1_073_741_824.0)
        } else if bytes >= 1_048_576 {
            write!(f, "{:.2} MB", bytes as f64 / 1_048_576.0)
        } else if bytes >= 1024 {
            write!(f, "{:.2} KB", bytes as f64 / 1024.0)
        } else {
            write!(f, "{bytes} B")
        }
    }
}

fn main() {
    let response = HttpResponse {
        status: 200,
        headers: vec![
            ("Content-Type".into(), "text/html".into()),
            ("Content-Length".into(), "1234".into()),
        ],
        body: "<h1>Hello</h1>".into(),
    };

    println!("{response}");
    println!("---");
    println!("{response:?}"); // Compare Display vs Debug

    let d = Duration { total_seconds: 3725 };
    println!("\nDuration: {d}"); // 1h 2m 5s

    let sizes = vec![
        ByteSize(500),
        ByteSize(15_360),
        ByteSize(2_621_440),
        ByteSize(5_368_709_120),
    ];
    for size in &sizes {
        println!("Size: {size}");
    }
}
```

## The Other Formatting Traits

Beyond `Display` and `Debug`, there are specialized formatting traits:

```rust
use std::fmt;

struct Color {
    r: u8,
    g: u8,
    b: u8,
}

impl fmt::Display for Color {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "rgb({}, {}, {})", self.r, self.g, self.b)
    }
}

impl fmt::LowerHex for Color {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "#{:02x}{:02x}{:02x}", self.r, self.g, self.b)
    }
}

impl fmt::UpperHex for Color {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "#{:02X}{:02X}{:02X}", self.r, self.g, self.b)
    }
}

impl fmt::Binary for Color {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "R:{:08b} G:{:08b} B:{:08b}", self.r, self.g, self.b)
    }
}

fn main() {
    let coral = Color { r: 255, g: 127, b: 80 };

    println!("Display:   {coral}");         // rgb(255, 127, 80)
    println!("Hex lower: {coral:x}");       // #ff7f50
    println!("Hex upper: {coral:X}");       // #FF7F50
    println!("Binary:    {coral:b}");       // R:11111111 G:01111111 B:01010000
}
```

The full list: `Display`, `Debug`, `Binary`, `Octal`, `LowerHex`, `UpperHex`, `LowerExp`, `UpperExp`, `Pointer`. You pick the right trait for the formatting specifier you want to support.

## Respecting Formatter Flags

A well-behaved `Display` implementation should respect the width, alignment, and fill flags from the format string. The `Formatter` has methods to help:

```rust
use std::fmt;

struct Tag(String);

impl fmt::Display for Tag {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // This respects width, alignment, and fill
        // by using the pad() method
        f.pad(&self.0)
    }
}

struct Score(f64);

impl fmt::Display for Score {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // Respect precision if specified, otherwise use default
        let precision = f.precision().unwrap_or(1);
        let formatted = format!("{:.prec$}%", self.0 * 100.0, prec = precision);
        f.pad(&formatted)
    }
}

fn main() {
    let tag = Tag("rust".into());
    println!("[{tag}]");           // [rust]
    println!("[{tag:>10}]");       // [      rust]
    println!("[{tag:*<10}]");      // [rust******]
    println!("[{tag:=^10}]");      // [===rust===]

    let score = Score(0.8756);
    println!("{score}");           // 87.6%
    println!("{score:.3}");        // 87.560%
    println!("{score:>12.2}");     //       87.56%
}
```

## Debug Derive and Custom Debug

The derived `Debug` works for most types, but sometimes you want to customize it:

```rust
use std::fmt;

struct Connection {
    host: String,
    port: u16,
    password: String, // Don't want this in debug output!
    connected: bool,
}

impl fmt::Debug for Connection {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Connection")
            .field("host", &self.host)
            .field("port", &self.port)
            .field("password", &"[REDACTED]")
            .field("connected", &self.connected)
            .finish()
    }
}

// For collections, use debug_list, debug_set, debug_map
struct Registry {
    items: Vec<(String, i32)>,
}

impl fmt::Debug for Registry {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_map()
            .entries(self.items.iter().map(|(k, v)| (k, v)))
            .finish()
    }
}

fn main() {
    let conn = Connection {
        host: "db.example.com".into(),
        port: 5432,
        password: "super_secret_123".into(),
        connected: true,
    };

    // Password is redacted
    println!("{conn:?}");
    println!("{conn:#?}"); // Pretty-printed

    let reg = Registry {
        items: vec![
            ("alpha".into(), 1),
            ("beta".into(), 2),
            ("gamma".into(), 3),
        ],
    };
    println!("{reg:#?}");
}
```

Redacting sensitive fields in `Debug` is a habit worth building. Debug output ends up in logs, error messages, panic messages — all places where passwords and tokens shouldn't appear.

## The format_args! Macro

Under the hood, `println!`, `format!`, `write!`, and `writeln!` all use `format_args!`. This macro creates a `fmt::Arguments` value without allocating a string — the formatting is deferred until it's actually written somewhere.

```rust
use std::fmt;
use std::io::{self, Write};

fn log(level: &str, args: fmt::Arguments<'_>) {
    let timestamp = "2024-09-24T11:05:00Z"; // simplified
    let mut stderr = io::stderr().lock();
    let _ = writeln!(stderr, "[{timestamp}] {level}: {args}");
}

macro_rules! info {
    ($($arg:tt)*) => {
        log("INFO", format_args!($($arg)*))
    };
}

macro_rules! warn {
    ($($arg:tt)*) => {
        log("WARN", format_args!($($arg)*))
    };
}

fn main() {
    info!("Server starting on port {}", 8080);
    warn!("Cache miss rate is {:.1}%", 23.7);
    info!("Processed {} requests in {}ms", 1500, 245);
}
```

This pattern avoids allocating a `String` for the formatted message — it writes directly to stderr. That matters in hot paths like logging.

## write! vs. format!

Use `write!` when you're building into an existing buffer. Use `format!` when you need a new `String`. The difference is allocation:

```rust
use std::fmt::Write; // Note: this is std::fmt::Write, not std::io::Write

fn main() {
    // format! always allocates a new String
    let s = format!("Hello, {}!", "world");

    // write! appends to an existing buffer — can reuse allocations
    let mut buf = String::with_capacity(1024);
    for i in 0..5 {
        buf.clear(); // Reuse the allocation
        write!(buf, "Item {i}: value = {}", i * 10).unwrap();
        println!("{buf}");
    }

    // Building complex strings efficiently
    let mut report = String::new();
    writeln!(report, "Performance Report").unwrap();
    writeln!(report, "==================").unwrap();
    for i in 1..=5 {
        writeln!(report, "  Metric {i}: {:.2}ms", i as f64 * 1.234).unwrap();
    }
    print!("{report}");
}
```

The `std::fmt::Write` trait (for `String`) and `std::io::Write` trait (for files, sockets, etc.) are different traits. Watch your imports — this trips people up.

## Practical Patterns

```rust
use std::fmt;

// Display for enums with variants
#[derive(Debug)]
enum LogLevel {
    Trace,
    Debug,
    Info,
    Warn,
    Error,
}

impl fmt::Display for LogLevel {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            LogLevel::Trace => write!(f, "TRACE"),
            LogLevel::Debug => write!(f, "DEBUG"),
            LogLevel::Info  => write!(f, "INFO "),
            LogLevel::Warn  => write!(f, "WARN "),
            LogLevel::Error => write!(f, "ERROR"),
        }
    }
}

// Display that delegates to inner types
struct Wrapper<T: fmt::Display>(T);

impl<T: fmt::Display> fmt::Display for Wrapper<T> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "[wrapped: {}]", self.0)
    }
}

// Joining collections
struct CommaSeparated<'a, T: fmt::Display>(&'a [T]);

impl<T: fmt::Display> fmt::Display for CommaSeparated<'_, T> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        for (i, item) in self.0.iter().enumerate() {
            if i > 0 {
                write!(f, ", ")?;
            }
            write!(f, "{item}")?;
        }
        Ok(())
    }
}

fn main() {
    let levels = vec![LogLevel::Info, LogLevel::Warn, LogLevel::Error];
    for level in &levels {
        println!("[{level}] something happened");
    }

    let wrapped = Wrapper(42);
    println!("{wrapped}");

    let items = vec![1, 2, 3, 4, 5];
    println!("Items: {}", CommaSeparated(&items));
}
```

Formatting is one of those things that seems trivial until you need it to be right. The `std::fmt` system gives you precise control over how your types present themselves — to users, to developers, to logs. The investment in learning the specifier syntax and implementing `Display` properly pays dividends every time someone reads your program's output.
