---
title: "Lesson 5: Iterators vs Loops — Performance characteristics"
author: Atharva Pandey
series: "Rust Performance Engineering"
lesson: 5
keywords: [Rust, rust, performance, iterators, loops, zero-cost abstractions, SIMD, autovectorization]
tags: [Rust tutorial, rust, performance]
date: '2025-03-23T09:12:00.000Z'
---

When I first started writing Rust, I wrote everything as `for` loops. Old habits from C. Then someone on my team rewrote one of my loops as an iterator chain and I got annoyed — it looked "slower" to me. More function calls, closures, chaining. Obviously that's more overhead, right?

I benchmarked it. Same performance. Down to the nanosecond. I looked at the assembly. Identical. That was the day I stopped assuming and started measuring.

## The Zero-Cost Abstraction Promise

Rust's iterators are the poster child of zero-cost abstractions. The claim: an iterator chain like `.iter().filter().map().sum()` compiles to the same machine code as an equivalent hand-written loop. Let's verify that claim — and explore where it breaks down.

## Proving It: Assembly Comparison

Here are two functions that do the same thing — sum the squares of even numbers:

```rust
pub fn sum_squares_loop(data: &[i32]) -> i64 {
    let mut total: i64 = 0;
    for &x in data {
        if x % 2 == 0 {
            total += (x as i64) * (x as i64);
        }
    }
    total
}

pub fn sum_squares_iter(data: &[i32]) -> i64 {
    data.iter()
        .filter(|&&x| x % 2 == 0)
        .map(|&x| (x as i64) * (x as i64))
        .sum()
}
```

