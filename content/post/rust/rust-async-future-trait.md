---
title: "Lesson 2: The Future Trait — What .await actually does"
author: Atharva Pandey
series: "Async Rust Masterclass"
lesson: 2
keywords: [Rust, rust, async, tokio, future trait, poll, waker, await]
tags: [Rust tutorial, rust, async, tokio]
date: '2025-01-08T14:37:42.000Z'
---

After I understood the mental model from lesson 1, my next question was obvious: what happens *mechanically* when I write `.await`? I could accept "it's a state machine" as a hand-wave, but I wanted to see the gears turning.

Turns out, the answer is beautifully simple once you strip away the syntax sugar. The entire async machinery in Rust boils down to one trait, two types, and a contract.

## The Future Trait, For Real This Time

```rust
use std::pin::Pin;
use std::task::{Context, Poll};

pub trait Future {
    type Output;
    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output>;
}
```

Three things to note:

1. **`self: Pin<&mut Self>`** — The future is pinned in memory. We'll cover why in lesson 17. For now, just accept it.
2. **`cx: &mut Context<'_>`** — This carries a `Waker` that the future uses to signal "hey, I might be ready now, poll me again."
3. **`Poll<Self::Output>`** — Either `Ready(value)` or `Pending`.

That's it. That's the entire async abstraction in Rust.

## Building a Future by Hand

The best way to understand futures is to build one. Here's a future that completes after being polled a certain number of times:

```rust
use std::future::Future;
use std::pin::Pin;
use std::task::{Context, Poll};

struct CountdownFuture {
    remaining: u32,
}

impl Future for CountdownFuture {
    type Output = String;

    fn poll(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<String> {
        if self.remaining == 0 {
            Poll::Ready("Liftoff!".to_string())
        } else {
            println!("  countdown: {}", self.remaining);
            self.remaining -= 1;
            cx.waker().wake_by_ref(); // Schedule another poll immediately
            Poll::Pending
        }
    }
}

#[tokio::main]
async fn main() {
    let result = CountdownFuture { remaining: 3 }.await;
    println!("{result}");
}
```

Output:
```
  countdown: 3
  countdown: 2
  countdown: 1
Liftoff!
```

Every time we return `Poll::Pending`, we call `cx.waker().wake_by_ref()` first. This is critical — if you return `Pending` without waking, nobody will ever poll you again. Your future just... dies. Silently. No error. No warning. Just a task that never completes.

## The Waker Contract

The `Waker` is the mechanism by which futures communicate with the runtime. The contract is:

1. Before returning `Poll::Pending`, you **must** arrange for the waker to be called when progress can be made
2. Calling `wake()` tells the runtime "this task should be polled again"
3. Wakers are cheap to clone and can be sent across threads

In practice, leaf futures (the ones that actually do I/O) register the waker with the OS's event system. When a socket has data or a timer fires, the OS notifies the runtime, which calls `wake()`, which schedules the task for polling.

```rust
use std::future::Future;
use std::pin::Pin;
use std::task::{Context, Poll};
use std::time::{Duration, Instant};

struct Delay {
    when: Instant,
}

impl Delay {
    fn new(dur: Duration) -> Self {
        Delay {
            when: Instant::now() + dur,
        }
    }
}

impl Future for Delay {
    type Output = ();

    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<()> {
        if Instant::now() >= self.when {
            Poll::Ready(())
        } else {
            // In a real implementation, you'd register with a timer wheel.
            // This is a busy-poll hack for demonstration.
            let waker = cx.waker().clone();
            let when = self.when;
            std::thread::spawn(move || {
                let now = Instant::now();
                if now < when {
                    std::thread::sleep(when - now);
                }
                waker.wake();
            });
            Poll::Pending
        }
    }
}

#[tokio::main]
async fn main() {
    println!("waiting...");
    Delay::new(Duration::from_secs(1)).await;
    println!("done!");
}
```

This Delay future is deliberately naive — spawning a thread per poll is terrible. Real runtimes use efficient timer wheels and epoll/kqueue. But it shows the pattern: register the waker somewhere, and call `wake()` when ready.

## What .await Desugars To

When you write:

```rust
let value = some_future.await;
```

The compiler generates something like:

```rust
let value = {
    let mut future = some_future;
    let mut future = unsafe { Pin::new_unchecked(&mut future) };
    loop {
        match future.as_mut().poll(cx) {
            Poll::Ready(val) => break val,
            Poll::Pending => yield, // return Pending to OUR caller
        }
    }
};
```

The `yield` part is the key — when the inner future returns `Pending`, our enclosing future *also* returns `Pending` to whoever is polling *us*. This is how the suspension propagates up through the entire chain of futures until it reaches the executor.

The executor is the thing at the top of the chain that actually runs the event loop and calls `poll()` on root tasks.

## Composing Futures

Futures compose naturally because a future can poll other futures:

```rust
use std::future::Future;
use std::pin::Pin;
use std::task::{Context, Poll};

struct ThenFuture<A, B, F>
where
    A: Future,
    F: FnOnce(A::Output) -> B,
    B: Future,
{
    state: ThenState<A, B, F>,
}

enum ThenState<A, B, F>
where
    A: Future,
    F: FnOnce(A::Output) -> B,
    B: Future,
{
    First(A, Option<F>),
    Second(B),
    Done,
}

impl<A, B, F> Future for ThenFuture<A, B, F>
where
    A: Future,
    F: FnOnce(A::Output) -> B,
    B: Future,
    A: Unpin,
    B: Unpin,
{
    type Output = B::Output;

    fn poll(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<B::Output> {
        loop {
            match &mut self.state {
                ThenState::First(a, f) => {
                    match Pin::new(a).poll(cx) {
                        Poll::Ready(val) => {
                            let f = f.take().unwrap();
                            let b = f(val);
                            self.state = ThenState::Second(b);
                        }
                        Poll::Pending => return Poll::Pending,
                    }
                }
                ThenState::Second(b) => {
                    match Pin::new(b).poll(cx) {
                        Poll::Ready(val) => {
                            self.state = ThenState::Done;
                            return Poll::Ready(val);
                        }
                        Poll::Pending => return Poll::Pending,
                    }
                }
                ThenState::Done => panic!("polled after completion"),
            }
        }
    }
}
```

This is essentially what the compiler generates when you chain `.await` calls. The `async/await` syntax is sugar over manual state machine construction. Thank god for that sugar — writing these by hand is painful.

## Poll Rules You Must Follow

There's an informal contract around `Future::poll` that you violate at your own peril:

**Rule 1: After returning `Ready`, you must not be polled again.**
Some futures panic if polled after completion. The `FusedFuture` trait exists for futures that are safe to poll again (they just return `Pending` forever).

**Rule 2: If you return `Pending`, the waker must be registered.**
Otherwise your task is forgotten. This is the #1 bug in hand-rolled futures.

**Rule 3: `poll` must not block.**
If `poll` blocks the thread, the entire executor stalls. Always use async-aware I/O primitives.

**Rule 4: `poll` should be fast.**
Even if you're Ready, do the minimal work possible. Heavy computation should be offloaded to `tokio::task::spawn_blocking`.

## A Practical Example: Combining Manual Futures with Async

```rust
use std::future::Future;
use std::pin::Pin;
use std::task::{Context, Poll};
use tokio::time::{sleep, Duration};

/// A future that yields a value after a delay,
/// wrapping tokio's sleep internally.
struct DelayedValue<T> {
    value: Option<T>,
    delay: Pin<Box<tokio::time::Sleep>>,
}

impl<T> DelayedValue<T> {
    fn new(value: T, duration: Duration) -> Self {
        DelayedValue {
            value: Some(value),
            delay: Box::pin(sleep(duration)),
        }
    }
}

impl<T: Unpin> Future for DelayedValue<T> {
    type Output = T;

    fn poll(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<T> {
        // First, check if the delay is complete
        match self.delay.as_mut().poll(cx) {
            Poll::Pending => Poll::Pending,
            Poll::Ready(()) => {
                let value = self.value.take().expect("polled after completion");
                Poll::Ready(value)
            }
        }
    }
}

#[tokio::main]
async fn main() {
    let fast = DelayedValue::new("fast", Duration::from_millis(100));
    let slow = DelayedValue::new("slow", Duration::from_millis(500));

    // Run concurrently
    let (a, b) = tokio::join!(fast, slow);
    println!("{a}, {b}"); // prints after ~500ms
}
```

This pattern — wrapping an inner future and adding behavior — is extremely common. Timeouts, retries, logging, metrics: they're all futures wrapping other futures.

## The Relationship Between Future and IntoFuture

Rust 1.64 introduced `IntoFuture`, which lets you customize what happens when someone `.await`s your type:

```rust
use std::future::{Future, IntoFuture};
use tokio::time::{sleep, Duration};

struct DatabaseQuery {
    sql: String,
}

impl IntoFuture for DatabaseQuery {
    type Output = Vec<String>;
    type IntoFuture = Pin<Box<dyn Future<Output = Vec<String>> + Send>>;

    fn into_future(self) -> Self::IntoFuture {
        Box::pin(async move {
            // Simulate query execution
            sleep(Duration::from_millis(100)).await;
            vec![format!("result of: {}", self.sql)]
        })
    }
}

#[tokio::main]
async fn main() {
    let query = DatabaseQuery {
        sql: "SELECT * FROM users".to_string(),
    };

    // This calls into_future() automatically
    let results = query.await;
    println!("{results:?}");
}
```

`IntoFuture` is how builder patterns work with `.await` — you configure the builder, then `.await` it to execute.

## What I Wish Someone Had Told Me

When I was learning this, I kept getting confused about *where* poll gets called. The answer is simple but non-obvious:

- **Leaf futures** (timers, I/O, channels) — these are polled by the runtime and register wakers with OS-level event sources
- **Combinator futures** (join, select, your async fns) — these poll their child futures and propagate Pending upward
- **The executor** — this polls the root tasks in a loop driven by OS events

Your `async fn` is in the middle. It polls leaf futures below it and returns `Poll` to whatever is above it. It's state machines all the way down until you hit actual I/O, and event loops all the way up until you hit the executor.

Once this clicks — and it will click — async Rust stops being mysterious. It's just state machines polling state machines, woken up by the OS.

Next up: we'll set up Tokio and see how a real runtime drives all of this.
