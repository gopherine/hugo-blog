---
title: "Lesson 5: Epoll and IO Multiplexing — How Go''s Netpoller Works"
author: Atharva Pandey
series: "Linux for Backend Engineers"
lesson: 5
keywords:
  - linux
  - epoll
  - io multiplexing
  - netpoller
  - Go runtime
  - non-blocking IO
  - backend engineering
tags:
  - fundamentals
  - linux
  - operating systems
date: '2024-06-27T00:00:00.000Z'
---

I used to wonder how a Go HTTP server could handle 100,000 concurrent connections with only 8 OS threads. If each connection required a dedicated thread, you would need 100,000 threads — which would require roughly 800 GB of stack space and would spend all their time in the kernel scheduler. The answer is epoll: a Linux kernel interface that lets a single thread wait on thousands of file descriptors simultaneously and be notified only when one is ready for I/O. Go's runtime uses epoll internally as the foundation of its network I/O model. This lesson explains how.

## How It Actually Works

Before epoll, the standard approach to handling multiple connections was `select()` or `poll()`. Both have fundamental limitations:

- `select()`: limited to file descriptors below 1024; the kernel scans all registered fds on every call
- `poll()`: no fd number limit but still scans all registered fds on every call
- Both: O(n) scan cost regardless of how many fds are actually ready

`epoll` solves this with an event-driven model:

1. **`epoll_create1()`**: creates an epoll instance — a kernel data structure that tracks interesting fds
2. **`epoll_ctl()`**: register/modify/remove interest in an fd (readable, writable, error)
3. **`epoll_wait()`**: block until one or more registered fds are ready, returning only the ready ones

The key improvement: `epoll_wait()` returns in O(ready events) time, not O(registered fds) time. 10,000 registered connections, 5 ready to read → `epoll_wait` returns 5 events. No scanning of the idle 9,995.

Here is a minimal epoll event loop in Go using `syscall`:

```go
package main

import (
    "fmt"
    "net"
    "syscall"
)

func epollServer(addr string) error {
    // Create epoll instance
    epfd, err := syscall.EpollCreate1(syscall.EPOLL_CLOEXEC)
    if err != nil {
        return fmt.Errorf("epoll_create1: %w", err)
    }
    defer syscall.Close(epfd)

    // Set up listening socket
    ln, err := net.Listen("tcp", addr)
    if err != nil {
        return err
    }
    rawConn, _ := ln.(*net.TCPListener).SyscallConn()
    var listenFD int
    rawConn.Control(func(fd uintptr) { listenFD = int(fd) })

    // Register listener with epoll
    syscall.EpollCtl(epfd, syscall.EPOLL_CTL_ADD, listenFD, &syscall.EpollEvent{
        Events: syscall.EPOLLIN, // notify when readable (new connection ready)
        Fd:     int32(listenFD),
    })

    events := make([]syscall.EpollEvent, 128)
    for {
        // Block until events are ready — no busy-waiting, no scanning
        n, err := syscall.EpollWait(epfd, events, -1)
        if err != nil {
            if err == syscall.EINTR {
                continue // interrupted by signal — retry
            }
            return fmt.Errorf("epoll_wait: %w", err)
        }

        for i := 0; i < n; i++ {
            fd := int(events[i].Fd)
            if fd == listenFD {
                // Accept new connection
                connFD, _, err := syscall.Accept4(listenFD, syscall.SOCK_NONBLOCK)
                if err != nil {
                    continue
                }
                // Register new connection with epoll
                syscall.EpollCtl(epfd, syscall.EPOLL_CTL_ADD, connFD, &syscall.EpollEvent{
                    Events: syscall.EPOLLIN | syscall.EPOLLET, // edge-triggered
                    Fd:     int32(connFD),
                })
            } else {
                // Data ready on an existing connection
                handleConnection(fd)
            }
        }
    }
}
```

**Edge-triggered vs Level-triggered**:
- Level-triggered (default): epoll notifies you as long as the fd is ready. If you don't read all the data, the next `epoll_wait` also returns this fd.
- Edge-triggered (`EPOLLET`): epoll notifies you only once per state change. Requires non-blocking fds and reading until `EAGAIN`. More efficient but requires careful coding.

## Why It Matters

