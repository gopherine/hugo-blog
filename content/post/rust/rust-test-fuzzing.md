---
title: "Lesson 7: Fuzz Testing with cargo-fuzz — Breaking your code automatically"
author: Atharva Pandey
series: "Rust Testing Masterclass"
lesson: 7
keywords: [Rust, rust, testing, fuzz testing, cargo-fuzz, libfuzzer, security, crash detection]
tags: [Rust tutorial, rust, testing]
date: '2024-08-24T20:05:00.000Z'
---

A friend of mine maintains a binary parser in Rust. It had hundreds of unit tests, property tests, the works. He ran a fuzzer against it on a Friday afternoon, left it running over the weekend. Monday morning: 23 unique crashes. Some were panics from unexpected inputs. Two were infinite loops. One was a stack overflow from deeply nested structures. All from inputs no human would ever think to construct.

Fuzz testing doesn't test what your code *should* do. It tests what your code does when reality gets weird.

## The Problem

Property-based testing generates random inputs within constraints you define — valid integers, well-formed strings, structured data. But real-world input isn't constrained. Network packets get corrupted. Files get truncated. Users paste random garbage into text fields. Attackers deliberately craft malicious inputs.

Fuzzing throws raw, mutated bytes at your code and watches what happens. If your function panics, hangs, or crashes, the fuzzer captures the input that caused it. It's like hiring someone to beat on your code with a baseball bat while you're at lunch.

## Setting Up cargo-fuzz

`cargo-fuzz` is the standard fuzzing tool for Rust. It uses LLVM's libFuzzer under the hood and requires the nightly compiler.

```bash
# Install cargo-fuzz
cargo install cargo-fuzz

# Initialize fuzzing in your project
cargo fuzz init
```

This creates a `fuzz/` directory:

```
fuzz/
├── Cargo.toml
└── fuzz_targets/
    └── fuzz_target_1.rs
```

## Your First Fuzz Target

Let's fuzz a simple parser. Here's the library code:

```rust
// src/lib.rs

#[derive(Debug, PartialEq)]
pub struct Header {
    pub version: u8,
    pub flags: u8,
    pub payload_len: u16,
}

#[derive(Debug)]
pub enum ParseError {
    TooShort,
    InvalidVersion,
    PayloadTooLarge,
}

pub fn parse_header(data: &[u8]) -> Result<Header, ParseError> {
    if data.len() < 4 {
        return Err(ParseError::TooShort);
    }

    let version = data[0];
    if version == 0 || version > 3 {
        return Err(ParseError::InvalidVersion);
    }

    let flags = data[1];
    let payload_len = u16::from_le_bytes([data[2], data[3]]);

    if payload_len > 8192 {
        return Err(ParseError::PayloadTooLarge);
    }

    Ok(Header {
        version,
        flags,
        payload_len,
    })
}

pub fn process_message(data: &[u8]) -> Result<Vec<u8>, ParseError> {
    let header = parse_header(data)?;
    let payload_start = 4;
    let payload_end = payload_start + header.payload_len as usize;

    // BUG: what if data.len() < payload_end?
    let payload = &data[payload_start..payload_end];
    Ok(payload.to_vec())
}
```

Now the fuzz target:

```rust
// fuzz/fuzz_targets/fuzz_target_1.rs

#![no_main]

use libfuzzer_sys::fuzz_target;
use my_crate::process_message;

fuzz_target!(|data: &[u8]| {
    // We don't care about the result — we care about panics
    let _ = process_message(data);
});
```

Run it:

```bash
cargo +nightly fuzz run fuzz_target_1
```

Within seconds, the fuzzer will find a panic: `process_message` trusts `payload_len` from the header but doesn't check if the actual data has enough bytes. The fuzzer generates a 4-byte input where `payload_len` says 100 but there are only 4 bytes total. Boom — index out of bounds.

The fix:

```rust
pub fn process_message(data: &[u8]) -> Result<Vec<u8>, ParseError> {
    let header = parse_header(data)?;
    let payload_start = 4;
    let payload_end = payload_start + header.payload_len as usize;

    if data.len() < payload_end {
        return Err(ParseError::TooShort);
    }

    let payload = &data[payload_start..payload_end];
    Ok(payload.to_vec())
}
```

