---
title: "Lesson 14: Channels — How goroutines talk to each other"
author: "Atharva Pandey"
keywords:
  - "Go"
  - "golang"
  - "channels"
  - "goroutines"
  - "buffered channels"
  - "unbuffered channels"
  - "deadlock"
  - "Go from Scratch"
tags:
  - "Go tutorial"
  - "golang"
  - "beginner"
series: "Go from Scratch"
lesson: 14
date: "2024-03-18"
---

In the last lesson, I showed you how to run code concurrently with goroutines. But goroutines that can't talk to each other aren't very useful. What if one goroutine produces a result that another needs? What if you want to signal that work is done without using a `WaitGroup`? That's where channels come in.

Go's guiding philosophy on concurrency is: "don't communicate by sharing memory; share memory by communicating." Channels are the mechanism for that. Instead of multiple goroutines reading and writing the same variable, they pass values through a channel — and the channel ensures only one goroutine handles the value at a time.

## The Basics

A channel is a typed conduit. You create one with `make`, specifying the type of values it will carry:

```go
ch := make(chan int) // a channel that carries ints
```

You send a value into a channel with `<-`:

```go
ch <- 42 // send 42 into ch
```

You receive a value from a channel with `<-` on the other side:

```go
v := <-ch // receive from ch, store in v
```

Here's a complete example:

```go
package main

import "fmt"

func double(n int, ch chan int) {
    ch <- n * 2 // send the result back through the channel
}

func main() {
    ch := make(chan int)
    go double(5, ch)
    result := <-ch // wait here until the goroutine sends a value
    fmt.Println(result) // 10
}
```

The `<-ch` on the main goroutine blocks until a value arrives. This is how channels synchronise goroutines — the receiver waits for the sender, and the sender waits for a receiver.

**Unbuffered vs buffered channels**

The channel we just made is **unbuffered**. A send on an unbuffered channel blocks until another goroutine is ready to receive. A receive blocks until a sender sends. They meet in the middle.

A **buffered** channel has a queue. The sender can place up to N values without waiting for a receiver. You create one by passing a capacity:

```go
ch := make(chan string, 3) // can hold up to 3 strings before blocking
ch <- "first"
ch <- "second"
ch <- "third"
// sending a 4th would block here until someone receives
```

Use unbuffered channels when you want goroutines to synchronise — when you care that the receiver got the message before the sender moves on. Use buffered channels when you want the sender to be able to keep working without waiting.

**Closing channels and ranging over them**

When a sender is done sending, it can close the channel:

```go
close(ch)
```

A receiver can check if a channel is closed:

```go
v, ok := <-ch
if !ok {
    fmt.Println("channel closed")
}
```

More commonly, you range over a channel to receive all values until it's closed:

```go
package main

import "fmt"

func generate(ch chan int) {
    for i := 0; i < 5; i++ {
        ch <- i
    }
    close(ch) // tell receivers we're done
}

func main() {
    ch := make(chan int)
    go generate(ch)

    for v := range ch { // loops until ch is closed
        fmt.Println(v)
    }
}
```

Output: 0, 1, 2, 3, 4. The `range` loop exits automatically when `generate` closes the channel.

**A real pipeline**

Here's a pattern you'll see often — one goroutine produces values, another consumes them:

```go
package main

import "fmt"

func producer(out chan<- int) { // chan<- means send-only
    for i := 1; i <= 5; i++ {
        out <- i * i // squares: 1, 4, 9, 16, 25
    }
    close(out)
}

func consumer(in <-chan int) { // <-chan means receive-only
    for v := range in {
        fmt.Println("received:", v)
    }
}

func main() {
    ch := make(chan int)
    go producer(ch)
    consumer(ch) // runs in main goroutine, blocks until producer closes ch
}
```

The `chan<-` and `<-chan` type annotations are optional but make the code self-documenting. They also let the compiler catch mistakes like accidentally sending on a receive-only channel.

## Try It Yourself

Build a simple pipeline that squares numbers and then prints only the even results:

```go
package main

import "fmt"

func square(in <-chan int, out chan<- int) {
    for v := range in {
        out <- v * v
    }
    close(out)
}

func main() {
    nums := make(chan int)
    squares := make(chan int)

    go func() {
        for i := 1; i <= 10; i++ {
            nums <- i
        }
        close(nums)
    }()

    go square(nums, squares)

    for v := range squares {
        if v%2 == 0 {
            fmt.Println(v) // prints 4, 16, 36, 64, 100
        }
    }
}
```

Run it. Then try adding a third stage that multiplies each value by 10.

## Common Mistakes

**Deadlock from an unbuffered channel with no receiver.** This hangs forever:

```go
ch := make(chan int)
ch <- 42 // blocks forever — nobody is receiving
```

Go's runtime detects this and prints `all goroutines are asleep - deadlock!`. If you see that message, look for a send with no corresponding receive.

**Closing a channel twice.** This panics immediately. Only the sender should close a channel, and only once:

```go
close(ch)
close(ch) // panic: close of closed channel
```

**Sending on a closed channel.** Also panics. Once a channel is closed, you can still receive remaining values from it, but you cannot send to it.

**Using a nil channel.** A channel declared but not initialised with `make` is `nil`. Sends and receives on a nil channel block forever. Always use `make`:

```go
var ch chan int   // nil — don't use this
ch := make(chan int) // correct
```

## Key Takeaway

Channels let goroutines communicate safely without sharing memory. Unbuffered channels synchronise sender and receiver — both must be ready at the same time. Buffered channels give the sender room to work ahead. Always close channels from the sender side, and use `range` to receive until a channel closes. In the next lesson, we'll look at `select`, which lets you wait on multiple channels at once.

---

**Series: Go from Scratch**

- Previous: [Lesson 13: Goroutines](/post/go/go-scratch-goroutines)
- Next: [Lesson 15: Select](/post/go/go-scratch-select)
