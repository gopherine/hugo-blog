---
title: "Lesson 9: Miri — Your safety net for unsafe Rust"
author: Atharva Pandey
series: "Rust Memory Model & Internals"
lesson: 9
keywords: [Rust, rust, memory, internals, Miri, undefined behavior, UB, unsafe, memory safety, interpreter]
tags: [Rust tutorial, rust, internals]
date: '2025-03-19T08:45:00.000Z'
---

I once shipped a Rust library with an unsafe block that worked perfectly on x86, passed all tests, and ran flawlessly in production for months. Then someone compiled it on ARM and it segfaulted immediately. The undefined behavior had been lurking the whole time — x86 just happened to tolerate the misaligned read that ARM wouldn't. If I'd run Miri before releasing, I would have caught it in thirty seconds. Lesson learned the hard way.

## What Is Miri?

Miri is an interpreter for Rust's Mid-level Intermediate Representation (MIR). Instead of compiling your code to machine code and running it natively, Miri executes it step by step in a virtual machine that tracks every memory access, every pointer, every allocation. It catches undefined behavior that the compiler can't prove at compile time and that tests might not trigger on your particular hardware.

Think of it as Valgrind or AddressSanitizer, but specifically designed for Rust's memory model. It understands ownership, borrowing, and the exact rules that unsafe code must follow.

## Installing and Running Miri

Miri ships as a rustup component:

```bash
rustup component add miri
```

Run your test suite under Miri:

```bash
cargo miri test
```

Run a specific binary:

```bash
cargo miri run
```

That's it. No configuration, no build system changes, no linking against special libraries. Just prefix your cargo command with `miri`.

The catch: Miri runs your code 10-100x slower than native execution. It's not for running your full application — it's for running your test suite, especially tests that exercise unsafe code.

## What Miri Catches

### 1. Out-of-Bounds Access

```rust
fn main() {
    let x = [1, 2, 3];
    let val = unsafe { *x.as_ptr().add(5) }; // reading past the end
    println!("{}", val);
}
```

Native execution might print garbage, crash, or even print a "correct" value (if there happens to be valid memory there). Miri catches it immediately:

```
error: Undefined Behavior: memory access at offset 20, but alloc only has size 12
```

### 2. Use After Free

```rust
fn main() {
    let ptr = {
        let x = Box::new(42);
        let raw = &*x as *const i32;
        raw
        // x is dropped here
    };

    let val = unsafe { *ptr }; // reading freed memory
    println!("{}", val);
}
```

Miri output:

```
error: Undefined Behavior: pointer to alloc which was already freed
```

### 3. Uninitialized Memory Reads

```rust
use std::mem::MaybeUninit;

fn main() {
    let val: i32 = unsafe {
        let uninit: MaybeUninit<i32> = MaybeUninit::uninit();
        uninit.assume_init() // reading uninitialized memory
    };
    println!("{}", val);
}
```

This might "work" on your machine — the garbage bytes might even look like a valid integer. Miri knows better:

```
error: Undefined Behavior: using uninitialized data
```

### 4. Invalid References

```rust
fn main() {
    let ptr: *const i32 = std::ptr::null();
    let _ref = unsafe { &*ptr }; // creating a reference from null
}
```

In Rust, creating a null reference is undefined behavior — not just dereferencing it. Miri catches the creation:

```
error: Undefined Behavior: null reference
```

### 5. Misaligned Access

This was the one that bit me on ARM:

```rust
fn main() {
    let bytes: [u8; 8] = [0; 8];
    let ptr = bytes.as_ptr().wrapping_add(1) as *const u32;
    let val = unsafe { *ptr }; // reading a u32 from a non-4-aligned address
    println!("{}", val);
}
```

x86 handles misaligned reads (slowly). ARM doesn't. Miri catches it regardless of your platform:

```
error: Undefined Behavior: accessing memory with alignment 1, but alignment 4 is required
```

### 6. Violating Stacked Borrows

