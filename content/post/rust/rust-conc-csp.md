---
title: "Lesson 21: CSP-Style Concurrency — Go channels in Rust"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 21
keywords: [Rust, rust, concurrency, CSP, communicating sequential processes, Go channels, select]
tags: [Rust tutorial, rust, concurrency]
date: '2024-12-15T10:40:00.000Z'
---

Before I wrote Rust full-time, I spent two years writing Go. The goroutine-plus-channel model gets into your brain. You start thinking about problems as independent processes connected by typed pipes. When I switched to Rust, the first thing I looked for was the equivalent of Go's `select` statement. Crossbeam has it — and in some ways it's even better.

CSP (Communicating Sequential Processes) is the formal model behind Go's concurrency. The idea: concurrent processes interact only by passing messages through channels. No shared memory. Each process is sequential internally. Concurrency comes from composition.

## CSP vs Actor Model

They look similar but differ in a key way:

| CSP | Actor Model |
|-----|------------|
| Channels are first-class | Actors (processes) are first-class |
| Anonymous endpoints | Named actors with addresses |
| Synchronous by default | Asynchronous by default |
| Processes know about channels | Actors have mailboxes |

In Go, you pass channels around. In Erlang, you send messages to process IDs. The practical difference: CSP emphasizes the *connections* between processes, while actors emphasize the *processes* themselves.

## Go-Style Concurrency in Rust

Let's translate common Go patterns to Rust using crossbeam channels.

### The Basic goroutine + channel

Go:
```go
ch := make(chan string)
go func() {
    ch <- "hello"
}()
msg := <-ch
```

Rust:
```rust
use crossbeam_channel::unbounded;
use std::thread;

fn main() {
    let (tx, rx) = unbounded();

    thread::spawn(move || {
        tx.send("hello").unwrap();
    });

    let msg = rx.recv().unwrap();
    println!("{}", msg);
}
```

Nearly identical. Rust's version is slightly more verbose because of explicit error handling and `move`.

### Select

Go's `select` waits on multiple channels:

```go
select {
case msg := <-ch1:
    fmt.Println("ch1:", msg)
case msg := <-ch2:
    fmt.Println("ch2:", msg)
case <-time.After(time.Second):
    fmt.Println("timeout")
}
```

Rust with crossbeam:

```rust
use crossbeam_channel::{bounded, select, after};
use std::thread;
use std::time::Duration;

fn main() {
    let (tx1, rx1) = bounded(1);
    let (tx2, rx2) = bounded(1);

    thread::spawn(move || {
        thread::sleep(Duration::from_millis(500));
        tx1.send("from channel 1").unwrap();
    });

    thread::spawn(move || {
        thread::sleep(Duration::from_millis(300));
        tx2.send("from channel 2").unwrap();
    });

    let timeout = after(Duration::from_secs(1));

    select! {
        recv(rx1) -> msg => println!("ch1: {:?}", msg),
        recv(rx2) -> msg => println!("ch2: {:?}", msg),
        recv(timeout) -> _ => println!("timeout"),
    }
}
```

### Fan-Out with Select

Distributing work across multiple workers and collecting results:

```rust
use crossbeam_channel::{bounded, select, Receiver};
use std::thread;

fn worker(id: usize, rx: Receiver<i32>) -> Receiver<(usize, i32)> {
    let (tx, result_rx) = bounded(10);
    thread::spawn(move || {
        while let Ok(n) = rx.recv() {
            let result = n * n; // "work"
            let _ = tx.send((id, result));
        }
    });
    result_rx
}

fn main() {
    let (work_tx, work_rx) = bounded(10);

    // Fan out to 4 workers
    let results: Vec<Receiver<(usize, i32)>> = (0..4)
        .map(|id| worker(id, work_rx.clone()))
        .collect();
    drop(work_rx); // close original

    // Send work
    thread::spawn(move || {
        for i in 0..20 {
            work_tx.send(i).unwrap();
        }
    });

    // Merge results from all workers using select
    let mut done_count = 0;
    let mut total_results = 0;

    loop {
        let mut sel = crossbeam_channel::Select::new();
        for rx in &results {
            sel.recv(rx);
        }

        let oper = sel.select();
        let idx = oper.index();
        match oper.recv(&results[idx]) {
            Ok((worker_id, result)) => {
                println!("Worker {} produced {}", worker_id, result);
                total_results += 1;
            }
            Err(_) => {
                done_count += 1;
                if done_count >= results.len() {
                    break;
                }
            }
        }

        if total_results >= 20 {
            break;
        }
    }

    println!("Got {} results", total_results);
}
```

### Pipeline (like Go's pipeline pattern)

```rust
use crossbeam_channel::{bounded, Receiver, Sender};
use std::thread;

fn generate(nums: Vec<i32>) -> Receiver<i32> {
    let (tx, rx) = bounded(10);
    thread::spawn(move || {
        for n in nums {
            tx.send(n).unwrap();
        }
    });
    rx
}

fn square(input: Receiver<i32>) -> Receiver<i32> {
    let (tx, rx) = bounded(10);
    thread::spawn(move || {
        while let Ok(n) = input.recv() {
            tx.send(n * n).unwrap();
        }
    });
    rx
}

fn filter_even(input: Receiver<i32>) -> Receiver<i32> {
    let (tx, rx) = bounded(10);
    thread::spawn(move || {
        while let Ok(n) = input.recv() {
            if n % 2 == 0 {
                tx.send(n).unwrap();
            }
        }
    });
    rx
}

fn main() {
    // Compose the pipeline: generate → square → filter
    let nums = (1..=20).collect();
    let pipeline = filter_even(square(generate(nums)));

    // Consume
    let mut results = vec![];
    while let Ok(n) = pipeline.recv() {
        results.push(n);
    }

    println!("Even squares: {:?}", results);
}
```

