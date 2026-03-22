---
title: "Lesson 2: Strategy Pattern — Trait objects and generics"
author: Atharva Pandey
series: "Rust Design Patterns"
lesson: 2
keywords: [Rust, rust, design patterns, strategy pattern, trait objects, generics, dynamic dispatch, static dispatch]
tags: [Rust tutorial, rust, design-patterns, architecture]
date: '2025-10-03T14:15:00.000Z'
---

In my first real Go project, I wrote an interface for a payment processor. Two implementations — Stripe and PayPal. Simple polymorphism. When I tried the same thing in Rust, the compiler hit me with a wall of errors about `dyn`, `Box`, object safety, and sized types. It took me a full afternoon to understand what was happening. The Strategy pattern — which is trivial in most OOP languages — forced me to actually understand Rust's type system. And I came out the other side a better programmer for it.

## What's the Strategy Pattern?

Quick recap: Strategy lets you define a family of algorithms, encapsulate each one, and make them interchangeable. The client code picks a strategy at configuration time and runs it without knowing the implementation details. In Java, you'd create an interface, implement it a few times, and inject the implementation. Done.

Rust gives you *two* ways to do this, and which one you pick has real consequences for performance, binary size, and API design.

## Approach 1: Generics (Static Dispatch)

The Rust-idiomatic first choice. Define a trait, implement it, and use generics to accept any implementation:

```rust
pub trait Compressor {
    fn compress(&self, data: &[u8]) -> Vec<u8>;
    fn name(&self) -> &str;
}

pub struct GzipCompressor {
    level: u32,
}

impl GzipCompressor {
    pub fn new(level: u32) -> Self {
        Self { level }
    }
}

impl Compressor for GzipCompressor {
    fn compress(&self, data: &[u8]) -> Vec<u8> {
        // Real implementation would use flate2
        let mut result = format!("gzip-L{}:", self.level).into_bytes();
        result.extend_from_slice(data);
        result
    }

    fn name(&self) -> &str {
        "gzip"
    }
}

pub struct ZstdCompressor {
    dictionary: Option<Vec<u8>>,
}

impl Compressor for ZstdCompressor {
    fn compress(&self, data: &[u8]) -> Vec<u8> {
        let mut result = b"zstd:".to_vec();
        result.extend_from_slice(data);
        result
    }

    fn name(&self) -> &str {
        "zstd"
    }
}
```

Now the consumer uses a generic parameter:

```rust
pub struct Archiver<C: Compressor> {
    compressor: C,
    output_dir: std::path::PathBuf,
}

impl<C: Compressor> Archiver<C> {
    pub fn new(compressor: C, output_dir: impl Into<std::path::PathBuf>) -> Self {
        Self {
            compressor,
            output_dir: output_dir.into(),
        }
    }

    pub fn archive(&self, files: &[(&str, &[u8])]) -> Vec<(String, Vec<u8>)> {
        files
            .iter()
            .map(|(name, data)| {
                let compressed = self.compressor.compress(data);
                println!(
                    "Compressed {} with {} ({} -> {} bytes)",
                    name,
                    self.compressor.name(),
                    data.len(),
                    compressed.len()
                );
                (format!("{}.{}", name, self.compressor.name()), compressed)
            })
            .collect()
    }
}

fn main() {
    let archiver = Archiver::new(GzipCompressor::new(6), "/tmp/archive");
    let files = vec![("readme.txt", b"hello world" as &[u8])];
    archiver.archive(&files);
}
```

The compiler monomorphizes this — it generates specialized code for `Archiver<GzipCompressor>` and `Archiver<ZstdCompressor>` separately. No vtable lookup, no indirection. The function calls get inlined. This is as fast as hand-writing separate implementations.

But there's a cost: each specialization is duplicated code in the binary. And you can't easily switch strategies at runtime.

## Approach 2: Trait Objects (Dynamic Dispatch)

When you need to choose the strategy at runtime — based on user input, a config file, a feature flag — you need dynamic dispatch:

```rust
pub struct DynamicArchiver {
    compressor: Box<dyn Compressor>,
    output_dir: std::path::PathBuf,
}

impl DynamicArchiver {
    pub fn new(
        compressor: Box<dyn Compressor>,
        output_dir: impl Into<std::path::PathBuf>,
    ) -> Self {
        Self {
            compressor,
            output_dir: output_dir.into(),
        }
    }

    pub fn archive(&self, files: &[(&str, &[u8])]) -> Vec<(String, Vec<u8>)> {
        files
            .iter()
            .map(|(name, data)| {
                let compressed = self.compressor.compress(data);
                (format!("{}.{}", name, self.compressor.name()), compressed)
            })
            .collect()
    }
}

fn create_archiver(algorithm: &str) -> DynamicArchiver {
    let compressor: Box<dyn Compressor> = match algorithm {
        "gzip" => Box::new(GzipCompressor::new(6)),
        "zstd" => Box::new(ZstdCompressor { dictionary: None }),
        _ => panic!("unknown algorithm: {}", algorithm),
    };
    DynamicArchiver::new(compressor, "/tmp/archive")
}
```

`Box<dyn Compressor>` is a fat pointer — it stores a pointer to the data and a pointer to a vtable that maps method calls to the concrete implementation. There's a small runtime cost per call, but in practice it's negligible for anything except the tightest inner loops.

## The Object Safety Trap

Here's where newcomers get burned. Not every trait can be used as `dyn Trait`. Rust has rules — the trait must be "object safe." The most common violations:

```rust
// This trait is NOT object safe
trait BadStrategy {
    fn process<T: Display>(&self, item: T); // generic method
    fn clone_self(&self) -> Self;            // returns Self
}

// This trait IS object safe
trait GoodStrategy {
    fn process(&self, item: &dyn Display);   // no generics
    fn clone_boxed(&self) -> Box<dyn GoodStrategy>; // returns Box<dyn>
}
```

