---
title: "Lesson 11: Crossbeam — Scoped threads and lock-free structures"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 11
keywords: [Rust, rust, concurrency, crossbeam, scoped threads, lock-free, channels, deque]
tags: [Rust tutorial, rust, concurrency]
date: '2024-11-25T11:05:00.000Z'
---

Before Rust 1.63 added `thread::scope` to the standard library, crossbeam was the only ergonomic way to spawn threads that could borrow local data. Even now that std has scoped threads, crossbeam remains essential. Its channels are faster than `std::sync::mpsc`, it provides lock-free data structures, and its utilities fill gaps the standard library doesn't cover.

I reach for crossbeam in nearly every concurrent Rust project. Here's why.

## Crossbeam Channels

The biggest win: crossbeam's channels are multi-producer, multi-consumer (MPMC) and significantly faster than `std::sync::mpsc`.

```toml
[dependencies]
crossbeam-channel = "0.5"
```

```rust
use crossbeam_channel::{unbounded, bounded};
use std::thread;

fn main() {
    // Unbounded channel
    let (tx, rx) = unbounded();

    thread::spawn(move || {
        tx.send("hello").unwrap();
    });

    println!("{}", rx.recv().unwrap());
}
```

Looks the same as `mpsc`, right? The key difference: you can clone *both* the sender and the receiver.

### Multi-Consumer Work Distribution

```rust
use crossbeam_channel::bounded;
use std::thread;

fn main() {
    let (tx, rx) = bounded(100);
    let mut handles = vec![];

    // 4 worker threads — all consuming from the same channel
    for id in 0..4 {
        let rx = rx.clone();
        handles.push(thread::spawn(move || {
            let mut count = 0;
            while let Ok(task) = rx.recv() {
                // process task
                count += 1;
                if count % 100 == 0 {
                    println!("Worker {} processed {} tasks (last: {})", id, count, task);
                }
            }
            println!("Worker {} finished, total: {}", id, count);
        }));
    }

    // Send 1000 tasks
    for i in 0..1000 {
        tx.send(i).unwrap();
    }
    drop(tx); // close the channel

    for h in handles {
        h.join().unwrap();
    }
}
```

Try doing this with `std::sync::mpsc` — you'd need `Arc<Mutex<Receiver>>`, which serializes all receives through a lock. Crossbeam's receivers are designed for concurrent access. Each message goes to exactly one consumer, with efficient work distribution.

### Select: Waiting on Multiple Channels

The real superpower of crossbeam channels. `select!` waits on multiple channels simultaneously:

```rust
use crossbeam_channel::{bounded, select, tick, after};
use std::thread;
use std::time::Duration;

fn main() {
    let (tx_data, rx_data) = bounded(10);
    let (tx_control, rx_control) = bounded(1);

    // Data producer
    thread::spawn(move || {
        for i in 0..20 {
            tx_data.send(format!("data-{}", i)).unwrap();
            thread::sleep(Duration::from_millis(200));
        }
    });

    // Control signal — shutdown after 2 seconds
    thread::spawn(move || {
        thread::sleep(Duration::from_secs(2));
        tx_control.send("shutdown").unwrap();
    });

    // Tick channel — fires every 500ms
    let ticker = tick(Duration::from_millis(500));

    loop {
        select! {
            recv(rx_data) -> msg => {
                match msg {
                    Ok(data) => println!("Data: {}", data),
                    Err(_) => {
                        println!("Data channel closed");
                        break;
                    }
                }
            }
            recv(rx_control) -> msg => {
                println!("Control: {:?}", msg);
                break;
            }
            recv(ticker) -> _ => {
                println!("Tick — still alive");
            }
        }
    }
    println!("Exiting");
}
```

If you've used Go's `select`, this is Rust's answer. Multiple channels, first one ready wins. The `tick` and `after` channels are built-in utilities for timeouts and periodic events.

### Timeout Patterns

```rust
use crossbeam_channel::{bounded, after};
use std::thread;
use std::time::Duration;

fn main() {
    let (tx, rx) = bounded(1);

    thread::spawn(move || {
        thread::sleep(Duration::from_secs(5)); // slow producer
        let _ = tx.send("late response");
    });

    let timeout = after(Duration::from_secs(2));

    crossbeam_channel::select! {
        recv(rx) -> msg => println!("Got: {:?}", msg),
        recv(timeout) -> _ => println!("Timed out after 2 seconds"),
    }
}
```

## Crossbeam Utils

### Scoped Threads

Before Rust 1.63, crossbeam was the only option. Now std has `thread::scope`, but crossbeam's version is functionally identical:

```rust
use crossbeam_utils::thread;

fn main() {
    let data = vec![1, 2, 3, 4, 5];

    thread::scope(|s| {
        s.spawn(|_| {
            println!("Thread sees: {:?}", &data);
        });
        s.spawn(|_| {
            println!("This one too: {:?}", &data);
        });
    }).unwrap();

    println!("Main: {:?}", data);
}
```

Note the slight API difference: crossbeam's scoped spawn closures take a `&Scope` parameter (the `|_|`), and `scope()` returns a `Result`.

### WaitGroup

Like Go's `sync.WaitGroup`:

```rust
use crossbeam_utils::sync::WaitGroup;
use std::thread;

fn main() {
    let wg = WaitGroup::new();
    let mut handles = vec![];

    for i in 0..5 {
        let wg = wg.clone();
        handles.push(thread::spawn(move || {
            println!("Worker {} starting", i);
            std::thread::sleep(std::time::Duration::from_millis(100 * i as u64));
            println!("Worker {} done", i);
            drop(wg); // decrements the counter
        }));
    }

    wg.wait(); // blocks until all clones are dropped
    println!("All workers finished");
}
```

### ShardedLock

A sharded read-write lock that performs better under high read contention:

```rust
use crossbeam_utils::sync::ShardedLock;
use std::thread;

fn main() {
    let data = ShardedLock::new(vec![1, 2, 3, 4, 5]);

    thread::scope(|s| {
        // Many concurrent readers
        for i in 0..20 {
            s.spawn(|| {
                let d = data.read().unwrap();
                println!("Reader {}: {:?}", i, *d);
            });
        }
    });
}
```

`ShardedLock` distributes read locks across multiple shards to reduce contention. Reads are faster than `RwLock` when you have many readers, but writes are slower. Use it for read-heavy workloads.

## Crossbeam Epoch: Lock-Free Memory Reclamation

This is the deep end. Crossbeam provides epoch-based memory reclamation for lock-free data structures:

```rust
use crossbeam_epoch::{self as epoch, Atomic, Owned, Shared};
use std::sync::atomic::Ordering;

fn main() {
    let data: Atomic<String> = Atomic::new("initial".to_string());

    // Pin the current thread to an epoch
    let guard = epoch::pin();

    // Load the current value
    let current = data.load(Ordering::Acquire, &guard);
    unsafe {
        if let Some(val) = current.as_ref() {
            println!("Current: {}", val);
        }
    }

    // Atomically swap in a new value
    let old = data.swap(
        Owned::new("updated".to_string()),
        Ordering::AcqRel,
        &guard,
    );

    // Defer deallocation — the old value will be freed after all threads
    // in the current epoch finish
    unsafe {
        if !old.is_null() {
            guard.defer_destroy(old);
        }
    }

    // Read the new value
    let new = data.load(Ordering::Acquire, &guard);
    unsafe {
        if let Some(val) = new.as_ref() {
            println!("Updated: {}", val);
        }
    }
}
```

Epoch-based reclamation solves the "when is it safe to free memory?" problem in lock-free data structures. The idea: divide time into epochs. An object is only freed when all threads have advanced past the epoch in which it was removed.

You probably won't use this directly. But it powers crossbeam's lock-free queues and deques under the hood.

## Crossbeam Deque: Work-Stealing Queues

This is what Rayon uses internally:

```rust
use crossbeam_deque::{Injector, Stealer, Worker};

fn main() {
    let injector = Injector::new();
    let worker = Worker::new_fifo();
    let stealer = worker.stealer();

    // Inject global tasks
    for i in 0..10 {
        injector.push(i);
    }

    // Worker pops from its local queue
    // If empty, steals from the injector or other workers
    loop {
        match worker.pop() {
            Some(task) => println!("Local task: {}", task),
            None => {
                // Try to steal from the global injector
                match injector.steal_batch_and_pop(&worker) {
                    crossbeam_deque::Steal::Success(task) => {
                        println!("Stolen task: {}", task);
                    }
                    crossbeam_deque::Steal::Empty => break,
                    crossbeam_deque::Steal::Retry => continue,
                }
            }
        }
    }
}
```

Work-stealing deques are the backbone of efficient task schedulers. Each thread has a local deque. It pushes and pops from one end (LIFO for cache locality). When empty, it steals from other threads' deques (FIFO for fairness).

## Crossbeam Queue: Lock-Free Queues

```rust
use crossbeam_queue::{ArrayQueue, SegQueue};
use std::sync::Arc;
use std::thread;

fn main() {
    // ArrayQueue — bounded, lock-free
    let q = Arc::new(ArrayQueue::new(100));
    let mut handles = vec![];

    for i in 0..4 {
        let q = Arc::clone(&q);
        handles.push(thread::spawn(move || {
            for j in 0..25 {
                q.push(i * 25 + j).unwrap();
            }
        }));
    }

    for h in handles {
        h.join().unwrap();
    }

    let mut results = vec![];
    while let Some(val) = q.pop() {
        results.push(val);
    }
    println!("ArrayQueue: {} items", results.len()); // 100

    // SegQueue — unbounded, lock-free
    let sq = Arc::new(SegQueue::new());
    let sq2 = Arc::clone(&sq);

    let producer = thread::spawn(move || {
        for i in 0..1000 {
            sq2.push(i);
        }
    });

    producer.join().unwrap();
    println!("SegQueue length: {}", sq.len());
}
```

`ArrayQueue` is bounded (returns `Err` when full). `SegQueue` is unbounded (grows as needed). Both are lock-free and safe for multiple producers and consumers.

## When to Reach for Crossbeam

- **Need MPMC channels?** — crossbeam-channel
- **Need `select!` on multiple channels?** — crossbeam-channel
- **Need lock-free queues?** — crossbeam-queue
- **Building a task scheduler?** — crossbeam-deque
- **Need a sharded lock?** — crossbeam-utils
- **Building lock-free data structures?** — crossbeam-epoch

For most applications, crossbeam-channel is the star. It's faster than std, more flexible (MPMC, select), and just as ergonomic. I default to crossbeam channels over std in any project that has crossbeam as a dependency anyway.

---

Next — building worker pools for bounded concurrency.
