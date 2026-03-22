---
title: "Lesson 1: What unsafe Actually Means — The contract you're signing"
author: Atharva Pandey
series: "Rust Unsafe & FFI"
lesson: 1
keywords: [Rust, rust, unsafe, unsafe block, safety invariants, undefined behavior]
tags: [Rust tutorial, rust, unsafe, ffi]
date: '2025-06-12T08:32:00.000Z'
---

I spent my first six months writing Rust thinking `unsafe` meant "this code is dangerous and you should feel bad." That misunderstanding cost me weeks — I'd bend over backwards to avoid it, writing convoluted safe wrappers around problems that genuinely needed a raw pointer or two. Once I actually read the Rustonomicon and understood what `unsafe` *really* means, everything clicked.

Let's clear this up properly.

## unsafe Is Not What You Think

Here's the biggest misconception in the Rust ecosystem: `unsafe` does not mean "this code is broken" or "this code does bad things." It means **"I, the programmer, am upholding invariants that the compiler cannot verify."**

That's it. That's the whole thing.

Safe Rust gives you a guarantee: if your code compiles, it won't have data races, use-after-free bugs, dangling pointers, or buffer overflows. The compiler checks all of this. But there are legitimate operations — talking to hardware, calling C libraries, implementing custom allocators — where the compiler simply *can't* verify safety. It doesn't have enough information.

`unsafe` is the mechanism that says: "Hey compiler, I need to do something you can't check. I promise I've done the checking myself."

```rust
fn main() {
    let x: i32 = 42;
    let ptr: *const i32 = &x;

    // Creating a raw pointer? Totally safe. No unsafe needed.
    // The pointer exists, it just sits there.

    // DEREFERENCING a raw pointer? That's where unsafe comes in.
    unsafe {
        println!("Value: {}", *ptr);
    }
}
```

Notice something? Creating the raw pointer is safe. The compiler doesn't care that you have a `*const i32` floating around. What it *does* care about is when you try to read through that pointer — because it can't guarantee the pointer is still valid, properly aligned, or pointing to initialized memory.

## The Five unsafe Superpowers

Inside an `unsafe` block, you can do exactly five things that safe Rust forbids. Not four, not six. Five:

1. **Dereference a raw pointer**
2. **Call an unsafe function or method**
3. **Access or modify a mutable static variable**
4. **Implement an unsafe trait**
5. **Access fields of a union**

That's the complete list. Everything else works exactly the same inside `unsafe` as it does outside. The borrow checker still runs. Types still must match. Lifetimes are still enforced. You don't get to throw away the whole type system — you get five specific escape hatches.

```rust
// Superpower 1: Dereference raw pointers
unsafe {
    let val = *some_raw_pointer;
}

// Superpower 2: Call unsafe functions
unsafe {
    std::ptr::write(dst, value);
}

// Superpower 3: Mutable statics
static mut COUNTER: u32 = 0;
unsafe {
    COUNTER += 1;
}

// Superpower 4: Unsafe trait implementation
unsafe impl Send for MyType {}

// Superpower 5: Union field access
union IntOrFloat {
    i: i32,
    f: f32,
}
let u = IntOrFloat { i: 42 };
unsafe {
    println!("{}", u.f); // Reinterprets the bits
}
```

## The Contract

When you write `unsafe`, you're entering a contract. The compiler's side of the deal is: "I'll let you do things I can't verify." Your side is: "I guarantee this code upholds all of Rust's safety invariants."

What does that actually mean in practice? You're promising that:

- Raw pointers you dereference are non-null, properly aligned, and point to valid, initialized memory
- The memory you're accessing hasn't been freed
- You're not creating mutable aliases — two `&mut` references to the same data
- Any values you produce are valid for their types (no invalid enum discriminants, no uninitialized bools)
- You're not causing data races

Break any of these promises and you get **undefined behavior**. Not a crash, not an exception — *undefined behavior*. The compiler is allowed to assume your promises hold true and optimize accordingly. If they don't, anything can happen. Your program might work fine in debug mode and corrupt memory in release. It might work today and break next Tuesday after a compiler update.

```rust
// This is UNDEFINED BEHAVIOR. Don't do this.
fn terrible_idea() -> &'static str {
    let s = String::from("hello");
    let ptr = s.as_ptr();
    let len = s.len();
    drop(s); // s is freed here

    // ptr now dangles — the memory has been deallocated
    unsafe {
        let slice = std::slice::from_raw_parts(ptr, len);
        std::str::from_utf8_unchecked(slice) // UB: reading freed memory
    }
}
```

This code might actually *appear to work* in some cases — the allocator might not have reused the memory yet. That's the insidious part. Undefined behavior doesn't mean "instant crash." It means "all bets are off."

## unsafe Blocks vs unsafe Functions

There's a subtle but important distinction here. An `unsafe` block says "I'm about to use one of the five superpowers, and I've verified it's okay." An `unsafe fn` says "calling this function requires the caller to uphold certain invariants."

```rust
// This function is unsafe to CALL because the caller
// must ensure ptr is valid and properly aligned
unsafe fn read_value(ptr: *const i32) -> i32 {
    *ptr
}

// This function is SAFE to call because it handles
// the unsafe parts internally
fn safe_read(slice: &[i32], index: usize) -> Option<i32> {
    if index < slice.len() {
        // SAFETY: we just checked that index is in bounds
        unsafe { Some(*slice.as_ptr().add(index)) }
    } else {
        None
    }
}
```

