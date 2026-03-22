---
title: "Lesson 1: Performance Philosophy — Measure, don't guess"
author: Atharva Pandey
series: "Rust Performance Engineering"
lesson: 1
keywords: [Rust, rust, performance, profiling, optimization philosophy, premature optimization]
tags: [Rust tutorial, rust, performance]
date: '2025-03-15T08:32:00.000Z'
---

I once spent three days rewriting a hot loop to avoid a single allocation per iteration. Hand-rolled a custom arena, eliminated two clones, even switched from `HashMap` to a hand-tuned open-addressing table. Benchmarked the result: 0.3% improvement. The actual bottleneck? A DNS lookup buried in a library call that I never bothered to profile.

Three days. Zero meaningful impact. That's the lesson I want to start this entire course with.

## The Golden Rule

Here it is, the one rule that should govern every performance decision you make in Rust:

**Measure first. Optimize second. Measure again.**

I know, it sounds obvious. Everyone nods along when they hear it. Then they go off and start inlining functions because "it should be faster" or switching from `String` to `&str` everywhere because "allocations are bad." Without data, you're just guessing. And guessing is how you waste three days on a DNS bottleneck.

Rust gives you an incredible advantage here — the language is already fast by default. You're not fighting a garbage collector. You're not dealing with JIT warmup. The baseline performance of idiomatic Rust is *excellent*. Which means most "performance work" in Rust is actually about finding the specific spots where you're leaving performance on the table, not about rewriting everything.

## Why Intuition Fails

Modern hardware is adversarial to human intuition. Here's why your gut feeling about performance is usually wrong:

**CPU caches matter more than algorithmic complexity for small N.** A linear scan through a contiguous array is often faster than a binary search through a tree, because the array fits in L1 cache and the tree has you chasing pointers through main memory. We're talking 100x latency difference between L1 hits and cache misses.

**Branch prediction hides costs — until it doesn't.** That `if` statement in your hot loop might be free (predicted correctly 99% of the time) or devastatingly expensive (mispredicted, flushing the pipeline). You can't tell by reading the code.

**The compiler is smarter than you think.** LLVM performs inlining, loop unrolling, vectorization, dead code elimination, and dozens of other optimizations. That "optimization" you're about to hand-write? The compiler might already be doing it. Or your hand-written version might actually *prevent* an optimization the compiler would have applied.

Here's a concrete example that trips people up:

```rust
// "This must be slower — it allocates a Vec!"
fn sum_with_collect(data: &[i32]) -> i32 {
    data.iter().copied().collect::<Vec<_>>().iter().sum()
}

// "This is obviously faster — no allocation!"
fn sum_direct(data: &[i32]) -> i32 {
    data.iter().copied().sum()
}
```

Yes, `sum_direct` is faster — but have you actually measured by how much? For small inputs, the difference might be noise. For large inputs, the allocation cost is dwarfed by the memory bandwidth cost of reading the data. The *real* question is: does it matter in your actual use case? You need numbers to answer that.

## The Performance Engineering Workflow

After years of production Rust, I've settled on this workflow:

### 1. Define Your Performance Requirements

Before you optimize anything, know what "fast enough" means. Are you targeting:
- Latency? (p50, p99, p999)
- Throughput? (requests/second, bytes/second)
- Memory usage? (peak RSS, allocation rate)
- Binary size?
- Compile time?

These often conflict. Inlining improves throughput but increases binary size and compile time. Preallocating buffers reduces latency variance but increases memory usage. You need to know which trade-offs are acceptable.

### 2. Establish a Baseline

