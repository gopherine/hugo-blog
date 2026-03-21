---
title: "Lesson 1: Designing HTTP APIs in Go — net/http is enough"
author: Atharva Pandey
series: "Go API and Service Design"
lesson: 1
keywords: [Go, golang, API, backend, net/http, REST, HTTP design]
tags: [Go tutorial, golang, backend]
date: '2024-06-12T00:00:00.000Z'
---

I spent the first few months of writing Go services reaching straight for a framework. Chi, Gorilla, Echo — I cycled through them trying to find the "right" one. Then a colleague reviewed my code and asked a simple question: "What does this framework give you that `net/http` doesn't?" I couldn't answer. That conversation changed how I think about HTTP in Go.

## The Problem

Most developers coming from Node.js or Python assume they need a framework to build a real HTTP API. Express, FastAPI, Django — these tools abstract away so much of the protocol that going back to the raw standard library feels like stepping down. But Go's `net/http` package is not an afterthought. It was designed with production use in mind, and the gap between it and popular frameworks is much smaller than you'd expect.

The real problem is not missing functionality — it's knowing what the standard library already provides and how to structure code around it cleanly. Without that knowledge, you end up with a framework dependency that mostly just adds a router and middleware chain, both of which you can build yourself in a hundred lines.

## The Idiomatic Way

The core of any Go HTTP service is `http.Handler`:

```go
type Handler interface {
    ServeHTTP(ResponseWriter, *Request)
}
```

Everything in `net/http` speaks this interface. You can compose handlers, wrap them, and route between them without any third-party code. Here is a minimal but production-shaped API:

```go
package main

import (
    "encoding/json"
    "log"
    "net/http"
    "time"
)

type Server struct {
    router *http.ServeMux
    logger *log.Logger
}

func NewServer(logger *log.Logger) *Server {
    s := &Server{
        router: http.NewServeMux(),
        logger: logger,
    }
    s.routes()
    return s
}

func (s *Server) routes() {
    s.router.HandleFunc("GET /health", s.handleHealth)
    s.router.HandleFunc("GET /api/v1/users/{id}", s.handleGetUser)
    s.router.HandleFunc("POST /api/v1/users", s.handleCreateUser)
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    s.router.ServeHTTP(w, r)
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
    writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

type User struct {
    ID    string `json:"id"`
    Name  string `json:"name"`
    Email string `json:"email"`
}

func (s *Server) handleGetUser(w http.ResponseWriter, r *http.Request) {
    id := r.PathValue("id")
    // fetch from DB — omitted for brevity
    user := User{ID: id, Name: "Atharva", Email: "a@example.com"}
    writeJSON(w, http.StatusOK, user)
}

func (s *Server) handleCreateUser(w http.ResponseWriter, r *http.Request) {
    var req User
    if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
        writeError(w, http.StatusBadRequest, "invalid request body")
        return
    }
    req.ID = "generated-id"
    writeJSON(w, http.StatusCreated, req)
}

func writeJSON(w http.ResponseWriter, status int, v any) {
    w.Header().Set("Content-Type", "application/json")
    w.WriteHeader(status)
    json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, status int, msg string) {
    writeJSON(w, status, map[string]string{"error": msg})
}

func main() {
    logger := log.Default()
    srv := &http.Server{
        Addr:         ":8080",
        Handler:      NewServer(logger),
        ReadTimeout:  5 * time.Second,
        WriteTimeout: 10 * time.Second,
        IdleTimeout:  120 * time.Second,
    }
    logger.Printf("listening on %s", srv.Addr)
    log.Fatal(srv.ListenAndServe())
}
```

Notice a few things. The `Server` struct implements `http.Handler` directly. This means you can pass it wherever a handler is expected — including in tests. The `routes()` method lives in one place. And since Go 1.22, the standard `http.ServeMux` supports method-scoped routing with path parameters (`{id}`), eliminating the main reason people reached for third-party routers.

The `writeJSON` helper is tiny but eliminates a class of bugs. Forgetting to set `Content-Type` before calling `WriteHeader` is a common mistake that causes silent failures downstream.

## In The Wild

Real services need versioned routes, grouped prefixes, and per-route middleware. Here is how I structure that without a framework:

```go
func (s *Server) routes() {
    // Health lives outside versioned prefix
    s.router.HandleFunc("GET /health", s.handleHealth)

    // v1 sub-mux — all middleware applied once here
    v1 := http.NewServeMux()
    v1.HandleFunc("GET /users/{id}", s.handleGetUser)
    v1.HandleFunc("POST /users", s.handleCreateUser)
    v1.HandleFunc("GET /posts", s.handleListPosts)

    // Strip /api/v1 prefix before handing off to v1 mux
    s.router.Handle("/api/v1/", http.StripPrefix("/api/v1", chain(
        v1,
        s.withLogging,
        s.withAuth,
    )))
}

// chain applies middleware right-to-left so the first in the list is outermost
func chain(h http.Handler, middleware ...func(http.Handler) http.Handler) http.Handler {
    for i := len(middleware) - 1; i >= 0; i-- {
        h = middleware[i](h)
    }
    return h
}
```

This pattern gives you the ergonomics of a framework's route grouping using nothing but the standard library. When you need to switch Go versions or audit dependencies, there is nothing to update here.

## The Gotchas

**Headers must be set before WriteHeader.** Once you call `w.WriteHeader(status)`, the response head is sent. Any `w.Header().Set(...)` calls after that are silently ignored. My `writeJSON` helper always sets `Content-Type` before `WriteHeader` for exactly this reason.

**ServeMux trailing-slash semantics.** A route registered as `/api/v1/` matches everything under that prefix. A route registered as `/api/v1` only matches the exact path. This trips people up when they expect a 404 but get a redirect instead.

**Path parameters require Go 1.22.** If you are on an older version, `r.PathValue("id")` will always return an empty string without any error. Double-check your `go.mod` toolchain directive.

**http.Server timeouts are not optional.** The zero value for `ReadTimeout` and `WriteTimeout` means no timeout at all. A slow client can hold a goroutine open indefinitely. Always set at minimum `ReadTimeout`, `WriteTimeout`, and `IdleTimeout`.

**`json.NewEncoder(w).Encode(v)` appends a newline.** This is usually harmless for JSON APIs but worth knowing when comparing raw responses in tests.

## Key Takeaway

Go's `net/http` has grown substantially with each release. Method-based routing, path parameters, and clean middleware composition are all available without any dependency. Reach for a framework when you have a specific, measurable problem it solves — not as a reflex. A service built on the standard library is easier to understand, easier to test, and has zero import-graph risk. I start every new Go API with `net/http` and add a third-party router only when I hit a concrete limit. After three years of production Go services, I have only crossed that threshold once.

---

**Series: Go API and Service Design**

[Lesson 2: Middleware Patterns — Wrap the handler, not the logic →](/post/go/go-api-middleware)
