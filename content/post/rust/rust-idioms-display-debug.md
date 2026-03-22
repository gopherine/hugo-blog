---
title: "Lesson 13: Display and Debug — Formatting done right"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 13
keywords: [Rust, rust, idiomatic, Display, Debug, formatting, traits, println]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-04-30T18:45:00.000Z'
---

Pop quiz: what's the difference between `{}` and `{:?}` in a `println!`? If your answer is "one looks prettier," you're not wrong — but you're missing the bigger picture.

`Display` and `Debug` serve fundamentally different audiences. `Display` is for humans — end users, log readers, UI consumers. `Debug` is for developers — it's what you see in error messages, test failures, and debug sessions. Conflating the two leads to types that are either too verbose for users or too opaque for debugging.

---

## Debug: The Developer's View

`Debug` is almost always derived. You rarely need to implement it by hand.

```rust
#[derive(Debug)]
struct User {
    id: u64,
    name: String,
    email: String,
    active: bool,
}

fn main() {
    let user = User {
        id: 42,
        name: String::from("Atharva"),
        email: String::from("atharva@example.com"),
        active: true,
    };

    println!("{:?}", user);
    // User { id: 42, name: "Atharva", email: "atharva@example.com", active: true }

    println!("{:#?}", user);
    // User {
    //     id: 42,
    //     name: "Atharva",
    //     email: "atharva@example.com",
    //     active: true,
    // }
}
```

`{:?}` gives you the compact form. `{:#?}` gives you the pretty-printed form. Both show the struct name and all field names and values. This is exactly what you want when you're staring at a test failure or a log line.

**Rule: derive `Debug` on every type you create.** There's no downside, and the upside is enormous — every error message, every `dbg!()` call, every assert failure becomes readable.

---

## Display: The User's View

`Display` is what you implement when your type needs to be shown to humans. It's used by `{}` in format strings, by `.to_string()`, and by the `Error` trait.

```rust
use std::fmt;

struct User {
    name: String,
    email: String,
}

impl fmt::Display for User {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} <{}>", self.name, self.email)
    }
}

fn main() {
    let user = User {
        name: String::from("Atharva"),
        email: String::from("atharva@example.com"),
    };

    println!("{}", user);  // Atharva <atharva@example.com>
}
```

Notice: `Display` cannot be derived. You have to implement it manually, because the compiler can't know how you want your type presented to humans. That's a design decision, and it's yours to make.

---

## When to Implement Display

Not every type needs `Display`. Here's my decision tree:

**Implement Display when:**
- The type represents a domain concept that has a natural string representation (email addresses, currency, coordinates)
- The type is an error (required by the `Error` trait)
- The type will appear in user-facing output
- You want `.to_string()` to work

**Skip Display when:**
- The type is an internal implementation detail
- There's no single "right" way to display it (what would `Display` for a HashMap look like?)
- `Debug` is sufficient for your debugging needs

```rust
use std::fmt;

// Good candidate for Display — clear, natural representation
#[derive(Debug)]
struct IpAddress {
    octets: [u8; 4],
}

impl fmt::Display for IpAddress {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}.{}.{}.{}", self.octets[0], self.octets[1], self.octets[2], self.octets[3])
    }
}

// Poor candidate for Display — no single natural representation
#[derive(Debug)]
struct QueryPlan {
    tables: Vec<String>,
    joins: Vec<(String, String)>,
    filters: Vec<String>,
}
// Just use Debug for this one
```

---

## Implementing Both

Most types that implement `Display` should also derive `Debug`:

```rust
use std::fmt;

#[derive(Debug, Clone)]
struct Money {
    cents: i64,
    currency: String,
}

impl Money {
    fn new(dollars: f64, currency: &str) -> Self {
        Money {
            cents: (dollars * 100.0).round() as i64,
            currency: currency.to_string(),
        }
    }
}

impl fmt::Display for Money {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let dollars = self.cents as f64 / 100.0;
        if self.cents < 0 {
            write!(f, "-{}{:.2}", self.currency, -dollars)
        } else {
            write!(f, "{}{:.2}", self.currency, dollars)
        }
    }
}

fn main() {
    let price = Money::new(29.99, "$");
    println!("Display: {}", price);  // $29.99
    println!("Debug: {:?}", price);  // Money { cents: 2999, currency: "$" }

    // Display is for users, Debug is for developers.
    // Both views are valuable.
}
```

See the difference? `Display` shows `$29.99` — what a user wants to see. `Debug` shows `Money { cents: 2999, currency: "$" }` — what a developer needs to see.

---

## Format Specifiers You Should Know

The `Formatter` gives you access to width, precision, fill, and alignment:

```rust
use std::fmt;

#[derive(Debug)]
struct Percentage(f64);

impl fmt::Display for Percentage {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // Respect precision if specified
        if let Some(precision) = f.precision() {
            write!(f, "{:.prec$}%", self.0 * 100.0, prec = precision)
        } else {
            write!(f, "{:.1}%", self.0 * 100.0)
        }
    }
}

fn main() {
    let p = Percentage(0.8567);

    println!("{}", p);      // 85.7%
    println!("{:.0}", p);   // 86%
    println!("{:.3}", p);   // 85.670%
    println!("{:>10}", p);  // "     85.7%"
}
```

