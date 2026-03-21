---
title: 'Lesson 4: Buffered vs Unbuffered Channels — Buffering hides bugs'
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 4
keywords:
  - Go
  - golang
  - concurrency
  - buffered channel
  - unbuffered channel
  - channel semantics
  - throughput
  - backpressure
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-05-12T00:00:00.000Z'
---

The first time someone tells you that buffered channels are faster, you believe them — and you spend the next year throwing `make(chan T, 100)` at every performance complaint, wondering why your system still deadlocks sometimes, and why those deadlocks disappeared when you bumped the buffer to 200. The buffer wasn't fixing anything. It was delaying the problem. Understanding why requires actually thinking about what buffering changes — not at the performance level, but at the coordination level.

## The Problem

Here's the classic example of using buffering to "fix" a deadlock without fixing the actual problem:

```go
// WRONG — buffering masks a missing receiver
func main() {
    ch := make(chan int, 5) // "fixed" the deadlock by adding a buffer

    go func() {
        for i := 0; i < 5; i++ {
            ch <- i // all 5 fit in the buffer — no blocking
        }
        // We never close ch, and there's no receiver goroutine
    }()

    time.Sleep(100 * time.Millisecond)
    // ch has 5 values in it, nobody reads them, we just... move on
    fmt.Println("done")
}
```

The original code deadlocked because the send had nobody to receive it. Adding a buffer of 5 made it *not deadlock* — but nothing reads those values. The buffer didn't fix the program logic, it just made the failure mode invisible. Increase load to 6 items, or forget to size the buffer right, and you're back to deadlocking.

Here's the subtler production version — using a large buffer as a substitute for proper backpressure:

```go
// WRONG — buffer as a pressure release valve
type EventProcessor struct {
    events chan Event
}

func NewEventProcessor() *EventProcessor {
    return &EventProcessor{
        events: make(chan Event, 10000), // "should be enough"
    }
}

func (p *EventProcessor) Submit(e Event) {
    p.events <- e // blocks when buffer full — caller has no idea
}

func (p *EventProcessor) Run() {
    for e := range p.events {
        processSlowly(e) // slower than submit rate
    }
}
```

If `processSlowly` can't keep up with `Submit`, the buffer fills. When it's full, `Submit` blocks — silently, from the caller's perspective. The caller might be an HTTP handler. Now your HTTP handlers are blocking. Latency climbs. No metrics, no errors, no obvious signal — just slow degradation until everything falls over.

## The Idiomatic Way

**Unbuffered channels guarantee synchronization** — the send and receive happen at exactly the same moment. This is a strong, useful guarantee. It means two goroutines have rendezvous'd. Use unbuffered channels when you want to know that a value was received, not just sent.

```go
// RIGHT — unbuffered for synchronization semantics
func runWithHandoff(ctx context.Context) {
    ready := make(chan struct{}) // unbuffered — signal of coordination

    go func() {
        setupResources()
        ready <- struct{}{} // blocks until main receives — true handoff
    }()

    select {
    case <-ready:
        fmt.Println("resources ready, proceeding")
    case <-ctx.Done():
        fmt.Println("setup cancelled")
    }
}
```

**Buffered channels are for throughput and decoupling** — they let a producer continue without waiting for a consumer on every item. Use them when the sender shouldn't be slowed down by the receiver's pace, and when you can reason about the maximum number of items in flight.

```go
// RIGHT — buffered for throughput with explicit capacity reasoning
func processBatch(ctx context.Context, items []WorkItem) error {
    // Buffer = number of workers. Producer fills buffer; workers drain it.
    // When buffer is full, producer naturally slows to worker pace.
    workerCount := runtime.NumCPU()
    jobs := make(chan WorkItem, workerCount)
    results := make(chan error, len(items)) // one slot per item — no blocking

    // Workers
    var wg sync.WaitGroup
    for i := 0; i < workerCount; i++ {
        wg.Add(1)
        go func() {
            defer wg.Done()
            for job := range jobs {
                results <- doWork(ctx, job)
            }
        }()
    }

    // Producer
    go func() {
        defer close(jobs)
        for _, item := range items {
            select {
            case jobs <- item:
            case <-ctx.Done():
                return
            }
        }
    }()

    wg.Wait()
    close(results)

    for err := range results {
        if err != nil {
            return err
        }
    }
    return nil
}
```

