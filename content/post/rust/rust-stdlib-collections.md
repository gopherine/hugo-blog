---
title: "Lesson 1: Collections Deep Dive — Vec, VecDeque, BTreeMap and when to use which"
author: Atharva Pandey
series: "Rust Standard Library Mastery"
lesson: 1
keywords: [Rust, rust, standard library, collections, Vec, VecDeque, HashMap, BTreeMap, HashSet]
tags: [Rust tutorial, rust, stdlib]
date: '2024-09-15T10:22:00.000Z'
---

I spent two days debugging a performance cliff in a data pipeline — turns out I'd been using a `HashMap` where a `BTreeMap` would've cut iteration time in half because I needed sorted output downstream. The collection you pick matters more than most people think.

## The Big Picture

Rust's standard library ships a small but deliberate set of collections. Unlike languages that give you seventeen flavors of list, Rust gives you a few well-designed options and expects you to understand the trade-offs. That's actually a feature — fewer choices means you can develop genuine intuition about when to use what.

Here's the mental model I use: every collection sits on a spectrum between "fast random access" and "fast insertion/removal." Your job is to figure out which operations your code actually cares about.

## Vec — Your Default Choice

`Vec<T>` is a growable array. Contiguous memory, cache-friendly, amortized O(1) push to the back. If you're not sure what to use, start with `Vec`. You'll be right more often than not.

```rust
fn main() {
    let mut scores: Vec<i32> = Vec::new();

    // Push is amortized O(1) — Vec doubles capacity when full
    scores.push(95);
    scores.push(87);
    scores.push(92);

    // Index access is O(1)
    println!("First score: {}", scores[0]);

    // But .get() is safer — returns Option<&T>
    match scores.get(10) {
        Some(s) => println!("Score: {s}"),
        None => println!("No score at index 10"),
    }

    // Pre-allocate when you know the size
    let mut known_size: Vec<String> = Vec::with_capacity(1000);
    println!("Length: {}, Capacity: {}", known_size.len(), known_size.capacity());

    for i in 0..1000 {
        known_size.push(format!("item-{i}"));
    }
    // No reallocations happened — we allocated exactly once

    // Drain gives you ownership of removed elements
    let removed: Vec<String> = known_size.drain(0..5).collect();
    println!("Removed {} items", removed.len());

    // Retain is your in-place filter
    let mut nums = vec![1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
    nums.retain(|&x| x % 2 == 0);
    assert_eq!(nums, vec![2, 4, 6, 8, 10]);
}
```

The thing people miss about `Vec` is that operations at the *front* are O(n). Every `insert(0, val)` shifts the entire contents. I've seen codebases where someone builds a queue with `Vec::insert(0, ...)` and `Vec::pop()` — that's an O(n) enqueue on every call. Don't do this.

### Vec Tricks Worth Knowing

```rust
fn main() {
    // Dedup removes *consecutive* duplicates — sort first if you want all dupes gone
    let mut vals = vec![3, 1, 2, 1, 3, 2, 1];
    vals.sort();
    vals.dedup();
    assert_eq!(vals, vec![1, 2, 3]);

    // split_off chops a Vec in two
    let mut first_half = vec![1, 2, 3, 4, 5, 6];
    let second_half = first_half.split_off(3);
    assert_eq!(first_half, vec![1, 2, 3]);
    assert_eq!(second_half, vec![4, 5, 6]);

    // windows and chunks for sliding window patterns
    let data = vec![1, 2, 3, 4, 5];
    let moving_averages: Vec<f64> = data
        .windows(3)
        .map(|w| w.iter().sum::<i32>() as f64 / 3.0)
        .collect();
    println!("Moving averages: {moving_averages:?}");

    // swap_remove is O(1) removal if you don't care about order
    let mut items = vec!["a", "b", "c", "d", "e"];
    items.swap_remove(1); // Swaps "b" with "e", then pops
    assert_eq!(items, vec!["a", "e", "c", "d"]);
}
```

`swap_remove` is criminally underused. If you're removing elements from a Vec and order doesn't matter, it's O(1) instead of O(n). I use it constantly in game-loop style code where you're processing and removing entities.

## VecDeque — When You Need Both Ends

`VecDeque<T>` is a double-ended queue backed by a ring buffer. O(1) push and pop from *both* front and back. This is what you want when you're building a queue, a sliding window, or any FIFO structure.

```rust
use std::collections::VecDeque;

fn main() {
    let mut queue: VecDeque<String> = VecDeque::new();

    // O(1) operations on both ends
    queue.push_back("first".to_string());
    queue.push_back("second".to_string());
    queue.push_back("third".to_string());
    queue.push_front("zeroth".to_string());

    // FIFO: pop from the front
    while let Some(item) = queue.pop_front() {
        println!("Processing: {item}");
    }

    // Sliding window of last N items
    let mut window: VecDeque<f64> = VecDeque::with_capacity(5);
    let readings = [1.0, 2.5, 3.7, 4.2, 5.1, 6.3, 7.0];

    for reading in readings {
        if window.len() == 5 {
            window.pop_front();
        }
        window.push_back(reading);

        let avg: f64 = window.iter().sum::<f64>() / window.len() as f64;
        println!("Window avg: {avg:.2} (size: {})", window.len());
    }
}
```

