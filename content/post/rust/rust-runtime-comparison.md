---
title: "Lesson 8: Runtime Comparison — Tokio vs async-std vs smol vs glommio"
author: Atharva Pandey
series: "Rust Async Internals & Runtime Design"
lesson: 8
keywords: [Rust, rust, async, runtime, Tokio, async-std, smol, glommio, comparison, benchmark, thread-per-core]
tags: [Rust tutorial, rust, async, runtime, internals]
date: '2025-10-02T16:55:31.000Z'
---

Every few months, someone asks me "which async runtime should I use?" and every time my answer is frustrating: "it depends." But after spending the last seven lessons understanding how runtimes work from the inside, we can finally have a real conversation about what the differences actually are, why they exist, and when each one is the right choice.

I've used all four of these runtimes in production. Tokio for most things, smol for a lightweight embedded project, glommio for a storage engine prototype, and async-std briefly before migrating away from it. Let me share what I've learned — not from reading documentation, but from debugging real problems at 2 AM.

## The Contenders

| Runtime | Architecture | I/O Backend | First Stable | Focus |
|---------|-------------|-------------|--------------|-------|
| **Tokio** | Multi-thread + work-stealing | mio (epoll/kqueue) | 2016 | General purpose, ecosystem |
| **async-std** | Multi-thread + work-stealing | polling + async-io | 2019 | stdlib-like API |
| **smol** | Single/multi thread | polling + async-io | 2020 | Minimalism |
| **glommio** | Thread-per-core | io_uring | 2020 | Max throughput, Linux-only |

## Tokio: The Default Choice

Tokio is the gorilla in the room. It's the most used, most tested, and has the largest ecosystem. When people say "async Rust," they usually mean Tokio.

**Architecture:** Work-stealing thread pool with per-thread I/O drivers. Tasks are `Send + 'static` because they can migrate between threads. There's also a `current_thread` runtime for single-threaded use.

```rust
// The standard Tokio setup
#[tokio::main]
async fn main() {
    // Multi-threaded by default, one thread per CPU core
    let listener = tokio::net::TcpListener::bind("0.0.0.0:8080")
        .await
        .unwrap();

    loop {
        let (stream, _) = listener.accept().await.unwrap();
        tokio::spawn(async move {
            handle_connection(stream).await;
        });
    }
}
```

**Strengths:**
- Massive ecosystem. Most async libraries are Tokio-native (axum, tonic, reqwest, sqlx, etc.).
- Excellent tooling: `tokio-console` for runtime inspection, `tokio::test` for async tests.
- Mature and battle-tested. Running in production at AWS, Discord, Cloudflare, and thousands of other companies.
- Comprehensive feature set: timers, channels, synchronization primitives, I/O utilities, fs, process, signal handling.

**Weaknesses:**
- Heavy dependency tree. `tokio` with all features pulls in a lot of code.
- Work-stealing adds overhead for latency-sensitive workloads.
- Tasks must be `Send`, which means no `Rc`, no `Cell`, no thread-local shortcuts.
- The `current_thread` runtime is sometimes an afterthought — some libraries assume multi-threaded.

**When I use it:** Almost always. Unless I have a specific reason not to, Tokio is the default. The ecosystem advantage alone is worth the tradeoffs.

## async-std: The stdlib Mirror

async-std tried to be what its name suggests — a standard library for async Rust, mirroring `std` APIs with async equivalents.

```rust
use async_std::net::TcpListener;
use async_std::prelude::*;
use async_std::task;

fn main() {
    task::block_on(async {
        let listener = TcpListener::bind("0.0.0.0:8080").await.unwrap();
        let mut incoming = listener.incoming();

        while let Some(stream) = incoming.next().await {
            let stream = stream.unwrap();
            task::spawn(async move {
                handle_connection(stream).await;
            });
        }
    });
}
```

**Architecture:** Uses `async-io` and `polling` crates under the hood — the same stack that `smol` uses. Work-stealing thread pool similar to Tokio, but implemented differently.

**Strengths:**
- Familiar API if you know `std`. `async_std::fs::read_to_string` mirrors `std::fs::read_to_string`.
- Lighter than Tokio for simple use cases.
- Built-in `block_on` — no need for a `#[main]` macro.

**Weaknesses:**
- Ecosystem is small. Most crates target Tokio, not async-std. In 2025, this is a real problem.
- Development has slowed considerably. The community momentum shifted to Tokio.
- Some subtle behavioral differences from `std` that can bite you.

**My honest take:** I stopped recommending async-std around 2023. The ecosystem problem is self-reinforcing — fewer users means fewer libraries, which means fewer users. If you're starting a new project, there's no compelling reason to choose async-std over Tokio or smol. That said, existing codebases using it work fine and don't urgently need migration.

## smol: Radical Simplicity

`smol` is what happens when you strip everything down to the essentials. It's a tiny runtime — the core is around 1500 lines of code — that proves you don't need Tokio's complexity for most workloads.

