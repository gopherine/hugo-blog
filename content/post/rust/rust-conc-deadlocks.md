---
title: "Lesson 18: Debugging Deadlocks and Data Races — Tools and techniques"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 18
keywords: [Rust, rust, concurrency, deadlock, data race, debugging, ThreadSanitizer, tracing]
tags: [Rust tutorial, rust, concurrency]
date: '2024-12-09T11:30:00.000Z'
---

The worst deadlock I ever encountered wasn't between two mutexes. It was between a mutex and a channel. Thread A held a lock and tried to send on a full bounded channel. Thread B was the consumer for that channel but was waiting to acquire the same lock before it could receive. Clean deadlock. Took me four hours to find because I was looking at mutex ordering and the real problem was a channel.

Rust prevents data races at compile time. But deadlocks, logic races, and performance issues? You need tools.

## What Rust Prevents vs What It Doesn't

**Compiler prevents:**
- Data races (two threads accessing same memory, at least one writing, no synchronization)
- Use-after-free across threads
- Sending non-`Send` types to threads

**Compiler does NOT prevent:**
- Deadlocks (circular lock dependencies)
- Livelocks (threads spinning, making no useful progress)
- Logic races (check-then-act without atomicity)
- Starvation (one thread never gets the lock)
- Channel deadlocks (full sender blocked, receiver waiting for lock held by sender)

## Creating a Deadlock

For demonstration purposes:

```rust
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

fn main() {
    let a = Arc::new(Mutex::new(1));
    let b = Arc::new(Mutex::new(2));

    let a1 = Arc::clone(&a);
    let b1 = Arc::clone(&b);
    let t1 = thread::spawn(move || {
        let _ga = a1.lock().unwrap();
        println!("Thread 1: got lock A");
        thread::sleep(Duration::from_millis(100));
        println!("Thread 1: waiting for lock B...");
        let _gb = b1.lock().unwrap(); // DEADLOCK — Thread 2 holds B
        println!("Thread 1: got lock B");
    });

    let a2 = Arc::clone(&a);
    let b2 = Arc::clone(&b);
    let t2 = thread::spawn(move || {
        let _gb = b2.lock().unwrap();
        println!("Thread 2: got lock B");
        thread::sleep(Duration::from_millis(100));
        println!("Thread 2: waiting for lock A...");
        let _ga = a2.lock().unwrap(); // DEADLOCK — Thread 1 holds A
        println!("Thread 2: got lock A");
    });

    t1.join().unwrap();
    t2.join().unwrap();
    println!("This line never prints");
}
```

This program hangs forever. No error, no panic, no output after the "waiting for" messages. Just silence.

## Technique 1: Lock Ordering

The simplest deadlock prevention — always acquire locks in the same order:

```rust
use std::sync::{Arc, Mutex};
use std::thread;

fn main() {
    let a = Arc::new(Mutex::new(1));
    let b = Arc::new(Mutex::new(2));

    let a1 = Arc::clone(&a);
    let b1 = Arc::clone(&b);
    let t1 = thread::spawn(move || {
        let _ga = a1.lock().unwrap(); // always A first
        let _gb = b1.lock().unwrap(); // then B
        println!("Thread 1: got both locks");
    });

    let a2 = Arc::clone(&a);
    let b2 = Arc::clone(&b);
    let t2 = thread::spawn(move || {
        let _ga = a2.lock().unwrap(); // always A first — same order!
        let _gb = b2.lock().unwrap(); // then B
        println!("Thread 2: got both locks");
    });

    t1.join().unwrap();
    t2.join().unwrap();
    println!("No deadlock");
}
```

If every thread acquires locks in the same global order, circular dependencies are impossible. The challenge is enforcing this convention across a large codebase.

## Technique 2: try_lock with Backoff

