---
title: "Lesson 9: Inlining — #[inline] and LTO"
author: Atharva Pandey
series: "Rust Performance Engineering"
lesson: 9
keywords: [Rust, rust, performance, inlining, inline, LTO, link-time optimization, codegen units]
tags: [Rust tutorial, rust, performance]
date: '2025-03-31T19:05:00.000Z'
---

A few years ago I was profiling a JSON parser and noticed something weird. A tiny function — four lines, no allocations — was showing up as a 15% hot spot. Not because it was slow, but because it was called 8 million times per second and the function call overhead (push registers, set up stack frame, call, pop registers, return) was eating 2 nanoseconds each call. That's 16 milliseconds per second just in function prologues and epilogues.

Added `#[inline]` and the function vanished from the profile. Zero overhead. The computation was still happening, but it was folded directly into the caller's code. No function call, no stack frame, no overhead.

But here's the thing — you should almost never need to do this manually. Let me explain why, and when you actually should.

## What Inlining Does

When the compiler inlines a function, it replaces the call site with the function's body. Instead of:

```
caller:
    push registers
    call small_function    ; ~2ns overhead
    pop registers
    continue

small_function:
    ; do work
    ret
```

You get:

```
caller:
    ; do work (inlined)    ; 0ns overhead
    continue
```

But the benefits go beyond removing call overhead. Inlining enables **further optimizations**:

- **Constant propagation.** If the caller passes a constant, the compiler can evaluate expressions at compile time.
- **Dead code elimination.** If the caller only uses one branch, the other branches get removed.
- **Loop optimization.** The compiler can unroll, vectorize, or fuse loops that span the inlined boundary.
- **Alias analysis.** With all the code visible, the compiler can reason about pointer aliasing more effectively.

This is why inlining can give you 10x speedups, not just 2ns — the secondary optimizations often matter more than removing the call.

## How LLVM Decides to Inline

LLVM has a sophisticated inlining heuristic. It considers:

- **Function size.** Small functions (roughly <100 LLVM IR instructions) are inlined eagerly.
- **Call frequency.** Hot call sites (inside loops) are more likely to be inlined.
- **Optimization level.** `-O2` and `-O3` inline more aggressively than `-O1`.
- **Inline cost model.** Each instruction adds "cost." If the total cost exceeds a threshold, LLVM won't inline.

For functions within the **same crate**, LLVM has full visibility and inlines aggressively. The problems start at crate boundaries.

## The Cross-Crate Inlining Problem

By default, Rust compiles each crate independently. When crate A calls a function in crate B, LLVM doesn't have the function body from crate B — it only has the declaration. It can't inline it.

This is the main reason `#[inline]` exists in Rust. It's not telling LLVM "you should inline this" — it's telling the Rust compiler "include this function's body in the crate metadata so downstream crates can inline it."

```rust
// In crate `my_lib`

// Without #[inline]: only inlined within my_lib
fn helper(x: u32) -> u32 {
    x.wrapping_mul(2654435761)
}

// With #[inline]: body available to callers in other crates
#[inline]
pub fn hash_u32(x: u32) -> u32 {
    x.wrapping_mul(2654435761)
}
```

### The Three Inline Attributes

```rust
// Hint: "make this available for inlining across crates"
// LLVM still decides whether to actually inline
#[inline]
pub fn small_utility(x: u32) -> u32 { x + 1 }

// Strong hint: "always inline this, please"
// LLVM almost always obeys, but can still refuse
#[inline(always)]
pub fn critical_hot_path(x: u32) -> u32 { x + 1 }

// "Never inline this"
// Useful for cold error-handling paths
#[inline(never)]
pub fn cold_error_handler(err: &str) {
    eprintln!("Fatal: {}", err);
    std::process::exit(1);
}
```

My usage guidelines:

- **`#[inline]`** — Use on small public functions in libraries that are likely called in hot paths. This is the common case.
- **`#[inline(always)]`** — Use sparingly. Only when you've profiled and confirmed that LLVM isn't inlining something it should. I use this maybe once or twice per project.
- **`#[inline(never)]`** — Use on error handling and cold paths. Prevents bloating the hot code with rarely-executed instructions.

## When #[inline] Actually Helps

### Case 1: Small Functions in Library Crates

```rust
// lib.rs in a utility crate
// Without #[inline], callers in other crates pay call overhead
#[inline]
pub fn is_ascii_whitespace(b: u8) -> bool {
    matches!(b, b' ' | b'\t' | b'\n' | b'\r')
}
```

This is the textbook use case. The function is tiny, it's called millions of times, and callers are in other crates.

### Case 2: Generic Functions (Usually Don't Need It)

Generic functions are **monomorphized** — the compiler generates a specialized version for each concrete type. Because each specialization is generated in the calling crate, the body is already available for inlining. You don't need `#[inline]`.

```rust
// No #[inline] needed — monomorphization handles it
pub fn max<T: Ord>(a: T, b: T) -> T {
    if a >= b { a } else { b }
}
```

Exception: if your generic function calls non-generic helper functions, those helpers might still need `#[inline]`.

### Case 3: Trait Method Default Implementations

```rust
trait FastHash {
    fn hash_bytes(&self, bytes: &[u8]) -> u64;

    // Default implementations in traits are cross-crate boundaries
    #[inline]
    fn hash_str(&self, s: &str) -> u64 {
        self.hash_bytes(s.as_bytes())
    }
}
```

