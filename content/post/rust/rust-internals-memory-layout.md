---
title: "Lesson 1: Memory Layout — Size, alignment, and the padding you never asked for"
author: Atharva Pandey
series: "Rust Memory Model & Internals"
lesson: 1
keywords: [Rust, rust, memory, internals, memory layout, alignment, padding, size, struct layout]
tags: [Rust tutorial, rust, internals]
date: '2025-03-01T10:22:00.000Z'
---

I spent an embarrassing amount of time debugging a networking project where my hand-crafted packet structs were mysteriously three bytes too large. Turns out the compiler was inserting padding I didn't know about. That's when I realized most Rust developers — myself included — treat memory layout as a black box. Let's crack it open.

## Why Layout Matters

Every type in Rust has two fundamental properties: **size** and **alignment**. The size is how many bytes the value occupies. The alignment is which memory addresses the value is allowed to start at. These two numbers determine everything about how your data sits in memory, how much RAM your program uses, and whether your structs can talk to C code or hardware registers.

If you've never thought about this, that's fine — Rust's defaults are sane. But once you're writing FFI bindings, building serialization formats, optimizing cache performance, or doing anything involving `unsafe`, you need to understand what the compiler is doing behind your back.

## Size and Alignment Basics

Every primitive type has a natural alignment equal to its size:

```rust
use std::mem;

fn main() {
    // Primitive sizes and alignments
    println!("u8:  size={}, align={}", mem::size_of::<u8>(),  mem::align_of::<u8>());
    println!("u16: size={}, align={}", mem::size_of::<u16>(), mem::align_of::<u16>());
    println!("u32: size={}, align={}", mem::size_of::<u32>(), mem::align_of::<u32>());
    println!("u64: size={}, align={}", mem::size_of::<u64>(), mem::align_of::<u64>());
    println!("f64: size={}, align={}", mem::size_of::<f64>(), mem::align_of::<f64>());
    println!("bool: size={}, align={}", mem::size_of::<bool>(), mem::align_of::<bool>());
}
```

Output on a 64-bit system:

```
u8:  size=1, align=1
u16: size=2, align=2
u32: size=4, align=4
u64: size=8, align=8
f64: size=8, align=8
bool: size=1, align=1
```

Alignment means a `u32` must start at an address divisible by 4. A `u64` must start at an address divisible by 8. The CPU on most architectures can't (or won't efficiently) load a 4-byte value from an odd address — it would need two memory fetches instead of one. So the compiler enforces alignment to keep things fast.

## Struct Layout and Padding

Here's where it gets interesting. When you put fields into a struct, the compiler has to satisfy the alignment requirements of every field. That often means inserting invisible padding bytes between fields.

```rust
use std::mem;

struct Naive {
    a: u8,   // 1 byte
    b: u64,  // 8 bytes
    c: u16,  // 2 bytes
}

fn main() {
    println!("Naive: size={}, align={}",
        mem::size_of::<Naive>(),
        mem::align_of::<Naive>());
}
```

You'd expect 1 + 8 + 2 = 11 bytes. But you'll get **24 bytes**. What happened?

The compiler lays fields out in declaration order (by default — more on this in a moment). Here's the memory map:

```
Offset 0:  a (u8)       — 1 byte
Offset 1:  PADDING      — 7 bytes (to align b to offset 8)
Offset 8:  b (u64)      — 8 bytes
Offset 16: c (u16)      — 2 bytes
Offset 18: PADDING      — 6 bytes (to make total size a multiple of align=8)
Total: 24 bytes
```

That's 13 bytes of padding. More padding than actual data. The struct's overall alignment is the maximum alignment of any field (here, 8 from the `u64`), and the total size must be a multiple of that alignment so arrays of the struct work correctly.

## Rust Reorders Fields (and That's a Good Thing)

Here's the thing that surprises people coming from C: **Rust does not guarantee field order in memory**. The compiler is free to reorder struct fields to minimize padding. This is different from C, where field order is guaranteed to match declaration order.

