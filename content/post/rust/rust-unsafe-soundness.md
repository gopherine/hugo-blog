---
title: "Lesson 10: Soundness — The ultimate safety guarantee"
author: Atharva Pandey
series: "Rust Unsafe & FFI"
lesson: 10
keywords: [Rust, rust, unsafe, soundness, safety invariant, validity invariant, undefined behavior, Miri]
tags: [Rust tutorial, rust, unsafe, ffi]
date: '2025-07-04T16:50:00.000Z'
---

A few months ago, someone filed a soundness bug against a crate I maintained. The report was elegant — three lines of safe code that triggered a use-after-free through my API. No `unsafe` in the caller's code. The bug was in my `unsafe` implementation. I fixed it within the hour, cut a patch release, and filed a CVE advisory. That's the social contract of soundness in Rust: if safe code can cause undefined behavior, the bug is *always* in the library.

Soundness is the capstone of everything we've covered in this course. Raw pointers, transmute, safe abstractions, FFI — they all converge on one question: can a user of your API, writing only safe Rust, trigger undefined behavior? If yes, your code is unsound. If no, it's sound. There's no middle ground.

## What Soundness Means

A Rust API is **sound** if no sequence of calls using only safe code can cause undefined behavior. Period. It doesn't matter how weird the usage is. It doesn't matter if "nobody would do that." If it's expressible in safe Rust and it causes UB, it's a bug in your library.

```rust
// UNSOUND — safe code can cause UB
pub struct BadVec<T> {
    ptr: *mut T,
    len: usize,
    cap: usize,
}

impl<T> BadVec<T> {
    pub fn new() -> Self {
        BadVec {
            ptr: std::ptr::null_mut(),
            len: 0,
            cap: 0,
        }
    }

    // BUG: This is public, so users can set len to anything.
    // If they set len > cap, get() will read out of bounds.
    pub fn set_len(&mut self, new_len: usize) {
        self.len = new_len;
    }

    pub fn get(&self, index: usize) -> Option<&T> {
        if index < self.len {
            // SAFETY: index < len... but len might be garbage
            // because set_len doesn't validate anything!
            unsafe { Some(&*self.ptr.add(index)) }
        } else {
            None
        }
    }
}

// Exploit — pure safe code, no unsafe anywhere:
fn exploit() {
    let v = BadVec::<u8>::new();
    // v.ptr is null, v.len is 0, v.cap is 0
    // v.set_len(100); // Now len is 100 but nothing is allocated
    // v.get(0); // Dereferences null + 0 = null pointer. UB.
}
```

The fix is obvious: make `set_len` unsafe, or remove it, or add validation. The standard library's `Vec::set_len` is `unsafe` for exactly this reason.

## The Safety vs. Validity Distinction

Rust has two layers of type invariants that matter for soundness:

**Safety invariant** — must hold for the type to function correctly. Breaking it might cause logic bugs or panic, but not UB. It's enforced by the type's methods.

**Validity invariant** — must hold for the compiler's assumptions to be correct. Breaking it causes UB. It's enforced by `unsafe` code.

```rust
// String has both:
// Validity invariant: The buffer pointed to by ptr is allocated,
//   len <= cap, and the bytes in 0..len are valid UTF-8.
// Safety invariant: Same as above — for String, they're identical.

// Vec<T> is more nuanced:
// Validity invariant: ptr is a valid allocation (or dangling for cap=0),
//   len <= cap.
// Safety invariant: Elements 0..len are initialized and valid T values.
//   This is stronger than what the compiler strictly requires for the
//   Vec struct itself, but it's what makes Vec's methods correct.

// BTreeMap has:
// Validity invariant: Internal pointers are valid, nodes are allocated.
// Safety invariant: The tree is actually a valid B-tree with keys in order.
//   If you corrupt the ordering, you won't get UB, but lookup results
//   will be nonsensical.
```

When writing `unsafe` code, you must uphold *both* invariants. But soundness specifically means: can safe code violate the validity invariant? If yes, that's a soundness bug.

