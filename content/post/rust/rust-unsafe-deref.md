---
title: "Lesson 3: Dereferencing Raw Pointers Safely — The patterns that work"
author: Atharva Pandey
series: "Rust Unsafe & FFI"
lesson: 3
keywords: [Rust, rust, unsafe, dereference, raw pointers, MaybeUninit, safety patterns]
tags: [Rust tutorial, rust, unsafe, ffi]
date: '2025-06-16T10:45:00.000Z'
---

A colleague once showed me a bug that took them three days to find. Their `unsafe` code dereferenced a pointer that was valid when created but dangling by the time it was used — a classic lifetime mismatch. The fix was two lines. The debugging was seventy-two hours. That ratio is why this lesson exists.

Dereferencing raw pointers is the most common `unsafe` operation you'll encounter, and getting it right means following specific patterns. Not guidelines — patterns. Repeatable, auditable approaches that make your `unsafe` code reviewable.

## The Preconditions Checklist

Every time you dereference a raw pointer, you need to verify five things. Every. Single. Time.

1. **Non-null** — the pointer isn't `0x0`
2. **Aligned** — the address is a multiple of `align_of::<T>()`
3. **Points to valid memory** — the memory is allocated and accessible
4. **Points to initialized data** — the bytes represent a valid `T`
5. **No aliasing violations** — you're not creating conflicting references

Miss any one of these and you've got undefined behavior. Let me show you how to check each one.

```rust
/// Safely reads a value through a raw pointer, checking all preconditions.
///
/// # Safety
/// Caller must ensure:
/// - `ptr` points to allocated memory that won't be freed during this call
/// - The memory at `ptr` contains a valid, initialized T
/// - No mutable references to the same memory exist
unsafe fn checked_read<T: Copy>(ptr: *const T) -> Option<T> {
    // Check 1: Non-null
    if ptr.is_null() {
        return None;
    }

    // Check 2: Alignment
    if ptr.align_offset(std::mem::align_of::<T>()) != 0 {
        return None;
    }

    // Checks 3, 4, 5: These can't be verified at runtime in general.
    // They're part of the safety contract — the caller must guarantee them.
    // That's why the function is still `unsafe`.

    // SAFETY: Caller guarantees validity, initialization, and no aliasing.
    // We've verified non-null and alignment.
    Some(*ptr)
}
```

Notice the function is *still* `unsafe` even with runtime checks. That's because some preconditions are fundamentally unverifiable at runtime — you can't check whether memory is still allocated or whether someone else holds a `&mut` to it. Those remain part of the trust contract.

## Pattern 1: Borrow-Derived Pointers

The safest raw pointer pattern is when the pointer comes from a reference you control. The reference already guarantees validity, alignment, and initialization:

```rust
fn borrow_derived() {
    let mut data = vec![1, 2, 3, 4, 5];

    // The pointer is derived from a valid &mut [i32],
    // so it inherits all the guarantees.
    let ptr = data.as_mut_ptr();
    let len = data.len();

    // SAFETY: ptr comes from data.as_mut_ptr(), so it's valid,
    // aligned, and initialized. We stay within bounds (i < len).
    // No other references to data exist because we took &mut.
    unsafe {
        for i in 0..len {
            *ptr.add(i) *= 2;
        }
    }

    assert_eq!(data, vec![2, 4, 6, 8, 10]);
}
```

The critical thing here is the temporal aspect — the pointer is only valid as long as `data` isn't moved, resized, or dropped. If you push to the `Vec` between creating the pointer and using it, the `Vec` might reallocate and your pointer dangles.

```rust
fn dangling_danger() {
    let mut data = vec![1, 2, 3];
    let ptr = data.as_ptr();

    data.push(4); // MIGHT reallocate, invalidating ptr

    // DON'T DO THIS — ptr might be dangling now
    // unsafe { println!("{}", *ptr); }
}
```

## Pattern 2: Bounds-Checked Indexing

