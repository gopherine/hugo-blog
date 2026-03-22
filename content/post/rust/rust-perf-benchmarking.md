---
title: "Lesson 2: Benchmarking with criterion and divan — Statistically rigorous benchmarks"
author: Atharva Pandey
series: "Rust Performance Engineering"
lesson: 2
keywords: [Rust, rust, performance, benchmarking, criterion, divan, cargo bench]
tags: [Rust tutorial, rust, performance]
date: '2025-03-17T14:18:00.000Z'
---

Last year I reviewed a PR where someone claimed their new serialization code was "2x faster." Their benchmark? `std::time::Instant::now()` called once before and once after. Single run. No warmup. No statistical analysis. The "2x speedup" was thermal throttling on the first run.

Benchmarking is harder than it looks. Let's do it properly.

## Why Naive Benchmarks Lie

Before we get into the tools, let me show you all the ways a naive benchmark can mislead you:

```rust
// DON'T do this
use std::time::Instant;

fn main() {
    let data: Vec<u32> = (0..1_000_000).collect();

    let start = Instant::now();
    let result = data.iter().filter(|&&x| x % 2 == 0).count();
    let elapsed = start.elapsed();

    println!("Count: {result}, took: {elapsed:?}");
}
```

Problems with this approach:

1. **The compiler might eliminate your computation.** If `result` isn't used in a way the compiler can't optimize away, LLVM will just... delete it. Your benchmark measures nothing.

2. **No warmup.** The first run pays for cache warming, page faults, and potential frequency scaling. It's not representative.

3. **Single sample.** You have no idea if this measurement is typical. Was there a context switch? Was the CPU throttling? One number tells you nothing.

4. **No statistical analysis.** Without multiple samples and proper statistics, you can't distinguish signal from noise. Is a 3% difference real or random variation?

## Setting Up criterion

criterion is the gold standard for Rust benchmarking. It runs your code hundreds of times, performs statistical analysis, detects outliers, and compares against previous runs automatically.

```toml
# Cargo.toml
[dev-dependencies]
criterion = { version = "0.5", features = ["html_reports"] }

[[bench]]
name = "my_benchmarks"
harness = false
```

Your first benchmark:

```rust
// benches/my_benchmarks.rs
use criterion::{black_box, criterion_group, criterion_main, Criterion};

fn fibonacci(n: u64) -> u64 {
    match n {
        0 => 0,
        1 => 1,
        n => fibonacci(n - 1) + fibonacci(n - 2),
    }
}

fn bench_fibonacci(c: &mut Criterion) {
    c.bench_function("fibonacci_20", |b| {
        b.iter(|| fibonacci(black_box(20)))
    });
}

criterion_group!(benches, bench_fibonacci);
criterion_main!(benches);
```

Run it with `cargo bench`. You'll get output like:

```
fibonacci_20            time:   [24.891 µs 25.034 µs 25.199 µs]
                        change: [-1.2304% -0.4785% +0.3546%] (p = 0.23 > 0.05)
                        No change in performance detected.
```

That output tells you: the mean time is ~25µs, the 95% confidence interval is [24.89µs, 25.20µs], and compared to the last run there's no statistically significant change. This is *infinitely* more useful than a single `Instant::now()` measurement.

## Understanding black_box

`black_box` is critical. It's a function that tells the compiler "don't optimize this away, and don't assume anything about this value." Without it, the compiler might:

- Precompute the result at compile time (constant folding)
- Eliminate the computation because the result isn't "really" used
- Hoist invariant computations out of the benchmark loop

```rust
// BAD: compiler might compute this at compile time
b.iter(|| fibonacci(20));

// GOOD: compiler can't see through black_box
b.iter(|| fibonacci(black_box(20)));

// ALSO GOOD: black_box the output
b.iter(|| black_box(fibonacci(20)));
```

I usually `black_box` both inputs and outputs when I'm being paranoid:

```rust
b.iter(|| black_box(fibonacci(black_box(20))));
```

## Benchmark Groups and Comparisons

The real power of criterion is comparing implementations:

```rust
use criterion::{
    black_box, criterion_group, criterion_main,
    BenchmarkId, Criterion,
};

fn linear_search(data: &[i32], target: i32) -> Option<usize> {
    data.iter().position(|&x| x == target)
}

fn binary_search_wrapper(data: &[i32], target: i32) -> Option<usize> {
    data.binary_search(&target).ok()
}

fn bench_search(c: &mut Criterion) {
    let mut group = c.benchmark_group("search");

    for size in [100, 1_000, 10_000, 100_000] {
        let data: Vec<i32> = (0..size).collect();
        let target = size / 2; // middle element — worst case for linear

        group.bench_with_input(
            BenchmarkId::new("linear", size),
            &size,
            |b, _| b.iter(|| linear_search(black_box(&data), black_box(target))),
        );

        group.bench_with_input(
            BenchmarkId::new("binary", size),
            &size,
            |b, _| b.iter(|| binary_search_wrapper(black_box(&data), black_box(target))),
        );
    }

    group.finish();
}

criterion_group!(benches, bench_search);
criterion_main!(benches);
```

This generates an HTML report (in `target/criterion/`) showing performance curves across input sizes. You can visually see where binary search starts winning — and it's not always at the size you'd expect, because cache effects matter.

## Throughput Benchmarks

For IO-bound or data-processing workloads, you want to measure throughput, not just time:

```rust
use criterion::{criterion_group, criterion_main, Criterion, Throughput, black_box};

fn bench_parsing(c: &mut Criterion) {
    let mut group = c.benchmark_group("json_parsing");

    for size in [1_000, 10_000, 100_000] {
        let input = generate_json(size); // hypothetical
        let bytes = input.len() as u64;

        group.throughput(Throughput::Bytes(bytes));
        group.bench_with_input(
            criterion::BenchmarkId::new("serde_json", size),
            &input,
            |b, input| {
                b.iter(|| {
                    let _: serde_json::Value = serde_json::from_str(
                        black_box(input)
                    ).unwrap();
                })
            },
        );
    }

    group.finish();
}

fn generate_json(num_entries: usize) -> String {
    let entries: Vec<String> = (0..num_entries)
        .map(|i| format!(r#"{{"id":{},"name":"item_{}","value":{}}}"#, i, i, i * 10))
        .collect();
    format!("[{}]", entries.join(","))
}

criterion_group!(benches, bench_parsing);
criterion_main!(benches);
```

Now criterion reports both time and MB/s, which is the number you actually care about for parsing workloads.

## divan — The Modern Alternative

divan is newer and I've been reaching for it more often lately. The API is cleaner, setup is minimal, and it runs faster because it uses a different statistical approach.

```toml
# Cargo.toml
[dev-dependencies]
divan = "0.1"

[[bench]]
name = "my_divan_benchmarks"
harness = false
```

```rust
// benches/my_divan_benchmarks.rs
fn main() {
    divan::main();
}

#[divan::bench]
fn fibonacci_20() -> u64 {
    fibonacci(divan::black_box(20))
}

#[divan::bench(args = [100, 1_000, 10_000, 100_000])]
fn linear_search(n: usize) -> Option<usize> {
    let data: Vec<i32> = (0..n as i32).collect();
    let target = n as i32 / 2;
    data.iter().position(|&x| x == divan::black_box(target))
}

fn fibonacci(n: u64) -> u64 {
    match n {
        0 => 0,
        1 => 1,
        n => fibonacci(n - 1) + fibonacci(n - 2),
    }
}
```

That's it. No `criterion_group!`, no `criterion_main!`, no builder pattern. Annotate your functions and go.

divan's output is a clean table:

```
Timer precision: 41 ns
my_divan_benchmarks    fastest       │ slowest       │ median        │ mean
├─ fibonacci_20        24.93 µs      │ 26.12 µs      │ 25.04 µs      │ 25.11 µs
├─ linear_search
│  ├─ 100              38.21 ns      │ 45.33 ns      │ 39.01 ns      │ 39.44 ns
│  ├─ 1000             302.1 ns      │ 351.8 ns      │ 310.2 ns      │ 312.7 ns
│  ├─ 10000            3.012 µs      │ 3.411 µs      │ 3.054 µs      │ 3.071 µs
│  ╰─ 100000           30.88 µs      │ 33.21 µs      │ 31.12 µs      │ 31.34 µs
```

### divan Generic Benchmarks

divan handles type parameterization beautifully:

```rust
#[divan::bench(types = [Vec<u8>, Vec<u32>, Vec<u64>])]
fn sum_collection<T>() -> T
where
    T: FromIterator<u8> + IntoIterator<Item = u8>,
{
    // This doesn't quite work as-is — but you get the idea.
    // divan makes parameterizing over types straightforward.
    (0..100u8).collect::<T>()
}
```

## criterion vs divan — Which Should You Use?

