---
title: "Lesson 1: Rust's Error Philosophy — No exceptions, no surprises"
author: Atharva Pandey
series: "Rust Error Handling at Scale"
lesson: 1
keywords: [Rust, rust, error handling, error philosophy, Result, exceptions, panics]
tags: [Rust tutorial, rust, error-handling]
date: '2024-07-05T11:23:00.000Z'
---

I spent three years writing Java before I touched Rust. In Java, any function call might throw an exception — checked, unchecked, runtime, whatever. You never really *know* what's going to blow up until it does. The first time I wrote Rust code that forced me to handle every possible failure at the call site, I thought it was annoying. Six months later, I realized it was the sanest approach to errors I'd ever used.

## Why Most Languages Get Errors Wrong

Here's the fundamental problem with exceptions: they create invisible control flow.

```python
def process_order(order_id):
    order = fetch_order(order_id)       # might throw
    payment = charge_card(order.total)  # might throw
    receipt = send_receipt(order.email)  # might throw
    return receipt
```

Reading this code, you have no idea which of those three lines might fail. You don't know *how* they'll fail. You don't know if some middleware three layers up is catching the exception or if it'll crash your process. The error paths are completely hidden from the call site.

Java tried to fix this with checked exceptions, but everyone hated them so much that most modern Java code just throws `RuntimeException` everywhere. Go took a different approach — return error values — but Go's `if err != nil` is verbose and error values are just strings half the time. There's no type safety.

Rust looked at all of this and said: errors are *values*. They go through the type system. The compiler won't let you forget them.

## The Core Idea: Errors Are Just Data

In Rust, there's no `try/catch`. There's no exception stack unwinding (well, there's `panic!`, but that's different — we'll get there in lesson 9). Instead, functions that can fail return a `Result`:

```rust
enum Result<T, E> {
    Ok(T),
    Err(E),
}
```

That's it. It's a generic enum with two variants. `Ok` holds the success value, `Err` holds the error. Every function signature tells you upfront: "I might fail, and here's the type of failure you'll get."

```rust
use std::fs;
use std::io;

fn read_config(path: &str) -> Result<String, io::Error> {
    fs::read_to_string(path)
}

fn main() {
    match read_config("config.toml") {
        Ok(contents) => println!("Config loaded: {} bytes", contents.len()),
        Err(e) => eprintln!("Failed to read config: {}", e),
    }
}
```

Look at that function signature: `Result<String, io::Error>`. You know *exactly* what success looks like (a `String`) and *exactly* what failure looks like (an `io::Error`). No surprises. No hidden exceptions. No guessing.

## The Compiler Won't Let You Ignore Errors

This is the part that changed how I think about code. In most languages, you *can* ignore errors. In Python, you just don't write a `try/except`. In Go, you write `_, _ = someFunction()` and move on. The language lets you get away with it.

Rust doesn't. If a function returns `Result`, you must handle it:

```rust
use std::fs;

fn main() {
    // This compiles but gives a warning:
    // "unused `Result` that must be used"
    fs::read_to_string("config.toml");
}
```

The `#[must_use]` attribute on `Result` means the compiler will yell at you if you throw away a result. And if you want to actually *use* the value inside, you have to pattern match or use one of the combinator methods — which forces you to deal with the error case.

```rust
use std::fs;

fn main() {
    // You MUST handle both cases
    let contents = match fs::read_to_string("config.toml") {
        Ok(s) => s,
        Err(e) => {
            eprintln!("Cannot read config: {}", e);
            std::process::exit(1);
        }
    };

    println!("Got {} bytes", contents.len());
}
```

## Option: The "Nothing Here" Type

Alongside `Result`, Rust has `Option`:

```rust
enum Option<T> {
    Some(T),
    None,
}
```

`Option` is for cases where a value might not exist — but it's not an *error*. Looking up a key in a HashMap? You get `Option<&V>`. Finding the first element of an empty iterator? `Option<T>`. Parsing a string that might not contain a substring? `Option<usize>`.

```rust
use std::collections::HashMap;

fn find_user_email(users: &HashMap<u64, String>, user_id: u64) -> Option<&String> {
    users.get(&user_id)
}

fn main() {
    let mut users = HashMap::new();
    users.insert(1, String::from("atharva@example.com"));

    match find_user_email(&users, 2) {
        Some(email) => println!("Found: {}", email),
        None => println!("User not found"),
    }
}
```

The difference between `Option` and `Result` is semantic. `Option` says "this might be absent." `Result` says "this operation might fail, and here's why." Both force you to handle the case where you don't get what you wanted.

No null pointers. No `NullPointerException`. No billion-dollar mistakes. The type system makes absence explicit.

## How This Compares to Other Languages

| Language | Error mechanism | Compiler-enforced? | Type-safe? |
|----------|----------------|-------------------|-----------|
| C | Return codes, errno | No | Barely |
| C++ | Exceptions | No | Sort of |
| Java | Checked/unchecked exceptions | Partially | Yes |
| Python | Exceptions | No | No |
| Go | Return `error` interface | No | Weakly |
| Rust | `Result<T, E>` | **Yes** | **Yes** |

Rust is the only mainstream language where the compiler *guarantees* you've handled every error path. That's not a small thing. That's the difference between "we think we handle errors correctly" and "the compiler proved we handle errors correctly."

## The Mental Model Shift

Here's what tripped me up coming from exception-based languages: in Rust, you don't think about "what might throw." You think about "what does this function return?"

Every function signature is a contract. If it returns `Result<T, E>`, it can fail. If it returns `T`, it won't (barring panics, which are for bugs, not expected failures). You read the signature, you know what to expect.

This makes code reviews faster. It makes refactoring safer. When you change an error type, every call site that needs updating will fail to compile. The compiler is your error-handling linter, and it never takes a day off.

```rust
use std::num::ParseIntError;

// This signature IS the documentation
fn parse_port(s: &str) -> Result<u16, ParseIntError> {
    s.parse::<u16>()
}

// Caller knows exactly what they're dealing with
fn main() {
    let port = match parse_port("8080") {
        Ok(p) => p,
        Err(e) => {
            eprintln!("Invalid port: {}", e);
            3000 // default
        }
    };

    println!("Using port {}", port);
}
```

## What's Coming

This course is ten lessons on Rust error handling — from the basics we covered here through building production error architectures for real systems. Here's the rough map:

- **Lesson 2**: `Result` and `Option` in depth — combinators, conversions, and the methods you'll use daily
- **Lesson 3**: The `?` operator — how propagation actually works
- **Lesson 4**: Custom error types — modeling your domain's failures
- **Lesson 5**: `thiserror` — deriving error types without boilerplate
- **Lesson 6**: `anyhow` — when you just need errors to work
- **Lesson 7**: Library vs application error strategies — they're fundamentally different
- **Lesson 8**: Error context and chains — making errors debuggable
- **Lesson 9**: `panic!`, `unwrap`, `expect` — when crashing is the right call
- **Lesson 10**: Production patterns — logging, reporting, and recovery at scale

The philosophy matters because every technical decision in the rest of this course flows from it: errors are values, the type system enforces handling, and the compiler is your safety net. Once you internalize that, everything else clicks.
