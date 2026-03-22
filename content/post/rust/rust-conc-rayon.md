---
title: "Lesson 10: Rayon — Data parallelism made easy"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 10
keywords: [Rust, rust, concurrency, rayon, data parallelism, parallel iterators, par_iter]
tags: [Rust tutorial, rust, concurrency]
date: '2024-11-23T15:20:00.000Z'
---

I had a batch processing job — reading 50,000 JSON files, parsing them, running validation, writing results. Single-threaded, it took 12 minutes. I added Rayon, changed `.iter()` to `.par_iter()`, and it dropped to 90 seconds. Five characters added to my code. That's it.

Rayon is probably the most impressive crate in the Rust ecosystem for the effort-to-impact ratio. If you have CPU-bound work that operates on collections, Rayon makes parallelism trivial.

## The Problem: Manual Thread Management

Parallelizing work by hand is tedious and error-prone:

```rust
use std::thread;

fn process(item: &str) -> String {
    // expensive computation
    std::thread::sleep(std::time::Duration::from_millis(10));
    item.to_uppercase()
}

fn main() {
    let items: Vec<String> = (0..1000).map(|i| format!("item-{}", i)).collect();
    let chunk_size = items.len() / num_cpus::get();

    let mut handles = vec![];
    for chunk in items.chunks(chunk_size) {
        let owned: Vec<String> = chunk.to_vec();
        handles.push(thread::spawn(move || {
            owned.iter().map(|item| process(item)).collect::<Vec<_>>()
        }));
    }

    let results: Vec<String> = handles
        .into_iter()
        .flat_map(|h| h.join().unwrap())
        .collect();

    println!("Processed {} items", results.len());
}
```

That's a lot of boilerplate. You're managing chunks, spawning threads, collecting results, dealing with ownership. And what if the chunks are uneven? What if some items take longer than others?

## The Solution: par_iter

```rust
use rayon::prelude::*;

fn process(item: &str) -> String {
    std::thread::sleep(std::time::Duration::from_millis(10));
    item.to_uppercase()
}

fn main() {
    let items: Vec<String> = (0..1000).map(|i| format!("item-{}", i)).collect();

    let results: Vec<String> = items
        .par_iter()
        .map(|item| process(item))
        .collect();

    println!("Processed {} items", results.len());
}
```

Add `rayon` to your `Cargo.toml`, change `iter()` to `par_iter()`, done. Rayon handles thread pool management, work stealing, load balancing — all of it.

```toml
[dependencies]
rayon = "1.10"
```

## How Rayon Works

Rayon uses a **work-stealing thread pool**. Here's the basic idea:

1. Rayon maintains a global thread pool (by default, one thread per CPU core)
2. When you call `par_iter()`, the work is split recursively into chunks
3. Each thread grabs a chunk and processes it
4. If a thread finishes early, it *steals* work from other threads' queues
5. Results are combined and returned

Work stealing is what makes Rayon handle uneven workloads well. If 90% of the work is in 10% of the items, threads that finish the easy items steal from the busy thread's queue. No idle cores.

## Parallel Iterator Methods

Most iterator methods have parallel equivalents:

```rust
use rayon::prelude::*;

fn main() {
    let numbers: Vec<i64> = (0..1_000_000).collect();

    // par_iter — parallel immutable iteration
    let sum: i64 = numbers.par_iter().sum();
    println!("Sum: {}", sum);

    // map + collect
    let squares: Vec<i64> = numbers.par_iter().map(|&n| n * n).collect();

    // filter + map + collect
    let even_squares: Vec<i64> = numbers
        .par_iter()
        .filter(|&&n| n % 2 == 0)
        .map(|&n| n * n)
        .collect();
    println!("Even squares: {} items", even_squares.len());

    // any / all
    let has_negative = numbers.par_iter().any(|&n| n < 0);
    let all_positive = numbers.par_iter().all(|&n| n >= 0);
    println!("has_negative: {}, all_positive: {}", has_negative, all_positive);

    // find_any — returns Some(&T), but not necessarily the first match
    let found = numbers.par_iter().find_any(|&&n| n == 500_000);
    println!("Found: {:?}", found);

    // for_each — parallel side effects
    numbers.par_iter().for_each(|&n| {
        if n % 100_000 == 0 {
            println!("Processing: {}", n);
        }
    });

    // reduce
    let product: i64 = (1i64..=20)
        .into_par_iter()
        .reduce(|| 1, |a, b| a * b);
    println!("20! = {}", product);
}
```

**Important:** `find_any` vs `find_first`. `find_any` returns *any* matching element (whichever thread finds one first). `find_first` returns the *first* matching element in iteration order, which is slower because it can't short-circuit as aggressively.

## par_iter vs into_par_iter vs par_iter_mut

```rust
use rayon::prelude::*;

fn main() {
    let data = vec![1, 2, 3, 4, 5];

    // par_iter: borrows — &T
    let sum: i32 = data.par_iter().sum();

    // par_iter_mut: mutable borrows — &mut T
    let mut data = vec![1, 2, 3, 4, 5];
    data.par_iter_mut().for_each(|x| *x *= 2);
    println!("{:?}", data); // [2, 4, 6, 8, 10]

    // into_par_iter: takes ownership — T
    let data = vec![String::from("a"), String::from("b"), String::from("c")];
    let upper: Vec<String> = data.into_par_iter().map(|s| s.to_uppercase()).collect();
    // data is consumed, can't use it anymore
    println!("{:?}", upper);

    // Ranges work too
    let vals: Vec<i32> = (0..100).into_par_iter().map(|n| n * n).collect();
    println!("{} items", vals.len());
}
```

