---
title: "Lesson 8: Reproducible Builds — Same source, same binary"
author: Atharva Pandey
series: "Rust Compiler Plugins & Build System"
lesson: 8
keywords: [Rust, rust, cargo, build system, reproducible builds, deterministic, supply chain, verification]
tags: [Rust tutorial, rust, cargo, build-system]
date: '2025-08-18T15:25:00.000Z'
---

A security auditor once asked me to prove that the binary running in production was actually built from the source code we claimed. I confidently ran `cargo build --release`, compared the hash of the output with the deployed binary, and... they were different. Same source, same compiler, same machine, different binary. That's when I learned that reproducible builds aren't automatic — even in Rust.

## Why Reproducible Builds Matter

A reproducible build means: given the same source code, same dependencies, same compiler, and same configuration, you get a bit-for-bit identical binary every time, on any machine.

Why care?

**Supply chain security.** If you can reproduce the exact binary from source, you can verify that the deployed artifact hasn't been tampered with. This is increasingly required in regulated industries and for open-source software distribution.

**Debugging confidence.** When you can reproduce the exact binary that a customer is running, you can debug with confidence that your local build matches their environment.

**Cache effectiveness.** Build caches work better when the same inputs always produce the same outputs. Non-determinism invalidates caches unnecessarily.

**Compliance.** Standards like SLSA (Supply-chain Levels for Software Artifacts) require reproducible builds at higher levels.

## What Makes Builds Non-Reproducible

Rust is actually pretty close to reproducible by default, but several things can break it:

### 1. Timestamps

The most common culprit. If your build script embeds the current time, every build produces a different binary:

```rust
// build.rs — THIS BREAKS REPRODUCIBILITY
fn main() {
    // Every build gets a different timestamp
    println!(
        "cargo:rustc-env=BUILD_TIME={}",
        chrono::Utc::now().to_rfc3339()
    );
}
```

Fix: use `SOURCE_DATE_EPOCH`, a standard environment variable for reproducible builds:

```rust
// build.rs — reproducible version
fn main() {
    let timestamp = std::env::var("SOURCE_DATE_EPOCH")
        .ok()
        .and_then(|s| s.parse::<i64>().ok())
        .unwrap_or_else(|| {
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_secs() as i64
        });

    println!("cargo:rustc-env=BUILD_TIMESTAMP={timestamp}");
    println!("cargo:rerun-if-env-changed=SOURCE_DATE_EPOCH");
}
```

When building for reproducibility, set `SOURCE_DATE_EPOCH` to the timestamp of your last commit:

```bash
SOURCE_DATE_EPOCH=$(git log -1 --format=%ct) cargo build --release
```

### 2. Absolute File Paths

The Rust compiler embeds absolute file paths in debug info and panic messages. Building on `/home/alice/project` and `/home/bob/project` produces different binaries.

```toml
# Cargo.toml — remap source paths
[profile.release]
# Available since Rust 1.77
trim-paths = "all"
```

Or via rustflags:

```toml
# .cargo/config.toml
[build]
rustflags = ["--remap-path-prefix=/home/user/project=."]
```

The `trim-paths` option is cleaner — it remaps all source paths to relative paths automatically. When you see a panic message, it'll say `src/main.rs:42` instead of `/home/alice/project/src/main.rs:42`.

### 3. Non-Deterministic Build Scripts

Build scripts that read from the environment, call external tools, or use randomness produce different outputs each time:

```rust
// build.rs — non-deterministic
fn main() {
    // Different on every machine
    let hostname = hostname::get().unwrap();
    println!("cargo:rustc-env=BUILD_HOST={}", hostname.to_string_lossy());

    // Different every time
    let uuid = uuid::Uuid::new_v4();
    println!("cargo:rustc-env=BUILD_ID={uuid}");
}
```

Fix: derive everything from source-controlled inputs:

```rust
// build.rs — deterministic
fn main() {
    // Derived from git — same for the same commit
    let hash = git_hash();
    println!("cargo:rustc-env=BUILD_ID={hash}");

    // Derived from Cargo.toml — changes are tracked
    let version = std::env::var("CARGO_PKG_VERSION").unwrap();
    println!("cargo:rustc-env=BUILD_VERSION={version}");

    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed=.git/HEAD");
}

fn git_hash() -> String {
    std::process::Command::new("git")
        .args(["rev-parse", "HEAD"])
        .output()
        .map(|o| String::from_utf8(o.stdout).unwrap_or_default().trim().to_string())
        .unwrap_or_else(|_| "unknown".to_string())
}
```