```rust
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

fn try_lock_both(
    a: &Mutex<i32>,
    b: &Mutex<i32>,
) -> Option<(std::sync::MutexGuard<'_, i32>, std::sync::MutexGuard<'_, i32>)> {
    let ga = a.try_lock().ok()?;
    match b.try_lock() {
        Ok(gb) => Some((ga, gb)),
        Err(_) => {
            drop(ga); // release A if we can't get B
            None
        }
    }
}

fn main() {
    let a = Arc::new(Mutex::new(1));
    let b = Arc::new(Mutex::new(2));

    let a1 = Arc::clone(&a);
    let b1 = Arc::clone(&b);
    let t1 = thread::spawn(move || {
        loop {
            if let Some((mut ga, mut gb)) = try_lock_both(&a1, &b1) {
                *ga += 10;
                *gb += 20;
                println!("Thread 1: done");
                return;
            }
            thread::sleep(Duration::from_millis(1)); // backoff
        }
    });

    let a2 = Arc::clone(&a);
    let b2 = Arc::clone(&b);
    let t2 = thread::spawn(move || {
        loop {
            if let Some((mut ga, mut gb)) = try_lock_both(&b2, &a2) {
                *ga += 100;
                *gb += 200;
                println!("Thread 2: done");
                return;
            }
            thread::sleep(Duration::from_millis(1));
        }
    });

    t1.join().unwrap();
    t2.join().unwrap();
}
```

If you can't get both locks, release what you have and retry. This prevents deadlock but can cause livelock if both threads keep retrying at the same time. Randomized backoff helps.

## Technique 3: Reduce Lock Scope

Often, deadlocks happen because you hold locks for too long:

```rust
use std::sync::{Arc, Mutex};

// BAD: holding lock A while doing something that needs lock B
fn bad_transfer(from: &Mutex<i32>, to: &Mutex<i32>, amount: i32) {
    let mut from_balance = from.lock().unwrap();
    let mut to_balance = to.lock().unwrap(); // potential deadlock
    *from_balance -= amount;
    *to_balance += amount;
}

// BETTER: single lock protecting both accounts
struct Bank {
    accounts: Mutex<Vec<i32>>,
}

impl Bank {
    fn transfer(&self, from: usize, to: usize, amount: i32) {
        let mut accounts = self.accounts.lock().unwrap();
        accounts[from] -= amount;
        accounts[to] += amount;
    }
}
```

Fewer locks = fewer deadlock opportunities. Sometimes restructuring your data to use one coarser lock is better than fine-grained locking that's correct on paper but deadlock-prone in practice.

## Detecting Deadlocks: parking_lot

parking_lot has built-in deadlock detection:

```rust
// Enable deadlock detection (debug builds)
// Add to Cargo.toml:
// parking_lot = { version = "0.12", features = ["deadlock_detection"] }

use parking_lot::Mutex;
use std::sync::Arc;
use std::thread;
use std::time::Duration;

fn main() {
    // Start a background thread that checks for deadlocks periodically
    thread::spawn(move || {
        loop {
            thread::sleep(Duration::from_secs(5));
            let deadlocks = parking_lot::deadlock::check_deadlock();
            if deadlocks.is_empty() {
                continue;
            }

            println!("{} deadlocks detected!", deadlocks.len());
            for (i, threads) in deadlocks.iter().enumerate() {
                println!("Deadlock #{}:", i + 1);
                for t in threads {
                    println!(
                        "  Thread {} at:\n{:?}",
                        t.thread_id(),
                        t.backtrace()
                    );
                }
            }
        }
    });

    // ... rest of your program
    let a = Arc::new(Mutex::new(1));
    let b = Arc::new(Mutex::new(2));

    let a1 = Arc::clone(&a);
    let b1 = Arc::clone(&b);
    thread::spawn(move || {
        let _ga = a1.lock();
        thread::sleep(Duration::from_millis(100));
        let _gb = b1.lock(); // deadlock
    });

    let a2 = Arc::clone(&a);
    let b2 = Arc::clone(&b);
    thread::spawn(move || {
        let _gb = b2.lock();
        thread::sleep(Duration::from_millis(100));
        let _ga = a2.lock(); // deadlock
    });

    thread::sleep(Duration::from_secs(10));
}
```

This is incredibly useful in testing and staging environments. The background thread periodically checks the lock dependency graph for cycles.

## ThreadSanitizer (TSan)

For finding data races in `unsafe` code, TSan is your best friend. Rust prevents safe data races, but `unsafe` blocks can still have them:

```bash
# Compile with ThreadSanitizer
RUSTFLAGS="-Z sanitizer=thread" cargo +nightly run

# Or for tests
RUSTFLAGS="-Z sanitizer=thread" cargo +nightly test
```

