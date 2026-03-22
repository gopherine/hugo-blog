---
title: "Lesson 15: Condvar — Waiting for conditions"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 15
keywords: [Rust, rust, concurrency, Condvar, condition variable, wait, notify, signaling]
tags: [Rust tutorial, rust, concurrency]
date: '2024-12-03T14:40:00.000Z'
---

Early in my career, I wrote a producer-consumer queue using a mutex and a busy-wait loop. The consumer would lock the mutex, check if there's data, unlock, sleep for 10 milliseconds, and repeat. It worked, but it burned CPU doing nothing and had up to 10ms of latency on every message. My tech lead pointed me to condition variables, and the latency dropped to microseconds while CPU usage went to near zero.

Condition variables are the standard mechanism for "wait until something is true." They let a thread sleep efficiently until another thread signals that the world has changed.

## The Problem: Busy Waiting

```rust
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

fn main() {
    let queue = Arc::new(Mutex::new(Vec::<i32>::new()));

    let producer_queue = Arc::clone(&queue);
    let producer = thread::spawn(move || {
        for i in 0..5 {
            thread::sleep(Duration::from_millis(500));
            producer_queue.lock().unwrap().push(i);
            println!("Produced: {}", i);
        }
    });

    // BAD: busy waiting — burns CPU, adds latency
    let consumer_queue = Arc::clone(&queue);
    let consumer = thread::spawn(move || {
        let mut consumed = 0;
        while consumed < 5 {
            let mut q = consumer_queue.lock().unwrap();
            if let Some(val) = q.pop() {
                println!("Consumed: {}", val);
                consumed += 1;
            }
            drop(q);
            thread::sleep(Duration::from_millis(10)); // wasteful polling
        }
    });

    producer.join().unwrap();
    consumer.join().unwrap();
}
```

Two problems: the consumer holds the lock while checking (blocking the producer), and the `sleep(10ms)` adds unnecessary latency. If the producer pushes an item right after the consumer sleeps, it won't be processed for up to 10ms.

## The Solution: Condvar

A `Condvar` (condition variable) pairs with a `Mutex`. The consumer *waits* on the condvar, releasing the mutex and sleeping. When the producer pushes data, it *notifies* the condvar, waking the consumer.

```rust
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::Duration;

fn main() {
    let pair = Arc::new((Mutex::new(Vec::<i32>::new()), Condvar::new()));

    let producer_pair = Arc::clone(&pair);
    let producer = thread::spawn(move || {
        for i in 0..5 {
            thread::sleep(Duration::from_millis(500));
            let (lock, cvar) = &*producer_pair;
            lock.lock().unwrap().push(i);
            cvar.notify_one(); // wake one waiting thread
            println!("Produced: {}", i);
        }
    });

    let consumer_pair = Arc::clone(&pair);
    let consumer = thread::spawn(move || {
        let (lock, cvar) = &*consumer_pair;
        let mut consumed = 0;
        while consumed < 5 {
            let mut queue = lock.lock().unwrap();
            // wait() atomically releases the mutex and sleeps
            // When woken, it re-acquires the mutex
            while queue.is_empty() {
                queue = cvar.wait(queue).unwrap();
            }
            while let Some(val) = queue.pop() {
                println!("Consumed: {}", val);
                consumed += 1;
            }
        }
    });

    producer.join().unwrap();
    consumer.join().unwrap();
}
```

The magic is in `cvar.wait(queue)`:

1. Atomically releases the mutex and puts the thread to sleep
2. When `notify_one()` or `notify_all()` is called, the thread wakes up
3. It re-acquires the mutex before `wait()` returns

No CPU burned. No polling delay. The consumer wakes up almost instantly when data is available.

## Spurious Wakeups

A critical detail: `wait()` can return *without* being notified. This is called a spurious wakeup, and it happens because of how OS-level condition variables work. That's why you must always check the condition in a loop:

```rust
// WRONG — might consume empty queue on spurious wakeup
let mut queue = lock.lock().unwrap();
queue = cvar.wait(queue).unwrap();
let val = queue.pop().unwrap(); // PANIC if spurious wakeup

// RIGHT — loop until the condition is actually true
let mut queue = lock.lock().unwrap();
while queue.is_empty() {
    queue = cvar.wait(queue).unwrap();
}
let val = queue.pop().unwrap(); // safe — we checked
```

Rust provides a convenience method that handles this:

```rust
let mut queue = lock.lock().unwrap();
// wait_while loops internally, checking the predicate after each wakeup
queue = cvar.wait_while(queue, |q| q.is_empty()).unwrap();
let val = queue.pop().unwrap(); // safe
```

`wait_while` keeps waiting as long as the predicate returns `true`. It handles spurious wakeups automatically.

## notify_one vs notify_all

- `notify_one()` — Wakes one waiting thread. Use when only one thread should respond (e.g., producer-consumer).
- `notify_all()` — Wakes all waiting threads. Use when the condition change affects multiple waiters (e.g., barrier release).

