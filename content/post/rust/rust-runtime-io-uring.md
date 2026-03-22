---
title: "Lesson 3: io_uring — Zero-copy async I/O on Linux"
author: Atharva Pandey
series: "Rust Async Internals & Runtime Design"
lesson: 3
keywords: [Rust, rust, async, runtime, io_uring, zero-copy, linux, kernel, submission queue, completion queue]
tags: [Rust tutorial, rust, async, runtime, internals]
date: '2025-09-19T10:33:08.000Z'
---

I ran a benchmark last year that genuinely surprised me. A simple TCP echo server using `io_uring` was handling 40% more connections per second than the same server on `epoll`, with measurably lower tail latencies. Not 5% — forty percent. On the same hardware, same kernel version, same application logic. That's when I stopped treating `io_uring` as a curiosity and started treating it as the future of Linux I/O.

If you've been building async runtimes on `epoll` (or even `kqueue` on macOS), `io_uring` changes the game completely. Let me show you why.

## The Problem with Traditional Async I/O

Traditional async I/O on Linux works like this:

1. You have a set of file descriptors (sockets, files, pipes).
2. You ask the kernel "which of these are ready for reading/writing?" using `epoll`.
3. The kernel tells you which ones are ready.
4. You perform the actual read/write syscalls.
5. Go back to step 2.

The problem? **Steps 2 and 4 are separate syscalls.** Every I/O operation requires at least two context switches between userspace and kernel space — one to check readiness, one to do the actual I/O. For high-throughput servers handling millions of operations, those syscalls add up.

```rust
// Pseudocode: traditional epoll-based I/O
loop {
    // Syscall 1: ask kernel what's ready
    let events = epoll_wait(epoll_fd, &mut event_buffer, timeout);

    for event in events {
        if event.is_readable() {
            // Syscall 2: actually read the data
            let n = read(event.fd, &mut buffer);
            // process the data...
        }
        if event.is_writable() {
            // Syscall 3: actually write
            write(event.fd, &data);
        }
    }
}
```

With `epoll`, a single request-response cycle on a socket requires *at minimum* four syscalls: `epoll_wait` (read ready), `read`, `epoll_wait` (write ready), `write`. For a web server handling 100k requests/second, that's 400k syscalls per second just for basic I/O.

## io_uring: The Shared Ring Buffer Approach

`io_uring` (introduced in Linux 5.1) takes a radically different approach. Instead of readiness notification + separate syscall, you submit I/O operations to a shared ring buffer and the kernel completes them asynchronously. There are two rings:

- **Submission Queue (SQ):** You write I/O requests here. The kernel reads from it.
- **Completion Queue (CQ):** The kernel writes completion events here. You read from it.

Both rings live in shared memory between userspace and the kernel. In the best case, no syscalls are needed at all — the kernel can poll the submission queue directly (with `SQPOLL` mode).

```
Userspace                    Kernel
┌──────────────────┐        ┌──────────────────┐
│ Submit I/O ops   │───────>│ Process ops      │
│ to SQ ring       │        │ from SQ ring     │
│                  │        │                  │
│ Read completions │<───────│ Post completions │
│ from CQ ring     │        │ to CQ ring       │
└──────────────────┘        └──────────────────┘
     shared memory              shared memory
```

## Using io_uring from Rust

The `io-uring` crate provides safe(ish) bindings to the kernel interface. Let's look at the raw mechanics:

```rust
use io_uring::{opcode, types, IoUring};
use std::fs::File;
use std::io;
use std::os::unix::io::AsRawFd;

fn read_file_with_io_uring(path: &str) -> io::Result<Vec<u8>> {
    // Create the io_uring instance with 256 entries
    let mut ring = IoUring::new(256)?;

    let file = File::open(path)?;
    let fd = types::Fd(file.as_raw_fd());

    // Prepare a buffer for reading
    let mut buf = vec![0u8; 4096];

    // Build a read operation
    let read_op = opcode::Read::new(fd, buf.as_mut_ptr(), buf.len() as u32)
        .offset(0)
        .build()
        .user_data(0x42); // arbitrary tag to identify this operation

    // Submit the operation to the submission queue
    unsafe {
        ring.submission()
            .push(&read_op)
            .expect("submission queue full");
    }

    // Tell the kernel to process submissions
    ring.submit_and_wait(1)?;

    // Read the completion
    let cqe = ring.completion().next().expect("no completion event");
    let bytes_read = cqe.result();

    if bytes_read < 0 {
        return Err(io::Error::from_raw_os_error(-bytes_read));
    }

    assert_eq!(cqe.user_data(), 0x42);
    buf.truncate(bytes_read as usize);
    Ok(buf)
}
```

The key insight: we submitted a read operation and the kernel performed it. We never called `read()` directly. The kernel did the I/O on our behalf and gave us the result through the completion queue.