```rust
use std::mem;

struct Reordered {
    a: u8,
    b: u64,
    c: u16,
}

fn main() {
    println!("Reordered: size={}, align={}",
        mem::size_of::<Reordered>(),
        mem::align_of::<Reordered>());

    // The compiler might lay this out as: b (u64), c (u16), a (u8)
    // Which gives: 8 + 2 + 1 + 5 padding = 16 bytes
    // Or even: b (u64), c (u16), a (u8), 5 padding = 16 bytes
}
```

In practice, the Rust compiler (as of today) will reorder this to 16 bytes by putting the `u64` first, then the `u16`, then the `u8`, with 5 bytes of trailing padding. That's 8 bytes saved compared to the naive C layout.

This optimization is why you should **never** rely on field order for Rust structs unless you explicitly opt into a specific layout with `#[repr(C)]` or similar. We'll cover `repr` attributes in detail in Lesson 6.

## Enums Are Structs With a Tag

Enum layout is one of the more fascinating aspects of Rust's memory model. A basic enum is just a discriminant (the "tag" that says which variant is active) plus enough space for the largest variant.

```rust
use std::mem;

enum Shape {
    Circle(f64),           // 8 bytes of data
    Rectangle(f64, f64),   // 16 bytes of data
    Point,                 // 0 bytes of data
}

fn main() {
    println!("Shape: size={}, align={}",
        mem::size_of::<Shape>(),
        mem::align_of::<Shape>());
}
```

This prints `size=24, align=8`. The layout is roughly:

```
Bytes 0-7:   discriminant (stored efficiently, but aligned)
Bytes 8-23:  space for the largest variant (Rectangle = two f64s)
```

The `Point` variant still takes 24 bytes because every `Shape` value must be the same size — the compiler has to know the size at compile time, and you could assign any variant to a `Shape` variable.

## Niche Optimization — The Coolest Trick

Rust performs an optimization called **niche filling** that C developers can only dream of. The classic example:

```rust
use std::mem;

fn main() {
    println!("Option<&u64>: size={}", mem::size_of::<Option<&u64>>());
    println!("&u64:         size={}", mem::size_of::<&u64>());
}
```

Both print 8. `Option<&T>` is the same size as `&T` — zero overhead. How? A reference can never be null in Rust, so the compiler uses the bit pattern for null (all zeros) to represent `None`. The "niche" is the unused bit pattern.

This works for more than just references:

```rust
use std::mem;
use std::num::NonZeroU64;

fn main() {
    println!("Option<NonZeroU64>: size={}", mem::size_of::<Option<NonZeroU64>>());
    println!("NonZeroU64:         size={}", mem::size_of::<NonZeroU64>());

    // Even nested Options can be optimized
    println!("Option<Option<&u64>>: size={}", mem::size_of::<Option<Option<&u64>>>());
    // Still 8! Uses different niche values.
}
```

`NonZeroU64` guarantees its value is never zero, so `Option<NonZeroU64>` uses zero for `None`. And `Option<Option<&u64>>` is *still* 8 bytes because there are plenty of unused bit patterns in a pointer.

This isn't just a party trick. It means `Option<Box<T>>` is the same size as a raw pointer. The entire `Option` abstraction around nullable values costs nothing at runtime. This is what zero-cost abstractions actually looks like.

## Zero-Sized Types

Rust has types with a size of zero. That sounds useless, but it's actually fundamental.

```rust
use std::mem;

struct UnitStruct;
struct Marker;

fn main() {
    println!("():         size={}", mem::size_of::<()>());
    println!("UnitStruct: size={}", mem::size_of::<UnitStruct>());
    println!("Marker:     size={}", mem::size_of::<Marker>());
    println!("[u8; 0]:    size={}", mem::size_of::<[u8; 0]>());

    // PhantomData is the most important ZST
    println!("PhantomData<String>: size={}",
        mem::size_of::<std::marker::PhantomData<String>>());
}
```

