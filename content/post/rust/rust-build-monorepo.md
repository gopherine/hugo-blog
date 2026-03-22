---
title: "Lesson 6: Monorepo Management with Workspaces — Scaling Rust projects"
author: Atharva Pandey
series: "Rust Compiler Plugins & Build System"
lesson: 6
keywords: [Rust, rust, cargo, build system, monorepo, workspaces, multi-crate, project structure]
tags: [Rust tutorial, rust, cargo, build-system]
date: '2025-08-12T13:10:00.000Z'
---

Our Rust project started as a single crate. Then we split the API handlers from the domain logic. Then we extracted shared types. Then someone added a CLI tool. Then a worker service. Before we knew it, we had nine crates and `cargo build` was doing weird things because three of them depended on different versions of `serde`. That's when we sat down and properly set up a workspace.

I've now managed Rust workspaces ranging from 5 crates to over 40. The patterns I'm going to share come from real mistakes — dependency hell, circular imports, CI builds that took 45 minutes, the works.

## Workspace Architecture

A well-structured workspace follows the dependency inversion principle — high-level crates depend on low-level crates, never the reverse. Here's the layout I use for most projects:

```
my-platform/
├── Cargo.toml                  # Workspace root
├── Cargo.lock                  # Single lockfile for everything
├── .cargo/
│   └── config.toml             # Shared cargo config
├── rust-toolchain.toml         # Pin Rust version
├── clippy.toml                 # Shared lint config
├── xtask/                      # Build automation
│   ├── Cargo.toml
│   └── src/main.rs
├── crates/
│   ├── core/                   # Domain logic, zero external deps
│   │   ├── Cargo.toml
│   │   └── src/lib.rs
│   ├── types/                  # Shared types, DTOs
│   │   ├── Cargo.toml
│   │   └── src/lib.rs
│   ├── db/                     # Database layer
│   │   ├── Cargo.toml
│   │   └── src/lib.rs
│   ├── auth/                   # Authentication
│   │   ├── Cargo.toml
│   │   └── src/lib.rs
│   ├── api/                    # HTTP handlers
│   │   ├── Cargo.toml
│   │   └── src/lib.rs
│   └── derive/                 # Proc macro crate
│       ├── Cargo.toml
│       └── src/lib.rs
├── services/
│   ├── server/                 # Main API server (binary)
│   │   ├── Cargo.toml
│   │   └── src/main.rs
│   ├── worker/                 # Background job worker (binary)
│   │   ├── Cargo.toml
│   │   └── src/main.rs
│   └── cli/                    # CLI tool (binary)
│       ├── Cargo.toml
│       └── src/main.rs
└── tests/
    └── integration/            # Cross-crate integration tests
        ├── Cargo.toml
        └── tests/
```

```toml
# Root Cargo.toml
[workspace]
members = [
    "crates/*",
    "services/*",
    "tests/*",
    "xtask",
]
resolver = "2"

[workspace.package]
version = "0.8.0"
edition = "2021"
authors = ["Your Team"]
license = "MIT"
repository = "https://github.com/your-org/my-platform"

[workspace.dependencies]
# Internal crates — reference by path
myapp-core = { path = "crates/core" }
myapp-types = { path = "crates/types" }
myapp-db = { path = "crates/db" }
myapp-auth = { path = "crates/auth" }
myapp-api = { path = "crates/api" }
myapp-derive = { path = "crates/derive" }

# External dependencies — single version for the whole workspace
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
tokio = { version = "1", features = ["full"] }
sqlx = { version = "0.7", features = ["runtime-tokio", "postgres", "uuid", "chrono"] }
axum = "0.7"
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter", "json"] }
anyhow = "1.0"
thiserror = "1.0"
uuid = { version = "1", features = ["v4", "serde"] }
chrono = { version = "0.4", features = ["serde"] }
clap = { version = "4", features = ["derive"] }
reqwest = { version = "0.11", features = ["json"] }
tower = "0.4"
tower-http = { version = "0.5", features = ["cors", "trace"] }

# Dev/test dependencies
assert_matches = "1.5"
mockall = "0.12"
wiremock = "0.6"
testcontainers = "0.15"

[workspace.lints.clippy]
unwrap_used = "deny"
expect_used = "warn"
pedantic = { level = "warn", priority = -1 }
module_name_repetitions = "allow"

[workspace.lints.rust]
unsafe_code = "deny"
```

Every member crate pulls from workspace dependencies:

```toml
# crates/core/Cargo.toml
[package]
name = "myapp-core"
version.workspace = true
edition.workspace = true

[lints]
workspace = true

[dependencies]
thiserror = { workspace = true }
uuid = { workspace = true }
chrono = { workspace = true }
# Notice: core has NO framework dependencies — no tokio, no axum, no sqlx
```

```toml
# services/server/Cargo.toml
[package]
name = "myapp-server"
version.workspace = true
edition.workspace = true

[lints]
workspace = true

[dependencies]
myapp-core = { workspace = true }
myapp-types = { workspace = true }
myapp-db = { workspace = true }
myapp-auth = { workspace = true }
myapp-api = { workspace = true }
tokio = { workspace = true }
axum = { workspace = true }
tracing = { workspace = true }
tracing-subscriber = { workspace = true }
tower = { workspace = true }
tower-http = { workspace = true }
```

## The Dependency Layer Cake

The most important architectural decision in a workspace is the dependency direction. I follow this strict ordering:

```
Layer 0: types          (zero deps — just structs, enums, newtypes)
Layer 1: core           (depends on: types)
Layer 2: db, auth       (depends on: core, types)
Layer 3: api            (depends on: core, types, db, auth)
Layer 4: server, worker (depends on: everything above)
```

Rules:

1. **Lower layers never depend on higher layers.** `core` cannot import from `api`. Ever.
2. **Layers at the same level can depend on each other, but be careful.** If `db` and `auth` depend on each other, you might have a design problem.
3. **Binary crates (services) are always at the top.** They wire everything together.
4. **The `types` crate has no logic.** Just data structures, serialization, and maybe validation. This makes it safe for everything to depend on.

### Why This Matters

Circular dependencies are a compile error in Rust. Unlike some languages where you can have cyclic imports at the module level, Cargo will flat-out refuse to build a crate graph with cycles. This sounds annoying but it's actually a gift — it forces clean architecture.

When I'm tempted to add a dependency from a lower layer to a higher one, that's a code smell. It usually means I need to:
- Extract a shared trait or type into `core` or `types`
- Use dependency injection (pass a trait object or closure instead of importing directly)
- Restructure the code so the dependency flows downward

```rust
// BAD: db crate importing from api crate
// This creates a circular dependency

// GOOD: define the interface in core, implement in db, use in api
// crates/core/src/lib.rs
pub trait UserRepository: Send + Sync {
    async fn find_by_id(&self, id: UserId) -> Result<User, RepositoryError>;
    async fn create(&self, user: NewUser) -> Result<User, RepositoryError>;
}

// crates/db/src/lib.rs
use myapp_core::UserRepository;

pub struct PgUserRepository { pool: PgPool }

impl UserRepository for PgUserRepository {
    async fn find_by_id(&self, id: UserId) -> Result<User, RepositoryError> {
        // sqlx query here
    }
    // ...
}

// crates/api/src/lib.rs
use myapp_core::UserRepository;

pub async fn get_user<R: UserRepository>(
    repo: &R,
    id: UserId,
) -> Result<UserResponse, ApiError> {
    let user = repo.find_by_id(id).await?;
    Ok(user.into())
}
```

## Dependency Version Management

One of the biggest workspace headaches is dependency version conflicts. Without `[workspace.dependencies]`, each crate declares its own versions:

```toml
# crate A: serde = "1.0.180"
# crate B: serde = "1.0.190"
# crate C: serde = "1.0.175"
```

Cargo resolves these to a single version (the highest compatible one), but it's messy. Someone upgrades serde in one crate and doesn't test the others. The fix: centralize everything in the workspace root.

When you need to upgrade a dependency, you change one line in the root `Cargo.toml` and run `cargo test --workspace`. Done.

### The Lockfile

A workspace has exactly one `Cargo.lock` at the root. This is crucial — it means every crate in the workspace uses the exact same resolved dependency versions. Commit the lockfile for applications (binaries). For library-only workspaces, the convention is debatable, but I commit it anyway for reproducibility.

## Build Performance at Scale

With 20+ crates, build times can get painful. Here's what I do:

### Parallel Compilation

Cargo already compiles independent crates in parallel. But it can only do this if your dependency graph allows it. A wide, shallow graph compiles faster than a deep, narrow one.

```
// Slow: deep chain — each must wait for the previous
types → core → db → api → server

// Faster: wide graph — db, auth, api can compile in parallel
         ┌─ db ──┐
types → core ─┤        ├─ server
         └─ auth ┘
         └─ api ──┘
```

