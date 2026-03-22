---
title: "Lesson 5: Building Safe Abstractions Over Unsafe Code — The encapsulation pattern"
author: Atharva Pandey
series: "Rust Unsafe & FFI"
lesson: 5
keywords: [Rust, rust, unsafe, safe abstraction, encapsulation, invariant, module boundary]
tags: [Rust tutorial, rust, unsafe, ffi]
date: '2025-06-20T09:22:00.000Z'
---

The standard library's `Vec<T>` contains over 50 `unsafe` blocks. `HashMap` has even more. Yet you use both every day without thinking about safety — because their public APIs are entirely safe. The `unsafe` is invisible, encapsulated behind type system boundaries that make misuse impossible.

This is the most important pattern in Rust: unsafe internals, safe surface. Master it, and you can build anything.

## The Core Principle

An `unsafe` block means "I've verified the invariants." A safe API means "the type system prevents invariant violations." The goal is to push all the verification into the implementation so that *users* of your code can't break the invariants no matter what they do.

This isn't just good practice — it's the foundation of Rust's entire safety story. If a safe program causes undefined behavior, that's a *soundness bug* in whatever library it used. The blame goes to the library author, not the user. That's the contract.

```rust
// BAD: Users must remember to check bounds
pub unsafe fn get_unchecked(data: &[i32], index: usize) -> i32 {
    *data.as_ptr().add(index)
}

// GOOD: Bounds checking is the library's responsibility
pub fn get(data: &[i32], index: usize) -> Option<i32> {
    if index < data.len() {
        // SAFETY: We just verified index is in bounds.
        Some(unsafe { *data.as_ptr().add(index) })
    } else {
        None
    }
}
```

## The Encapsulation Pattern

Here's the recipe. I've used this dozens of times, and it always follows the same shape:

1. **Define your invariants** — what must always be true about your data structure?
2. **Make fields private** — so external code can't break the invariants
3. **Use `unsafe` internally** — to do the operations that rely on those invariants
4. **Expose safe methods** — that maintain the invariants through every operation
5. **Test under Miri** — to verify you haven't missed anything

Let's build a real example: a sorted, deduplicated array that supports O(log n) lookup.

```rust
/// A sorted, deduplicated collection backed by a contiguous array.
///
/// Invariants:
/// - `data` is always sorted in ascending order
/// - `data` contains no duplicate elements
/// - These invariants enable binary search on the raw buffer
pub struct SortedSet<T: Ord> {
    data: Vec<T>,
}

impl<T: Ord> SortedSet<T> {
    pub fn new() -> Self {
        SortedSet { data: Vec::new() }
    }

    /// Insert a value, maintaining sort order and uniqueness.
    /// Returns true if the value was inserted (wasn't already present).
    pub fn insert(&mut self, value: T) -> bool {
        match self.data.binary_search(&value) {
            Ok(_) => false,  // Already present
            Err(pos) => {
                self.data.insert(pos, value);
                true
            }
        }
    }

    /// Check if a value exists using binary search.
    pub fn contains(&self, value: &T) -> bool {
        self.data.binary_search(value).is_ok()
    }

    /// Get a value by its sorted position without bounds checking.
    /// This is where `unsafe` earns its keep in hot paths.
    pub fn get(&self, index: usize) -> Option<&T> {
        if index < self.data.len() {
            // SAFETY: We verified index < len. Vec guarantees
            // its buffer is valid and initialized up to len.
            Some(unsafe { self.data.get_unchecked(index) })
        } else {
            None
        }
    }

    pub fn len(&self) -> usize {
        self.data.len()
    }

    pub fn is_empty(&self) -> bool {
        self.data.is_empty()
    }

    /// Iterate over elements in sorted order.
    pub fn iter(&self) -> impl Iterator<Item = &T> {
        self.data.iter()
    }
}
```

Notice: the fields are private. Nobody outside this module can touch `data` directly. Every public method maintains the invariants — `insert` uses `binary_search` to find the correct position, and the only `unsafe` is behind a bounds check. Users literally cannot cause UB through this API.

## Private Fields Are Your Safety Boundary

This is the part people miss: `unsafe` code's soundness often depends on *module privacy*. If someone could access your internal fields, they could break your invariants, which would make your `unsafe` code UB.

