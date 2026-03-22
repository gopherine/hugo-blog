---
title: "Lesson 11: Binary Size Reduction — Smaller deployments"
author: Atharva Pandey
series: "Rust Performance Engineering"
lesson: 11
keywords: [Rust, rust, performance, binary size, strip, LTO, embedded, containers, Wasm]
tags: [Rust tutorial, rust, performance]
date: '2025-04-07T13:17:00.000Z'
---

I shipped a "hello world" Rust binary to a team once and they came back confused: "Why is this 4 megabytes?" Fair question. A C hello-world is 16KB. A Go hello-world is about 2MB. A default Rust hello-world with standard linking is 3-4MB.

That 4MB isn't wasted — it's the Rust standard library, panic handling, formatting machinery, and debug symbols. But when you're building container images, deploying to embedded devices, or targeting WebAssembly, every megabyte counts. Here's how to cut Rust binaries down to size.

## Why Rust Binaries Are Large

Several factors contribute:

1. **Static linking.** Rust statically links the standard library by default. The entire `std` is baked into your binary.
2. **Monomorphization.** Every generic function generates a specialized copy for each type. `Vec<u32>`, `Vec<String>`, `Vec<MyStruct>` — each produces separate machine code.
3. **Debug symbols.** Even in release builds, Rust includes some debug information.
4. **Panic infrastructure.** The panic handler, backtrace machinery, and formatting code add up.
5. **Dead code isn't always removed.** The linker does dead code elimination, but it's not perfect, especially without LTO.

## Measuring Binary Size

```bash
# Basic size
ls -lh target/release/my_binary

# Detailed breakdown
cargo install cargo-bloat

# Show which functions take the most space
cargo bloat --release -n 20

# Show which crates contribute the most
cargo bloat --release --crates

# Show all sections
size -A target/release/my_binary
```

`cargo-bloat` is essential. It tells you *where* the bytes are going:

```
 File  .text     Size    Crate Name
 3.2%  11.1%  95.3KiB  serde_json serde_json::de::Deserializer<R>::parse_value
 2.1%   7.3%  62.8KiB       regex regex::regex::string::Regex::new
 1.8%   6.2%  53.2KiB         std std::backtrace_rs::symbolize::...
 ...
```

Now you know: serde_json's deserializer is 95KB, regex is 63KB, backtrace formatting is 53KB. These are your targets.

## The Easy Wins

### 1. Strip Debug Symbols

```toml
# Cargo.toml
[profile.release]
strip = true          # strips debug symbols and DWARF info
# strip = "debuginfo" # strips only debug info, keeps symbols
# strip = "symbols"   # strips everything (same as true)
```

Or manually:

```bash
strip target/release/my_binary
```

Impact: typically reduces binary by 30-60%. A 4MB binary might drop to 1.5MB.

### 2. Enable LTO

```toml
[profile.release]
lto = true
codegen-units = 1
```

LTO lets LLVM see across crate boundaries and eliminate dead code more aggressively. Combined with `codegen-units = 1`, this typically saves 10-25% of binary size.

### 3. Optimize for Size

```toml
[profile.release]
opt-level = "s"   # optimize for size
# opt-level = "z" # optimize aggressively for size (may be slower)
```

`opt-level = "s"` trades some speed for smaller code — it discourages inlining and loop unrolling. `opt-level = "z"` is more aggressive. The runtime performance impact varies but is usually 5-15%.

```
# Same binary with different opt-levels:
# opt-level = 3:   2.1 MB, runs in 450µs
# opt-level = "s": 1.6 MB, runs in 490µs
# opt-level = "z": 1.4 MB, runs in 530µs
```

### 4. Abort on Panic

```toml
[profile.release]
panic = "abort"
```

By default, Rust uses "unwind" panics — the stack is unwound, destructors are called, and a backtrace is generated. This requires a lot of supporting code. `panic = "abort"` just calls `abort()`, which is tiny.

Impact: 10-20% size reduction, plus eliminates the unwinding tables.

Trade-off: you lose `catch_unwind`, panic hooks won't run, and resources might not be cleaned up on panic. For servers and CLI tools, this is usually fine.

## Aggressive Optimization

### 5. Remove the Formatting Machinery

`std::fmt` is surprisingly large. If you use `println!`, `format!`, or `write!`, you pull in formatting code for every type you format — integers, floats, strings, debug output.

```rust
// Each of these pulls in formatting code:
println!("value: {}", x);
format!("error: {}", msg);
eprintln!("{:?}", debug_struct);
```

For extreme size constraints (embedded, Wasm), consider:
- Using `write!` to a fixed buffer instead of `format!`
- Implementing `Display` manually without pulling in `Debug`
- Using `defmt` for embedded (deferred formatting)

### 6. Use `#[no_std]` Where Possible

If you can live without the standard library:

```rust
#![no_std]
#![no_main]

#[panic_handler]
fn panic(_info: &core::panic::PanicInfo) -> ! {
    loop {}
}

#[no_mangle]
pub extern "C" fn main() -> i32 {
    // Your code here — no std, no formatting, no allocator
    42
}
```

A `#[no_std]` binary can be under 10KB. But you give up `String`, `Vec`, `HashMap`, file I/O, networking — basically everything that makes Rust comfortable. Only worth it for embedded or extreme optimization.

