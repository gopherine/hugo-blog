---
title: "Lesson 2: Raw Pointers — *const T, *mut T and when you need them"
author: Atharva Pandey
series: "Rust Unsafe & FFI"
lesson: 2
keywords: [Rust, rust, unsafe, raw pointers, const T, mut T, pointer arithmetic]
tags: [Rust tutorial, rust, unsafe, ffi]
date: '2025-06-14T14:17:00.000Z'
---

The first time I used raw pointers in Rust, I was porting a ring buffer from C. I'd written ring buffers in C a dozen times — head pointer, tail pointer, wrap around, done. In Rust, the borrow checker wanted nothing to do with my two mutable pointers into the same buffer. That's when I learned what raw pointers are actually for.

## References vs Raw Pointers

Rust references (`&T` and `&mut T`) come with guarantees enforced by the compiler:

- They're never null
- They're always aligned
- They always point to valid, initialized data
- `&mut T` is always exclusive — no other reference to the same data exists

Raw pointers (`*const T` and `*mut T`) have *none* of these guarantees. They're just addresses. They might be null. They might be dangling. They might be misaligned. The compiler doesn't care — it's on you.

```rust
fn main() {
    let x = 42i32;

    // Reference: compiler guarantees validity
    let ref_x: &i32 = &x;

    // Raw pointer: no guarantees, just an address
    let ptr_x: *const i32 = &x as *const i32;

    // You can also get a raw pointer directly
    let ptr_x2: *const i32 = &x;  // Coercion, no cast needed

    println!("ref: {}", ref_x);       // Fine
    // println!("ptr: {}", *ptr_x);   // Won't compile without unsafe
    unsafe {
        println!("ptr: {}", *ptr_x);  // Now it works
    }
}
```

Key insight: creating a raw pointer is safe. The pointer is just a number — an address in memory. It's *dereferencing* it (reading or writing through it) that requires `unsafe`, because that's when you're making claims about what lives at that address.

## Creating Raw Pointers

There are several ways to get raw pointers, and it matters which one you use.

### From References

The most common way. You take a reference and convert it to a raw pointer:

```rust
let mut value = 10;

// From shared reference → *const T
let const_ptr: *const i32 = &value;

// From mutable reference → *mut T
let mut_ptr: *mut i32 = &mut value;

// You can also cast *const T to *mut T (but be careful)
let also_mut: *mut i32 = const_ptr as *mut i32;
// ^ Legal to create, but writing through this when the
//   original data isn't mutable is UB
```

When you create a raw pointer from a reference, the pointer is valid for at least as long as the reference would have been. But the compiler stops tracking that for you — it's your job now.

### From Addresses

Sometimes you need a pointer to a specific memory address. Embedded programming, memory-mapped I/O, that sort of thing:

```rust
// Pointer to a specific address (common in embedded)
let mmio_register = 0x4000_0000 as *mut u32;

// Null pointer
let null_ptr: *const i32 = std::ptr::null();
let null_mut_ptr: *mut i32 = std::ptr::null_mut();
```

### From Allocations

When you're doing manual memory management:

```rust
use std::alloc::{alloc, dealloc, Layout};

fn manual_allocation() {
    let layout = Layout::new::<[u32; 100]>();

    unsafe {
        let ptr = alloc(layout) as *mut u32;
        if ptr.is_null() {
            panic!("Allocation failed");
        }

        // Write values
        for i in 0..100 {
            ptr.add(i).write(i as u32);
        }

        // Read them back
        for i in 0..100 {
            assert_eq!(ptr.add(i).read(), i as u32);
        }

        // Don't forget to clean up
        dealloc(ptr as *mut u8, layout);
    }
}
```

## Pointer Arithmetic

If you've done C, pointer arithmetic will feel familiar — but Rust's API is more explicit, which I actually prefer. No implicit scaling by element size, no accidentally walking off the end of an array.

```rust
fn pointer_arithmetic_demo() {
    let data = [10, 20, 30, 40, 50];
    let base: *const i32 = data.as_ptr();

    unsafe {
        // .add(n) moves forward by n elements (not bytes!)
        assert_eq!(*base.add(0), 10);
        assert_eq!(*base.add(2), 30);
        assert_eq!(*base.add(4), 50);

        // .sub(n) moves backward
        let end = base.add(4);
        assert_eq!(*end.sub(2), 30);

        // .offset(n) takes an isize — can go forward or back
        assert_eq!(*base.offset(3), 40);
        assert_eq!(*end.offset(-1), 40);

        // .byte_add(n) moves by n bytes, not elements
        // Useful when element size isn't what you want
        let byte_ptr = base as *const u8;
        let third_element = byte_ptr.byte_add(8) as *const i32; // 2 * 4 bytes
        assert_eq!(*third_element, 30);
    }
}
```

