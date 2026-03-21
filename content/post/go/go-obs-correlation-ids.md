---
title: "Lesson 4: Correlation IDs — Connect the logs to the trace to the user"
author: Atharva Pandey
series: "Go Observability in Production"
lesson: 4
keywords:
  - Go
  - golang
  - observability
  - correlation IDs
  - request ID
  - trace ID
  - context propagation
  - middleware
tags:
  - Go tutorial
  - golang
  - observability
date: '2024-10-18T00:00:00.000Z'
---

A support ticket lands: "User 8842 says their order failed at 2:47 PM yesterday." You open your log aggregator. You search for `user_id = 8842`. You get 4,000 log lines — the user made 80 requests that afternoon. You filter to the 2:43–2:51 PM window. You get 300 lines. They interleave with log lines from 12 concurrent requests from other users because your log output is not partitioned by request. The error message, when you find it, says `internal server error`. No stack trace, no underlying cause, no request that produced it.

You switch to your tracing backend. You search for `user.id = 8842`. You get five traces. One of them is in the right time window. You click it and see a 500ms span with a child span marked as error: a database connection from the pool was stale and the retry logic had a bug. The fix is a one-liner. Total incident time: 23 minutes.

The difference between those two experiences is correlation: every log line, every metric label, and every trace span for a given request carries the same identifier, and that identifier is propagated end-to-end without any individual developer having to think about it.

## The Problem

The typical approach in codebases that haven't thought about this yet:

```go
// each team adds their own request ID — inconsistently
func handlerA(w http.ResponseWriter, r *http.Request) {
    id := uuid.New().String()
    log.Printf("starting handlerA, id=%s", id)
    // id is local — never passed downstream, never in headers
}

func handlerB(w http.ResponseWriter, r *http.Request) {
    id := r.Header.Get("X-Request-ID")
    if id == "" {
        id = "unknown"  // silently lost
    }
    log.Printf("handlerB request id=%s", id)
}
```

Three problems: IDs are generated inconsistently, they're not passed through the context to sub-functions, and they're not propagated to downstream services. Every service island has its own log lines with their own IDs that don't connect to anything else.

The second failure is conflating the request ID with the trace ID. They're related but different. The request ID is a stable identifier you control, often set by your API gateway or load balancer, that you use in support tickets and user communication. The trace ID is the OpenTelemetry trace identifier that your tracing backend understands. In production, you want both — and you want them cross-linked.

## The Idiomatic Way

The solution is a single middleware that runs before everything else, establishes both IDs, stores them in the context, and attaches them to the logger and the active trace span.

```go
package middleware

import (
    "context"
    "net/http"

    "github.com/google/uuid"
    "go.opentelemetry.io/otel/trace"
    "log/slog"
)

type contextKey int

const (
    requestIDKey contextKey = iota
    loggerKey
)

func CorrelationMiddleware(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        // prefer the ID from the upstream gateway, generate one if absent
        reqID := r.Header.Get("X-Request-ID")
        if reqID == "" {
            reqID = uuid.New().String()
        }

        // propagate the request ID to downstream services
        r.Header.Set("X-Request-ID", reqID)
        w.Header().Set("X-Request-ID", reqID)

        // extract the active trace ID from the span in context
        // (requires tracing middleware to have run first)
        traceID := ""
        if span := trace.SpanFromContext(r.Context()); span.SpanContext().IsValid() {
            traceID = span.SpanContext().TraceID().String()
        }

        // build a logger with both IDs baked in
        log := slog.With(
            "request_id", reqID,
            "trace_id",   traceID,
            "user_id",    userIDFromContext(r.Context()),
            "method",     r.Method,
            "path",       r.URL.Path,
        )

        // store both in context for handler use
        ctx := r.Context()
        ctx = context.WithValue(ctx, requestIDKey, reqID)
        ctx = context.WithValue(ctx, loggerKey, log)

        next.ServeHTTP(w, r.WithContext(ctx))
    })
}

func RequestIDFromContext(ctx context.Context) string {
    if id, ok := ctx.Value(requestIDKey).(string); ok {
        return id
    }
    return ""
}

func LoggerFromContext(ctx context.Context) *slog.Logger {
    if l, ok := ctx.Value(loggerKey).(*slog.Logger); ok {
        return l
    }
    return slog.Default()
}
```

The middleware order matters. Your OpenTelemetry HTTP middleware must run first (it creates the trace span and puts it in the context), then this correlation middleware runs to pull the trace ID out and put it into the logger. After that, every `LoggerFromContext(ctx)` call anywhere in the handler tree returns a logger that already has `request_id`, `trace_id`, and `user_id` attached.

**Propagating IDs to downstream HTTP calls**

When your service calls another service, it must forward the request ID:

```go
func callInventoryService(ctx context.Context, itemID string) (*InventoryResponse, error) {
    req, err := http.NewRequestWithContext(ctx, http.MethodGet,
        "http://inventory-svc/items/"+itemID, nil)
    if err != nil {
        return nil, err
    }

    // forward the request ID so the downstream service's logs correlate
    if reqID := RequestIDFromContext(ctx); reqID != "" {
        req.Header.Set("X-Request-ID", reqID)
    }

    // the otelhttp transport handles W3C traceparent propagation automatically
    resp, err := otelHTTPClient.Do(req)
    if err != nil {
        return nil, err
    }
    defer resp.Body.Close()
    // ...
}
```

With `otelhttp.NewTransport`, the `traceparent` header is injected automatically, so the trace context propagates. You need to manually forward `X-Request-ID` for your request ID. A wrapper client that does this once is better than relying on every call site to remember:

```go
type correlatingClient struct {
    inner *http.Client
}

func (c *correlatingClient) Do(ctx context.Context, req *http.Request) (*http.Response, error) {
    if reqID := RequestIDFromContext(ctx); reqID != "" {
        req.Header.Set("X-Request-ID", reqID)
    }
    return c.inner.Do(req)
}
```

## In The Wild

The cross-linking between logs and traces is where this pays off most. In your log aggregator, every log line for a request has `trace_id`. In Jaeger or Grafana Tempo, every span has the same `trace_id`. You can jump from a log line to the full trace with one click — or from the trace to the full log stream with a filtered query.

To make this link explicit in your logging output:

```go
// in your structured log output
{
  "time": "2024-10-18T14:32:01Z",
  "level": "ERROR",
  "msg": "payment failed",
  "request_id": "req_7f3a2b1c",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "user_id": "8842",
  "order_id": "ord_9d2e",
  "err": "connection refused"
}
```

Grafana has a derived fields feature that turns `trace_id` in a log line into a clickable link to Tempo. Set that up once and incident response becomes: find the error log, click the trace ID, see the full call graph.

For gRPC services, the correlation pattern is the same but uses metadata instead of headers:

```go
import "google.golang.org/grpc/metadata"

func correlatingUnaryInterceptor(ctx context.Context, req interface{},
    info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (interface{}, error) {

    md, _ := metadata.FromIncomingContext(ctx)
    reqIDs := md.Get("x-request-id")
    reqID := ""
    if len(reqIDs) > 0 {
        reqID = reqIDs[0]
    } else {
        reqID = uuid.New().String()
    }

    log := slog.With("request_id", reqID, "method", info.FullMethod)
    ctx = context.WithValue(ctx, loggerKey, log)
    ctx = context.WithValue(ctx, requestIDKey, reqID)

    return handler(ctx, req)
}
```

## The Gotchas

**Never store the context in a struct field.** Context is a per-request value. If you do `s.ctx = ctx` in your handler and then access `s.ctx` in a method called from a goroutine, you're sharing a request-scoped context across request boundaries. Pass the context as the first argument to every function that needs it.

**User ID must come from authenticated context, not request parameters.** Your correlation middleware should pull `user_id` from the auth token in the context (set by your authentication middleware, which runs first), not from a query parameter or header. A user can forge `X-User-ID: admin` in a header. They cannot forge the value set by your auth middleware after token validation.

**`X-Request-ID` from untrusted sources needs validation.** If your API gateway sets `X-Request-ID` and your service trusts it, an attacker can inject an arbitrary string. Validate the format — a UUID regex check — or generate your own ID and include the upstream one as `X-Upstream-Request-ID` for correlation without trust.

**Watch for ID loss at async boundaries.** When a handler dispatches work to a goroutine or a message queue, the context does not automatically follow. For goroutines, pass a detached context with the relevant values but without cancellation tied to the HTTP request:

```go
// pass a context that carries the IDs but doesn't cancel when the HTTP request ends
detachedCtx := context.WithValue(context.Background(), requestIDKey, RequestIDFromContext(ctx))
detachedCtx = context.WithValue(detachedCtx, loggerKey, LoggerFromContext(ctx))
go processAsync(detachedCtx, payload)
```

For message queues, serialize the request ID and trace ID into the message payload and reconstruct the context in the consumer.

## Key Takeaway

Correlation IDs are the glue between your three observability signals: logs, metrics, and traces. The implementation is a single middleware that runs before your handlers: pull `X-Request-ID` from the incoming request (or generate one), extract the OpenTelemetry trace ID from the active span, build a logger with both baked in, and store everything in the context. Every downstream function gets a logger with both IDs for free — they never have to think about it. The payoff is direct: during an incident, you go from "a user reported an error" to "here is the exact trace and the full log stream for that request" in under a minute.

---

← [Previous: Distributed Tracing with OpenTelemetry](/post/go/go-obs-tracing) | [Course Index](/post/go/) | [Next: Profiling in Production →](/post/go/go-obs-profiling)
