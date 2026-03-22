---
title: "Lesson 13: Monomorphization — How generics become fast"
author: Atharva Pandey
series: "Rust Traits & Generics Masterclass"
lesson: 13
keywords: [Rust, rust, traits, generics, monomorphization, zero-cost abstraction, codegen, binary size]
tags: [Rust tutorial, rust, traits, generics]
date: '2024-07-07T11:45:00.000Z'
---

"Zero-cost abstractions" is Rust's battle cry. But what does that actually mean for generics? How does `Vec<i32>` and `Vec<String>` both exist without runtime overhead? The answer is monomorphization — the compiler generates a separate, specialized copy of your generic code for each concrete type used. You write it once, the compiler duplicates it for each type, and the result runs as fast as hand-written specialized code.

This is brilliant. It's also a double-edged sword. And understanding the mechanism changes how you design generic APIs.

## What Actually Happens

When you write a generic function:

```rust
fn add_one<T: std::ops::Add<Output = T> + From<i32>>(x: T) -> T {
    x + T::from(1)
}

fn main() {
    let a: i32 = add_one(5);
    let b: f64 = add_one(5.0_f64);
    let c: i64 = add_one(100_i64);
    println!("{}, {}, {}", a, b, c);
}
```

The compiler sees three call sites with three different types. It generates three separate functions:

```rust
// What the compiler essentially produces:
fn add_one_i32(x: i32) -> i32 { x + 1 }
fn add_one_f64(x: f64) -> f64 { x + 1.0 }
fn add_one_i64(x: i64) -> i64 { x + 1 }
```

Each function is fully specialized — no vtable, no indirection, no type checks. The CPU executes the same instructions as if you'd written the type-specific version by hand. This is monomorphization: "making mono-morphic" — turning polymorphic code into single-type code.

## Seeing It in Action

You can actually observe monomorphization by looking at the generated symbols:

```rust
fn identity<T>(x: T) -> T {
    x
}

fn main() {
    let _a = identity(42_i32);
    let _b = identity("hello");
    let _c = identity(3.14_f64);
    let _d = identity(vec![1, 2, 3]);
}
```

If you compile this and look at the symbols (using `nm` or `objdump`), you'll find four separate instantiations of `identity` — one per type. Each has been fully compiled with the concrete type baked in.

## The Performance Guarantee

Monomorphization means:

1. **No runtime type dispatch** — the function to call is known at compile time
2. **Full inlining opportunity** — the compiler can inline the specialized function
3. **Type-specific optimizations** — SIMD for numeric types, specialized instructions, etc.

Here's a benchmark-style comparison:

```rust
use std::time::Instant;

trait Summable {
    fn zero() -> Self;
    fn add(self, other: Self) -> Self;
}

impl Summable for i64 {
    fn zero() -> Self { 0 }
    fn add(self, other: Self) -> Self { self + other }
}

impl Summable for f64 {
    fn zero() -> Self { 0.0 }
    fn add(self, other: Self) -> Self { self + other }
}

// Generic version — monomorphized at compile time
fn sum_generic<T: Summable + Copy>(slice: &[T]) -> T {
    let mut total = T::zero();
    for &item in slice {
        total = total.add(item);
    }
    total
}

// Hand-written i64 version
fn sum_i64(slice: &[i64]) -> i64 {
    let mut total = 0i64;
    for &item in slice {
        total += item;
    }
    total
}

fn main() {
    let data: Vec<i64> = (0..1_000_000).collect();

    let start = Instant::now();
    let result1 = sum_generic(&data);
    let generic_time = start.elapsed();

    let start = Instant::now();
    let result2 = sum_i64(&data);
    let manual_time = start.elapsed();

    println!("Generic: {} in {:?}", result1, generic_time);
    println!("Manual:  {} in {:?}", result2, manual_time);
    println!("Same result: {}", result1 == result2);
    // These will be nearly identical in release builds
}
```

In release mode, `sum_generic::<i64>` and `sum_i64` compile to virtually identical assembly. The generic version is not an abstraction you pay for — it's an abstraction the compiler pays for at compile time.

## The Cost: Binary Size

Here's the tradeoff nobody mentions in the "zero-cost abstractions" pitch: monomorphization can bloat your binary. Every unique `T` spawns a new copy of the function:

```rust
fn process<T: std::fmt::Debug>(items: &[T]) {
    for item in items {
        println!("{:?}", item);
    }
}

fn main() {
    process(&[1_i32, 2, 3]);
    process(&[1_u8, 2, 3]);
    process(&[1_i64, 2, 3]);
    process(&[1_u64, 2, 3]);
    process(&["a", "b", "c"]);
    process(&[1.0_f32, 2.0, 3.0]);
    process(&[1.0_f64, 2.0, 3.0]);
    process(&[true, false]);
    // That's 8 copies of `process` in your binary
}
```

For a small function, 8 copies is nothing. But for a large generic function used with dozens of type combinations across a big codebase? The binary can grow significantly. I've seen Rust binaries that were 30% larger than equivalent C++ due to aggressive monomorphization.

## The Dyn Alternative: Trading Speed for Size

When binary size matters more than per-call performance, `dyn Trait` gives you a single function body at the cost of vtable dispatch:

```rust
use std::fmt::Debug;

// Monomorphized — one copy per T
fn process_static<T: Debug>(items: &[T]) {
    for item in items {
        println!("{:?}", item);
    }
}

// Single function body — dynamic dispatch
fn process_dynamic(items: &[&dyn Debug]) {
    for item in items {
        println!("{:?}", item);
    }
}

fn main() {
    // Static — generates separate copies
    process_static(&[1, 2, 3]);
    process_static(&["a", "b"]);

    // Dynamic — one function, vtable dispatch
    let items: Vec<&dyn Debug> = vec![&1, &"hello", &3.14, &true];
    process_dynamic(&items);
}
```

