---
title: "Lesson 13: Fan-Out Fan-In in Rust — Parallel pipelines"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 13
keywords: [Rust, rust, concurrency, fan-out, fan-in, pipeline, parallel processing, scatter gather]
tags: [Rust tutorial, rust, concurrency]
date: '2024-11-29T12:50:00.000Z'
---

One of the most satisfying architectures I've built was a real-time analytics pipeline. Events came in through a single ingestion point, fanned out to eight processing workers, then fanned back in to a single aggregator that wrote results to the database. Throughput went from 2,000 events/second to 14,000 with zero data loss.

Fan-out/fan-in is one of those patterns that looks simple on a whiteboard but has real subtlety in implementation. Getting the channel lifecycle right, handling errors, managing backpressure — that's where things get interesting.

## The Pattern

Fan-out/fan-in has three stages:

1. **Producer** — Generates work items
2. **Fan-out** — Distributes items to N workers (scatter)
3. **Fan-in** — Collects results from all workers into one stream (gather)

```
                ┌─ Worker 1 ─┐
Producer ──►────┤─ Worker 2 ─├────►─── Collector
                ├─ Worker 3 ─┤
                └─ Worker 4 ─┘
```

## Basic Implementation

```rust
use std::sync::mpsc;
use std::thread;

fn main() {
    let num_workers = 4;

    // Stage 1: Producer → Workers
    let (work_tx, work_rx) = mpsc::channel::<u64>();

    // Stage 2: Workers → Collector (fan-in)
    let (result_tx, result_rx) = mpsc::channel::<(u64, u64)>();

    // Wrap receiver in Arc<Mutex> for multi-consumer
    let work_rx = std::sync::Arc::new(std::sync::Mutex::new(work_rx));

    // Spawn workers
    let mut worker_handles = vec![];
    for id in 0..num_workers {
        let work_rx = std::sync::Arc::clone(&work_rx);
        let result_tx = result_tx.clone();

        worker_handles.push(thread::spawn(move || {
            loop {
                let item = work_rx.lock().unwrap().recv();
                match item {
                    Ok(n) => {
                        // Simulate expensive computation
                        let result = (0..n).sum::<u64>();
                        result_tx.send((n, result)).unwrap();
                    }
                    Err(_) => break, // channel closed
                }
            }
            println!("Worker {} done", id);
        }));
    }

    // Drop the original result_tx so the fan-in channel closes
    // when all workers finish
    drop(result_tx);

    // Producer: send work
    let producer = thread::spawn(move || {
        for i in 1..=100 {
            work_tx.send(i * 1000).unwrap();
        }
        // work_tx dropped here → channel closes → workers exit
    });

    // Collector: fan-in results
    let collector = thread::spawn(move || {
        let mut total = 0u64;
        let mut count = 0;
        for (input, result) in result_rx {
            total += result;
            count += 1;
            if count % 20 == 0 {
                println!("Collected {} results so far", count);
            }
        }
        (count, total)
    });

    producer.join().unwrap();
    for h in worker_handles {
        h.join().unwrap();
    }
    let (count, total) = collector.join().unwrap();
    println!("Processed {} items, total sum: {}", count, total);
}
```

## With Crossbeam (Much Cleaner)

Crossbeam's MPMC channels eliminate the `Arc<Mutex<Receiver>>` hack:

```rust
use crossbeam_channel::{bounded, Sender, Receiver};
use std::thread;

fn fan_out_fan_in<T, R, F>(
    items: Vec<T>,
    num_workers: usize,
    queue_depth: usize,
    f: F,
) -> Vec<R>
where
    T: Send + 'static,
    R: Send + 'static,
    F: Fn(T) -> R + Send + Sync + 'static + Clone,
{
    let (work_tx, work_rx) = bounded::<T>(queue_depth);
    let (result_tx, result_rx) = bounded::<R>(queue_depth);

    // Spawn workers
    let mut handles = vec![];
    for _ in 0..num_workers {
        let rx = work_rx.clone();
        let tx = result_tx.clone();
        let f = f.clone();
        handles.push(thread::spawn(move || {
            while let Ok(item) = rx.recv() {
                let result = f(item);
                if tx.send(result).is_err() {
                    break;
                }
            }
        }));
    }

    // Drop extra channel ends
    drop(work_rx);
    drop(result_tx);

    // Producer (in current thread)
    let producer = thread::spawn(move || {
        for item in items {
            if work_tx.send(item).is_err() {
                break;
            }
        }
    });

    // Collect results
    let results: Vec<R> = result_rx.iter().collect();

    producer.join().unwrap();
    for h in handles {
        h.join().unwrap();
    }

    results
}

fn main() {
    let inputs: Vec<u64> = (1..=50).collect();

    let results = fan_out_fan_in(
        inputs,
        4,    // workers
        16,   // queue depth
        |n: u64| {
            // Simulate work
            std::thread::sleep(std::time::Duration::from_millis(10));
            n * n
        },
    );

    println!("Got {} results", results.len());
    println!("First 10: {:?}", &results[..10.min(results.len())]);
}
```

