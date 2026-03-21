---
title: 'Lesson 18: Backpressure Design — Slow down or blow up'
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 18
keywords:
  - Go
  - golang
  - concurrency
  - backpressure
  - rate limiting
  - golang.org/x/time/rate
  - queue design
  - blocking sends
  - message dropping
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-10-19T00:00:00.000Z'
---

Every system has a throughput ceiling. The question isn't whether your service can be overwhelmed — it can. The question is what happens when it is. Does it slow down gracefully, maintaining correctness and giving the caller a clear signal? Or does it blow up — OOM-killed, goroutines piling up, latency spiking to infinity while requests pile up in an unbounded queue?

Backpressure is the mechanism that answers that question. It's how a component communicates to its upstream: "I'm full — slow down." Without it, fast producers eat slow consumers alive. In Go, backpressure is naturally expressed through blocking channel sends — and understanding when to block versus when to drop is one of the more interesting design decisions in concurrent systems.

## The Problem

The most dangerous pattern I've seen in production codebases is an unbounded goroutine-per-request model:

```go
// WRONG — unbounded goroutine creation, no backpressure
func (s *Server) HandleEvent(event Event) {
    go func() {
        s.processEvent(event) // could be slow, could fail
    }()
}
```

When events arrive faster than `processEvent` can handle them, goroutines pile up. Each goroutine holds memory. At some point the runtime is spending more time GC-ing goroutine stacks than doing actual work, and shortly after that the process is OOM-killed. The load balancer routes traffic to the next instance, which dies the same way. Cascading failure.

The second flavour of this mistake is an unbounded in-memory queue:

```go
// WRONG — unbounded queue, same OOM outcome
type EventProcessor struct {
    queue []Event
    mu    sync.Mutex
}

func (p *EventProcessor) Enqueue(event Event) {
    p.mu.Lock()
    p.queue = append(p.queue, event) // always succeeds, grows without bound
    p.mu.Unlock()
}
```

The queue grows until you run out of memory. You've just moved the explosion from goroutine stacks to heap allocations.

## The Idiomatic Way

A buffered channel with a deliberate size *is* your queue, and a blocking send *is* your backpressure signal:

```go
// RIGHT — bounded queue with blocking backpressure
type EventProcessor struct {
    queue chan Event
}

func NewEventProcessor(queueSize int) *EventProcessor {
    p := &EventProcessor{
        queue: make(chan Event, queueSize),
    }
    go p.run()
    return p
}

func (p *EventProcessor) Enqueue(ctx context.Context, event Event) error {
    select {
    case p.queue <- event:
        return nil
    case <-ctx.Done():
        return ctx.Err()
    }
}

func (p *EventProcessor) run() {
    for event := range p.queue {
        p.processEvent(event)
    }
}
```

When the queue is full, `Enqueue` blocks — that block propagates back through the call stack to the HTTP handler, to the connection, to the client. The client sees slow responses and backs off. That's backpressure working correctly. The context integration means the caller can also give up explicitly rather than waiting indefinitely.

For rate limiting at the ingress — controlling how many operations you *start* per second rather than how many are *in flight* — `golang.org/x/time/rate` is the standard library answer:

```go
// RIGHT — token bucket rate limiter for ingress control
import "golang.org/x/time/rate"

type RateLimitedProcessor struct {
    limiter *rate.Limiter
    process func(Event) error
}

func NewRateLimitedProcessor(rps int, burst int, fn func(Event) error) *RateLimitedProcessor {
    return &RateLimitedProcessor{
        limiter: rate.NewLimiter(rate.Limit(rps), burst),
        process: fn,
    }
}

func (p *RateLimitedProcessor) Handle(ctx context.Context, event Event) error {
    if err := p.limiter.Wait(ctx); err != nil {
        return fmt.Errorf("rate limit wait: %w", err)
    }
    return p.process(event)
}
```

`rate.NewLimiter(rate.Limit(rps), burst)` creates a token-bucket limiter. `burst` is how many events you can process in a single instant; `rps` is the steady-state rate. `limiter.Wait(ctx)` blocks until a token is available or the context expires. This is the right tool when you need to control *throughput* — requests per second — rather than *concurrency*.

