---
title: "Lesson 17: Lock-Free Data Structures in Rust — Beyond mutexes"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 17
keywords: [Rust, rust, concurrency, lock-free, wait-free, CAS, compare-and-swap, concurrent data structures]
tags: [Rust tutorial, rust, concurrency]
date: '2024-12-07T08:50:00.000Z'
---

A few years ago I was profiling a metrics collection service and found that 60% of the CPU time was spent on mutex contention. Thirty-two threads, one mutex protecting a counter map. The actual "work" — incrementing counters — took nanoseconds. The locking overhead was three orders of magnitude more expensive than the operation it protected.

That's when lock-free data structures become worth the complexity. When the lock is the bottleneck, remove the lock.

## Lock-Free vs Wait-Free vs Obstruction-Free

Terminology matters here:

- **Lock-free**: At least one thread always makes progress. Individual threads might retry, but the system as a whole never stalls.
- **Wait-free**: Every thread makes progress in a bounded number of steps. The strongest guarantee.
- **Obstruction-free**: A thread makes progress if no other threads are active. The weakest useful guarantee.

Most practical "lock-free" data structures are lock-free, not wait-free. Wait-free is theoretically nicer but often slower in practice due to the overhead of guaranteeing per-thread progress.

## A Lock-Free Stack

The simplest lock-free data structure — a Treiber stack. Push and pop use CAS:

```rust
use std::sync::atomic::{AtomicPtr, Ordering};
use std::ptr;

struct Node<T> {
    data: T,
    next: *mut Node<T>,
}

pub struct LockFreeStack<T> {
    head: AtomicPtr<Node<T>>,
}

impl<T> LockFreeStack<T> {
    pub fn new() -> Self {
        LockFreeStack {
            head: AtomicPtr::new(ptr::null_mut()),
        }
    }

    pub fn push(&self, data: T) {
        let new_node = Box::into_raw(Box::new(Node {
            data,
            next: ptr::null_mut(),
        }));

        loop {
            let old_head = self.head.load(Ordering::Relaxed);
            unsafe { (*new_node).next = old_head; }

            match self.head.compare_exchange_weak(
                old_head,
                new_node,
                Ordering::Release,
                Ordering::Relaxed,
            ) {
                Ok(_) => return,
                Err(_) => continue, // someone else pushed first, retry
            }
        }
    }

    pub fn pop(&self) -> Option<T> {
        loop {
            let old_head = self.head.load(Ordering::Acquire);
            if old_head.is_null() {
                return None;
            }

            let next = unsafe { (*old_head).next };

            match self.head.compare_exchange_weak(
                old_head,
                next,
                Ordering::Relaxed,
                Ordering::Relaxed,
            ) {
                Ok(_) => {
                    let node = unsafe { Box::from_raw(old_head) };
                    return Some(node.data);
                }
                Err(_) => continue,
            }
        }
    }
}

impl<T> Drop for LockFreeStack<T> {
    fn drop(&mut self) {
        while self.pop().is_some() {}
    }
}

// Safety: LockFreeStack can be sent between threads if T can
unsafe impl<T: Send> Send for LockFreeStack<T> {}
unsafe impl<T: Send> Sync for LockFreeStack<T> {}

fn main() {
    use std::sync::Arc;
    use std::thread;

    let stack = Arc::new(LockFreeStack::new());
    let mut handles = vec![];

    // Pushers
    for i in 0..4 {
        let stack = Arc::clone(&stack);
        handles.push(thread::spawn(move || {
            for j in 0..1000 {
                stack.push(i * 1000 + j);
            }
        }));
    }

    for h in handles {
        h.join().unwrap();
    }

    let mut count = 0;
    while stack.pop().is_some() {
        count += 1;
    }
    println!("Popped {} items", count); // 4000
}
```

**Warning**: This implementation has the ABA problem. Thread A reads head → X, gets preempted. Thread B pops X, pushes Y, pushes X back. Thread A wakes up, sees head is still X, CAS succeeds — but the stack state has changed. For production use, you need epoch-based reclamation (like crossbeam-epoch) to handle safe memory reclamation.

## The ABA Problem

This is the fundamental hazard of lock-free programming with CAS:

```
Thread A: reads head = X (with X.next = Y)
Thread B: pops X
Thread B: pops Y
Thread B: pushes X back (with X.next = Z, some new node)
Thread A: CAS(head, X, Y) succeeds — but Y was freed!
```

Thread A thinks nothing changed because the pointer is the same. But the node it read as `next` is gone.

Solutions:
1. **Tagged pointers** — Increment a counter with each CAS. Compare pointer AND tag.
2. **Epoch-based reclamation** — Defer freeing memory until all threads have moved past the current epoch (crossbeam-epoch).
3. **Hazard pointers** — Each thread announces which pointers it's currently using. Memory is only freed when no thread has it marked.

For Rust, crossbeam-epoch is the standard approach. Building your own epoch system from scratch is an exercise in humility.

## Using Crossbeam's Lock-Free Structures

Instead of rolling your own (which is almost always wrong), use battle-tested implementations:

```rust
use crossbeam_queue::{ArrayQueue, SegQueue};
use std::sync::Arc;
use std::thread;

fn main() {
    // SegQueue: unbounded lock-free MPMC queue
    let queue = Arc::new(SegQueue::new());
    let mut handles = vec![];

    // Multiple producers
    for id in 0..4 {
        let q = Arc::clone(&queue);
        handles.push(thread::spawn(move || {
            for i in 0..10_000 {
                q.push(id * 10_000 + i);
            }
        }));
    }

    // Multiple consumers
    for id in 0..4 {
        let q = Arc::clone(&queue);
        handles.push(thread::spawn(move || {
            let mut count = 0;
            loop {
                match q.pop() {
                    Some(_val) => count += 1,
                    None => {
                        // Queue might be temporarily empty
                        thread::yield_now();
                        if q.is_empty() {
                            break;
                        }
                    }
                }
            }
            println!("Consumer {} processed {} items", id, count);
        }));
    }

    for h in handles {
        h.join().unwrap();
    }
}
```

### ArrayQueue for Bounded Scenarios

```rust
use crossbeam_queue::ArrayQueue;
use std::sync::Arc;
use std::thread;

fn main() {
    let queue = Arc::new(ArrayQueue::new(1024));
    let mut handles = vec![];

    // Producer
    let q = Arc::clone(&queue);
    handles.push(thread::spawn(move || {
        let mut sent = 0;
        for i in 0..10_000 {
            loop {
                match q.push(i) {
                    Ok(()) => {
                        sent += 1;
                        break;
                    }
                    Err(_) => {
                        // Queue full — spin
                        thread::yield_now();
                    }
                }
            }
        }
        println!("Sent {} items", sent);
    }));

    // Consumer
    let q = Arc::clone(&queue);
    handles.push(thread::spawn(move || {
        let mut received = 0;
        let mut empty_count = 0;
        loop {
            match q.pop() {
                Some(_) => {
                    received += 1;
                    empty_count = 0;
                }
                None => {
                    empty_count += 1;
                    if empty_count > 1000 {
                        break; // probably done
                    }
                    thread::yield_now();
                }
            }
        }
        println!("Received {} items", received);
    }));

    for h in handles {
        h.join().unwrap();
    }
}
```

## Lock-Free Atomics Patterns

Beyond full data structures, atomic operations enable useful lock-free patterns:

### Sequence Lock (SeqLock)

For one writer, many readers. Readers never block. Extremely fast reads:

```rust
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::thread;

struct SeqLock<T: Copy> {
    sequence: AtomicU64,
    data: std::cell::UnsafeCell<T>,
}

unsafe impl<T: Copy + Send> Send for SeqLock<T> {}
unsafe impl<T: Copy + Send> Sync for SeqLock<T> {}

impl<T: Copy> SeqLock<T> {
    fn new(data: T) -> Self {
        SeqLock {
            sequence: AtomicU64::new(0),
            data: std::cell::UnsafeCell::new(data),
        }
    }

    /// Writer: must have exclusive access (only one writer)
    fn write(&self, value: T) {
        // Odd sequence = write in progress
        self.sequence.fetch_add(1, Ordering::Release);
        unsafe { *self.data.get() = value; }
        self.sequence.fetch_add(1, Ordering::Release);
    }

    /// Reader: lock-free, wait-free. May retry on concurrent write.
    fn read(&self) -> T {
        loop {
            let seq1 = self.sequence.load(Ordering::Acquire);
            if seq1 & 1 != 0 {
                // Write in progress, retry
                std::hint::spin_loop();
                continue;
            }

            let value = unsafe { *self.data.get() };

            let seq2 = self.sequence.load(Ordering::Acquire);
            if seq1 == seq2 {
                return value; // consistent read
            }
            // Sequence changed during read, retry
            std::hint::spin_loop();
        }
    }
}

fn main() {
    let lock = Arc::new(SeqLock::new((0u64, 0u64)));

    // Single writer
    let writer_lock = Arc::clone(&lock);
    let writer = thread::spawn(move || {
        for i in 0..1_000_000u64 {
            writer_lock.write((i, i * 2));
        }
    });

    // Many readers
    let mut readers = vec![];
    for _ in 0..4 {
        let lock = Arc::clone(&lock);
        readers.push(thread::spawn(move || {
            let mut reads = 0u64;
            for _ in 0..1_000_000 {
                let (a, b) = lock.read();
                assert_eq!(b, a * 2, "Inconsistent read: {} {}", a, b);
                reads += 1;
            }
            reads
        }));
    }

    writer.join().unwrap();
    for r in readers {
        let reads = r.join().unwrap();
        println!("Reader did {} consistent reads", reads);
    }
}
```

SeqLock is perfect for things like timestamps, coordinates, or small structs that are updated by one thread and read by many. Readers never block the writer. The writer never blocks readers. Reads just retry if they caught a write mid-flight.

## When Lock-Free Is Actually Worth It

Be honest with yourself about whether you need this:

**Lock-free wins when:**
- Very high contention (many threads, short critical sections)
- Real-time constraints (can't afford to block on a lock)
- The data structure is simple (stack, queue, counter)

**Lock-free loses when:**
- Low contention (mutex is basically free when uncontended)
- Complex operations (multi-step updates are nightmarish without locks)
- Correctness is hard to verify (lock-free code is subtle and error-prone)

I've seen teams spend weeks building lock-free data structures that performed 10% better than a `Mutex<HashMap>`. The code was unreadable, had two bugs found in production, and nobody could maintain it.

My rule: start with `Mutex`. Profile. If mutex contention is *actually* your bottleneck (not just something you worry about), *then* consider lock-free alternatives. And when you do, use crossbeam's battle-tested implementations, not your own.

---

Next — debugging deadlocks and data races with real tools.
