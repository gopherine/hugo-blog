---
title: "Lesson 14: parking_lot — Faster mutexes"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 14
keywords: [Rust, rust, concurrency, parking_lot, mutex, RwLock, performance, locking]
tags: [Rust tutorial, rust, concurrency]
date: '2024-12-01T09:25:00.000Z'
---

I switched a high-contention service from `std::sync::Mutex` to `parking_lot::Mutex` and saw lock acquisition time drop by 30% under load. The API is nearly identical — it was a find-and-replace job. That's the kind of optimization I like: massive payoff, zero complexity cost.

`parking_lot` is one of those crates that probably should have been in the standard library. It provides the same synchronization primitives as std, but faster, smaller, and with more features.

## Why parking_lot?

The standard library's `Mutex` and `RwLock` are wrappers around OS primitives (`pthread_mutex` on Unix, `SRWLOCK` on Windows). They're correct and battle-tested, but they carry overhead:

1. **Size**: `std::sync::Mutex<()>` is 40 bytes on Linux (it wraps a `pthread_mutex_t`). `parking_lot::Mutex<()>` is 1 byte.
2. **Poisoning**: std mutexes track poisoning state. parking_lot doesn't (which is often what you want).
3. **Syscall overhead**: std always goes through OS primitives. parking_lot uses adaptive spinning before falling back to OS-level parking.

```toml
[dependencies]
parking_lot = "0.12"
```

## Drop-In Replacement

The API is almost identical to std:

```rust
use parking_lot::Mutex;

fn main() {
    let data = Mutex::new(vec![1, 2, 3]);

    {
        let mut guard = data.lock(); // no .unwrap() needed — no poisoning!
        guard.push(4);
    }

    println!("{:?}", *data.lock());
}
```

Notice: `lock()` returns `MutexGuard<T>` directly, not `Result<MutexGuard<T>>`. No poisoning means no `.unwrap()`. Cleaner code, fewer error paths.

## No Poisoning — Is That Safe?

std's poisoning philosophy: if a thread panics while holding a lock, the data inside might be in an inconsistent state. Other threads should be warned.

parking_lot's philosophy: panics should be caught and handled at a higher level, not at every lock acquisition. If your data is in a bad state after a panic, the right response is usually to restart the component, not try to recover from poisoned locks.

In my experience, parking_lot is right. I've never seen a codebase that actually handled mutex poisoning usefully. The pattern was always `.lock().unwrap()` or `.lock().expect("mutex poisoned")` — effectively panicking on poison, which means you're just cascading the original panic.

## RwLock

parking_lot's `RwLock` is also a significant improvement:

```rust
use parking_lot::RwLock;
use std::thread;

fn main() {
    let config = RwLock::new(vec![
        ("host", "localhost"),
        ("port", "5432"),
    ]);

    thread::scope(|s| {
        for i in 0..10 {
            s.spawn(|| {
                let cfg = config.read(); // no unwrap
                println!("Reader {}: {:?}", i, *cfg);
            });
        }

        s.spawn(|| {
            let mut cfg = config.write(); // no unwrap
            cfg.push(("timeout", "30"));
        });
    });
}
```

parking_lot's `RwLock` has a key feature std doesn't: it prevents writer starvation. In std's `RwLock`, a continuous stream of readers can starve writers indefinitely. parking_lot's implementation is fair — writers get priority once they start waiting.

## Additional Features

### try_lock with timeout

```rust
use parking_lot::Mutex;
use std::time::Duration;

fn main() {
    let data = Mutex::new(42);

    // Immediate try
    if let Some(guard) = data.try_lock() {
        println!("Got it: {}", *guard);
    }

    // Try with timeout
    if let Some(guard) = data.try_lock_for(Duration::from_millis(100)) {
        println!("Got it within 100ms: {}", *guard);
    }

    // Try until deadline
    use std::time::Instant;
    let deadline = Instant::now() + Duration::from_secs(1);
    if let Some(guard) = data.try_lock_until(deadline) {
        println!("Got it before deadline: {}", *guard);
    }
}
```

std's `Mutex` only has `try_lock()` — no timeout variant. This is useful for implementing lock hierarchies or avoiding deadlocks in production.

### ReentrantMutex

A mutex that can be locked multiple times by the same thread:

```rust
use parking_lot::ReentrantMutex;
use std::cell::RefCell;

fn main() {
    let data = ReentrantMutex::new(RefCell::new(0));

    let guard1 = data.lock();
    let guard2 = data.lock(); // same thread, doesn't deadlock

    *guard1.borrow_mut() = 42;
    println!("{}", *guard2.borrow());

    drop(guard2);
    drop(guard1);
}
```

Use this sparingly. Reentrant mutexes usually indicate a design problem — if you need to lock the same mutex twice, you probably have a function that doesn't know whether it's being called with the lock held or not. Refactoring to make lock boundaries clear is almost always better.

### FairMutex

