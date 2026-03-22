---
title: "Lesson 24: Testing Concurrent Code — Loom and beyond"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 24
keywords: [Rust, rust, concurrency, testing, loom, concurrent testing, thread testing, model checking]
tags: [Rust tutorial, rust, concurrency]
date: '2024-12-21T09:15:00.000Z'
---

I spent a full week writing tests for a lock-free queue. Ran them a thousand times — all green. Shipped it. Two days later, a production crash. A race condition that occurred roughly once every 50,000 operations under specific timing. My tests never hit it because standard testing can't explore all possible thread interleavings. That's when I found Loom.

Testing concurrent code is fundamentally different from testing sequential code. A test that passes doesn't mean the code is correct — it means the code was correct *for that particular thread scheduling*. Run the same test with different timing and you might get a different result.

## The Problem: Non-Determinism

```rust
use std::sync::{Arc, Mutex};
use std::thread;

fn buggy_counter() -> u64 {
    let counter = Arc::new(Mutex::new(0u64));
    let mut handles = vec![];

    for _ in 0..4 {
        let counter = Arc::clone(&counter);
        handles.push(thread::spawn(move || {
            for _ in 0..1000 {
                *counter.lock().unwrap() += 1;
            }
        }));
    }

    for h in handles {
        h.join().unwrap();
    }

    *counter.lock().unwrap()
}

#[test]
fn test_counter() {
    assert_eq!(buggy_counter(), 4000); // passes — because Mutex makes it correct
}
```

This test passes. But what if the code had a bug? What if we used a broken lock-free counter instead? The test might still pass 999 out of 1000 runs, because the OS scheduler happened to avoid the problematic interleaving.

## Strategy 1: Stress Testing

Run the test many times and hope to hit the bug:

```rust
#[test]
fn stress_test_counter() {
    for trial in 0..1000 {
        let result = buggy_counter();
        assert_eq!(result, 4000, "Failed on trial {}", trial);
    }
}
```

Better than running once. But fundamentally limited — you're relying on the OS scheduler to create problematic interleavings, which it might never do.

## Strategy 2: Loom — Exhaustive Interleaving Testing

Loom is a model checker for Rust concurrency. Instead of running threads on the OS, Loom simulates them and systematically explores *every possible* interleaving of operations.

```toml
[dev-dependencies]
loom = "0.7"
```

```rust
// Use loom's types instead of std
#[cfg(test)]
mod tests {
    use loom::sync::Arc;
    use loom::sync::atomic::{AtomicUsize, Ordering};
    use loom::thread;

    #[test]
    fn test_atomic_counter() {
        loom::model(|| {
            let counter = Arc::new(AtomicUsize::new(0));

            let counter1 = Arc::clone(&counter);
            let t1 = thread::spawn(move || {
                counter1.fetch_add(1, Ordering::SeqCst);
            });

            let counter2 = Arc::clone(&counter);
            let t2 = thread::spawn(move || {
                counter2.fetch_add(1, Ordering::SeqCst);
            });

            t1.join().unwrap();
            t2.join().unwrap();

            assert_eq!(counter.load(Ordering::SeqCst), 2);
        });
    }
}
```

`loom::model` runs the closure hundreds or thousands of times, each time choosing a different interleaving of thread operations. If any interleaving leads to an assertion failure, Loom reports it.

### Finding Real Bugs with Loom

Here's a broken "check-then-act" pattern:

```rust
#[cfg(test)]
mod tests {
    use loom::sync::Arc;
    use loom::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
    use loom::thread;

    // Broken: race between check and increment
    fn broken_increment_if_below(
        counter: &AtomicUsize,
        flag: &AtomicBool,
        limit: usize,
    ) {
        let current = counter.load(Ordering::SeqCst);
        if current < limit {
            // Another thread could increment between check and store
            counter.store(current + 1, Ordering::SeqCst);
            if current + 1 >= limit {
                flag.store(true, Ordering::SeqCst);
            }
        }
    }

    #[test]
    fn test_broken_increment() {
        loom::model(|| {
            let counter = Arc::new(AtomicUsize::new(0));
            let flag = Arc::new(AtomicBool::new(false));
            let limit = 2;

            let c1 = Arc::clone(&counter);
            let f1 = Arc::clone(&flag);
            let t1 = thread::spawn(move || {
                broken_increment_if_below(&c1, &f1, limit);
            });

            let c2 = Arc::clone(&counter);
            let f2 = Arc::clone(&flag);
            let t2 = thread::spawn(move || {
                broken_increment_if_below(&c2, &f2, limit);
            });

            t1.join().unwrap();
            t2.join().unwrap();

            let final_count = counter.load(Ordering::SeqCst);
            // BUG: both threads read 0, both increment to 1
            // counter ends up at 1 instead of 2
            // Or both read 0, both store 1, counter is 1
            assert!(
                final_count <= limit,
                "Counter {} exceeded limit {}",
                final_count,
                limit
            );
        });
    }
}
```

Loom will find the interleaving where both threads read the same value and overwrite each other's increment. Standard tests might never hit this.

### The Fixed Version

```rust
fn correct_increment_if_below(
    counter: &loom::sync::atomic::AtomicUsize,
    flag: &loom::sync::atomic::AtomicBool,
    limit: usize,
) {
    use loom::sync::atomic::Ordering;

    loop {
        let current = counter.load(Ordering::SeqCst);
        if current >= limit {
            return;
        }
        match counter.compare_exchange(
            current,
            current + 1,
            Ordering::SeqCst,
            Ordering::SeqCst,
        ) {
            Ok(_) => {
                if current + 1 >= limit {
                    flag.store(true, Ordering::SeqCst);
                }
                return;
            }
            Err(_) => continue, // retry
        }
    }
}
```

CAS ensures the increment is atomic with respect to the check. Loom verifies this is correct across all possible interleavings.

## Strategy 3: Property-Based Testing with Proptest

Generate random operations and check invariants:

```rust
#[cfg(test)]
mod tests {
    use std::sync::{Arc, Mutex};
    use std::thread;

    struct ConcurrentSet {
        data: Mutex<Vec<i32>>,
    }

    impl ConcurrentSet {
        fn new() -> Self {
            ConcurrentSet {
                data: Mutex::new(Vec::new()),
            }
        }

        fn insert(&self, val: i32) {
            let mut data = self.data.lock().unwrap();
            if !data.contains(&val) {
                data.push(val);
            }
        }

        fn contains(&self, val: &i32) -> bool {
            self.data.lock().unwrap().contains(val)
        }

        fn len(&self) -> usize {
            self.data.lock().unwrap().len()
        }
    }

    #[test]
    fn test_concurrent_set_invariants() {
        for _ in 0..100 {
            let set = Arc::new(ConcurrentSet::new());
            let mut handles = vec![];

            // Each thread inserts the same set of values
            for _ in 0..4 {
                let set = Arc::clone(&set);
                handles.push(thread::spawn(move || {
                    for val in 0..100 {
                        set.insert(val);
                    }
                }));
            }

            for h in handles {
                h.join().unwrap();
            }

            // Invariant: set contains exactly 100 unique values
            assert_eq!(set.len(), 100);

            // Invariant: all values 0..100 are present
            for val in 0..100 {
                assert!(set.contains(&val), "Missing value: {}", val);
            }
        }
    }
}
```

## Strategy 4: Deterministic Scheduling

You can add yield points to make bugs more likely to manifest:

```rust
use std::sync::{Arc, Mutex};
use std::thread;

#[cfg(test)]
fn yield_point() {
    thread::yield_now();
    // In debug builds, also sleep briefly to vary timing
    #[cfg(debug_assertions)]
    std::thread::sleep(std::time::Duration::from_micros(1));
}

#[cfg(not(test))]
fn yield_point() {}

fn guarded_operation(mutex: &Mutex<Vec<i32>>) {
    yield_point(); // make interleaving more likely
    let mut data = mutex.lock().unwrap();
    yield_point();
    data.push(42);
    yield_point();
}
```

