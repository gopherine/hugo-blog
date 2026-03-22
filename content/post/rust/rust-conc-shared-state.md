---
title: "Lesson 5: Shared State — Mutex, RwLock, and poisoning"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 5
keywords: [Rust, rust, concurrency, Mutex, RwLock, poisoning, shared state, locking]
tags: [Rust tutorial, rust, concurrency]
date: '2024-11-13T16:55:00.000Z'
---

The nastiest production bug I ever tracked down involved a Java ConcurrentHashMap that was "thread-safe" in the API sense but not in the logic sense. Two threads would read a key, both see it's absent, both insert with computed values, and one would silently overwrite the other. The map itself was fine — the *access pattern* was broken.

Rust's Mutex won't save you from logic bugs either. But it will absolutely prevent you from forgetting the lock in the first place.

## Mutex<T> — Exclusive Access

A `Mutex` wraps data and forces you to acquire a lock before touching it:

```rust
use std::sync::Mutex;

fn main() {
    let m = Mutex::new(5);

    {
        let mut num = m.lock().unwrap();
        *num = 6;
        // lock is released when `num` (the MutexGuard) goes out of scope
    }

    println!("m = {:?}", m);
}
```

Notice what's different from C++ or Java mutexes: the data lives *inside* the `Mutex`. You can't access the data without going through `lock()`. There's no "oops, I forgot to lock before reading" because the API literally doesn't give you the data any other way.

`lock()` returns a `MutexGuard<T>` — a smart pointer that `Deref`s to `T` and releases the lock when dropped. RAII at its finest.

## The Problem: Forgetting to Unlock

In C++:

```cpp
// C++ — the classic bug
std::mutex mtx;
int shared_data = 0;

void bad_function() {
    mtx.lock();
    shared_data += 1;
    if (shared_data > 10) {
        return; // BUG: mutex never unlocked
    }
    mtx.unlock();
}
```

In Rust, this is structurally impossible. The lock is released when the guard is dropped — which happens automatically when it goes out of scope, even through early returns, panics, or unwinding:

```rust
use std::sync::Mutex;

fn good_function(m: &Mutex<i32>) {
    let mut guard = m.lock().unwrap();
    *guard += 1;
    if *guard > 10 {
        return; // guard is dropped here — lock released
    }
    // guard is dropped here too — lock released
}
```

You literally cannot forget to unlock. The type system makes it impossible.

## Mutex Poisoning

Here's where Rust gets opinionated. If a thread panics while holding a mutex lock, the mutex becomes *poisoned*:

```rust
use std::sync::Mutex;
use std::thread;

fn main() {
    let m = Mutex::new(0);

    let _ = thread::spawn({
        let m = &m; // borrow for scoped thread below
        move || {
            // This won't actually work with thread::spawn (needs 'static)
            // Using scope for illustration
        }
    });

    // Let's use thread::scope to show poisoning properly
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        thread::scope(|s| {
            s.spawn(|| {
                let mut guard = m.lock().unwrap();
                *guard = 42;
                panic!("oh no!"); // panic while holding the lock
            });
        });
    }));

    // The mutex is now poisoned
    match m.lock() {
        Ok(guard) => println!("Value: {}", *guard),
        Err(poisoned) => {
            println!("Mutex was poisoned!");
            // You can still access the data if you want
            let guard = poisoned.into_inner();
            println!("Value inside: {}", *guard);
        }
    }
}
```

Why poisoning? Because if a thread panicked while modifying shared state, that state might be in an inconsistent half-updated condition. Rust's stance: better to surface this problem loudly than to let other threads blindly continue with possibly corrupted data.

In practice, most people just `.unwrap()` or `.expect()` the lock, which means a poisoned mutex will crash the receiving thread too. That's usually the right call — if your data is corrupted, fail fast.

If you genuinely want to recover from poisoning:

```rust
use std::sync::Mutex;

fn resilient_access(m: &Mutex<Vec<i32>>) -> Vec<i32> {
    match m.lock() {
        Ok(guard) => guard.clone(),
        Err(poisoned) => {
            eprintln!("Warning: mutex was poisoned, recovering");
            let guard = poisoned.into_inner();
            guard.clone()
        }
    }
}
```

## RwLock<T> — Readers and Writers

`Mutex` gives exclusive access — one thread at a time, whether reading or writing. If your workload is read-heavy, this is wasteful. `RwLock` allows multiple simultaneous readers OR one exclusive writer:

```rust
use std::sync::RwLock;
use std::thread;

fn main() {
    let config = RwLock::new(vec![
        String::from("host=localhost"),
        String::from("port=5432"),
    ]);

    thread::scope(|s| {
        // Multiple readers — all can run simultaneously
        for i in 0..5 {
            s.spawn(|| {
                let cfg = config.read().unwrap();
                println!("Reader {}: {:?}", i, *cfg);
            });
        }

        // Writer — waits for all readers to finish, then gets exclusive access
        s.spawn(|| {
            let mut cfg = config.write().unwrap();
            cfg.push(String::from("timeout=30"));
            println!("Writer updated config");
        });
    });
}
```

