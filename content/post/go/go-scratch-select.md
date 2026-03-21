---
title: "Lesson 15: Select — Waiting on multiple channels at once"
author: "Atharva Pandey"
keywords:
  - "Go"
  - "golang"
  - "select"
  - "channels"
  - "timeout"
  - "time.After"
  - "context"
  - "for-select pattern"
  - "Go from Scratch"
tags:
  - "Go tutorial"
  - "golang"
  - "beginner"
series: "Go from Scratch"
lesson: 15
date: "2024-03-22"
---

By now you know how to create goroutines and how to communicate between them using channels. But what happens when a goroutine needs to listen to two channels at the same time? Maybe it's waiting for work to arrive, but it also needs to stop if a timeout fires. With a single channel you'd be stuck — receiving from one blocks the other.

`select` solves this. It's like a `switch` statement for channels. It waits until one of several channel operations is ready, then executes that case. If multiple are ready at the same time, it picks one at random.

## The Basics

Here's the simplest `select`:

```go
package main

import "fmt"

func main() {
    ch1 := make(chan string)
    ch2 := make(chan string)

    go func() { ch1 <- "from ch1" }()
    go func() { ch2 <- "from ch2" }()

    // wait for whichever channel sends first
    select {
    case msg := <-ch1:
        fmt.Println("received:", msg)
    case msg := <-ch2:
        fmt.Println("received:", msg)
    }
}
```

Both goroutines send at roughly the same time. `select` picks one randomly. Run it a few times — you'll see both outputs eventually.

**Timeouts with time.After**

One of the most useful patterns in Go is using `select` to impose a timeout. `time.After(d)` returns a channel that receives a value after duration `d`. Put that in a `select` case and you have a timeout:

```go
package main

import (
    "fmt"
    "time"
)

func slowOperation(ch chan string) {
    time.Sleep(2 * time.Second)
    ch <- "result"
}

func main() {
    ch := make(chan string)
    go slowOperation(ch)

    select {
    case result := <-ch:
        fmt.Println("got result:", result)
    case <-time.After(1 * time.Second):
        fmt.Println("timed out — operation took too long")
    }
}
```

Because the operation takes 2 seconds but the timeout fires after 1 second, you'll always see "timed out." Change the sleep to 500ms and the result arrives first. This pattern is everywhere in real Go code — HTTP clients, database calls, any operation that shouldn't run forever.

**The default case**

A `select` with a `default` case never blocks. If no channel is ready, it runs `default` immediately:

```go
ch := make(chan int, 1)

select {
case v := <-ch:
    fmt.Println("received:", v)
default:
    fmt.Println("nothing ready, moving on")
}
```

This is useful for non-blocking channel checks — "give me a value if one is ready, otherwise keep going." Be careful not to use `default` when you actually want to wait; it turns your blocking wait into a busy loop.

**Using select with context.Done()**

The `context` package is how Go programs signal cancellation. A `context.Context` has a `Done()` method that returns a channel. When the context is cancelled, that channel closes. `select` lets you react to it:

```go
package main

import (
    "context"
    "fmt"
    "time"
)

func doWork(ctx context.Context, ch chan string) {
    for {
        select {
        case <-ctx.Done():
            fmt.Println("work cancelled:", ctx.Err())
            return
        case ch <- "working":
            time.Sleep(300 * time.Millisecond)
        }
    }
}

func main() {
    ctx, cancel := context.WithTimeout(context.Background(), 1*time.Second)
    defer cancel()

    ch := make(chan string, 1)
    go doWork(ctx, ch)

    for {
        select {
        case msg := <-ch:
            fmt.Println(msg)
        case <-ctx.Done():
            fmt.Println("main: context done")
            return
        }
    }
}
```

The goroutine keeps sending "working" every 300ms. After 1 second, the context times out, both the goroutine and main detect `ctx.Done()`, and the program exits cleanly.

**The for-select pattern**

In real programs, goroutines rarely do one thing and stop. They loop continuously, handling incoming events until told to stop. The `for-select` pattern combines a `for` loop with a `select` to create a goroutine that runs until cancelled:

```go
func worker(jobs <-chan string, done <-chan struct{}) {
    for {
        select {
        case job := <-jobs:
            fmt.Println("processing:", job)
        case <-done:
            fmt.Println("worker shutting down")
            return
        }
    }
}

func main() {
    jobs := make(chan string, 5)
    done := make(chan struct{})

    go worker(jobs, done)

    jobs <- "task 1"
    jobs <- "task 2"
    jobs <- "task 3"

    time.Sleep(100 * time.Millisecond)
    close(done) // signal the worker to stop

    time.Sleep(100 * time.Millisecond) // let it print the shutdown message
}
```

The `done` channel carries `struct{}` — an empty struct. It carries zero bytes. It's the conventional way to signal "stop" through a channel when you don't need to send any data, only a signal.

## Try It Yourself

Write a program that fans out to two workers and uses a timeout to stop everything if both are too slow:

```go
package main

import (
    "fmt"
    "time"
)

func worker(id int, out chan<- string) {
    delay := time.Duration(id*400) * time.Millisecond
    time.Sleep(delay)
    out <- fmt.Sprintf("worker %d done", id)
}

func main() {
    ch := make(chan string, 2)

    go worker(1, ch) // finishes in 400ms
    go worker(2, ch) // finishes in 800ms

    timeout := time.After(600 * time.Millisecond)

    for i := 0; i < 2; i++ {
        select {
        case msg := <-ch:
            fmt.Println(msg)
        case <-timeout:
            fmt.Println("timed out waiting for remaining workers")
            return
        }
    }
}
```

Worker 1 reports in at 400ms. The timeout fires at 600ms before worker 2 finishes. Try changing the delays to see different outcomes.

## Common Mistakes

**Using select with only one case.** If you have only one channel, you don't need `select`. Just receive from the channel directly. `select` is for choosing between multiple channels.

**Forgetting that select chooses randomly when multiple cases are ready.** If both `ch1` and `ch2` have values waiting, `select` doesn't guarantee which one runs first. Don't write code that depends on a specific ordering.

**Busy-looping with default.** This will max out a CPU core:

```go
// WRONG — loops millions of times per second burning CPU
for {
    select {
    case v := <-ch:
        process(v)
    default:
        // nothing ready, try again immediately
    }
}
```

If you're waiting for work, block. Remove the `default` case, or add a small sleep, or use `context.Done()` as your exit condition.

**Not handling context cancellation in a for-select.** If your goroutine loops with `for { select { ... } }` and you don't have a `case <-ctx.Done()` or `case <-done`, the goroutine will run forever after the rest of the program has moved on. Always give your goroutines a way to stop.

## Key Takeaway

`select` is how you write goroutines that respond to multiple things at once — incoming work, timeouts, cancellation signals. The `for-select` pattern is the foundation of nearly every long-running goroutine you'll write in production Go. Keep your cases simple, always include a way to stop, and use `time.After` or `context.WithTimeout` when an operation must not run forever.

---

**Series: Go from Scratch**

- Previous: [Lesson 14: Channels](/post/go/go-scratch-channels)
- Next: [Lesson 16: Packages](/post/go/go-scratch-packages)
