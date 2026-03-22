---
title: "Lesson 2: Integration Tests — The tests/ directory"
author: Atharva Pandey
series: "Rust Testing Masterclass"
lesson: 2
keywords: [Rust, rust, testing, integration tests, tests directory, cargo test, public API]
tags: [Rust tutorial, rust, testing]
date: '2024-08-12T09:45:00.000Z'
---

A colleague once reviewed one of my Rust libraries and said, "Your unit tests pass, but I can't actually use the crate — the public API doesn't compose the way anyone would expect." He was right. I'd tested every internal function meticulously and completely ignored the experience of someone actually calling my library from the outside. That's the gap integration tests fill.

## The Problem

Unit tests live inside your source files and can access private functions. That's great for verifying internal logic, but it creates a blind spot: you never validate that your *public API* actually makes sense. You can have perfect internal functions that combine into a terrible user experience.

Integration tests sit outside your crate entirely. They can only use your public API — exactly like a real user would. If something is hard to test from the integration test side, it's probably hard to use in real code too.

## How Integration Tests Work in Rust

Rust has a convention: files in a `tests/` directory at the root of your crate are integration tests. Each file in that directory is compiled as a separate crate that depends on your library.

Here's a typical project layout:

```
my_crate/
├── Cargo.toml
├── src/
│   └── lib.rs
└── tests/
    ├── api_tests.rs
    └── parsing_tests.rs
```

Each file in `tests/` is its own crate. It imports your library with `use my_crate::...` and can only access public items. No `use super::*`, no reaching into private modules.

## A Working Example

Say you're building a small key-value store library.

```rust
// src/lib.rs

use std::collections::HashMap;
use std::fmt;

#[derive(Debug)]
pub enum StoreError {
    KeyNotFound(String),
    InvalidKey(String),
}

impl fmt::Display for StoreError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            StoreError::KeyNotFound(k) => write!(f, "key not found: {}", k),
            StoreError::InvalidKey(k) => write!(f, "invalid key: {}", k),
        }
    }
}

impl std::error::Error for StoreError {}

pub struct KvStore {
    data: HashMap<String, String>,
}

impl KvStore {
    pub fn new() -> Self {
        KvStore {
            data: HashMap::new(),
        }
    }

    pub fn set(&mut self, key: &str, value: &str) -> Result<(), StoreError> {
        if key.is_empty() || key.contains(' ') {
            return Err(StoreError::InvalidKey(key.to_string()));
        }
        self.data.insert(key.to_string(), value.to_string());
        Ok(())
    }

    pub fn get(&self, key: &str) -> Result<&str, StoreError> {
        self.data
            .get(key)
            .map(|v| v.as_str())
            .ok_or_else(|| StoreError::KeyNotFound(key.to_string()))
    }

    pub fn delete(&mut self, key: &str) -> Result<String, StoreError> {
        self.data
            .remove(key)
            .ok_or_else(|| StoreError::KeyNotFound(key.to_string()))
    }

    pub fn keys(&self) -> Vec<&str> {
        let mut keys: Vec<&str> = self.data.keys().map(|k| k.as_str()).collect();
        keys.sort();
        keys
    }

    pub fn len(&self) -> usize {
        self.data.len()
    }

    pub fn is_empty(&self) -> bool {
        self.data.is_empty()
    }
}
```

Now the integration tests. These go in `tests/`:

```rust
// tests/store_tests.rs

use my_crate::{KvStore, StoreError};

#[test]
fn test_basic_set_and_get() {
    let mut store = KvStore::new();
    store.set("name", "Atharva").unwrap();
    assert_eq!(store.get("name").unwrap(), "Atharva");
}

#[test]
fn test_overwrite_value() {
    let mut store = KvStore::new();
    store.set("color", "blue").unwrap();
    store.set("color", "red").unwrap();
    assert_eq!(store.get("color").unwrap(), "red");
}

#[test]
fn test_get_missing_key() {
    let store = KvStore::new();
    let err = store.get("ghost").unwrap_err();
    assert!(matches!(err, StoreError::KeyNotFound(_)));
}

#[test]
fn test_delete_existing_key() {
    let mut store = KvStore::new();
    store.set("temp", "data").unwrap();
    let removed = store.delete("temp").unwrap();
    assert_eq!(removed, "data");
    assert!(store.get("temp").is_err());
}

#[test]
fn test_delete_missing_key() {
    let mut store = KvStore::new();
    let err = store.delete("nope").unwrap_err();
    assert!(matches!(err, StoreError::KeyNotFound(_)));
}

#[test]
fn test_invalid_key_rejected() {
    let mut store = KvStore::new();
    assert!(store.set("", "value").is_err());
    assert!(store.set("has space", "value").is_err());
}

#[test]
fn test_keys_sorted() {
    let mut store = KvStore::new();
    store.set("zebra", "1").unwrap();
    store.set("apple", "2").unwrap();
    store.set("mango", "3").unwrap();
    assert_eq!(store.keys(), vec!["apple", "mango", "zebra"]);
}

#[test]
fn test_len_and_empty() {
    let mut store = KvStore::new();
    assert!(store.is_empty());
    assert_eq!(store.len(), 0);

    store.set("a", "1").unwrap();
    assert!(!store.is_empty());
    assert_eq!(store.len(), 1);

    store.delete("a").unwrap();
    assert!(store.is_empty());
}
```

