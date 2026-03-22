---
title: "Lesson 8: Memory Ordering — Relaxed, Acquire, Release, SeqCst"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 8
keywords: [Rust, rust, concurrency, memory ordering, Relaxed, Acquire, Release, SeqCst, happens-before]
tags: [Rust tutorial, rust, concurrency]
date: '2024-11-19T13:15:00.000Z'
---

Memory ordering is the thing that separates people who *use* concurrent code from people who *write* concurrent primitives. I avoided understanding it for years, using `SeqCst` everywhere like a safety blanket. It worked. But it left performance on the table and, more importantly, left me unable to read half the lock-free code I encountered.

So here's the actual explanation — no hand-waving.

## Why Ordering Matters

Modern CPUs and compilers reorder instructions for performance. Your code says "write A, then write B," but the CPU might execute B first if there's no dependency between them. On a single thread, this is invisible — the final result is the same.

With multiple threads, reordering becomes visible. Thread 1 writes data then sets a flag. Thread 2 sees the flag and reads the data. But if the CPU reordered thread 1's writes, thread 2 might see the flag *before* the data is actually written. Boom — you're reading garbage.

```rust
// CONCEPTUAL PROBLEM — not real Rust (this is what can go wrong)
// Thread 1:
data = 42;        // might be reordered AFTER the flag store
flag = true;

// Thread 2:
if flag {
    use(data);    // might read stale data because data write hasn't happened yet
}
```

Memory ordering tells the CPU and compiler: "These operations must be visible in this order."

## The Four Orderings in Rust

Rust exposes the same memory orderings as C++:

### Relaxed

No ordering guarantees relative to other operations. Only guarantees atomicity — the operation itself is indivisible.

```rust
use std::sync::atomic::{AtomicU64, Ordering};

let counter = AtomicU64::new(0);
counter.fetch_add(1, Ordering::Relaxed);
```

When to use: Independent counters, statistics, anything where you don't care about the *order* operations become visible to other threads. Just that each individual operation is atomic.

### Acquire

When a load operation uses `Acquire`, all subsequent reads and writes in the current thread are guaranteed to happen *after* the load. It "acquires" the state published by the corresponding `Release`.

### Release

When a store operation uses `Release`, all preceding reads and writes in the current thread are guaranteed to happen *before* the store. It "releases" or "publishes" the data for an `Acquire` load to pick up.

### SeqCst (Sequentially Consistent)

The strongest ordering. All `SeqCst` operations across all threads appear to happen in a single, global, total order that all threads agree on. This is the easiest to reason about but the most expensive.

## Acquire-Release in Practice

The `Acquire`-`Release` pair is the workhorse of concurrent synchronization. Here's the canonical example — using a flag to signal that data is ready:

```rust
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;

fn main() {
    let data = std::cell::UnsafeCell::new(0u64);
    let ready = AtomicBool::new(false);

    // Safety: we use acquire/release to ensure proper synchronization
    unsafe {
        thread::scope(|s| {
            // Producer
            s.spawn(|| {
                // Write data BEFORE setting the flag
                *data.get() = 42;
                // Release: everything before this store is visible
                // to any thread that does an Acquire load of this value
                ready.store(true, Ordering::Release);
            });

            // Consumer
            s.spawn(|| {
                // Acquire: everything the producer did before their Release
                // is guaranteed to be visible after this load returns true
                while !ready.load(Ordering::Acquire) {
                    std::hint::spin_loop();
                }
                // Safe to read — acquire guarantees we see the producer's writes
                assert_eq!(*data.get(), 42);
                println!("Got: {}", *data.get());
            });
        });
    }
}
```

The `Release` store on the flag creates a "happens-before" relationship with the `Acquire` load. Everything the producer wrote before `Release` is guaranteed to be visible to the consumer after `Acquire`. This is a formal guarantee — not just "probably works."

If we used `Relaxed` instead, the consumer might see `ready == true` but still read `data == 0` because the data write wasn't ordered relative to the flag write.

## When to Use Each Ordering

Here's my decision framework:

**`Relaxed`** — The value is independent of everything else. No other data depends on seeing this update in a particular order.
- Counters for metrics
- Statistics that are read periodically
- Progress indicators

**`Acquire`/`Release`** — You're using an atomic as a synchronization point. One thread publishes data and signals via Release; another thread receives the signal via Acquire and reads the data.
- Lock implementations
- Producer-consumer flags
- "Data ready" signals
- Most real synchronization patterns

**`SeqCst`** — You need all threads to agree on the total order of operations. This is rare.
- When you have multiple atomic variables that must be seen in the same order by all threads
- Fence operations for complex protocols
- When you're not sure and correctness matters more than performance

## A Safer Example: One-Shot Channel