All zero. A `Vec<()>` only stores a length and capacity — it never allocates. `HashMap<K, ()>` is basically a `HashSet<K>` (and that's literally how `HashSet` is implemented in the standard library).

`PhantomData` is the most practically important ZST. It lets you pretend a struct "contains" a type without actually storing it, which affects lifetime and variance analysis. It shows up constantly in unsafe code and FFI wrappers.

## Dynamically Sized Types

Not every type has a size known at compile time. The two most common dynamically sized types (DSTs) are `str` and `[T]`. You can't put these on the stack directly — you always access them through a reference or pointer, and that reference carries the length alongside the data pointer. We'll dig into these "fat pointers" in Lesson 5.

```rust
use std::mem;

fn main() {
    // &str is a fat pointer: (pointer, length)
    println!("&str:     size={}", mem::size_of::<&str>());     // 16
    println!("&[u8]:    size={}", mem::size_of::<&[u8]>());    // 16
    println!("&u8:      size={}", mem::size_of::<&u8>());      // 8
    println!("&dyn Send: size={}", mem::size_of::<&dyn Send>()); // 16
}
```

Regular references are 8 bytes (on 64-bit). References to DSTs are 16 bytes — a pointer plus metadata. For slices, the metadata is the length. For trait objects, it's a pointer to the vtable.

## Inspecting Layout at Runtime

Sometimes you just need to see what's going on. `std::mem` gives you the tools:

```rust
use std::mem;

#[derive(Default)]
struct Example {
    flag: bool,
    counter: u64,
    tag: u8,
    value: u32,
}

fn main() {
    let e = Example::default();

    println!("Size: {} bytes", mem::size_of::<Example>());
    println!("Alignment: {} bytes", mem::align_of::<Example>());

    // You can also check at the value level
    println!("Size of val: {} bytes", mem::size_of_val(&e));

    // Field offsets aren't directly available without repr(C),
    // but you can compute them with pointer arithmetic:
    let base = &e as *const Example as usize;
    let flag_offset = &e.flag as *const bool as usize - base;
    let counter_offset = &e.counter as *const u64 as usize - base;
    let tag_offset = &e.tag as *const u8 as usize - base;
    let value_offset = &e.value as *const u32 as usize - base;

    println!("flag offset:    {}", flag_offset);
    println!("counter offset: {}", counter_offset);
    println!("tag offset:     {}", tag_offset);
    println!("value offset:   {}", value_offset);
}
```

The offsets will vary between Rust versions because the compiler can reorder fields however it wants. On my machine right now, `counter` is at offset 0, `value` at 8, `tag` at 12, and `flag` at 13. The compiler sorted by alignment, largest first. But that's an implementation detail — don't depend on it.

## Practical Implications

Why should you care about any of this?

**Cache performance.** Structs with less padding fit more instances into a cache line (typically 64 bytes). If you're iterating over millions of structs, the difference between 24 bytes and 16 bytes per struct is real. Smaller structs mean fewer cache misses, which means faster code.

**Memory usage.** A `Vec` of a million badly-padded structs can waste megabytes. If you're memory-constrained — embedded systems, large datasets, long-running services — field layout matters.

**FFI correctness.** When talking to C libraries or hardware, byte layout is a contract. Get it wrong and you'll corrupt data or crash. This is why `#[repr(C)]` exists.

**Serialization.** If you're doing zero-copy deserialization (reading binary data directly as typed structs), layout must be exactly right. No wiggle room.

## What's Next

Now that you understand how types are laid out in memory, the next question is: where does that memory come from? In Lesson 2, we'll look at the stack and the heap — two very different places your data can live, with very different performance characteristics and tradeoffs.

The stack is fast, automatic, and limited. The heap is flexible, manually managed (by the ownership system), and relatively expensive. Understanding which one Rust chooses — and why — is fundamental to writing efficient code.