### 7. Minimize Dependencies

Every dependency adds code. Audit them:

```bash
# List all dependencies
cargo tree

# Find duplicated dependencies (different versions)
cargo tree --duplicates

# Check what specific deps contribute to binary size
cargo bloat --release --crates
```

Common bloat sources and alternatives:

```toml
# Instead of pulling in all of serde for one derive:
# Consider manually implementing Serialize/Deserialize

# Instead of regex for simple patterns:
# Use str::contains, str::find, or simple matching

# Instead of chrono for timestamps:
# Use std::time or the lighter `time` crate
```

### 8. Use Dynamic Linking (When Appropriate)

```bash
# Dynamically link against libc (smaller binary, needs libc at runtime)
RUSTFLAGS="-C prefer-dynamic" cargo build --release
```

This reduces binary size significantly but requires the Rust runtime libraries to be present on the target system. Useful for systems where you control the environment.

## Profile for Binary Size

Here's a comprehensive size-optimized profile:

```toml
# Cargo.toml
[profile.release-small]
inherits = "release"
opt-level = "z"
lto = true
codegen-units = 1
panic = "abort"
strip = true
```

Build with:

```bash
cargo build --profile release-small
```

## Real-World Example

Let me show the cumulative impact on a real project — a CLI tool that parses JSON and writes CSV:

```
Configuration                                    Binary Size
─────────────────────────────────────────────────────────────
Default release build                            4.2 MB
+ strip = true                                   1.8 MB  (-57%)
+ lto = true, codegen-units = 1                  1.4 MB  (-22%)
+ opt-level = "z"                                1.1 MB  (-21%)
+ panic = "abort"                                0.9 MB  (-18%)
+ Replace serde_json with simd-json (minimal)    0.7 MB  (-22%)
─────────────────────────────────────────────────────────────
Total reduction                                  83% smaller
```

From 4.2MB to 700KB. The tool runs about 10% slower due to `opt-level = "z"`, but for a CLI tool that runs for milliseconds, nobody notices.

## Container Image Optimization

For Docker deployments, binary size directly affects image size and pull times:

```dockerfile
# Multi-stage build with size-optimized binary
FROM rust:1.77 AS builder
WORKDIR /app
COPY . .
RUN cargo build --profile release-small

# Minimal runtime image
FROM scratch
COPY --from=builder /app/target/release-small/my_binary /my_binary
ENTRYPOINT ["/my_binary"]
```

Using `scratch` as the base image gives you the absolute minimum — just your binary, nothing else. Final image size: your binary size + a few KB of metadata.

If you need a shell and basic tools:

```dockerfile
FROM gcr.io/distroless/cc-debian12
COPY --from=builder /app/target/release-small/my_binary /my_binary
ENTRYPOINT ["/my_binary"]
```

Distroless images are ~20MB. Alpine is ~5MB but requires musl linking:

```bash
# Build for musl (fully static, works with Alpine)
rustup target add x86_64-unknown-linux-musl
cargo build --profile release-small --target x86_64-unknown-linux-musl
```

## WebAssembly Binary Size

Wasm binaries have even tighter constraints — they're downloaded by the browser over the network.

```bash
# Build for Wasm
rustup target add wasm32-unknown-unknown
cargo build --profile release-small --target wasm32-unknown-unknown

# Use wasm-opt for additional shrinking
wasm-opt -Oz -o output.wasm target/wasm32-unknown-unknown/release-small/my_wasm.wasm

# Check size
ls -lh output.wasm
```

`wasm-opt` from the Binaryen toolkit can squeeze another 10-20% out of Wasm binaries. Combined with the Cargo optimizations, a simple Wasm module can be under 50KB.

```toml
# Additional Wasm-specific optimizations
[profile.release-wasm]
inherits = "release"
opt-level = "z"
lto = true
codegen-units = 1
panic = "abort"
strip = true

# For wasm-pack projects
[package.metadata.wasm-pack.profile.release]
wasm-opt = ["-Oz"]
```

## Analyzing What's Left

After applying all optimizations, use `cargo-bloat` to see what's still large:

```bash
cargo bloat --profile release-small -n 30

# If a specific function is unexpectedly large:
cargo bloat --profile release-small --filter "my_module"
```

Common survivors:
- **Panic formatting** — even with `panic = "abort"`, some formatting code survives if you use `unwrap()` with messages
- **Hash table implementation** — if you use `HashMap`, the hash table code is substantial
- **Iterator machinery** — heavily generic code generates a lot of specialized instances

## The Takeaway

Rust's default binary sizes are larger than necessary for most deployments. The good news: you can control it precisely. The minimum viable configuration for smaller binaries:

```toml
[profile.release]
strip = true
lto = true
codegen-units = 1
panic = "abort"
```

These four lines typically cut binary size by 70-80% with no code changes. Add `opt-level = "z"` if you're willing to trade some speed for size.

For containers, combine this with multi-stage Docker builds and minimal base images. For Wasm, add `wasm-opt`. For embedded, consider `#[no_std]`.

Measure with `cargo-bloat`, target the biggest contributors, and stop when you've hit your size budget.
