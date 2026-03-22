---
title: "Lesson 6: Work-Stealing Schedulers — Balancing load across cores"
author: Atharva Pandey
series: "Rust Async Internals & Runtime Design"
lesson: 6
keywords: [Rust, rust, async, runtime, work-stealing, scheduler, thread pool, deque, load balancing, Tokio]
tags: [Rust tutorial, rust, async, runtime, internals]
date: '2025-09-27T13:45:50.000Z'
---

I once spent two days debugging a performance issue where our Tokio application was using four cores but only one was doing any work. Three threads were idle, one was pegged at 100%. The problem wasn't Tokio's scheduler — it was ours. We'd accidentally created a pattern where all tasks were spawned from and waking on the same thread, and nothing triggered work-stealing because the tasks completed too quickly.

That experience taught me that you can't treat the scheduler as a black box. If you understand how work-stealing works, you can design your task topology to take advantage of it. If you don't, you'll write code that accidentally defeats it.

## Why Work-Stealing?

The naive approach to multi-threaded task scheduling is a single shared queue: all threads push and pop tasks from one queue, protected by a mutex.

```rust
use std::collections::VecDeque;
use std::sync::Mutex;

struct SharedQueue<T> {
    tasks: Mutex<VecDeque<T>>,
}

impl<T> SharedQueue<T> {
    fn push(&self, task: T) {
        self.tasks.lock().unwrap().push_back(task);
    }

    fn pop(&self) -> Option<T> {
        self.tasks.lock().unwrap().pop_front()
    }
}
```

This works, but it's a bottleneck. Every push and pop contends on the same mutex. With 8 threads hammering the queue, you spend more time fighting for the lock than doing actual work. I've measured this — at around 16 threads, a single shared queue becomes the dominant cost in the system.

The opposite extreme is per-thread queues with no sharing:

```rust
// Each thread has its own queue — no contention!
thread_local! {
    static LOCAL_QUEUE: RefCell<VecDeque<Task>> = RefCell::new(VecDeque::new());
}
```

Zero contention, but terrible load balancing. If thread 1 has 1000 tasks and thread 2 has zero, thread 2 sits idle. You get the throughput of a single-threaded runtime.

Work-stealing is the middle ground: each thread has its own queue, but idle threads can steal tasks from busy threads. You get low contention in the common case (threads work on their own queues) and good load balancing when workloads are uneven.

## The Work-Stealing Deque

The foundation of work-stealing is a special data structure: the **work-stealing deque** (double-ended queue). The key insight is that the owning thread and stealing threads access different ends:

- The **owner** pushes and pops from the **back** (LIFO — last in, first out).
- **Thieves** steal from the **front** (FIFO — first in, first out).

This asymmetry is what makes lock-free operation possible. Let's implement one:

```rust
use std::sync::atomic::{AtomicIsize, Ordering};
use std::cell::UnsafeCell;

const CAPACITY: usize = 1024;

struct WorkStealingDeque<T> {
    buffer: Box<[UnsafeCell<Option<T>>]>,
    // The back index — only modified by the owner thread
    back: AtomicIsize,
    // The front index — modified by thieves (and sometimes the owner)
    front: AtomicIsize,
}

unsafe impl<T: Send> Sync for WorkStealingDeque<T> {}

impl<T> WorkStealingDeque<T> {
    fn new() -> Self {
        let mut buffer = Vec::with_capacity(CAPACITY);
        for _ in 0..CAPACITY {
            buffer.push(UnsafeCell::new(None));
        }
        WorkStealingDeque {
            buffer: buffer.into_boxed_slice(),
            back: AtomicIsize::new(0),
            front: AtomicIsize::new(0),
        }
    }

    // Owner pushes to the back — no synchronization needed with other pushes
    fn push(&self, item: T) {
        let back = self.back.load(Ordering::Relaxed);
        let front = self.front.load(Ordering::Acquire);

        if back - front >= CAPACITY as isize {
            panic!("deque is full — need to grow");
        }

        let index = (back % CAPACITY as isize) as usize;
        unsafe {
            *self.buffer[index].get() = Some(item);
        }
        self.back.store(back + 1, Ordering::Release);
    }

    // Owner pops from the back — may race with steal
    fn pop(&self) -> Option<T> {
        let back = self.back.load(Ordering::Relaxed) - 1;
        self.back.store(back, Ordering::SeqCst);

        let front = self.front.load(Ordering::SeqCst);

        if front <= back {
            // Multiple items in queue — safe to pop without racing
            let index = (back % CAPACITY as isize) as usize;
            let item = unsafe { (*self.buffer[index].get()).take() };
            Some(item.unwrap())
        } else if front == back + 1 {
            // Queue is now empty — restore back
            self.back.store(front, Ordering::Relaxed);
            None
        } else {
            // Raced with a steal on the last element
            // Try to claim it with a CAS on front
            let index = (back % CAPACITY as isize) as usize;
            if self
                .front
                .compare_exchange(front, front + 1, Ordering::SeqCst, Ordering::Relaxed)
                .is_ok()
            {
                self.back.store(front + 1, Ordering::Relaxed);
                let item = unsafe { (*self.buffer[index].get()).take() };
                Some(item.unwrap())
            } else {
                self.back.store(front + 1, Ordering::Relaxed);
                None
            }
        }
    }

    // Thief steals from the front
    fn steal(&self) -> Option<T> {
        let front = self.front.load(Ordering::Acquire);
        let back = self.back.load(Ordering::Acquire);

        if front >= back {
            return None; // empty
        }

        let index = (front % CAPACITY as isize) as usize;
        let item = unsafe { (*self.buffer[index].get()).clone() };

        // CAS to claim this slot — another thief might race us
        if self
            .front
            .compare_exchange(front, front + 1, Ordering::SeqCst, Ordering::Relaxed)
            .is_ok()
        {
            item
        } else {
            None // lost the race — try again
        }
    }
}
```

