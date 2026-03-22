---
title: "Lesson 10: Benchmarking with criterion — Measure, don't guess"
author: Atharva Pandey
series: "Rust Testing Masterclass"
lesson: 10
keywords: [Rust, rust, testing, benchmarking, criterion, performance, profiling, cargo bench]
tags: [Rust tutorial, rust, testing]
date: '2024-09-02T13:20:00.000Z'
---

I once optimized a hot loop by replacing a `HashMap` lookup with a `Vec` indexed by a precomputed key. Felt clever. Ran the benchmarks. The "optimized" version was 15% *slower* because the `Vec` was huge, cache-cold, and the access pattern was random. Without the benchmark, I would've shipped that "improvement" and bragged about it. Performance intuition is unreliable. Measurement isn't.

## The Problem

Rust is fast by default, but that doesn't mean your code is fast. You can still write O(n^2) algorithms, cause cache thrashing, allocate unnecessarily, or clone data you could borrow. Worse, optimizations that seem obvious can backfire in ways you don't expect.

Benchmarking tells you two things: how fast your code is *now*, and whether it got faster or slower after a change. Without it, you're guessing — and guessing about performance is how you end up "optimizing" code into being slower.

## Why Not `#[bench]`?

Rust has built-in benchmarking with `#[bench]`, but it's been unstable for years and requires nightly. It also has fundamental measurement issues — no statistical analysis, no warmup control, and susceptibility to noise.

`criterion` is the community standard. It handles warmup, statistical significance, outlier detection, and generates HTML reports with charts. It works on stable Rust. There's no reason to use anything else.

## Setting Up criterion

```toml
[dev-dependencies]
criterion = { version = "0.5", features = ["html_reports"] }

[[bench]]
name = "my_benchmarks"
harness = false
```

The `harness = false` line is critical — it tells Cargo not to use the built-in test harness for this benchmark file. criterion provides its own `main()`.

## Your First Benchmark

```rust
// benches/my_benchmarks.rs

use criterion::{black_box, criterion_group, criterion_main, Criterion};

fn fibonacci_recursive(n: u64) -> u64 {
    match n {
        0 => 0,
        1 => 1,
        n => fibonacci_recursive(n - 1) + fibonacci_recursive(n - 2),
    }
}

fn fibonacci_iterative(n: u64) -> u64 {
    if n <= 1 {
        return n;
    }
    let mut a = 0u64;
    let mut b = 1u64;
    for _ in 2..=n {
        let temp = a + b;
        a = b;
        b = temp;
    }
    b
}

fn bench_fibonacci(c: &mut Criterion) {
    c.bench_function("fibonacci_recursive_20", |b| {
        b.iter(|| fibonacci_recursive(black_box(20)))
    });

    c.bench_function("fibonacci_iterative_20", |b| {
        b.iter(|| fibonacci_iterative(black_box(20)))
    });
}

criterion_group!(benches, bench_fibonacci);
criterion_main!(benches);
```

Run it:

```bash
cargo bench
```

Output looks something like:

```
fibonacci_recursive_20  time:   [25.432 µs 25.687 µs 25.955 µs]
fibonacci_iterative_20  time:   [4.1234 ns 4.1891 ns 4.2612 ns]
```

Three numbers: lower bound, estimate, upper bound (95% confidence interval). The recursive version is ~6000x slower. No surprises there, but now you have *numbers*.

### What's `black_box`?

`black_box` prevents the compiler from optimizing away your benchmark. Without it, the compiler might realize the result is unused and skip the computation entirely. Always wrap inputs in `black_box`.

## Comparing Implementations

criterion makes comparison benchmarks easy with groups:

```rust
use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion};

fn sum_loop(data: &[i32]) -> i64 {
    let mut total: i64 = 0;
    for &x in data {
        total += x as i64;
    }
    total
}

fn sum_iter(data: &[i32]) -> i64 {
    data.iter().map(|&x| x as i64).sum()
}

fn sum_fold(data: &[i32]) -> i64 {
    data.iter().fold(0i64, |acc, &x| acc + x as i64)
}

fn bench_sum_methods(c: &mut Criterion) {
    let sizes = [100, 1_000, 10_000, 100_000];

    let mut group = c.benchmark_group("sum");

    for &size in &sizes {
        let data: Vec<i32> = (0..size).collect();

        group.bench_with_input(
            BenchmarkId::new("loop", size),
            &data,
            |b, data| b.iter(|| sum_loop(black_box(data))),
        );

        group.bench_with_input(
            BenchmarkId::new("iter", size),
            &data,
            |b, data| b.iter(|| sum_iter(black_box(data))),
        );

        group.bench_with_input(
            BenchmarkId::new("fold", size),
            &data,
            |b, data| b.iter(|| sum_fold(black_box(data))),
        );
    }

    group.finish();
}

criterion_group!(benches, bench_sum_methods);
criterion_main!(benches);
```

This benchmarks all three implementations across four input sizes. The HTML report (in `target/criterion/`) shows comparison charts, violin plots, and regression analysis. It's genuinely beautiful.

## Benchmarking with Setup

When your benchmark needs setup that shouldn't be measured:

