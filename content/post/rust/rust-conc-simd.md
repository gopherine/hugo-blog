---
title: "Lesson 22: SIMD — Explicit vectorization"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 22
keywords: [Rust, rust, concurrency, SIMD, vectorization, SSE, AVX, parallel computation, portable simd]
tags: [Rust tutorial, rust, concurrency]
date: '2024-12-17T08:20:00.000Z'
---

I had an image processing pipeline that took 4.2 seconds per frame on a single core. After rewriting the hot loop with SIMD intrinsics, it dropped to 0.9 seconds. Same core, same algorithm, 4.7x faster. SIMD doesn't add more cores — it makes each core do more work per clock cycle.

SIMD (Single Instruction, Multiple Data) is the other kind of parallelism. While threads run code on different cores, SIMD runs the same operation on multiple data elements simultaneously within a single core.

## What SIMD Actually Does

A normal CPU instruction operates on one value at a time:

```
add r1, r2  → one addition
```

A SIMD instruction operates on multiple values packed into a wide register:

```
vaddps ymm0, ymm1, ymm2  → eight float additions simultaneously
```

Modern CPUs have 128-bit (SSE), 256-bit (AVX2), or 512-bit (AVX-512) registers. A 256-bit register holds 8 × 32-bit floats or 4 × 64-bit doubles. One instruction processes all of them.

## Auto-Vectorization

The Rust compiler (via LLVM) can automatically vectorize some loops:

```rust
fn sum_scalar(data: &[f32]) -> f32 {
    data.iter().sum()
}

fn add_arrays(a: &[f32], b: &[f32], result: &mut [f32]) {
    for i in 0..a.len().min(b.len()).min(result.len()) {
        result[i] = a[i] + b[i];
    }
}
```

Compile with `RUSTFLAGS="-C target-cpu=native"` and the compiler might vectorize these loops. But "might" is the operative word. Auto-vectorization is fragile — a slight code change can prevent it.

To check if your code was vectorized:

```bash
RUSTFLAGS="-C target-cpu=native" cargo rustc --release -- --emit asm
# Look for vaddps, vmulps, etc. in the assembly
```

## Explicit SIMD with std::arch

For guaranteed SIMD, use architecture-specific intrinsics:

```rust
#[cfg(target_arch = "x86_64")]
use std::arch::x86_64::*;

#[cfg(target_arch = "x86_64")]
unsafe fn dot_product_avx2(a: &[f32], b: &[f32]) -> f32 {
    assert_eq!(a.len(), b.len());
    assert!(a.len() % 8 == 0, "Length must be multiple of 8 for AVX2");

    let mut sum = _mm256_setzero_ps(); // 8 zeros

    for i in (0..a.len()).step_by(8) {
        let va = _mm256_loadu_ps(a.as_ptr().add(i));
        let vb = _mm256_loadu_ps(b.as_ptr().add(i));
        let product = _mm256_mul_ps(va, vb);
        sum = _mm256_add_ps(sum, product);
    }

    // Horizontal sum of 8 floats
    let high = _mm256_extractf128_ps(sum, 1);
    let low = _mm256_castps256_ps128(sum);
    let sum128 = _mm_add_ps(high, low);
    let high64 = _mm_movehl_ps(sum128, sum128);
    let sum64 = _mm_add_ps(sum128, high64);
    let high32 = _mm_shuffle_ps(sum64, sum64, 0x1);
    let sum32 = _mm_add_ss(sum64, high32);
    _mm_cvtss_f32(sum32)
}

fn main() {
    #[cfg(target_arch = "x86_64")]
    {
        if is_x86_feature_detected!("avx2") {
            let a: Vec<f32> = (0..1024).map(|i| i as f32).collect();
            let b: Vec<f32> = (0..1024).map(|i| (i * 2) as f32).collect();

            let result = unsafe { dot_product_avx2(&a, &b) };
            println!("Dot product: {}", result);

            // Verify against scalar
            let scalar: f32 = a.iter().zip(b.iter()).map(|(a, b)| a * b).sum();
            println!("Scalar result: {}", scalar);
        } else {
            println!("AVX2 not supported on this CPU");
        }
    }
}
```

This is the raw approach. It's fast, but:
- It's `unsafe`
- It's architecture-specific (x86 only)
- It's verbose and hard to read
- You need to handle the "tail" (remaining elements not fitting in a SIMD register)

## Runtime Feature Detection

Always check for CPU support at runtime:

```rust
#[cfg(target_arch = "x86_64")]
fn process_data(data: &mut [f32]) {
    if is_x86_feature_detected!("avx2") {
        unsafe { process_avx2(data) }
    } else if is_x86_feature_detected!("sse4.1") {
        unsafe { process_sse41(data) }
    } else {
        process_scalar(data)
    }
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "avx2")]
unsafe fn process_avx2(data: &mut [f32]) {
    // AVX2 implementation
    for chunk in data.chunks_exact_mut(8) {
        let v = _mm256_loadu_ps(chunk.as_ptr());
        let doubled = _mm256_add_ps(v, v);
        _mm256_storeu_ps(chunk.as_mut_ptr(), doubled);
    }
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "sse4.1")]
unsafe fn process_sse41(data: &mut [f32]) {
    use std::arch::x86_64::*;
    for chunk in data.chunks_exact_mut(4) {
        let v = _mm_loadu_ps(chunk.as_ptr());
        let doubled = _mm_add_ps(v, v);
        _mm_storeu_ps(chunk.as_mut_ptr(), doubled);
    }
}

fn process_scalar(data: &mut [f32]) {
    for x in data.iter_mut() {
        *x *= 2.0;
    }
}
```