```rust
mod my_buffer {
    /// A buffer that tracks how many elements are initialized.
    ///
    /// Invariant: elements at indices 0..initialized are valid T values.
    /// Elements at indices initialized..capacity are uninitialized.
    pub struct InitBuffer<T> {
        ptr: std::ptr::NonNull<T>,
        initialized: usize, // MUST be private
        capacity: usize,    // MUST be private
    }

    impl<T> InitBuffer<T> {
        pub fn new(capacity: usize) -> Self {
            let layout = std::alloc::Layout::array::<T>(capacity).unwrap();
            // SAFETY: layout is non-zero (capacity > 0 checked by Layout)
            let ptr = unsafe { std::alloc::alloc(layout) as *mut T };
            let ptr = std::ptr::NonNull::new(ptr)
                .expect("allocation failed");

            InitBuffer {
                ptr,
                initialized: 0,
                capacity,
            }
        }

        pub fn push(&mut self, value: T) -> bool {
            if self.initialized >= self.capacity {
                return false;
            }

            // SAFETY: initialized < capacity, so this slot is
            // within the allocation. The slot is uninitialized,
            // so we use write (not assignment) to avoid dropping garbage.
            unsafe {
                self.ptr.as_ptr().add(self.initialized).write(value);
            }
            self.initialized += 1;
            true
        }

        pub fn get(&self, index: usize) -> Option<&T> {
            if index >= self.initialized {
                return None;
            }
            // SAFETY: index < initialized, so this element has been
            // written via push() and contains a valid T.
            unsafe { Some(&*self.ptr.as_ptr().add(index)) }
        }

        pub fn len(&self) -> usize {
            self.initialized
        }
    }

    impl<T> Drop for InitBuffer<T> {
        fn drop(&mut self) {
            // Drop all initialized elements
            for i in 0..self.initialized {
                // SAFETY: indices 0..initialized contain valid T values.
                unsafe {
                    std::ptr::drop_in_place(self.ptr.as_ptr().add(i));
                }
            }
            // Deallocate the buffer
            if self.capacity > 0 {
                let layout = std::alloc::Layout::array::<T>(self.capacity).unwrap();
                // SAFETY: ptr was allocated with this layout in new()
                unsafe {
                    std::alloc::dealloc(self.ptr.as_ptr() as *mut u8, layout);
                }
            }
        }
    }
}
```

If `initialized` were public, a user could set it to any value — including one larger than the number of elements actually written. Then `get()` would return a reference to uninitialized memory. Game over.

The module boundary is part of the safety argument. Document that.

## The Drop Guarantee

When your type owns heap memory or resources, `Drop` is mandatory. And implementing `Drop` for types with `unsafe` internals requires special care:

```rust
struct OwnedBuffer {
    ptr: *mut u8,
    len: usize,
    cap: usize,
}

impl OwnedBuffer {
    fn new(capacity: usize) -> Self {
        let layout = std::alloc::Layout::from_size_align(capacity, 1).unwrap();
        let ptr = unsafe { std::alloc::alloc(layout) };
        if ptr.is_null() {
            std::alloc::handle_alloc_error(layout);
        }
        OwnedBuffer {
            ptr,
            len: 0,
            cap: capacity,
        }
    }

    fn write(&mut self, data: &[u8]) -> usize {
        let available = self.cap - self.len;
        let to_write = data.len().min(available);

        // SAFETY: ptr + len is within the allocation (len <= cap),
        // and data[..to_write] is valid. Regions don't overlap
        // because data is a separate allocation.
        unsafe {
            std::ptr::copy_nonoverlapping(
                data.as_ptr(),
                self.ptr.add(self.len),
                to_write,
            );
        }
        self.len += to_write;
        to_write
    }

    fn as_slice(&self) -> &[u8] {
        // SAFETY: ptr is valid, aligned (alignment 1), and
        // elements 0..len have been initialized via write().
        unsafe { std::slice::from_raw_parts(self.ptr, self.len) }
    }
}

impl Drop for OwnedBuffer {
    fn drop(&mut self) {
        if self.cap > 0 {
            let layout = std::alloc::Layout::from_size_align(self.cap, 1).unwrap();
            // SAFETY: ptr was allocated with this exact layout in new().
            // No other code frees this pointer because it's private.
            unsafe {
                std::alloc::dealloc(self.ptr, layout);
            }
        }
    }
}

// Prevent accidental copies — we own the allocation
impl Clone for OwnedBuffer {
    fn clone(&self) -> Self {
        let mut new_buf = OwnedBuffer::new(self.cap);
        new_buf.write(self.as_slice());
        new_buf
    }
}
```

