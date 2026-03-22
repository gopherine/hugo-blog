---
title: "Lesson 9: napi-rs — Rust extensions for Node.js"
author: Atharva Pandey
series: "Rust Unsafe & FFI"
lesson: 9
keywords: [Rust, rust, unsafe, FFI, napi-rs, Node.js, napi, native addon, JavaScript]
tags: [Rust tutorial, rust, unsafe, ffi]
date: '2025-07-01T13:26:00.000Z'
---

We had a Node.js microservice that validated JWTs. Under load, the `jsonwebtoken` npm package was burning 40% of CPU on RSA signature verification. I wrote the verification in Rust with napi-rs, dropped it in as a replacement, and CPU usage fell to 8%. The JavaScript API didn't change at all — same function name, same arguments, same return type. Just 5x faster.

napi-rs is to Node.js what PyO3 is to Python. You write Rust, export it as a native Node addon, and call it from JavaScript like any other module. The framework handles all the N-API complexity, type marshaling, and async integration.

## Project Setup

napi-rs has its own CLI that scaffolds everything:

```bash
# Install the CLI
npm install -g @napi-rs/cli

# Create a new project
napi new my-rust-lib
cd my-rust-lib
```

Or set it up manually:

```toml
# Cargo.toml
[package]
name = "my-rust-lib"
version = "0.1.0"
edition = "2021"

[lib]
crate-type = ["cdylib"]

[dependencies]
napi = { version = "2", features = ["full"] }
napi-derive = "2"

[build-dependencies]
napi-build = "2"
```

```rust
// build.rs
extern crate napi_build;

fn main() {
    napi_build::setup();
}
```

```rust
// src/lib.rs
#[macro_use]
extern crate napi_derive;

#[napi]
pub fn sum(a: i32, b: i32) -> i32 {
    a + b
}
```

Build it:

```bash
# Using napi CLI (recommended)
napi build --release

# Or using cargo directly
cargo build --release
# Then copy the .node file to the right place
```

```javascript
// index.js
const { sum } = require('./my-rust-lib');
console.log(sum(3, 4)); // 7
```

## Basic Function Exports

napi-rs uses the `#[napi]` attribute macro. It's clean — way less boilerplate than raw N-API:

```rust
use napi::bindgen_prelude::*;

/// Simple function — types convert automatically
#[napi]
pub fn add(a: f64, b: f64) -> f64 {
    a + b
}

/// String processing
#[napi]
pub fn to_upper(s: String) -> String {
    s.to_uppercase()
}

/// Working with arrays
#[napi]
pub fn sum_array(values: Vec<f64>) -> f64 {
    values.iter().sum()
}

/// Returning structured data — becomes a plain JS object
#[napi(object)]
pub struct Stats {
    pub mean: f64,
    pub min: f64,
    pub max: f64,
    pub count: u32,
}

#[napi]
pub fn compute_stats(values: Vec<f64>) -> Result<Stats> {
    if values.is_empty() {
        return Err(Error::new(
            Status::InvalidArg,
            "Cannot compute stats on empty array".to_owned(),
        ));
    }

    let sum: f64 = values.iter().sum();
    let count = values.len() as u32;
    let mean = sum / count as f64;
    let min = values.iter().cloned().fold(f64::INFINITY, f64::min);
    let max = values.iter().cloned().fold(f64::NEG_INFINITY, f64::max);

    Ok(Stats { mean, min, max, count })
}
```

```javascript
const lib = require('./my-rust-lib');

console.log(lib.add(1.5, 2.5));        // 4
console.log(lib.toUpper('hello'));       // HELLO
console.log(lib.sumArray([1, 2, 3, 4])); // 10

const stats = lib.computeStats([10, 20, 30, 40, 50]);
console.log(stats);
// { mean: 30, min: 10, max: 50, count: 5 }
```

Notice the naming: Rust's `snake_case` automatically converts to JavaScript's `camelCase`. `compute_stats` becomes `computeStats`. napi-rs handles this.