## Parallel Sorting

```rust
use rayon::prelude::*;

fn main() {
    let mut data: Vec<i32> = (0..10_000_000).rev().collect();

    // Parallel sort — significantly faster for large collections
    data.par_sort();

    // Parallel unstable sort — even faster, doesn't preserve order of equal elements
    data.par_sort_unstable();

    // With custom comparator
    data.par_sort_unstable_by(|a, b| b.cmp(a)); // descending

    println!("First 5: {:?}", &data[..5]);
}
```

Parallel sorting shines with millions of elements. For a few thousand, the overhead of parallelization isn't worth it.

## Custom Thread Pool

By default, Rayon uses a global thread pool. You can customize it or create your own:

```rust
use rayon::prelude::*;

fn main() {
    // Configure the global pool
    rayon::ThreadPoolBuilder::new()
        .num_threads(4)
        .thread_name(|idx| format!("rayon-worker-{}", idx))
        .build_global()
        .unwrap();

    let result: Vec<i32> = (0..100).into_par_iter().map(|n| n * 2).collect();
    println!("{:?}", &result[..10]);
}
```

Or create a separate pool for isolation:

```rust
use rayon::prelude::*;

fn main() {
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(2)
        .build()
        .unwrap();

    let result = pool.install(|| {
        (0..1000).into_par_iter().map(|n| n * n).sum::<i64>()
    });

    println!("Result: {}", result);
}
```

`pool.install()` runs the closure on the custom pool instead of the global one. Useful when you need different parallelism levels for different workloads.

## When Rayon Hurts

Rayon isn't free. The overhead of task splitting, scheduling, and work stealing matters when:

```rust
use rayon::prelude::*;
use std::time::Instant;

fn main() {
    let data: Vec<i32> = (0..100).collect();

    // Sequential — probably faster for tiny workloads
    let start = Instant::now();
    let _: Vec<i32> = data.iter().map(|&n| n + 1).collect();
    println!("Sequential: {:?}", start.elapsed());

    // Parallel — overhead exceeds benefit for trivial operations on small data
    let start = Instant::now();
    let _: Vec<i32> = data.par_iter().map(|&n| n + 1).collect();
    println!("Parallel: {:?}", start.elapsed());
}
```

Rules of thumb:
- **Fewer than ~1000 elements with cheap operations** — stick with sequential
- **Cheap operations on large data** — parallel helps but gains are modest
- **Expensive operations on any size data** — parallel almost always wins
- **IO-bound work** — Rayon is for CPU work. Use async for IO.

## Real Example: Image Processing

```rust
use rayon::prelude::*;

#[derive(Clone)]
struct Pixel {
    r: u8,
    g: u8,
    b: u8,
}

impl Pixel {
    fn grayscale(&self) -> Pixel {
        let gray = (0.299 * self.r as f64 + 0.587 * self.g as f64 + 0.114 * self.b as f64) as u8;
        Pixel { r: gray, g: gray, b: gray }
    }
}

fn main() {
    // Simulate a 4K image: 3840 x 2160 pixels
    let width = 3840;
    let height = 2160;
    let image: Vec<Pixel> = (0..width * height)
        .map(|i| Pixel {
            r: (i % 256) as u8,
            g: ((i / 256) % 256) as u8,
            b: ((i / 65536) % 256) as u8,
        })
        .collect();

    let start = std::time::Instant::now();
    let _gray: Vec<Pixel> = image.iter().map(|p| p.grayscale()).collect();
    println!("Sequential: {:?}", start.elapsed());

    let start = std::time::Instant::now();
    let _gray: Vec<Pixel> = image.par_iter().map(|p| p.grayscale()).collect();
    println!("Parallel: {:?}", start.elapsed());
}
```

For 8 million pixels, the parallel version typically runs 4-6x faster on 8 cores.

## Parallel Chunks

For row-based processing where you need the index:

```rust
use rayon::prelude::*;

fn main() {
    let width = 1920;
    let height = 1080;
    let mut pixels = vec![0u8; width * height * 3]; // RGB

    // Process each row in parallel
    pixels
        .par_chunks_mut(width * 3)
        .enumerate()
        .for_each(|(row, chunk)| {
            for x in 0..width {
                let idx = x * 3;
                chunk[idx] = (x % 256) as u8;         // R
                chunk[idx + 1] = (row % 256) as u8;    // G
                chunk[idx + 2] = 128;                   // B
            }
        });

    println!("Filled {} rows", height);
}
```

`par_chunks_mut` splits a slice into mutable chunks and processes them in parallel. Each chunk is exclusively owned by one thread — no races possible.

## Combining Rayon with Other Patterns

Rayon pairs well with channels for streaming results:

```rust
use rayon::prelude::*;
use std::sync::mpsc;

fn main() {
    let (tx, rx) = mpsc::channel();

    // Spawn a thread to consume results as they're produced
    let consumer = std::thread::spawn(move || {
        let mut count = 0;
        for result in rx {
            count += 1;
            if count % 1000 == 0 {
                println!("Consumed {} results, latest: {}", count, result);
            }
        }
        count
    });

    // Parallel processing, streaming results via channel
    let items: Vec<i32> = (0..10_000).collect();
    items.par_iter().for_each_with(tx, |tx, &item| {
        let result = item * item; // expensive computation
        tx.send(result).unwrap();
    });

    let total = consumer.join().unwrap();
    println!("Total processed: {}", total);
}
```

`for_each_with` clones the sender for each thread in the pool — exactly what you need for multi-producer patterns.

---

Next — Crossbeam, with scoped threads and lock-free data structures that go beyond what std provides.
