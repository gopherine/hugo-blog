---
title: "Lesson 4: epoll/kqueue — Platform event loops"
author: Atharva Pandey
series: "Rust Async Internals & Runtime Design"
lesson: 4
keywords: [Rust, rust, async, runtime, epoll, kqueue, event loop, mio, polling, file descriptor]
tags: [Rust tutorial, rust, async, runtime, internals]
date: '2025-09-21T17:08:45.000Z'
---

Before I understood event loops, I thought async I/O was some kind of kernel magic. You register interest in a socket, and somehow the OS tells you when data arrives — without blocking a thread. How? I imagined complex kernel subsystems doing heavy lifting behind the scenes.

Turns out, the mechanism is almost embarrassingly simple. The kernel maintains a list of file descriptors you care about. When something happens on one of them, it flips a bit. You ask "what happened?", it tells you. That's it. The entire async I/O ecosystem — Tokio, Node.js, nginx, everything — is built on this one primitive.

Let's dig into how it actually works on Linux (`epoll`) and macOS/BSD (`kqueue`), and then build a cross-platform event loop in Rust.

## epoll on Linux

`epoll` is Linux's scalable I/O event notification mechanism. It replaced the older `select` and `poll` syscalls, which had O(n) overhead in the number of watched file descriptors. `epoll` is O(1) for the common case.

Three syscalls make up the API:

```rust
// Pseudocode — these are the actual Linux syscalls

// 1. Create an epoll instance
let epoll_fd = epoll_create1(0);

// 2. Register interest in a file descriptor
let mut event = epoll_event {
    events: EPOLLIN | EPOLLET,  // interested in reads, edge-triggered
    u64: socket_fd as u64,      // user data — we'll get this back
};
epoll_ctl(epoll_fd, EPOLL_CTL_ADD, socket_fd, &event);

// 3. Wait for events
let mut events = [epoll_event::default(); 1024];
let n = epoll_wait(epoll_fd, &mut events, 1024, timeout_ms);
// n events are ready — process them
```

There are two triggering modes that matter:

**Level-triggered (default):** `epoll_wait` returns the fd as ready as long as there's data available. If you don't read all the data, the next `epoll_wait` call will return it again. Forgiving, but can cause extra syscalls.

**Edge-triggered (`EPOLLET`):** `epoll_wait` only tells you about *transitions* — when a fd goes from "not ready" to "ready." If you don't drain all available data, you won't be notified again until new data arrives. More efficient, but you must drain the fd completely in non-blocking mode.

```rust
use std::io::{self, Read};
use std::net::TcpListener;
use std::os::unix::io::AsRawFd;

// Edge-triggered requires draining the socket completely
fn drain_socket(socket: &mut impl Read, buf: &mut [u8]) -> io::Result<Vec<u8>> {
    let mut data = Vec::new();
    loop {
        match socket.read(buf) {
            Ok(0) => break, // EOF
            Ok(n) => data.extend_from_slice(&buf[..n]),
            Err(e) if e.kind() == io::ErrorKind::WouldBlock => break,
            Err(e) => return Err(e),
        }
    }
    Ok(data)
}
```

Tokio uses edge-triggered epoll. This is one reason you must always use non-blocking I/O with Tokio — if a read or write would block, you need to return `WouldBlock` so the runtime knows to wait for the next edge trigger.

## kqueue on macOS/BSD

macOS and the BSD family use `kqueue` instead of `epoll`. The concepts are similar, but the API is more general — kqueue can watch file descriptors, processes, signals, timers, and file system changes.

```rust
// Pseudocode for kqueue

// 1. Create the kqueue
let kq = kqueue();

// 2. Register interest using "change events"
let change = kevent {
    ident: socket_fd as usize,
    filter: EVFILT_READ,        // interested in readability
    flags: EV_ADD | EV_ENABLE,  // add and enable
    fflags: 0,
    data: 0,
    udata: std::ptr::null_mut(),
};
kevent(kq, &[change], &mut [], null_timespec);

// 3. Wait for events
let mut events = [kevent::default(); 1024];
let n = kevent(kq, &[], &mut events, timeout);
```

The biggest difference from epoll: kqueue uses a single `kevent` syscall for both registration and waiting. You pass in changes and get back events in one call. Also, kqueue is edge-triggered by default (you can make it level-triggered with `EV_CLEAR`).

Another nice property of kqueue: you can watch for the *amount* of data available. The `data` field in a read event tells you how many bytes are ready to read. With epoll, you just know "it's readable" — you have to try reading to find out how much.