A mutex that guarantees FIFO ordering — threads acquire the lock in the order they requested it:

```rust
use parking_lot::FairMutex;
use std::thread;
use std::time::Duration;

fn main() {
    let counter = FairMutex::new(0u64);

    thread::scope(|s| {
        for id in 0..4 {
            s.spawn(|| {
                for _ in 0..100 {
                    let mut val = counter.lock();
                    *val += 1;
                    // Hold the lock briefly
                    thread::sleep(Duration::from_micros(10));
                }
                println!("Thread {} done", id);
            });
        }
    });

    println!("Counter: {}", *counter.lock());
}
```

Fair mutexes have slightly higher overhead than regular mutexes, but they prevent starvation. Use them when you need predictable latency rather than maximum throughput.

### RwLock Upgrades and Downgrades

```rust
use parking_lot::RwLock;

fn main() {
    let data = RwLock::new(vec![1, 2, 3]);

    // Start with a read lock
    let read_guard = data.read();
    println!("Reading: {:?}", *read_guard);

    // Can't upgrade directly — need to drop and reacquire
    // But parking_lot has upgradable reads:
    drop(read_guard);

    let upgradable = data.upgradable_read();
    println!("Upgradable read: {:?}", *upgradable);

    // Now upgrade to write without releasing
    let mut write_guard = parking_lot::RwLockUpgradableReadGuard::upgrade(upgradable);
    write_guard.push(4);
    println!("After write: {:?}", *write_guard);

    // Downgrade back to read
    let read_guard = parking_lot::RwLockWriteGuard::downgrade(write_guard);
    println!("Downgraded: {:?}", *read_guard);
}
```

Upgradable reads are powerful for the "check then modify" pattern. You start with a read lock, check if modification is needed, then upgrade to a write lock without releasing. No TOCTOU race.

## Benchmarking: std vs parking_lot

Here's a rough comparison under contention:

```rust
use std::time::Instant;
use std::thread;

fn bench_std_mutex(threads: usize, iterations: usize) -> std::time::Duration {
    let mutex = std::sync::Arc::new(std::sync::Mutex::new(0u64));
    let start = Instant::now();

    thread::scope(|s| {
        for _ in 0..threads {
            let mutex = mutex.clone();
            s.spawn(move || {
                for _ in 0..iterations {
                    *mutex.lock().unwrap() += 1;
                }
            });
        }
    });

    start.elapsed()
}

fn bench_parking_lot_mutex(threads: usize, iterations: usize) -> std::time::Duration {
    let mutex = std::sync::Arc::new(parking_lot::Mutex::new(0u64));
    let start = Instant::now();

    thread::scope(|s| {
        for _ in 0..threads {
            let mutex = mutex.clone();
            s.spawn(move || {
                for _ in 0..iterations {
                    *mutex.lock() += 1;
                }
            });
        }
    });

    start.elapsed()
}

fn main() {
    let threads = 8;
    let iterations = 1_000_000;

    let std_time = bench_std_mutex(threads, iterations);
    let pl_time = bench_parking_lot_mutex(threads, iterations);

    println!("std::sync::Mutex:    {:?}", std_time);
    println!("parking_lot::Mutex:  {:?}", pl_time);
    println!("Speedup: {:.2}x", std_time.as_nanos() as f64 / pl_time.as_nanos() as f64);
}
```

Typical results on Linux: parking_lot is 20-40% faster under high contention. On macOS, the difference can be smaller because the OS mutex implementation is already quite good. On Windows, parking_lot tends to win bigger because it avoids the SRWLOCK overhead.

## Migration Guide

Switching from std to parking_lot is mostly mechanical:

```rust
// Before
use std::sync::{Mutex, RwLock};

let m = Mutex::new(data);
let guard = m.lock().unwrap();  // unwrap because of poisoning
let guard = m.try_lock().unwrap(); // Option → Result

// After
use parking_lot::{Mutex, RwLock};

let m = Mutex::new(data);
let guard = m.lock();           // no unwrap needed
let guard = m.try_lock();       // returns Option<MutexGuard>
```

The main API differences:

1. `lock()` doesn't return `Result` — no poisoning
2. `try_lock()` returns `Option` not `Result`
3. Guards are `!Send` (same as std)
4. Size is much smaller (1 byte for `Mutex<()>` vs 40 bytes)

## When to Stick with std

- **You need poisoning** — Some safety-critical systems genuinely benefit from mutex poisoning detection
- **Minimizing dependencies** — parking_lot is a dependency; std is not
- **Lock contention isn't your bottleneck** — If profiling shows your locks aren't contended, switching won't help
- **You're writing a library** — Some library authors prefer std to minimize transitive dependencies

For applications, I default to parking_lot. For libraries, I think about it more carefully — though many popular crates (including `tokio` and `rayon`) use parking_lot internally.

---

Next — condition variables, for when you need threads to wait for specific conditions.
