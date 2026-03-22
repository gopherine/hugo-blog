---
title: "Lesson 7: Linking Strategies — Static, dynamic, LTO"
author: Atharva Pandey
series: "Rust Compiler Plugins & Build System"
lesson: 7
keywords: [Rust, rust, cargo, build system, linking, static linking, dynamic linking, LTO, binary size, musl]
tags: [Rust tutorial, rust, cargo, build-system]
date: '2025-08-15T10:40:00.000Z'
---

I deployed a Rust service to a minimal Docker container once — Alpine Linux, nothing installed except the binary. It crashed immediately with "not a dynamic executable." Turns out my binary was dynamically linked against glibc, but Alpine uses musl. I'd never thought about linking before that day. Now it's one of the first things I configure on any new project.

## What Linking Actually Is

When you write `cargo build`, the compiler doesn't produce a binary directly. It produces object files — chunks of machine code for each compilation unit. The *linker* takes all those object files, plus any libraries you depend on, and stitches them together into a single executable.

There are two fundamental strategies:

**Static linking** — copy the library code directly into your binary. The result is a single, self-contained file. It's larger but has zero runtime dependencies.

**Dynamic linking** — include references to shared libraries (`.so` on Linux, `.dylib` on macOS, `.dll` on Windows). The operating system loads these at runtime. Smaller binary, but needs the right libraries installed.

Rust defaults to statically linking everything it can — all your Rust code and Rust dependencies. But it dynamically links the system C library (libc) by default on most targets.

## Rust's Default Linking Behavior

Let's see what actually happens:

```bash
$ cargo build --release
$ ldd target/release/myapp
    linux-vdso.so.1
    libgcc_s.so.1 => /lib/x86_64-linux-gnu/libgcc_s.so.1
    libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6
    /lib64/ld-linux-x86-64.so.2
```

Three dynamic dependencies: `libc`, `libgcc_s`, and the dynamic linker itself. Your Rust code and all crate dependencies are statically linked — only the C runtime is dynamic.

Why? Because the C library is everywhere. Every Linux system has it. Dynamically linking it means your binary benefits from security patches to libc without recompilation. And some libc functions (`getaddrinfo`, `dlopen`) don't work well when statically linked.

## Fully Static Binaries with musl

If you want a truly standalone binary — zero runtime dependencies — you need to statically link libc too. The easiest way is to target musl instead of glibc:

```bash
# Install the musl target
rustup target add x86_64-unknown-linux-musl

# Build with it
cargo build --release --target x86_64-unknown-linux-musl

# Verify — no dynamic dependencies
$ ldd target/x86_64-unknown-linux-musl/release/myapp
    not a dynamic executable
```

That "not a dynamic executable" message means success — the binary is fully self-contained. You can copy it to any Linux machine and it'll run. Any kernel version from the last decade, any distro, any container image — even `FROM scratch`.

### The musl Docker Pattern

This is my go-to for containerized Rust services:

```dockerfile
# Build stage
FROM rust:1.78 AS builder

RUN apt-get update && apt-get install -y musl-tools
RUN rustup target add x86_64-unknown-linux-musl

WORKDIR /app
COPY . .

RUN cargo build --release --target x86_64-unknown-linux-musl

# Runtime stage — nothing but the binary
FROM scratch

COPY --from=builder /app/target/x86_64-unknown-linux-musl/release/myapp /myapp
# Copy TLS certificates if your app makes HTTPS requests
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/

ENTRYPOINT ["/myapp"]
```

The resulting image is just your binary plus TLS certs. No shell, no package manager, no libc, nothing. It's typically 5-20MB depending on your binary size. Compare that to a typical `rust:latest` based image at 1.5GB.

### musl Performance Considerations

musl isn't always faster than glibc. In fact, for some workloads — particularly anything doing heavy memory allocation — musl's `malloc` implementation is noticeably slower than glibc's.

If performance matters, you have options:

```toml
# Cargo.toml — use jemalloc with musl
[target.'cfg(target_env = "musl")'.dependencies]
tikv-jemallocator = "0.5"
```

