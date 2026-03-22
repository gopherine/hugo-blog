---
title: "Lesson 22: Feature Flags and Conditional Compilation — cfg and features"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 22
keywords: [Rust, rust, idiomatic, cfg, features, conditional compilation, feature flags]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-05-16T12:45:00.000Z'
---

I once worked on a crate that pulled in 200+ transitive dependencies because it unconditionally depended on `tokio`, `serde`, `reqwest`, and `tracing`. Most users only needed one of those. The compile time was brutal, and the binary was enormous.

Feature flags solve this. They let users opt into functionality they need and skip everything else. It's the "pay for what you use" principle applied to compilation.

---

## cfg — Conditional Compilation

The `#[cfg(...)]` attribute conditionally includes code based on compile-time configuration:

```rust
#[cfg(target_os = "linux")]
fn platform_specific() {
    println!("Running on Linux");
}

#[cfg(target_os = "macos")]
fn platform_specific() {
    println!("Running on macOS");
}

#[cfg(target_os = "windows")]
fn platform_specific() {
    println!("Running on Windows");
}

fn main() {
    platform_specific();
}
```

The non-matching versions are completely removed from compilation — they don't exist in the binary.

### Common cfg predicates

```rust
// Operating system
#[cfg(target_os = "linux")]
#[cfg(target_os = "macos")]
#[cfg(target_os = "windows")]

// Unix-like (Linux, macOS, BSDs)
#[cfg(unix)]
#[cfg(windows)]

// Architecture
#[cfg(target_arch = "x86_64")]
#[cfg(target_arch = "aarch64")]

// Build profile
#[cfg(debug_assertions)]  // debug builds
#[cfg(not(debug_assertions))]  // release builds

// Test mode
#[cfg(test)]
```

---

## cfg_attr — Conditional Attributes

`cfg_attr` applies an attribute only when a condition is met:

```rust
// Only derive Serialize in release builds (hypothetical example)
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[derive(Debug, Clone)]
struct Config {
    host: String,
    port: u16,
}

fn main() {
    let c = Config { host: "localhost".into(), port: 8080 };
    println!("{:?}", c);
}
```

This is incredibly useful — the `serde` dependency and derive macro are only compiled when the `serde` feature is enabled.

---

## Feature Flags in Cargo.toml

Features are declared in your `Cargo.toml`:

```toml
[package]
name = "my-crate"
version = "0.1.0"

[features]
default = ["json"]     # enabled by default
json = ["dep:serde", "dep:serde_json"]
yaml = ["dep:serde", "dep:serde_yaml"]
async = ["dep:tokio"]
full = ["json", "yaml", "async"]

[dependencies]
serde = { version = "1", features = ["derive"], optional = true }
serde_json = { version = "1", optional = true }
serde_yaml = { version = "0.9", optional = true }
tokio = { version = "1", features = ["full"], optional = true }
```

Key points:
- `optional = true` makes a dependency only compile when its feature is enabled.
- `dep:serde` syntax (Rust 2021+) explicitly ties a feature to a dependency.
- `default` features are enabled unless the user says `default-features = false`.
- Features are additive — enabling more features can never remove functionality.

---

## Using Features in Code

```rust
#[derive(Debug, Clone)]
#[cfg_attr(feature = "json", derive(serde::Serialize, serde::Deserialize))]
pub struct User {
    pub name: String,
    pub email: String,
}

impl User {
    pub fn new(name: &str, email: &str) -> Self {
        User {
            name: name.to_string(),
            email: email.to_string(),
        }
    }
}

#[cfg(feature = "json")]
impl User {
    pub fn to_json(&self) -> Result<String, serde_json::Error> {
        serde_json::to_string_pretty(self)
    }

    pub fn from_json(json: &str) -> Result<Self, serde_json::Error> {
        serde_json::from_str(json)
    }
}

#[cfg(feature = "async")]
impl User {
    pub async fn fetch(url: &str) -> Result<Self, Box<dyn std::error::Error>> {
        let body = reqwest::get(url).await?.text().await?;
        let user: User = serde_json::from_str(&body)?;
        Ok(user)
    }
}

fn main() {
    let user = User::new("Atharva", "atharva@example.com");
    println!("{:?}", user);

    #[cfg(feature = "json")]
    {
        let json = user.to_json().unwrap();
        println!("JSON: {}", json);
    }
}
```

Without the `json` feature, the serde derive, `to_json()`, and `from_json()` methods don't exist. The serde dependency isn't compiled. The binary is smaller.

---

## Runtime Feature Detection with cfg!

The `cfg!()` macro (note: lowercase, no `#`) returns a boolean at runtime. The code is still compiled — it's just a branch:

```rust
fn main() {
    if cfg!(debug_assertions) {
        println!("Debug build — extra checks enabled");
    } else {
        println!("Release build — optimized");
    }

    if cfg!(target_os = "macos") {
        println!("Running on macOS");
    }

    // This is compiled on all platforms — it's just a runtime branch
    // The optimizer will remove the dead branch in release builds
}
```

The difference:
- `#[cfg(...)]` — code is conditionally *compiled*. Dead code doesn't exist.
- `cfg!(...)` — code is always compiled. Returns `true` or `false` at runtime (optimizer may eliminate dead branches).

Use `#[cfg]` for platform-specific types and imports. Use `cfg!()` for simple runtime branching where both sides are valid on all platforms.

---

## Feature Flag Design Patterns

### Pattern 1: Optional dependency features

```toml
[features]
default = []
serde = ["dep:serde"]
tracing = ["dep:tracing"]
```

```rust
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[derive(Debug, Clone)]
pub struct Event {
    pub name: String,
    pub timestamp: u64,
}

pub fn process_event(event: &Event) {
    #[cfg(feature = "tracing")]
    tracing::info!("Processing event: {}", event.name);

    #[cfg(not(feature = "tracing"))]
    println!("Processing event: {}", event.name);
}
```

### Pattern 2: Backend selection

```toml
[features]
default = ["sqlite"]
sqlite = ["dep:rusqlite"]
postgres = ["dep:tokio-postgres"]
```

```rust
#[cfg(feature = "sqlite")]
mod sqlite_backend;

#[cfg(feature = "postgres")]
mod postgres_backend;

pub trait Database {
    fn query(&self, sql: &str) -> Vec<String>;
}

#[cfg(feature = "sqlite")]
pub fn default_database() -> impl Database {
    sqlite_backend::SqliteDb::new()
}

#[cfg(feature = "postgres")]
pub fn default_database() -> impl Database {
    postgres_backend::PostgresDb::new()
}
```

### Pattern 3: Debug/development helpers

```rust
#[derive(Debug)]
pub struct Parser {
    input: String,
    position: usize,
}

impl Parser {
    pub fn parse(&mut self) -> Result<Vec<String>, String> {
        let mut tokens = Vec::new();

        while self.position < self.input.len() {
            #[cfg(feature = "debug-parser")]
            eprintln!("[PARSER] pos={}, remaining='{}'",
                self.position, &self.input[self.position..]);

            if let Some(token) = self.next_token() {
                tokens.push(token);
            }
        }
        Ok(tokens)
    }

    fn next_token(&mut self) -> Option<String> {
        // simplified
        let rest = &self.input[self.position..];
        let end = rest.find(' ').unwrap_or(rest.len());
        if end == 0 {
            self.position += 1;
            return None;
        }
        let token = rest[..end].to_string();
        self.position += end;
        Some(token)
    }
}
```

---

## Testing With Features

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_basic() {
        // Always runs
        assert_eq!(2 + 2, 4);
    }

    #[test]
    #[cfg(feature = "json")]
    fn test_json_serialization() {
        let user = User::new("test", "test@test.com");
        let json = user.to_json().unwrap();
        let parsed = User::from_json(&json).unwrap();
        assert_eq!(user.name, parsed.name);
    }
}
```

Run feature-specific tests with:

```bash
cargo test --features json
cargo test --all-features
cargo test --no-default-features
```

---

## Common Mistakes

### Don't make features subtractive

Features should only *add* functionality. Never remove something when a feature is enabled.

```rust
// BAD: feature removes functionality
// #[cfg(not(feature = "minimal"))]
// pub fn advanced_feature() { ... }

// GOOD: feature adds functionality
#[cfg(feature = "advanced")]
pub fn advanced_feature() {
    println!("Advanced!");
}
```

### Test all feature combinations

The most common source of breakage: code that compiles with `--all-features` but not with `--no-default-features` (or vice versa).

```bash
# In CI, test multiple combinations:
cargo check --no-default-features
cargo check --features json
cargo check --features yaml
cargo check --all-features
cargo test --all-features
```

### Don't over-feature

Not everything needs a feature flag. If a function adds minimal compile time and most users will need it, just include it unconditionally. Feature flags add maintenance burden — every feature doubles your testing matrix.

---

## Key Takeaways

- `#[cfg(...)]` removes code from compilation based on conditions — platform, feature, build profile.
- Feature flags in `Cargo.toml` let users opt into optional functionality and dependencies.
- Use `optional = true` on dependencies and `dep:name` in feature definitions.
- Features must be additive — enabling a feature should never remove functionality.
- `cfg!()` (lowercase, runtime) returns a boolean. `#[cfg()]` (attribute, compile-time) removes code entirely.
- Test all feature combinations in CI: `--no-default-features`, individual features, `--all-features`.
- Don't over-feature — feature flags have a maintenance cost. Only use them for genuinely optional heavy dependencies or platform-specific code.
