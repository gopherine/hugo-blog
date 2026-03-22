---
title: "Lesson 8: std::time — Duration, Instant, SystemTime"
author: Atharva Pandey
series: "Rust Standard Library Mastery"
lesson: 8
keywords: [Rust, rust, standard library, time, Duration, Instant, SystemTime, benchmarking, timing]
tags: [Rust tutorial, rust, stdlib]
date: '2024-10-01T07:50:00.000Z'
---

A few months ago I tracked down a bug where a cache TTL check was wrong because someone compared `SystemTime` values that had been serialized and deserialized across a system clock adjustment. The cached timestamps jumped backward, and suddenly entries that should've expired were "fresh" again. That's the kind of lesson that teaches you the difference between monotonic clocks and wall clocks.

## Two Clocks, Two Types

Rust gives you two time types because computers have two fundamentally different clocks:

- **`Instant`** — monotonic clock. Always moves forward. Perfect for measuring elapsed time. Cannot be converted to a date or time of day. Not affected by NTP adjustments or daylight saving time.

- **`SystemTime`** — wall clock. Represents a point in civil time. Can go backward (NTP corrections, manual clock changes). Use it when you need timestamps that are meaningful to humans.

This distinction exists in every operating system, but most languages paper over it. Rust makes you choose, which means you can't accidentally use the wrong one.

## Duration — The Measurement Unit

`Duration` represents a span of time. Both `Instant` and `SystemTime` use `Duration` for their arithmetic.

```rust
use std::time::Duration;

fn main() {
    // Creating durations
    let five_secs = Duration::from_secs(5);
    let half_sec = Duration::from_millis(500);
    let tiny = Duration::from_micros(100);
    let tinier = Duration::from_nanos(500);

    // Fractional seconds
    let precise = Duration::from_secs_f64(2.5);
    println!("2.5 seconds = {}ms", precise.as_millis());

    // Accessing components
    println!("5 secs: {} seconds, {} nanos", five_secs.as_secs(), five_secs.subsec_nanos());
    println!("As millis: {}", five_secs.as_millis());
    println!("As micros: {}", five_secs.as_micros());
    println!("As f64: {}", five_secs.as_secs_f64());

    // Arithmetic
    let total = five_secs + half_sec;
    println!("5s + 500ms = {}ms", total.as_millis());

    let doubled = five_secs * 2;
    println!("5s * 2 = {}s", doubled.as_secs());

    let halved = five_secs / 2;
    println!("5s / 2 = {}ms", halved.as_millis());

    // checked_add for overflow-safe arithmetic
    let big = Duration::from_secs(u64::MAX - 10);
    match big.checked_add(Duration::from_secs(20)) {
        Some(result) => println!("Result: {result:?}"),
        None => println!("Would overflow!"),
    }

    // Zero duration
    let zero = Duration::ZERO;
    println!("Is zero: {}", zero.is_zero());

    // Maximum duration
    let max = Duration::MAX;
    println!("Max duration: {} seconds", max.as_secs());
}
```

## Instant — Measuring Elapsed Time

`Instant` is what you want 90% of the time. Benchmarking, timeouts, rate limiting, cooldowns — anything where you need to know how much time has passed.

```rust
use std::time::Instant;
use std::thread;

fn main() {
    // Basic timing
    let start = Instant::now();

    // Do some work
    let mut sum: u64 = 0;
    for i in 0..10_000_000 {
        sum += i;
    }

    let elapsed = start.elapsed();
    println!("Computation took: {elapsed:?}");
    println!("Result: {sum}");

    // elapsed() is sugar for Instant::now() - start
    let start = Instant::now();
    thread::sleep(std::time::Duration::from_millis(100));
    let end = Instant::now();
    println!("Sleep took: {:?}", end - start);
    println!("Same thing: {:?}", start.elapsed());
}
```

### Practical: Function Timer

```rust
use std::time::Instant;

fn timed<F, R>(name: &str, f: F) -> R
where
    F: FnOnce() -> R,
{
    let start = Instant::now();
    let result = f();
    let elapsed = start.elapsed();
    println!("[{name}] {elapsed:?}");
    result
}

fn fibonacci(n: u64) -> u64 {
    if n <= 1 { return n; }
    fibonacci(n - 1) + fibonacci(n - 2)
}

fn main() {
    let result = timed("fibonacci(30)", || fibonacci(30));
    println!("fib(30) = {result}");

    let result = timed("fibonacci(35)", || fibonacci(35));
    println!("fib(35) = {result}");

    // Sorting comparison
    let mut data: Vec<i32> = (0..100_000).rev().collect();
    timed("sort 100k items", || data.sort());

    let mut data: Vec<i32> = (0..100_000).rev().collect();
    timed("sort_unstable 100k items", || data.sort_unstable());
}
```

