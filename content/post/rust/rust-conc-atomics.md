---
title: "Lesson 7: Atomics — Lock-free primitives"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 7
keywords: [Rust, rust, concurrency, atomics, AtomicBool, AtomicUsize, lock-free, CAS]
tags: [Rust tutorial, rust, concurrency]
date: '2024-11-17T07:45:00.000Z'
---

I once replaced a `Mutex<u64>` counter in a hot path with an `AtomicU64` and saw throughput jump 40%. Not because mutexes are slow — they're fast. But for a single integer being incremented by 32 threads, the overhead of acquiring and releasing a lock millions of times per second adds up to real time.

Atomics are the foundation of lock-free programming. They let you do thread-safe operations on primitive values without any lock at all.

## What Are Atomics?

An atomic operation is indivisible — it completes entirely or not at all. No thread can observe it half-done. The CPU hardware guarantees this.

Rust provides atomic types in `std::sync::atomic`:

- `AtomicBool`
- `AtomicI8`, `AtomicI16`, `AtomicI32`, `AtomicI64`, `AtomicIsize`
- `AtomicU8`, `AtomicU16`, `AtomicU32`, `AtomicU64`, `AtomicUsize`
- `AtomicPtr<T>`

Each one wraps a primitive value and provides atomic operations on it.

## Basic Usage: Counters and Flags

The most common use cases are dead simple:

```rust
use std::sync::atomic::{AtomicU64, Ordering};
use std::thread;

fn main() {
    let counter = AtomicU64::new(0);

    thread::scope(|s| {
        for _ in 0..10 {
            s.spawn(|| {
                for _ in 0..100_000 {
                    counter.fetch_add(1, Ordering::Relaxed);
                }
            });
        }
    });

    println!("Counter: {}", counter.load(Ordering::Relaxed));
    // Always 1_000_000
}
```

No `Mutex`, no `Arc` (scoped threads borrow directly), no lock/unlock ceremony. `fetch_add` atomically increments the value and returns the previous value. The CPU does this in a single instruction.

### Shutdown Flag

```rust
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

fn main() {
    let running = Arc::new(AtomicBool::new(true));
    let mut handles = vec![];

    for id in 0..4 {
        let running = Arc::clone(&running);
        handles.push(thread::spawn(move || {
            let mut iterations = 0u64;
            while running.load(Ordering::Relaxed) {
                // do work
                iterations += 1;
                if iterations % 1_000_000 == 0 {
                    thread::yield_now();
                }
            }
            println!("Worker {} did {} iterations", id, iterations);
        }));
    }

    thread::sleep(Duration::from_secs(2));
    running.store(false, Ordering::Relaxed);
    println!("Shutdown signal sent");

    for h in handles {
        h.join().unwrap();
    }
}
```

One thread sets the flag, all others observe it. No locking needed. This is the standard graceful shutdown pattern.

## Atomic Operations

Every atomic type supports these core operations:

```rust
use std::sync::atomic::{AtomicI32, Ordering};

fn main() {
    let val = AtomicI32::new(10);

    // Load: read the current value
    let current = val.load(Ordering::SeqCst);
    println!("current: {}", current); // 10

    // Store: write a new value
    val.store(20, Ordering::SeqCst);

    // Swap: store new value, return old value
    let old = val.swap(30, Ordering::SeqCst);
    println!("was: {}, now: {}", old, val.load(Ordering::SeqCst)); // 20, 30

    // Compare-and-swap (CAS): atomic conditional update
    // "If the value is 30, change it to 40"
    match val.compare_exchange(30, 40, Ordering::SeqCst, Ordering::SeqCst) {
        Ok(prev) => println!("CAS succeeded, was {}", prev), // 30
        Err(actual) => println!("CAS failed, value was {}", actual),
    }

    // Fetch-and-modify operations
    val.store(100, Ordering::SeqCst);
    let prev = val.fetch_add(5, Ordering::SeqCst);
    println!("was {}, now {}", prev, val.load(Ordering::SeqCst)); // 100, 105

    let prev = val.fetch_sub(10, Ordering::SeqCst);
    println!("was {}, now {}", prev, val.load(Ordering::SeqCst)); // 105, 95

    let prev = val.fetch_max(200, Ordering::SeqCst);
    println!("was {}, now {}", prev, val.load(Ordering::SeqCst)); // 95, 200

    let prev = val.fetch_min(50, Ordering::SeqCst);
    println!("was {}, now {}", prev, val.load(Ordering::SeqCst)); // 200, 50
}
```

The star of the show is `compare_exchange` (CAS — compare and swap). It's the building block for all lock-free algorithms: "If the value is what I expect, update it. Otherwise, tell me what it actually is."

## Compare-and-Swap Loops

CAS is how you build complex atomic operations from simple ones. The pattern: load the current value, compute the new value, try to CAS. If another thread changed it first, retry.

