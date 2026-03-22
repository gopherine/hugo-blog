---
title: "Lesson 15: Backpressure — Bounded channels and flow control"
author: Atharva Pandey
series: "Async Rust Masterclass"
lesson: 15
keywords: [Rust, rust, async, tokio, backpressure, flow control, bounded channels, buffer, overload]
tags: [Rust tutorial, rust, async, tokio]
date: '2025-02-02T10:33:45.000Z'
---

The fastest way to kill a production service is to accept work faster than you can process it. I learned this the hard way when a log ingestion pipeline I built consumed 50GB of RAM and crashed because I used unbounded channels everywhere. "It works fine in testing" — yeah, testing with 100 messages, not 10 million.

Backpressure is the mechanism by which a system says "slow down, I'm full." Without it, fast producers overwhelm slow consumers, and your only flow control is the OOM killer.

## The Problem Without Backpressure

```rust
use tokio::sync::mpsc;
use tokio::time::{sleep, Duration};

#[tokio::main]
async fn main() {
    // Unbounded channel — no backpressure!
    let (tx, mut rx) = mpsc::unbounded_channel::<Vec<u8>>();

    // Fast producer: 10,000 messages per second
    tokio::spawn(async move {
        loop {
            // Each message is 1MB
            let data = vec![0u8; 1_000_000];
            if tx.send(data).is_err() {
                break;
            }
            // No backpressure — producer never slows down
        }
    });

    // Slow consumer: processes 100 per second
    while let Some(data) = rx.recv().await {
        // Simulate slow processing
        sleep(Duration::from_millis(10)).await;
        println!("Processed {} bytes", data.len());
    }

    // This program will eat all available memory and die
}
```

## The Fix: Bounded Channels

```rust
use tokio::sync::mpsc;
use tokio::time::{sleep, Duration, Instant};

#[tokio::main]
async fn main() {
    // Bounded channel — producer blocks when buffer is full
    let (tx, mut rx) = mpsc::channel::<Vec<u8>>(10); // Only 10 items buffered

    let start = Instant::now();

    // Producer
    tokio::spawn(async move {
        for i in 0..50 {
            let data = vec![i as u8; 1000];
            // This .await blocks when the channel is full!
            // The producer is forced to slow down to the consumer's pace
            tx.send(data).await.unwrap();
            println!("[{:.1}s] Produced item {i}",
                start.elapsed().as_secs_f64());
        }
    });

    // Slow consumer
    while let Some(data) = rx.recv().await {
        sleep(Duration::from_millis(100)).await;
        println!("[{:.1}s] Consumed {} bytes (first byte: {})",
            start.elapsed().as_secs_f64(), data.len(), data[0]);
    }
}
```

With a bounded channel, the producer naturally slows down when the consumer can't keep up. Memory usage stays constant. No OOM crashes. The system degrades gracefully.

## Backpressure Strategies

### Strategy 1: Block the Producer (Bounded Channel)

The simplest and most common. We just saw this above. The producer's `.send().await` blocks until there's room.

**Pros:** Simple, reliable, memory-bounded
**Cons:** If the producer is latency-sensitive (like an HTTP handler), blocking it means slower response times

### Strategy 2: Drop Messages

Sometimes it's better to lose data than to slow down:

```rust
use tokio::sync::mpsc;
use tokio::time::{sleep, Duration};

struct MetricsCollector {
    tx: mpsc::Sender<Metric>,
}

struct Metric {
    name: String,
    value: f64,
}

impl MetricsCollector {
    fn new() -> (Self, mpsc::Receiver<Metric>) {
        let (tx, rx) = mpsc::channel(1000);
        (MetricsCollector { tx }, rx)
    }

    fn record(&self, name: &str, value: f64) {
        // try_send: don't block, just drop the metric if buffer is full
        match self.tx.try_send(Metric {
            name: name.to_string(),
            value,
        }) {
            Ok(()) => {}
            Err(mpsc::error::TrySendError::Full(_)) => {
                // Metric dropped — acceptable for non-critical data
                eprintln!("Metrics buffer full, dropping metric");
            }
            Err(mpsc::error::TrySendError::Closed(_)) => {
                eprintln!("Metrics consumer gone");
            }
        }
    }
}

async fn metrics_consumer(mut rx: mpsc::Receiver<Metric>) {
    let mut batch = Vec::new();

    loop {
        tokio::select! {
            Some(metric) = rx.recv() => {
                batch.push(metric);
                if batch.len() >= 100 {
                    println!("Flushing {} metrics", batch.len());
                    // Simulate slow flush
                    sleep(Duration::from_millis(500)).await;
                    batch.clear();
                }
            }
            _ = sleep(Duration::from_secs(5)) => {
                if !batch.is_empty() {
                    println!("Periodic flush of {} metrics", batch.len());
                    batch.clear();
                }
            }
        }
    }
}

#[tokio::main]
async fn main() {
    let (collector, rx) = MetricsCollector::new();
    tokio::spawn(metrics_consumer(rx));

    // High-frequency metric recording
    for i in 0..5000 {
        collector.record("request_count", i as f64);
        if i % 1000 == 0 {
            sleep(Duration::from_millis(1)).await;
        }
    }

    sleep(Duration::from_secs(2)).await;
}
```

