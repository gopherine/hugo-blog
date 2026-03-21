---
title: 'Lesson 4: Distributed Tracing — Follow the request across 5 services'
author: Atharva Pandey
series: "Go Microservices Patterns"
lesson: 4
keywords:
  - Go
  - golang
  - microservices
  - distributed tracing
  - OpenTelemetry
  - Jaeger
  - observability
tags:
  - Go tutorial
  - golang
  - microservices
date: '2025-01-28T00:00:00.000Z'
---

Debugging a microservices system with only logs is like debugging a multi-threaded program with only print statements — possible, but painful in ways that are entirely avoidable. The first time I had to trace a slow request through six services using log grep, I understood why distributed tracing exists. An hour of log correlation that should have been a 10-second click on a flame chart. Distributed tracing gives you that flame chart.

## The Problem

Logs from individual services tell you what happened in that service. They don't tell you how the services relate to each other for a single request.

```
// Logs from a slow checkout request — three services, no correlation
// order-service: [INFO] 2025-01-15 14:23:01 processing checkout for user 482
// inventory-service: [INFO] 2025-01-15 14:23:01 reserve request received
// inventory-service: [INFO] 2025-01-15 14:23:03 reservation complete (2.1s)
// payment-service: [INFO] 2025-01-15 14:23:03 charge initiated
// payment-service: [INFO] 2025-01-15 14:23:04 charge complete (0.8s)
// order-service: [INFO] 2025-01-15 14:23:04 checkout complete (3.2s)
```

This looks traceable — but in production you have thousands of these log lines interleaved, and "user 482" might have multiple concurrent requests. Without a correlation ID threading through all services, you're guessing which inventory log lines belong to which checkout.

## The Idiomatic Way

OpenTelemetry (OTel) is the standard for distributed tracing in Go. It provides a vendor-neutral SDK that sends traces to any compatible backend (Jaeger, Tempo, Honeycomb, Datadog).

**Instrument the application entrypoint:**

```go
// main.go — set up the OTel tracer provider once
package main

import (
    "go.opentelemetry.io/otel"
    "go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
    "go.opentelemetry.io/otel/sdk/resource"
    sdktrace "go.opentelemetry.io/otel/sdk/trace"
    semconv "go.opentelemetry.io/otel/semconv/v1.21.0"
)

func initTracer(ctx context.Context, serviceName string) (func(), error) {
    exporter, err := otlptracehttp.New(ctx,
        otlptracehttp.WithEndpoint(os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT")),
        otlptracehttp.WithInsecure(),
    )
    if err != nil {
        return nil, fmt.Errorf("create exporter: %w", err)
    }

    tp := sdktrace.NewTracerProvider(
        sdktrace.WithBatcher(exporter),
        sdktrace.WithResource(resource.NewWithAttributes(
            semconv.SchemaURL,
            semconv.ServiceName(serviceName),
        )),
        sdktrace.WithSampler(sdktrace.AlwaysSample()), // 100% in dev; tune in prod
    )

    otel.SetTracerProvider(tp)

    return func() {
        tp.Shutdown(context.Background())
    }, nil
}
```

**Instrument HTTP handlers with automatic middleware:**

```go
// Use the OTel HTTP middleware to auto-instrument all handlers
import "go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"

func main() {
    shutdown, _ := initTracer(ctx, "order-service")
    defer shutdown()

    mux := http.NewServeMux()
    mux.HandleFunc("/checkout", handleCheckout)

    // Wrapping the mux creates a root span for every request
    handler := otelhttp.NewHandler(mux, "order-service")
    http.ListenAndServe(":8080", handler)
}
```

**Add custom spans for operations within a service:**

```go
// order/service.go
var tracer = otel.Tracer("order-service")

func (s *Service) Submit(ctx context.Context, order Order) (Order, error) {
    ctx, span := tracer.Start(ctx, "order.Submit")
    defer span.End()

    // Child span for inventory reservation
    ctx, invSpan := tracer.Start(ctx, "inventory.Reserve")
    reservation, err := s.inventory.Reserve(ctx, order.Items)
    invSpan.End()
    if err != nil {
        span.RecordError(err)
        span.SetStatus(codes.Error, "inventory reservation failed")
        return Order{}, fmt.Errorf("reserve: %w", err)
    }

    // Span attributes provide searchable context in Jaeger/Tempo
    span.SetAttributes(
        attribute.Int64("order.user_id", order.UserID),
        attribute.Int("order.item_count", len(order.Items)),
        attribute.String("reservation.id", reservation.ID),
    )

    saved, err := s.store.Insert(ctx, order, reservation.ID)
    if err != nil {
        span.RecordError(err)
        return Order{}, err
    }

    return saved, nil
}
```

The trace context propagates automatically through HTTP headers when you use OTel's HTTP client instrumentation — no manual header passing required.

## In The Wild

An e-commerce platform I worked on was seeing intermittent checkout timeouts. The timeouts were in the 3–4 second range, well above the SLA. Logs showed the requests completing, but slowly. Without tracing, we couldn't identify which service was responsible for the delay.

After adding OTel instrumentation across five services in two days, we opened Jaeger and filtered for traces over 3 seconds. The flame chart was immediately clear: 2.8 seconds were being spent in the inventory service, specifically in a database query that was missing an index. The query had been fast with a small dataset, but had degraded as the inventory table grew past 500k rows.

Without tracing, that investigation might have taken days. With tracing, it took 20 minutes from "we have a problem" to "here's the exact query, here's the missing index."

## The Gotchas

**Context must be threaded through the entire call chain.** Tracing context lives in `context.Context`. If you ever drop the context and start with `context.Background()` mid-chain, you sever the trace — the spans from that point forward become a disconnected trace. Never discard a context in a hot path.

**Sampling in production is not optional.** Tracing every request at 100% in high-traffic production systems can add meaningful overhead and produce more data than your backend can handle. Use head-based sampling (decide at the first span) or tail-based sampling (decide after you know if the trace was interesting) to keep overhead manageable. A 1–5% sample rate is common for high-traffic systems.

**Span names should be stable, not dynamic.** `span.Start(ctx, fmt.Sprintf("process-%d", userID))` creates a unique span name for every user, making it impossible to aggregate or search. Use stable names like `"order.Submit"` and put variable data in span attributes.

**Database queries need instrumentation too.** If your database calls aren't instrumented, the trace will show a gap where the DB query ran. Use an OTel-instrumented database driver like `otelsql` for `database/sql` or the native OTel support in `pgx`.

## Key Takeaway

Distributed tracing converts the question "why is this request slow?" from a multi-hour log archaeology exercise into a 10-second click. Use OpenTelemetry — it's the vendor-neutral standard that works with every observability backend. Instrument your HTTP handlers and database clients with OTel middleware to get traces automatically. Add custom spans at meaningful boundaries in your business logic. Thread context through every call and never discard it. Tune sampling in production to balance observability with overhead.

---

[← Lesson 3: Service Discovery](/post/go/go-micro-discovery) | [Course Index](/series/go-microservices-patterns) | [Next → Lesson 5: Saga Pattern](/post/go/go-micro-sagas)
