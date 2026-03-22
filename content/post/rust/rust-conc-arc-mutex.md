---
title: "Lesson 6: Arc<Mutex<T>> — The shared mutable state pattern"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 6
keywords: [Rust, rust, concurrency, Arc, Mutex, shared state, thread safety, Arc Mutex pattern]
tags: [Rust tutorial, rust, concurrency]
date: '2024-11-15T10:30:00.000Z'
---

I remember staring at `Arc<Mutex<HashMap<String, Vec<u8>>>>` in a codebase and thinking "this is the ugliest type I've ever seen." Three months later, after debugging a similar system in Go that had zero type safety around its concurrent map access, I came crawling back to Rust's ugly-but-correct approach.

The `Arc<Mutex<T>>` pattern is everywhere in Rust concurrent code. Understanding *why* it exists — not just how to type it — is the key.

## The Problem: Mutex Alone Isn't Enough

Last lesson we used `Mutex` with scoped threads. But what about `thread::spawn`? The spawned thread needs `'static` data — it might outlive the scope where the mutex was created.

```rust
use std::sync::Mutex;
use std::thread;

fn main() {
    let data = Mutex::new(vec![1, 2, 3]);

    // THIS WON'T COMPILE
    let handle = thread::spawn(|| {
        let mut d = data.lock().unwrap();
        d.push(4);
    });

    handle.join().unwrap();
}
```

We can't borrow `data` in the closure because `thread::spawn` requires `'static`. We could `move` it — but then the main thread loses access. What if multiple threads need to share the same mutex?

You can't clone a `Mutex`. And you can't have multiple owners of the same value in Rust. Unless...

## Arc: Atomic Reference Counting

`Arc<T>` (Atomic Reference Counted) gives you shared ownership across threads. It's like `Rc<T>` but uses atomic operations for the reference count, making it safe to share between threads.

```rust
use std::sync::Arc;
use std::thread;

fn main() {
    let data = Arc::new(vec![1, 2, 3]);

    let data2 = Arc::clone(&data);
    let handle = thread::spawn(move || {
        println!("Thread sees: {:?}", data2);
    });

    println!("Main sees: {:?}", data);
    handle.join().unwrap();
}
```

`Arc::clone` doesn't clone the data — it increments the reference count. Both `data` and `data2` point to the same heap-allocated `Vec`. When the last `Arc` is dropped, the data is freed.

But `Arc` alone gives you shared *immutable* access. You can read but not write. For mutable access, you need a `Mutex` inside the `Arc`.

## Arc<Mutex<T>>: The Full Pattern

```rust
use std::sync::{Arc, Mutex};
use std::thread;

fn main() {
    let counter = Arc::new(Mutex::new(0));
    let mut handles = vec![];

    for _ in 0..10 {
        let counter = Arc::clone(&counter);
        let handle = thread::spawn(move || {
            let mut num = counter.lock().unwrap();
            *num += 1;
        });
        handles.push(handle);
    }

    for handle in handles {
        handle.join().unwrap();
    }

    println!("Result: {}", *counter.lock().unwrap()); // always 10
}
```

Here's what's happening layer by layer:

1. `Mutex::new(0)` — wraps the integer, providing exclusive access
2. `Arc::new(...)` — wraps the mutex, providing shared ownership
3. `Arc::clone(...)` — creates another reference to the same mutex
4. `move` closure — takes ownership of the cloned Arc
5. `.lock().unwrap()` — acquires the mutex, returns a guard
6. `*num += 1` — modifies the data through the guard

Each thread owns an `Arc` pointing to the same `Mutex<i32>`. The `Arc` keeps the `Mutex` alive as long as any thread holds a reference. The `Mutex` ensures only one thread touches the integer at a time.

## Real-World Example: Shared Cache

```rust
use std::sync::{Arc, Mutex};
use std::collections::HashMap;
use std::thread;

type Cache = Arc<Mutex<HashMap<String, String>>>;

fn lookup(cache: &Cache, key: &str) -> Option<String> {
    cache.lock().unwrap().get(key).cloned()
}

fn insert(cache: &Cache, key: String, value: String) {
    cache.lock().unwrap().insert(key, value);
}

fn main() {
    let cache: Cache = Arc::new(Mutex::new(HashMap::new()));
    let mut handles = vec![];

    // Writers
    for i in 0..5 {
        let cache = Arc::clone(&cache);
        handles.push(thread::spawn(move || {
            let key = format!("key-{}", i);
            let value = format!("value-{}", i * 10);
            insert(&cache, key, value);
        }));
    }

    // Wait for writers
    for h in handles {
        h.join().unwrap();
    }

    // Readers
    let mut handles = vec![];
    for i in 0..5 {
        let cache = Arc::clone(&cache);
        handles.push(thread::spawn(move || {
            let key = format!("key-{}", i);
            match lookup(&cache, &key) {
                Some(v) => println!("Found {}: {}", key, v),
                None => println!("{} not found", key),
            }
        }));
    }

    for h in handles {
        h.join().unwrap();
    }
}
```

Type alias `Cache` keeps the signature readable. Without it, every function would have `Arc<Mutex<HashMap<String, String>>>` in its signature, and your eyes would glaze over.

## Arc<RwLock<T>> for Read-Heavy Workloads

If reads vastly outnumber writes, swap `Mutex` for `RwLock`:

```rust
use std::sync::{Arc, RwLock};
use std::collections::HashMap;
use std::thread;

