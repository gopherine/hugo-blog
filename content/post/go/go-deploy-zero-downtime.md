---
title: 'Lesson 8: Zero-Downtime Deploys — Rolling updates without dropping requests'
author: Atharva Pandey
series: "Go Deployment & Operations"
lesson: 8
keywords:
  - Go
  - golang
  - zero downtime
  - graceful shutdown
  - Kubernetes
  - rolling update
  - SIGTERM
  - deployment
tags:
  - Go tutorial
  - golang
  - deployment
  - devops
date: '2025-06-15T00:00:00.000Z'
---

The first rolling deployment I did without graceful shutdown handling produced about 200 errors for in-flight requests. Kubernetes sent `SIGTERM` to the old pods, the Go process exited immediately, and every active request was terminated mid-flight. The users got 502s. The monitoring dashboard lit up red for about 30 seconds per deploy, every time.

Kubernetes's rolling update strategy replaces pods one at a time and can achieve zero request drops — but only if your application cooperates. The contract is: Kubernetes sends `SIGTERM`, your application finishes in-flight requests, then exits. If you ignore `SIGTERM` and exit immediately, Kubernetes kills you with `SIGKILL` after the grace period anyway, and you drop the requests. The work is in your application code, not in the Kubernetes config.

## The Problem

The naive `main.go` that ignores shutdown signals:

```go
// WRONG — exits immediately on SIGTERM, dropping in-flight requests
func main() {
    mux := http.NewServeMux()
    mux.HandleFunc("/api/", apiHandler)
    log.Fatal(http.ListenAndServe(":8080", mux))
}
```

When Kubernetes terminates this pod, the process exits the moment `SIGTERM` arrives. Any request that was being processed — reading the body, running a database query, writing the response — is terminated. The client gets a connection reset or a 502 from the load balancer.

The second problem is the Kubernetes timing gap. After Kubernetes sends `SIGTERM`, there's a delay before the pod is removed from the service endpoints. During this gap, the load balancer might still route new requests to the pod. Your application needs to handle this gracefully too — not just drain in-flight requests, but also stop accepting new ones through the readiness probe before acknowledging the shutdown.

## The Idiomatic Way

Graceful shutdown in Go requires two things: catching `SIGTERM`/`SIGINT`, and calling `http.Server.Shutdown()` with a timeout:

```go
package main

import (
    "context"
    "errors"
    "log/slog"
    "net/http"
    "os"
    "os/signal"
    "syscall"
    "time"
)

func main() {
    // Readiness state — we'll flip this on shutdown to stop receiving new traffic
    // before we start draining in-flight requests.
    var isReady atomic.Bool
    isReady.Store(true)

    mux := http.NewServeMux()
    mux.HandleFunc("/api/", apiHandler)
    mux.HandleFunc("/readyz", func(w http.ResponseWriter, r *http.Request) {
        if !isReady.Load() {
            http.Error(w, "shutting down", http.StatusServiceUnavailable)
            return
        }
        w.WriteHeader(http.StatusOK)
    })
    mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
        w.WriteHeader(http.StatusOK)
    })

    server := &http.Server{
        Addr:         ":8080",
        Handler:      mux,
        ReadTimeout:  15 * time.Second,
        WriteTimeout: 30 * time.Second,
        IdleTimeout:  60 * time.Second,
    }

    // Start server in background
    serverErr := make(chan error, 1)
    go func() {
        slog.Info("server starting", "addr", server.Addr)
        if err := server.ListenAndServe(); !errors.Is(err, http.ErrServerClosed) {
            serverErr <- err
        }
    }()

    // Wait for shutdown signal
    quit := make(chan os.Signal, 1)
    signal.Notify(quit, syscall.SIGTERM, syscall.SIGINT)

    select {
    case sig := <-quit:
        slog.Info("shutdown signal received", "signal", sig)
    case err := <-serverErr:
        slog.Error("server error", "err", err)
        os.Exit(1)
    }

    // Step 1: Mark not-ready so Kubernetes stops routing new requests here.
    // The readiness probe will start returning 503.
    isReady.Store(false)
    slog.Info("marked not-ready")

    // Step 2: Wait for Kubernetes to propagate the endpoint removal.
    // This is the "preStop sleep" — give the load balancer time to stop
    // sending new requests before we close the server.
    time.Sleep(5 * time.Second)

    // Step 3: Gracefully shut down — wait for in-flight requests to complete.
    // The timeout must be less than terminationGracePeriodSeconds in the pod spec.
    ctx, cancel := context.WithTimeout(context.Background(), 25*time.Second)
    defer cancel()

    if err := server.Shutdown(ctx); err != nil {
        slog.Error("shutdown error", "err", err)
    }

    slog.Info("server stopped gracefully")
}
```

