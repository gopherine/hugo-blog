---
title: "Lesson 5: Fat Pointers — &dyn Trait, &[T], and &str under the hood"
author: Atharva Pandey
series: "Rust Memory Model & Internals"
lesson: 5
keywords: [Rust, rust, memory, internals, fat pointers, wide pointers, slice, str, dyn Trait, DST]
tags: [Rust tutorial, rust, internals]
date: '2025-03-09T16:40:00.000Z'
---

I remember being confused why `&str` was 16 bytes on a 64-bit system. A pointer is 8 bytes — what's the other 8? That question sent me down a rabbit hole that fundamentally changed how I think about Rust's type system. Turns out, some references carry extra baggage, and that baggage is the entire reason dynamically sized types work.

## Thin Pointers vs Fat Pointers

Most references in Rust are "thin" — a single machine word pointing to the data:

```rust
use std::mem;

fn main() {
    // Thin pointers — 8 bytes each (on 64-bit)
    println!("&u64:     {}", mem::size_of::<&u64>());          // 8
    println!("&String:  {}", mem::size_of::<&String>());       // 8
    println!("&Vec<u8>: {}", mem::size_of::<&Vec<u8>>());      // 8
    println!("&[u8; 4]: {}", mem::size_of::<&[u8; 4]>());      // 8

    // Fat pointers — 16 bytes each
    println!("&[u8]:       {}", mem::size_of::<&[u8]>());       // 16
    println!("&str:        {}", mem::size_of::<&str>());        // 16
    println!("&dyn Send:   {}", mem::size_of::<&dyn Send>());   // 16

    // Box follows the same pattern
    println!("Box<u64>:       {}", mem::size_of::<Box<u64>>());       // 8
    println!("Box<[u8]>:      {}", mem::size_of::<Box<[u8]>>());      // 16
    println!("Box<dyn Send>:  {}", mem::size_of::<Box<dyn Send>>());  // 16
}
```

A thin pointer works because the compiler knows everything about the type — its size, alignment, methods — at compile time. A `&u64` just points at 8 bytes, and the compiler knows exactly how to work with them.

But some types don't have a known size at compile time. These are **dynamically sized types** (DSTs), and references to them need extra information — the metadata. That metadata is stuffed right into the pointer, making it "fat."

There are exactly two kinds of fat pointers in Rust.

## Fat Pointer Type 1: Slice Pointers

`&[T]` and `&str` are slice pointers. They carry a data pointer and a length:

```
&[T] layout (16 bytes):
+------------------+------------------+
| data pointer     | length (usize)   |
| (points to first | (number of       |
|  element)        |  elements)       |
+------------------+------------------+
   8 bytes             8 bytes
```

```rust
fn main() {
    let array = [10u32, 20, 30, 40, 50];
    let slice: &[u32] = &array[1..4]; // [20, 30, 40]

    // Extract the raw components
    let ptr = slice.as_ptr();
    let len = slice.len();

    println!("Pointer: {:?}", ptr);
    println!("Length:  {}", len);

    // We can reconstruct the slice from raw parts
    let reconstructed: &[u32] = unsafe {
        std::slice::from_raw_parts(ptr, len)
    };
    assert_eq!(reconstructed, &[20, 30, 40]);
}
```

The key idea: a `[u32; 5]` is a sized type — the compiler knows it's exactly 20 bytes. But `[u32]` (without the length) is unsized — it could be any number of elements. The length has to come from somewhere, and that somewhere is the fat pointer.

`&str` is the same thing but for UTF-8 encoded bytes:

```rust
fn main() {
    let s: &str = "hello, world";

    let ptr = s.as_ptr();
    let len = s.len();

    println!("Pointer: {:?}", ptr);
    println!("Length: {} bytes", len); // 12 bytes of UTF-8

    // Reconstruct from raw parts
    let bytes = unsafe { std::slice::from_raw_parts(ptr, len) };
    let reconstructed = std::str::from_utf8(bytes).unwrap();
    assert_eq!(reconstructed, "hello, world");
}
```

`&str` is literally `&[u8]` with the additional guarantee that the bytes are valid UTF-8. Same fat pointer layout, same two-word representation.

## Fat Pointer Type 2: Trait Object Pointers

We covered this in Lesson 4, but let's put it side by side with slices. `&dyn Trait` carries a data pointer and a vtable pointer:

```
&dyn Trait layout (16 bytes):
+------------------+------------------+
| data pointer     | vtable pointer   |
| (points to the   | (points to the   |
|  concrete value) |  static vtable)  |
+------------------+------------------+
   8 bytes             8 bytes
```

