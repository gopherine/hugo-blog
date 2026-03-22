---
title: "Lesson 19: Thread-Local Storage — Per-thread state"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 19
keywords: [Rust, rust, concurrency, thread-local, thread_local, TLS, per-thread state]
tags: [Rust tutorial, rust, concurrency]
date: '2024-12-11T09:55:00.000Z'
---

I was optimizing a JSON serializer that allocated a buffer for every call. Under profiling, those allocations were 30% of the cost. The fix? A thread-local buffer that gets reused across calls on the same thread. No synchronization needed. No contention. Each thread has its own buffer. Throughput doubled.

Thread-local storage is the ultimate escape hatch from synchronization overhead. If each thread has its own copy, there's nothing to synchronize.

## The Problem: Unnecessary Sharing

Not all state needs to be shared. Per-request allocators, cached computations, RNG state — these are inherently per-thread. Putting them behind a mutex adds overhead for zero benefit.

```rust
use std::sync::{Arc, Mutex};

// BAD: sharing a buffer pool across threads for no reason
struct SharedBufferPool {
    buffers: Mutex<Vec<Vec<u8>>>,
}

impl SharedBufferPool {
    fn get_buffer(&self) -> Vec<u8> {
        self.buffers.lock().unwrap().pop().unwrap_or_else(|| Vec::with_capacity(4096))
    }

    fn return_buffer(&self, buf: Vec<u8>) {
        self.buffers.lock().unwrap().push(buf);
    }
}
```

Every buffer checkout locks a mutex. With 32 threads serializing data at high rates, this becomes a bottleneck fast.

## thread_local! Macro

Rust's `thread_local!` macro creates a value that's unique to each thread:

```rust
use std::cell::RefCell;

thread_local! {
    static BUFFER: RefCell<Vec<u8>> = RefCell::new(Vec::with_capacity(4096));
}

fn serialize_something(data: &[u8]) -> Vec<u8> {
    BUFFER.with(|buf| {
        let mut buf = buf.borrow_mut();
        buf.clear();
        buf.extend_from_slice(data);
        // Process buf...
        buf.clone() // return a copy of the result
    })
}

fn main() {
    use std::thread;

    thread::scope(|s| {
        for _ in 0..4 {
            s.spawn(|| {
                for i in 0..1000 {
                    let data = format!("item-{}", i);
                    let _result = serialize_something(data.as_bytes());
                }
            });
        }
    });

    println!("Done — each thread reused its own buffer");
}
```

`BUFFER` looks like a global, but each thread gets its own `Vec<u8>`. No synchronization, no contention. The `with()` method gives you access to the current thread's value.

## How It Works

Thread-local storage uses OS facilities (pthreads TLS on Unix, TLS slots on Windows) to give each thread its own copy of the variable. When a thread is created, its thread-locals are initialized lazily — the first time they're accessed.

```rust
use std::cell::Cell;

thread_local! {
    static COUNTER: Cell<u32> = Cell::new(0);
}

fn increment() -> u32 {
    COUNTER.with(|c| {
        let val = c.get() + 1;
        c.set(val);
        val
    })
}

fn main() {
    use std::thread;

    // Main thread
    println!("Main: {}", increment()); // 1
    println!("Main: {}", increment()); // 2

    // Spawned thread — gets its own counter starting at 0
    thread::spawn(|| {
        println!("Thread: {}", increment()); // 1 (not 3!)
        println!("Thread: {}", increment()); // 2
    })
    .join()
    .unwrap();

    // Main thread's counter is independent
    println!("Main: {}", increment()); // 3
}
```

Each thread starts with a fresh counter. There's no sharing, no interference.

## Practical Patterns

### Per-Thread RNG

```rust
use std::cell::RefCell;

// Simple (bad) RNG for illustration — use `rand` crate in real code
struct SimpleRng {
    state: u64,
}

impl SimpleRng {
    fn new(seed: u64) -> Self {
        SimpleRng { state: seed }
    }

    fn next(&mut self) -> u64 {
        self.state = self.state.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        self.state
    }
}

thread_local! {
    static RNG: RefCell<SimpleRng> = RefCell::new(SimpleRng::new(
        // Seed from thread ID for uniqueness
        std::thread::current().id().as_u64().get()
    ));
}

fn random_u64() -> u64 {
    RNG.with(|rng| rng.borrow_mut().next())
}

fn main() {
    use std::thread;

    thread::scope(|s| {
        for _ in 0..4 {
            s.spawn(|| {
                let vals: Vec<u64> = (0..5).map(|_| random_u64()).collect();
                println!("{:?}: {:?}", thread::current().id(), vals);
            });
        }
    });
}
```

Each thread gets its own RNG with a unique seed. No lock contention, no coordination. This is how `rand::thread_rng()` works under the hood.

### Per-Thread Metrics