When you need raw pointer access for performance but want safety guarantees, check bounds first:

```rust
struct FastBuffer {
    data: Vec<f64>,
}

impl FastBuffer {
    fn get(&self, index: usize) -> Option<f64> {
        if index >= self.data.len() {
            return None;
        }
        // SAFETY: We've verified index < len, so ptr.add(index)
        // is within the allocation. The Vec guarantees its buffer
        // is valid, aligned, and initialized up to len.
        unsafe { Some(*self.data.as_ptr().add(index)) }
    }

    /// Unchecked version for hot loops where bounds are
    /// verified externally.
    ///
    /// # Safety
    /// Caller must ensure index < self.data.len()
    unsafe fn get_unchecked(&self, index: usize) -> f64 {
        debug_assert!(index < self.data.len());
        // SAFETY: Caller ensures index is in bounds.
        *self.data.as_ptr().add(index)
    }

    /// Process a range with a single bounds check instead of one per element
    fn process_range(&self, start: usize, end: usize) -> f64 {
        assert!(start <= end && end <= self.data.len());
        let mut sum = 0.0;
        let ptr = self.data.as_ptr();

        // SAFETY: We've verified the entire range [start, end) is
        // within bounds. The Vec's buffer is valid and initialized.
        unsafe {
            for i in start..end {
                sum += *ptr.add(i);
            }
        }
        sum
    }
}
```

The `process_range` method is the key pattern here — one bounds check for the whole range, then unchecked access inside the loop. This is exactly what the standard library's slice iterators do internally.

## Pattern 3: MaybeUninit for Fresh Allocations

When you allocate memory but haven't written to it yet, `MaybeUninit<T>` is your friend. It explicitly represents "this memory might not be initialized," which prevents the compiler from making invalid assumptions:

```rust
use std::mem::MaybeUninit;

fn build_array() -> [i32; 10] {
    // Create an uninitialized array — no UB because MaybeUninit
    // doesn't require initialization
    let mut arr: [MaybeUninit<i32>; 10] = unsafe {
        MaybeUninit::uninit().assume_init()
    };
    // Note: the above is safe because [MaybeUninit<T>; N] is valid
    // even when uninitialized — that's the whole point of MaybeUninit.

    // Initialize each element
    for i in 0..10 {
        arr[i] = MaybeUninit::new(i as i32 * i as i32);
    }

    // SAFETY: All elements have been initialized via MaybeUninit::new
    unsafe {
        // Transmute [MaybeUninit<i32>; 10] → [i32; 10]
        // This is safe because MaybeUninit<T> has the same layout as T
        std::mem::transmute(arr)
    }
}

// A more modern approach using assume_init_array (nightly) or
// doing it element by element:
fn build_array_stable() -> [i32; 10] {
    let mut arr = [MaybeUninit::<i32>::uninit(); 10];

    for i in 0..10 {
        arr[i].write(i as i32 * i as i32);
    }

    // SAFETY: Every element has been initialized
    unsafe { arr.map(|x| x.assume_init()) }
}
```

Without `MaybeUninit`, reading uninitialized memory is instant UB — even for integer types. The compiler is allowed to assume integers are initialized and will optimize based on that assumption. `MaybeUninit` tells the compiler "don't assume anything about this memory."

## Pattern 4: Pointer-to-Reference Conversion

Sometimes you receive a raw pointer (from FFI, from a C callback, from a `Box::into_raw`) and need to work with it as a Rust reference:

```rust
/// Convert a raw pointer back to a reference.
///
/// # Safety
/// - ptr must be non-null and properly aligned
/// - ptr must point to a valid, initialized T
/// - The returned reference must not outlive the pointed-to data
/// - No mutable references to the same data may exist
unsafe fn ptr_to_ref<'a, T>(ptr: *const T) -> Option<&'a T> {
    // as_ref() handles the null check for us
    ptr.as_ref()
}

/// Mutable version
///
/// # Safety
/// Same as above, plus:
/// - No other references (mutable or shared) to the same data may exist
unsafe fn ptr_to_mut<'a, T>(ptr: *mut T) -> Option<&'a mut T> {
    ptr.as_mut()
}

// Real-world example: recovering a Box from a raw pointer
fn round_trip_box() {
    let original = Box::new(String::from("hello, unsafe world"));
    let ptr = Box::into_raw(original);
    // original is consumed — the Box no longer exists,
    // but the heap allocation is still alive

    // ... do things with ptr ...

    // SAFETY: ptr came from Box::into_raw, so it's valid,
    // aligned, and points to an initialized String.
    // No other references to this data exist.
    let recovered = unsafe { Box::from_raw(ptr) };
    println!("{}", recovered);
    // recovered is dropped here, freeing the memory
}
```

The `Box::into_raw` / `Box::from_raw` pattern is extremely common in FFI. You'll see it whenever you need to pass Rust-owned data through a C callback — you convert to a raw pointer, pass it as a `void*`, and reconstruct the Box on the other side.

## Pattern 5: Slice Reconstruction

Building a slice from a raw pointer and length is something you'll do constantly in FFI:

```rust
use std::slice;

/// Reconstruct a slice from a C-style pointer + length pair.
///
/// # Safety
/// - ptr must be non-null and properly aligned for T
/// - ptr must point to len consecutive initialized T values
/// - The total size (len * size_of::<T>()) must not exceed isize::MAX
/// - The memory must not be mutated through other pointers for the
///   lifetime of the returned slice
unsafe fn make_slice<'a, T>(ptr: *const T, len: usize) -> &'a [T] {
    debug_assert!(!ptr.is_null());
    debug_assert!(ptr.align_offset(std::mem::align_of::<T>()) == 0);
    debug_assert!(
        len.checked_mul(std::mem::size_of::<T>())
            .map(|size| size <= isize::MAX as usize)
            .unwrap_or(false)
    );

    // SAFETY: Caller guarantees all preconditions.
    // Debug asserts catch violations in testing.
    slice::from_raw_parts(ptr, len)
}

// Using it with FFI data:
fn process_c_array(data: *const f32, count: usize) -> f32 {
    if data.is_null() || count == 0 {
        return 0.0;
    }
    // SAFETY: Caller guarantees data points to count valid f32 values.
    let slice = unsafe { slice::from_raw_parts(data, count) };

    // Now we're back in safe Rust land
    slice.iter().sum()
}
```

The `debug_assert!` calls are critical — they cost nothing in release builds but catch violations during testing. I add them to every `unsafe` function that takes raw pointers.

## Pattern 6: The NonNull Wrapper

`std::ptr::NonNull<T>` is a raw pointer that's guaranteed non-null. It's covariant in `T` (unlike `*mut T`, which is invariant) and is the foundation of most unsafe data structures in the standard library:

```rust
use std::ptr::NonNull;

struct Node<T> {
    value: T,
    next: Option<NonNull<Node<T>>>,
}

struct SimpleList<T> {
    head: Option<NonNull<Node<T>>>,
    len: usize,
}

impl<T> SimpleList<T> {
    fn new() -> Self {
        SimpleList { head: None, len: 0 }
    }

    fn push_front(&mut self, value: T) {
        let node = Box::new(Node {
            value,
            next: self.head,
        });
        // SAFETY: Box::into_raw returns a non-null, aligned,
        // valid pointer to an initialized Node<T>.
        self.head = Some(unsafe { NonNull::new_unchecked(Box::into_raw(node)) });
        self.len += 1;
    }

    fn pop_front(&mut self) -> Option<T> {
        self.head.map(|node_ptr| {
            // SAFETY: node_ptr came from Box::into_raw in push_front,
            // so it's valid and initialized. We're the only owner.
            let node = unsafe { Box::from_raw(node_ptr.as_ptr()) };
            self.head = node.next;
            self.len -= 1;
            node.value
        })
    }
}

impl<T> Drop for SimpleList<T> {
    fn drop(&mut self) {
        while self.pop_front().is_some() {}
    }
}
```