---

## Custom Debug Implementations

Sometimes the derived `Debug` output is too verbose or reveals internals you'd rather hide:

```rust
use std::fmt;

struct SecretKey {
    key: Vec<u8>,
}

// DON'T derive Debug — it would expose the key!
impl fmt::Debug for SecretKey {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("SecretKey")
            .field("key", &"[REDACTED]")
            .finish()
    }
}

struct Connection {
    host: String,
    port: u16,
    pool_size: usize,
    internal_id: u64,  // implementation detail
}

impl fmt::Debug for Connection {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Connection")
            .field("host", &self.host)
            .field("port", &self.port)
            .field("pool_size", &self.pool_size)
            // Omit internal_id — it's not useful for debugging
            .finish()
    }
}

fn main() {
    let key = SecretKey { key: vec![0xDE, 0xAD, 0xBE, 0xEF] };
    println!("{:?}", key); // SecretKey { key: "[REDACTED]" }

    let conn = Connection {
        host: "localhost".into(),
        port: 5432,
        pool_size: 10,
        internal_id: 0xDEAD,
    };
    println!("{:?}", conn); // Connection { host: "localhost", port: 5432, pool_size: 10 }
}
```

The `f.debug_struct()` builder gives you full control over what fields appear in the debug output while maintaining the standard Rust debug format.

---

## Display for Enums

Enums are where `Display` really earns its keep:

```rust
use std::fmt;

#[derive(Debug)]
enum LogLevel {
    Error,
    Warn,
    Info,
    Debug,
    Trace,
}

impl fmt::Display for LogLevel {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            LogLevel::Error => write!(f, "ERROR"),
            LogLevel::Warn  => write!(f, "WARN"),
            LogLevel::Info  => write!(f, "INFO"),
            LogLevel::Debug => write!(f, "DEBUG"),
            LogLevel::Trace => write!(f, "TRACE"),
        }
    }
}

#[derive(Debug)]
enum HttpStatus {
    Ok,
    NotFound,
    InternalError,
    Custom(u16, String),
}

impl fmt::Display for HttpStatus {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            HttpStatus::Ok => write!(f, "200 OK"),
            HttpStatus::NotFound => write!(f, "404 Not Found"),
            HttpStatus::InternalError => write!(f, "500 Internal Server Error"),
            HttpStatus::Custom(code, msg) => write!(f, "{} {}", code, msg),
        }
    }
}

fn main() {
    println!("[{}] Server started", LogLevel::Info);
    // [INFO] Server started

    println!("Response: {}", HttpStatus::NotFound);
    // Response: 404 Not Found
}
```

---

## The `dbg!()` Macro

Quick aside: `dbg!()` is your best friend for quick debugging. It prints the expression, its value (via `Debug`), the file, and the line number — and returns the value so you can use it inline:

```rust
fn main() {
    let x = 42;
    let y = dbg!(x * 2) + 1;
    // [src/main.rs:3] x * 2 = 84
    println!("y = {}", y); // y = 85

    let names = dbg!(vec!["alice", "bob"]);
    // [src/main.rs:6] vec!["alice", "bob"] = ["alice", "bob"]
}
```

Use `dbg!()` for quick investigation, `println!("{:?}", x)` for output you want to keep, and proper logging for production code.

---

## Implementing Display for Error Types

If you're using `thiserror` (from the previous lesson), `Display` is generated by the `#[error("...")]` attribute. But if you're doing it manually:

```rust
use std::fmt;

#[derive(Debug)]
enum AppError {
    NotFound(String),
    PermissionDenied { user: String, resource: String },
    Internal(String),
}

impl fmt::Display for AppError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            AppError::NotFound(what) => write!(f, "{} not found", what),
            AppError::PermissionDenied { user, resource } => {
                write!(f, "user '{}' cannot access '{}'", user, resource)
            }
            AppError::Internal(msg) => write!(f, "internal error: {}", msg),
        }
    }
}

impl std::error::Error for AppError {}

fn main() {
    let err = AppError::PermissionDenied {
        user: "atharva".into(),
        resource: "/admin".into(),
    };
    println!("Error: {}", err);
    // Error: user 'atharva' cannot access '/admin'
}
```

---

## Key Takeaways

- **Always derive `Debug`** on your types. No exceptions.
- **Implement `Display`** when the type has a natural human-readable representation.
- `Debug` (`{:?}`) is for developers. `Display` (`{}`) is for users. Don't confuse them.
- Use `f.debug_struct()` for custom `Debug` implementations — redact secrets, hide internals.
- `Display` cannot be derived — it requires a deliberate design choice about presentation.
- `dbg!()` is for quick debugging, not for production output.
- If you implement `Display`, you automatically get `.to_string()` for free.
