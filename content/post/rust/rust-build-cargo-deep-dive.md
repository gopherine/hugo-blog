---
title: "Lesson 1: Cargo Deep Dive — Workspaces, features, profiles"
author: Atharva Pandey
series: "Rust Compiler Plugins & Build System"
lesson: 1
keywords: [Rust, rust, cargo, build system, workspaces, features, profiles, cargo.toml]
tags: [Rust tutorial, rust, cargo, build-system]
date: '2025-08-01T09:15:00.000Z'
---

I'd been writing Rust for about a year before I realized I was only using maybe 20% of what Cargo actually offers. `cargo build`, `cargo run`, `cargo test` — that was my entire workflow. Then I joined a team managing a Rust monorepo with 30+ crates, custom build profiles, and feature flags controlling everything from database backends to telemetry. Suddenly my surface-level Cargo knowledge wasn't cutting it.

## Cargo Is Not Just a Build Tool

Most people coming from other languages think of Cargo as "npm for Rust" or "Maven for Rust." That undersells it massively. Cargo is a build system, package manager, test runner, benchmark runner, documentation generator, and project convention enforcer — all rolled into one binary. And unlike most build tools, it's actually pleasant to use.

The `Cargo.toml` file is where everything lives. But there's way more to it than `[dependencies]`.

## Workspaces: Organizing Real Projects

Once your project grows beyond a few thousand lines, you'll want to split it into multiple crates. A workspace is Cargo's answer to "how do I manage multiple related crates without losing my mind."

```toml
# Root Cargo.toml
[workspace]
members = [
    "core",
    "api",
    "cli",
    "shared-types",
    "integration-tests",
]
resolver = "2"

# Shared dependencies — all workspace members use the same versions
[workspace.dependencies]
serde = { version = "1.0", features = ["derive"] }
tokio = { version = "1", features = ["full"] }
anyhow = "1.0"
tracing = "0.1"
```

Each member crate then references workspace dependencies like this:

```toml
# core/Cargo.toml
[package]
name = "myapp-core"
version = "0.1.0"
edition = "2021"

[dependencies]
serde = { workspace = true }
tokio = { workspace = true }
anyhow = { workspace = true }

# Internal crate dependency
myapp-shared-types = { path = "../shared-types" }
```

Why bother with workspaces? Three reasons:

1. **Shared `target/` directory.** Without a workspace, each crate builds its own copy of every dependency. With a workspace, they share. On our 30-crate project, this cut CI build times by about 60%.

2. **Unified dependency versions.** `workspace.dependencies` means you declare `serde = "1.0"` once, not thirty times. When you bump it, you bump it in one place.

3. **Cross-crate testing.** `cargo test --workspace` runs tests across every member. `cargo clippy --workspace` lints everything. One command, complete coverage.

### Workspace Inheritance

You can also inherit package metadata across workspace members:

```toml
# Root Cargo.toml
[workspace.package]
version = "0.5.0"
edition = "2021"
authors = ["Your Name <you@example.com>"]
license = "MIT"
repository = "https://github.com/you/project"

# Member Cargo.toml
[package]
name = "myapp-core"
version.workspace = true
edition.workspace = true
authors.workspace = true
license.workspace = true
```

This is fantastic for libraries where you want all crates to share the same version number. Bump the root, and every member follows.

## Features: Compile-Time Configuration

Features are Cargo's conditional compilation mechanism. They let users of your crate choose what gets compiled — and more importantly, what *doesn't* get compiled.

```toml
[package]
name = "myapp-core"
version = "0.1.0"

[features]
default = ["json"]
json = ["dep:serde_json"]
yaml = ["dep:serde_yaml"]
postgres = ["dep:sqlx", "sqlx/postgres"]
sqlite = ["dep:sqlx", "sqlx/sqlite"]
telemetry = ["dep:opentelemetry", "dep:tracing-opentelemetry"]
full = ["json", "yaml", "postgres", "telemetry"]

[dependencies]
serde_json = { version = "1.0", optional = true }
serde_yaml = { version = "0.9", optional = true }
sqlx = { version = "0.7", optional = true, default-features = false }
opentelemetry = { version = "0.21", optional = true }
tracing-opentelemetry = { version = "0.22", optional = true }
```

