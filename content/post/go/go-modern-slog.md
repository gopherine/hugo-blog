---
title: "Lesson 4: Structured Logging with slog — The stdlib logger Go always needed"
author: Atharva Pandey
series: "Modern Go: 1.21 to 1.26"
lesson: 4
keywords:
  - Go 1.21
  - slog
  - structured logging
  - log/slog
  - golang logging
  - JSON logging
  - zap
  - zerolog
tags:
  - Go tutorial
  - golang
  - modern Go
date: "2024-12-12T00:00:00.000Z"
---

I have switched logging libraries in Go more times than I care to admit. Started with the standard `log` package — fine for scripts, useless in production because you cannot query plain text logs efficiently. Moved to `logrus` because everyone was using it. Switched to `zap` when I needed better performance. Considered `zerolog` when I wanted allocation-free hot paths. Each migration meant updating every file that imported the old library, convincing teammates, and writing bridge adapters for third-party code that used a different logger.

When Go 1.21 shipped `log/slog`, I read the design document carefully. My first reaction was relief. Not because `slog` outperforms every library on every benchmark — it does not. But because it is the *standard library*. That means third-party packages can accept `*slog.Logger` or `slog.Handler` without creating an import cycle or forcing you into their logging dependency. The ecosystem can converge. I have been on that convergence since 1.21 and have not looked back.

## The Problem

The original `log` package does one thing: it writes lines to an `io.Writer`. It has no concept of levels, no key-value structured fields, no JSON output. In production, you need to filter logs by level, attach fields like `request_id`, `user_id`, `service_name`, and query them in a log aggregation system. You cannot do any of that with plain-text unstructured lines.

The ecosystem responded with a proliferation of libraries, each with different APIs:

```go
// logrus
logrus.WithField("user_id", 42).Info("user logged in")

// zap
logger.Info("user logged in", zap.Int("user_id", 42))

// zerolog
log.Info().Int("user_id", 42).Msg("user logged in")
```

All valid, all producing structured JSON, but incompatible. Accepting a logger in a library function meant picking a logging framework and imposing it on consumers. The common workaround was to accept an opaque `interface{}` logger or to define your own logging interface — but now every library defined a different interface and nothing was interoperable.

## How It Works

`log/slog` introduces two key types:

**`slog.Logger`** — the thing you call in your code. Methods: `Debug`, `Info`, `Warn`, `Error`. Each accepts a message string followed by alternating key-value pairs (the "any" style) or typed `slog.Attr` values:

```go
logger.Info("user logged in",
    "user_id", 42,
    "email", "user@example.com",
)

// Or with typed attrs — slightly more efficient, avoids reflection
logger.Info("user logged in",
    slog.Int("user_id", 42),
    slog.String("email", "user@example.com"),
)
```

**`slog.Handler`** — the interface that controls where and how records are written. The standard library ships two:

- `slog.NewTextHandler(w, opts)` — human-readable key=value format
- `slog.NewJSONHandler(w, opts)` — JSON, one object per line

You can implement `slog.Handler` yourself for custom output: writing to a cloud logging service, sampling high-volume debug logs, routing different levels to different sinks.

**Creating a logger:**

```go
logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
    Level: slog.LevelInfo,
}))
```

**Adding persistent fields** — the `With` method returns a new logger with fields pre-attached:

```go
requestLogger := logger.With(
    "request_id", r.Header.Get("X-Request-ID"),
    "method", r.Method,
    "path", r.URL.Path,
)
// All subsequent calls on requestLogger include request_id, method, path
requestLogger.Info("request received")
requestLogger.Error("handler failed", "error", err)
```

**Groups** — `WithGroup` namespaces attributes:

```go
dbLogger := logger.WithGroup("db")
dbLogger.Info("query executed", "query", q, "duration_ms", 12)
// Output: {..., "db": {"query": "...", "duration_ms": 12}}
```

**The default logger.** Package-level functions `slog.Info(...)`, `slog.Error(...)` etc. use a global default logger. You can replace it:

```go
slog.SetDefault(logger)
```

After this, the old `log.Printf` and `log.Println` functions also route through `slog`, which is useful when migrating legacy code.

## In Practice

A typical service setup:

```go
func main() {
    level := slog.LevelInfo
    if os.Getenv("DEBUG") == "true" {
        level = slog.LevelDebug
    }

    logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
        Level: level,
        AddSource: true, // includes file:line in every record
    }))
    slog.SetDefault(logger)

    // Pass logger via context or direct injection
    srv := &Server{log: logger.With("service", "api")}
    srv.Run()
}
```

**Middleware that attaches request context:**

```go
func loggingMiddleware(log *slog.Logger, next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        reqLog := log.With(
            "request_id", uuid.New().String(),
            "method", r.Method,
            "path", r.URL.Path,
            "remote_addr", r.RemoteAddr,
        )
        ctx := context.WithValue(r.Context(), logKey, reqLog)
        next.ServeHTTP(w, r.WithContext(ctx))
    })
}

func LoggerFromContext(ctx context.Context) *slog.Logger {
    if l, ok := ctx.Value(logKey).(*slog.Logger); ok {
        return l
    }
    return slog.Default()
}
```

**Custom handler for sampling.** You can wrap the JSON handler to drop a fraction of debug logs under high load:

```go
type SamplingHandler struct {
    inner  slog.Handler
    rate   int // keep 1 in N debug records
    count  atomic.Int64
}

func (h *SamplingHandler) Handle(ctx context.Context, r slog.Record) error {
    if r.Level == slog.LevelDebug {
        if h.count.Add(1) % int64(h.rate) != 0 {
            return nil // drop
        }
    }
    return h.inner.Handle(ctx, r)
}
```

## The Gotchas

**The "any" key-value style has no compile-time checking.** `logger.Info("msg", "key", value)` pairs the string key with the next argument by position. If you pass an odd number of arguments, the last key gets no value and slog logs a warning about the malformed call. Use `slog.Attr` constructors (`slog.Int`, `slog.String`, etc.) in hot paths or when you want type safety.

**`slog.LevelDebug` is negative.** The level values are `Debug = -4`, `Info = 0`, `Warn = 4`, `Error = 8`. You can define custom levels between these for finer-grained control — but using integer literals instead of the named constants is a footgun.

**`AddSource: true` has a cost.** Computing `runtime.Callers` for every log record adds overhead. Fine for development. In high-throughput production paths, leave it off or restrict it to warn/error.

**`With` copies, it does not mutate.** `logger.With(...)` returns a new logger; the original is unchanged. If you forget to assign the result, you lose the fields:

```go
logger.With("key", "value") // WRONG: result discarded
reqLog := logger.With("key", "value") // correct
```

**Migrating from zap/zerolog.** If you need the raw performance of zerolog's zero-allocation approach in extremely hot paths, slog's default handlers do allocate. You can get close with a custom handler that avoids allocations, or bridge slog to zerolog's handler — both slog and zerolog support the bridge pattern through `slog.Handler`.

## Key Takeaway

`log/slog` is the structured logger the Go standard library always needed. It ships with JSON and text handlers, supports levels, structured fields, groups, and a pluggable handler interface. Its most valuable property is being in the standard library: third-party packages can now depend on `slog.Handler` without forcing a logging framework on you. If you are starting a new service on Go 1.21+, start with `slog`. If you are on an older service using `logrus` or `zap`, the migration path is a custom handler that forwards records — you can do it incrementally.

---

**Previous:** [Lesson 3: Loop Variable Fix](/post/go/go-modern-loop-var/)

**Next:** [Lesson 5: What's New in Go 1.25–1.26](/post/go/go-modern-latest/)