### 4. HashMap Iteration Order

If your build script or proc macro iterates over a `HashMap` and generates code based on the order, you'll get non-deterministic output:

```rust
// build.rs — non-deterministic ordering
use std::collections::HashMap;

fn generate_constants(map: &HashMap<String, i32>) -> String {
    let mut code = String::new();
    // HashMap iteration order is random!
    for (name, value) in map {
        code.push_str(&format!("pub const {name}: i32 = {value};\n"));
    }
    code
}
```

Fix: use `BTreeMap` or sort before generating:

```rust
use std::collections::BTreeMap;

fn generate_constants(map: &BTreeMap<String, i32>) -> String {
    let mut code = String::new();
    // BTreeMap iterates in sorted order — deterministic
    for (name, value) in map {
        code.push_str(&format!("pub const {name}: i32 = {value};\n"));
    }
    code
}
```

### 5. Dependency Resolution

Even with a lockfile, dependencies can introduce non-determinism through their own build scripts. Some crates probe the system for installed libraries, CPU features, or OS capabilities.

```toml
# Cargo.lock pins exact versions — commit this file
# But dependency build scripts can still behave differently on different machines
```

The lockfile ensures you get the same dependency *versions*, but not necessarily the same dependency *behavior* if those dependencies probe the system.

## A Reproducible Build Pipeline

Here's what a production-grade reproducible build setup looks like:

### Step 1: Pin Everything

```toml
# rust-toolchain.toml — pin the exact compiler
[toolchain]
channel = "1.78.0"
components = ["rustfmt", "clippy"]
```

```toml
# Cargo.toml — use workspace dependencies for version consistency
[workspace.dependencies]
# Pin exact versions for reproducibility
serde = { version = "=1.0.197", features = ["derive"] }
tokio = { version = "=1.37.0", features = ["full"] }
```

Wait — should you use exact version pinning (`=1.0.197`) in `Cargo.toml`? Generally no. The lockfile already pins exact versions. Exact pinning in `Cargo.toml` prevents `cargo update` from working and can cause conflicts with other crates. The lockfile is your reproducibility mechanism for versions.

### Step 2: Containerized Builds

The most reliable way to get reproducible builds is to build inside a well-defined container:

```dockerfile
# Build container with pinned everything
FROM rust:1.78.0-bookworm AS builder

# Pin system packages too
RUN apt-get update && apt-get install -y \
    musl-tools=1.2.4-1 \
    pkg-config=1.8.1-1 \
    && rm -rf /var/lib/apt/lists/*

RUN rustup target add x86_64-unknown-linux-musl

WORKDIR /build

# Cache dependencies
COPY Cargo.toml Cargo.lock ./
COPY crates/*/Cargo.toml crates/
RUN mkdir -p src && echo "fn main() {}" > src/main.rs
RUN cargo build --release --target x86_64-unknown-linux-musl || true

# Build the actual application
COPY . .
RUN SOURCE_DATE_EPOCH=$(git log -1 --format=%ct) \
    CARGO_INCREMENTAL=0 \
    cargo build --release --target x86_64-unknown-linux-musl

FROM scratch
COPY --from=builder /build/target/x86_64-unknown-linux-musl/release/myapp /myapp
ENTRYPOINT ["/myapp"]
```

Key environment variables for reproducible builds:

```bash
# Disable incremental compilation — it introduces non-determinism
export CARGO_INCREMENTAL=0

# Set source date epoch for timestamps
export SOURCE_DATE_EPOCH=$(git log -1 --format=%ct)

# Remap paths (if not using trim-paths in Cargo.toml)
export RUSTFLAGS="--remap-path-prefix=$(pwd)=/build"
```

### Step 3: Verification

Build twice and compare:

```bash
#!/bin/bash
# verify-reproducible.sh

set -euo pipefail

export SOURCE_DATE_EPOCH=$(git log -1 --format=%ct)
export CARGO_INCREMENTAL=0

# First build
cargo build --release --target x86_64-unknown-linux-musl
cp target/x86_64-unknown-linux-musl/release/myapp /tmp/build1

# Clean and rebuild
cargo clean
cargo build --release --target x86_64-unknown-linux-musl
cp target/x86_64-unknown-linux-musl/release/myapp /tmp/build2

# Compare
HASH1=$(sha256sum /tmp/build1 | awk '{print $1}')
HASH2=$(sha256sum /tmp/build2 | awk '{print $1}')

if [ "$HASH1" = "$HASH2" ]; then
    echo "PASS: Builds are identical ($HASH1)"
else
    echo "FAIL: Builds differ"
    echo "  Build 1: $HASH1"
    echo "  Build 2: $HASH2"

    # Show what's different
    diffoscope /tmp/build1 /tmp/build2 || true
fi
```

`diffoscope` is incredibly useful here — it shows you exactly which bytes differ and why. It can identify "this section contains a timestamp" or "this section contains an absolute path."

## Cargo.lock: The Foundation

The lockfile is the single most important file for reproducibility. It records the exact version, source, and checksum of every dependency:

```toml
# Cargo.lock (fragment)
[[package]]
name = "serde"
version = "1.0.197"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "3fb1c873e1b9b056a4dc4c0c198b24c3ffa059243875b5b70fcc4066ec02093f"
dependencies = [
 "serde_derive",
]
```

That `checksum` field is a hash of the crate's contents. If crates.io served a different version of `serde 1.0.197` than what you originally downloaded, the checksum would fail and the build would abort.

**Always commit `Cargo.lock` for binary projects.** For libraries, it's optional (library consumers use their own lockfile), but I commit it anyway because it helps contributors reproduce CI failures.

### Auditing Dependencies

Reproducible builds don't help if your dependencies are compromised. Use these tools:

```bash
# Check for known vulnerabilities
cargo audit

# Check dependency licenses
cargo deny check licenses

# Check for yanked or removed crates
cargo deny check bans

# Vendoring — copy all dependencies into your repo
cargo vendor
```

Vendoring deserves special mention. When you `cargo vendor`, all dependency source code is copied into a `vendor/` directory in your project. You can then build without network access:

```toml
# .cargo/config.toml (after running cargo vendor)
[source.crates-io]
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "vendor"
```

This protects against crate registry outages, crate yanking, and supply chain attacks. The tradeoff is a much larger repository.

## Recording Build Provenance

For compliance and auditing, record everything about a build:

```rust
// build.rs
use std::env;
use std::fs;
use std::path::Path;

fn main() {
    let out_dir = env::var("OUT_DIR").unwrap();

    // Collect build provenance
    let provenance = format!(
        r#"pub const BUILD_PROVENANCE: &str = r#"{{}
  "source_commit": "{}",
  "source_date_epoch": "{}",
  "rust_version": "{}",
  "cargo_version": "{}",
  "target": "{}",
  "profile": "{}",
  "opt_level": "{}",
  "features": "{}"
}}"#;"#,
        git_hash(),
        env::var("SOURCE_DATE_EPOCH").unwrap_or_else(|_| "unset".into()),
        rustc_version(),
        env::var("CARGO_PKG_VERSION").unwrap_or_default(),
        env::var("TARGET").unwrap_or_default(),
        env::var("PROFILE").unwrap_or_default(),
        env::var("OPT_LEVEL").unwrap_or_default(),
        env::var("CARGO_FEATURE_FLAGS").unwrap_or_default(),
    );

    fs::write(
        Path::new(&out_dir).join("provenance.rs"),
        provenance,
    ).unwrap();

    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-env-changed=SOURCE_DATE_EPOCH");
}

fn git_hash() -> String {
    std::process::Command::new("git")
        .args(["rev-parse", "HEAD"])
        .output()
        .map(|o| String::from_utf8(o.stdout).unwrap_or_default().trim().to_string())
        .unwrap_or_else(|_| "unknown".into())
}

fn rustc_version() -> String {
    std::process::Command::new("rustc")
        .arg("--version")
        .output()
        .map(|o| String::from_utf8(o.stdout).unwrap_or_default().trim().to_string())
        .unwrap_or_else(|_| "unknown".into())
}
```