### Rate Limiter with Instant

```rust
use std::time::{Duration, Instant};

struct RateLimiter {
    max_per_second: u32,
    window_start: Instant,
    count: u32,
}

impl RateLimiter {
    fn new(max_per_second: u32) -> Self {
        RateLimiter {
            max_per_second,
            window_start: Instant::now(),
            count: 0,
        }
    }

    fn allow(&mut self) -> bool {
        let now = Instant::now();

        // Reset window if a second has passed
        if now.duration_since(self.window_start) >= Duration::from_secs(1) {
            self.window_start = now;
            self.count = 0;
        }

        if self.count < self.max_per_second {
            self.count += 1;
            true
        } else {
            false
        }
    }
}

fn main() {
    let mut limiter = RateLimiter::new(5);

    for i in 0..10 {
        if limiter.allow() {
            println!("Request {i}: allowed");
        } else {
            println!("Request {i}: RATE LIMITED");
        }
    }
}
```

### Deadline Pattern

```rust
use std::time::{Duration, Instant};

fn process_with_deadline(items: &[String], timeout: Duration) -> usize {
    let deadline = Instant::now() + timeout;
    let mut processed = 0;

    for item in items {
        if Instant::now() >= deadline {
            println!("Deadline reached after processing {processed} items");
            break;
        }

        // Simulate work
        std::thread::sleep(Duration::from_millis(10));
        processed += 1;
    }

    processed
}

fn main() {
    let items: Vec<String> = (0..1000).map(|i| format!("item-{i}")).collect();

    let count = process_with_deadline(&items, Duration::from_millis(50));
    println!("Processed {count} out of {} items", items.len());
}
```

## SystemTime — Wall Clock Timestamps

`SystemTime` represents a point on the civil clock. Use it when you need to record when something happened in terms humans understand.

```rust
use std::time::{SystemTime, UNIX_EPOCH, Duration};

fn main() {
    let now = SystemTime::now();
    println!("System time: {now:?}");

    // Duration since Unix epoch
    match now.duration_since(UNIX_EPOCH) {
        Ok(duration) => {
            println!("Unix timestamp: {}", duration.as_secs());
            println!("Unix timestamp (ms): {}", duration.as_millis());
        }
        Err(e) => {
            // This happens if the clock is set before 1970 somehow
            println!("Clock error: {e}");
        }
    }

    // Create a SystemTime from a Unix timestamp
    let timestamp = 1700000000u64; // Nov 14, 2023
    let time = UNIX_EPOCH + Duration::from_secs(timestamp);
    println!("\nTimestamp {timestamp} = {time:?}");

    // Time comparison
    let earlier = UNIX_EPOCH + Duration::from_secs(1_000_000_000);
    let later = UNIX_EPOCH + Duration::from_secs(1_700_000_000);

    match later.duration_since(earlier) {
        Ok(diff) => println!("Difference: {} days", diff.as_secs() / 86400),
        Err(_) => println!("Clock went backward!"),
    }
}
```

### Why duration_since Returns Result

Here's the important part — `SystemTime::duration_since()` returns `Result`, not just `Duration`. That's because the wall clock can go backward:

```rust
use std::time::{SystemTime, Duration, UNIX_EPOCH};

fn main() {
    let time_a = UNIX_EPOCH + Duration::from_secs(1000);
    let time_b = UNIX_EPOCH + Duration::from_secs(500);

    // This fails because time_a is after time_b
    match time_b.duration_since(time_a) {
        Ok(d) => println!("Duration: {d:?}"),
        Err(e) => {
            println!("System time went backward by {:?}", e.duration());
        }
    }

    // elapsed() can also "fail" — returns Ok(0) in that case
    // In practice, the clock rarely jumps backward, but NTP corrections
    // and VM snapshots can cause it
    let now = SystemTime::now();
    match now.elapsed() {
        Ok(d) => println!("Elapsed since 'now': {d:?}"),
        Err(e) => println!("Clock jumped backward: {e}"),
    }
}
```

