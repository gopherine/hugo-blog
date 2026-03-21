---
title: 'Lesson 3: Health Checks — Readiness vs liveness, and why both matter'
author: Atharva Pandey
series: "Go Deployment & Operations"
lesson: 3
keywords:
  - Go
  - golang
  - health check
  - readiness probe
  - liveness probe
  - Kubernetes
  - net/http
  - deployment
tags:
  - Go tutorial
  - golang
  - deployment
  - devops
date: '2024-09-22T00:00:00.000Z'
---

I've seen two types of health check implementations in production: the ones that always return 200 OK, and the ones that actually check something. The first kind is theater — Kubernetes thinks the pod is healthy and routes traffic to it, but the pod is actually deadlocked or its database connection pool is exhausted. The second kind is what saves you at 3am.

Health checks are Kubernetes's mechanism for deciding when a pod is ready to receive traffic and when it needs to be restarted. Get them right and Kubernetes becomes a genuinely reliable self-healing system. Get them wrong and you have an elaborate system that sends traffic to broken pods and restarts healthy ones.

## The Problem

The minimal health check that everyone starts with:

```go
// WRONG — tells Kubernetes nothing useful
http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
    w.WriteHeader(http.StatusOK)
})
```

This passes the liveness probe as long as the HTTP server is accepting connections. But what if the database connection pool is saturated? What if the service is in the middle of startup and hasn't loaded its configuration yet? What if a critical background goroutine panicked silently? The `/health` endpoint returns 200 for all of these.

The second problem: conflating readiness and liveness. Some services implement only one endpoint and use it for both probes. Liveness and readiness mean different things and should be separate:

- **Liveness**: Is this pod alive? If not, restart it. Use for detecting deadlocks, panics, and unrecoverable states.
- **Readiness**: Is this pod ready to serve traffic? If not, remove it from the load balancer. Use for startup completion, database connectivity, and dependency checks.

A pod can be alive (process running, not deadlocked) but not ready (still warming up its cache, or database is temporarily unavailable). You want to remove it from rotation — not restart it.

## The Idiomatic Way

A production health check implementation with separate endpoints:

```go
package health

import (
    "context"
    "encoding/json"
    "net/http"
    "sync/atomic"
    "time"
)

// Checker is the interface a dependency must implement to participate in health checks.
type Checker interface {
    Check(ctx context.Context) error
}

// Handler exposes /healthz (liveness) and /readyz (readiness) endpoints.
type Handler struct {
    checks   map[string]Checker
    ready    atomic.Bool
}

func NewHandler(checks map[string]Checker) *Handler {
    return &Handler{checks: checks}
}

// SetReady marks the service as ready to receive traffic.
// Call this after startup initialization is complete.
func (h *Handler) SetReady(ready bool) {
    h.ready.Store(ready)
}

// LivenessHandler returns 200 if the process is running and not stuck.
// It performs no external checks — just confirms the process is alive.
func (h *Handler) LivenessHandler(w http.ResponseWriter, r *http.Request) {
    w.Header().Set("Content-Type", "application/json")
    w.WriteHeader(http.StatusOK)
    json.NewEncoder(w).Encode(map[string]string{"status": "alive"})
}

// ReadinessHandler returns 200 only when the service is fully ready.
// It checks all registered dependencies.
func (h *Handler) ReadinessHandler(w http.ResponseWriter, r *http.Request) {
    w.Header().Set("Content-Type", "application/json")

    if !h.ready.Load() {
        w.WriteHeader(http.StatusServiceUnavailable)
        json.NewEncoder(w).Encode(map[string]string{
            "status": "not_ready",
            "reason": "startup in progress",
        })
        return
    }

    // Run all dependency checks with a tight timeout.
    ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
    defer cancel()

    results := make(map[string]string, len(h.checks))
    allHealthy := true

    for name, checker := range h.checks {
        if err := checker.Check(ctx); err != nil {
            results[name] = err.Error()
            allHealthy = false
        } else {
            results[name] = "ok"
        }
    }

    if !allHealthy {
        w.WriteHeader(http.StatusServiceUnavailable)
        json.NewEncoder(w).Encode(map[string]any{
            "status": "unhealthy",
            "checks": results,
        })
        return
    }

    w.WriteHeader(http.StatusOK)
    json.NewEncoder(w).Encode(map[string]any{
        "status": "ready",
        "checks": results,
    })
}
```

Implementing checkers for common dependencies:

```go
// DBChecker verifies the database pool has an active connection.
type DBChecker struct{ db *sql.DB }

func (c *DBChecker) Check(ctx context.Context) error {
    return c.db.PingContext(ctx)
}

// RedisChecker verifies the Redis connection.
type RedisChecker struct{ rdb *redis.Client }

func (c *RedisChecker) Check(ctx context.Context) error {
    return c.rdb.Ping(ctx).Err()
}

// KafkaChecker verifies the Kafka writer can connect to at least one broker.
type KafkaChecker struct{ writer *kafka.Writer }

func (c *KafkaChecker) Check(ctx context.Context) error {
    // Writer exposes Stats() — check that at least one broker is reachable.
    stats := c.writer.Stats()
    if stats.Errors > 0 && stats.Writes == 0 {
        return fmt.Errorf("kafka writer has errors with no successful writes")
    }
    return nil
}
```

Wiring it all together in `main`:

```go
func main() {
    db := openDB()
    rdb := openRedis()

    healthHandler := health.NewHandler(map[string]health.Checker{
        "database": &health.DBChecker{DB: db},
        "redis":    &health.RedisChecker{RDB: rdb},
    })

    mux := http.NewServeMux()
    mux.HandleFunc("/healthz", healthHandler.LivenessHandler)
    mux.HandleFunc("/readyz", healthHandler.ReadinessHandler)
    mux.Handle("/", appHandler)

    // Mark ready after initialization is complete
    if err := warmUpCache(db); err != nil {
        log.Fatalf("warm up: %v", err)
    }
    healthHandler.SetReady(true)

    log.Fatal(http.ListenAndServe(":8080", mux))
}
```

## In The Wild

The Kubernetes probe configuration to match:

```yaml
livenessProbe:
  httpGet:
    path: /healthz
    port: 8080
  # Start checking after 10 seconds — give the process time to start.
  initialDelaySeconds: 10
  # Check every 15 seconds.
  periodSeconds: 15
  # Allow 3 consecutive failures before restarting.
  failureThreshold: 3
  timeoutSeconds: 5

readinessProbe:
  httpGet:
    path: /readyz
    port: 8080
  # More frequent checks — we want traffic to start quickly after startup.
  initialDelaySeconds: 5
  periodSeconds: 5
  # Remove from load balancer after 2 consecutive failures.
  failureThreshold: 2
  # Add back after 1 success.
  successThreshold: 1
  timeoutSeconds: 3
```

The startup probe is a third option worth knowing about — it disables liveness checks during initial startup to give slow-starting applications time to initialize without being killed:

```yaml
startupProbe:
  httpGet:
    path: /readyz
    port: 8080
  # Allow up to 30 × 10s = 300s for startup
  failureThreshold: 30
  periodSeconds: 10
```

Once the startup probe succeeds once, Kubernetes switches to the liveness probe. This prevents premature restarts for services with genuinely long initialization phases — loading a large ML model, warming a cache from a snapshot, etc.

## The Gotchas

**Don't check external dependencies in the liveness probe.** If the database goes down and your liveness probe checks the database, Kubernetes will restart every pod simultaneously — which is worse than leaving them running without database access. Liveness should only check if the process itself is alive and functional. Readiness checks dependencies.

**Probe timeout must be less than period.** If `timeoutSeconds` is 5 and `periodSeconds` is also 5, a slow response delays the next check. Set timeout to half the period or less.

**Healthy pods are not necessarily correct.** Health checks verify availability, not correctness. A service that's returning 200 for every request but producing wrong data passes all health checks. Use integration tests and end-to-end monitoring for correctness; use health checks for availability.

**The health check endpoint itself needs a timeout.** If your database check blocks for 30 seconds and the probe timeout is 5 seconds, the probe fails but the goroutine is still blocked. Use `context.WithTimeout` inside every check function, not just at the handler level.

## Key Takeaway

Two endpoints, two contracts: `/healthz` tells Kubernetes whether to restart the pod; `/readyz` tells Kubernetes whether to send it traffic. Liveness checks nothing external — it only confirms the process hasn't deadlocked. Readiness checks all critical dependencies with a tight timeout. Mark the service not-ready during startup and dependency outages. That's it.

---

**Previous:** [Lesson 2: Docker for Go](/post/go/go-deploy-docker/)
**Next:** [Lesson 4: Config Injection — Environment variables, flags, files — in that order](/post/go/go-deploy-config-injection/)