The real implementations (like the `crossbeam-deque` crate that Tokio uses) are more sophisticated — they handle growing buffers, use epoch-based reclamation for memory safety, and have better memory ordering guarantees. But the core algorithm is this: owner works on the back, thieves take from the front, and they only contend when the deque has exactly one element.

## Why LIFO for the Owner?

This isn't arbitrary. LIFO means the owner works on the most recently pushed task. That task's data is likely still in the CPU cache — temporal locality. The thief, taking from the front, gets the oldest task. That task's data is probably cold anyway, so the cache miss from moving it to another core is less costly.

Also, in recursive divide-and-conquer patterns, LIFO gives you depth-first traversal (small working set) while stealing gives breadth-first (exposes more parallelism). It's a neat property.

```rust
// Consider a parallel merge sort spawning sub-tasks:
async fn parallel_sort(data: &mut [i32]) {
    if data.len() <= 1024 {
        data.sort(); // base case
        return;
    }
    let mid = data.len() / 2;
    let (left, right) = data.split_at_mut(mid);

    // These get pushed to the local deque
    let left_handle = tokio::spawn(async { parallel_sort(left).await });
    let right_handle = tokio::spawn(async { parallel_sort(right).await });

    // Owner processes left (LIFO — depth first)
    // Thief steals right (FIFO — breadth first, exposing parallelism)
    left_handle.await.unwrap();
    right_handle.await.unwrap();
}
```

## Tokio's Scheduler Architecture

Tokio's multi-threaded scheduler uses this design:

```
┌─────────────────────────────────────────────────────┐
│                  Global Inject Queue                 │
│            (overflow + cross-thread spawns)          │
└──────────────┬──────────────┬───────────────────────┘
               │              │
    ┌──────────▼──┐    ┌──────▼───────┐    ┌──────────────┐
    │  Worker 0   │    │  Worker 1    │    │  Worker 2    │
    │             │    │              │    │              │
    │ Local Deque │◄──►│ Local Deque  │◄──►│ Local Deque  │
    │ (steal ←→)  │    │ (steal ←→)   │    │ (steal ←→)   │
    │             │    │              │    │              │
    │ I/O Driver  │    │ I/O Driver   │    │ I/O Driver   │
    │ Timer Wheel │    │ Timer Wheel  │    │ Timer Wheel  │
    └─────────────┘    └──────────────┘    └──────────────┘
```

Each worker thread has:
- A **local deque** for tasks spawned on that thread.
- An **I/O driver** (epoll/kqueue instance) for that thread's I/O resources.
- Access to the **global inject queue** for overflow and cross-thread spawns.

The worker loop looks roughly like this:

```rust
fn worker_loop(
    local_queue: &WorkStealingDeque<Task>,
    global_queue: &SharedQueue<Task>,
    all_workers: &[WorkStealingDeque<Task>],
    worker_index: usize,
) {
    loop {
        // 1. Try the local queue first (fastest — no contention)
        if let Some(task) = local_queue.pop() {
            run_task(task);
            continue;
        }

        // 2. Check the global inject queue
        if let Some(task) = global_queue.pop() {
            run_task(task);
            continue;
        }

        // 3. Try to steal from other workers
        let mut stolen = false;
        let num_workers = all_workers.len();
        // Start from a random worker to avoid thundering herd
        let start = random::<usize>() % num_workers;

        for i in 0..num_workers {
            let target = (start + i) % num_workers;
            if target == worker_index {
                continue; // don't steal from yourself
            }

            if let Some(task) = all_workers[target].steal() {
                run_task(task);
                stolen = true;
                break;
            }
        }

        if !stolen {
            // 4. Nothing to do — park the thread
            // (will be unparked when new tasks arrive)
            park_thread();
        }
    }
}
```

## Batch Stealing

Stealing one task at a time is inefficient. If a worker has 100 tasks and a thief steals one, the thief will be back to steal another almost immediately. Tokio steals half the tasks from the victim's queue in a single operation:

```rust
fn steal_batch(
    victim: &WorkStealingDeque<Task>,
    my_queue: &WorkStealingDeque<Task>,
) -> bool {
    let front = victim.front.load(Ordering::Acquire);
    let back = victim.back.load(Ordering::Acquire);

    let available = back - front;
    if available <= 0 {
        return false;
    }

    // Steal half (at least 1, at most 32)
    let steal_count = (available / 2).max(1).min(32);

    // Atomically claim the range [front, front + steal_count)
    if victim
        .front
        .compare_exchange(
            front,
            front + steal_count,
            Ordering::SeqCst,
            Ordering::Relaxed,
        )
        .is_err()
    {
        return false; // someone else stole first
    }

    // Move stolen tasks to our local queue
    for i in 0..steal_count {
        let index = ((front + i) % CAPACITY as isize) as usize;
        let task = unsafe { (*victim.buffer[index].get()).take().unwrap() };
        my_queue.push(task);
    }

    true
}
```

## The LIFO Slot Optimization

Tokio has a subtle optimization: the **LIFO slot**. Each worker has a single slot that holds the most recently woken task. When a task wakes another task (common in pipelines), the newly woken task goes into the LIFO slot, not the deque. The worker checks the LIFO slot before the deque.

Why? Latency. If task A wakes task B, and B's data is still in A's cache, running B immediately on the same core gives you excellent cache locality. Without the LIFO slot, B would go to the back of the deque and potentially get stolen by another core.

```rust
struct Worker {
    lifo_slot: Option<Task>,
    local_queue: WorkStealingDeque<Task>,
}

impl Worker {
    fn schedule_local(&mut self, task: Task) {
        // Put in LIFO slot, bumping any existing task to the deque
        if let Some(old) = self.lifo_slot.replace(task) {
            self.local_queue.push(old);
        }
    }

    fn next_task(&mut self) -> Option<Task> {
        // LIFO slot first — hottest task
        if let Some(task) = self.lifo_slot.take() {
            return Some(task);
        }
        // Then local deque
        self.local_queue.pop()
    }
}
```

## Budget and Yielding

There's a problem with work-stealing that isn't immediately obvious: starvation. If one task keeps waking itself (like a busy loop that yields), it can monopolize a worker thread. Other tasks on that thread never get to run.

Tokio handles this with a **budget** system. Each task gets a budget of polls — currently 128. After 128 polls within a single `poll()` call, the runtime forces the task to yield:

```rust
// Simplified budget check — this is what happens inside Tokio's I/O
fn poll_with_budget<F: Future>(
    future: Pin<&mut F>,
    cx: &mut Context<'_>,
) -> Poll<F::Output> {
    // Decrement the budget
    let budget = CURRENT_BUDGET.with(|b| {
        let val = b.get();
        if val > 0 {
            b.set(val - 1);
        }
        val
    });

    if budget == 0 {
        // Budget exhausted — yield to let other tasks run
        cx.waker().wake_by_ref();
        return Poll::Pending;
    }

    future.poll(cx)
}
```

This is why `tokio::task::yield_now()` exists — sometimes you need to explicitly give other tasks a chance to run. If you're writing a CPU-bound loop inside an async task, calling `yield_now()` periodically prevents starvation:

```rust
async fn cpu_intensive_work(data: &[u32]) -> u64 {
    let mut sum: u64 = 0;
    for (i, &val) in data.iter().enumerate() {
        sum += val as u64;
        if i % 10_000 == 0 {
            tokio::task::yield_now().await;
        }
    }
    sum
}
```

## Measuring Scheduler Behavior

You can observe Tokio's scheduler behavior using `tokio-console`, but you can also add simple instrumentation:

```rust
use std::sync::atomic::{AtomicU64, Ordering};

static TASKS_POLLED: AtomicU64 = AtomicU64::new(0);
static TASKS_STOLEN: AtomicU64 = AtomicU64::new(0);
static TASKS_FROM_GLOBAL: AtomicU64 = AtomicU64::new(0);

// Tokio exposes runtime metrics (unstable feature)
#[tokio::main]
async fn main() {
    let handle = tokio::runtime::Handle::current();
    let metrics = handle.metrics();

    // After running workload...
    for i in 0..metrics.num_workers() {
        println!(
            "Worker {}: polled={}, stolen={}, local_queue_depth={}",
            i,
            metrics.worker_poll_count(i),
            metrics.worker_steal_count(i),
            metrics.worker_local_queue_depth(i),
        );
    }
}
```

If you see one worker with 90% of the poll count, your task spawning pattern is defeating work-stealing. Common causes:
- All tasks spawned from the same thread.
- Tasks that complete before a steal can happen (too fast to steal).
- Tasks that always wake on the same thread due to I/O affinity.

## When Work-Stealing Hurts

Work-stealing isn't free. Moving a task between cores means:
- Cache invalidation — the task's data is cold on the new core.
- False sharing — if stolen tasks share cache lines with the victim's data.
- Synchronization overhead — the CAS operations in steal aren't free.

For latency-sensitive workloads, sometimes you're better off with thread-per-core architectures (like glommio) where tasks never migrate. You lose load balancing but gain predictable cache behavior. There's no single right answer — it depends on whether your bottleneck is CPU efficiency or load imbalance.

Work-stealing is the right default for most server workloads. Understanding how it works lets you recognize the cases where it isn't, and more importantly, lets you structure your code to work *with* the scheduler instead of against it.