| Feature | criterion | divan |
|---------|----------|-------|
| Setup complexity | Moderate (macros, groups) | Minimal (attributes) |
| HTML reports | Yes, excellent | No |
| Regression detection | Yes, automatic | Basic |
| Statistical rigor | Extremely thorough | Good, faster |
| Ecosystem maturity | Very mature | Newer but solid |
| Compile time impact | Noticeable | Lighter |

My take: use criterion for CI pipelines where you want regression detection and HTML reports. Use divan for quick exploration and iterative development. They're not mutually exclusive — I have projects that use both.

## Common Benchmarking Mistakes

### Mistake 1: Benchmarking Unrealistic Inputs

```rust
// BAD: benchmarking with 10 elements tells you nothing
// about production performance with 10 million elements
#[divan::bench]
fn sort_tiny() {
    let mut v = vec![5, 3, 1, 4, 2, 8, 7, 6, 9, 0];
    v.sort();
}

// BETTER: benchmark across the range you'll see in production
#[divan::bench(args = [100, 10_000, 1_000_000])]
fn sort_realistic(n: usize) {
    let mut v: Vec<u64> = (0..n as u64).rev().collect();
    divan::black_box(&mut v).sort();
}
```

### Mistake 2: Including Setup in the Measurement

```rust
// BAD: you're benchmarking Vec creation + the actual work
c.bench_function("process", |b| {
    b.iter(|| {
        let data: Vec<u32> = (0..100_000).collect(); // setup!
        process(black_box(&data))
    })
});

// GOOD: create data once, benchmark only the processing
c.bench_function("process", |b| {
    let data: Vec<u32> = (0..100_000).collect();
    b.iter(|| process(black_box(&data)))
});
```

### Mistake 3: Not Accounting for Caching Effects

If your benchmark operates on the same data repeatedly, it'll be hot in cache. Production code might not have that luxury.

```rust
// This data will be in L1/L2 cache after the first iteration
let data: Vec<u32> = (0..1_000).collect();
b.iter(|| sum(black_box(&data)));

// For cache-cold benchmarks, you need to invalidate between iterations
// or use data sets larger than your cache
let data: Vec<u32> = (0..10_000_000).collect(); // way bigger than L3
b.iter(|| sum(black_box(&data)));
```

### Mistake 4: Forgetting --release

I put this in every lesson because people keep forgetting:

```bash
# This benchmarks your debug build. It's useless.
cargo bench

# Actually, criterion/divan handle this — they compile benchmarks
# in release mode by default. But if you're running code manually:
cargo run --release
```

criterion and divan both compile benchmarks with optimizations by default, but double-check your `Cargo.toml` profile settings.

## Setting Up CI Regression Detection

criterion can catch performance regressions in CI. Here's a basic approach:

```yaml
# .github/workflows/bench.yml
name: Benchmarks
on:
  pull_request:
    branches: [main]

jobs:
  benchmark:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable

      - name: Run benchmarks
        run: cargo bench -- --output-format bencher | tee output.txt

      - name: Store benchmark result
        uses: benchmark-action/github-action-benchmark@v1
        with:
          tool: 'cargo'
          output-file-path: output.txt
          alert-threshold: '110%'  # alert on >10% regression
          fail-on-alert: true
```

This fails the PR if any benchmark regresses by more than 10%. Adjust the threshold based on your noise floor — some benchmarks have 5% variance just from system jitter.

## Practical Tips

**Disable Turbo Boost for stable numbers.** CPU frequency scaling adds noise. On Linux:

```bash
echo 1 | sudo tee /sys/devices/system/cpu/intel_pstate/no_turbo
```

**Pin to a specific CPU core:**

```bash
taskset -c 0 cargo bench
```

**Close everything else.** Browser tabs, Slack, Docker containers — they all compete for cache and CPU time. I have a dedicated "benchmark mode" script that kills non-essential processes.

**Run benchmarks multiple times.** Even with criterion's statistical analysis, I usually run benchmarks 3 times on different occasions before drawing conclusions. System load varies more than you think.

**Commit your benchmark code.** Benchmarks are tests. They belong in version control. Put them in `benches/` and treat them like any other test suite.

## The Takeaway

Good benchmarking isn't hard, but it requires discipline. Use criterion or divan — they handle the statistics so you don't have to. Always `black_box` your inputs and outputs. Benchmark with realistic data sizes. And never, ever trust a single measurement.

In the next lesson, we'll go beyond benchmarks into profiling — finding *where* your program spends its time, not just *how long* it takes.
