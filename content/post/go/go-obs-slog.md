---
title: "Lesson 1: Structured Logging with slog — Printf is not observability"
author: Atharva Pandey
series: "Go Observability in Production"
lesson: 1
keywords:
  - Go
  - golang
  - observability
  - slog
  - structured logging
  - log/slog
  - JSON logging
  - log levels
tags:
  - Go tutorial
  - golang
  - observability
date: '2024-06-25T00:00:00.000Z'
---

I spent two years writing Go services that logged like this:

```go
log.Printf("processing order %s for user %d, amount %.2f", orderID, userID, amount)
```

It worked fine — until the day I needed to find every failed payment over $500 from a specific user across three weeks of logs in a production incident at 2 AM. I was grepping through gigabytes of text, trying to parse freeform strings with jq, getting nowhere. The logs existed. The information was technically there. But I couldn't *query* it in any meaningful way.

That is the core failure of `fmt.Sprintf`-style logging: it turns structured data into unstructured text at the moment of writing, and you can never get the structure back. Printf is not observability. It's a fancy way of printing to stdout.

## The Problem

The pattern I see most often in Go codebases:

```go
// freeform strings — impossible to query reliably
log.Printf("user %d logged in from %s at %s", userID, ipAddr, time.Now().Format(time.RFC3339))
log.Printf("request failed: %v", err)
log.Printf("cache hit for key %s in %dms", key, elapsed.Milliseconds())
```

Three problems compound here. First, the field names are buried inside the format string — a log aggregator has to parse English prose to extract them, and that parsing breaks the moment someone changes the string slightly. Second, there is no consistent severity level attached to the message — everything goes to stdout at the same loudness, which makes alerting on errors impossible without fragile regex. Third, there is no trace or request ID correlating these lines — three concurrent requests interleave their log lines with no way to group them.

When you move to a log aggregation system like Loki, Elasticsearch, or CloudWatch Logs Insights, the query language expects fields. `level = "error" AND user_id = 42` is a valid, fast query. `message CONTAINS "user 42"` is a slow, fragile full-text scan that breaks if the string format ever changes.

## The Idiomatic Way

Go 1.21 shipped `log/slog` in the standard library, ending years of fragmentation across `logrus`, `zap`, and `zerolog`. Unless you have extreme throughput requirements where every nanosecond matters, `slog` is now the right default.

The fundamental shift is explicit key-value fields instead of format strings:

```go
import "log/slog"

// set up a JSON handler once at startup
logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
    Level: slog.LevelInfo,
}))
slog.SetDefault(logger)

// log with structured fields
slog.Info("user logged in",
    "user_id", userID,
    "ip_addr", ipAddr,
    "method", "password",
)

slog.Error("payment failed",
    "user_id", userID,
    "order_id", orderID,
    "amount_cents", amountCents,
    "err", err,
)
```

The JSON output looks like this:

```json
{"time":"2024-06-25T14:32:01Z","level":"INFO","msg":"user logged in","user_id":42,"ip_addr":"203.0.113.5","method":"password"}
{"time":"2024-06-25T14:32:01Z","level":"ERROR","msg":"payment failed","user_id":42,"order_id":"ord_8f2c","amount_cents":7500,"err":"card declined: insufficient funds"}
```

Every field is a first-class key. Your log aggregator can index `user_id`, `order_id`, and `level` independently. The query to find all failed payments over $75 from user 42 is now three indexed lookups.

**Adding context with `With`**

The pattern I use everywhere is building a child logger with fields that are fixed for a request's lifetime:

```go
func handleOrder(w http.ResponseWriter, r *http.Request) {
    log := slog.With(
        "request_id", r.Header.Get("X-Request-ID"),
        "user_id",    userIDFromContext(r.Context()),
        "handler",    "handleOrder",
    )

    order, err := db.GetOrder(r.Context(), orderID)
    if err != nil {
        log.Error("failed to fetch order", "order_id", orderID, "err", err)
        http.Error(w, "not found", http.StatusNotFound)
        return
    }

    log.Info("order fetched", "order_id", order.ID, "status", order.Status)
    // every subsequent log call from this logger automatically includes request_id and user_id
}
```

