---
title: "Lesson 25: When to Reach for unsafe — And when not to"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 25
keywords: [Rust, rust, idiomatic, unsafe, safety, FFI, raw pointers, soundness]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-05-22T17:08:00.000Z'
---

This is the lesson I almost didn't write. Not because `unsafe` is hard to explain, but because most Rust developers will never need it — and I don't want to encourage reaching for it prematurely. I've seen codebases riddled with `unsafe` blocks because the developer didn't know the safe alternative existed. That's the worst outcome.

But `unsafe` exists for a reason. Understanding when it's appropriate — and more importantly, when it isn't — is part of being a competent Rust developer.

---

## What unsafe Actually Means

`unsafe` doesn't mean "this code is dangerous." It means "the compiler can't verify the safety guarantees here — the programmer is taking responsibility."

An `unsafe` block lets you do five things that safe Rust doesn't allow:

1. **Dereference raw pointers** (`*const T`, `*mut T`)
2. **Call unsafe functions** (including FFI functions)
3. **Access or modify mutable static variables**
4. **Implement unsafe traits**
5. **Access fields of unions**

That's the complete list. Everything else — ownership, borrowing, lifetimes, type checking — still applies inside `unsafe` blocks.

```rust
fn main() {
    let x = 42;
    let ptr: *const i32 = &x;

    // Creating a raw pointer is safe
    // Dereferencing it requires unsafe
    unsafe {
        println!("Value: {}", *ptr);
    }
}
```

---

## When unsafe Is Appropriate

### 1. FFI — Calling C libraries

This is the most common legitimate use of `unsafe`. C functions have no Rust-compatible safety guarantees.

```rust
// Binding to a C function
extern "C" {
    fn strlen(s: *const std::os::raw::c_char) -> usize;
}

fn safe_strlen(s: &str) -> usize {
    let c_string = std::ffi::CString::new(s).expect("string contains null byte");
    unsafe { strlen(c_string.as_ptr()) }
}

fn main() {
    println!("Length: {}", safe_strlen("hello"));
}
```

The pattern: create a safe wrapper around the `unsafe` FFI call. The wrapper validates inputs, handles edge cases, and presents a safe API. Callers never see `unsafe`.

### 2. Performance-critical code where the safe version is provably slower

Sometimes the borrow checker prevents an optimization that you *know* is safe. But this is rare — and you should prove it with benchmarks.

```rust
/// Splits a mutable slice into two non-overlapping parts.
/// This already exists as `split_at_mut`, but here's how it works:
fn split_at_mut_manual<T>(slice: &mut [T], mid: usize) -> (&mut [T], &mut [T]) {
    assert!(mid <= slice.len());

    let ptr = slice.as_mut_ptr();
    let len = slice.len();

    unsafe {
        (
            std::slice::from_raw_parts_mut(ptr, mid),
            std::slice::from_raw_parts_mut(ptr.add(mid), len - mid),
        )
    }
}

fn main() {
    let mut data = vec![1, 2, 3, 4, 5];
    let (left, right) = split_at_mut_manual(&mut data, 3);
    left[0] = 10;
    right[0] = 40;
    println!("Left: {:?}, Right: {:?}", left, right);
}
```

This is safe because the two slices don't overlap — we've proven it by construction. The compiler can't prove it, so `unsafe` is needed. But note: `split_at_mut` already exists in the standard library. Don't rewrite standard library functions.

### 3. Building safe abstractions over inherently unsafe operations

The standard library itself uses `unsafe` internally to implement safe abstractions like `Vec`, `String`, `HashMap`, `Arc`, and `Mutex`. If you're building a similar data structure, you might need `unsafe`.