```rust
use std::sync::{Arc, Condvar, Mutex};
use std::thread;

fn main() {
    let pair = Arc::new((Mutex::new(false), Condvar::new()));
    let mut handles = vec![];

    // Multiple waiters
    for id in 0..4 {
        let pair = Arc::clone(&pair);
        handles.push(thread::spawn(move || {
            let (lock, cvar) = &*pair;
            let mut started = lock.lock().unwrap();
            while !*started {
                started = cvar.wait(started).unwrap();
            }
            println!("Worker {} started!", id);
        }));
    }

    thread::sleep(std::time::Duration::from_secs(1));

    // Signal all waiters
    {
        let (lock, cvar) = &*pair;
        *lock.lock().unwrap() = true;
        cvar.notify_all(); // wake ALL waiting threads
    }

    for h in handles {
        h.join().unwrap();
    }
}
```

Using `notify_one()` here would only wake one of the four workers. The others would sleep forever (or until a spurious wakeup).

## Timed Waits

```rust
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::Duration;

fn main() {
    let pair = Arc::new((Mutex::new(false), Condvar::new()));
    let pair2 = Arc::clone(&pair);

    let waiter = thread::spawn(move || {
        let (lock, cvar) = &*pair2;
        let mut ready = lock.lock().unwrap();

        let result = cvar.wait_timeout(ready, Duration::from_secs(2)).unwrap();
        ready = result.0;

        if result.1.timed_out() {
            println!("Timed out — condition was not met");
        } else {
            println!("Condition met: {}", *ready);
        }
    });

    // Don't signal — let it time out
    // Or: uncomment to signal before timeout
    // thread::sleep(Duration::from_secs(1));
    // *pair.0.lock().unwrap() = true;
    // pair.1.notify_one();

    waiter.join().unwrap();
}
```

`wait_timeout` returns a tuple of `(MutexGuard, WaitTimeoutResult)`. Check `timed_out()` to know if the wait expired or was signaled.

## Building a Blocking Queue

Putting it all together — a bounded blocking queue with backpressure:

```rust
use std::sync::{Condvar, Mutex};
use std::collections::VecDeque;

struct BlockingQueue<T> {
    inner: Mutex<VecDeque<T>>,
    capacity: usize,
    not_empty: Condvar,
    not_full: Condvar,
}

impl<T> BlockingQueue<T> {
    fn new(capacity: usize) -> Self {
        BlockingQueue {
            inner: Mutex::new(VecDeque::with_capacity(capacity)),
            capacity,
            not_empty: Condvar::new(),
            not_full: Condvar::new(),
        }
    }

    fn push(&self, item: T) {
        let mut queue = self.inner.lock().unwrap();
        while queue.len() >= self.capacity {
            queue = self.not_full.wait(queue).unwrap();
        }
        queue.push_back(item);
        self.not_empty.notify_one();
    }

    fn pop(&self) -> T {
        let mut queue = self.inner.lock().unwrap();
        while queue.is_empty() {
            queue = self.not_empty.wait(queue).unwrap();
        }
        let item = queue.pop_front().unwrap();
        self.not_full.notify_one();
        item
    }

    fn len(&self) -> usize {
        self.inner.lock().unwrap().len()
    }
}

fn main() {
    use std::sync::Arc;
    use std::thread;

    let queue = Arc::new(BlockingQueue::new(5));
    let mut handles = vec![];

    // Producers
    for id in 0..3 {
        let q = Arc::clone(&queue);
        handles.push(thread::spawn(move || {
            for i in 0..10 {
                q.push(format!("p{}-item{}", id, i));
                println!("[Producer {}] pushed item {}", id, i);
            }
        }));
    }

    // Consumers
    for id in 0..2 {
        let q = Arc::clone(&queue);
        handles.push(thread::spawn(move || {
            for _ in 0..15 {
                let item = q.pop();
                println!("[Consumer {}] got: {}", id, item);
                thread::sleep(std::time::Duration::from_millis(50));
            }
        }));
    }

    for h in handles {
        h.join().unwrap();
    }
}
```

Two condvars, two conditions:
- `not_empty` — Consumers wait here when the queue is empty
- `not_full` — Producers wait here when the queue is at capacity

This is essentially what `std::sync::mpsc::sync_channel` does internally.

## parking_lot Condvar

parking_lot also provides a `Condvar` with a nicer API:

```rust
use parking_lot::{Condvar, Mutex};

fn main() {
    let data = Mutex::new(false);
    let cvar = Condvar::new();

    // No unwrap() needed — no poisoning
    let mut guard = data.lock();
    while !*guard {
        cvar.wait(&mut guard); // takes &mut MutexGuard, not by value
    }
}
```

The key difference: parking_lot's `wait` takes `&mut MutexGuard` rather than consuming and returning the guard. Less ownership juggling.

## Common Mistakes

**Forgetting to hold the lock when notifying:**

```rust
// This works but might miss notifications
cvar.notify_one(); // notify without holding the lock

// Better: notify while holding the lock (or right after dropping it)
{
    let mut guard = lock.lock().unwrap();
    *guard = true;
    cvar.notify_one();
} // lock released, notified thread can proceed
```

**Not looping on the condition:**

```rust
// WRONG — spurious wakeup will break this
queue = cvar.wait(queue).unwrap();
process(queue.pop().unwrap()); // might panic

// RIGHT
while queue.is_empty() {
    queue = cvar.wait(queue).unwrap();
}
```

**Using notify_one when you should use notify_all:**

If multiple threads could be interested in the same event (like a shutdown signal), use `notify_all`. If only one thread should wake up (like one consumer for one item), use `notify_one`.

---

Next — barriers and `Once`, for coordinating thread startup and one-time initialization.