Why `NonNull` instead of `*mut T`? Three reasons:
1. Null checks are a class of bugs you can eliminate at the type level
2. `Option<NonNull<T>>` is the same size as `*mut T` due to niche optimization — Option uses the null state as `None`
3. It's covariant, which means `NonNull<&'long T>` can be used where `NonNull<&'short T>` is expected, matching reference semantics

## The Debug Assert Strategy

I can't stress this enough — `debug_assert!` is your primary defense when writing `unsafe` code. In debug/test builds, they catch violations. In release builds, they're compiled out entirely — zero cost.

```rust
unsafe fn copy_elements<T: Copy>(
    src: *const T,
    dst: *mut T,
    count: usize,
) {
    debug_assert!(!src.is_null(), "source pointer is null");
    debug_assert!(!dst.is_null(), "destination pointer is null");
    debug_assert!(
        src.align_offset(std::mem::align_of::<T>()) == 0,
        "source pointer is misaligned"
    );
    debug_assert!(
        dst.align_offset(std::mem::align_of::<T>()) == 0,
        "destination pointer is misaligned"
    );

    // Check for overlap — if they overlap, use copy instead
    // of copy_nonoverlapping
    let src_range = src as usize..src as usize + count * std::mem::size_of::<T>();
    let dst_start = dst as usize;
    debug_assert!(
        !src_range.contains(&dst_start),
        "source and destination overlap — use ptr::copy instead"
    );

    // SAFETY: All preconditions verified by caller (debug_assert in dev).
    std::ptr::copy_nonoverlapping(src, dst, count);
}
```

My rule: every assumption about a raw pointer gets a `debug_assert!`. Even the obvious ones. Especially the obvious ones — those are the ones you'll forget to check three months from now when you modify the calling code.

## Testing with Miri

Every `unsafe` function needs tests, and those tests need to run under Miri. Here's the workflow:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_fast_buffer_in_bounds() {
        let buf = FastBuffer {
            data: vec![1.0, 2.0, 3.0],
        };
        assert_eq!(buf.get(0), Some(1.0));
        assert_eq!(buf.get(2), Some(3.0));
        assert_eq!(buf.get(3), None);
    }

    #[test]
    fn test_fast_buffer_range() {
        let buf = FastBuffer {
            data: vec![1.0, 2.0, 3.0, 4.0],
        };
        assert_eq!(buf.process_range(1, 3), 5.0);
    }

    #[test]
    fn test_simple_list() {
        let mut list = SimpleList::new();
        list.push_front(3);
        list.push_front(2);
        list.push_front(1);

        assert_eq!(list.pop_front(), Some(1));
        assert_eq!(list.pop_front(), Some(2));
        assert_eq!(list.pop_front(), Some(3));
        assert_eq!(list.pop_front(), None);
    }

    #[test]
    fn test_build_array() {
        let arr = build_array();
        assert_eq!(arr, [0, 1, 4, 9, 16, 25, 36, 49, 64, 81]);
    }
}
```

```bash
# Run normally
cargo test

# Run under Miri to detect UB
cargo miri test
```

If Miri reports a violation, don't ignore it. Don't say "well it works in practice." Fix it. Miri catches real bugs that only manifest under specific compiler optimizations or on specific platforms.

## The Golden Rule

Every raw pointer dereference should be justifiable in a code review. If you can't explain why each of the five preconditions holds — with reference to specific code that establishes those conditions — you shouldn't be dereferencing that pointer yet.

Write the SAFETY comment. Add the debug asserts. Test under Miri. These aren't optional steps — they're the difference between `unsafe` code that works and `unsafe` code that works *until it doesn't*.

Next up: `transmute` — the most powerful and dangerous tool in Rust's `unsafe` arsenal.