```rust
use smol::net::TcpListener;
use smol::io::AsyncReadExt;

fn main() -> std::io::Result<()> {
    smol::block_on(async {
        let listener = TcpListener::bind("0.0.0.0:8080").await?;

        loop {
            let (mut stream, _) = listener.accept().await?;
            smol::spawn(async move {
                let mut buf = [0u8; 1024];
                while let Ok(n) = stream.read(&mut buf).await {
                    if n == 0 { break; }
                    // process data...
                }
            })
            .detach();
        }
    })
}
```

**Architecture:** `smol` is actually a collection of small crates:
- `async-executor` — the task executor
- `async-io` — async wrapper around `polling`
- `polling` — cross-platform I/O event notification (epoll/kqueue/IOCP)
- `async-channel` — async channels
- `async-lock` — async synchronization primitives

Each crate is usable independently. You can use `async-executor` with your own I/O layer, or use `async-io` with a different executor.

**Strengths:**
- Tiny. The whole runtime compiles fast and produces small binaries.
- Composable. Pick the pieces you need.
- Compatible with `futures` traits — works with many Tokio-independent crates.
- Good for learning. The code is readable in an afternoon.

**Weaknesses:**
- Fewer features. No built-in timer wheel (uses OS timers), no `tokio::select!` equivalent.
- Smaller ecosystem than Tokio.
- Less optimized for high-concurrency workloads.
- The blocking thread pool can sometimes behave unexpectedly.

**When I use it:** Embedded projects, CLI tools, and situations where I want a small dependency footprint. Also excellent for learning — if you read the smol source after this course, everything will click.

## glommio: The Performance Radical

`glommio` takes a completely different architectural approach: thread-per-core with io_uring. No work-stealing, no task migration, no shared state between cores. Each core is an island.

```rust
use glommio::prelude::*;
use glommio::net::TcpListener;

fn main() {
    // One executor per core — tasks never migrate
    LocalExecutorBuilder::default()
        .spawn(|| async move {
            let listener = TcpListener::bind("0.0.0.0:8080").unwrap();

            loop {
                let stream = listener.accept().await.unwrap();
                glommio::spawn_local(async move {
                    handle_connection(stream).await;
                })
                .detach();
            }
        })
        .unwrap()
        .join()
        .unwrap();
}
```

**Architecture:** Each CPU core runs an independent executor with its own io_uring instance. Tasks are `!Send` — they never leave their core. Cores communicate through shared-nothing message passing.

**Strengths:**
- Highest possible I/O throughput on Linux. io_uring with SQPOLL can achieve near-kernel-bypass performance.
- Predictable latencies. No work-stealing means no cache invalidation from task migration.
- `!Send` tasks. You can use `Rc`, `Cell`, thread-local state — no synchronization overhead.
- Excellent for storage engines and data-intensive applications.

**Weaknesses:**
- Linux-only. Requires kernel 5.8+ for full io_uring support.
- Load balancing is your problem. If one core is busy and another is idle, glommio won't fix that automatically.
- Smaller ecosystem. Most async libraries expect `Send` tasks.
- Learning curve for the shared-nothing model.

**When I use it:** Storage engines, custom databases, network proxies that need predictable tail latencies. If you're building infrastructure that lives on dedicated Linux servers and performance is the primary concern, glommio is worth serious consideration.

## Head-to-Head: Practical Differences

### Task Spawning

```rust
// Tokio — requires Send + 'static
tokio::spawn(async move {
    // Can't use Rc, Cell, or &references to local data
    let data = Arc::new(Mutex::new(vec![1, 2, 3]));
    process(data).await;
});

// glommio — allows !Send, !Sync
glommio::spawn_local(async {
    // Can use Rc, Cell, RefCell freely
    let data = Rc::new(RefCell::new(vec![1, 2, 3]));
    process(data).await;
});

// smol — requires Send + 'static (for multi-thread)
smol::spawn(async move {
    process(data).await;
}).detach();

// smol — !Send with LocalExecutor
let local_ex = smol::LocalExecutor::new();
local_ex.spawn(async {
    // !Send is fine here
    let data = Rc::new(vec![1, 2, 3]);
    process(data).await;
}).detach();
```

### File I/O

This is where the runtimes diverge most:

```rust
// Tokio — uses a blocking thread pool for file I/O
// (because epoll doesn't work with regular files)
let data = tokio::fs::read("big_file.dat").await?;
// Under the hood: spawns onto tokio::task::spawn_blocking

// glommio — uses io_uring for true async file I/O
let file = glommio::io::DmaFile::open("big_file.dat").await?;
let buf = file.read_at(0, 4096).await?;
// Actually async — no blocking threads involved

// smol — uses async-io's blocking pool
let data = smol::fs::read("big_file.dat").await?;
// Similar to Tokio — blocking pool under the hood
```

Glommio's advantage for file I/O is significant. Tokio's file operations aren't truly async — they run on a blocking thread pool. For a database engine that does heavy random reads, the difference between "async via thread pool" and "actually async via io_uring" is measurable.