```rust
use criterion::{black_box, criterion_group, criterion_main, Criterion};
use std::collections::HashMap;

fn bench_lookup(c: &mut Criterion) {
    // Setup: build the map (not measured)
    let mut map = HashMap::new();
    for i in 0..10_000 {
        map.insert(format!("key_{}", i), i);
    }

    c.bench_function("hashmap_lookup_hit", |b| {
        b.iter(|| {
            map.get(black_box("key_5000"))
        })
    });

    c.bench_function("hashmap_lookup_miss", |b| {
        b.iter(|| {
            map.get(black_box("nonexistent"))
        })
    });
}

criterion_group!(benches, bench_lookup);
criterion_main!(benches);
```

The `HashMap` construction happens once, outside the measured loop. Only the lookup is benchmarked.

For benchmarks that need fresh state on each iteration, use `iter_batched`:

```rust
use criterion::{black_box, criterion_group, criterion_main, BatchSize, Criterion};

fn bench_vec_sort(c: &mut Criterion) {
    c.bench_function("sort_1000_random", |b| {
        b.iter_batched(
            || {
                // Setup: create a random-ish vector (runs before each measurement)
                let mut v: Vec<i32> = (0..1000).rev().collect();
                // Simple shuffle
                for i in (1..v.len()).rev() {
                    let j = i % (i + 1);
                    v.swap(i, j);
                }
                v
            },
            |mut v| {
                // Measured: sort in place
                v.sort();
                black_box(v)
            },
            BatchSize::SmallInput,
        )
    });
}

criterion_group!(benches, bench_vec_sort);
criterion_main!(benches);
```

`iter_batched` separates setup from the measured operation. `BatchSize::SmallInput` tells criterion the setup is cheap and can run once per iteration. For expensive setup, use `BatchSize::LargeInput`.

## Detecting Performance Regressions

When you run `cargo bench` after a code change, criterion automatically compares against the previous results:

```
fibonacci_iterative_20  time:   [4.1234 ns 4.1891 ns 4.2612 ns]
                        change: [+2.1234% +3.4567% +4.8901%] (p = 0.02 < 0.05)
                        Performance has regressed.
```

It tells you:
- The percentage change (with confidence interval)
- Whether the change is statistically significant (p-value)
- Whether it's an improvement or regression

This is enormously useful in CI. You can catch performance regressions before they merge.

## A Real-World Benchmark: String Processing

```rust
use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion};

fn count_words_split(text: &str) -> usize {
    text.split_whitespace().count()
}

fn count_words_manual(text: &str) -> usize {
    let mut count = 0;
    let mut in_word = false;
    for ch in text.chars() {
        if ch.is_whitespace() {
            in_word = false;
        } else if !in_word {
            in_word = true;
            count += 1;
        }
    }
    count
}

fn count_words_bytes(text: &str) -> usize {
    let bytes = text.as_bytes();
    let mut count = 0;
    let mut in_word = false;
    for &b in bytes {
        if b == b' ' || b == b'\n' || b == b'\t' || b == b'\r' {
            in_word = false;
        } else if !in_word {
            in_word = true;
            count += 1;
        }
    }
    count
}

fn generate_text(word_count: usize) -> String {
    (0..word_count)
        .map(|i| {
            let word_len = (i % 8) + 2;
            "a".repeat(word_len)
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn bench_word_count(c: &mut Criterion) {
    let mut group = c.benchmark_group("word_count");

    for &size in &[100, 1_000, 10_000] {
        let text = generate_text(size);

        group.bench_with_input(
            BenchmarkId::new("split_whitespace", size),
            &text,
            |b, text| b.iter(|| count_words_split(black_box(text))),
        );

        group.bench_with_input(
            BenchmarkId::new("manual_chars", size),
            &text,
            |b, text| b.iter(|| count_words_manual(black_box(text))),
        );

        group.bench_with_input(
            BenchmarkId::new("manual_bytes", size),
            &text,
            |b, text| b.iter(|| count_words_bytes(black_box(text))),
        );
    }

    group.finish();
}

criterion_group!(benches, bench_word_count);
criterion_main!(benches);
```

You'd expect the byte-level version to be fastest (no UTF-8 decoding overhead), but you might be surprised — `split_whitespace` is heavily optimized in the standard library and can outperform naive manual implementations. The benchmark will tell you the truth.

## Benchmark Hygiene

**Close other programs.** Background processes steal CPU cycles and add noise. Your benchmark running alongside a video call will give meaningless numbers.

**Run multiple times.** A single benchmark run can be an outlier. criterion handles this automatically — it runs hundreds or thousands of iterations and applies statistical analysis. Trust the confidence intervals.

**Benchmark realistic inputs.** Benchmarking with an empty vector or a 3-element array tells you nothing about production performance. Use input sizes that match your real workload.

**Don't benchmark debug builds.** Always use `cargo bench`, which compiles with optimizations. Debug builds have completely different performance characteristics.

**Profile before optimizing.** A benchmark tells you *how fast* something is. A profiler tells you *why* it's slow. Use both — benchmark first to identify the slow function, then profile to understand the bottleneck.

## Benchmarks in CI

You can run benchmarks in CI without the HTML reports:

```bash
# Just run, don't compare against previous
cargo bench -- --output-format bencher

# Save baseline
cargo bench -- --save-baseline main

# Compare against baseline
cargo bench -- --baseline main
```

There are GitHub Actions for criterion that post benchmark results as PR comments. The `criterion-compare-action` is particularly useful — it runs benchmarks on the base branch and PR branch and reports the diff.

## What's Next

You've got tests, coverage, and benchmarks. Now you need to run all of them automatically on every push. Setting up CI for Rust projects has some specific considerations — caching, test splitting, platform matrix testing — and that's what we'll cover next.
