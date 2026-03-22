---
title: "Lesson 2: std::thread — Spawning and joining"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 2
keywords: [Rust, rust, concurrency, threads, std::thread, spawn, join, JoinHandle]
tags: [Rust tutorial, rust, concurrency]
date: '2024-11-07T11:45:00.000Z'
---

A few years back I was reviewing a Go service that spawned goroutines like confetti at a parade — hundreds of them, no tracking, no lifecycle management. When the service shut down, half those goroutines just vanished mid-work. Orphaned database connections everywhere. The Go runtime made it *so easy* to fire off concurrent work that nobody stopped to think about cleanup.

Rust's threading model forces you to think about it. Every thread gives you a handle. Every handle demands acknowledgment. You can ignore it — but you have to do so explicitly.

## Spawning a Thread

The basic API lives in `std::thread`. You call `thread::spawn` with a closure, and you get back a `JoinHandle`:

```rust
use std::thread;
use std::time::Duration;

fn main() {
    let handle = thread::spawn(|| {
        for i in 1..=5 {
            println!("spawned thread: {}", i);
            thread::sleep(Duration::from_millis(100));
        }
    });

    for i in 1..=3 {
        println!("main thread: {}", i);
        thread::sleep(Duration::from_millis(150));
    }

    handle.join().unwrap();
    println!("done");
}
```

A few things to notice:

- `thread::spawn` takes a closure (we'll talk about `move` closures next lesson)
- It returns a `JoinHandle<T>` where `T` is whatever the closure returns
- `join()` blocks the current thread until the spawned thread finishes
- `join()` returns a `Result` — it's `Err` if the spawned thread panicked

That last point matters. If a spawned thread panics, the panic is *captured* in the `JoinHandle`. It doesn't crash your whole program (unlike C++ where an unhandled exception in a thread calls `std::terminate`). You decide what to do with it.

## What Happens Without join()

Here's a subtle gotcha:

```rust
use std::thread;
use std::time::Duration;

fn main() {
    thread::spawn(|| {
        for i in 1..=10 {
            println!("background: {}", i);
            thread::sleep(Duration::from_millis(100));
        }
    });

    println!("main is done");
    // main thread exits → process exits → spawned thread is killed
}
```

When `main` returns, the process terminates. Any spawned threads that haven't finished? Gone. No cleanup, no warning, no nothing.

This is actually sane behavior — it's what every OS does. But it means you *must* join threads if their work matters. Don't just fire and forget unless you genuinely don't care about the result.

## JoinHandle and Return Values

The spawned thread's closure can return a value, and you get it through `join()`:

```rust
use std::thread;

fn main() {
    let handle = thread::spawn(|| {
        let mut sum = 0u64;
        for i in 1..=1_000_000 {
            sum += i;
        }
        sum
    });

    let result = handle.join().unwrap();
    println!("Sum: {}", result); // 500000500000
}
```

The type signature is `JoinHandle<u64>` here because the closure returns a `u64`. `join()` gives you `Result<u64, Box<dyn Any + Send>>` — the `Err` variant contains the panic payload if the thread panicked.

## Handling Panics

Spawned threads can panic without crashing the program:

```rust
use std::thread;

fn main() {
    let handle = thread::spawn(|| {
        panic!("something went wrong!");
    });

    match handle.join() {
        Ok(val) => println!("Thread returned: {:?}", val),
        Err(e) => {
            // e is Box<dyn Any + Send>
            if let Some(msg) = e.downcast_ref::<&str>() {
                println!("Thread panicked with: {}", msg);
            } else if let Some(msg) = e.downcast_ref::<String>() {
                println!("Thread panicked with: {}", msg);
            } else {
                println!("Thread panicked with an unknown payload");
            }
        }
    }

    println!("Main thread continues fine");
}
```

This is one of Rust's underappreciated features. You can isolate failures in concurrent work. A computation thread panics? Catch it, log it, retry if needed. Your main thread stays alive.

## Spawning Multiple Threads

The pattern for spawning a bunch of threads and collecting results:

```rust
use std::thread;

fn main() {
    let mut handles = vec![];

    for i in 0..8 {
        let handle = thread::spawn(move || {
            let result = i * i;
            println!("Thread {} computed {}", i, result);
            result
        });
        handles.push(handle);
    }

    let results: Vec<i32> = handles
        .into_iter()
        .map(|h| h.join().unwrap())
        .collect();

    println!("All results: {:?}", results);
}
```

Notice the `move` keyword on the closure — we'll cover that properly next lesson. For now just know it transfers ownership of `i` into the closure so the thread can use it.

## Thread Builder

Want more control? Use `thread::Builder`:

```rust
use std::thread;

fn main() {
    let builder = thread::Builder::new()
        .name("worker-1".to_string())
        .stack_size(4 * 1024 * 1024); // 4 MB stack

    let handle = builder.spawn(|| {
        let name = thread::current().name().unwrap_or("unnamed").to_string();
        println!("Running on thread: {}", name);
    }).expect("Failed to spawn thread");

    handle.join().unwrap();
}
```

Named threads are extremely useful for debugging. When you're looking at log output from 20 threads, "worker-3" tells you a lot more than "ThreadId(7)".

The `Builder::spawn` returns `io::Result<JoinHandle<T>>` instead of just `JoinHandle<T>` — because thread creation can actually fail (usually when the OS runs out of resources).

## How Many Threads?

OS threads are not free. Each one typically gets:

- A stack (usually 2-8 MB by default)
- Kernel scheduling overhead
- Context switch costs

A few hundred threads? Fine. Ten thousand? You'll start feeling it. A million? Not happening.

```rust
use std::thread;

fn main() {
    // This is fine
    let handles: Vec<_> = (0..100)
        .map(|i| {
            thread::spawn(move || {
                // do work
                i * 2
            })
        })
        .collect();

    let results: Vec<_> = handles.into_iter().map(|h| h.join().unwrap()).collect();
    println!("Processed {} items", results.len());
}
```

For CPU-bound work, you generally want threads equal to the number of CPU cores. For IO-bound work, you can go higher — but at some point you should switch to async instead of spawning more OS threads.

A good rule of thumb: `num_cpus::get()` for CPU work, and a fixed thread pool for IO work. We'll build thread pools later in this series.

## thread::scope — Scoped Threads (Rust 1.63+)

Standard `thread::spawn` requires `'static` lifetimes — everything the closure captures must be owned or live forever. That's because the compiler can't prove the spawned thread won't outlive the data it references.

Scoped threads fix this:

```rust
use std::thread;

fn main() {
    let mut data = vec![1, 2, 3, 4, 5];

    thread::scope(|s| {
        s.spawn(|| {
            println!("Thread sees: {:?}", &data);
        });

        s.spawn(|| {
            println!("This thread also sees: {:?}", &data);
        });
    });
    // Both threads are guaranteed to finish here

    // Safe to mutate data again — threads are done
    data.push(6);
    println!("After threads: {:?}", data);
}
```

`thread::scope` guarantees that all threads spawned within the scope finish before the scope exits. This means borrowing local data is safe — the compiler knows the data will outlive the threads.

This is a game-changer for parallel computations that don't need to persist beyond a function call.

## Common Mistakes

**Mistake 1: Detaching threads unintentionally**

```rust
// BAD — handle is dropped, thread runs detached
fn fire_and_forget() {
    let _ = thread::spawn(|| {
        // This thread might get killed when the process exits
        important_work();
    });
}

// GOOD — explicitly manage the lifecycle
fn tracked_work() -> thread::JoinHandle<()> {
    thread::spawn(|| {
        important_work();
    })
}
```

**Mistake 2: Joining inside a loop that spawns**

```rust
// BAD — sequential, not parallel!
for i in 0..10 {
    let handle = thread::spawn(move || compute(i));
    handle.join().unwrap(); // blocks until this thread finishes
    // Next iteration only starts after this thread is done
}

// GOOD — spawn all, then join all
let handles: Vec<_> = (0..10)
    .map(|i| thread::spawn(move || compute(i)))
    .collect();
let results: Vec<_> = handles.into_iter().map(|h| h.join().unwrap()).collect();
```

That second mistake is surprisingly common. I've seen it in production code from experienced engineers. The spawn-then-join pattern needs to be separate loops — spawn all first, join all second.

## When to Use Raw Threads

Raw `std::thread` is appropriate when:

- You have a small, fixed number of long-running tasks
- You're doing CPU-bound work and want explicit control
- You're building higher-level abstractions (thread pools, etc.)

For most concurrent workloads, you'll eventually reach for something higher-level — Rayon for data parallelism, a thread pool for bounded concurrency, or async for IO-heavy work. But understanding raw threads is the foundation everything else builds on.

---

Next up — `move` closures, and why the compiler keeps yelling at you about lifetimes when you try to use data in spawned threads.