One gotcha: `VecDeque` doesn't guarantee contiguous memory. If you need to pass its contents to something expecting a slice, call `make_contiguous()` first or use `as_slices()` which gives you two slices (the ring buffer might wrap around).

```rust
use std::collections::VecDeque;

fn process_slice(data: &[i32]) {
    println!("Processing {} items", data.len());
}

fn main() {
    let mut deque = VecDeque::from(vec![1, 2, 3, 4, 5]);

    // This might be two separate slices internally
    let (front, back) = deque.as_slices();
    println!("Front: {front:?}, Back: {back:?}");

    // Force it into one contiguous slice
    let slice = deque.make_contiguous();
    process_slice(slice);
}
```

## HashMap and HashSet — Fast Lookup

`HashMap<K, V>` is your go-to for key-value lookups. O(1) average case for insert, lookup, and removal. Rust's implementation uses a Robin Hood hashing strategy (via hashbrown) that's genuinely fast.

```rust
use std::collections::HashMap;

fn main() {
    let mut word_count: HashMap<String, usize> = HashMap::new();

    let text = "the quick brown fox jumps over the lazy dog the fox";
    for word in text.split_whitespace() {
        // entry() is the idiomatic way to handle "insert if missing"
        *word_count.entry(word.to_string()).or_insert(0) += 1;
    }

    for (word, count) in &word_count {
        println!("{word}: {count}");
    }

    // or_insert_with for expensive default values
    let mut cache: HashMap<String, Vec<u8>> = HashMap::new();
    let key = "large-computation".to_string();
    let result = cache.entry(key).or_insert_with(|| {
        println!("Computing...");
        vec![0u8; 1024] // Only runs if key is missing
    });
    println!("Result length: {}", result.len());
}
```

The `entry` API is one of my favorite things about Rust's HashMap. In most languages, you'd do a lookup, check if the key exists, then insert — that's two hash computations. `entry()` hashes once and gives you a handle to either the existing value or the vacant slot.

```rust
use std::collections::{HashMap, HashSet};

fn main() {
    // HashSet is just HashMap<T, ()> underneath
    let mut seen: HashSet<String> = HashSet::new();

    let items = vec!["apple", "banana", "apple", "cherry", "banana"];
    for item in items {
        if seen.insert(item.to_string()) {
            println!("New item: {item}");
        } else {
            println!("Already seen: {item}");
        }
    }

    // Set operations
    let a: HashSet<i32> = [1, 2, 3, 4].into_iter().collect();
    let b: HashSet<i32> = [3, 4, 5, 6].into_iter().collect();

    let union: HashSet<_> = a.union(&b).collect();
    let intersection: HashSet<_> = a.intersection(&b).collect();
    let difference: HashSet<_> = a.difference(&b).collect();

    println!("Union: {union:?}");
    println!("Intersection: {intersection:?}");
    println!("A - B: {difference:?}");
}
```

### HashMap Performance Pitfalls

The biggest one: **don't use `String` as a key when `&str` would work.** Every lookup with a `String` key means you're allocating just to do a hash comparison. Thankfully, `HashMap<String, V>` implements `get` for `&str` thanks to the `Borrow` trait:

```rust
use std::collections::HashMap;

fn main() {
    let mut map: HashMap<String, i32> = HashMap::new();
    map.insert("key".to_string(), 42);

    // This works — no allocation needed for lookup
    let val = map.get("key");
    println!("{val:?}");

    // Pre-size your maps when you know roughly how many entries
    let mut large_map: HashMap<u64, String> = HashMap::with_capacity(10_000);
    for i in 0..10_000u64 {
        large_map.insert(i, format!("value-{i}"));
    }
    // No rehashing happened
}
```

## BTreeMap and BTreeSet — Sorted Collections

`BTreeMap<K, V>` keeps keys sorted. Operations are O(log n) instead of O(1), but you get ordered iteration, range queries, and `first`/`last` access. If you need your data sorted or you need range scans, this is your collection.

```rust
use std::collections::BTreeMap;

fn main() {
    let mut scores: BTreeMap<String, Vec<i32>> = BTreeMap::new();

    scores.insert("Charlie".to_string(), vec![85, 92]);
    scores.insert("Alice".to_string(), vec![95, 88]);
    scores.insert("Eve".to_string(), vec![78, 91]);
    scores.insert("Bob".to_string(), vec![90, 87]);
    scores.insert("Diana".to_string(), vec![82, 94]);

    // Iteration is always sorted by key
    for (name, s) in &scores {
        let avg: f64 = s.iter().sum::<i32>() as f64 / s.len() as f64;
        println!("{name}: {avg:.1}");
    }
    // Prints: Alice, Bob, Charlie, Diana, Eve — alphabetical

    // Range queries — get all names from "B" to "D" (inclusive)
    println!("\nNames B through D:");
    for (name, _) in scores.range("B".to_string()..="D".to_string()) {
        println!("  {name}");
    }

    // First and last
    if let Some((first, _)) = scores.first_key_value() {
        println!("\nFirst: {first}");
    }
    if let Some((last, _)) = scores.last_key_value() {
        println!("Last: {last}");
    }
}
```

