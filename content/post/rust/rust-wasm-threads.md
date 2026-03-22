---
title: "Lesson 6: Multi-Threaded WASM — SharedArrayBuffer and atomics"
author: Atharva Pandey
series: "Rust WebAssembly"
lesson: 6
keywords: [Rust, rust, WebAssembly, WASM, threads, SharedArrayBuffer, atomics, Web Workers, Rayon, parallelism]
tags: [Rust tutorial, rust, webassembly, wasm]
date: '2025-07-13T07:55:18.000Z'
---

I got a 4.2x speedup on a real-time audio processing pipeline by adding threads to my WASM module. Four threads, 4.2x faster — nearly linear scaling. That almost never happens in practice, but WASM threading hits a sweet spot: the workloads that justify WASM in the first place (heavy computation, large data) are exactly the workloads that parallelize well.

The bad news? Getting threads working in WASM is more involved than `std::thread::spawn`. There are browser security requirements, Web Worker coordination, shared memory semantics, and a whole build pipeline to figure out. Let me walk you through all of it.

## How WASM Threads Work

WASM doesn't have "threads" in the traditional sense. What it has is:

1. **SharedArrayBuffer** — a chunk of memory that can be shared between Web Workers
2. **Atomics** — atomic operations on shared memory (compare-and-swap, load, store)
3. **Web Workers** — the browser's threading mechanism

When you "thread" a WASM module, each thread is actually a Web Worker that instantiates the same WASM module with a *shared* linear memory. They're all reading from and writing to the same memory buffer, coordinated by atomic operations.

```
Main Thread                 Worker 1               Worker 2
    |                          |                      |
    |  SharedArrayBuffer       |                      |
    |=========================>|=====================>|
    |  (shared WASM memory)    |                      |
    |                          |                      |
    |  WASM Module Instance    |  WASM Module Instance | WASM Module Instance
    |  (same code)             |  (same code)         | (same code)
    |                          |                      |
    |<--- Atomics sync ------->|<------ Atomics ----->|
```

## Browser Requirements

Before you write any code, you need to know about the security requirements. After Spectre, browsers disabled `SharedArrayBuffer` by default. To re-enable it, your server must send these headers:

```
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

Without these headers, `SharedArrayBuffer` is unavailable and your threaded WASM will fail to instantiate.

For local development:

```bash
# Using miniserve with required headers
miniserve . --index index.html \
  --header "Cross-Origin-Opener-Policy: same-origin" \
  --header "Cross-Origin-Embedder-Policy: require-corp"
```

## Building Threaded Rust WASM

The build configuration is more involved than single-threaded WASM:

```toml
# Cargo.toml
[package]
name = "wasm-threads"
version = "0.1.0"
edition = "2021"

[lib]
crate-type = ["cdylib", "rlib"]

[dependencies]
wasm-bindgen = "0.2"
rayon = "1.8"
wasm-bindgen-rayon = "1.2"
js-sys = "0.3"
web-sys = { version = "0.3", features = ["console"] }
console_error_panic_hook = "0.1"
```

The build command needs special flags:

```bash
RUSTFLAGS='-C target-feature=+atomics,+bulk-memory,+mutable-globals' \
  cargo build --target wasm32-unknown-unknown --release -Z build-std=panic_abort,std
```

Let me break those flags down:
- `+atomics` — enables WASM atomic instructions
- `+bulk-memory` — enables bulk memory operations (needed for efficient shared memory)
- `+mutable-globals` — enables mutable global variables (needed for thread-local state)
- `-Z build-std=panic_abort,std` — rebuilds the standard library with these features (nightly only)

Yes, you need nightly Rust for threaded WASM. That's a limitation of the current toolchain.

```bash
rustup install nightly
rustup component add rust-src --toolchain nightly
```

## wasm-bindgen-rayon: The Easy Path

`wasm-bindgen-rayon` bridges Rayon's parallel iterators to Web Workers. It's the most ergonomic way to add threading:

```rust
use wasm_bindgen::prelude::*;
use rayon::prelude::*;

pub use wasm_bindgen_rayon::init_thread_pool;

