---
title: "Lesson 3: Box — Heap allocation by choice"
author: Atharva Pandey
series: "Rust Memory Model & Internals"
lesson: 3
keywords: [Rust, rust, memory, internals, Box, heap allocation, smart pointer, recursive types]
tags: [Rust tutorial, rust, internals]
date: '2025-03-05T08:30:00.000Z'
---

When I first started writing Rust, I used `Box` everywhere. Came from a Java background where everything lives on the heap, and wrapping things in `Box` felt natural. Took me a while to realize I was fighting the language — most of the time, you don't need it. But when you do need `Box`, nothing else will do.

## What Box Actually Is

`Box<T>` is the simplest smart pointer in Rust. It allocates a value of type `T` on the heap and gives you ownership of that allocation through a pointer on the stack. When the `Box` goes out of scope, it frees the heap memory. That's it. No reference counting, no garbage collection, no magic.

```rust
fn main() {
    let x = Box::new(42u64);
    // Stack: 8 bytes (pointer to heap)
    // Heap: 8 bytes (the u64 value 42)

    println!("Value: {}", *x);  // dereference to get the inner value
    println!("Size of Box<u64>: {}", std::mem::size_of::<Box<u64>>());
    // Prints 8 — just a pointer
}
// x is dropped here, heap memory is freed
```

The memory layout is straightforward:

```
Stack:           Heap:
+--------+      +------+
| ptr ---|----→ |  42  |
+--------+      +------+
 8 bytes         8 bytes
```

A `Box<T>` is exactly one pointer wide — 8 bytes on 64-bit, 4 bytes on 32-bit. It has zero overhead beyond the pointer itself and the heap allocation. The compiler knows this and optimizes accordingly — `Option<Box<T>>` is also 8 bytes because `Box` is guaranteed non-null, so `None` can use the null pointer representation.

## When You Actually Need Box

`Box` has a few legitimate use cases, and they're more specific than you might think.

### 1. Recursive Types

This is the classic, unavoidable use case. The compiler needs to know the size of every type at compile time, but a recursive type has infinite size:

```rust
// This won't compile — infinite size
// enum List {
//     Cons(i32, List),
//     Nil,
// }

// Box breaks the recursion — now it's a known-size pointer
enum List {
    Cons(i32, Box<List>),
    Nil,
}

fn main() {
    let list = List::Cons(1,
        Box::new(List::Cons(2,
            Box::new(List::Cons(3,
                Box::new(List::Nil))))));

    println!("Size of List: {}", std::mem::size_of::<List>());
    // 16 bytes: 4 (i32) + 4 (padding) + 8 (Box pointer)
}
```

Without `Box`, `List` contains a `List` which contains a `List` — it's turtles all the way down. `Box` introduces a level of indirection: instead of containing a `List` directly, it contains a pointer to a `List` on the heap. The pointer has a known size (8 bytes), so the compiler is happy.

Binary trees, linked lists, expression trees, AST nodes — all need `Box` or some other pointer type to break the recursion.

### 2. Trait Objects

When you want to store a value of a type that implements a trait, but you don't know the concrete type at compile time, you need dynamic dispatch. `Box<dyn Trait>` puts the value on the heap and attaches a vtable for method dispatch:

```rust
trait Shape {
    fn area(&self) -> f64;
    fn name(&self) -> &str;
}

struct Circle { radius: f64 }
struct Square { side: f64 }

impl Shape for Circle {
    fn area(&self) -> f64 { std::f64::consts::PI * self.radius * self.radius }
    fn name(&self) -> &str { "circle" }
}

impl Shape for Square {
    fn area(&self) -> f64 { self.side * self.side }
    fn name(&self) -> &str { "square" }
}

fn main() {
    let shapes: Vec<Box<dyn Shape>> = vec![
        Box::new(Circle { radius: 3.0 }),
        Box::new(Square { side: 4.0 }),
    ];

    for shape in &shapes {
        println!("{}: area = {:.2}", shape.name(), shape.area());
    }

    println!("Size of Box<dyn Shape>: {}",
        std::mem::size_of::<Box<dyn Shape>>());
    // 16 bytes — a data pointer + a vtable pointer (fat pointer)
}
```

Notice `Box<dyn Shape>` is 16 bytes, not 8. That's because trait objects use fat pointers — we'll cover these in detail in Lesson 5.

### 3. Large Values You Want Off the Stack

If you have a large struct or array that you don't want consuming stack space, `Box` moves it to the heap:

```rust
struct HugeConfig {
    buffer: [u8; 65536],       // 64 KB
    lookup_table: [u32; 8192], // 32 KB
    // ...other fields
}

fn load_config() -> Box<HugeConfig> {
    // Without Box, this 96+ KB struct would be on the stack
    // and would be copied on every return
    Box::new(HugeConfig {
        buffer: [0; 65536],
        lookup_table: [0; 8192],
    })
}

fn main() {
    let config = load_config();
    println!("Config buffer size: {}", config.buffer.len());
}
```