## When #[inline] Hurts

Inlining isn't free. Every inlined function increases the size of the caller. This has consequences:

**Instruction cache pressure.** If your hot loop's code expands beyond the L1 instruction cache (~32KB), you get I-cache misses. This can make inlining a net negative.

**Binary size.** Aggressive inlining duplicates code everywhere. A 100-byte function inlined at 500 call sites is 50KB of duplicated instructions.

**Compile time.** More inlining means more code for LLVM to optimize. This directly increases compile time.

```rust
// DON'T inline large functions
#[inline(always)]  // BAD — this function is too big
pub fn parse_complex_message(data: &[u8]) -> Result<Message, Error> {
    // 200 lines of parsing logic
    // Inlining this everywhere wastes I-cache
}

// DO mark the error path as cold
pub fn parse_complex_message(data: &[u8]) -> Result<Message, Error> {
    if data.is_empty() {
        return Err(handle_empty_input()); // cold path
    }
    // hot parsing path
    parse_inner(data)
}

#[inline(never)]
#[cold]
fn handle_empty_input() -> Error {
    Error::new("empty input")
}
```

The `#[cold]` attribute tells LLVM that this function is rarely called. Combined with `#[inline(never)]`, it keeps error-handling code out of the hot path entirely.

## Link-Time Optimization (LTO)

LTO is the nuclear option for inlining. Instead of compiling each crate independently, LTO merges all crates into a single compilation unit. LLVM then has complete visibility and can inline across any crate boundary.

```toml
# Cargo.toml
[profile.release]
lto = true         # Full LTO — maximum optimization
# lto = "thin"     # Thin LTO — faster compile, nearly as good
# lto = "fat"      # Same as true
```

### Full LTO vs Thin LTO

**Full LTO (`lto = true`):**
- Merges everything into one LLVM module
- Maximum optimization potential
- Very slow compilation (10-30x slower)
- Best for final release builds

**Thin LTO (`lto = "thin"`):**
- Parallel, incremental LTO
- 90-95% of Full LTO's benefit
- 2-4x slower compile (much better than full)
- Good for most cases

```toml
# My typical release profile
[profile.release]
lto = "thin"
opt-level = 3
codegen-units = 1   # see below
debug = true        # keep debug info for profiling
```

### Codegen Units

By default, Rust splits each crate into multiple **codegen units** for parallel compilation. This speeds up compilation but prevents some optimizations — LLVM can't inline across codegen units within the same crate.

```toml
[profile.release]
codegen-units = 1   # single codegen unit — maximum optimization
```

Setting `codegen-units = 1` is like intra-crate LTO. Combined with `lto = "thin"`, this gives you the best optimization with reasonable compile times.

### Benchmark: LTO Impact

Here's a real example — a JSON parser that depends on several crates:

```toml
# Benchmark with different LTO settings

# No LTO (default):
#   parse_json:  12.4 µs
#   binary size: 2.1 MB
#   compile:     8s

# Thin LTO + codegen-units = 1:
#   parse_json:  9.8 µs    (-21%)
#   binary size: 1.6 MB    (-24%)
#   compile:     22s

# Full LTO + codegen-units = 1:
#   parse_json:  9.5 µs    (-23%)
#   binary size: 1.4 MB    (-33%)
#   compile:     65s
```

21% faster from just changing two lines in `Cargo.toml`. The improvement comes from LLVM inlining hot functions from serde, serde_json, and other dependencies.

## Profile-Guided Optimization (PGO)

PGO takes inlining decisions to the next level. You run your program with instrumentation, collect data about which functions are hot, then recompile using that data to guide inlining decisions.

```bash
# Step 1: Build with instrumentation
RUSTFLAGS="-Cprofile-generate=/tmp/pgo-data" \
    cargo build --release

# Step 2: Run your workload to collect profile data
./target/release/my_program < representative_input.txt

# Step 3: Merge profile data
llvm-profdata merge -o /tmp/pgo-data/merged.profdata /tmp/pgo-data

# Step 4: Rebuild with profile data
RUSTFLAGS="-Cprofile-use=/tmp/pgo-data/merged.profdata" \
    cargo build --release
```

PGO typically gives an additional 5-15% improvement on top of LTO. It's particularly effective for branch-heavy code because LLVM uses the profile data to optimize branch layouts.

## A Practical Inlining Checklist

1. **Don't add `#[inline]` everywhere.** Within a single crate, LLVM handles inlining fine. Only annotate public functions in libraries.

2. **Profile first.** If a function shows up in your flamegraph with significant self time and it's small, *then* consider `#[inline]`.

3. **Enable Thin LTO for release builds.** It's the biggest bang-for-buck optimization you'll get.

4. **Set `codegen-units = 1` for release.** Combined with LTO, this maximizes optimization opportunities.

5. **Use `#[inline(never)]` and `#[cold]` on error paths.** Keep the hot path tight and I-cache-friendly.

6. **Consider PGO for production binaries.** If you can run representative workloads, PGO is worth the build complexity.

## The Takeaway

Inlining is the foundation of most compiler optimizations in Rust. Within a crate, LLVM handles it well. Across crates, you need `#[inline]` or LTO to give the optimizer visibility.

For most projects, the highest-impact change is adding `lto = "thin"` and `codegen-units = 1` to your release profile. Do that before you start sprinkling `#[inline]` on individual functions.
