---
title: "Lesson 7: Panic, Recover, and When They're Actually Justified — Panic is not error handling"
author: Atharva Pandey
series: "Go Error Design at Scale"
lesson: 7
keywords:
  - Go
  - golang
  - error handling
  - panic
  - recover
  - panic recover pattern
  - middleware
tags:
  - Go tutorial
  - golang
date: '2025-05-13T00:00:00.000Z'
---

I've seen `panic(err)` used as error handling more times than I'd like to admit — including in codebases I helped build. The reasoning always sounds logical at the time: "this error should never happen, and if it does, the program state is corrupt anyway, so why not just panic?" The problem is that "should never happen" is a statement about your expectations, not about reality. And "program state is corrupt" is almost never true — one request failed, the other thousand are still fine.

Panic is a nuclear option. It unwinds the entire goroutine's stack, runs deferred functions, and either terminates the program or gets caught by a recover. In a web server, a panic in a handler goroutine that's not recovered kills the entire process. Knowing when panic is actually the right call — and when it isn't — is what this lesson is about.

## The Problem

The most common misuse I see is using panic as a shorthand for error return when error returns feel tedious.

```go
// WRONG — panic as lazy error handling
func mustParseConfig(path string) *Config {
    data, err := os.ReadFile(path)
    if err != nil {
        panic(err) // kills the whole server if config is missing
    }
    var cfg Config
    if err := json.Unmarshal(data, &cfg); err != nil {
        panic(err) // same — any malformed config = crash
    }
    return &cfg
}

// Somewhere in a hot path:
func processRequest(r *http.Request) {
    userID := r.Header.Get("X-User-ID")
    if userID == "" {
        panic("missing user id") // kills the server for one bad request
    }
    // ...
}
```

Both of these should return errors. The missing config case is arguably startup-time only, so panicking there is acceptable — but only at startup. The missing header case is a caller error — it should return a 400. Panicking kills every other request being processed concurrently.

The second misuse is `panic(err)` inside goroutines that weren't launched with a recover wrapper — because the panic won't be caught by any recover in the parent goroutine.

```go
// WRONG — panic in goroutine is uncatchable by the parent
func (s *Server) startWorker() {
    go func() {
        for job := range s.jobs {
            result, err := processJob(job)
            if err != nil {
                panic(err) // THIS KILLS THE ENTIRE PROCESS
                // no recover in this goroutine catches it
            }
            s.results <- result
        }
    }()
}
```

This pattern is especially treacherous because it works fine in testing (no bad jobs) and explodes in production (first bad job crashes everything).

## The Idiomatic Way

The legitimate uses of panic fall into two categories: **programming errors** detected at startup or initialization, and **middleware crash protection** via recover.

**Panic for programming errors at initialization time:**

```go
// RIGHT — panic for genuinely unrecoverable setup failures
func NewServer(cfg *Config) *Server {
    if cfg == nil {
        panic("NewServer: cfg must not be nil") // programming error, caught in tests
    }
    if cfg.Port == 0 {
        panic("NewServer: port must be non-zero")
    }

    db, err := sql.Open("postgres", cfg.DatabaseURL)
    if err != nil {
        // sql.Open rarely fails — if it does, something is very wrong with setup
        panic(fmt.Sprintf("NewServer: open db: %v", err))
    }

    return &Server{cfg: cfg, db: db}
}
```

These panics fire at startup during development or in tests. They protect against incorrect wiring — the kind of error that should be caught before deployment.

**Recover in middleware for crash protection:**

```go
// RIGHT — recover in HTTP middleware to catch unexpected panics
func RecoveryMiddleware(logger *slog.Logger) func(http.Handler) http.Handler {
    return func(next http.Handler) http.Handler {
        return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
            defer func() {
                if rec := recover(); rec != nil {
                    // capture stack trace
                    buf := make([]byte, 4096)
                    n := runtime.Stack(buf, false)
                    stack := string(buf[:n])

                    logger.Error("handler panic",
                        "panic", fmt.Sprintf("%v", rec),
                        "stack", stack,
                        "path", r.URL.Path,
                        "method", r.Method,
                    )

                    // respond with 500 instead of crashing the server
                    w.WriteHeader(http.StatusInternalServerError)
                    json.NewEncoder(w).Encode(map[string]string{
                        "error": "internal server error",
                    })
                }
            }()
            next.ServeHTTP(w, r)
        })
    }
}
```