## Classes

Exposing Rust structs as JavaScript classes follows the same pattern as PyO3:

```rust
use napi::bindgen_prelude::*;
use std::collections::HashMap;

#[napi]
pub struct LRUCache {
    map: HashMap<String, String>,
    order: Vec<String>,
    capacity: usize,
}

#[napi]
impl LRUCache {
    #[napi(constructor)]
    pub fn new(capacity: u32) -> Self {
        LRUCache {
            map: HashMap::new(),
            order: Vec::new(),
            capacity: capacity as usize,
        }
    }

    #[napi]
    pub fn get(&mut self, key: String) -> Option<String> {
        if let Some(value) = self.map.get(&key).cloned() {
            // Move to front (most recently used)
            self.order.retain(|k| k != &key);
            self.order.push(key);
            Some(value)
        } else {
            None
        }
    }

    #[napi]
    pub fn set(&mut self, key: String, value: String) {
        if self.map.contains_key(&key) {
            self.order.retain(|k| k != &key);
        } else if self.map.len() >= self.capacity {
            // Evict least recently used
            if let Some(evicted) = self.order.first().cloned() {
                self.order.remove(0);
                self.map.remove(&evicted);
            }
        }

        self.map.insert(key.clone(), value);
        self.order.push(key);
    }

    #[napi(getter)]
    pub fn size(&self) -> u32 {
        self.map.len() as u32
    }

    #[napi]
    pub fn keys(&self) -> Vec<String> {
        self.order.clone()
    }

    #[napi]
    pub fn clear(&mut self) {
        self.map.clear();
        self.order.clear();
    }
}
```

```javascript
const { LRUCache } = require('./my-rust-lib');

const cache = new LRUCache(3);
cache.set('a', '1');
cache.set('b', '2');
cache.set('c', '3');
console.log(cache.size);        // 3
console.log(cache.get('a'));     // '1'

cache.set('d', '4');             // Evicts 'b' (least recently used)
console.log(cache.get('b'));     // null (evicted)
console.log(cache.keys());      // ['c', 'a', 'd']
```

## Async Functions

This is where napi-rs really outshines the competition. Node.js is async-first, and napi-rs makes it trivial to run Rust code on the libuv thread pool without blocking the event loop:

```rust
use napi::bindgen_prelude::*;

/// Async function — runs on the thread pool, returns a Promise to JS.
#[napi]
pub async fn hash_password(password: String, rounds: u32) -> Result<String> {
    // This runs on a libuv worker thread, not the main thread.
    // The event loop stays responsive.
    let cost = rounds.max(4).min(31);

    // Simulating bcrypt-like work
    let mut hash = password.clone();
    for _ in 0..cost {
        hash = format!("{:x}", md5_ish(&hash));
    }

    Ok(hash)
}

fn md5_ish(input: &str) -> u64 {
    // Simplified hash for demonstration
    let mut hash: u64 = 0xcbf29ce484222325;
    for byte in input.bytes() {
        hash ^= byte as u64;
        hash = hash.wrapping_mul(0x100000001b3);
    }
    hash
}

/// Async file processing — read, process, return results
#[napi]
pub async fn count_lines(file_path: String) -> Result<u32> {
    let content = tokio::fs::read_to_string(&file_path)
        .await
        .map_err(|e| Error::new(Status::GenericFailure, format!("IO error: {}", e)))?;

    Ok(content.lines().count() as u32)
}
```

```javascript
const { hashPassword, countLines } = require('./my-rust-lib');

// Returns a Promise — works with async/await
async function main() {
    const hash = await hashPassword('my-secret-password', 12);
    console.log(`Hash: ${hash}`);

    const lines = await countLines('/etc/hosts');
    console.log(`Lines: ${lines}`);
}

main().catch(console.error);
```

Under the hood, napi-rs spawns the Rust future on a worker thread. The JavaScript side gets a native `Promise`. No callbacks, no manual thread management. It just works.

