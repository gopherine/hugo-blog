---
title: "Lesson 25: Production Concurrency Architecture — Putting it all together"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 25
keywords: [Rust, rust, concurrency, production, architecture, design patterns, concurrent systems]
tags: [Rust tutorial, rust, concurrency]
date: '2024-12-22T10:00:00.000Z'
---

After 24 lessons of building blocks, let's talk about how they compose in real systems. I've shipped concurrent Rust services handling millions of requests per day, and the architecture patterns that survive production are surprisingly consistent. Not because they're clever — because they're boring. Boring is good when your pager is involved.

This lesson is the blueprint I wish I'd had when I started building concurrent Rust systems for real.

## The Architecture Layers

Every production concurrent system I've built has the same layers:

```
┌─────────────────────────────────┐
│  API / Interface Layer          │  ← Accepts work
├─────────────────────────────────┤
│  Dispatch Layer                 │  ← Routes work to processors
├─────────────────────────────────┤
│  Processing Layer               │  ← Does the actual computation
├─────────────────────────────────┤
│  State Management Layer         │  ← Shared state, caches, persistence
├─────────────────────────────────┤
│  Lifecycle Layer                │  ← Startup, shutdown, health checks
└─────────────────────────────────┘
```

Let's build a realistic service that uses every concurrency primitive we've covered.

## The System: A Real-Time Data Pipeline

We're building a metrics ingestion service:

1. Accepts metric data points via a channel (simulating HTTP/gRPC)
2. Validates and enriches data points
3. Aggregates metrics in a time window
4. Flushes aggregates to storage periodically
5. Serves queries against current aggregates
6. Handles graceful shutdown

