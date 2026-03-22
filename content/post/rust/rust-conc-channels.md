---
title: "Lesson 4: Channels — mpsc and beyond"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 4
keywords: [Rust, rust, concurrency, channels, mpsc, message passing, producer consumer]
tags: [Rust tutorial, rust, concurrency]
date: '2024-11-11T09:10:00.000Z'
---

The first concurrent system I built that actually worked well was a log aggregation pipeline. Multiple producers writing log lines, one consumer batching and flushing to disk. No shared state, no locks, no races. Just messages flowing through a pipe.

That experience sold me on message passing. And Rust's channel implementation makes it surprisingly ergonomic.

## The Problem: Shared State Is Hard

You *can* share state between threads with mutexes. But every shared mutable variable is a coordination point, a potential bottleneck, and a source of bugs. The more threads touching the same data, the harder the code is to reason about.

Message passing flips the model. Instead of threads reaching into shared data, threads send data to each other. "Don't communicate by sharing memory; share memory by communicating." — Go proverb, but Rust executes it better because ownership transfer is enforced by the compiler.

## std::sync::mpsc

Rust's standard library provides `mpsc` — **m**ultiple **p**roducer, **s**ingle **c**onsumer. You get a `Sender` and a `Receiver`:

```rust
use std::sync::mpsc;
use std::thread;

fn main() {
    let (tx, rx) = mpsc::channel();

    thread::spawn(move || {
        tx.send("hello from the thread").unwrap();
    });

    let message = rx.recv().unwrap();
    println!("Got: {}", message);
}
```

`send()` puts a value into the channel. `recv()` blocks until a value arrives. Ownership of the value transfers from sender to receiver — the sending thread can't use it after calling `send()`.

This is Rust being Rust. The ownership system guarantees that once you send a value, you don't have a copy of it sitting around. No accidental aliasing.

## Sending Multiple Values

Channels work as streams. The receiver can iterate:

```rust
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

fn main() {
    let (tx, rx) = mpsc::channel();

    thread::spawn(move || {
        let messages = vec![
            String::from("one"),
            String::from("two"),
            String::from("three"),
        ];

        for msg in messages {
            tx.send(msg).unwrap();
            thread::sleep(Duration::from_millis(200));
        }
        // tx is dropped here — channel closes
    });

    // rx implements Iterator — this loop ends when the channel closes
    for received in rx {
        println!("Got: {}", received);
    }
    println!("Channel closed, done.");
}
```

When all `Sender` instances are dropped, the channel closes. The `for` loop on the receiver naturally terminates. Clean, predictable lifecycle.

## Multiple Producers

The "mp" in `mpsc` means you can clone the sender:

```rust
use std::sync::mpsc;
use std::thread;

fn main() {
    let (tx, rx) = mpsc::channel();
    let mut handles = vec![];

    for i in 0..5 {
        let tx = tx.clone();
        handles.push(thread::spawn(move || {
            let msg = format!("Message from thread {}", i);
            tx.send(msg).unwrap();
        }));
    }

    // IMPORTANT: drop the original tx
    // Otherwise the channel never closes because this tx is still alive
    drop(tx);

    for msg in rx {
        println!("{}", msg);
    }

    for h in handles {
        h.join().unwrap();
    }
}
```

That `drop(tx)` is a classic gotcha. You clone `tx` for each thread, but the *original* `tx` still exists in `main`. If you don't drop it, the channel never closes, and the `for msg in rx` loop hangs forever.

I've seen this bug stall production pipelines. Always drop the original sender after cloning.

## Bounded vs Unbounded Channels

`mpsc::channel()` creates an unbounded (asynchronous) channel — producers can send as fast as they want, and messages queue up without limit. This can eat all your memory if the consumer is slower than the producers.

`mpsc::sync_channel(n)` creates a bounded (synchronous) channel with a buffer of size `n`:

```rust
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

fn main() {
    // Buffer size of 2 — blocks when buffer is full
    let (tx, rx) = mpsc::sync_channel(2);

    thread::spawn(move || {
        for i in 0..10 {
            println!("Sending {}", i);
            tx.send(i).unwrap(); // blocks when buffer is full
            println!("Sent {}", i);
        }
    });

    for val in rx {
        println!("Received {}", val);
        thread::sleep(Duration::from_millis(500)); // slow consumer
    }
}
```

With a buffer of 2, the producer can get 2 messages ahead of the consumer. After that, `send()` blocks until the consumer catches up. This is *backpressure* — the producer naturally slows down to match the consumer's pace.

Use bounded channels in production. Unbounded channels are a memory leak waiting to happen.

## Error Handling

Channel operations can fail:

```rust
use std::sync::mpsc;

fn main() {
    let (tx, rx) = mpsc::channel::<i32>();

    // Sending to a closed channel (receiver dropped)
    drop(rx);
    match tx.send(42) {
        Ok(()) => println!("sent"),
        Err(e) => println!("Send failed: {} — receiver is gone", e),
    }

    // Receiving from a closed channel (sender dropped)
    let (tx2, rx2) = mpsc::channel::<i32>();
    drop(tx2);
    match rx2.recv() {
        Ok(val) => println!("got {}", val),
        Err(e) => println!("Recv failed: {} — channel closed", e),
    }
}
```