## Common Soundness Bugs

After reviewing dozens of soundness advisories in the Rust ecosystem, these are the patterns I see repeatedly.

### 1. Leaking Uninitialized Memory

```rust
// UNSOUND
pub fn make_buffer(size: usize) -> Vec<u8> {
    let mut buf = Vec::with_capacity(size);
    // BUG: set_len without initializing the memory
    // Users can now read uninitialized memory, which is UB
    unsafe { buf.set_len(size); }
    buf
}

// SOUND
pub fn make_buffer_sound(size: usize) -> Vec<u8> {
    vec![0u8; size] // Initialized to zero
}

// Also sound: use MaybeUninit explicitly
pub fn make_buffer_explicit(size: usize) -> Vec<u8> {
    let mut buf = Vec::with_capacity(size);
    // SAFETY: We write to every byte before setting len.
    unsafe {
        std::ptr::write_bytes(buf.as_mut_ptr(), 0, size);
        buf.set_len(size);
    }
    buf
}
```

### 2. Aliasing &mut References

```rust
// UNSOUND — creates two &mut to the same data
pub fn split_at_mut_bad<T>(slice: &mut [T], mid: usize) -> (&mut [T], &mut [T]) {
    let ptr = slice.as_mut_ptr();
    let len = slice.len();
    assert!(mid <= len);

    unsafe {
        // This is actually fine — std does exactly this.
        // But what if we got the ranges wrong?
        (
            std::slice::from_raw_parts_mut(ptr, mid),
            std::slice::from_raw_parts_mut(ptr.add(mid), len - mid),
        )
    }
}

// If someone wrote the ranges overlapping:
pub fn overlapping_bad<T>(slice: &mut [T]) -> (&mut [T], &mut [T]) {
    let ptr = slice.as_mut_ptr();
    let len = slice.len();
    unsafe {
        // BUG: Both slices cover the entire range — aliasing &mut!
        (
            std::slice::from_raw_parts_mut(ptr, len),
            std::slice::from_raw_parts_mut(ptr, len),
        )
    }
}
```

The Rust compiler assumes `&mut T` references are exclusive. If two `&mut` references point to overlapping memory, the optimizer may reorder or eliminate operations, causing silent data corruption.

### 3. Missing Send/Sync Bounds

```rust
use std::cell::Cell;
use std::rc::Rc;

// UNSOUND — this type contains Rc which is not Send,
// but we're claiming it's safe to send between threads
struct BadWrapper {
    data: Rc<Cell<i32>>,
}

// DON'T DO THIS without verifying thread safety
// unsafe impl Send for BadWrapper {}

// If you implement Send for a type containing Rc, two threads
// can call Rc::clone simultaneously, causing a data race on
// the reference count (which is a non-atomic usize).
```

Auto-trait implementations of `Send` and `Sync` are sound by default — the compiler only implements them when all fields are `Send`/`Sync`. Manually implementing these traits incorrectly is one of the most common soundness bugs in the ecosystem.

### 4. Lifetime Unsoundness

```rust
// UNSOUND — returns a reference that outlives the data
pub struct Ref<'a, T> {
    ptr: *const T,
    _marker: std::marker::PhantomData<&'a T>,
}

impl<'a, T> Ref<'a, T> {
    pub fn new(value: &'a T) -> Self {
        Ref {
            ptr: value,
            _marker: std::marker::PhantomData,
        }
    }

    pub fn get(&self) -> &'a T {
        // SAFETY: The pointer is valid for lifetime 'a
        // because it was created from a &'a T reference.
        unsafe { &*self.ptr }
    }
}

// This is actually sound IF nobody can change ptr.
// But if we add this:
impl<'a, T> Ref<'a, T> {
    // UNSOUND: allows replacing the pointer with one
    // that has a shorter lifetime
    pub fn replace(&mut self, other: &T) {
        self.ptr = other;
        // Now ptr might not live for 'a!
    }
}

// Exploit:
fn exploit() {
    let long_lived = 42;
    let mut r = Ref::new(&long_lived);
    {
        let short_lived = 99;
        r.replace(&short_lived);
    }
    // short_lived is dropped, but r.get() still returns
    // a reference to it. Use-after-free.
    // println!("{}", r.get()); // UB!
}
```

