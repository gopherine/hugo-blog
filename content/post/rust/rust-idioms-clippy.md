---
title: "Lesson 23: Clippy Is Your Mentor — Listen to it"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 23
keywords: [Rust, rust, idiomatic, clippy, linting, code quality, best practices]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-05-18T10:55:00.000Z'
---

I'll say something controversial: Clippy taught me more about idiomatic Rust than any book. Not because it explains concepts — it doesn't. But because it catches you every time you write non-idiomatic code and shows you the better way. It's like having a senior Rust developer looking over your shoulder, constantly saying "there's a cleaner way to do that."

Run `cargo clippy` on every project. Every commit. No exceptions.

---

## What Clippy Is

Clippy is Rust's official linter — over 700 lint rules that catch everything from style issues to actual bugs. It ships with rustup, so you already have it.

```bash
# Run clippy
cargo clippy

# Run clippy and fail on warnings (for CI)
cargo clippy -- -D warnings

# Run clippy on all targets including tests
cargo clippy --all-targets --all-features -- -D warnings
```

---

## Clippy Catches Bugs

Not just style — actual bugs:

### Using `==` on floating-point numbers

```rust
fn main() {
    let x: f64 = 0.1 + 0.2;

    // Clippy warns: "strict comparison of `f64`"
    // BAD:
    if x == 0.3 {
        println!("equal");
    }

    // GOOD:
    if (x - 0.3).abs() < f64::EPSILON {
        println!("approximately equal");
    }
}
```

### Forgetting to use a `Result`

```rust
fn do_something() -> Result<(), String> {
    Ok(())
}

fn main() {
    // Clippy warns: "unused `Result` that must be used"
    do_something(); // BAD: ignoring the result

    let _ = do_something(); // GOOD: explicitly discarding
    do_something().unwrap(); // GOOD: handling it (even if crudely)
}
```

### Off-by-one with ranges

```rust
fn main() {
    let v = vec![1, 2, 3, 4, 5];

    // Clippy may warn about manual range patterns
    // BAD:
    for i in 0..v.len() {
        println!("{}", v[i]);
    }

    // GOOD:
    for item in &v {
        println!("{}", item);
    }
}
```

---

## Clippy Teaches Idiomatic Style

This is where Clippy really shines. It knows all the patterns from this course and enforces them:

### Use `if let` instead of single-arm match

```rust
fn process(value: Option<i32>) {
    // Clippy: "this match could be replaced by `if let`"
    // BAD:
    match value {
        Some(n) => println!("{}", n),
        None => {}
    }

    // GOOD:
    if let Some(n) = value {
        println!("{}", n);
    }
}
```

### Use `unwrap_or_else` instead of match

```rust
fn get_name(custom: Option<String>) -> String {
    // Clippy: "this match could be replaced by `unwrap_or_else`"
    // BAD:
    match custom {
        Some(n) => n,
        None => String::from("default"),
    }

    // GOOD:
    // custom.unwrap_or_else(|| String::from("default"))
}
```

### Use iterator methods instead of manual loops

```rust
fn count_positives(numbers: &[i32]) -> usize {
    // Clippy might suggest:
    // BAD:
    let mut count = 0;
    for &n in numbers {
        if n > 0 {
            count += 1;
        }
    }
    // GOOD:
    // numbers.iter().filter(|&&n| n > 0).count()
    count
}
```

### Take `&str` instead of `&String`

```rust
// Clippy: "writing `&String` instead of `&str` involves a new object"
// BAD:
fn greet(name: &String) {
    println!("Hello, {}", name);
}

// GOOD:
fn greet_better(name: &str) {
    println!("Hello, {}", name);
}

fn main() {
    let s = String::from("Atharva");
    greet(&s);
    greet_better(&s);
}
```

---

## My Favorite Clippy Lints

### `clippy::needless_return`

```rust
// BAD:
fn add(a: i32, b: i32) -> i32 {
    return a + b;
}

// GOOD: last expression is the return value
fn add_clean(a: i32, b: i32) -> i32 {
    a + b
}
```

### `clippy::manual_map`

```rust
fn double_if_present(x: Option<i32>) -> Option<i32> {
    // BAD:
    match x {
        Some(n) => Some(n * 2),
        None => None,
    }

    // GOOD:
    // x.map(|n| n * 2)
}
```

### `clippy::redundant_closure`

