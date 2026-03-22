---
title: "Lesson 1: Future Internals — Poll, Waker, Context"
author: Atharva Pandey
series: "Rust Async Internals & Runtime Design"
lesson: 1
keywords: [Rust, rust, async, runtime, executor, Future, Poll, Waker, Context, state machine]
tags: [Rust tutorial, rust, async, runtime, internals]
date: '2025-09-15T08:45:22.000Z'
---

I thought I understood Rust futures until I tried to implement one without `async`/`await`. Not a toy future that immediately returns `Ready`. A real future — one that yields, gets woken up, and resumes where it left off. That exercise broke every mental model I had and rebuilt it from scratch.

The `async`/`await` syntax is one of Rust's great lies. It looks simple. It *feels* like you're writing sequential code. Under the hood, the compiler is generating state machines, threading waker references through call graphs, and constructing self-referential structs that would make most C++ programmers nervous. Let's rip the lid off.

## What a Future Actually Is

Strip away the sugar and a `Future` is just a trait with one method:

```rust
pub trait Future {
    type Output;
    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output>;
}

pub enum Poll<T> {
    Ready(T),
    Pending,
}
```

That's it. One method, two possible return values. Either the future is done and has a value (`Ready(T)`), or it's not done yet (`Pending`). There's no "running" state, no "cancelled" state, no "errored" state. It's binary.

The critical thing most people miss: **futures do nothing unless polled**. In JavaScript, when you create a `Promise`, it starts executing immediately. In Rust, a future is lazy. You can create ten thousand futures and nothing happens until something calls `poll` on them.

```rust
// This creates a future but does NOT start any work
let my_future = async {
    expensive_computation().await;
    println!("done!");
};

// Nothing has happened yet. my_future is sitting there, inert.
// Only when an executor polls it does anything execute.
```

## The State Machine Behind async/await

When you write an `async` block or function, the compiler transforms it into an anonymous type that implements `Future`. Each `.await` point becomes a state transition. Here's a concrete example:

```rust
async fn fetch_and_process() -> String {
    let data = fetch_data().await;     // yield point 1
    let parsed = parse(data).await;    // yield point 2
    format!("Result: {}", parsed)
}
```

The compiler generates something conceptually like this:

```rust
enum FetchAndProcessState {
    // Initial state — hasn't started yet
    Start,
    // Waiting on fetch_data()
    WaitingFetch {
        fetch_future: FetchDataFuture,
    },
    // Waiting on parse()
    WaitingParse {
        parse_future: ParseFuture,
    },
    // Terminal state — already returned Ready
    Complete,
}

impl Future for FetchAndProcess {
    type Output = String;

    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<String> {
        // Safety: we carefully manage the pin projections
        let this = unsafe { self.get_unchecked_mut() };

        loop {
            match &mut this.state {
                FetchAndProcessState::Start => {
                    // Transition: start the fetch
                    let fetch_future = fetch_data();
                    this.state = FetchAndProcessState::WaitingFetch { fetch_future };
                }
                FetchAndProcessState::WaitingFetch { fetch_future } => {
                    // Pin the inner future and poll it
                    let pinned = unsafe { Pin::new_unchecked(fetch_future) };
                    match pinned.poll(cx) {
                        Poll::Ready(data) => {
                            let parse_future = parse(data);
                            this.state = FetchAndProcessState::WaitingParse { parse_future };
                        }
                        Poll::Pending => return Poll::Pending,
                    }
                }
                FetchAndProcessState::WaitingParse { parse_future } => {
                    let pinned = unsafe { Pin::new_unchecked(parse_future) };
                    match pinned.poll(cx) {
                        Poll::Ready(parsed) => {
                            this.state = FetchAndProcessState::Complete;
                            return Poll::Ready(format!("Result: {}", parsed));
                        }
                        Poll::Pending => return Poll::Pending,
                    }
                }
                FetchAndProcessState::Complete => {
                    panic!("polled after completion");
                }
            }
        }
    }
}
```

Every `async fn` becomes one of these state machines. Nested `.await` calls create nested state machines. An `async fn` that awaits ten things has eleven states. The compiler handles all of this, and it does it with zero heap allocations for the state machine itself — everything lives on the stack (or in whatever future is holding it).

