---
title: "Lesson 3: Conditional Compilation — cfg, features, target"
author: Atharva Pandey
series: "Rust Compiler Plugins & Build System"
lesson: 3
keywords: [Rust, rust, cargo, build system, conditional compilation, cfg, features, target, platform]
tags: [Rust tutorial, rust, cargo, build-system]
date: '2025-08-05T11:45:00.000Z'
---

A few months back I was debugging a test failure that only happened on our Linux CI server, never on my Mac. Turns out someone had written platform-specific file path handling without proper `cfg` guards — the code compiled fine on both platforms but silently did the wrong thing on Linux. That's when I really internalized why conditional compilation needs to be treated as a first-class skill, not something you google when you need it.

## The cfg Attribute

Conditional compilation in Rust revolves around the `#[cfg(...)]` attribute. It tells the compiler: "only include this item if the condition is true." If the condition is false, the item doesn't exist — it's not compiled, not type-checked, nothing. It's as if you deleted it from the source.

```rust
// This function only exists on Linux
#[cfg(target_os = "linux")]
fn get_memory_usage() -> u64 {
    let status = std::fs::read_to_string("/proc/self/status").unwrap();
    // Parse VmRSS from /proc/self/status
    parse_vm_rss(&status)
}

// This function only exists on macOS
#[cfg(target_os = "macos")]
fn get_memory_usage() -> u64 {
    // Use mach APIs
    unsafe { macos_memory_usage() }
}

// Fallback for everything else
#[cfg(not(any(target_os = "linux", target_os = "macos")))]
fn get_memory_usage() -> u64 {
    0 // Can't determine memory usage on this platform
}
```

This is fundamentally different from runtime `if` statements. With `cfg`, the unused code literally doesn't exist in the compiled binary. There's no dead code, no unused imports, no wasted binary size.

## cfg vs cfg! vs cfg_attr

Rust has three conditional compilation mechanisms, and they serve different purposes:

### #[cfg(...)] — Compile-time item removal

Applies to items: functions, structs, impl blocks, modules, entire files.

```rust
#[cfg(feature = "metrics")]
mod metrics {
    pub fn record_latency(duration: std::time::Duration) {
        // Only compiled when "metrics" feature is enabled
    }
}

#[cfg(feature = "metrics")]
use metrics::record_latency;
```

### cfg!(...) — Compile-time boolean expression

Returns `true` or `false` at compile time. The code in both branches still gets compiled and type-checked, but the dead branch gets optimized away.

```rust
fn init_logging() {
    if cfg!(debug_assertions) {
        // Debug builds: verbose logging to stdout
        env_logger::Builder::new()
            .filter_level(log::LevelFilter::Debug)
            .init();
    } else {
        // Release builds: structured JSON to file
        let file = std::fs::File::create("app.log").unwrap();
        env_logger::Builder::new()
            .filter_level(log::LevelFilter::Info)
            .target(env_logger::Target::Pipe(Box::new(file)))
            .init();
    }
}
```

The key difference: with `#[cfg(...)]`, the false branch doesn't need to type-check. With `cfg!(...)`, both branches must be valid Rust. Use `#[cfg]` when the false branch wouldn't compile (e.g., missing types or dependencies). Use `cfg!()` when both branches are valid and you just want different runtime behavior.

### #[cfg_attr(...)] — Conditional attributes

Applies an attribute only when a condition is true:

```rust
// Only derive Serialize when the "serde" feature is enabled
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[derive(Debug, Clone)]
pub struct Config {
    pub host: String,
    pub port: u16,
    pub workers: usize,
}

// Only inline in release builds
#[cfg_attr(not(debug_assertions), inline(always))]
fn hot_path(data: &[u8]) -> u64 {
    // performance-critical code
    data.iter().map(|&b| b as u64).sum()
}
```

This is incredibly useful for optional serde support. Your crate doesn't need to depend on serde at all unless the user opts in with a feature flag.

## Built-in cfg Predicates

Rust provides a bunch of built-in predicates you can use with `cfg`:

```rust
// Target operating system
#[cfg(target_os = "linux")]
#[cfg(target_os = "macos")]
#[cfg(target_os = "windows")]

// Target architecture
#[cfg(target_arch = "x86_64")]
#[cfg(target_arch = "aarch64")]
#[cfg(target_arch = "wasm32")]

// Target environment (libc variant)
#[cfg(target_env = "gnu")]
#[cfg(target_env = "musl")]
#[cfg(target_env = "msvc")]

// Target pointer width
#[cfg(target_pointer_width = "64")]
#[cfg(target_pointer_width = "32")]

// Target endianness
#[cfg(target_endian = "little")]
#[cfg(target_endian = "big")]

// Target vendor
#[cfg(target_vendor = "apple")]
#[cfg(target_vendor = "unknown")]

// Convenience families
#[cfg(unix)]        // linux, macos, bsd, etc.
#[cfg(windows)]
#[cfg(target_family = "wasm")]

// Build profile
#[cfg(debug_assertions)]   // true in dev, false in release (usually)
#[cfg(test)]               // true when running tests

// Feature flags (from Cargo.toml)
#[cfg(feature = "metrics")]
#[cfg(feature = "postgres")]
```

