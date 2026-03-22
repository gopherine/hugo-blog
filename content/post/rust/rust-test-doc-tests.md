---
title: "Lesson 3: Doc Tests — Tested documentation"
author: Atharva Pandey
series: "Rust Testing Masterclass"
lesson: 3
keywords: [Rust, rust, testing, doc tests, documentation, rustdoc, code examples]
tags: [Rust tutorial, rust, testing]
date: '2024-08-14T18:10:00.000Z'
---

I once read the docs for a popular Rust crate, copied the example verbatim, and it didn't compile. The API had changed two versions ago but nobody updated the docs. I spent twenty minutes debugging code that was supposed to be the "getting started" guide. Rust has a built-in solution to this exact problem, and it's one of the most underappreciated features in the language.

## The Problem

Documentation lies. Not on purpose — it lies because code evolves and docs don't. A function signature changes, a parameter gets renamed, a return type shifts from `String` to `&str`, and the example in the docstring quietly becomes fiction. No compiler warning, no test failure, just a frustrated user copy-pasting broken code.

Rust's doc tests solve this by compiling and running every code example in your documentation as part of `cargo test`. If the example doesn't compile or produces the wrong output, the test fails. Your documentation is always honest or your CI pipeline screams.

## How Doc Tests Work

Any code block inside a `///` doc comment is treated as a test. Rust wraps it in a `fn main()` and tries to compile and run it.

```rust
/// Adds two numbers together.
///
/// ```
/// let result = my_crate::add(2, 3);
/// assert_eq!(result, 5);
/// ```
pub fn add(a: i32, b: i32) -> i32 {
    a + b
}
```

When you run `cargo test`, this example gets extracted, wrapped in a hidden `main()`, compiled against your crate, and executed. If `add(2, 3)` doesn't return `5`, the test fails.

You don't need `#[test]`. You don't need a test module. The code block *is* the test.

## Writing Good Doc Tests

### Basic Examples

The simplest doc test shows how to call the function and what to expect.

```rust
/// Reverses a string.
///
/// ```
/// let reversed = my_crate::reverse("hello");
/// assert_eq!(reversed, "olleh");
/// ```
///
/// Works with empty strings too:
///
/// ```
/// let reversed = my_crate::reverse("");
/// assert_eq!(reversed, "");
/// ```
pub fn reverse(s: &str) -> String {
    s.chars().rev().collect()
}
```

Multiple code blocks = multiple tests. Each one runs independently.

### Hiding Boilerplate with `#`

Lines starting with `# ` are compiled but hidden from the rendered documentation. This keeps your docs clean while still providing the full context the compiler needs.

```rust
/// Parses a config file and returns the values.
///
/// ```
/// # use std::collections::HashMap;
/// # fn main() -> Result<(), Box<dyn std::error::Error>> {
/// let config = my_crate::parse_config("key=value\nname=rust")?;
/// assert_eq!(config.get("key"), Some(&"value".to_string()));
/// assert_eq!(config.get("name"), Some(&"rust".to_string()));
/// # Ok(())
/// # }
/// ```
pub fn parse_config(input: &str) -> Result<std::collections::HashMap<String, String>, Box<dyn std::error::Error>> {
    let mut map = std::collections::HashMap::new();
    for line in input.lines() {
        let parts: Vec<&str> = line.splitn(2, '=').collect();
        if parts.len() == 2 {
            map.insert(parts[0].to_string(), parts[1].to_string());
        }
    }
    Ok(map)
}
```

In the rendered docs, users see the clean version:

```rust
let config = my_crate::parse_config("key=value\nname=rust")?;
assert_eq!(config.get("key"), Some(&"value".to_string()));
assert_eq!(config.get("name"), Some(&"rust".to_string()));
```

But the test compiles with all the hidden imports and the `main` wrapper. Best of both worlds.

### Testing Error Cases

Use `should_panic` to document functions that panic under certain conditions:

```rust
/// Divides two numbers.
///
/// # Panics
///
/// Panics if `b` is zero.
///
/// ```
/// let result = my_crate::divide(10, 2);
/// assert_eq!(result, 5);
/// ```
///
/// ```should_panic
/// my_crate::divide(10, 0); // panics!
/// ```
pub fn divide(a: i32, b: i32) -> i32 {
    if b == 0 {
        panic!("division by zero");
    }
    a / b
}
```

The `should_panic` annotation on the code fence tells the test runner to expect a panic. If the function *doesn't* panic, the test fails.

### Compile-Only Tests

Sometimes you want to show that code compiles correctly without actually running it. Use `no_run`:

```rust
/// Connects to a database.
///
/// ```no_run
/// let conn = my_crate::connect("postgres://localhost/mydb").unwrap();
/// conn.execute("SELECT 1").unwrap();
/// ```
pub fn connect(url: &str) -> Result<Connection, DbError> {
    // ...
    # unimplemented!()
}
```

This verifies the example compiles but doesn't execute it — perfect for code that needs network access, file system changes, or other side effects you don't want in a test suite.

### Ignoring Code Blocks

For code that shouldn't be tested at all (pseudocode, other languages, shell commands), use `ignore`:

````rust
/// # Usage
///
/// ```ignore
/// $ cargo run -- --config myconfig.toml
/// ```
````