This is pure CSP — each stage is a sequential process, connected by channels. The pipeline composes naturally. Adding a stage means wrapping one more function call.

### Done Channel (Cancellation)

Go uses a "done" channel for cancellation:

```go
done := make(chan struct{})
go func() {
    for {
        select {
        case <-done:
            return
        default:
            // do work
        }
    }
}()
close(done) // cancel
```

Rust equivalent:

```rust
use crossbeam_channel::{bounded, select, never, Receiver};
use std::thread;
use std::time::Duration;

fn cancellable_worker(done: Receiver<()>) -> thread::JoinHandle<u64> {
    thread::spawn(move || {
        let mut count = 0u64;
        let ticker = crossbeam_channel::tick(Duration::from_millis(10));

        loop {
            select! {
                recv(done) -> _ => {
                    println!("Worker cancelled after {} iterations", count);
                    return count;
                }
                recv(ticker) -> _ => {
                    // do periodic work
                    count += 1;
                }
            }
        }
    })
}

fn main() {
    let (cancel_tx, cancel_rx) = bounded(0); // synchronous — close to signal

    let worker = cancellable_worker(cancel_rx);

    thread::sleep(Duration::from_secs(2));
    drop(cancel_tx); // "close" the channel — signals cancellation

    let count = worker.join().unwrap();
    println!("Worker did {} iterations", count);
}
```

Dropping the sender closes the channel, which causes `recv` to return `Err`. In the `select!`, this triggers the cancellation branch. Same pattern as Go's `close(done)`.

### Or-Done Channel

Wait for the first of several long-running operations:

```rust
use crossbeam_channel::{bounded, select};
use std::thread;
use std::time::Duration;

fn fetch_from_service(name: &str, delay_ms: u64) -> crossbeam_channel::Receiver<String> {
    let (tx, rx) = bounded(1);
    let name = name.to_string();
    thread::spawn(move || {
        thread::sleep(Duration::from_millis(delay_ms));
        let _ = tx.send(format!("{} responded", name));
    });
    rx
}

fn main() {
    let svc_a = fetch_from_service("service-a", 500);
    let svc_b = fetch_from_service("service-b", 200);
    let svc_c = fetch_from_service("service-c", 800);
    let timeout = crossbeam_channel::after(Duration::from_secs(1));

    // First response wins
    select! {
        recv(svc_a) -> msg => println!("Got: {:?}", msg.unwrap()),
        recv(svc_b) -> msg => println!("Got: {:?}", msg.unwrap()),
        recv(svc_c) -> msg => println!("Got: {:?}", msg.unwrap()),
        recv(timeout) -> _ => println!("All services timed out"),
    }
}
```

This is the "hedged request" pattern — fire off multiple requests, take the first response. Common in distributed systems to reduce tail latency.

## Where Rust's CSP Differs from Go

**Compile-time safety.** Go channels can carry `interface{}` (any type). Rust channels are strongly typed. You can't accidentally send the wrong type.

**Ownership transfer.** When you `send` a value in Rust, you *move* it. The sender can't use it afterward. In Go, you might send a pointer and keep using it — data race city.

**No goroutine-style lightweight threading.** Go's goroutines are green threads multiplexed onto OS threads. Rust's `thread::spawn` creates OS threads. For thousands of concurrent operations, Rust needs async (tokio, etc.) while Go handles it natively.

**Crossbeam select vs Go select.** Crossbeam's `select!` is a macro that expands to efficient polling code. Go's `select` is a language-level construct. Functionally equivalent, but Rust's doesn't support sending in select as cleanly.

## Sending in Select

Go allows sending in select:

```go
select {
case ch <- value:
    // sent
case msg := <-other:
    // received
}
```

Crossbeam supports this too:

```rust
use crossbeam_channel::{bounded, select};

fn main() {
    let (tx, rx) = bounded(0); // synchronous channel

    // Try to send or receive — whichever is ready first
    let other_val = 42;
    select! {
        send(tx, "hello") -> res => {
            println!("Sent: {:?}", res);
        }
        default => {
            println!("Nobody ready to receive");
        }
    }
}
```

The `default` branch fires if no channel is ready — equivalent to Go's `default` case in select.

## When to Use CSP-Style in Rust

CSP shines when your problem naturally decomposes into:
- Independent processes with well-defined interfaces
- Pipelines where data flows through stages
- Event loops that react to multiple sources
- Services that need cancellation and timeouts

For pure computation (crunching numbers), Rayon's data parallelism is better. For complex stateful components, the actor model provides more structure. CSP lives in the middle — structured enough to be maintainable, flexible enough for most concurrent designs.

---

Next — SIMD, where we go beyond threads and into single-instruction-multiple-data parallelism.