This is Miri's most unique capability. Rust's aliasing model (called "Stacked Borrows" or the newer "Tree Borrows") has rules about which pointers are valid at any given time. Even if the memory access is technically correct, violating the aliasing rules is UB:

```rust
fn main() {
    let mut x = 42;
    let ptr1 = &mut x as *mut i32;
    let ptr2 = &mut x as *mut i32; // creates a second mutable pointer

    unsafe {
        *ptr1 = 1; // writes through ptr1 — but ptr2 invalidated ptr1
    }
    println!("{}", x);
}
```

This compiles and runs fine natively. But it violates the aliasing rules — two mutable pointers to the same data. Miri catches it:

```
error: Undefined Behavior: attempting a write access using a tag that was already invalidated
```

This matters because the compiler is allowed to optimize based on the assumption that mutable references don't alias. Your code might work today, but a future compiler optimization could break it.

## Stacked Borrows: The Mental Model

Miri tracks a "borrow stack" for each memory location. Every time you create a reference or raw pointer, it's pushed onto the stack. When you use a pointer, Miri checks that it's still valid — that nothing has invalidated it by creating a conflicting reference.

```rust
fn main() {
    let mut data = 10;

    let ref1 = &mut data;       // stack: [ref1]
    let ptr = ref1 as *mut i32; // stack: [ref1, ptr]
    let ref2 = &mut *ref1;     // stack: [ref1, ptr, ref2]

    // Using ref2 is fine — it's on top
    unsafe { println!("{}", *ref2); }

    // Using ptr pops ref2 off the stack — ref2 is now invalid
    unsafe { *ptr = 20; }       // stack: [ref1, ptr]

    // Using ref2 again would be UB — it was popped
    // unsafe { println!("{}", *ref2); } // Miri would catch this
}
```

The rules are nuanced, but the intuition is: using an older pointer invalidates all newer pointers that were derived after it. This matches the borrow checker's static rules but applies to raw pointers, which the borrow checker can't track.

## Writing Miri-Friendly Tests

The best practice is to write tests specifically for your unsafe code and run them under Miri regularly:

```rust
// In your library
pub unsafe fn copy_bytes(src: *const u8, dst: *mut u8, count: usize) {
    // Hypothetical unsafe function
    for i in 0..count {
        unsafe {
            *dst.add(i) = *src.add(i);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_copy_bytes_basic() {
        let src = [1u8, 2, 3, 4, 5];
        let mut dst = [0u8; 5];

        unsafe {
            copy_bytes(src.as_ptr(), dst.as_mut_ptr(), 5);
        }
        assert_eq!(dst, [1, 2, 3, 4, 5]);
    }

    #[test]
    fn test_copy_bytes_zero_length() {
        let src = [1u8];
        let mut dst = [0u8; 1];

        unsafe {
            copy_bytes(src.as_ptr(), dst.as_mut_ptr(), 0);
        }
        assert_eq!(dst, [0]); // unchanged
    }

    #[test]
    fn test_copy_bytes_partial() {
        let src = [10u8, 20, 30, 40, 50];
        let mut dst = [0u8; 5];

        unsafe {
            copy_bytes(src.as_ptr().add(2), dst.as_mut_ptr(), 3);
        }
        assert_eq!(dst, [30, 40, 50, 0, 0]);
    }
}
```

Run with `cargo miri test`. Miri will execute each test in its interpreter and flag any UB.

### Testing Concurrent Code

Miri can detect data races in multi-threaded code, though it uses a random thread scheduler, so you might need multiple runs:

```rust
#[test]
fn test_concurrent_access() {
    use std::sync::Arc;
    use std::sync::atomic::{AtomicU64, Ordering};

    let counter = Arc::new(AtomicU64::new(0));
    let mut handles = vec![];

    for _ in 0..4 {
        let c = Arc::clone(&counter);
        handles.push(std::thread::spawn(move || {
            for _ in 0..100 {
                c.fetch_add(1, Ordering::Relaxed);
            }
        }));
    }

    for h in handles {
        h.join().unwrap();
    }

    assert_eq!(counter.load(Ordering::SeqCst), 400);
}
```

