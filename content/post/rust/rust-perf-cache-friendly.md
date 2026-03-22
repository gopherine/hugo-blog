---
title: "Lesson 8: Cache-Friendly Data Structures — Data-oriented design"
author: Atharva Pandey
series: "Rust Performance Engineering"
lesson: 8
keywords: [Rust, rust, performance, cache, data-oriented design, SoA, AoS, cache line, CPU cache, struct of arrays]
tags: [Rust tutorial, rust, performance]
date: '2025-03-29T07:40:00.000Z'
---

Here's a number that should change how you think about data structures: reading from L1 cache takes about 1 nanosecond. Reading from main memory takes about 100 nanoseconds. That's a 100x penalty for a cache miss. On a modern CPU running at 4 GHz, a single cache miss stalls the processor for roughly 400 cycles. Four hundred cycles where your CPU is sitting there, doing nothing, waiting for data to arrive from RAM.

I spent a month optimizing a particle simulation once. The algorithm was perfect — O(n log n) neighbor finding, spatial hashing, the works. But it was crawling. Profiling showed 35% of cycles wasted on cache misses. The data layout was the bottleneck, not the algorithm. Restructuring the data — same algorithm, same logic — gave me a 4x speedup.

This lesson is about how to think about data layout.

## How CPU Caches Work (The Short Version)

Your CPU has a hierarchy of caches:

```
L1 cache:  ~32 KB,  ~1 ns latency,   ~4 cycles
L2 cache:  ~256 KB, ~5 ns latency,   ~12 cycles
L3 cache:  ~8 MB,   ~20 ns latency,  ~40 cycles
Main RAM:  ~GB,     ~100 ns latency, ~200+ cycles
```

When you access memory, the CPU doesn't fetch a single byte. It fetches a **cache line** — typically 64 bytes. This means that after you access one byte, the next 63 bytes are already in cache. If your data is laid out so that you access things sequentially, you get those cache fills for free. If your data is scattered across memory, every access is a potential cache miss.

The CPU also has a **prefetcher** that detects sequential access patterns and speculatively loads the next few cache lines before you ask for them. This is why iterating a `Vec` is incredibly fast — the prefetcher keeps the pipeline fed.

## Array of Structs vs Struct of Arrays

This is the most impactful layout decision you'll make. Let me show you with a game-engine example.

### Array of Structs (AoS) — The Default

```rust
// Array of Structs — how most people write it
struct Particle {
    position: [f32; 3],  // 12 bytes
    velocity: [f32; 3],  // 12 bytes
    color: [f32; 4],     // 16 bytes
    mass: f32,           // 4 bytes
    lifetime: f32,       // 4 bytes
    is_active: bool,     // 1 byte + 3 padding
    _padding: [u8; 3],
}
// Total: 52 bytes per particle (rounded to 56 with alignment)

let particles: Vec<Particle> = Vec::with_capacity(100_000);
```

Memory layout:

```
[pos vel color mass life active | pos vel color mass life active | ...]
 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
 particle 0 (56 bytes)            particle 1 (56 bytes)
```

Now imagine you want to update just the positions based on velocities. You iterate all particles and touch the `position` and `velocity` fields. But each cache line (64 bytes) barely fits one particle. You're loading 56 bytes per particle but only using 24 bytes (position + velocity). That's 57% wasted bandwidth.

### Struct of Arrays (SoA) — Cache Optimized

```rust
// Struct of Arrays — data-oriented design
struct Particles {
    positions: Vec<[f32; 3]>,   // all positions contiguous
    velocities: Vec<[f32; 3]>,  // all velocities contiguous
    colors: Vec<[f32; 4]>,      // all colors contiguous
    masses: Vec<f32>,
    lifetimes: Vec<f32>,
    active: Vec<bool>,
}

impl Particles {
    fn new(capacity: usize) -> Self {
        Self {
            positions: Vec::with_capacity(capacity),
            velocities: Vec::with_capacity(capacity),
            colors: Vec::with_capacity(capacity),
            masses: Vec::with_capacity(capacity),
            lifetimes: Vec::with_capacity(capacity),
            active: Vec::with_capacity(capacity),
        }
    }

    fn update_positions(&mut self, dt: f32) {
        // Now we're iterating two contiguous arrays
        // Each cache line holds ~5 positions — perfect utilization
        for (pos, vel) in self.positions.iter_mut()
            .zip(self.velocities.iter())
        {
            pos[0] += vel[0] * dt;
            pos[1] += vel[1] * dt;
            pos[2] += vel[2] * dt;
        }
    }
}
```