You can't improve what you haven't measured. Write benchmarks *before* you start optimizing. Use criterion or divan (we'll cover both in the next lesson). Get stable, reproducible numbers.

```rust
use criterion::{black_box, criterion_group, criterion_main, Criterion};

fn baseline_benchmark(c: &mut Criterion) {
    let data: Vec<i32> = (0..10_000).collect();

    c.bench_function("process_data_baseline", |b| {
        b.iter(|| {
            // Your actual code, unchanged
            process_data(black_box(&data))
        })
    });
}

fn process_data(data: &[i32]) -> i64 {
    data.iter()
        .filter(|&&x| x % 2 == 0)
        .map(|&x| x as i64 * x as i64)
        .sum()
}

criterion_group!(benches, baseline_benchmark);
criterion_main!(benches);
```

### 3. Profile to Find Bottlenecks

Don't guess where the bottleneck is. Use a profiler. On Linux, `perf` + flamegraphs. On macOS, `samply` or Instruments. On any platform, `cargo flamegraph`. We'll cover all of these in Lesson 3.

The profiler will show you where your program actually spends its time. It's almost never where you think.

### 4. Optimize the Bottleneck

Now — and only now — do you start optimizing. And you optimize the *actual bottleneck*, not the code that looks slow to your human eyes.

### 5. Measure the Impact

Run the same benchmarks again. Did it actually get faster? By how much? Was the complexity worth it?

I've had "optimizations" that made code *slower*. It happens. That's why you measure.

## Amdahl's Law — The Math You Can't Escape

Quick refresher because I see people ignore this constantly:

If a function takes 5% of your total runtime, and you make it *infinitely fast* (literally zero cost), your program is only 5% faster. That's Amdahl's Law.

```
Speedup = 1 / ((1 - P) + P/S)

Where:
  P = fraction of time spent in the optimized section
  S = speedup factor for that section
```

Let's say your program spends 60% of its time parsing JSON and 40% doing business logic. If you make the JSON parsing 10x faster:

```
Speedup = 1 / ((1 - 0.6) + 0.6/10)
        = 1 / (0.4 + 0.06)
        = 1 / 0.46
        = 2.17x
```

A 10x improvement in the hot path gives you just over 2x overall. If you'd optimized the business logic (40% of runtime) by 10x instead:

```
Speedup = 1 / ((1 - 0.4) + 0.4/10)
        = 1 / (0.6 + 0.04)
        = 1 / 0.64
        = 1.56x
```

The profiler doesn't just tell you *what* to optimize — it tells you the *maximum possible payoff*. If something is only 2% of runtime, walk away. Your time is better spent elsewhere.

## Rust-Specific Performance Considerations

A few things that are unique to Rust's performance profile:

**Monomorphization is your friend (mostly).** Generics in Rust are compiled to specialized versions for each concrete type. This means generic code is just as fast as hand-written specialized code. But it also means your binary can balloon in size, which can cause instruction cache pressure. We'll cover this in detail in the binary size lesson.

**Ownership eliminates a class of performance problems.** No GC pauses. No reference counting overhead (unless you use `Rc`/`Arc`). Deterministic destruction means memory is freed exactly when you expect. This is a real advantage — don't throw it away by wrapping everything in `Arc<Mutex<>>`.

**Iterators compile to the same code as manual loops.** This is one of Rust's great achievements. The iterator chain `data.iter().filter(pred).map(f).sum()` compiles to the same assembly as a hand-written `for` loop with an `if` and an accumulator. We'll prove this in Lesson 5.

**Debug builds are slow.** Like, really slow. Sometimes 10-50x slower than release builds. Always benchmark with `--release`. I've seen people file "Rust is slow" bug reports that were just debug builds.

```toml
# Cargo.toml — always benchmark in release mode
[profile.bench]
opt-level = 3
debug = true  # Keep debug symbols for profiling
```

## A Framework for Thinking About Performance

When I'm evaluating a performance-sensitive piece of code, I ask these questions in order:

1. **Is the algorithm right?** No amount of micro-optimization will save an O(n²) algorithm processing a million items. Check your algorithmic complexity first.

2. **Is the data layout right?** Are you using arrays of structs when structs of arrays would be better? Are you chasing pointers through a linked list when a `Vec` would keep data contiguous? (Lesson 8 goes deep on this.)

3. **Are you allocating unnecessarily?** Allocations aren't free — `malloc` takes ~25-100ns depending on the allocator. If you're allocating in a hot loop, that adds up. But profile first — the allocation might not be in the hot loop at all. (Lesson 4.)

4. **Is the compiler able to optimize?** Some patterns inhibit optimization. Dynamic dispatch (`dyn Trait`) prevents inlining. Excessive use of `Cell`/`RefCell` prevents aliasing optimizations. Short functions that aren't inlined add call overhead. (Lessons 5, 9.)

5. **Are you fighting the hardware?** Cache misses, branch mispredictions, false sharing in concurrent code. These are advanced topics but they're often the final bottleneck once you've fixed everything else. (Lesson 8.)

## What This Course Covers

Over the next 11 lessons, we'll work through the entire Rust performance engineering toolkit:

- **Benchmarking** (Lesson 2): criterion, divan, and how to write benchmarks that don't lie to you
- **Profiling** (Lesson 3): perf, flamegraphs, samply — finding where time actually goes
- **Allocations** (Lesson 4): Stack allocation, arenas, SmallVec, and reducing heap pressure
- **Iterators vs Loops** (Lesson 5): Do iterators actually compile to the same code? (Yes, with caveats)
- **String Performance** (Lesson 6): SmartString, CompactStr, and when string performance matters
- **Collections** (Lesson 7): Choosing the right data structure for your access pattern
- **Cache-Friendly Design** (Lesson 8): Data-oriented design, SoA vs AoS, and thinking about cache lines
- **Inlining** (Lesson 9): `#[inline]`, LTO, and when the compiler needs hints
- **Compile Times** (Lesson 10): Strategies that actually reduce `cargo build` time
- **Binary Size** (Lesson 11): Smaller binaries for containers and embedded
- **Zero-Copy Parsing** (Lesson 12): bytes, nom, winnow, and parsing without allocations

Every lesson follows the same pattern: here's the problem, here's how to measure it, here's the solution, here's the benchmark proving it works.

## The Takeaway

Performance engineering isn't about making things as fast as possible. It's about making things *fast enough*, with the minimum investment of complexity. Every optimization you add is code someone has to maintain. Every clever trick is a trick someone has to understand.

Start with idiomatic Rust. Measure. Profile. Optimize the bottleneck. Measure again. Stop when you've hit your target.

That's the philosophy. Let's get into the tools.
