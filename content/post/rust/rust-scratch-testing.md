---
title: "Lesson 23: Writing Your First Tests — #[test] and assert!"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 23
keywords: [Rust, rust, testing, unit tests, test, assert, assert_eq, test module, TDD, integration tests]
tags: [Rust tutorial, rust, beginner]
date: '2024-04-14T11:00:00.000Z'
---

I have a rule: I won't merge code without tests. Not because I'm a purist — because I've been burned too many times. Rust makes testing so frictionless that there's no excuse to skip it. Tests live in the same file as your code. They run with one command. The tooling just works.

## Your First Test

```rust
fn add(a: i32, b: i32) -> i32 {
    a + b
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_add() {
        assert_eq!(add(2, 3), 5);
    }

    #[test]
    fn test_add_negative() {
        assert_eq!(add(-1, 1), 0);
    }

    #[test]
    fn test_add_zero() {
        assert_eq!(add(0, 0), 0);
    }
}
```

Run with `cargo test`:

```
running 3 tests
test tests::test_add ... ok
test tests::test_add_negative ... ok
test tests::test_add_zero ... ok

test result: ok. 3 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
```

That's it. No test framework to install. No configuration files. No test runner to set up. `#[test]` marks a function as a test, `#[cfg(test)]` ensures the test module is only compiled during testing, and `use super::*` imports everything from the parent module.

## Assert Macros

Rust provides three main assertion macros:

```rust
#[cfg(test)]
mod tests {
    #[test]
    fn test_assert() {
        let x = 5;
        assert!(x > 0);              // assert a condition is true
        assert!(x > 0, "x should be positive, got {x}");  // with message
    }

    #[test]
    fn test_assert_eq() {
        let result = 2 + 2;
        assert_eq!(result, 4);       // assert two values are equal
        assert_eq!(result, 4, "math is broken: {} != 4", result);
    }

    #[test]
    fn test_assert_ne() {
        let result = 2 + 2;
        assert_ne!(result, 5);       // assert two values are NOT equal
    }
}
```

When `assert_eq!` fails, it prints both values — incredibly helpful for debugging:

```
assertion `left == right` failed
  left: 4
  right: 5
```

## Testing for Panics

Some code *should* panic under certain conditions. Test that with `#[should_panic]`:

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
    fn test_divide() {
        assert_eq!(divide(10, 2), 5);
    }

    #[test]
    #[should_panic]
    fn test_divide_by_zero_panics() {
        divide(10, 0);
    }

    #[test]
    #[should_panic(expected = "division by zero")]
    fn test_divide_by_zero_message() {
        divide(10, 0);  // must panic with message containing "division by zero"
    }
}
```

The `expected` parameter checks that the panic message contains the given string. Use it — a test that just says "something panicked" isn't very useful.

## Testing with Result

Tests can return `Result` instead of panicking:

```rust
#[cfg(test)]
mod tests {
    #[test]
    fn test_parse() -> Result<(), Box<dyn std::error::Error>> {
        let number: i32 = "42".parse()?;
        assert_eq!(number, 42);
        Ok(())
    }
}
```

This lets you use `?` in tests. If any operation returns `Err`, the test fails. Cleaner than `.unwrap()` everywhere.

## Ignoring Tests

Mark slow or temporarily broken tests with `#[ignore]`:

```rust
#[cfg(test)]
mod tests {
    #[test]
    #[ignore]
    fn expensive_test() {
        // This test takes a long time
        std::thread::sleep(std::time::Duration::from_secs(10));
        assert!(true);
    }
}
```

Ignored tests don't run with `cargo test`. Run them explicitly with `cargo test -- --ignored`. Run everything (including ignored) with `cargo test -- --include-ignored`.

## Filtering Tests

Run specific tests:

```rust
// cargo test test_add          — runs all tests containing "test_add"
// cargo test tests::test_add   — fully qualified name
// cargo test -- --test-threads=1  — run tests sequentially (not in parallel)
```

## Test Organization

### Unit Tests — Same File

The convention: put unit tests in a `#[cfg(test)]` module at the bottom of the file being tested.