### Runtime Configuration

```rust
// Tokio — highly configurable
let rt = tokio::runtime::Builder::new_multi_thread()
    .worker_threads(4)
    .max_blocking_threads(512)
    .enable_all()
    .thread_name("my-worker")
    .on_thread_start(|| println!("thread started"))
    .build()
    .unwrap();

// smol — minimal configuration, relies on env vars
// SMOL_THREADS=4 sets the executor thread count
smol::block_on(async { /* ... */ });

// glommio — per-core configuration
LocalExecutorBuilder::default()
    .pin_to_cpu(0)  // pin to specific CPU core
    .spin_before_park(Duration::from_micros(100))
    .name("core-0")
    .spawn(|| async { /* ... */ })
    .unwrap();
```

### Error Handling Patterns

```rust
// Tokio JoinHandle — can detect panics
let handle = tokio::spawn(async { panic!("oops") });
match handle.await {
    Ok(val) => println!("got: {:?}", val),
    Err(e) if e.is_panic() => println!("task panicked"),
    Err(e) if e.is_cancelled() => println!("task cancelled"),
    Err(e) => println!("other error: {:?}", e),
}

// smol Task — similar behavior
let task = smol::spawn(async { 42 });
let result = task.await; // panics propagate

// glommio — panics abort the executor
// This is deliberate — in thread-per-core, a panic
// on one core shouldn't affect others
```

## Performance Characteristics

Rather than giving you synthetic benchmarks (which are always misleading), here's what I've observed in real workloads:

**HTTP server throughput (requests/sec):**
- Tokio and smol are within 5-10% of each other for typical web workloads.
- Glommio can be 20-40% faster for high-connection-count scenarios on Linux, primarily due to io_uring.
- The difference shrinks as application logic (database queries, business logic) dominates.

**Tail latency (p99):**
- Tokio's work-stealing adds 5-15 microseconds of jitter from task migration.
- Glommio's thread-per-core model gives sub-microsecond jitter in I/O dispatch.
- For most applications, this difference is noise. For trading systems, it's everything.

**Memory usage:**
- smol has the lowest baseline memory usage.
- Tokio's task representation is more optimized (smaller per-task overhead).
- Glommio's io_uring buffers use more memory but avoid copies.

**Compile time:**
- smol: fastest to compile by far.
- Tokio with all features: slowest. `tokio = { features = ["full"] }` pulls in a lot.
- glommio: moderate, but io_uring bindings add some time.

## The Decision Framework

Here's my actual decision tree, not the diplomatic one:

**Default choice → Tokio.** Unless you have a specific reason not to. The ecosystem advantage is overwhelming. When you hit a problem at 2 AM, Stack Overflow has Tokio answers.

**Minimal dependency tree → smol.** If you're building a CLI tool, a small service, or care about compile times. smol gives you 90% of Tokio's capabilities at 10% of the dependency weight.

**Maximum I/O performance on Linux → glommio.** If you're building a storage engine, a custom database, or a proxy where every microsecond of latency matters. Accept the Linux-only constraint and the shared-nothing architecture.

**async-std → migrate away when convenient.** I know this sounds harsh, but the ecosystem momentum isn't there anymore. If you have a working async-std codebase, it's fine — but new projects should pick one of the other three.

## Mixing Runtimes

One last thing — you can mix runtimes in the same application:

```rust
// Use Tokio for the main app
#[tokio::main]
async fn main() {
    // But use smol's executor for a specific subsystem
    let result = tokio::task::spawn_blocking(|| {
        smol::block_on(async {
            // smol-based code here
            42
        })
    })
    .await
    .unwrap();

    println!("Got: {}", result);
}
```

This is more common than you'd think. I've seen applications that use Tokio for HTTP handling but glommio for disk I/O, bridged via channels. The async traits (`Future`, `AsyncRead`, `AsyncWrite`) are runtime-agnostic — only the I/O types and spawn functions are runtime-specific.

## What We've Covered

Over these eight lessons, we went from "what is a Future?" to "how do I design a custom runtime?" Let me recap the key insights:

1. **Futures are lazy state machines.** They do nothing until polled. Each `.await` is a state transition.
2. **Wakers are the notification mechanism.** They bridge OS events to the executor.
3. **io_uring changes everything** for Linux I/O — completion-based instead of readiness-based.
4. **epoll/kqueue are the foundation** of traditional async I/O — readiness notification.
5. **The reactor pattern** connects OS events to futures through wakers.
6. **Work-stealing** balances load across cores but adds cache invalidation overhead.
7. **Custom runtimes** make sense when you need thread-per-core, custom I/O, or minimal footprint.
8. **Runtime choice** depends on ecosystem, performance profile, and deployment constraints.

None of this is magic. It's engineering — tradeoffs, measurements, and design decisions. The async Rust ecosystem gives you more control over these decisions than any other language I've worked with. That control comes with complexity, but now you understand what that complexity is actually doing.
