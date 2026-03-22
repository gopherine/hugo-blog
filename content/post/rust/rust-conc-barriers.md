---
title: "Lesson 16: Barriers and Once — Synchronization primitives"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 16
keywords: [Rust, rust, concurrency, Barrier, Once, OnceLock, OnceCell, lazy initialization]
tags: [Rust tutorial, rust, concurrency]
date: '2024-12-05T10:15:00.000Z'
---

I was building a benchmark suite once — eight threads, each measuring throughput of a different operation. The problem was that threads started at different times depending on OS scheduling. Thread 0 might start 50ms before thread 7, skewing the results. I needed all threads to start their measurement at exactly the same time.

That's what barriers do. Everyone waits at the barrier until the last thread arrives, then they all proceed together.

## Barrier

A `Barrier` blocks threads until a specified number have called `wait()`:

```rust
use std::sync::{Arc, Barrier};
use std::thread;
use std::time::Instant;

fn main() {
    let num_threads = 8;
    let barrier = Arc::new(Barrier::new(num_threads));
    let mut handles = vec![];

    for id in 0..num_threads {
        let barrier = Arc::clone(&barrier);
        handles.push(thread::spawn(move || {
            // Setup phase — takes different time per thread
            println!("Thread {} setting up", id);
            thread::sleep(std::time::Duration::from_millis(id as u64 * 50));

            // Wait for ALL threads to finish setup
            barrier.wait();

            // Now all threads start simultaneously
            let start = Instant::now();
            // ... do measured work ...
            thread::sleep(std::time::Duration::from_millis(100));
            let elapsed = start.elapsed();
            println!("Thread {} measured: {:?}", id, elapsed);
        }));
    }

    for h in handles {
        h.join().unwrap();
    }
}
```

`wait()` blocks until all N threads have called it. Then all threads proceed. The barrier resets automatically — you can use it again for the next round.

### Multi-Round Barriers

```rust
use std::sync::{Arc, Barrier};
use std::thread;

fn main() {
    let num_threads = 4;
    let rounds = 3;
    let barrier = Arc::new(Barrier::new(num_threads));
    let mut handles = vec![];

    for id in 0..num_threads {
        let barrier = Arc::clone(&barrier);
        handles.push(thread::spawn(move || {
            for round in 0..rounds {
                // Do work for this round
                println!("Thread {} working on round {}", id, round);
                thread::sleep(std::time::Duration::from_millis(50));

                // Synchronize before next round
                let result = barrier.wait();
                if result.is_leader() {
                    println!("--- Round {} complete ---", round);
                }
            }
        }));
    }

    for h in handles {
        h.join().unwrap();
    }
}
```

`wait()` returns a `BarrierWaitResult`. Exactly one thread gets `is_leader() == true` — useful for doing one-time work between rounds (printing, aggregating, etc.) without requiring a separate coordinator thread.

### Practical Use: Parallel Simulation

```rust
use std::sync::{Arc, Barrier, Mutex};
use std::thread;

fn main() {
    let size = 100;
    let num_threads = 4;
    let steps = 10;

    let grid = Arc::new(Mutex::new(vec![0.0f64; size]));
    let barrier = Arc::new(Barrier::new(num_threads));

    // Initialize
    {
        let mut g = grid.lock().unwrap();
        for i in 0..size {
            g[i] = (i as f64).sin();
        }
    }

    let chunk_size = size / num_threads;
    let mut handles = vec![];

    for tid in 0..num_threads {
        let grid = Arc::clone(&grid);
        let barrier = Arc::clone(&barrier);
        let start = tid * chunk_size;
        let end = if tid == num_threads - 1 { size } else { (tid + 1) * chunk_size };

        handles.push(thread::spawn(move || {
            for step in 0..steps {
                // Read phase: compute updates based on current state
                let updates: Vec<(usize, f64)> = {
                    let g = grid.lock().unwrap();
                    (start..end)
                        .map(|i| {
                            let left = if i > 0 { g[i - 1] } else { 0.0 };
                            let right = if i < size - 1 { g[i + 1] } else { 0.0 };
                            (i, (left + right) / 2.0)
                        })
                        .collect()
                };

                // Barrier: ensure all threads have finished reading
                barrier.wait();

                // Write phase: apply updates
                {
                    let mut g = grid.lock().unwrap();
                    for (i, val) in updates {
                        g[i] = val;
                    }
                }

                // Barrier: ensure all threads have finished writing
                let result = barrier.wait();
                if result.is_leader() && step % 3 == 0 {
                    let g = grid.lock().unwrap();
                    let sum: f64 = g.iter().sum();
                    println!("Step {}: sum = {:.4}", step, sum);
                }
            }
        }));
    }

    for h in handles {
        h.join().unwrap();
    }
}
```