TSan instruments every memory access and reports races:

```
WARNING: ThreadSanitizer: data race (pid=12345)
  Write of size 8 at 0x7f... by thread T2:
    #0 my_crate::unsafe_function::... src/lib.rs:42
  Previous read of size 8 at 0x7f... by thread T1:
    #0 my_crate::other_function::... src/lib.rs:38
```

## Tracing and Logging

For production debugging, structured logging with thread info is invaluable:

```rust
use std::thread;
use std::sync::{Arc, Mutex};
use std::time::Instant;

fn traced_lock<T>(
    name: &str,
    mutex: &Mutex<T>,
) -> std::sync::MutexGuard<'_, T> {
    let thread_name = thread::current()
        .name()
        .unwrap_or("unnamed")
        .to_string();
    let start = Instant::now();
    let guard = mutex.lock().unwrap();
    let wait_time = start.elapsed();

    if wait_time.as_millis() > 10 {
        eprintln!(
            "[SLOW LOCK] thread={} lock={} waited={:?}",
            thread_name, name, wait_time
        );
    }

    guard
}

fn main() {
    let data = Arc::new(Mutex::new(0));

    thread::scope(|s| {
        for i in 0..4 {
            let data = &data;
            s.spawn(move || {
                let current = thread::current();
                // Name the thread for better logging
                for _ in 0..100 {
                    let mut guard = traced_lock("counter", data);
                    *guard += 1;
                }
            });
        }
    });

    println!("Final: {}", *data.lock().unwrap());
}
```

## Logic Races

Even without data races, you can have logic bugs:

```rust
use std::sync::{Arc, Mutex};
use std::collections::HashMap;
use std::thread;

fn main() {
    let cache = Arc::new(Mutex::new(HashMap::<String, String>::new()));

    // LOGIC RACE: check-then-act is not atomic
    let cache1 = Arc::clone(&cache);
    let t1 = thread::spawn(move || {
        let key = "important".to_string();

        // These two operations are NOT atomic together
        let exists = cache1.lock().unwrap().contains_key(&key);
        if !exists {
            // Another thread might insert between check and insert
            let value = expensive_compute(&key);
            cache1.lock().unwrap().insert(key, value);
        }
    });

    let cache2 = Arc::clone(&cache);
    let t2 = thread::spawn(move || {
        let key = "important".to_string();
        let exists = cache2.lock().unwrap().contains_key(&key);
        if !exists {
            let value = expensive_compute(&key);
            cache2.lock().unwrap().insert(key, value);
            // Both threads might compute — wasted work
        }
    });

    t1.join().unwrap();
    t2.join().unwrap();
}

fn expensive_compute(key: &str) -> String {
    thread::sleep(std::time::Duration::from_millis(100));
    format!("computed-{}", key)
}
```

Fix: hold the lock across the entire check-then-act:

```rust
use std::sync::{Arc, Mutex};
use std::collections::HashMap;

fn get_or_compute(
    cache: &Mutex<HashMap<String, String>>,
    key: &str,
) -> String {
    let mut map = cache.lock().unwrap();
    map.entry(key.to_string())
        .or_insert_with(|| {
            // Computed while holding the lock — no race
            // But: holds the lock during computation — potential bottleneck
            format!("computed-{}", key)
        })
        .clone()
}
```

There's a trade-off: holding the lock during computation prevents the logic race but serializes all computations. For expensive operations, you might accept the duplicate work or use a more sophisticated double-checked locking pattern.

## Debugging Checklist

When your concurrent program misbehaves:

1. **Hangs (potential deadlock)**:
   - Enable parking_lot deadlock detection
   - Add logging around lock acquisitions
   - Check for channel + lock interactions
   - Review lock ordering

2. **Wrong results (potential logic race)**:
   - Look for check-then-act patterns
   - Verify all related state updates are under the same lock
   - Add assertions inside critical sections

3. **Crashes in unsafe code (potential data race)**:
   - Run with ThreadSanitizer
   - Review all `unsafe impl Send/Sync`
   - Check that raw pointers are properly synchronized

4. **Poor performance (contention)**:
   - Profile lock wait times
   - Reduce critical section duration
   - Consider sharding or lock-free alternatives

---

Next — thread-local storage for per-thread state that avoids synchronization entirely.