The 5-second sleep after marking not-ready is the critical piece that most implementations miss. When Kubernetes sends `SIGTERM`, there's a delay before the kube-proxy on each node updates its iptables rules to stop routing to this pod. During that gap, new requests can still arrive. The sleep gives the cluster time to propagate the endpoint removal.

The Kubernetes configuration needs to match:

```yaml
spec:
  containers:
    - name: myapp
      # ...
      lifecycle:
        preStop:
          exec:
            # Alternative approach: preStop hook adds the delay before SIGTERM
            # Use either the sleep in code OR the preStop hook, not both
            command: ["/bin/sh", "-c", "sleep 5"]

  # Must be longer than your graceful shutdown timeout + the sleep
  terminationGracePeriodSeconds: 60

  readinessProbe:
    httpGet:
      path: /readyz
      port: 8080
    periodSeconds: 2
    failureThreshold: 1  # stop routing quickly once not-ready
```

The rolling update strategy controls how many old pods are replaced at a time:

```yaml
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxSurge: 1        # allow 1 extra pod during rollout
    maxUnavailable: 0  # never reduce capacity below desired
```

With `maxUnavailable: 0`, Kubernetes always starts the new pod and waits for it to pass readiness checks before terminating an old pod. No capacity reduction, no traffic spikes.

## In The Wild

For services that hold long-lived connections — WebSocket servers, gRPC streaming servers — graceful shutdown is more involved. `http.Server.Shutdown` stops accepting new connections and waits for active ones to finish, but it doesn't close hijacked connections (which WebSockets use). You need to track these manually:

```go
// Track active WebSocket connections
type wsHub struct {
    mu    sync.Mutex
    conns map[*websocket.Conn]struct{}
}

func (h *wsHub) add(c *websocket.Conn) {
    h.mu.Lock()
    h.conns[c] = struct{}{}
    h.mu.Unlock()
}

func (h *wsHub) remove(c *websocket.Conn) {
    h.mu.Lock()
    delete(h.conns, c)
    h.mu.Unlock()
}

// On shutdown, close all active connections with a close message
func (h *wsHub) closeAll() {
    h.mu.Lock()
    defer h.mu.Unlock()
    for conn := range h.conns {
        conn.WriteMessage(websocket.CloseMessage,
            websocket.FormatCloseMessage(websocket.CloseGoingAway, "server shutdown"))
        conn.Close()
    }
}
```

Background workers — goroutines running periodic tasks, queue consumers, outbox relays — also need graceful shutdown. Use a `context.Context` derived from the shutdown signal:

```go
ctx, cancel := context.WithCancel(context.Background())

// Start background workers with the cancellable context
go outboxRelay(ctx, db, kafkaWriter)
go metricsCollector(ctx)

// On shutdown signal
signal.Notify(quit, syscall.SIGTERM)
<-quit
cancel() // tells all background goroutines to stop
// ... then shut down the HTTP server
```

## The Gotchas

**`terminationGracePeriodSeconds` must be longer than your shutdown timeout.** If Kubernetes's grace period is 30 seconds and your application takes 35 seconds to drain, Kubernetes sends `SIGKILL` before you're done. Set the pod's `terminationGracePeriodSeconds` to at least 2x your longest expected request duration plus the pre-shutdown sleep.

**The preStop hook runs before SIGTERM.** Kubernetes's lifecycle hook order is: preStop hook → SIGTERM → wait → SIGKILL. If you use both a preStop sleep and a sleep in your shutdown code, they run sequentially and extend the total shutdown time.

**Don't block on `signal.Notify` without a buffered channel.** The signal notification is asynchronous. If the goroutine isn't blocked on the channel when the signal arrives, the signal is dropped. Use `make(chan os.Signal, 1)` — a buffered channel — so the signal is never dropped.

**Test your shutdown.** Run `kill -TERM $(pgrep myapp)` against a running instance and verify in-flight requests complete successfully. Use `wrk` or `hey` to generate continuous load and measure the error rate during shutdown. Zero-downtime is a claim that needs to be verified, not assumed.

## Key Takeaway

Zero-downtime deploys require your application to cooperate with Kubernetes's rolling update mechanism. The sequence: receive SIGTERM, mark not-ready, sleep to let the load balancer drain, call `http.Server.Shutdown` with a context timeout. Set `terminationGracePeriodSeconds` to exceed the total shutdown time. Set `maxUnavailable: 0` to prevent capacity reduction during rollouts. Test it under load.

🎓 **Course Complete!** You've finished Go Deployment & Operations. You can now build static Go binaries, containerize them in tiny Docker images, implement proper health checks, inject configuration from the environment, set up CI pipelines with race detection, profile in production containers, and deploy with zero dropped requests.

---

**Previous:** [Lesson 7: Profiling in Containers](/post/go/go-deploy-profiling-containers/)
**Up next:** Go Standard Library Mastery — starting with net/http
