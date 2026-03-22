---
title: "Lesson 2: Building a Minimal Executor — Your own async runtime"
author: Atharva Pandey
series: "Rust Async Internals & Runtime Design"
lesson: 2
keywords: [Rust, rust, async, runtime, executor, task queue, waker, scheduler]
tags: [Rust tutorial, rust, async, runtime, internals]
date: '2025-09-17T14:12:37.000Z'
---

The moment I built my first executor from scratch, async Rust stopped being scary. Not because executors are simple — they're not — but because once you see the machinery, every "mysterious" behavior has an obvious explanation. Futures hanging? The waker isn't being called. Tasks not making progress? The executor's run loop has a bug. Performance terrible? You're probably polling too aggressively or not enough.

So let's build one. A real, working executor. Not a production-quality one (that's Tokio's job), but one that actually runs futures to completion, handles multiple tasks, and demonstrates every concept from the previous lesson.

## The Simplest Possible Executor

Let's start with the absolute minimum: an executor that can run a single future to completion.

```rust
use std::future::Future;
use std::pin::Pin;
use std::task::{Context, Poll, RawWaker, RawWakerVTable, Waker};

fn block_on<F: Future>(mut future: F) -> F::Output {
    // Create a no-op waker — we're going to busy-poll anyway
    let waker = noop_waker();
    let mut cx = Context::from_waker(&waker);

    // Pin the future on the stack
    let mut future = unsafe { Pin::new_unchecked(&mut future) };

    // Spin until the future completes
    loop {
        match future.as_mut().poll(&mut cx) {
            Poll::Ready(output) => return output,
            Poll::Pending => {
                // In a real executor: park the thread until woken
                // Here: just spin (terrible, but educational)
                std::thread::yield_now();
            }
        }
    }
}

fn noop_waker() -> Waker {
    fn no_op(_: *const ()) {}
    fn clone(data: *const ()) -> RawWaker {
        RawWaker::new(data, &VTABLE)
    }
    static VTABLE: RawWakerVTable =
        RawWakerVTable::new(clone, no_op, no_op, no_op);
    unsafe { Waker::from_raw(RawWaker::new(std::ptr::null(), &VTABLE)) }
}
```

This is terrible and I love it. It busy-polls, it wastes CPU, and it can only run one future. But it works! You can do `block_on(async { 42 })` and get `42` back. That's a functioning async runtime in under 30 lines.

The problem, obviously, is that it busy-waits. When a future returns `Pending`, we just... try again immediately. And again. And again. A real executor needs to actually sleep until a waker fires.

## Adding Proper Wake Notification

Let's fix the busy-waiting problem. We need the executor to park the thread when there's no work, and unpark it when a waker fires:

```rust
use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;
use std::task::{Context, Poll, Wake};
use std::thread;

struct ThreadWaker {
    thread: thread::Thread,
}

impl Wake for ThreadWaker {
    fn wake(self: Arc<Self>) {
        self.thread.unpark();
    }

    fn wake_by_ref(self: &Arc<Self>) {
        self.thread.unpark();
    }
}

fn block_on<F: Future>(mut future: F) -> F::Output {
    // Create a waker that unparks the current thread
    let waker = Arc::new(ThreadWaker {
        thread: thread::current(),
    })
    .into(); // Arc<impl Wake> -> Waker

    let mut cx = Context::from_waker(&waker);
    let mut future = unsafe { Pin::new_unchecked(&mut future) };

    loop {
        match future.as_mut().poll(&mut cx) {
            Poll::Ready(output) => return output,
            Poll::Pending => {
                // Park the thread — it'll be unparked when wake() is called
                thread::park();
            }
        }
    }
}
```

Now we're using the `Wake` trait (stabilized in Rust 1.51) instead of raw vtable nonsense. The executor creates a waker tied to the current thread. When the future returns `Pending`, we park the thread. When something calls `waker.wake()`, the thread unparks and polls again. Zero busy-waiting.

But we still can only run one future. Let's fix that.

## A Multi-Task Executor

A real executor needs to manage multiple tasks. Each task wraps a future along with the machinery to poll it. Here's where things get interesting:

```rust
use std::collections::VecDeque;
use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll, Wake, Waker};

// A task is a boxed future that we can poll
struct Task {
    future: Mutex<Pin<Box<dyn Future<Output = ()> + Send>>>,
    queue: Arc<TaskQueue>,
}

struct TaskQueue {
    ready: Mutex<VecDeque<Arc<Task>>>,
}

impl TaskQueue {
    fn new() -> Arc<Self> {
        Arc::new(TaskQueue {
            ready: Mutex::new(VecDeque::new()),
        })
    }

    fn push(&self, task: Arc<Task>) {
        self.ready.lock().unwrap().push_back(task);
    }

    fn pop(&self) -> Option<Arc<Task>> {
        self.ready.lock().unwrap().pop_front()
    }
}

// When a task's waker fires, re-enqueue it
impl Wake for Task {
    fn wake(self: Arc<Self>) {
        self.queue.push(self.clone());
    }

    fn wake_by_ref(self: &Arc<Self>) {
        self.queue.push(self.clone());
    }
}

struct Executor {
    queue: Arc<TaskQueue>,
}

impl Executor {
    fn new() -> Self {
        Executor {
            queue: TaskQueue::new(),
        }
    }

    fn spawn<F>(&self, future: F)
    where
        F: Future<Output = ()> + Send + 'static,
    {
        let task = Arc::new(Task {
            future: Mutex::new(Box::pin(future)),
            queue: self.queue.clone(),
        });
        self.queue.push(task);
    }

    fn run(&self) {
        while let Some(task) = self.queue.pop() {
            let waker: Waker = task.clone().into();
            let mut cx = Context::from_waker(&waker);

            let mut future = task.future.lock().unwrap();
            match future.as_mut().poll(&mut cx) {
                Poll::Ready(()) => {
                    // Task is done — just drop it
                }
                Poll::Pending => {
                    // Task will be re-queued when its waker fires
                    // We do NOT re-queue it here — that would be busy-polling
                }
            }
        }
    }
}
```

Let's walk through the key design decisions:

**Tasks are `Arc<Task>`** because they need to be shared between the executor (which polls them) and the waker (which re-enqueues them). The `Arc` ensures the task stays alive as long as either side needs it.

**The waker IS the task.** When you implement `Wake` for `Task`, calling `waker.wake()` directly re-enqueues the task onto the ready queue. This is elegant — the waker and the scheduling mechanism are the same thing.

**We don't re-enqueue on Pending.** This is the critical difference from our busy-polling version. When a future returns `Pending`, it's the future's job to arrange for `waker.wake()` to be called later. We trust that it will.

## Testing Our Executor

Let's actually run some futures on this thing:

```rust
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

// A simple future that yields once then completes
struct YieldOnce {
    yielded: bool,
}

impl Future for YieldOnce {
    type Output = ();

    fn poll(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<()> {
        if self.yielded {
            Poll::Ready(())
        } else {
            self.yielded = true;
            // Wake immediately — we're ready to be polled again
            cx.waker().wake_by_ref();
            Poll::Pending
        }
    }
}

fn main() {
    let executor = Executor::new();

    executor.spawn(async {
        println!("Task 1: starting");
        YieldOnce { yielded: false }.await;
        println!("Task 1: resumed after yield");
    });

    executor.spawn(async {
        println!("Task 2: starting");
        YieldOnce { yielded: false }.await;
        println!("Task 2: resumed after yield");
    });

    executor.spawn(async {
        println!("Task 3: I complete immediately");
    });

    executor.run();
    println!("All tasks completed");
}
```

Output (order may vary between tasks):
```
Task 1: starting
Task 2: starting
Task 3: I complete immediately
Task 1: resumed after yield
Task 2: resumed after yield
All tasks completed
```

The executor interleaves the tasks. Task 1 starts, hits `YieldOnce`, returns `Pending` (but immediately wakes itself), so the executor moves on to Task 2, then Task 3, then comes back to finish Task 1 and Task 2.

## Adding a Spawn Handle for Results

Our executor only handles `Future<Output = ()>`. What if you want to get a value back from a spawned task? You need a `JoinHandle`:

```rust
use std::sync::mpsc;

struct JoinHandle<T> {
    receiver: mpsc::Receiver<T>,
}

impl<T> JoinHandle<T> {
    fn join(self) -> T {
        self.receiver.recv().unwrap()
    }
}

impl Executor {
    fn spawn_with_handle<F, T>(&self, future: F) -> JoinHandle<T>
    where
        F: Future<Output = T> + Send + 'static,
        T: Send + 'static,
    {
        let (sender, receiver) = mpsc::channel();

        self.spawn(async move {
            let result = future.await;
            let _ = sender.send(result);
        });

        JoinHandle { receiver }
    }
}

fn main() {
    let executor = Executor::new();

    let handle = executor.spawn_with_handle(async {
        42u64
    });

    executor.run();

    let result = handle.join();
    println!("Got: {}", result); // Got: 42
}
```

