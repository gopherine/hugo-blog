---
title: "Lesson 1: Async Mental Model — Futures, not threads"
author: Atharva Pandey
series: "Async Rust Masterclass"
lesson: 1
keywords: [Rust, rust, async, tokio, futures, mental model, concurrency]
tags: [Rust tutorial, rust, async, tokio]
date: '2025-01-06T09:22:14.000Z'
---

I spent three weeks writing async Rust code that compiled, ran, and produced correct results — while having absolutely no idea what was actually happening. I was copy-pasting `async fn`, slapping `.await` on things, and hoping for the best. Sound familiar?

The problem wasn't syntax. The problem was that I was thinking about async Rust the same way I thought about threads in Go or Java. And that mental model is *wrong* for Rust.

## The Thread Model Is a Trap

Here's how most of us learn concurrency: you spawn a thread, it runs in the background, the OS schedules it. You've got preemptive multitasking — the OS can yank control away from your thread at any point and hand it to another one.

```rust
use std::thread;

fn main() {
    let handle = thread::spawn(|| {
        // This runs on a real OS thread
        // The OS decides when it runs
        println!("Hello from a thread!");
    });
    handle.join().unwrap();
}
```

Simple. Intuitive. And completely different from async.

When you write `async fn` in Rust, you're not creating a thread. You're creating a **state machine**. That's the single most important sentence in this entire course. Let it sink in.

## Futures Are Lazy State Machines

Here's the thing that trips everyone up:

```rust
async fn do_work() -> i32 {
    println!("doing work");
    42
}

fn main() {
    let future = do_work(); // Nothing happens here!
    // "doing work" was NOT printed
    // `future` is just a value sitting in memory
    drop(future); // We threw it away. No work was ever done.
}
```

Calling an async function doesn't run it. It constructs a `Future` — a value that *describes* work to be done. Nothing executes until something *polls* that future.

This is fundamentally different from threads. When you call `thread::spawn`, work starts immediately. When you call an `async fn`, you get back a recipe, not a meal.

## What a Future Actually Is

At its core, a `Future` is a trait:

```rust
use std::pin::Pin;
use std::task::{Context, Poll};

trait Future {
    type Output;
    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output>;
}
```

Don't worry about `Pin` and `Context` right now — we'll cover those later in the course. What matters is `Poll`:

```rust
enum Poll<T> {
    Ready(T),    // "I'm done, here's the result"
    Pending,     // "Not done yet, I'll wake you up later"
}
```

When you poll a future, it either gives you the answer or says "come back later." That's it. That's the entire model.

## The Cooperative Scheduling Contract

Threads are preemptive — the OS interrupts them. Futures are **cooperative** — they voluntarily yield control by returning `Poll::Pending`.

This is both the superpower and the footgun of async Rust.

The superpower: no locks needed for task switching. No context switch overhead. Thousands of concurrent tasks on a single thread.

