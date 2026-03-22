---
title: "Lesson 1: Unit Tests — #[test] and assertions"
author: Atharva Pandey
series: "Rust Testing Masterclass"
lesson: 1
keywords: [Rust, rust, testing, unit tests, assertions, test module, cargo test]
tags: [Rust tutorial, rust, testing]
date: '2024-08-10T14:22:00.000Z'
---

I shipped a Rust library last year that had zero tests. Not because I'm lazy — I was prototyping, moving fast, "I'll add tests later." You know how that story ends. A one-character typo in a boundary check sat in production for three weeks before someone's data got silently corrupted. Three weeks. I'd have caught it with one `assert_eq!` and thirty seconds of effort.

Never again. Here's how testing actually works in Rust, from the ground up.

## The Problem

Most languages bolt testing on as an afterthought. You need a separate framework, a test runner, a config file, and probably a twenty-minute detour into dependency hell before you write your first assertion. Rust's approach is different — testing is built directly into the language and toolchain. But that doesn't mean it's obvious how to use it well.

## Your First Test

Every Rust test is a function annotated with `#[test]`. That's it. No classes to inherit, no interfaces to implement.

```rust
fn add(a: i32, b: i32) -> i32 {
    a + b
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_add_positive_numbers() {
        assert_eq!(add(2, 3), 5);
    }

    #[test]
    fn test_add_negative_numbers() {
        assert_eq!(add(-1, -1), -2);
    }

    #[test]
    fn test_add_zero() {
        assert_eq!(add(0, 42), 42);
    }
}
```

Run it with `cargo test`. That's the entire workflow. No config file. No test runner installation. No XML manifesto about your project structure.

A few things worth noting:

- `#[cfg(test)]` means the module only compiles when running tests. Your production binary won't include any of this code.
- `use super::*` imports everything from the parent module — which lets tests access private functions. This is intentional. Unit tests in Rust are meant to live next to the code they test and have full access to internals.
- Each `#[test]` function runs independently, in its own thread by default.

## The Assertion Toolkit

Rust gives you three built-in assertion macros. You'll use all of them constantly.

### `assert!`

Checks that a boolean expression is true. Use it when you care about a condition, not a specific value.

```rust
#[cfg(test)]
mod tests {
    #[test]
    fn test_is_positive() {
        let value = 42;
        assert!(value > 0);
        assert!(value.is_positive());
    }
}
```

### `assert_eq!` and `assert_ne!`

These compare two values for equality or inequality. When they fail, they print both values — which is enormously helpful for debugging.

```rust
#[derive(Debug, PartialEq)]
struct Point {
    x: f64,
    y: f64,
}

impl Point {
    fn distance_from_origin(&self) -> f64 {
        (self.x * self.x + self.y * self.y).sqrt()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_distance() {
        let p = Point { x: 3.0, y: 4.0 };
        assert_eq!(p.distance_from_origin(), 5.0);
    }

    #[test]
    fn test_not_origin() {
        let p = Point { x: 1.0, y: 1.0 };
        assert_ne!(p, Point { x: 0.0, y: 0.0 });
    }
}
```

Important detail: `assert_eq!` requires the types to implement both `PartialEq` and `Debug`. The first for comparison, the second so it can print meaningful error messages. If you forget `#[derive(Debug, PartialEq)]`, the compiler will tell you — loudly.

### Custom Failure Messages

All three macros accept optional format strings. Use them. "assertion failed" tells you nothing at 2 AM.

```rust
#[cfg(test)]
mod tests {
    #[test]
    fn test_with_context() {
        let input = "hello world";
        let result = input.len();
        assert_eq!(
            result, 11,
            "Expected string '{}' to have length 11, got {}",
            input, result
        );
    }
}
```

## Testing for Panics

Sometimes the correct behavior *is* to panic. Rust handles this with `#[should_panic]`.

```rust
fn divide(a: i32, b: i32) -> i32 {
    if b == 0 {
        panic!("division by zero");
    }
    a / b
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[should_panic]
    fn test_divide_by_zero_panics() {
        divide(10, 0);
    }

    #[test]
    #[should_panic(expected = "division by zero")]
    fn test_divide_by_zero_message() {
        divide(10, 0);
    }
}
```

The `expected` parameter is a substring match against the panic message. Always use it when you can — a bare `#[should_panic]` will pass even if your function panics for the *wrong* reason. That's a false positive, which is worse than no test at all.

## Result-Based Tests

Since Rust 2018, test functions can return `Result<(), E>`. This is cleaner than panicking when you're testing fallible operations.

```rust
use std::num::ParseIntError;

fn parse_and_double(s: &str) -> Result<i32, ParseIntError> {
    let n: i32 = s.parse()?;
    Ok(n * 2)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_valid() -> Result<(), ParseIntError> {
        let result = parse_and_double("21")?;
        assert_eq!(result, 42);
        Ok(())
    }

    #[test]
    fn test_parse_invalid() {
        let result = parse_and_double("not_a_number");
        assert!(result.is_err());
    }
}
```

