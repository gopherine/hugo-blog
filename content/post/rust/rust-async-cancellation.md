---
title: "Lesson 10: Cancellation Safety — The silent footgun"
author: Atharva Pandey
series: "Async Rust Masterclass"
lesson: 10
keywords: [Rust, rust, async, tokio, cancellation, safety, select, drop, footgun]
tags: [Rust tutorial, rust, async, tokio]
date: '2025-01-24T09:41:22.000Z'
---

This is the lesson I wish someone had shoved in my face before I wrote my first `select!` loop. I lost three days to a bug where messages were disappearing from a queue. No errors. No panics. Just... gone. Turns out, `select!` was cancelling a future that had already read the message from the channel but hadn't finished processing it.

Cancellation safety is the most under-discussed footgun in async Rust. If you use `select!`, you need to understand this.

## What Is Cancellation?

In async Rust, cancellation means **dropping a future before it completes**. This happens in several places:

1. **`select!`** — losing branches are dropped
2. **`tokio::time::timeout`** — the inner future is dropped if time runs out
3. **Dropping a `JoinHandle`** — doesn't cancel, but `abort()` does
4. **Dropping a `JoinSet`** — all tasks are aborted

When a future is dropped, its `Drop` implementation runs, cleaning up any resources. But here's the problem: if the future had made *partial progress* before being dropped, that progress might be lost.

## The Problem, Illustrated

```rust
use tokio::sync::mpsc;
use tokio::time::{sleep, Duration};

#[tokio::main]
async fn main() {
    let (tx, mut rx) = mpsc::channel::<u32>(10);

    // Producer
    tokio::spawn(async move {
        for i in 0..10 {
            tx.send(i).await.unwrap();
            sleep(Duration::from_millis(150)).await;
        }
    });

    // Consumer with select! — this is SAFE for mpsc::recv
    // But let me show you what an UNSAFE pattern looks like
    let mut received = Vec::new();

    loop {
        tokio::select! {
            val = rx.recv() => {
                match val {
                    Some(v) => {
                        received.push(v);
                        println!("Got: {v}");
                    }
                    None => break,
                }
            }
            _ = sleep(Duration::from_secs(2)) => {
                println!("No messages for 2 seconds, stopping");
                break;
            }
        }
    }

    println!("Received {} messages: {:?}", received.len(), received);
}
```

This code is actually fine because `mpsc::Receiver::recv()` is cancellation-safe. But what if we were reading from a socket?

```rust
use tokio::io::AsyncReadExt;
use tokio::net::TcpStream;
use tokio::time::{sleep, Duration};

async fn dangerous_read(stream: &mut TcpStream) {
    let mut buf = [0u8; 1024];

    loop {
        tokio::select! {
            // BUG: read() is NOT cancellation-safe!
            // If the timeout fires while read() has partially filled `buf`,
            // those bytes are lost forever.
            result = stream.read(&mut buf) => {
                match result {
                    Ok(0) => break,
                    Ok(n) => println!("Read {n} bytes"),
                    Err(e) => {
                        eprintln!("Error: {e}");
                        break;
                    }
                }
            }
            _ = sleep(Duration::from_secs(5)) => {
                println!("Timeout!");
                // The read future is dropped — partial data is lost!
            }
        }
    }
}
```

## What Makes a Future Cancellation-Safe?

A future is **cancellation-safe** if dropping it at any `.await` point doesn't lose data or leave things in an inconsistent state.

**Cancellation-safe** (from Tokio docs):
- `tokio::sync::mpsc::Receiver::recv()` — either a message is received or it isn't
- `tokio::sync::oneshot::Receiver` — the value is either received or stays in the channel
- `tokio::sync::broadcast::Receiver::recv()` — messages stay in the broadcast queue
- `tokio::net::TcpListener::accept()` — connections stay in the OS queue
- `tokio::time::sleep()` — just a timer, no state to lose
- `tokio::sync::Mutex::lock()` — lock is either acquired or not

**NOT cancellation-safe:**
- `tokio::io::AsyncReadExt::read()` — might have partially read data
- `tokio::io::AsyncReadExt::read_exact()` — partial reads are lost
- `tokio::io::AsyncBufReadExt::read_line()` — partial line data is lost
- Any future that does internal buffering or multi-step operations

## How to Make Unsafe Operations Safe in select!

### Strategy 1: Use cancellation-safe alternatives

Instead of `read()` in a select loop, use `readable()`:

```rust
use tokio::io::AsyncReadExt;
use tokio::net::TcpListener;
use tokio::time::{sleep, Duration};
use tokio::io::Interest;

async fn safe_read_loop() -> Result<(), Box<dyn std::error::Error>> {
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let addr = listener.local_addr()?;
    println!("Listening on {addr}");

    // Spawn a client that connects (for demonstration)
    tokio::spawn(async move {
        sleep(Duration::from_millis(100)).await;
        let mut stream = tokio::net::TcpStream::connect(addr).await.unwrap();
        tokio::io::AsyncWriteExt::write_all(&mut stream, b"hello world").await.unwrap();
    });

    let (mut stream, _) = listener.accept().await?;
    let mut buf = vec![0u8; 1024];

    loop {
        tokio::select! {
            // readable() IS cancellation-safe — it just checks if data is available
            result = stream.readable() => {
                result?;
                // Now do the actual read outside of select!
                match stream.try_read(&mut buf) {
                    Ok(0) => break,
                    Ok(n) => {
                        println!("Read: {:?}", &buf[..n]);
                    }
                    Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        continue;
                    }
                    Err(e) => return Err(e.into()),
                }
            }
            _ = sleep(Duration::from_secs(5)) => {
                println!("Timeout");
                break;
            }
        }
    }

    Ok(())
}

#[tokio::main]
async fn main() {
    safe_read_loop().await.unwrap();
}
```