I reach for `BTreeMap` in three situations: when I need sorted output, when I need range queries, or when my keys don't implement `Hash` (since `BTreeMap` only requires `Ord`). That third case comes up more than you'd expect with custom types.

```rust
use std::collections::BTreeMap;

// This type doesn't implement Hash, but it does implement Ord
#[derive(Debug, Eq, PartialEq, Ord, PartialOrd)]
struct Version {
    major: u32,
    minor: u32,
    patch: u32,
}

fn main() {
    let mut releases: BTreeMap<Version, String> = BTreeMap::new();

    releases.insert(Version { major: 1, minor: 0, patch: 0 }, "Initial release".into());
    releases.insert(Version { major: 1, minor: 2, patch: 0 }, "Added widgets".into());
    releases.insert(Version { major: 1, minor: 1, patch: 0 }, "Bug fixes".into());
    releases.insert(Version { major: 2, minor: 0, patch: 0 }, "Breaking changes".into());

    // Automatically sorted by version
    for (ver, desc) in &releases {
        println!("{}.{}.{}: {desc}", ver.major, ver.minor, ver.patch);
    }
}
```

## LinkedList — You Probably Don't Want This

Rust has `LinkedList<T>` in the standard library. I'm mentioning it so I can tell you to almost never use it. Linked lists have terrible cache locality — every node is a separate heap allocation, so traversal causes cache misses on nearly every step. `Vec` or `VecDeque` will beat `LinkedList` in almost every real-world scenario.

The only legitimate use case I've encountered is when you need O(1) splicing — merging two lists or splitting one in half. And even then, measure first.

## BinaryHeap — Priority Queues

`BinaryHeap<T>` gives you a max-heap. O(log n) push and pop, and `peek()` always returns the largest element.

```rust
use std::collections::BinaryHeap;
use std::cmp::Reverse;

fn main() {
    // Max-heap by default
    let mut max_heap = BinaryHeap::new();
    max_heap.push(5);
    max_heap.push(1);
    max_heap.push(10);
    max_heap.push(3);

    while let Some(val) = max_heap.pop() {
        print!("{val} "); // 10 5 3 1
    }
    println!();

    // Min-heap: wrap values in Reverse
    let mut min_heap: BinaryHeap<Reverse<i32>> = BinaryHeap::new();
    min_heap.push(Reverse(5));
    min_heap.push(Reverse(1));
    min_heap.push(Reverse(10));
    min_heap.push(Reverse(3));

    while let Some(Reverse(val)) = min_heap.pop() {
        print!("{val} "); // 1 3 5 10
    }
    println!();

    // Task scheduling with priorities
    #[derive(Debug, Eq, PartialEq)]
    struct Task {
        priority: u32,
        name: String,
    }

    impl Ord for Task {
        fn cmp(&self, other: &Self) -> std::cmp::Ordering {
            self.priority.cmp(&other.priority)
        }
    }

    impl PartialOrd for Task {
        fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
            Some(self.cmp(other))
        }
    }

    let mut tasks = BinaryHeap::new();
    tasks.push(Task { priority: 3, name: "Send email".into() });
    tasks.push(Task { priority: 10, name: "Fix production bug".into() });
    tasks.push(Task { priority: 1, name: "Update docs".into() });
    tasks.push(Task { priority: 7, name: "Code review".into() });

    while let Some(task) = tasks.pop() {
        println!("[P{}] {}", task.priority, task.name);
    }
}
```

## Decision Framework

Here's the cheat sheet I keep in my head:

| Need | Use | Why |
|------|-----|-----|
| Growable array, random access | `Vec<T>` | Cache-friendly, O(1) index |
| Queue (FIFO) or deque | `VecDeque<T>` | O(1) both ends |
| Key-value lookup | `HashMap<K, V>` | O(1) average |
| Sorted key-value | `BTreeMap<K, V>` | O(log n) but ordered iteration |
| Unique set membership | `HashSet<T>` | O(1) contains |
| Sorted unique set | `BTreeSet<T>` | O(log n) but range queries |
| Priority queue | `BinaryHeap<T>` | O(log n) push/pop, O(1) peek max |

And here's the question I always ask myself: "What operation does this code do *most frequently*?" If it's random access, use Vec. If it's front-and-back operations, VecDeque. If it's lookups by key, HashMap. If it's "give me the top N," BinaryHeap.

The wrong collection choice won't crash your program. It'll just make it slower than it needs to be — and that slowness will compound in production under load, exactly when you need performance most.
