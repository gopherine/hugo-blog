---
title: "Lesson 8: Release Profiles and Build Optimization — Shipping fast binaries"
author: Atharva Pandey
series: "Rust Deployment & Operations"
lesson: 8
keywords: [Rust, rust, deployment, release profiles, LTO, optimization, PGO, binary size, codegen units, build optimization]
tags: [Rust tutorial, rust, deployment, devops]
date: '2025-05-08T13:50:00.000Z'
---

I was benchmarking two builds of the same service — one with default release settings, one with a tuned profile. Same code. Same hardware. The tuned build was 22% faster on our hot path and 40% smaller. I didn't change a single line of Rust. Just `Cargo.toml` settings.

Most Rust developers know about `cargo build --release`. Fewer know that `--release` is just a starting point — there's a whole set of knobs in the release profile that trade compile time for runtime performance, or binary size, or debuggability. Let me walk you through every one that matters.

## What `--release` Actually Does

When you run `cargo build --release`, Cargo uses the `[profile.release]` section of `Cargo.toml`. The defaults are:

```toml
[profile.release]
opt-level = 3       # Maximum optimization
debug = false       # No debug info
strip = "none"      # Don't strip symbols
lto = false         # No link-time optimization
codegen-units = 16  # Parallelize codegen
panic = "unwind"    # Stack unwinding on panic
overflow-checks = false  # No integer overflow checks
incremental = false # No incremental compilation
```

These defaults balance compile speed with runtime performance. For production, we can do better.

## The Production Profile I Use

Here's the release profile I've landed on for most production services:

```toml
[profile.release]
opt-level = 3
lto = "fat"
codegen-units = 1
strip = true
panic = "abort"
```

Let's break each one down.

### opt-level: How Hard the Optimizer Works

```toml
opt-level = 3  # Maximum optimization (default for release)
```

Options:
- `0` — no optimization (debug default)
- `1` — basic optimization
- `2` — most optimizations
- `3` — all optimizations, including auto-vectorization and loop unrolling
- `"s"` — optimize for binary size
- `"z"` — aggressively optimize for binary size

For services, `3` is almost always right. For embedded or WASM targets where code size matters more than speed, `"s"` or `"z"` can shrink binaries by 10-20% at a modest performance cost.

The difference between `2` and `3` is usually 5-15% on computation-heavy workloads. For IO-bound services (most web APIs), you won't notice. But it's free — the extra compile time is negligible compared to LTO.

### LTO: Link-Time Optimization

```toml
lto = "fat"
```

This is the single biggest optimization you can make. LTO lets the compiler optimize across crate boundaries, which normal compilation can't do.

Without LTO, each crate is compiled to object code independently. Functions from `serde` can't be inlined into your code. Call patterns between crates can't be optimized. With LTO, the compiler sees your entire program as one unit and can inline, devirtualize, and dead-code-eliminate across crate boundaries.

Options:
- `false` or `"off"` — no LTO (default)
- `"thin"` — lightweight cross-crate optimization, faster compilation
- `true` or `"fat"` — full LTO, maximum optimization, slowest compilation

**Fat LTO** gives the best runtime performance but can double or triple compile times. For CI pipelines where you're already caching dependencies, the incremental cost is usually acceptable — you're only recompiling your own code with LTO applied.

**Thin LTO** is a good middle ground. It provides most of LTO's benefits with much less compile time overhead. If fat LTO makes your CI builds too slow, try thin first:

```toml
lto = "thin"  # 80% of fat LTO's benefit, 30% of the compile cost
```

Real numbers from a project of mine (workspace with ~30 crates, ~50K lines):
- No LTO: 45s compile, 100% baseline performance
- Thin LTO: 70s compile, 108% performance
- Fat LTO: 130s compile, 112% performance

### codegen-units: Parallelism vs Optimization

```toml
codegen-units = 1
```

By default, release builds split each crate into 16 chunks and compile them in parallel. This is faster to compile but prevents some optimizations that require seeing the whole crate at once.

Setting `codegen-units = 1` forces single-threaded codegen per crate, which:
- Enables more inlining within the crate
- Allows better register allocation
- Permits more aggressive dead code elimination
- Typically improves performance by 5-10%

The tradeoff is compilation speed. With `codegen-units = 1`, you lose parallelism during the code generation phase. Combined with fat LTO, this can make release builds noticeably slower. But if you're building in CI with caching, the impact is manageable.