Or specify a different language:

````rust
/// # Example config file
///
/// ```toml
/// [database]
/// url = "postgres://localhost/mydb"
/// ```
````

Code blocks tagged with a language other than `rust` are automatically ignored by the test runner.

## Doc Tests for Structs and Modules

Doc tests aren't just for functions. You can — and should — add them to structs, enums, traits, and modules.

```rust
/// A bounded buffer that rejects elements when full.
///
/// ```
/// let mut buf = my_crate::BoundedBuffer::new(3);
/// assert!(buf.push(1).is_ok());
/// assert!(buf.push(2).is_ok());
/// assert!(buf.push(3).is_ok());
/// assert!(buf.push(4).is_err()); // full!
///
/// assert_eq!(buf.pop(), Some(1));
/// assert!(buf.push(4).is_ok()); // room again
/// ```
pub struct BoundedBuffer<T> {
    data: Vec<T>,
    capacity: usize,
}

#[derive(Debug)]
pub struct BufferFullError;

impl<T> BoundedBuffer<T> {
    /// Creates a new buffer with the given capacity.
    ///
    /// ```
    /// let buf = my_crate::BoundedBuffer::<i32>::new(10);
    /// assert_eq!(buf.len(), 0);
    /// assert!(buf.is_empty());
    /// ```
    pub fn new(capacity: usize) -> Self {
        BoundedBuffer {
            data: Vec::with_capacity(capacity),
            capacity,
        }
    }

    /// Pushes an element onto the buffer.
    ///
    /// Returns `Err(BufferFullError)` if the buffer is at capacity.
    ///
    /// ```
    /// let mut buf = my_crate::BoundedBuffer::new(2);
    /// buf.push(42).unwrap();
    /// assert_eq!(buf.len(), 1);
    /// ```
    pub fn push(&mut self, item: T) -> Result<(), BufferFullError> {
        if self.data.len() >= self.capacity {
            return Err(BufferFullError);
        }
        self.data.push(item);
        Ok(())
    }

    /// Removes and returns the first element, or `None` if empty.
    ///
    /// ```
    /// let mut buf = my_crate::BoundedBuffer::new(5);
    /// buf.push("hello").unwrap();
    /// assert_eq!(buf.pop(), Some("hello"));
    /// assert_eq!(buf.pop(), None);
    /// ```
    pub fn pop(&mut self) -> Option<T> {
        if self.data.is_empty() {
            None
        } else {
            Some(self.data.remove(0))
        }
    }

    pub fn len(&self) -> usize {
        self.data.len()
    }

    pub fn is_empty(&self) -> bool {
        self.data.is_empty()
    }
}
```

The struct-level doc test shows the complete workflow. The method-level doc tests show individual operations. Together, they form a tutorial that can't go stale.

## Running Doc Tests

```bash
# Run all tests including doc tests
cargo test

# Run ONLY doc tests
cargo test --doc

# Run doc tests for a specific function (by name)
cargo test --doc add
```

Doc tests are slower than unit tests because each one compiles as a separate binary. For large crates with hundreds of doc tests, this can add meaningful time to your CI. That's a trade-off I'm willing to make — accurate docs are worth the extra minutes.

## Doc Test Gotchas

**Crate naming.** In doc tests, you reference your crate by its name from `Cargo.toml`, not with `crate::` or `super::`. If your crate is called `my_crate`, the example uses `my_crate::function_name()`.

**Implicit main.** Each doc test is wrapped in `fn main() { ... }` unless you provide your own. If you need to return a `Result`, you need to write the `main` explicitly (and probably hide it with `#`).

**Edition matters.** Doc tests use the same edition as your crate. If you're on edition 2021, your doc tests get 2021 features.

**External dependencies.** Doc tests can use dependencies from your `Cargo.toml`, but not `dev-dependencies`. If your doc test needs `serde_json`, it must be a regular dependency, not just a dev one. This occasionally forces awkward decisions about what goes in your dependency list.

## The Documentation Culture

Here's my hot take: doc tests are the most important tests in your crate. Not because they're the most thorough — unit tests and integration tests handle that. But because they're the first thing a new user sees. When someone visits docs.rs and reads your API documentation, those examples are their first impression.

If the examples work, they trust your library. If the examples are wrong, they move on to the next crate. I've made that decision myself dozens of times.

Write doc tests for every public function. Show the common case and at least one edge case. Use hidden lines to keep the visible code clean. Run `cargo doc --open` regularly to see what your documentation actually looks like rendered.

## What's Next

We've covered the three built-in testing mechanisms: unit tests, integration tests, and doc tests. Starting next lesson, we'll get into the infrastructure side — how to set up test fixtures so you're not copy-pasting setup code across every test function.