The `#[target_feature(enable = "avx2")]` attribute tells the compiler to emit AVX2 instructions for that specific function — even if the overall compilation target doesn't include AVX2.

## Portable SIMD (Nightly)

Rust's `std::simd` (nightly only, as of late 2024) provides a portable abstraction:

```rust
#![feature(portable_simd)]
use std::simd::prelude::*;

fn sum_simd(data: &[f32]) -> f32 {
    let (chunks, remainder) = data.as_chunks::<8>();

    let mut sum = f32x8::splat(0.0);
    for chunk in chunks {
        sum += f32x8::from_array(*chunk);
    }

    let mut total = sum.reduce_sum();
    for &val in remainder {
        total += val;
    }
    total
}

fn dot_product_simd(a: &[f32], b: &[f32]) -> f32 {
    let (a_chunks, a_remainder) = a.as_chunks::<8>();
    let (b_chunks, _) = b.as_chunks::<8>();

    let mut sum = f32x8::splat(0.0);
    for (ac, bc) in a_chunks.iter().zip(b_chunks.iter()) {
        let va = f32x8::from_array(*ac);
        let vb = f32x8::from_array(*bc);
        sum += va * vb;
    }

    let mut total = sum.reduce_sum();
    for i in 0..a_remainder.len() {
        let a_idx = a_chunks.len() * 8 + i;
        if a_idx < b.len() {
            total += a[a_idx] * b[a_idx];
        }
    }
    total
}
```

This compiles to SSE, AVX, NEON, or whatever the target supports — one code path, portable across architectures. Much cleaner than raw intrinsics.

## SIMD + Rayon: Multi-Core Vectorized

The real power play — SIMD within each core, Rayon across cores:

```rust
use rayon::prelude::*;

fn process_chunk(chunk: &mut [f32]) {
    // SIMD within the chunk (simplified — use actual SIMD in production)
    for x in chunk.iter_mut() {
        *x = (*x * *x + *x).sqrt();
    }
}

fn main() {
    let mut data: Vec<f32> = (0..10_000_000).map(|i| i as f32 * 0.001).collect();

    // Rayon splits across cores, each core processes its chunk with SIMD
    data.par_chunks_mut(65536)
        .for_each(|chunk| process_chunk(chunk));

    println!("Processed {} elements", data.len());
}
```

With 8 cores and 8-wide AVX2, you get up to 64x throughput improvement over scalar single-threaded code for perfectly parallelizable float math.

## Practical Example: Fast String Search

SIMD is excellent for byte-level operations:

```rust
#[cfg(target_arch = "x86_64")]
use std::arch::x86_64::*;

/// Count occurrences of a byte in a buffer using SIMD
#[cfg(target_arch = "x86_64")]
unsafe fn count_byte_avx2(data: &[u8], target: u8) -> usize {
    if !is_x86_feature_detected!("avx2") {
        return data.iter().filter(|&&b| b == target).count();
    }

    let target_vec = _mm256_set1_epi8(target as i8);
    let mut total = 0usize;

    let chunks = data.len() / 32;
    for i in 0..chunks {
        let chunk = _mm256_loadu_si256(data.as_ptr().add(i * 32) as *const __m256i);
        let cmp = _mm256_cmpeq_epi8(chunk, target_vec);
        let mask = _mm256_movemask_epi8(cmp) as u32;
        total += mask.count_ones() as usize;
    }

    // Handle remainder
    for &byte in &data[chunks * 32..] {
        if byte == target {
            total += 1;
        }
    }

    total
}

fn main() {
    let data = b"Hello, World! Hello, Rust! Hello, SIMD!";

    #[cfg(target_arch = "x86_64")]
    {
        let count = unsafe { count_byte_avx2(data, b'l') };
        println!("'l' appears {} times", count);

        // Verify
        let scalar_count = data.iter().filter(|&&b| b == b'l').count();
        assert_eq!(count, scalar_count);
    }
}
```

Processing 32 bytes per instruction instead of one — that's a significant speedup for large buffers. Libraries like `memchr` use this internally.

## When SIMD Helps (and When It Doesn't)

**SIMD wins:**
- Array/vector math (dot products, matrix multiplication)
- Image processing (per-pixel operations)
- Audio processing (sample-level DSP)
- String/byte searching (counting, finding patterns)
- Compression/decompression
- Parsing (validating UTF-8, JSON scanning)

**SIMD doesn't help:**
- Branchy code (if/else per element kills SIMD efficiency)
- Pointer-chasing (linked lists, tree traversal)
- Small data sets (setup overhead exceeds benefit)
- Complex operations that don't map to SIMD instructions

The key requirement: your operation must be *uniform* — the same thing applied to every element. If each element needs different logic, SIMD can't help.

## Crates for SIMD

Instead of writing raw intrinsics, consider:

- **`packed_simd2`** — Portable SIMD on stable Rust
- **`wide`** — Safe SIMD types
- **`simdeez`** — Write once, run on SSE/AVX/AVX-512
- **`pulp`** — SIMD with runtime dispatch

For most applications, using a library that already uses SIMD internally (like `serde_json` for parsing, `image` for image processing, or `nalgebra` for linear algebra) gives you the benefit without writing SIMD yourself.

---

Next — GPU computing from Rust with wgpu and compute shaders.
