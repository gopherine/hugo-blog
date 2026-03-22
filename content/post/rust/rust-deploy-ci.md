---
title: "Lesson 3: CI/CD for Rust — GitHub Actions, caching, cargo-nextest"
author: Atharva Pandey
series: "Rust Deployment & Operations"
lesson: 3
keywords: [Rust, rust, deployment, CI/CD, GitHub Actions, cargo-nextest, caching, continuous integration]
tags: [Rust tutorial, rust, deployment, devops]
date: '2025-04-24T11:30:00.000Z'
---

My first Rust CI pipeline took 45 minutes. Forty-five. Every push triggered a full dependency build, tests ran sequentially, and clippy ran as a separate job that also built everything from scratch. I was burning through GitHub Actions minutes like they were free — which they are for open source, but my patience certainly wasn't.

Getting Rust CI right is mostly about caching. The compilation model means a clean build downloads and compiles hundreds of crates, and without caching, every single CI run pays that cost. Let me show you the pipeline I've landed on after iterating through dozens of projects.

## The Foundation: A Complete GitHub Actions Workflow

Here's the workflow I use for most Rust projects. I'll explain each piece after:

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

env:
  CARGO_TERM_COLOR: always
  RUSTFLAGS: "-Dwarnings"

jobs:
  check:
    name: Check & Lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: dtolnay/rust-toolchain@stable
        with:
          components: clippy, rustfmt

      - uses: Swatinem/rust-cache@v2

      - name: Check formatting
        run: cargo fmt --all -- --check

      - name: Run clippy
        run: cargo clippy --all-targets --all-features

      - name: Check compilation
        run: cargo check --all-targets --all-features

  test:
    name: Test
    runs-on: ubuntu-latest
    needs: check
    steps:
      - uses: actions/checkout@v4

      - uses: dtolnay/rust-toolchain@stable

      - uses: Swatinem/rust-cache@v2

      - uses: taiki-e/install-action@nextest

      - name: Run tests
        run: cargo nextest run --all-features

      - name: Run doc tests
        run: cargo test --doc --all-features

  build:
    name: Build Release
    runs-on: ubuntu-latest
    needs: test
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4

      - uses: dtolnay/rust-toolchain@stable

      - uses: Swatinem/rust-cache@v2

      - name: Build release binary
        run: cargo build --release

      - name: Upload binary
        uses: actions/upload-artifact@v4
        with:
          name: myservice
          path: target/release/myservice
```

Let's break down the decisions here.

## Toolchain Management: dtolnay/rust-toolchain

I use `dtolnay/rust-toolchain` instead of `actions-rs/toolchain` — the latter is unmaintained and doesn't support newer action runner features. David Tolnay's action is minimal, fast, and well-maintained:

```yaml
- uses: dtolnay/rust-toolchain@stable
  with:
    components: clippy, rustfmt
```

Pin to `stable` for most projects. If you need a specific version:

```yaml
- uses: dtolnay/rust-toolchain@1.82.0
```

For nightly (say, for benchmarks or feature-gated tests):

```yaml
- uses: dtolnay/rust-toolchain@nightly
  with:
    components: rustfmt
```

## Caching: The Make-or-Break Factor

`Swatinem/rust-cache` is the standard caching action for Rust. Without it, every job rebuilds every dependency from source. With it, incremental builds drop from 5-10 minutes to 30-60 seconds:

```yaml
- uses: Swatinem/rust-cache@v2
```

It caches `~/.cargo/registry`, `~/.cargo/git`, and the `target` directory. The cache key is based on `Cargo.lock`, so it invalidates when dependencies change — which is exactly what you want.

A few tips on caching:

**Share caches across jobs carefully.** Each job has its own cache, and different feature flags or targets produce different compilation artifacts. Don't try to share a cache between a `--all-features` job and a `--no-default-features` job — the artifacts aren't compatible, and you'll get confusing compilation errors.

**Cache size matters.** GitHub limits action caches to 10 GB per repository. Rust's `target` directory can easily exceed this for large workspaces. `rust-cache` handles cleanup, but if you have many branches with separate caches, you might hit the limit. The action prunes old entries automatically, but be aware of it.

**Set a shared key for related jobs:**

```yaml
- uses: Swatinem/rust-cache@v2
  with:
    shared-key: "main-build"
```

This lets the `test` job benefit from artifacts built in the `check` job.

## cargo-nextest: Better Test Runner

`cargo test` works fine, but `cargo-nextest` is better in every way that matters for CI:

```yaml
- uses: taiki-e/install-action@nextest

- name: Run tests
  run: cargo nextest run --all-features
```

Why nextest?

**Parallel execution.** nextest runs each test in its own process, enabling true parallelism. `cargo test` runs tests within a binary in separate threads, but different test binaries run sequentially by default. nextest runs everything in parallel.

**Better output.** When a test fails, nextest shows you exactly which test failed with its output isolated. No scrolling through interleaved stdout from 200 passing tests to find the one failure.

**Retries.** Flaky tests are a reality. nextest can retry failures:

```yaml
- name: Run tests
  run: cargo nextest run --all-features --retries 2
