---
title: "Lesson 12: Worker Pool Patterns — Bounded concurrency"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 12
keywords: [Rust, rust, concurrency, worker pool, thread pool, bounded concurrency, task queue]
tags: [Rust tutorial, rust, concurrency]
date: '2024-11-27T08:35:00.000Z'
---

I blew up a production database once by spawning unlimited concurrent connections. A batch job that normally processed 100 items suddenly got 50,000. Each item opened a database connection. The connection pool maxed out, then the OS ran out of file descriptors. The database server stopped accepting connections from any service. All because I wrote `for item in items { thread::spawn(...) }` without thinking about bounds.

Worker pools solve this. Fixed number of threads, bounded queue, backpressure when the system is overloaded. After that incident, I never write unbounded concurrency again.

## The Problem: Unbounded Spawning

```rust
use std::thread;

fn process(id: usize) {
    // Imagine this opens a DB connection, makes HTTP calls, etc.
    std::thread::sleep(std::time::Duration::from_millis(100));
    println!("Processed {}", id);
}

fn main() {
    let mut handles = vec![];

    // DON'T DO THIS with large inputs
    for i in 0..50_000 {
        handles.push(thread::spawn(move || {
            process(i);
        }));
    }

    for h in handles {
        h.join().unwrap();
    }
}
```

50,000 OS threads. Each with a multi-megabyte stack. That's 100+ GB of virtual memory and thousands of context switches per second. Even if it doesn't crash, performance will be terrible.

## Building a Worker Pool from Scratch

Let's build one. A worker pool has:

1. A fixed number of worker threads
2. A channel for submitting tasks
3. Graceful shutdown

```rust
use std::sync::mpsc;
use std::thread;

type Job = Box<dyn FnOnce() + Send + 'static>;

struct ThreadPool {
    workers: Vec<thread::JoinHandle<()>>,
    sender: Option<mpsc::Sender<Job>>,
}

impl ThreadPool {
    fn new(size: usize) -> Self {
        let (tx, rx) = mpsc::channel::<Job>();
        let rx = std::sync::Arc::new(std::sync::Mutex::new(rx));
        let mut workers = Vec::with_capacity(size);

        for id in 0..size {
            let rx = std::sync::Arc::clone(&rx);
            workers.push(thread::spawn(move || {
                loop {
                    // Lock the receiver, grab one job, release the lock
                    let job = rx.lock().unwrap().recv();
                    match job {
                        Ok(job) => {
                            println!("[Worker {}] executing job", id);
                            job();
                        }
                        Err(_) => {
                            println!("[Worker {}] channel closed, shutting down", id);
                            break;
                        }
                    }
                }
            }));
        }

        ThreadPool {
            workers,
            sender: Some(tx),
        }
    }

    fn execute<F>(&self, f: F)
    where
        F: FnOnce() + Send + 'static,
    {
        let job = Box::new(f);
        self.sender.as_ref().unwrap().send(job).unwrap();
    }
}

impl Drop for ThreadPool {
    fn drop(&mut self) {
        // Drop the sender to close the channel
        drop(self.sender.take());

        // Wait for all workers to finish
        for worker in self.workers.drain(..) {
            worker.join().unwrap();
        }
    }
}

fn main() {
    let pool = ThreadPool::new(4);

    for i in 0..20 {
        pool.execute(move || {
            std::thread::sleep(std::time::Duration::from_millis(100));
            println!("Task {} complete", i);
        });
    }

    // pool is dropped here — waits for all tasks to finish
    println!("All tasks submitted");
}
```

This is a real, working thread pool. Four threads handle 20 tasks. At most 4 tasks run concurrently. The `Drop` implementation ensures graceful shutdown — all submitted tasks complete before the pool is destroyed.

## Using Crossbeam for a Better Pool

The `Arc<Mutex<Receiver>>` in our pool is a bottleneck — all workers contend on one lock to receive tasks. Crossbeam's MPMC channel eliminates this:

```rust
use crossbeam_channel::{bounded, Sender, Receiver};
use std::thread;

type Job = Box<dyn FnOnce() + Send + 'static>;

struct WorkerPool {
    sender: Sender<Job>,
    workers: Vec<thread::JoinHandle<()>>,
}

impl WorkerPool {
    fn new(num_workers: usize, queue_size: usize) -> Self {
        let (tx, rx) = bounded::<Job>(queue_size);
        let mut workers = Vec::with_capacity(num_workers);

        for id in 0..num_workers {
            let rx = rx.clone();
            workers.push(thread::spawn(move || {
                while let Ok(job) = rx.recv() {
                    job();
                }
                println!("Worker {} shutting down", id);
            }));
        }

        WorkerPool {
            sender: tx,
            workers,
        }
    }

    fn submit<F>(&self, f: F)
    where
        F: FnOnce() + Send + 'static,
    {
        // This blocks if the queue is full — backpressure!
        self.sender.send(Box::new(f)).unwrap();
    }

    fn shutdown(self) {
        drop(self.sender); // close the channel
        for w in self.workers {
            w.join().unwrap();
        }
    }
}

fn main() {
    let pool = WorkerPool::new(4, 16); // 4 workers, queue capacity 16

    for i in 0..100 {
        pool.submit(move || {
            std::thread::sleep(std::time::Duration::from_millis(50));
            println!("Task {} done", i);
        });
    }

    pool.shutdown();
    println!("All done");
}
```

