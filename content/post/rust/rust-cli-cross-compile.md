---
title: "Lesson 7: Cross-Compilation for Linux, Mac, Windows — Build once, run anywhere"
author: Atharva Pandey
series: "Rust CLI & Tooling"
lesson: 7
keywords: [Rust, rust, CLI, cross-compilation, target, musl, static linking, GitHub Actions, CI]
tags: [Rust tutorial, rust, cli]
date: '2024-09-14T10:25:00.000Z'
---

First time I tried to cross-compile a Rust binary from my Mac to Linux, I ran `cargo build --target x86_64-unknown-linux-gnu` and got hit with a wall of linker errors. Missing `cc`, wrong `libc`, something about `crt1.o`. It felt like the "build once, run anywhere" promise was a lie. It wasn't — I just didn't understand how Rust's compilation model interacts with system libraries. Once that clicked, cross-compilation became routine.

## How Rust Compilation Works

Rust compiles to LLVM IR, then LLVM generates machine code for the target architecture. That part works across platforms — LLVM knows how to emit x86_64, aarch64, arm, riscv, wasm, and more. The problem is the *linker*.

After LLVM generates object files, a linker stitches them together into a binary. The linker needs to know about the target's system libraries, C runtime, and ABI. On your Mac, the system linker knows about macOS. It has no clue about Linux's `glibc` or Windows' `msvcrt`.

This means cross-compilation in Rust has two cases:

1. **Pure Rust, no system dependencies** — Easy. Provide the right linker and you're done.
2. **Uses C libraries (OpenSSL, SQLite, etc.)** — Harder. You need cross-compiled versions of those libraries.

## Setting Up Targets

First, install the target you want to build for:

```bash
# List all available targets
rustup target list

# Install common targets
rustup target add x86_64-unknown-linux-gnu
rustup target add x86_64-unknown-linux-musl
rustup target add aarch64-unknown-linux-gnu
rustup target add x86_64-pc-windows-gnu
rustup target add aarch64-apple-darwin
rustup target add x86_64-apple-darwin
```

This installs the Rust standard library compiled for each target. But you still need a linker.

## The Easy Path: cross

The `cross` tool from the cross-rs project wraps `cargo` and handles all the linker complexity using Docker containers:

```bash
cargo install cross

# Build for Linux from macOS — just works
cross build --target x86_64-unknown-linux-gnu --release
cross build --target aarch64-unknown-linux-gnu --release

# Build for Windows from macOS
cross build --target x86_64-pc-windows-gnu --release

# Run tests on the target platform (inside Docker)
cross test --target x86_64-unknown-linux-gnu
```

`cross` pulls a Docker image with the right compiler toolchain, linker, and system libraries for the target. Your source code is mounted into the container, compiled, and the binary comes back out. It handles OpenSSL, zlib, and other common C dependencies.

The downside: Docker. You need Docker running, the first build pulls a ~1GB image, and builds are slower because you're running in a container. For CI, this is fine. For rapid iteration during development, it's painful.

## Manual Cross-Compilation: Linux musl

For CLI tools, the best target is often `x86_64-unknown-linux-musl`. musl is an alternative C library that supports *fully static linking*. The resulting binary has zero runtime dependencies — no shared libraries, no specific glibc version. It runs on any Linux system, period.

On macOS, you need a musl cross-compiler:

```bash
# Install musl cross-compiler toolchain
brew install filosottile/musl-cross/musl-cross

# Add the target
rustup target add x86_64-unknown-linux-musl
```

Tell cargo which linker to use. In `.cargo/config.toml`:

```toml
[target.x86_64-unknown-linux-musl]
linker = "x86_64-linux-musl-gcc"
```

Now build:

```bash
cargo build --target x86_64-unknown-linux-musl --release
```

The resulting binary at `target/x86_64-unknown-linux-musl/release/myapp` is completely static:

```bash
# On the Linux machine, verify it's static
$ file myapp
myapp: ELF 64-bit LSB executable, x86-64, statically linked

$ ldd myapp
not a dynamic executable
```

No glibc dependency means it runs on Alpine, Debian, Ubuntu, CentOS, Amazon Linux — any x86_64 Linux, even minimal Docker containers built `FROM scratch`.