Memory layout:

```
positions:  [pos0 pos1 pos2 pos3 pos4 | pos5 pos6 pos7 pos8 pos9 | ...]
velocities: [vel0 vel1 vel2 vel3 vel4 | vel5 vel6 vel7 vel8 vel9 | ...]
```

When updating positions, every byte we load is data we actually use. The prefetcher has a trivially predictable sequential pattern. And LLVM can auto-vectorize this with SIMD instructions because the data is contiguous and uniformly typed.

### Benchmark: AoS vs SoA

```rust
use criterion::{black_box, criterion_group, criterion_main, Criterion};

// AoS version
struct ParticleAoS {
    position: [f32; 3],
    velocity: [f32; 3],
    color: [f32; 4],
    mass: f32,
    lifetime: f32,
}

fn update_aos(particles: &mut [ParticleAoS], dt: f32) {
    for p in particles.iter_mut() {
        p.position[0] += p.velocity[0] * dt;
        p.position[1] += p.velocity[1] * dt;
        p.position[2] += p.velocity[2] * dt;
    }
}

// SoA version
struct ParticlesSoA {
    positions: Vec<[f32; 3]>,
    velocities: Vec<[f32; 3]>,
}

fn update_soa(particles: &mut ParticlesSoA, dt: f32) {
    for (pos, vel) in particles.positions.iter_mut()
        .zip(particles.velocities.iter())
    {
        pos[0] += vel[0] * dt;
        pos[1] += vel[1] * dt;
        pos[2] += vel[2] * dt;
    }
}

fn bench(c: &mut Criterion) {
    let n = 100_000;

    let mut aos: Vec<ParticleAoS> = (0..n)
        .map(|i| ParticleAoS {
            position: [i as f32, 0.0, 0.0],
            velocity: [1.0, 0.5, 0.25],
            color: [1.0, 1.0, 1.0, 1.0],
            mass: 1.0,
            lifetime: 10.0,
        })
        .collect();

    let mut soa = ParticlesSoA {
        positions: (0..n).map(|i| [i as f32, 0.0, 0.0]).collect(),
        velocities: vec![[1.0, 0.5, 0.25]; n],
    };

    c.bench_function("update_aos_100k", |b| {
        b.iter(|| update_aos(black_box(&mut aos), 0.016))
    });

    c.bench_function("update_soa_100k", |b| {
        b.iter(|| update_soa(black_box(&mut soa), 0.016))
    });
}

criterion_group!(benches, bench);
criterion_main!(benches);

// Typical results:
// update_aos_100k: ~310 µs
// update_soa_100k: ~85 µs
```

3.6x faster. Same algorithm, same computation, different data layout. The SoA version is faster because:
1. Better cache utilization (100% of loaded data is used)
2. Prefetcher works perfectly (sequential access)
3. LLVM can vectorize the SoA version with SIMD

## Hot/Cold Splitting

Sometimes full SoA is overkill. A simpler approach: split your struct into hot (frequently accessed) and cold (rarely accessed) parts:

```rust
// Instead of one big struct...
struct Entity {
    // Hot — accessed every frame
    position: [f32; 3],
    velocity: [f32; 3],
    health: f32,

    // Cold — accessed rarely
    name: String,
    description: String,
    creation_time: u64,
    metadata: HashMap<String, String>,
}

// Split into hot and cold
struct EntityHot {
    position: [f32; 3],
    velocity: [f32; 3],
    health: f32,
}

struct EntityCold {
    name: String,
    description: String,
    creation_time: u64,
    metadata: HashMap<String, String>,
}

struct World {
    hot: Vec<EntityHot>,    // iterated every frame
    cold: Vec<EntityCold>,  // accessed on demand
}
```

Now the hot data is tightly packed. Each cache line holds multiple `EntityHot` structs. The cold data doesn't pollute the cache during the update loop.

## Pointer Chasing — The Cache Killer

Every time you follow a pointer, you potentially pay a cache miss. Linked lists are the classic example:

```rust
// Linked list: every node is a separate allocation
// Following ->next is a potential cache miss
// DO NOT use LinkedList in performance-sensitive code

// Vec: contiguous memory, sequential access
// No pointer chasing, prefetcher-friendly
let data: Vec<Item> = items.collect();
```

But it's not just linked lists. Any `Box`, `String`, or `Vec` inside your struct is a pointer to heap memory:

```rust
struct Record {
    id: u64,
    name: String,     // pointer → heap
    tags: Vec<String>, // pointer → heap → more pointers → more heap
}
```

