---
title: "Lesson 9: std::sync — Mutex, RwLock, Once, Barrier"
author: Atharva Pandey
series: "Rust Standard Library Mastery"
lesson: 9
keywords: [Rust, rust, standard library, sync, Mutex, RwLock, Arc, Once, Barrier, concurrency]
tags: [Rust tutorial, rust, stdlib]
date: '2024-10-04T13:25:00.000Z'
---

I shipped a data race to production exactly once. Go program, shared map, no lock. Took three weeks to reproduce — only happened under heavy load when two goroutines hit the same key simultaneously. The crash dump was useless. In Rust, that code wouldn't have compiled. That's not marketing — it's literally how the type system works.

## Arc — Shared Ownership Across Threads

Before we talk about locks, we need `Arc`. You can't share data between threads with just `Rc` — it's not thread-safe. `Arc` (Atomic Reference Counted) is the thread-safe version.

```rust
use std::sync::Arc;
use std::thread;

fn main() {
    let data = Arc::new(vec![1, 2, 3, 4, 5]);

    let mut handles = vec![];

    for i in 0..3 {
        let data = Arc::clone(&data);
        handles.push(thread::spawn(move || {
            let sum: i32 = data.iter().sum();
            println!("Thread {i}: sum = {sum}");
        }));
    }

    for handle in handles {
        handle.join().unwrap();
    }

    // data is still accessible here — the Arc keeps it alive
    // until the last reference is dropped
    println!("Main: {:?}", data);
}
```

`Arc` gives you shared *read* access. For mutable access, you need a lock inside the `Arc`.

## Mutex — Mutual Exclusion

`Mutex<T>` wraps a value and ensures only one thread can access it at a time. You call `.lock()` to get a guard, and the guard auto-unlocks when dropped.

```rust
use std::sync::{Arc, Mutex};
use std::thread;

fn main() {
    let counter = Arc::new(Mutex::new(0));
    let mut handles = vec![];

    for _ in 0..10 {
        let counter = Arc::clone(&counter);
        handles.push(thread::spawn(move || {
            // lock() blocks until the mutex is available
            let mut num = counter.lock().unwrap();
            *num += 1;
            // Guard is dropped here — mutex is unlocked
        }));
    }

    for handle in handles {
        handle.join().unwrap();
    }

    println!("Final count: {}", *counter.lock().unwrap());
}
```

### Why lock() Returns Result

`lock()` returns `Result<MutexGuard, PoisonError>`. A mutex becomes "poisoned" when a thread panics while holding the lock. The idea is that if a thread panicked mid-mutation, the data might be in an inconsistent state.

```rust
use std::sync::{Arc, Mutex};
use std::thread;

fn main() {
    let data = Arc::new(Mutex::new(vec![1, 2, 3]));

    // This thread will panic while holding the lock
    let data_clone = Arc::clone(&data);
    let handle = thread::spawn(move || {
        let mut guard = data_clone.lock().unwrap();
        guard.push(4);
        panic!("oh no!"); // Lock is poisoned
    });

    let _ = handle.join(); // Join the panicked thread

    // Subsequent locks will fail with PoisonError
    match data.lock() {
        Ok(guard) => println!("Data: {guard:?}"),
        Err(poisoned) => {
            // You can still access the data if you want
            let guard = poisoned.into_inner();
            println!("Poisoned, but data is: {guard:?}");
        }
    }
}
```

In practice, most code just uses `.lock().unwrap()` — if a thread panicked, you probably want to crash too. But in robust systems (databases, servers), you might want to recover from poisoned locks.

### Scoping Lock Guards

The guard's lifetime determines how long you hold the lock. Keep it short:

```rust
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

fn main() {
    let shared = Arc::new(Mutex::new(Vec::<String>::new()));

    let mut handles = vec![];

    for i in 0..5 {
        let shared = Arc::clone(&shared);
        handles.push(thread::spawn(move || {
            // BAD: holding the lock while doing expensive work
            // let mut data = shared.lock().unwrap();
            // expensive_computation(); // Other threads are blocked!
            // data.push(result);

            // GOOD: compute first, then lock briefly
            let result = format!("result-{i}");
            thread::sleep(Duration::from_millis(50)); // Simulate work

            {
                let mut data = shared.lock().unwrap();
                data.push(result);
            } // Lock released here
        }));
    }

    for h in handles {
        h.join().unwrap();
    }

    println!("Results: {:?}", shared.lock().unwrap());
}
```

The explicit block `{ let mut data = ...; ... }` is the standard pattern for controlling lock scope. Without it, the guard lives until the end of the function — which means you're holding the lock much longer than necessary.

## RwLock — Multiple Readers, One Writer

`RwLock<T>` allows multiple simultaneous readers but only one writer. Use it when reads vastly outnumber writes.