Notice the `dep:` prefix — that's the modern way to declare that a feature enables an optional dependency. Before Rust 1.60, optional dependencies automatically created implicit features with the same name, which was confusing.

In your code, you use `cfg` attributes to conditionally compile:

```rust
#[cfg(feature = "json")]
pub mod json {
    use serde_json;

    pub fn parse_json(input: &str) -> Result<serde_json::Value, serde_json::Error> {
        serde_json::from_str(input)
    }
}

#[cfg(feature = "postgres")]
pub mod database {
    use sqlx::PgPool;

    pub async fn connect(url: &str) -> Result<PgPool, sqlx::Error> {
        PgPool::connect(url).await
    }
}
```

### Feature Design Principles

After designing feature flags for several crates, here's what I've learned:

**Features should be additive.** Enabling a feature should never *remove* functionality. If feature A works alone and feature B works alone, enabling both should also work. This seems obvious, but it's easy to violate accidentally with mutually exclusive features.

**Don't put too much behind features.** If 90% of your users need a feature, just make it a default. Features add testing complexity — ideally you test every combination, but that grows exponentially.

**Name features after what they enable, not what they depend on.** `postgres` is better than `sqlx`. `telemetry` is better than `opentelemetry`. Your users care about capabilities, not implementation details.

```bash
# Users interact with features like this:
cargo add myapp-core --features postgres,telemetry
cargo build --features "json,postgres"
cargo build --no-default-features --features yaml
cargo build --all-features  # useful for CI
```

## Profiles: Controlling the Build

Profiles control compiler behavior — optimization levels, debug info, overflow checks, and more. Cargo has four built-in profiles: `dev`, `release`, `test`, and `bench`.

```toml
# These are the defaults — you override what you need

[profile.dev]
opt-level = 0        # No optimization
debug = true         # Full debug info
overflow-checks = true
lto = false
codegen-units = 256  # Fast compilation, less optimization
incremental = true

[profile.release]
opt-level = 3        # Maximum optimization
debug = false        # No debug info
overflow-checks = false
lto = false
codegen-units = 16
incremental = false
```

But the defaults aren't always what you want. Here are the customizations I use on most projects:

### Fast Dev Builds with Release-Level Dependencies

Your application code changes constantly during development, but dependencies don't. You can optimize dependencies while keeping your code unoptimized:

```toml
# Optimize dependencies even in dev mode
[profile.dev.package."*"]
opt-level = 2

# Your code stays at opt-level 0 for fast compilation
[profile.dev]
opt-level = 0
debug = true
```

This is a game-changer for projects using heavy dependencies like `regex`, `serde`, or anything cryptographic. Your code compiles fast, but the hot paths in dependencies aren't painfully slow during development.

### Custom Profiles

Since Rust 1.57, you can define custom profiles that inherit from built-in ones:

```toml
# A "profiling" profile — optimized but with debug symbols
[profile.profiling]
inherits = "release"
debug = true          # Keep debug symbols for perf/flamegraph
strip = false

# A "dist" profile for final distribution binaries
[profile.dist]
inherits = "release"
lto = "fat"           # Full link-time optimization
codegen-units = 1     # Single codegen unit for max optimization
strip = true          # Strip symbols for smallest binary
panic = "abort"       # Don't include unwinding code
```

Use them with `cargo build --profile profiling` or `cargo build --profile dist`.

### LTO: The Final Optimization

Link-Time Optimization deserves special attention. There are three settings:

```toml
lto = false    # No LTO (default for dev)
lto = "thin"   # Fast LTO — good tradeoff
lto = "fat"    # Full LTO — slowest build, best optimization
```

