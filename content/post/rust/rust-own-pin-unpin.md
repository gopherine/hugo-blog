---
title: "Lesson 14: Pin and Unpin — Why Async Needs Them"
author: Atharva Pandey
series: "Rust Ownership & Lifetimes Masterclass"
lesson: 14
keywords: [Rust, rust, ownership, lifetimes, Pin, Unpin, async, futures]
tags: [Rust tutorial, rust, ownership, lifetimes]
date: '2024-06-14T20:05:00.000Z'
---

`Pin` is the concept that makes experienced Rust developers pause. Not because it's inherently complex — it's actually a pretty small API — but because understanding *why* it exists requires connecting several ideas: self-referential structs, async/await desugaring, and move semantics.

I struggled with Pin for months. Then I understood what async does under the hood, and Pin suddenly made perfect sense. So that's how I'm going to explain it.

## The Setup: What Async Really Does

When you write an async function:

```rust
async fn fetch_data() -> String {
    let url = String::from("https://example.com");
    let response = reqwest::get(&url).await;  // hypothetical
    format!("Got data from {}", url)
}
```

The compiler transforms this into a state machine — a struct that implements `Future`. That struct captures all local variables that are alive across `.await` points:

```rust
// ROUGHLY what the compiler generates (simplified)
enum FetchDataFuture {
    // State before first await
    State0 {
        url: String,
    },
    // State after first await — url is still alive
    State1 {
        url: String,
        response: Response,
    },
    Done,
}
```

Now here's the problem. What if the code looks like this?

```rust
async fn process() -> usize {
    let data = vec![1, 2, 3, 4, 5];
    let slice = &data[1..4];  // reference into data
    some_async_op().await;     // await point
    slice.len()                // uses slice after await
}
```

The generated future struct needs to hold both `data` AND `slice`. But `slice` is a reference into `data`. That's a self-referential struct — exactly what we said was impossible in lesson 10.

```rust
// ROUGHLY what the compiler generates
struct ProcessFuture {
    data: Vec<i32>,
    slice: &[i32],  // points into data — SELF REFERENCE
    // ... state tracking
}
```

If this struct moves, `slice` would point to invalid memory. The old `data` field is gone.

## Why Moves Are Dangerous for Futures

Futures get polled by the executor (tokio, async-std, etc.). Between polls, the future sits somewhere in memory. If the executor moves the future — say, to a different task queue or thread — the self-references inside it would break.

```rust
// Hypothetical danger
let mut future = process(); // future is at address 0x1000
                             // future.slice points to future.data at 0x1000

// If we move it...
let future2 = future;       // future2 is at address 0x2000
                             // future2.slice STILL points to 0x1000
                             // DANGLING!
```

## Enter Pin

`Pin<P>` wraps a pointer `P` (like `Box`, `&mut`, etc.) and guarantees that the pointed-to value **will not be moved**. Once something is pinned, you can't get `&mut T` out of it (which would let you `mem::swap` or `mem::replace` it to move it).

```rust
use std::pin::Pin;

fn main() {
    let mut data = Box::new(42);

    // Regular Box — we can move the inner value
    let inner = *data;  // moved out of the box

    let mut data = Box::new(42);
    let pinned: Pin<Box<i32>> = Box::pin(42);

    // Pinned — we can read but can't move the inner value out
    println!("Pinned value: {}", *pinned);

    // Can't do: let inner = *pinned;  — would need to move
    // Can't do: std::mem::swap(pinned.get_mut(), &mut other);
    // (actually for i32 we can, because i32 is Unpin — more on that below)
}
```

## Unpin: The Opt-Out

Most types in Rust are `Unpin` — they're safe to move even when pinned. Pinning an `i32` or a `String` is meaningless because they don't have self-references.

```rust
use std::pin::Pin;

fn main() {
    let mut s = Box::pin(String::from("hello"));

    // String is Unpin, so we can get &mut through Pin
    let s_mut: &mut String = Pin::as_mut(&mut s).get_mut();
    s_mut.push_str(" world");

    println!("{}", s);
}
```

`Unpin` is an auto-trait — almost everything implements it automatically. The exceptions:

- Compiler-generated future types (async blocks/functions)
- Types containing `PhantomPinned`
- Types that manually opt out

```rust
use std::marker::PhantomPinned;

struct CantMove {
    data: String,
    _pin: PhantomPinned,  // opts out of Unpin
}

// CantMove does NOT implement Unpin
// Once pinned, it cannot be moved
```

## Pin in Practice: The Future Trait

Here's why this all matters. The `Future` trait requires `Pin<&mut Self>`:

```rust
pub trait Future {
    type Output;
    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output>;
}
```

The executor calls `poll` with a `Pin<&mut Self>`. This guarantees that between poll calls, the future hasn't moved. Self-references inside the future remain valid.

