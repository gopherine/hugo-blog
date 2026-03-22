---
title: "Lesson 10: Compile Time Optimization — Strategies that actually work"
author: Atharva Pandey
series: "Rust Performance Engineering"
lesson: 10
keywords: [Rust, rust, performance, compile time, build time, incremental compilation, cargo, linker, mold]
tags: [Rust tutorial, rust, performance]
date: '2025-04-03T08:55:00.000Z'
---

My main Rust project at work took 4 minutes and 38 seconds for a clean build. That was two years ago. Today it takes 52 seconds. Same codebase — more code, actually. Same hardware. The difference is about a dozen targeted changes, none of which involved rewriting application code.

Rust's compile times are its most legitimate criticism. But "Rust is slow to compile" is the starting point, not the conclusion. Most projects can cut their build times by 50-80% with the right techniques. Let me show you what actually moves the needle.

## Measuring Build Times

Before optimizing, measure. Cargo has built-in timing:

```bash
# Time a clean build with timing per crate
cargo build --timings --release

# This generates an HTML report at
# target/cargo-timings/cargo-timing.html
```

The HTML report shows a waterfall chart — which crates compiled in parallel, which were on the critical path, and how long each took. This immediately shows you which crate to focus on.

For more granularity:

```bash
# Show time spent in each phase
cargo build --release 2>&1 | grep -E "Compiling|Finished"

# Or use cargo-build-timings
cargo install cargo-chef  # useful for Docker builds
```

## The Big Wins

### 1. Use a Faster Linker

This is the single biggest improvement for incremental builds. The default linker is slow. Replace it:

**On Linux — use `mold`:**

```bash
# Install mold
sudo apt install mold

# Or from source for the latest version
git clone https://github.com/rui314/mold.git
cd mold && cmake -B build && cmake --build build -j$(nproc)
sudo cmake --install build
```

```toml
# .cargo/config.toml
[target.x86_64-unknown-linux-gnu]
linker = "clang"
rustflags = ["-C", "link-arg=-fuse-ld=mold"]
```

**On macOS — use `lld` or the default is already decent:**

```toml
# .cargo/config.toml (macOS)
[target.aarch64-apple-darwin]
rustflags = ["-C", "link-arg=-fuse-ld=lld"]

# If lld isn't available, the macOS default linker is
# reasonably fast already. mold doesn't support macOS yet
# but "sold" (a macOS port) is available
```

Impact: linking goes from 5-30 seconds to 0.5-2 seconds. For incremental builds where only linking changes, this is transformative.

### 2. Split Your Crate into a Workspace

The biggest compile-time problem in Rust projects is the "one giant crate" pattern. Everything in one crate means any change recompiles everything.

```
# Before: monolith
my-project/
  src/
    main.rs
    parser.rs    # change this...
    server.rs    # ...recompiles this too
    database.rs  # ...and this
    models.rs    # ...and everything else
```

Split into a workspace:

```
# After: workspace
my-project/
  Cargo.toml          # workspace root
  crates/
    my-models/         # stable types, rarely changes
    my-parser/         # parser logic
    my-database/       # database layer
    my-server/         # HTTP server
    my-app/            # binary, depends on all above
```

```toml
# Root Cargo.toml
[workspace]
members = ["crates/*"]

# Shared dependency versions
[workspace.dependencies]
serde = { version = "1", features = ["derive"] }
tokio = { version = "1", features = ["full"] }
```

Now changing `my-parser` only recompiles `my-parser` and `my-app` (which depends on it). `my-database`, `my-server`, and `my-models` are untouched. On a project with 50K lines, this cut my incremental build from 45 seconds to 8 seconds.

### 3. Reduce Dependency Weight

Every dependency you add increases compile time. Some are worse than others:

```bash
# See your full dependency tree
cargo tree

# Count total dependencies
cargo tree | wc -l

# Find the heaviest dependencies
cargo build --timings --release
# Check the HTML report for slow crates
```

Common heavy dependencies and lighter alternatives:

| Heavy | Lighter Alternative | Savings |
|-------|-------------------|---------|
| `reqwest` (with all features) | `ureq` (sync) or `reqwest` with minimal features | 20-40s |
| `tokio` (full) | `tokio` with only needed features | 10-20s |
| `serde_json` + `serde` derive | `simd-json` or `nanoserde` | 5-15s |
| `clap` (derive) | `clap` (builder) or `pico-args` | 5-10s |
| `chrono` | `time` or `jiff` | 5-10s |

**Disable unused features:**

```toml
# BAD: compiles everything
tokio = { version = "1", features = ["full"] }

# GOOD: only what you need
tokio = { version = "1", features = ["rt-multi-thread", "net", "macros"] }
```

### 4. Use `cargo-nextest` for Faster Tests

`cargo-nextest` runs tests in parallel more effectively than `cargo test`:

```bash
cargo install cargo-nextest

# Run tests
cargo nextest run

# Typical improvement: 2-5x faster test execution
```

### 5. Optimize Development Profile

Your dev builds don't need the same settings as release:

```toml
# Cargo.toml

# Fast debug builds
[profile.dev]
opt-level = 0        # no optimization (fastest compile)
debug = true
incremental = true   # enabled by default, but be explicit

# Optimize dependencies even in dev mode
[profile.dev.package."*"]
opt-level = 2        # optimize dependencies, not your code
```