## In The Wild

Real queue design needs to handle both "block and wait" and "drop with a signal" strategies, and the right choice depends on the situation:

```go
// RIGHT — explicit drop policy for non-critical events
type MetricsCollector struct {
    queue chan Metric
    dropped atomic.Int64
}

func NewMetricsCollector() *MetricsCollector {
    c := &MetricsCollector{
        queue: make(chan Metric, 10_000),
    }
    go c.flush()
    return c
}

// RecordNonBlocking drops the metric if the queue is full
func (c *MetricsCollector) RecordNonBlocking(m Metric) {
    select {
    case c.queue <- m:
    default:
        c.dropped.Add(1) // count drops so you can alert on them
    }
}

// RecordBlocking blocks until the queue has space
func (c *MetricsCollector) RecordBlocking(ctx context.Context, m Metric) error {
    select {
    case c.queue <- m:
        return nil
    case <-ctx.Done():
        return ctx.Err()
    }
}
```

For metrics and observability data, dropping under load is usually acceptable — you'd rather have slightly incomplete metrics than slow down user-facing requests. For business-critical events like payments or inventory updates, dropping is unacceptable and blocking with a timeout is the right call. Having both methods on the type lets callers choose the contract that matches their criticality.

Track your drop counter and alert on it. A non-zero drop rate means your queue size is too small, your consumer is too slow, or your inbound rate is genuinely higher than your system's capacity. Any of those is worth knowing about.

## The Gotchas

**Blocking in an HTTP handler without a timeout.** If your handler blocks on `queue <- event` waiting for space, and the queue never drains, every connection eventually times out at the load balancer level — often with a confusing "upstream closed the connection" error rather than a clean 503. Always pair blocking queue sends with a context deadline:

```go
// RIGHT — handler returns 503 if it can't enqueue within a deadline
func (s *Server) handleRequest(w http.ResponseWriter, r *http.Request) {
    ctx, cancel := context.WithTimeout(r.Context(), 100*time.Millisecond)
    defer cancel()

    if err := s.queue.Enqueue(ctx, parseEvent(r)); err != nil {
        http.Error(w, "service busy", http.StatusServiceUnavailable)
        return
    }
    w.WriteHeader(http.StatusAccepted)
}
```

**Sizing the queue buffer.** Too small and you're dropping or blocking healthy traffic. Too large and you delay the backpressure signal — by the time the queue fills, you've buffered 10 seconds of events and latency for the first item is already unacceptably high. A useful heuristic: buffer size should be no more than a few seconds' worth of traffic at your P99 processing rate. For a processor that handles 1,000 events/second, a buffer of 2,000–5,000 is reasonable. A buffer of 1,000,000 is a time bomb.

**Rate limiters that don't shed load.** `rate.Limiter.Wait` blocks. If your upstream sends bursts that exceed your rate limit, requests queue behind `Wait`. Under sustained overload, every goroutine ends up parked in `Wait` and you're back to the unbounded goroutine problem. Use `limiter.Allow()` to shed load non-blockingly when the queue is already deep:

```go
if !limiter.Allow() {
    http.Error(w, "too many requests", http.StatusTooManyRequests)
    return
}
```

**Backpressure doesn't solve slow consumers.** Backpressure slows producers to match consumer throughput. If the consumer is too slow to handle steady-state traffic — not just spikes — backpressure just serializes the failure. You also need to scale the consumer, optimize the processing path, or explicitly cap incoming traffic at ingress.

## Key Takeaway

Backpressure is what separates systems that degrade gracefully from systems that fall over. In Go, a blocking channel send is the most natural expression of backpressure — when the channel is full, the sender waits, and that wait propagates back through the call stack. For ingress rate control, `golang.org/x/time/rate` gives you a token-bucket limiter that's context-aware and composable. For non-critical data like metrics, a non-blocking send with a drop counter is often the right trade-off. The key design question is always: what should happen when my consumer can't keep up? Answer that before you write the first line of code.

---

← [Previous: Go Scheduler Behavior](/post/go/go-concurrency-scheduler) | [Course Index](/post/go/) | [Next: Ordering vs Throughput](/post/go/go-concurrency-ordering-throughput) →
