---
title: "Lesson 2: Metrics That Matter — Count, measure, alert"
author: Atharva Pandey
series: "Go Observability in Production"
lesson: 2
keywords:
  - Go
  - golang
  - observability
  - metrics
  - Prometheus
  - histograms
  - counters
  - gauges
  - alerting
tags:
  - Go tutorial
  - golang
  - observability
date: '2024-07-28T00:00:00.000Z'
---

The first metrics dashboard I built for a Go service had forty-two graphs. CPU, memory, goroutine count, heap allocations, GC pause duration, request rate, error rate, and about thirty-five other things that felt important when I added them. Six months later I was on call at 3 AM and the service was degraded. I opened that dashboard, looked at forty-two graphs, and had no idea where to start.

Metrics are only useful when you know what to alert on, and you can only alert on things you understand. More metrics is not the same as better observability. The question is not "what can I measure?" but "what breaks, how do I know it's broken, and how quickly can I narrow down why?"

## The Problem

Two failure modes I see constantly. The first is measuring the wrong things:

```go
// tracking things that don't connect to user experience
var (
    gcPauseMs     = promauto.NewGauge(prometheus.GaugeOpts{Name: "go_gc_pause_ms"})
    goroutineCount = promauto.NewGauge(prometheus.GaugeOpts{Name: "go_goroutine_count"})
    heapAllocBytes = promauto.NewGauge(prometheus.GaugeOpts{Name: "go_heap_alloc_bytes"})
)
```

These are fine as supplementary signals. But if none of your alerts fire on request latency or error rate, none of them will wake you up when users are having a bad time. GC pauses are a root cause, not a symptom. Alert on symptoms; diagnose root causes after you're paged.

The second failure mode is measuring with the wrong instrument. Using a counter for something that goes up and down, using a gauge for something that never decreases, or — most painfully — using a counter instead of a histogram for latency:

```go
// WRONG — you can compute average latency from this, but not percentiles
var totalLatencyMs = promauto.NewCounter(prometheus.CounterOpts{
    Name: "http_request_duration_ms_total",
})

func handleRequest(w http.ResponseWriter, r *http.Request) {
    start := time.Now()
    // ... handle
    totalLatencyMs.Add(float64(time.Since(start).Milliseconds()))
}
```

Average latency is almost always misleading. A p99 of 2 seconds with a p50 of 5ms looks like a 6ms average if 99% of your traffic is fast. Averages hide the tail.

## The Idiomatic Way

Go's de-facto standard for metrics is the [Prometheus client library](https://github.com/prometheus/client_golang). The four instrument types map to four questions:

- **Counter** — how many times has X happened? (always goes up)
- **Gauge** — what is the current value of X? (can go up or down)
- **Histogram** — how is the distribution of X shaped? (latency, sizes)
- **Summary** — like histogram but computes quantiles client-side (avoid in most cases)

Here is a well-instrumented HTTP handler:

```go
package metrics

import (
    "net/http"
    "strconv"
    "time"

    "github.com/prometheus/client_golang/prometheus"
    "github.com/prometheus/client_golang/prometheus/promauto"
)

var (
    httpRequestsTotal = promauto.NewCounterVec(
        prometheus.CounterOpts{
            Name: "http_requests_total",
            Help: "Total number of HTTP requests.",
        },
        []string{"method", "path", "status"},
    )

    httpRequestDuration = promauto.NewHistogramVec(
        prometheus.HistogramOpts{
            Name:    "http_request_duration_seconds",
            Help:    "HTTP request latency distribution.",
            Buckets: prometheus.DefBuckets, // .005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10
        },
        []string{"method", "path"},
    )

    httpRequestsInFlight = promauto.NewGauge(prometheus.GaugeOpts{
        Name: "http_requests_in_flight",
        Help: "Current number of HTTP requests being served.",
    })
)

func InstrumentHandler(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        httpRequestsInFlight.Inc()
        defer httpRequestsInFlight.Dec()

        start := time.Now()
        rw := &statusRecorder{ResponseWriter: w, status: 200}
        next.ServeHTTP(rw, r)

        duration := time.Since(start).Seconds()
        status := strconv.Itoa(rw.status)

        httpRequestsTotal.WithLabelValues(r.Method, r.URL.Path, status).Inc()
        httpRequestDuration.WithLabelValues(r.Method, r.URL.Path).Observe(duration)
    })
}
```

The histogram is the key instrument for latency. Prometheus histograms store counts per bucket on the server side, which means you can query `histogram_quantile(0.99, ...)` across multiple instances without client-side aggregation. Your p99 is a real server-side percentile, not a mathematical fiction.

**Choosing histogram buckets that matter**