## The mio Crate: Cross-Platform Abstraction

Writing platform-specific epoll/kqueue code directly is miserable. That's why Tokio's team created `mio` — a thin cross-platform abstraction over OS event systems. It's what Tokio uses internally.

```rust
use mio::{Events, Interest, Poll, Token};
use mio::net::TcpListener;
use std::io;

const SERVER: Token = Token(0);

fn main() -> io::Result<()> {
    // Create the Poll instance — wraps epoll/kqueue
    let mut poll = Poll::new()?;
    let mut events = Events::with_capacity(1024);

    // Bind a TCP listener
    let addr = "127.0.0.1:8080".parse().unwrap();
    let mut server = TcpListener::bind(addr)?;

    // Register interest in incoming connections
    poll.registry()
        .register(&mut server, SERVER, Interest::READABLE)?;

    println!("Server listening on {}", addr);

    loop {
        // Block until events arrive
        poll.poll(&mut events, None)?;

        for event in events.iter() {
            match event.token() {
                SERVER => {
                    // Accept new connections
                    loop {
                        match server.accept() {
                            Ok((mut connection, addr)) => {
                                println!("New connection from {}", addr);
                                // In a real server: register this connection
                                // with a unique Token and handle reads/writes
                            }
                            Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                                break; // no more pending connections
                            }
                            Err(e) => return Err(e),
                        }
                    }
                }
                _ => unreachable!(),
            }
        }
    }
}
```

`mio` is intentionally low-level. It doesn't manage buffers, doesn't provide futures, doesn't schedule tasks. It just gives you a portable way to wait for I/O events. The runtime builds everything else on top.

## Building an Event Loop from Scratch

Let's build a more complete event loop that can handle multiple connections. This is essentially what sits at the heart of every async runtime:

```rust
use mio::{Events, Interest, Poll, Token};
use mio::net::{TcpListener, TcpStream};
use std::collections::HashMap;
use std::io::{self, Read, Write};

const SERVER: Token = Token(0);

struct Connection {
    stream: TcpStream,
    read_buf: Vec<u8>,
    write_buf: Vec<u8>,
}

struct EventLoop {
    poll: Poll,
    events: Events,
    server: TcpListener,
    connections: HashMap<Token, Connection>,
    next_token: usize,
}

impl EventLoop {
    fn new(addr: &str) -> io::Result<Self> {
        let poll = Poll::new()?;
        let mut server = TcpListener::bind(addr.parse().unwrap())?;

        poll.registry()
            .register(&mut server, SERVER, Interest::READABLE)?;

        Ok(EventLoop {
            poll,
            events: Events::with_capacity(1024),
            server,
            connections: HashMap::new(),
            next_token: 1,
        })
    }

    fn run(&mut self) -> io::Result<()> {
        loop {
            self.poll.poll(&mut self.events, None)?;

            // Collect tokens first to avoid borrow issues
            let event_list: Vec<(Token, bool, bool)> = self
                .events
                .iter()
                .map(|e| (e.token(), e.is_readable(), e.is_writable()))
                .collect();

            for (token, readable, writable) in event_list {
                if token == SERVER {
                    self.accept_connections()?;
                } else {
                    if readable {
                        self.handle_read(token)?;
                    }
                    if writable {
                        self.handle_write(token)?;
                    }
                }
            }
        }
    }

    fn accept_connections(&mut self) -> io::Result<()> {
        loop {
            match self.server.accept() {
                Ok((mut stream, addr)) => {
                    let token = Token(self.next_token);
                    self.next_token += 1;

                    self.poll.registry().register(
                        &mut stream,
                        token,
                        Interest::READABLE | Interest::WRITABLE,
                    )?;

                    self.connections.insert(
                        token,
                        Connection {
                            stream,
                            read_buf: vec![0u8; 4096],
                            write_buf: Vec::new(),
                        },
                    );

                    println!("Accepted connection from {} as {:?}", addr, token);
                }
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => break,
                Err(e) => return Err(e),
            }
        }
        Ok(())
    }

    fn handle_read(&mut self, token: Token) -> io::Result<()> {
        let conn = match self.connections.get_mut(&token) {
            Some(c) => c,
            None => return Ok(()),
        };

        loop {
            match conn.stream.read(&mut conn.read_buf) {
                Ok(0) => {
                    // Connection closed
                    println!("{:?} disconnected", token);
                    self.connections.remove(&token);
                    return Ok(());
                }
                Ok(n) => {
                    // Echo: copy read data to write buffer
                    conn.write_buf.extend_from_slice(&conn.read_buf[..n]);
                }
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => break,
                Err(e) => {
                    self.connections.remove(&token);
                    return Err(e);
                }
            }
        }
        Ok(())
    }

    fn handle_write(&mut self, token: Token) -> io::Result<()> {
        let conn = match self.connections.get_mut(&token) {
            Some(c) => c,
            None => return Ok(()),
        };

        if conn.write_buf.is_empty() {
            return Ok(());
        }

        match conn.stream.write(&conn.write_buf) {
            Ok(n) => {
                conn.write_buf.drain(..n);
            }
            Err(e) if e.kind() == io::ErrorKind::WouldBlock => {}
            Err(e) => {
                self.connections.remove(&token);
                return Err(e);
            }
        }
        Ok(())
    }
}

fn main() -> io::Result<()> {
    println!("Starting echo server on 127.0.0.1:8080");
    let mut event_loop = EventLoop::new("127.0.0.1:8080")?;
    event_loop.run()
}
```

