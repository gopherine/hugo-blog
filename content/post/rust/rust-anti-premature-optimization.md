---
title: "Lesson 8: Premature Optimization — Profile before you optimize"
author: Atharva Pandey
series: "Rust Anti-Patterns & Code Smells"
lesson: 8
keywords: [Rust, rust, anti-patterns, code quality, premature optimization, profiling, benchmarking, performance]
tags: [Rust tutorial, rust, anti-patterns, code-quality]
date: '2025-04-19T13:42:00.000Z'
---

A teammate once spent three days replacing every `String` in our data model with a custom arena-allocated string type. The rationale: "String allocations are slow, and we're processing a lot of data." Sounds reasonable, right? After the rewrite, I ran the benchmarks. The improvement was within noise — less than 1%. The actual bottleneck was network I/O to an external API, which accounted for 94% of the request latency. Those three days of intricate unsafe string manipulation? Completely wasted. And the code was now harder to read, harder to maintain, and had a subtle use-after-free bug that we found six weeks later.

Rust attracts performance-minded people. That's one of its strengths. But it also means the Rust community has a particularly virulent strain of premature optimization, where developers sacrifice readability and correctness for performance gains that don't matter.

## The Smell

Premature optimization in Rust takes specific forms:

```rust
// "Vec allocation is expensive, I'll use SmallVec everywhere"
use smallvec::SmallVec;

fn process_items(items: &[Item]) -> SmallVec<[ProcessedItem; 8]> {
    items.iter()
        .filter(|i| i.is_valid())
        .map(|i| process(i))
        .collect()
    // The Vec would have been fine — this function runs twice per request
}

// "I need to avoid cloning this string"
fn format_greeting<'a>(first: &'a str, last: &'a str, buf: &'a mut String) -> &'a str {
    buf.clear();
    buf.push_str("Hello, ");
    buf.push_str(first);
    buf.push(' ');
    buf.push_str(last);
    buf.push('!');
    buf.as_str()
    // A simple format!("Hello, {first} {last}!") would've been crystal clear
}

// "HashMap lookups are O(1) but with a bad constant factor, let me use a BTreeMap"
// (with 12 entries)
fn build_config() -> BTreeMap<&'static str, ConfigValue> {
    let mut map = BTreeMap::new();
    map.insert("host", ConfigValue::Str("localhost"));
    map.insert("port", ConfigValue::Int(8080));
    // ... 10 more entries
    map
    // For 12 entries, even a Vec<(K, V)> with linear search would be fine
}

// Lifetime gymnastics to avoid one allocation
struct Parser<'input, 'config, 'scratch> {
    input: &'input str,
    config: &'config ParseConfig,
    scratch: &'scratch mut Vec<u8>,
    // Three lifetime parameters to avoid allocating a Vec
    // This function processes 10 requests per second
}
```

## Why It's Actually Bad

**You're optimizing the wrong thing.** This is the fundamental problem. Without profiling, you're guessing where the bottleneck is. And humans are terrible at guessing. Study after study — and my own experience — confirms that developers almost always optimize code that isn't the bottleneck.

Here's what typically dominates latency in real applications:

- Network I/O: 1-100ms per call
- Disk I/O: 0.1-10ms per operation
- Database queries: 1-50ms per query
- Memory allocation: 0.00001ms (10 nanoseconds)

If your request takes 50ms because of a database query, saving 10 nanoseconds on a string allocation is optimizing 0.00002% of the total time. You could make that string allocation infinitely fast and nobody would notice.

**Readability suffers.** `format!("Hello, {name}!")` is instantly understandable. The buffer-reuse version with lifetime annotations requires you to understand ownership, borrowing, and the caller's responsibility to maintain the buffer. That cognitive overhead has a real cost — every developer who reads the code pays it, forever.

**Correctness suffers.** The more "clever" the code, the more likely it contains bugs. Arena allocators, custom unsafe string types, hand-rolled SIMD — these are all sources of subtle, hard-to-detect bugs. The person who wrote the code might understand it perfectly. The person maintaining it six months later will not.

**It makes profiling harder later.** Over-optimized code obscures the actual performance characteristics. When everything is hand-tuned, it's harder to see the forest for the trees — harder to identify the real bottlenecks when they emerge.

## The Fix

### Step 1: Write clear, idiomatic code first

Start with the simplest, most readable implementation:

```rust
fn process_orders(orders: Vec<Order>) -> Vec<ProcessedOrder> {
    orders
        .into_iter()
        .filter(|o| o.is_valid())
        .map(|o| ProcessedOrder {
            id: o.id,
            total: calculate_total(&o.items),
            summary: format!("Order {} — {} items, ${:.2}", o.id, o.items.len(), calculate_total(&o.items)),
            processed_at: Utc::now(),
        })
        .collect()
}
```

Is this optimal? No. It calls `calculate_total` twice. It allocates a string for each order. It creates a new `Vec`. But it's clear, correct, and takes 30 seconds to understand.

### Step 2: Measure before changing anything

