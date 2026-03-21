---
title: 'Lesson 19: Ordering vs Throughput — You can have fast or ordered, pick one'
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 19
keywords:
  - Go
  - golang
  - concurrency
  - ordering
  - throughput
  - fan-out
  - reorder buffer
  - sequencing
  - pipeline
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-10-31T00:00:00.000Z'
---

Here's a tension that comes up in almost every real concurrent system: you want to process things fast, which means doing them in parallel, but the output needs to come out in the same order the input arrived. These two goals are in direct conflict. Parallel execution means things finish in unpredictable order. Ordered output means you have to wait for the slowest thing in each batch.

The engineers who understand this tension build systems that make the trade-off explicitly. The engineers who don't notice it build systems that either throttle themselves to single-goroutine throughput "to preserve order" or silently return results in the wrong order and wonder why tests fail non-deterministically.

## The Problem

The accidental serialization problem: someone adds an ordering requirement to what was a parallel operation, and the parallelism quietly disappears:

```go
// WRONG — preserving order by processing sequentially
func processOrdered(inputs []Request) []Response {
    results := make([]Response, len(inputs))
    for i, req := range inputs {
        results[i] = process(req) // sequential, "safe" order
    }
    return results
}
```

This is correct — output order matches input order — but it's leaving parallelism on the table. If `process` takes 100ms and you have 100 requests, this takes 10 seconds. If you can run them in parallel, it takes ~100ms plus a small sequencing overhead. That's two orders of magnitude.

The opposite mistake is going parallel without caring about order and shipping results that downstream depends on arriving in sequence:

```go
// WRONG — parallel but loses order; downstream breaks
func processUnordered(inputs []Request, out chan<- Response) {
    for _, req := range inputs {
        go func(r Request) {
            out <- process(r) // finishes and sends whenever it finishes
        }(req)
    }
}
```

If the caller expects response[0] to correspond to input[0], this breaks silently. No error, just wrong data.

## The Idiomatic Way

For cases where order doesn't actually matter — and there are many — unordered fan-out is the simplest and fastest pattern:

```go
// RIGHT — unordered fan-out when caller doesn't need ordered results
func processUnordered(ctx context.Context, inputs []Request) ([]Response, error) {
    g, ctx := errgroup.WithContext(ctx)
    results := make(chan Response, len(inputs))

    for _, req := range inputs {
        req := req
        g.Go(func() error {
            resp, err := process(ctx, req)
            if err != nil {
                return err
            }
            results <- resp
            return nil
        })
    }

    if err := g.Wait(); err != nil {
        return nil, err
    }
    close(results)

    var out []Response
    for r := range results {
        out = append(out, r)
    }
    return out, nil
}
```

The results arrive in whatever order they complete. If the caller can handle that — rendering a list of search results where all items are equivalent, for example — this is the right design. Maximum parallelism, minimal complexity.

When order genuinely matters, the reorder buffer pattern preserves throughput while guaranteeing sequence:

```go
// RIGHT — parallel execution with ordered output via index preservation
func processOrdered(ctx context.Context, inputs []Request) ([]Response, error) {
    type indexedResult struct {
        idx  int
        resp Response
        err  error
    }

    results := make(chan indexedResult, len(inputs))
    var wg sync.WaitGroup

    for i, req := range inputs {
        wg.Add(1)
        go func(idx int, r Request) {
            defer wg.Done()
            resp, err := process(ctx, r)
            results <- indexedResult{idx: idx, resp: resp, err: err}
        }(i, req)
    }

    go func() {
        wg.Wait()
        close(results)
    }()

    ordered := make([]Response, len(inputs))
    for res := range results {
        if res.err != nil {
            return nil, res.err
        }
        ordered[res.idx] = res.resp
    }
    return ordered, nil
}
```

Each goroutine tags its result with the original index. The collector goroutine fills a pre-allocated slice at the correct position. All the parallelism is preserved — goroutines run in arbitrary order — but the output slice is in input order. The throughput overhead is just the pre-allocated slice and the index bookkeeping. Negligible.

## In The Wild

A streaming scenario where inputs arrive continuously and ordered output must be emitted downstream — think log lines that must be processed and forwarded in sequence — requires a sliding-window reorder buffer:

```go
// RIGHT — sliding window reorder buffer for streaming ordered output
type ReorderBuffer struct {
    mu       sync.Mutex
    next     int64          // next sequence number to emit
    pending  map[int64]Item // items waiting to be emitted in order
    out      chan<- Item
}

func NewReorderBuffer(out chan<- Item) *ReorderBuffer {
    return &ReorderBuffer{
        pending: make(map[int64]Item),
        out:     out,
    }
}

func (b *ReorderBuffer) Submit(seq int64, item Item) {
    b.mu.Lock()
    defer b.mu.Unlock()

    b.pending[seq] = item

    // emit any consecutive items from next onward
    for {
        if item, ok := b.pending[b.next]; ok {
            b.out <- item
            delete(b.pending, b.next)
            b.next++
        } else {
            break
        }
    }
}
```

Workers process items concurrently and call `Submit` with their sequence number whenever they finish. The reorder buffer holds results that arrived early in `pending` and emits them in order as the gaps fill in. The downstream channel receives a perfectly ordered stream regardless of which worker finished first.

This is essentially how TCP reassembly works — out-of-order segments arrive and get buffered until the sequence gap is filled, then everything flows to the application layer in order.

The key design question for the reorder buffer: what do you do if a sequence number never arrives (the goroutine panicked, the item was dropped)? You need a timeout mechanism or a maximum pending size to avoid the buffer growing unbounded waiting for a gap that will never close.

## The Gotchas

**Assuming you need ordering when you don't.** Before implementing a reorder buffer, ask: does the caller actually require ordered results? Often the answer is "it seemed like they should be ordered" rather than "yes, the downstream breaks if they're not." Unordered parallel results are almost always fast enough and far simpler to implement.

**The reorder buffer doesn't help with the stragglers.** Parallel execution with ordered output means your total latency is bounded by the slowest item, not the average. If 99 out of 100 items process in 10ms and one takes 10 seconds, your ordered output is delayed 10 seconds even though 99% of results are ready. For pipelines where some items are inherently slow, consider separating the fast path from the slow path rather than holding everything behind the straggler.

**Index-tagged results require care with error handling.** When you pre-allocate `ordered := make([]Response, len(inputs))` and a goroutine returns an error, you need to decide: cancel everything, skip the failed index, or return partial results? Make this decision explicitly. The temptation to silently leave a zero value in the slice at the failed index produces subtle bugs — the caller gets a full-length result slice with some zero-value entries and no indication of which ones failed.

**Memory pressure from buffering.** A reorder buffer that's waiting for a slow or missing sequence number accumulates items in memory. In a streaming system with high throughput, this can be significant. Set a maximum pending size and reject or drop items that would exceed it. Better to signal backpressure than to silently consume memory.

## Key Takeaway

The fundamental trade-off is simple: unordered fan-out is fast and simple; ordered output requires sequencing overhead. The right choice depends on whether the downstream truly requires ordering — which is worth checking rather than assuming. When ordering is required, the index-preservation pattern (tag each result with its input index, collect into a pre-allocated slice) handles the one-shot case cleanly. For streaming workloads where results arrive continuously, a sliding-window reorder buffer gives you ordered output from a parallel worker pool. In both cases, total latency is bounded by the slowest item — that's the unavoidable cost of preserving order across parallel execution.

---

← [Previous: Backpressure Design](/post/go/go-concurrency-backpressure) | [Course Index](/post/go/) | [Next: Profiling Contention](/post/go/go-concurrency-contention-profiling) →