Key things I want you to notice:

1. No `Copy` implementation — copying this struct would create two owners of the same allocation, leading to a double-free
2. `Clone` does a deep copy — allocates new memory and copies the data
3. `Drop` deallocates — and it uses the same layout as the original allocation

## Unsafe Traits: Promises About Behavior

Sometimes the safety boundary isn't a struct — it's a trait. `Send` and `Sync` are the classic examples. Implementing them means "I promise this type is safe to use across threads."

```rust
/// A trait for types that can be safely zero-initialized.
///
/// # Safety
/// Implementors must ensure that the all-zeros bit pattern
/// is a valid, fully-initialized value of Self.
unsafe trait SafelyZeroable: Sized {
    fn zeroed() -> Self {
        // SAFETY: Implementor guarantees all-zeros is valid.
        unsafe { std::mem::zeroed() }
    }
}

// Safe to implement for primitive numeric types
unsafe impl SafelyZeroable for u8 {}
unsafe impl SafelyZeroable for u16 {}
unsafe impl SafelyZeroable for u32 {}
unsafe impl SafelyZeroable for u64 {}
unsafe impl SafelyZeroable for i8 {}
unsafe impl SafelyZeroable for i16 {}
unsafe impl SafelyZeroable for i32 {}
unsafe impl SafelyZeroable for i64 {}
unsafe impl SafelyZeroable for f32 {}
unsafe impl SafelyZeroable for f64 {}
unsafe impl SafelyZeroable for usize {}
unsafe impl SafelyZeroable for isize {}

// NOT safe for bool (0 is valid, but the trait is a blanket promise)
// Actually, 0u8 is false, so bool could work — but let's be conservative.

// NOT safe for references (null is not a valid reference)
// NOT safe for String, Vec, Box (null internal pointer)

// Safe for arrays of SafelyZeroable types
unsafe impl<T: SafelyZeroable, const N: usize> SafelyZeroable for [T; N] {}

// Safe for #[repr(C)] structs where all fields are SafelyZeroable
#[repr(C)]
struct Point {
    x: f64,
    y: f64,
}
unsafe impl SafelyZeroable for Point {}

fn demo_zeroed() {
    let p = Point::zeroed();
    assert_eq!(p.x, 0.0);
    assert_eq!(p.y, 0.0);

    let arr = <[u32; 100]>::zeroed();
    assert!(arr.iter().all(|&x| x == 0));
}
```

The `unsafe` on the trait implementation means: "I've verified that my type satisfies the trait's safety contract." If I incorrectly implement `SafelyZeroable` for a type where all-zeros is invalid, any code using `zeroed()` on that type could trigger UB.

## The Audit Trail

When you maintain an unsafe abstraction, you need to be able to audit it. Here's my approach:

### 1. Document Invariants at the Type Level

```rust
/// A pool allocator for fixed-size objects.
///
/// # Safety Invariants
///
/// 1. `storage` is a single contiguous allocation of `cap * slot_size` bytes
/// 2. `free_list` contains only indices in range [0, cap)
/// 3. Each index appears in `free_list` at most once
/// 4. `slot_size >= size_of::<T>()` and `slot_size` is a multiple of `align_of::<T>()`
/// 5. Allocated slots (not in free_list) contain valid, initialized T values
pub struct Pool<T> {
    storage: *mut u8,
    free_list: Vec<usize>,
    cap: usize,
    slot_size: usize,
    _marker: std::marker::PhantomData<T>,
}
```

### 2. Comment Every unsafe Block

