---
title: "Lesson 6: Property-Based Testing with proptest — Let the computer find your bugs"
author: Atharva Pandey
series: "Rust Testing Masterclass"
lesson: 6
keywords: [Rust, rust, testing, property-based testing, proptest, quickcheck, fuzzing, generative testing]
tags: [Rust tutorial, rust, testing]
date: '2024-08-22T15:40:00.000Z'
---

I wrote a URL parser once. Had fifty hand-crafted test cases. All green. Pushed to production. Within a week, a user sent a URL with a percent-encoded space followed by a Unicode character, and the parser crashed. I never would have thought to write that test case. A property-based test would have found it in under a second.

## The Problem

When you write tests by hand, you test the cases you think of. But bugs don't live in the cases you think of — they live in the ones you don't. Your test for `sort([3, 1, 2])` passes, but does your sort handle a list with ten million duplicates? A list of all negative numbers? An empty list? A list with `i32::MAX` and `i32::MIN` adjacent?

You can try to enumerate edge cases, but you'll always miss some. Your brain has blind spots. The computer doesn't.

Property-based testing flips the approach. Instead of specifying inputs and expected outputs, you specify *properties* that should hold for *any* input, and the framework generates hundreds or thousands of random inputs to try to break them.

## Getting Started with proptest

Add it to your project:

```toml
[dev-dependencies]
proptest = "1.4"
```

### Your First Property Test

```rust
fn reverse<T: Clone>(items: &[T]) -> Vec<T> {
    items.iter().rev().cloned().collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    proptest! {
        #[test]
        fn reversing_twice_gives_original(ref v in prop::collection::vec(any::<i32>(), 0..100)) {
            let reversed_twice = reverse(&reverse(v));
            prop_assert_eq!(&reversed_twice, v);
        }

        #[test]
        fn reverse_preserves_length(ref v in prop::collection::vec(any::<i32>(), 0..100)) {
            prop_assert_eq!(reverse(v).len(), v.len());
        }

        #[test]
        fn reverse_first_is_last(ref v in prop::collection::vec(any::<i32>(), 1..100)) {
            let reversed = reverse(v);
            prop_assert_eq!(reversed.first(), v.last());
        }
    }
}
```

Each test runs with hundreds of randomly generated vectors. If any input violates the property, proptest reports the *smallest* failing case — it automatically shrinks the input to find the minimal reproduction.

That shrinking is enormously valuable. Instead of "your function fails with this 87-element vector," you get "your function fails with `[0, -1]`." Much easier to debug.

### What's a Property?

A property is an invariant — something that should be true regardless of input. Finding good properties takes practice, but here are the common patterns:

**Round-trip:** Encoding then decoding gives back the original.

```rust
use proptest::prelude::*;

fn encode(s: &str) -> Vec<u8> {
    s.as_bytes().to_vec()
}

fn decode(bytes: &[u8]) -> Result<String, std::string::FromUtf8Error> {
    String::from_utf8(bytes.to_vec())
}

proptest! {
    #[test]
    fn encode_decode_roundtrip(s in "\\PC*") {
        let encoded = encode(&s);
        let decoded = decode(&encoded).unwrap();
        prop_assert_eq!(decoded, s);
    }
}
```

**Idempotence:** Doing it twice is the same as doing it once.

```rust
use proptest::prelude::*;

fn normalize_whitespace(s: &str) -> String {
    s.split_whitespace().collect::<Vec<_>>().join(" ")
}

proptest! {
    #[test]
    fn normalize_is_idempotent(s in "\\PC{0,200}") {
        let once = normalize_whitespace(&s);
        let twice = normalize_whitespace(&once);
        prop_assert_eq!(once, twice);
    }
}
```

**Invariant preservation:** An operation maintains some structural property.

```rust
use proptest::prelude::*;

fn sort(v: &mut Vec<i32>) {
    v.sort();
}

proptest! {
    #[test]
    fn sort_preserves_length(mut v in prop::collection::vec(any::<i32>(), 0..100)) {
        let original_len = v.len();
        sort(&mut v);
        prop_assert_eq!(v.len(), original_len);
    }

    #[test]
    fn sort_is_ordered(mut v in prop::collection::vec(any::<i32>(), 0..100)) {
        sort(&mut v);
        for window in v.windows(2) {
            prop_assert!(window[0] <= window[1]);
        }
    }

    #[test]
    fn sort_preserves_elements(mut v in prop::collection::vec(any::<i32>(), 0..100)) {
        let mut original = v.clone();
        sort(&mut v);
        original.sort(); // use known-good sort for comparison
        prop_assert_eq!(v, original);
    }
}
```