The `jobs` buffer is sized to `workerCount` — just enough to keep workers busy without accumulating a large backlog. The `results` buffer is sized to `len(items)` so workers never block writing results. These aren't arbitrary numbers — they're derived from the structure of the program.

For the submit/process pattern, the right approach is to make the buffer overflow visible and give callers a choice:

```go
// RIGHT — explicit backpressure with non-blocking submit
type EventProcessor struct {
    events chan Event
}

func NewEventProcessor(bufferSize int) *EventProcessor {
    return &EventProcessor{
        events: make(chan Event, bufferSize),
    }
}

// TrySubmit returns false if the processor is at capacity — caller decides what to do
func (p *EventProcessor) TrySubmit(e Event) bool {
    select {
    case p.events <- e:
        return true
    default:
        return false // buffer full — explicit signal, not silent block
    }
}

// Submit blocks with context — caller controls how long they wait
func (p *EventProcessor) Submit(ctx context.Context, e Event) error {
    select {
    case p.events <- e:
        return nil
    case <-ctx.Done():
        return ctx.Err()
    }
}
```

Now callers know when the processor is overwhelmed. They can drop the event, log it, increment a metric, or return an error to their own caller. The buffer is still there — but it's a tool, not a hiding place.

## In The Wild

A notification system I worked on had an unbuffered channel for sending push notifications. The thought was "unbuffered means we know every notification was picked up" — which is true, but costly. The notification sender was the rate-limiting factor: it batched API calls in 100ms windows. Every goroutine trying to send a notification was blocking for up to 100ms waiting for the sender to accept.

We switched to a buffered channel sized to the expected burst — about 500 notifications — which decoupled producers from the 100ms batching cycle. Goroutines could submit and move on. The sender drained at its own pace.

```go
// RIGHT — buffered channel matches the batching rhythm
type NotificationQueue struct {
    ch      chan Notification
    metrics *prometheus.CounterVec
}

func NewNotificationQueue(burstCapacity int, m *prometheus.CounterVec) *NotificationQueue {
    return &NotificationQueue{ch: make(chan Notification, burstCapacity), metrics: m}
}

func (q *NotificationQueue) Enqueue(ctx context.Context, n Notification) error {
    select {
    case q.ch <- n:
        q.metrics.WithLabelValues("enqueued").Inc()
        return nil
    case <-ctx.Done():
        q.metrics.WithLabelValues("dropped_cancel").Inc()
        return ctx.Err()
    default:
        q.metrics.WithLabelValues("dropped_full").Inc()
        return ErrQueueFull
    }
}
```

The key addition was the metric on `dropped_full`. We could see exactly when the queue was saturated — and we tuned `burstCapacity` based on real data rather than guessing.

## The Gotchas

**Using `make(chan T, 1)` thinking it's "basically unbuffered."** A buffer of 1 means the first send always succeeds without a receiver. That completely changes the synchronization guarantee. If you want synchronization — rendezvous semantics — use buffer size 0. If you want "send and move on for one item," use 1, but be deliberate about it.

**Sizing buffers by feel rather than by analysis.** "100 should be enough" is not a design. Buffers should be sized based on the maximum burst you expect, the processing latency you can tolerate, and the memory cost per item. For most pipelines, a buffer equal to the number of concurrent workers is a reasonable starting point for the work queue.

**Assuming a buffered channel won't block.** It will — when it's full. Code that sends to a buffered channel without a `select`/`ctx.Done()` case will block the goroutine if the receiver falls behind. This is fine when it's intentional backpressure; it's a latency bug when the caller was an HTTP handler expecting sub-millisecond sends.

## Key Takeaway

Buffering isn't an optimization — it's a design choice about when producer and consumer are coupled. Unbuffered channels say "we meet here, right now." Buffered channels say "you can run ahead, but only this far." Neither is universally better. The question to ask every time you write `make(chan T, N)` is: what does N represent? If you can't answer that without saying "I just wanted to avoid blocking," the buffer is hiding a problem you haven't solved.

---

[← Lesson 3: Channel Ownership Rules](/post/go/go-concurrency-channel-ownership) | [Course Index](/series/go-concurrency-masterclass) | [Next → Lesson 5: sync.WaitGroup](/post/go/go-concurrency-waitgroup)