`slog.With` returns a new `*slog.Logger` with those fields baked in. Every log line from this logger carries `request_id` and `user_id` without you having to repeat them. This is the single change that makes post-incident log analysis go from minutes of grep to a two-second filter query.

## In The Wild

In a payment processing service I work on, the observability contract we established is this: every log line in a request handler must carry `request_id`, `user_id`, and `service`. We encode this with a middleware:

```go
func loggingMiddleware(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        reqID := r.Header.Get("X-Request-ID")
        if reqID == "" {
            reqID = uuid.New().String()
        }

        log := slog.With(
            "request_id", reqID,
            "method",     r.Method,
            "path",       r.URL.Path,
            "service",    "payment-api",
        )

        // store the logger in context so handlers can retrieve it
        ctx := context.WithValue(r.Context(), loggerKey{}, log)

        start := time.Now()
        rw := &responseWriter{ResponseWriter: w}
        next.ServeHTTP(rw, r.WithContext(ctx))

        log.Info("request completed",
            "status",      rw.status,
            "duration_ms", time.Since(start).Milliseconds(),
        )
    })
}

func loggerFromContext(ctx context.Context) *slog.Logger {
    if l, ok := ctx.Value(loggerKey{}).(*slog.Logger); ok {
        return l
    }
    return slog.Default()
}
```

Every handler calls `loggerFromContext(r.Context())` and gets a logger that already has `request_id` attached. No discipline required from individual handlers — the structure is enforced at the middleware layer.

For sampling high-volume debug logs without drowning your aggregation pipeline, use a custom handler:

```go
type samplingHandler struct {
    inner      slog.Handler
    sampleRate int // log 1 in N debug messages
    counter    atomic.Int64
}

func (h *samplingHandler) Handle(ctx context.Context, r slog.Record) error {
    if r.Level == slog.LevelDebug {
        n := h.counter.Add(1)
        if n%int64(h.sampleRate) != 0 {
            return nil
        }
    }
    return h.inner.Handle(ctx, r)
}
```

This pattern keeps debug volume manageable in production without completely losing debug visibility.

## The Gotchas

**Slow key-value pairs are still evaluated eagerly.** Even if the log level is below your handler's minimum, the arguments to `slog.Debug(...)` are evaluated before the call. If you're logging an expensive value:

```go
// the String() call happens regardless of whether debug is enabled
slog.Debug("cache contents", "data", cache.ExpensiveDump())
```

Use `slog.LogAttrs` with `slog.AnyValue` lazily, or guard with `slog.Default().Enabled(ctx, slog.LevelDebug)`.

**Mismatched key-value pairs produce broken output.** `slog` pairs keys and values positionally. An odd number of arguments results in a `!BADKEY` entry in the JSON. Enable the `go vet` check (`go vet ./...` catches this in recent toolchains) and write a linter rule that forbids bare string-only log calls.

**`slog.Group` is underused.** For nested structure — HTTP request details, database query metadata — use groups:

```go
slog.Info("db query",
    slog.Group("query",
        "table",      "orders",
        "rows",       rowCount,
        "duration_ms", elapsed.Milliseconds(),
    ),
)
// produces: {"msg":"db query","query":{"table":"orders","rows":150,"duration_ms":3}}
```

Grouped fields make dashboards and aggregation queries much cleaner.

**Avoid global state, but do set the default.** Call `slog.SetDefault(logger)` once at startup with your configured handler. Then any library code that uses `slog.Info(...)` without a logger reference automatically gets structured JSON output instead of the default text format.

## Key Takeaway

Structured logging is not about the library you pick — it's about treating every log line as a record with typed fields, not a sentence for a human to read. `log/slog` ships in the standard library, outputs JSON, and gives you `With` for baking context into child loggers. The discipline is: every request handler gets a logger with `request_id` and `user_id` attached before the first log call. That one habit compresses a two-hour grep session into a two-second query. Printf is for scripts and toy programs. Production services deserve fields.

---

[Course Index](/post/go/) | [Next: Metrics That Matter →](/post/go/go-obs-metrics)
