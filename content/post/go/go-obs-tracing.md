---
title: "Lesson 3: Distributed Tracing with OpenTelemetry — Follow the request across services"
author: Atharva Pandey
series: "Go Observability in Production"
lesson: 3
keywords:
  - Go
  - golang
  - observability
  - distributed tracing
  - OpenTelemetry
  - otel
  - spans
  - Jaeger
  - trace context
tags:
  - Go tutorial
  - golang
  - observability
date: '2024-09-15T00:00:00.000Z'
---

We had an incident where checkout was timing out intermittently. The logs showed the API gateway receiving the request and returning a 504 after 30 seconds. The payment service logged nothing. The inventory service logged nothing. Something was hanging somewhere in the middle, and we had no way to see where.

I spent four hours bisecting the call graph by adding temporary log lines, redeploying, and re-triggering the error. We eventually found a database query in the inventory service that was waiting on a lock — a lock held by a background job nobody had thought to instrument. Logs told me what each service did in isolation. They told me nothing about the shape of a single request as it flowed across all of them.

Distributed tracing is the answer to that question. A trace is a tree of spans, where each span represents a unit of work — an HTTP call, a database query, a cache lookup — with a start time, duration, and key-value attributes. Every span carries the same trace ID, so you can reconstruct the full call graph of a single request after the fact.

## The Problem

The naive approach to correlating requests across services is to pass a request ID in a header and log it everywhere. That gets you correlation — you can filter all logs for a given request — but it doesn't give you timing information, parent-child relationships, or a visual representation of the call graph.

```go
// You can filter logs by request_id, but you can't answer:
// - which service was slowest?
// - which database call blocked the checkout?
// - did the inventory service call Redis before or after Postgres?
slog.Info("calling inventory service", "request_id", reqID)
resp, err := inventoryClient.Check(ctx, itemID)
slog.Info("inventory check complete", "request_id", reqID, "available", resp.Available)
```

You have timestamps on the log lines, so you could subtract them to get the duration — but this breaks down when services run on machines with clock skew, when a service makes parallel calls, or when you're trying to understand nested call graphs across five services.

What you need is a span that records the start and end on the *same* clock, carries the trace context through every downstream call, and reports its duration to a central backend.

## The Idiomatic Way

OpenTelemetry is the industry standard for distributed tracing (and now metrics and logs). The Go SDK is mature and the instrumentation model is straightforward.

**Step 1: Set up the tracer provider at startup**

```go
package telemetry

import (
    "context"

    "go.opentelemetry.io/otel"
    "go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
    "go.opentelemetry.io/otel/sdk/resource"
    sdktrace "go.opentelemetry.io/otel/sdk/trace"
    semconv "go.opentelemetry.io/otel/semconv/v1.21.0"
)

func InitTracer(ctx context.Context, serviceName string) (func(context.Context) error, error) {
    exporter, err := otlptracegrpc.New(ctx,
        otlptracegrpc.WithEndpoint("otel-collector:4317"),
        otlptracegrpc.WithInsecure(),
    )
    if err != nil {
        return nil, err
    }

    res := resource.NewWithAttributes(
        semconv.SchemaURL,
        semconv.ServiceName(serviceName),
        semconv.ServiceVersion("1.0.0"),
    )

    tp := sdktrace.NewTracerProvider(
        sdktrace.WithBatcher(exporter),
        sdktrace.WithResource(res),
        sdktrace.WithSampler(sdktrace.ParentBased(sdktrace.TraceIDRatioBased(0.1))),
    )

    otel.SetTracerProvider(tp)
    return tp.Shutdown, nil
}
```

Call `InitTracer` in `main`, and defer the shutdown function. The sampler here samples 10% of new traces while always sampling if the parent span is already sampled — this is the standard production strategy, letting you adjust sampling at the edge without changing every service.

**Step 2: Create spans in your handlers**

```go
var tracer = otel.Tracer("payment-api")

func handleCheckout(w http.ResponseWriter, r *http.Request) {
    ctx, span := tracer.Start(r.Context(), "handleCheckout")
    defer span.End()

    // add attributes to the span for filtering in Jaeger/Tempo
    span.SetAttributes(
        attribute.String("user.id", userIDFromContext(r.Context())),
        attribute.String("order.id", r.URL.Query().Get("order_id")),
    )

    inventory, err := checkInventory(ctx, orderID)
    if err != nil {
        span.RecordError(err)
        span.SetStatus(codes.Error, "inventory check failed")
        http.Error(w, "inventory unavailable", http.StatusServiceUnavailable)
        return
    }

    if err := processPayment(ctx, order, inventory); err != nil {
        span.RecordError(err)
        span.SetStatus(codes.Error, "payment processing failed")
        http.Error(w, "payment failed", http.StatusInternalServerError)
        return
    }

    span.SetStatus(codes.Ok, "")
}
```