`read()` returns a `RwLockReadGuard` — shared access. `write()` returns a `RwLockWriteGuard` — exclusive access. Same RAII pattern as `Mutex`.

### When RwLock Helps (and When It Doesn't)

RwLock shines when:
- Reads vastly outnumber writes (10:1 or more)
- Read operations are non-trivial (not just reading a single integer)

RwLock hurts when:
- Write frequency is high (writers starve or readers starve, depending on the implementation)
- The critical section is tiny (the overhead of managing reader/writer counts can exceed the savings)

A common mistake: using `RwLock` for everything because "it's more concurrent." If your access pattern is 50/50 reads and writes, a plain `Mutex` is often faster because it's simpler. Profile before optimizing.

## Lock Scope Matters

One of the most impactful things you can do with locks is *minimize how long you hold them*:

```rust
use std::sync::Mutex;

struct Cache {
    data: Mutex<Vec<String>>,
}

impl Cache {
    // BAD: holds the lock during expensive computation
    fn process_bad(&self) {
        let data = self.data.lock().unwrap();
        for item in data.iter() {
            expensive_computation(item); // other threads blocked this whole time
        }
    }

    // GOOD: copy what you need, release the lock, then compute
    fn process_good(&self) {
        let snapshot = {
            let data = self.data.lock().unwrap();
            data.clone()
        }; // lock released here

        for item in snapshot.iter() {
            expensive_computation(&item); // other threads can proceed
        }
    }
}

fn expensive_computation(s: &str) {
    // imagine this takes 100ms
    std::thread::sleep(std::time::Duration::from_millis(10));
    println!("Processed: {}", s);
}
```

The curly braces around the lock acquisition force the guard to drop early. This is the single most important optimization pattern for locked code.

## Common Patterns

**Pattern: Thread-safe counter**

```rust
use std::sync::Mutex;
use std::thread;

fn main() {
    let counter = Mutex::new(0u64);

    thread::scope(|s| {
        for _ in 0..10 {
            s.spawn(|| {
                for _ in 0..10_000 {
                    *counter.lock().unwrap() += 1;
                }
            });
        }
    });

    println!("Counter: {}", *counter.lock().unwrap()); // always 100000
}
```

(Though for a simple counter, you'd use atomics — lesson 7.)

**Pattern: Shared mutable collection**

```rust
use std::sync::Mutex;
use std::thread;
use std::collections::HashMap;

fn main() {
    let map = Mutex::new(HashMap::new());

    thread::scope(|s| {
        for i in 0..10 {
            s.spawn(|| {
                let value = expensive_lookup(i);
                map.lock().unwrap().insert(i, value);
            });
        }
    });

    let final_map = map.lock().unwrap();
    println!("Map has {} entries", final_map.len());
    for (k, v) in final_map.iter() {
        println!("  {} -> {}", k, v);
    }
}

fn expensive_lookup(key: i32) -> String {
    std::thread::sleep(std::time::Duration::from_millis(50));
    format!("value-for-{}", key)
}
```

## Deadlock Potential

Rust prevents data races but not deadlocks. Classic scenario:

```rust
use std::sync::Mutex;
use std::thread;

fn main() {
    let a = Mutex::new(1);
    let b = Mutex::new(2);

    thread::scope(|s| {
        s.spawn(|| {
            let _ga = a.lock().unwrap();
            std::thread::sleep(std::time::Duration::from_millis(100));
            let _gb = b.lock().unwrap(); // waits for b
            println!("Thread 1 got both");
        });

        s.spawn(|| {
            let _gb = b.lock().unwrap();
            std::thread::sleep(std::time::Duration::from_millis(100));
            let _ga = a.lock().unwrap(); // waits for a — DEADLOCK
            println!("Thread 2 got both");
        });
    });
}
```

Thread 1 holds `a`, waits for `b`. Thread 2 holds `b`, waits for `a`. Neither makes progress. Rust compiles this without complaint.

The fix: always lock in the same order. Or better yet, restructure to avoid holding multiple locks simultaneously.

## try_lock

If you're worried about deadlocks, `try_lock` doesn't block:

```rust
use std::sync::Mutex;

fn main() {
    let m = Mutex::new(42);

    let guard = m.lock().unwrap();

    // This won't block — returns immediately
    match m.try_lock() {
        Ok(g) => println!("Got it: {}", *g),
        Err(_) => println!("Couldn't acquire lock — it's held elsewhere"),
    }

    drop(guard); // release the first lock
}
```

Useful for implementing lock hierarchies or timeouts, but don't overuse it. If you're `try_lock`ing everywhere, your concurrency design probably needs rethinking.

---

Next — `Arc<Mutex<T>>`, the pattern that makes shared mutable state work across threads.