The default Prometheus buckets span 5ms to 10s. For most web APIs, that range is too wide at the high end and too coarse at the low end. Define buckets based on your SLA:

```go
// For an API with a 100ms SLA, you want density below 100ms
httpRequestDuration = promauto.NewHistogramVec(
    prometheus.HistogramOpts{
        Name: "http_request_duration_seconds",
        Help: "HTTP request latency distribution.",
        Buckets: []float64{
            0.001, 0.005, 0.01, 0.025, 0.05,
            0.075, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0, 2.0,
        },
    },
    []string{"method", "path"},
)
```

Put more buckets in the region where your SLA lives. Having a bucket at exactly 100ms means you can write a precise alert: `histogram_quantile(0.99, ...) > 0.1`.

## In The Wild

In a real service, I separate metrics into three tiers that map directly to alert severity:

```go
// Tier 1 — page immediately (user-visible)
var (
    paymentFailuresTotal = promauto.NewCounterVec(
        prometheus.CounterOpts{Name: "payment_failures_total"},
        []string{"reason", "provider"},
    )
    checkoutLatency = promauto.NewHistogramVec(
        prometheus.HistogramOpts{
            Name:    "checkout_duration_seconds",
            Buckets: []float64{0.1, 0.25, 0.5, 1.0, 2.0, 5.0},
        },
        []string{"step"},
    )
)

// Tier 2 — ticket next business day
var (
    cacheHitRatio = promauto.NewGaugeVec(
        prometheus.GaugeOpts{Name: "cache_hit_ratio"},
        []string{"cache_name"},
    )
    dbConnectionsActive = promauto.NewGauge(
        prometheus.GaugeOpts{Name: "db_connections_active"},
    )
)

// Tier 3 — track trend, no alert
var (
    gcPauseDuration = promauto.NewHistogram(prometheus.HistogramOpts{
        Name:    "go_gc_pause_duration_seconds",
        Buckets: prometheus.ExponentialBuckets(0.0001, 2, 12),
    })
)
```

Tier 1 metrics map directly to user experience — if the checkout latency p99 exceeds 2 seconds or the payment failure rate exceeds 1%, someone's phone rings. Tier 2 metrics indicate degradation that hasn't yet reached users. Tier 3 is informational for post-incident analysis.

The alert rules in Prometheus for Tier 1 look like this:

```yaml
groups:
  - name: payment-api.rules
    rules:
      - alert: HighCheckoutLatency
        expr: histogram_quantile(0.99, rate(checkout_duration_seconds_bucket[5m])) > 2.0
        for: 2m
        labels:
          severity: page
        annotations:
          summary: "Checkout p99 latency > 2s for 2 minutes"

      - alert: PaymentFailureRateHigh
        expr: rate(payment_failures_total[5m]) / rate(http_requests_total{path="/checkout"}[5m]) > 0.01
        for: 1m
        labels:
          severity: page
```

## The Gotchas

**High cardinality labels kill Prometheus.** Labels are multiplied across time series. Adding a `user_id` label to a request counter sounds useful until you realize you're creating millions of time series — one per user — and your Prometheus memory explodes. Labels should have bounded cardinality: HTTP method (5 values), status code class (5 values), endpoint path (dozens, not thousands). Never use user IDs, order IDs, or session tokens as labels.

**`promauto` registers globally.** `promauto.NewCounterVec` registers with the default Prometheus registry. In tests, if you import two packages that both use `promauto` to register the same metric name, you get a panic on startup. The fix: use `prometheus.NewRegistry()` per test, or use `MustRegister` with explicit registries in your metric constructors.

**Counters don't survive restarts.** A Prometheus counter is `0` when the process starts. If you restart frequently, `rate()` handles this correctly (it detects resets), but raw counter values are meaningless across process boundaries. Always use `rate()` or `increase()` in your queries, never raw counter values.

**Record rules reduce query cost.** If your dashboard queries compute the same expensive `histogram_quantile` on every refresh, push that computation into a Prometheus recording rule so it's precomputed:

```yaml
- record: job:http_request_duration_seconds:p99
  expr: histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket[5m])) by (le, job))
```

## Key Takeaway

Pick the right instrument for the question you're asking: counter for events, gauge for current state, histogram for distributions. Define your SLA in numeric terms first, then build your buckets and alert thresholds around that number. Keep label cardinality bounded — high cardinality is the single most common way teams blow up their Prometheus installation. And tier your metrics: a small set of user-visible signals that page on-call, a larger set that generate tickets, and everything else as informational dashboards. More metrics is not better observability. The right metrics, with the right alerts, is the whole game.

---

← [Previous: Structured Logging with slog](/post/go/go-obs-slog) | [Course Index](/post/go/) | [Next: Distributed Tracing with OpenTelemetry →](/post/go/go-obs-tracing)