```rust
/// A simple fixed-size ring buffer.
pub struct RingBuffer<T> {
    data: Vec<Option<T>>,
    head: usize,
    tail: usize,
    count: usize,
}

impl<T> RingBuffer<T> {
    pub fn new(capacity: usize) -> Self {
        let mut data = Vec::with_capacity(capacity);
        for _ in 0..capacity {
            data.push(None);
        }
        RingBuffer { data, head: 0, tail: 0, count: 0 }
    }

    pub fn push(&mut self, item: T) -> Option<T> {
        let old = self.data[self.tail].take();
        self.data[self.tail] = Some(item);
        self.tail = (self.tail + 1) % self.data.len();
        if self.count < self.data.len() {
            self.count += 1;
        } else {
            self.head = (self.head + 1) % self.data.len();
        }
        old
    }

    pub fn pop(&mut self) -> Option<T> {
        if self.count == 0 {
            return None;
        }
        let item = self.data[self.head].take();
        self.head = (self.head + 1) % self.data.len();
        self.count -= 1;
        item
    }
}

fn main() {
    let mut rb = RingBuffer::new(3);
    rb.push(1);
    rb.push(2);
    rb.push(3);
    rb.push(4); // overwrites 1
    println!("{:?}", rb.pop()); // Some(2)
    println!("{:?}", rb.pop()); // Some(3)
    println!("{:?}", rb.pop()); // Some(4)
}
```

Look — no `unsafe` at all! A ring buffer doesn't need it when you use `Vec<Option<T>>`. The point: always try the safe approach first. You'd be surprised how often it works.

---

## When unsafe Is NOT Appropriate

### "The borrow checker won't let me"

99% of the time, the borrow checker is telling you your design has a bug. Restructure the code instead of reaching for `unsafe`.

```rust
// BAD: using unsafe to work around the borrow checker
fn bad_example(data: &mut Vec<i32>) {
    // "I need to iterate and modify simultaneously"
    // Don't use unsafe raw pointers here!

    // GOOD: collect indices first, then modify
    let indices: Vec<usize> = data.iter()
        .enumerate()
        .filter(|(_, &v)| v > 10)
        .map(|(i, _)| i)
        .collect();

    for i in indices {
        data[i] *= 2;
    }
}

fn main() {
    let mut v = vec![5, 15, 8, 20, 3];
    bad_example(&mut v);
    println!("{:?}", v); // [5, 30, 8, 40, 3]
}
```

### "I want to avoid the bounds check"

```rust
fn sum_unchecked(data: &[i32]) -> i32 {
    // DON'T do this:
    // let mut sum = 0;
    // for i in 0..data.len() {
    //     sum += unsafe { *data.get_unchecked(i) };
    // }

    // DO this:
    data.iter().sum()
    // The compiler eliminates the bounds check in release mode anyway!
}
```

The compiler is smarter than you think. In release mode with optimizations, bounds checks in loops over known ranges are typically eliminated. Don't use `unsafe` to avoid a check that the optimizer already removes.

### "I want shared mutable state"

Use `Cell`, `RefCell`, `Mutex`, or `RwLock` — not `unsafe`.

```rust
use std::cell::RefCell;

struct Counter {
    value: RefCell<i32>,
}

impl Counter {
    fn new() -> Self {
        Counter { value: RefCell::new(0) }
    }

    fn increment(&self) {
        *self.value.borrow_mut() += 1;
    }

    fn get(&self) -> i32 {
        *self.value.borrow()
    }
}

fn main() {
    let counter = Counter::new();
    counter.increment();
    counter.increment();
    println!("Count: {}", counter.get());
}
```

---

## The unsafe Boundary Pattern

When you *do* use `unsafe`, contain it behind a safe API. This is called the "unsafe boundary" or "safety boundary" pattern.

