---
title: "Lesson 2: Stack vs Heap — Where your data actually lives"
author: Atharva Pandey
series: "Rust Memory Model & Internals"
lesson: 2
keywords: [Rust, rust, memory, internals, stack, heap, allocation, stack frame, memory regions]
tags: [Rust tutorial, rust, internals]
date: '2025-03-03T14:45:00.000Z'
---

A colleague once asked me why their Rust program was ten times slower than expected. They were allocating a `Vec<u8>` inside a tight loop — millions of heap allocations per second. Moved the vec outside the loop, pre-allocated with `with_capacity`, and the function went from 2 seconds to 80 milliseconds. Stack vs heap isn't an academic distinction. It's the difference between fast code and slow code.

## Two Memory Regions, Two Personalities

Your program has (at minimum) two regions of memory to work with: the **stack** and the **heap**. They behave differently, perform differently, and serve different purposes. Rust makes the choice between them more explicit than most languages, which is one of the reasons it's fast by default.

### The Stack

The stack is a contiguous block of memory that grows and shrinks from one end, like a stack of plates. Each function call pushes a **stack frame** containing that function's local variables, and when the function returns, the frame is popped off. No allocation, no deallocation — just moving a pointer.

```rust
fn add(a: u32, b: u32) -> u32 {
    let result = a + b;  // `result` lives on the stack
    result               // value is copied to caller's frame
}

fn main() {
    let x = 42u32;       // on the stack
    let y = 58u32;       // on the stack
    let z = add(x, y);   // `z` on the stack, `a`, `b`, `result` were on add's stack frame
    println!("{z}");
}
```

When `main` calls `add`, the stack looks roughly like:

```
| main's frame     |
|   x: 42          |
|   y: 58          |
|   z: (uninitialized) |
| add's frame      | <-- stack pointer
|   a: 42          |
|   b: 58          |
|   result: 100    |
```

When `add` returns, its frame vanishes. The stack pointer moves back up. This is why stack allocation is essentially free — it's just pointer arithmetic.

**Stack properties:**
- Allocation: O(1) — just bump a pointer
- Deallocation: O(1) — just move the pointer back
- Size must be known at compile time
- Limited total size (typically 2-8 MB per thread)
- Grows downward on most architectures
- Automatically cleaned up when scope ends

### The Heap

The heap is a large pool of memory where you can allocate chunks of arbitrary size at runtime. It's managed by an allocator (usually the system allocator, which wraps `malloc`/`free`). Heap allocation is more expensive because the allocator has to find a free block, update bookkeeping data structures, and eventually reclaim the memory when you're done.

```rust
fn main() {
    // This Vec's buffer lives on the heap
    let mut data = Vec::new();
    data.push(1u64);
    data.push(2u64);
    data.push(3u64);

    // The Vec struct itself (pointer, length, capacity) is on the stack
    // The actual [1, 2, 3] data is on the heap
    println!("Stack size of Vec: {}", std::mem::size_of::<Vec<u64>>());
    // Prints 24 — three 8-byte fields on the stack
}
```

The `Vec` struct sits on the stack and is 24 bytes: a pointer to heap memory, a length, and a capacity. The actual data buffer is somewhere on the heap.

```
Stack:                    Heap:
+-----------+            +---+---+---+---+
| ptr  -----+---------> | 1 | 2 | 3 | _ |
| len: 3    |            +---+---+---+---+
| cap: 4    |            (allocated for 4, used 3)
+-----------+
```

**Heap properties:**
- Allocation: O(varies) — allocator must search for free space
- Deallocation: O(varies) — allocator must update free lists
- Size can be determined at runtime
- Practically unlimited (bounded by available RAM)
- Can outlive the scope that created it (via ownership transfer)
- Must be explicitly managed (Rust handles this through ownership)

## What Goes Where in Rust?

Rust doesn't have a `new` keyword or explicit stack/heap annotations like some languages. Instead, the choice is mostly determined by the type:

**Stack by default.** Local variables of known size go on the stack:

```rust
fn stack_examples() {
    let a: u64 = 42;                    // stack
    let b: [u8; 1024] = [0; 1024];     // stack (1 KB array)
    let c: (f64, f64, f64) = (1.0, 2.0, 3.0); // stack
    let d: Option<u32> = Some(7);       // stack

    struct Point { x: f64, y: f64 }
    let p = Point { x: 1.0, y: 2.0 };  // stack
}
```

**Heap when you ask for it.** You explicitly opt into heap allocation with types like `Box`, `Vec`, `String`, `Rc`, `Arc`, and `HashMap`:

```rust
fn heap_examples() {
    let a: Box<u64> = Box::new(42);              // heap
    let b: Vec<u8> = vec![0; 1024];              // heap buffer
    let c: String = String::from("hello");        // heap buffer
    let d: Box<[f64; 10000]> = Box::new([0.0; 10000]); // heap
}
```

There's a subtlety with `Box::new` — the value is technically constructed on the stack first and then moved to the heap. For small values, this doesn't matter. For huge arrays, it can cause a stack overflow. The unstable `box` syntax and `Box::new_uninit` exist partly to address this, but they're not stable yet. In practice, this rarely bites you.

## Stack Frames in Detail

Let's trace through a more realistic example to see how stack frames are created and destroyed:

