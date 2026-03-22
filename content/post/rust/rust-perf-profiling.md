---
title: "Lesson 3: Profiling — perf, flamegraph, samply"
author: Atharva Pandey
series: "Rust Performance Engineering"
lesson: 3
keywords: [Rust, rust, performance, profiling, perf, flamegraph, samply, cargo-flamegraph]
tags: [Rust tutorial, rust, performance]
date: '2025-03-19T10:45:00.000Z'
---

A colleague once asked me to look at a Rust service that was "slow." They'd already spent a week trying to optimize the JSON parsing layer because "parsing is always the bottleneck." I ran a profiler. Sixty-three percent of CPU time was spent in `Drop` implementations, deallocating thousands of small strings that were created and immediately discarded. The JSON parsing was 4% of runtime.

Profiling would've found that in five minutes. That's why this lesson exists.

## Benchmarking vs Profiling

Quick distinction, because people confuse these:

- **Benchmarking** tells you *how fast* something is. "This function takes 450µs."
- **Profiling** tells you *where time is spent*. "73% of that 450µs is in `HashMap::insert`."

Benchmarking without profiling is like knowing your car is slow but not knowing whether it's the engine, the brakes, or the tires dragging. You need both.

## Profiling on Linux: perf

`perf` is the king of profilers on Linux. It's a sampling profiler — it periodically interrupts your program and records which function is executing. Run it enough times and you get a statistical picture of where CPU time goes.

### Setup

```bash
# Install perf (Ubuntu/Debian)
sudo apt install linux-tools-common linux-tools-$(uname -r)

# Allow perf for non-root users
echo 0 | sudo tee /proc/sys/kernel/perf_event_paranoid
```

### Basic Usage

First, compile with debug symbols in release mode:

```toml
# Cargo.toml
[profile.release]
debug = true  # Include debug info for profiling
```

Then:

```bash
# Build in release mode
cargo build --release

# Profile with perf
perf record -g --call-graph dwarf ./target/release/my_program

# View the report
perf report
```

`perf report` gives you an interactive TUI showing which functions consumed the most CPU time. Navigate with arrow keys, press Enter to drill into a function's callers and callees.

Sample output:

```
Overhead  Command      Shared Object        Symbol
  34.21%  my_program   my_program           [.] my_crate::parser::parse_record
  18.44%  my_program   my_program           [.] alloc::vec::Vec<T>::push
  12.87%  my_program   libc.so.6            [.] __memmove_avx_unaligned_erms
   8.33%  my_program   my_program           [.] core::str::converts::from_utf8
   6.12%  my_program   my_program           [.] std::collections::hash::map::HashMap<K,V>::insert
```

This tells you immediately: 34% is parsing, 18% is `Vec::push` (probably reallocations), 13% is memory copies. Now you know where to look.

### Useful perf Commands

```bash
# Record for a specific duration
perf record -g --call-graph dwarf -p $(pidof my_program) -- sleep 10

# Count specific hardware events
perf stat -e cache-misses,cache-references,instructions,cycles \
    ./target/release/my_program

# Sample output:
#   1,234,567  cache-misses    # 3.45% of all cache refs
#  35,789,012  cache-references
# 892,345,678  instructions    # 1.82 insn per cycle
# 490,123,456  cycles
```

That `perf stat` output is gold. It tells you your cache miss rate and instructions-per-cycle (IPC). An IPC below 1.0 usually means you're stalled waiting for memory. Above 2.0 means you're compute-bound. Between 1.0 and 2.0 is the typical range for most code.

## Flamegraphs

Flamegraphs take profiling data and turn it into a visual representation. Each horizontal bar is a function, the width is proportional to CPU time, and the stack grows upward. Wide bars at the top = that function is hot. Wide bars at the bottom = that function calls a lot of hot code.

### cargo-flamegraph

The easiest way to get flamegraphs in Rust:

```bash
# Install
cargo install flamegraph

# Generate (Linux — uses perf under the hood)
cargo flamegraph --release -- --your-program-args

# Generate for benchmarks
cargo flamegraph --bench my_benchmarks -- --bench "specific_bench_name"
```

This produces an SVG file you can open in a browser. The SVG is interactive — click on a stack frame to zoom in.

### Reading a Flamegraph

Here's how I read them:

1. **Start at the top.** The widest bars at the top of the graph are where your program actually spends CPU time. These are your hot functions.

2. **Look for plateaus.** A wide, flat section means a function is doing a lot of work itself (not just calling other functions). These are optimization targets.

3. **Look for unexpected entries.** If you see `malloc` or `free` taking 20% of your flamegraph, you have an allocation problem. If you see `memcpy` everywhere, you might be cloning too much.

4. **Look for deep stacks.** Very deep call stacks can indicate recursion or over-layered abstractions that prevent inlining.

### Differential Flamegraphs

This is the real superpower. You can compare two flamegraphs to see what changed:

```bash
# Record baseline
perf record -g --call-graph dwarf -o perf_baseline.data \
    ./target/release/my_program_v1

# Record optimized version
perf record -g --call-graph dwarf -o perf_optimized.data \
    ./target/release/my_program_v2

# Generate differential flamegraph
# (requires inferno or flamegraph tools)
perf script -i perf_baseline.data | inferno-collapse-perf > baseline.folded
perf script -i perf_optimized.data | inferno-collapse-perf > optimized.folded
inferno-diff-folded baseline.folded optimized.folded | inferno-flamegraph > diff.svg
```

