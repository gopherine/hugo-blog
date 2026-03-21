---
title: "Lesson 2: Middleware Patterns — Wrap the handler, not the logic"
author: Atharva Pandey
series: "Go API and Service Design"
lesson: 2
keywords: [Go, golang, API, backend, middleware, HTTP, handler]
tags: [Go tutorial, golang, backend]
date: '2024-07-05T00:00:00.000Z'
---

The first time I wrote authentication logic, I put it directly inside each handler. Copy-paste, then copy-paste again. By handler number five I had four slightly different versions of the same JWT check scattered across the codebase. When the security team asked me to add a new validation step, I had to find and update every single one. That was the day I understood middleware.

## The Problem

Business logic in a handler should answer one question: "What does this endpoint do?" But handlers inevitably accumulate cross-cutting concerns — logging, authentication, rate limiting, request ID injection, panic recovery. When these live inside the handler body, every handler becomes a tangle of concerns that are hard to test, hard to change, and impossible to apply consistently.

The naive fix is a base handler or a helper function you call at the top of every handler. That is marginally better but still requires every new handler to remember to opt in. What you really want is a mechanism that wraps behaviour around handlers without modifying them — something the HTTP stack applies automatically.

## The Idiomatic Way

In Go, middleware is just a function that takes an `http.Handler` and returns an `http.Handler`. That is the entire contract. Because `http.Handler` is an interface, you can nest as many wrappers as you like, and the inner handler never knows it is wrapped.

```go
// The middleware signature — take a handler, return a handler
type Middleware func(http.Handler) http.Handler

// RequestID injects a unique ID into every request's context
func RequestID(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        id := r.Header.Get("X-Request-ID")
        if id == "" {
            id = generateID() // uuid or similar
        }
        ctx := context.WithValue(r.Context(), requestIDKey, id)
        w.Header().Set("X-Request-ID", id)
        next.ServeHTTP(w, r.WithContext(ctx))
    })
}

// Logger logs method, path, status, and latency for every request
func Logger(logger *slog.Logger) Middleware {
    return func(next http.Handler) http.Handler {
        return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
            start := time.Now()
            rw := &responseWriter{ResponseWriter: w, status: http.StatusOK}
            next.ServeHTTP(rw, r)
            logger.Info("request",
                "method", r.Method,
                "path", r.URL.Path,
                "status", rw.status,
                "duration_ms", time.Since(start).Milliseconds(),
                "request_id", RequestIDFromContext(r.Context()),
            )
        })
    }
}

// responseWriter wraps http.ResponseWriter to capture the status code
type responseWriter struct {
    http.ResponseWriter
    status int
}

func (rw *responseWriter) WriteHeader(code int) {
    rw.status = code
    rw.ResponseWriter.WriteHeader(code)
}
```

The `responseWriter` wrapper is a pattern I use in every project. The standard `http.ResponseWriter` does not expose the status code after it has been written, so wrapping it is the only way to capture it for logging.

## In The Wild

Here is a more complete middleware stack that I use in production services — recovery from panics, authentication, and a CORS header setter, all chained together:

```go
// Recoverer catches panics, logs the stack trace, and returns a 500
func Recoverer(logger *slog.Logger) Middleware {
    return func(next http.Handler) http.Handler {
        return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
            defer func() {
                if rec := recover(); rec != nil {
                    buf := make([]byte, 4096)
                    n := runtime.Stack(buf, false)
                    logger.Error("panic recovered",
                        "panic", rec,
                        "stack", string(buf[:n]),
                    )
                    http.Error(w, "internal server error", http.StatusInternalServerError)
                }
            }()
            next.ServeHTTP(w, r)
        })
    }
}

// RequireAuth validates a Bearer token and injects the claims into context
func RequireAuth(verifier TokenVerifier) Middleware {
    return func(next http.Handler) http.Handler {
        return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
            header := r.Header.Get("Authorization")
            if !strings.HasPrefix(header, "Bearer ") {
                http.Error(w, `{"error":"missing token"}`, http.StatusUnauthorized)
                return
            }
            claims, err := verifier.Verify(strings.TrimPrefix(header, "Bearer "))
            if err != nil {
                http.Error(w, `{"error":"invalid token"}`, http.StatusUnauthorized)
                return
            }
            ctx := context.WithValue(r.Context(), claimsKey, claims)
            next.ServeHTTP(w, r.WithContext(ctx))
        })
    }
}

// Stack chains middleware so the first item in the slice is the outermost wrapper
func Stack(middleware ...Middleware) Middleware {
    return func(final http.Handler) http.Handler {
        for i := len(middleware) - 1; i >= 0; i-- {
            final = middleware[i](final)
        }
        return final
    }
}

// Wiring it all together
func (s *Server) routes() {
    base := Stack(
        Recoverer(s.logger),
        RequestID,
        Logger(s.logger),
    )
    authed := Stack(
        Recoverer(s.logger),
        RequestID,
        Logger(s.logger),
        RequireAuth(s.verifier),
    )

    s.mux.Handle("GET /health", base(http.HandlerFunc(s.handleHealth)))
    s.mux.Handle("GET /api/v1/users/{id}", authed(http.HandlerFunc(s.handleGetUser)))
    s.mux.Handle("POST /api/v1/users", authed(http.HandlerFunc(s.handleCreateUser)))
}
```

The `Stack` helper inverts the slice order so the first middleware you name is the first one a request hits. This matches how people naturally read "outer to inner" and avoids the mental gymnastics of building a chain in reverse.

## The Gotchas

**Context key collisions.** If you store values in context using a plain string as the key, any package can clobber yours with the same string. Always use an unexported custom type as the key:

```go
type contextKey string
const requestIDKey contextKey = "request_id"
```

This makes the key package-scoped and prevents collisions even if another package uses the string `"request_id"`.

**Middleware that runs after the response.** The logger in my example defers its log line until after `next.ServeHTTP` returns. This is the correct order — you need the status code. But if you put cleanup logic in the wrong place, you can log before the handler writes, ending up with `status: 200` for every request.

**Calling WriteHeader twice.** If your middleware calls `w.WriteHeader(401)` on auth failure and then the handler also calls `w.WriteHeader(200)` (because your middleware forgot to return), Go will log a "superfluous response.WriteHeader call" warning and the client will see whatever was sent first. Always `return` after writing an error response.

**Order matters.** Recovery should always be the outermost middleware so it catches panics from everything inside, including authentication middleware. Logging should be just inside recovery so it captures the final status code, including 500s from recovered panics. Auth sits further inside because you want it logged.

## Key Takeaway

Middleware is the correct place for cross-cutting concerns. The Go model — a function from handler to handler — is so simple that you do not need a framework to implement it. Write your middleware as standalone functions that close over their dependencies. Compose them with a `Stack` helper. Keep each middleware focused on one concern, and keep that concern out of your handler bodies. When you wire everything together in `routes()`, the structure of your service becomes self-documenting.

---

**Series: Go API and Service Design**

[← Lesson 1: Designing HTTP APIs in Go](/post/go/go-api-http-design) | [Lesson 3: Request Validation →](/post/go/go-api-validation)