A word of caution here: `Box::new` technically creates the value on the stack first, then moves it to the heap. For a 96 KB struct, that could be a problem. In practice, the compiler often optimizes this away (it's called "placement new" optimization or NRVO), but it's not guaranteed. For truly enormous values, you might need `unsafe` tricks or the nightly `box` syntax.

### 4. Ownership Transfer Without Copying

When you have a large value and want to transfer ownership without copying all the data:

```rust
struct LargeData {
    payload: [u8; 10000],
}

fn produce() -> Box<LargeData> {
    Box::new(LargeData { payload: [42; 10000] })
}

fn consume(data: Box<LargeData>) {
    println!("First byte: {}", data.payload[0]);
}

fn main() {
    let data = produce();
    // Moving `data` into `consume` copies 8 bytes (the pointer),
    // not 10,000 bytes (the payload)
    consume(data);
}
```

## What Box Does Under the Hood

Let's look at what `Box::new` actually does. Simplified, it's roughly:

```rust
// This is a simplified conceptual version — the real implementation
// is more complex and uses compiler intrinsics

// pub fn new(value: T) -> Box<T> {
//     let layout = Layout::new::<T>();
//     let ptr = Global.allocate(layout).unwrap().cast::<T>();
//     ptr.as_ptr().write(value);
//     Box::from_raw(ptr.as_ptr())
// }
```

1. Calculate the memory layout (size and alignment) for type `T`
2. Ask the global allocator for a block of that size and alignment
3. Write the value into the allocated memory
4. Wrap the raw pointer in a `Box`

When the `Box` is dropped, the reverse happens:

```rust
// Conceptual Drop implementation:
// impl<T> Drop for Box<T> {
//     fn drop(&mut self) {
//         unsafe {
//             let ptr = Box::into_raw(self);
//             ptr.drop_in_place(); // drop the inner value first
//             Global.deallocate(ptr.cast(), Layout::new::<T>());
//         }
//     }
// }
```

The inner value is dropped first (its `Drop` implementation runs), then the heap memory is freed. This ordering matters — if `T` itself owns heap memory (like a `String`), that memory gets freed before the `Box`'s memory.

## Box and Deref

`Box<T>` implements `Deref<Target = T>` and `DerefMut`, which means you can use it almost interchangeably with `T`:

```rust
fn main() {
    let boxed_string = Box::new(String::from("hello"));

    // Deref coercion: Box<String> -> &String -> &str
    print_str(&boxed_string);

    // You can call String methods directly on Box<String>
    let len = boxed_string.len(); // no need for (*boxed_string).len()
    println!("Length: {}", len);

    // Method calls auto-deref through the Box
    let mut boxed_vec = Box::new(vec![1, 2, 3]);
    boxed_vec.push(4); // calls Vec::push through DerefMut
    println!("{:?}", boxed_vec);
}

fn print_str(s: &str) {
    println!("{}", s);
}
```

Deref coercion is one of Rust's ergonomic superpowers. When a function expects `&str`, you can pass a `&Box<String>`, and the compiler automatically dereferences through `Box` to `String` to `str`. This chain of coercions happens at compile time with zero runtime cost.

## Converting Between Box and Raw Pointers

When doing FFI or unsafe work, you often need to convert between `Box` and raw pointers:

```rust
fn main() {
    // Box to raw pointer (no deallocation happens)
    let boxed = Box::new(42u64);
    let raw: *mut u64 = Box::into_raw(boxed);
    // `boxed` is consumed — we're responsible for the memory now

    // Do something with the raw pointer
    unsafe {
        println!("Value: {}", *raw);
        *raw = 99;
        println!("Modified: {}", *raw);
    }

    // Raw pointer back to Box (so it gets deallocated properly)
    let boxed_again = unsafe { Box::from_raw(raw) };
    println!("Back in box: {}", boxed_again);
    // Memory is freed when boxed_again is dropped
}
```

`Box::into_raw` consumes the `Box` without freeing the memory and gives you a raw pointer. `Box::from_raw` does the reverse. This is essential for passing owned data through C APIs that take and return `void*` pointers.

The critical rule: every `Box::into_raw` must eventually be paired with a `Box::from_raw` (or manual deallocation), or you leak memory.

## When NOT to Use Box

I see beginners reach for `Box` way too often. Here are common anti-patterns:

```rust
// BAD: Boxing for no reason
fn bad_example() {
    let x: Box<u64> = Box::new(42); // just use let x: u64 = 42
    let s: Box<String> = Box::new(String::from("hi")); // String already heap-allocates its buffer
}

// BAD: Boxing to return from a function
fn bad_return() -> Box<Vec<u32>> {
    Box::new(vec![1, 2, 3])
    // Vec is already 24 bytes on the stack. Moving it is cheap.
    // Just return Vec<u32>.
}

// BAD: Boxing when you could use a reference
fn bad_param(data: Box<Vec<u32>>) {
    // This takes ownership AND heap-allocates unnecessarily.
    // Use &[u32] instead.
    println!("{:?}", data);
}
```

`String` already allocates its character data on the heap — boxing a `String` just adds another level of indirection. Same for `Vec`. You're paying for two allocations instead of one, and following two pointer indirections instead of one.

The rule of thumb: if the type already manages heap memory internally, don't wrap it in `Box`. Use `Box` for types that are large, recursive, or need to be type-erased behind a trait object.

## Box::leak — Intentional Leaking

Sometimes you want a value to live forever — for the entire duration of the program. `Box::leak` converts a `Box<T>` into a `&'static mut T`:

```rust
fn create_global_config() -> &'static str {
    let config = format!("config-{}", std::process::id());
    // Leak the String — it lives forever, we get a &'static str
    Box::leak(config.into_boxed_str())
}

fn main() {
    let config: &'static str = create_global_config();
    println!("Config: {}", config);
    // `config` is never freed — it lives until the process exits
}
```

This is useful for lazily-initialized global state, string interning, and building data structures that genuinely need to outlive everything. The memory is never freed, but since the OS reclaims everything when the process exits, it's fine for values you need for the program's entire lifetime.

## What's Next

`Box<T>` is a thin pointer to a known concrete type. But what about `Box<dyn Trait>`, where the concrete type isn't known at compile time? That requires a vtable — a lookup table of function pointers that enables dynamic dispatch. In Lesson 4, we'll open up vtables and see exactly how `dyn Trait` works at the machine level.