Red sections got slower. Blue sections got faster. This immediately shows you the impact of your changes.

## samply — The macOS Solution

If you're on macOS, `perf` isn't available. `samply` fills that gap beautifully — it's a sampling profiler that exports to the Firefox Profiler UI.

```bash
# Install
cargo install samply

# Profile
samply record ./target/release/my_program

# This opens the Firefox Profiler in your browser automatically
```

The Firefox Profiler UI is *excellent*. You get flamegraphs, call trees, timeline views, and you can filter by thread. I actually prefer it to `perf report` for interactive exploration.

```bash
# Profile a cargo benchmark
samply record cargo bench --bench my_benchmarks

# Profile with specific arguments
samply record ./target/release/my_server --port 8080
```

### samply Tips

- Build with `debug = true` in your release profile for function names
- Use `RUSTFLAGS="-C force-frame-pointers=yes"` for better stack traces on macOS
- The Firefox Profiler UI lets you share profiles as links — great for team discussions

```bash
# Best quality stack traces on macOS
RUSTFLAGS="-C force-frame-pointers=yes" cargo build --release
samply record ./target/release/my_program
```

## Instruments (macOS Alternative)

If you're in the Apple ecosystem, Xcode's Instruments is another option:

```bash
# Build with debug symbols
cargo build --release

# Open in Instruments (Time Profiler template)
open -a Instruments ./target/release/my_program
```

I don't use Instruments much for Rust — the UI is geared toward Swift/ObjC and the Rust symbol demangling is hit-or-miss. samply is almost always better for Rust specifically.

## Memory Profiling with DHAT

CPU profiling tells you where time goes. Memory profiling tells you where allocations happen. DHAT (Dynamic Heap Analysis Tool) is built into Rust's allocator infrastructure:

```toml
# Cargo.toml
[dependencies]
dhat = "0.3"

[profile.release]
debug = true
```

```rust
#[cfg(feature = "dhat-heap")]
#[global_allocator]
static ALLOC: dhat::Alloc = dhat::Alloc;

fn main() {
    #[cfg(feature = "dhat-heap")]
    let _profiler = dhat::Profiler::new_heap();

    // Your program here
    let mut data = Vec::new();
    for i in 0..100_000 {
        data.push(format!("item_{}", i)); // 100K allocations!
    }

    // Drop _profiler prints stats and generates dhat-heap.json
}
```

Run with `cargo run --release --features dhat-heap`, then open the output in the [DHAT viewer](https://nnethercote.github.io/dh_view/dh_view.html).

DHAT tells you:
- Total bytes allocated
- Number of allocations
- Where each allocation happened (full stack trace)
- How long allocations lived
- How much of the allocated memory was actually used

That last one is surprisingly useful. If you allocate a 1MB buffer but only write 100 bytes, DHAT will catch that.

## A Real Profiling Session

Let me walk through an actual profiling session. Here's a program that processes log lines:

```rust
use std::collections::HashMap;
use std::io::{self, BufRead};

fn process_logs(input: &str) -> HashMap<String, usize> {
    let mut counts: HashMap<String, usize> = HashMap::new();

    for line in input.lines() {
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() >= 3 {
            let level = parts[2].to_string();
            *counts.entry(level).or_insert(0) += 1;
        }
    }

    counts
}
```

After profiling, the flamegraph shows:
- 35% in `to_string()` — we're allocating a new String for every log line
- 22% in `HashMap::entry` — the entry API is doing a hash + lookup + potential insert
- 15% in `split_whitespace().collect::<Vec>()` — allocating a Vec per line

The optimized version:

```rust
use std::collections::HashMap;

fn process_logs_optimized(input: &str) -> HashMap<&str, usize> {
    let mut counts: HashMap<&str, usize> = HashMap::new();

    for line in input.lines() {
        // Skip collecting into Vec — just advance the iterator
        if let Some(level) = line.split_whitespace().nth(2) {
            *counts.entry(level).or_insert(0) += 1;
        }
    }

    counts
}
```

Changes: borrow instead of clone (`&str` instead of `String`), skip the `Vec` allocation by using `nth(2)` directly. Re-profiling shows a 3x improvement — the hot spots are now `split_whitespace` (which is the actual work) and `HashMap` lookups (unavoidable).

## Profiling Checklist

Here's what I do every time:

1. **Build with release + debug symbols.** `[profile.release] debug = true` in Cargo.toml.

2. **Enable frame pointers if needed.** `RUSTFLAGS="-C force-frame-pointers=yes"` gives cleaner stack traces at minimal cost (~1-2% overhead).

3. **Profile the real workload.** Don't profile with toy inputs. Use production-sized data.

4. **Look at the flamegraph first.** It gives you the big picture faster than any other tool.

5. **Drill into specifics with perf report or the Firefox Profiler.** Once you know the hot function, look at its callers and callees.

6. **Profile allocations separately.** CPU profiling won't show you allocation overhead clearly. Use DHAT or `perf record -e malloc` for that.

7. **Profile again after optimizing.** Verify the hot spot moved. Sometimes fixing one bottleneck reveals the next one.

## The Takeaway

Profiling is the most important skill in performance engineering. It takes five minutes and saves days of misguided optimization. Every profiler I've shown you is free. There's no excuse for guessing.

Use `perf` + flamegraphs on Linux. Use `samply` on macOS. Use DHAT for allocation analysis. Profile first, then benchmark your fix, then profile again.

Next up: we'll start applying what we find. Lesson 4 dives into the most common profiling result — too many allocations — and what to do about it.
