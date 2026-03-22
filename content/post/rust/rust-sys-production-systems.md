---
title: "Lesson 12: Production Systems Software — Databases, runtimes, proxies"
author: Atharva Pandey
series: "Rust for Systems Programming"
lesson: 12
keywords: [Rust, rust, systems programming, database, runtime, proxy, production, storage engine, network proxy]
tags: [Rust tutorial, rust, systems, low-level]
date: '2025-08-08T09:33:27.000Z'
---

I've been building systems software professionally for a while now, and here's what I've noticed: the skills we've covered in this course — `no_std` programming, memory-mapped I/O, custom allocators, interrupt handlers, network protocols — they all converge when you build production systems software. A database engine is just a file system on top of a storage engine with a query processor. A network proxy is packet parsing plus connection management. A language runtime is memory management plus a scheduler.

This final lesson connects the dots. We're going to look at how real production systems are built in Rust, with enough code to understand the architecture, and enough context to know where to go deeper.

## Building a Storage Engine

Every database starts with a storage engine. Let's build a simplified LSM-tree (Log-Structured Merge-tree), the architecture behind RocksDB, LevelDB, and Cassandra:

```rust
use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::io::{self, BufReader, BufWriter, Read, Write, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::sync::{Arc, RwLock, Mutex};

/// Write-Ahead Log — ensures durability
struct Wal {
    file: BufWriter<File>,
    path: PathBuf,
}

impl Wal {
    fn new(path: &Path) -> io::Result<Self> {
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)?;
        Ok(Self {
            file: BufWriter::new(file),
            path: path.to_path_buf(),
        })
    }

    fn append(&mut self, key: &[u8], value: Option<&[u8]>) -> io::Result<()> {
        // Format: [key_len:4][value_len:4][key][value]
        // value_len = u32::MAX means deletion (tombstone)
        let key_len = key.len() as u32;
        self.file.write_all(&key_len.to_le_bytes())?;

        match value {
            Some(v) => {
                let val_len = v.len() as u32;
                self.file.write_all(&val_len.to_le_bytes())?;
                self.file.write_all(key)?;
                self.file.write_all(v)?;
            }
            None => {
                self.file.write_all(&u32::MAX.to_le_bytes())?;
                self.file.write_all(key)?;
            }
        }

        self.file.flush()?; // fsync for durability
        Ok(())
    }
}

/// In-memory table (memtable) — sorted key-value store
struct MemTable {
    data: BTreeMap<Vec<u8>, Option<Vec<u8>>>, // None = tombstone (deleted)
    size: usize,
}

impl MemTable {
    fn new() -> Self {
        Self {
            data: BTreeMap::new(),
            size: 0,
        }
    }

    fn put(&mut self, key: Vec<u8>, value: Vec<u8>) {
        self.size += key.len() + value.len();
        self.data.insert(key, Some(value));
    }

    fn delete(&mut self, key: Vec<u8>) {
        self.size += key.len();
        self.data.insert(key, None);
    }

    fn get(&self, key: &[u8]) -> Option<Option<&[u8]>> {
        self.data.get(key).map(|v| v.as_deref())
    }
}

/// Sorted String Table — immutable on-disk sorted file
struct SSTable {
    path: PathBuf,
    index: BTreeMap<Vec<u8>, u64>, // key -> offset in file
}

impl SSTable {
    /// Write a memtable to disk as an SSTable
    fn from_memtable(path: &Path, memtable: &MemTable) -> io::Result<Self> {
        let mut file = BufWriter::new(File::create(path)?);
        let mut index = BTreeMap::new();
        let mut offset: u64 = 0;

        for (key, value) in &memtable.data {
            index.insert(key.clone(), offset);

            let key_len = key.len() as u32;
            file.write_all(&key_len.to_le_bytes())?;
            offset += 4;

            match value {
                Some(v) => {
                    let val_len = v.len() as u32;
                    file.write_all(&val_len.to_le_bytes())?;
                    file.write_all(key)?;
                    file.write_all(v)?;
                    offset += 4 + key.len() as u64 + v.len() as u64;
                }
                None => {
                    file.write_all(&u32::MAX.to_le_bytes())?;
                    file.write_all(key)?;
                    offset += 4 + key.len() as u64;
                }
            }
        }

        file.flush()?;
        Ok(Self {
            path: path.to_path_buf(),
            index,
        })
    }

    fn get(&self, key: &[u8]) -> io::Result<Option<Option<Vec<u8>>>> {
        let offset = match self.index.get(key) {
            Some(o) => *o,
            None => return Ok(None),
        };

        let mut file = File::open(&self.path)?;
        file.seek(SeekFrom::Start(offset))?;

        let mut buf = [0u8; 4];
        file.read_exact(&mut buf)?;
        let key_len = u32::from_le_bytes(buf) as usize;

        file.read_exact(&mut buf)?;
        let val_len = u32::from_le_bytes(buf);

        // Skip key
        let mut key_buf = vec![0u8; key_len];
        file.read_exact(&mut key_buf)?;

        if val_len == u32::MAX {
            Ok(Some(None)) // Tombstone
        } else {
            let mut val_buf = vec![0u8; val_len as usize];
            file.read_exact(&mut val_buf)?;
            Ok(Some(Some(val_buf)))
        }
    }
}

/// The LSM-tree storage engine
pub struct LsmEngine {
    memtable: RwLock<MemTable>,
    wal: Mutex<Wal>,
    sstables: RwLock<Vec<SSTable>>,
    data_dir: PathBuf,
    memtable_size_limit: usize,
    next_sstable_id: Mutex<u64>,
}

impl LsmEngine {
    pub fn open(data_dir: &Path) -> io::Result<Self> {
        fs::create_dir_all(data_dir)?;

        let wal_path = data_dir.join("wal.log");
        let wal = Wal::new(&wal_path)?;

        Ok(Self {
            memtable: RwLock::new(MemTable::new()),
            wal: Mutex::new(wal),
            sstables: RwLock::new(Vec::new()),
            data_dir: data_dir.to_path_buf(),
            memtable_size_limit: 4 * 1024 * 1024, // 4MB
            next_sstable_id: Mutex::new(0),
        })
    }

    pub fn put(&self, key: &[u8], value: &[u8]) -> io::Result<()> {
        // Write to WAL first (durability)
        self.wal.lock().unwrap().append(key, Some(value))?;

        // Then write to memtable
        let mut memtable = self.memtable.write().unwrap();
        memtable.put(key.to_vec(), value.to_vec());

        // Check if memtable should be flushed
        if memtable.size >= self.memtable_size_limit {
            self.flush_memtable(&mut memtable)?;
        }

        Ok(())
    }

    pub fn get(&self, key: &[u8]) -> io::Result<Option<Vec<u8>>> {
        // Check memtable first (most recent data)
        {
            let memtable = self.memtable.read().unwrap();
            if let Some(result) = memtable.get(key) {
                return match result {
                    Some(value) => Ok(Some(value.to_vec())),
                    None => Ok(None), // Tombstone — key was deleted
                };
            }
        }

        // Check SSTables from newest to oldest
        let sstables = self.sstables.read().unwrap();
        for sstable in sstables.iter().rev() {
            if let Some(result) = sstable.get(key)? {
                return match result {
                    Some(value) => Ok(Some(value)),
                    None => Ok(None), // Tombstone
                };
            }
        }

        Ok(None) // Key not found
    }

    pub fn delete(&self, key: &[u8]) -> io::Result<()> {
        self.wal.lock().unwrap().append(key, None)?;

        let mut memtable = self.memtable.write().unwrap();
        memtable.delete(key.to_vec());

        if memtable.size >= self.memtable_size_limit {
            self.flush_memtable(&mut memtable)?;
        }

        Ok(())
    }

    fn flush_memtable(&self, memtable: &mut MemTable) -> io::Result<()> {
        let mut id = self.next_sstable_id.lock().unwrap();
        let path = self.data_dir.join(format!("sstable_{:06}.dat", *id));
        *id += 1;

        let sstable = SSTable::from_memtable(&path, memtable)?;

        let mut sstables = self.sstables.write().unwrap();
        sstables.push(sstable);

        // Reset memtable
        *memtable = MemTable::new();

        Ok(())
    }
}
```