The footgun: if a future never yields (say, it's doing a long CPU computation), it blocks the entire executor thread. Nothing else makes progress.

```rust
use tokio::time::{sleep, Duration};

async fn good_citizen() {
    // This yields control back to the executor while waiting
    sleep(Duration::from_secs(1)).await;
    println!("I played nice");
}

async fn bad_citizen() {
    // This blocks the executor thread — nothing else can run
    std::thread::sleep(std::time::Duration::from_secs(1));
    println!("I hogged the thread");
}
```

The `.await` point is where the magic happens. When you `.await` a future, you're saying "if this isn't ready, pause me and let someone else run." Without `.await` points, your async function is just a synchronous function with extra steps.

## The Two-Layer Model

I find it helpful to think of async Rust as two layers:

**Layer 1: Futures (your code)**
You write `async fn` and use `.await`. The compiler transforms your code into state machines. Each `.await` becomes a state transition point.

**Layer 2: The Executor (the runtime)**
Something needs to actually call `poll()` on your futures. That's the executor — Tokio, async-std, smol, or whatever runtime you pick. The executor manages a collection of futures, polling them when they're ready to make progress.

```rust
// Layer 1: You write this
async fn fetch_data() -> String {
    let response = make_request().await;  // State transition point
    let body = response.text().await;     // Another state transition
    body
}

// Layer 2: The runtime does this (simplified)
// loop {
//     for task in ready_tasks {
//         match task.poll() {
//             Poll::Ready(val) => complete(task, val),
//             Poll::Pending => /* task registered a waker, we'll come back */,
//         }
//     }
//     wait_for_events(); // epoll/kqueue/IOCP
// }
```

## State Machines: What the Compiler Actually Generates

When you write this:

```rust
async fn example() -> String {
    let a = step_one().await;
    let b = step_two(a).await;
    format!("{a} {b}")
}
```

The compiler generates something *roughly* like this (simplified heavily):

```rust
enum ExampleFuture {
    Start,
    WaitingOnStepOne { fut: StepOneFuture },
    WaitingOnStepTwo { a: String, fut: StepTwoFuture },
    Done,
}

impl Future for ExampleFuture {
    type Output = String;

    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<String> {
        loop {
            match self.state {
                Start => {
                    let fut = step_one();
                    self.state = WaitingOnStepOne { fut };
                }
                WaitingOnStepOne { fut } => {
                    match fut.poll(cx) {
                        Poll::Ready(a) => {
                            let fut = step_two(a.clone());
                            self.state = WaitingOnStepTwo { a, fut };
                        }
                        Poll::Pending => return Poll::Pending,
                    }
                }
                WaitingOnStepTwo { a, fut } => {
                    match fut.poll(cx) {
                        Poll::Ready(b) => {
                            self.state = Done;
                            return Poll::Ready(format!("{a} {b}"));
                        }
                        Poll::Pending => return Poll::Pending,
                    }
                }
                Done => panic!("polled after completion"),
            }
        }
    }
}
```

Each `.await` point becomes a variant in the enum. The future remembers where it left off and what local variables it still needs. This is why async functions that hold references across `.await` points get complicated — those references are stored in the state machine struct, and self-referential structs are... a whole thing.

## Why This Matters in Practice

Understanding the state machine model explains so many things that seem arbitrary:

**Why `async fn` returns an `impl Future` rather than spawning work:** Because the function just builds the state machine. It doesn't run it.

**Why you can't hold a `MutexGuard` across `.await`:** The guard would be stored in the state machine, but the future might resume on a different thread.

**Why async adds zero-cost abstraction (mostly):** The state machine is a plain Rust enum on the stack. No heap allocation required for the future itself (though the runtime may box it).

**Why blocking the executor is so bad:** There's no preemption. If your state machine's `poll` method runs for 100ms doing CPU work, every other task on that executor thread is stuck for 100ms.

## A Complete Example: Seeing the Model in Action

```rust
use tokio::time::{sleep, Duration};

async fn fetch_user(id: u32) -> String {
    // Simulating an async I/O operation
    sleep(Duration::from_millis(100)).await;
    format!("User-{id}")
}

async fn fetch_email(user: &str) -> String {
    sleep(Duration::from_millis(50)).await;
    format!("{user}@example.com")
}

#[tokio::main]
async fn main() {
    // These futures are created but NOT started
    let user_future = fetch_user(1);
    let user2_future = fetch_user(2);

    // Now we drive them — sequentially
    let user1 = user_future.await;
    let user2 = user2_future.await;

    // Total time: ~200ms (sequential)
    println!("{user1}, {user2}");

    // To run concurrently, we need tokio::join!
    let (u3, u4) = tokio::join!(fetch_user(3), fetch_user(4));
    // Total time: ~100ms (concurrent)
    println!("{u3}, {u4}");

    // Sequential chain
    let user = fetch_user(5).await;
    let email = fetch_email(&user).await;
    println!("{user}: {email}");
}
```

Notice that `tokio::join!` runs futures concurrently — not in parallel (unless you're on a multi-threaded runtime and they get spawned onto different threads). Concurrent means "making progress on multiple things by interleaving." Parallel means "actually running at the same physical time on multiple cores."

## The Mental Model, Summarized

1. **Async functions create state machines**, they don't start work
2. **`.await` is a yield point** — "pause me here if not ready"
3. **The runtime polls futures** when they signal readiness
4. **Cooperation is mandatory** — blocking the thread blocks everything
5. **Futures compose** — a future can contain other futures (it's state machines all the way down)

If you internalize one thing from this lesson, make it this: every time you see `async fn`, think "this returns a state machine." Every time you see `.await`, think "this is where the state machine can pause and resume."

The rest of this course builds on this foundation. We'll dig into the `Future` trait itself next, then move to Tokio, then start building real systems. But without this mental model, everything else is just memorizing syntax.

Get this right, and async Rust clicks. I promise.
