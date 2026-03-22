---
title: "Lesson 24: Writing Great Rust Documentation — rustdoc that actually helps"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 24
keywords: [Rust, rust, idiomatic, documentation, rustdoc, doc comments, doc tests]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-05-20T14:30:00.000Z'
---

The Rust ecosystem has some of the best documentation I've seen in any language. And it's not an accident — the tooling actively *encourages* good documentation. Doc comments are first-class citizens. Examples in docs are compiled and tested. The standard library sets an impossibly high bar that crate authors actually try to meet.

If you're publishing a crate and the docs are an afterthought, you're doing it wrong. Documentation is the API.

---

## Doc Comments: `///` and `//!`

Rust has two kinds of doc comments:

```rust
//! This is a module-level doc comment.
//! It describes the module or crate itself.
//! Used at the top of lib.rs or mod.rs.

/// This is an item-level doc comment.
/// It describes the function, struct, enum, or trait below it.
fn my_function() {}
```

`///` documents the next item. `//!` documents the enclosing item (the module or crate).

```rust
//! # My Awesome Crate
//!
//! This crate provides utilities for parsing configuration files.
//!
//! ## Quick Start
//!
//! ```rust
//! use my_crate::Config;
//!
//! let config = Config::from_file("app.toml").unwrap();
//! println!("Port: {}", config.port());
//! ```

/// A parsed configuration file.
///
/// Supports TOML and JSON formats. Use [`Config::from_file`] to load
/// a configuration, or [`Config::builder`] for programmatic construction.
pub struct Config {
    port: u16,
    host: String,
}
```

---

## The Standard Documentation Structure

The Rust community follows a consistent structure for doc comments. Follow it, and your docs will feel familiar to every Rust developer:

```rust
/// Brief one-line description of what this does.
///
/// Longer description with more detail. Explain the "why,"
/// not just the "what." Mention important behavior, invariants,
/// and relationships to other types.
///
/// # Examples
///
/// ```rust
/// let result = add(2, 3);
/// assert_eq!(result, 5);
/// ```
///
/// # Errors
///
/// Returns [`ParseError`] if the input string is malformed.
///
/// # Panics
///
/// Panics if `divisor` is zero. (But prefer not to panic.)
///
/// # Safety
///
/// (Only for unsafe functions — explain the invariants the caller must uphold.)
pub fn add(a: i32, b: i32) -> i32 {
    a + b
}
```

The sections, in order:
1. **Brief description** (first paragraph — shown in summary views)
2. **Extended description** (additional paragraphs)
3. **Examples** — runnable code
4. **Errors** — when `Result::Err` is returned
5. **Panics** — when the function panics
6. **Safety** — for `unsafe` functions only

---

## Doc Tests: Your Docs Are Your Tests

This is Rust's killer documentation feature: code blocks in doc comments are compiled and run as tests.

```rust
/// Splits a string into words and returns the count.
///
/// # Examples
///
/// ```
/// use my_crate::word_count;
///
/// assert_eq!(word_count("hello world"), 2);
/// assert_eq!(word_count(""), 0);
/// assert_eq!(word_count("one"), 1);
/// ```
pub fn word_count(text: &str) -> usize {
    text.split_whitespace().count()
}
```

Run doc tests with `cargo test`. If the examples don't compile or the assertions fail, your tests fail. This means:

- **Examples can never be outdated.** If you change the API, the doc tests break.
- **Examples are guaranteed correct.** They compile and run.
- **Documentation is tested.** No more "the example in the README doesn't work."

### Hiding boilerplate in doc tests

Use `#` to hide setup lines that aren't relevant to the example:

```rust
/// Loads configuration from a TOML string.
///
/// ```
/// # use std::collections::HashMap;
/// # fn main() -> Result<(), Box<dyn std::error::Error>> {
/// let toml = r#"
///     port = 8080
///     host = "localhost"
/// "#;
///
/// let config = parse_toml(toml)?;
/// assert_eq!(config["port"], "8080");
/// # Ok(())
/// # }
/// # fn parse_toml(s: &str) -> Result<HashMap<String, String>, Box<dyn std::error::Error>> {
/// #     let mut map = HashMap::new();
/// #     map.insert("port".into(), "8080".into());
/// #     Ok(map)
/// # }
/// ```
pub fn documented_function() {}
```

Lines starting with `#` are compiled and run but not shown in the rendered documentation. Use them for imports, error handling boilerplate, and helper functions.

### Marking examples that should fail

```rust
/// This function panics on empty input.
///
/// ```should_panic
/// # fn only_non_empty(s: &str) -> &str {
/// #     if s.is_empty() { panic!("empty!"); }
/// #     s
/// # }
/// only_non_empty(""); // panics!
/// ```
pub fn only_non_empty(s: &str) -> &str {
    assert!(!s.is_empty(), "Input must not be empty");
    s
}
```

### Examples that shouldn't be run

```rust
/// Connects to a database.
///
/// ```no_run
/// let conn = connect("postgres://localhost/mydb").unwrap();
/// conn.query("SELECT 1").unwrap();
/// ```
pub fn connect(_url: &str) -> Result<(), String> {
    Ok(())
}
```

`no_run` compiles the example (verifying it's valid) but doesn't execute it. Use this for examples that need a running database, network, or other external resource.

---

## Linking to Other Items

Use square bracket syntax to create links to other types, functions, and modules:

```rust
/// A builder for [`Config`].
///
/// Use [`ConfigBuilder::new`] to start building, then chain
/// methods like [`ConfigBuilder::port`] and [`ConfigBuilder::host`].
///
/// See also: [`Config::from_file`] for loading from disk.
pub struct ConfigBuilder {
    port: u16,
    host: String,
}

