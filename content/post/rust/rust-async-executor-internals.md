---
title: "Lesson 16: How Async Executors Work Under the Hood — Demystifying the runtime"
author: Atharva Pandey
series: "Async Rust Masterclass"
lesson: 16
keywords: [Rust, rust, async, tokio, executor, runtime, internals, waker, epoll, work stealing]
tags: [Rust tutorial, rust, async, tokio]
date: '2025-02-04T13:56:18.000Z'
---

There's a moment in every async Rust developer's journey where the runtime stops being a black box and starts being a machine you understand. For me, it was when I built a toy executor from scratch. Suddenly, `Waker`, `Context`, `poll_ready` — all of it made sense. Not as abstract concepts, but as mechanical parts.

This lesson won't make you build a production executor. But it will show you how the pieces fit together, and that understanding will change how you write and debug async code.

## The Simplest Possible Executor

An executor does three things:
1. Holds a collection of tasks (futures)
2. Polls them when they're ready
3. Sleeps when nothing is ready

Here's the most minimal executor imaginable:

```rust
use std::collections::VecDeque;
use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll, Wake, Waker};

struct MiniExecutor {
    queue: VecDeque<Pin<Box<dyn Future<Output = ()> + Send>>>,
}

/// A waker that does nothing — it just re-polls everything.
/// This is terrible for performance but correct for learning.
struct NoopWaker;

impl Wake for NoopWaker {
    fn wake(self: Arc<Self>) {
        // Do nothing — our executor polls everything anyway
    }
}

impl MiniExecutor {
    fn new() -> Self {
        MiniExecutor {
            queue: VecDeque::new(),
        }
    }

    fn spawn(&mut self, future: impl Future<Output = ()> + Send + 'static) {
        self.queue.push_back(Box::pin(future));
    }

    fn run(&mut self) {
        let waker = Waker::from(Arc::new(NoopWaker));
        let mut cx = Context::from_waker(&waker);

        while !self.queue.is_empty() {
            let mut task = self.queue.pop_front().unwrap();

            match task.as_mut().poll(&mut cx) {
                Poll::Ready(()) => {
                    // Task is done
                }
                Poll::Pending => {
                    // Task isn't done — put it back
                    self.queue.push_back(task);
                }
            }
        }
    }
}

fn main() {
    let mut executor = MiniExecutor::new();

    executor.spawn(async {
        println!("Task 1: hello");
    });

    executor.spawn(async {
        println!("Task 2: world");
    });

    executor.run();
}
```

This works for futures that complete on the first poll. But it busy-loops for anything that returns `Pending` because our waker does nothing. Let's fix that.

## Adding a Real Waker

The waker's job is to tell the executor "this task is ready to be polled again." Let's build a waker that actually communicates:

```rust
use std::collections::VecDeque;
use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, Mutex, Condvar};
use std::task::{Context, Poll, Wake, Waker};

type Task = Pin<Box<dyn Future<Output = ()> + Send>>;

struct TaskQueue {
    tasks: Mutex<VecDeque<usize>>,
    condvar: Condvar,
}

struct TaskWaker {
    task_id: usize,
    queue: Arc<TaskQueue>,
}

impl Wake for TaskWaker {
    fn wake(self: Arc<Self>) {
        let mut tasks = self.queue.tasks.lock().unwrap();
        if !tasks.contains(&self.task_id) {
            tasks.push_back(self.task_id);
        }
        self.queue.condvar.notify_one();
    }
}

struct BetterExecutor {
    tasks: Vec<Option<Task>>,
    ready_queue: Arc<TaskQueue>,
}

impl BetterExecutor {
    fn new() -> Self {
        BetterExecutor {
            tasks: Vec::new(),
            ready_queue: Arc::new(TaskQueue {
                tasks: Mutex::new(VecDeque::new()),
                condvar: Condvar::new(),
            }),
        }
    }

    fn spawn(&mut self, future: impl Future<Output = ()> + Send + 'static) {
        let id = self.tasks.len();
        self.tasks.push(Some(Box::pin(future)));
        self.ready_queue.tasks.lock().unwrap().push_back(id);
    }

    fn run(&mut self) {
        loop {
            // Wait for a task to be ready
            let task_id = {
                let mut ready = self.ready_queue.tasks.lock().unwrap();
                while ready.is_empty() {
                    // Check if all tasks are done
                    if self.tasks.iter().all(|t| t.is_none()) {
                        return;
                    }
                    ready = self.ready_queue.condvar.wait(ready).unwrap();
                }
                ready.pop_front().unwrap()
            };

            // Get the task
            let task = match self.tasks.get_mut(task_id) {
                Some(Some(task)) => task,
                _ => continue,
            };

            // Create a waker for this task
            let waker = Waker::from(Arc::new(TaskWaker {
                task_id,
                queue: self.ready_queue.clone(),
            }));
            let mut cx = Context::from_waker(&waker);

            // Poll the task
            match task.as_mut().poll(&mut cx) {
                Poll::Ready(()) => {
                    self.tasks[task_id] = None; // Task complete
                    println!("Task {task_id} completed");
                }
                Poll::Pending => {
                    // The task registered the waker internally.
                    // When whatever it's waiting for is ready,
                    // it'll call wake(), which puts this task_id
                    // back on the ready queue.
                }
            }
        }
    }
}

fn main() {
    let mut executor = BetterExecutor::new();

    executor.spawn(async {
        println!("Task A starting");
        // In a real executor, this would be a timer
        // that registers with the reactor
        println!("Task A done");
    });

    executor.spawn(async {
        println!("Task B starting");
        println!("Task B done");
    });

    executor.run();
}
```

Now the executor sleeps (via `Condvar::wait`) when no tasks are ready, and wakes up when a waker fires. This is the fundamental loop of every async runtime.

## How Tokio's Executor Works (Simplified)

Tokio's multi-threaded executor is much more sophisticated, but the core loop is the same:

```
Thread 1:                    Thread 2:
┌────────────────┐          ┌────────────────┐
│ Local Queue    │          │ Local Queue    │
│ [Task A]       │ ←steal── │ [Task C]       │
│ [Task B]       │          │ [Task D]       │
└────────────────┘          └────────────────┘
        ↕                          ↕
┌─────────────────────────────────────────────┐
│              Global Queue                    │
│              [Task E, Task F, ...]          │
└─────────────────────────────────────────────┘
        ↕
┌─────────────────────────────────────────────┐
│              Reactor (epoll/kqueue)          │
│              Monitors: sockets, timers       │
│              Wakes tasks when events fire    │
└─────────────────────────────────────────────┘
```

Each worker thread has:
1. A **local run queue** for tasks (fast access, no lock contention)
2. Access to a **global run queue** (shared, needs synchronization)
3. **Work stealing** — when a thread's local queue is empty, it steals tasks from other threads

The **reactor** is a separate component that:
1. Registers interest in I/O events (socket readable, timer expired)
2. Waits for events using `epoll_wait` (Linux) or `kevent` (macOS)
3. Wakes the appropriate tasks by calling their wakers

## The Reactor: How I/O Becomes Async

When you do `TcpStream::read()` in Tokio, here's what happens:

```
1. read() calls poll_read()
2. poll_read() tries a non-blocking read from the socket
3. If data is available: return Poll::Ready(data)
4. If no data: register the socket with the reactor
   - "Wake me when this socket fd is readable"
   - Return Poll::Pending
5. The executor parks this task
6. Later, the reactor's epoll_wait returns "socket fd is readable"
7. The reactor calls the task's waker
8. The executor re-polls the task
9. This time, poll_read() finds data and returns Poll::Ready(data)
```

This is why async I/O is efficient — no thread is blocked waiting. The OS tells us when data is available.

## Work Stealing: Load Balancing