This is the core architecture of engines like RocksDB (used in CockroachDB, TiKV) and the Rust-native `sled` embedded database. The LSM pattern gives you fast writes (sequential I/O), durable storage (WAL), and sorted iteration (SSTables). The tradeoff is read amplification — a point lookup might check the memtable plus multiple SSTables.

Production engines add bloom filters (reduce unnecessary SSTable reads), compaction (merge SSTables to reduce read amplification), compression, concurrent compaction, rate limiting, and much more.

## Building a Network Proxy

Network proxies sit between clients and servers, routing, load balancing, or transforming traffic. Here's a simplified TCP proxy:

```rust
use std::io::{self, Read, Write};
use std::net::{TcpListener, TcpStream, SocketAddr};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::thread;
use std::time::{Duration, Instant};

struct ProxyConfig {
    listen_addr: SocketAddr,
    backends: Vec<SocketAddr>,
}

struct ProxyStats {
    connections_total: AtomicU64,
    connections_active: AtomicU64,
    bytes_sent: AtomicU64,
    bytes_received: AtomicU64,
}

impl ProxyStats {
    fn new() -> Self {
        Self {
            connections_total: AtomicU64::new(0),
            connections_active: AtomicU64::new(0),
            bytes_sent: AtomicU64::new(0),
            bytes_received: AtomicU64::new(0),
        }
    }
}

struct LoadBalancer {
    backends: Vec<SocketAddr>,
    next: AtomicU64,
}

impl LoadBalancer {
    fn new(backends: Vec<SocketAddr>) -> Self {
        Self {
            backends,
            next: AtomicU64::new(0),
        }
    }

    /// Round-robin backend selection
    fn next_backend(&self) -> SocketAddr {
        let idx = self.next.fetch_add(1, Ordering::Relaxed) as usize;
        self.backends[idx % self.backends.len()]
    }
}

pub struct TcpProxy {
    config: ProxyConfig,
    stats: Arc<ProxyStats>,
    lb: Arc<LoadBalancer>,
}

impl TcpProxy {
    pub fn new(config: ProxyConfig) -> Self {
        let lb = Arc::new(LoadBalancer::new(config.backends.clone()));
        Self {
            config,
            stats: Arc::new(ProxyStats::new()),
            lb,
        }
    }

    pub fn run(&self) -> io::Result<()> {
        let listener = TcpListener::bind(self.config.listen_addr)?;
        println!("Proxy listening on {}", self.config.listen_addr);

        // Stats reporting thread
        let stats = self.stats.clone();
        thread::spawn(move || {
            loop {
                thread::sleep(Duration::from_secs(10));
                println!(
                    "Stats: total={}, active={}, sent={} bytes, recv={} bytes",
                    stats.connections_total.load(Ordering::Relaxed),
                    stats.connections_active.load(Ordering::Relaxed),
                    stats.bytes_sent.load(Ordering::Relaxed),
                    stats.bytes_received.load(Ordering::Relaxed),
                );
            }
        });

        for stream in listener.incoming() {
            match stream {
                Ok(client) => {
                    let stats = self.stats.clone();
                    let lb = self.lb.clone();

                    thread::spawn(move || {
                        stats.connections_total.fetch_add(1, Ordering::Relaxed);
                        stats.connections_active.fetch_add(1, Ordering::Relaxed);

                        if let Err(e) = handle_connection(client, &lb, &stats) {
                            eprintln!("Connection error: {}", e);
                        }

                        stats.connections_active.fetch_sub(1, Ordering::Relaxed);
                    });
                }
                Err(e) => eprintln!("Accept error: {}", e),
            }
        }

        Ok(())
    }
}

fn handle_connection(
    mut client: TcpStream,
    lb: &LoadBalancer,
    stats: &ProxyStats,
) -> io::Result<()> {
    let backend_addr = lb.next_backend();
    let mut backend = TcpStream::connect_timeout(&backend_addr, Duration::from_secs(5))?;

    // Set timeouts
    client.set_read_timeout(Some(Duration::from_secs(30)))?;
    backend.set_read_timeout(Some(Duration::from_secs(30)))?;

    let mut client_clone = client.try_clone()?;
    let mut backend_clone = backend.try_clone()?;
    let stats2 = stats as *const ProxyStats;

    // Client → Backend (in this thread)
    let handle = thread::spawn(move || -> io::Result<()> {
        let stats = unsafe { &*stats2 };
        let mut buf = [0u8; 8192];
        loop {
            match client_clone.read(&mut buf) {
                Ok(0) => break, // Client closed
                Ok(n) => {
                    backend_clone.write_all(&buf[..n])?;
                    stats.bytes_sent.fetch_add(n as u64, Ordering::Relaxed);
                }
                Err(ref e) if e.kind() == io::ErrorKind::WouldBlock => continue,
                Err(e) => return Err(e),
            }
        }
        backend_clone.shutdown(std::net::Shutdown::Write)?;
        Ok(())
    });

    // Backend → Client (in main connection thread)
    let mut buf = [0u8; 8192];
    loop {
        match backend.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                client.write_all(&buf[..n])?;
                stats.bytes_received.fetch_add(n as u64, Ordering::Relaxed);
            }
            Err(ref e) if e.kind() == io::ErrorKind::WouldBlock => continue,
            Err(e) => {
                eprintln!("Backend read error: {}", e);
                break;
            }
        }
    }
    client.shutdown(std::net::Shutdown::Write)?;

    handle.join().unwrap_or(Ok(()))?;
    Ok(())
}
```

