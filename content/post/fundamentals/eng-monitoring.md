---
title: "Lesson 4: Monitoring and Alerting — SLOs and alert fatigue"
author: Atharva Pandey
keywords:
  - monitoring
  - alerting
  - SLO
  - SLA
  - SLI
  - alert fatigue
  - Prometheus
  - engineering practices
tags:
  - fundamentals
  - engineering practices
series: "Engineering Practices That Scale"
lesson: 4
date: '2024-06-24T00:00:00.000Z'
---

The on-call rotation I inherited had 47 active alerts. On a bad week, the on-call engineer got paged 30 times. Most pages were "something might be wrong" noise — high CPU on one instance, a spike in error rate that self-resolved in 30 seconds, disk space at 70% on a server with months of capacity remaining. Engineers stopped taking the pages seriously. Then the one real incident got buried in the noise, and we had a 4-hour outage because no one treated the first alert seriously. Alert fatigue is not a monitoring problem. It's an architecture-of-trust problem.

## How It Works

**The SLI → SLO → SLA hierarchy**

- **SLI (Service Level Indicator)**: A specific, measurable aspect of your service's behavior. "The percentage of HTTP requests that return a 2xx response in under 300ms." A single number.
- **SLO (Service Level Objective)**: A target for your SLI. "99.5% of requests return a 2xx response in under 300ms, measured over a 28-day rolling window." A threshold your team commits to maintaining.
- **SLA (Service Level Agreement)**: A contractual commitment to users or customers, often with financial consequences for breach. Built on top of SLOs, with stricter thresholds and legal weight.

The SLI/SLO framework solves alert fatigue at the design level. Instead of alerting on every metric anomaly ("CPU > 80%"), you alert on "are we burning through our error budget faster than we should be?" This shifts monitoring from symptoms to outcomes.

**Error Budgets**

An SLO of 99.5% availability means you're allowed 0.5% bad minutes over your measurement window. For a 28-day window: 28 days × 24 hours × 60 minutes × 0.5% = approximately 201 minutes of allowed downtime. This is your error budget.

The error budget is shared between the team and the business:
- When the budget is full: can deploy frequently, take risks, experiment
- When the budget is at 50%: slow down, be more careful with deployments
- When the budget is exhausted: freeze releases, focus exclusively on reliability

This turns "reliability vs velocity" from an argument into a quantified negotiation.

**Burn Rate Alerting**

Alerting on burn rate — how fast you're consuming the error budget — is more useful than alerting on absolute error rate:

```
Error budget for the window: 201 minutes
Normal burn rate: 1x (you'll use exactly the budget if this continues)
Burn rate of 5x means: consuming budget 5x faster than normal
  → You'll exhaust the budget in 201/5 = 40 minutes

Alert when:
- Short window (1h), high burn rate (> 14x): 2% budget in 1 hour → page immediately
- Medium window (6h), elevated burn rate (> 5x): gradual degradation → page soon
- Long window (3 days), low-level burn (> 1x): chronic problem → ticket
```

This gives you three tiers of alerts with very different urgency, all derived from a single SLO.

**The Four Golden Signals**

Google SRE's four metrics that matter for almost every service:

1. **Latency**: How long requests take. Distinguish successful vs failed — a fast error response isn't latency you've solved.
2. **Traffic**: How much demand is hitting your system. Requests/second, transactions/second, queries/second.
3. **Errors**: The rate of failing requests. Explicit failures (500s) and implicit failures (wrong data, timeouts).
4. **Saturation**: How "full" your service is. CPU, memory, database connections, queue depth. Most systems degrade before they hit 100% — measure the constraint, not just the capacity.

A monitoring setup covering all four golden signals gives you enough information to diagnose most production problems.

## Why It Matters

Alert fatigue is a reliability problem, not just an inconvenience. Engineers who are paged constantly develop coping mechanisms: muting alerts, auto-acknowledging pages, deprioritizing on-call. The alerts that mattered get the same treatment. The result is that monitoring — your primary mechanism for knowing when the system is unhealthy — becomes unreliable because humans have lost confidence in it.

SLO-based alerting fixes this by grounding alerts in user impact. "Are users experiencing bad requests?" is the question. "Is CPU > 80%?" is not — CPU at 80% might be perfectly fine, or it might be a precursor to a problem, but it doesn't directly tell you whether users are being served.

## Production Example

Implementing SLO monitoring in Prometheus and Grafana:

```yaml
# prometheus/rules/slo-payment-service.yaml
groups:
  - name: payment_service_slo
    interval: 30s
    rules:
      # SLI: success rate for payment authorization
      - record: job:payment_authorization_requests:success_rate5m
        expr: |
          sum(rate(http_requests_total{job="payment-service",status=~"2.."}[5m]))
          /
          sum(rate(http_requests_total{job="payment-service"}[5m]))

      # Error budget burn rate alerts (SLO: 99.5% success rate)
      # Page immediately: high burn rate in short window
      - alert: PaymentSLOHighBurnRate
        expr: |
          (
            job:payment_authorization_requests:success_rate5m < 0.995 * (1 - 14 * 0.005)
          )
        for: 1m
        labels:
          severity: page
          team: payments
        annotations:
          summary: "Payment service burning error budget at high rate"
          description: "Success rate {{ $value | humanizePercentage }} - check payment service immediately"

      # Notify but don't page: elevated burn in medium window
      - alert: PaymentSLOElevatedBurnRate
        expr: |
          (
            job:payment_authorization_requests:success_rate5m < 0.995 * (1 - 5 * 0.005)
          )
        for: 6h
        labels:
          severity: warning
          team: payments
        annotations:
          summary: "Payment service elevated error budget consumption"
```

