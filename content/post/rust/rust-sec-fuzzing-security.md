---
title: "Lesson 8: Security Fuzzing — Finding vulnerabilities before attackers do"
author: Atharva Pandey
series: "Rust Security in Production"
lesson: 8
keywords: [Rust, rust, security, fuzzing, cargo-fuzz, AFL, libFuzzer, property testing, vulnerability discovery]
tags: [Rust tutorial, rust, security]
date: '2025-05-18T13:29:00.000Z'
---

I found a panic in a production parser by accident last year. A user in Japan sent a request with a multi-byte UTF-8 character right at a boundary where our code was slicing a string by byte index. It worked fine for ASCII. It worked fine for most Unicode. But this particular combination of character and position triggered an index-out-of-bounds panic that crashed the request handler.

I fixed the bug in ten minutes. What bothered me was that we'd had unit tests, integration tests, and even some property-based tests — and none of them caught it. The input space was too large. The edge case was too specific. A human writing test cases would never think to put a 3-byte UTF-8 character at exactly that offset.

A fuzzer would have found it in about thirty seconds.

## What Fuzzing Actually Is

Fuzzing is automated testing with randomly generated inputs. But modern fuzzers aren't just throwing random bytes at your code — they're using coverage-guided feedback to evolve inputs that explore new code paths. When a mutated input triggers a new branch, the fuzzer remembers it and builds on it. Over time, fuzzers explore deep, complex code paths that humans would never think to test.

For security, fuzzing is absurdly effective. It finds:

- Panics from unwrap/expect on unexpected input
- Integer overflows and underflows
- Infinite loops from malicious input (DoS)
- Logic bugs in parsers
- Memory issues in `unsafe` code (with sanitizers)
- Malformed data that passes validation but breaks downstream

## cargo-fuzz — The Standard Tool

`cargo-fuzz` is the official Rust fuzzing tool. It wraps LLVM's libFuzzer and integrates with Cargo.

### Setup

```bash
# Install cargo-fuzz
cargo install cargo-fuzz

# Initialize fuzzing in your project
cargo fuzz init

# This creates:
# fuzz/
#   Cargo.toml
#   fuzz_targets/
#     fuzz_target_1.rs
```

### Your First Fuzz Target

Let's say you have a parser:

```rust
// src/lib.rs

/// Parse a simple key-value protocol:
/// FORMAT: <key_len:u16><key><value_len:u32><value>
pub fn parse_kv(data: &[u8]) -> Result<(String, Vec<u8>), ParseError> {
    if data.len() < 2 {
        return Err(ParseError::TooShort);
    }

    let key_len = u16::from_be_bytes([data[0], data[1]]) as usize;
    if data.len() < 2 + key_len + 4 {
        return Err(ParseError::TooShort);
    }

    let key = std::str::from_utf8(&data[2..2 + key_len])
        .map_err(|_| ParseError::InvalidUtf8)?
        .to_string();

    let value_offset = 2 + key_len;
    let value_len = u32::from_be_bytes([
        data[value_offset],
        data[value_offset + 1],
        data[value_offset + 2],
        data[value_offset + 3],
    ]) as usize;

    let value_start = value_offset + 4;
    if data.len() < value_start + value_len {
        return Err(ParseError::TooShort);
    }

    let value = data[value_start..value_start + value_len].to_vec();
    Ok((key, value))
}

#[derive(Debug)]
pub enum ParseError {
    TooShort,
    InvalidUtf8,
}
```

Now write a fuzz target:

```rust
// fuzz/fuzz_targets/fuzz_parse_kv.rs
#![no_main]

use libfuzzer_sys::fuzz_target;
use myapp::parse_kv;

fuzz_target!(|data: &[u8]| {
    // The fuzzer will call this with millions of different byte slices.
    // We just need to make sure it doesn't panic.
    let _ = parse_kv(data);
});
```

### Running the Fuzzer

