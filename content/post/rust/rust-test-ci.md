---
title: "Lesson 11: Testing in CI — GitHub Actions for Rust"
author: Atharva Pandey
series: "Rust Testing Masterclass"
lesson: 11
keywords: [Rust, rust, testing, CI, GitHub Actions, continuous integration, automation, cargo test]
tags: [Rust tutorial, rust, testing]
date: '2024-09-05T16:45:00.000Z'
---

I merged a PR last year that passed all tests on my M2 MacBook and broke on the Linux CI runner. The issue? I'd used `std::path::PathBuf` with hardcoded forward slashes, which worked fine on macOS but blew up on Linux because the test setup expected a specific path format. CI exists to catch exactly this kind of "it works on my machine" problem. Here's how to set it up properly for Rust.

## The Problem

Running `cargo test` locally is great for development, but it doesn't catch:

- Platform-specific bugs (Linux vs macOS vs Windows behavior differences)
- Toolchain version issues (your local nightly vs the MSRV you claim to support)
- Missing dependencies you installed manually months ago
- Formatting and linting violations you've configured your editor to auto-fix
- Race conditions that only appear under different hardware timing

CI runs your tests in a clean, reproducible environment on every push. It's the last line of defense before broken code hits main.

## A Complete GitHub Actions Workflow

Here's the workflow I use for most Rust projects. I'll explain each section.

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
    name: Check
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - uses: Swatinem/rust-cache@v2
      - name: cargo check
        run: cargo check --all-targets --all-features

  fmt:
    name: Format
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
        with:
          components: rustfmt
      - name: cargo fmt
        run: cargo fmt --all -- --check

  clippy:
    name: Clippy
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
        with:
          components: clippy
      - uses: Swatinem/rust-cache@v2
      - name: cargo clippy
        run: cargo clippy --all-targets --all-features -- -D warnings

  test:
    name: Test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - uses: Swatinem/rust-cache@v2
      - name: cargo test
        run: cargo test --all-features

  doc:
    name: Doc tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - uses: Swatinem/rust-cache@v2
      - name: cargo doc
        run: cargo doc --no-deps --all-features
        env:
          RUSTDOCFLAGS: "-Dwarnings"
```

### Breaking It Down

**`RUSTFLAGS: "-Dwarnings"`** — Treats all warnings as errors. This prevents warnings from accumulating. If it warns, it fails. Strict, but it keeps the codebase clean.

**`dtolnay/rust-toolchain`** — The standard action for installing Rust. Use `@stable`, `@nightly`, or `@1.75.0` for a specific version.

**`Swatinem/rust-cache`** — Caches your `target/` directory and Cargo registry between runs. First CI run takes 3-5 minutes to compile everything. Subsequent runs take 30-60 seconds. This single action saves more time than anything else.

**Separate jobs for check, fmt, clippy, test** — They run in parallel. If formatting fails, you know immediately without waiting for the full test suite. Fast feedback.

## Cross-Platform Testing

If your crate runs on multiple platforms, test on all of them:

```yaml
  test:
    name: Test (${{ matrix.os }})
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - uses: Swatinem/rust-cache@v2
      - name: cargo test
        run: cargo test --all-features
```

The matrix strategy runs the test job three times — once per OS. You'll catch path separator issues, line ending differences, and platform-specific API behavior.

## Minimum Supported Rust Version (MSRV)

If you publish a crate, you should test against your declared MSRV:

```yaml
  msrv:
    name: MSRV
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@1.70.0
      - uses: Swatinem/rust-cache@v2
      - name: cargo check
        run: cargo check --all-features
```

`cargo check` is enough — you don't need to run the full test suite on old toolchains. You just need to verify the code compiles.

## Adding Coverage to CI

```yaml
  coverage:
    name: Coverage
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
        with:
          components: llvm-tools-preview
      - uses: taiki-e/install-action@cargo-llvm-cov
      - uses: Swatinem/rust-cache@v2
      - name: Generate coverage
        run: cargo llvm-cov --all-features --lcov --output-path lcov.info
      - name: Upload coverage
        uses: codecov/codecov-action@v4
        with:
          files: lcov.info
```

This generates coverage data and uploads it to Codecov. You get coverage comments on PRs showing which lines your changes added or left uncovered.

## Snapshot Test Review

If you use `insta` for snapshot tests, add a check that prevents merging unreviewed snapshots:

```yaml
  snapshots:
    name: Snapshot Review
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - uses: Swatinem/rust-cache@v2
      - name: Install cargo-insta
        run: cargo install cargo-insta
      - name: Check snapshots
        run: cargo insta test --review=fail