To increase the chance of catching race conditions, run Miri with different random seeds:

```bash
MIRIFLAGS="-Zmiri-seed=42" cargo miri test
```

Or run multiple seeds in a loop:

```bash
for seed in $(seq 1 20); do
    echo "Seed: $seed"
    MIRIFLAGS="-Zmiri-seed=$seed" cargo miri test || break
done
```

## Miri Flags and Configuration

Miri has several useful configuration options:

```bash
# Detect memory leaks
MIRIFLAGS="-Zmiri-leak-check" cargo miri test

# Use Tree Borrows instead of Stacked Borrows (more permissive, newer model)
MIRIFLAGS="-Zmiri-tree-borrows" cargo miri test

# Disable isolation (allows file I/O, network, etc.)
MIRIFLAGS="-Zmiri-disable-isolation" cargo miri run

# Set a specific random seed for reproducible results
MIRIFLAGS="-Zmiri-seed=12345" cargo miri test

# Increase the backtrace verbosity
MIRIFLAGS="-Zmiri-backtrace=full" cargo miri test
```

### Leak Detection

Miri can find memory leaks — allocations that are never freed:

```rust
fn main() {
    let x = Box::new(42);
    let raw = Box::into_raw(x);
    // Forgot to call Box::from_raw(raw) — memory leak!
}
```

With `-Zmiri-leak-check`:

```
error: memory leak: alloc was never freed
```

## What Miri Cannot Do

Miri is powerful but has limitations:

**No FFI.** Miri can't interpret C code or call into native libraries. If your unsafe code wraps C functions, Miri can't check the C side. It can check that you're calling the Rust-side wrappers correctly, but not the underlying C behavior.

**Slow execution.** 10-100x slower than native. Not suitable for benchmarks, load tests, or anything with large data sets. Keep your Miri test inputs small.

**No I/O by default.** Miri runs in an isolated environment. File I/O, network access, and system calls are blocked unless you pass `-Zmiri-disable-isolation`. Even then, support is limited.

**Not all UB is detectable.** Miri catches most common forms of undefined behavior, but some subtle cases might slip through. It's a best-effort tool, not a formal proof.

**Not a substitute for the borrow checker.** Safe Rust doesn't need Miri — the compiler already prevents UB in safe code. Miri is specifically for verifying that your unsafe code upholds the safety invariants that the borrow checker assumes.

## CI Integration

Put Miri in your CI pipeline. Here's what that looks like in a GitHub Action:

```yaml
# .github/workflows/miri.yml
name: Miri
on: [push, pull_request]

jobs:
  miri:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@nightly
        with:
          components: miri
      - run: cargo miri test
        env:
          MIRIFLAGS: "-Zmiri-leak-check"
```

Run Miri on every PR that touches unsafe code. It's slow, but catching UB before it ships is worth the CI minutes.

## Practical Workflow

Here's how I use Miri in practice:

1. **Write the unsafe code.** Keep unsafe blocks as small as possible.
2. **Write targeted tests.** Cover the edge cases: zero-length inputs, null pointers, maximum sizes, concurrent access.
3. **Run `cargo miri test` locally.** Fix any issues immediately.
4. **Run with multiple seeds.** `for seed in $(seq 1 10); do MIRIFLAGS="-Zmiri-seed=$seed" cargo miri test; done`
5. **Add to CI.** Run on every PR.
6. **Periodically update Miri.** The stacked borrows model evolves, and newer versions catch more issues.

The goal isn't to eliminate all unsafe code — sometimes you need it. The goal is to verify that your unsafe code is actually correct, not just "works on my machine."

## What's Next

We've gone deep into Rust's memory model: layouts, the stack and heap, pointers, allocation, drop order, and how to verify it all with Miri. For the final lesson, we'll zoom out and look at the big picture — how does `rustc` actually turn your source code into a binary? In Lesson 10, we'll trace the entire compilation pipeline from parsing to machine code, touching on the HIR, MIR (where Miri gets its name), and LLVM codegen. Understanding the pipeline helps you understand *why* Rust makes the decisions it does.