```bash
# Run the fuzzer
cargo fuzz run fuzz_parse_kv

# Run with a time limit (useful for CI)
cargo fuzz run fuzz_parse_kv -- -max_total_time=300

# Run with a max input length
cargo fuzz run fuzz_parse_kv -- -max_len=1024

# Run with address sanitizer (catches memory bugs in unsafe code)
RUSTFLAGS="-Zsanitizer=address" cargo fuzz run fuzz_parse_kv

# Show coverage information
cargo fuzz coverage fuzz_parse_kv
```

When the fuzzer finds a crash, it saves the input to `fuzz/artifacts/fuzz_parse_kv/`. You can reproduce it:

```bash
# Reproduce a crash
cargo fuzz run fuzz_parse_kv fuzz/artifacts/fuzz_parse_kv/crash-abc123...
```

### Writing Effective Fuzz Targets

The basic "just call the function" approach works, but you can do better.

**Structured fuzzing with `arbitrary`:**

Instead of feeding raw bytes, derive structured inputs:

```toml
# fuzz/Cargo.toml
[dependencies]
libfuzzer-sys = "0.11"
arbitrary = { version = "1", features = ["derive"] }
```

```rust
// fuzz/fuzz_targets/fuzz_structured.rs
#![no_main]

use arbitrary::Arbitrary;
use libfuzzer_sys::fuzz_target;

#[derive(Debug, Arbitrary)]
struct FuzzInput {
    key: String,
    value: Vec<u8>,
    operation: Operation,
}

#[derive(Debug, Arbitrary)]
enum Operation {
    Insert,
    Delete,
    Lookup,
    Update { new_value: Vec<u8> },
}

fuzz_target!(|input: FuzzInput| {
    let mut store = myapp::KeyValueStore::new();

    match input.operation {
        Operation::Insert => {
            let _ = store.insert(&input.key, &input.value);
        }
        Operation::Delete => {
            let _ = store.delete(&input.key);
        }
        Operation::Lookup => {
            let _ = store.get(&input.key);
        }
        Operation::Update { ref new_value } => {
            let _ = store.insert(&input.key, &input.value);
            let _ = store.update(&input.key, new_value);
        }
    }
});
```

The `arbitrary` crate converts the fuzzer's raw bytes into structured Rust types. The fuzzer still does its coverage-guided mutation, but now it's exploring meaningful operation sequences instead of random byte patterns.

**Differential fuzzing — compare two implementations:**

```rust
#![no_main]

use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    // Compare your implementation against a reference
    let our_result = myapp::parse_json(data);
    let reference_result = serde_json::from_slice::<serde_json::Value>(data);

    match (our_result, reference_result) {
        (Ok(ours), Ok(theirs)) => {
            // Both succeeded — results should match
            assert_eq!(
                format!("{:?}", ours),
                format!("{:?}", theirs),
                "Divergent results for input: {:?}",
                data
            );
        }
        (Err(_), Err(_)) => {
            // Both failed — that's fine
        }
        (Ok(_), Err(_)) => {
            // We accepted something the reference rejected.
            // Maybe a bug, maybe we're more permissive.
            // Log it for review.
        }
        (Err(_), Ok(_)) => {
            // We rejected something the reference accepted.
            // Probably a bug in our parser.
            panic!("Our parser rejected valid input: {:?}", data);
        }
    }
});
```

## Fuzzing for Specific Vulnerability Classes

### Denial of Service — Algorithmic Complexity

Some inputs can cause quadratic or exponential behavior. Fuzzing with timeouts catches this:

```rust
#![no_main]

use libfuzzer_sys::fuzz_target;
use std::time::{Duration, Instant};

fuzz_target!(|data: &[u8]| {
    let start = Instant::now();

    let _ = myapp::process_request(data);

    // If processing takes more than 100ms for a small input,
    // we probably have an algorithmic complexity issue
    let elapsed = start.elapsed();
    if elapsed > Duration::from_millis(100) && data.len() < 1024 {
        panic!(
            "Possible DoS: {} bytes took {:?} to process",
            data.len(),
            elapsed
        );
    }
});
```