```rust
// src/math.rs

pub fn factorial(n: u64) -> u64 {
    match n {
        0 | 1 => 1,
        _ => n * factorial(n - 1),
    }
}

pub fn is_prime(n: u64) -> bool {
    if n < 2 {
        return false;
    }
    if n == 2 {
        return true;
    }
    if n % 2 == 0 {
        return false;
    }
    let mut i = 3;
    while i * i <= n {
        if n % i == 0 {
            return false;
        }
        i += 2;
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_factorial_base_cases() {
        assert_eq!(factorial(0), 1);
        assert_eq!(factorial(1), 1);
    }

    #[test]
    fn test_factorial() {
        assert_eq!(factorial(5), 120);
        assert_eq!(factorial(10), 3_628_800);
    }

    #[test]
    fn test_is_prime() {
        assert!(!is_prime(0));
        assert!(!is_prime(1));
        assert!(is_prime(2));
        assert!(is_prime(3));
        assert!(!is_prime(4));
        assert!(is_prime(5));
        assert!(is_prime(97));
        assert!(!is_prime(100));
    }

    // Unit tests can access private functions!
    // (The test module is a child of the module being tested)
}
```

A major advantage of Rust's test organization: unit tests can access private functions. The test module is a child module, so it can see everything in the parent. This makes testing internals easy without exposing them publicly.

### Integration Tests — tests/ Directory

Integration tests live in a `tests/` directory at the project root:

```
my_project/
├── Cargo.toml
├── src/
│   └── lib.rs
└── tests/
    └── integration_test.rs
```

`tests/integration_test.rs`:
```rust
// Integration tests can only use your crate's public API
use my_project::factorial;

#[test]
fn test_factorial_integration() {
    assert_eq!(factorial(20), 2_432_902_008_176_640_000);
}
```

Integration tests treat your crate as an external user would. They can only access public API. No `#[cfg(test)]` needed — everything in `tests/` is a test.

## Test Helpers

Avoid code duplication in tests:

```rust
#[derive(Debug, PartialEq)]
struct User {
    name: String,
    age: u32,
}

impl User {
    fn new(name: &str, age: u32) -> Result<Self, String> {
        if name.is_empty() {
            return Err(String::from("name cannot be empty"));
        }
        if age > 150 {
            return Err(String::from("unrealistic age"));
        }
        Ok(User {
            name: name.to_string(),
            age,
        })
    }

    fn is_adult(&self) -> bool {
        self.age >= 18
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Helper function — not marked with #[test]
    fn make_test_user(name: &str, age: u32) -> User {
        User::new(name, age).expect("test user should be valid")
    }

    #[test]
    fn test_user_creation() {
        let user = make_test_user("Alice", 30);
        assert_eq!(user.name, "Alice");
        assert_eq!(user.age, 30);
    }

    #[test]
    fn test_empty_name_fails() {
        let result = User::new("", 30);
        assert!(result.is_err());
        assert_eq!(result.unwrap_err(), "name cannot be empty");
    }

    #[test]
    fn test_unrealistic_age_fails() {
        let result = User::new("Alice", 200);
        assert!(result.is_err());
    }

    #[test]
    fn test_is_adult() {
        assert!(make_test_user("Alice", 18).is_adult());
        assert!(make_test_user("Bob", 30).is_adult());
        assert!(!make_test_user("Charlie", 17).is_adult());
        assert!(!make_test_user("Diana", 0).is_adult());
    }
}
```

## Table-Driven Tests

When you have many similar test cases, use a data-driven approach:

```rust
fn fizzbuzz(n: u32) -> String {
    match (n % 3, n % 5) {
        (0, 0) => String::from("FizzBuzz"),
        (0, _) => String::from("Fizz"),
        (_, 0) => String::from("Buzz"),
        _ => n.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_fizzbuzz() {
        let cases = vec![
            (1, "1"),
            (2, "2"),
            (3, "Fizz"),
            (4, "4"),
            (5, "Buzz"),
            (6, "Fizz"),
            (10, "Buzz"),
            (15, "FizzBuzz"),
            (30, "FizzBuzz"),
        ];

        for (input, expected) in cases {
            assert_eq!(
                fizzbuzz(input), expected,
                "fizzbuzz({input}) should be {expected}"
            );
        }
    }
}
```