`send()` returns `Err(SendError(value))` when the receiver is dropped — you get the value back. `recv()` returns `Err(RecvError)` when all senders are dropped and the channel is empty.

These errors tell you about the lifecycle of the other end. They're not failures — they're signals.

## try_recv and recv_timeout

Sometimes you don't want to block:

```rust
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

fn main() {
    let (tx, rx) = mpsc::channel();

    thread::spawn(move || {
        thread::sleep(Duration::from_secs(1));
        tx.send("delayed message").unwrap();
    });

    // Non-blocking: returns immediately
    match rx.try_recv() {
        Ok(msg) => println!("Got: {}", msg),
        Err(mpsc::TryRecvError::Empty) => println!("Nothing yet"),
        Err(mpsc::TryRecvError::Disconnected) => println!("Channel closed"),
    }

    // Blocking with timeout
    match rx.recv_timeout(Duration::from_secs(2)) {
        Ok(msg) => println!("Got: {}", msg),
        Err(mpsc::RecvTimeoutError::Timeout) => println!("Timed out"),
        Err(mpsc::RecvTimeoutError::Disconnected) => println!("Channel closed"),
    }
}
```

`try_recv` is useful in event loops where you're polling multiple sources. `recv_timeout` is essential for any system that needs to react within a deadline.

## Building a Pipeline

Here's a real pattern — a three-stage pipeline:

```rust
use std::sync::mpsc;
use std::thread;

fn main() {
    // Stage 1: Generate data
    let (tx1, rx1) = mpsc::channel();
    // Stage 2: Transform
    let (tx2, rx2) = mpsc::channel();

    // Producer
    let producer = thread::spawn(move || {
        for i in 0..20 {
            tx1.send(i).unwrap();
        }
    });

    // Transformer — squares each number
    let transformer = thread::spawn(move || {
        for val in rx1 {
            tx2.send(val * val).unwrap();
        }
    });

    // Consumer — collects results
    let consumer = thread::spawn(move || {
        let mut results = vec![];
        for val in rx2 {
            results.push(val);
        }
        results
    });

    producer.join().unwrap();
    transformer.join().unwrap();
    let results = consumer.join().unwrap();
    println!("Pipeline output: {:?}", results);
}
```

Each stage owns its piece of the pipeline. Data flows through cleanly. Adding a new transformation stage means adding one more channel pair and one more thread. Composable and easy to reason about.

## Typed Channels with Enums

Channels are typed — `mpsc::channel::<String>()` only carries strings. But sometimes you need to send different kinds of messages. Use an enum:

```rust
use std::sync::mpsc;
use std::thread;

enum Command {
    Process(String),
    Shutdown,
}

fn main() {
    let (tx, rx) = mpsc::channel();

    let worker = thread::spawn(move || {
        loop {
            match rx.recv() {
                Ok(Command::Process(data)) => {
                    println!("Processing: {}", data);
                }
                Ok(Command::Shutdown) => {
                    println!("Shutting down");
                    break;
                }
                Err(_) => {
                    println!("Channel closed unexpectedly");
                    break;
                }
            }
        }
    });

    tx.send(Command::Process("task-1".into())).unwrap();
    tx.send(Command::Process("task-2".into())).unwrap();
    tx.send(Command::Shutdown).unwrap();

    worker.join().unwrap();
}
```

This is the actor model in miniature — a thread that receives typed messages and responds to them. We'll build a full actor system later in the course.

## The Limitations of mpsc

Rust's `mpsc` is solid for basic use but has some rough edges:

1. **Single consumer only** — If you need multiple consumers (work distribution), you need something else
2. **No select** — You can't wait on multiple channels simultaneously with std
3. **Performance** — It's fine, but not the fastest option (crossbeam has better channels)

For multi-consumer scenarios, you'd either use a `Mutex<Receiver>` (ugly but works) or reach for crossbeam's channels, which we'll cover later.

```rust
// Multi-consumer with Mutex — works but not ideal
use std::sync::{mpsc, Arc, Mutex};
use std::thread;

fn main() {
    let (tx, rx) = mpsc::channel();
    let rx = Arc::new(Mutex::new(rx));
    let mut handles = vec![];

    for id in 0..4 {
        let rx = Arc::clone(&rx);
        handles.push(thread::spawn(move || {
            loop {
                let msg = rx.lock().unwrap().recv();
                match msg {
                    Ok(val) => println!("Worker {} got: {}", id, val),
                    Err(_) => break,
                }
            }
        }));
    }

    for i in 0..20 {
        tx.send(i).unwrap();
    }
    drop(tx);

    for h in handles {
        h.join().unwrap();
    }
}
```

## When to Use Channels vs Shared State

This isn't a religious question. Use channels when:

- Data flows in one direction
- You have producer-consumer patterns
- You want decoupled components
- The work is a pipeline

Use shared state (`Mutex`, `RwLock`) when:

- Multiple threads need to read/write the same data structure
- You're implementing a cache
- The data doesn't flow — it's queried and updated in place
- Channels would force awkward request-response patterns

I lean toward channels for new designs. They're easier to reason about and harder to deadlock. But don't force message passing where a shared `HashMap` behind a `Mutex` would be simpler.

---

Next — shared state with `Mutex`, `RwLock`, and the joys of mutex poisoning.