This is recover's proper job: not to hide bugs, but to keep the server running while you fix them. The panic is logged with a full stack trace. The response is a clean 500. Other requests are unaffected. You still get paged; you just don't have a full outage.

## In The Wild

The standard library uses panic+recover internally in a few places — `encoding/json` is the most well-known example. The pattern there is: use panic to propagate errors through deeply recursive code where returning errors at every level would require threading error returns through many levels of the call stack, then catch the panic at the top of the public API and convert it back to an error return.

```go
// RIGHT — panic/recover as internal control flow (not as public API)
// This is the pattern encoding/json uses internally

type jsonEncoder struct {
    buf []byte
}

// Internal type for panic-based error propagation
type encodeError struct{ err error }

func (enc *jsonEncoder) encodeValue(v reflect.Value) {
    switch v.Kind() {
    case reflect.String:
        enc.buf = append(enc.buf, '"')
        enc.buf = appendEscaped(enc.buf, v.String())
        enc.buf = append(enc.buf, '"')
    case reflect.Struct:
        enc.encodeStruct(v)
    default:
        // instead of threading error returns through recursive calls,
        // panic with a typed value that we catch at the top
        panic(encodeError{fmt.Errorf("unsupported type: %v", v.Type())})
    }
}

// Public API — catches the internal panic and returns it as an error
func (enc *jsonEncoder) Encode(v any) ([]byte, error) {
    defer func() {
        if r := recover(); r != nil {
            if ee, ok := r.(encodeError); ok {
                // convert internal panic to returned error — clean public API
                _ = ee // in real code, you'd capture and return it
            } else {
                panic(r) // re-panic for anything unexpected
            }
        }
    }()
    enc.encodeValue(reflect.ValueOf(v))
    return enc.buf, nil
}
```

The key detail: the panic type is a private `encodeError` struct. The recover checks for that specific type and re-panics for anything else. This is important — a bare `recover()` that swallows all panics is dangerous. You want to only catch panics you intended to throw.

## The Gotchas

**Never use a bare `recover()` that returns nil for unexpected panics.** If something you didn't anticipate panics (a nil pointer dereference, an index out of bounds), you want to know about it. Catch only what you threw.

```go
// WRONG — swallows all panics including bugs
defer func() {
    if r := recover(); r != nil {
        log.Printf("recovered: %v", r) // bug silently swallowed
    }
}()

// RIGHT — only recover from expected panic types
defer func() {
    if r := recover(); r != nil {
        if ee, ok := r.(encodeError); ok {
            err = ee.err // handle expected case
            return
        }
        panic(r) // re-panic for unexpected cases — let crash protection handle it
    }
}()
```

**Goroutines need their own recover.** Each goroutine has its own panic/recover stack. A recover in the spawning goroutine does nothing for a panic in a goroutine it launched.

```go
// RIGHT — recovery wrapper for background goroutines
func safeGo(logger *slog.Logger, fn func()) {
    go func() {
        defer func() {
            if r := recover(); r != nil {
                buf := make([]byte, 4096)
                n := runtime.Stack(buf, false)
                logger.Error("goroutine panic",
                    "panic", fmt.Sprintf("%v", r),
                    "stack", string(buf[:n]),
                )
            }
        }()
        fn()
    }()
}

// Usage:
safeGo(logger, func() {
    processLongRunningJob(ctx, job)
})
```

**`panic(nil)` is a trap.** A nil panic value makes `recover()` return nil — same as if there was no panic at all. You can't distinguish "no panic" from "panicked with nil" without using `recover()` inside `defer` and checking with a boolean flag. Avoid panicking with nil values entirely.

## Key Takeaway

Panic is not error handling — it's an emergency stop. Use it for true programming errors caught at startup, where continuation would produce undefined behavior. Use recover in middleware and goroutine wrappers to keep the process alive and log crashes with full stack traces. Never use panic in request handlers or hot paths as a substitute for returning errors. When you use the panic/recover pattern internally (like the standard library does), always catch only the specific panic type you threw and re-panic for everything else. One bad request should never kill your server.

---

Previous: [Lesson 6: Error Boundaries Across Layers](go-errors-boundaries.md) | Next: [Lesson 8: Production Error Architecture — Designing the error system for a real service](go-errors-production-architecture.md)