### Combining Predicates

You can combine these with boolean logic:

```rust
// AND: both must be true
#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
fn use_avx2_optimization() { /* ... */ }

// OR: either can be true
#[cfg(any(target_os = "linux", target_os = "macos"))]
fn use_unix_sockets() { /* ... */ }

// NOT: must be false
#[cfg(not(target_os = "windows"))]
fn use_posix_signals() { /* ... */ }

// Complex combinations
#[cfg(all(
    unix,
    target_pointer_width = "64",
    not(target_os = "ios"),
    any(target_arch = "x86_64", target_arch = "aarch64")
))]
fn optimized_server_code() { /* ... */ }
```

## Platform-Specific Modules

For larger platform-specific code, I prefer using whole module conditionals rather than sprinkling `#[cfg]` on individual functions:

```rust
// src/lib.rs

// Platform-specific implementations
#[cfg(unix)]
mod platform_unix;
#[cfg(windows)]
mod platform_windows;

// Re-export the right platform module
#[cfg(unix)]
pub use platform_unix as platform;
#[cfg(windows)]
pub use platform_windows as platform;

// Common interface that both modules must implement
pub trait FileWatcher {
    fn watch(&mut self, path: &std::path::Path) -> Result<(), WatchError>;
    fn poll(&mut self) -> Option<FileEvent>;
    fn stop(&mut self);
}
```

```rust
// src/platform_unix.rs
use crate::FileWatcher;

pub struct NativeWatcher {
    inotify_fd: i32,
    // ...
}

impl FileWatcher for NativeWatcher {
    fn watch(&mut self, path: &std::path::Path) -> Result<(), crate::WatchError> {
        // Use inotify on Linux, kqueue on macOS
        #[cfg(target_os = "linux")]
        { self.add_inotify_watch(path) }

        #[cfg(target_os = "macos")]
        { self.add_kqueue_watch(path) }
    }

    fn poll(&mut self) -> Option<crate::FileEvent> {
        // Platform-specific polling
        todo!()
    }

    fn stop(&mut self) {
        // Cleanup
    }
}
```

This keeps the platform-specific code isolated and the public API clean.

## Feature Flags Done Right

We touched on features in Lesson 1, but let's dig into how they interact with conditional compilation in practice.

### The Additive Rule

Here's the rule that trips everyone up: **Cargo features are additive and unioned across the dependency graph.** If crate A depends on your crate with feature "json" and crate B depends on your crate with feature "yaml", *both* features get enabled when both A and B are in the same build.

This means mutually exclusive features don't work:

```toml
# DON'T DO THIS
[features]
backend-postgres = ["dep:sqlx"]
backend-sqlite = ["dep:sqlx"]
# What happens if someone enables both? Chaos.
```

Instead, design for composition:

```rust
// This works because both can be enabled simultaneously
#[cfg(feature = "backend-postgres")]
pub mod postgres {
    pub fn connect_pg(url: &str) -> PgConnection { /* ... */ }
}

#[cfg(feature = "backend-sqlite")]
pub mod sqlite {
    pub fn connect_sqlite(path: &str) -> SqliteConnection { /* ... */ }
}

// Provide a unified interface if needed
pub enum DatabaseConnection {
    #[cfg(feature = "backend-postgres")]
    Postgres(postgres::PgConnection),
    #[cfg(feature = "backend-sqlite")]
    Sqlite(sqlite::SqliteConnection),
}
```

### Feature-Gated Public API

When your crate exposes types from optional dependencies, gate the re-exports:

```rust
// src/lib.rs
pub mod core;

#[cfg(feature = "json")]
pub mod json;

#[cfg(feature = "grpc")]
pub mod grpc;

// Re-export commonly used types
pub use core::*;

#[cfg(feature = "json")]
pub use json::{JsonSerializer, JsonDeserializer};

#[cfg(feature = "grpc")]
pub use grpc::{GrpcClient, GrpcServer};
```

### Testing Feature Combinations

This is the hard part. With N features, you have 2^N possible combinations. You can't test them all, but you should test the common ones:

```yaml
# In your CI pipeline (e.g., GitHub Actions)
jobs:
  test:
    strategy:
      matrix:
        features:
          - ""                              # no features
          - "--all-features"                # everything
          - "--no-default-features"         # minimal
          - "--features json"               # just json
          - "--features 'json,postgres'"    # common combo
    steps:
      - run: cargo test ${{ matrix.features }}
      - run: cargo clippy ${{ matrix.features }} -- -D warnings
```

