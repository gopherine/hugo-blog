---
title: "Lesson 4: Reducing Allocations — Stack, arena, SmallVec"
author: Atharva Pandey
series: "Rust Performance Engineering"
lesson: 4
keywords: [Rust, rust, performance, allocations, arena, SmallVec, bumpalo, stack allocation, heap]
tags: [Rust tutorial, rust, performance]
date: '2025-03-21T16:30:00.000Z'
---

I profiled a Rust web service once and found it was allocating 47,000 times per request. Forty-seven thousand. Most were tiny — 16-byte strings, 3-element vectors, temporary buffers. Each individual allocation was fast (jemalloc is good), but 47,000 of them at ~30ns each is 1.4ms of pure allocator overhead. Per request. At 10K RPS that's 14 seconds of CPU time per second, just asking the allocator for memory.

The fix took half a day and cut allocations to about 200 per request. Here's everything I know about reducing allocations in Rust.

## Why Allocations Are Expensive

A heap allocation (`Box::new`, `Vec::new()`, `String::from()`) involves:

1. **Calling the allocator.** Even the fastest allocators (jemalloc, mimalloc) take 25-100ns per allocation. That's ~50-200 CPU cycles.

2. **Potential system calls.** If the allocator's free list is empty, it needs to ask the OS for more memory via `mmap` or `brk`. That's microseconds, not nanoseconds.

3. **Cache pollution.** Newly allocated memory is cold — it's not in any cache level. Accessing it triggers a cache miss (~100ns for an L3 miss, potentially 200ns+ if it goes to main memory).

4. **Fragmentation.** Thousands of small allocations scatter your data across the heap. This kills cache locality and increases TLB misses.

Stack allocation, by contrast, is free. It's literally a pointer bump — adjust the stack pointer by the size you need. Zero overhead.

## Strategy 1: Keep Data on the Stack

The simplest optimization. If you know the size at compile time, don't heap-allocate.

```rust
// HEAP: allocates on every call
fn make_buffer() -> Vec<u8> {
    vec![0u8; 256]
}

// STACK: zero allocation overhead
fn make_buffer_stack() -> [u8; 256] {
    [0u8; 256]
}
```

Benchmark difference:

```rust
use divan::black_box;

#[divan::bench]
fn heap_buffer() -> Vec<u8> {
    black_box(vec![0u8; 256])
}

#[divan::bench]
fn stack_buffer() -> [u8; 256] {
    black_box([0u8; 256])
}

// Typical results:
// heap_buffer:  ~25 ns
// stack_buffer: ~2 ns
```

That's a 12x difference for a 256-byte buffer. For smaller sizes it's even more dramatic because the allocation overhead dominates.

### ArrayVec and ArrayString

When you need Vec-like behavior but know the maximum size, `arrayvec` gives you a stack-allocated growable container:

```rust
use arrayvec::ArrayVec;

// Stack-allocated, can hold up to 16 items
fn collect_small_results() -> ArrayVec<u32, 16> {
    let mut results = ArrayVec::new();
    for i in 0..10 {
        results.push(i * i);
    }
    results
}

// Same thing for strings
use arrayvec::ArrayString;

fn format_status_code(code: u16) -> ArrayString<3> {
    let mut s = ArrayString::new();
    // write! into an ArrayString — no allocation
    use std::fmt::Write;
    write!(&mut s, "{}", code).unwrap();
    s
}
```

The trade-off: if you exceed the capacity, `ArrayVec::push` panics (or you can use `try_push` which returns a `Result`). You need to know your upper bound.

## Strategy 2: SmallVec — Best of Both Worlds

`SmallVec` stores elements inline (on the stack) up to a threshold, then spills to the heap. It's perfect when most instances are small but you can't rule out large ones.

```rust
use smallvec::SmallVec;

// Stores up to 8 elements inline, spills to heap after that
type Tags = SmallVec<[String; 8]>;

fn parse_tags(input: &str) -> Tags {
    input.split(',').map(|s| s.trim().to_string()).collect()
}
```

The performance profile:

```rust
#[divan::bench(args = [1, 4, 8, 16, 64])]
fn with_vec(n: usize) -> Vec<u32> {
    (0..n as u32).collect()
}

#[divan::bench(args = [1, 4, 8, 16, 64])]
fn with_smallvec(n: usize) -> SmallVec<[u32; 8]> {
    (0..n as u32).collect()
}

// Typical results:
//                   1 elem    4 elem    8 elem    16 elem   64 elem
// with_vec:         28 ns     35 ns     42 ns     55 ns     180 ns
// with_smallvec:     5 ns      8 ns     12 ns     62 ns     195 ns
```

For 1-8 elements: SmallVec is 3-5x faster (no allocation). At 16+ elements: roughly equal (SmallVec had to spill to heap). The heap spill includes the cost of moving inline data to the heap, so there's a small penalty right at the transition point.

### Choosing the Inline Size

Pick the inline size based on your actual data distribution. If 90% of your vectors have 4 or fewer elements, `SmallVec<[T; 4]>` captures most of the benefit. Don't go crazy with `SmallVec<[T; 64]>` — that's 64 * sizeof(T) bytes on the stack for *every* instance, even empty ones.

```rust
// Good: covers 95% of cases for HTTP headers
type HeaderValues = SmallVec<[String; 4]>;

// Bad: 512 bytes on the stack per instance even if empty
type Overkill = SmallVec<[u64; 64]>;
```

## Strategy 3: Arena Allocation with bumpalo

An arena allocator is beautifully simple: you allocate a big chunk of memory upfront, then hand out pieces of it. When you're done, you free the entire arena at once. No individual deallocations, no fragmentation, and allocation is just a pointer bump (~2ns).

This is perfect for request-scoped work — parse a request, do some processing, send a response, then throw away all the memory at once.

```rust
use bumpalo::Bump;

fn process_request(input: &str) {
    // Create an arena for this request
    let arena = Bump::new();

    // Allocate strings in the arena — nearly free
    let parts: Vec<&str> = input.split(',').collect();
    let mut processed = bumpalo::collections::Vec::new_in(&arena);

    for part in &parts {
        // This String lives in the arena, not the global heap
        let upper = bumpalo::collections::String::from_str_in(
            &part.to_uppercase(),
            &arena,
        );
        processed.push(upper);
    }

    // Do something with processed...

    // When `arena` drops, ALL memory is freed at once.
    // No individual destructors, no free-list management.
}
```

Benchmark:

```rust
use bumpalo::Bump;

#[divan::bench]
fn standard_alloc() {
    let mut results: Vec<String> = Vec::new();
    for i in 0..1000 {
        results.push(format!("item_{}", i));
    }
    divan::black_box(&results);
}

#[divan::bench]
fn arena_alloc() {
    let arena = Bump::with_capacity(64 * 1024); // 64KB upfront
    let mut results = bumpalo::collections::Vec::new_in(&arena);
    for i in 0..1000 {
        results.push(bumpalo::format!(in &arena, "item_{}", i));
    }
    divan::black_box(&results);
}

// Typical results:
// standard_alloc: ~45 µs
// arena_alloc:    ~18 µs
```

2.5x faster for 1000 allocations. The savings compound as you allocate more — arena allocation is O(1) regardless of heap state, while general-purpose allocators can slow down under fragmentation.

### Arena Gotchas

**Drop isn't called.** When you put types in a bumpalo arena, their `Drop` implementations are *not* called when the arena is freed. For `String` and `Vec` this is fine (the arena owns the backing memory). For types that hold external resources (file handles, network connections), this is a problem.

```rust
// BAD: the file handle will leak
let file = bumpalo::boxed::Box::new_in(File::open("data.txt")?, &arena);
// When arena drops, File::drop() is NOT called

// bumpalo does offer drop support, but you opt in:
// Use bumpalo's `collections` types which handle this correctly
```

**The arena can't free individual items.** That's the trade-off. If you allocate a 10MB structure in an arena, that memory stays allocated until the entire arena is dropped. Arenas work best for request-scoped or phase-scoped lifetimes where everything gets freed together.

## Strategy 4: Reuse Allocations

Sometimes the best allocation is the one you already have.