## The Waker: How Futures Get Re-Polled

Here's the question nobody asks early enough: when a future returns `Pending`, who calls `poll` again? And *when*?

If the executor just spins in a loop polling every future constantly, that's busy-waiting. Terrible for CPU usage. You need a way for the future to say "hey, I'm ready to make progress — poll me again." That's the `Waker`.

```rust
// The Context passed to poll() contains a Waker
pub struct Context<'a> {
    waker: &'a Waker,
    // ... (other fields in newer Rust versions)
}
```

The `Waker` is essentially a function pointer (wrapped in a vtable) that, when called, tells the executor: "this specific future needs to be polled again." Let's look at how it actually works under the hood:

```rust
use std::task::{RawWaker, RawWakerVTable, Waker};

// A Waker is constructed from a RawWaker, which is:
// - A data pointer (void*)
// - A vtable with clone/wake/wake_by_ref/drop

fn create_waker(task_id: usize) -> Waker {
    // The raw data — in a real runtime this might be an Arc<Task>
    let data = task_id as *const ();

    // The vtable defines the behavior
    let vtable = &RawWakerVTable::new(
        // clone: duplicate the waker
        |data| {
            let task_id = data as usize;
            println!("Cloning waker for task {}", task_id);
            RawWaker::new(data, vtable_ref())
        },
        // wake: signal the executor (consumes the waker)
        |data| {
            let task_id = data as usize;
            println!("WAKE task {} — schedule it for re-polling", task_id);
            // In a real runtime: push task_id onto a ready queue
        },
        // wake_by_ref: signal without consuming
        |data| {
            let task_id = data as usize;
            println!("WAKE (by ref) task {}", task_id);
        },
        // drop: clean up
        |_data| {
            // nothing to clean up for a simple usize
        },
    );

    unsafe { Waker::from_raw(RawWaker::new(data, vtable)) }
}

fn vtable_ref() -> &'static RawWakerVTable {
    &RawWakerVTable::new(
        |d| RawWaker::new(d, vtable_ref()),
        |d| { let _ = d as usize; },
        |d| { let _ = d as usize; },
        |_| {},
    )
}
```

The flow looks like this:

1. Executor polls a future, passing a `Context` containing a `Waker`.
2. The future can't complete — maybe it's waiting on a socket.
3. The future *stores a clone of the waker* and returns `Pending`.
4. Later, when the socket is ready (signaled by epoll/kqueue/io_uring), the I/O reactor calls `waker.wake()`.
5. The executor sees the wake notification and re-polls that specific future.

## Building a Manual Future

Let's build a future by hand — no `async`, no `await`. A simple timer future that completes after a specified duration:

```rust
use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll, Waker};
use std::thread;
use std::time::Duration;

struct TimerFuture {
    shared_state: Arc<Mutex<SharedState>>,
}

struct SharedState {
    completed: bool,
    waker: Option<Waker>,
}

impl TimerFuture {
    fn new(duration: Duration) -> Self {
        let shared_state = Arc::new(Mutex::new(SharedState {
            completed: false,
            waker: None,
        }));

        let state_clone = shared_state.clone();
        // Spawn a real thread to act as our "I/O source"
        thread::spawn(move || {
            thread::sleep(duration);
            let mut state = state_clone.lock().unwrap();
            state.completed = true;
            // THIS is the critical line — wake the executor
            if let Some(waker) = state.waker.take() {
                waker.wake();
            }
        });

        TimerFuture { shared_state }
    }
}

impl Future for TimerFuture {
    type Output = ();

    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<()> {
        let mut state = self.shared_state.lock().unwrap();

        if state.completed {
            Poll::Ready(())
        } else {
            // Store the waker so the timer thread can wake us
            // We ALWAYS update the waker — the executor might have
            // moved us to a different thread since last poll
            state.waker = Some(cx.waker().clone());
            Poll::Pending
        }
    }
}
```

There's a subtlety here that trips up a lot of people: **you must always update the stored waker**. The executor is free to poll your future from different threads, and each poll might come with a different `Waker`. If you cache a stale waker, you'll wake the wrong thing (or nothing at all), and your future will hang forever.