See the difference? `read_value` pushes the safety responsibility to the caller. `safe_read` takes on that responsibility itself by checking bounds first. Both use `unsafe` internally, but only one requires trust from its callers.

This is the foundation of Rust's entire standard library. `Vec`, `HashMap`, `String` — they're all built on `unsafe` code internally, wrapped in safe APIs. You use them every day without thinking about it.

## The SAFETY Comment Convention

The Rust community has a strong convention: every `unsafe` block should have a `// SAFETY:` comment explaining *why* the invariants hold. This isn't just style — it's documentation of your reasoning that future you (and your teammates) can audit.

```rust
fn get_unchecked(data: &[u8], index: usize) -> u8 {
    debug_assert!(index < data.len());
    // SAFETY: Caller must ensure index < data.len().
    // We add a debug_assert above to catch violations in testing.
    unsafe { *data.as_ptr().add(index) }
}
```

I've reviewed plenty of Rust code where the `// SAFETY` comment was wrong, or where writing it forced the author to realize their reasoning was flawed. The comment isn't bureaucracy — it's a forcing function for clear thinking.

Clippy will actually warn you if you have an `unsafe` block without a safety comment (`clippy::undocumented_unsafe_blocks`). Turn that lint on. Seriously.

## When Is unsafe Actually Necessary?

In my experience, there are roughly four categories where you genuinely need `unsafe`:

**1. Performance-critical code where bounds checks matter.** If you're processing millions of pixels or parsing gigabytes of network data, sometimes that bounds check on every array access adds up. Profile first — but when the numbers justify it, `get_unchecked` exists for a reason.

**2. FFI (Foreign Function Interface).** Calling into C libraries is inherently unsafe because C doesn't have Rust's guarantees. We'll spend several lessons on this.

**3. Low-level systems programming.** Writing an allocator, implementing lock-free data structures, talking to hardware registers — these need raw pointers and atomics that the compiler can't reason about.

**4. Working around the borrow checker when you can prove safety.** Sometimes the borrow checker is too conservative. Self-referential structs, intrusive linked lists, arena allocation — there are legitimate patterns where you know the code is safe but the compiler disagrees.

```rust
// Example: A simple arena allocator needs unsafe
struct Arena {
    storage: Vec<u8>,
    offset: usize,
}

impl Arena {
    fn new(capacity: usize) -> Self {
        Arena {
            storage: vec![0u8; capacity],
            offset: 0,
        }
    }

    fn alloc<T>(&mut self, value: T) -> &mut T {
        let align = std::mem::align_of::<T>();
        let size = std::mem::size_of::<T>();

        // Align the offset
        self.offset = (self.offset + align - 1) & !(align - 1);

        assert!(
            self.offset + size <= self.storage.len(),
            "Arena out of memory"
        );

        let ptr = self.storage.as_mut_ptr();
        // SAFETY: We've verified that offset + size fits within storage,
        // and we've aligned offset to T's alignment requirement.
        // The returned reference borrows self, so the arena can't be
        // dropped while the reference exists.
        unsafe {
            let dest = ptr.add(self.offset) as *mut T;
            std::ptr::write(dest, value);
            self.offset += size;
            &mut *dest
        }
    }
}
```

## What unsafe Does NOT Let You Do

I want to be crystal clear about what `unsafe` doesn't change:

- The borrow checker still applies to references (`&T` and `&mut T`)
- Type checking still applies
- Lifetime checking still applies to references
- Pattern matching exhaustiveness still applies
- Trait bounds still apply

`unsafe` only unlocks those five specific superpowers. Everything else stays locked down. I've seen people write `unsafe` blocks thinking they can now cast between arbitrary types freely or ignore lifetime constraints on references — they can't. The compiler will still reject invalid safe code inside an `unsafe` block.

```rust
fn still_checked() {
    let mut x = 5;
    let r1 = &x;
    let r2 = &mut x; // ERROR: still rejected inside unsafe too

    unsafe {
        // unsafe doesn't help here — this is a borrow checker error,
        // not a raw pointer operation
    }
}
```

## Miri: Your unsafe Best Friend

Before we wrap up, I want to mention Miri — an interpreter for Rust's intermediate representation that can detect undefined behavior. If you're writing `unsafe` code, Miri should be part of your workflow.

```bash
rustup component add miri
cargo miri test
cargo miri run
```

Miri catches things like use-after-free, out-of-bounds access, invalid alignments, and violations of Stacked Borrows (Rust's aliasing model). It won't catch everything — it only checks the execution paths your tests actually exercise — but it catches a *lot*.

```rust
#[test]
fn test_arena_basic() {
    let mut arena = Arena::new(1024);
    let x = arena.alloc(42i32);
    assert_eq!(*x, 42);
    *x = 100;
    assert_eq!(*x, 100);
}
// Run with: cargo miri test test_arena_basic
```

## The Mental Model

Here's how I think about `unsafe` after years of using it: it's a scalpel, not a sledgehammer. You use it for precise, well-understood operations, wrap it in a safe API, document your reasoning, and test the hell out of it with Miri.

The rest of this course is about learning to use that scalpel properly — raw pointers, transmute, FFI, and the patterns that keep `unsafe` code sound. We'll start with raw pointers in the next lesson.

If you're nervous about `unsafe`, good. That nervousness will make you careful. But don't let it stop you from learning it. Every serious Rust programmer needs to understand what's happening beneath the safe abstractions. Not because you'll use `unsafe` every day — but because understanding it makes you better at everything else.