The `add` and `sub` methods have a critical requirement: the resulting pointer must be within the same allocated object (or one past the end). Going out of bounds is undefined behavior, even if you never dereference the result. This trips people up — in C, the pointer itself is just a number, but Rust's provenance model is stricter.

```rust
// This is technically UB even though we never dereference it:
let arr = [1, 2, 3];
let ptr = arr.as_ptr();
unsafe {
    let way_out = ptr.add(1000); // UB: way past the allocation
    // Even if we never read *way_out, creating this pointer
    // with .add() violates the rules
}

// Use wrapping_add if you need arithmetic without UB:
let safe_ish = ptr.wrapping_add(1000);
// ^ Not UB to create, but still UB to dereference
```

## Reading and Writing Through Pointers

There's more to it than just `*ptr`. The standard library gives you `read`, `write`, `copy`, and friends — and they handle edge cases that plain dereferencing doesn't.

```rust
use std::ptr;

fn read_write_patterns() {
    let mut buffer = [0u8; 32];
    let ptr = buffer.as_mut_ptr();

    unsafe {
        // ptr::write — writes a value without reading the old one
        // Important for uninitialized memory!
        ptr::write(ptr as *mut u32, 0xDEADBEEF);

        // ptr::read — reads without moving
        let val = ptr::read(ptr as *const u32);
        assert_eq!(val, 0xDEADBEEF);

        // ptr::copy — like memmove (handles overlapping regions)
        ptr::copy(ptr, ptr.add(8), 4); // src, dst, count in bytes

        // ptr::copy_nonoverlapping — like memcpy (faster, but
        // source and destination must not overlap)
        ptr::copy_nonoverlapping(ptr, ptr.add(16), 4);

        // ptr::write_bytes — like memset
        ptr::write_bytes(ptr.add(24), 0xFF, 4);
    }
}
```

Why use `ptr::write` instead of `*ptr = value`? Because `*ptr = value` will try to drop the old value at that location. If the memory is uninitialized, that's a problem — you'd be "dropping" garbage, which is undefined behavior for any type that has a `Drop` implementation.

```rust
unsafe fn init_vec_element<T>(vec: &mut Vec<T>, index: usize, value: T) {
    let ptr = vec.as_mut_ptr().add(index);

    // BAD: *ptr = value would drop whatever was at ptr,
    // but if the memory is uninitialized, that's UB for
    // types with Drop impls.

    // GOOD: write doesn't read/drop the old value
    std::ptr::write(ptr, value);
}
```

## Null Pointer Handling

Unlike references, raw pointers can be null. You should check:

```rust
fn process_nullable(ptr: *const i32) -> Option<i32> {
    if ptr.is_null() {
        return None;
    }
    // SAFETY: We just verified ptr is non-null.
    // Caller must still ensure it points to valid, aligned memory.
    unsafe { Some(*ptr) }
}

// There's also a handy method to convert to a reference:
fn ptr_to_ref<'a>(ptr: *const i32) -> Option<&'a i32> {
    // SAFETY: If non-null, caller must ensure ptr is valid,
    // aligned, and the data lives for 'a.
    unsafe { ptr.as_ref() }
}
```

`as_ref()` on a raw pointer returns `Option<&T>` — it checks for null and wraps the result in `Some` if non-null. It's the cleanest way to bridge from pointer-land back to reference-land.

## A Practical Example: Ring Buffer

Let's build something real. Here's a ring buffer — a fixed-size queue where the head and tail wrap around:

```rust
struct RingBuffer<T> {
    storage: Box<[std::mem::MaybeUninit<T>]>,
    head: usize,
    tail: usize,
    len: usize,
    cap: usize,
}

impl<T> RingBuffer<T> {
    fn new(capacity: usize) -> Self {
        assert!(capacity > 0, "Capacity must be positive");
        let storage = (0..capacity)
            .map(|_| std::mem::MaybeUninit::uninit())
            .collect::<Vec<_>>()
            .into_boxed_slice();

        RingBuffer {
            storage,
            head: 0,
            tail: 0,
            len: 0,
            cap: capacity,
        }
    }

    fn push(&mut self, value: T) -> Result<(), T> {
        if self.len == self.cap {
            return Err(value); // Full
        }

        // SAFETY: tail is always < cap (maintained by modular arithmetic),
        // and we've verified the slot is unused (len < cap).
        unsafe {
            self.storage
                .as_mut_ptr()
                .add(self.tail)
                .cast::<T>()
                .write(value);
        }

        self.tail = (self.tail + 1) % self.cap;
        self.len += 1;
        Ok(())
    }

    fn pop(&mut self) -> Option<T> {
        if self.len == 0 {
            return None;
        }

        // SAFETY: head is always < cap, and we've verified
        // the slot contains a valid T (len > 0).
        let value = unsafe {
            self.storage
                .as_ptr()
                .add(self.head)
                .cast::<T>()
                .read()
        };

        self.head = (self.head + 1) % self.cap;
        self.len -= 1;
        Some(value)
    }

    fn len(&self) -> usize {
        self.len
    }

    fn is_empty(&self) -> bool {
        self.len == 0
    }
}

impl<T> Drop for RingBuffer<T> {
    fn drop(&mut self) {
        // Must drop any remaining elements
        while self.pop().is_some() {}
    }
}
```

Notice how the safe API (`push`, `pop`) hides all the pointer manipulation. Users of `RingBuffer` never touch `unsafe`. The `MaybeUninit` type is crucial here — it tells the compiler "this memory might not contain a valid T yet," which prevents it from assuming the memory is initialized.

## Pointer Provenance — The Thing Nobody Talks About

Here's something that'll bite you if you're not careful. In Rust's memory model, pointers carry *provenance* — metadata about which allocation they came from and what operations are allowed through them.

```rust
fn provenance_matters() {
    let a = [1, 2, 3];
    let b = [4, 5, 6];

    let ptr_a = a.as_ptr();
    let ptr_b = b.as_ptr();

    // Even if ptr_a.add(3) happens to equal ptr_b numerically,
    // you CANNOT use ptr_a.add(3) to access b's memory.
    // The pointer has provenance from allocation 'a', not 'b'.
    // This is UB even if the addresses match.
}
```

This is different from C, where a pointer is just an address and you can reach any memory through any pointer as long as the address is right. Rust's stricter model enables better optimizations, but it means you have to be more careful about where your pointers come from.

If you need to round-trip a pointer through an integer (for tagging, for instance), use `ptr.expose_provenance()` and `ptr::with_exposed_provenance()`:

```rust
fn tagged_pointer_example() {
    let data = Box::new(42u64);
    let ptr = Box::into_raw(data);

    // Store the pointer as an integer with a tag in the low bits
    let addr = ptr.expose_provenance();
    let tagged = addr | 0x1; // Tag bit

    // Later, recover the pointer
    let untagged = tagged & !0x1;
    let recovered: *mut u64 = std::ptr::with_exposed_provenance_mut(untagged);

    unsafe {
        assert_eq!(*recovered, 42);
        drop(Box::from_raw(recovered));
    }
}
```

## Common Pitfalls

A few things that have personally burned me:

**Forgetting alignment.** On x86, misaligned reads usually work (with a performance penalty). On ARM, they can SIGBUS your process. Always verify alignment.

```rust
fn check_alignment(ptr: *const u8) -> Option<&i32> {
    if ptr.align_offset(std::mem::align_of::<i32>()) != 0 {
        return None; // Not properly aligned for i32
    }
    // SAFETY: alignment verified above, caller ensures validity
    unsafe { (ptr as *const i32).as_ref() }
}
```

**Using raw pointers to circumvent the borrow checker without understanding why.** If the borrow checker rejects your code, there's usually a reason. Raw pointers let you bypass the checker, but you inherit all the responsibility. Make sure you actually understand the aliasing rules you need to uphold.

**Assuming pointer equality means anything.** Two pointers can have the same address but different provenance. Comparing them with `==` checks the address, not whether they're interchangeable.

## When to Reach for Raw Pointers

In practice, most Rust code never needs raw pointers directly. Here's my decision tree:

1. Can you solve it with references and lifetimes? Do that.
2. Can you solve it with `Rc`, `Arc`, `Cell`, `RefCell`? Do that.
3. Are you writing a data structure that genuinely needs interior pointer manipulation? Now consider raw pointers.
4. Are you doing FFI? You'll need raw pointers — that's how C works.

Raw pointers are the tool of last resort within pure Rust, but they're unavoidable when crossing language boundaries. In the next lesson, we'll dig into how to dereference them safely — the patterns and checks that keep your `unsafe` code sound.