Instrumenting a Go service with Prometheus:

```go
package main

import (
    "net/http"
    "time"

    "github.com/prometheus/client_golang/prometheus"
    "github.com/prometheus/client_golang/prometheus/promauto"
    "github.com/prometheus/client_golang/prometheus/promhttp"
)

var (
    httpRequestsTotal = promauto.NewCounterVec(
        prometheus.CounterOpts{
            Name: "http_requests_total",
            Help: "Total HTTP requests",
        },
        []string{"method", "path", "status"},
    )
    httpRequestDuration = promauto.NewHistogramVec(
        prometheus.HistogramOpts{
            Name:    "http_request_duration_seconds",
            Help:    "HTTP request duration in seconds",
            // Buckets matter — define them based on your SLO
            Buckets: []float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5},
        },
        []string{"method", "path", "status"},
    )
    // Saturation metric
    dbConnectionsInUse = promauto.NewGauge(prometheus.GaugeOpts{
        Name: "db_connections_in_use",
        Help: "Current number of database connections in use",
    })
)

func instrumentedHandler(method, path string, next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        start := time.Now()
        rw := &statusRecorder{ResponseWriter: w, Status: 200}
        next.ServeHTTP(rw, r)

        duration := time.Since(start).Seconds()
        status := fmt.Sprintf("%d", rw.Status)

        httpRequestsTotal.WithLabelValues(method, path, status).Inc()
        httpRequestDuration.WithLabelValues(method, path, status).Observe(duration)
    })
}

// Expose metrics endpoint
mux.Handle("/metrics", promhttp.Handler())
```

Alert runbook structure — each alert should link to its runbook:

```markdown
# Alert: PaymentSLOHighBurnRate

## What is this?
The payment service is returning errors at a rate that will exhaust the
monthly error budget within 2 hours if it continues.

## Immediate actions (< 5 minutes)
1. Check current error rate: [Grafana dashboard link]
2. Check recent deployments: `kubectl rollout history deploy/payment-service`
3. If a recent deployment: roll back immediately
   `kubectl rollout undo deploy/payment-service`

## Diagnosis (if rollback not applicable)
1. Check error logs: [Datadog query link]
2. Check database connection pool: [Grafana panel link]
3. Check upstream dependency health: [status dashboard link]

## Escalation
- If not resolved in 15 minutes: page @priya (payments team lead)
- If data loss suspected: page @cto immediately
```

## The Tradeoffs

**SLOs vs micromanaging metrics**: SLOs free you from monitoring every metric — you monitor outcomes and let the error budget absorb normal variation. The risk: your SLO might not capture real user pain. A service might have 99.9% success rate but 30-second P99 latency. If your SLO only measures success rate, latency degradation is invisible. Define SLIs that capture what users actually experience.

**Alert noise vs missed incidents**: Fewer, higher-quality alerts are better. But if you're over-aggressive in raising alert thresholds to eliminate noise, you'll miss real problems. Track your "noise ratio" — what percentage of pages require action vs self-resolve. Target < 20% false-positive pages.

**Aggregation hiding problems**: Aggregate metrics (average, total) hide problems in the tail. P99 latency can be 10x your P50 while the average looks fine. Always monitor percentiles for latency. Use histograms in Prometheus, not averages.

**Dashboards that no one reads**: A 40-panel Grafana dashboard is security theater if on-call engineers can't interpret it under pressure at 3am. Design dashboards for the person paged at 3am, not for the quarterly review. First panel: "Is the service healthy?" Second panel: "What's broken?"

**Toil from alert management**: Setting up SLOs and alerts has a maintenance cost. As services change, SLOs need adjustment. Alerts need runbooks. Runbooks need to be tested and kept current. Budget 10-20% of platform/SRE time for monitoring maintenance.

## Key Takeaway

Alert fatigue destroys on-call effectiveness — when everything pages, nothing matters. SLO-based alerting grounds alerts in user experience: you page when users are being harmed, not when a CPU metric looks unusual. Error budgets quantify how much failure is acceptable and provide a forcing function for reliability work. Instrument your services with the four golden signals (latency, traffic, errors, saturation), define SLOs against the metrics that matter to users, and alert on burn rate rather than absolute thresholds. Keep dashboards simple enough to read at 3am. Every alert that fires should require human action — anything less is noise.

---

**Previous:** [Lesson 3: Incident Response](/post/fundamentals/eng-incidents/)
**Next:** [Lesson 5: Load Testing — k6, vegeta, realistic patterns](/post/fundamentals/eng-load-testing/)