### strip: Removing Debug Symbols

```toml
strip = true
```

Debug symbols add 30-80% to binary size but aren't needed at runtime. `strip = true` removes them. Options:

- `"none"` — keep everything
- `"debuginfo"` — remove debug info but keep symbol names
- `true` or `"symbols"` — remove everything

For production binaries, `true` is the right call. If you need to debug production crashes, keep an unstripped binary as a CI artifact and use it for symbolication. Don't ship debug info to production.

Size difference on a typical web service:
- Not stripped: 25 MB
- `strip = "debuginfo"`: 12 MB
- `strip = true`: 8 MB

### panic: Unwind vs Abort

```toml
panic = "abort"
```

When Rust code panics, the default behavior is *unwinding* — running destructors up the call stack, like C++ exceptions. This ensures cleanup happens but requires unwinding tables in the binary.

`panic = "abort"` skips all that and immediately terminates the process. Benefits:

- Smaller binary (no unwinding tables, ~5-10% size reduction)
- Slightly faster code (the compiler can assume panics never return)
- Cleaner crash behavior — the OS can capture a core dump

The downside: `catch_unwind` stops working. If you use `catch_unwind` (directly or through libraries that do), you can't use `panic = "abort"`. In practice, very few production services need `catch_unwind` — if you're panicking, something is seriously wrong, and aborting is the right response. Let your orchestrator restart the process.

**One gotcha:** some testing frameworks use `catch_unwind` to handle test panics. If you set `panic = "abort"` globally, your tests might behave differently. Use a separate test profile:

```toml
[profile.release]
panic = "abort"

# Tests still use unwind
[profile.test]
panic = "unwind"
```

## Profile-Guided Optimization (PGO)

PGO is the nuclear option for performance. The idea: compile your code, run it under realistic workload, collect profiling data, then recompile using that data to guide optimization decisions.

```bash
# Step 1: Build with instrumentation
RUSTFLAGS="-Cprofile-generate=/tmp/pgo-data" \
    cargo build --release --target=x86_64-unknown-linux-gnu

# Step 2: Run under realistic workload
./target/release/myservice &
# Run your benchmark suite / load test against it
wrk -t4 -c100 -d60s http://localhost:3000/api/v1/users
kill %1

# Step 3: Merge profiling data
llvm-profdata merge -o /tmp/pgo-data/merged.profdata /tmp/pgo-data

# Step 4: Rebuild with profiling data
RUSTFLAGS="-Cprofile-use=/tmp/pgo-data/merged.profdata -Cllvm-args=-pgo-warn-missing-function" \
    cargo build --release --target=x86_64-unknown-linux-gnu
```

PGO typically gives 10-20% improvement on hot paths. The compiler uses real execution data to decide:
- Which branches are likely/unlikely
- Which functions to inline aggressively
- How to lay out code in memory for better instruction cache hit rates

Is it worth the complexity? For most services, no. LTO + codegen-units=1 gets you 80% of the way there with zero operational burden. PGO is worth it when you're at the point where 15% performance improvement saves you a meaningful number of servers.

### PGO in CI

If you decide PGO is worth it, automate it in CI:

```yaml
  pgo-build:
    name: PGO Release Build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable

      # Build instrumented binary
      - name: Build with PGO instrumentation
        env:
          RUSTFLAGS: "-Cprofile-generate=/tmp/pgo-data"
        run: cargo build --release

      # Run benchmark workload
      - name: Collect profile data
        run: |
          ./target/release/myservice &
          SERVER_PID=$!
          sleep 2
          # Run representative workload
          ./scripts/benchmark.sh
          kill $SERVER_PID
          wait $SERVER_PID || true

      # Merge and rebuild
      - name: Merge profile data
        run: llvm-profdata merge -o /tmp/merged.profdata /tmp/pgo-data

      - name: Build with PGO
        env:
          RUSTFLAGS: "-Cprofile-use=/tmp/merged.profdata"
        run: cargo build --release
```

## Comparing Profiles

Here's a comparison across different profile configurations for a real HTTP service (JSON API with database queries):