```rust
pub struct AlignedBuffer {
    ptr: *mut u8,
    len: usize,
    capacity: usize,
}

impl AlignedBuffer {
    /// Creates a new buffer aligned to `align` bytes.
    ///
    /// # Panics
    ///
    /// Panics if `align` is not a power of two or if allocation fails.
    pub fn new(capacity: usize, align: usize) -> Self {
        assert!(align.is_power_of_two(), "alignment must be power of two");
        assert!(capacity > 0, "capacity must be non-zero");

        let layout = std::alloc::Layout::from_size_align(capacity, align)
            .expect("invalid layout");

        let ptr = unsafe { std::alloc::alloc_zeroed(layout) };
        if ptr.is_null() {
            std::alloc::handle_alloc_error(layout);
        }

        AlignedBuffer { ptr, len: 0, capacity }
    }

    /// Returns the contents as a byte slice.
    pub fn as_slice(&self) -> &[u8] {
        unsafe { std::slice::from_raw_parts(self.ptr, self.len) }
    }

    /// Writes data to the buffer.
    ///
    /// # Panics
    ///
    /// Panics if the data exceeds the buffer capacity.
    pub fn write(&mut self, data: &[u8]) {
        assert!(data.len() <= self.capacity - self.len, "buffer overflow");
        unsafe {
            std::ptr::copy_nonoverlapping(data.as_ptr(), self.ptr.add(self.len), data.len());
        }
        self.len += data.len();
    }
}

impl Drop for AlignedBuffer {
    fn drop(&mut self) {
        let layout = std::alloc::Layout::from_size_align(self.capacity, 1)
            .expect("invalid layout");
        unsafe { std::alloc::dealloc(self.ptr, layout); }
    }
}

fn main() {
    let mut buf = AlignedBuffer::new(1024, 64);
    buf.write(b"hello world");
    println!("{:?}", std::str::from_utf8(buf.as_slice()));
}
```

The `unsafe` code is concentrated in the implementation. The public API (`new`, `as_slice`, `write`) is entirely safe. Users of `AlignedBuffer` never write `unsafe`. They can't misuse the buffer because the safe API prevents it.

---

## Documenting Safety

Every `unsafe` block should have a `// SAFETY:` comment explaining why it's sound:

```rust
fn get_unchecked_example(data: &[i32], index: usize) -> i32 {
    assert!(index < data.len());
    // SAFETY: We just verified that index < data.len(),
    // so this access is within bounds.
    unsafe { *data.get_unchecked(index) }
}
```

And every `unsafe fn` should have a `# Safety` section in its doc comment:

```rust
/// Converts raw parts into a `Vec`.
///
/// # Safety
///
/// - `ptr` must have been allocated by the global allocator.
/// - `length` must be <= `capacity`.
/// - The first `length` elements must be properly initialized.
/// - `capacity` must be the capacity the pointer was allocated with.
pub unsafe fn from_raw_parts(ptr: *mut i32, length: usize, capacity: usize) -> Vec<i32> {
    Vec::from_raw_parts(ptr, length, capacity)
}
```

---

## The unsafe Audit Checklist

Before writing `unsafe`, ask yourself:

1. **Is there a safe alternative?** Check the standard library, check popular crates. Almost certainly someone has already wrapped this in a safe API.
2. **Can I restructure the code?** Often a design change eliminates the need for `unsafe`.
3. **Can I use `Cell`/`RefCell`/`Mutex`?** Interior mutability usually beats raw pointers.
4. **Is this actually performance-critical?** Have you profiled? Don't optimize prematurely with `unsafe`.
5. **Can I minimize the unsafe surface area?** Keep `unsafe` blocks as small as possible.
6. **Have I documented the safety invariants?** Every `unsafe` block needs a `// SAFETY:` comment.
7. **Have I tested thoroughly?** Run with `miri` (`cargo +nightly miri test`) to detect undefined behavior.

---

## Using Miri to Validate unsafe Code

Miri is Rust's undefined behavior detector. It interprets your program and catches issues that the compiler can't:

```bash
# Install miri
rustup +nightly component add miri

# Run tests under miri
cargo +nightly miri test
```

Miri catches:
- Use after free
- Out of bounds access
- Invalid pointer alignment
- Data races
- Violation of aliasing rules

If your `unsafe` code passes Miri, you can be reasonably confident it's sound. Not certain — Miri doesn't catch everything — but much more confident than "it seems to work."

---

## Key Takeaways

- `unsafe` means "I'm taking responsibility for safety guarantees the compiler can't verify."
- Legitimate uses: FFI, performance-critical code with proof, building safe abstractions.
- Illegitimate uses: working around the borrow checker, avoiding bounds checks the optimizer removes, shared mutable state (use `Cell`/`Mutex`).
- Contain `unsafe` behind safe APIs — the "unsafe boundary" pattern.
- Document every `unsafe` block with a `// SAFETY:` comment explaining why it's sound.
- Use Miri (`cargo +nightly miri test`) to detect undefined behavior.
- When in doubt, don't use `unsafe`. There's almost always a safe way.
