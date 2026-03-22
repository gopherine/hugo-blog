---
title: 'Lesson 3: File Descriptors — Why Too Many Open Files Kills Your Server'
author: Atharva Pandey
series: "Linux for Backend Engineers"
lesson: 3
keywords:
  - linux
  - file descriptors
  - ulimit
  - open files
  - socket
  - backend engineering
tags:
  - fundamentals
  - linux
  - operating systems
date: '2024-05-27T00:00:00.000Z'
---

I got a 3 AM page for a service that was returning connection errors to every client. The application logs said `dial tcp: lookup ...: too many open files`. We hadn't changed anything. Load was normal. But over 48 hours, something had been slowly accumulating open file descriptors and not closing them, and we hit the per-process limit. Restarting the service fixed the immediate problem; understanding why it happened required me to actually learn what file descriptors are and how the kernel manages them.

## How It Actually Works

A **file descriptor** (fd) is a non-negative integer that represents an open "file" in the Linux kernel. The word "file" is used loosely — file descriptors refer to:

- Regular files on disk
- Directories
- Sockets (TCP, UDP, Unix domain)
- Pipes (both ends)
- `/dev/null`, `/dev/random`, and other device files
- `epoll` and `inotify` instances
- `timerfd` and `eventfd` instances

The kernel maintains a **file description table** (global, per open file/socket) and a **file descriptor table** (per process, maps integers to kernel file objects). When you `open()` a file, the kernel:
1. Creates a kernel file object (tracking position, flags, reference count)
2. Finds the lowest unused integer in your process's fd table
3. Returns that integer — that is the file descriptor

Standard file descriptors: 0 = stdin, 1 = stdout, 2 = stderr. Your first `open()` returns 3.

```
Process fd table          Kernel file objects
  0 → stdin  ──────────→ terminal (refcount 1)
  1 → stdout ──────────→ terminal (refcount 2) ← shared
  2 → stderr ──────────→ terminal (refcount 3) ←
  3 → /etc/passwd ────→ inode 12345 (file offset, flags)
  4 → socket ─────────→ TCP socket (send/recv buffers, state)
  5 → pipe read end ──→ pipe buffer
  6 → epoll fd ───────→ epoll interest list
```

The per-process limit on open file descriptors is controlled by `ulimit -n` (soft limit, enforceable by the process) and `ulimit -Hn` (hard limit). The kernel-wide limit is `fs.file-max` in `/proc/sys/fs/file-max`.

```bash
# Check current limits for a running process
cat /proc/$(pgrep myservice)/limits | grep "open files"

# Check how many fds a process has open right now
ls /proc/$(pgrep myservice)/fd | wc -l

# See what each fd points to
ls -la /proc/$(pgrep myservice)/fd
```

## Why It Matters

Every network connection is a file descriptor. Every goroutine in a Go HTTP server that's handling a request holds at least one socket fd. If your service handles 10,000 concurrent connections and the default limit is 1,024, you will exhaust fds quickly.

The cascade failure looks like this:
1. fd table full → `accept()` fails with `EMFILE` (too many open files)
2. New connections are rejected at the OS level
3. Application logs show `too many open files`
4. Load balancer health checks fail → service removed from rotation
5. Traffic shifts to other instances → they get more load → they hit the limit too

Common fd leaks in Go:
- Opening files without `defer f.Close()`
- HTTP responses where you read the body but don't close it: `resp.Body.Close()` is mandatory
- Database connections not returned to the pool (calling `rows.Close()` is not optional)
- Goroutines that block forever on a channel — if they hold a file descriptor, it leaks

## Production Example

Setting the fd limit correctly for a high-connection service:

```bash
# In /etc/security/limits.conf or a systemd unit file
# For systemd services:
[Service]
LimitNOFILE=65536
```

```go
// At startup, log the current fd limit so you can catch misconfiguration
import "syscall"

func logFDLimit() {
    var rlimit syscall.Rlimit
    if err := syscall.Getrlimit(syscall.RLIMIT_NOFILE, &rlimit); err != nil {
        log.Printf("getrlimit error: %v", err)
        return
    }
    log.Printf("fd limit: soft=%d hard=%d", rlimit.Cur, rlimit.Max)

    // Optionally raise soft limit to hard limit
    rlimit.Cur = rlimit.Max
    if err := syscall.Setrlimit(syscall.RLIMIT_NOFILE, &rlimit); err != nil {
        log.Printf("setrlimit error: %v", err)
    }
}
```

For detecting fd leaks in production, expose a metric:

```go
func countOpenFDs() (int, error) {
    entries, err := os.ReadDir("/proc/self/fd")
    if err != nil {
        return 0, err
    }
    return len(entries), nil
}

// Register as a Prometheus gauge
fdGauge := prometheus.NewGauge(prometheus.GaugeOpts{
    Name: "process_open_fds",
    Help: "Number of open file descriptors",
})
prometheus.MustRegister(fdGauge)

go func() {
    for range time.Tick(15 * time.Second) {
        n, err := countOpenFDs()
        if err == nil {
            fdGauge.Set(float64(n))
        }
    }
}()
```

The common Go mistake with HTTP response bodies:

```go
// WRONG — fd leak
resp, err := http.Get(url)
if err != nil {
    return err
}
// forgot resp.Body.Close() — the socket fd leaks

// CORRECT
resp, err := http.Get(url)
if err != nil {
    return err
}
defer resp.Body.Close()
body, err := io.ReadAll(resp.Body)
```

## The Tradeoffs

**Increasing limits**: setting `LimitNOFILE=unlimited` in systemd is possible but inadvisable. Each fd has a kernel-side cost (a struct in memory, kernel bookkeeping). For most services, 65,536 is sufficient. For services that genuinely need more (proxy servers, databases), 1,048,576 is common.

**`SO_REUSEPORT`**: multiple processes/goroutines can bind to the same port, each with their own accept queue. This reduces the load on a single fd for incoming connections.

**`fork()` and fd inheritance**: by default, child processes inherit all open file descriptors. Using `O_CLOEXEC` when opening files prevents this. Go's `os.Open` sets `O_CLOEXEC` automatically.

**The `select()` limitation**: the traditional `select()` syscall only works on fds up to 1023. This is why `epoll` was invented — no fd number limit. We cover this in Lesson 5.

## Key Takeaway

A file descriptor is the kernel's integer handle to any open resource: files, sockets, pipes, devices. Every network connection consumes one. The per-process limit defaults to 1,024 on many systems — far too low for backend services. Raise it to 65,536 or more via systemd. Always close fds explicitly: `defer f.Close()`, `defer resp.Body.Close()`, `defer rows.Close()`. Monitor open fd count in production and alert before you hit the limit.

---

**Previous:** [Lesson 2: Virtual Memory](/post/fundamentals/linux-virtual-memory/) | **Next:** [Lesson 4: TCP/IP Stack — SYN Floods, TIME_WAIT, Connection Tuning](/post/fundamentals/linux-tcp-ip/)