Two improvements: crossbeam's receiver doesn't need a mutex (lock-free MPMC), and the bounded channel provides backpressure — `submit` blocks when 16 tasks are queued, preventing memory from growing unbounded.

## Worker Pool with Results

Often you need results back from your tasks. Use oneshot channels:

```rust
use crossbeam_channel::{bounded, Sender};
use std::thread;

type Job = Box<dyn FnOnce() + Send + 'static>;

struct Pool {
    sender: Sender<Job>,
    _workers: Vec<thread::JoinHandle<()>>,
}

impl Pool {
    fn new(size: usize) -> Self {
        let (tx, rx) = bounded::<Job>(size * 2);
        let mut workers = Vec::with_capacity(size);

        for _ in 0..size {
            let rx = rx.clone();
            workers.push(thread::spawn(move || {
                while let Ok(job) = rx.recv() {
                    job();
                }
            }));
        }

        Pool {
            sender: tx,
            _workers: workers,
        }
    }

    fn submit<F, R>(&self, f: F) -> crossbeam_channel::Receiver<R>
    where
        F: FnOnce() -> R + Send + 'static,
        R: Send + 'static,
    {
        let (result_tx, result_rx) = bounded(1);
        let job = Box::new(move || {
            let result = f();
            let _ = result_tx.send(result);
        });
        self.sender.send(job).unwrap();
        result_rx
    }
}

fn main() {
    let pool = Pool::new(4);

    // Submit tasks and collect futures (result receivers)
    let futures: Vec<_> = (0..10)
        .map(|i| {
            pool.submit(move || {
                std::thread::sleep(std::time::Duration::from_millis(100));
                i * i
            })
        })
        .collect();

    // Collect results
    let results: Vec<i32> = futures.into_iter().map(|f| f.recv().unwrap()).collect();
    println!("Results: {:?}", results);
}
```

Each submitted task gets a oneshot channel for its result. The caller holds the receiver and can block on it whenever they need the result. Poor man's futures, essentially.

## Handling Panics in Workers

If a worker panics, it dies and the pool loses a thread. You need to detect this and respawn:

```rust
use crossbeam_channel::{bounded, Receiver, Sender};
use std::thread;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

type Job = Box<dyn FnOnce() + Send + 'static>;

struct ResilientPool {
    sender: Sender<Job>,
    active_workers: Arc<AtomicUsize>,
}

fn spawn_worker(
    id: usize,
    rx: Receiver<Job>,
    counter: Arc<AtomicUsize>,
) -> thread::JoinHandle<()> {
    counter.fetch_add(1, Ordering::SeqCst);
    let counter_clone = counter.clone();
    thread::Builder::new()
        .name(format!("worker-{}", id))
        .spawn(move || {
            while let Ok(job) = rx.recv() {
                // Catch panics so the worker thread survives
                let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(job));
                if let Err(e) = result {
                    eprintln!("Worker {} caught panic: {:?}", id, e);
                }
            }
            counter_clone.fetch_sub(1, Ordering::SeqCst);
        })
        .unwrap()
}

impl ResilientPool {
    fn new(size: usize) -> Self {
        let (tx, rx) = bounded::<Job>(size * 2);
        let counter = Arc::new(AtomicUsize::new(0));

        for id in 0..size {
            spawn_worker(id, rx.clone(), counter.clone());
        }

        ResilientPool {
            sender: tx,
            active_workers: counter,
        }
    }

    fn submit<F>(&self, f: F)
    where
        F: FnOnce() + Send + 'static,
    {
        self.sender.send(Box::new(f)).unwrap();
    }

    fn active_count(&self) -> usize {
        self.active_workers.load(Ordering::SeqCst)
    }
}

fn main() {
    let pool = ResilientPool::new(4);
    println!("Active workers: {}", pool.active_count());

    // Submit a panicking task
    pool.submit(|| {
        panic!("intentional panic");
    });

    // Submit normal tasks — they still run
    for i in 0..10 {
        pool.submit(move || {
            println!("Task {} completed", i);
        });
    }

    std::thread::sleep(std::time::Duration::from_secs(1));
    println!("Active workers after panic: {}", pool.active_count());
}
```

By wrapping each job in `catch_unwind`, the worker survives panicking tasks and continues processing the next job. This is how production-grade thread pools work.

## Sizing Your Pool

**CPU-bound work:** `num_cpus::get()` threads. More than that and you're just adding context switch overhead.

**IO-bound work:** More threads than cores, because threads spend most of their time waiting. A common formula: `num_cpus::get() * 2` or even higher for heavy IO.

**Mixed workloads:** Separate pools. A CPU pool sized to cores, an IO pool sized larger. Don't let IO-bound tasks starve CPU-bound work or vice versa.

```rust
fn main() {
    let cpu_cores = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(4);

    println!("Available cores: {}", cpu_cores);
    println!("CPU pool size: {}", cpu_cores);
    println!("IO pool size: {}", cpu_cores * 2);
}
```

## When to Use What

- **Rayon** — Data parallelism on collections. Use `par_iter()` and don't think about threads.
- **Custom worker pool** — When you need control over queue depth, backpressure, and worker lifecycle.
- **Thread per connection** — Fine for a small number of long-lived connections. Don't scale past dozens.
- **Async runtime** — For thousands of concurrent IO operations. Different paradigm entirely.

The worker pool is the workhorse of bounded concurrency. Learn to build one, understand the trade-offs, and you'll handle most concurrent workloads with confidence.

---

Next — fan-out/fan-in patterns for parallel pipelines.
