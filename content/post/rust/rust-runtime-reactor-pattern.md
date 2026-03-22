---
title: "Lesson 5: The Reactor Pattern — How Tokio actually works"
author: Atharva Pandey
series: "Rust Async Internals & Runtime Design"
lesson: 5
keywords: [Rust, rust, async, runtime, reactor, Tokio, driver, I/O driver, timer, event loop]
tags: [Rust tutorial, rust, async, runtime, internals]
date: '2025-09-24T09:27:13.000Z'
---

I read the Tokio source code on a Sunday afternoon, expecting to find something inscrutable. Layers of unsafe code, impenetrable abstractions, the kind of thing that makes you question your career choices. What I actually found was a clean, well-documented reactor implementation that I could follow. Not easily — but followably. The architecture is elegant once you see how the pieces connect.

This lesson is about that architecture. Not Tokio's API — you already know how to use `tokio::spawn` and `TcpStream`. This is about what happens *underneath* when you call those functions. The reactor pattern, the I/O driver, the timer wheel, and how they all feed into the executor.

## The Reactor Pattern

The reactor pattern is a design pattern for handling I/O events. The idea is simple:

1. **Register** interest in I/O events (socket readable, timer expired, etc.).
2. **Wait** for one or more events to occur (blocking the thread efficiently).
3. **Dispatch** each event to the appropriate handler.
4. Repeat.

In an async runtime, "dispatch" means "wake the future that's waiting on this event." The reactor is the bridge between OS-level event notifications and Rust's `Waker` mechanism.

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│   Futures    │────>│   Reactor    │────>│   OS Events  │
│ (register    │     │ (maps events │     │ (epoll/kqueue│
│  wakers)     │<────│  to wakers)  │<────│  /io_uring)  │
└─────────────┘     └──────────────┘     └──────────────┘
       ▲                                        │
       │              ┌──────────────┐          │
       └──────────────│   Executor   │──────────┘
                      │ (polls ready │
                      │  futures)    │
                      └──────────────┘
```

## Inside Tokio's Architecture

Tokio's runtime has three major subsystems:

**1. The I/O Driver** — Wraps `mio::Poll` and maps I/O events to wakers. When a `TcpStream` future returns `Pending`, it registers with the I/O driver. When the socket becomes readable, the driver wakes the future.

**2. The Timer Driver** — Manages `sleep` and `timeout` futures using a hierarchical timer wheel. More efficient than having one OS timer per future.

**3. The Task Scheduler** — The executor we built in lesson 2, but multi-threaded with work-stealing. Polls ready futures and manages the task lifecycle.

Let's trace through a real scenario to see how these interact.

## Tracing a TCP Read Through Tokio

Say you write this:

```rust
use tokio::net::TcpStream;
use tokio::io::AsyncReadExt;

async fn handle_connection(mut stream: TcpStream) {
    let mut buf = [0u8; 1024];
    let n = stream.read(&mut buf).await.unwrap();
    println!("Read {} bytes", n);
}
```

Here's what happens step by step:

**Step 1: TcpStream creation.** When you create a `TcpStream`, Tokio registers the underlying socket with the I/O driver (via `mio::Poll`). It gets a unique `Token` that identifies this socket in the event system.

**Step 2: First poll of `read`.** The executor polls the `read` future. Internally, `TcpStream::read` tries a non-blocking `read()` syscall. If data is available, great — return `Ready`. If not (returns `EWOULDBLOCK`), it stores the current waker in the I/O driver's registration for this socket, and returns `Pending`.

```rust
// Simplified version of what TcpStream::poll_read does
fn poll_read(
    self: Pin<&mut Self>,
    cx: &mut Context<'_>,
    buf: &mut ReadBuf<'_>,
) -> Poll<io::Result<()>> {
    loop {
        // Check if the I/O driver says we're ready
        let ready = ready!(self.io.poll_read_ready(cx)?);

        match self.io.try_read(buf) {
            Ok(()) => return Poll::Ready(Ok(())),
            Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                // Not actually ready — clear the readiness and wait again
                ready.clear_ready();
                // poll_read_ready registered the waker, so we return Pending
            }
            Err(e) => return Poll::Ready(Err(e)),
        }
    }
}
```

**Step 3: The executor parks.** After polling all ready futures, the executor has nothing to do. In Tokio's multi-threaded runtime, one thread becomes the "parker" — it runs the I/O driver's event loop. The other threads try to steal work or park themselves.

**Step 4: Data arrives on the socket.** The OS signals the event (via epoll/kqueue). The I/O driver picks it up, finds the waker registered for that socket's `Token`, and calls `waker.wake()`.

**Step 5: Task gets re-scheduled.** The waker pushes the task onto the executor's ready queue. A worker thread picks it up and re-polls the future.

**Step 6: Read succeeds.** This time, the non-blocking `read()` returns data. The future returns `Ready(Ok(()))` and the task completes.

## The I/O Driver in Detail

Let's look at how the I/O driver manages registrations:

```rust
use mio::{Events, Interest, Poll, Token};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::task::Waker;