Compile with `cargo rustc --release -- --emit=asm` or check on [Compiler Explorer](https://godbolt.org/). The assembly output for both functions is identical. LLVM inlines the iterator adapter methods, removes the intermediate closures, and produces the same loop.

This isn't a toy example — it holds for complex chains too:

```rust
pub fn complex_chain(data: &[u32]) -> Vec<u64> {
    data.iter()
        .copied()
        .filter(|&x| x > 10)
        .map(|x| x as u64)
        .flat_map(|x| [x, x * 2])
        .take(1000)
        .collect()
}
```

The equivalent hand-written loop would be longer, harder to read, and *no faster*.

## When Iterators Win

There are cases where iterators actually outperform hand-written loops. Sounds counterintuitive, but it comes down to what information the compiler has.

### Autovectorization

LLVM can autovectorize iterator chains more effectively than some hand-written loops because the iterator abstraction gives it a clearer picture of the data flow:

```rust
pub fn sum_iter(data: &[f64]) -> f64 {
    data.iter().copied().sum()
}

pub fn sum_loop(data: &[f64]) -> f64 {
    let mut total = 0.0f64;
    for &x in data {
        total += x;
    }
    total
}
```

Both of these produce similar code, but consider a more complex case:

```rust
pub fn dot_product_iter(a: &[f64], b: &[f64]) -> f64 {
    a.iter().zip(b.iter()).map(|(&x, &y)| x * y).sum()
}

pub fn dot_product_loop(a: &[f64], b: &[f64]) -> f64 {
    let mut sum = 0.0;
    let len = a.len().min(b.len());
    for i in 0..len {
        sum += a[i] * b[i];
    }
    sum
}
```

The iterator version using `zip` gives LLVM a guarantee: we're processing pairs of elements from two slices. The hand-written loop with indexing introduces potential aliasing concerns and bounds checks (though LLVM often eliminates those too). In practice, both versions usually vectorize well, but the iterator version communicates intent more clearly to the optimizer.

### Bounds Check Elimination

Iterators avoid bounds checks entirely because the iteration is built into the abstraction. Hand-written index-based loops may retain bounds checks:

```rust
// May retain bounds checks (compiler might not prove i < len)
pub fn sum_indexed(data: &[i32]) -> i64 {
    let mut total: i64 = 0;
    for i in 0..data.len() {
        total += data[i] as i64; // potential bounds check
    }
    total
}

// No bounds checks possible — the iterator handles it
pub fn sum_iterator(data: &[i32]) -> i64 {
    data.iter().map(|&x| x as i64).sum()
}
```

In simple cases like this, LLVM eliminates the bounds check anyway. But in more complex indexing patterns — multiple arrays, computed indices — bounds checks can survive and cost 1-2ns each.

## When Iterators Lose

Okay, so iterators aren't always identical. Here are the cases where you might get better performance from a manual loop:

### Short-Circuiting with Complex State

When you need to maintain complex mutable state while iterating and potentially bail out early:

```rust
// This is awkward as an iterator chain
fn find_pattern(data: &[u8]) -> Option<usize> {
    let mut state = 0u32;
    for (i, &byte) in data.iter().enumerate() {
        state = update_state(state, byte);
        if state == MATCH_STATE {
            return Some(i);
        }
    }
    None
}

// Trying to force this into iterators isn't worth it
fn find_pattern_iter(data: &[u8]) -> Option<usize> {
    data.iter()
        .enumerate()
        .scan(0u32, |state, (i, &byte)| {
            *state = update_state(*state, byte);
            Some((i, *state))
        })
        .find_map(|(i, state)| {
            if state == MATCH_STATE { Some(i) } else { None }
        })
}
```

The `scan` + `find_map` version is harder to read, and while performance should be identical, you gain nothing from the abstraction here.

### Multiple Mutable References

When you need to update multiple parts of a data structure during iteration:

```rust
fn partition_in_place(data: &mut [i32], pivot: i32) -> usize {
    let mut write_idx = 0;
    for read_idx in 0..data.len() {
        if data[read_idx] <= pivot {
            data.swap(write_idx, read_idx);
            write_idx += 1;
        }
    }
    write_idx
}
```

This kind of in-place mutation with multiple indices doesn't map well to iterators. Don't force it.

### Nested Loops with Early Exit

```rust
fn find_pair(data: &[i32], target: i32) -> Option<(usize, usize)> {
    for i in 0..data.len() {
        for j in (i + 1)..data.len() {
            if data[i] + data[j] == target {
                return Some((i, j));
            }
        }
    }
    None
}
```

You *can* write this with iterators, but the result is worse in every way — readability, performance, and maintainability.

## Iterator Performance Pitfalls

### Pitfall 1: Collect Where You Don't Need To

```rust
// BAD: allocates an intermediate Vec for no reason
let result: i64 = data.iter()
    .filter(|&&x| x > 0)
    .collect::<Vec<_>>()  // unnecessary allocation!
    .iter()
    .map(|&&x| x as i64)
    .sum();

// GOOD: just chain them
let result: i64 = data.iter()
    .filter(|&&x| x > 0)
    .map(|&x| x as i64)
    .sum();
```

Every `.collect()` is a heap allocation. Only collect when you actually need a materialized collection.

### Pitfall 2: Cloning in Iterators

```rust
// BAD: cloning every string just to filter
let long_names: Vec<String> = names.iter()
    .cloned()  // clones every string!
    .filter(|name| name.len() > 10)
    .collect();

// GOOD: filter on references, clone only what survives
let long_names: Vec<String> = names.iter()
    .filter(|name| name.len() > 10)
    .cloned()  // only clones strings with len > 10
    .collect();
```

Order matters. Filter first, then clone/map/transform. This can be the difference between cloning 10,000 strings and cloning 50.

### Pitfall 3: Chain Length and Compilation

Very long iterator chains can increase compile times because LLVM has to inline and optimize through many layers. This rarely affects runtime performance, but it's worth knowing:

```rust
// This compiles to efficient code, but the type signature
// is extremely complex and LLVM works hard to optimize it
let result = data.iter()
    .filter(predicate1)
    .map(transform1)
    .flat_map(expand)
    .filter(predicate2)
    .map(transform2)
    .enumerate()
    .filter_map(|(i, x)| if i % 3 == 0 { Some(x) } else { None })
    .take(100)
    .collect::<Vec<_>>();
```

If you notice compile times creeping up, consider breaking long chains into named intermediate variables. The runtime cost is zero (LLVM inlines everything), but it helps the compiler.

## Benchmark: Real-World Comparison

Let's do a proper comparison with realistic data:

```rust
use criterion::{black_box, criterion_group, criterion_main, Criterion};

const DATA_SIZE: usize = 100_000;

fn setup_data() -> Vec<f64> {
    (0..DATA_SIZE).map(|i| i as f64 * 0.1).collect()
}

fn normalize_loop(data: &[f64]) -> Vec<f64> {
    let sum: f64 = {
        let mut s = 0.0;
        for &x in data { s += x; }
        s
    };
    let mean = sum / data.len() as f64;

    let variance: f64 = {
        let mut v = 0.0;
        for &x in data {
            let diff = x - mean;
            v += diff * diff;
        }
        v / data.len() as f64
    };
    let std_dev = variance.sqrt();

    let mut result = Vec::with_capacity(data.len());
    for &x in data {
        result.push((x - mean) / std_dev);
    }
    result
}

fn normalize_iter(data: &[f64]) -> Vec<f64> {
    let sum: f64 = data.iter().copied().sum();
    let mean = sum / data.len() as f64;

    let variance: f64 = data.iter()
        .map(|&x| {
            let diff = x - mean;
            diff * diff
        })
        .sum::<f64>() / data.len() as f64;
    let std_dev = variance.sqrt();

    data.iter()
        .map(|&x| (x - mean) / std_dev)
        .collect()
}

fn bench(c: &mut Criterion) {
    let data = setup_data();

    c.bench_function("normalize_loop", |b| {
        b.iter(|| normalize_loop(black_box(&data)))
    });

    c.bench_function("normalize_iter", |b| {
        b.iter(|| normalize_iter(black_box(&data)))
    });
}

criterion_group!(benches, bench);
criterion_main!(benches);
```

Typical results on my machine (100K f64 elements):

```
normalize_loop          time:   [312.45 µs 314.21 µs 316.33 µs]
normalize_iter          time:   [311.89 µs 313.75 µs 315.98 µs]
```

Within noise. Same performance. The iterator version is cleaner, more composable, and communicates intent better. That's the whole point.

## Guidelines

Here's my decision framework:

**Use iterators when:**
- The operation maps naturally to filter/map/fold
- You're chaining multiple transformations
- Readability matters (it always does)
- You want bounds-check-free iteration

**Use manual loops when:**
- Complex mutable state during iteration
- Multiple simultaneous mutable borrows needed
- Nested loops with early exit
- The iterator chain would be more confusing than a loop

**Never force it either way.** If the iterator version is contorted and hard to read, use a loop. If the loop version is boilerplate-heavy and error-prone, use iterators. Performance is the same — readability is the tiebreaker.

## The Takeaway

Rust's iterators genuinely are zero-cost abstractions. In the vast majority of cases, `.iter().filter().map().collect()` compiles to the same machine code as an equivalent `for` loop. The compiler is doing extraordinary work behind the scenes — inlining closures, eliminating intermediate values, and even autovectorizing.

Use whichever style is clearer for the specific problem. Profile if you suspect a difference. And stop feeling guilty about iterator chains — they're not slow.