### Strategy 2: Pin the future outside the loop

If you can't avoid a non-cancellation-safe future, don't recreate it each iteration:

```rust
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::time::{sleep, Duration};

async fn demo_pinned_read() -> Result<(), Box<dyn std::error::Error>> {
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let addr = listener.local_addr()?;

    tokio::spawn(async move {
        sleep(Duration::from_millis(50)).await;
        let mut stream = tokio::net::TcpStream::connect(addr).await.unwrap();
        stream.write_all(b"hello").await.unwrap();
        sleep(Duration::from_millis(200)).await;
        stream.write_all(b" world").await.unwrap();
        drop(stream);
    });

    let (mut stream, _) = listener.accept().await?;
    let mut full_buf = Vec::new();
    let mut buf = [0u8; 1024];

    // Pin the read future OUTSIDE the loop
    loop {
        let mut read_fut = std::pin::pin!(stream.read(&mut buf));

        tokio::select! {
            result = &mut read_fut => {
                match result {
                    Ok(0) => break,
                    Ok(n) => {
                        full_buf.extend_from_slice(&buf[..n]);
                        println!("Read {n} bytes, total: {}", full_buf.len());
                    }
                    Err(e) => return Err(e.into()),
                }
            }
            _ = sleep(Duration::from_secs(5)) => {
                println!("Timeout");
                break;
            }
        }
    }

    println!("Full message: {:?}", String::from_utf8_lossy(&full_buf));
    Ok(())
}

#[tokio::main]
async fn main() {
    demo_pinned_read().await.unwrap();
}
```

### Strategy 3: Wrap in a helper that guarantees atomicity

```rust
use tokio::sync::mpsc;
use tokio::time::{sleep, Duration};

/// A wrapper that ensures either the full operation completes
/// or nothing happens.
async fn receive_and_process(rx: &mut mpsc::Receiver<String>) -> Option<String> {
    // This whole function is "atomic" from select!'s perspective.
    // If select! cancels us before recv() returns, no message is lost.
    // If recv() returns a message, we process it before returning.
    let msg = rx.recv().await?;
    let processed = format!("PROCESSED: {msg}");
    Some(processed)
}

#[tokio::main]
async fn main() {
    let (tx, mut rx) = mpsc::channel(10);

    tokio::spawn(async move {
        for i in 0..5 {
            tx.send(format!("msg-{i}")).await.unwrap();
            sleep(Duration::from_millis(100)).await;
        }
    });

    loop {
        tokio::select! {
            result = receive_and_process(&mut rx) => {
                match result {
                    Some(processed) => println!("{processed}"),
                    None => break,
                }
            }
            _ = sleep(Duration::from_secs(2)) => {
                println!("Idle timeout");
                break;
            }
        }
    }
}
```

## Testing for Cancellation Bugs

Here's a pattern to stress-test cancellation:

```rust
use tokio::sync::mpsc;
use tokio::time::{sleep, timeout, Duration};

async fn run_cancellation_test() {
    let (tx, mut rx) = mpsc::channel::<u32>(100);
    let send_count = 1000u32;

    // Producer: send numbers 0..999
    let producer = tokio::spawn(async move {
        for i in 0..send_count {
            if tx.send(i).await.is_err() {
                break;
            }
            // Random-ish delays to create race conditions
            if i % 10 == 0 {
                tokio::task::yield_now().await;
            }
        }
    });

    // Consumer: receive with aggressive timeouts (to trigger cancellation)
    let mut received = Vec::new();
    loop {
        tokio::select! {
            val = rx.recv() => {
                match val {
                    Some(v) => received.push(v),
                    None => break,
                }
            }
            _ = sleep(Duration::from_millis(1)) => {
                // Very short timeout — will trigger often
                // If recv() weren't cancellation-safe, we'd lose messages
            }
        }

        // Exit after all messages should have been sent
        if received.len() >= send_count as usize {
            break;
        }
    }

    producer.await.unwrap();

    // With a cancellation-safe recv(), we should get all messages
    // (Though some might still be in the channel)
    while let Ok(val) = rx.try_recv() {
        received.push(val);
    }

    received.sort();
    println!("Received {} of {} messages", received.len(), send_count);

    // Check for gaps
    for (i, &val) in received.iter().enumerate() {
        if val != i as u32 {
            println!("GAP: expected {i}, got {val}");
        }
    }
}

#[tokio::main]
async fn main() {
    run_cancellation_test().await;
}
```

## Rules of Thumb

1. **Know which Tokio operations are cancellation-safe.** Check the docs — they're explicit about it.
2. **In `select!` loops, only use cancellation-safe futures.** Or use the strategies above.
3. **When in doubt, don't use `select!`.** A dedicated task with a channel is always cancellation-safe.
4. **Test cancellation paths explicitly.** Use short timeouts in tests to trigger cancellation frequently.
5. **`timeout()` has the same issues.** `tokio::time::timeout(dur, read_exact(&mut buf))` can lose data if the timeout fires mid-read.

Cancellation is the price you pay for cooperative scheduling. In a threaded world, you can't "drop" a running thread — it runs to completion (or the process exits). In async Rust, any `.await` point is a potential cancellation point.

Respect it, and your async code will be rock solid. Ignore it, and you'll spend days chasing phantom data loss bugs.

Next lesson: timeouts, deadlines, and graceful shutdown — building on cancellation to handle bounded operations correctly.