In production, you'd use `tokio` for async I/O instead of one thread per connection. Envoy (C++), Linkerd (Go → Rust rewrite), and Pingora (Cloudflare's Rust proxy) show the range of what production proxies handle.

## Building a Task Runtime

Language runtimes manage execution — scheduling tasks, handling async I/O, managing green threads. Here's a minimal task runtime:

```rust
use std::collections::VecDeque;
use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll, RawWaker, RawWakerVTable, Waker};

type BoxFuture = Pin<Box<dyn Future<Output = ()> + Send>>;

/// A minimal single-threaded async runtime
pub struct MiniRuntime {
    ready_queue: VecDeque<Task>,
}

struct Task {
    future: BoxFuture,
}

impl MiniRuntime {
    pub fn new() -> Self {
        Self {
            ready_queue: VecDeque::new(),
        }
    }

    /// Spawn a future onto the runtime
    pub fn spawn(&mut self, future: impl Future<Output = ()> + Send + 'static) {
        self.ready_queue.push_back(Task {
            future: Box::pin(future),
        });
    }

    /// Run all spawned futures to completion
    pub fn run(&mut self) {
        while let Some(mut task) = self.ready_queue.pop_front() {
            // Create a waker that re-enqueues the task
            let waker = noop_waker(); // Simplified — always polls
            let mut cx = Context::from_waker(&waker);

            match task.future.as_mut().poll(&mut cx) {
                Poll::Ready(()) => {
                    // Task completed
                }
                Poll::Pending => {
                    // Task not done — re-enqueue
                    self.ready_queue.push_back(task);
                }
            }
        }
    }
}

/// Create a no-op waker (real runtimes would actually wake the executor)
fn noop_waker() -> Waker {
    fn clone(_: *const ()) -> RawWaker {
        RawWaker::new(std::ptr::null(), &VTABLE)
    }
    fn wake(_: *const ()) {}
    fn wake_by_ref(_: *const ()) {}
    fn drop(_: *const ()) {}

    static VTABLE: RawWakerVTable = RawWakerVTable::new(clone, wake, wake_by_ref, drop);

    unsafe { Waker::from_raw(RawWaker::new(std::ptr::null(), &VTABLE)) }
}

// Usage:
fn runtime_demo() {
    let mut rt = MiniRuntime::new();

    rt.spawn(async {
        println!("Task 1: starting");
        // In a real runtime, this would yield to the scheduler
        println!("Task 1: done");
    });

    rt.spawn(async {
        println!("Task 2: starting");
        println!("Task 2: done");
    });

    rt.run();
}
```