## macOS Universal Binaries

Apple's transition from Intel to ARM means you often want a universal binary that runs on both:

```bash
# Install both targets
rustup target add x86_64-apple-darwin
rustup target add aarch64-apple-darwin

# Build both
cargo build --target x86_64-apple-darwin --release
cargo build --target aarch64-apple-darwin --release

# Combine into universal binary
lipo -create \
    target/x86_64-apple-darwin/release/myapp \
    target/aarch64-apple-darwin/release/myapp \
    -output myapp-universal
```

`lipo` is Apple's tool for creating fat/universal binaries. The result runs natively on both Intel and Apple Silicon Macs without Rosetta translation.

## Cargo Configuration for Multiple Targets

When you're regularly building for multiple targets, set up `.cargo/config.toml` in your project:

```toml
# Default to release optimizations during cross-compilation
# (you'll pass --release anyway, but this documents your intent)

[target.x86_64-unknown-linux-musl]
linker = "x86_64-linux-musl-gcc"

[target.x86_64-unknown-linux-gnu]
linker = "x86_64-unknown-linux-gnu-gcc"

[target.aarch64-unknown-linux-gnu]
linker = "aarch64-unknown-linux-gnu-gcc"

[target.x86_64-pc-windows-gnu]
linker = "x86_64-w64-mingw32-gcc"
```

## Build Script for All Targets

Here's the `Makefile` pattern I use for multi-platform builds:

```makefile
NAME := myapp
VERSION := $(shell grep '^version' Cargo.toml | head -1 | cut -d'"' -f2)

TARGETS := \
    x86_64-unknown-linux-musl \
    aarch64-unknown-linux-musl \
    x86_64-apple-darwin \
    aarch64-apple-darwin \
    x86_64-pc-windows-gnu

.PHONY: all clean $(TARGETS)

all: $(TARGETS)

x86_64-unknown-linux-musl:
	cross build --release --target $@
	cp target/$@/release/$(NAME) dist/$(NAME)-$(VERSION)-linux-amd64

aarch64-unknown-linux-musl:
	cross build --release --target $@
	cp target/$@/release/$(NAME) dist/$(NAME)-$(VERSION)-linux-arm64

x86_64-apple-darwin:
	cargo build --release --target $@
	cp target/$@/release/$(NAME) dist/$(NAME)-$(VERSION)-darwin-amd64

aarch64-apple-darwin:
	cargo build --release --target $@
	cp target/$@/release/$(NAME) dist/$(NAME)-$(VERSION)-darwin-arm64

x86_64-pc-windows-gnu:
	cross build --release --target $@
	cp target/$@/release/$(NAME).exe dist/$(NAME)-$(VERSION)-windows-amd64.exe

clean:
	rm -rf dist/
	cargo clean
```

## GitHub Actions: The Real Answer

For most projects, you shouldn't cross-compile locally at all. Let CI do it. Each platform builds on its native runner:

```yaml
name: Release
on:
  push:
    tags: ['v*']

permissions:
  contents: write

jobs:
  build:
    strategy:
      matrix:
        include:
          - target: x86_64-unknown-linux-musl
            os: ubuntu-latest
            name: linux-amd64
          - target: aarch64-unknown-linux-musl
            os: ubuntu-latest
            name: linux-arm64
            cross: true
          - target: x86_64-apple-darwin
            os: macos-latest
            name: darwin-amd64
          - target: aarch64-apple-darwin
            os: macos-latest
            name: darwin-arm64
          - target: x86_64-pc-windows-msvc
            os: windows-latest
            name: windows-amd64

    runs-on: ${{ matrix.os }}

    steps:
      - uses: actions/checkout@v4

      - uses: dtolnay/rust-toolchain@stable
        with:
          targets: ${{ matrix.target }}

      - name: Install cross
        if: matrix.cross
        run: cargo install cross

      - name: Build (cross)
        if: matrix.cross
        run: cross build --release --target ${{ matrix.target }}

      - name: Build (native)
        if: "!matrix.cross"
        run: cargo build --release --target ${{ matrix.target }}

      - name: Package (unix)
        if: runner.os != 'Windows'
        run: |
          cd target/${{ matrix.target }}/release
          tar czf ../../../myapp-${{ matrix.name }}.tar.gz myapp
          cd ../../..

      - name: Package (windows)
        if: runner.os == 'Windows'
        run: |
          cd target/${{ matrix.target }}/release
          7z a ../../../myapp-${{ matrix.name }}.zip myapp.exe
          cd ../../..

      - name: Upload release assets
        uses: softprops/action-gh-release@v1
        with:
          files: |
            myapp-*.tar.gz
            myapp-*.zip
```