fn main() {
    let config = Arc::new(RwLock::new(HashMap::from([
        ("timeout".to_string(), 30),
        ("retries".to_string(), 3),
        ("pool_size".to_string(), 10),
    ])));

    let mut handles = vec![];

    // Many concurrent readers
    for i in 0..20 {
        let config = Arc::clone(&config);
        handles.push(thread::spawn(move || {
            let cfg = config.read().unwrap();
            let timeout = cfg.get("timeout").unwrap();
            println!("Reader {}: timeout = {}", i, timeout);
        }));
    }

    // One writer
    {
        let config = Arc::clone(&config);
        handles.push(thread::spawn(move || {
            let mut cfg = config.write().unwrap();
            cfg.insert("timeout".to_string(), 60);
            println!("Writer updated timeout");
        }));
    }

    for h in handles {
        h.join().unwrap();
    }
}
```

All 20 readers can run simultaneously. The writer waits for all readers to finish, then gets exclusive access. In practice, `RwLock` only wins when your read critical section is non-trivial — for quick map lookups, the overhead of managing reader counts can negate the benefit.

## The Wrong Way: Multiple Arcs to Separate Mutexes

A mistake I see from newcomers — creating separate Arcs instead of sharing one:

```rust
use std::sync::{Arc, Mutex};
use std::thread;

fn main() {
    // WRONG — these are two independent mutexes protecting two independent values
    let a = Arc::new(Mutex::new(0));
    let b = Arc::new(Mutex::new(0));

    // If you need to update both atomically, you need them in the same Mutex
    let both = Arc::new(Mutex::new((0, 0)));

    let both_clone = Arc::clone(&both);
    thread::spawn(move || {
        let mut guard = both_clone.lock().unwrap();
        guard.0 += 1;
        guard.1 += 1;
        // Both updated atomically — single lock protects both values
    });
}
```

If two values must always be consistent with each other, they belong under the same lock. Separate mutexes mean separate critical sections, and the values can be observed in an inconsistent state between the two locks.

## Reducing Lock Contention

The biggest performance problem with `Arc<Mutex<T>>` is contention — threads waiting for each other. Some strategies:

### Sharded Locking

Instead of one mutex for the whole map, use multiple mutexes for different key ranges:

```rust
use std::sync::{Arc, Mutex};
use std::collections::HashMap;
use std::thread;

const SHARD_COUNT: usize = 16;

struct ShardedMap {
    shards: Vec<Mutex<HashMap<String, String>>>,
}

impl ShardedMap {
    fn new() -> Self {
        let shards = (0..SHARD_COUNT)
            .map(|_| Mutex::new(HashMap::new()))
            .collect();
        ShardedMap { shards }
    }

    fn shard_index(&self, key: &str) -> usize {
        let mut hash: usize = 0;
        for byte in key.bytes() {
            hash = hash.wrapping_mul(31).wrapping_add(byte as usize);
        }
        hash % SHARD_COUNT
    }

    fn insert(&self, key: String, value: String) {
        let idx = self.shard_index(&key);
        self.shards[idx].lock().unwrap().insert(key, value);
    }

    fn get(&self, key: &str) -> Option<String> {
        let idx = self.shard_index(key);
        self.shards[idx].lock().unwrap().get(key).cloned()
    }
}

fn main() {
    let map = Arc::new(ShardedMap::new());
    let mut handles = vec![];

    for i in 0..100 {
        let map = Arc::clone(&map);
        handles.push(thread::spawn(move || {
            let key = format!("key-{}", i);
            map.insert(key.clone(), format!("val-{}", i));
            map.get(&key)
        }));
    }

    for h in handles {
        let result = h.join().unwrap();
        if let Some(v) = result {
            // each thread sees its own insert
            assert!(v.starts_with("val-"));
        }
    }

    println!("All insertions complete");
}
```

With 16 shards, up to 16 threads can write simultaneously (to different shards) without contention. This is essentially how Java's `ConcurrentHashMap` works under the hood.

### Minimize Critical Section

```rust
use std::sync::{Arc, Mutex};
use std::thread;

fn main() {
    let results = Arc::new(Mutex::new(Vec::new()));
    let mut handles = vec![];

    for i in 0..10 {
        let results = Arc::clone(&results);
        handles.push(thread::spawn(move || {
            // Do expensive work OUTSIDE the lock
            let computed = expensive_work(i);

            // Only hold the lock for the push
            results.lock().unwrap().push(computed);
        }));
    }

    for h in handles {
        h.join().unwrap();
    }

    println!("Results: {:?}", results.lock().unwrap());
}

fn expensive_work(n: i32) -> i32 {
    std::thread::sleep(std::time::Duration::from_millis(100));
    n * n
}
```

Do the heavy computation outside the lock. Only hold the lock for the minimal operation needed.

## When Arc<Mutex<T>> Is the Wrong Choice

Not everything needs shared mutable state. Consider alternatives:

- **Channels**: When data flows one direction, message passing is cleaner
- **Atomics**: For simple counters, flags, and statistics (lesson 7)
- **Immutable shared data**: `Arc<T>` without `Mutex` when you only read
- **Thread-local storage**: When each thread needs its own copy (lesson 19)

`Arc<Mutex<T>>` is the swiss army knife. It works for everything. But specialized tools often work better for specific jobs.

---

Next — atomic operations for when a full mutex is overkill.