```rust
impl<T> Pool<T> {
    pub fn alloc(&mut self, value: T) -> Option<PoolHandle> {
        let index = self.free_list.pop()?;

        let offset = index * self.slot_size;
        // SAFETY: `index` came from `free_list`, which only contains
        // valid indices (invariant 2). `offset` is therefore within
        // the allocation (invariant 1). The slot is currently free,
        // so writing to it doesn't alias any existing references.
        // We use `write` because the slot is uninitialized.
        unsafe {
            let ptr = self.storage.add(offset) as *mut T;
            ptr.write(value);
        }

        Some(PoolHandle { index })
    }
}
```

### 3. Test Exhaustively Under Miri

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pool_alloc_and_free() {
        let mut pool: Pool<String> = Pool::new(4);

        let h1 = pool.alloc(String::from("hello")).unwrap();
        let h2 = pool.alloc(String::from("world")).unwrap();

        assert_eq!(pool.get(&h1).unwrap(), "hello");
        assert_eq!(pool.get(&h2).unwrap(), "world");

        pool.free(h1);
        // h1's slot is now back in the free list

        let h3 = pool.alloc(String::from("reused")).unwrap();
        assert_eq!(pool.get(&h3).unwrap(), "reused");
    }

    #[test]
    fn pool_exhaustion() {
        let mut pool: Pool<u32> = Pool::new(2);
        assert!(pool.alloc(1).is_some());
        assert!(pool.alloc(2).is_some());
        assert!(pool.alloc(3).is_none()); // Full
    }

    // Run with: cargo miri test
}
```

## A Complete Example: Safe String Interning

Let's put it all together with a string interner — a data structure that deduplicates strings and returns stable references:

```rust
use std::collections::HashMap;

/// An interner that deduplicates strings and provides
/// lightweight handles for O(1) comparison.
///
/// # Invariants
/// - Every `InternedStr` handle returned by `intern()` is valid
///   for the lifetime of the `Interner`
/// - The backing `Vec<String>` is append-only (never removes elements)
/// - `map` always maps to valid indices in `strings`
pub struct Interner {
    strings: Vec<String>,
    map: HashMap<String, u32>,
}

#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct InternedStr(u32);

impl Interner {
    pub fn new() -> Self {
        Interner {
            strings: Vec::new(),
            map: HashMap::new(),
        }
    }

    pub fn intern(&mut self, s: &str) -> InternedStr {
        if let Some(&id) = self.map.get(s) {
            return InternedStr(id);
        }

        let id = self.strings.len() as u32;
        self.strings.push(s.to_owned());
        self.map.insert(s.to_owned(), id);
        InternedStr(id)
    }

    pub fn resolve(&self, handle: InternedStr) -> &str {
        // SAFETY argument (no actual unsafe needed here, but
        // illustrating the invariant): handle.0 was produced by
        // intern(), which only creates handles for valid indices.
        // The strings vec is append-only, so the index stays valid.
        &self.strings[handle.0 as usize]
    }

    pub fn len(&self) -> usize {
        self.strings.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_interning() {
        let mut interner = Interner::new();

        let a = interner.intern("hello");
        let b = interner.intern("world");
        let c = interner.intern("hello"); // Same as a

        assert_eq!(a, c);          // Same handle
        assert_ne!(a, b);          // Different handles
        assert_eq!(interner.resolve(a), "hello");
        assert_eq!(interner.resolve(b), "world");
        assert_eq!(interner.len(), 2); // Only 2 unique strings
    }
}
```

This particular example doesn't even need `unsafe` — it demonstrates that sometimes the invariant-maintenance pattern works with safe code alone. The insight is the same: private fields, append-only semantics, handles that can only be created through the API.

## Rules I Follow

After building a bunch of these, here's what I've learned:

1. **Minimize the unsafe surface.** The less `unsafe` code you have, the less you need to audit. Concentrate it in small, well-tested functions.

2. **Make invariant violations impossible through types.** If an index must be in-bounds, use a handle type that can only be created when bounds are verified. If a value must be initialized, use a type state pattern.

3. **Never expose raw pointers in your public API.** If your users need to see a raw pointer, you've failed at abstraction.

4. **The module boundary is a safety boundary.** Private fields aren't just encapsulation for cleanliness — they're load-bearing for soundness.

5. **If you can't write the SAFETY comment, you can't write the code.** This isn't a style rule. If you can't articulate why the invariants hold, you don't understand the code well enough to ship it.

Next, we cross the language boundary. FFI is where `unsafe` stops being optional and starts being the whole point.