The fix: either remove `replace`, make it `unsafe`, or require the new reference to have the same lifetime `'a`.

## How to Audit for Soundness

When I review `unsafe` code — mine or someone else's — here's the process:

### Step 1: List All Public API Surfaces

Every `pub` function, method, trait, and type is a potential entry point. For each one, ask: "What's the worst thing a caller could do with this?"

### Step 2: Find All unsafe Blocks

For each `unsafe` block, identify:
- Which of the five superpowers is being used?
- What invariants does it rely on?
- Are those invariants guaranteed by the type system, or by convention?

### Step 3: Check the Invariant Chain

For every invariant an `unsafe` block relies on, trace it back to the source. Is it established by a constructor? Maintained by every mutating method? Can any public API break it?

```rust
// Invariant chain example:
// 1. Constructor establishes: ptr is valid, len = 0, cap = allocated size
// 2. push() maintains: len <= cap, elements 0..len are initialized
// 3. get() relies on: elements 0..len are initialized
// 4. Question: Can any public method break invariant 2?
//    - If set_len is public and unchecked → UNSOUND
//    - If set_len is unsafe → sound (caller takes responsibility)
//    - If set_len doesn't exist → sound
```

### Step 4: Consider Adversarial Inputs

Think like someone trying to break your code. What happens with:
- Zero-length inputs
- Maximum-size inputs (`usize::MAX`)
- Concurrent access (if `Send`/`Sync` is implemented)
- Values that satisfy the type system but are semantically wrong

### Step 5: Run Miri

```bash
cargo miri test
```

Miri checks for:
- Out-of-bounds memory access
- Use of uninitialized memory
- Dangling pointer dereference
- Aliasing violations (Stacked Borrows)
- Data races
- Memory leaks (with `-Zmiri-leak-check`)

It won't catch everything — it only checks execution paths your tests exercise — but it catches a remarkable amount.

## The Stacked Borrows Model

Miri enforces "Stacked Borrows," which is Rust's formal model for pointer aliasing. Understanding it helps you write sound `unsafe` code.

The basic idea: every memory location has a "borrow stack" tracking which pointers are allowed to access it. When you create a reference, it's pushed onto the stack. When you use a pointer, anything above it on the stack is invalidated.

```rust
fn stacked_borrows_example() {
    let mut x = 42;
    let ptr = &mut x as *mut i32;  // Raw pointer on the stack

    let ref_x = &x;               // Shared ref pushed above ptr
    println!("{}", ref_x);         // Fine — ref_x is on top

    // Using ptr now would invalidate ref_x (and everything above ptr)
    // because ptr is below ref_x on the stack.
    // If we use ref_x after that, it's UB.

    unsafe { *ptr = 100; }         // This invalidates ref_x
    // println!("{}", ref_x);      // UB! ref_x was invalidated

    println!("{}", x);             // Fine — x itself is always valid
}
```

Miri will catch Stacked Borrows violations. Run it. Trust it. When it says you have a violation, you do — even if the code "works" in practice.

## Writing a Soundness Test Suite

Soundness tests are different from regular tests. They exercise edge cases and adversarial usage patterns:

```rust
#[cfg(test)]
mod soundness_tests {
    use super::*;

    // Test that dropping in the middle of iteration is sound
    #[test]
    fn drop_during_iteration() {
        let mut v = MyVec::new();
        for i in 0..100 {
            v.push(String::from(format!("string_{}", i)));
        }
        let mut count = 0;
        for item in v.iter() {
            count += 1;
            if count == 50 {
                break; // Stop iteration early
            }
        }
        // v should still be valid and droppable
        drop(v);
    }

    // Test zero-size types
    #[test]
    fn zero_size_type() {
        let mut v = MyVec::new();
        for _ in 0..1000 {
            v.push(()); // ZST
        }
        assert_eq!(v.len(), 1000);
    }

    // Test with types that panic in Drop
    #[test]
    fn panic_in_drop() {
        struct PanicOnDrop;
        impl Drop for PanicOnDrop {
            fn drop(&mut self) {
                // Don't actually panic — just test that it's called
            }
        }

        let mut v = MyVec::new();
        v.push(PanicOnDrop);
        v.push(PanicOnDrop);
        // Both should be dropped
    }

    // Test that we don't double-drop
    #[test]
    fn no_double_drop() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        static DROP_COUNT: AtomicUsize = AtomicUsize::new(0);

        struct Counted;
        impl Drop for Counted {
            fn drop(&mut self) {
                DROP_COUNT.fetch_add(1, Ordering::SeqCst);
            }
        }

        DROP_COUNT.store(0, Ordering::SeqCst);
        {
            let mut v = MyVec::new();
            v.push(Counted);
            v.push(Counted);
            v.push(Counted);
        }
        assert_eq!(DROP_COUNT.load(Ordering::SeqCst), 3);
    }

    // Test maximum capacity
    #[test]
    fn capacity_overflow() {
        let result = std::panic::catch_unwind(|| {
            let _v = MyVec::<u8>::with_capacity(usize::MAX);
        });
        assert!(result.is_err()); // Should panic, not UB
    }
}
```

## The Soundness Ecosystem

Rust has infrastructure for dealing with soundness:

**RustSec Advisory Database** — a database of security advisories for Rust crates, including soundness bugs. Run `cargo audit` to check your dependencies.

```bash
cargo install cargo-audit
cargo audit
```

**cargo-careful** — runs your tests with extra safety checks enabled in the standard library:

```bash
cargo install cargo-careful
cargo careful test
```

**unsafe code guidelines** — the Rust project maintains a document (the UCG) that defines what `unsafe` code is and isn't allowed to do. It's not finished, but it's the best reference we have.

## The Responsibility Model

Let me be blunt about how responsibility works in Rust's ecosystem:

1. **Safe code has zero responsibility for UB.** If a safe program causes UB, it's never the safe code's fault.

2. **unsafe code has total responsibility.** If your `unsafe` block enables UB through any sequence of safe API calls, you have a soundness bug.

3. **Library authors bear the burden.** You wrote the `unsafe` — you own the consequences. Your users shouldn't need to read your source code to avoid UB.

4. **Soundness bugs are security vulnerabilities.** They get CVEs. They get emergency patch releases. They're treated with the same severity as buffer overflows in C code, because that's exactly what they are.

This is why the earlier lessons in this course stressed encapsulation so heavily. Private fields aren't a style choice — they're a load-bearing safety mechanism. Safe API design isn't about ergonomics — it's about making unsoundness structurally impossible.

## Bringing It All Together

This course has been a journey from "what does `unsafe` mean" to "how do I guarantee my code is sound." Here's the condensed version:

- `unsafe` means "I'm upholding invariants the compiler can't check"
- Raw pointers give you manual memory control — check alignment, validity, and aliasing
- `transmute` reinterprets bits — only valid when bit patterns are compatible
- Safe abstractions encapsulate `unsafe` behind APIs that can't be misused
- FFI is inherently unsafe — C has no safety guarantees
- PyO3 and napi-rs hide FFI complexity behind ergonomic interfaces
- Soundness is the ultimate test: can safe code trigger UB through your API?

Every piece of `unsafe` code you write should be small, well-documented, thoroughly tested under Miri, and wrapped in a safe API. If you follow that discipline, `unsafe` isn't scary — it's a precision tool for the cases where the compiler needs your help.

The Rustonomicon's closing advice applies here: "be careful, be correct, and when in doubt, don't use `unsafe`." I'd add one more: when you do use it, make sure nobody else has to.
