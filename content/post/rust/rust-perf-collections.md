---
title: "Lesson 7: Choosing the Right Collection — It's not always Vec"
author: Atharva Pandey
series: "Rust Performance Engineering"
lesson: 7
keywords: [Rust, rust, performance, collections, HashMap, BTreeMap, Vec, VecDeque, IndexMap, data structures]
tags: [Rust tutorial, rust, performance]
date: '2025-03-27T15:22:00.000Z'
---

A friend asked me to review their service that was doing "thousands of lookups per second" against a `Vec` of about 50,000 entries. Linear scan every time. They'd chosen `Vec` because "it's the default" and hadn't thought about it further. Swapping to a `HashMap` took the lookup from 12µs to 40ns. Three hundred times faster. From a one-line change.

Choosing the right collection is one of the highest-leverage performance decisions you can make, and it requires basically zero cleverness. Just know your access patterns.

## The Decision Framework

Before picking a collection, answer three questions:

1. **What's the primary operation?** Lookup by key? Ordered iteration? Append? Random insert?
2. **How many elements?** 10? 10,000? 10,000,000?
3. **Do you need ordering?** Insertion order? Sorted order? Neither?

The answers map directly to the right collection.

## Vec — The Default (And Usually Right)

`Vec<T>` is a contiguous, growable array. It should be your default choice because:

- **Cache-friendly.** Elements are adjacent in memory, so iterating a Vec is as fast as it gets. L1 cache prefetchers love sequential access.
- **Cheap append.** `push` is amortized O(1).
- **Compact.** No per-element overhead — just the data plus 24 bytes for the Vec header.

```rust
// Vec shines for:
// - Sequential processing
// - Building up results
// - Small collections (< 100 elements) where linear search beats hash overhead

let mut events: Vec<Event> = Vec::with_capacity(1000);
events.push(event);

// Linear search is FAST for small Vecs — cache effects dominate
let found = events.iter().find(|e| e.id == target_id);
```

### When Vec Beats HashMap

For small collections (under ~50-100 elements), linear search on a Vec is often faster than HashMap lookup because:

- No hashing cost
- No hash table overhead (empty slots, metadata)
- Perfect cache locality

```rust
use criterion::{black_box, criterion_group, criterion_main, Criterion};
use std::collections::HashMap;

fn bench_lookup(c: &mut Criterion) {
    for size in [10, 50, 100, 500, 1000] {
        let vec: Vec<(u64, u64)> = (0..size).map(|i| (i, i * 10)).collect();
        let map: HashMap<u64, u64> = vec.iter().copied().collect();
        let target = size / 2;

        c.bench_function(&format!("vec_linear_{}", size), |b| {
            b.iter(|| {
                vec.iter().find(|&&(k, _)| k == black_box(target))
            })
        });

        c.bench_function(&format!("hashmap_lookup_{}", size), |b| {
            b.iter(|| {
                map.get(&black_box(target))
            })
        });
    }
}

criterion_group!(benches, bench_lookup);
criterion_main!(benches);

// Typical results:
//                   10 elem   50 elem   100 elem   500 elem   1000 elem
// vec_linear:       8 ns      35 ns     65 ns      310 ns     620 ns
// hashmap_lookup:   18 ns     19 ns     19 ns      20 ns      20 ns
```

The crossover point is typically around 30-80 elements, depending on the key type. Below that, Vec wins. Above that, HashMap's O(1) lookup dominates.

## HashMap — The Workhorse

`HashMap<K, V>` gives you O(1) average-case lookup, insert, and delete. Rust's HashMap uses SwissTable (hashbrown) internally — it's one of the fastest hash table implementations in existence.

```rust
use std::collections::HashMap;

// Good use: lookup-heavy workloads with many entries
let mut users: HashMap<UserId, User> = HashMap::with_capacity(10_000);
users.insert(id, user);
let user = users.get(&id); // O(1)
```

### HashMap Performance Tips

**Pre-allocate.** `HashMap::with_capacity(n)` avoids rehashing:

```rust
// BAD: triggers multiple rehashes as it grows
let mut map = HashMap::new();
for i in 0..100_000 {
    map.insert(i, value);
}

// GOOD: single allocation
let mut map = HashMap::with_capacity(100_000);
for i in 0..100_000 {
    map.insert(i, value);
}
```

**Use integer keys when possible.** Hashing integers is faster than hashing strings:

```rust
// Fast: integer hash is a few nanoseconds
let mut map: HashMap<u64, Data> = HashMap::new();

// Slower: string hash traverses every byte
let mut map: HashMap<String, Data> = HashMap::new();
```

**Use the entry API for insert-or-update patterns:**

```rust
// BAD: hashes the key twice
if !map.contains_key(&key) {
    map.insert(key.clone(), default_value);
}
*map.get_mut(&key).unwrap() += 1;

// GOOD: hashes once
map.entry(key).and_modify(|v| *v += 1).or_insert(1);
```

### Custom Hashers

Rust's default hasher (SipHash) is designed for DoS resistance — it's not the fastest option. For non-adversarial workloads, faster hashers exist:

```rust
use std::collections::HashMap;
use std::hash::BuildHasherDefault;
use rustc_hash::FxHasher;

type FxHashMap<K, V> = HashMap<K, V, BuildHasherDefault<FxHasher>>;

let mut map: FxHashMap<u64, String> = FxHashMap::default();
```

Or use the `ahash` crate, which is the default in `hashbrown`:

```rust
use ahash::AHashMap;

let mut map: AHashMap<String, u64> = AHashMap::new();
```

Benchmark difference:

```
// HashMap with SipHash (default):  ~22 ns per lookup
// HashMap with FxHash:             ~12 ns per lookup
// HashMap with AHash:              ~14 ns per lookup
```

FxHash is fastest but has worse distribution for some key patterns. AHash is a good middle ground.

## BTreeMap — When You Need Order

`BTreeMap<K, V>` keeps keys sorted. Lookups are O(log n) instead of O(1), but you get:

- Sorted iteration
- Range queries
- No hashing required (just `Ord`)

```rust
use std::collections::BTreeMap;

let mut prices: BTreeMap<String, f64> = BTreeMap::new();
prices.insert("AAPL".into(), 150.0);
prices.insert("GOOG".into(), 2800.0);
prices.insert("MSFT".into(), 300.0);

// Range query: all tickers between "A" and "M"
for (ticker, price) in prices.range("A".to_string().."M".to_string()) {
    println!("{}: {}", ticker, price);
}
```

BTreeMap is also more cache-friendly than you'd expect — B-tree nodes store multiple keys contiguously, unlike binary trees where each node is a separate allocation.

**Use BTreeMap when:**
- You need sorted iteration
- You need range queries
- Your keys don't implement `Hash`
- You have a moderate number of elements (<100K) and need predictable performance (no hash collisions)

## IndexMap — Insertion Order + Fast Lookup

`IndexMap` from the `indexmap` crate gives you HashMap-speed lookups while preserving insertion order:

```rust
use indexmap::IndexMap;

let mut headers: IndexMap<String, String> = IndexMap::new();
headers.insert("Content-Type".into(), "application/json".into());
headers.insert("Authorization".into(), "Bearer xyz".into());
headers.insert("Accept".into(), "application/json".into());

// Fast lookup: O(1)
let ct = headers.get("Content-Type");

// Iteration in insertion order (unlike HashMap which is random)
for (key, value) in &headers {
    println!("{}: {}", key, value);
}
```

This is perfect for HTTP headers, configuration maps, and anywhere insertion order matters.

## VecDeque — Double-Ended Queue

`VecDeque<T>` is a ring buffer. O(1) push/pop at both ends, unlike Vec which is O(n) for `insert(0, x)` or `remove(0)`.

```rust
use std::collections::VecDeque;

// Perfect for: sliding windows, BFS, work queues
let mut queue: VecDeque<Task> = VecDeque::with_capacity(1000);
queue.push_back(task);       // O(1)
let next = queue.pop_front(); // O(1)
```