## Implementing a Simple Future

```rust
use std::future::Future;
use std::pin::Pin;
use std::task::{Context, Poll};

struct CountDown {
    remaining: u32,
}

impl Future for CountDown {
    type Output = String;

    fn poll(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output> {
        if self.remaining == 0 {
            Poll::Ready(String::from("Done!"))
        } else {
            self.remaining -= 1;
            cx.waker().wake_by_ref();  // schedule another poll
            Poll::Pending
        }
    }
}

// CountDown is Unpin (no self-references), so Pin is basically a formality here
```

For simple futures without self-references, `Pin` is a no-op. You can access `&mut self` through `Pin` because the type is `Unpin`.

## When Pin Gets Real: Self-Referential Futures

The compiler-generated futures from async blocks are where Pin actually matters:

```rust
async fn example() {
    let buffer = vec![0u8; 1024];
    let slice = &buffer[..10];
    tokio::time::sleep(std::time::Duration::from_secs(1)).await;
    println!("Slice length: {}", slice.len());
}

// The generated future is !Unpin because it has self-references
// (slice points into buffer)
// Pin prevents it from being moved between the sleep and the println
```

You don't have to think about this when writing async code — the compiler handles it. But when you're *consuming* futures or building executor-like code, you need to understand Pin.

## Pinning to the Stack vs Heap

**Heap pinning** with `Box::pin`:

```rust
use std::pin::Pin;

fn main() {
    let pinned: Pin<Box<String>> = Box::pin(String::from("heap pinned"));
    println!("{}", pinned);
    // pinned lives on the heap — can't be moved
    // (well, String is Unpin so it could be, but !Unpin types couldn't)
}
```

**Stack pinning** with `pin!` macro (std since Rust 1.68):

```rust
use std::pin::pin;

fn main() {
    let pinned = pin!(String::from("stack pinned"));
    // pinned: Pin<&mut String>
    // The String lives on the stack, but the Pin prevents moving it
    println!("{}", pinned);
}
```

Stack pinning is cheaper (no allocation) but the value can't outlive the current stack frame.

## The pin_mut! Pattern in Async

When working with futures manually:

```rust
use std::pin::pin;
use std::future::Future;

async fn do_work() -> i32 {
    42
}

async fn run() {
    let future = do_work();
    let pinned = pin!(future);
    // pinned: Pin<&mut impl Future<Output = i32>>
    // Now you can pass it to something that needs Pin<&mut dyn Future>
    let result = pinned.await;
    println!("{}", result);
}
```

## Practical Rules

1. **Writing async functions/blocks:** Pin is invisible. The compiler handles everything.

2. **Calling `.await`:** Pin is invisible. Just use `.await`.

3. **Storing futures in structs:** You'll need `Pin<Box<dyn Future>>`:

```rust
use std::pin::Pin;
use std::future::Future;

struct TaskQueue {
    tasks: Vec<Pin<Box<dyn Future<Output = ()>>>>,
}

impl TaskQueue {
    fn add<F: Future<Output = ()> + 'static>(&mut self, future: F) {
        self.tasks.push(Box::pin(future));
    }
}
```

4. **Writing your own `Future` impl:** Accept `Pin<&mut Self>`. If your future is `Unpin` (no self-references), you can access `&mut self` freely. If it's `!Unpin`, you need `unsafe` to access the inner fields through projection.

5. **If you're confused:** Just use `Box::pin()` and move on. Understand the concept, optimize later.

## Pin Projection

This is advanced territory but worth mentioning. When you have a pinned struct and want to access its fields:

```rust
use std::pin::Pin;

struct MyFuture {
    value: String,
    count: u32,
}

impl MyFuture {
    // Safe because String is Unpin — pinning the struct
    // doesn't need to pin the String
    fn value(self: Pin<&mut Self>) -> &mut String {
        // Safety: String is Unpin, so accessing it through Pin is fine
        unsafe { &mut self.get_unchecked_mut().value }
    }

    fn count(self: Pin<&mut Self>) -> &mut u32 {
        unsafe { &mut self.get_unchecked_mut().count }
    }
}
```

In practice, use the `pin-project` crate — it generates safe projections without manual `unsafe`:

```rust
// With pin-project crate:
// use pin_project::pin_project;
//
// #[pin_project]
// struct MyFuture {
//     #[pin]
//     inner_future: SomeOtherFuture,  // pinned — might be !Unpin
//     count: u32,                      // not pinned — always Unpin
// }
```

## The One-Sentence Summary

Pin guarantees a value won't move in memory, which is necessary for self-referential types like compiler-generated async futures. Most of the time it's invisible — the compiler and async runtime handle it. When you do encounter it, `Box::pin()` is your friend.

That's less scary than the reputation suggests. Pin isn't about complexity — it's about making one specific guarantee so that async/await can exist safely.