Here's an acquire/release pattern without `UnsafeCell`:

```rust
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;

struct OneShotChannel<T> {
    data: std::sync::Mutex<Option<T>>,
    ready: AtomicBool,
}

impl<T> OneShotChannel<T> {
    fn new() -> Self {
        OneShotChannel {
            data: std::sync::Mutex::new(None),
            ready: AtomicBool::new(false),
        }
    }

    fn send(&self, value: T) {
        *self.data.lock().unwrap() = Some(value);
        self.ready.store(true, Ordering::Release);
    }

    fn recv(&self) -> T {
        while !self.ready.load(Ordering::Acquire) {
            std::hint::spin_loop();
        }
        self.data.lock().unwrap().take().unwrap()
    }
}

fn main() {
    let channel = Arc::new(OneShotChannel::new());

    let sender = {
        let ch = Arc::clone(&channel);
        thread::spawn(move || {
            ch.send(String::from("hello from sender"));
        })
    };

    let receiver = {
        let ch = Arc::clone(&channel);
        thread::spawn(move || {
            let msg = ch.recv();
            println!("Received: {}", msg);
        })
    };

    sender.join().unwrap();
    receiver.join().unwrap();
}
```

The `Release` on `send` ensures the data write (via Mutex) is visible before `ready` becomes true. The `Acquire` on `recv` ensures we see that data after observing `ready == true`.

## The SeqCst Tax

`SeqCst` is more expensive than `Acquire`/`Release` because it requires a full memory fence — essentially flushing write buffers and stalling the pipeline. On x86, the difference is small. On ARM or RISC-V, it can be significant.

```rust
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;

// This requires SeqCst — the "store buffer litmus test"
fn main() {
    let x = AtomicBool::new(false);
    let y = AtomicBool::new(false);
    let mut a = false;
    let mut b = false;

    thread::scope(|s| {
        s.spawn(|| {
            x.store(true, Ordering::SeqCst);
            a = y.load(Ordering::SeqCst);
        });

        s.spawn(|| {
            y.store(true, Ordering::SeqCst);
            b = x.load(Ordering::SeqCst);
        });
    });

    // With SeqCst: at least one of a or b must be true
    // With Relaxed: both could be false (!) due to store buffer reordering
    assert!(a || b, "This should never fail with SeqCst");
    println!("a={}, b={}", a, b);
}
```

This is the classic example where `Relaxed` gives surprising results. Each CPU's store buffer might hold its own write while reading a stale value of the other's variable. `SeqCst` prevents this by forcing stores to be globally visible before subsequent loads.

In practice, I've never needed `SeqCst` in application code. `Acquire`/`Release` covers 99% of real synchronization needs. `SeqCst` is for the remaining 1% — and for when you're writing a paper about memory models.

## Ordering Rules for CAS

`compare_exchange` takes *two* orderings — one for success, one for failure:

```rust
use std::sync::atomic::{AtomicI32, Ordering};

let val = AtomicI32::new(5);

// Success: Release (we're publishing something)
// Failure: Relaxed (we're just reading the current value, no sync needed)
val.compare_exchange(5, 10, Ordering::Release, Ordering::Relaxed);

// For lock acquisition:
// Success: Acquire (we need to see data protected by the lock)
// Failure: Relaxed (failed to acquire, no data to synchronize)
val.compare_exchange(0, 1, Ordering::Acquire, Ordering::Relaxed);
```

The failure ordering can be weaker than the success ordering. This matters on ARM where stronger orderings have real cost.

## Fences

You can also use standalone fences to apply ordering without an atomic operation:

```rust
use std::sync::atomic::{fence, Ordering};

// Equivalent to an Acquire load followed by...
fence(Ordering::Acquire);
// ...any reads/writes here are guaranteed after the fence

// Equivalent to ...preceding reads/writes... followed by a Release store
fence(Ordering::Release);
```

Fences are a power tool. You rarely need them in application code. They show up in implementations of lock-free data structures and custom synchronization primitives.

## Practical Guidelines

1. **Start with `SeqCst`** when prototyping. It's never wrong, just potentially slow.
2. **Switch to `Acquire`/`Release`** when you understand the synchronization pattern. Pair them: `Release` on the store, `Acquire` on the load.
3. **Use `Relaxed`** only for truly independent operations — counters, statistics, progress bars.
4. **Never use `Relaxed`** for synchronization flags unless you've proven correctness.
5. **When in doubt, use `SeqCst`**. The performance difference is almost never your bottleneck.

The vast majority of Rust code never touches memory orderings directly. `Mutex`, `RwLock`, channels — they all handle ordering internally. You only need this when building low-level primitives or optimizing extremely hot paths.

---

Next — `Send` and `Sync`, the traits that make all of this work at the type system level.
