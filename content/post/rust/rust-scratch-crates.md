---
title: "Lesson 18: Crates, Cargo.toml, and Dependencies — The Rust ecosystem"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 18
keywords: [Rust, rust, crates, cargo, Cargo.toml, dependencies, crates.io, packages, library crate, binary crate]
tags: [Rust tutorial, rust, beginner]
date: '2024-04-05T10:30:00.000Z'
---

The first time I added a dependency in Rust, I was shocked. Add one line to Cargo.toml, run `cargo build`, and it downloads, compiles, and links everything automatically. Coming from C++ where dependency management is a special circle of hell, Cargo felt like cheating.

## What Is a Crate?

A crate is a compilation unit in Rust — the smallest amount of code the compiler considers at a time. There are two kinds:

- **Binary crate** — has a `main()` function, compiles to an executable
- **Library crate** — no `main()`, compiles to a library other code can use

When someone says "add the `serde` crate," they mean "add the `serde` library as a dependency." Crates are Rust's unit of code sharing.

## Cargo.toml

Every Rust project has a `Cargo.toml`:

```toml
[package]
name = "my_project"
version = "0.1.0"
edition = "2021"
authors = ["Your Name <you@example.com>"]
description = "A short description"

[dependencies]
# Your dependencies go here
```

The `[package]` section describes your project. The `[dependencies]` section lists external crates you depend on.

## Adding Dependencies

To add a dependency, list it under `[dependencies]`:

```toml
[dependencies]
serde = "1.0"
serde_json = "1.0"
rand = "0.8"
```

