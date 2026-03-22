---
title: "Lesson 7: Designing a Custom Async Runtime — When Tokio isn't enough"
author: Atharva Pandey
series: "Rust Async Internals & Runtime Design"
lesson: 7
keywords: [Rust, rust, async, runtime, custom runtime, thread-per-core, executor, reactor, design]
tags: [Rust tutorial, rust, async, runtime, internals]
date: '2025-09-30T11:20:06.000Z'
---

A colleague once asked me, "Why would anyone build a custom async runtime when Tokio exists?" Fair question. Tokio is battle-tested, well-maintained, and fast. For 95% of use cases, it's the right answer. But I've now been in three situations where it wasn't — and each one taught me something about what a runtime actually does.

The first was a latency-sensitive trading system where work-stealing's cache invalidation was unacceptable. The second was an embedded system with no allocator. The third was a specialized database engine where we needed precise control over I/O scheduling. Each time, understanding how to build a runtime from the ground up saved the project.

Let's design one. Not a toy — a real runtime with I/O, timers, and task scheduling.

## When to Build Your Own

Before writing a single line of code, let me be blunt about when this makes sense:

**Thread-per-core architecture.** You want tasks pinned to specific cores with no migration. Tokio's work-stealing scheduler moves tasks between threads, which invalidates caches. For latency-sensitive workloads (trading, game servers, real-time audio), this is a problem.

**Custom I/O backends.** You need io_uring exclusively, or you're targeting an embedded platform with a non-standard I/O mechanism. Tokio's I/O driver is built on mio, which means epoll/kqueue.

**Minimal footprint.** You're on a microcontroller or WebAssembly target where Tokio's dependencies are too heavy.

**Specialized scheduling.** You need priority queues, deadline scheduling, or fair-share scheduling that Tokio doesn't offer.

If none of these apply, use Tokio. Seriously.

## The Architecture

Our runtime will have four components:

```
┌────────────────────────────────────────────────────────┐
│                     Runtime                             │
│                                                         │
│  ┌───────────┐  ┌────────────┐  ┌──────────┐  ┌──────┐│
│  │  Executor │  │  I/O       │  │  Timer   │  │ Spawn││
│  │  (task    │  │  Reactor   │  │  Wheel   │  │ API  ││
│  │  polling) │  │  (epoll)   │  │          │  │      ││
│  └───────────┘  └────────────┘  └──────────┘  └──────┘│
└────────────────────────────────────────────────────────┘
```

Let's build each piece.

## Step 1: Task Representation

A task is a heap-allocated future with metadata for scheduling:

```rust
use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll, Wake, Waker};

struct Task {
    /// The future being driven
    future: Mutex<Pin<Box<dyn Future<Output = ()> + Send>>>,
    /// Handle to the runtime for re-scheduling
    runtime: Arc<RuntimeInner>,
    /// Task ID for debugging
    id: u64,
}

impl Wake for Task {
    fn wake(self: Arc<Self>) {
        // When woken, push ourselves back onto the ready queue
        self.runtime.schedule(self.clone());
    }

    fn wake_by_ref(self: &Arc<Self>) {
        self.runtime.schedule(self.clone());
    }
}
```

One design decision here: we're using `Mutex<Pin<Box<dyn Future>>>`. The `Mutex` is needed because wakers can fire from any thread, but we only poll the future from the executor thread. In a thread-per-core design, you could use `RefCell` instead — but then your futures can't be `Send`, which is a tradeoff.

For a thread-per-core runtime:

```rust
use std::cell::RefCell;

struct LocalTask {
    future: RefCell<Pin<Box<dyn Future<Output = ()>>>>,
    // No Send bound needed — task never leaves this thread
    runtime: Rc<LocalRuntimeInner>,
    id: u64,
}
```

## Step 2: The Task Queue

For a thread-per-core runtime, we use a simple `VecDeque` — no synchronization needed:

```rust
use std::collections::VecDeque;
use std::cell::RefCell;
use std::rc::Rc;

struct LocalQueue {
    ready: RefCell<VecDeque<Rc<LocalTask>>>,
}

impl LocalQueue {
    fn new() -> Self {
        LocalQueue {
            ready: RefCell::new(VecDeque::new()),
        }
    }

    fn push(&self, task: Rc<LocalTask>) {
        self.ready.borrow_mut().push_back(task);
    }

    fn pop(&self) -> Option<Rc<LocalTask>> {
        self.ready.borrow_mut().pop_front()
    }

    fn is_empty(&self) -> bool {
        self.ready.borrow().is_empty()
    }
}
```