```rust
use std::cell::Cell;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

// Thread-local accumulators, periodically flushed to global counters
thread_local! {
    static LOCAL_REQUESTS: Cell<u64> = Cell::new(0);
    static LOCAL_BYTES: Cell<u64> = Cell::new(0);
}

struct Metrics {
    total_requests: AtomicU64,
    total_bytes: AtomicU64,
}

impl Metrics {
    fn new() -> Self {
        Metrics {
            total_requests: AtomicU64::new(0),
            total_bytes: AtomicU64::new(0),
        }
    }

    fn record_request(&self, bytes: u64) {
        LOCAL_REQUESTS.with(|c| c.set(c.get() + 1));
        LOCAL_BYTES.with(|c| c.set(c.get() + bytes));

        // Flush every 1000 requests to avoid frequent atomics
        LOCAL_REQUESTS.with(|c| {
            if c.get() >= 1000 {
                self.flush();
            }
        });
    }

    fn flush(&self) {
        let reqs = LOCAL_REQUESTS.with(|c| {
            let v = c.get();
            c.set(0);
            v
        });
        let bytes = LOCAL_BYTES.with(|c| {
            let v = c.get();
            c.set(0);
            v
        });
        self.total_requests.fetch_add(reqs, Ordering::Relaxed);
        self.total_bytes.fetch_add(bytes, Ordering::Relaxed);
    }

    fn snapshot(&self) -> (u64, u64) {
        (
            self.total_requests.load(Ordering::Relaxed),
            self.total_bytes.load(Ordering::Relaxed),
        )
    }
}

fn main() {
    use std::thread;

    let metrics = Arc::new(Metrics::new());
    let mut handles = vec![];

    for _ in 0..8 {
        let m = Arc::clone(&metrics);
        handles.push(thread::spawn(move || {
            for _ in 0..10_000 {
                m.record_request(256);
            }
            m.flush(); // flush remaining
        }));
    }

    for h in handles {
        h.join().unwrap();
    }

    let (reqs, bytes) = metrics.snapshot();
    println!("Total requests: {}, bytes: {}", reqs, bytes);
}
```

This pattern — accumulate locally, flush globally — dramatically reduces contention. Instead of 80,000 atomic operations (one per request per thread), you get 80 (one per flush). That's a 1000x reduction in cross-thread synchronization.

### Thread-Local Allocator/Arena

```rust
use std::cell::RefCell;

struct Arena {
    storage: Vec<u8>,
    offset: usize,
}

impl Arena {
    fn new(capacity: usize) -> Self {
        Arena {
            storage: vec![0u8; capacity],
            offset: 0,
        }
    }

    fn alloc(&mut self, size: usize) -> &mut [u8] {
        let start = self.offset;
        self.offset += size;
        if self.offset > self.storage.len() {
            panic!("Arena out of memory");
        }
        &mut self.storage[start..start + size]
    }

    fn reset(&mut self) {
        self.offset = 0;
    }
}

thread_local! {
    static ARENA: RefCell<Arena> = RefCell::new(Arena::new(1024 * 1024)); // 1MB per thread
}

fn with_arena<F, R>(f: F) -> R
where
    F: FnOnce(&mut Arena) -> R,
{
    ARENA.with(|arena| {
        let mut arena = arena.borrow_mut();
        arena.reset();
        f(&mut arena)
    })
}

fn main() {
    use std::thread;

    thread::scope(|s| {
        for id in 0..4 {
            s.spawn(move || {
                with_arena(|arena| {
                    let buf = arena.alloc(256);
                    buf[0] = id as u8;
                    println!("Thread {} allocated 256 bytes from arena", id);
                });
            });
        }
    });
}
```

## Limitations

Thread-local storage has some gotchas:

**1. Destructors run in unpredictable order:**

```rust
thread_local! {
    static A: RefCell<String> = RefCell::new(String::from("hello"));
    static B: RefCell<String> = RefCell::new(String::from("world"));
}

// When the thread exits, A and B are dropped — but the order isn't guaranteed.
// If B's destructor tries to access A, it might find A already dropped.
```

**2. Accessing thread-locals during thread shutdown can panic:**

`with()` panics if the thread-local has already been destroyed. This happens during thread cleanup if one thread-local's destructor tries to access another.

**3. Thread-locals don't work well with thread pools:**

If you're using a thread pool (Rayon, etc.), thread-locals persist across tasks. A value set by task A will still be there when task B runs on the same thread. This can cause subtle bugs if you expect fresh state per task.

```rust
// With thread pools: always reset state at the start of each task
fn process_task(task_id: usize) {
    LOCAL_COUNTER.with(|c| c.set(0)); // reset!
    // ... do work ...
}
```

**4. No way to iterate over all thread-locals:**

You can't "collect all thread-local counters" from the main thread. You need an explicit flush mechanism (like the metrics pattern above) or a registry where threads register their values.

## with_borrow and with_borrow_mut (Rust 1.73+)

Newer Rust versions added convenience methods:

```rust
use std::cell::RefCell;

thread_local! {
    static DATA: RefCell<Vec<i32>> = RefCell::new(Vec::new());
}

fn main() {
    // Before Rust 1.73
    DATA.with(|d| d.borrow_mut().push(42));
    let val = DATA.with(|d| d.borrow()[0]);

    // Rust 1.73+
    DATA.with_borrow_mut(|d| d.push(43));
    let val = DATA.with_borrow(|d| d[0]);
    println!("{}", val);
}
```

Less nesting, same semantics.

## When to Use Thread-Local Storage

- **Caches** — Per-thread caches that avoid synchronization
- **Buffers** — Reusable scratch space (serialization, formatting)
- **RNG** — Random number generators
- **Metrics** — Local accumulators flushed to global counters
- **Context** — Request context in server applications (but be careful with async)

The pattern is always the same: "This state is per-thread, and sharing it would just add overhead." When you find yourself wrapping something in `Arc<Mutex<T>>` that never actually needs to be shared, thread-local might be the answer.

---

Next — the actor model, where we turn message passing into a full architecture.