## Batching: Where io_uring Really Shines

The real power isn't single operations — it's batching. You can submit dozens of I/O operations in a single syscall (or even zero syscalls with `SQPOLL`):

```rust
use io_uring::{opcode, types, IoUring};
use std::fs::File;
use std::os::unix::io::AsRawFd;

fn read_multiple_files(paths: &[&str]) -> Vec<Vec<u8>> {
    let mut ring = IoUring::new(256).unwrap();
    let mut files: Vec<File> = Vec::new();
    let mut buffers: Vec<Vec<u8>> = Vec::new();

    // Submit ALL reads at once
    for (i, path) in paths.iter().enumerate() {
        let file = File::open(path).unwrap();
        let mut buf = vec![0u8; 8192];

        let read_op = opcode::Read::new(
            types::Fd(file.as_raw_fd()),
            buf.as_mut_ptr(),
            buf.len() as u32,
        )
        .build()
        .user_data(i as u64);

        unsafe {
            ring.submission().push(&read_op).unwrap();
        }

        files.push(file);
        buffers.push(buf);
    }

    // ONE syscall to submit all operations
    ring.submit_and_wait(paths.len() as u32).unwrap();

    // Collect all completions
    let mut results = vec![Vec::new(); paths.len()];
    for cqe in ring.completion() {
        let idx = cqe.user_data() as usize;
        let bytes_read = cqe.result() as usize;
        results[idx] = buffers[idx][..bytes_read].to_vec();
    }

    results
}
```

We submitted N read operations with a single `submit_and_wait` call. With `epoll`, this would have been N `read()` syscalls plus `epoll_wait` calls. The syscall reduction is dramatic.

## Integrating io_uring with a Future-Based Runtime

Now for the fun part — making `io_uring` work with async/await. We need to bridge the completion-based model (io_uring) with the poll-based model (Rust futures).

Here's a simplified reactor that wraps `io_uring`:

```rust
use io_uring::{opcode, types, IoUring, squeue};
use std::cell::RefCell;
use std::collections::HashMap;
use std::future::Future;
use std::io;
use std::os::unix::io::RawFd;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll, Waker};

// Shared state for a single I/O operation
struct OpState {
    result: Option<i32>,
    waker: Option<Waker>,
}

struct IoUringReactor {
    ring: RefCell<IoUring>,
    ops: RefCell<HashMap<u64, Arc<Mutex<OpState>>>>,
    next_id: RefCell<u64>,
}

impl IoUringReactor {
    fn new() -> io::Result<Self> {
        Ok(IoUringReactor {
            ring: RefCell::new(IoUring::new(1024)?),
            ops: RefCell::new(HashMap::new()),
            next_id: RefCell::new(0),
        })
    }

    fn submit_read(
        &self,
        fd: RawFd,
        buf: *mut u8,
        len: u32,
        offset: u64,
    ) -> ReadFuture {
        let mut id = self.next_id.borrow_mut();
        let op_id = *id;
        *id += 1;

        let state = Arc::new(Mutex::new(OpState {
            result: None,
            waker: None,
        }));

        self.ops.borrow_mut().insert(op_id, state.clone());

        let entry = opcode::Read::new(types::Fd(fd), buf, len)
            .offset(offset as _)
            .build()
            .user_data(op_id);

        unsafe {
            self.ring
                .borrow_mut()
                .submission()
                .push(&entry)
                .expect("sq full");
        }

        // Submit to kernel
        self.ring.borrow().submitter().submit().unwrap();

        ReadFuture { state }
    }

    fn process_completions(&self) {
        let mut ring = self.ring.borrow_mut();
        let ops = self.ops.borrow();

        // Non-blocking check for completions
        for cqe in ring.completion() {
            let op_id = cqe.user_data();
            if let Some(state) = ops.get(&op_id) {
                let mut state = state.lock().unwrap();
                state.result = Some(cqe.result());
                if let Some(waker) = state.waker.take() {
                    waker.wake();
                }
            }
        }
    }
}

struct ReadFuture {
    state: Arc<Mutex<OpState>>,
}

impl Future for ReadFuture {
    type Output = io::Result<i32>;

    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output> {
        let mut state = self.state.lock().unwrap();

        if let Some(result) = state.result {
            if result < 0 {
                Poll::Ready(Err(io::Error::from_raw_os_error(-result)))
            } else {
                Poll::Ready(Ok(result))
            }
        } else {
            state.waker = Some(cx.waker().clone());
            Poll::Pending
        }
    }
}
```

The pattern here:
1. When a user calls an async read, we submit the operation to io_uring's submission queue and return a `ReadFuture`.
2. The future initially returns `Pending` — the kernel hasn't completed the I/O yet.
3. The executor's event loop calls `process_completions()` to check the completion queue.
4. When a completion arrives, we store the result and wake the future.
5. The executor re-polls the future, which now returns `Ready` with the result.