```rust
use std::time::Instant;
use tokio::time::{sleep, Duration};

#[tokio::main(flavor = "multi_thread", worker_threads = 4)]
async fn main() {
    let start = Instant::now();

    // Spawn many tasks — they'll be distributed across worker threads
    let mut handles = Vec::new();
    for i in 0..20 {
        handles.push(tokio::spawn(async move {
            // Simulate varying work
            let work_ms = (i % 5 + 1) * 20;
            sleep(Duration::from_millis(work_ms as u64)).await;

            let thread = std::thread::current();
            println!("[{:.2}s] Task {i} on thread {:?}",
                start.elapsed().as_secs_f64(),
                thread.name().unwrap_or("unknown"));
        }));
    }

    for h in handles {
        h.await.unwrap();
    }

    println!("All done in {:.2}s", start.elapsed().as_secs_f64());
}
```

You'll notice tasks running on different threads. Tokio distributes them automatically and steals tasks from busy threads to keep all cores utilized.

## Why This Matters For Your Code

Understanding the executor changes how you write async Rust:

### 1. Don't block the executor thread

```rust
// BAD: This blocks one of Tokio's worker threads
async fn bad() {
    std::thread::sleep(std::time::Duration::from_secs(1)); // Blocks!
}

// GOOD: Offload to the blocking pool
async fn good() {
    tokio::task::spawn_blocking(|| {
        std::thread::sleep(std::time::Duration::from_secs(1));
    }).await.unwrap();
}
```

Every moment a worker thread is blocked, it can't poll other tasks. With 4 worker threads, blocking one means 25% of your capacity is gone.

### 2. Yield points matter

```rust
use tokio::task;

async fn cpu_heavy_work(data: &[u32]) -> u64 {
    let mut sum = 0u64;
    for (i, &val) in data.iter().enumerate() {
        sum += val as u64;
        // Yield periodically so other tasks can run
        if i % 10_000 == 0 {
            task::yield_now().await;
        }
    }
    sum
}

#[tokio::main]
async fn main() {
    let data: Vec<u32> = (0..1_000_000).collect();
    let sum = cpu_heavy_work(&data).await;
    println!("Sum: {sum}");
}
```

### 3. Task size affects scheduling

Each spawned task is a heap allocation. Millions of tiny tasks waste memory on bookkeeping. A few huge tasks that never yield starve everything else. Find the balance.

### 4. The runtime is a resource — configure it

```rust
fn main() {
    // For CPU-bound work: match core count
    // For I/O-bound work: default is usually fine
    // For mixed: profile and adjust

    let rt = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(4)
        .max_blocking_threads(32) // For spawn_blocking
        .enable_all()
        .build()
        .unwrap();

    rt.block_on(async {
        // Your application
    });
}
```

## What Happens When You Await

The complete picture, from your code to the OS and back:

```
You write:       let data = stream.read(&mut buf).await;
                          │
Compiler generates:       State machine with poll()
                          │
Your task's poll():       Calls stream.poll_read()
                          │
Stream's poll_read():     Non-blocking syscall
                          │ (no data available)
                          │
                    Registers FD with reactor
                    Returns Poll::Pending
                          │
Executor:           Parks the task
                    Worker thread picks up another task
                          │
                    ... time passes ...
                          │
OS (epoll/kqueue):  "FD is readable!"
                          │
Reactor:            Calls task's Waker::wake()
                          │
Executor:           Puts task back on run queue
                    Worker thread picks it up
                          │
Your task's poll():       Calls stream.poll_read() again
                          │
Stream's poll_read():     Non-blocking syscall
                          │ (data available!)
                          │
                    Returns Poll::Ready(data)
                          │
You get:           data = the bytes that were read
```

That's the whole journey. Every `.await` in your code potentially goes through this entire cycle. And it all happens without a single thread being blocked.

Understanding this flow is what separates someone who writes async Rust from someone who *understands* async Rust. The next lesson covers `Pin` — the other piece that makes the whole state machine work safely.