Run with a per-execution timeout:

```bash
cargo fuzz run fuzz_dos -- -timeout=5
```

### Integer Overflow

```rust
#![no_main]

use libfuzzer_sys::fuzz_target;
use arbitrary::Arbitrary;

#[derive(Debug, Arbitrary)]
struct MathInput {
    a: u32,
    b: u32,
    op: MathOp,
}

#[derive(Debug, Arbitrary)]
enum MathOp {
    Add,
    Mul,
    Sub,
    Div,
}

fuzz_target!(|input: MathInput| {
    // This will find integer overflow, division by zero, etc.
    let result = match input.op {
        MathOp::Add => input.a.checked_add(input.b),
        MathOp::Mul => input.a.checked_mul(input.b),
        MathOp::Sub => input.a.checked_sub(input.b),
        MathOp::Div => {
            if input.b == 0 {
                None
            } else {
                Some(input.a / input.b)
            }
        }
    };

    // The function under test should handle all cases without panicking
    let _ = myapp::calculate(input.a, input.b, match input.op {
        MathOp::Add => "+",
        MathOp::Mul => "*",
        MathOp::Sub => "-",
        MathOp::Div => "/",
    });
});
```

### Unsafe Code Memory Bugs

For crates with `unsafe` code, fuzzing with sanitizers is essential:

```bash
# Address sanitizer — catches buffer overflows, use-after-free
RUSTFLAGS="-Zsanitizer=address" cargo +nightly fuzz run fuzz_target

# Memory sanitizer — catches uninitialized memory reads
RUSTFLAGS="-Zsanitizer=memory" cargo +nightly fuzz run fuzz_target

# Thread sanitizer — catches data races
RUSTFLAGS="-Zsanitizer=thread" cargo +nightly fuzz run fuzz_target
```

These sanitizers add significant overhead but catch bugs that normal fuzzing misses.

## Seed Corpora — Help the Fuzzer Start Smart

Fuzzers work better when they have good starting inputs. Create a corpus of known-valid inputs:

```bash
# Create a seed corpus directory
mkdir -p fuzz/corpus/fuzz_parse_kv

# Add some valid inputs
echo -ne '\x00\x03foo\x00\x00\x00\x03bar' > fuzz/corpus/fuzz_parse_kv/valid_basic
echo -ne '\x00\x00\x00\x00\x00\x00' > fuzz/corpus/fuzz_parse_kv/empty_key_empty_value
```

Or generate seed inputs programmatically:

```rust
// scripts/generate_corpus.rs
use std::fs;
use std::io::Write;

fn main() {
    let corpus_dir = "fuzz/corpus/fuzz_parse_kv";
    fs::create_dir_all(corpus_dir).unwrap();

    // Generate various valid inputs
    let test_cases = vec![
        ("simple", "hello", b"world".to_vec()),
        ("empty_value", "key", vec![]),
        ("unicode_key", "日本語", b"data".to_vec()),
        ("long_value", "k", vec![0xAA; 10000]),
        ("max_key", &"x".repeat(65535), b"v".to_vec()),
    ];

    for (name, key, value) in test_cases {
        let key_bytes = key.as_bytes();
        let key_len = (key_bytes.len() as u16).to_be_bytes();
        let value_len = (value.len() as u32).to_be_bytes();

        let mut data = Vec::new();
        data.extend_from_slice(&key_len);
        data.extend_from_slice(key_bytes);
        data.extend_from_slice(&value_len);
        data.extend_from_slice(&value);

        let path = format!("{}/{}", corpus_dir, name);
        let mut file = fs::File::create(&path).unwrap();
        file.write_all(&data).unwrap();
        println!("Created seed: {}", path);
    }
}
```

## Integrating Fuzzing Into CI

You can't fuzz for hours in CI on every commit, but you can run short fuzzing sessions to catch regressions:

```yaml
# .github/workflows/fuzz.yml
name: Fuzzing

on:
  push:
    branches: [main]
  schedule:
    # Extended fuzzing session nightly
    - cron: '0 2 * * *'

jobs:
  fuzz:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        target: [fuzz_parse_kv, fuzz_api_handler, fuzz_deserialize]
    steps:
      - uses: actions/checkout@v4

      - name: Install nightly toolchain
        uses: dtolnay/rust-toolchain@nightly

      - name: Install cargo-fuzz
        run: cargo install cargo-fuzz

      - name: Download existing corpus
        uses: actions/cache@v4
        with:
          path: fuzz/corpus/${{ matrix.target }}
          key: fuzz-corpus-${{ matrix.target }}-${{ github.sha }}
          restore-keys: fuzz-corpus-${{ matrix.target }}-

      - name: Run fuzzer
        run: |
          # Short run on PRs, long run on nightly schedule
          if [ "${{ github.event_name }}" = "schedule" ]; then
            DURATION=3600  # 1 hour for nightly
          else
            DURATION=120   # 2 minutes for push
          fi
          cargo +nightly fuzz run ${{ matrix.target }} -- \
            -max_total_time=$DURATION \
            -max_len=4096

      - name: Save corpus
        if: always()
        uses: actions/cache/save@v4
        with:
          path: fuzz/corpus/${{ matrix.target }}
          key: fuzz-corpus-${{ matrix.target }}-${{ github.sha }}
```

The corpus cache is important — it preserves interesting inputs between runs so the fuzzer doesn't start from scratch every time.

## Turning Fuzz Findings Into Tests

When the fuzzer finds a crash, turn it into a regression test:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    // Regression test for fuzzer finding:
    // crash-2024-03-15-abc123
    // Parser panicked on truncated UTF-8 at boundary
    #[test]
    fn test_truncated_utf8_at_boundary() {
        let data = vec![
            0x00, 0x03, // key_len = 3
            0xE6, 0x97, 0xA5, // "日" in UTF-8
            0x00, 0x00, 0x00, 0x01, // value_len = 1
            0x41, // "A"
        ];
        // This should not panic
        let result = parse_kv(&data);
        assert!(result.is_ok());
    }

    // Regression test for fuzzer finding:
    // Integer overflow in length calculation
    #[test]
    fn test_overflow_in_length_fields() {
        let data = vec![
            0xFF, 0xFF, // key_len = 65535
            // Not enough data to satisfy this length
            0x00, 0x00,
        ];
        let result = parse_kv(&data);
        assert!(result.is_err());
    }
}
```

Every crash gets a test. Every test gets a comment linking to the original fuzzer artifact. This prevents regressions and documents what went wrong.

## What to Fuzz

Not everything benefits equally from fuzzing. Focus on:

1. **Parsers** — anything that reads untrusted data (HTTP, JSON, protobuf, custom formats)
2. **Serialization/Deserialization** — roundtrip properties should hold
3. **Cryptographic code** — especially anything with `unsafe`
4. **State machines** — sequences of operations that might reach invalid states
5. **Codec and compression** — encode/decode roundtrips
6. **Anything that uses `unsafe`** — with sanitizers enabled

Don't bother fuzzing:
- Pure business logic with small, well-defined input spaces (unit tests are better)
- Code that only handles validated, typed inputs (fuzz the validation layer instead)
- Thin wrappers around well-fuzzed libraries

## The Bottom Line

Fuzzing finds bugs that humans don't think to test for. It's particularly effective for security because attackers think like fuzzers — they send unexpected, malformed, boundary-pushing input to find cracks in your parsing and validation.

Set up `cargo-fuzz` on every crate that handles untrusted input. Run short fuzzing sessions in CI. Run long sessions overnight. Keep your corpus. Turn every finding into a regression test.

The bugs the fuzzer finds are the bugs an attacker would have found first. Find them yourself.
