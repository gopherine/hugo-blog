---
title: 'Lesson 3: Channel Ownership Rules — Who closes the channel?'
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 3
keywords:
  - Go
  - golang
  - concurrency
  - channels
  - channel ownership
  - close channel
  - nil channel
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-04-25T00:00:00.000Z'
---

At some point, you're going to close a channel twice and the runtime is going to panic with `close of closed channel`. Or you're going to close a channel from the wrong goroutine and send a value into it right after, and the runtime is going to panic with `send on closed channel`. These aren't subtle race conditions that only show up under load — they're logic errors that exist because nobody in the codebase agreed on who *owns* the channel. That agreement is the whole game.

## The Problem

Here's the most common version — multiple goroutines sharing a channel with no clear ownership:

```go
// WRONG — both goroutines think they can close the channel
func runWorkers(jobs []Job) {
    results := make(chan Result)

    var wg sync.WaitGroup
    for _, job := range jobs {
        wg.Add(1)
        job := job
        go func() {
            defer wg.Done()
            results <- process(job)
            // If we're the last one, we close. But we don't know who's last.
            // Every goroutine races to close — panic waiting to happen.
        }()
    }

    // Meanwhile, in another goroutine...
    go func() {
        wg.Wait()
        close(results) // might race with a send above
    }()

    for r := range results {
        handleResult(r)
    }
}
```

The close on `results` races with ongoing sends. If even one goroutine hasn't finished sending when `close` fires, you get a `send on closed channel` panic. The `sync.WaitGroup` helps, but the structure is still wrong — the goroutines doing the sending shouldn't be responsible for, or aware of, the channel's closure at all.

Here's another flavor — reading from a nil channel:

```go
// WRONG — nil channel blocks the select forever
type Aggregator struct {
    lowPriority  chan Event
    highPriority chan Event
}

func (a *Aggregator) process() {
    var extras chan Event // never initialized — nil

    for {
        select {
        case e := <-a.highPriority:
            handle(e)
        case e := <-a.lowPriority:
            handle(e)
        case e := <-extras: // blocks forever — steals no CPU but confused intent
            handleExtra(e)
        }
    }
}
```

A nil channel never receives — the `case` is never selected. This is actually *useful* when done deliberately (more on that shortly), but here it's an uninitialized variable that makes the code confusing and hides a bug.

## The Idiomatic Way

The rule is simple enough to fit on a sticky note: **the goroutine that creates a channel is responsible for closing it, and senders should never close a channel they don't own.** The pattern that makes this concrete is: producer owns the channel, producer closes it, consumers just range over it.

```go
// RIGHT — clear ownership: producer creates, sends, closes
func generate(ctx context.Context, jobs []Job) <-chan Result {
    results := make(chan Result, len(jobs)) // or unbuffered, your call
    go func() {
        defer close(results) // owner closes — exactly once, guaranteed
        for _, job := range jobs {
            select {
            case <-ctx.Done():
                return
            default:
                results <- process(job)
            }
        }
    }()
    return results // caller gets read-only view
}

// Consumer doesn't know or care about close — range handles it
func consume(ctx context.Context, jobs []Job) {
    for result := range generate(ctx, jobs) {
        handleResult(result)
    }
}
```

The return type `<-chan Result` — a receive-only channel — is a signal in the type system: "you can read from this, but you don't own it, so don't close it." The compiler enforces it. If you try to `close(results)` from the consumer, it won't compile.

When you have multiple producers sending into the same channel, use a `WaitGroup` inside the owner to know when all producers are done, then close:

```go
// RIGHT — fan-in with clear ownership, single closer
func fanIn(ctx context.Context, producers []<-chan Event) <-chan Event {
    merged := make(chan Event)
    var wg sync.WaitGroup

    forward := func(ch <-chan Event) {
        defer wg.Done()
        for {
            select {
            case v, ok := <-ch:
                if !ok {
                    return
                }
                select {
                case merged <- v:
                case <-ctx.Done():
                    return
                }
            case <-ctx.Done():
                return
            }
        }
    }

    wg.Add(len(producers))
    for _, p := range producers {
        go forward(p)
    }

    // One goroutine waits for all forwarders, then closes merged — single closer
    go func() {
        wg.Wait()
        close(merged)
    }()

    return merged
}
```

Now here's the deliberate nil channel trick — using it to disable a `select` case dynamically:

```go
// RIGHT — nil channel as a way to "turn off" a select case
func drainBoth(primary, secondary <-chan Event) {
    for primary != nil || secondary != nil {
        select {
        case e, ok := <-primary:
            if !ok {
                primary = nil // disable this case — it won't block
                continue
            }
            handle(e)
        case e, ok := <-secondary:
            if !ok {
                secondary = nil
                continue
            }
            handle(e)
        }
    }
}
```

When a channel closes, `ok` is false. Setting the variable to nil disables the `select` case for future iterations. This is one of the more elegant patterns in Go — nil channels aren't a bug, they're a feature when used intentionally.

## In The Wild

We had a data ingestion pipeline where multiple goroutines scraped different data sources and fed into a single processing channel. The original code had every goroutine close the channel when it was done — whoever finished last would close it, whoever didn't finish would panic. We hit this in staging with two goroutines finishing at nearly the same time.

The fix was a classic fan-in with the `WaitGroup`-then-close pattern:

```go
// RIGHT — production fan-in pipeline
func buildPipeline(ctx context.Context, sources []DataSource) <-chan Record {
    out := make(chan Record, 256)

    var wg sync.WaitGroup
    wg.Add(len(sources))

    for _, src := range sources {
        src := src
        go func() {
            defer wg.Done()
            if err := src.Stream(ctx, out); err != nil && !errors.Is(err, context.Canceled) {
                log.Printf("source %s error: %v", src.Name(), err)
            }
        }()
    }

    go func() {
        wg.Wait()
        close(out) // single, unambiguous close
    }()

    return out
}
```

Every `DataSource` implementation sends to `out` but never closes it — that's the contract. The fan-in function is the only code that touches `close(out)`. Six months later, somebody added a seventh data source and it just worked — no close panics, no confusion about ownership.

## The Gotchas

**The comma-ok idiom on a closed channel.** When a channel is closed, receiving from it returns the zero value and `ok = false`. If you're ranging over a channel, this is handled automatically — the range ends. But if you're using a raw receive expression (`v := <-ch`) without checking `ok`, you silently get zero values after close. For most pipelines, range is the right tool. Raw receives are for when you need that `ok` check to do something specific.

**Sending to a closed channel panics — there's no recovery path in production.** This means the "just recover from the panic" approach is wrong. If your architecture requires a sender not to know whether a channel is open, that's a design problem — fix the ownership model, don't catch the panic.

**Buffered channels can hide close panics during testing.** If a buffered channel has room, sends succeed even if a close races — your test passes. In production under load, the buffer fills, and the send that previously succeeded now blocks long enough for the close to win. The test environment lied to you. Design ownership properly and don't rely on buffering to paper over close races.

## Key Takeaway

Channel ownership is a design decision, not an implementation detail — make it explicit before you write a single line of concurrent code. The producer creates the channel, the producer closes it, and consumers just read. Return read-only channel types to signal non-ownership in the type system. Use `WaitGroup`-then-close for fan-in. Use nil channels deliberately to disable `select` cases. If you find yourself asking "who should close this?" mid-implementation, stop and redesign the ownership model — the answer should never be ambiguous.

---

[← Lesson 2: Cancellation with context.Context](/post/go/go-concurrency-context-cancellation) | [Course Index](/series/go-concurrency-masterclass) | [Next → Lesson 4: Buffered vs Unbuffered Channels](/post/go/go-concurrency-buffered-vs-unbuffered)