This is a toy, but it shows the fundamental pattern that Tokio, async-std, and smol are built on: a queue of futures, a polling loop, and a waker mechanism to re-schedule futures when their I/O is ready.

## Patterns That Production Systems Share

After building a few of these systems, you start seeing the same patterns everywhere:

### Buffer Pool Management

Databases, proxies, and runtimes all manage pools of reusable buffers:

```rust
use std::sync::Mutex;

struct BufferPool {
    pool: Mutex<Vec<Vec<u8>>>,
    buffer_size: usize,
    max_buffers: usize,
}

impl BufferPool {
    fn new(buffer_size: usize, max_buffers: usize) -> Self {
        Self {
            pool: Mutex::new(Vec::with_capacity(max_buffers)),
            buffer_size,
            max_buffers,
        }
    }

    fn acquire(&self) -> PooledBuffer {
        let buffer = self.pool.lock().unwrap().pop()
            .unwrap_or_else(|| vec![0u8; self.buffer_size]);
        PooledBuffer { buffer, pool: self }
    }
}

struct PooledBuffer<'a> {
    buffer: Vec<u8>,
    pool: &'a BufferPool,
}

impl<'a> Drop for PooledBuffer<'a> {
    fn drop(&mut self) {
        let mut buffer = std::mem::take(&mut self.buffer);
        buffer.clear();
        let mut pool = self.pool.pool.lock().unwrap();
        if pool.len() < self.pool.max_buffers {
            pool.push(buffer);
        }
        // Otherwise, buffer is dropped (deallocated)
    }
}

impl<'a> std::ops::Deref for PooledBuffer<'a> {
    type Target = Vec<u8>;
    fn deref(&self) -> &Vec<u8> { &self.buffer }
}

impl<'a> std::ops::DerefMut for PooledBuffer<'a> {
    fn deref_mut(&mut self) -> &mut Vec<u8> { &mut self.buffer }
}
```

