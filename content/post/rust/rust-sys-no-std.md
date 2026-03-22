---
title: "Lesson 1: no_std — Rust without the standard library"
author: Atharva Pandey
series: "Rust for Systems Programming"
lesson: 1
keywords: [Rust, rust, systems programming, no_std, bare metal, embedded, core library]
tags: [Rust tutorial, rust, systems, low-level]
date: '2025-07-10T08:14:33.000Z'
---

The first time I tried to compile a Rust program with `#![no_std]`, I felt like someone had pulled the floor out from under me. No `println!`. No `String`. No `Vec`. No `HashMap`. Half the stuff I relied on daily — just *gone*.

And that's exactly the point.

## Why Would Anyone Do This?

Here's the thing most Rust tutorials won't tell you up front: the standard library is *enormous*. It pulls in heap allocation, threading, file I/O, networking, and a whole OS-level runtime. That's great for application development. It's a non-starter for:

- Microcontrollers with 32KB of RAM
- Bootloaders that run before any OS exists
- Kernel modules that can't use userspace APIs
- Safety-critical firmware where every byte counts

When you write `#![no_std]`, you're telling the compiler: "I don't have an operating system. I don't have a heap. I might not even have a stack that anyone set up for me. Give me only what I can use."

## The Layered Architecture of Rust's Libraries

Before we strip things away, let's understand what we're working with:

```
┌─────────────────────────────┐
│          std                │  ← Full standard library (needs OS)
│  ┌──────────────────────┐   │
│  │       alloc           │   │  ← Heap allocation (needs allocator)
│  │  ┌───────────────┐   │   │
│  │  │     core       │   │   │  ← No dependencies at all
│  │  └───────────────┘   │   │
│  └──────────────────────┘   │
└─────────────────────────────┘
```

- **`core`**: The foundation. Primitive types, iterators, `Option`, `Result`, slices, basic math — stuff that needs zero OS support. This is always available.
- **`alloc`**: Everything that needs heap memory. `Vec`, `String`, `Box`, `Rc`, `Arc`. You can use this in `no_std` *if* you provide a global allocator.
- **`std`**: The full kitchen sink. File I/O, networking, threads, process management. Requires a real OS underneath.

Going `no_std` means you drop `std` but keep `core`. You *optionally* get `alloc` if you're willing to set up an allocator.

## Your First no_std Program

Let's start with the absolute minimum viable `no_std` binary:

```rust
#![no_std]
#![no_main]

use core::panic::PanicInfo;

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    loop {}
}

#[no_mangle]
pub extern "C" fn _start() -> ! {
    // We're running. No OS. No runtime. Just us.
    loop {}
}
```

Let's break down every piece because each one matters:

**`#![no_std]`** — Don't link the standard library. We only get `core`.

**`#![no_main]`** — Don't use the normal `main()` entry point. The standard Rust runtime (which calls `main`) is part of `std`, so we can't use it.

**`#[panic_handler]`** — Required. When Rust panics, it needs to know what to do. Normally `std` provides this (prints a message, unwinds the stack, exits). Without `std`, *you* have to define it. The `-> !` return type means "this function never returns" — because where would it return to?

**`#[no_mangle]`** and **`extern "C"`** — The linker needs to find our entry point by name. `#[no_mangle]` prevents Rust from mangling the symbol name, and `extern "C"` uses the C calling convention so the linker/hardware can actually call it.

**`_start`** — The conventional entry point name for ELF binaries on Linux. On other platforms, this might be different.

## Compiling for Bare Metal

You can't just `cargo build` this. The default target assumes a hosted environment with an OS. We need a bare-metal target:

```bash
# Add a bare-metal target (ARM Cortex-M3 as an example)
rustup target add thumbv7m-none-eabi

# Or for x86_64 bare metal
rustup target add x86_64-unknown-none
```

Create a `.cargo/config.toml` for your project:

```toml
[build]
target = "x86_64-unknown-none"

[target.x86_64-unknown-none]
rustflags = ["-C", "link-arg=-nostartfiles"]
```

Now `cargo build` works. You'll get a binary with no OS dependencies whatsoever.

## What You Keep in core

Here's what might surprise you — `core` is actually pretty rich. You still get:

```rust
#![no_std]

use core::panic::PanicInfo;

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    loop {}
}

// Option and Result — fully available
fn divide(a: u32, b: u32) -> Option<u32> {
    if b == 0 {
        None
    } else {
        Some(a / b)
    }
}

// Iterators — work perfectly on slices
fn sum_array(data: &[i32]) -> i32 {
    data.iter().sum()
}

// Fixed-size arrays
fn zero_buffer() -> [u8; 256] {
    [0u8; 256]
}

// All the integer types, floating point, bool, char
// Traits: Copy, Clone, Debug, Display (for formatting, not printing)
// Closures, pattern matching, generics, lifetimes — all of it

// core::fmt for formatting (but you need somewhere to write to)
use core::fmt::{self, Write};

struct UartWriter;

impl fmt::Write for UartWriter {
    fn write_str(&mut self, s: &str) -> fmt::Result {
        for byte in s.bytes() {
            unsafe {
                // Write to a hypothetical UART register
                core::ptr::write_volatile(0x4000_0000 as *mut u8, byte);
            }
        }
        Ok(())
    }
}

fn log_something() {
    let mut writer = UartWriter;
    // write! and writeln! macros work — they use core::fmt, not std::io
    writeln!(writer, "Temperature: {} degrees", 42).ok();
}
```

The mental model shift is this: Rust-the-language is almost entirely available in `no_std`. What you lose is Rust-the-runtime.

## Bringing Back Heap Allocation with alloc

Sometimes you need dynamic memory but still can't use `std`. Maybe you're writing a kernel that has its own memory management, or firmware on a chip with enough RAM for a heap.

```rust
#![no_std]
#![no_main]

extern crate alloc;

use alloc::vec::Vec;
use alloc::string::String;
use alloc::boxed::Box;
use core::alloc::{GlobalAlloc, Layout};
use core::panic::PanicInfo;

// You MUST provide a global allocator
struct BumpAllocator {
    heap_start: usize,
    heap_end: usize,
    next: core::sync::atomic::AtomicUsize,
}

unsafe impl GlobalAlloc for BumpAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let size = layout.size();
        let align = layout.align();

        let current = self.next.load(core::sync::atomic::Ordering::Relaxed);
        let aligned = (current + align - 1) & !(align - 1);
        let new_next = aligned + size;

        if new_next > self.heap_end {
            core::ptr::null_mut() // Out of memory
        } else {
            self.next.store(new_next, core::sync::atomic::Ordering::Relaxed);
            aligned as *mut u8
        }
    }

    unsafe fn dealloc(&self, _ptr: *mut u8, _layout: Layout) {
        // Bump allocators don't deallocate. We'll build better ones later.
    }
}

#[global_allocator]
static ALLOCATOR: BumpAllocator = BumpAllocator {
    heap_start: 0x2000_0000,
    heap_end: 0x2000_8000, // 32KB heap
    next: core::sync::atomic::AtomicUsize::new(0x2000_0000),
};

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    loop {}
}

#[no_mangle]
pub extern "C" fn _start() -> ! {
    // Now we can use Vec, String, Box!
    let mut data: Vec<u32> = Vec::new();
    data.push(1);
    data.push(2);
    data.push(3);

    let name = String::from("bare metal");
    let boxed: Box<u32> = Box::new(42);

    loop {}
}
```

This is genuinely powerful. You get all the ergonomic collection types Rust is known for, running on bare metal, with *your own* memory allocator underneath. The bump allocator above is trivial — we'll build real allocators in Lesson 8 — but it demonstrates the principle.

## Handling Out-of-Memory

In `std` Rust, allocation failure causes an abort. In `no_std` with `alloc`, you need to define what happens when you run out of memory:

```rust
#[alloc_error_handler]
fn alloc_error(layout: Layout) -> ! {
    // Log the failure if possible
    // On embedded, maybe blink an LED in an error pattern
    // On a kernel, trigger a kernel panic
    loop {}
}
```

Or if you're on nightly, you can use the `try_*` variants that return `Result`:

```rust
// Nightly only for now
#![feature(allocator_api)]

use alloc::vec::Vec;

fn try_allocate() -> Result<Vec<u8>, alloc::alloc::AllocError> {
    let mut v = Vec::new();
    v.try_reserve(1024)?;
    Ok(v)
}
```

Fallible allocation is one of those areas where `no_std` Rust is actually *better* than regular Rust. You're forced to think about allocation failure, which is exactly what systems code should do.

