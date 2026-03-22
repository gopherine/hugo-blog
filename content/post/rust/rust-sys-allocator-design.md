---
title: "Lesson 8: Designing Custom Allocators — Beyond the global allocator"
author: Atharva Pandey
series: "Rust for Systems Programming"
lesson: 8
keywords: [Rust, rust, systems programming, allocator, memory management, bump allocator, slab allocator, arena]
tags: [Rust tutorial, rust, systems, low-level]
date: '2025-07-27T13:19:56.000Z'
---

Here's a dirty secret of systems programming: `malloc` is not magic. It's just code. Code that somebody wrote, code that makes tradeoffs, and code you can replace when those tradeoffs don't match your workload.

I spent two years writing performance-sensitive Rust before I realized that the global allocator was my bottleneck. Not CPU. Not I/O. Memory allocation — thousands of tiny allocations per request, each one hitting a lock, each one fragmenting the heap a little more. Switching to a bump allocator for request-scoped data cut latency by 40%.

Custom allocators aren't exotic. They're a practical tool that every systems programmer should understand.

## How the Default Allocator Works (Roughly)

The default Rust allocator (which wraps `malloc` from your system's libc) typically uses some variant of this approach:

```
┌──────────────────────────────────────────┐
│  Free list organized by size classes     │
│                                          │
│  8 bytes:   [free] → [free] → [free]    │
│  16 bytes:  [free] → [free]              │
│  32 bytes:  [free] → [free] → [free]    │
│  64 bytes:  [free]                       │
│  128 bytes: [free] → [free]              │
│  ...                                     │
│  Large: mmap individual allocations      │
└──────────────────────────────────────────┘
```

When you call `alloc(size)`, it finds the smallest size class that fits, pops a block from that free list, and returns it. When you call `dealloc()`, it pushes the block back onto the list. Large allocations go directly to the OS via `mmap`.

This is general-purpose and reasonably fast. But "general-purpose" means it can't optimize for your specific access patterns. That's where custom allocators come in.

## The GlobalAlloc Trait

Rust's allocator interface is simple:

```rust
use core::alloc::{GlobalAlloc, Layout};

unsafe impl GlobalAlloc for MyAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8;
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout);

    // Optional overrides:
    // unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8;
    // unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8;
}
```

`Layout` tells you the size and alignment requirements. That's it — two functions, and you have a complete allocator.

## Allocator 1: Bump Allocator

The simplest useful allocator. Allocations just bump a pointer forward. Deallocation is a no-op — you free everything at once by resetting the pointer.

```rust
use core::alloc::{GlobalAlloc, Layout};
use core::cell::UnsafeCell;
use core::sync::atomic::{AtomicUsize, Ordering};

pub struct BumpAllocator {
    heap_start: usize,
    heap_end: usize,
    next: AtomicUsize,
    allocations: AtomicUsize, // Track count for debugging
}

impl BumpAllocator {
    pub const fn new(heap_start: usize, heap_size: usize) -> Self {
        Self {
            heap_start,
            heap_end: heap_start + heap_size,
            next: AtomicUsize::new(heap_start),
            allocations: AtomicUsize::new(0),
        }
    }

    pub fn reset(&self) {
        self.next.store(self.heap_start, Ordering::SeqCst);
        self.allocations.store(0, Ordering::SeqCst);
    }

    pub fn used(&self) -> usize {
        self.next.load(Ordering::Relaxed) - self.heap_start
    }
}

unsafe impl GlobalAlloc for BumpAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        loop {
            let current = self.next.load(Ordering::Relaxed);

            // Align up
            let aligned = (current + layout.align() - 1) & !(layout.align() - 1);
            let new_next = aligned + layout.size();

            if new_next > self.heap_end {
                return core::ptr::null_mut(); // OOM
            }

            // CAS loop for thread safety
            match self.next.compare_exchange_weak(
                current,
                new_next,
                Ordering::SeqCst,
                Ordering::Relaxed,
            ) {
                Ok(_) => {
                    self.allocations.fetch_add(1, Ordering::Relaxed);
                    return aligned as *mut u8;
                }
                Err(_) => continue, // Another thread beat us, retry
            }
        }
    }

    unsafe fn dealloc(&self, _ptr: *mut u8, _layout: Layout) {
        // Bump allocators don't deallocate individual allocations.
        // Memory is reclaimed when the entire allocator is reset.
        self.allocations.fetch_sub(1, Ordering::Relaxed);
    }
}
```

When to use a bump allocator:
- Request/frame-scoped allocations (web servers, game loops)
- Phase-based computation (compile one file, reset, compile the next)
- Anywhere you know the lifetime of all allocations up front

The Rust compiler itself uses a bump allocator (arena) for AST nodes. Each compilation creates thousands of AST nodes that all die at the same time — perfect for bump allocation.

## Allocator 2: Pool / Slab Allocator

A pool allocator pre-divides memory into equal-sized slots. Allocation is O(1) — pop from a free list. Deallocation is O(1) — push back onto the free list.

```rust
use core::alloc::{GlobalAlloc, Layout};
use core::ptr;

/// A free-list node (stored inside free slots)
struct FreeNode {
    next: *mut FreeNode,
}

pub struct PoolAllocator {
    slot_size: usize,
    slots: usize,
    memory: *mut u8,
    free_list: UnsafeCell<*mut FreeNode>,
}

// Safety: We ensure single-threaded access in this implementation.
// For multi-threaded use, wrap free_list in a Mutex or use atomic operations.
unsafe impl Send for PoolAllocator {}
unsafe impl Sync for PoolAllocator {}

use core::cell::UnsafeCell;

impl PoolAllocator {
    /// Create a pool allocator with `slots` slots of `slot_size` bytes each
    ///
    /// # Safety
    /// `memory` must point to at least `slots * slot_size` valid bytes
    pub unsafe fn new(memory: *mut u8, slot_size: usize, slots: usize) -> Self {
        // slot_size must be at least size of a pointer (for free list)
        let slot_size = core::cmp::max(slot_size, core::mem::size_of::<FreeNode>());

        // Initialize the free list
        let mut current = memory as *mut FreeNode;
        for i in 0..slots - 1 {
            let next = memory.add((i + 1) * slot_size) as *mut FreeNode;
            (*current).next = next;
            current = next;
        }
        (*current).next = ptr::null_mut(); // Last slot

        PoolAllocator {
            slot_size,
            slots,
            memory,
            free_list: UnsafeCell::new(memory as *mut FreeNode),
        }
    }

    pub fn allocate(&self) -> *mut u8 {
        unsafe {
            let head = *self.free_list.get();
            if head.is_null() {
                return ptr::null_mut();
            }
            *self.free_list.get() = (*head).next;
            head as *mut u8
        }
    }

    pub fn deallocate(&self, ptr: *mut u8) {
        unsafe {
            let node = ptr as *mut FreeNode;
            (*node).next = *self.free_list.get();
            *self.free_list.get() = node;
        }
    }
}
```

Pool allocators shine when you allocate and deallocate the same type of object repeatedly. Database connection pools, network packet buffers, game entity components — all classic use cases.

## Allocator 3: Free List Allocator

The free list allocator handles variable-size allocations with individual deallocation — the closest to a general-purpose allocator:

```rust
use core::alloc::Layout;
use core::ptr;

#[repr(C)]
struct BlockHeader {
    size: usize,      // Size of data area (not including header)
    is_free: bool,
    next: *mut BlockHeader,
}

const HEADER_SIZE: usize = core::mem::size_of::<BlockHeader>();

pub struct FreeListAllocator {
    head: UnsafeCell<*mut BlockHeader>,
    heap_start: usize,
    heap_size: usize,
}

use core::cell::UnsafeCell;

unsafe impl Send for FreeListAllocator {}
unsafe impl Sync for FreeListAllocator {}

impl FreeListAllocator {
    /// # Safety
    /// `heap_start` must point to at least `heap_size` bytes of valid memory
    pub unsafe fn new(heap_start: usize, heap_size: usize) -> Self {
        // Initialize with one big free block
        let initial_block = heap_start as *mut BlockHeader;
        (*initial_block).size = heap_size - HEADER_SIZE;
        (*initial_block).is_free = true;
        (*initial_block).next = ptr::null_mut();

        FreeListAllocator {
            head: UnsafeCell::new(initial_block),
            heap_start,
            heap_size,
        }
    }

    pub unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let size = layout.size();
        let align = layout.align();

        let mut current = *self.head.get();

        // First-fit search
        while !current.is_null() {
            if (*current).is_free && (*current).size >= size {
                let data_start = (current as usize) + HEADER_SIZE;
                let aligned = (data_start + align - 1) & !(align - 1);
                let padding = aligned - data_start;
                let total_needed = padding + size;

                if (*current).size >= total_needed {
                    // Can we split this block?
                    let remaining = (*current).size - total_needed;
                    if remaining > HEADER_SIZE + 16 {
                        // Split: create a new free block after our allocation
                        let new_block = (aligned + size) as *mut BlockHeader;
                        (*new_block).size = remaining - HEADER_SIZE;
                        (*new_block).is_free = true;
                        (*new_block).next = (*current).next;

                        (*current).size = total_needed;
                        (*current).next = new_block;
                    }

                    (*current).is_free = false;
                    return aligned as *mut u8;
                }
            }
            current = (*current).next;
        }

        ptr::null_mut() // OOM
    }

    pub unsafe fn dealloc(&self, ptr: *mut u8, _layout: Layout) {
        // Find the block header for this pointer
        // (walk the list — in production, you'd store it adjacent to the data)
        let mut current = *self.head.get();

        while !current.is_null() {
            let data_start = (current as usize) + HEADER_SIZE;
            let data_end = data_start + (*current).size;

            if ptr as usize >= data_start && (ptr as usize) < data_end {
                (*current).is_free = true;

                // Coalesce with next block if it's also free
                let next = (*current).next;
                if !next.is_null() && (*next).is_free {
                    (*current).size += HEADER_SIZE + (*next).size;
                    (*current).next = (*next).next;
                }

                return;
            }

            current = (*current).next;
        }
    }
}
```

The free list allocator has the problem of *fragmentation* — after many alloc/dealloc cycles, you end up with many small free blocks that can't satisfy large allocations even though the total free memory is sufficient. The coalescing step helps, but doesn't solve it entirely.

## Allocator 4: Buddy Allocator

The buddy system is elegant. Memory is divided into power-of-two blocks. When you need N bytes, you find the smallest power-of-two block that fits. If there isn't one, you split a larger block in half (creating "buddies"). When you free a block, you check if its buddy is also free — if so, merge them back together.

```rust
const MIN_BLOCK_SIZE: usize = 64;    // Smallest allocatable block
const MAX_ORDER: usize = 20;         // 2^20 * 64 = 64MB max
const NUM_ORDERS: usize = MAX_ORDER + 1;

pub struct BuddyAllocator {
    free_lists: [Vec<usize>; NUM_ORDERS], // Free lists per order
    base_addr: usize,
    total_size: usize,
}

impl BuddyAllocator {
    pub fn new(base_addr: usize, total_size: usize) -> Self {
        let mut allocator = BuddyAllocator {
            free_lists: Default::default(),
            base_addr,
            total_size,
        };

        // Find the largest order that fits
        let max_order = (total_size / MIN_BLOCK_SIZE).trailing_zeros() as usize;
        let order = core::cmp::min(max_order, MAX_ORDER);
        allocator.free_lists[order].push(base_addr);

        allocator
    }

    fn block_size(order: usize) -> usize {
        MIN_BLOCK_SIZE << order
    }

    fn order_for_size(size: usize) -> usize {
        let mut order = 0;
        let mut block = MIN_BLOCK_SIZE;
        while block < size {
            block <<= 1;
            order += 1;
        }
        order
    }

    fn buddy_addr(addr: usize, order: usize) -> usize {
        addr ^ Self::block_size(order)
    }

    pub fn alloc(&mut self, size: usize) -> Option<usize> {
        let target_order = Self::order_for_size(size);

        // Find the smallest available block that's big enough
        let mut found_order = None;
        for order in target_order..NUM_ORDERS {
            if !self.free_lists[order].is_empty() {
                found_order = Some(order);
                break;
            }
        }

        let order = found_order?;
        let block_addr = self.free_lists[order].pop()?;

        // Split down to target order
        let mut current_order = order;
        while current_order > target_order {
            current_order -= 1;
            let buddy = block_addr + Self::block_size(current_order);
            self.free_lists[current_order].push(buddy);
        }

        Some(block_addr)
    }

    pub fn dealloc(&mut self, mut addr: usize, size: usize) {
        let mut order = Self::order_for_size(size);

        // Try to merge with buddy
        while order < MAX_ORDER {
            let buddy = Self::buddy_addr(addr, order);

            // Check if buddy is in the free list
            if let Some(pos) = self.free_lists[order].iter().position(|&a| a == buddy) {
                // Buddy is free — merge!
                self.free_lists[order].swap_remove(pos);
                addr = core::cmp::min(addr, buddy); // Use lower address
                order += 1;
            } else {
                break; // Buddy is allocated, can't merge
            }
        }

        self.free_lists[order].push(addr);
    }
}
```

The Linux kernel uses a buddy allocator for page-level allocation. The power-of-two constraint means some internal fragmentation (a 33-byte allocation wastes 31 bytes in a 64-byte block), but external fragmentation is minimal because buddies always merge cleanly.

## Arena Allocator — The Practical Powerhouse

Arenas are bump allocators with a twist: they can grow by allocating new chunks. The `bumpalo` crate is the standard Rust arena:

```rust
use bumpalo::Bump;

fn arena_example() {
    let arena = Bump::new();

    // Allocate different types in the same arena
    let x: &mut i32 = arena.alloc(42);
    let s: &mut str = arena.alloc_str("hello");
    let v: &mut [u8] = arena.alloc_slice_copy(&[1, 2, 3, 4]);

    // Allocate a Vec that uses the arena
    let mut items = bumpalo::collections::Vec::new_in(&arena);
    items.push("first");
    items.push("second");
    items.push("third");

    // Everything is valid as long as `arena` lives
    println!("{}, {}, {:?}", x, s, v);
    println!("{:?}", items);

    // When `arena` drops, ALL memory is freed at once
    // No individual destructors, no free-list management
}

/// Practical example: request-scoped allocation in a web server
struct Request<'a> {
    method: &'a str,
    path: &'a str,
    headers: bumpalo::collections::Vec<'a, (&'a str, &'a str)>,
    body: &'a [u8],
}

fn handle_request(raw_data: &[u8]) {
    let arena = Bump::with_capacity(4096); // Pre-allocate 4KB

    // All request parsing allocates from the arena
    let request = parse_request(&arena, raw_data);

    // Process the request...
    // All allocations during processing use the arena too

    // Arena drops here — single bulk free instead of hundreds of individual frees
}

fn parse_request<'a>(arena: &'a Bump, _data: &[u8]) -> Request<'a> {
    Request {
        method: arena.alloc_str("GET"),
        path: arena.alloc_str("/api/users"),
        headers: bumpalo::collections::Vec::new_in(arena),
        body: arena.alloc_slice_copy(&[]),
    }
}
```

## Choosing the Right Allocator

| Allocator | Alloc | Free | Fragmentation | Best For |
|-----------|-------|------|---------------|----------|
| Bump | O(1) | N/A (bulk) | None | Phase-based, request-scoped |
| Pool/Slab | O(1) | O(1) | None (fixed size) | Uniform-size objects |
| Free List | O(n) | O(1) | Moderate | General purpose, small heaps |
| Buddy | O(log n) | O(log n) | Internal only | Page allocation, OS kernels |

## Measuring Allocator Performance

Don't guess — measure:

```rust
use std::time::Instant;

fn benchmark_allocator<A: Allocator>(allocator: &A, sizes: &[usize], iterations: usize) {
    let mut ptrs = Vec::with_capacity(iterations);

    let start = Instant::now();
    for i in 0..iterations {
        let size = sizes[i % sizes.len()];
        let layout = Layout::from_size_align(size, 8).unwrap();
        let ptr = unsafe { allocator.alloc(layout) };
        if ptr.is_null() {
            panic!("OOM at iteration {}", i);
        }
        ptrs.push((ptr, layout));
    }
    let alloc_time = start.elapsed();

    let start = Instant::now();
    for (ptr, layout) in ptrs {
        unsafe { allocator.dealloc(ptr, layout) };
    }
    let dealloc_time = start.elapsed();

    println!("  Alloc:   {:?} ({:.0} ns/op)",
        alloc_time, alloc_time.as_nanos() as f64 / iterations as f64);
    println!("  Dealloc: {:?} ({:.0} ns/op)",
        dealloc_time, dealloc_time.as_nanos() as f64 / iterations as f64);
}
```

The allocator that's "fastest" depends entirely on your workload. Profile your application, understand its allocation patterns, then pick (or build) the allocator that matches.

## What I've Learned the Hard Way

**The global allocator is a shared resource.** In multi-threaded programs, every allocation hits a lock (or at least an atomic operation). Thread-local allocators or per-thread arenas eliminate this contention entirely.

**Allocation size distribution matters more than total allocation count.** Many small allocations are harder on allocators than few large ones.

**Memory fragmentation is a slow death.** Your program might run fine for hours, then suddenly fail to allocate 1MB even though there's 100MB "free" — just scattered across thousands of tiny gaps.

**The best allocation is the one you don't make.** Before reaching for a custom allocator, consider: can you use stack allocation? Can you reuse buffers? Can you restructure your data to avoid allocation altogether?

Next lesson, we go to where timing is everything — interrupt handlers and real-time constraints.