For CPU-bound work, you can use `#[napi]` with Rayon:

```rust
use napi::bindgen_prelude::*;

/// Parallel computation using Rayon — CPU-bound work off the main thread.
#[napi]
pub async fn parallel_fibonacci(numbers: Vec<u32>) -> Vec<u64> {
    use rayon::prelude::*;

    // Rayon parallelizes across all CPU cores
    numbers.par_iter()
        .map(|&n| {
            let mut a: u64 = 0;
            let mut b: u64 = 1;
            for _ in 0..n {
                let temp = b;
                b = a.saturating_add(b);
                a = temp;
            }
            a
        })
        .collect()
}
```

## Buffer Handling

Node.js `Buffer` objects map to Rust byte slices. This is critical for anything dealing with binary data:

```rust
use napi::bindgen_prelude::*;

/// Process a Node.js Buffer directly — zero-copy read access.
#[napi]
pub fn crc32(data: &[u8]) -> u32 {
    let mut crc: u32 = 0xFFFFFFFF;
    for &byte in data {
        crc ^= byte as u32;
        for _ in 0..8 {
            if crc & 1 == 1 {
                crc = (crc >> 1) ^ 0xEDB88320;
            } else {
                crc >>= 1;
            }
        }
    }
    !crc
}

/// Return a new Buffer from Rust.
#[napi]
pub fn generate_bytes(len: u32) -> Buffer {
    let mut bytes = vec![0u8; len as usize];
    // Simple PRNG for demonstration
    let mut state: u64 = 12345;
    for byte in bytes.iter_mut() {
        state = state.wrapping_mul(6364136223846793005).wrapping_add(1);
        *byte = (state >> 33) as u8;
    }
    bytes.into()
}

/// XOR encryption/decryption — in-place mutation of a Buffer.
#[napi]
pub fn xor_cipher(data: &mut [u8], key: &[u8]) {
    if key.is_empty() {
        return;
    }
    for (i, byte) in data.iter_mut().enumerate() {
        *byte ^= key[i % key.len()];
    }
}
```

```javascript
const { crc32, generateBytes, xorCipher } = require('./my-rust-lib');

// CRC32 of a buffer
const buf = Buffer.from('Hello, world!');
console.log(crc32(buf).toString(16)); // crc32 hash

// Generate random bytes
const random = generateBytes(16);
console.log(random); // <Buffer ...>

// In-place XOR cipher
const data = Buffer.from('secret message');
const key = Buffer.from('key');
xorCipher(data, key); // data is now encrypted
console.log(data);
xorCipher(data, key); // XOR again to decrypt
console.log(data.toString()); // 'secret message'
```

## Error Handling

napi-rs errors become JavaScript `Error` objects. You can throw typed errors:

```rust
use napi::bindgen_prelude::*;

#[napi]
pub fn parse_json_value(input: String, path: String) -> Result<String> {
    let parsed: serde_json::Value = serde_json::from_str(&input)
        .map_err(|e| Error::new(
            Status::InvalidArg,
            format!("Invalid JSON: {}", e),
        ))?;

    let parts: Vec<&str> = path.split('.').collect();
    let mut current = &parsed;

    for part in &parts {
        current = current.get(part).ok_or_else(|| {
            Error::new(
                Status::GenericFailure,
                format!("Path '{}' not found at '{}'", path, part),
            )
        })?;
    }

    match current {
        serde_json::Value::String(s) => Ok(s.clone()),
        other => Ok(other.to_string()),
    }
}
```

```javascript
const { parseJsonValue } = require('./my-rust-lib');

try {
    const result = parseJsonValue('{"user": {"name": "Atharva"}}', 'user.name');
    console.log(result); // 'Atharva'
} catch (e) {
    console.error(e.message);
}

try {
    parseJsonValue('not json', 'foo');
} catch (e) {
    console.error(e.message); // 'Invalid JSON: ...'
}
```

## TypeScript Support