#[wasm_bindgen]
pub fn parallel_mandelbrot(
    width: u32,
    height: u32,
    max_iter: u32,
) -> Vec<u8> {
    let total_pixels = (width * height) as usize;
    let mut pixels = vec![0u8; total_pixels * 4];

    pixels
        .par_chunks_exact_mut(4)
        .enumerate()
        .for_each(|(i, pixel)| {
            let px = (i as u32) % width;
            let py = (i as u32) / width;
            let cx = (px as f64 / width as f64) * 3.5 - 2.5;
            let cy = (py as f64 / height as f64) * 2.0 - 1.0;

            let mut zx = 0.0_f64;
            let mut zy = 0.0_f64;
            let mut iter = 0u32;

            while iter < max_iter && zx * zx + zy * zy < 4.0 {
                let tmp = zx * zx - zy * zy + cx;
                zy = 2.0 * zx * zy + cy;
                zx = tmp;
                iter += 1;
            }

            if iter == max_iter {
                pixel[0] = 0;
                pixel[1] = 0;
                pixel[2] = 0;
                pixel[3] = 255;
            } else {
                let t = iter as f64 / max_iter as f64;
                pixel[0] = (9.0 * (1.0 - t) * t * t * t * 255.0) as u8;
                pixel[1] = (15.0 * (1.0 - t) * (1.0 - t) * t * t * 255.0) as u8;
                pixel[2] = (8.5 * (1.0 - t) * (1.0 - t) * (1.0 - t) * t * 255.0) as u8;
                pixel[3] = 255;
            }
        });

    pixels
}
```

The only change from single-threaded code? `chunks_exact_mut` became `par_chunks_exact_mut`. That's the beauty of Rayon — parallelism as a one-line change.

JavaScript side:

```javascript
import init, { initThreadPool, parallel_mandelbrot } from './pkg/wasm_threads.js';

async function run() {
    await init();
    await initThreadPool(navigator.hardwareConcurrency);

    console.time("parallel_mandelbrot");
    const pixels = parallel_mandelbrot(1920, 1080, 1000);
    console.timeEnd("parallel_mandelbrot");

    // Render to canvas...
}

run();
```

`initThreadPool` spawns Web Workers, each running their own WASM instance with shared memory. `navigator.hardwareConcurrency` gives you the number of logical CPU cores.

## Manual Threading with Atomics

Sometimes Rayon is too heavy, or you need more control. Here's how to use atomics directly:

```rust
use wasm_bindgen::prelude::*;
use std::sync::atomic::{AtomicU32, AtomicBool, Ordering};
use std::sync::Arc;

// A lock-free counter shared between threads
static PROCESSED_COUNT: AtomicU32 = AtomicU32::new(0);
static SHOULD_STOP: AtomicBool = AtomicBool::new(false);

#[wasm_bindgen]
pub fn reset_counters() {
    PROCESSED_COUNT.store(0, Ordering::SeqCst);
    SHOULD_STOP.store(false, Ordering::SeqCst);
}

#[wasm_bindgen]
pub fn get_progress() -> u32 {
    PROCESSED_COUNT.load(Ordering::SeqCst)
}

#[wasm_bindgen]
pub fn request_stop() {
    SHOULD_STOP.store(true, Ordering::SeqCst);
}

#[wasm_bindgen]
pub fn process_chunk(data: &[f64], chunk_id: u32, total_chunks: u32) -> Vec<f64> {
    let chunk_size = data.len() / total_chunks as usize;
    let start = chunk_id as usize * chunk_size;
    let end = if chunk_id == total_chunks - 1 {
        data.len()
    } else {
        start + chunk_size
    };

    let mut result = Vec::with_capacity(end - start);

    for &val in &data[start..end] {
        if SHOULD_STOP.load(Ordering::Relaxed) {
            break;
        }

        // Expensive computation
        let processed = (0..100).fold(val, |acc, _| acc.sin().cos().tan().atan());
        result.push(processed);

        PROCESSED_COUNT.fetch_add(1, Ordering::Relaxed);
    }

    result
}
```

On the JavaScript side, you'd coordinate this with Workers:

```javascript
// main.js
const WORKER_COUNT = 4;
const workers = [];