// Each registered I/O resource gets one of these
struct IoRegistration {
    token: Token,
    // Readiness state — tracks what the OS says is ready
    readiness: u8, // bitmask: READABLE | WRITABLE
    // Wakers waiting for specific events
    read_waker: Option<Waker>,
    write_waker: Option<Waker>,
}

struct IoDriver {
    poll: Poll,
    registrations: Mutex<HashMap<Token, IoRegistration>>,
    events: Mutex<Events>,
}

impl IoDriver {
    fn new() -> Self {
        IoDriver {
            poll: Poll::new().unwrap(),
            registrations: Mutex::new(HashMap::new()),
            events: Mutex::new(Events::with_capacity(1024)),
        }
    }

    // Called by the runtime's event loop thread
    fn turn(&self) {
        let mut events = self.events.lock().unwrap();
        // This blocks until events arrive or timeout
        self.poll.poll(&mut events, None).unwrap();

        let mut registrations = self.registrations.lock().unwrap();
        for event in events.iter() {
            if let Some(reg) = registrations.get_mut(&event.token()) {
                if event.is_readable() {
                    reg.readiness |= 0x01; // READABLE
                    if let Some(waker) = reg.read_waker.take() {
                        waker.wake();
                    }
                }
                if event.is_writable() {
                    reg.readiness |= 0x02; // WRITABLE
                    if let Some(waker) = reg.write_waker.take() {
                        waker.wake();
                    }
                }
            }
        }
    }

    // Called by I/O futures when they need to wait for readiness
    fn poll_readable(
        &self,
        token: Token,
        waker: &Waker,
    ) -> Poll<()> {
        let mut registrations = self.registrations.lock().unwrap();
        let reg = registrations.get_mut(&token).unwrap();

        if reg.readiness & 0x01 != 0 {
            // Already readable — clear the flag and return Ready
            reg.readiness &= !0x01;
            Poll::Ready(())
        } else {
            // Not ready — store the waker
            reg.read_waker = Some(waker.clone());
            Poll::Pending
        }
    }
}
```

Notice the readiness flag. When the OS says a socket is readable, we set the flag AND wake the future. But the future might not poll immediately — by the time it does, the readiness state is already cached. This avoids a race between the event arriving and the future checking for it.

## The Timer Wheel

Tokio doesn't use `epoll`/`kqueue` for timers. It uses a hierarchical timer wheel — a data structure that can efficiently manage millions of timers with O(1) insertion and O(1) expiration for most cases.

The basic idea:

```rust
use std::collections::BTreeMap;
use std::task::Waker;
use std::time::{Duration, Instant};

// Simplified timer wheel — real Tokio uses a hierarchical structure
struct TimerWheel {
    // Timers ordered by expiration time
    timers: BTreeMap<Instant, Vec<Waker>>,
}

impl TimerWheel {
    fn new() -> Self {
        TimerWheel {
            timers: BTreeMap::new(),
        }
    }

    // Register a timer
    fn add_timer(&mut self, deadline: Instant, waker: Waker) {
        self.timers
            .entry(deadline)
            .or_insert_with(Vec::new)
            .push(waker);
    }

    // Fire all expired timers, return time until next expiration
    fn process_timers(&mut self) -> Option<Duration> {
        let now = Instant::now();
        let mut expired_keys = Vec::new();

        for (deadline, wakers) in self.timers.iter() {
            if *deadline <= now {
                for waker in wakers {
                    waker.wake_by_ref();
                }
                expired_keys.push(*deadline);
            } else {
                // First non-expired timer — return time until it fires
                return Some(*deadline - now);
            }
        }

        for key in expired_keys {
            self.timers.remove(&key);
        }

        None // no pending timers
    }
}
```

When `tokio::time::sleep(Duration::from_secs(5))` returns `Pending`, it registers a waker with the timer wheel at `Instant::now() + 5 seconds`. The runtime uses the next timer expiration as the timeout for `epoll_wait`/`kevent`. When the timeout fires, the timer wheel processes expired timers and wakes the corresponding futures.

This is cleverer than using OS timers because:
- Creating a `sleep` future is just inserting into a data structure — no syscall.
- You can have millions of active timers without kernel overhead.
- The timer resolution is controlled by the runtime, not the OS scheduler.

## The Runtime Loop

Now let's put it all together. Here's what Tokio's runtime loop looks like conceptually:

```rust
fn runtime_thread_loop(
    executor: &Executor,
    io_driver: &IoDriver,
    timer_wheel: &mut TimerWheel,
) {
    loop {
        // 1. Poll all ready tasks
        while let Some(task) = executor.pop_ready_task() {
            let waker = task.waker();
            let mut cx = Context::from_waker(&waker);
            match task.poll(&mut cx) {
                Poll::Ready(()) => {
                    executor.complete_task(task);
                }
                Poll::Pending => {
                    // Task will be re-queued by its waker
                }
            }
        }

        // 2. Process expired timers
        let next_timer = timer_wheel.process_timers();

        // 3. Check for I/O events
        // Use the next timer expiration as the poll timeout
        // so we wake up in time to fire the timer
        let timeout = next_timer.unwrap_or(Duration::from_secs(60));
        io_driver.turn_with_timeout(timeout);

        // 4. If new tasks were woken, loop back to step 1
        // If no tasks remain, exit
        if executor.is_empty() {
            break;
        }
    }
}
```

The key insight: **the I/O driver and timer wheel integrate into the executor's main loop.** There isn't a separate thread for I/O and another for timers. The executor thread itself drives I/O when it has no tasks to poll. This minimizes context switching and keeps the hot path on a single thread.

## Current-Thread vs Multi-Thread Runtime

Tokio offers two runtime flavors:

```rust
// Single-threaded — everything on one thread
#[tokio::main(flavor = "current_thread")]
async fn main() {
    // One thread runs executor + I/O driver + timer wheel
}