```

If any snapshot is pending review, CI fails. This forces developers to explicitly accept snapshot changes before merging.

## Running Expensive Tests Separately

Remember `#[ignore]` from lesson 1? Here's where it pays off:

```yaml
  test-fast:
    name: Fast Tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - uses: Swatinem/rust-cache@v2
      - name: cargo test
        run: cargo test --all-features

  test-slow:
    name: Slow Tests
    runs-on: ubuntu-latest
    needs: test-fast  # only run if fast tests pass
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - uses: Swatinem/rust-cache@v2
      - name: cargo test (ignored)
        run: cargo test --all-features -- --ignored
```

Fast tests run first. Slow tests (integration tests with real databases, network calls, heavy computation) only run if fast tests pass. No point waiting 20 minutes for slow tests when a unit test is broken.

## Caching Strategies

`Swatinem/rust-cache` handles most caching automatically, but there are tuning options:

```yaml
      - uses: Swatinem/rust-cache@v2
        with:
          # Cache key includes the lockfile hash
          # Different features = different cache
          key: "v1-${{ matrix.os }}"
          # Shared cache across branches (speeds up PR builds)
          shared-key: "stable"
          # Save even on failure (useful for debugging)
          save-if: ${{ github.ref == 'refs/heads/main' }}
```

One gotcha: cache size. The `target/` directory can grow to several GB. If your CI is slow despite caching, the cache upload/download might be the bottleneck. In that case, restrict what gets cached.

## Security Considerations

A few things to watch for:

**Don't run `cargo test` with `--release` unless you need to.** Release builds take significantly longer to compile, and debug builds catch more issues (integer overflow checks, debug assertions).

**Pin your action versions.** Use `@v4` not `@main` for actions. Pinning to a commit SHA is even safer:

```yaml
      - uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11
```

**Be careful with secrets in tests.** If your tests need API keys, use GitHub's encrypted secrets. Never hardcode them. And remember that secrets are not available in PR builds from forks — design your tests to skip or mock external calls when secrets are missing.

```yaml
      - name: Integration tests
        if: github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository
        run: cargo test --test integration -- --include-ignored
        env:
          API_KEY: ${{ secrets.API_KEY }}
```

## Complete Production Workflow

Here's what I use in production for a serious Rust project:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

env:
  CARGO_TERM_COLOR: always
  RUSTFLAGS: "-Dwarnings"

jobs:
  fmt:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
        with:
          components: rustfmt
      - run: cargo fmt --all -- --check

  clippy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
        with:
          components: clippy
      - uses: Swatinem/rust-cache@v2
      - run: cargo clippy --all-targets --all-features -- -D warnings

  test:
    needs: [fmt, clippy]
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
        rust: [stable, "1.75.0"]
        exclude:
          - os: macos-latest
            rust: "1.75.0"
          - os: windows-latest
            rust: "1.75.0"
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@master
        with:
          toolchain: ${{ matrix.rust }}
      - uses: Swatinem/rust-cache@v2
        with:
          key: ${{ matrix.os }}-${{ matrix.rust }}
      - run: cargo test --all-features
      - run: cargo doc --no-deps --all-features
        if: matrix.rust == 'stable' && matrix.os == 'ubuntu-latest'

  coverage:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
        with:
          components: llvm-tools-preview
      - uses: taiki-e/install-action@cargo-llvm-cov
      - uses: Swatinem/rust-cache@v2
      - run: cargo llvm-cov --all-features --lcov --output-path lcov.info
      - uses: codecov/codecov-action@v4
        with:
          files: lcov.info
```

The matrix runs stable on all three platforms and MSRV on Linux only (that's enough to catch compatibility issues). Coverage runs after tests pass. Formatting and linting run in parallel with everything else — they're fast and catch obvious issues immediately.

## Common CI Pitfalls

**Forgetting to cache.** Without caching, every CI run compiles your entire dependency tree from scratch. For a project with 200 dependencies, that's 10+ minutes of wasted time.

**Testing only on Linux.** If your crate is cross-platform, test on at least Linux and macOS. Windows if you can afford it. Platform bugs are real and they're embarrassing.

**Not testing with all features.** Feature flags change which code compiles. `cargo test --all-features` ensures nothing is silently broken behind a flag.

**Slow CI feedback loops.** If CI takes 30 minutes, developers stop waiting for it and merge blindly. Keep it under 10 minutes. Use caching, parallel jobs, and the fast/slow test split.

## What's Next

We've covered every type of test and how to run them automatically. The last piece is strategy: when do you write a unit test vs. an integration test vs. an e2e test? How do you structure a test suite for a real project? That's the architecture lesson, and it ties everything together.