Notice something? These tests read like a user story. Someone new to the crate could read these tests and understand exactly how to use the API. That's not an accident — it's one of the biggest benefits of integration tests.

## Sharing Code Between Integration Tests

Here's where things get slightly tricky. Each file in `tests/` is a separate crate, so they don't share code with each other by default. If you need helper functions across multiple integration test files, use a subdirectory with a `mod.rs`:

```
tests/
├── common/
│   └── mod.rs
├── api_tests.rs
└── workflow_tests.rs
```

```rust
// tests/common/mod.rs

use my_crate::KvStore;

pub fn populated_store() -> KvStore {
    let mut store = KvStore::new();
    store.set("name", "test").unwrap();
    store.set("version", "1.0").unwrap();
    store.set("debug", "true").unwrap();
    store
}
```

```rust
// tests/workflow_tests.rs

mod common;

use my_crate::KvStore;

#[test]
fn test_workflow_update_and_verify() {
    let mut store = common::populated_store();
    store.set("version", "2.0").unwrap();
    assert_eq!(store.get("version").unwrap(), "2.0");
    assert_eq!(store.len(), 3); // count shouldn't change
}
```

The key detail: `tests/common/mod.rs` — not `tests/common.rs`. If you put a file directly in `tests/`, Cargo treats it as a standalone integration test and tries to run it. A subdirectory with `mod.rs` is treated as a helper module, not a test file.

## Running Integration Tests

```bash
# Run all tests (unit + integration)
cargo test

# Run only integration tests
cargo test --test store_tests

# Run a specific integration test by name
cargo test --test store_tests test_basic_set_and_get

# Run all integration tests (skip unit tests)
cargo test --test '*'
```

The `--test` flag takes the filename (without `.rs`). This is useful when your integration tests are slow and you only want to run the relevant ones during development.

## Integration Tests for Binary Crates

One important limitation: integration tests only work for library crates (`src/lib.rs`). If your project is a binary crate (`src/main.rs` only), there's no library to import.

The standard workaround — and it's a good practice regardless — is to put your logic in `src/lib.rs` and keep `src/main.rs` as a thin wrapper.

```rust
// src/lib.rs
pub fn run(args: &[String]) -> Result<(), Box<dyn std::error::Error>> {
    // actual logic here
    Ok(())
}

// src/main.rs
fn main() {
    let args: Vec<String> = std::env::args().collect();
    if let Err(e) = my_crate::run(&args) {
        eprintln!("Error: {}", e);
        std::process::exit(1);
    }
}
```

Now your integration tests can `use my_crate::run` and test the actual behavior. Your `main.rs` is just plumbing — it doesn't need its own tests.

## When to Use Integration Tests vs. Unit Tests

This is where I see people get confused. Here's my rule of thumb:

**Unit tests** verify that individual functions and methods work correctly in isolation. They test the *mechanics* — does this parsing function handle edge cases? Does this algorithm produce the right output?

**Integration tests** verify that the public API provides a coherent experience. They test *workflows* — can a user create a store, add data, query it, and delete it? Do the error types make sense when something goes wrong?

You need both. Unit tests catch implementation bugs. Integration tests catch API design mistakes. I've had plenty of situations where all unit tests passed but the integration tests revealed that two public functions couldn't be composed together without an awkward intermediate step.

## Organizing Large Test Suites

For bigger projects, I organize integration tests by feature area:

```
tests/
├── common/
│   └── mod.rs
├── auth_tests.rs      # authentication workflows
├── storage_tests.rs   # data storage operations
├── query_tests.rs     # search and filtering
└── error_tests.rs     # error handling paths
```

Each file focuses on one area of functionality. When a test fails, the filename alone tells me which subsystem is broken. Keep the files focused — if one grows past 200 lines, it's probably testing too many things and should be split.

## Common Mistakes

**Putting integration tests in `src/`.** They belong in `tests/` at the project root. If they're in `src/`, they're unit tests with extra steps.

**Testing private internals from integration tests.** If you need to access private functions, you're either testing at the wrong level or your public API is missing something.

**No shared setup code.** If three test files all create the same setup, extract it into `tests/common/mod.rs`. DRY applies to tests too.

**Ignoring compilation time.** Each integration test file is a separate crate. Twenty test files means twenty separate compilations. Group related tests in the same file when they don't need isolation from each other.

## What's Next

There's another kind of test built into Rust that most people underuse: doc tests. They serve double duty — they're tests *and* documentation. When your examples compile and pass, you know your docs aren't lying. That's next.