Iterating a `Vec<Record>` means: read Record from Vec (sequential, fast), then dereference `name` (cache miss), then dereference each tag (more cache misses). The Vec iteration is fast, but the data it points to is scattered.

Mitigation strategies:
- Use `CompactString` (Lesson 6) to keep short strings inline
- Use `SmallVec` to keep short arrays inline
- Use flat indices instead of pointers when possible

```rust
// Instead of Box<Node> for tree nodes, use a Vec + indices
struct Arena {
    nodes: Vec<Node>,
}

struct Node {
    value: u64,
    left: Option<u32>,   // index into Arena.nodes
    right: Option<u32>,  // index into Arena.nodes
}

// All nodes are contiguous in memory
// "Following a pointer" is just indexing into the Vec
```

## Struct Size and Alignment

Rust lays out struct fields with padding for alignment. This can waste space:

```rust
// Poor layout — lots of padding
struct Wasteful {
    a: u8,      // 1 byte + 7 padding
    b: u64,     // 8 bytes
    c: u8,      // 1 byte + 3 padding
    d: u32,     // 4 bytes
}
// Size: 24 bytes (only 14 bytes of data)

// Rust's default repr reorders fields for optimal packing
// But #[repr(C)] preserves your order — avoid it unless needed
```

By default, Rust is free to reorder struct fields for better packing. But if you've used `#[repr(C)]` (for FFI compatibility), you've forced the compiler to use your field order, which might be suboptimal.

You can check struct sizes and alignment:

```rust
println!("Size: {}", std::mem::size_of::<MyStruct>());
println!("Align: {}", std::mem::align_of::<MyStruct>());
```

For cache performance, try to keep frequently-accessed structs within 64 bytes (one cache line) or within 128 bytes (two cache lines).

## False Sharing

In concurrent code, false sharing happens when two threads modify different variables that happen to be on the same cache line. The CPU's cache coherency protocol forces both cores to invalidate and reload the line, even though they're not actually sharing data.

```rust
use std::sync::atomic::{AtomicU64, Ordering};

// BAD: both counters on the same cache line
struct Counters {
    counter_a: AtomicU64,  // thread 1 writes this
    counter_b: AtomicU64,  // thread 2 writes this
    // These are 8 bytes apart — same cache line!
}

// GOOD: pad to separate cache lines
#[repr(C)]
struct PaddedCounters {
    counter_a: AtomicU64,
    _pad_a: [u8; 56],     // fill rest of 64-byte cache line
    counter_b: AtomicU64,
    _pad_b: [u8; 56],
}
```

Or use `crossbeam_utils::CachePadded`:

```rust
use crossbeam_utils::CachePadded;

struct Counters {
    counter_a: CachePadded<AtomicU64>,
    counter_b: CachePadded<AtomicU64>,
}
```

False sharing can cause 10-50x slowdowns in concurrent code. If you're seeing unexpectedly bad scaling with more threads, check for this.

## Measuring Cache Performance

Use `perf stat` to measure cache behavior:

```bash
perf stat -e cache-misses,cache-references,L1-dcache-load-misses,LLC-load-misses \
    ./target/release/my_program

# Sample output:
#    1,234,567  cache-misses       # 3.45% of all cache refs
#   35,789,012  cache-references
#      456,789  L1-dcache-load-misses
#       23,456  LLC-load-misses    # Last-level cache misses = main memory trips
```

A cache miss rate above 5% usually means your data layout needs work. Below 1% is excellent.

## Practical Guidelines

1. **Use Vec for almost everything.** Contiguous memory is king. Even when you need a "tree" or "graph," consider storing nodes in a Vec and using indices.

2. **Profile before restructuring.** SoA is more complex to work with than AoS. Only switch if profiling shows cache misses are your bottleneck.

3. **Keep hot data small.** The less data you touch per iteration, the more elements fit in cache. Remove unused fields from hot structs.

4. **Avoid unnecessary indirection.** Every `Box`, `String`, `Vec` field is a pointer chase. Use inline alternatives when the data is small.

5. **Sort your data.** If you're doing lookups, sorted data has better cache behavior than random-order data because the prefetcher can predict access patterns.

6. **Batch your operations.** Process all items for operation A, then all items for operation B. Don't interleave — it thrashes the cache.

## The Takeaway

Cache-friendly data layout can give you 2-10x speedups with zero algorithmic changes. The key insight: CPUs are fast, memory is slow, and caches bridge the gap — but only if your data is laid out to exploit them.

Think about what data you access together and keep it together. Think about what data you access sequentially and make it contiguous. The hardware will reward you.