This is why `Instant` exists — it uses a monotonic clock that *guarantees* time never goes backward. For measuring elapsed time, always use `Instant`. For timestamps in logs, databases, or APIs, use `SystemTime`.

## File Timestamps

`SystemTime` shows up in file metadata:

```rust
use std::fs;
use std::io;
use std::time::{SystemTime, UNIX_EPOCH};

fn format_system_time(t: SystemTime) -> String {
    match t.duration_since(UNIX_EPOCH) {
        Ok(d) => {
            let secs = d.as_secs();
            // Very rough formatting — use chrono for real code
            let days_since_epoch = secs / 86400;
            let time_of_day = secs % 86400;
            let hours = time_of_day / 3600;
            let minutes = (time_of_day % 3600) / 60;
            format!("Day {days_since_epoch} {hours:02}:{minutes:02} UTC")
        }
        Err(_) => "before epoch".to_string(),
    }
}

fn main() -> io::Result<()> {
    let metadata = fs::metadata("Cargo.toml")?;

    if let Ok(modified) = metadata.modified() {
        println!("Modified: {}", format_system_time(modified));
    }

    if let Ok(created) = metadata.created() {
        println!("Created:  {}", format_system_time(created));
    }

    if let Ok(accessed) = metadata.accessed() {
        println!("Accessed: {}", format_system_time(accessed));
    }

    Ok(())
}
```

For real date/time formatting, you want the `chrono` or `time` crate. The standard library deliberately doesn't include calendar types — dates are surprisingly complex (time zones, leap seconds, daylight saving), and Rust's philosophy is to keep the stdlib small.

## Benchmarking Pattern

A simple but effective micro-benchmark:

```rust
use std::time::{Duration, Instant};

fn bench<F: FnMut()>(name: &str, iterations: u32, mut f: F) {
    // Warmup
    for _ in 0..iterations / 10 {
        f();
    }

    let start = Instant::now();
    for _ in 0..iterations {
        f();
    }
    let total = start.elapsed();
    let per_op = total / iterations;

    println!(
        "{name}: {total:?} total, {per_op:?} per op ({iterations} iterations)"
    );
}

fn main() {
    let data: Vec<i32> = (0..10_000).collect();

    bench("Vec::contains", 1000, || {
        let _ = data.contains(&9999);
    });

    bench("binary_search", 1000, || {
        let _ = data.binary_search(&9999);
    });

    bench("iterator find", 1000, || {
        let _ = data.iter().find(|&&x| x == 9999);
    });

    // String allocation
    bench("String::from", 10_000, || {
        let _ = String::from("hello world this is a test string");
    });

    bench("&str (no alloc)", 10_000, || {
        let _: &str = "hello world this is a test string";
    });
}
```

For serious benchmarking, use `criterion` — it handles warm-up, statistical analysis, and noise reduction. But this pattern is great for quick sanity checks during development.

## Timeout Wrapper

```rust
use std::time::{Duration, Instant};
use std::thread;
use std::sync::mpsc;

fn with_timeout<F, T>(timeout: Duration, f: F) -> Option<T>
where
    F: FnOnce() -> T + Send + 'static,
    T: Send + 'static,
{
    let (tx, rx) = mpsc::channel();

    thread::spawn(move || {
        let result = f();
        let _ = tx.send(result);
    });

    match rx.recv_timeout(timeout) {
        Ok(result) => Some(result),
        Err(_) => None,
    }
}

fn main() {
    // This completes in time
    let result = with_timeout(Duration::from_secs(2), || {
        thread::sleep(Duration::from_millis(100));
        42
    });
    println!("Fast task: {result:?}"); // Some(42)

    // This times out
    let result = with_timeout(Duration::from_millis(50), || {
        thread::sleep(Duration::from_secs(5));
        42
    });
    println!("Slow task: {result:?}"); // None
}
```

## The Mental Model

Here's how I think about it:

- **Need to measure how long something takes?** Use `Instant`.
- **Need to implement a timeout or deadline?** Use `Instant`.
- **Need to record when something happened?** Use `SystemTime`.
- **Need to compare timestamps across processes or machines?** Use `SystemTime` (with awareness that clocks can drift).
- **Need to do math with time spans?** Use `Duration`.

The standard library gives you the primitives. For anything involving calendars, time zones, or human-readable formatting, reach for a crate. That split is intentional — the hard part of time isn't measuring it, it's representing it for humans.