macOS targets build on macOS runners. Windows builds on Windows with MSVC (better compatibility than MinGW). Linux builds on Ubuntu with musl for static linking. Each build runs natively — no cross-compilation headaches.

## Conditional Compilation

Sometimes you need platform-specific code. Rust's `cfg` attributes handle this:

```rust
use std::path::PathBuf;

fn config_dir() -> PathBuf {
    #[cfg(target_os = "linux")]
    {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/root".to_string());
        PathBuf::from(home).join(".config").join("myapp")
    }

    #[cfg(target_os = "macos")]
    {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/Users/Shared".to_string());
        PathBuf::from(home)
            .join("Library")
            .join("Application Support")
            .join("myapp")
    }

    #[cfg(target_os = "windows")]
    {
        let appdata = std::env::var("APPDATA").unwrap_or_else(|_| "C:\\Users\\Public".to_string());
        PathBuf::from(appdata).join("myapp")
    }
}

fn open_browser(url: &str) {
    #[cfg(target_os = "linux")]
    {
        let _ = std::process::Command::new("xdg-open").arg(url).spawn();
    }

    #[cfg(target_os = "macos")]
    {
        let _ = std::process::Command::new("open").arg(url).spawn();
    }

    #[cfg(target_os = "windows")]
    {
        let _ = std::process::Command::new("cmd")
            .args(["/c", "start", url])
            .spawn();
    }
}

fn main() {
    println!("Config dir: {}", config_dir().display());
    // open_browser("https://example.com");
}
```

In practice, use the `directories` crate for config paths and the `open` crate for browser opening. But understanding `cfg` is essential for cases where crates don't exist.

## Handling C Dependencies

The biggest cross-compilation headache is C dependencies. OpenSSL is the classic problem child. Options:

**Option 1: Use pure Rust alternatives.** `rustls` instead of OpenSSL, `rusqlite` with the `bundled` feature instead of linking to system SQLite. This is almost always the right choice for CLI tools.

```toml
[dependencies]
# Instead of openssl:
reqwest = { version = "0.12", features = ["rustls-tls"], default-features = false }

# Instead of linking to system sqlite:
rusqlite = { version = "0.31", features = ["bundled"] }
```

**Option 2: Vendor the C library.** Some crates support a `vendored` or `bundled` feature that compiles the C library from source during `cargo build`:

```toml
[dependencies]
openssl = { version = "0.10", features = ["vendored"] }
```

**Option 3: Use `cross`.** It has the system libraries pre-installed in its Docker images.

My advice: avoid C dependencies in CLI tools whenever possible. Pure Rust dependencies cross-compile without any toolchain setup. That single decision eliminates 90% of cross-compilation problems.

## Stripping and Optimizing Binaries

Release binaries can be surprisingly large. A simple clap-based CLI can be 4MB+. Here's how to slim it down:

```toml
# Cargo.toml
[profile.release]
opt-level = "z"      # Optimize for size instead of speed
lto = true           # Link-time optimization — slower builds, smaller binary
codegen-units = 1    # Single codegen unit — slower builds, better optimization
panic = "abort"      # Don't include unwinding code
strip = true         # Strip debug symbols
```

Before: ~4.2MB. After: ~1.1MB. For a CLI tool, `opt-level = "z"` is usually fine — the performance difference is negligible when your bottleneck is I/O or network, not CPU.

On Linux, you can also use `upx` to compress the binary further, but it adds startup overhead and can trigger false positives with antivirus software. I generally skip it.

Cross-compilation in Rust is genuinely good once you understand the tooling. Use `cross` for CI, musl for static Linux binaries, and pure Rust dependencies to avoid headaches. Next up — distribution. Getting your binary into users' hands via Homebrew, GitHub Releases, and cargo install.