Then run `cargo build` (or just `cargo check`). Cargo downloads the crates from [crates.io](https://crates.io), compiles them, and makes them available in your code.

You can also use `cargo add`:

```rust
// In your terminal:
// cargo add serde --features derive
// cargo add rand
```

`cargo add` modifies Cargo.toml for you and is generally faster than editing the file by hand.

## Version Requirements

The version strings in Cargo.toml follow SemVer and Cargo's version resolution:

```toml
[dependencies]
# Caret (default) — compatible updates
serde = "1.0"       # same as "^1.0" — any 1.x.y where x >= 0
serde = "1.0.193"   # any 1.x.y where (x, y) >= (0, 193)

# Exact version
serde = "=1.0.193"  # exactly this version, nothing else

# Tilde — patch-level updates only
serde = "~1.0.193"  # 1.0.x where x >= 193

# Wildcard
serde = "1.*"        # any 1.x.y

# Range
serde = ">=1.0, <2.0"
```

**My recommendation**: use `"1.0"` (bare version) for most dependencies. It allows compatible updates. Pin exact versions (`"=1.0.193"`) only when you have a specific reason — like a regression in a newer version.

## Cargo.lock

When you first build, Cargo creates `Cargo.lock` — a file that records the exact versions of every dependency (and their dependencies). This ensures reproducible builds.

- **Binary projects**: commit `Cargo.lock` to git. Everyone builds the same thing.
- **Library crates**: don't commit `Cargo.lock`. Let downstream users resolve versions.

## Using Dependencies

Once declared in Cargo.toml, use them in your code:

```rust
use rand::Rng;

fn main() {
    let mut rng = rand::thread_rng();

    // Random integer in range
    let n: i32 = rng.gen_range(1..=100);
    println!("Random number: {n}");

    // Random float
    let f: f64 = rng.gen();
    println!("Random float: {f}");

    // Random boolean
    let b: bool = rng.gen();
    println!("Random bool: {b}");
}
```

```rust
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
struct User {
    name: String,
    age: u32,
    email: String,
}

fn main() {
    let user = User {
        name: String::from("Alice"),
        age: 30,
        email: String::from("alice@example.com"),
    };

    // Serialize to JSON
    let json = serde_json::to_string_pretty(&user).unwrap();
    println!("JSON:\n{json}");

    // Deserialize from JSON
    let parsed: User = serde_json::from_str(&json).unwrap();
    println!("Parsed: {:?}", parsed);
}
```

For the serde example, your Cargo.toml needs:

```toml
[dependencies]
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
```

The `features = ["derive"]` part enables serde's derive macros. Features are optional functionality that dependencies can provide — more on this below.

## Features

Crates can define optional features that enable additional functionality:

```toml
[dependencies]
# Enable specific features
tokio = { version = "1", features = ["full"] }
serde = { version = "1.0", features = ["derive"] }
reqwest = { version = "0.11", features = ["json", "rustls-tls"] }
```

Features keep compile times down by letting you include only what you need. `tokio` with `features = ["full"]` is convenient but slow to compile. In production, list only the features you actually use.

## Dev Dependencies

Dependencies only needed for tests and examples:

```toml
[dev-dependencies]
assert_cmd = "2.0"
predicates = "3.0"
tempfile = "3.0"
```

Dev dependencies are not included when someone depends on your crate. They're only compiled for `cargo test` and `cargo bench`.

## Build Dependencies

Dependencies needed by build scripts (`build.rs`):

```toml
[build-dependencies]
cc = "1.0"
bindgen = "0.69"
```

You won't need these as a beginner. They're for generating code at compile time or linking C libraries.

## Creating a Library Crate

To create a library instead of a binary:

```rust
// In terminal: cargo new my_library --lib
```

This creates `src/lib.rs` instead of `src/main.rs`:

```rust
// src/lib.rs

/// Adds two numbers together.
///
/// # Examples
///
/// ```
/// let result = my_library::add(2, 3);
/// assert_eq!(result, 5);
/// ```
pub fn add(a: i32, b: i32) -> i32 {
    a + b
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_add() {
        assert_eq!(add(2, 3), 5);
        assert_eq!(add(-1, 1), 0);
    }
}
```

Everything `pub` in `lib.rs` is your crate's public API. The `#[cfg(test)]` block is only compiled during testing.

## Both Binary and Library in One Crate

You can have both:

```
my_project/
├── Cargo.toml
└── src/
    ├── lib.rs     # library code
    └── main.rs    # binary that uses the library
```

`src/main.rs`:
```rust
use my_project::add;  // use the crate name to import from lib.rs

fn main() {
    println!("2 + 3 = {}", add(2, 3));
}
```

This pattern is common for tools that also want to expose a library API.

## Essential Crates

Here are the crates I reach for on almost every project:

| Crate | What it does |
|-------|-------------|
| `serde` + `serde_json` | Serialization/deserialization |
| `tokio` | Async runtime |
| `clap` | Command-line argument parsing |
| `anyhow` | Easy error handling for applications |
| `thiserror` | Custom error types for libraries |
| `tracing` | Structured logging |
| `reqwest` | HTTP client |
| `chrono` | Date/time handling |
| `regex` | Regular expressions |
| `rand` | Random number generation |

`anyhow` and `thiserror` deserve special mention. For application code, `anyhow` lets you use `?` with any error type without defining conversions. For library code, `thiserror` generates the boilerplate for custom error types. Together, they cover 95% of error handling needs.

## Cargo Commands Recap

```
cargo new project_name      # create a new binary project
cargo new lib_name --lib     # create a new library project
cargo add crate_name         # add a dependency
cargo build                  # compile
cargo run                    # compile and run
cargo test                   # run tests
cargo check                  # type-check without building
cargo doc --open             # generate and view documentation
cargo update                 # update dependencies within version constraints
cargo tree                   # show dependency tree
cargo clippy                 # run the linter
cargo fmt                    # format code
```

`cargo tree` is invaluable for understanding your dependency graph. When your compile times creep up, it shows you exactly where the bloat is.

## Finding Crates

- **[crates.io](https://crates.io)** — the official registry
- **[lib.rs](https://lib.rs)** — better search and categorization
- **[docs.rs](https://docs.rs)** — documentation for every published crate

When evaluating a crate, I check:
1. **Download count** — is it widely used?
2. **Last updated** — is it maintained?
3. **Documentation** — is it documented?
4. **Dependencies** — does it pull in the entire universe?

A crate with 10 million downloads, updated last month, with good docs and few dependencies? That's a safe bet.

Next lesson: traits — your first real abstraction mechanism.