// Multi-threaded — work-stealing thread pool
#[tokio::main(flavor = "multi_thread", worker_threads = 4)]
async fn main() {
    // 4 worker threads, each with its own I/O driver
    // Tasks can migrate between threads via work-stealing
}
```

The current-thread runtime is simpler and has lower overhead for I/O-bound workloads. No synchronization needed, no atomic operations on the task queue. It's what you want for single-connection proxies, CLI tools, or when you're manually managing thread affinity.

The multi-threaded runtime adds work-stealing (covered in lesson 6) and per-thread I/O drivers. When a task is stolen by another thread, it takes its waker with it — the waker now points to the new thread's ready queue. The I/O registration stays on the original thread's driver, but the waker correctly routes the task to wherever it ended up.

## Thread-Local I/O Resources

One consequence of Tokio's per-thread I/O driver design: I/O resources are tied to a specific runtime. You can't create a `TcpStream` on one runtime and use it on another. Tokio enforces this at compile time with the `'static` bound and at runtime with panics.

```rust
// This works fine
#[tokio::main]
async fn main() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:8080")
        .await
        .unwrap();
    // listener is tied to this runtime's I/O driver
}

// This panics at runtime
fn bad_idea() {
    let rt1 = tokio::runtime::Runtime::new().unwrap();
    let rt2 = tokio::runtime::Runtime::new().unwrap();

    let stream = rt1.block_on(async {
        tokio::net::TcpStream::connect("127.0.0.1:8080")
            .await
            .unwrap()
    });

    // Trying to use rt1's stream on rt2 — panic!
    rt2.block_on(async move {
        use tokio::io::AsyncReadExt;
        let mut buf = [0u8; 1024];
        // This will panic because the I/O resource is registered
        // with rt1's driver, not rt2's
        stream.read(&mut buf).await.unwrap();
    });
}
```

This isn't a bug — it's a deliberate design decision. Allowing I/O resources to migrate between runtimes would require global synchronization on every I/O operation, which kills performance.

## The Lifecycle of a tokio::spawn

Let me trace through `tokio::spawn` to show how all the pieces fit:

```rust
#[tokio::main]
async fn main() {
    let handle = tokio::spawn(async {
        tokio::time::sleep(Duration::from_secs(1)).await;
        println!("done!");
        42
    });

    let result = handle.await.unwrap();
    assert_eq!(result, 42);
}
```

1. `tokio::spawn` wraps the future in a `Task`, allocates it on the heap (via `Box::pin`), and pushes it onto the current thread's local run queue.

2. The executor polls the task. The `sleep` future registers with the timer wheel (deadline = now + 1 second) and returns `Pending`.

3. The executor has no more ready tasks. It calls the I/O driver with a timeout of 1 second (the next timer deadline).

4. 1 second passes. The I/O driver returns (timeout). The timer wheel fires, waking the sleep future's waker.

5. The task is pushed back onto the ready queue. The executor polls it. `sleep` returns `Ready(())`. The `println!` runs. The async block returns `42`.

6. The task completes. The `JoinHandle` is woken, `handle.await` returns `Ok(42)`.

Clean, predictable, and entirely driven by the reactor pattern. No magic — just a well-orchestrated loop of poll, wait, wake, repeat.

Understanding this flow is what separates people who use Tokio from people who debug Tokio. When a future hangs, you can now ask the right questions: Is the waker being registered? Is the I/O driver running? Is the timer wheel processing? The answer is always somewhere in this loop.