```rust
use crossbeam_channel::{bounded, select, tick, Receiver, Sender};
use parking_lot::{Mutex, RwLock};
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant, SystemTime};

// ─── Data Types ───────────────────────────────────────────────

#[derive(Debug, Clone)]
struct DataPoint {
    metric: String,
    value: f64,
    timestamp: u64,
    tags: HashMap<String, String>,
}

#[derive(Debug, Clone, Default)]
struct AggregatedMetric {
    count: u64,
    sum: f64,
    min: f64,
    max: f64,
    last_updated: u64,
}

impl AggregatedMetric {
    fn update(&mut self, value: f64, timestamp: u64) {
        if self.count == 0 {
            self.min = value;
            self.max = value;
        } else {
            self.min = self.min.min(value);
            self.max = self.max.max(value);
        }
        self.count += 1;
        self.sum += value;
        self.last_updated = timestamp;
    }

    fn mean(&self) -> f64 {
        if self.count == 0 {
            0.0
        } else {
            self.sum / self.count as f64
        }
    }
}

// ─── Stats (Lock-Free) ───────────────────────────────────────

struct PipelineStats {
    received: AtomicU64,
    processed: AtomicU64,
    dropped: AtomicU64,
    flush_count: AtomicU64,
    errors: AtomicU64,
}

impl PipelineStats {
    fn new() -> Self {
        PipelineStats {
            received: AtomicU64::new(0),
            processed: AtomicU64::new(0),
            dropped: AtomicU64::new(0),
            flush_count: AtomicU64::new(0),
            errors: AtomicU64::new(0),
        }
    }

    fn report(&self) {
        println!(
            "Pipeline stats — received: {}, processed: {}, dropped: {}, flushes: {}, errors: {}",
            self.received.load(Ordering::Relaxed),
            self.processed.load(Ordering::Relaxed),
            self.dropped.load(Ordering::Relaxed),
            self.flush_count.load(Ordering::Relaxed),
            self.errors.load(Ordering::Relaxed),
        );
    }
}

// ─── Aggregation Store (RwLock for read-heavy access) ────────

struct AggregationStore {
    current: RwLock<HashMap<String, AggregatedMetric>>,
    history: Mutex<Vec<HashMap<String, AggregatedMetric>>>,
}

impl AggregationStore {
    fn new() -> Self {
        AggregationStore {
            current: RwLock::new(HashMap::new()),
            history: Mutex::new(Vec::new()),
        }
    }

    fn update(&self, metric: &str, value: f64, timestamp: u64) {
        let mut store = self.current.write();
        store
            .entry(metric.to_string())
            .or_default()
            .update(value, timestamp);
    }

    fn query(&self, metric: &str) -> Option<AggregatedMetric> {
        self.current.read().get(metric).cloned()
    }

    fn query_all(&self) -> HashMap<String, AggregatedMetric> {
        self.current.read().clone()
    }

    fn flush(&self) -> usize {
        let mut current = self.current.write();
        let snapshot = std::mem::take(&mut *current);
        let count = snapshot.len();
        self.history.lock().push(snapshot);
        count
    }
}

// ─── Pipeline Components ─────────────────────────────────────

fn spawn_validators(
    num_workers: usize,
    input: Receiver<DataPoint>,
    output: Sender<DataPoint>,
    stats: Arc<PipelineStats>,
) -> Vec<thread::JoinHandle<()>> {
    (0..num_workers)
        .map(|id| {
            let input = input.clone();
            let output = output.clone();
            let stats = Arc::clone(&stats);

            thread::Builder::new()
                .name(format!("validator-{}", id))
                .spawn(move || {
                    while let Ok(point) = input.recv() {
                        // Validate
                        if point.metric.is_empty() || point.value.is_nan() {
                            stats.dropped.fetch_add(1, Ordering::Relaxed);
                            continue;
                        }

                        // Enrich: add processing timestamp
                        let mut enriched = point;
                        enriched.tags.insert(
                            "processed_by".to_string(),
                            format!("validator-{}", id),
                        );

                        if output.send(enriched).is_err() {
                            break;
                        }
                    }
                })
                .unwrap()
        })
        .collect()
}

fn spawn_aggregators(
    num_workers: usize,
    input: Receiver<DataPoint>,
    store: Arc<AggregationStore>,
    stats: Arc<PipelineStats>,
) -> Vec<thread::JoinHandle<()>> {
    (0..num_workers)
        .map(|id| {
            let input = input.clone();
            let store = Arc::clone(&store);
            let stats = Arc::clone(&stats);

            thread::Builder::new()
                .name(format!("aggregator-{}", id))
                .spawn(move || {
                    while let Ok(point) = input.recv() {
                        store.update(&point.metric, point.value, point.timestamp);
                        stats.processed.fetch_add(1, Ordering::Relaxed);
                    }
                })
                .unwrap()
        })
        .collect()
}

fn spawn_flusher(
    interval: Duration,
    store: Arc<AggregationStore>,
    stats: Arc<PipelineStats>,
    running: Arc<AtomicBool>,
) -> thread::JoinHandle<()> {
    thread::Builder::new()
        .name("flusher".to_string())
        .spawn(move || {
            let ticker = tick(interval);
            while running.load(Ordering::Relaxed) {
                select! {
                    recv(ticker) -> _ => {
                        let flushed = store.flush();
                        stats.flush_count.fetch_add(1, Ordering::Relaxed);
                        println!("Flushed {} metrics to storage", flushed);
                    }
                }
            }
            // Final flush on shutdown
            let flushed = store.flush();
            println!("Final flush: {} metrics", flushed);
        })
        .unwrap()
}

fn spawn_stats_reporter(
    interval: Duration,
    stats: Arc<PipelineStats>,
    running: Arc<AtomicBool>,
) -> thread::JoinHandle<()> {
    thread::Builder::new()
        .name("stats-reporter".to_string())
        .spawn(move || {
            let ticker = tick(interval);
            while running.load(Ordering::Relaxed) {
                select! {
                    recv(ticker) -> _ => {
                        stats.report();
                    }
                }
            }
        })
        .unwrap()
}

// ─── Pipeline Orchestration ──────────────────────────────────

struct Pipeline {
    ingest_tx: Sender<DataPoint>,
    store: Arc<AggregationStore>,
    stats: Arc<PipelineStats>,
    running: Arc<AtomicBool>,
    handles: Vec<thread::JoinHandle<()>>,
}

impl Pipeline {
    fn new() -> Self {
        let running = Arc::new(AtomicBool::new(true));
        let stats = Arc::new(PipelineStats::new());
        let store = Arc::new(AggregationStore::new());

        // Channels with bounded capacity for backpressure
        let (ingest_tx, ingest_rx) = bounded::<DataPoint>(10_000);
        let (validated_tx, validated_rx) = bounded::<DataPoint>(5_000);

        let mut handles = vec![];

        // Validation workers
        handles.extend(spawn_validators(
            4,
            ingest_rx,
            validated_tx,
            Arc::clone(&stats),
        ));

        // Aggregation workers
        handles.extend(spawn_aggregators(
            2,
            validated_rx,
            Arc::clone(&store),
            Arc::clone(&stats),
        ));

        // Flusher
        handles.push(spawn_flusher(
            Duration::from_secs(10),
            Arc::clone(&store),
            Arc::clone(&stats),
            Arc::clone(&running),
        ));

        // Stats reporter
        handles.push(spawn_stats_reporter(
            Duration::from_secs(5),
            Arc::clone(&stats),
            Arc::clone(&running),
        ));

        Pipeline {
            ingest_tx,
            store,
            stats,
            running,
            handles,
        }
    }

    fn ingest(&self, point: DataPoint) -> Result<(), String> {
        self.stats.received.fetch_add(1, Ordering::Relaxed);
        self.ingest_tx
            .try_send(point)
            .map_err(|_| "Pipeline full — backpressure".to_string())
    }

    fn query(&self, metric: &str) -> Option<AggregatedMetric> {
        self.store.query(metric)
    }

    fn query_all(&self) -> HashMap<String, AggregatedMetric> {
        self.store.query_all()
    }

    fn shutdown(self) {
        println!("Initiating graceful shutdown...");
        self.running.store(false, Ordering::SeqCst);
        drop(self.ingest_tx); // close the ingestion channel

        for handle in self.handles {
            handle.join().unwrap();
        }

        self.stats.report();
        println!("Shutdown complete");
    }
}

// ─── Main ────────────────────────────────────────────────────

fn main() {
    let pipeline = Pipeline::new();

    // Simulate producers
    let ingest_tx = pipeline.ingest_tx.clone();
    let stats = Arc::clone(&pipeline.stats);

    let producer_handles: Vec<_> = (0..4)
        .map(|producer_id| {
            let tx = ingest_tx.clone();
            let stats = Arc::clone(&stats);
            thread::spawn(move || {
                let metrics = vec!["cpu.usage", "mem.used", "disk.io", "net.rx", "net.tx"];
                for i in 0..5_000 {
                    let point = DataPoint {
                        metric: metrics[i % metrics.len()].to_string(),
                        value: (i as f64) * 0.1 + producer_id as f64,
                        timestamp: SystemTime::now()
                            .duration_since(SystemTime::UNIX_EPOCH)
                            .unwrap()
                            .as_secs(),
                        tags: HashMap::from([
                            ("host".to_string(), format!("server-{}", producer_id)),
                        ]),
                    };
                    stats.received.fetch_add(1, Ordering::Relaxed);
                    if tx.send(point).is_err() {
                        break;
                    }
                }
            })
        })
        .collect();
    drop(ingest_tx); // drop the extra sender

    // Wait for producers to finish
    for h in producer_handles {
        h.join().unwrap();
    }

    // Give the pipeline time to process
    thread::sleep(Duration::from_secs(2));

    // Query results
    let all = pipeline.query_all();
    println!("\nCurrent aggregates:");
    for (metric, agg) in &all {
        println!(
            "  {}: count={}, mean={:.2}, min={:.2}, max={:.2}",
            metric,
            agg.count,
            agg.mean(),
            agg.min,
            agg.max
        );
    }

    pipeline.shutdown();
}
```