napi-rs generates TypeScript declarations automatically. When you build with the napi CLI, it produces a `.d.ts` file alongside the `.node` binary:

```typescript
// Generated index.d.ts — you don't write this, napi-rs does
export function add(a: number, b: number): number
export function toUpper(s: string): string
export function computeStats(values: Array<number>): Stats
export function hashPassword(password: string, rounds: number): Promise<string>

export interface Stats {
  mean: number
  min: number
  max: number
  count: number
}

export class LRUCache {
  constructor(capacity: number)
  get(key: string): string | null
  set(key: string, value: string): void
  get size(): number
  keys(): Array<string>
  clear(): void
}
```

This is a huge advantage over the C FFI approach. Your TypeScript consumers get full type checking, autocomplete, and documentation — all generated from your Rust code.

## A Real-World Example: JSON Schema Validator

Here's something close to a production use case — a JSON schema validator that's significantly faster than pure JS alternatives for complex schemas:

```rust
use napi::bindgen_prelude::*;
use serde_json::Value;
use std::collections::HashMap;

#[napi(object)]
pub struct ValidationError {
    pub path: String,
    pub message: String,
}

#[napi(object)]
pub struct ValidationResult {
    pub valid: bool,
    pub errors: Vec<ValidationError>,
}

#[napi]
pub struct SchemaValidator {
    required_fields: Vec<String>,
    field_types: HashMap<String, String>,
    string_patterns: HashMap<String, regex::Regex>,
    number_ranges: HashMap<String, (f64, f64)>,
}

#[napi]
impl SchemaValidator {
    #[napi(constructor)]
    pub fn new() -> Self {
        SchemaValidator {
            required_fields: Vec::new(),
            field_types: HashMap::new(),
            string_patterns: HashMap::new(),
            number_ranges: HashMap::new(),
        }
    }

    #[napi]
    pub fn require_field(&mut self, name: String) {
        self.required_fields.push(name);
    }

    #[napi]
    pub fn expect_type(&mut self, field: String, type_name: String) {
        self.field_types.insert(field, type_name);
    }

    #[napi]
    pub fn expect_pattern(&mut self, field: String, pattern: String) -> Result<()> {
        let re = regex::Regex::new(&pattern).map_err(|e| {
            Error::new(Status::InvalidArg, format!("Invalid regex: {}", e))
        })?;
        self.string_patterns.insert(field, re);
        Ok(())
    }

    #[napi]
    pub fn expect_range(&mut self, field: String, min: f64, max: f64) {
        self.number_ranges.insert(field, (min, max));
    }

    #[napi]
    pub fn validate(&self, json_str: String) -> Result<ValidationResult> {
        let value: Value = serde_json::from_str(&json_str).map_err(|e| {
            Error::new(Status::InvalidArg, format!("Invalid JSON: {}", e))
        })?;

        let obj = match value.as_object() {
            Some(o) => o,
            None => {
                return Ok(ValidationResult {
                    valid: false,
                    errors: vec![ValidationError {
                        path: "$".to_string(),
                        message: "Expected object at root".to_string(),
                    }],
                });
            }
        };

        let mut errors = Vec::new();

        // Check required fields
        for field in &self.required_fields {
            if !obj.contains_key(field) {
                errors.push(ValidationError {
                    path: format!("$.{}", field),
                    message: format!("Required field '{}' is missing", field),
                });
            }
        }

        // Check types
        for (field, expected_type) in &self.field_types {
            if let Some(val) = obj.get(field) {
                let actual_type = match val {
                    Value::String(_) => "string",
                    Value::Number(_) => "number",
                    Value::Bool(_) => "boolean",
                    Value::Array(_) => "array",
                    Value::Object(_) => "object",
                    Value::Null => "null",
                };
                if actual_type != expected_type.as_str() {
                    errors.push(ValidationError {
                        path: format!("$.{}", field),
                        message: format!(
                            "Expected type '{}', got '{}'",
                            expected_type, actual_type
                        ),
                    });
                }
            }
        }

        // Check string patterns
        for (field, pattern) in &self.string_patterns {
            if let Some(Value::String(s)) = obj.get(field) {
                if !pattern.is_match(s) {
                    errors.push(ValidationError {
                        path: format!("$.{}", field),
                        message: format!(
                            "Value '{}' doesn't match pattern '{}'",
                            s,
                            pattern.as_str()
                        ),
                    });
                }
            }
        }

        // Check number ranges
        for (field, (min, max)) in &self.number_ranges {
            if let Some(Value::Number(n)) = obj.get(field) {
                if let Some(val) = n.as_f64() {
                    if val < *min || val > *max {
                        errors.push(ValidationError {
                            path: format!("$.{}", field),
                            message: format!(
                                "Value {} is outside range [{}, {}]",
                                val, min, max
                            ),
                        });
                    }
                }
            }
        }

        Ok(ValidationResult {
            valid: errors.is_empty(),
            errors,
        })
    }

    /// Validate many JSON strings in parallel.
    #[napi]
    pub async fn validate_batch(&self, json_strings: Vec<String>) -> Result<Vec<ValidationResult>> {
        // For real parallelism, you'd use rayon here
        let results: Vec<ValidationResult> = json_strings
            .iter()
            .map(|s| {
                self.validate(s.clone())
                    .unwrap_or_else(|e| ValidationResult {
                        valid: false,
                        errors: vec![ValidationError {
                            path: "$".to_string(),
                            message: e.to_string(),
                        }],
                    })
            })
            .collect();

        Ok(results)
    }
}
```