```rust
fn main() {
    let numbers = vec![1, 2, 3];

    // BAD: redundant closure
    let strings: Vec<String> = numbers.iter().map(|n| n.to_string()).collect();

    // GOOD: pass the method directly
    let strings: Vec<String> = numbers.iter().map(ToString::to_string).collect();

    println!("{:?}", strings);
}
```

### `clippy::or_fun_call`

```rust
fn main() {
    let x: Option<String> = None;

    // BAD: always constructs the default, even when Some
    let _val = x.clone().unwrap_or(String::from("default"));

    // GOOD: only constructs default when None
    let _val = x.unwrap_or_else(|| String::from("default"));
}
```

---

## Configuring Clippy

### Per-project configuration

Create a `clippy.toml` or `.clippy.toml` in your project root:

```toml
# Allow up to 7 function parameters (default is 7)
too-many-arguments-threshold = 7

# Set the cognitive complexity threshold
cognitive-complexity-threshold = 30

# Limit type complexity
type-complexity-threshold = 250
```

### Allowing specific lints

Sometimes Clippy is wrong (or you disagree). Suppress specific warnings:

```rust
#[allow(clippy::too_many_arguments)]
fn complex_function(a: i32, b: i32, c: i32, d: i32, e: i32, f: i32, g: i32, h: i32) -> i32 {
    a + b + c + d + e + f + g + h
}

// Or at the module level:
#![allow(clippy::module_name_repetitions)]
```

### Denying specific lints

Make certain lints hard errors:

```rust
// At the crate root (lib.rs or main.rs):
#![deny(clippy::unwrap_used)]        // No .unwrap() in production code
#![deny(clippy::expect_used)]        // No .expect() either
#![deny(clippy::panic)]              // No panic!() macro

// These are aggressive but great for library code
```

---

## Clippy Lint Categories

Clippy organizes lints into categories. The defaults are sensible, but you can opt into stricter ones:

| Category | Default | Description |
|----------|---------|-------------|
| `clippy::correctness` | Deny | Actual bugs |
| `clippy::suspicious` | Warn | Likely bugs |
| `clippy::style` | Warn | Style issues |
| `clippy::complexity` | Warn | Unnecessarily complex code |
| `clippy::perf` | Warn | Performance issues |
| `clippy::pedantic` | Allow | Stricter, opinionated lints |
| `clippy::nursery` | Allow | Experimental lints |
| `clippy::restriction` | Allow | Very strict, context-dependent |

For libraries, I recommend enabling pedantic:

```rust
#![warn(clippy::pedantic)]
#![allow(clippy::module_name_repetitions)]  // this one is too noisy
```

Pedantic catches things like missing docs on public items, non-ergonomic APIs, and subtle style issues.

---

## Clippy in CI

Add Clippy to your CI pipeline. Here's a GitHub Actions snippet:

```yaml
- name: Clippy
  run: cargo clippy --all-targets --all-features -- -D warnings
```

The `-D warnings` flag treats all warnings as errors, failing the build if Clippy finds issues. This prevents regressions — nobody can merge code with Clippy warnings.

---

## Fixing Clippy Warnings Automatically

Many Clippy lints have auto-fixes:

```bash
# Apply Clippy's suggested fixes automatically
cargo clippy --fix --allow-dirty
```

This modifies your source files in place. Review the changes before committing — auto-fixes are usually correct, but not always.

---

## The Clippy Learning Loop

Here's how I use Clippy as a learning tool:

1. Write code the way that feels natural.
2. Run `cargo clippy`.
3. Read the warning and the suggested fix.
4. Understand *why* the suggestion is better.
5. Apply the fix.
6. Next time, write it the better way from the start.

After a few weeks of this loop, you'll internalize most of the patterns. Clippy will find fewer and fewer issues in your code. That's not because you're suppressing warnings — it's because you've absorbed the idioms.

---

## Key Takeaways

- Run `cargo clippy` on every project, every commit. It catches bugs and teaches idioms.
- Clippy has 700+ lints covering correctness, style, complexity, and performance.
- Use `#[allow(clippy::lint_name)]` for specific suppressions. Don't blanket-suppress.
- Enable `clippy::pedantic` for libraries — it catches API design issues.
- Add `cargo clippy -- -D warnings` to your CI pipeline.
- Clippy is a teacher. Read its suggestions, understand them, internalize them. After a few weeks, you'll write idiomatic Rust by default.