## Conditional std/no_std Libraries

If you're writing a library that should work in both environments, the standard pattern is:

```rust
// In lib.rs
#![cfg_attr(not(feature = "std"), no_std)]

#[cfg(feature = "std")]
extern crate std;

#[cfg(not(feature = "std"))]
extern crate alloc;

// Re-export so the rest of your code doesn't care
#[cfg(feature = "std")]
use std::vec::Vec;

#[cfg(not(feature = "std"))]
use alloc::vec::Vec;
```

In your `Cargo.toml`:

```toml
[features]
default = ["std"]
std = []
```

This way, users who need `no_std` can opt in with `default-features = false`, and everyone else gets the standard experience. Most serious Rust crates follow this pattern — `serde`, `rand`, `hashbrown`, you name it.

## Practical Pattern: A no_std Ring Buffer

Let's build something real — a fixed-size ring buffer that works in pure `no_std` with no allocator:

```rust
#![no_std]

pub struct RingBuffer<const N: usize> {
    buffer: [u8; N],
    read_pos: usize,
    write_pos: usize,
    count: usize,
}

impl<const N: usize> RingBuffer<N> {
    pub const fn new() -> Self {
        Self {
            buffer: [0u8; N],
            read_pos: 0,
            write_pos: 0,
            count: 0,
        }
    }

    pub fn push(&mut self, byte: u8) -> Result<(), u8> {
        if self.count == N {
            return Err(byte); // Buffer full
        }
        self.buffer[self.write_pos] = byte;
        self.write_pos = (self.write_pos + 1) % N;
        self.count += 1;
        Ok(())
    }

    pub fn pop(&mut self) -> Option<u8> {
        if self.count == 0 {
            return None;
        }
        let byte = self.buffer[self.read_pos];
        self.read_pos = (self.read_pos + 1) % N;
        self.count -= 1;
        Some(byte)
    }

    pub fn len(&self) -> usize {
        self.count
    }

    pub fn is_empty(&self) -> bool {
        self.count == 0
    }

    pub fn is_full(&self) -> bool {
        self.count == N
    }

    pub fn capacity(&self) -> usize {
        N
    }
}

// This can be used as a static — no heap needed
static mut UART_BUFFER: RingBuffer<64> = RingBuffer::new();
```

The `const N: usize` generic parameter means the buffer size is determined at compile time. No allocation. No runtime overhead. The buffer lives wherever you put it — on the stack, in a static, wherever.

## Common Pitfalls I've Hit

**Forgetting about string formatting.** `format!()` returns a `String`, which needs `alloc`. In pure `no_std`, you need to use `write!()` with a buffer you provide:

```rust
use core::fmt::Write;

let mut buf = [0u8; 128];
let mut wrapper = SliceWriter::new(&mut buf);
write!(wrapper, "Value: {}", 42).ok();
```

**Integer-to-string conversion.** Can't just `.to_string()` — that returns a `String`. Use `itoa` crate (which supports `no_std`) or write into a fixed buffer.

**Floating point.** Some bare-metal targets don't have FPU hardware. Using `f32`/`f64` might pull in soft-float routines that bloat your binary. Be intentional about it.

**The `derive` trap.** `#[derive(Debug)]` works because `Debug` is in `core`. But if you `#[derive(Hash)]` and try to use `HashMap`, that's `std` territory. `hashbrown` gives you a `no_std` alternative.

## When to Use no_std vs. std

Use `no_std` when:
- There's literally no OS (embedded, bootloaders, kernels)
- You need to minimize binary size (WebAssembly, firmware)
- You're in a `#[panic_handler]` context where you can't use std
- You're writing a library that should work everywhere

Stick with `std` when:
- You have an OS and there's no reason not to
- You need file I/O, networking, or threading
- Development speed matters more than binary size

The dividing line is simple: if you have an OS with a libc, use `std`. If you don't, or if you're writing the OS itself, go `no_std`.

## What's Coming

This lesson is the foundation for everything else in this course. Every topic from here — embedded programming, kernel modules, bootloaders, hypervisors — builds on the `no_std` mindset. You're going to get very comfortable with `core`, and you'll start to appreciate just how much of Rust's power comes from the language itself rather than the standard library.

Next up, we're taking this to actual hardware — writing Rust for microcontrollers. The loop at the end of `_start` is about to get a lot more interesting.