The key: pass `ctx` (which now carries the active span) into every downstream function. Child functions that create their own spans will automatically become children of this span in the trace.

**Step 3: Instrument downstream calls**

```go
func checkInventory(ctx context.Context, orderID string) (*Inventory, error) {
    ctx, span := tracer.Start(ctx, "checkInventory")
    defer span.End()

    span.SetAttributes(attribute.String("order.id", orderID))

    // wrap your HTTP client with otel transport to propagate trace context
    inv, err := inventoryClient.Check(ctx, orderID)
    if err != nil {
        span.RecordError(err)
        return nil, err
    }

    span.SetAttributes(attribute.Int("inventory.items_available", inv.Count))
    return inv, nil
}
```

For HTTP clients, use the `otelhttp` transport wrapper:

```go
import "go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"

client := &http.Client{
    Transport: otelhttp.NewTransport(http.DefaultTransport),
}
```

This injects the `traceparent` header automatically, so the downstream service can pick up the trace context and add its spans to the same tree.

## In The Wild

The full picture in production requires instrumentation at every layer. For a service with HTTP, database, and cache calls, the span tree for a single checkout request looks like:

```
handleCheckout (120ms)
  ├── checkInventory (30ms)
  │     ├── redis.Get inventory:item_42 (2ms)
  │     └── postgres.Query SELECT * FROM inventory (27ms)  ← slow
  └── processPayment (85ms)
        ├── stripe.ChargeCreate (80ms)
        └── postgres.Exec INSERT INTO payments (3ms)
```

You can see at a glance that the Postgres inventory query is the bottleneck. Without tracing, this is invisible — the logs just say "checkInventory took 30ms" with no breakdown.

For database instrumentation, use `otelsql` or the relevant driver wrapper:

```go
import "github.com/XSAM/otelsql"

db, err := otelsql.Open("postgres", dsn,
    otelsql.WithAttributes(semconv.DBSystemPostgreSQL),
    otelsql.WithSpanOptions(otelsql.SpanOptions{
        OmitConnResetSession: true,
    }),
)
```

Every query now automatically creates a child span with the SQL text and row count as attributes.

## The Gotchas

**Context propagation breaks if you drop the context.** The entire tracing model depends on passing `ctx` through every call. If any function in the chain creates a `context.Background()` or `context.TODO()` instead of threading the incoming context, the child spans created downstream will be orphaned — they'll appear as root spans in your tracing backend, invisible in the parent trace. Audit your codebase for `context.Background()` calls inside request handlers.

**Sampling must be consistent across services.** If service A samples at 10% and service B (downstream) samples at 1%, you'll lose 90% of the traces that service A would have kept. The standard pattern is head-based sampling at the edge (your API gateway or first service), with `ParentBased` sampler everywhere else so that downstream services follow the parent's sampling decision.

**Span attributes have size limits.** OTLP collectors and backends typically limit attribute values to 1024 characters. Logging a full SQL query or a large JSON payload as a span attribute will get truncated silently. For large values, log them with the trace ID attached and retrieve them from your log system.

**Don't confuse spans with logs.** `span.AddEvent("cache miss")` adds a point-in-time event to the span timeline — useful for marking significant moments within a long operation. It is not a replacement for structured logs. Use both: the log carries the full detail, the span event marks the moment in the call graph timeline.

**Zero-value tracer panics on nil.** If `InitTracer` is never called (common in unit tests), `otel.Tracer("name")` returns a no-op tracer that silently does nothing. This is actually the correct behavior — no instrumentation needed in unit tests. But if you see no traces in production, verify the tracer provider was initialized before any request was handled.

## Key Takeaway

Distributed tracing answers the question logs cannot: which service was slow, which call failed, and how did the latency distribute across the entire request tree. OpenTelemetry is the standard SDK — set up the tracer provider once at startup, pass `ctx` through every function, and wrap your HTTP clients and database drivers with the OpenTelemetry transport. The sampling strategy is: head-based at the edge, `ParentBased` everywhere else. The most common mistake is dropping the context somewhere in the middle of the call graph, which orphans all the spans downstream. Fix that first, and the rest falls into place.

---

← [Previous: Metrics That Matter](/post/go/go-obs-metrics) | [Course Index](/post/go/) | [Next: Correlation IDs →](/post/go/go-obs-correlation-ids)