```rust
// BAD: allocates a new Vec every iteration
fn process_batches(data: &[Vec<u32>]) -> Vec<u64> {
    let mut results = Vec::new();
    for batch in data {
        let processed: Vec<u64> = batch.iter().map(|&x| x as u64 * 2).collect();
        results.extend(processed.iter());
    }
    results
}

// GOOD: reuse the buffer
fn process_batches_reuse(data: &[Vec<u32>]) -> Vec<u64> {
    let mut results = Vec::new();
    let mut buffer: Vec<u64> = Vec::new();

    for batch in data {
        buffer.clear(); // resets length to 0, keeps capacity
        buffer.extend(batch.iter().map(|&x| x as u64 * 2));
        results.extend(buffer.iter());
    }
    results
}
```

`Vec::clear()` resets the length to zero without freeing memory. The next `push` or `extend` reuses the existing capacity. This is a massive win when you're processing many batches of similar size.

The same pattern works for `String`:

```rust
let mut buf = String::new();
for item in items {
    buf.clear();
    write!(&mut buf, "processed: {}", item).unwrap();
    output.push_str(&buf);
}
```

## Strategy 5: Pre-allocate with Capacity

If you know (or can estimate) how many elements you'll have, tell the allocator upfront:

```rust
// BAD: starts at capacity 0, reallocates multiple times as it grows
// Vec doubles capacity each time: 0 → 4 → 8 → 16 → 32 → ...
let mut v = Vec::new();
for i in 0..1000 {
    v.push(i); // triggers ~10 reallocations
}

// GOOD: single allocation, no reallocations
let mut v = Vec::with_capacity(1000);
for i in 0..1000 {
    v.push(i); // never reallocates
}
```

Each reallocation means: allocate new buffer (2x size), copy old data to new buffer, free old buffer. For a Vec that grows to 1000 elements, that's roughly 10 reallocations and 10 `memcpy` calls. `with_capacity` eliminates all of them.

```rust
// Same for HashMap — preallocate if you know the size
let mut map = HashMap::with_capacity(expected_entries);

// And String
let mut s = String::with_capacity(estimated_length);
```

## Strategy 6: Cow — Clone on Write

`Cow` (Clone on Write) lets you avoid cloning when you might not need to modify the data:

```rust
use std::borrow::Cow;

fn normalize_path(path: &str) -> Cow<'_, str> {
    if path.contains("//") {
        // Only allocate when we actually need to modify
        Cow::Owned(path.replace("//", "/"))
    } else {
        // No allocation — just borrow the input
        Cow::Borrowed(path)
    }
}
```

If 90% of paths are already normalized, you save 90% of the allocations. The callee returns either a borrowed reference (zero cost) or an owned String (allocated only when necessary).

## Measuring Allocation Impact

Use the global allocator trick to count allocations:

```rust
use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicUsize, Ordering};

static ALLOC_COUNT: AtomicUsize = AtomicUsize::new(0);
static ALLOC_BYTES: AtomicUsize = AtomicUsize::new(0);

struct CountingAllocator;

unsafe impl GlobalAlloc for CountingAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        ALLOC_COUNT.fetch_add(1, Ordering::Relaxed);
        ALLOC_BYTES.fetch_add(layout.size(), Ordering::Relaxed);
        unsafe { System.alloc(layout) }
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        unsafe { System.dealloc(ptr, layout) }
    }
}

#[global_allocator]
static GLOBAL: CountingAllocator = CountingAllocator;

fn main() {
    ALLOC_COUNT.store(0, Ordering::SeqCst);
    ALLOC_BYTES.store(0, Ordering::SeqCst);

    // Your code here
    do_work();

    println!(
        "Allocations: {}, Bytes: {}",
        ALLOC_COUNT.load(Ordering::SeqCst),
        ALLOC_BYTES.load(Ordering::SeqCst),
    );
}
```

I use this regularly during development. It's a simple way to track allocation counts without external tools.

## The Takeaway

Reducing allocations isn't about eliminating every `Box` or `Vec`. It's about eliminating *unnecessary* allocations in hot paths. Profile first (Lesson 3), identify allocation-heavy code, then apply the right strategy:

- **Known size, small?** Use arrays or `ArrayVec`.
- **Usually small, sometimes big?** Use `SmallVec`.
- **Many allocations, freed together?** Use `bumpalo` arenas.
- **Same buffer used repeatedly?** Reuse with `clear()`.
- **Known final size?** Pre-allocate with `with_capacity`.
- **Might not need to modify?** Use `Cow`.

In the next lesson, we'll look at a question I get constantly: are iterators actually as fast as hand-written loops? The answer will surprise you — or maybe it won't.