## Architecture Patterns That Work

### 1. Bounded Channels Everywhere

Every channel in the system is bounded. This gives you:
- **Backpressure** — Slow consumers throttle fast producers
- **Memory bounds** — You know the maximum memory usage
- **Overload detection** — `try_send` failing tells you the system is overloaded

Never use unbounded channels in production. Ever.

### 2. Separate Thread Pools by Workload Type

```
CPU-bound work   → Fixed pool (num_cpus threads)
IO-bound work    → Larger pool or async
Periodic tasks   → Dedicated threads with tick channels
Critical paths   → Dedicated threads (no sharing)
```

Don't put CPU work and IO work in the same pool. A CPU-heavy task will monopolize threads that IO-bound tasks need for receiving data.

### 3. Graceful Shutdown Protocol

```
1. Signal shutdown (AtomicBool or channel)
2. Close input channels (drop senders)
3. Workers drain remaining work
4. Workers exit naturally when channels close
5. Join all worker threads
6. Flush any remaining state
7. Report final statistics
```

The key insight: closing channels is the shutdown signal for channel-based workers. Drop the sender, and `recv()` returns `Err`, breaking the worker loop. Clean, automatic, no special shutdown messages needed.

### 4. Metrics and Observability

Use atomics for counters. Read them from a stats reporter thread. Don't put metrics behind mutexes — the overhead defeats the purpose.

