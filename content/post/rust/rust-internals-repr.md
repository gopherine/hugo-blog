---
title: "Lesson 6: repr(C), repr(transparent), repr(packed) — Taking control of memory layout"
author: Atharva Pandey
series: "Rust Memory Model & Internals"
lesson: 6
keywords: [Rust, rust, memory, internals, repr, repr C, repr transparent, repr packed, FFI, layout control]
tags: [Rust tutorial, rust, internals]
date: '2025-03-11T09:55:00.000Z'
---

The first time I wrote Rust FFI bindings to a C library, I defined a struct, passed it across the boundary, and got garbage data back. The C side was reading fields at fixed offsets, but Rust had silently reordered my fields for padding efficiency. Took me twenty minutes of staring at hex dumps to realize the layouts didn't match. That's the day I learned about `#[repr(C)]`.

## Default Rust Layout: repr(Rust)

By default, Rust structs use `repr(Rust)` — the compiler is free to reorder fields, add padding wherever it wants, and generally optimize the layout however it sees fit. The only guarantees are:

- Each field is properly aligned
- The struct's total size is a multiple of its alignment
- Fields don't overlap (unless you're using `union`)

```rust
use std::mem;

struct DefaultLayout {
    a: u8,    // 1 byte
    b: u64,   // 8 bytes
    c: u16,   // 2 bytes
    d: u8,    // 1 byte
}

fn main() {
    println!("DefaultLayout: size={}, align={}",
        mem::size_of::<DefaultLayout>(),
        mem::align_of::<DefaultLayout>());

    let val = DefaultLayout { a: 0, b: 0, c: 0, d: 0 };
    let base = &val as *const DefaultLayout as usize;
    println!("a offset: {}", &val.a as *const u8 as usize - base);
    println!("b offset: {}", &val.b as *const u64 as usize - base);
    println!("c offset: {}", &val.c as *const u16 as usize - base);
    println!("d offset: {}", &val.d as *const u8 as usize - base);
}
```

The compiler will likely reorder this to `b, c, a, d` (sorted by descending alignment), giving a 16-byte struct instead of the 24 bytes you'd get with the declared order. Good for memory usage, bad for predictability.

This is fine for pure Rust code. You should never care about field ordering unless you're doing one of these things: FFI, memory-mapped I/O, binary serialization, or performance-sensitive struct packing.

## repr(C): Predictable Layout for FFI

`#[repr(C)]` tells the compiler to lay out the struct exactly like a C compiler would — fields in declaration order, with C-compatible padding rules:

```rust
use std::mem;

#[repr(C)]
struct CLayout {
    a: u8,    // offset 0, 1 byte
    // 7 bytes padding
    b: u64,   // offset 8, 8 bytes
    c: u16,   // offset 16, 2 bytes
    d: u8,    // offset 18, 1 byte
    // 5 bytes padding
}

fn main() {
    println!("CLayout: size={}, align={}",
        mem::size_of::<CLayout>(),
        mem::align_of::<CLayout>());
    // size=24, align=8

    let val = CLayout { a: 0, b: 0, c: 0, d: 0 };
    let base = &val as *const CLayout as usize;
    println!("a offset: {}", &val.a as *const u8 as usize - base);   // 0
    println!("b offset: {}", &val.b as *const u64 as usize - base);  // 8
    println!("c offset: {}", &val.c as *const u16 as usize - base);  // 16
    println!("d offset: {}", &val.d as *const u8 as usize - base);   // 18
}
```

Now the layout is deterministic and matches what a C compiler would produce. The C rules are:

1. Fields are laid out in declaration order
2. Each field is aligned to its alignment requirement
3. Padding is inserted between fields as needed
4. The struct's total size is rounded up to a multiple of the largest field alignment

This is essential when sharing structs with C code:

```rust
// C header:
// struct PacketHeader {
//     uint32_t magic;
//     uint16_t version;
//     uint16_t flags;
//     uint64_t timestamp;
// };

#[repr(C)]
struct PacketHeader {
    magic: u32,
    version: u16,
    flags: u16,
    timestamp: u64,
}

extern "C" {
    fn parse_header(data: *const u8, len: usize) -> PacketHeader;
    fn send_packet(header: *const PacketHeader) -> i32;
}
```

Without `repr(C)`, Rust might reorder `timestamp` before `magic`, and the C code would read garbage.

### repr(C) on Enums

`repr(C)` on enums gives them a C-compatible representation with an explicit integer discriminant:

```rust
#[repr(C)]
enum Color {
    Red,    // 0
    Green,  // 1
    Blue,   // 2
}

#[repr(C)]
enum Status {
    Ok = 0,
    Error = -1,
    Pending = 100,
}

fn main() {
    println!("Color size: {}", std::mem::size_of::<Color>());   // 4 (i32 discriminant)
    println!("Status size: {}", std::mem::size_of::<Status>()); // 4
}
```

For enums with data, `repr(C)` lays them out as a C tagged union — a discriminant field followed by a union of all variants:

```rust
#[repr(C)]
enum Event {
    Click { x: f64, y: f64 },
    KeyPress { code: u32 },
    Quit,
}

// Equivalent C:
// enum EventTag { Click, KeyPress, Quit };
// struct ClickData { double x; double y; };
// struct KeyPressData { uint32_t code; };
// struct Event {
//     enum EventTag tag;
//     union { ClickData click; KeyPressData key_press; } data;
// };

fn main() {
    println!("Event size: {}", std::mem::size_of::<Event>());
    // Discriminant (4) + padding (4) + largest variant data (16) = 24
}
```

Notice this is less efficient than Rust's default enum layout, which can use niche optimization. If you don't need C compatibility, don't use `repr(C)` on enums.

## repr(transparent): Same Layout as the Inner Type

`#[repr(transparent)]` guarantees that a struct with a single non-zero-sized field has the exact same layout as that field. This is crucial for newtype patterns in FFI:

```rust
use std::mem;

#[repr(transparent)]
struct Meters(f64);

#[repr(transparent)]
struct UserId(u64);

#[repr(transparent)]
struct Wrapper<T> {
    inner: T,
    // You can have ZST fields — they don't affect layout
    _marker: std::marker::PhantomData<T>,
}

fn main() {
    // Same size, same alignment, same ABI as the inner type
    assert_eq!(mem::size_of::<Meters>(), mem::size_of::<f64>());
    assert_eq!(mem::align_of::<Meters>(), mem::align_of::<f64>());

    assert_eq!(mem::size_of::<UserId>(), mem::size_of::<u64>());
    assert_eq!(mem::size_of::<Wrapper<String>>(), mem::size_of::<String>());
}
```

Why does this matter? Because without `repr(transparent)`, the compiler makes no guarantee about the ABI of a single-field struct. A `Meters` without `repr(transparent)` *might* have the same layout as `f64`, but it's not guaranteed — especially when passing through function calls.

With `repr(transparent)`, you're guaranteed that `Meters` and `f64` are interchangeable at the ABI level. You can safely transmute between them, pass `*const Meters` to a C function expecting `*const double`, and so on:

```rust
#[repr(transparent)]
struct Handle(u64);

extern "C" {
    // C expects a uint64_t, but we want type safety in Rust
    fn close_handle(h: Handle);
    // This is safe because Handle has the same ABI as u64
}
```

Common uses:
- Newtype wrappers for FFI safety (`Handle`, `FileDescriptor`, `ErrorCode`)
- Wrapper types that add invariants without changing layout (`NonZeroU32` uses this internally)
- Smart pointer types that want to be ABI-compatible with raw pointers

## repr(packed): Eliminate All Padding

`#[repr(packed)]` removes all padding between fields, giving the smallest possible size. Fields are aligned to 1 byte regardless of their natural alignment:

```rust
use std::mem;

#[repr(C)]
struct Normal {
    a: u8,
    b: u64,
    c: u16,
}

#[repr(C, packed)]
struct Packed {
    a: u8,
    b: u64,
    c: u16,
}

fn main() {
    println!("Normal: size={}, align={}",
        mem::size_of::<Normal>(), mem::align_of::<Normal>());
    // size=24, align=8

    println!("Packed: size={}, align={}",
        mem::size_of::<Packed>(), mem::align_of::<Packed>());
    // size=11, align=1
}
```

`Normal` is 24 bytes with 13 bytes of padding. `Packed` is 11 bytes with zero padding. The memory layout of `Packed`:

```
Offset 0:  a (u8)  — 1 byte
Offset 1:  b (u64) — 8 bytes (UNALIGNED!)
Offset 9:  c (u16) — 2 bytes (UNALIGNED!)
Total: 11 bytes
```

**Here's the danger:** packed structs have unaligned fields. Taking a reference to an unaligned field is undefined behavior:

```rust
#[repr(C, packed)]
struct Danger {
    a: u8,
    b: u32,
}

fn main() {
    let d = Danger { a: 1, b: 42 };

    // WRONG — this is UB! &d.b might be an unaligned reference
    // let b_ref: &u32 = &d.b;

    // CORRECT — read the value by copy
    let b_val: u32 = d.b;
    println!("b = {}", b_val);

    // CORRECT — use ptr::read_unaligned for raw pointer access
    let b_val = unsafe {
        std::ptr::read_unaligned(std::ptr::addr_of!(d.b))
    };
    println!("b = {}", b_val);
}
```

The compiler will warn you (and in recent editions, error) if you try to take a reference to a field of a packed struct. Always copy packed fields by value or use `read_unaligned`.

### repr(packed(N)): Partial Packing

You can specify a maximum alignment for packed structs:

```rust
use std::mem;

#[repr(C, packed(2))]
struct PartiallyPacked {
    a: u8,
    b: u64,
    c: u16,
}

fn main() {
    println!("PartiallyPacked: size={}, align={}",
        mem::size_of::<PartiallyPacked>(),
        mem::align_of::<PartiallyPacked>());
    // size=12, align=2
    // b is aligned to 2 instead of 8, saving some padding
}
```

`packed(2)` caps alignment at 2 bytes. Fields that naturally need less than 2-byte alignment are unaffected. This is a middle ground — less wasted space than full alignment, less dangerous than `packed(1)`.

## repr(align): Force Minimum Alignment

Sometimes you want *more* alignment than the default, not less. `repr(align(N))` sets a minimum alignment:

```rust
use std::mem;

#[repr(align(64))]
struct CacheAligned {
    data: [u8; 32],
}

#[repr(C, align(16))]
struct SimdReady {
    values: [f32; 4],
}

fn main() {
    println!("CacheAligned: size={}, align={}",
        mem::size_of::<CacheAligned>(), mem::align_of::<CacheAligned>());
    // size=64, align=64

    println!("SimdReady: size={}, align={}",
        mem::size_of::<SimdReady>(), mem::align_of::<SimdReady>());
    // size=16, align=16
}
```

Common uses:

**Cache line alignment.** CPU cache lines are typically 64 bytes. If two threads are writing to different fields of the same struct, and those fields share a cache line, you get false sharing — the CPUs ping-pong the cache line between them, destroying performance. Aligning to 64 bytes ensures each struct gets its own cache line:

```rust
use std::sync::atomic::{AtomicU64, Ordering};

#[repr(align(64))]
struct PaddedCounter {
    value: AtomicU64,
    // Without align(64), this counter might share a cache line
    // with a neighboring counter, causing false sharing
}

fn main() {
    let counters: Vec<PaddedCounter> = (0..8)
        .map(|_| PaddedCounter { value: AtomicU64::new(0) })
        .collect();

    // Each counter is on its own cache line — no false sharing
    println!("Counter size: {}", std::mem::size_of::<PaddedCounter>());
    // 64 bytes
}
```

