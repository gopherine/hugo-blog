---
title: "Lesson 5: Multi-Crate Workspace Architecture — Scaling your codebase"
author: Atharva Pandey
series: "Rust in Production — Real-World Architecture"
lesson: 5
keywords: [Rust, rust, architecture, production, workspace, multi-crate, cargo, monorepo]
tags: [Rust tutorial, rust, architecture, production]
date: '2025-10-23T16:42:00.000Z'
---

Our compile times hit 8 minutes. Not from scratch — *incremental*. Change one line in the domain model and wait 8 minutes to see if it worked. Three engineers were actively avoiding making changes to shared code because the feedback loop was unbearable.

The problem was obvious: everything lived in one crate. The domain model, the HTTP handlers, the database layer, the gRPC server, the background workers — all sharing one `Cargo.toml` with 47 dependencies. Touch anything and the whole thing recompiles.

Splitting into a workspace took us about a week. Incremental builds dropped to under 30 seconds. And we got some architectural benefits we didn't even plan for.

## When a Single Crate Stops Working

You don't need a workspace from day one. A single crate is fine for most projects up to about 20-30 thousand lines. But there are clear signals that you've outgrown it:

- **Incremental compile times exceed your patience.** For me that's about 30 seconds.
- **You have multiple binaries** (an API server, a CLI tool, a worker process) that share code.
- **Teams own different parts of the codebase** and step on each other.
- **Your dependency graph is contradictory.** The CLI doesn't need `axum` or `sqlx`, but it pulls them in through shared code.
- **Feature flags are getting tangled.** You're using `#[cfg(feature = "...")]` to conditionally compile things that should just be separate crates.

## The Workspace Layout

Here's the structure I've landed on for production services:

```
my-platform/
├── Cargo.toml              # workspace root
├── Cargo.lock              # shared lock file
├── crates/
│   ├── domain/
│   │   ├── Cargo.toml
│   │   └── src/
│   │       ├── lib.rs
│   │       ├── user/
│   │       ├── order/
│   │       └── inventory/
│   ├── infra/
│   │   ├── Cargo.toml
│   │   └── src/
│   │       ├── lib.rs
│   │       ├── postgres/
│   │       ├── redis/
│   │       └── s3/
│   ├── api/
│   │   ├── Cargo.toml
│   │   └── src/
│   │       ├── lib.rs
│   │       ├── handlers/
│   │       ├── middleware/
│   │       └── dto/
│   ├── worker/
│   │   ├── Cargo.toml
│   │   └── src/
│   │       ├── lib.rs
│   │       └── jobs/
│   ├── cli/
│   │   ├── Cargo.toml
│   │   └── src/
│   │       └── main.rs
│   ├── common/
│   │   ├── Cargo.toml
│   │   └── src/
│   │       ├── lib.rs
│   │       ├── config.rs
│   │       ├── telemetry.rs
│   │       └── error.rs
│   └── server/
│       ├── Cargo.toml
│       └── src/
│           └── main.rs       # the actual binary entry point
├── migrations/
├── tests/                    # integration tests
│   ├── Cargo.toml
│   └── src/
└── scripts/
```

The workspace `Cargo.toml` is minimal:

```toml
# Cargo.toml (workspace root)
[workspace]
resolver = "2"
members = [
    "crates/domain",
    "crates/infra",
    "crates/api",
    "crates/worker",
    "crates/cli",
    "crates/common",
    "crates/server",
    "tests",
]

[workspace.package]
version = "0.1.0"
edition = "2021"
authors = ["Your Team"]

[workspace.dependencies]
# Pin versions once, use everywhere
tokio = { version = "1.35", features = ["full"] }
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
uuid = { version = "1.6", features = ["v4", "serde"] }
chrono = { version = "0.4", features = ["serde"] }
tracing = "0.1"
thiserror = "1.0"
anyhow = "1.0"
async-trait = "0.1"
sqlx = { version = "0.7", features = ["runtime-tokio-rustls", "postgres", "uuid", "chrono", "json"] }
axum = "0.7"
reqwest = { version = "0.11", features = ["json"] }
clap = { version = "4.4", features = ["derive"] }
```

The `[workspace.dependencies]` block is crucial. It means every crate in the workspace uses the same version of shared dependencies. No more accidentally having two versions of `serde` because `api/` pinned one and `infra/` pinned another.

## Crate-Level Cargo.toml

Each crate inherits from the workspace and declares only what it needs:

```toml
# crates/domain/Cargo.toml
[package]
name = "myplatform-domain"
version.workspace = true
edition.workspace = true

[dependencies]
uuid.workspace = true
chrono.workspace = true
thiserror.workspace = true
async-trait.workspace = true
serde.workspace = true          # only for event types
```

