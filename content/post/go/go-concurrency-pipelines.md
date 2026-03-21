---
title: 'Lesson 10: Pipelines and Stage Isolation — Each stage owns its output channel'
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 10
keywords:
  - Go
  - golang
  - concurrency
  - pipeline
  - stage isolation
  - generator
  - transform
  - sink
  - error propagation
  - channels
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-07-17T00:00:00.000Z'
---

A pipeline is how you turn a stream of work into a sequence of transformations without writing a single monolithic function that does everything. Each stage reads from one channel, does its thing, and writes to another. Stages compose. Stages are independently testable. And because each stage runs concurrently with every other stage, the whole pipeline processes multiple items simultaneously — like an assembly line rather than a factory worker doing each product start to finish.

The pattern sounds obvious. The discipline required to implement it correctly is less obvious. Specifically: each stage must own its output channel, close it exactly once, and propagate errors without silently dropping them.

## The Problem

The temptation with data transformation is to write one function that does everything:

```go
// WRONG — monolithic, untestable, hard to modify
func processRecords(records []Record) ([]Report, error) {
    var reports []Report
    for _, r := range records {
        // validate
        if r.ID == "" {
            continue // silently dropped
        }
        // enrich
        extra, err := fetchMetadata(r.ID)
        if err != nil {
            continue // also silently dropped
        }
        r.Extra = extra
        // transform
        report := Report{
            ID:    r.ID,
            Score: computeScore(r),
            Tags:  extra.Tags,
        }
        reports = append(reports, report)
    }
    return reports, nil
}
```

This works until you need to: add a new transformation stage, run the enrichment step in parallel, add error handling that doesn't silently swallow failures, or test the scoring logic in isolation. None of these are easy in the monolith. Every change touches the whole function.

The second mistake is building a pipeline but wiring stages incorrectly — specifically, having a stage close a channel it doesn't own, or forgetting to close it entirely:

```go
// WRONG — caller closes the channel that the stage writes to
func validate(in <-chan Record, out chan<- Record) {
    for r := range in {
        if r.ID != "" {
            out <- r
        }
    }
    // forgot defer close(out) — next stage blocks forever
}

func main() {
    raw := make(chan Record)
    validated := make(chan Record)
    go validate(raw, validated) // BUG: validated never closed

    go func() {
        defer close(raw)
        for _, r := range loadRecords() {
            raw <- r
        }
    }()

    for r := range validated { // blocks forever after raw is exhausted
        fmt.Println(r)
    }
}
```

When `raw` closes, `validate` exits its `for range` loop, returns, and never closes `validated`. The main goroutine blocks on `for r := range validated` forever.

## The Idiomatic Way

The rule: a stage creates its own output channel, starts its goroutine, and returns the channel to the caller. The stage's goroutine closes the channel when it's done writing. This is the "generator" function signature pattern.

```go
// RIGHT — generator: source of data, owns its output channel
func generate(ctx context.Context, records []Record) <-chan Record {
    out := make(chan Record)
    go func() {
        defer close(out)
        for _, r := range records {
            select {
            case out <- r:
            case <-ctx.Done():
                return
            }
        }
    }()
    return out
}

// RIGHT — transform stage: reads input, owns its output channel
func validate(ctx context.Context, in <-chan Record) <-chan Record {
    out := make(chan Record)
    go func() {
        defer close(out)
        for r := range in {
            if r.ID == "" || r.Timestamp.IsZero() {
                continue // invalid, skip
            }
            select {
            case out <- r:
            case <-ctx.Done():
                return
            }
        }
    }()
    return out
}

// RIGHT — sink: terminal stage, returns results
func collect(in <-chan Record) []Record {
    var results []Record
    for r := range in {
        results = append(results, r)
    }
    return results
}

// Composing the pipeline
func main() {
    ctx := context.Background()
    records := loadRecords()

    pipeline := collect(
        validate(ctx,
            generate(ctx, records),
        ),
    )
    fmt.Printf("processed %d valid records\n", len(pipeline))
}
```

This composes cleanly because each stage's return type is `<-chan T` — it's just a value you can pass to the next stage. The goroutine inside each stage will run until its input is exhausted, then close its output, which propagates the "done" signal downstream automatically.

Now the more realistic version — with error propagation. This is where most pipeline implementations fall short. Errors need to flow downstream or be collected. Silently dropping them is the same bug as the monolith, just spread across more functions.