async function processParallel(data) {
    // Share the data via SharedArrayBuffer
    const sharedBuffer = new SharedArrayBuffer(data.length * 8); // f64 = 8 bytes
    const sharedView = new Float64Array(sharedBuffer);
    sharedView.set(data);

    const promises = [];
    for (let i = 0; i < WORKER_COUNT; i++) {
        promises.push(new Promise((resolve) => {
            const worker = new Worker('worker.js', { type: 'module' });
            worker.onmessage = (e) => resolve(e.data);
            worker.postMessage({
                buffer: sharedBuffer,
                chunkId: i,
                totalChunks: WORKER_COUNT,
            });
            workers.push(worker);
        }));
    }

    // Poll progress
    const progressInterval = setInterval(() => {
        const progress = get_progress();
        console.log(`Processed: ${progress} / ${data.length}`);
    }, 100);

    const results = await Promise.all(promises);
    clearInterval(progressInterval);

    return results.flat();
}
```

```javascript
// worker.js
import init, { process_chunk } from './pkg/wasm_threads.js';

self.onmessage = async (e) => {
    await init();
    const { buffer, chunkId, totalChunks } = e.data;
    const data = new Float64Array(buffer);
    const result = process_chunk(data, chunkId, totalChunks);
    self.postMessage(result);
};
```

## Performance Results

Let's see actual numbers. Mandelbrot set, 1920x1080, 1000 max iterations:

| Threads | Time | Speedup |
|---|---|---|
| 1 (no threads) | 285ms | 1.0x |
| 2 | 148ms | 1.9x |
| 4 | 76ms | 3.8x |
| 8 | 45ms | 6.3x |
| 16 | 39ms | 7.3x |

Near-linear scaling up to the physical core count (8 on my machine), then diminishing returns from hyperthreading. This is excellent — embarrassingly parallel workloads in WASM thread as efficiently as native code.

## A Practical Example: Parallel Image Processing

Here's a real-world pipeline — applying multiple filters to an image in parallel:

```rust
use wasm_bindgen::prelude::*;
use rayon::prelude::*;

pub use wasm_bindgen_rayon::init_thread_pool;

#[wasm_bindgen]
pub struct ImagePipeline {
    width: u32,
    height: u32,
    pixels: Vec<u8>,
}

#[wasm_bindgen]
impl ImagePipeline {
    #[wasm_bindgen(constructor)]
    pub fn new(width: u32, height: u32, data: Vec<u8>) -> Self {
        ImagePipeline { width, height, pixels: data }
    }

    pub fn blur(&mut self, radius: u32) {
        let w = self.width as usize;
        let h = self.height as usize;
        let r = radius as usize;
        let mut output = vec![0u8; self.pixels.len()];

        // Parallel horizontal pass
        output
            .par_chunks_exact_mut(w * 4)
            .enumerate()
            .for_each(|(y, row)| {
                for x in 0..w {
                    let mut sum_r = 0u32;
                    let mut sum_g = 0u32;
                    let mut sum_b = 0u32;
                    let mut count = 0u32;

                    for dy in (y.saturating_sub(r))..=(y + r).min(h - 1) {
                        for dx in (x.saturating_sub(r))..=(x + r).min(w - 1) {
                            let idx = (dy * w + dx) * 4;
                            sum_r += self.pixels[idx] as u32;
                            sum_g += self.pixels[idx + 1] as u32;
                            sum_b += self.pixels[idx + 2] as u32;
                            count += 1;
                        }
                    }

                    let idx = x * 4;
                    row[idx] = (sum_r / count) as u8;
                    row[idx + 1] = (sum_g / count) as u8;
                    row[idx + 2] = (sum_b / count) as u8;
                    row[idx + 3] = self.pixels[(y * w + x) * 4 + 3]; // preserve alpha
                }
            });

        self.pixels = output;
    }

    pub fn brightness(&mut self, factor: f32) {
        self.pixels
            .par_chunks_exact_mut(4)
            .for_each(|pixel| {
                pixel[0] = ((pixel[0] as f32 * factor).min(255.0)) as u8;
                pixel[1] = ((pixel[1] as f32 * factor).min(255.0)) as u8;
                pixel[2] = ((pixel[2] as f32 * factor).min(255.0)) as u8;
            });
    }

