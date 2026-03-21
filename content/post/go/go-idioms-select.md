---
title: 'Lesson 17: select Is Elegant — Waiting on multiple futures without blocking'
author: Atharva Pandey
keywords:
  - Go
  - golang
  - select
  - channels
  - timeout
  - context
  - concurrency
  - worker pattern
tags:
  - Go tutorial
  - golang
date: '2025-12-15T00:00:00.000Z'
series: "Idiomatic Go"
lesson: 17
---

Every non-trivial concurrent program eventually needs to wait on more than one thing at a time. Maybe you're waiting on a job channel but also need to respond to cancellation. Maybe you want to fetch from a channel but bail out after a timeout. Sequential channel receives can't do this — they block on one thing and miss everything else. `select` is the solution, and it's more powerful than it looks.

Once you're comfortable with `select`, writing concurrent Go starts to feel like composing small, clean pieces rather than wrestling with coordination logic.

## The Problem

Without `select`, you're forced into sequential channel reads. The goroutine blocks on the first channel even when the second one has data ready:

```go
// WRONG — sequential reads miss simultaneous readiness
func drain(ch1, ch2 <-chan string) {
    for {
        msg := <-ch1 // blocks here even if ch2 has data
        fmt.Println("ch1:", msg)
        msg = <-ch2
        fmt.Println("ch2:", msg)
    }
}
```

The same problem shows up with timeouts. If you just receive from a channel with no timeout, and the sender never sends, the goroutine parks there forever:

```go
// WRONG — waiting with no timeout
func fetchData(ch <-chan []byte) ([]byte, error) {
    data := <-ch // what if the sender dies?
    return data, nil
}
```

In production, this becomes a goroutine that leaks silently under partial failure — exactly the kind of bug you don't find until 3am.

## The Idiomatic Way

`select` blocks until one of its cases can proceed, then executes that case. If multiple cases are ready simultaneously, Go picks one at random — which is a feature, not a bug, since it prevents any single channel from starving others.

**Waiting on multiple channels simultaneously:**

```go
// RIGHT — select waits on both simultaneously
func drain(ch1, ch2 <-chan string) {
    for {
        select {
        case msg := <-ch1:
            fmt.Println("ch1:", msg)
        case msg := <-ch2:
            fmt.Println("ch2:", msg)
        }
    }
}
```

**Timeout using `time.After`:**

```go
// RIGHT — timeout using select and time.After
func fetchData(ch <-chan []byte, timeout time.Duration) ([]byte, error) {
    select {
    case data := <-ch:
        return data, nil
    case <-time.After(timeout):
        return nil, fmt.Errorf("fetchData: timed out after %v", timeout)
    }
}
```

`time.After(timeout)` returns a channel that receives a value when the duration elapses. `select` sees that case become ready and takes it. Clean, readable, and composable.

**Non-blocking operations with `default`:**

```go
// RIGHT — non-blocking receive with default
func tryReceive(ch <-chan int) (int, bool) {
    select {
    case v := <-ch:
        return v, true
    default:
        return 0, false
    }
}
```

The `default` case runs immediately if no other case is ready. This turns any channel operation into a non-blocking poll.

**Cancellation with `ctx.Done()`:**

```go
// RIGHT — check cancellation on each iteration
func processItems(ctx context.Context, items []Item) error {
    for _, item := range items {
        select {
        case <-ctx.Done():
            return ctx.Err()
        default:
        }
        if err := heavyProcess(ctx, item); err != nil {
            return fmt.Errorf("processItems: %v: %w", item.ID, err)
        }
    }
    return nil
}
```

## In The Wild

The canonical production pattern is `select` inside a `for` loop — the worker loop. The loop keeps the goroutine alive; `select` handles everything it needs to react to.

```go
func runWorker(ctx context.Context, jobs <-chan Job, errors chan<- error) {
    for {
        select {
        case <-ctx.Done():
            return

        case job, ok := <-jobs:
            if !ok {
                return // jobs channel closed
            }

            if err := job.Execute(); err != nil {
                select {
                case errors <- fmt.Errorf("job %d: %w", job.ID, err):
                default:
                    // error channel full — log and move on
                    log.Printf("dropped error for job %d: %v", job.ID, err)
                }
            }
        }
    }
}
```

The inner `select` on the errors channel uses `default` to avoid stalling the worker if the error channel is full. Three things handled cleanly: cancellation, orderly shutdown, error reporting.

There's also a clever trick using nil channels to disable `select` cases. A nil channel blocks forever, so setting a channel variable to nil inside a `select` loop effectively removes that case:

```go
func merge(ch1, ch2 <-chan int) <-chan int {
    out := make(chan int)
    go func() {
        defer close(out)
        for ch1 != nil || ch2 != nil {
            select {
            case v, ok := <-ch1:
                if !ok {
                    ch1 = nil // disable this case
                    continue
                }
                out <- v
            case v, ok := <-ch2:
                if !ok {
                    ch2 = nil // disable this case
                    continue
                }
                out <- v
            }
        }
    }()
    return out
}
```

When `ch1` closes, setting it to `nil` removes it from contention. The loop keeps draining `ch2` until it closes too.

## The Gotchas

**Relying on case selection order.** When multiple cases are ready, Go picks randomly. You cannot predict which one runs. If you need priority — always handle cancellation before a job — use a non-blocking check before the main `select`:

```go
// Check cancellation first (non-blocking)
select {
case <-ctx.Done():
    return
default:
}

// Now block on either
select {
case <-ctx.Done():
    return
case job := <-jobs:
    process(job)
}
```

**Polling in a tight loop with `default`.** Using `default` for non-blocking checks is fine for occasional polls. If you find yourself doing it in a tight loop with no sleep or backoff, you've built a spin-wait. That hammers the CPU with no benefit. Non-blocking selects are for occasional checks, not replacing blocking reads.

**`time.After` in hot loops.** `time.After` creates a `time.Timer` internally that isn't garbage collected until it fires. In tight loops with many short timeouts, create a `time.NewTimer` once and reset it with `timer.Reset()` between iterations instead of calling `time.After` every time.

## Key Takeaway

`select` is not just a `switch` for channels — it's the primitive that makes Go concurrency composable. Timeout, cancellation, non-blocking checks, worker loops, nil-disabling cases: these patterns all build on `select` and they all compose cleanly with each other. I've seen engineers who learned Go from tutorials skip over `select` and end up with concurrent code full of brittle timing assumptions and goroutine leaks. Learn the patterns once — they show up everywhere in real Go codebases, and once you see them you can't unsee them.

---

← [Previous: Channels Are for Coordination](/post/go/go-idioms-channels-coordination) | [Course Index](/post/go/) | [Next: sync.Mutex Is Often Simpler](/post/go/go-idioms-mutex-simpler) →