```

**JUnit output for CI integration:**

```yaml
- name: Run tests
  run: cargo nextest run --all-features --profile ci

# .config/nextest.toml
[profile.ci]
failure-output = "immediate-final"
status-level = "skip"

[profile.ci.junit]
path = "target/nextest/ci/junit.xml"
```

One caveat: nextest doesn't run doc tests. Doc tests use a fundamentally different execution model (they're compiled as separate binaries), so you need a separate step:

```yaml
- name: Run doc tests
  run: cargo test --doc --all-features
```

## RUSTFLAGS and Lint Enforcement

Setting `RUSTFLAGS: "-Dwarnings"` at the workflow level turns all warnings into errors. This is critical for CI — you don't want warnings accumulating silently. If it compiles with warnings locally, it should fail in CI:

```yaml
env:
  RUSTFLAGS: "-Dwarnings"
```

Pair this with a `clippy.toml` or `#![deny(...)]` in your `lib.rs` for project-specific lint rules:

```rust
// src/lib.rs
#![deny(clippy::unwrap_used)]
#![deny(clippy::expect_used)]
#![deny(missing_docs)]
```

## Docker Build in CI

If you're building Docker images (from Lesson 1), integrate it into your pipeline:

```yaml
  docker:
    name: Build & Push Docker Image
    runs-on: ubuntu-latest
    needs: test
    if: github.ref == 'refs/heads/main'
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4

      - uses: docker/setup-buildx-action@v3

      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: |
            ghcr.io/${{ github.repository }}:${{ github.sha }}
            ghcr.io/${{ github.repository }}:latest
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

The `cache-from` and `cache-to` directives use GitHub Actions' built-in cache for Docker layers. This means your multi-stage build's dependency layer (from Lesson 1) is cached across CI runs — same as your Cargo caches.

## Cross-Compilation in CI

Need to build for multiple targets? Add a matrix:

```yaml
  cross-build:
    name: Build (${{ matrix.target }})
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        include:
          - target: x86_64-unknown-linux-musl
            os: ubuntu-latest
          - target: aarch64-unknown-linux-musl
            os: ubuntu-latest
          - target: x86_64-apple-darwin
            os: macos-latest
          - target: aarch64-apple-darwin
            os: macos-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
        with:
          targets: ${{ matrix.target }}
      - uses: Swatinem/rust-cache@v2
        with:
          key: ${{ matrix.target }}

      - name: Install musl tools
        if: contains(matrix.target, 'musl')
        run: sudo apt-get update && sudo apt-get install -y musl-tools

      - name: Build
        run: cargo build --release --target ${{ matrix.target }}

      - uses: actions/upload-artifact@v4
        with:
          name: binary-${{ matrix.target }}
          path: target/${{ matrix.target }}/release/myservice
```

Note the `key: ${{ matrix.target }}` on the cache action — each target needs its own cache since the compiled artifacts are architecture-specific.

## Security Scanning

Add `cargo-audit` to catch known vulnerabilities in your dependency tree:

```yaml
  security:
    name: Security Audit
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: taiki-e/install-action@cargo-audit
      - name: Audit dependencies
        run: cargo audit
```

Run this on a schedule too, not just on push — new vulnerabilities are disclosed regularly:

```yaml
on:
  push:
    branches: [main]
  pull_request:
  schedule:
    - cron: '0 6 * * 1'  # Every Monday at 6 AM
```

## Optimizing Total Pipeline Time

A few more tricks I've picked up:

**Run fmt check first and fail fast.** Formatting is the cheapest check. If it fails, don't bother compiling anything.

**Use `cargo check` before `cargo build`.** `cargo check` is significantly faster because it skips code generation. Use it for the lint/check job, and only do a full build for the release artifact.

**Separate concerns into jobs with dependencies.** The `needs` keyword prevents wasteful builds:

```yaml
jobs:
  check:    # Fast: fmt + clippy + check
  test:     # Medium: compile + run tests
    needs: check
  build:    # Slow: release build + Docker
    needs: test
```

If formatting fails, tests never run. If tests fail, the release build never starts. This saves minutes and Actions billing.

**Consider `sccache` for large monorepos.** For projects with many contributors, a shared remote compilation cache can dramatically reduce build times:

```yaml
- name: Configure sccache
  uses: mozilla-actions/sccache-action@v0.0.4

- name: Build
  env:
    RUSTC_WRAPPER: sccache
  run: cargo build --release
```

## My Actual Pipeline Times

On a mid-size project (~50 crates in a workspace, ~200 dependencies):

- **Cold build (no cache):** 8 minutes
- **Warm build (cached, no source changes):** 40 seconds
- **Warm build (cached, source changes):** 1.5 minutes
- **Full pipeline (check → test → build):** 4 minutes with warm caches

That's fast enough that I never feel the urge to skip CI. Which is the whole point.

## Next Up

With CI producing our artifacts, we need to make sure what's running in production is actually observable. The next lesson covers tracing, metrics, and OpenTelemetry — because a binary that runs but tells you nothing about what it's doing is only slightly better than one that doesn't run at all.
