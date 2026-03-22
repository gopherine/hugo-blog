---
title: "Lesson 8: Custom Allocators — GlobalAlloc, arena allocation, and beyond"
author: Atharva Pandey
series: "Rust Memory Model & Internals"
lesson: 8
keywords: [Rust, rust, memory, internals, allocator, GlobalAlloc, arena, memory pool, jemalloc, bump allocator]
tags: [Rust tutorial, rust, internals]
date: '2025-03-16T13:10:00.000Z'
---

I was building a JSON parser that processed millions of small documents per second. Profiling showed that 40% of the time was spent in `malloc` and `free` — not parsing, not I/O, just allocating and deallocating tiny strings and vectors. Switched to an arena allocator that bulk-freed everything after each document, and throughput doubled. That experience taught me that the allocator isn't just plumbing — it's a performance lever.

## How Rust Allocates Memory

When you write `Box::new(42)` or `Vec::with_capacity(100)`, Rust asks the **global allocator** for memory. By default, this is the system allocator — `malloc`/`free` on Unix, `HeapAlloc`/`HeapFree` on Windows. It's a general-purpose allocator designed to handle any allocation pattern reasonably well, but it's not optimized for any specific pattern.

The allocation pipeline looks like this:

```
Your code: Box::new(42)
    ↓
std::alloc::Global (the global allocator)
    ↓
System allocator (malloc/free)
    ↓
OS kernel (mmap/brk on Linux, VirtualAlloc on Windows)
```

Every heap allocation goes through this chain. For most programs, it's fine. But if allocation is in your hot path, you can replace any layer.

## The GlobalAlloc Trait

Rust's allocator interface is the `GlobalAlloc` trait:

```rust
use std::alloc::{GlobalAlloc, Layout};

unsafe impl GlobalAlloc for MyAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8;
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout);

    // Optional — default implementations exist
    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8;
    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8;
}
```

`Layout` carries the size and alignment requirements. The allocator returns a raw pointer to the allocated block, or null on failure.

To replace the global allocator, you define a static and annotate it with `#[global_allocator]`:

```rust
use std::alloc::{GlobalAlloc, Layout, System};

struct CountingAllocator;

static ALLOC_COUNT: std::sync::atomic::AtomicU64 =
    std::sync::atomic::AtomicU64::new(0);
static DEALLOC_COUNT: std::sync::atomic::AtomicU64 =
    std::sync::atomic::AtomicU64::new(0);

unsafe impl GlobalAlloc for CountingAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        ALLOC_COUNT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        unsafe { System.alloc(layout) }
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        DEALLOC_COUNT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        unsafe { System.dealloc(ptr, layout) }
    }
}

#[global_allocator]
static GLOBAL: CountingAllocator = CountingAllocator;

fn main() {
    // Reset counters
    let start_alloc = ALLOC_COUNT.load(std::sync::atomic::Ordering::Relaxed);

    let mut v: Vec<u32> = Vec::new();
    for i in 0..100 {
        v.push(i);
    }
    drop(v);

    let total_allocs = ALLOC_COUNT.load(std::sync::atomic::Ordering::Relaxed) - start_alloc;
    let total_deallocs = DEALLOC_COUNT.load(std::sync::atomic::Ordering::Relaxed);

    println!("Allocations: {}", total_allocs);
    println!("Deallocations: {}", total_deallocs);
    // Vec grows: 0->1->2->4->8->16->32->64->128 = ~8 allocations
}
```

This counting allocator wraps the system allocator and tracks every allocation. Dead simple, but incredibly useful for finding allocation-heavy code. I've used variants of this in production to set allocation budgets — "this request handler should not allocate more than 50 times."

## Using jemalloc

The most common reason to swap allocators is performance. `jemalloc` is a battle-tested allocator originally built for FreeBSD that excels at multi-threaded workloads. It reduces fragmentation and lock contention compared to the system allocator on Linux.

```toml
# Cargo.toml
[dependencies]
tikv-jemallocator = "0.6"
```

```rust
use tikv_jemallocator::Jemalloc;

#[global_allocator]
static GLOBAL: Jemalloc = Jemalloc;

fn main() {
    // Everything now uses jemalloc
    let v: Vec<u64> = (0..1_000_000).collect();
    println!("Allocated {} elements", v.len());
}
```

That's it. Two lines and your entire program uses jemalloc. In my experience, jemalloc gives a 5-15% throughput improvement for allocation-heavy, multi-threaded Rust programs. The gains come from better handling of thread-local caches and reduced lock contention.

Other alternative allocators worth knowing:
- **mimalloc** (Microsoft) — great general performance, especially on Windows
- **snmalloc** — optimized for message-passing concurrency patterns
- **dlmalloc** — Doug Lea's classic allocator, available as a Rust crate for embedded/no_std

## Building a Bump Allocator

A bump allocator (also called a linear or arena allocator) is the simplest possible allocator. It maintains a pointer into a pre-allocated buffer and "bumps" it forward on each allocation. Deallocation is a no-op — you free everything at once by resetting the pointer.

