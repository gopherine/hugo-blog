---
title: "Lesson 4: Test Fixtures and Setup/Teardown — Reusable test infrastructure"
author: Atharva Pandey
series: "Rust Testing Masterclass"
lesson: 4
keywords: [Rust, rust, testing, fixtures, setup, teardown, test infrastructure, rstest]
tags: [Rust tutorial, rust, testing]
date: '2024-08-17T11:30:00.000Z'
---

I had a test file last year where every single test started with the same twelve lines of setup — creating a database connection, inserting seed data, configuring a logger. Twelve lines, copy-pasted forty times. When I needed to change the seed data, I had to update forty tests. I missed three. Those three tests silently tested against stale data for two months.

Fixtures exist to prevent this kind of insanity.

## The Problem

Rust doesn't have built-in `setUp()` and `tearDown()` methods like JUnit or pytest. There's no `@Before` annotation, no `conftest.py`. Each `#[test]` function is standalone — which is great for isolation but terrible for DRY.

When your tests need shared setup (creating structs, opening files, initializing state), you end up with two bad options: copy-paste the setup into every test, or call a helper function manually. Both work, but both have friction that leads to inconsistency.

## The Manual Approach (No Dependencies)

Before reaching for a crate, you can get surprisingly far with plain Rust.

### Helper Functions

The simplest fixture is just a function that returns your test state.

```rust
use std::collections::HashMap;

struct AppConfig {
    settings: HashMap<String, String>,
    debug_mode: bool,
    max_retries: u32,
}

impl AppConfig {
    fn new() -> Self {
        AppConfig {
            settings: HashMap::new(),
            debug_mode: false,
            max_retries: 3,
        }
    }

    fn set(&mut self, key: &str, value: &str) {
        self.settings.insert(key.to_string(), value.to_string());
    }

    fn get(&self, key: &str) -> Option<&str> {
        self.settings.get(key).map(|s| s.as_str())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn default_config() -> AppConfig {
        let mut config = AppConfig::new();
        config.set("database_url", "postgres://localhost/test");
        config.set("redis_url", "redis://localhost:6379");
        config.debug_mode = true;
        config
    }

    #[test]
    fn test_get_setting() {
        let config = default_config();
        assert_eq!(config.get("database_url"), Some("postgres://localhost/test"));
    }

    #[test]
    fn test_override_setting() {
        let mut config = default_config();
        config.set("database_url", "postgres://prod/main");
        assert_eq!(config.get("database_url"), Some("postgres://prod/main"));
    }

    #[test]
    fn test_debug_mode_enabled() {
        let config = default_config();
        assert!(config.debug_mode);
    }
}
```

Simple, clean, no dependencies. Every test gets its own `AppConfig` — no shared mutable state, no ordering issues.

### RAII for Teardown

Rust's ownership system gives you automatic teardown for free. When a value goes out of scope, its `Drop` implementation runs. This is perfect for cleaning up temp files, closing connections, or resetting state.

```rust
use std::fs;
use std::path::{Path, PathBuf};

struct TempDir {
    path: PathBuf,
}

impl TempDir {
    fn new(name: &str) -> Self {
        let path = std::env::temp_dir().join(format!("test_{}", name));
        fs::create_dir_all(&path).expect("failed to create temp dir");
        TempDir { path }
    }

    fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn test_write_and_read_file() {
        let dir = TempDir::new("write_read");
        let file_path = dir.path().join("data.txt");

        fs::write(&file_path, "hello").unwrap();
        let content = fs::read_to_string(&file_path).unwrap();
        assert_eq!(content, "hello");
        // TempDir drops here, directory is cleaned up
    }

    #[test]
    fn test_multiple_files() {
        let dir = TempDir::new("multi_files");

        for i in 0..5 {
            let path = dir.path().join(format!("file_{}.txt", i));
            fs::write(&path, format!("content {}", i)).unwrap();
        }

        let entries: Vec<_> = fs::read_dir(dir.path())
            .unwrap()
            .filter_map(|e| e.ok())
            .collect();
        assert_eq!(entries.len(), 5);
        // cleanup happens automatically
    }
}
```

No explicit teardown code in the test body. The `Drop` trait handles it. Even if the test panics, `Drop` still runs (with some caveats around double panics, but in practice this works reliably).