## Custom cfg Flags from build.rs

You're not limited to Cargo's built-in predicates. Build scripts can emit custom `cfg` flags:

```rust
// build.rs
fn main() {
    // Detect CPU features at build time
    if std::env::var("CARGO_CFG_TARGET_FEATURE")
        .unwrap_or_default()
        .contains("avx2")
    {
        println!("cargo:rustc-cfg=has_avx2");
    }

    // Detect library versions
    if let Ok(output) = std::process::Command::new("pkg-config")
        .args(["--modversion", "openssl"])
        .output()
    {
        let version = String::from_utf8(output.stdout).unwrap_or_default();
        if version.starts_with("3.") {
            println!("cargo:rustc-cfg=openssl3");
        }
    }

    // Detect OS-specific capabilities
    #[cfg(target_os = "linux")]
    {
        if std::path::Path::new("/sys/kernel/io_uring").exists() {
            println!("cargo:rustc-cfg=has_io_uring");
        }
    }

    println!("cargo:rerun-if-changed=build.rs");
}
```

Then use them in your code:

```rust
#[cfg(has_avx2)]
fn hash_block(data: &[u8; 64]) -> [u8; 32] {
    // AVX2-accelerated implementation
    avx2_sha256(data)
}

#[cfg(not(has_avx2))]
fn hash_block(data: &[u8; 64]) -> [u8; 32] {
    // Scalar fallback
    scalar_sha256(data)
}

#[cfg(has_io_uring)]
async fn read_file(path: &str) -> Vec<u8> {
    io_uring_read(path).await
}

#[cfg(not(has_io_uring))]
async fn read_file(path: &str) -> Vec<u8> {
    tokio::fs::read(path).await.unwrap()
}
```

## The cfg-if Crate

When you have lots of platform-specific branches, the nested `#[cfg]` attributes get ugly fast. The `cfg-if` crate provides a cleaner syntax:

```rust
use cfg_if::cfg_if;

cfg_if! {
    if #[cfg(target_os = "linux")] {
        mod linux;
        use linux as platform;
    } else if #[cfg(target_os = "macos")] {
        mod macos;
        use macos as platform;
    } else if #[cfg(target_os = "windows")] {
        mod windows;
        use windows as platform;
    } else {
        compile_error!("Unsupported platform");
    }
}
```

Much more readable than nested `#[cfg(not(any(...)))]` chains.

## Compile-Time Assertions

You can use `cfg` with `compile_error!` to enforce build requirements:

```rust
#[cfg(not(target_pointer_width = "64"))]
compile_error!("This crate requires a 64-bit platform");

#[cfg(all(feature = "openssl", feature = "rustls"))]
compile_error!(
    "Features 'openssl' and 'rustls' are mutually exclusive. \
     Please enable only one TLS backend."
);

// Ensure at least one backend is selected
#[cfg(not(any(feature = "postgres", feature = "sqlite", feature = "mysql")))]
compile_error!(
    "At least one database backend must be enabled. \
     Use --features postgres, --features sqlite, or --features mysql"
);
```

This is a thousand times better than a runtime panic. The build fails immediately with a clear message telling the user exactly what to do.

## Practical Pattern: Optional Tracing

Here's a pattern I use in every library crate — optional instrumentation that adds zero overhead when disabled:

```rust
// Cargo.toml
// [features]
// tracing = ["dep:tracing"]

#[cfg(feature = "tracing")]
use tracing::instrument;

// When tracing is enabled, instrument the function
// When disabled, this is just a regular function
#[cfg_attr(feature = "tracing", instrument(skip(data), fields(len = data.len())))]
pub fn process_batch(data: &[Record]) -> Result<Summary, ProcessError> {
    let mut summary = Summary::default();

    for record in data {
        #[cfg(feature = "tracing")]
        tracing::debug!(id = record.id, "processing record");

        summary.add(process_record(record)?);
    }

    Ok(summary)
}

// Macro to conditionally emit trace events without cluttering code
#[cfg(feature = "tracing")]
macro_rules! trace_event {
    ($($arg:tt)*) => { tracing::trace!($($arg)*) };
}

#[cfg(not(feature = "tracing"))]
macro_rules! trace_event {
    ($($arg:tt)*) => {};
}
```

Users who don't need tracing pay nothing — no dependency, no compile time, no binary size, no runtime cost. Users who want it just add `features = ["tracing"]` and get full instrumentation.

Conditional compilation is one of Rust's superpowers. It lets you write portable code without runtime overhead, ship libraries that work across platforms, and give users fine-grained control over what gets included. Master `cfg`, and you'll write Rust code that's simultaneously flexible and lean.

Next up: custom lints with clippy and dylint. Your team's rules, enforced by the compiler.
