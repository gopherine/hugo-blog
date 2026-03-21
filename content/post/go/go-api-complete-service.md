---
title: "Lesson 10: The Complete Go Service — HTTP + DB + workers + shutdown in 200 lines"
author: Atharva Pandey
series: "Go API and Service Design"
lesson: 10
keywords: [Go, golang, API, backend, complete service, graceful shutdown, workers, database, production]
tags: [Go tutorial, golang, backend]
date: '2025-06-20T00:00:00.000Z'
---

Every lesson in this series has been a piece of a puzzle. HTTP routing, middleware, validation, error responses, pagination, idempotency, timeouts, rate limiting, config management — each one addresses a specific concern in isolation. This final lesson puts them together into a single, production-shaped service that you can actually use as a starting point.

The goal is not a complete application. It is a skeleton that demonstrates how all the pieces wire together in `main.go` and what a well-structured Go service looks like before you add your business logic.

## The Problem

The jump from individual patterns to a working service is not trivial. You know how to write middleware. You know how to validate requests. But how does the server start? When does the database connection pool get created? How do background workers start without racing with the HTTP server? How does everything shut down cleanly without dropping requests?

These questions have answers that are not obvious from reading about individual patterns in isolation. The architecture has to be deliberate.

## The Idiomatic Way

The complete `main.go` — approximately 200 lines that orchestrates everything:

```go
package main

import (
    "context"
    "database/sql"
    "fmt"
    "log/slog"
    "net/http"
    "os"
    "os/signal"
    "syscall"
    "time"

    _ "github.com/lib/pq"
)

func main() {
    // 1. Load and validate all configuration up front.
    // If any required variable is missing, we exit before initialising anything.
    cfg, err := config.Load()
    if err != nil {
        fmt.Fprintf(os.Stderr, "fatal: config: %v\n", err)
        os.Exit(1)
    }

    // 2. Build the structured logger. All components share this logger.
    logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
        Level: parseLogLevel(cfg.Observability.LogLevel),
    }))

    // 3. Connect to the database and verify connectivity.
    db, err := openDB(cfg.Database)
    if err != nil {
        logger.Error("database connection failed", "error", err)
        os.Exit(1)
    }
    defer db.Close()
    logger.Info("database connected")

    // 4. Build application dependencies.
    // Repositories depend only on *sql.DB.
    // Services depend only on repositories.
    // Handlers depend only on services.
    userRepo := store.NewUserRepository(db)
    userSvc  := service.NewUserService(userRepo)
    notifSvc := service.NewNotificationService(db, logger)

    // 5. Build the HTTP server. It receives only what it needs.
    srv := api.NewServer(api.ServerDeps{
        Config:   cfg.Server,
        Logger:   logger,
        Users:    userSvc,
    })

    httpServer := &http.Server{
        Addr:         fmt.Sprintf(":%d", cfg.Server.Port),
        Handler:      srv,
        ReadTimeout:  cfg.Server.ReadTimeout,
        WriteTimeout: cfg.Server.WriteTimeout,
        IdleTimeout:  cfg.Server.IdleTimeout,
    }

    // 6. Set up graceful shutdown.
    // The root context is cancelled when SIGINT or SIGTERM is received.
    ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
    defer stop()

    // 7. Start background workers.
    // Each worker receives the root context. When it is cancelled, workers wind down.
    workerErrs := make(chan error, 2)
    go func() {
        workerErrs <- notifSvc.RunDispatcher(ctx)
    }()
    go func() {
        workerErrs <- runMetricsServer(ctx, cfg.Observability.MetricsAddr, logger)
    }()

    // 8. Start the HTTP server in a goroutine.
    serverErr := make(chan error, 1)
    go func() {
        logger.Info("http server starting", "addr", httpServer.Addr)
        if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
            serverErr <- err
        }
        close(serverErr)
    }()

    // 9. Wait for a signal or a fatal error.
    select {
    case <-ctx.Done():
        logger.Info("shutdown signal received")
    case err := <-serverErr:
        logger.Error("http server failed", "error", err)
    case err := <-workerErrs:
        logger.Error("worker failed", "error", err)
    }

    // 10. Graceful shutdown: give in-flight requests time to complete.
    shutdownCtx, cancel := context.WithTimeout(context.Background(), cfg.Server.ShutdownTimeout)
    defer cancel()

    if err := httpServer.Shutdown(shutdownCtx); err != nil {
        logger.Error("http server shutdown error", "error", err)
    }
    logger.Info("shutdown complete")
}
```

This is the skeleton. Every production Go service I have written follows this flow, with only the dependencies and workers changing.

## In The Wild

The `api.NewServer` constructor shows how to wire middleware and routes together cleanly:

```go
package api

import (
    "net/http"
    "log/slog"
)

type ServerDeps struct {
    Config config.ServerConfig
    Logger *slog.Logger
    Users  UserService
}

type Server struct {
    mux    *http.ServeMux
    logger *slog.Logger
    users  UserService
}

func NewServer(deps ServerDeps) *Server {
    s := &Server{
        mux:    http.NewServeMux(),
        logger: deps.Logger,
        users:  deps.Users,
    }
    s.routes()
    return s
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    s.mux.ServeHTTP(w, r)
}

func (s *Server) routes() {
    // Global middleware stack applied to all routes
    base := middleware.Stack(
        middleware.Recoverer(s.logger),
        middleware.RequestID,
        middleware.Logger(s.logger),
        middleware.RequestTimeout(5 * time.Second),
    )

    // Authenticated middleware stack
    authed := middleware.Stack(
        middleware.Recoverer(s.logger),
        middleware.RequestID,
        middleware.Logger(s.logger),
        middleware.RequestTimeout(5 * time.Second),
        middleware.RequireAuth(s.verifier),
        middleware.RateLimit(s.rateLimiter),
    )

    // Public routes
    s.mux.Handle("GET /health",    base(http.HandlerFunc(s.handleHealth)))
    s.mux.Handle("GET /ready",     base(http.HandlerFunc(s.handleReady)))

    // Authenticated API routes
    s.mux.Handle("GET /api/v1/users",      authed(http.HandlerFunc(s.handleListUsers)))
    s.mux.Handle("POST /api/v1/users",     authed(http.HandlerFunc(s.handleCreateUser)))
    s.mux.Handle("GET /api/v1/users/{id}", authed(http.HandlerFunc(s.handleGetUser)))
    s.mux.Handle("PUT /api/v1/users/{id}", authed(http.HandlerFunc(s.handleUpdateUser)))
}
```

The background worker pattern shows how a long-running process integrates with the context-based shutdown:

```go
package service

// RunDispatcher processes notifications until ctx is cancelled.
// It returns nil on clean shutdown, or an error on unexpected failure.
func (s *NotificationService) RunDispatcher(ctx context.Context) error {
    ticker := time.NewTicker(5 * time.Second)
    defer ticker.Stop()

    s.logger.Info("notification dispatcher started")
    for {
        select {
        case <-ctx.Done():
            s.logger.Info("notification dispatcher stopping")
            return nil
        case <-ticker.C:
            if err := s.processQueue(ctx); err != nil {
                if ctx.Err() != nil {
                    // Context cancelled during processing — clean shutdown
                    return nil
                }
                s.logger.Error("notification dispatch error", "error", err)
                // Log and continue — transient errors should not crash the worker
            }
        }
    }
}

func (s *NotificationService) processQueue(ctx context.Context) error {
    // Fetch pending notifications and send them
    // This is where your business logic lives
    notifications, err := s.repo.ListPending(ctx, 100)
    if err != nil {
        return fmt.Errorf("fetch pending: %w", err)
    }
    for _, n := range notifications {
        if err := s.send(ctx, n); err != nil {
            s.logger.Warn("failed to send notification", "id", n.ID, "error", err)
            continue
        }
        _ = s.repo.MarkSent(ctx, n.ID)
    }
    return nil
}
```

## The Gotchas

**`http.ErrServerClosed` is expected, not an error.** When you call `httpServer.Shutdown()`, `ListenAndServe` returns `http.ErrServerClosed`. This is the expected signal that the server stopped accepting connections. Do not treat it as a fatal error.

**Shutdown timeout must be longer than your longest request.** If your API has endpoints that take up to 10 seconds (a file upload, a complex query), your `ShutdownTimeout` must be at least 10 seconds or some in-flight requests will be cut off. Check your P99 latency and set the shutdown timeout generously above it.

**Workers must respect context cancellation.** A background worker that ignores `ctx.Done()` will block your shutdown indefinitely. The select pattern in `RunDispatcher` above is the correct idiom. Every blocking call inside a worker — database queries, HTTP calls, sleep loops — should accept and check the context.

**Database connection pool sizing matters.** `SetMaxOpenConns(25)` is a common default but it may be wrong for your workload. Too low and requests queue waiting for connections. Too high and you overwhelm the database. The right number depends on your database's `max_connections` setting divided by the number of service instances. Monitor `db.Stats().WaitCount` — if it is non-zero, your pool is too small.

## Key Takeaway

A production Go service is a composition of well-understood patterns: config loaded and validated at startup, structured logging shared across all components, a database pool pinged before accepting requests, an HTTP server with explicit timeouts, background workers that respect context cancellation, and a graceful shutdown sequence that gives in-flight work time to complete. None of these patterns is complex in isolation. The skill is knowing all of them and wiring them together correctly.

This is the architecture I use as the foundation for every new Go service. Not because it is perfect but because it makes every property of the service explicit — what it depends on, how it starts, how it handles failures, and how it stops. That explicitness is what makes a service maintainable six months after you first write it.

---

**Series: Go API and Service Design**

[← Lesson 9: Config Management](/post/go/go-api-config)

---

🎓 **Course Complete!** You have reached the end of the Go API and Service Design series. From `net/http` basics through middleware, validation, error design, pagination, idempotency, timeouts, rate limiting, config, and finally a complete production service — you now have a complete toolkit for building maintainable, resilient Go APIs. Go build something.