**Oracle testing:** Compare against a known-good (but maybe slow) implementation.

```rust
use proptest::prelude::*;

fn fast_max(v: &[i32]) -> Option<i32> {
    // Our optimized implementation
    if v.is_empty() {
        return None;
    }
    let mut max = v[0];
    for &x in &v[1..] {
        if x > max {
            max = x;
        }
    }
    Some(max)
}

proptest! {
    #[test]
    fn fast_max_matches_stdlib(ref v in prop::collection::vec(any::<i32>(), 1..1000)) {
        let expected = v.iter().copied().max();
        let actual = fast_max(v);
        prop_assert_eq!(actual, expected);
    }
}
```

## Custom Strategies

proptest's power comes from its strategy system. A strategy defines how to generate random values of a type.

### Basic Strategies

```rust
use proptest::prelude::*;

proptest! {
    // Integer ranges
    #[test]
    fn test_positive_numbers(n in 1..1000i32) {
        prop_assert!(n > 0);
        prop_assert!(n < 1000);
    }

    // String patterns (regex)
    #[test]
    fn test_email_like(s in "[a-z]{1,10}@[a-z]{1,10}\\.[a-z]{2,4}") {
        prop_assert!(s.contains('@'));
        prop_assert!(s.contains('.'));
    }

    // Optional values
    #[test]
    fn test_optional(opt in prop::option::of(1..100i32)) {
        if let Some(n) = opt {
            prop_assert!(n >= 1);
            prop_assert!(n < 100);
        }
    }
}
```

### Composing Strategies

Build complex types from simple strategies:

```rust
use proptest::prelude::*;

#[derive(Debug, Clone, PartialEq)]
struct Rectangle {
    width: f64,
    height: f64,
}

impl Rectangle {
    fn area(&self) -> f64 {
        self.width * self.height
    }

    fn perimeter(&self) -> f64 {
        2.0 * (self.width + self.height)
    }

    fn is_square(&self) -> bool {
        (self.width - self.height).abs() < f64::EPSILON
    }
}

fn rectangle_strategy() -> impl Strategy<Value = Rectangle> {
    (0.1f64..1000.0, 0.1f64..1000.0).prop_map(|(w, h)| Rectangle {
        width: w,
        height: h,
    })
}

fn square_strategy() -> impl Strategy<Value = Rectangle> {
    (0.1f64..1000.0).prop_map(|side| Rectangle {
        width: side,
        height: side,
    })
}

proptest! {
    #[test]
    fn area_is_positive(rect in rectangle_strategy()) {
        prop_assert!(rect.area() > 0.0);
    }

    #[test]
    fn perimeter_gt_zero(rect in rectangle_strategy()) {
        prop_assert!(rect.perimeter() > 0.0);
    }

    #[test]
    fn squares_detected(rect in square_strategy()) {
        prop_assert!(rect.is_square());
    }

    #[test]
    fn area_less_than_perimeter_squared(rect in rectangle_strategy()) {
        // Area <= (perimeter/4)^2 by AM-GM inequality
        let bound = (rect.perimeter() / 4.0).powi(2);
        prop_assert!(rect.area() <= bound + 0.001); // small epsilon for float
    }
}
```

### Filtering Strategies

Use `prop_filter` to restrict generated values:

```rust
use proptest::prelude::*;

fn divide(a: i32, b: i32) -> i32 {
    a / b
}

proptest! {
    #[test]
    fn divide_by_nonzero(
        a in any::<i32>(),
        b in any::<i32>().prop_filter("non-zero", |b| *b != 0)
    ) {
        // Won't panic because b is never 0
        let _ = divide(a, b);
    }
}
```

Be careful with filters — if too few values pass the filter, proptest will give up. Use `prop_flat_map` for dependent generation instead.

### Dependent Generation

When one value depends on another:

```rust
use proptest::prelude::*;

fn get_element(v: &[i32], index: usize) -> i32 {
    v[index]
}

proptest! {
    #[test]
    fn valid_indexing(
        v in prop::collection::vec(any::<i32>(), 1..50)
            .prop_flat_map(|v| {
                let len = v.len();
                (Just(v), 0..len)
            })
    ) {
        let (vec, idx) = v;
        // idx is always valid for vec
        let _ = get_element(&vec, idx);
    }
}
```