The custom message in `assert_eq!` is crucial for table-driven tests. Without it, a failure just says "4 != Fizz" with no indication of which input caused it.

## Doc Tests

Code examples in documentation comments are compiled and run as tests:

```rust
/// Reverses a string.
///
/// # Examples
///
/// ```
/// let reversed = my_project::reverse("hello");
/// assert_eq!(reversed, "olleh");
/// ```
///
/// Empty strings return empty:
///
/// ```
/// assert_eq!(my_project::reverse(""), "");
/// ```
pub fn reverse(s: &str) -> String {
    s.chars().rev().collect()
}
```

`cargo test` runs these examples. Your documentation is always correct because the compiler enforces it. I cannot overstate how valuable this is — stale examples are one of the worst forms of documentation rot.

## Test Output

By default, `cargo test` captures stdout from passing tests. To see output:

```rust
// cargo test -- --nocapture
```

Or use `eprintln!` — stderr is not captured:

```rust
#[cfg(test)]
mod tests {
    #[test]
    fn test_with_output() {
        eprintln!("This always prints, even if the test passes");
        assert!(true);
    }
}
```

## A Practical Example: Testing a Stack

```rust
#[derive(Debug)]
struct Stack<T> {
    elements: Vec<T>,
}

impl<T> Stack<T> {
    fn new() -> Self {
        Stack { elements: Vec::new() }
    }

    fn push(&mut self, item: T) {
        self.elements.push(item);
    }

    fn pop(&mut self) -> Option<T> {
        self.elements.pop()
    }

    fn peek(&self) -> Option<&T> {
        self.elements.last()
    }

    fn is_empty(&self) -> bool {
        self.elements.is_empty()
    }

    fn len(&self) -> usize {
        self.elements.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_new_stack_is_empty() {
        let stack: Stack<i32> = Stack::new();
        assert!(stack.is_empty());
        assert_eq!(stack.len(), 0);
    }

    #[test]
    fn test_push_and_pop() {
        let mut stack = Stack::new();
        stack.push(1);
        stack.push(2);
        stack.push(3);

        assert_eq!(stack.pop(), Some(3));
        assert_eq!(stack.pop(), Some(2));
        assert_eq!(stack.pop(), Some(1));
        assert_eq!(stack.pop(), None);
    }

    #[test]
    fn test_peek() {
        let mut stack = Stack::new();
        assert_eq!(stack.peek(), None);

        stack.push(42);
        assert_eq!(stack.peek(), Some(&42));
        assert_eq!(stack.len(), 1);  // peek doesn't remove
    }

    #[test]
    fn test_len() {
        let mut stack = Stack::new();
        assert_eq!(stack.len(), 0);

        stack.push("a");
        assert_eq!(stack.len(), 1);

        stack.push("b");
        assert_eq!(stack.len(), 2);

        stack.pop();
        assert_eq!(stack.len(), 1);
    }

    #[test]
    fn test_with_strings() {
        let mut stack = Stack::new();
        stack.push(String::from("hello"));
        stack.push(String::from("world"));

        assert_eq!(stack.pop(), Some(String::from("world")));
        assert_eq!(stack.pop(), Some(String::from("hello")));
    }
}
```

## My Testing Philosophy

1. **Test behavior, not implementation.** Your tests should verify what a function *does*, not how it does it internally. This way, you can refactor without breaking tests.
2. **One assertion per test is aspirational, not mandatory.** Logical grouping is fine. Testing `push` followed by `pop` in one test is clearer than splitting them.
3. **Test edge cases.** Empty inputs, zero, negative numbers, maximum values. Bugs hide at boundaries.
4. **Name tests descriptively.** `test_empty_input_returns_none` is better than `test1`.
5. **Run `cargo test` before every commit.** No exceptions.

Next: file I/O — reading and writing files in Rust.
