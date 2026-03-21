---
title: 'Lesson 1: net/http Deep Dive — The most important package you use wrong'
author: Atharva Pandey
series: "Go Standard Library Mastery"
lesson: 1
keywords:
  - Go
  - golang
  - net/http
  - HTTP server
  - ServeMux
  - Handler
  - middleware
  - stdlib
tags:
  - Go tutorial
  - golang
  - stdlib
date: '2024-07-02T00:00:00.000Z'
---

Every Go web service I've reviewed starts with `net/http`. Most of them use it correctly for the obvious parts and incorrectly for the subtle ones. The package API is deceptively simple — `http.HandleFunc`, `http.ListenAndServe`, and you're serving HTTP. But the design decisions underneath — how request routing works, what a `Handler` actually is, how the server manages connections, what the default timeouts are — contain enough traps to keep a senior engineer busy for a week.

`net/http` is the package I've spent the most time studying in the entire standard library. Here's what I wish I'd known at the start.

## The Problem

The minimal Go web server compiles and runs without complaint:

```go
// COMPILES. RUNS. HAS MULTIPLE PRODUCTION BUGS.
func main() {
    http.HandleFunc("/api/users", getUsers)
    http.HandleFunc("/api/users/", getUserByID)
    log.Fatal(http.ListenAndServe(":8080", nil))
}
```

Bug one: `nil` as the handler means using `http.DefaultServeMux`, which is a package-level variable. Any package you import can register handlers on it with `http.HandleFunc` — including test utilities, debug packages, and any third-party library that calls `http.HandleFunc` in its `init`. You don't control what's on this mux.

Bug two: no timeouts. `http.ListenAndServe` with default settings has no read timeout, no write timeout, and no idle timeout. A client that opens a connection and sends headers very slowly will occupy a goroutine and a file descriptor indefinitely.

Bug three: the routing behavior of `net/http`'s `ServeMux` has a specific subtlety. The `/api/users/` pattern (trailing slash) is a subtree pattern and matches anything starting with that prefix. The `/api/users` pattern (no trailing slash) is an exact match. But there's an automatic redirect: if you request `/api/users` and only the subtree `/api/users/` is registered, the mux redirects to `/api/users/`. This is often surprising.

## The Idiomatic Way

A production-ready server starts with a configured `http.Server`, not `http.ListenAndServe`:

```go
package main

import (
    "log"
    "net/http"
    "time"
)

func main() {
    mux := http.NewServeMux()
    registerRoutes(mux)

    server := &http.Server{
        Addr:    ":8080",
        Handler: mux,

        // Timeouts prevent resource exhaustion from slow/malicious clients.
        // ReadTimeout covers reading the entire request including body.
        ReadTimeout: 15 * time.Second,
        // WriteTimeout covers writing the response. Should be > ReadTimeout
        // to allow processing time after the request is read.
        WriteTimeout: 30 * time.Second,
        // IdleTimeout applies to keep-alive connections between requests.
        IdleTimeout: 60 * time.Second,
        // ReadHeaderTimeout is separate from ReadTimeout and applies to
        // just the headers. Useful for slow header attacks.
        ReadHeaderTimeout: 5 * time.Second,
    }

    log.Printf("listening on %s", server.Addr)
    if err := server.ListenAndServe(); err != nil {
        log.Fatal(err)
    }
}
```

The `Handler` interface is the core abstraction:

```go
type Handler interface {
    ServeHTTP(ResponseWriter, *Request)
}
```

Any type with a `ServeHTTP` method is an `http.Handler`. This is the foundation for middleware — wrapping one handler with another:

```go
// Middleware: wraps any handler to add logging
func withLogging(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        start := time.Now()

        // Use a ResponseWriter wrapper to capture the status code.
        rw := &responseWriter{ResponseWriter: w, status: http.StatusOK}
        next.ServeHTTP(rw, r)

        log.Printf("%s %s %d %v", r.Method, r.URL.Path, rw.status, time.Since(start))
    })
}

// responseWriter wraps http.ResponseWriter to capture status codes.
type responseWriter struct {
    http.ResponseWriter
    status int
    written bool
}

func (rw *responseWriter) WriteHeader(status int) {
    rw.status = status
    rw.written = true
    rw.ResponseWriter.WriteHeader(status)
}

// Stacking middleware — applied right to left, so logging runs outermost
handler := withLogging(withAuth(withRecovery(mux)))
```

Go 1.22 introduced path parameters and method routing in the standard `ServeMux`, eliminating the need for third-party routers for most services:

```go
mux := http.NewServeMux()

// Method + path routing (Go 1.22+)
mux.HandleFunc("GET /api/users", listUsers)
mux.HandleFunc("POST /api/users", createUser)
mux.HandleFunc("GET /api/users/{id}", getUser)
mux.HandleFunc("PUT /api/users/{id}", updateUser)
mux.HandleFunc("DELETE /api/users/{id}", deleteUser)

// Path parameters accessed via Request.PathValue
func getUser(w http.ResponseWriter, r *http.Request) {
    id := r.PathValue("id")
    // ...
}
```

## In The Wild

Request context is how you pass values and cancellation signals through the handler chain:

```go
type contextKey string

const (
    contextKeyUserID  contextKey = "userID"
    contextKeyTraceID contextKey = "traceID"
)

// Auth middleware: extract user from token, add to context
func withAuth(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        token := r.Header.Get("Authorization")
        userID, err := validateToken(token)
        if err != nil {
            http.Error(w, "unauthorized", http.StatusUnauthorized)
            return
        }

        ctx := context.WithValue(r.Context(), contextKeyUserID, userID)
        next.ServeHTTP(w, r.WithContext(ctx))
    })
}

// Handler: extract user from context — type-safe access
func getUser(w http.ResponseWriter, r *http.Request) {
    userID, ok := r.Context().Value(contextKeyUserID).(string)
    if !ok {
        http.Error(w, "no user in context", http.StatusInternalServerError)
        return
    }
    // use userID
}
```

The `r.WithContext(ctx)` call creates a shallow copy of the request with the new context attached — the original request is unmodified.

## The Gotchas

**`http.Error` does not stop handler execution.** After calling `http.Error(w, "message", code)`, the handler function continues executing. If you have code after the `http.Error` call, it runs. Always pair `http.Error` with a `return`.

**Writing to `ResponseWriter` after the handler returns panics in tests.** If you launch a goroutine inside a handler that writes to `w` after the handler function returns, you'll get a panic in httptest. Goroutines should not escape the handler's lifetime.

**Registered patterns are permanent.** You can't unregister a handler from `http.ServeMux` after it's been registered. For tests, create a new mux per test.

**The request body is limited.** There's no default limit on request body size. A client can send a 10GB body and your handler will try to read it. Use `http.MaxBytesReader` to limit body size:

```go
r.Body = http.MaxBytesReader(w, r.Body, 1<<20) // 1 MB limit
```

**TLS termination.** In Kubernetes, TLS is usually terminated by the ingress controller, and your Go service sees plain HTTP. But if you need TLS in the service itself, use `server.ListenAndServeTLS(certFile, keyFile)` — never implement TLS manually.

## Key Takeaway

`net/http` is the right choice for most Go web services without a third-party framework. Configure `http.Server` explicitly — never use `http.ListenAndServe` without timeouts. Use your own `ServeMux` — never use `http.DefaultServeMux`. Understand the `Handler` interface and middleware wrapping. Know what `ReadTimeout` and `WriteTimeout` cover. These foundations carry you far.

---

**Previous:** Series introduction
**Next:** [Lesson 2: io Patterns — Reader, Writer, and the composability that makes Go great](/post/go/go-stdlib-io/)