```rust
use std::sync::{Arc, RwLock};
use std::thread;
use std::time::Duration;

fn main() {
    let config = Arc::new(RwLock::new(std::collections::HashMap::from([
        ("timeout".to_string(), "30".to_string()),
        ("retries".to_string(), "3".to_string()),
        ("debug".to_string(), "false".to_string()),
    ])));

    let mut handles = vec![];

    // Spawn 5 reader threads
    for i in 0..5 {
        let config = Arc::clone(&config);
        handles.push(thread::spawn(move || {
            for _ in 0..3 {
                let guard = config.read().unwrap();
                println!(
                    "Reader {i}: timeout = {}",
                    guard.get("timeout").unwrap()
                );
                // Multiple readers can hold this simultaneously
                thread::sleep(Duration::from_millis(10));
            }
        }));
    }

    // Spawn 1 writer thread
    {
        let config = Arc::clone(&config);
        handles.push(thread::spawn(move || {
            thread::sleep(Duration::from_millis(20));
            let mut guard = config.write().unwrap();
            // This blocks until ALL readers release their guards
            guard.insert("timeout".to_string(), "60".to_string());
            println!("Writer: updated timeout to 60");
        }));
    }

    for h in handles {
        h.join().unwrap();
    }

    println!("Final config: {:?}", config.read().unwrap());
}
```

### Mutex vs. RwLock: When to Use Which

Don't default to `RwLock` thinking it's always faster than `Mutex`. `RwLock` has higher overhead per operation because it tracks reader counts atomically. The crossover point depends on your workload:

- **Mostly writes, or short critical sections** — use `Mutex`. The simpler locking is faster.
- **Many readers, rare writers, and the read-side work is non-trivial** — use `RwLock`. The parallelism wins.
- **Not sure** — use `Mutex`. It's simpler and harder to misuse.

## Once and OnceLock — One-Time Initialization

`Once` guarantees a function runs exactly once, even if called from multiple threads simultaneously. `OnceLock` is the modern version that stores a value.

```rust
use std::sync::OnceLock;

// Global configuration — initialized exactly once
static CONFIG: OnceLock<AppConfig> = OnceLock::new();

#[derive(Debug)]
struct AppConfig {
    database_url: String,
    max_connections: u32,
    debug: bool,
}

fn get_config() -> &'static AppConfig {
    CONFIG.get_or_init(|| {
        println!("Initializing config (this prints once)");
        AppConfig {
            database_url: "postgres://localhost/mydb".to_string(),
            max_connections: 10,
            debug: false,
        }
    })
}

fn main() {
    // First call initializes
    let config = get_config();
    println!("DB: {}", config.database_url);

    // Second call returns the same value — no initialization
    let config2 = get_config();
    println!("Debug: {}", config2.debug);

    // They're the same reference
    assert!(std::ptr::eq(config, config2));
}
```

`OnceLock` replaced the old pattern of `lazy_static!` or `once_cell::sync::Lazy` for most use cases. It's in the standard library since Rust 1.80.

For when you don't need to store a value:

```rust
use std::sync::Once;

static INIT: Once = Once::new();

fn initialize_logging() {
    INIT.call_once(|| {
        // Set up logging framework
        println!("Logging initialized");
    });
}

fn main() {
    // Safe to call from multiple threads — runs exactly once
    initialize_logging();
    initialize_logging();
    initialize_logging();
    // "Logging initialized" prints only once
}
```

## Barrier — Synchronization Point

A `Barrier` blocks threads until all of them have reached the barrier point, then releases them all simultaneously.

```rust
use std::sync::{Arc, Barrier};
use std::thread;
use std::time::{Duration, Instant};

fn main() {
    let num_threads = 4;
    let barrier = Arc::new(Barrier::new(num_threads));

    let mut handles = vec![];

    for i in 0..num_threads {
        let barrier = Arc::clone(&barrier);
        handles.push(thread::spawn(move || {
            // Phase 1: each thread does different amounts of setup
            let setup_time = Duration::from_millis(50 * (i as u64 + 1));
            println!("Thread {i}: starting setup ({setup_time:?})");
            thread::sleep(setup_time);
            println!("Thread {i}: setup complete, waiting at barrier");

            // All threads wait here until everyone arrives
            barrier.wait();

            // Phase 2: all threads proceed together
            let start = Instant::now();
            println!("Thread {i}: past barrier at {start:?}");
        }));
    }

    for h in handles {
        h.join().unwrap();
    }
}
```

Barriers are useful for phased computations — when all threads must complete phase N before any thread starts phase N+1. Think parallel matrix operations, simulation steps, or multi-phase data processing.

## Condvar — Condition Variables

`Condvar` lets threads wait for a condition to become true. It's always paired with a `Mutex`.