This is the magic of fuzzing. The bug was obvious once the fuzzer found it, but I didn't think to test for it because I mentally assumed the data would be well-formed. The fuzzer makes no such assumption.

## Structured Fuzzing with Arbitrary

Raw bytes are great for binary parsers, but for structured input, use the `arbitrary` crate. It lets you derive random instances of your types.

```toml
# Cargo.toml
[dependencies]
arbitrary = { version = "1", features = ["derive"] }
```

```rust
// src/lib.rs

use arbitrary::Arbitrary;

#[derive(Debug, Arbitrary)]
pub struct Config {
    pub max_connections: u32,
    pub timeout_ms: u64,
    pub retry_count: u8,
    pub host: String,
}

pub fn validate_config(config: &Config) -> Result<(), String> {
    if config.max_connections == 0 {
        return Err("max_connections must be positive".to_string());
    }
    if config.timeout_ms > 300_000 {
        return Err("timeout too large".to_string());
    }
    if config.host.is_empty() {
        return Err("host required".to_string());
    }
    if config.retry_count > 10 {
        return Err("too many retries".to_string());
    }
    Ok(())
}

pub fn apply_config(config: &Config) -> String {
    if validate_config(config).is_err() {
        return "invalid".to_string();
    }

    // Potential bug: what happens with very long host strings?
    let connection_string = format!(
        "{}:{}/pool={}",
        config.host,
        config.timeout_ms,
        config.max_connections
    );

    connection_string
}
```

```rust
// fuzz/fuzz_targets/fuzz_config.rs

#![no_main]

use libfuzzer_sys::fuzz_target;
use arbitrary::Arbitrary;
use my_crate::{Config, apply_config};

fuzz_target!(|config: Config| {
    let _ = apply_config(&config);
});
```

The `Arbitrary` derive automatically generates random `Config` values from raw bytes. The fuzzer explores the input space intelligently, guided by code coverage — it prefers inputs that exercise new code paths.

## Working with Fuzz Corpuses

The fuzzer maintains a *corpus* — a set of interesting inputs that exercise different code paths.

```bash
# The corpus lives here
fuzz/corpus/fuzz_target_1/

# Crashes are saved here
fuzz/artifacts/fuzz_target_1/
```

You can seed the corpus with known-good inputs:

```bash
# Create a seed directory
mkdir -p fuzz/corpus/fuzz_target_1

# Add seed inputs (just raw files)
echo -ne '\x01\x00\x04\x00AAAA' > fuzz/corpus/fuzz_target_1/valid_message
```

Seed inputs give the fuzzer a head start. It mutates them to explore nearby inputs, which is much more efficient than starting from nothing.

## Reproducing Crashes

When the fuzzer finds a crash, it saves the input:

```
fuzz/artifacts/fuzz_target_1/crash-abc123def456
```

Reproduce it:

```bash
cargo +nightly fuzz run fuzz_target_1 fuzz/artifacts/fuzz_target_1/crash-abc123def456
```

Or write a regression test from it:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn regression_crash_short_payload() {
        // From fuzzer crash artifact
        let data = b"\x01\x00\x64\x00";  // version=1, payload_len=100, only 4 bytes
        let result = process_message(data);
        assert!(result.is_err()); // should not panic
    }
}
```

Always convert fuzzer crashes into unit tests. The fuzz test found the bug; the unit test prevents regression.

## Fuzzing Strategies

### Time-Limited Runs

In CI, you probably want bounded fuzzing:

```bash
# Run for 60 seconds
cargo +nightly fuzz run fuzz_target_1 -- -max_total_time=60

# Run for 10,000 iterations
cargo +nightly fuzz run fuzz_target_1 -- -runs=10000
```

### Multiple Targets

Create separate fuzz targets for different entry points:

```bash
cargo fuzz add fuzz_parser
cargo fuzz add fuzz_serializer
cargo fuzz add fuzz_validator
```

Each gets its own file in `fuzz/fuzz_targets/` and its own corpus.

### Coverage-Guided Fuzzing

libFuzzer instruments your code to track which branches are taken. It prioritizes mutations that reach new code. You can view coverage:

```bash
cargo +nightly fuzz coverage fuzz_target_1
```

This generates LLVM coverage data you can visualize to see which parts of your code the fuzzer has and hasn't reached.

## A More Realistic Example: Fuzzing a Tokenizer

```rust
// src/lib.rs