**SIMD requirements.** Some CPU instructions require data to be aligned to 16, 32, or even 64 bytes. `repr(align)` guarantees the alignment.

## Combining repr Attributes

You can combine `repr(C)` with `repr(align)` or `repr(packed)`:

```rust
// C layout with cache line alignment
#[repr(C, align(64))]
struct CacheAlignedBuffer {
    data: [u8; 48],
    len: usize,
}

// C layout, tightly packed
#[repr(C, packed)]
struct WireFormat {
    msg_type: u8,
    payload_len: u32,
    sequence: u16,
}

fn main() {
    println!("CacheAlignedBuffer: size={}, align={}",
        std::mem::size_of::<CacheAlignedBuffer>(),
        std::mem::align_of::<CacheAlignedBuffer>());
    // size=64, align=64

    println!("WireFormat: size={}, align={}",
        std::mem::size_of::<WireFormat>(),
        std::mem::align_of::<WireFormat>());
    // size=7, align=1
}
```

You **cannot** combine `repr(packed)` with `repr(align)` — they conflict. And you can't combine `repr(C)` with `repr(transparent)` — they're different layout strategies.

## Real-World Example: Binary Protocol Parsing

Here's a practical example — parsing a network packet header with exact layout control:

```rust
#[repr(C, packed)]
#[derive(Debug, Clone, Copy)]
struct EthernetHeader {
    dst_mac: [u8; 6],
    src_mac: [u8; 6],
    ether_type: u16,
}

#[repr(C, packed)]
#[derive(Debug, Clone, Copy)]
struct IPv4Header {
    version_ihl: u8,
    dscp_ecn: u8,
    total_length: u16,
    identification: u16,
    flags_fragment: u16,
    ttl: u8,
    protocol: u8,
    checksum: u16,
    src_ip: [u8; 4],
    dst_ip: [u8; 4],
}

fn parse_packet(data: &[u8]) -> Option<(EthernetHeader, IPv4Header)> {
    let eth_size = std::mem::size_of::<EthernetHeader>();
    let ip_size = std::mem::size_of::<IPv4Header>();

    if data.len() < eth_size + ip_size {
        return None;
    }

    let eth: EthernetHeader = unsafe {
        std::ptr::read_unaligned(data.as_ptr() as *const EthernetHeader)
    };

    let ip: IPv4Header = unsafe {
        std::ptr::read_unaligned(
            data.as_ptr().add(eth_size) as *const IPv4Header
        )
    };

    Some((eth, ip))
}

fn main() {
    println!("EthernetHeader: {} bytes", std::mem::size_of::<EthernetHeader>());
    // 14 bytes — exactly matches the wire format
    println!("IPv4Header: {} bytes", std::mem::size_of::<IPv4Header>());
    // 20 bytes — exactly matches the wire format
}
```

Without `repr(C, packed)`, these structs would have platform-dependent padding, and the offsets wouldn't match the network protocol specification.

## Decision Guide

Which `repr` should you use?

| Situation | Use |
|---|---|
| Pure Rust, no special needs | Default (no attribute) |
| FFI with C code | `repr(C)` |
| Newtype wrappers for FFI | `repr(transparent)` |
| Binary protocols, wire formats | `repr(C, packed)` |
| SIMD, cache optimization | `repr(C, align(N))` |
| Preventing false sharing | `repr(align(64))` |
| Memory-constrained embedded | `repr(packed)` carefully |

The default is almost always right. Reach for `repr(C)` when crossing language boundaries. Use `repr(packed)` with caution and only when byte-exact layout is required. And remember — `repr(transparent)` is your friend for zero-cost newtype wrappers.

## What's Next

We've covered how data is laid out and how to control that layout. But what happens when data is destroyed? In Lesson 7, we'll explore Rust's drop order — the precise, deterministic rules for when and in what sequence destructors run. It's more nuanced than you'd expect, and getting it wrong can cause resource leaks or deadlocks.