That's a fully functional echo server without a single thread (beyond the main thread). It handles thousands of concurrent connections by multiplexing them through a single event loop. This is the same architectural pattern that nginx, Redis, and Node.js use.

## Connecting the Event Loop to Futures

The missing piece is connecting these raw events to the `Waker` mechanism from the futures system. Here's how a reactor bridges the gap:

```rust
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::task::Waker;

struct Reactor {
    poll: Poll,
    wakers: Arc<Mutex<HashMap<Token, Waker>>>,
}

impl Reactor {
    fn register_waker(&self, token: Token, waker: Waker) {
        self.wakers.lock().unwrap().insert(token, waker);
    }

    fn process_events(&self) -> io::Result<()> {
        let mut events = Events::with_capacity(256);
        self.poll.poll(&mut events, None)?;

        let wakers = self.wakers.lock().unwrap();
        for event in events.iter() {
            if let Some(waker) = wakers.get(&event.token()) {
                // This future's I/O is ready — wake it up
                waker.wake_by_ref();
            }
        }
        Ok(())
    }
}
```

When an async I/O future (like Tokio's `TcpStream::read`) returns `Pending`, it registers a waker with the reactor for its specific `Token`. The reactor's event loop calls `poll()` on the OS-level event system, and when events arrive, it wakes the corresponding futures. The executor then re-polls those futures, which can now make progress because the I/O is ready.

## Platform Differences That Bite

I've hit a few platform-specific issues that are worth knowing about:

**File I/O on Linux.** `epoll` doesn't work with regular files — only sockets, pipes, and certain other file descriptors. Regular file reads always return "ready" immediately, even if the data hasn't been loaded from disk. This is why `tokio::fs` uses a blocking thread pool under the hood, not epoll. (io_uring fixes this — it handles file I/O natively.)

**`EPOLLET` and missed events.** If you register with edge-triggering and don't drain all available data, you'll miss events until new data arrives. I've debugged hung connections caused by exactly this mistake. Always drain in a loop until `WouldBlock`.

**kqueue and `EV_EOF`.** On macOS, kqueue gives you an explicit `EV_EOF` flag when the peer disconnects. epoll doesn't — you discover a disconnect by getting a zero-length read. Small difference, but it can cause behavioral mismatches in cross-platform code.

**Thundering herd.** If multiple threads wait on the same epoll instance and a single event arrives, older kernels would wake all threads (`EPOLLEXCLUSIVE` fixes this on Linux 4.5+). kqueue handles this per-thread naturally since each thread typically has its own kqueue instance.

## Why This Matters for Runtime Design

Understanding the event loop layer matters because it determines the constraints of your runtime:

- The reactor is inherently single-threaded (one thread calls `poll()` at a time). If you want multi-threaded execution, you either have one reactor per thread or a shared reactor with careful synchronization.
- The event loop's granularity determines your minimum latency. If you batch too many operations before checking for events, you add latency. If you check too often, you waste CPU on syscalls.
- Platform differences in the event system propagate up through the entire stack. Tokio's behavior on Linux vs macOS can differ in subtle ways because epoll and kqueue have different semantics.

Tokio chose one reactor per thread in its multi-threaded runtime. Each worker thread has its own epoll/kqueue instance and processes I/O events locally. This avoids cross-thread synchronization on the hot path. We'll see how this fits into the bigger picture when we cover the reactor pattern in the next lesson.
