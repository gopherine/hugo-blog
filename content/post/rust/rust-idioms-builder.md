---
title: "Lesson 8: The Builder Pattern in Rust — Ergonomic construction"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 8
keywords: [Rust, rust, idiomatic, builder pattern, construction, API design]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-04-22T19:42:00.000Z'
---

Every Rust dev eventually runs into the "struct with 12 fields" problem. You've got a configuration type. Some fields are required, some are optional, some have sensible defaults. In Java, you'd use a Builder. In Python, you'd use keyword arguments. In Go, you'd use functional options.

In Rust? You have options. And one of them is clearly better than the rest.

---

## The Problem: Constructor Explosion

```rust
struct ServerConfig {
    host: String,
    port: u16,
    max_connections: usize,
    timeout_secs: u64,
    tls_enabled: bool,
    log_level: String,
    workers: usize,
}

// This is painful
fn main() {
    let config = ServerConfig {
        host: String::from("0.0.0.0"),
        port: 8080,
        max_connections: 1000,
        timeout_secs: 30,
        tls_enabled: false,
        log_level: String::from("info"),
        workers: 4,
    };
}
```

Every field must be specified. There's no concept of "default." If you add a new field later, every construction site breaks. And callers have no idea which values are "important" and which are "just use the default."

---

## Approach 1: Default + Struct Update Syntax

Rust's `Default` trait combined with struct update syntax (`..`) handles many cases:

```rust
#[derive(Debug)]
struct ServerConfig {
    host: String,
    port: u16,
    max_connections: usize,
    timeout_secs: u64,
    tls_enabled: bool,
    log_level: String,
    workers: usize,
}

impl Default for ServerConfig {
    fn default() -> Self {
        ServerConfig {
            host: String::from("0.0.0.0"),
            port: 8080,
            max_connections: 1000,
            timeout_secs: 30,
            tls_enabled: false,
            log_level: String::from("info"),
            workers: num_cpus(),
        }
    }
}

fn num_cpus() -> usize {
    4 // simplified
}

fn main() {
    // Override only what you need
    let config = ServerConfig {
        port: 3000,
        tls_enabled: true,
        ..Default::default()
    };
    println!("{:?}", config);
}
```

This is good for simple cases. But it requires all fields to be public, doesn't support validation, and can't enforce that certain fields are always provided.

---

## Approach 2: The Builder Pattern

For anything non-trivial, use a builder. Here's the standard Rust approach:

```rust
#[derive(Debug)]
struct ServerConfig {
    host: String,
    port: u16,
    max_connections: usize,
    timeout_secs: u64,
    tls_enabled: bool,
    log_level: String,
    workers: usize,
}

struct ServerConfigBuilder {
    host: String,
    port: u16,
    max_connections: usize,
    timeout_secs: u64,
    tls_enabled: bool,
    log_level: String,
    workers: usize,
}

impl ServerConfigBuilder {
    fn new(host: &str, port: u16) -> Self {
        ServerConfigBuilder {
            host: host.to_string(),
            port,
            max_connections: 1000,
            timeout_secs: 30,
            tls_enabled: false,
            log_level: String::from("info"),
            workers: 4,
        }
    }

    fn max_connections(mut self, n: usize) -> Self {
        self.max_connections = n;
        self
    }

    fn timeout_secs(mut self, secs: u64) -> Self {
        self.timeout_secs = secs;
        self
    }

    fn tls(mut self, enabled: bool) -> Self {
        self.tls_enabled = enabled;
        self
    }

    fn log_level(mut self, level: &str) -> Self {
        self.log_level = level.to_string();
        self
    }

    fn workers(mut self, n: usize) -> Self {
        self.workers = n;
        self
    }

    fn build(self) -> ServerConfig {
        ServerConfig {
            host: self.host,
            port: self.port,
            max_connections: self.max_connections,
            timeout_secs: self.timeout_secs,
            tls_enabled: self.tls_enabled,
            log_level: self.log_level,
            workers: self.workers,
        }
    }
}

fn main() {
    let config = ServerConfigBuilder::new("0.0.0.0", 8080)
        .tls(true)
        .workers(8)
        .timeout_secs(60)
        .build();

    println!("{:?}", config);
}
```

Key design decisions:
- **Required fields go in `new()`** — you can't construct the builder without them.
- **Optional fields have defaults** — only override what you need.
- **Each setter takes `mut self` and returns `Self`** — enabling method chaining.
- **`build()` consumes the builder** — preventing reuse of a half-configured builder.

---

## Builder With Validation

The real power of builders: validation at build time.