### Graceful Shutdown

Every long-running system needs clean shutdown:

```rust
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

struct ShutdownSignal {
    shutdown: Arc<AtomicBool>,
}

impl ShutdownSignal {
    fn new() -> Self {
        let shutdown = Arc::new(AtomicBool::new(false));
        let s = shutdown.clone();

        ctrlc::set_handler(move || {
            eprintln!("\nShutdown signal received");
            s.store(true, Ordering::SeqCst);
        }).ok();

        Self { shutdown }
    }

    fn is_shutdown(&self) -> bool {
        self.shutdown.load(Ordering::SeqCst)
    }

    fn token(&self) -> Arc<AtomicBool> {
        self.shutdown.clone()
    }
}

// Usage in a server:
fn run_server() {
    let signal = ShutdownSignal::new();

    // Pass shutdown token to worker threads
    let token = signal.token();
    let worker = std::thread::spawn(move || {
        while !token.load(Ordering::SeqCst) {
            // Do work...
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
        eprintln!("Worker: draining remaining work...");
        // Finish in-flight requests
        eprintln!("Worker: shutdown complete");
    });

    while !signal.is_shutdown() {
        // Accept connections, etc.
        std::thread::sleep(std::time::Duration::from_millis(100));
    }

    eprintln!("Main: waiting for workers...");
    worker.join().unwrap();
    eprintln!("Main: clean shutdown");
}
```