## Buffer Management and Ownership

There's a gnarly problem lurking here that most tutorials gloss over: **buffer ownership**.

With `epoll` + `read()`, you own the buffer the entire time. You pass a `&mut [u8]` to `read()`, the kernel fills it, `read()` returns, you own the buffer again. Simple.

With `io_uring`, you submit a pointer to your buffer, and the kernel writes to it *asynchronously*. Between submission and completion, the kernel might write to that buffer at any time. If you move the buffer, resize it, or free it before the operation completes — you get memory corruption.

```rust
// DANGEROUS: don't do this
let mut buf = vec![0u8; 4096];
let future = reactor.submit_read(fd, buf.as_mut_ptr(), 4096, 0);

// If buf gets dropped here (e.g., it goes out of scope),
// the kernel will write to freed memory!
drop(buf); // UAF bug!

// The future still holds a pointer to the now-freed buffer
let result = future.await; // undefined behavior
```

The solution is to tie the buffer's lifetime to the I/O operation. There are a few approaches:

```rust
// Approach 1: The future owns the buffer
struct OwnedReadFuture {
    state: Arc<Mutex<OpState>>,
    buf: Vec<u8>, // buffer lives here until completion
}

impl Future for OwnedReadFuture {
    type Output = io::Result<Vec<u8>>;

    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output> {
        let this = unsafe { self.get_unchecked_mut() };
        let mut state = this.state.lock().unwrap();

        if let Some(result) = state.result {
            if result < 0 {
                Poll::Ready(Err(io::Error::from_raw_os_error(-result)))
            } else {
                let mut buf = std::mem::take(&mut this.buf);
                buf.truncate(result as usize);
                Poll::Ready(Ok(buf))
            }
        } else {
            state.waker = Some(cx.waker().clone());
            Poll::Pending
        }
    }
}

// Approach 2: Pre-registered buffer pool
// io_uring supports registering buffers with the kernel upfront.
// This avoids per-operation buffer setup and enables true zero-copy.
fn register_buffers(ring: &IoUring, buffers: &[Vec<u8>]) {
    let iovecs: Vec<libc::iovec> = buffers
        .iter()
        .map(|buf| libc::iovec {
            iov_base: buf.as_ptr() as *mut _,
            iov_len: buf.len(),
        })
        .collect();

    ring.submitter()
        .register_buffers(&iovecs)
        .expect("failed to register buffers");
}
```

The `tokio-uring` crate handles this by taking ownership of buffers on submission and returning them on completion. It's a fundamentally different API from the `AsyncRead`/`AsyncWrite` traits, which assume borrowed buffers.

## SQPOLL: Zero-Syscall I/O

The ultimate `io_uring` optimization is `SQPOLL` mode. Instead of calling `submit()` to notify the kernel about new submissions, the kernel spawns a dedicated thread that continuously polls the submission queue:

```rust
use io_uring::{IoUring, Builder};

// Create a ring with kernel-side polling
let ring = Builder::default()
    .setup_sqpoll(2000) // kernel thread idle timeout: 2000ms
    .build(128)
    .expect("failed to create SQPOLL ring");

// Now submissions don't require a syscall!
// The kernel thread picks them up automatically.
// You only need a syscall to wait for completions.
```

With SQPOLL, the hot path is:
1. Write submission entry to shared memory (no syscall).
2. Kernel thread picks it up and performs the I/O.
3. Kernel writes completion to shared memory.
4. Userspace reads completion from shared memory.

In the absolute best case — when both sides are actively polling — the entire I/O operation happens with zero context switches. This is why `io_uring`-based servers can achieve kernel-bypass-like performance without actually bypassing the kernel.

## When to Use io_uring

Don't reach for `io_uring` by default. Here's my honest take on when it makes sense:

**Use io_uring when:** You're building a high-throughput server on Linux 5.6+, you're doing lots of batched I/O, you care about tail latencies, or you're doing file I/O (where `epoll` literally doesn't help).

**Stick with epoll when:** You need portability (macOS, older Linux), your application isn't I/O-bound, or the complexity isn't justified by your performance requirements.

**The glommio crate** is a Rust runtime built entirely on `io_uring` with a thread-per-core architecture. If you want to use `io_uring` without building your own runtime, it's the most mature option. We'll compare it against Tokio in lesson 8.

The Linux kernel team is actively expanding `io_uring`'s capabilities — it now supports networking, file I/O, timers, and even `splice`/`tee` for zero-copy data movement between file descriptors. This isn't a niche feature anymore. It's the direction Linux I/O is heading, and Rust's ownership model makes it one of the best languages for using it safely.