Go's runtime builds its entire network I/O model on top of epoll (Linux), kqueue (macOS/BSD), or IOCP (Windows). This is the **netpoller**.

When a goroutine calls `conn.Read()`, here is what actually happens:
1. The goroutine calls into the runtime's network layer
2. The runtime tries a non-blocking read on the underlying socket fd
3. If the fd is not ready (`EAGAIN`), the goroutine is parked — taken off its OS thread and put in a wait queue associated with that fd
4. The netpoller goroutine (which runs `epoll_wait` in a loop) waits for the fd to become readable
5. When epoll reports the fd is ready, the parked goroutine is made runnable again
6. The OS thread picks it up and the `conn.Read()` call returns with data

This is why you can write blocking-style Go code (`conn.Read()`, `conn.Write()`) and it scales to hundreds of thousands of connections. The blocking appearance is an abstraction — underneath, the runtime is doing non-blocking I/O with epoll.

## Production Example

You rarely interact with epoll directly in Go — the runtime handles it. But understanding it explains several important behaviors:

```go
// Why this scales to 100k connections: each goroutine's Read()
// parks itself via the netpoller when no data is available.
// Zero OS thread context switches while waiting.
func handleConn(conn net.Conn) {
    defer conn.Close()
    buf := make([]byte, 4096)
    for {
        n, err := conn.Read(buf) // parks goroutine via epoll until data arrives
        if err != nil {
            return
        }
        // process buf[:n]
        conn.Write(response(buf[:n])) // parks goroutine via epoll until write buffer has space
    }
}

func main() {
    ln, _ := net.Listen("tcp", ":8080")
    for {
        conn, err := ln.Accept()
        if err != nil {
            continue
        }
        go handleConn(conn) // one goroutine per connection — but they mostly sleep in epoll
    }
}
```

Setting deadlines is critical because a goroutine parked in epoll waiting for a client that never sends more data is a resource leak:

```go
func handleConn(conn net.Conn) {
    defer conn.Close()
    // Set a deadline — goroutine unparks and returns after this time
    // even if no data has arrived
    conn.SetDeadline(time.Now().Add(30 * time.Second))

    buf := make([]byte, 4096)
    for {
        conn.SetDeadline(time.Now().Add(30 * time.Second)) // reset on each operation
        n, err := conn.Read(buf)
        if err != nil {
            if netErr, ok := err.(net.Error); ok && netErr.Timeout() {
                // Expected timeout — client was idle
                return
            }
            return
        }
        // process data
    }
}
```

## The Tradeoffs

**Epoll and CGo**: if your goroutine calls into C code that performs blocking I/O directly (not through Go's net package), it bypasses the netpoller entirely. The OS thread blocks, the runtime creates a new OS thread for other goroutines, and you get thread explosion. Use Go's net package, not raw C I/O, from goroutines.

**Busy-polling**: some low-latency networking frameworks poll fds with `epoll_wait(timeout=0)` instead of blocking. This burns CPU but reduces latency by avoiding the syscall overhead of sleeping. Not appropriate for typical backend services.

**`EPOLLONESHOT`**: removes an fd from epoll after the first event. Useful for multi-threaded event loops where you want exactly one thread to handle each event. Go's netpoller doesn't use this — it relies on the scheduler to ensure only one goroutine reads from a connection at a time.

**`io_uring`** (Linux 5.1+): a newer, more powerful async I/O interface than epoll. Supports batching of I/O operations, true async for disk I/O (epoll doesn't work for regular files), and reduced syscall overhead. Go's netpoller does not yet use io_uring by default, but there is ongoing work in this area.

## Key Takeaway

Epoll is the Linux kernel mechanism that lets a single thread wait on thousands of I/O events efficiently. Go's netpoller wraps epoll to park goroutines when I/O is not ready, transparently converting blocking-style code into non-blocking I/O under the hood. This is how Go achieves high connection concurrency without requiring one OS thread per connection. Set connection deadlines to prevent goroutines from parking indefinitely waiting for slow or dead clients.

---

**Previous:** [Lesson 4: TCP/IP Stack](/post/fundamentals/linux-tcp-ip/) | **Next:** [Lesson 6: Signals — SIGTERM vs SIGKILL and Graceful Shutdown](/post/fundamentals/linux-signals/)