The rules boil down to: the compiler needs to know the size and layout of the vtable at compile time. Generic methods would need infinite vtable entries (one per possible `T`). Methods returning `Self` don't work because `Self` has an unknown size behind `dyn`.

If you need a clonable trait object — and you will, eventually — here's the standard workaround:

```rust
pub trait Strategy: StrategyClone {
    fn execute(&self, input: &str) -> String;
}

pub trait StrategyClone {
    fn clone_box(&self) -> Box<dyn Strategy>;
}

impl<T> StrategyClone for T
where
    T: 'static + Strategy + Clone,
{
    fn clone_box(&self) -> Box<dyn Strategy> {
        Box::new(self.clone())
    }
}

impl Clone for Box<dyn Strategy> {
    fn clone(&self) -> Self {
        self.clone_box()
    }
}
```

It's boilerplate, but it works. The `dyn-clone` crate automates this if you don't want to write it yourself.

## Approach 3: Closures as Strategies

Here's the thing — in Rust, you don't always need a trait to implement Strategy. If your strategy is a single function, a closure is simpler:

```rust
pub struct Processor {
    transform: Box<dyn Fn(&str) -> String>,
}

impl Processor {
    pub fn new(transform: impl Fn(&str) -> String + 'static) -> Self {
        Self {
            transform: Box::new(transform),
        }
    }

    pub fn process(&self, input: &str) -> String {
        (self.transform)(input)
    }
}

fn main() {
    let upper = Processor::new(|s| s.to_uppercase());
    let repeat = Processor::new(|s| format!("{s}{s}"));

    println!("{}", upper.process("hello"));   // HELLO
    println!("{}", repeat.process("hello"));  // hellohello
}
```

This is how most Rust codebases actually implement Strategy for simple cases. No trait definitions, no structs for each variant — just a function pointer or closure. It's less ceremony, and it composes beautifully with iterators and other functional patterns.

## When to Use Which

Here's my decision framework after years of writing Rust:

**Use generics** when:
- Performance is critical (inner loops, hot paths)
- The strategy is known at compile time
- You're writing library code and want zero-cost abstraction
- The strategy trait has multiple methods

**Use trait objects** when:
- The strategy is chosen at runtime (config, user input)
- You need to store heterogeneous strategies in a collection
- You want to reduce binary size (one implementation, not N monomorphized copies)
- You're building plugin-style architectures

**Use closures** when:
- The strategy is a single function
- You want minimal boilerplate
- The strategies are short-lived or defined inline

## A Real-World Example: Retry Strategies

Let me show you how I've used this in production. A retry mechanism with pluggable backoff strategies:

```rust
use std::time::Duration;

pub trait BackoffStrategy: Send + Sync {
    fn delay(&self, attempt: u32) -> Duration;
    fn should_retry(&self, attempt: u32, max_attempts: u32) -> bool {
        attempt < max_attempts
    }
}

pub struct FixedBackoff {
    delay: Duration,
}

impl BackoffStrategy for FixedBackoff {
    fn delay(&self, _attempt: u32) -> Duration {
        self.delay
    }
}

pub struct ExponentialBackoff {
    base: Duration,
    max_delay: Duration,
}

impl BackoffStrategy for ExponentialBackoff {
    fn delay(&self, attempt: u32) -> Duration {
        let delay = self.base * 2u32.saturating_pow(attempt);
        delay.min(self.max_delay)
    }
}

pub struct JitteredBackoff<B: BackoffStrategy> {
    inner: B,
    jitter_pct: f64,
}

impl<B: BackoffStrategy> BackoffStrategy for JitteredBackoff<B> {
    fn delay(&self, attempt: u32) -> Duration {
        let base = self.inner.delay(attempt);
        let jitter = (base.as_millis() as f64 * self.jitter_pct) as u64;
        // In real code, use rand crate here
        let offset = jitter / 2;
        base + Duration::from_millis(offset)
    }
}

pub struct RetryExecutor<B: BackoffStrategy> {
    strategy: B,
    max_attempts: u32,
}

impl<B: BackoffStrategy> RetryExecutor<B> {
    pub fn new(strategy: B, max_attempts: u32) -> Self {
        Self {
            strategy,
            max_attempts,
        }
    }

    pub fn execute<F, T, E>(&self, mut operation: F) -> Result<T, E>
    where
        F: FnMut() -> Result<T, E>,
    {
        let mut attempt = 0;
        loop {
            match operation() {
                Ok(val) => return Ok(val),
                Err(e) => {
                    if !self.strategy.should_retry(attempt, self.max_attempts) {
                        return Err(e);
                    }
                    let delay = self.strategy.delay(attempt);
                    std::thread::sleep(delay);
                    attempt += 1;
                }
            }
        }
    }
}
```

Notice `JitteredBackoff` — it wraps another strategy, adding jitter. This is Strategy *and* Decorator working together, and the generic approach means the compiler can inline the entire call chain. No allocation, no indirection, no runtime cost. Try doing that in Java without a JIT hoping to inline your interface calls.

## Key Takeaways

The Strategy pattern in Rust isn't one pattern — it's three, depending on your constraints. Generics give you zero-cost abstraction. Trait objects give you runtime flexibility. Closures give you simplicity. Know all three, pick the right one for your situation, and don't reflexively reach for `Box<dyn Trait>` just because that's what you'd do in Java.

The object safety rules feel restrictive at first, but they exist for a reason. Once you internalize them, they stop being obstacles and start being guardrails that push you toward better designs.