```javascript
const { SchemaValidator } = require('./my-rust-lib');

const validator = new SchemaValidator();
validator.requireField('email');
validator.requireField('name');
validator.expectType('email', 'string');
validator.expectType('age', 'number');
validator.expectPattern('email', '^[^@]+@[^@]+\\.[^@]+$');
validator.expectRange('age', 0, 150);

const result = validator.validate(JSON.stringify({
    name: 'Atharva',
    email: 'not-an-email',
    age: 200
}));

console.log(result.valid); // false
for (const err of result.errors) {
    console.log(`${err.path}: ${err.message}`);
}
// $.email: Value 'not-an-email' doesn't match pattern ...
// $.age: Value 200 is outside range [0, 150]
```

## Cross-Platform Publishing

napi-rs supports building for multiple platforms and publishing pre-built binaries. Your users don't need Rust installed — they just `npm install` and get a precompiled binary:

```json
{
  "name": "my-rust-lib",
  "napi": {
    "name": "my-rust-lib",
    "triples": {
      "defaults": true,
      "additional": [
        "aarch64-apple-darwin",
        "aarch64-unknown-linux-gnu",
        "x86_64-unknown-linux-musl"
      ]
    }
  }
}
```

The napi CLI handles the rest — it builds for each target, packages the `.node` files as separate npm packages, and sets up the optional dependencies so npm installs only the binary for the user's platform.

## When napi-rs Is Worth It

I use napi-rs when:

- **CPU-bound work** that's measurably slow in JS (hash functions, compression, image processing, parsing)
- **Memory-critical operations** where you need precise control over allocation
- **Reusing existing Rust libraries** — wrap them for Node instead of rewriting in JS
- **Performance-sensitive middleware** — JWT validation, request parsing, serialization

I don't use it for:

- Simple CRUD logic (JS is fast enough, and the FFI boundary adds complexity)
- I/O-bound operations (Node's async I/O is already efficient)
- Prototyping (the compile cycle is slower than JS iteration)

The overhead of crossing the JS-Rust boundary is roughly 50-100 nanoseconds per call. For functions that do microseconds of work, that overhead is negligible. For functions called millions of times per second with nanoseconds of work each, the boundary cost dominates — batch your calls instead.

Last lesson: we talk about soundness — the ultimate safety guarantee that ties everything in this course together.