### Strategy 3: Adaptive Batching

Process items in batches that grow when the system is busy:

```rust
use tokio::sync::mpsc;
use tokio::time::{sleep, timeout, Duration, Instant};

async fn adaptive_consumer(mut rx: mpsc::Receiver<String>) {
    loop {
        // Wait for the first item
        let first = match rx.recv().await {
            Some(item) => item,
            None => break,
        };

        let mut batch = vec![first];
        let batch_deadline = Instant::now() + Duration::from_millis(50);

        // Collect more items until deadline or max batch size
        loop {
            if batch.len() >= 100 {
                break; // Max batch size reached
            }

            let remaining = batch_deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                break; // Deadline reached
            }

            match timeout(remaining, rx.recv()).await {
                Ok(Some(item)) => batch.push(item),
                Ok(None) => break, // Channel closed
                Err(_) => break,   // Timeout
            }
        }

        // Process the whole batch at once
        println!("Processing batch of {} items", batch.len());
        sleep(Duration::from_millis(10)).await;
    }
}

#[tokio::main]
async fn main() {
    let (tx, rx) = mpsc::channel(1000);

    tokio::spawn(adaptive_consumer(rx));

    // Simulate bursty production
    for burst in 0..3 {
        println!("--- Burst {burst} ---");
        for i in 0..200 {
            tx.send(format!("burst-{burst}-item-{i}")).await.unwrap();
        }
        sleep(Duration::from_millis(500)).await;
    }

    drop(tx);
    sleep(Duration::from_millis(100)).await;
}
```

### Strategy 4: Semaphore-Based Concurrency Control

```rust
use std::sync::Arc;
use tokio::sync::Semaphore;
use tokio::time::{sleep, Duration};

struct WorkQueue {
    semaphore: Arc<Semaphore>,
}

impl WorkQueue {
    fn new(max_in_flight: usize) -> Self {
        WorkQueue {
            semaphore: Arc::new(Semaphore::new(max_in_flight)),
        }
    }

    async fn submit<F, Fut, T>(&self, work: F) -> T
    where
        F: FnOnce() -> Fut,
        Fut: std::future::Future<Output = T>,
    {
        // Wait for capacity — this IS backpressure
        let _permit = self.semaphore.acquire().await.unwrap();
        work().await
        // permit dropped, slot freed
    }

    fn available_capacity(&self) -> usize {
        self.semaphore.available_permits()
    }
}

#[tokio::main]
async fn main() {
    let queue = Arc::new(WorkQueue::new(5));

    let mut handles = vec![];
    for i in 0..20 {
        let queue = queue.clone();
        handles.push(tokio::spawn(async move {
            let result = queue.submit(|| async move {
                println!("Processing {i} (capacity: {})",
                    queue.available_capacity());
                sleep(Duration::from_millis(200)).await;
                i * 2
            }).await;
            println!("Result {i}: {result}");
        }));
    }

    for h in handles {
        h.await.unwrap();
    }
}
```

## End-to-End Backpressure

Real systems have multiple stages, and backpressure needs to flow through all of them:

```rust
use tokio::sync::mpsc;
use tokio::time::{sleep, Duration};

// Stage 1: Ingest → validate
// Stage 2: Validate → process
// Stage 3: Process → store

async fn ingest(tx: mpsc::Sender<String>) {
    for i in 0..100 {
        let item = format!("raw-data-{i}");
        // If the validator is slow, this backs up the ingest
        tx.send(item).await.unwrap();
        println!("[ingest] Sent item {i}");
    }
}

async fn validate(mut rx: mpsc::Receiver<String>, tx: mpsc::Sender<String>) {
    while let Some(item) = rx.recv().await {
        // Simulate validation
        sleep(Duration::from_millis(10)).await;
        let validated = format!("validated-{item}");
        // If the processor is slow, this backs up the validator,
        // which backs up the ingester
        tx.send(validated).await.unwrap();
    }
}

async fn process(mut rx: mpsc::Receiver<String>, tx: mpsc::Sender<String>) {
    while let Some(item) = rx.recv().await {
        // Simulate heavy processing
        sleep(Duration::from_millis(50)).await;
        let processed = format!("processed-{item}");
        tx.send(processed).await.unwrap();
    }
}

async fn store(mut rx: mpsc::Receiver<String>) {
    let mut count = 0;
    while let Some(item) = rx.recv().await {
        // Simulate database write
        sleep(Duration::from_millis(20)).await;
        count += 1;
        if count % 10 == 0 {
            println!("[store] Stored {count} items (latest: {item})");
        }
    }
    println!("[store] Total stored: {count}");
}

#[tokio::main]
async fn main() {
    // Each channel has a small buffer — backpressure flows naturally
    let (ingest_tx, validate_rx) = mpsc::channel(5);
    let (validate_tx, process_rx) = mpsc::channel(5);
    let (process_tx, store_rx) = mpsc::channel(5);

    let h1 = tokio::spawn(ingest(ingest_tx));
    let h2 = tokio::spawn(validate(validate_rx, validate_tx));
    let h3 = tokio::spawn(process(process_rx, process_tx));
    let h4 = tokio::spawn(store(store_rx));

    h1.await.unwrap();
    drop(h2); // These will complete as channels drain
    drop(h3);
    sleep(Duration::from_secs(10)).await;
}
```

The beauty here: if the store is slow, the process channel fills up, which slows the processor, which fills the validate channel, which slows the validator, which fills the ingest channel, which slows the ingester. Backpressure propagates automatically through the bounded channels.

## Choosing Buffer Sizes

This comes up a lot. There's no universal answer, but here are my guidelines:

- **1-10:** When you want tight coupling and immediate backpressure. Good for pipelines where each stage should run at roughly the same speed.
- **100-1000:** When you want to absorb bursts. Good for bursty producers with steady consumers.
- **Unbounded:** Almost never. The only valid case is when the sender can't block (e.g., a signal handler or a Drop impl).

A good starting point: set the buffer to 2x the number of consumer tasks. Then measure under load and adjust.

## Monitoring Backpressure

You can't fix what you can't see. Monitor channel capacity in production:

```rust
use tokio::sync::mpsc;
use tokio::time::{interval, Duration};

fn channel_with_metrics<T>(capacity: usize, name: &'static str)
    -> (mpsc::Sender<T>, mpsc::Receiver<T>)
{
    let (tx, rx) = mpsc::channel(capacity);

    // Monitor fill level periodically
    let tx_weak = tx.downgrade();
    tokio::spawn(async move {
        let mut tick = interval(Duration::from_secs(5));
        loop {
            tick.tick().await;
            match tx_weak.upgrade() {
                Some(tx) => {
                    let capacity_total = capacity;
                    // Note: there's no direct API for queue length in mpsc
                    // In production, use metrics crate to track send/recv rates
                    println!("[{name}] channel capacity: {capacity_total}");
                }
                None => break, // Channel dropped
            }
        }
    });

    (tx, rx)
}

#[tokio::main]
async fn main() {
    let (tx, mut rx) = channel_with_metrics::<String>(100, "work-queue");

    tokio::spawn(async move {
        while let Some(item) = rx.recv().await {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    });

    for i in 0..50 {
        tx.send(format!("item-{i}")).await.unwrap();
    }

    tokio::time::sleep(Duration::from_secs(6)).await;
}
```

Backpressure isn't glamorous. It's plumbing. But it's the plumbing that keeps your service alive at 3 AM when traffic spikes 10x. Get it right from the start, and your system degrades gracefully instead of catastrophically.

Next lesson: we go deep. How async executors actually work under the hood.