```rust
trait Animal {
    fn speak(&self) -> &str;
}

struct Dog;
impl Animal for Dog {
    fn speak(&self) -> &str { "woof" }
}

fn main() {
    let dog = Dog;
    let animal: &dyn Animal = &dog;

    // Extract the raw components
    let raw: [usize; 2] = unsafe { std::mem::transmute(animal) };
    println!("Data pointer:   {:#x}", raw[0]);
    println!("Vtable pointer: {:#x}", raw[1]);

    // The vtable contains: drop_in_place, size, align, then method pointers
    // We can peek at the size and alignment stored in the vtable:
    println!("Size via size_of_val:  {}", std::mem::size_of_val(animal));
    println!("Align via align_of_val: {}", std::mem::align_of_val(animal));
}
```

`size_of_val` and `align_of_val` work on trait objects because they read the size and alignment from the vtable — that metadata is always there, even for ZSTs like `Dog`.

## The Unified View: Pointee Metadata

Rust has a concept (stabilized as `std::ptr::Pointee` on nightly, but the behavior is stable) where every pointer carries metadata appropriate to its target type:

| Target type | Metadata | Pointer size |
|---|---|---|
| `T` (sized) | `()` — nothing | 8 bytes (thin) |
| `[T]` (slice) | `usize` — length | 16 bytes (fat) |
| `str` | `usize` — byte length | 16 bytes (fat) |
| `dyn Trait` | `*const VTable` | 16 bytes (fat) |

Every pointer in Rust follows this pattern. The metadata type is determined by what the pointer points to. This consistency means generic code over pointers works predictably.

## Creating Fat Pointers From Thin Ones

The most common way fat pointers are created is through coercion — the compiler automatically widens a thin pointer to a fat one:

```rust
fn main() {
    // Array to slice: compiler adds the length
    let array: [u32; 5] = [1, 2, 3, 4, 5];
    let slice: &[u32] = &array; // thin &[u32; 5] → fat &[u32]

    // Concrete type to trait object: compiler adds the vtable pointer
    let value: u64 = 42;
    let sendable: &dyn Send = &value; // thin &u64 → fat &dyn Send

    // String to str: the String already has the length
    let string = String::from("hello");
    let str_ref: &str = &string; // borrows the String's buffer as &str

    println!("slice len: {}", slice.len());
    println!("sendable size: {}", std::mem::size_of_val(sendable));
    println!("str_ref len: {}", str_ref.len());
}
```

These coercions are called **unsized coercions** and they only go one direction: from sized to unsized. You can go from `&[u32; 5]` to `&[u32]`, but never back. Going back would require the compiler to verify the length at compile time, and it can't — the length is only known at runtime.

## Fat Pointers and Option

Remember niche optimization from Lesson 1? It works with fat pointers too:

```rust
use std::mem;

fn main() {
    // Fat pointer Options get niche optimization
    println!("Option<&[u8]>:      {}", mem::size_of::<Option<&[u8]>>());      // 16
    println!("&[u8]:              {}", mem::size_of::<&[u8]>());              // 16

    println!("Option<&str>:       {}", mem::size_of::<Option<&str>>());       // 16
    println!("&str:               {}", mem::size_of::<&str>());               // 16

    println!("Option<&dyn Send>:  {}", mem::size_of::<Option<&dyn Send>>()); // 16
    println!("&dyn Send:          {}", mem::size_of::<&dyn Send>());          // 16
}
```

`Option<&[u8]>` is the same size as `&[u8]` — 16 bytes, not 24. The data pointer is guaranteed non-null, so `None` uses the null data pointer. The length field doesn't matter when the data pointer is null. Same deal for `Option<&dyn Trait>` and `Option<&str>`.

## Working With Raw Fat Pointers

When you need to manually construct or deconstruct fat pointers — typically in unsafe or FFI code — you use `std::ptr::slice_from_raw_parts` and friends:

```rust
use std::ptr;

fn main() {
    // Manually constructing a slice fat pointer
    let data = vec![10u32, 20, 30, 40, 50];
    let raw_ptr: *const u32 = data.as_ptr();
    let len: usize = 3;

    // Create a raw fat pointer to the first 3 elements
    let raw_slice: *const [u32] = ptr::slice_from_raw_parts(raw_ptr, len);

    let slice: &[u32] = unsafe { &*raw_slice };
    assert_eq!(slice, &[10, 20, 30]);

    // Mutable version
    let mut data = vec![1u32, 2, 3];
    let raw_mut: *mut u32 = data.as_mut_ptr();
    let raw_mut_slice: *mut [u32] = ptr::slice_from_raw_parts_mut(raw_mut, 3);

    unsafe {
        (*raw_mut_slice)[0] = 99;
    }
    assert_eq!(data, vec![99, 2, 3]);
}
```