Then expose it:

```rust
include!(concat!(env!("OUT_DIR"), "/provenance.rs"));

fn main() {
    if std::env::args().any(|a| a == "--provenance") {
        println!("{BUILD_PROVENANCE}");
        return;
    }
    // ... normal application code
}
```

```bash
$ ./myapp --provenance
{
  "source_commit": "a1b2c3d4e5f6789...",
  "source_date_epoch": "1723993200",
  "rust_version": "rustc 1.78.0 (9b00956e5 2024-04-29)",
  "target": "x86_64-unknown-linux-musl",
  "profile": "release",
  ...
}
```

## CI/CD for Reproducible Builds

Here's a GitHub Actions workflow that builds reproducibly and publishes attestations:

```yaml
name: Release

on:
  push:
    tags: ['v*']

jobs:
  build:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        target:
          - x86_64-unknown-linux-musl
          - aarch64-unknown-linux-musl

    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0  # Need full history for git log

      - uses: dtolnay/rust-toolchain@1.78.0
        with:
          targets: ${{ matrix.target }}

      - name: Install cross-compilation tools
        run: |
          sudo apt-get update
          sudo apt-get install -y musl-tools

      - name: Build
        env:
          SOURCE_DATE_EPOCH: ${{ github.event.head_commit.timestamp }}
          CARGO_INCREMENTAL: '0'
        run: |
          cargo build --release --target ${{ matrix.target }}

      - name: Record checksums
        run: |
          sha256sum target/${{ matrix.target }}/release/myapp > checksums.txt
          cat checksums.txt

      - name: Upload artifacts
        uses: actions/upload-artifact@v4
        with:
          name: binary-${{ matrix.target }}
          path: |
            target/${{ matrix.target }}/release/myapp
            checksums.txt

  verify:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: dtolnay/rust-toolchain@1.78.0
        with:
          targets: x86_64-unknown-linux-musl

      - name: Rebuild and verify
        env:
          SOURCE_DATE_EPOCH: ${{ github.event.head_commit.timestamp }}
          CARGO_INCREMENTAL: '0'
        run: |
          cargo build --release --target x86_64-unknown-linux-musl
          sha256sum target/x86_64-unknown-linux-musl/release/myapp

      - name: Compare with build artifact
        uses: actions/download-artifact@v4
        with:
          name: binary-x86_64-unknown-linux-musl
          path: /tmp/original

      - name: Verify reproducibility
        run: |
          ORIGINAL=$(sha256sum /tmp/original/myapp | awk '{print $1}')
          REBUILT=$(sha256sum target/x86_64-unknown-linux-musl/release/myapp | awk '{print $1}')
          if [ "$ORIGINAL" != "$REBUILT" ]; then
            echo "ERROR: Build is not reproducible!"
            exit 1
          fi
          echo "Verified: build is reproducible ($ORIGINAL)"
```

## The Checklist

When setting up reproducible builds, work through this list:

1. **Pin the Rust toolchain** — `rust-toolchain.toml`
2. **Commit Cargo.lock** — always for binaries
3. **Disable incremental compilation** — `CARGO_INCREMENTAL=0`
4. **Set SOURCE_DATE_EPOCH** — for any timestamp-dependent code
5. **Remap source paths** — `trim-paths = "all"` or `--remap-path-prefix`
6. **Audit build scripts** — no randomness, no system probing, sorted iteration
7. **Use BTreeMap in codegen** — never HashMap for ordered output
8. **Build in containers** — same OS, same tools, same everything
9. **Verify with two builds** — build twice, compare hashes
10. **Record provenance** — commit hash, compiler version, target, profile

Reproducible builds are one of those things that feel like overhead until you need them. When a security incident happens, when an auditor comes knocking, or when you need to debug a production issue from three months ago — that's when all this preparation pays off.

And that wraps up the Rust Compiler Plugins & Build System course. From Cargo workspaces to custom lints, from code generation to reproducible builds — you now have the tools to manage Rust projects of any scale. The build system is the foundation everything else rests on. Get it right, and your team moves fast. Get it wrong, and you're fighting your tools instead of shipping software.