In hot paths, stick with generics. In cold paths (logging, error handling, configuration), consider `dyn` to keep the binary lean.

## Monomorphization and Compile Times

The other cost: compile time. More instantiations means more code to compile. This is why heavily generic crates (like `serde`, `diesel`, `tokio`) can be slow to compile — the compiler is generating specialized code for every type combination used across your entire dependency tree.

Some strategies to mitigate this:

### 1. Thin Generic Wrappers

Push non-generic logic into a non-generic inner function:

```rust
use std::fmt::Display;

// The public API is generic
pub fn log_value<T: Display>(label: &str, value: &T) {
    let formatted = format!("{}", value);
    log_inner(label, &formatted); // delegate to non-generic
}

// The heavy lifting is non-generic — compiled once
fn log_inner(label: &str, formatted: &str) {
    let timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    println!("[{}] {}: {}", timestamp, label, formatted);
    // imagine more complex logic here: file I/O, buffering, etc.
}

fn main() {
    log_value("count", &42);
    log_value("name", &"Atharva");
    log_value("ratio", &3.14);
}
```

Only the thin `format!` call gets monomorphized per type. The real work in `log_inner` is compiled once.

### 2. Box the Generic Part

```rust
use std::fmt::Debug;

struct EventQueue {
    events: Vec<Box<dyn Debug>>,
}

impl EventQueue {
    fn new() -> Self {
        EventQueue { events: Vec::new() }
    }

    // Generic entry point — thin
    fn push<T: Debug + 'static>(&mut self, event: T) {
        self.events.push(Box::new(event));
    }

    // Non-generic — compiled once
    fn drain_and_print(&mut self) {
        for event in self.events.drain(..) {
            println!("Event: {:?}", event);
        }
    }
}

fn main() {
    let mut queue = EventQueue::new();
    queue.push(42);
    queue.push("login event");
    queue.push(vec![1, 2, 3]);
    queue.drain_and_print();
}
```

## Monomorphization Across Crate Boundaries

Generic functions can be monomorphized in the *calling* crate, not the defining crate. This is why generic functions in library crates must have their full source available — typically through inline or generic function bodies in headers.

```rust
// In a library crate:
pub fn transform<T: Clone>(item: &T) -> (T, T) {
    (item.clone(), item.clone())
}

// When your crate calls transform::<String>(...),
// the compiler generates the specialized version
// in YOUR crate's compilation unit
```

This is why Rust libraries compile fast on their own but can increase your crate's compile time significantly — the monomorphization happens in your build.

## A Real-World Decision: Generic vs Monomorphization-Aware Design

Here's how I structure a real generic API to balance performance and compile time:

```rust
use std::collections::HashMap;
use std::fmt::Display;
use std::hash::Hash;

// The public interface is generic
pub struct Cache<K, V> {
    inner: CacheInner,
    _phantom: std::marker::PhantomData<(K, V)>,
    data: HashMap<K, V>,
}

// Non-generic inner state — shared across all instantiations
struct CacheInner {
    hit_count: u64,
    miss_count: u64,
    max_size: usize,
}

impl CacheInner {
    fn new(max_size: usize) -> Self {
        CacheInner { hit_count: 0, miss_count: 0, max_size }
    }

    fn record_hit(&mut self) { self.hit_count += 1; }
    fn record_miss(&mut self) { self.miss_count += 1; }

    fn stats(&self) -> String {
        let total = self.hit_count + self.miss_count;
        let ratio = if total > 0 {
            self.hit_count as f64 / total as f64
        } else {
            0.0
        };
        format!("hits: {}, misses: {}, ratio: {:.1}%",
                self.hit_count, self.miss_count, ratio * 100.0)
    }
}

impl<K: Eq + Hash, V> Cache<K, V> {
    pub fn new(max_size: usize) -> Self {
        Cache {
            inner: CacheInner::new(max_size),
            _phantom: std::marker::PhantomData,
            data: HashMap::new(),
        }
    }

    pub fn get(&mut self, key: &K) -> Option<&V> {
        if let Some(v) = self.data.get(key) {
            self.inner.record_hit();
            Some(v)
        } else {
            self.inner.record_miss();
            None
        }
    }

    pub fn insert(&mut self, key: K, value: V) {
        if self.data.len() >= self.inner.max_size {
            // In a real cache, you'd evict here
            return;
        }
        self.data.insert(key, value);
    }

    pub fn stats(&self) -> String {
        self.inner.stats() // delegates to non-generic code
    }
}

fn main() {
    let mut cache: Cache<String, i32> = Cache::new(100);
    cache.insert(String::from("a"), 1);
    cache.insert(String::from("b"), 2);

    cache.get(&String::from("a"));
    cache.get(&String::from("c")); // miss

    println!("{}", cache.stats());
}
```

The stats tracking, eviction logic, and reporting are all in `CacheInner` — compiled once regardless of how many `Cache<K, V>` instantiations exist. The generic parts handle only the type-specific HashMap operations.

## Key Takeaways

Monomorphization is Rust's strategy for zero-cost generics: the compiler generates specialized code for each type, enabling full optimization and inlining. The costs are binary size and compile time. Mitigate them by keeping generic functions thin and pushing logic into non-generic helpers.

The mental model: write generic code like a template. The compiler stamps out a concrete version for each type. If you'd be uncomfortable copy-pasting the function 50 times by hand, maybe the inner logic should be non-generic.

Next — const generics, where types are parameterized by values instead of types.