Use real profiling tools. Not intuition. Not "I think this is slow." Actual measurements.

**For micro-benchmarks, use `criterion`:**

```rust
use criterion::{criterion_group, criterion_main, Criterion};

fn bench_process_orders(c: &mut Criterion) {
    let orders = generate_test_orders(1000);

    c.bench_function("process_orders", |b| {
        b.iter(|| process_orders(orders.clone()))
    });
}

criterion_group!(benches, bench_process_orders);
criterion_main!(benches);
```

Run it: `cargo bench`. You get statistically rigorous timing with confidence intervals. Now you know exactly how fast it is, not how fast you *think* it is.

**For application-level profiling, use `perf` or `flamegraph`:**

```bash
# Build with debug symbols
cargo build --release

# Profile with perf (Linux)
perf record --call-graph dwarf ./target/release/myapp
perf report

# Or generate a flamegraph
cargo install flamegraph
cargo flamegraph -- ./target/release/myapp
```

A flamegraph shows you exactly where CPU time is spent. Often the result is surprising. I've seen developers optimize a parser that accounts for 2% of CPU time while ignoring serialization that accounts for 40%.

**For memory profiling, use `dhat`:**

```rust
#[cfg(feature = "dhat-heap")]
#[global_allocator]
static ALLOC: dhat::Alloc = dhat::Alloc;

fn main() {
    #[cfg(feature = "dhat-heap")]
    let _profiler = dhat::Profiler::new_heap();

    // ... your program ...
}
```

This tells you how many allocations happen, how large they are, and where they come from. Maybe those `String` allocations you're worried about are 0.1% of total allocations and the real culprit is a `Vec` that grows quadratically.

### Step 3: Optimize the bottleneck, not the code you happen to be looking at

Once you've profiled, optimize what matters:

```rust
// Profile showed: calculate_total called twice per order, accounts for 15% of time
// Fix: calculate once
fn process_orders(orders: Vec<Order>) -> Vec<ProcessedOrder> {
    orders
        .into_iter()
        .filter(|o| o.is_valid())
        .map(|o| {
            let total = calculate_total(&o.items); // calculate once
            ProcessedOrder {
                id: o.id,
                summary: format!("Order {} — {} items, ${total:.2}", o.id, o.items.len()),
                total,
                processed_at: Utc::now(),
            }
        })
        .collect()
}
```

This is still readable. The optimization is obvious — we're just avoiding redundant work. No lifetime gymnastics, no custom allocators, no unsafe code.

### Step 4: If you need to optimize further, keep it isolated

When the profiler says a specific function is hot, optimize that function. Don't let the optimization leak into the rest of the codebase:

```rust
// Public API stays clean and simple
pub fn search(index: &Index, query: &str) -> Vec<SearchResult> {
    // Implementation can be optimized internally
    let normalized = normalize_query(query);
    let candidates = fast_candidate_selection(index, &normalized); // SIMD-optimized
    rank_and_filter(candidates)
}

// The optimization is contained in this module
mod fast_search {
    // Unsafe SIMD code lives here, behind a safe API
    // Nobody outside this module needs to know about it
    pub(super) fn fast_candidate_selection(index: &Index, query: &NormalizedQuery) -> Vec<Candidate> {
        // ... optimized implementation ...
    }
}
```

The callers of `search` never see the optimization. The API stays simple. The complexity is isolated.

## The Optimization Checklist

Before optimizing anything, check these boxes:

1. **Is there a measured performance problem?** Not "I think it might be slow" — an actual measurement showing it doesn't meet requirements.
2. **Have I profiled?** I know *specifically* which function/line is the bottleneck.
3. **Is this the top bottleneck?** Optimizing the #3 bottleneck when #1 is 10x larger is a waste.
4. **Will the optimization actually help?** Reducing a 1ms operation to 0.5ms doesn't matter if the request takes 200ms.
5. **Is the complexity worth it?** If the optimization saves 5ms but makes the code 3x harder to maintain, it might not be worth it.

## Rust-Specific Temptations to Resist

- **Replacing `String` with `&str` everywhere.** Sometimes owned strings are the right choice. If a value needs to outlive a function call, own it.
- **Avoiding `Vec` allocations with stack arrays.** Unless the profiler says allocation is the bottleneck, `Vec` is fine. It's one of the most optimized data structures in the standard library.
- **Using `unsafe` for performance.** The compiler is very good at optimizing safe code. `unsafe` should be a last resort after profiling proves safe code isn't fast enough.
- **`#[inline(always)]` on every function.** The compiler is better at inlining decisions than you are. Let it do its job.
- **Hand-rolling iterators instead of using combinators.** `iter().filter().map().collect()` optimizes down to a tight loop. You won't beat it with a hand-written `for` loop.

Rust already gives you excellent performance by default. Release mode with `-O2` or `-O3` produces fast code from clean, idiomatic Rust. Trust the compiler, trust the standard library, and save your optimization energy for the 5% of code where it actually matters.

Write it clean first. Measure. Then optimize what the profiler tells you to optimize. That's it. That's the whole methodology.