### Builder Pattern for Complex Fixtures

When your test setup has many optional parameters, a builder makes tests more readable.

```rust
struct TestServer {
    host: String,
    port: u16,
    auth_enabled: bool,
    max_connections: usize,
    timeout_ms: u64,
}

struct TestServerBuilder {
    host: String,
    port: u16,
    auth_enabled: bool,
    max_connections: usize,
    timeout_ms: u64,
}

impl TestServerBuilder {
    fn new() -> Self {
        TestServerBuilder {
            host: "localhost".to_string(),
            port: 8080,
            auth_enabled: false,
            max_connections: 100,
            timeout_ms: 5000,
        }
    }

    fn port(mut self, port: u16) -> Self {
        self.port = port;
        self
    }

    fn with_auth(mut self) -> Self {
        self.auth_enabled = true;
        self
    }

    fn max_connections(mut self, n: usize) -> Self {
        self.max_connections = n;
        self
    }

    fn timeout_ms(mut self, ms: u64) -> Self {
        self.timeout_ms = ms;
        self
    }

    fn build(self) -> TestServer {
        TestServer {
            host: self.host,
            port: self.port,
            auth_enabled: self.auth_enabled,
            max_connections: self.max_connections,
            timeout_ms: self.timeout_ms,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_server() {
        let server = TestServerBuilder::new().build();
        assert_eq!(server.port, 8080);
        assert!(!server.auth_enabled);
    }

    #[test]
    fn test_custom_server() {
        let server = TestServerBuilder::new()
            .port(9090)
            .with_auth()
            .max_connections(50)
            .build();

        assert_eq!(server.port, 9090);
        assert!(server.auth_enabled);
        assert_eq!(server.max_connections, 50);
    }
}
```

Each test only specifies what's different from the defaults. When you read the test, you immediately see what's important.

## rstest: Proper Fixture Support

For anything beyond trivial setup, the `rstest` crate is a game-changer. Add it to your `Cargo.toml`:

```toml
[dev-dependencies]
rstest = "0.21"
```

### Basic Fixtures

```rust
use rstest::*;
use std::collections::HashMap;

struct Database {
    data: HashMap<String, Vec<String>>,
}

impl Database {
    fn new() -> Self {
        Database {
            data: HashMap::new(),
        }
    }

    fn insert(&mut self, table: &str, value: &str) {
        self.data
            .entry(table.to_string())
            .or_default()
            .push(value.to_string());
    }

    fn query(&self, table: &str) -> Option<&Vec<String>> {
        self.data.get(table)
    }

    fn count(&self, table: &str) -> usize {
        self.data.get(table).map_or(0, |v| v.len())
    }
}

#[fixture]
fn db() -> Database {
    let mut db = Database::new();
    db.insert("users", "alice");
    db.insert("users", "bob");
    db.insert("posts", "hello world");
    db
}

#[rstest]
fn test_user_count(db: Database) {
    assert_eq!(db.count("users"), 2);
}

#[rstest]
fn test_post_count(db: Database) {
    assert_eq!(db.count("posts"), 1);
}

#[rstest]
fn test_query_users(db: Database) {
    let users = db.query("users").unwrap();
    assert!(users.contains(&"alice".to_string()));
    assert!(users.contains(&"bob".to_string()));
}

#[rstest]
fn test_missing_table(db: Database) {
    assert!(db.query("comments").is_none());
    assert_eq!(db.count("comments"), 0);
}
```

The `#[fixture]` macro marks a function as a fixture. Any `#[rstest]` test function that has a parameter with the same name and type automatically gets the fixture injected. No manual calls, no ceremony.

### Fixtures with Parameters

Fixtures can depend on other fixtures.