For a multi-threaded runtime with work-stealing, you'd use `crossbeam-deque` as we discussed in lesson 6.

## Step 3: The I/O Reactor

The reactor translates OS I/O events into waker notifications. Here's a production-grade version using mio:

```rust
use mio::{Events, Interest, Poll, Token, Registry};
use std::collections::HashMap;
use std::io;
use std::sync::{Arc, Mutex};
use std::task::Waker;

struct IoSource {
    read_waker: Option<Waker>,
    write_waker: Option<Waker>,
    is_ready_read: bool,
    is_ready_write: bool,
}

struct Reactor {
    poll: Poll,
    sources: HashMap<Token, IoSource>,
    next_token: usize,
}

impl Reactor {
    fn new() -> io::Result<Self> {
        Ok(Reactor {
            poll: Poll::new()?,
            sources: HashMap::new(),
            next_token: 0,
        })
    }

    /// Register a new I/O source and return its token
    fn register(
        &mut self,
        source: &mut impl mio::event::Source,
        interest: Interest,
    ) -> io::Result<Token> {
        let token = Token(self.next_token);
        self.next_token += 1;

        self.poll
            .registry()
            .register(source, token, interest)?;

        self.sources.insert(
            token,
            IoSource {
                read_waker: None,
                write_waker: None,
                is_ready_read: false,
                is_ready_write: false,
            },
        );

        Ok(token)
    }

    /// Register a waker for read readiness
    fn poll_readable(&mut self, token: Token, waker: &Waker) -> Poll<()> {
        let source = self.sources.get_mut(&token).unwrap();
        if source.is_ready_read {
            source.is_ready_read = false;
            Poll::Ready(())
        } else {
            source.read_waker = Some(waker.clone());
            Poll::Pending
        }
    }

    /// Register a waker for write readiness
    fn poll_writable(&mut self, token: Token, waker: &Waker) -> Poll<()> {
        let source = self.sources.get_mut(&token).unwrap();
        if source.is_ready_write {
            source.is_ready_write = false;
            Poll::Ready(())
        } else {
            source.write_waker = Some(waker.clone());
            Poll::Pending
        }
    }

    /// Process I/O events — called from the main loop
    fn turn(&mut self, timeout: Option<std::time::Duration>) -> io::Result<()> {
        let mut events = Events::with_capacity(256);
        self.poll.poll(&mut events, timeout)?;

        for event in events.iter() {
            if let Some(source) = self.sources.get_mut(&event.token()) {
                if event.is_readable() {
                    source.is_ready_read = true;
                    if let Some(waker) = source.read_waker.take() {
                        waker.wake();
                    }
                }
                if event.is_writable() {
                    source.is_ready_write = true;
                    if let Some(waker) = source.write_waker.take() {
                        waker.wake();
                    }
                }
            }
        }

        Ok(())
    }
}
```

## Step 4: The Timer Wheel

For our custom runtime, a simple sorted timer structure works well enough for moderate numbers of timers. For millions of timers, you'd want a hierarchical wheel like Tokio's.

```rust
use std::collections::BinaryHeap;
use std::cmp::Reverse;
use std::task::Waker;
use std::time::Instant;

struct TimerEntry {
    deadline: Instant,
    waker: Waker,
}

impl PartialEq for TimerEntry {
    fn eq(&self, other: &Self) -> bool {
        self.deadline == other.deadline
    }
}
impl Eq for TimerEntry {}
impl PartialOrd for TimerEntry {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}
impl Ord for TimerEntry {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        // Reverse so the earliest deadline is at the top of the heap
        other.deadline.cmp(&self.deadline)
    }
}

struct TimerWheel {
    timers: BinaryHeap<TimerEntry>,
}

impl TimerWheel {
    fn new() -> Self {
        TimerWheel {
            timers: BinaryHeap::new(),
        }
    }

    fn add(&mut self, deadline: Instant, waker: Waker) {
        self.timers.push(TimerEntry { deadline, waker });
    }

    /// Fire expired timers, return duration until next expiration
    fn process(&mut self) -> Option<std::time::Duration> {
        let now = Instant::now();
        while let Some(entry) = self.timers.peek() {
            if entry.deadline <= now {
                let entry = self.timers.pop().unwrap();
                entry.waker.wake();
            } else {
                return Some(entry.deadline - now);
            }
        }
        None
    }
}
```

## Step 5: Tying It All Together

Now the main runtime struct that orchestrates everything:

```rust
use std::cell::RefCell;
use std::rc::Rc;
use std::time::Duration;

struct Runtime {
    queue: LocalQueue,
    reactor: RefCell<Reactor>,
    timers: RefCell<TimerWheel>,
    task_count: RefCell<usize>,
    next_task_id: RefCell<u64>,
}

impl Runtime {
    fn new() -> io::Result<Rc<Self>> {
        Ok(Rc::new(Runtime {
            queue: LocalQueue::new(),
            reactor: RefCell::new(Reactor::new()?),
            timers: RefCell::new(TimerWheel::new()),
            task_count: RefCell::new(0),
            next_task_id: RefCell::new(0),
        }))
    }

    fn spawn(&self, future: impl Future<Output = ()> + 'static) {
        let mut id = self.next_task_id.borrow_mut();
        let task_id = *id;
        *id += 1;

        *self.task_count.borrow_mut() += 1;

        // For a thread-per-core runtime, tasks don't need Send
        // This is one of the big advantages
        let task = Rc::new(LocalTaskImpl {
            future: RefCell::new(Box::pin(future)),
            id: task_id,
        });

        self.queue.push(task);
    }

    fn run(&self) {
        loop {
            // 1. Poll all ready tasks
            let mut made_progress = true;
            while made_progress {
                made_progress = false;
                while let Some(task) = self.queue.pop() {
                    made_progress = true;

                    // Create a waker that re-enqueues this task
                    let waker = task_waker(task.clone(), &self.queue);
                    let mut cx = Context::from_waker(&waker);

                    let mut future = task.future.borrow_mut();
                    match future.as_mut().poll(&mut cx) {
                        Poll::Ready(()) => {
                            *self.task_count.borrow_mut() -= 1;
                        }
                        Poll::Pending => {
                            // Will be re-queued by waker
                        }
                    }
                }
            }

            // 2. All tasks complete?
            if *self.task_count.borrow() == 0 {
                break;
            }

            // 3. Process timers
            let next_timer = self.timers.borrow_mut().process();

            // 4. Wait for I/O events (using next timer as timeout)
            let timeout = next_timer.unwrap_or(Duration::from_millis(100));
            self.reactor.borrow_mut().turn(Some(timeout)).unwrap();
        }
    }
}

// Helper to create a waker for a local task
fn task_waker(task: Rc<LocalTaskImpl>, queue: &LocalQueue) -> Waker {
    // In practice, you'd use a proper Waker implementation
    // For thread-per-core, you can use unsafe RawWaker with Rc
    // Here's a simplified version using the Wake trait approach

    // Note: This requires the task to be Send for Arc<Wake>
    // For a true thread-per-core runtime, you'd use RawWaker
    // to avoid the Send requirement
    todo!("implement with RawWaker for non-Send tasks")
}
```

Let me show the proper `RawWaker` implementation for non-Send tasks — this is the key enabler for thread-per-core runtimes:

```rust
use std::task::{RawWaker, RawWakerVTable, Waker};

fn create_local_waker(
    task: Rc<LocalTaskImpl>,
    queue: Rc<LocalQueue>,
) -> Waker {
    struct WakerData {
        task: Rc<LocalTaskImpl>,
        queue: Rc<LocalQueue>,
    }

    let data = Box::new(WakerData {
        task,
        queue,
    });
    let ptr = Box::into_raw(data) as *const ();

    static VTABLE: RawWakerVTable = RawWakerVTable::new(
        // clone
        |ptr| {
            let data = unsafe { &*(ptr as *const WakerData) };
            let cloned = Box::new(WakerData {
                task: data.task.clone(),
                queue: data.queue.clone(),
            });
            RawWaker::new(Box::into_raw(cloned) as *const (), &VTABLE)
        },
        // wake (takes ownership)
        |ptr| {
            let data = unsafe { Box::from_raw(ptr as *mut WakerData) };
            data.queue.push(data.task.clone());
        },
        // wake_by_ref
        |ptr| {
            let data = unsafe { &*(ptr as *const WakerData) };
            data.queue.push(data.task.clone());
        },
        // drop
        |ptr| {
            unsafe { drop(Box::from_raw(ptr as *mut WakerData)) };
        },
    );

    // Safety: WakerData is managed correctly through the vtable
    unsafe { Waker::from_raw(RawWaker::new(ptr, &VTABLE)) }

    // This is a simplified version - we define WakerData inside
    // the function for encapsulation
    struct WakerData {
        task: Rc<LocalTaskImpl>,
        queue: Rc<LocalQueue>,
    }
}
```

## Adding Async I/O Types

A runtime is useless without async I/O types. Here's how to build an async TCP listener:

```rust
use mio::net::TcpListener as MioTcpListener;
use std::io;
use std::net::SocketAddr;

struct AsyncTcpListener {
    inner: MioTcpListener,
    token: Token,
}

impl AsyncTcpListener {
    fn bind(addr: SocketAddr, runtime: &Runtime) -> io::Result<Self> {
        let mut listener = MioTcpListener::bind(addr)?;
        let token = runtime
            .reactor
            .borrow_mut()
            .register(&mut listener, Interest::READABLE)?;

        Ok(AsyncTcpListener {
            inner: listener,
            token,
        })
    }

    async fn accept(&self, runtime: &Runtime) -> io::Result<(mio::net::TcpStream, SocketAddr)> {
        loop {
            // Try non-blocking accept
            match self.inner.accept() {
                Ok(result) => return Ok(result),
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                    // Not ready — wait for the reactor
                    WaitReadable {
                        token: self.token,
                        runtime,
                    }
                    .await;
                }
                Err(e) => return Err(e),
            }
        }
    }
}

struct WaitReadable<'a> {
    token: Token,
    runtime: &'a Runtime,
}

impl<'a> Future for WaitReadable<'a> {
    type Output = ();

    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<()> {
        self.runtime
            .reactor
            .borrow_mut()
            .poll_readable(self.token, cx.waker())
    }
}
```

## Adding Sleep Support

```rust
struct Sleep {
    deadline: Instant,
    registered: bool,
}

impl Sleep {
    fn new(duration: Duration) -> Self {
        Sleep {
            deadline: Instant::now() + duration,
            registered: false,
        }
    }
}

impl Future for Sleep {
    type Output = ();

    fn poll(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<()> {
        if Instant::now() >= self.deadline {
            return Poll::Ready(());
        }

        if !self.registered {
            // Access the runtime's timer wheel via thread-local
            CURRENT_RUNTIME.with(|rt| {
                rt.borrow()
                    .as_ref()
                    .unwrap()
                    .timers
                    .borrow_mut()
                    .add(self.deadline, cx.waker().clone());
            });
            self.registered = true;
        }

        Poll::Pending
    }
}

thread_local! {
    static CURRENT_RUNTIME: RefCell<Option<Rc<Runtime>>> = RefCell::new(None);
}

fn sleep(duration: Duration) -> Sleep {
    Sleep::new(duration)
}
```

## Testing the Runtime

Let's put it all together with a real test:

```rust
fn main() -> io::Result<()> {
    let rt = Runtime::new()?;

    // Set the thread-local runtime
    CURRENT_RUNTIME.with(|current| {
        *current.borrow_mut() = Some(rt.clone());
    });

    rt.spawn(async {
        println!("[task 1] starting");
        sleep(Duration::from_millis(100)).await;
        println!("[task 1] woke up after 100ms");
    });

    rt.spawn(async {
        println!("[task 2] starting");
        sleep(Duration::from_millis(50)).await;
        println!("[task 2] woke up after 50ms");
        sleep(Duration::from_millis(100)).await;
        println!("[task 2] woke up after another 100ms");
    });

    rt.spawn(async {
        println!("[task 3] I complete immediately");
    });

    rt.run();
    println!("All tasks completed");
    Ok(())
}
```

Expected output:
```
[task 1] starting
[task 2] starting
[task 3] I complete immediately
[task 2] woke up after 50ms
[task 1] woke up after 100ms
[task 2] woke up after another 100ms
All tasks completed
```

## Design Tradeoffs

Building your own runtime forces you to make explicit tradeoffs that Tokio has already made for you:

**Send vs !Send tasks.** Tokio requires `Send` because tasks can migrate between threads. A thread-per-core runtime can accept `!Send` tasks, which means you can use `Rc`, `Cell`, and thread-local state freely. This is a genuine advantage for performance-sensitive code.

**Static vs dynamic dispatch.** We used `Box<dyn Future>` for type erasure. You could use generics instead (`struct Task<F: Future>`) to avoid the vtable indirection, but then you can't have a heterogeneous task queue.

**Allocation strategy.** We allocate each task on the heap with `Box::pin`. A slab allocator (like Tokio's) reuses memory for tasks, reducing allocation pressure.

**I/O driver choice.** We used mio (epoll/kqueue). For Linux-only deployments, io_uring gives you better performance at the cost of portability.

The right choices depend entirely on your use case. That's why custom runtimes exist — not because Tokio is wrong, but because different problems have different optimal points in the design space. Now that you've built one, you understand what those tradeoffs are.