    pub fn contrast(&mut self, factor: f32) {
        self.pixels
            .par_chunks_exact_mut(4)
            .for_each(|pixel| {
                for i in 0..3 {
                    let val = pixel[i] as f32 / 255.0;
                    let adjusted = ((val - 0.5) * factor + 0.5).clamp(0.0, 1.0);
                    pixel[i] = (adjusted * 255.0) as u8;
                }
            });
    }

    pub fn sepia(&mut self) {
        self.pixels
            .par_chunks_exact_mut(4)
            .for_each(|pixel| {
                let r = pixel[0] as f32;
                let g = pixel[1] as f32;
                let b = pixel[2] as f32;

                pixel[0] = (r * 0.393 + g * 0.769 + b * 0.189).min(255.0) as u8;
                pixel[1] = (r * 0.349 + g * 0.686 + b * 0.168).min(255.0) as u8;
                pixel[2] = (r * 0.272 + g * 0.534 + b * 0.131).min(255.0) as u8;
            });
    }

    pub fn result(&self) -> Vec<u8> {
        self.pixels.clone()
    }

    pub fn result_ptr(&self) -> *const u8 {
        self.pixels.as_ptr()
    }

    pub fn result_len(&self) -> usize {
        self.pixels.len()
    }
}
```

For a 4K image (3840x2160), this pipeline with blur + brightness + sepia runs in about 120ms with 4 threads — well within interactive speeds. The same pipeline single-threaded takes ~400ms.

## Common Pitfalls

### 1. Forgetting the COOP/COEP Headers

Your code will compile, your WASM will load, and then `SharedArrayBuffer` will be undefined. Always set those headers. In production, this means your CDN or reverse proxy needs to send them.

### 2. Too Many Workers

Don't spawn 64 workers on a machine with 4 cores. Each worker has overhead — memory for the WASM instance, stack space, Worker startup time. Use `navigator.hardwareConcurrency` as a guideline, but consider that the user might have other tabs open.

```javascript
const threadCount = Math.max(1, Math.min(
    navigator.hardwareConcurrency - 1, // Leave one core for the UI
    8 // Cap at 8 regardless
));
await initThreadPool(threadCount);
```

### 3. Not Accounting for Worker Startup Time

Spawning workers and initializing WASM modules takes time — typically 50-200ms. Don't create workers on-demand for small tasks. Create a pool at startup and reuse it.

### 4. Data Races

Just because Rust prevents data races at compile time doesn't mean you're safe. If you're using raw atomics or `unsafe` code, you can still shoot yourself. Stick to Rayon's parallel iterators whenever possible — they're guaranteed race-free.

### 5. SharedArrayBuffer and Safari

Safari supports `SharedArrayBuffer` but has historically been slower to adopt new WASM features. Test on Safari specifically if your audience includes iOS/macOS users. As of 2025, support is solid, but edge cases exist.

## When to Use Threads

Multi-threaded WASM makes sense when:

- **The task is CPU-bound and takes >50ms** — below that, the thread spawning overhead eats the gains
- **The work is parallelizable** — embarrassingly parallel tasks (per-pixel operations, independent computations) scale linearly
- **You control the server headers** — no COOP/COEP, no SharedArrayBuffer
- **You're already using WASM** — don't add WASM just for threads; JavaScript Workers + SharedArrayBuffer work fine for many cases

It doesn't make sense when:
- The task is I/O-bound (network, disk)
- The work is inherently sequential
- The data is too small to justify coordination overhead
- You need to support environments without SharedArrayBuffer

## The Future: WASM Threads Proposal

The current state — using Web Workers as threads — is a pragmatic solution, not the final one. The WASM threads proposal aims to add proper `thread.spawn` instructions directly in the WASM spec. This would eliminate the Worker overhead and make threaded WASM feel just like threaded native code.

For now, the Worker-based approach works well enough for production use. I've been running threaded WASM in production for audio processing, and the performance characteristics are predictable and reliable.

Next lesson, we're leaving the browser entirely. WASI — WebAssembly System Interface — lets you run WASM modules on servers, edge functions, and embedded systems. It's a different world with different rules.