```rust
use std::alloc::{GlobalAlloc, Layout};
use std::cell::UnsafeCell;
use std::sync::atomic::{AtomicUsize, Ordering};

const ARENA_SIZE: usize = 1024 * 1024; // 1 MB

struct BumpAllocator {
    arena: UnsafeCell<[u8; ARENA_SIZE]>,
    next: AtomicUsize,
}

unsafe impl Sync for BumpAllocator {}

impl BumpAllocator {
    const fn new() -> Self {
        BumpAllocator {
            arena: UnsafeCell::new([0; ARENA_SIZE]),
            next: AtomicUsize::new(0),
        }
    }

    fn reset(&self) {
        self.next.store(0, Ordering::SeqCst);
    }
}

unsafe impl GlobalAlloc for BumpAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        loop {
            let current = self.next.load(Ordering::Relaxed);

            // Align up
            let aligned = (current + layout.align() - 1) & !(layout.align() - 1);
            let new_next = aligned + layout.size();

            if new_next > ARENA_SIZE {
                return std::ptr::null_mut(); // out of memory
            }

            // CAS loop for thread safety
            if self.next
                .compare_exchange_weak(
                    current,
                    new_next,
                    Ordering::SeqCst,
                    Ordering::Relaxed,
                )
                .is_ok()
            {
                let arena_ptr = unsafe { self.arena.get() as *mut u8 };
                return unsafe { arena_ptr.add(aligned) };
            }
        }
    }

    unsafe fn dealloc(&self, _ptr: *mut u8, _layout: Layout) {
        // No-op — memory is freed when the arena is reset
    }
}
```

This allocator is absurdly fast — allocation is just an atomic add. But it never frees individual allocations. It's perfect for:

- Request-scoped allocation (allocate during request processing, reset at the end)
- Compiler/parser passes (allocate AST nodes, free everything after the pass)
- Game frames (allocate per-frame data, reset at frame end)

## Arena Allocation With the bumpalo Crate

For practical arena allocation, use the `bumpalo` crate instead of rolling your own:

```toml
# Cargo.toml
[dependencies]
bumpalo = "3"
```

```rust
use bumpalo::Bump;

fn main() {
    let arena = Bump::new();

    // Allocate values in the arena
    let x = arena.alloc(42u64);
    let y = arena.alloc(String::from("hello"));
    let z = arena.alloc_slice_copy(&[1, 2, 3, 4, 5]);

    println!("x = {}", x);
    println!("y = {}", y);
    println!("z = {:?}", z);

    // Create a Vec that allocates in the arena
    let mut names = bumpalo::vec![in &arena; "Alice", "Bob"];
    names.push("Charlie");
    println!("names = {:?}", names);

    println!("Arena used: {} bytes", arena.allocated_bytes());
}
// All arena memory freed at once when `arena` is dropped
```

The beauty of `bumpalo` is that individual allocations within the arena are nearly free — just a pointer bump. And deallocation is O(1) regardless of how many allocations were made, because the entire arena is freed at once.

### Arena Allocation for a Parser

Here's a realistic example — using an arena for AST nodes:

```rust
use bumpalo::Bump;

#[derive(Debug)]
enum Expr<'arena> {
    Num(f64),
    Add(&'arena Expr<'arena>, &'arena Expr<'arena>),
    Mul(&'arena Expr<'arena>, &'arena Expr<'arena>),
}

fn parse_expression<'a>(arena: &'a Bump) -> &'a Expr<'a> {
    // Build: (1 + 2) * 3
    let one = arena.alloc(Expr::Num(1.0));
    let two = arena.alloc(Expr::Num(2.0));
    let sum = arena.alloc(Expr::Add(one, two));
    let three = arena.alloc(Expr::Num(3.0));
    let product = arena.alloc(Expr::Mul(sum, three));
    product
}

fn compute(expr: &Expr) -> f64 {
    match expr {
        Expr::Num(n) => *n,
        Expr::Add(a, b) => compute(a) + compute(b),
        Expr::Mul(a, b) => compute(a) * compute(b),
    }
}

fn main() {
    let arena = Bump::new();
    let expr = parse_expression(&arena);
    println!("Result: {}", compute(expr));
    // All AST nodes freed at once when arena drops
}
```

Each AST node is a tiny allocation. With the system allocator, you'd pay the `malloc` overhead for every single node. With the arena, each allocation is a pointer bump — maybe 2-3 nanoseconds. For a parser processing thousands of expressions per second, this makes a huge difference.

## Pool Allocators

A pool allocator pre-allocates a block of memory and divides it into fixed-size chunks. Allocating returns a free chunk; deallocating returns it to the pool. Unlike bump allocators, pools support individual deallocation:

```rust
use std::cell::RefCell;

struct Pool<T> {
    storage: Vec<Option<T>>,
    free_list: RefCell<Vec<usize>>,
}

impl<T> Pool<T> {
    fn new(capacity: usize) -> Self {
        let storage = (0..capacity).map(|_| None).collect();
        let free_list = RefCell::new((0..capacity).rev().collect());
        Pool { storage, free_list }
    }

    fn alloc(&mut self, value: T) -> Option<usize> {
        let index = self.free_list.borrow_mut().pop()?;
        self.storage[index] = Some(value);
        Some(index)
    }

    fn dealloc(&mut self, index: usize) {
        self.storage[index] = None;
        self.free_list.borrow_mut().push(index);
    }

    fn get(&self, index: usize) -> Option<&T> {
        self.storage[index].as_ref()
    }
}

fn main() {
    let mut pool = Pool::new(1024);

    let id1 = pool.alloc(String::from("hello")).unwrap();
    let id2 = pool.alloc(String::from("world")).unwrap();

    println!("{}", pool.get(id1).unwrap());
    println!("{}", pool.get(id2).unwrap());

    pool.dealloc(id1);
    // id1's slot is now available for reuse

    let id3 = pool.alloc(String::from("recycled")).unwrap();
    println!("{}", pool.get(id3).unwrap());
    // id3 reuses id1's slot
}
```

Pool allocators shine when you have many objects of the same type with unpredictable lifetimes — game entities, network connections, worker tasks.

## Tracking Allocations for Debugging

Here's a more practical debugging allocator that tracks allocation sizes and detects leaks:

```rust
use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicUsize, AtomicI64, Ordering};

struct TrackingAllocator;

static CURRENT_BYTES: AtomicI64 = AtomicI64::new(0);
static PEAK_BYTES: AtomicI64 = AtomicI64::new(0);
static TOTAL_ALLOCS: AtomicUsize = AtomicUsize::new(0);

unsafe impl GlobalAlloc for TrackingAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let ptr = unsafe { System.alloc(layout) };
        if !ptr.is_null() {
            let size = layout.size() as i64;
            let current = CURRENT_BYTES.fetch_add(size, Ordering::Relaxed) + size;
            TOTAL_ALLOCS.fetch_add(1, Ordering::Relaxed);

            // Update peak
            let mut peak = PEAK_BYTES.load(Ordering::Relaxed);
            while current > peak {
                match PEAK_BYTES.compare_exchange_weak(
                    peak, current, Ordering::Relaxed, Ordering::Relaxed,
                ) {
                    Ok(_) => break,
                    Err(actual) => peak = actual,
                }
            }
        }
        ptr
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        unsafe { System.dealloc(ptr, layout) };
        CURRENT_BYTES.fetch_sub(layout.size() as i64, Ordering::Relaxed);
    }
}

#[global_allocator]
static GLOBAL: TrackingAllocator = TrackingAllocator;

fn print_alloc_stats() {
    println!("Current: {} bytes", CURRENT_BYTES.load(Ordering::Relaxed));
    println!("Peak:    {} bytes", PEAK_BYTES.load(Ordering::Relaxed));
    println!("Total allocations: {}", TOTAL_ALLOCS.load(Ordering::Relaxed));
}

fn main() {
    {
        let mut data: Vec<String> = Vec::new();
        for i in 0..1000 {
            data.push(format!("item-{}", i));
        }
        println!("After filling vec:");
        print_alloc_stats();
    }

    println!("\nAfter dropping vec:");
    print_alloc_stats();
}
```

This is invaluable for understanding allocation behavior in your application. Want to know which handler allocates the most? Wrap the tracking in a scope, measure before and after. Want to find memory leaks? Check if `CURRENT_BYTES` grows unboundedly over time.

## The Allocator API (Unstable)

There's a more powerful allocator interface on nightly Rust — the `Allocator` trait (distinct from `GlobalAlloc`). It allows collections to be parameterized by their allocator:

```rust
// Nightly only — but this is the future direction
// #![feature(allocator_api)]
//
// use std::alloc::Global;
// use bumpalo::Bump;
//
// let arena = Bump::new();
//
// // Vec that allocates in a specific arena
// let mut v = Vec::new_in(&arena);
// v.push(1);
// v.push(2);
//
// // HashMap with a custom allocator
// let mut map = HashMap::new_in(&arena);
// map.insert("key", "value");
```

When this stabilizes, you'll be able to use any allocator with standard collections without changing the global allocator. This is a much cleaner model — instead of replacing allocation globally, you choose per-collection.

## Choosing an Allocator Strategy

| Situation | Allocator | Why |
|---|---|---|
| General purpose, multi-threaded | jemalloc or mimalloc | Less fragmentation, better thread scaling |
| Short-lived batch processing | Bump/arena (bumpalo) | Bulk free, near-zero alloc cost |
| Many objects of same type | Pool allocator | O(1) alloc/dealloc, no fragmentation |
| Debugging, profiling | Tracking wrapper | Measure allocation patterns |
| Embedded / no_std | Static bump or pool | No OS allocator available |
| Real-time / low-latency | Pre-allocated pools | No allocation in hot path |

The best approach is often hybrid: use jemalloc as the global allocator for general work, and arena/pool allocators for specific hot paths.

## What's Next

We've been talking about what Rust guarantees about memory. But how do you verify those guarantees, especially in unsafe code? In Lesson 9, we'll look at Miri — Rust's interpreter that can detect undefined behavior, memory leaks, data races, and other problems that the compiler can't catch statically. If you write any unsafe Rust, Miri is the tool you reach for first.
