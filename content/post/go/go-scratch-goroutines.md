---
title: "Lesson 13: Goroutines — Lightweight threads that cost almost nothing"
author: "Atharva Pandey"
keywords:
  - "Go"
  - "golang"
  - "goroutines"
  - "concurrency"
  - "sync.WaitGroup"
  - "go keyword"
  - "Go from Scratch"
tags:
  - "Go tutorial"
  - "golang"
  - "beginner"
series: "Go from Scratch"
lesson: 13
date: "2024-03-12"
---

One of the first things people told me about Go was "it makes concurrency easy." I was sceptical — concurrency is never easy. But the more I used goroutines, the more I understood what they meant. It's not that concurrency becomes simple. It's that the cost of starting a concurrent task drops so low that you stop avoiding it.

In most languages, spawning a thread involves megabytes of stack memory and significant overhead. A Go goroutine starts with about 2KB of stack. You can run tens of thousands of them on a normal laptop without breaking a sweat.

## The Basics

A goroutine is a function running concurrently with the rest of your program. You start one by putting the `go` keyword in front of a function call.

```go
package main

import (
    "fmt"
    "time"
)

func sayHello() {
    fmt.Println("hello from goroutine")
}

func main() {
    go sayHello()
    time.Sleep(100 * time.Millisecond) // wait a bit so the goroutine can run
    fmt.Println("hello from main")
}
```

The `go sayHello()` line starts `sayHello` as a goroutine and returns immediately. The main function continues while `sayHello` runs concurrently in the background.

**The main goroutine problem**

There's one critical thing to understand: when the `main` goroutine exits, the entire program exits — no matter what other goroutines are still running.

```go
func main() {
    go fmt.Println("I might never print")
    // main ends here immediately, before the goroutine gets a chance to run
}
```

This is why I used `time.Sleep` in the first example. It's a hack, though — you should never sleep just to wait for goroutines. There are proper tools for that.

**sync.WaitGroup**

`sync.WaitGroup` is the right way to wait for a group of goroutines to finish. You tell it how many goroutines you're launching, each goroutine calls `Done()` when it finishes, and you call `Wait()` to block until the count reaches zero.

```go
package main

import (
    "fmt"
    "sync"
)

func worker(id int, wg *sync.WaitGroup) {
    defer wg.Done() // marks this goroutine as finished when the function returns
    fmt.Printf("worker %d starting\n", id)
    // simulate some work
    fmt.Printf("worker %d done\n", id)
}

func main() {
    var wg sync.WaitGroup

    for i := 1; i <= 3; i++ {
        wg.Add(1) // tell the WaitGroup to expect one more goroutine
        go worker(i, &wg)
    }

    wg.Wait() // block until all workers call Done()
    fmt.Println("all workers finished")
}
```

A few things to note here:
- `wg.Add(1)` goes before the `go` call, not inside the goroutine. If you put it inside, there's a race: the goroutine might not add to the count before `Wait` is called.
- We pass `&wg` (a pointer). If you pass `wg` by value, each goroutine gets its own copy and `Done` never decrements the original.
- `defer wg.Done()` ensures `Done` is called even if the function panics or returns early.

**Running things in parallel**

Here's a more realistic example: fetching multiple URLs concurrently.

```go
package main

import (
    "fmt"
    "sync"
    "time"
)

func fetch(url string, wg *sync.WaitGroup) {
    defer wg.Done()
    // pretend this is an HTTP request
    time.Sleep(100 * time.Millisecond)
    fmt.Printf("fetched: %s\n", url)
}

func main() {
    urls := []string{
        "https://example.com",
        "https://golang.org",
        "https://github.com",
    }

    var wg sync.WaitGroup
    for _, url := range urls {
        wg.Add(1)
        go fetch(url, &wg)
    }
    wg.Wait()
    fmt.Println("all fetches complete")
}
```

Without goroutines, fetching three URLs sequentially would take 300ms. With goroutines, all three start at the same time and the whole thing takes roughly 100ms.

## Try It Yourself

Write a program that counts from 1 to 5 in three separate goroutines, each with a different label:

```go
package main

import (
    "fmt"
    "sync"
)

func counter(label string, wg *sync.WaitGroup) {
    defer wg.Done()
    for i := 1; i <= 5; i++ {
        fmt.Printf("%s: %d\n", label, i)
    }
}

func main() {
    var wg sync.WaitGroup

    labels := []string{"A", "B", "C"}
    for _, label := range labels {
        wg.Add(1)
        go counter(label, &wg)
    }

    wg.Wait()
    fmt.Println("done")
}
```

Run it several times. Notice the output order changes each run. That's concurrency — the goroutines run in whatever order the scheduler decides, and it's different every time.

## Common Mistakes

**Forgetting that main exiting kills everything.** This is the most common mistake I see from beginners:

```go
func main() {
    go doWork() // starts a goroutine
    // program ends immediately, doWork never completes
}
```

Always use `sync.WaitGroup` or a channel (more on channels next lesson) to wait for goroutines.

**Capturing a loop variable by reference.** This is a classic bug:

```go
// WRONG
for i := 0; i < 3; i++ {
    go func() {
        fmt.Println(i) // all goroutines share the same i
    }()
}
// likely prints 3, 3, 3
```

```go
// CORRECT — pass i as an argument
for i := 0; i < 3; i++ {
    go func(n int) {
        fmt.Println(n)
    }(i)
}
```

Since Go 1.22, the loop variable behaviour changed so each iteration gets its own copy, but many codebases still run older versions. Passing `i` as an argument is always explicit and safe.

**Passing WaitGroup by value.** Always pass `*sync.WaitGroup`, never `sync.WaitGroup`. A value copy means `Done()` decrements a different counter than the one `Wait()` is watching.

## Key Takeaway

A goroutine is just a function with `go` in front of it. The runtime handles the scheduling, and each goroutine starts with only ~2KB of stack — so you can have thousands running at once. The key rule is simple: if you start goroutines, you are responsible for waiting for them before main exits. `sync.WaitGroup` is the cleanest tool for that. In the next lesson, we'll look at channels — how goroutines communicate with each other.

---

**Series: Go from Scratch**

- Previous: [Lesson 12: Error Handling](/post/go/go-scratch-errors)
- Next: [Lesson 14: Channels](/post/go/go-scratch-channels)