| Profile | Binary Size | Requests/sec | p99 Latency | Compile Time |
|---------|------------|-------------|-------------|-------------|
| Default release | 22 MB | 45,000 | 12ms | 40s |
| +strip | 8 MB | 45,000 | 12ms | 40s |
| +LTO thin | 7 MB | 48,500 | 10ms | 65s |
| +LTO fat | 6.5 MB | 50,200 | 9ms | 120s |
| +codegen-units=1 | 6 MB | 51,800 | 8.5ms | 140s |
| +panic=abort | 5.5 MB | 52,000 | 8.5ms | 135s |
| +PGO | 5.8 MB | 58,000 | 7ms | 300s (2 passes) |

The default-to-fully-optimized jump is substantial: 29% more throughput, 42% lower tail latency, 75% smaller binary. And all of it is just `Cargo.toml` configuration.

## Custom Profiles

Rust supports custom profiles for different scenarios:

```toml
# Fast compilation for development, but faster than debug
[profile.dev-fast]
inherits = "dev"
opt-level = 1

# Release-like but with debug info for profiling
[profile.profiling]
inherits = "release"
debug = true
strip = "none"

# Maximum optimization for production
[profile.production]
inherits = "release"
lto = "fat"
codegen-units = 1
strip = true
panic = "abort"
```

Use them:

```bash
cargo build --profile dev-fast
cargo build --profile profiling
cargo build --profile production
```

The `profiling` profile is particularly useful. It gives you release-mode performance with debug symbols, so tools like `perf`, `flamegraph`, and `samply` can show you function names and source locations in their output. I keep this around for every project — when you need to profile, you don't want to wait for a custom build.

## Target-Specific Optimization

You can optimize for the specific CPU your code will run on:

```bash
RUSTFLAGS="-C target-cpu=native" cargo build --release
```

This enables instructions specific to your CPU — AVX2, BMI2, whatever your hardware supports. Good for 3-5% improvement on compute-heavy workloads. But the binary might not run on older CPUs that lack those instructions.

For Docker deployments where you control the hardware:

```dockerfile
ENV RUSTFLAGS="-C target-cpu=x86-64-v3"
RUN cargo build --release
```

`x86-64-v3` targets CPUs from roughly 2015 onwards (Haswell and newer). It enables AVX2 and other useful instructions while remaining compatible with most server hardware.

## Binary Size Deep Dive

If binary size is your primary concern (CLI tools, WASM, embedded), here's the aggressive approach:

```toml
[profile.release-small]
inherits = "release"
opt-level = "z"
lto = "fat"
codegen-units = 1
strip = true
panic = "abort"
```

Additional tricks:

```toml
# Cargo.toml
[dependencies]
# Use minimal feature sets
serde = { version = "1", default-features = false, features = ["derive"] }
tokio = { version = "1", default-features = false, features = ["rt", "net", "macros"] }
```

Audit your dependency tree — every unused feature pulls in code. `cargo tree --edges features` shows you what features are enabled and why.

For extreme size reduction, `cargo-bloat` shows exactly what's taking space:

```bash
cargo install cargo-bloat
cargo bloat --release -n 20
```

## The Practical Recommendation

For most teams, this is all you need:

```toml
[profile.release]
lto = "thin"
strip = true
```

That's it. Two lines. You get 80% of the performance benefit with minimal compile time impact. Add `codegen-units = 1` and `lto = "fat"` when you've confirmed the compile time tradeoff is acceptable for your CI pipeline.

Don't cargo-cult the full optimization suite. Profile your application, identify whether it's CPU-bound or IO-bound, and optimize accordingly. An IO-bound JSON API won't benefit from PGO. A compute-heavy data pipeline absolutely will.

## Course Wrap-Up

Over these eight lessons, we've covered the full journey from Rust code to production:

1. **Docker** — efficient multi-stage builds with dependency caching
2. **Static linking** — single-binary deploys with musl
3. **CI/CD** — GitHub Actions with caching and cargo-nextest
4. **Observability** — tracing, metrics, and OpenTelemetry
5. **Health checks** — liveness and readiness probes
6. **Graceful shutdown** — draining connections without data loss
7. **Configuration** — typed, layered config with validation
8. **Release profiles** — tuning builds for performance and size

These aren't theoretical — they're the patterns I use every day for shipping Rust services. The beauty of Rust deployment is that once you've got these pieces in place, things are remarkably stable. The type system catches most bugs at compile time, the performance is predictable, and the operational footprint is tiny. That's the production Rust experience — boring in the best possible way.