```rust
use std::sync::atomic::{AtomicU64, Ordering};
use std::thread;

/// Atomically update the max value seen
fn update_max(current_max: &AtomicU64, new_value: u64) {
    let mut current = current_max.load(Ordering::Relaxed);
    loop {
        if new_value <= current {
            return; // new value isn't larger, nothing to do
        }
        match current_max.compare_exchange_weak(
            current,
            new_value,
            Ordering::Relaxed,
            Ordering::Relaxed,
        ) {
            Ok(_) => return, // successfully updated
            Err(actual) => current = actual, // someone else changed it, retry
        }
    }
}

fn main() {
    let max = AtomicU64::new(0);

    thread::scope(|s| {
        for _ in 0..8 {
            s.spawn(|| {
                for val in 0..1000 {
                    update_max(&max, val);
                }
            });
        }
    });

    println!("Max: {}", max.load(Ordering::Relaxed)); // 999
}
```

`compare_exchange_weak` can spuriously fail on some architectures (ARM, for instance), which is fine in a loop — you just retry. It's slightly faster than `compare_exchange` on those platforms because it avoids an extra synchronization step.

## Practical Examples

### Statistics Collector

```rust
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

struct Stats {
    requests: AtomicU64,
    errors: AtomicU64,
    bytes_processed: AtomicU64,
}

impl Stats {
    fn new() -> Self {
        Stats {
            requests: AtomicU64::new(0),
            errors: AtomicU64::new(0),
            bytes_processed: AtomicU64::new(0),
        }
    }

    fn record_request(&self, bytes: u64, is_error: bool) {
        self.requests.fetch_add(1, Ordering::Relaxed);
        self.bytes_processed.fetch_add(bytes, Ordering::Relaxed);
        if is_error {
            self.errors.fetch_add(1, Ordering::Relaxed);
        }
    }

    fn snapshot(&self) -> (u64, u64, u64) {
        (
            self.requests.load(Ordering::Relaxed),
            self.errors.load(Ordering::Relaxed),
            self.bytes_processed.load(Ordering::Relaxed),
        )
    }
}

fn main() {
    let stats = Arc::new(Stats::new());
    let mut handles = vec![];

    // Worker threads
    for _ in 0..8 {
        let stats = Arc::clone(&stats);
        handles.push(thread::spawn(move || {
            for i in 0..10_000 {
                stats.record_request(256, i % 100 == 0);
            }
        }));
    }

    // Reporter thread
    {
        let stats = Arc::clone(&stats);
        handles.push(thread::spawn(move || {
            for _ in 0..5 {
                thread::sleep(Duration::from_millis(100));
                let (reqs, errs, bytes) = stats.snapshot();
                println!("Requests: {}, Errors: {}, Bytes: {}", reqs, errs, bytes);
            }
        }));
    }

    for h in handles {
        h.join().unwrap();
    }

    let (reqs, errs, bytes) = stats.snapshot();
    println!("\nFinal — Requests: {}, Errors: {}, Bytes: {}", reqs, errs, bytes);
}
```

No locks anywhere. Each counter is updated independently and atomically. The snapshot might show slightly stale values for some fields (since they're read separately), but that's fine for metrics.

### Spin Lock (Educational — Don't Use In Production)

```rust
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;

struct SpinLock {
    locked: AtomicBool,
}

impl SpinLock {
    fn new() -> Self {
        SpinLock {
            locked: AtomicBool::new(false),
        }
    }

    fn lock(&self) {
        while self
            .locked
            .compare_exchange_weak(false, true, Ordering::Acquire, Ordering::Relaxed)
            .is_err()
        {
            // Spin — hint to the CPU that we're in a spin loop
            std::hint::spin_loop();
        }
    }

    fn unlock(&self) {
        self.locked.store(false, Ordering::Release);
    }
}

fn main() {
    let lock = SpinLock::new();
    let counter = std::cell::UnsafeCell::new(0u64);

    // Safety: we're using the spin lock to ensure exclusive access
    // In real code, use Mutex instead
    unsafe {
        thread::scope(|s| {
            for _ in 0..4 {
                s.spawn(|| {
                    for _ in 0..100_000 {
                        lock.lock();
                        *counter.get() += 1;
                        lock.unlock();
                    }
                });
            }
        });

        println!("Counter: {}", *counter.get());
    }
}
```

This is purely educational. Real spin locks need careful tuning, and `Mutex` almost always outperforms a naive spin lock because it puts the thread to sleep instead of burning CPU cycles.

## Atomics vs Mutex: When to Use Which

**Use atomics when:**
- You're modifying a single primitive value (counter, flag, pointer)
- The operation is one of the built-in atomic ops (add, sub, CAS, etc.)
- You need maximum throughput on a hot path

**Use Mutex when:**
- You're protecting a complex data structure
- The critical section involves multiple values that must be consistent
- The operation isn't expressible as a single atomic instruction

A mutex internally uses atomics and OS primitives. For a single integer, going straight to atomics skips that overhead. For anything more complex, trying to build lock-free structures from raw atomics is a minefield — and almost never worth it.

## The Ordering Parameter

You might have noticed every atomic operation takes an `Ordering` parameter. We've been using `Relaxed` and `SeqCst` without explanation. That's the next lesson — memory ordering is a deep topic that deserves its own treatment.

Quick preview: `Ordering::Relaxed` means "just make the operation atomic, I don't care about ordering relative to other operations." `Ordering::SeqCst` means "full sequential consistency — everyone sees operations in the same order." For counters and flags, `Relaxed` is usually fine. For anything that coordinates between threads, you need stronger ordering.

---

Next — memory ordering. The part of concurrency that makes even experienced engineers reach for a textbook.