That last setting is magical. Your code compiles at `-O0` (fast), but dependencies like `serde`, `regex`, `tokio` compile at `-O2` (slow build but fast runtime). Since dependencies rarely change, they're cached and you don't pay the cost after the first build.

### 6. Reduce Proc Macro Usage

Proc macros (`#[derive(...)]`, `#[tokio::main]`, etc.) run at compile time and can be surprisingly expensive:

```rust
// Every time this file changes, serde re-derives
// Serialize and Deserialize for all structs in it
#[derive(Serialize, Deserialize)]
struct MyBigStruct {
    // 30 fields...
}
```

Strategies:
- Put types with derives in a separate crate that changes rarely
- Consider manual implementations for simple cases
- Avoid derives you don't actually use

```rust
// Do you really need Debug on this type?
// If it's never printed, drop the derive
#[derive(Clone)]  // only what you actually use
struct InternalState {
    // ...
}
```

### 7. Cranelift Backend for Dev Builds

The `cranelift` codegen backend compiles much faster than LLVM at the cost of slower runtime. Perfect for development:

```bash
# Install nightly (required for cranelift)
rustup install nightly

# Use cranelift for dev builds
CARGO_PROFILE_DEV_CODEGEN_BACKEND=cranelift cargo +nightly build
```

Or in `Cargo.toml`:

```toml
# Cargo.toml (nightly only)
[profile.dev]
codegen-backend = "cranelift"
```

Cranelift can cut compile times by 30-50% at the cost of 2-5x slower runtime. Since you're not benchmarking dev builds anyway, this is a great trade-off.

## Docker Build Optimization

Docker builds deserve special attention because they're often the slowest part of CI:

```dockerfile
# BAD: rebuilds everything on any code change
FROM rust:1.77
COPY . .
RUN cargo build --release

# GOOD: cache dependencies separately
FROM rust:1.77 AS builder

# First, build only dependencies
COPY Cargo.toml Cargo.lock ./
COPY crates/*/Cargo.toml ./dummy_crates/
RUN mkdir src && echo "fn main() {}" > src/main.rs
RUN cargo build --release
RUN rm -rf src

# Then build your code (dependencies are cached)
COPY . .
RUN cargo build --release
```

Or better yet, use `cargo-chef`:

```dockerfile
FROM rust:1.77 AS chef
RUN cargo install cargo-chef

FROM chef AS planner
COPY . .
RUN cargo chef prepare --recipe-path recipe.json

FROM chef AS builder
COPY --from=planner /app/recipe.json recipe.json
RUN cargo chef cook --release --recipe-path recipe.json
COPY . .
RUN cargo build --release
```

`cargo-chef` generates a "recipe" that captures your dependency graph, then builds dependencies without your source code. When your code changes, only the final `cargo build` needs to run.

## CI-Specific Tips

```yaml
# GitHub Actions example
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable

      # Cache cargo registry + build artifacts
      - uses: Swatinem/rust-cache@v2
        with:
          cache-all-crates: "true"

      # Use mold linker
      - name: Install mold
        run: sudo apt-get install -y mold

      - name: Build
        run: cargo build --release
        env:
          CARGO_REGISTRIES_CRATES_IO_PROTOCOL: sparse  # faster index
```

The `sparse` registry protocol (default since Rust 1.70) significantly speeds up the initial `cargo` invocation by not cloning the entire crates.io index.

## Measuring Improvement

Track your build times over time:

```bash
# Quick benchmark script
#!/bin/bash
cargo clean
time cargo build --release 2>&1 | tail -1
echo "---"
# Touch a single file and rebuild
touch src/main.rs
time cargo build --release 2>&1 | tail -1
```

Keep a log of clean build time and incremental build time. As your project grows, these numbers will creep up — catch it early.

## My Standard Configuration

Here's what I put in every new Rust project:

```toml
# .cargo/config.toml
[target.x86_64-unknown-linux-gnu]
linker = "clang"
rustflags = ["-C", "link-arg=-fuse-ld=mold"]

[target.aarch64-apple-darwin]
rustflags = ["-C", "link-arg=-fuse-ld=lld"]
```

```toml
# Cargo.toml
[profile.dev]
opt-level = 0
incremental = true

[profile.dev.package."*"]
opt-level = 2

[profile.release]
lto = "thin"
codegen-units = 1
opt-level = 3
debug = true
```

This gives me fast dev builds and optimized release builds with minimal configuration.

## The Takeaway

Rust compile times are a real problem, but they're a *solvable* problem. The highest-impact changes, in order:

1. **Use mold/lld as your linker** — saves 5-30 seconds per build
2. **Split into a workspace** — saves 30-80% on incremental builds
3. **Disable unused features** — often saves 10-30 seconds
4. **Optimize deps in dev profile** — faster dev builds, cached deps
5. **Use cargo-chef for Docker** — caches dependencies across builds
6. **Consider cranelift for dev** — 30-50% faster dev compilation

You don't need all of these. Start with the linker and workspace, which take 30 minutes to set up and deliver the biggest improvements. Then measure and decide if you need more.