```rust
use rstest::*;

struct Config {
    db_url: String,
    pool_size: usize,
}

struct ConnectionPool {
    url: String,
    size: usize,
    connections: Vec<String>,
}

impl ConnectionPool {
    fn new(url: &str, size: usize) -> Self {
        let connections = (0..size)
            .map(|i| format!("conn_{}", i))
            .collect();
        ConnectionPool {
            url: url.to_string(),
            size,
            connections,
        }
    }

    fn active_count(&self) -> usize {
        self.connections.len()
    }
}

#[fixture]
fn config() -> Config {
    Config {
        db_url: "postgres://localhost/test".to_string(),
        pool_size: 5,
    }
}

#[fixture]
fn pool(config: Config) -> ConnectionPool {
    ConnectionPool::new(&config.db_url, config.pool_size)
}

#[rstest]
fn test_pool_size(pool: ConnectionPool) {
    assert_eq!(pool.active_count(), 5);
}

#[rstest]
fn test_pool_url(pool: ConnectionPool) {
    assert_eq!(pool.url, "postgres://localhost/test");
}
```

`pool` depends on `config`. When a test requests `pool`, rstest creates `config` first, then passes it into `pool`. The dependency chain resolves automatically.

### Parametrized Tests

This is where rstest really shines. Instead of writing five nearly-identical tests, parameterize one:

```rust
use rstest::*;

fn is_palindrome(s: &str) -> bool {
    let cleaned: String = s.chars()
        .filter(|c| c.is_alphanumeric())
        .map(|c| c.to_lowercase().next().unwrap())
        .collect();
    cleaned == cleaned.chars().rev().collect::<String>()
}

#[rstest]
#[case("racecar", true)]
#[case("hello", false)]
#[case("A man a plan a canal Panama", true)]
#[case("", true)]
#[case("a", true)]
#[case("ab", false)]
#[case("Madam", true)]
fn test_palindrome(#[case] input: &str, #[case] expected: bool) {
    assert_eq!(is_palindrome(input), expected, "Failed for input: '{}'", input);
}
```

Seven test cases, one function. Each `#[case]` generates a separate test that shows up individually in the test output. When one fails, you see exactly which input caused it.

### Matrix Testing

You can combine parameters to test all combinations:

```rust
use rstest::*;

fn format_greeting(name: &str, formal: bool, time: &str) -> String {
    let greeting = if formal {
        match time {
            "morning" => "Good morning",
            "evening" => "Good evening",
            _ => "Hello",
        }
    } else {
        match time {
            "morning" => "Morning",
            "evening" => "Hey",
            _ => "Hi",
        }
    };
    format!("{}, {}!", greeting, name)
}

#[rstest]
fn test_greeting(
    #[values("Alice", "Bob")] name: &str,
    #[values(true, false)] formal: bool,
    #[values("morning", "evening", "afternoon")] time: &str,
) {
    let result = format_greeting(name, formal, time);
    assert!(result.contains(name));
    assert!(result.ends_with('!'));
}
```

This generates 2 x 2 x 3 = 12 test cases automatically. Extremely useful for functions with multiple independent parameters.

## Async Fixtures

If you're testing async code, rstest handles that too:

```rust
use rstest::*;

struct AsyncClient {
    base_url: String,
}

impl AsyncClient {
    async fn new(url: &str) -> Self {
        // Simulate async initialization
        AsyncClient {
            base_url: url.to_string(),
        }
    }

    async fn get(&self, path: &str) -> String {
        format!("{}{}", self.base_url, path)
    }
}

#[fixture]
async fn client() -> AsyncClient {
    AsyncClient::new("http://localhost:8080").await
}

#[rstest]
#[tokio::test]
async fn test_client_get(#[future] client: AsyncClient) {
    let client = client.await;
    let url = client.get("/api/users").await;
    assert_eq!(url, "http://localhost:8080/api/users");
}
```

Note the `#[future]` attribute on the parameter — it tells rstest that this fixture is async and needs to be awaited.

## My Fixture Strategy

Here's how I organize fixtures in real projects:

1. **Simple fixtures** go in the test module as plain functions. No crate needed.
2. **RAII cleanup** handles teardown — temp files, temp directories, mock servers.
3. **rstest fixtures** for anything with dependencies or parameterization.
4. **Builder pattern** when fixtures have many optional configurations.
5. **Shared fixtures** across test files go in `tests/common/mod.rs`.

The goal is always the same: each test body should contain *only* the logic being tested. Setup and teardown are infrastructure — they should be invisible.

## What's Next

Fixtures give you reusable test infrastructure. But what about dependencies? When your function calls a database, an API, or a filesystem, you need to swap those real implementations for controlled fakes. That's mocking, and it's next.