For trait object fat pointers, manual construction is trickier and requires more unsafe gymnastics. In practice, you rarely need to do this — coercions handle it.

## Custom DSTs

You can define your own dynamically sized types by having the last field be unsized:

```rust
use std::mem;

// Custom DST: the last field is a slice
struct Header {
    version: u32,
    flags: u32,
}

#[repr(C)]
struct Packet {
    header: Header,
    payload: [u8], // unsized — makes Packet a DST
}

fn main() {
    // You can't create a Packet on the stack directly
    // You need to go through raw allocation or slicing

    // Usually you'd work with &Packet or Box<Packet>
    println!("&Packet size: {}", mem::size_of::<&Packet>()); // 16 — fat pointer

    // Creating a custom DST value requires unsafe:
    let data: Vec<u8> = vec![
        1, 0, 0, 0,  // version = 1
        2, 0, 0, 0,  // flags = 2
        0xDE, 0xAD, 0xBE, 0xEF, // payload
    ];

    let ptr = std::ptr::slice_from_raw_parts(data.as_ptr(), 4) as *const Packet;
    let packet: &Packet = unsafe { &*ptr };

    println!("Version: {}", packet.header.version);
    println!("Payload: {:x?}", &packet.payload);
}
```

Custom DSTs are used in networking code, serialization libraries, and OS-level data structures where you need a fixed header followed by variable-length data. The fat pointer carries the length of the trailing slice.

## The extern Type — Truly Opaque

There's a third kind of unsized type you might encounter: `extern` types (nightly feature). These represent opaque types from C that have no known size at all — not even at runtime:

```rust
// Nightly only — but you'll see this pattern in FFI code
// extern "C" {
//     type OpaqueForeignType;
// }

// On stable, this is approximated with a ZST:
#[repr(C)]
struct OpaqueType {
    _private: [u8; 0],
}

// References to opaque types are thin pointers — no metadata needed
// because you never access the data directly, only pass pointers to C
```

You can't dereference a pointer to an `extern` type or take its size. It exists purely as a type-level marker for pointers.

## Performance Considerations

Fat pointers are twice the size of thin pointers, and this has real implications:

```rust
use std::mem;

fn main() {
    // A Vec of thin pointers
    println!("Vec<&u64> element size:     {}", mem::size_of::<&u64>());     // 8

    // A Vec of fat pointers
    println!("Vec<&[u8]> element size:    {}", mem::size_of::<&[u8]>());    // 16
    println!("Vec<&dyn Send> element size: {}", mem::size_of::<&dyn Send>()); // 16
}
```

If you have a million fat pointers in a vector, that's 16 MB instead of 8 MB. More memory means more cache pressure, more TLB misses, slower iteration. In hot paths with lots of pointers, this matters.

One optimization is to store indices instead of pointers. Instead of `Vec<&dyn Widget>`, consider a `Vec<WidgetId>` with a separate storage that owns the widgets. Index-based approaches use thin indices (4 or 8 bytes) and can be more cache-friendly.

Another angle: if all your trait objects are the same concrete type (but you don't know which until runtime), consider enum dispatch instead of trait objects:

```rust
enum Shape {
    Circle(Circle),
    Square(Square),
    Triangle(Triangle),
}

impl Shape {
    fn area(&self) -> f64 {
        match self {
            Shape::Circle(c) => c.area(),
            Shape::Square(s) => s.area(),
            Shape::Triangle(t) => t.area(),
        }
    }
}

// Vec<Shape> — no fat pointers, no heap allocation per element,
// no vtable lookups. Just a match statement.
```

This is sometimes called "enum dispatch" and it's faster than `dyn Trait` in benchmarks. The tradeoff is that you need a closed set of types — you can't add new shapes without modifying the enum.

## What's Next

We've seen how Rust lays out types in memory, where that memory lives, and how pointers carry metadata for unsized types. But so far, we've let the compiler choose the layout. What if you need exact control? In Lesson 6, we'll explore `#[repr(C)]`, `#[repr(transparent)]`, and `#[repr(packed)]` — the attributes that let you override the compiler's layout decisions. Essential for FFI, hardware registers, and binary protocols.