```rust
// Good: lock-free stats
stats.processed.fetch_add(1, Ordering::Relaxed);

// Bad: locking a mutex for every metric update
stats_mutex.lock().unwrap().processed += 1;
```

### 5. Error Isolation

Workers should never panic. Catch panics, log them, continue:

```rust
while let Ok(item) = rx.recv() {
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        process(item);
    }));
    if let Err(e) = result {
        eprintln!("Worker caught panic: {:?}", e);
        stats.errors.fetch_add(1, Ordering::Relaxed);
    }
}
```

One bad input shouldn't take down a worker. One bad worker shouldn't take down the pipeline.

## Choosing the Right Primitive

| Problem | Primitive | Why |
|---------|-----------|-----|
| Counters, flags | Atomics | No lock overhead |
| Read-heavy config | RwLock | Multiple concurrent readers |
| Mutable shared state | Mutex (parking_lot) | Simple, fast |
| Work distribution | Bounded channels | Backpressure, decoupling |
| Periodic tasks | tick channels + select | Clean, cancellable |
| One-time init | LazyLock / OnceLock | Zero-cost after init |
| Parallel computation | Rayon par_iter | Automatic work stealing |
| Per-thread scratch | thread_local | Zero synchronization |

## Anti-Patterns to Avoid

**1. Arc<Mutex<>> for everything.** It works but it's a crutch. Think about whether you actually need shared mutable state or whether channels would be cleaner.

**2. Unbounded spawning.** Every `thread::spawn` should be justified. If you can't predict the number of threads at design time, use a pool.

**3. Lock-free data structures when a Mutex will do.** Profile first. Mutex contention is rarely the bottleneck. Lock-free code is hard to write and harder to maintain.

**4. Ignoring backpressure.** If producers are faster than consumers, something has to give. Either buffer (bounded), drop (lossy), or block (backpressure). Decide explicitly — don't let it happen by accident.

**5. No graceful shutdown.** Every thread should have a way to stop. Every resource should have a way to be cleaned up. If you can't shut down cleanly, you can't restart cleanly either.

## The Decision Framework

When designing a concurrent Rust system:

1. **Identify the data flow.** Where does data enter? Where does it exit? What transformations happen in between?

2. **Identify the shared state.** What data needs to be accessed by multiple threads? Can you eliminate the sharing? Can you make it read-only?

3. **Choose communication patterns.** Channels for data flow. Shared state for caches and configuration. Atomics for counters and flags.

4. **Bound everything.** Queue sizes. Thread counts. Memory usage. Timeouts. If something can grow without limit, it will — usually at 3 AM.

5. **Plan for failure.** Workers crash. Channels fill up. Deadlines expire. Design each component to handle its neighbors failing.

6. **Add observability.** You can't fix what you can't see. Metrics, logging, health checks — add them from the start, not after the first incident.

That's the course. 25 lessons from thread spawning to production architecture. The compiler gives you the safety guarantees. These patterns give you the design clarity. The combination is why Rust concurrency — despite its steeper learning curve — produces more reliable systems than anything else I've used.

Build something. Break something. Ship something. The compiler has your back.