## The Run Loop Problem

Our `run` method has a flaw: it returns when the ready queue is empty, even if there are futures that are `Pending` and waiting to be woken. In a real runtime, you'd want `run` to block until all tasks complete. Here's one way to handle that:

```rust
use std::sync::{Condvar, Mutex as StdMutex};

struct ExecutorInner {
    queue: VecDeque<Arc<Task>>,
    task_count: usize,
}

struct BetterExecutor {
    inner: Arc<(StdMutex<ExecutorInner>, Condvar)>,
}

impl BetterExecutor {
    fn new() -> Self {
        BetterExecutor {
            inner: Arc::new((
                StdMutex::new(ExecutorInner {
                    queue: VecDeque::new(),
                    task_count: 0,
                }),
                Condvar::new(),
            )),
        }
    }

    fn spawn<F>(&self, future: F)
    where
        F: Future<Output = ()> + Send + 'static,
    {
        let (lock, cvar) = &*self.inner;
        let mut inner = lock.lock().unwrap();
        inner.task_count += 1;

        let task = Arc::new(BetterTask {
            future: Mutex::new(Box::pin(future)),
            executor: self.inner.clone(),
        });

        inner.queue.push_back(task);
        cvar.notify_one();
    }

    fn run(&self) {
        let (lock, cvar) = &*self.inner;

        loop {
            let task = {
                let mut inner = lock.lock().unwrap();

                // No tasks left at all — we're done
                if inner.task_count == 0 {
                    return;
                }

                // No tasks ready — wait for a wake
                while inner.queue.is_empty() && inner.task_count > 0 {
                    inner = cvar.wait(inner).unwrap();
                }

                if inner.task_count == 0 {
                    return;
                }

                inner.queue.pop_front()
            };

            if let Some(task) = task {
                let waker: Waker = task.clone().into();
                let mut cx = Context::from_waker(&waker);
                let mut future = task.future.lock().unwrap();

                match future.as_mut().poll(&mut cx) {
                    Poll::Ready(()) => {
                        let mut inner = lock.lock().unwrap();
                        inner.task_count -= 1;
                        cvar.notify_one();
                    }
                    Poll::Pending => {
                        // Will be re-queued by waker
                    }
                }
            }
        }
    }
}

struct BetterTask {
    future: Mutex<Pin<Box<dyn Future<Output = ()> + Send>>>,
    executor: Arc<(StdMutex<ExecutorInner>, Condvar)>,
}

impl Wake for BetterTask {
    fn wake(self: Arc<Self>) {
        let (lock, cvar) = &*self.executor;
        lock.lock().unwrap().queue.push_back(self.clone());
        cvar.notify_one();
    }
}
```

Now the executor properly blocks when there are pending tasks and wakes up when any of them become ready. It only returns when all tasks have completed.

## What We're Missing

This executor is functional but far from production-ready. Here's what the real runtimes add on top:

**I/O integration.** Our executor can poll futures, but it doesn't have a reactor — the thing that watches file descriptors and fires wakers when I/O is ready. Without that, you can't do async networking. We'll cover this in lessons 4 and 5.

**Multi-threaded execution.** Our executor runs on a single thread. Tokio's multi-threaded runtime distributes tasks across a thread pool using work-stealing (lesson 6).

**Timer support.** Tokio has a timer wheel that efficiently manages thousands of `sleep` futures. Our executor would need external threads for timers.

**Task cancellation.** When you drop a `JoinHandle` in Tokio, the task gets cancelled. We don't have that.

**Fairness.** Our executor drains the queue in FIFO order, which is okay, but doesn't handle starvation well. What if one task keeps waking itself and starving others?

But the core loop — poll, check for ready tasks, sleep when idle, wake on notification — that's the same in every runtime. Whether it's Tokio with its work-stealing scheduler and io_uring support, or smol with its minimalist design, or the executor we just built. The differences are in the optimizations, not the fundamental architecture.

## The Mental Model

After building this, here's how I think about async Rust:

An executor is a loop. It pulls tasks from a queue, polls them, and either collects results or waits for wakers. A waker is a callback that says "this task can make progress now." A future is a state machine that either produces a value or says "not yet, call me later."

That's it. Everything else — reactors, thread pools, timers, I/O drivers — is infrastructure built around this core loop. Once you internalize this, debugging async Rust becomes mechanical instead of mystical.

Next up, we're going deep into the I/O side — specifically `io_uring`, the Linux kernel's modern async I/O interface that's changing how we think about building high-performance runtimes.