## A Real-World Example: Testing a JSON-like Parser

```rust
use proptest::prelude::*;

#[derive(Debug, Clone, PartialEq)]
enum Value {
    Null,
    Bool(bool),
    Int(i64),
    Str(String),
    Array(Vec<Value>),
}

fn serialize(value: &Value) -> String {
    match value {
        Value::Null => "null".to_string(),
        Value::Bool(b) => b.to_string(),
        Value::Int(n) => n.to_string(),
        Value::Str(s) => format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\"")),
        Value::Array(items) => {
            let inner: Vec<String> = items.iter().map(serialize).collect();
            format!("[{}]", inner.join(","))
        }
    }
}

fn parse(input: &str) -> Result<Value, String> {
    let input = input.trim();
    if input == "null" {
        Ok(Value::Null)
    } else if input == "true" {
        Ok(Value::Bool(true))
    } else if input == "false" {
        Ok(Value::Bool(false))
    } else if input.starts_with('"') && input.ends_with('"') {
        let inner = &input[1..input.len() - 1];
        let unescaped = inner.replace("\\\"", "\"").replace("\\\\", "\\");
        Ok(Value::Str(unescaped))
    } else if let Ok(n) = input.parse::<i64>() {
        Ok(Value::Int(n))
    } else if input.starts_with('[') && input.ends_with(']') {
        let inner = &input[1..input.len() - 1];
        if inner.trim().is_empty() {
            return Ok(Value::Array(vec![]));
        }
        // Simplified: only handles non-nested arrays
        let items: Result<Vec<Value>, String> = inner.split(',').map(|s| parse(s.trim())).collect();
        Ok(Value::Array(items?))
    } else {
        Err(format!("cannot parse: {}", input))
    }
}

fn value_strategy() -> impl Strategy<Value = Value> {
    prop_oneof![
        Just(Value::Null),
        any::<bool>().prop_map(Value::Bool),
        any::<i64>().prop_map(Value::Int),
        "[a-zA-Z0-9 ]{0,20}".prop_map(|s| Value::Str(s)),
    ]
}

fn array_strategy() -> impl Strategy<Value = Value> {
    prop::collection::vec(value_strategy(), 0..5).prop_map(Value::Array)
}

proptest! {
    #[test]
    fn serialize_parse_roundtrip_scalars(value in value_strategy()) {
        let serialized = serialize(&value);
        let parsed = parse(&serialized).unwrap();
        prop_assert_eq!(parsed, value);
    }

    #[test]
    fn serialize_parse_roundtrip_arrays(value in array_strategy()) {
        let serialized = serialize(&value);
        let parsed = parse(&serialized).unwrap();
        prop_assert_eq!(parsed, value);
    }

    #[test]
    fn null_serializes_correctly(_dummy in 0..1i32) {
        prop_assert_eq!(serialize(&Value::Null), "null");
    }
}
```

Notice how the round-trip test immediately exercises thousands of edge cases I'd never think to write by hand. Different string contents, negative numbers, empty arrays, mixed-type arrays — all generated automatically.

## Configuring Test Runs

Control how many cases proptest generates:

```rust
use proptest::prelude::*;

proptest! {
    // Override case count for this block
    #![proptest_config(ProptestConfig::with_cases(10000))]

    #[test]
    fn thorough_test(x in any::<i32>()) {
        // Runs 10,000 times instead of the default 256
        prop_assert!(x.wrapping_add(1) != x || x == i32::MAX);
    }
}
```

You can also create a `proptest.toml` in your project root for global settings, or set `PROPTEST_CASES=5000` as an environment variable.

## When Property-Based Tests Shine

- **Serialization/deserialization** — Round-trip tests are the killer app.
- **Data structure invariants** — Trees stay balanced, sets have no duplicates, queues maintain FIFO.
- **Numeric algorithms** — Overflow, underflow, NaN handling, precision loss.
- **Parsers** — Valid input parses successfully, round-trips work, invalid input produces errors (not panics).
- **Comparators and sorting** — Transitivity, reflexivity, antisymmetry.

They're less useful for testing specific business rules ("a user under 18 can't buy alcohol") where the property *is* the implementation.

## What's Next

Property-based testing generates structured random data guided by strategies. Fuzz testing takes this further — feeding completely random, mutated bytes into your code to find crashes, hangs, and security issues. It's a different beast entirely, and it's what we'll tackle next with `cargo-fuzz`.