```rust
// Vec::remove(0) is O(n) — shifts all elements left
// VecDeque::pop_front() is O(1) — just moves a pointer

#[divan::bench(args = [100, 1_000, 10_000])]
fn vec_remove_front(n: usize) {
    let mut v: Vec<u32> = (0..n as u32).collect();
    for _ in 0..100 {
        v.remove(0); // O(n) each time!
        v.push(0);
    }
}

#[divan::bench(args = [100, 1_000, 10_000])]
fn vecdeque_pop_front(n: usize) {
    let mut v: VecDeque<u32> = (0..n as u32).collect();
    for _ in 0..100 {
        v.pop_front(); // O(1)
        v.push_back(0);
    }
}

// At 10,000 elements:
// vec_remove_front:    ~180 µs
// vecdeque_pop_front:  ~0.3 µs
```

## HashSet — When You Only Need Keys

If you just need to check membership, `HashSet` avoids the overhead of storing values:

```rust
use std::collections::HashSet;

let mut seen: HashSet<u64> = HashSet::with_capacity(10_000);
if seen.insert(id) {
    // id was NOT already present — process it
    process_new(id);
}
```

For small sets (<50 elements), a sorted `Vec` with `binary_search` can be faster because of cache locality.

## BinaryHeap — Priority Queue

When you need the minimum or maximum element efficiently:

```rust
use std::collections::BinaryHeap;
use std::cmp::Reverse;

// Min-heap (smallest first)
let mut heap: BinaryHeap<Reverse<u32>> = BinaryHeap::new();
heap.push(Reverse(42));
heap.push(Reverse(17));
heap.push(Reverse(99));

assert_eq!(heap.pop(), Some(Reverse(17))); // smallest
```

O(log n) push and pop. Perfect for priority queues, top-K queries, and Dijkstra's algorithm.

## The Collection Cheat Sheet

| Need | Collection | Lookup | Insert | Iterate | Ordered |
|------|-----------|--------|--------|---------|---------|
| Sequential storage | `Vec` | O(n) | O(1)* | Fast | Insertion |
| Key-value lookup | `HashMap` | O(1) | O(1) | Medium | No |
| Sorted key-value | `BTreeMap` | O(log n) | O(log n) | Fast | Sorted |
| Ordered key-value | `IndexMap` | O(1) | O(1) | Fast | Insertion |
| Membership test | `HashSet` | O(1) | O(1) | Medium | No |
| FIFO queue | `VecDeque` | O(n) | O(1)** | Fast | Insertion |
| Priority queue | `BinaryHeap` | O(n) | O(log n) | N/A | Priority |

\* Amortized, at the end.
\** At both ends.

## Beyond std: Specialized Collections

For extreme performance needs:

- **`dashmap`** — Concurrent HashMap (sharded locks, no global mutex)
- **`slotmap`** — Generational arena with stable keys (great for ECS)
- **`slab`** — Pre-allocated storage with O(1) insert/remove/lookup
- **`im`** — Persistent/immutable collections (structural sharing)
- **`roaring`** — Compressed bitmaps for integer sets

```rust
use dashmap::DashMap;

// Concurrent HashMap — no Mutex needed
let map: DashMap<String, u64> = DashMap::new();

// Can be used from multiple threads without wrapping
std::thread::scope(|s| {
    s.spawn(|| { map.insert("a".into(), 1); });
    s.spawn(|| { map.insert("b".into(), 2); });
});
```

## The Takeaway

The right collection for the job depends on your access pattern, not your habits. Don't reach for `Vec` when you need O(1) lookups. Don't reach for `HashMap` when you have 10 elements. Don't reach for `BTreeMap` when you don't need ordering.

Profile your code (Lesson 3), identify the hot data structures, and evaluate whether a different collection would eliminate the bottleneck. It's usually the simplest optimization you can make — and often the most impactful.