## Strategy 5: ThreadSanitizer for Unsafe Code

If you have `unsafe` concurrent code, ThreadSanitizer catches data races at runtime:

```bash
RUSTFLAGS="-Z sanitizer=thread" cargo +nightly test -- --test-threads=1
```

```rust
// This has a data race that TSan will catch
#[test]
fn test_with_tsan() {
    use std::cell::UnsafeCell;
    use std::sync::Arc;
    use std::thread;

    let data = Arc::new(UnsafeCell::new(0u64));

    let d1 = Arc::clone(&data);
    let t1 = thread::spawn(move || {
        unsafe { *d1.get() = 42; }
    });

    let d2 = Arc::clone(&data);
    let t2 = thread::spawn(move || {
        unsafe { *d2.get() = 43; }
    });

    t1.join().unwrap();
    t2.join().unwrap();
    // TSan reports: WARNING: ThreadSanitizer: data race
}
```

## Testing Patterns for Common Concurrent Code

### Testing a Channel-Based Worker

```rust
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

struct Worker {
    sender: mpsc::Sender<String>,
    handle: Option<thread::JoinHandle<Vec<String>>>,
}

impl Worker {
    fn new() -> Self {
        let (tx, rx) = mpsc::channel();
        let handle = thread::spawn(move || {
            let mut results = vec![];
            while let Ok(msg) = rx.recv() {
                results.push(msg.to_uppercase());
            }
            results
        });
        Worker {
            sender: tx,
            handle: Some(handle),
        }
    }

    fn process(&self, item: String) {
        self.sender.send(item).unwrap();
    }

    fn finish(mut self) -> Vec<String> {
        drop(self.sender);
        self.handle.take().unwrap().join().unwrap()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_worker_processes_all_items() {
        let worker = Worker::new();

        let items = vec!["hello", "world", "foo"];
        for item in &items {
            worker.process(item.to_string());
        }

        let results = worker.finish();
        assert_eq!(results.len(), 3);
        assert!(results.contains(&"HELLO".to_string()));
        assert!(results.contains(&"WORLD".to_string()));
        assert!(results.contains(&"FOO".to_string()));
    }

    #[test]
    fn test_worker_handles_empty_input() {
        let worker = Worker::new();
        let results = worker.finish();
        assert!(results.is_empty());
    }
}
```

### Testing with Timeouts

Concurrent tests can hang. Always add timeouts:

```rust
#[cfg(test)]
mod tests {
    use std::sync::{Arc, Barrier};
    use std::thread;
    use std::time::{Duration, Instant};

    #[test]
    fn test_with_timeout() {
        let start = Instant::now();
        let timeout = Duration::from_secs(5);

        let barrier = Arc::new(Barrier::new(2));
        let b = Arc::clone(&barrier);

        let handle = thread::spawn(move || {
            b.wait();
            42
        });

        barrier.wait();
        let result = handle.join().unwrap();

        assert_eq!(result, 42);
        assert!(
            start.elapsed() < timeout,
            "Test took too long: {:?}",
            start.elapsed()
        );
    }
}
```

## The Testing Hierarchy

1. **Unit tests with single-threaded logic** — Test the core logic without threads. If your concurrent component has complex logic, extract it into a pure function and test it separately.

2. **Loom tests** — For lock-free and atomic-based code. Exhaustive interleaving coverage.

3. **Stress tests** — Run concurrent operations many times to shake out timing-dependent bugs.

4. **ThreadSanitizer** — For unsafe concurrent code. Catches data races that safe Rust prevents.

5. **Integration tests under load** — The closest to production conditions. Use realistic data volumes and access patterns.

My testing ratio: 60% unit tests (pure logic), 20% Loom tests (correctness of concurrent primitives), 15% stress tests (integration), 5% TSan (unsafe code). This catches most bugs before production.

The uncomfortable truth: you can never be 100% sure concurrent code is correct. But these tools get you close enough to sleep at night.

---

Next, the final lesson — production concurrency architecture, tying everything together.