Two barriers per iteration: one after reading (so writes don't interfere with reads), one after writing (so reads of the next iteration see consistent state). This is the standard pattern for parallel iterative computations.

## Once — One-Time Initialization

`Once` ensures a piece of code runs exactly once, even when called from multiple threads:

```rust
use std::sync::Once;

static INIT: Once = Once::new();

fn initialize() {
    INIT.call_once(|| {
        println!("Initializing...");
        // expensive setup that should only happen once
    });
}

fn main() {
    // Call from multiple threads — initialization happens exactly once
    std::thread::scope(|s| {
        for _ in 0..10 {
            s.spawn(|| {
                initialize();
                println!("Using initialized resource");
            });
        }
    });
}
```

`call_once` is thread-safe. If multiple threads call it simultaneously, one thread runs the closure while the others block until it finishes. All subsequent calls return immediately.

### Once for Global Configuration

```rust
use std::sync::Once;

struct Config {
    db_url: String,
    max_connections: usize,
}

static mut CONFIG: Option<Config> = None;
static CONFIG_INIT: Once = Once::new();

fn get_config() -> &'static Config {
    CONFIG_INIT.call_once(|| {
        let config = Config {
            db_url: std::env::var("DATABASE_URL")
                .unwrap_or_else(|_| "postgres://localhost/mydb".to_string()),
            max_connections: 10,
        };
        unsafe {
            CONFIG = Some(config);
        }
    });
    unsafe { CONFIG.as_ref().unwrap() }
}

fn main() {
    let cfg = get_config();
    println!("DB: {}", cfg.db_url);
    println!("Max connections: {}", cfg.max_connections);
}
```

This works but is ugly with the `static mut` and `unsafe`. Rust has better options now.

## OnceLock (Rust 1.70+)

`OnceLock` combines `Once` and the value in a safe package:

```rust
use std::sync::OnceLock;

fn get_config() -> &'static Config {
    static CONFIG: OnceLock<Config> = OnceLock::new();
    CONFIG.get_or_init(|| {
        Config {
            db_url: std::env::var("DATABASE_URL")
                .unwrap_or_else(|_| "postgres://localhost/mydb".to_string()),
            max_connections: 10,
        }
    })
}

struct Config {
    db_url: String,
    max_connections: usize,
}

fn main() {
    let cfg = get_config();
    println!("DB: {}", cfg.db_url);

    // Called again — returns the same instance
    let cfg2 = get_config();
    assert!(std::ptr::eq(cfg, cfg2)); // same reference
}
```

No `unsafe`. No `static mut`. The `OnceLock` handles everything: thread-safe initialization, storing the value, and returning a reference. This is the modern approach.

## LazyLock (Rust 1.80+)

For even more ergonomic lazy initialization:

```rust
use std::sync::LazyLock;

static COMPUTED: LazyLock<Vec<u64>> = LazyLock::new(|| {
    println!("Computing primes...");
    sieve_of_eratosthenes(10_000)
});

fn sieve_of_eratosthenes(limit: u64) -> Vec<u64> {
    let mut is_prime = vec![true; limit as usize + 1];
    is_prime[0] = false;
    if limit > 0 { is_prime[1] = false; }
    let mut i = 2;
    while i * i <= limit {
        if is_prime[i as usize] {
            let mut j = i * i;
            while j <= limit {
                is_prime[j as usize] = false;
                j += i;
            }
        }
        i += 1;
    }
    (2..=limit).filter(|&i| is_prime[i as usize]).collect()
}

fn main() {
    println!("Before access");
    println!("Primes up to 10000: {} found", COMPUTED.len());
    println!("First 10: {:?}", &COMPUTED[..10]);
}
```

`LazyLock` is initialized on first access, automatically. No `get_or_init()` call needed — just use it like a normal static.

## Comparison: Once vs OnceLock vs LazyLock

| Feature | `Once` | `OnceLock` | `LazyLock` |
|---------|--------|-----------|-----------|
| Stores a value | No | Yes | Yes |
| Requires unsafe for value access | Yes | No | No |
| Initialization closure | `call_once` | `get_or_init` | Constructor |
| Ergonomics | Low | Medium | High |
| Use case | Side effects only | Lazy static values | Lazy static values |

My recommendation: use `LazyLock` for lazy statics, `OnceLock` when you need to initialize at different points, and `Once` only when you need side effects without a stored value.

## parking_lot Equivalents

parking_lot provides `Once` too, with the same interface but better performance under contention:

```rust
use parking_lot::Once;

static INIT: Once = Once::new();

fn main() {
    INIT.call_once(|| {
        println!("Initialized!");
    });

    // Check if initialization happened
    if INIT.state() == parking_lot::OnceState::Done {
        println!("Already initialized");
    }
}
```

The `state()` method is a nice addition — you can check whether initialization has occurred without triggering it.

## When to Use Each

- **Barrier** — Synchronize a known number of threads at specific points. Benchmarks, simulations, phase-based algorithms.
- **Once/OnceLock/LazyLock** — One-time initialization of globals. Database connection pools, configuration, computed constants.

These primitives fill specific niches. You won't use them every day, but when you need them, nothing else does the job.

---

Next — lock-free data structures, for when even the fastest mutex isn't fast enough.