```go
// RIGHT — error propagation through a pipeline
type Result[T any] struct {
    Value T
    Err   error
}

func enrich(ctx context.Context, in <-chan Record) <-chan Result[Record] {
    out := make(chan Result[Record])
    go func() {
        defer close(out)
        for r := range in {
            extra, err := fetchMetadata(ctx, r.ID)
            if err != nil {
                select {
                case out <- Result[Record]{Err: fmt.Errorf("enrich %s: %w", r.ID, err)}:
                case <-ctx.Done():
                    return
                }
                continue
            }
            r.Extra = extra
            select {
            case out <- Result[Record]{Value: r}:
            case <-ctx.Done():
                return
            }
        }
    }()
    return out
}

func score(ctx context.Context, in <-chan Result[Record]) <-chan Result[Report] {
    out := make(chan Result[Report])
    go func() {
        defer close(out)
        for res := range in {
            if res.Err != nil {
                // pass errors through without modification
                out <- Result[Report]{Err: res.Err}
                continue
            }
            report := Report{
                ID:    res.Value.ID,
                Score: computeScore(res.Value),
            }
            out <- Result[Report]{Value: report}
        }
    }()
    return out
}
```

Errors ride through the same channel as successes. Each stage either handles the error, transforms it, or passes it through. The sink collects both and can separate them at the end. This keeps the pipeline stages decoupled — the `score` stage doesn't need to know that `enrich` might fail; it just propagates errors it can't handle.

## In The Wild

I built an event-processing pipeline for a telemetry service. Raw events came in from Kafka, needed to be decoded, deduped against a Redis cache, enriched with device metadata from a PostgreSQL lookup, and then written to both a time-series database and an audit log. Five stages, each doing independent I/O, all running concurrently.

```go
func BuildPipeline(ctx context.Context, consumer KafkaConsumer) <-chan Result[AuditEntry] {
    raw := consumeKafka(ctx, consumer)           // generator
    decoded := decodeEvents(ctx, raw)             // bytes → Event
    deduped := deduplicateEvents(ctx, decoded)    // filter seen IDs
    enriched := enrichWithDeviceData(ctx, deduped) // lookup device
    return fanOutToSinks(ctx, enriched)           // write + audit
}
```

The fanout sink writes to both the time-series DB and the audit log concurrently, then emits a `Result[AuditEntry]` back to the caller. Because each stage is a separate goroutine, Kafka consumption, decoding, deduplication, enrichment, and writing all run in parallel. The Kafka consumer runs slightly ahead, buffers a few events, and the pipeline processes them as fast as the slowest stage allows — which in this case was the PostgreSQL device lookup. That's where we added a worker pool inside the `enrichWithDeviceData` stage to parallelize lookups.

## The Gotchas

**Unbuffered output channels causing head-of-line blocking.** If a stage's output channel is unbuffered and the next stage is slow, the current stage blocks waiting to send. This serializes the stages instead of letting them run concurrently. Add a small buffer to output channels — even a buffer of 1 can make a significant difference in throughput.

**Multiple goroutines writing to one stage's output channel.** If you try to fan out inside a stage by spinning up multiple goroutines that all write to `out`, you have multiple writers — which means you can't `close(out)` safely from any of them. Use a `WaitGroup` to coordinate: all inner goroutines write to `out`, the WaitGroup goroutine closes it after they're all done.

**Ignoring context cancellation in blocking sends.** Without `select { case out <- v: case <-ctx.Done(): return }`, a stage that's blocked sending to a full output channel will hang even after the context is cancelled. Every send in a pipeline stage needs that select. It's verbose, but it's the correct behavior.

**Error type erasure in the passthrough pattern.** When you wrap errors as you propagate them (`fmt.Errorf("stage: %w", err)`), make sure downstream stages that need to type-assert on errors use `errors.As` rather than direct type assertions. The wrapping breaks direct type assertions.

## Key Takeaway

Pipelines work well when each stage follows the contract: create your output channel, start your goroutine, close the channel on exit, handle context cancellation in every blocking send. That contract makes stages composable and independently testable. Error propagation deserves explicit design — carry errors in a `Result[T]` type through the same channel as successes, so no stage silently swallows failures. The assembly line analogy holds: each station does one job, passes the piece to the next, and the whole line moves in parallel.

---

← [Previous: Worker Pools](/post/go/go-concurrency-worker-pools) | [Course Index](/post/go/) | [Next: Graceful Shutdown](/post/go/go-concurrency-graceful-shutdown) →