```rust
#[cfg(target_env = "musl")]
#[global_allocator]
static GLOBAL: tikv_jemallocator::Jemalloc = tikv_jemallocator::Jemalloc;
```

This gives you musl's static linking benefits with jemalloc's allocation performance. On one service I worked on, this recovered the 15% throughput regression we saw when switching from glibc to musl.

## Link-Time Optimization (LTO)

LTO is an optimization pass that runs during linking, after individual compilation units have been compiled. It lets the optimizer see across crate boundaries — inlining functions from dependencies, eliminating dead code that spans crates, and optimizing the whole program as a unit.

### LTO Modes

```toml
[profile.release]
lto = false    # No LTO — fastest build
lto = "thin"   # Parallel LTO — good tradeoff
lto = "fat"    # Full LTO — best optimization, slowest build
```

**No LTO** — each crate is optimized independently. Functions from dependencies can't be inlined into your code. Fast to build, leaves performance on the table.

**Thin LTO** — a modern approach that does cross-module optimization in parallel. It captures maybe 80-90% of fat LTO's benefits at a fraction of the build time. This is my default for release builds.

**Fat LTO** — the compiler merges everything into a single module and optimizes it as one unit. This enables the most aggressive optimizations but can be dramatically slower to build. I only use this for final distribution binaries.

### Real Numbers

I measured these on a medium-sized web service (about 50 crate dependencies):

| Profile | Binary Size | Build Time | Throughput (req/s) |
|---------|------------|------------|-------------------|
| No LTO | 18.2 MB | 45s | 42,000 |
| Thin LTO | 12.8 MB | 68s | 45,500 |
| Fat LTO | 11.5 MB | 142s | 46,200 |

Thin LTO is the clear winner for most situations. Fat LTO buys you another ~1.5% throughput for 2x the build time. Not worth it unless you're squeezing every last drop.

### Codegen Units

Related to LTO is the `codegen-units` setting:

```toml
[profile.release]
codegen-units = 1    # Single unit — max optimization
codegen-units = 16   # Default release — balance
codegen-units = 256  # Default dev — max parallelism
```

