---
title: 'Lesson 6: Signals — SIGTERM vs SIGKILL and Graceful Shutdown'
author: Atharva Pandey
series: "Linux for Backend Engineers"
lesson: 6
keywords:
  - linux
  - signals
  - SIGTERM
  - SIGKILL
  - graceful shutdown
  - Go signals
  - backend engineering
tags:
  - fundamentals
  - linux
  - operating systems
date: '2024-07-14T00:00:00.000Z'
---

The first time I deployed a service to Kubernetes and watched it restart, I noticed that requests in flight were sometimes failing with connection resets. The pod was receiving 502 responses for a few seconds before it disappeared. I had heard of "graceful shutdown" but hadn't implemented it. Kubernetes sends `SIGTERM` before killing a process, giving it time to finish in-flight requests — but my service was either ignoring the signal or exiting immediately, cutting off connections mid-request. Learning how signals work at the OS level, and then implementing correct signal handling in Go, fixed the issue permanently.

## How It Actually Works

A signal is a limited form of inter-process communication delivered by the kernel (or another process) to a process. It is asynchronous — the receiving process can be interrupted at almost any point in its execution.

Each signal has a number and a default action:

| Signal | Number | Default Action | Meaning |
|--------|--------|---------------|---------|
| `SIGHUP` | 1 | Terminate | Hang up / terminal closed |
| `SIGINT` | 2 | Terminate | Interrupt (Ctrl+C) |
| `SIGQUIT` | 3 | Core dump | Quit with core dump |
| `SIGKILL` | 9 | Terminate (unblockable) | Forced kill — cannot be caught |
| `SIGTERM` | 15 | Terminate | Termination request — can be caught |
| `SIGSTOP` | 19 | Stop (unblockable) | Pause process — cannot be caught |
| `SIGUSR1` | 10 | Terminate | User-defined signal 1 |
| `SIGUSR2` | 12 | Terminate | User-defined signal 2 |
| `SIGCHLD` | 17 | Ignore | Child process terminated |

The critical distinction:

**SIGTERM (15)**: a polite request to terminate. The process can catch this signal, run a cleanup handler, finish in-flight requests, close database connections, and exit gracefully. `SIGTERM` is what `kill <pid>` sends by default. It is also what Kubernetes, systemd, and Docker send before a hard kill.

**SIGKILL (9)**: the process is killed immediately by the kernel. No cleanup. No handlers. No chance to finish. The kernel reclaims all resources. `SIGKILL` cannot be caught, blocked, or ignored. It is `kill -9 <pid>`.

The typical shutdown sequence in orchestration systems:
1. Send `SIGTERM` to the process
2. Wait for a grace period (default 30 seconds in Kubernetes)
3. If still running, send `SIGKILL`

Your application gets the grace period to finish gracefully. If you don't handle `SIGTERM`, you get a default-action terminate — abrupt, no cleanup.

## Why It Matters

For backend services, graceful shutdown means:
- Stop accepting new connections/requests
- Allow in-flight requests to complete
- Close database connections cleanly (return them to the pool, commit/rollback open transactions)
- Flush any buffered writes (logs, metrics)
- Deregister from service discovery

Without it: clients mid-request receive connection resets. Database connections are abandoned (the server eventually notices via keepalive and cleans up). Open transactions may be left in an inconsistent state. Logs are lost.

## Production Example

In Go, signal handling is done via the `os/signal` package. Here is the pattern I use for every Go service:

```go
package main

import (
    "context"
    "log/slog"
    "net/http"
    "os"
    "os/signal"
    "syscall"
    "time"
)

func main() {
    server := &http.Server{Addr: ":8080", Handler: routes()}

    // Start server in a goroutine
    serverErr := make(chan error, 1)
    go func() {
        slog.Info("server starting", "addr", server.Addr)
        if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
            serverErr <- err
        }
    }()

    // Subscribe to termination signals
    quit := make(chan os.Signal, 1)
    signal.Notify(quit, syscall.SIGTERM, syscall.SIGINT)

    // Block until signal or server error
    select {
    case err := <-serverErr:
        slog.Error("server failed to start", "err", err)
        os.Exit(1)

    case sig := <-quit:
        slog.Info("shutdown signal received", "signal", sig)
    }

    // Graceful shutdown with a deadline
    // This is the "grace period" — in-flight requests have up to 30s to complete
    ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
    defer cancel()

    slog.Info("shutting down server...")
    if err := server.Shutdown(ctx); err != nil {
        slog.Error("server shutdown failed", "err", err)
        os.Exit(1)
    }
    slog.Info("server stopped cleanly")

    // Close other resources: DB pool, message queue connections, etc.
    // db.Close()
    // mqConn.Close()
}
```

`http.Server.Shutdown()` stops the listener, waits for all active connections to finish, and returns. Connections that are idle (keep-alive but not processing a request) are closed immediately. Active requests are allowed to finish up to the context deadline.

For SIGUSR1 as a signal to reload configuration without restart:

```go
reloadSig := make(chan os.Signal, 1)
signal.Notify(reloadSig, syscall.SIGUSR1)

go func() {
    for range reloadSig {
        slog.Info("received SIGUSR1 — reloading config")
        if err := reloadConfig(); err != nil {
            slog.Error("config reload failed", "err", err)
        }
    }
}()
```

## The Tradeoffs

**Grace period in Kubernetes**: `terminationGracePeriodSeconds` in the Pod spec (default 30s) is the time between SIGTERM and SIGKILL. Your shutdown handler must complete within this window. If your database connections take 10 seconds to close and your requests take 25 seconds to finish, you might get killed before you're done. Set your grace period longer than your worst-case shutdown time.

**PreStop hook**: Kubernetes sends SIGTERM to the container and simultaneously removes it from the service endpoint list, but these can be concurrent. Requests may still arrive after SIGTERM arrives. Adding a `preStop` sleep of 5–10 seconds delays SIGTERM slightly, giving the load balancer time to drain connections:

```yaml
lifecycle:
  preStop:
    exec:
      command: ["/bin/sh", "-c", "sleep 10"]
```

**SIGPIPE**: if your process writes to a closed pipe or socket, it receives SIGPIPE. The default action is to terminate. Go ignores SIGPIPE for pipes (returns an error) but you should be aware of it when using CGo or raw syscalls.

**Signal masking in goroutines**: signals are delivered to the process, not to a specific goroutine. The Go runtime handles signal routing. `signal.Notify` is the correct interface — never use `syscall.Signal` directly in Go programs.

## Key Takeaway

SIGTERM is a polite shutdown request that your process can catch and handle. SIGKILL is an immediate forced kill that cannot be caught. Backend services must handle SIGTERM with a graceful shutdown: stop accepting new requests, wait for in-flight requests to complete (up to a deadline), then exit cleanly. In Go, use `signal.Notify`, `http.Server.Shutdown`, and `context.WithTimeout`. Set Kubernetes's `terminationGracePeriodSeconds` to exceed your worst-case shutdown time.

---

**Previous:** [Lesson 5: Epoll and IO Multiplexing](/post/fundamentals/linux-epoll/) | **Next:** [Lesson 7: Containers from Scratch — Namespaces, Cgroups, What Docker Does](/post/fundamentals/linux-containers/)