#[derive(Debug, PartialEq)]
pub enum Token {
    Number(f64),
    Ident(String),
    Plus,
    Minus,
    Star,
    Slash,
    LParen,
    RParen,
    Whitespace,
}

pub fn tokenize(input: &str) -> Result<Vec<Token>, String> {
    let mut tokens = Vec::new();
    let mut chars = input.chars().peekable();

    while let Some(&ch) = chars.peek() {
        match ch {
            ' ' | '\t' | '\n' | '\r' => {
                chars.next();
                tokens.push(Token::Whitespace);
                while chars.peek().map_or(false, |c| c.is_whitespace()) {
                    chars.next();
                }
            }
            '+' => { chars.next(); tokens.push(Token::Plus); }
            '-' => { chars.next(); tokens.push(Token::Minus); }
            '*' => { chars.next(); tokens.push(Token::Star); }
            '/' => { chars.next(); tokens.push(Token::Slash); }
            '(' => { chars.next(); tokens.push(Token::LParen); }
            ')' => { chars.next(); tokens.push(Token::RParen); }
            '0'..='9' | '.' => {
                let mut num = String::new();
                let mut has_dot = false;
                while let Some(&c) = chars.peek() {
                    if c.is_ascii_digit() {
                        num.push(c);
                        chars.next();
                    } else if c == '.' && !has_dot {
                        has_dot = true;
                        num.push(c);
                        chars.next();
                    } else {
                        break;
                    }
                }
                let n: f64 = num.parse().map_err(|e| format!("bad number '{}': {}", num, e))?;
                tokens.push(Token::Number(n));
            }
            'a'..='z' | 'A'..='Z' | '_' => {
                let mut ident = String::new();
                while let Some(&c) = chars.peek() {
                    if c.is_alphanumeric() || c == '_' {
                        ident.push(c);
                        chars.next();
                    } else {
                        break;
                    }
                }
                tokens.push(Token::Ident(ident));
            }
            other => {
                return Err(format!("unexpected character: '{}'", other));
            }
        }
    }

    Ok(tokens)
}
```

The fuzz target:

```rust
// fuzz/fuzz_targets/fuzz_tokenizer.rs

#![no_main]

use libfuzzer_sys::fuzz_target;
use my_crate::tokenize;

fuzz_target!(|data: &[u8]| {
    if let Ok(s) = std::str::from_utf8(data) {
        // tokenize should never panic, only return Ok or Err
        let _ = tokenize(s);
    }
});
```

The contract is simple: `tokenize` should never panic, regardless of input. It can return an error for invalid input, but it must not crash. The fuzzer will throw every bizarre combination of bytes at it — null bytes, multi-byte UTF-8, emoji, control characters — until it either finds a panic or runs out of time.

## When to Fuzz

Fuzzing is most valuable for:

- **Parsers** — anything that accepts untrusted input (network protocols, file formats, user input).
- **Serialization/deserialization** — encode/decode chains where a mismatch means data corruption.
- **Unsafe code** — anywhere you've written `unsafe`, fuzzing helps verify your safety invariants hold.
- **Cryptographic code** — where edge cases can be security vulnerabilities.

It's less useful for pure business logic with well-defined inputs. Don't fuzz your shopping cart — fuzz your image decoder.

## Fuzzing vs. Property Testing

People conflate these, but they're different tools for different jobs.

| | Property Testing | Fuzz Testing |
|---|---|---|
| Input | Structured, constrained | Raw bytes, mutated |
| Duration | Seconds (hundreds of cases) | Minutes to hours (millions of cases) |
| Goal | Verify invariants | Find crashes |
| Guidance | Random | Coverage-guided |
| Shrinking | Built-in | Minimization pass |
| Best for | Logic bugs | Crash bugs, security issues |

Use property tests in your regular test suite. Use fuzzing as an additional layer, especially for code that handles untrusted input.

## What's Next

We've covered generating random inputs to find crashes. Next up is a completely different approach — snapshot testing. Instead of writing assertions about what the output *should* be, you capture the output once and automatically detect when it changes. It's particularly powerful for large, structured outputs where writing assertions by hand would be impractical.