```rust
#[derive(Debug)]
struct DatabaseConfig {
    url: String,
    pool_size: u32,
    connect_timeout_ms: u64,
    idle_timeout_ms: u64,
}

#[derive(Debug)]
enum ConfigError {
    InvalidPoolSize(String),
    InvalidTimeout(String),
    MissingUrl,
}

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ConfigError::InvalidPoolSize(msg) => write!(f, "Invalid pool size: {}", msg),
            ConfigError::InvalidTimeout(msg) => write!(f, "Invalid timeout: {}", msg),
            ConfigError::MissingUrl => write!(f, "Database URL is required"),
        }
    }
}

struct DatabaseConfigBuilder {
    url: Option<String>,
    pool_size: u32,
    connect_timeout_ms: u64,
    idle_timeout_ms: u64,
}

impl DatabaseConfigBuilder {
    fn new() -> Self {
        DatabaseConfigBuilder {
            url: None,
            pool_size: 10,
            connect_timeout_ms: 5000,
            idle_timeout_ms: 300_000,
        }
    }

    fn url(mut self, url: &str) -> Self {
        self.url = Some(url.to_string());
        self
    }

    fn pool_size(mut self, size: u32) -> Self {
        self.pool_size = size;
        self
    }

    fn connect_timeout_ms(mut self, ms: u64) -> Self {
        self.connect_timeout_ms = ms;
        self
    }

    fn idle_timeout_ms(mut self, ms: u64) -> Self {
        self.idle_timeout_ms = ms;
        self
    }

    fn build(self) -> Result<DatabaseConfig, ConfigError> {
        let url = self.url.ok_or(ConfigError::MissingUrl)?;

        if self.pool_size == 0 || self.pool_size > 100 {
            return Err(ConfigError::InvalidPoolSize(
                format!("must be 1-100, got {}", self.pool_size)
            ));
        }

        if self.connect_timeout_ms < 100 {
            return Err(ConfigError::InvalidTimeout(
                format!("connect timeout must be >= 100ms, got {}", self.connect_timeout_ms)
            ));
        }

        Ok(DatabaseConfig {
            url,
            pool_size: self.pool_size,
            connect_timeout_ms: self.connect_timeout_ms,
            idle_timeout_ms: self.idle_timeout_ms,
        })
    }
}

fn main() {
    // Good
    let config = DatabaseConfigBuilder::new()
        .url("postgres://localhost/mydb")
        .pool_size(20)
        .build();
    println!("{:?}", config);

    // Bad — missing URL
    let bad = DatabaseConfigBuilder::new()
        .pool_size(5)
        .build();
    println!("{:?}", bad);

    // Bad — invalid pool size
    let bad2 = DatabaseConfigBuilder::new()
        .url("postgres://localhost/mydb")
        .pool_size(0)
        .build();
    println!("{:?}", bad2);
}
```

The `build()` method returns `Result` — validation errors are caught before the config is used. You can't construct an invalid `DatabaseConfig` through the builder.

---

## The `&mut self` vs `mut self` Decision

I showed builders that take `mut self` (consuming the builder). Some codebases use `&mut self` instead:

```rust
struct QueryBuilder {
    table: String,
    conditions: Vec<String>,
    limit: Option<usize>,
}

impl QueryBuilder {
    fn new(table: &str) -> Self {
        QueryBuilder {
            table: table.to_string(),
            conditions: Vec::new(),
            limit: None,
        }
    }

    // &mut self — builder is reusable
    fn where_clause(&mut self, condition: &str) -> &mut Self {
        self.conditions.push(condition.to_string());
        self
    }

    fn limit(&mut self, n: usize) -> &mut Self {
        self.limit = Some(n);
        self
    }

    fn build(&self) -> String {
        let mut query = format!("SELECT * FROM {}", self.table);
        if !self.conditions.is_empty() {
            query.push_str(" WHERE ");
            query.push_str(&self.conditions.join(" AND "));
        }
        if let Some(limit) = self.limit {
            query.push_str(&format!(" LIMIT {}", limit));
        }
        query
    }
}

fn main() {
    let mut builder = QueryBuilder::new("users");
    builder.where_clause("age > 18").where_clause("active = true").limit(10);

    let query = builder.build();
    println!("{}", query);

    // Builder is still usable — can build another query
    builder.where_clause("verified = true");
    let query2 = builder.build();
    println!("{}", query2);
}
```

**My recommendation:** Use `mut self` (consuming) for configuration builders — you typically build once. Use `&mut self` for things like query builders where you might want to incrementally build or reuse.

---

## Skip the Builder: When Defaults and `new()` Suffice

Not everything needs a builder. For structs with 3-4 fields and no complex validation, a constructor is fine:

```rust
struct Point {
    x: f64,
    y: f64,
}

impl Point {
    fn new(x: f64, y: f64) -> Self {
        Point { x, y }
    }

    fn origin() -> Self {
        Point { x: 0.0, y: 0.0 }
    }
}
```

And for structs where every field has sensible defaults, `Default` + struct update is simpler than a builder:

```rust
#[derive(Debug)]
struct Pagination {
    page: usize,
    per_page: usize,
    sort_by: String,
    ascending: bool,
}

impl Default for Pagination {
    fn default() -> Self {
        Pagination {
            page: 1,
            per_page: 20,
            sort_by: String::from("created_at"),
            ascending: false,
        }
    }
}

fn main() {
    let page = Pagination {
        page: 3,
        ..Default::default()
    };
    println!("{:?}", page);
}
```

---

## The `derive_builder` Crate

For the common case where you want a standard builder without writing the boilerplate, the `derive_builder` crate generates it for you:

```rust
// In Cargo.toml: derive_builder = "0.12"

// use derive_builder::Builder;
//
// #[derive(Builder, Debug)]
// #[builder(setter(into))]
// struct ServerConfig {
//     host: String,
//     port: u16,
//     #[builder(default = "1000")]
//     max_connections: usize,
//     #[builder(default = "30")]
//     timeout_secs: u64,
// }
```

I use this for internal code where I want builders quickly. For public APIs, I prefer hand-written builders — you get more control over the interface and better documentation.

---

## Key Takeaways

- Use `Default` + struct update syntax for simple configuration structs.
- Use the builder pattern when you need validation, complex defaults, or a clean API for structs with many fields.
- Required fields go in the builder's `new()`. Optional fields have defaults and chainable setters.
- `build()` should return `Result` when validation is needed.
- Use consuming builders (`mut self`) for one-shot construction, borrowing builders (`&mut self`) for reusable/incremental building.
- Not everything needs a builder — sometimes `new()` with 3 parameters is perfectly clear.