```rust
fn process(data: &[u32]) -> u32 {
    let mut sum: u32 = 0;           // frame for `process`
    for &val in data {
        sum = sum.wrapping_add(val);
    }
    let average = sum / data.len() as u32;
    average                          // returned by value
}

fn build_data() -> Vec<u32> {
    let mut v = Vec::with_capacity(100); // Vec header on stack, buffer on heap
    for i in 0..100 {
        v.push(i);
    }
    v  // ownership of heap buffer transfers to caller
}

fn main() {
    let data = build_data();          // `data` owns the heap buffer
    let result = process(&data);      // borrow, no heap allocation
    println!("Result: {result}");
}   // `data` dropped here, heap buffer freed
```

The key insight: when `build_data` returns `v`, the `Vec`'s stack data (pointer, length, capacity) is copied to the caller's stack frame, but the heap buffer doesn't move. Ownership transfers without copying the underlying data. This is Rust's move semantics in action.

## The Cost Difference

Let me show you the actual performance difference:

```rust
use std::time::Instant;

fn stack_allocation_benchmark() {
    let start = Instant::now();
    for _ in 0..1_000_000 {
        let _data = [0u8; 256]; // stack allocation
        std::hint::black_box(&_data); // prevent optimization
    }
    println!("Stack: {:?}", start.elapsed());
}

fn heap_allocation_benchmark() {
    let start = Instant::now();
    for _ in 0..1_000_000 {
        let _data = vec![0u8; 256]; // heap allocation
        std::hint::black_box(&_data);
    }
    println!("Heap: {:?}", start.elapsed());
}

fn main() {
    stack_allocation_benchmark();
    heap_allocation_benchmark();
}
```

On my machine, the stack version completes in about 1ms. The heap version takes around 15ms. That's a 15x difference. For a million iterations. In a hot loop, this adds up fast.

The heap version is slower for two reasons:
1. The allocator has to find free memory and update metadata on each call
2. The newly allocated memory might not be in the CPU cache, causing cache misses

## Stack Overflow — The Hard Limit

The stack has a fixed size, and exceeding it crashes your program. This usually happens with deep recursion:

```rust
fn infinite_recursion(n: u64) -> u64 {
    // Each call adds a stack frame
    // Eventually: thread 'main' has overflowed its stack
    infinite_recursion(n + 1)
}

fn main() {
    // Don't actually run this
    // infinite_recursion(0);

    // Large stack allocations can also overflow:
    // let huge = [0u8; 100_000_000]; // 100 MB on stack — boom

    // Instead, use the heap:
    let huge = vec![0u8; 100_000_000]; // 100 MB on heap — fine
    println!("Allocated {} bytes on heap", huge.len());
}
```

The default stack size is usually 8 MB on Linux and 1 MB on Windows. You can change it for spawned threads:

```rust
use std::thread;

fn main() {
    let builder = thread::Builder::new()
        .stack_size(32 * 1024 * 1024); // 32 MB stack

    let handler = builder.spawn(|| {
        let big_array = [0u8; 16_000_000]; // 16 MB — fine with 32 MB stack
        println!("Allocated {} bytes on stack", big_array.len());
    }).unwrap();

    handler.join().unwrap();
}
```

But honestly, if you need more stack space, you probably should be using the heap instead.

## How Closures Capture: Stack or Heap?

Closures are an interesting case because they capture variables from their environment, and where those captures live depends on how you use the closure:

```rust
fn make_adder(x: u32) -> impl Fn(u32) -> u32 {
    // `x` is captured by value (moved into the closure)
    // The closure struct lives on the stack of whoever owns it
    move |y| x + y
}

fn make_boxed_adder(x: u32) -> Box<dyn Fn(u32) -> u32> {
    // Same closure, but boxed — now the closure struct lives on the heap
    Box::new(move |y| x + y)
}

fn main() {
    let add_five = make_adder(5);       // closure on stack
    let add_ten = make_boxed_adder(10); // closure on heap

    println!("{}", add_five(3));  // 8
    println!("{}", add_ten(3));   // 13
}
```

When a closure is stored as `impl Fn(...)`, the compiler knows the exact type and can put it on the stack. When it's `Box<dyn Fn(...)>`, it's heap-allocated. The `dyn` keyword (dynamic dispatch) is your signal that a heap allocation and vtable are involved. We'll dig into vtables in Lesson 4.

## Escape Analysis? Not in Rust

Languages like Go and Java perform "escape analysis" — the compiler analyzes whether a value could outlive its creating function and decides stack vs heap automatically. Rust doesn't do this because it doesn't need to. The ownership system already tells the compiler exactly how long every value lives.

If you want data on the stack, use a local variable. If you want it on the heap, use `Box`, `Vec`, or `String`. The programmer decides, not the compiler. This explicitness is a feature — you always know what you're paying for.

## Practical Guidelines

**Prefer the stack when:**
- The data size is known at compile time and is small (say, under a few kilobytes)
- The data doesn't need to outlive its scope
- You're in a hot path and every nanosecond counts

**Use the heap when:**
- The data size is determined at runtime (strings, vectors, user input)
- The data needs to be shared across scopes or threads
- The data is too large for the stack
- You need a recursive data structure (linked lists, trees)

**Performance tips:**
- Pre-allocate with `Vec::with_capacity` or `String::with_capacity` to avoid repeated heap allocations
- Reuse buffers instead of allocating new ones in loops
- Consider `SmallVec` (from the `smallvec` crate) for vectors that are usually small — it stores small arrays on the stack and spills to the heap only when needed

## What's Next

You now know that `Vec`, `String`, and `Box` put data on the heap. But `Box` is the most fundamental of these — it's a single heap allocation with ownership semantics, nothing more. In Lesson 3, we'll take apart `Box<T>` to understand exactly what it does, when to reach for it, and what it costs.