This is a reusable fan-out/fan-in function. Pass it items, a worker count, and a processing function — it handles all the plumbing.

## Ordered Fan-In

The basic pattern doesn't preserve order — results come back in whatever order workers finish. If you need ordered results, tag each item with an index:

```rust
use crossbeam_channel::bounded;
use std::thread;

fn ordered_fan_out_fan_in<T, R, F>(
    items: Vec<T>,
    num_workers: usize,
    f: F,
) -> Vec<R>
where
    T: Send + 'static,
    R: Send + Default + 'static,
    F: Fn(T) -> R + Send + Sync + Clone + 'static,
{
    let total = items.len();
    let (work_tx, work_rx) = bounded::<(usize, T)>(num_workers * 2);
    let (result_tx, result_rx) = bounded::<(usize, R)>(num_workers * 2);

    // Workers
    let mut handles = vec![];
    for _ in 0..num_workers {
        let rx = work_rx.clone();
        let tx = result_tx.clone();
        let f = f.clone();
        handles.push(thread::spawn(move || {
            while let Ok((idx, item)) = rx.recv() {
                let result = f(item);
                let _ = tx.send((idx, result));
            }
        }));
    }
    drop(work_rx);
    drop(result_tx);

    // Send indexed work
    let producer = thread::spawn(move || {
        for (idx, item) in items.into_iter().enumerate() {
            let _ = work_tx.send((idx, item));
        }
    });

    // Collect and reorder
    let mut results: Vec<Option<R>> = (0..total).map(|_| None).collect();
    for (idx, result) in result_rx {
        results[idx] = Some(result);
    }

    producer.join().unwrap();
    for h in handles {
        h.join().unwrap();
    }

    results.into_iter().map(|r| r.unwrap()).collect()
}

fn main() {
    let items: Vec<String> = (0..20)
        .map(|i| format!("item-{}", i))
        .collect();

    let results = ordered_fan_out_fan_in(items, 4, |s: String| {
        std::thread::sleep(std::time::Duration::from_millis(
            (s.len() as u64) * 5,
        ));
        s.to_uppercase()
    });

    // Results are in the original order despite parallel processing
    for (i, r) in results.iter().enumerate() {
        println!("{}: {}", i, r);
    }
}
```

## Multi-Stage Pipelines

Real systems often have multiple fan-out/fan-in stages chained together:

```rust
use crossbeam_channel::bounded;
use std::thread;

fn main() {
    // Stage 1: Parse
    let (raw_tx, raw_rx) = bounded::<String>(32);
    // Stage 2: Validate
    let (parsed_tx, parsed_rx) = bounded::<(String, u64)>(32);
    // Stage 3: Store
    let (valid_tx, valid_rx) = bounded::<(String, u64)>(32);

    // Parsers (fan-out)
    let mut handles = vec![];
    for _ in 0..2 {
        let rx = raw_rx.clone();
        let tx = parsed_tx.clone();
        handles.push(thread::spawn(move || {
            while let Ok(line) = rx.recv() {
                // Parse "key:value"
                if let Some((key, val)) = line.split_once(':') {
                    if let Ok(n) = val.trim().parse::<u64>() {
                        let _ = tx.send((key.to_string(), n));
                    }
                }
            }
        }));
    }
    drop(raw_rx);
    drop(parsed_tx);

    // Validators (fan-out)
    for _ in 0..2 {
        let rx = parsed_rx.clone();
        let tx = valid_tx.clone();
        handles.push(thread::spawn(move || {
            while let Ok((key, value)) = rx.recv() {
                if value > 0 && value < 1_000_000 {
                    let _ = tx.send((key, value));
                }
            }
        }));
    }
    drop(parsed_rx);
    drop(valid_tx);

    // Collector (fan-in)
    let collector = thread::spawn(move || {
        let mut results = std::collections::HashMap::new();
        while let Ok((key, value)) = valid_rx.recv() {
            *results.entry(key).or_insert(0u64) += value;
        }
        results
    });

    // Producer
    let producer = thread::spawn(move || {
        let data = vec![
            "users:42", "errors:0", "users:18", "latency:150",
            "users:7", "errors:3", "latency:89", "users:100",
            "invalid", "latency:99999999", "errors:1",
        ];
        for line in data {
            let _ = raw_tx.send(line.to_string());
        }
    });

    producer.join().unwrap();
    for h in handles {
        h.join().unwrap();
    }

    let results = collector.join().unwrap();
    println!("Aggregated results:");
    for (key, total) in &results {
        println!("  {}: {}", key, total);
    }
}
```

Each stage independently parallelizable, each channel providing backpressure. If parsing is fast but validation is slow, the bounded channel between them naturally throttles the parsers.

## Error Handling in Pipelines

The trickiest part. What happens when a worker encounters an error?

```rust
use crossbeam_channel::bounded;
use std::thread;

#[derive(Debug)]
enum PipelineItem<T> {
    Data(T),
    Error(String),
}

fn main() {
    let (tx, rx) = bounded::<PipelineItem<i32>>(16);
    let (out_tx, out_rx) = bounded::<PipelineItem<i32>>(16);

    // Workers that can fail
    let mut handles = vec![];
    for id in 0..4 {
        let rx = rx.clone();
        let tx = out_tx.clone();
        handles.push(thread::spawn(move || {
            while let Ok(item) = rx.recv() {
                match item {
                    PipelineItem::Data(n) => {
                        if n % 7 == 0 {
                            let _ = tx.send(PipelineItem::Error(
                                format!("Worker {}: {} is divisible by 7", id, n),
                            ));
                        } else {
                            let _ = tx.send(PipelineItem::Data(n * n));
                        }
                    }
                    PipelineItem::Error(e) => {
                        // Pass errors through
                        let _ = tx.send(PipelineItem::Error(e));
                    }
                }
            }
        }));
    }
    drop(rx);
    drop(out_tx);

    // Producer
    thread::spawn(move || {
        for i in 1..=30 {
            let _ = tx.send(PipelineItem::Data(i));
        }
    });

    // Collector — separates successes and errors
    let mut successes = 0;
    let mut errors = vec![];
    for item in out_rx {
        match item {
            PipelineItem::Data(_) => successes += 1,
            PipelineItem::Error(e) => errors.push(e),
        }
    }

    for h in handles {
        h.join().unwrap();
    }

    println!("Successes: {}, Errors: {}", successes, errors.len());
    for e in &errors {
        println!("  Error: {}", e);
    }
}
```

Wrapping items in a `Result`-like enum lets errors flow through the pipeline without killing workers. The collector decides what to do with them — log, retry, aggregate.

## Performance Considerations

1. **Queue depth matters.** Too small: workers idle waiting for work. Too large: memory bloat and high latency for individual items. Start with `2 * num_workers` and tune from there.

2. **Batch internally.** If each work item is tiny (microseconds), the channel overhead dominates. Have workers process batches:

```rust
use crossbeam_channel::bounded;

fn main() {
    let (tx, rx) = bounded::<Vec<i32>>(16);
    // Send batches of 100 items instead of individual items
    let items: Vec<i32> = (0..10_000).collect();
    for chunk in items.chunks(100) {
        tx.send(chunk.to_vec()).unwrap();
    }
}
```

3. **Match workers to the bottleneck.** If parsing is 10x faster than validation, put more workers on validation. Profile first.

Fan-out/fan-in is a fundamental pattern. Once you internalize it, you'll see it everywhere — data pipelines, web scrapers, batch processors, ETL jobs. The shape is always the same: split, process in parallel, merge.

---

Next — parking_lot, the crate that makes your mutexes faster.