The `?` operator works inside test functions that return `Result`. If any step fails, the test fails with the error. This is way more readable than wrapping everything in `unwrap()` calls.

## Controlling Test Execution

`cargo test` has a bunch of flags you should know.

### Running Specific Tests

```bash
# Run only tests with "parse" in the name
cargo test parse

# Run a single specific test
cargo test test_parse_valid

# Run tests in a specific module
cargo test tests::test_parse
```

### Ignoring Slow Tests

Mark tests with `#[ignore]` and they'll be skipped by default.

```rust
#[cfg(test)]
mod tests {
    #[test]
    #[ignore]
    fn test_expensive_computation() {
        // This takes 30 seconds to run
        std::thread::sleep(std::time::Duration::from_secs(30));
        assert!(true);
    }
}
```

```bash
# Run only ignored tests
cargo test -- --ignored

# Run ALL tests including ignored
cargo test -- --include-ignored
```

I use `#[ignore]` for tests that hit external services, run expensive computations, or need specific hardware. In CI I run `--include-ignored`. Locally I skip them unless I'm specifically working on that feature.

### Output Capture

By default, `cargo test` swallows `println!` output from passing tests. You only see output from failures. To see everything:

```bash
cargo test -- --nocapture
```

### Test Threads

Tests run in parallel by default. If you have tests that share state (a file, a database, a global config), you can force serial execution:

```bash
cargo test -- --test-threads=1
```

But honestly, if you need this flag regularly, your tests have a design problem. We'll fix that in lesson 4 when we talk about fixtures.

## A Real-World Example

Here's a more complete example — a simple string tokenizer with proper tests.

```rust
#[derive(Debug, PartialEq)]
enum Token {
    Word(String),
    Number(i64),
    Whitespace,
}

fn tokenize(input: &str) -> Vec<Token> {
    let mut tokens = Vec::new();
    let mut chars = input.chars().peekable();

    while let Some(&ch) = chars.peek() {
        if ch.is_whitespace() {
            tokens.push(Token::Whitespace);
            while chars.peek().map_or(false, |c| c.is_whitespace()) {
                chars.next();
            }
        } else if ch.is_ascii_digit() {
            let mut num_str = String::new();
            while chars.peek().map_or(false, |c| c.is_ascii_digit()) {
                num_str.push(chars.next().unwrap());
            }
            tokens.push(Token::Number(num_str.parse().unwrap()));
        } else if ch.is_alphabetic() {
            let mut word = String::new();
            while chars.peek().map_or(false, |c| c.is_alphabetic()) {
                word.push(chars.next().unwrap());
            }
            tokens.push(Token::Word(word));
        } else {
            chars.next(); // skip unknown chars
        }
    }

    tokens
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_empty_input() {
        assert_eq!(tokenize(""), vec![]);
    }

    #[test]
    fn test_single_word() {
        assert_eq!(tokenize("hello"), vec![Token::Word("hello".into())]);
    }

    #[test]
    fn test_single_number() {
        assert_eq!(tokenize("42"), vec![Token::Number(42)]);
    }

    #[test]
    fn test_mixed_input() {
        let result = tokenize("hello 42 world");
        assert_eq!(
            result,
            vec![
                Token::Word("hello".into()),
                Token::Whitespace,
                Token::Number(42),
                Token::Whitespace,
                Token::Word("world".into()),
            ]
        );
    }

    #[test]
    fn test_consecutive_whitespace_collapsed() {
        let result = tokenize("a   b");
        assert_eq!(
            result,
            vec![
                Token::Word("a".into()),
                Token::Whitespace,
                Token::Word("b".into()),
            ]
        );
    }

    #[test]
    fn test_only_whitespace() {
        assert_eq!(tokenize("   "), vec![Token::Whitespace]);
    }

    #[test]
    fn test_unknown_chars_skipped() {
        let result = tokenize("a@b");
        assert_eq!(
            result,
            vec![Token::Word("a".into()), Token::Word("b".into())]
        );
    }
}
```

Notice the pattern: I test edge cases (empty, single element), the happy path (mixed input), and specific behaviors I want to guarantee (whitespace collapsing, unknown character handling). Every test has a descriptive name that tells me exactly what broke without reading the test body.

## Common Mistakes

**Testing implementation details instead of behavior.** If you rename a private helper function and ten tests break, you've coupled your tests to the implementation. Test what the function *does*, not how it does it.

**Giant test functions.** Each test should verify one thing. If a test has fifteen assertions, split it up. When it fails, you want to know *which* behavior is broken without reading a novel.

**No edge cases.** Empty inputs, zero values, maximum values, `None`, empty strings — these are where bugs hide. The happy path usually works. It's the boundaries that kill you.

**Missing `#[cfg(test)]` on the module.** Your test code will compile into your production binary. It won't run, but it bloats your binary and can even cause compilation issues if test-only dependencies aren't properly gated.

## What's Next

Unit tests are the foundation, but they only test individual functions in isolation. In the next lesson, we'll look at integration tests — testing your crate's public API from the outside, the way your users actually interact with your code.