## Pin and Why It Exists

I can't talk about future internals without addressing the `Pin<&mut Self>` in the `poll` signature. Why is it there?

Remember those compiler-generated state machines? They can contain self-referential data. Consider:

```rust
async fn problematic() {
    let data = vec![1, 2, 3];
    let reference = &data;    // reference points to data
    some_async_op().await;    // yield point!
    println!("{:?}", reference);
}
```

After the `.await`, both `data` and `reference` need to survive. They're stored as fields in the generated state machine struct. But `reference` points to `data` — which is another field in the same struct. If that struct gets moved in memory, `reference` would be a dangling pointer.

`Pin` prevents this. A `Pin<&mut T>` guarantees that `T` will never be moved again (unless `T: Unpin`, which opts out of this guarantee). The compiler can safely store self-referential pointers inside the state machine because it knows the future won't be relocated.

```rust
use std::marker::PhantomPinned;
use std::pin::Pin;

struct SelfReferential {
    data: String,
    // This would point into `data`
    ptr: *const String,
    _pin: PhantomPinned,
}

impl SelfReferential {
    fn new(s: &str) -> Pin<Box<Self>> {
        let mut boxed = Box::new(SelfReferential {
            data: s.to_string(),
            ptr: std::ptr::null(),
            _pin: PhantomPinned,
        });
        // Set the pointer to point to the data field
        let self_ptr: *const String = &boxed.data;
        unsafe {
            let mut_ref = Pin::as_mut(&mut Pin::new_unchecked(&mut *boxed));
            Pin::get_unchecked_mut(mut_ref).ptr = self_ptr;
        }
        unsafe { Pin::new_unchecked(boxed) }
    }
}
```

For the simple `TimerFuture` above, we don't have self-referential data, so it implements `Unpin` automatically and `Pin` is a no-op. But the compiler-generated futures from `async fn` are `!Unpin` — they *need* pinning.

## How Sizes Work

One more thing worth understanding: the size of a future is the size of its largest state variant. The compiler generates an enum, and enums in Rust are sized by their largest variant plus a discriminant.

```rust
// If fetch_data() returns a future that's 64 bytes,
// and parse() returns a future that's 128 bytes,
// then the generated enum is roughly:
// max(size_of(Start), size_of(WaitingFetch), size_of(WaitingParse)) + discriminant

// This means deeply nested async call chains can produce large futures.
// That's why you sometimes see:
let boxed: Pin<Box<dyn Future<Output = String>>> = Box::pin(fetch_and_process());
// Boxing moves the future to the heap, reducing stack pressure.
```

You can check future sizes at compile time:

```rust
use std::mem;

async fn small_future() -> u32 { 42 }

async fn bigger_future() -> Vec<u8> {
    let mut v = Vec::new();
    tokio::time::sleep(std::time::Duration::from_secs(1)).await;
    v.push(1);
    v
}

fn main() {
    // These might surprise you
    println!("small: {} bytes", mem::size_of_val(&small_future()));
    println!("bigger: {} bytes", mem::size_of_val(&bigger_future()));
}
```

## The Key Takeaways

Here's what you should carry forward from this lesson:

**Futures are inert state machines.** They don't do anything until polled. Each `.await` is a state transition. The compiler generates the state machine for you.

**Wakers are the notification mechanism.** When a future can't make progress, it stores the waker and returns `Pending`. When the underlying I/O (or timer, or channel) is ready, it calls `waker.wake()` to tell the executor to poll again.

**Pin exists for safety.** Compiler-generated futures can be self-referential. Pin prevents moves that would invalidate internal pointers.

**There is no magic.** The async runtime is just regular Rust code — a loop that polls futures, a mechanism for tracking which ones are ready, and an I/O event system for driving wakers. We'll build one ourselves in the next lesson.

Understanding these internals isn't academic. When you're debugging a future that hangs, or figuring out why your async code is slower than expected, or choosing between `Box<dyn Future>` and a concrete type — this is the knowledge that matters. The abstractions are good, but they leak. And when they leak, you need to know what's underneath.