/// The main configuration type.
///
/// Created via [`ConfigBuilder`] or [`Config::from_file`].
pub struct Config {
    port: u16,
    host: String,
}

impl Config {
    /// Loads configuration from a file.
    ///
    /// The file must be valid TOML. See [`ConfigBuilder`] for
    /// programmatic construction.
    pub fn from_file(_path: &str) -> Result<Self, String> {
        Ok(Config { port: 8080, host: "localhost".into() })
    }
}

impl ConfigBuilder {
    pub fn new() -> Self {
        ConfigBuilder { port: 8080, host: "localhost".into() }
    }

    pub fn port(mut self, port: u16) -> Self {
        self.port = port;
        self
    }

    pub fn host(mut self, host: &str) -> Self {
        self.host = host.into();
        self
    }
}
```

The links render as clickable hyperlinks in `cargo doc` output. They also break your build if the target item doesn't exist — another form of documentation testing.

---

## Module-Level Documentation

Good crate-level docs make the difference between "I'll try this crate" and "I'll look for an alternative":

```rust
//! # my_parser
//!
//! A fast, zero-copy parser for the XYZ format.
//!
//! ## Features
//!
//! - Zero-copy parsing — borrows from the input string
//! - Streaming support via the [`StreamParser`] type
//! - Serde integration with the `serde` feature flag
//!
//! ## Quick Start
//!
//! ```rust
//! use my_parser::Parser;
//!
//! let input = "key: value\nother: data";
//! let parsed = Parser::new(input).parse().unwrap();
//! assert_eq!(parsed.get("key"), Some("value"));
//! ```
//!
//! ## Feature Flags
//!
//! | Feature | Description |
//! |---------|-------------|
//! | `serde` | Enable Serialize/Deserialize derives |
//! | `async` | Enable async parsing |
```

---

## Documenting Errors and Edge Cases

Don't just document the happy path. Document what goes wrong:

```rust
/// Divides two numbers.
///
/// # Examples
///
/// ```
/// # fn divide(a: f64, b: f64) -> Result<f64, String> {
/// #     if b == 0.0 { return Err("division by zero".into()); }
/// #     Ok(a / b)
/// # }
/// assert_eq!(divide(10.0, 2.0).unwrap(), 5.0);
/// assert!(divide(1.0, 0.0).is_err());
/// ```
///
/// # Errors
///
/// Returns `Err("division by zero")` if `b` is zero.
pub fn divide(a: f64, b: f64) -> Result<f64, String> {
    if b == 0.0 {
        return Err("division by zero".into());
    }
    Ok(a / b)
}
```

---

## Generating and Viewing Docs

```bash
# Generate docs
cargo doc

# Generate and open in browser
cargo doc --open

# Include private items (for internal documentation)
cargo doc --document-private-items

# Check for broken doc links
RUSTDOCFLAGS="-D warnings" cargo doc --no-deps
```

That last command is crucial for CI — it fails if any doc links are broken.

---

## Documentation Anti-Patterns

### Don't restate the type signature

```rust
// BAD: tells me nothing I can't see from the signature
/// Takes a string and returns a usize.
pub fn word_count(text: &str) -> usize {
    text.split_whitespace().count()
}

// GOOD: explains behavior
/// Counts whitespace-separated words in the given text.
///
/// Empty strings and strings containing only whitespace return 0.
pub fn word_count_good(text: &str) -> usize {
    text.split_whitespace().count()
}
```

### Don't document obvious things

```rust
// BAD: obvious from the code
/// Returns the name.
pub fn name(&self) -> &str { &self.name }

// GOOD: adds useful information
/// Returns the user's display name.
///
/// This may differ from the login name. Guaranteed non-empty
/// after successful authentication.
pub fn name(&self) -> &str { &self.name }
```

### Do document non-obvious behavior

```rust
/// Inserts a value into the map.
///
/// If the map already contains this key, the old value is
/// replaced and returned as `Some(old_value)`. If the key
/// is new, returns `None`.
///
/// # Examples
///
/// ```
/// # use std::collections::HashMap;
/// let mut map = HashMap::new();
/// assert_eq!(map.insert("key", 1), None);      // new key
/// assert_eq!(map.insert("key", 2), Some(1));    // replaced
/// ```
pub fn insert_example() {}
```

---

## Key Takeaways

- Use `///` for item docs and `//!` for module/crate docs. They're compiled to HTML by `cargo doc`.
- Doc examples are tested — they compile and run with `cargo test`. They can never go stale.
- Follow the standard structure: brief description, examples, errors, panics, safety.
- Use `[`backtick links`]` to cross-reference types and functions — they're checked at build time.
- Document behavior and edge cases, not type signatures. Tell the reader what they can't see from the code.
- Use `#` lines to hide boilerplate in doc examples while keeping them compilable.
- Add `RUSTDOCFLAGS="-D warnings" cargo doc --no-deps` to CI to catch broken doc links.
