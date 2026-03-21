---
title: "Lesson 7: The Complete Observability Stack — Logs, metrics, traces, profiles — wired together"
author: Atharva Pandey
series: "Go Observability in Production"
lesson: 7
keywords:
  - Go
  - golang
  - observability
  - complete stack
  - logs
  - metrics
  - traces
  - profiles
  - Grafana
  - OpenTelemetry
  - production
tags:
  - Go tutorial
  - golang
  - observability
date: '2025-03-05T00:00:00.000Z'
---

We have covered each signal in isolation — structured logs, Prometheus metrics, OpenTelemetry traces, correlation IDs, pprof profiles, and latency distributions. The preceding six lessons described the individual instruments. This one is about wiring them together into a system that actually works during an incident, not just in a demo.

The goal is this: when something breaks in production, you should be able to answer four questions within five minutes: Is it broken? Who is affected? Where in the system did it break? What is the root cause? Logs, metrics, traces, and profiles each answer one of those questions. The wiring between them — shared trace IDs, consistent service names, deployment markers on dashboards — is what lets you move between signals without losing context.

## The Problem

Observability tools deployed in isolation give you islands of information. You see a Prometheus alert fire. You open the Grafana dashboard. The latency spike is visible. You need to know which code path is slow — so you switch to Jaeger and search for high-latency traces. You find one. The trace shows a slow database query. You want to see the logs around that query — so you switch to Loki and search... but what do you filter on? The trace ID is there in the span, but your Loki log lines don't have a `trace_id` field because your logging middleware was set up by a different team before you added OpenTelemetry.

Every context switch between tools costs you working memory and time. In a real incident, these friction points compound. The solution is not more tools — it is explicit links between the tools you already have.

## The Idiomatic Way

The complete stack I run for Go services uses:

- **Logs**: `log/slog` with JSON output → Promtail/Alloy → **Grafana Loki**
- **Metrics**: Prometheus client library → **Prometheus** → **Grafana**
- **Traces**: OpenTelemetry SDK → OTLP exporter → **Grafana Tempo**
- **Profiles**: Pyroscope Go SDK → **Grafana Pyroscope**

All four backends are accessible from a single Grafana instance with cross-linking configured.

**The bootstrap function**

Every Go service starts with the same setup function:

```go
package observability

import (
    "context"
    "log/slog"
    "os"

    "github.com/grafana/pyroscope-go"
    "github.com/prometheus/client_golang/prometheus/promhttp"
    "go.opentelemetry.io/otel"
    "go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
    "go.opentelemetry.io/otel/propagation"
    sdkresource "go.opentelemetry.io/otel/sdk/resource"
    sdktrace "go.opentelemetry.io/otel/sdk/trace"
    semconv "go.opentelemetry.io/otel/semconv/v1.21.0"
)

type Config struct {
    ServiceName    string
    ServiceVersion string
    OTLPEndpoint   string
    PyroscopeAddr  string
    LogLevel       slog.Level
}

// Init sets up all four observability signals and returns a shutdown function.
// Call defer shutdown(ctx) in main immediately after calling Init.
func Init(ctx context.Context, cfg Config) (shutdown func(context.Context) error, err error) {
    // 1. Structured logging
    handler := slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
        Level: cfg.LogLevel,
        ReplaceAttr: func(groups []string, a slog.Attr) slog.Attr {
            // rename "msg" to "message" for Loki compatibility
            if a.Key == slog.MessageKey {
                a.Key = "message"
            }
            return a
        },
    })
    slog.SetDefault(slog.New(handler))

    // 2. Distributed tracing
    resource := sdkresource.NewWithAttributes(
        semconv.SchemaURL,
        semconv.ServiceName(cfg.ServiceName),
        semconv.ServiceVersion(cfg.ServiceVersion),
        semconv.DeploymentEnvironment(os.Getenv("ENV")),
    )

    traceExporter, err := otlptracegrpc.New(ctx,
        otlptracegrpc.WithEndpoint(cfg.OTLPEndpoint),
        otlptracegrpc.WithInsecure(),
    )
    if err != nil {
        return nil, fmt.Errorf("creating trace exporter: %w", err)
    }

    tp := sdktrace.NewTracerProvider(
        sdktrace.WithBatcher(traceExporter),
        sdktrace.WithResource(resource),
        sdktrace.WithSampler(sdktrace.ParentBased(sdktrace.TraceIDRatioBased(0.1))),
    )
    otel.SetTracerProvider(tp)
    otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
        propagation.TraceContext{},
        propagation.Baggage{},
    ))

    // 3. Continuous profiling
    pyroscope.Start(pyroscope.Config{
        ApplicationName: cfg.ServiceName,
        ServerAddress:   cfg.PyroscopeAddr,
        ProfileTypes: []pyroscope.ProfileType{
            pyroscope.ProfileCPU,
            pyroscope.ProfileAllocObjects,
            pyroscope.ProfileInuseObjects,
            pyroscope.ProfileGoroutines,
        },
        Tags: map[string]string{
            "version": cfg.ServiceVersion,
            "env":     os.Getenv("ENV"),
        },
    })

    // 4. Metrics endpoint (Prometheus scrapes this)
    // register /metrics on the pprof mux, not the public mux
    http.Handle("/metrics", promhttp.Handler())

    return func(ctx context.Context) error {
        return tp.Shutdown(ctx)
    }, nil
}
```

One call. One deferred shutdown. All four signals initialized consistently with the same service name and version, which is the prerequisite for cross-linking to work.

**The middleware stack**

Middleware order is critical. The tracing middleware must run before the correlation middleware, which must run before the logging middleware:

```go
func buildHandler(router http.Handler, svc string) http.Handler {
    // 1. Tracing: creates the root span, puts it in context
    h := otelhttp.NewHandler(router, svc,
        otelhttp.WithMessageEvents(otelhttp.ReadEvents, otelhttp.WriteEvents),
    )
    // 2. Correlation: extracts trace ID from span, builds request-scoped logger
    h = correlationMiddleware(h)
    // 3. Metrics: records request count and latency
    h = metricsMiddleware(h)
    // 4. Recovery: catches panics, records them as span errors
    h = recoveryMiddleware(h)
    return h
}
```

With this stack, every request gets: a span in Tempo, a logger with `trace_id` and `request_id` in every log line in Loki, and a request duration observation in Prometheus. The cross-link is the `trace_id` field present in both the span (in Tempo) and the log lines (in Loki).

## In The Wild

**Grafana cross-linking configuration**

In Grafana, configure Loki's derived fields to turn `trace_id` in log lines into a clickable link to Tempo:

```json
{
  "name": "TraceID",
  "matcherRegex": "\"trace_id\":\"(\\w+)\"",
  "url": "${__value.raw}",
  "urlDisplayLabel": "View Trace",
  "datasourceUid": "tempo-uid"
}
```

Configure Tempo's trace-to-logs feature to jump from a span to Loki filtered by the trace ID:

```yaml
# in grafana.ini or provisioning
[feature_toggles]
traceToLogs = true
```

And in the Tempo datasource settings, set the `tracesToLogsV2` option pointing at your Loki datasource with `{service_name="${__span.tags["service.name"]}"} | json | trace_id="${__trace.traceId}"` as the query template.

**An incident workflow end to end**

When an alert fires — say, `HighCheckoutLatency` — the workflow is:

1. Open the Grafana dashboard. The latency heatmap shows the spike started 12 minutes ago. The `db_connections_active` gauge is at its pool max of 25. First hypothesis: connection pool exhaustion.

2. Open Tempo. Search for traces from the last 15 minutes with `http.route = "/checkout"` and `duration > 1s`. Find five slow traces. Click one. The waterfall shows `postgres.Query` taking 900ms — well above the 20ms normal. The query itself is visible as a span attribute: `SELECT * FROM orders WHERE user_id = $1`.

3. Click the trace ID in the Tempo UI. Jump to Loki, filtered to that trace. The logs show: `"message": "db query slow", "query": "SELECT * FROM orders WHERE user_id = ?", "duration_ms": 912, "rows": 48000`. The user in question has 48,000 orders — the query is doing a full table scan because the `user_id` index was dropped during last night's migration.

4. Open Pyroscope for the time window of the incident. The CPU flame graph shows `(*sql.Rows).Next` consuming 40% of CPU during the spike — consistent with scanning 48,000 rows per request.

Total time from alert to root cause: 4 minutes. The index was re-created in 8 minutes. Incident resolved.

## The Gotchas

**Service name must be identical across all four signals.** If your logs emit `service = "payment-api"`, your traces report `service.name = "payment_api"` (underscore), and your Pyroscope tags use `app = "payment"`, the cross-linking breaks. Enforce a single canonical service name constant at startup and use it everywhere.

**Sampling asymmetry breaks trace-to-log correlation.** If you sample 10% of traces but log 100% of requests, jumping from a log line to its trace will fail 90% of the time — the trace was not sampled. Either increase trace sampling for high-value endpoints (checkout, payment) to 100%, or accept that only sampled traces have the full visual. Add a `sampled = true/false` field to your log lines so you know which ones have a corresponding trace.

**OTLP batch exporter buffers spans in memory.** The default batch size is 512 spans and the batch timeout is 5 seconds. Under a traffic spike, spans accumulate in memory before flushing. Set a bounded queue size to prevent this from consuming unbounded memory:

```go
sdktrace.WithBatcher(exporter,
    sdktrace.WithMaxExportBatchSize(128),
    sdktrace.WithBatchTimeout(2*time.Second),
    sdktrace.WithMaxQueueSize(1024), // drop spans rather than OOM
)
```

**Deploy events as annotations.** Every time you deploy, annotate your Grafana dashboards with the deployment event. This makes it trivial to correlate a latency or error spike with the deployment that caused it. Use the Grafana annotations API in your CI/CD pipeline:

```bash
curl -X POST http://grafana:3000/api/annotations \
  -H "Content-Type: application/json" \
  -d "{\"text\": \"Deploy: $SERVICE v$VERSION\", \"tags\": [\"deploy\", \"$SERVICE\"]}"
```

Every metric chart will show a vertical line at the deployment time. When the p99 spikes 10 minutes after a deploy, the causal link is visually obvious.

## Key Takeaway

The observability stack is only as useful as the connections between its signals. Logs with `trace_id`. Traces with `service.name` matching your metric labels. Profiles tagged with the same version as your deployment annotations. Set up the cross-links once — Loki derived fields pointing at Tempo, Tempo configured to query Loki — and every future incident benefits from them. The incident workflow becomes: alert fires, latency heatmap shows the distribution, a slow trace shows which code path, a log line shows what data triggered it, a profile shows what the CPU was doing. Four questions. Five minutes. That is what the complete observability stack looks like in practice.

---

← [Previous: Debugging Latency and Leaks](/post/go/go-obs-latency-leaks) | [Course Index](/post/go/)

🎓 Course Complete! You've finished the **Go Observability in Production** series. From structured logging with `slog` through metrics, tracing, correlation IDs, profiling, and latency analysis — you now have the full stack. Go make your services observable.