```toml
# crates/infra/Cargo.toml
[package]
name = "myplatform-infra"
version.workspace = true
edition.workspace = true

[dependencies]
myplatform-domain = { path = "../domain" }
myplatform-common = { path = "../common" }
sqlx.workspace = true
reqwest.workspace = true
async-trait.workspace = true
serde.workspace = true
serde_json.workspace = true
tracing.workspace = true
thiserror.workspace = true
uuid.workspace = true
chrono.workspace = true
```

```toml
# crates/api/Cargo.toml
[package]
name = "myplatform-api"
version.workspace = true
edition.workspace = true

[dependencies]
myplatform-domain = { path = "../domain" }
myplatform-infra = { path = "../infra" }
myplatform-common = { path = "../common" }
axum.workspace = true
tokio.workspace = true
serde.workspace = true
serde_json.workspace = true
tracing.workspace = true
uuid.workspace = true
```

```toml
# crates/server/Cargo.toml
[package]
name = "myplatform-server"
version.workspace = true
edition.workspace = true

[[bin]]
name = "server"
path = "src/main.rs"

[dependencies]
myplatform-api = { path = "../api" }
myplatform-infra = { path = "../infra" }
myplatform-common = { path = "../common" }
myplatform-domain = { path = "../domain" }
tokio.workspace = true
tracing.workspace = true
anyhow.workspace = true
```

Notice the dependency direction: `server` depends on `api`, which depends on `domain`. `domain` depends on nothing internal. This is the dependency rule — dependencies point inward.

## The Dependency Rule, Enforced

Here's the critical constraint:

```
domain ← infra ← api ← server
domain ← common
                  ↑
               worker
                  ↑
               cli
```

`domain` never imports from `infra`, `api`, or any other crate. If it did, Cargo would let you — it's just a `path` dependency. So how do you enforce this?

First, just don't add the dependency. The `domain/Cargo.toml` doesn't have `myplatform-infra` in its `[dependencies]`, so it physically cannot import from it.

Second, add a CI check:

```bash
#!/bin/bash
# scripts/check-deps.sh

# Domain crate should not depend on infra or api
if cargo tree -p myplatform-domain | grep -q "myplatform-infra\|myplatform-api"; then
    echo "ERROR: domain crate has forbidden dependencies"
    exit 1
fi

# Common crate should not depend on domain, infra, or api
if cargo tree -p myplatform-common | grep -q "myplatform-domain\|myplatform-infra\|myplatform-api"; then
    echo "ERROR: common crate has forbidden dependencies"
    exit 1
fi

echo "Dependency rules OK"
```

Three lines in CI. Saves you from architectural erosion that would take months to fix.

## The `common` Crate — Done Right

Every workspace needs shared utilities. The trick is keeping this crate from becoming a dumping ground:

```rust
// crates/common/src/lib.rs

pub mod config;
pub mod telemetry;
pub mod error;
```

```rust
// crates/common/src/config.rs

use serde::Deserialize;

#[derive(Debug, Deserialize)]
pub struct AppConfig {
    pub server: ServerConfig,
    pub database: DatabaseConfig,
    pub redis: Option<RedisConfig>,
    pub telemetry: TelemetryConfig,
}

#[derive(Debug, Deserialize)]
pub struct ServerConfig {
    pub host: String,
    pub port: u16,
}

#[derive(Debug, Deserialize)]
pub struct DatabaseConfig {
    pub url: String,
    pub max_connections: u32,
    pub min_connections: u32,
}

#[derive(Debug, Deserialize)]
pub struct RedisConfig {
    pub url: String,
}

#[derive(Debug, Deserialize)]
pub struct TelemetryConfig {
    pub log_level: String,
    pub otlp_endpoint: Option<String>,
}

impl AppConfig {
    pub fn load() -> Result<Self, config::ConfigError> {
        let env = std::env::var("APP_ENV").unwrap_or_else(|_| "development".into());

        config::Config::builder()
            .add_source(config::File::with_name("config/default"))
            .add_source(config::File::with_name(&format!("config/{}", env)).required(false))
            .add_source(config::Environment::with_prefix("APP").separator("__"))
            .build()?
            .try_deserialize()
    }
}
```

Rules for `common`:
- Configuration loading — yes.
- Telemetry setup — yes.
- Cross-cutting error types — yes.
- Business logic — **no.** That belongs in `domain`.
- Database code — **no.** That belongs in `infra`.

If you find yourself importing `common` in every file, something is leaking.

## Compile Time Improvements

The whole reason we're here. Let's measure. In a single-crate project:

```
$ cargo build (after changing domain model)
   Compiling myapp v0.1.0
   Finished dev in 8m 12s
```

In a workspace:

```
$ cargo build (after changing domain crate)
   Compiling myplatform-domain v0.1.0
   Compiling myplatform-infra v0.1.0
   Compiling myplatform-api v0.1.0
   Compiling myplatform-server v0.1.0
   Finished dev in 28s
```

Why the massive improvement? Cargo can skip recompiling crates that haven't changed. Change a handler in `api/`? Only `api/` and `server/` recompile. Change `common/config.rs`? Everything recompiles, but that's rare.

The other win: **parallel compilation.** Independent crates compile simultaneously. If `worker/` and `api/` don't depend on each other, they build in parallel.

Some additional tricks:

```toml
# .cargo/config.toml
[build]
# Use all available cores
jobs = 8

[profile.dev]
# Faster debug builds — less optimization
opt-level = 0

[profile.dev.package."*"]
# But optimize dependencies — they don't change often
opt-level = 2
```

This tells Cargo: "don't optimize my code in dev (fast compile), but do optimize dependencies (fast runtime for things like serde and sqlx)."

## Testing Across Crates

Unit tests live in each crate. Integration tests get their own crate:

```toml
# tests/Cargo.toml
[package]
name = "myplatform-tests"
version.workspace = true
edition.workspace = true

[dev-dependencies]
myplatform-domain = { path = "../crates/domain" }
myplatform-infra = { path = "../crates/infra" }
myplatform-api = { path = "../crates/api" }
myplatform-common = { path = "../crates/common" }
tokio.workspace = true
sqlx.workspace = true
reqwest.workspace = true
```

```rust
// tests/src/helpers.rs

use myplatform_common::config::AppConfig;
use myplatform_infra::postgres::create_pool;
use sqlx::PgPool;

pub struct TestContext {
    pub pool: PgPool,
    pub base_url: String,
}

impl TestContext {
    pub async fn new() -> Self {
        let config = AppConfig::load_test().expect("test config");
        let pool = create_pool(&config.database).await.expect("test pool");

        // Run migrations
        sqlx::migrate!("../migrations")
            .run(&pool)
            .await
            .expect("migrations");

        Self {
            pool,
            base_url: format!("http://{}:{}", config.server.host, config.server.port),
        }
    }

    pub async fn cleanup(&self) {
        sqlx::query("TRUNCATE users, orders, events CASCADE")
            .execute(&self.pool)
            .await
            .expect("cleanup");
    }
}
```

Running tests:

```bash
# Test just the domain (fast — no database needed)
cargo test -p myplatform-domain

# Test infra (needs database)
cargo test -p myplatform-infra

# Run integration tests
cargo test -p myplatform-tests

# Test everything
cargo test --workspace
```

The `-p` flag is your friend. During development, you almost never need `cargo test --workspace`. Test the crate you're working on.

## Feature Flags Across Crates

Sometimes you need optional functionality. Use Cargo features at the crate level:

```toml
# crates/infra/Cargo.toml
[features]
default = ["postgres"]
postgres = ["sqlx"]
redis = ["redis-rs"]
s3 = ["aws-sdk-s3"]

[dependencies]
sqlx = { workspace = true, optional = true }
redis-rs = { version = "0.24", optional = true }
aws-sdk-s3 = { version = "1.0", optional = true }
```

```toml
# crates/server/Cargo.toml
[dependencies]
myplatform-infra = { path = "../infra", features = ["postgres", "redis"] }

# crates/cli/Cargo.toml
[dependencies]
myplatform-infra = { path = "../infra", features = ["postgres"] }
# CLI doesn't need redis or s3
```

The CLI binary doesn't pull in Redis or S3 dependencies. Smaller binary, faster compilation.

## Publishing Internal Crates

If multiple repositories need your domain crate, you can publish it to a private registry:

```toml
# crates/domain/Cargo.toml
[package]
name = "myplatform-domain"
version = "0.3.0"    # override workspace version for independent versioning
publish = ["my-private-registry"]
```

But honestly? For most teams, a monorepo with a workspace is simpler than managing published crates. You lose lock-step versioning and atomic commits across crates when you split repositories. Only split when you have a genuine organizational need.

## The Practical Checklist

Before you restructure your codebase into a workspace, make sure:

1. **You actually have the compile time problem.** Measure first: `cargo build --timings`.
2. **You know your dependency direction.** Draw it on a whiteboard. If you have cycles, fix the architecture before splitting crates.
3. **You have CI.** A workspace with 7 crates and no CI is 7 places for things to break silently.
4. **Your team agrees on boundaries.** A workspace enforces module boundaries through `Cargo.toml` — but someone has to decide where the boundaries are.

The workspace itself is just plumbing. The architecture is the hard part. Get the boundaries right first, then reach for the workspace to enforce them.

Next up: API versioning and backwards compatibility — because the hardest part of production Rust isn't writing the code, it's not breaking the people who depend on it.