```rust
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::Duration;

fn main() {
    let pair = Arc::new((Mutex::new(false), Condvar::new()));

    // Waiting thread
    let pair_clone = Arc::clone(&pair);
    let waiter = thread::spawn(move || {
        let (lock, cvar) = &*pair_clone;
        let mut ready = lock.lock().unwrap();

        println!("Waiter: waiting for signal...");
        while !*ready {
            ready = cvar.wait(ready).unwrap();
            // wait() atomically:
            // 1. Releases the mutex
            // 2. Puts this thread to sleep
            // 3. Re-acquires the mutex when woken
        }
        println!("Waiter: got the signal!");
    });

    // Signaling thread
    thread::sleep(Duration::from_secs(1));
    let (lock, cvar) = &*pair;
    {
        let mut ready = lock.lock().unwrap();
        *ready = true;
        println!("Signaler: sending signal");
    }
    cvar.notify_one(); // Wake one waiting thread

    waiter.join().unwrap();
}
```

### Bounded Channel with Condvar

Here's a practical example — a bounded producer-consumer channel:

```rust
use std::collections::VecDeque;
use std::sync::{Arc, Condvar, Mutex};
use std::thread;

struct BoundedChannel<T> {
    queue: Mutex<VecDeque<T>>,
    not_empty: Condvar,
    not_full: Condvar,
    capacity: usize,
}

impl<T> BoundedChannel<T> {
    fn new(capacity: usize) -> Self {
        BoundedChannel {
            queue: Mutex::new(VecDeque::with_capacity(capacity)),
            not_empty: Condvar::new(),
            not_full: Condvar::new(),
            capacity,
        }
    }

    fn send(&self, item: T) {
        let mut queue = self.queue.lock().unwrap();
        while queue.len() >= self.capacity {
            queue = self.not_full.wait(queue).unwrap();
        }
        queue.push_back(item);
        self.not_empty.notify_one();
    }

    fn recv(&self) -> T {
        let mut queue = self.queue.lock().unwrap();
        while queue.is_empty() {
            queue = self.not_empty.wait(queue).unwrap();
        }
        let item = queue.pop_front().unwrap();
        self.not_full.notify_one();
        item
    }
}

fn main() {
    let channel = Arc::new(BoundedChannel::new(3));

    let producer = {
        let ch = Arc::clone(&channel);
        thread::spawn(move || {
            for i in 0..10 {
                println!("Producing: {i}");
                ch.send(i);
            }
        })
    };

    let consumer = {
        let ch = Arc::clone(&channel);
        thread::spawn(move || {
            for _ in 0..10 {
                let item = ch.recv();
                println!("Consumed: {item}");
                thread::sleep(std::time::Duration::from_millis(50));
            }
        })
    };

    producer.join().unwrap();
    consumer.join().unwrap();
}
```

## Atomic Types

For simple counters and flags, atomic types avoid the overhead of a `Mutex`:

```rust
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

fn main() {
    // Atomic counter — no lock needed
    let counter = Arc::new(AtomicU64::new(0));
    let mut handles = vec![];

    for _ in 0..10 {
        let counter = Arc::clone(&counter);
        handles.push(thread::spawn(move || {
            for _ in 0..1000 {
                counter.fetch_add(1, Ordering::Relaxed);
            }
        }));
    }

    for h in handles {
        h.join().unwrap();
    }
    println!("Counter: {}", counter.load(Ordering::Relaxed));

    // Atomic flag for shutdown signal
    let running = Arc::new(AtomicBool::new(true));

    let worker = {
        let running = Arc::clone(&running);
        thread::spawn(move || {
            let mut iterations = 0u64;
            while running.load(Ordering::Relaxed) {
                iterations += 1;
                thread::sleep(Duration::from_millis(10));
            }
            println!("Worker did {iterations} iterations");
        })
    };

    thread::sleep(Duration::from_millis(100));
    running.store(false, Ordering::Relaxed);
    worker.join().unwrap();
}
```

`Ordering` matters for correctness in complex scenarios. For simple counters and flags, `Relaxed` is usually fine. For synchronization between threads (where you need happens-before guarantees), use `SeqCst` or `Acquire`/`Release`. If you're not sure, use `SeqCst` — it's the safest but slowest ordering.

## The Decision Tree

Here's how I pick the right synchronization primitive:

1. **Do I need to share data across threads?** If no, you don't need any of this.
2. **Is the data read-only?** Use `Arc<T>` — no lock needed.
3. **Is it a simple counter or flag?** Use atomics (`AtomicU64`, `AtomicBool`).
4. **Is it a complex value with mostly reads?** Use `Arc<RwLock<T>>`.
5. **Is it a complex value with frequent writes?** Use `Arc<Mutex<T>>`.
6. **Need one-time initialization?** Use `OnceLock`.
7. **Need threads to wait for each other?** Use `Barrier` or `Condvar`.

Rust's type system means you literally cannot have a data race — if you forget the lock, it won't compile. That guarantee alone is worth the learning curve. Every other language I've worked with treats data races as a runtime bug to discover. Rust treats them as a compile-time error to prevent.