Thin LTO gives you most of the performance benefits with much faster build times than fat LTO. For most release builds, `lto = "thin"` is the sweet spot. Reserve `lto = "fat"` for final distribution builds where you're willing to wait.

On one project, switching from `lto = false` to `lto = "thin"` shrunk our binary from 14MB to 9MB and improved throughput by about 8%. The build took 40% longer though, which is why I only use it for release builds.

## Cargo Configuration: .cargo/config.toml

Beyond `Cargo.toml`, there's `.cargo/config.toml` for configuring Cargo itself — not your project, but how Cargo behaves.

```toml
# .cargo/config.toml

# Use a faster linker
[target.x86_64-unknown-linux-gnu]
linker = "clang"
rustflags = ["-C", "link-arg=-fuse-ld=lld"]

[target.x86_64-apple-darwin]
rustflags = ["-C", "link-arg=-fuse-ld=/usr/local/bin/zld"]

# Default build target (useful for embedded)
[build]
target = "x86_64-unknown-linux-gnu"

# Alias common commands
[alias]
xtask = "run --package xtask --"
ci = "test --workspace --all-features"
lint = "clippy --workspace --all-features -- -D warnings"

# Registry configuration
[registries.my-company]
index = "https://git.mycompany.com/rust-registry/index"
```

That `[alias]` section is underrated. Instead of typing `cargo run --package xtask --` every time, you just type `cargo xtask`. We'll cover xtask patterns later in this course.

### Faster Linking

The single biggest thing you can do for dev build speed is switch to a faster linker. The default linker on most systems is painfully slow for large Rust projects.

On Linux, use `mold` or `lld`. On macOS, `zld` or the new Apple linker in recent Xcode versions. The difference can be dramatic — I've seen link times drop from 8 seconds to under 1 second on a medium-sized project.

```toml
# .cargo/config.toml for mold (Linux)
[target.x86_64-unknown-linux-gnu]
linker = "clang"
rustflags = ["-C", "link-arg=-fuse-ld=mold"]
```

## Environment Variables and Build Metadata

Cargo sets a bunch of environment variables during compilation that you can access from your code:

```rust
fn main() {
    // These are set by Cargo at compile time
    let version = env!("CARGO_PKG_VERSION");
    let name = env!("CARGO_PKG_NAME");
    let authors = env!("CARGO_PKG_AUTHORS");

    println!("{name} v{version} by {authors}");

    // You can also check features at runtime (via cfg)
    if cfg!(feature = "telemetry") {
        println!("Telemetry enabled");
    }
}
```

For more advanced metadata injection, you'll want `build.rs` — which is exactly what we'll cover in the next lesson.

## Putting It All Together

Here's a realistic `Cargo.toml` for a production workspace:

```toml
[workspace]
members = ["server", "cli", "core", "migrations", "xtask"]
resolver = "2"

[workspace.package]
version = "1.2.0"
edition = "2021"
authors = ["Your Team"]
license = "MIT"

[workspace.dependencies]
serde = { version = "1.0", features = ["derive"] }
tokio = { version = "1", features = ["full"] }
sqlx = { version = "0.7", features = ["runtime-tokio", "postgres"] }
tracing = "0.1"
tracing-subscriber = "0.3"
anyhow = "1.0"
thiserror = "1.0"
clap = { version = "4", features = ["derive"] }

[profile.dev]
opt-level = 0
debug = true

[profile.dev.package."*"]
opt-level = 2

[profile.release]
opt-level = 3
lto = "thin"
codegen-units = 1
strip = true

[profile.dist]
inherits = "release"
lto = "fat"
panic = "abort"
```

Cargo handles the rest. Dependency resolution, build ordering, parallel compilation, caching — all automatic. That's the beauty of it. You declare what you want, and Cargo figures out how to make it happen.

Next up, we'll look at `build.rs` — Cargo's escape hatch for when declarative configuration isn't enough and you need to run actual code before your crate compiles.