The compiler splits each crate into multiple "codegen units" that can be compiled in parallel. More units = faster compilation but less optimization (the optimizer can't see across units). Fewer units = slower compilation but better optimization.

For maximum performance: `lto = "thin"` with `codegen-units = 1`. For maximum build speed: `lto = false` with the default codegen units.

## Stripping Symbols

Debug symbols can be a huge chunk of your binary. Stripping them is free performance... well, free in terms of runtime. You lose the ability to get meaningful backtraces.

```toml
[profile.release]
strip = true          # Strip everything
strip = "symbols"     # Same as true — strip symbol table
strip = "debuginfo"   # Strip debug info but keep symbol names
strip = "none"        # Keep everything (default)
```

You can also strip after the fact:

```bash
# Strip with the system strip tool (more control)
strip target/release/myapp

# See the difference
$ ls -la target/release/myapp.before
-rwxr-xr-x 1 user user 48M myapp.before
$ ls -la target/release/myapp.after
-rwxr-xr-x 1 user user 8M  myapp.after
```

I keep symbols in my profiling profile (for flamegraphs) and strip them in the distribution profile:

```toml
[profile.profiling]
inherits = "release"
debug = true
strip = false

[profile.dist]
inherits = "release"
strip = true
lto = "fat"
codegen-units = 1
```

## Dynamic Linking in Rust

Sometimes you *want* dynamic linking. Plugin systems are the classic case — load a `.so` at runtime without recompiling the host application.

```rust
use libloading::{Library, Symbol};

fn load_plugin(path: &str) -> Result<(), Box<dyn std::error::Error>> {
    unsafe {
        let lib = Library::new(path)?;

        // Load a function from the dynamic library
        let init: Symbol<fn() -> i32> = lib.get(b"plugin_init")?;
        let version = init();
        println!("Plugin version: {version}");

        // The library stays loaded until `lib` is dropped
        Ok(())
    }
}
```

The plugin crate:

```toml
# plugin/Cargo.toml
[lib]
crate-type = ["cdylib"]  # Produce a .so/.dylib/.dll
```

```rust
// plugin/src/lib.rs
#[no_mangle]
pub extern "C" fn plugin_init() -> i32 {
    42
}
```

**Be careful with this.** Dynamic linking across Rust crates is fragile because Rust doesn't have a stable ABI. If the host and plugin are compiled with different Rust versions, or even different compiler flags, things can break in subtle and horrifying ways. The `extern "C"` boundary gives you ABI stability but restricts you to C-compatible types.

For production plugin systems, I recommend either:
- Sticking to `extern "C"` interfaces exclusively
- Using a serialization boundary (the plugin sends/receives JSON or protobuf)
- Using WASM (compile plugins to WebAssembly and run them sandboxed)

## Linking C/C++ Libraries

When wrapping native libraries, you control how they're linked:

```rust
// build.rs
fn main() {
    // Dynamic linking (default)
    println!("cargo:rustc-link-lib=ssl");

    // Static linking
    println!("cargo:rustc-link-lib=static=mylib");

    // Let the linker decide (tries static first, falls back to dynamic)
    println!("cargo:rustc-link-lib=dylib=somelib");

    // Search paths
    println!("cargo:rustc-link-search=native=/usr/local/lib");
    println!("cargo:rustc-link-search=native=vendor/lib");
}
```

For vendoring C dependencies (building them from source), use the `cc` crate:

```rust
// build.rs
fn main() {
    cc::Build::new()
        .file("vendor/zstd/lib/zstd.c")
        .file("vendor/zstd/lib/common/pool.c")
        .include("vendor/zstd/lib")
        .opt_level(3)
        .compile("zstd");
    // This statically links the compiled C code into your Rust binary
}
```

## Measuring Binary Size

When your binary is too big, you need to figure out what's taking up space:

```bash
# Install cargo-bloat
cargo install cargo-bloat

# Show the biggest functions
cargo bloat --release -n 20

# Show the biggest crates
cargo bloat --release --crates

# Show size breakdown by crate
cargo bloat --release --crates -n 30
```

Sample output:

```
 File  .text    Size Crate
 8.5%  15.2% 1.3MiB regex
 6.2%  11.1% 960KiB serde_json
 5.1%   9.1% 780KiB tokio
 3.8%   6.8% 585KiB hyper
 2.9%   5.2% 448KiB sqlx
```

If `regex` is taking 1.3MB and you're only using it in one place, maybe a simpler pattern matching approach would save you a megabyte. These decisions matter for embedded systems, CLI tools, and WASM targets where binary size is constrained.

Another useful tool is `cargo-size`:

```bash
# Detailed section sizes
cargo size --release -- -A
```

## The Production Profile

Putting it all together, here's my production build configuration:

```toml
# Release profile — used for staging and performance testing
[profile.release]
opt-level = 3
lto = "thin"
codegen-units = 1
strip = "debuginfo"     # Keep symbol names for crash reports
panic = "unwind"        # Keep unwinding for graceful error handling
overflow-checks = false

# Distribution profile — final binaries shipped to users
[profile.dist]
inherits = "release"
lto = "fat"
strip = true
panic = "abort"         # Smaller binary, no unwinding code

# Profiling profile — optimized but debuggable
[profile.profiling]
inherits = "release"
debug = 2              # Full debug info
strip = false          # Keep everything for perf/flamegraph
```

```bash
# Development
cargo build

# Testing / staging
cargo build --release

# Final distribution
cargo build --profile dist --target x86_64-unknown-linux-musl

# Performance profiling
cargo build --profile profiling
```

Each profile serves a different purpose. Don't try to make one profile do everything — that's what custom profiles are for.

Linking is one of those topics that seems boring until it bites you. Understanding the difference between static and dynamic linking, knowing when to use LTO, and having the right profiles configured saves hours of debugging and megabytes of binary size. Get it right once and forget about it.

Last lesson coming up: reproducible builds. Same source code, same binary, every time.