### Metrics and Observability

You can't operate what you can't observe:

```rust
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;

struct Metrics {
    request_count: AtomicU64,
    error_count: AtomicU64,
    latency_sum_us: AtomicU64,
    latency_max_us: AtomicU64,
}

impl Metrics {
    fn new() -> Self {
        Self {
            request_count: AtomicU64::new(0),
            error_count: AtomicU64::new(0),
            latency_sum_us: AtomicU64::new(0),
            latency_max_us: AtomicU64::new(0),
        }
    }

    fn record_request(&self, start: Instant, success: bool) {
        let latency = start.elapsed().as_micros() as u64;
        self.request_count.fetch_add(1, Ordering::Relaxed);
        self.latency_sum_us.fetch_add(latency, Ordering::Relaxed);
        self.latency_max_us.fetch_max(latency, Ordering::Relaxed);

        if !success {
            self.error_count.fetch_add(1, Ordering::Relaxed);
        }
    }

    fn snapshot(&self) -> MetricsSnapshot {
        let count = self.request_count.load(Ordering::Relaxed);
        let sum = self.latency_sum_us.load(Ordering::Relaxed);
        MetricsSnapshot {
            request_count: count,
            error_count: self.error_count.load(Ordering::Relaxed),
            avg_latency_us: if count > 0 { sum / count } else { 0 },
            max_latency_us: self.latency_max_us.load(Ordering::Relaxed),
        }
    }
}

struct MetricsSnapshot {
    request_count: u64,
    error_count: u64,
    avg_latency_us: u64,
    max_latency_us: u64,
}
```

## The Rust Systems Software Landscape

To close this course, here's what's actually running in production, written in Rust:

**Databases:** TiKV (distributed KV store), sled (embedded DB), SurrealDB, Qdrant (vector DB), Meilisearch (search engine)

**Runtimes:** Tokio (async runtime), Wasmer/Wasmtime (WebAssembly runtimes), Deno (JavaScript runtime)

**Proxies/Networking:** Linkerd2-proxy, Pingora (Cloudflare), Quilkin (game server proxy)

**Infrastructure:** Firecracker (microVM), Bottlerocket (container OS), Hubris (embedded RTOS by Oxide Computer)

**Compilers/Tooling:** rustc itself, rust-analyzer, swc (JavaScript compiler), Ruff (Python linter)

These aren't experiments. They're handling billions of requests, petabytes of data, and millions of users. Rust's value proposition for systems software — performance of C/C++ with memory safety guarantees — has proven out in practice.

## Course Wrap-Up

We've covered a lot of ground in twelve lessons:

1. `no_std` — stripping away the standard library
2. Embedded Rust — microcontrollers and bare metal
3. Memory-mapped I/O — talking to hardware
4. Linux kernel modules — Rust in the kernel
5. OS concepts — processes, threads, signals
6. File systems — from blocks to files
7. Network protocols — TCP from scratch
8. Custom allocators — beyond the global allocator
9. Interrupt handlers — when timing matters
10. Bootloaders — the first code that runs
11. Hypervisors — virtualization in Rust
12. Production systems — databases, runtimes, proxies

The thread connecting all of these is *control*. Systems programming is about controlling the machine at every level — from the register values in a CPU to the bytes on a wire to the blocks on a disk. Rust gives you that control while preventing the memory safety bugs that have plagued systems code for decades.

The field is wide open. We need better embedded tooling, more kernel drivers, faster databases, more efficient runtimes. If you've made it through this course, you have the foundation to build any of them.

Go build something.
