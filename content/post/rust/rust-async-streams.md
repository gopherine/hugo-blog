---
title: "Lesson 6: Streams — Async iterators"
author: Atharva Pandey
series: "Async Rust Masterclass"
lesson: 6
keywords: [Rust, rust, async, tokio, streams, async iterator, StreamExt, tokio-stream]
tags: [Rust tutorial, rust, async, tokio]
date: '2025-01-16T10:31:07.000Z'
---

Regular iterators give you one value at a time, synchronously. Futures give you one value, asynchronously. Streams give you *multiple* values, asynchronously. It's the obvious combination, and once you start using them, you'll wonder how you ever processed sequences of async data without them.

I first needed streams when building a log aggregator. I had dozens of log sources, each producing lines at their own pace. I needed to merge them, filter them, and process them in real time. Without streams, that code was a tangled mess of channels and select loops. With streams, it was a pipeline.

## What Is a Stream?

A `Stream` is to `Iterator` what `Future` is to a regular function:

```rust
// Iterator: sync, multiple values
trait Iterator {
    type Item;
    fn next(&mut self) -> Option<Self::Item>;
}

// Future: async, single value
trait Future {
    type Output;
    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output>;
}

// Stream: async, multiple values
trait Stream {
    type Item;
    fn poll_next(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>>;
}
```

`poll_next` returns:
- `Poll::Ready(Some(item))` — here's the next value
- `Poll::Ready(None)` — stream is done
- `Poll::Pending` — no value yet, I'll wake you

## Setup

Streams live in the `futures` crate and `tokio-stream`:

```toml
[dependencies]
tokio = { version = "1", features = ["full"] }
tokio-stream = "0.1"
futures = "0.3"
```

## Creating Streams

### From iterators

```rust
use tokio_stream::StreamExt;
use tokio_stream::iter;

#[tokio::main]
async fn main() {
    let mut stream = iter(vec![1, 2, 3, 4, 5]);

    while let Some(val) = stream.next().await {
        println!("Got: {val}");
    }
}
```

### From async generators (using async_stream)

The `async-stream` crate gives you the cleanest syntax:

```toml
[dependencies]
async-stream = "0.3"
```

```rust
use async_stream::stream;
use tokio::time::{sleep, Duration};
use tokio_stream::StreamExt;

fn countdown(from: u32) -> impl tokio_stream::Stream<Item = u32> {
    stream! {
        for i in (0..=from).rev() {
            sleep(Duration::from_millis(100)).await;
            yield i;
        }
    }
}

#[tokio::main]
async fn main() {
    let mut s = countdown(5);
    while let Some(val) = s.next().await {
        println!("{val}...");
    }
    println!("Liftoff!");
}
```

`yield` is the key word here — it's like `return` for a single stream value, but the generator continues running.

### From channels

```rust
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
use tokio_stream::StreamExt;

#[tokio::main]
async fn main() {
    let (tx, rx) = mpsc::channel(10);

    tokio::spawn(async move {
        for i in 0..5 {
            tx.send(i).await.unwrap();
        }
    });

    let mut stream = ReceiverStream::new(rx);

    while let Some(val) = stream.next().await {
        println!("From channel: {val}");
    }
}
```

### From intervals

```rust
use tokio::time::{interval, Duration};
use tokio_stream::wrappers::IntervalStream;
use tokio_stream::StreamExt;

#[tokio::main]
async fn main() {
    let mut ticks = IntervalStream::new(interval(Duration::from_millis(200)))
        .take(5);  // Only take 5 ticks

    while let Some(_instant) = ticks.next().await {
        println!("tick");
    }
}
```

## Stream Combinators

Just like iterators have `map`, `filter`, `take`, etc., streams have async equivalents:

### map and filter

```rust
use tokio_stream::StreamExt;
use tokio_stream::iter;

#[tokio::main]
async fn main() {
    let mut stream = iter(1..=10)
        .filter(|x| x % 2 == 0)
        .map(|x| x * x);

    while let Some(val) = stream.next().await {
        println!("{val}"); // 4, 16, 36, 64, 100
    }
}
```

### then — async map

When your transformation is async, use `then`:

```rust
use tokio::time::{sleep, Duration};
use tokio_stream::StreamExt;
use tokio_stream::iter;

async fn fetch_name(id: u32) -> String {
    sleep(Duration::from_millis(50)).await;
    format!("user-{id}")
}

#[tokio::main]
async fn main() {
    let mut stream = iter(1..=5)
        .then(|id| fetch_name(id));

    while let Some(name) = stream.next().await {
        println!("{name}");
    }
}
```

### take_while and skip_while

```rust
use tokio_stream::StreamExt;
use tokio_stream::iter;

#[tokio::main]
async fn main() {
    let mut stream = iter(vec![1, 2, 3, 4, 5, 1, 2])
        .take_while(|x| *x < 4);

    let results: Vec<_> = stream.collect().await;
    println!("{results:?}"); // [1, 2, 3]
}
```

### collect

```rust
use tokio_stream::StreamExt;
use tokio_stream::iter;

#[tokio::main]
async fn main() {
    let items: Vec<i32> = iter(1..=5).collect().await;
    println!("{items:?}");
}
```

### fold

```rust
use tokio_stream::StreamExt;
use tokio_stream::iter;

#[tokio::main]
async fn main() {
    let sum = iter(1..=100)
        .fold(0i32, |acc, x| acc + x)
        .await;
    println!("Sum: {sum}"); // 5050
}
```

## Merging Streams

One of the most powerful patterns — combining multiple async sources into one:

```rust
use async_stream::stream;
use tokio::time::{sleep, Duration};
use tokio_stream::{StreamExt, Stream};

fn sensor_a() -> impl Stream<Item = String> {
    stream! {
        loop {
            sleep(Duration::from_millis(300)).await;
            yield "temperature: 22.5C".to_string();
        }
    }
}

fn sensor_b() -> impl Stream<Item = String> {
    stream! {
        loop {
            sleep(Duration::from_millis(500)).await;
            yield "humidity: 45%".to_string();
        }
    }
}

#[tokio::main]
async fn main() {
    let merged = tokio_stream::StreamExt::take(
        StreamExt::merge(sensor_a(), sensor_b()),
        10,
    );

    tokio::pin!(merged);

    while let Some(reading) = merged.next().await {
        println!("[sensor] {reading}");
    }
}
```

The merged stream interleaves values from both sources as they arrive.

## Chunking and Batching

Processing items one at a time is often inefficient. Batch them:

```rust
use tokio_stream::StreamExt;
use tokio_stream::iter;

#[tokio::main]
async fn main() {
    let stream = iter(0..20);
    let mut chunks = stream.chunks(5);

    while let Some(chunk) = chunks.next().await {
        println!("Batch: {chunk:?}");
        // Process the whole batch at once — e.g., bulk insert into DB
    }
}
```

## Timeout on Stream Items

What if a stream stops producing? Add per-item timeouts:

```rust
use async_stream::stream;
use tokio::time::{sleep, Duration};
use tokio_stream::StreamExt;

fn unreliable_source() -> impl tokio_stream::Stream<Item = String> {
    stream! {
        yield "fast-1".to_string();
        sleep(Duration::from_millis(100)).await;
        yield "fast-2".to_string();
        sleep(Duration::from_secs(5)).await; // Long delay!
        yield "slow-3".to_string();
    }
}

#[tokio::main]
async fn main() {
    let mut stream = unreliable_source()
        .timeout(Duration::from_secs(1));

    while let Some(result) = stream.next().await {
        match result {
            Ok(val) => println!("Got: {val}"),
            Err(_) => {
                println!("Timed out waiting for next item!");
                break;
            }
        }
    }
}
```

## Building a Stream by Hand

For full control, implement `Stream` directly:

```rust
use std::pin::Pin;
use std::task::{Context, Poll};
use tokio::time::{Sleep, sleep, Duration, Instant};
use tokio_stream::Stream;

struct Ticker {
    interval: Duration,
    delay: Pin<Box<Sleep>>,
    count: u32,
    max: u32,
}

impl Ticker {
    fn new(interval: Duration, max: u32) -> Self {
        Ticker {
            interval,
            delay: Box::pin(sleep(interval)),
            count: 0,
            max,
        }
    }
}

impl Stream for Ticker {
    type Item = u32;

    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<u32>> {
        if self.count >= self.max {
            return Poll::Ready(None); // Stream complete
        }

        match self.delay.as_mut().poll(cx) {
            Poll::Pending => Poll::Pending,
            Poll::Ready(()) => {
                self.count += 1;
                let count = self.count;
                // Reset the timer
                self.delay = Box::pin(sleep(self.interval));
                Poll::Ready(Some(count))
            }
        }
    }
}

use tokio_stream::StreamExt;

#[tokio::main]
async fn main() {
    let mut ticker = Ticker::new(Duration::from_millis(200), 5);

    while let Some(tick) = ticker.next().await {
        println!("Tick #{tick}");
    }
}
```

## Practical Example: Processing a Stream Pipeline

Here's a realistic data processing pipeline:

```rust
use async_stream::stream;
use tokio::time::{sleep, Duration};
use tokio_stream::{StreamExt, Stream};

#[derive(Debug)]
struct LogEntry {
    level: String,
    message: String,
}

fn log_source() -> impl Stream<Item = LogEntry> {
    stream! {
        let entries = vec![
            ("INFO", "Server started"),
            ("DEBUG", "Connection pool initialized"),
            ("ERROR", "Failed to connect to cache"),
            ("INFO", "Request handled in 23ms"),
            ("WARN", "Slow query detected: 1200ms"),
            ("ERROR", "Out of memory in worker 3"),
            ("INFO", "Health check passed"),
        ];

        for (level, msg) in entries {
            sleep(Duration::from_millis(50)).await;
            yield LogEntry {
                level: level.to_string(),
                message: msg.to_string(),
            };
        }
    }
}

#[tokio::main]
async fn main() {
    // Pipeline: source -> filter errors -> format -> collect
    let errors: Vec<String> = log_source()
        .filter(|entry| entry.level == "ERROR")
        .map(|entry| format!("[{}] {}", entry.level, entry.message))
        .collect()
        .await;

    println!("Error report:");
    for error in &errors {
        println!("  {error}");
    }
    println!("Total errors: {}", errors.len());
}
```

## When to Use Streams vs Channels

Streams and channels both give you async sequences of values. When do you pick which?

**Use streams when:**
- You have a data source with a natural "pull" model (files, database cursors, paginated APIs)
- You want composable transformations (map, filter, merge)
- The producer and consumer are tightly coupled

**Use channels when:**
- Producer and consumer are separate tasks
- You need backpressure between independent components
- Multiple producers or multiple consumers
- You need buffering between different parts of the system

In practice, you'll often convert between them. `ReceiverStream` wraps a channel receiver as a stream. Streams can feed into channels via `forward`.

Streams are one of those features that seem niche until you need them — and then they're indispensable. Next lesson, we'll look at async channels, which are the other side of the async data-passing coin.