Design your crate boundaries to maximize parallelism. If `api` doesn't actually depend on `db`, don't make it depend on `db`.

### Selective Testing

Don't run all tests on every change:

```bash
# Only test crates affected by your changes
cargo test -p myapp-core -p myapp-api

# Test everything (CI only)
cargo test --workspace

# Test a specific binary
cargo test -p myapp-server --test integration_tests
```

### Caching

In CI, cache the `target/` directory and `~/.cargo/registry`:

```yaml
# GitHub Actions
- uses: actions/cache@v4
  with:
    path: |
      ~/.cargo/registry/index/
      ~/.cargo/registry/cache/
      ~/.cargo/git/db/
      target/
    key: ${{ runner.os }}-cargo-${{ hashFiles('**/Cargo.lock') }}
    restore-keys: |
      ${{ runner.os }}-cargo-
```

Also consider `sccache` for distributed compilation caching across CI runs:

```toml
# .cargo/config.toml
[build]
rustc-wrapper = "sccache"
```

### Splitting CI Jobs

For large workspaces, split CI into parallel jobs:

```yaml
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - run: cargo check --workspace --all-features

  test-core:
    runs-on: ubuntu-latest
    needs: check
    steps:
      - run: cargo test -p myapp-core -p myapp-types

  test-services:
    runs-on: ubuntu-latest
    needs: check
    steps:
      - run: cargo test -p myapp-db -p myapp-auth -p myapp-api

  test-integration:
    runs-on: ubuntu-latest
    needs: [test-core, test-services]
    steps:
      - run: cargo test -p integration-tests
```

## When to Split a Crate

Not everything needs its own crate. Here's my decision framework:

**Split when:**
- The code has a clearly different rate of change (e.g., types change rarely, API handlers change daily)
- Multiple binaries share the same code
- You want different feature flags or optional dependencies for different parts
- Compile times would benefit from more parallelism
- The code is independently useful or testable

**Don't split when:**
- You're just organizing code — use modules instead
- The "crate" would have only one consumer
- It would create a circular dependency
- You're splitting for splitting's sake

A module within a crate gives you namespace organization, visibility control, and separate files — all without the overhead of another crate. Not every folder needs its own `Cargo.toml`.

## The Integration Test Crate

I always create a separate crate for integration tests that span multiple crates:

```toml
# tests/integration/Cargo.toml
[package]
name = "integration-tests"
version = "0.0.0"
edition = "2021"
publish = false

# This crate exists only for its tests
[[test]]
name = "integration"
path = "tests/main.rs"

[dev-dependencies]
myapp-core = { workspace = true }
myapp-db = { workspace = true }
myapp-api = { workspace = true }
myapp-server = { workspace = true }
tokio = { workspace = true }
reqwest = { workspace = true }
testcontainers = { workspace = true }
```

```rust
// tests/integration/tests/main.rs
mod api_tests;
mod auth_flow;
mod database;

// Shared test utilities
mod helpers;
```

This keeps integration tests out of the individual crates (where they'd slow down `cargo test -p myapp-core`) and gives them access to everything they need.

## Pinning the Toolchain

Always pin your Rust toolchain in a workspace:

```toml
# rust-toolchain.toml
[toolchain]
channel = "1.78.0"
components = ["rustfmt", "clippy", "rust-analyzer"]
targets = ["x86_64-unknown-linux-musl"]
```

Without this, team members using different Rust versions will get different compilation results, different Clippy warnings, and occasionally different behavior. Pin it, and everyone builds with the same compiler.

## Workspace-Wide Commands

A few commands I run daily:

```bash
# Check everything compiles (faster than build — skips codegen)
cargo check --workspace --all-features

# Lint everything
cargo clippy --workspace --all-features -- -D warnings

# Format everything
cargo fmt --all

# Update dependencies (shows what would change)
cargo update --dry-run

# Check for security advisories
cargo audit

# Visualize the dependency tree
cargo tree -p myapp-server

# Find duplicate dependencies
cargo tree --duplicates
```

That last one — `cargo tree --duplicates` — is worth running periodically. If you see the same crate at multiple versions, it means somewhere in your dependency graph, two crates want different versions of the same thing. That's wasted compile time and binary size.

Workspaces are the backbone of any serious Rust project. Get the structure right early, enforce the dependency direction, centralize your versions, and everything downstream — CI, deployment, developer experience — gets easier.

Next: linking strategies. Static vs dynamic, LTO, and why your binary is 50MB.
