---
title: "Lesson 1: Zero-Sized Types — PhantomData, () as design tools"
author: Atharva Pandey
series: "Rust Type System Wizardry"
lesson: 1
keywords: [Rust, rust, type system, zero-sized types, PhantomData, unit type, ZST]
tags: [Rust tutorial, rust, type-system, advanced]
date: '2025-09-01T08:32:00.000Z'
---

I remember staring at a struct definition in a library I was reading — it had a field of type `PhantomData<T>` and I thought, "this does literally nothing." The field takes up zero bytes. It has no runtime representation. Why on earth would you put a field in a struct that doesn't exist at runtime?

Turns out, zero-sized types are one of the most powerful design tools in Rust. They let you embed meaning into the type system without paying a single byte of overhead. Once you get this, you'll start seeing ZSTs everywhere — and you'll start using them yourself.

## What Is a Zero-Sized Type?

A zero-sized type (ZST) is any type whose `size_of` is zero. The most obvious one is the unit type `()`:

```rust
use std::mem;

fn main() {
    println!("Size of (): {}", mem::size_of::<()>());       // 0
    println!("Size of [u8; 0]: {}", mem::size_of::<[u8; 0]>()); // 0

    struct Empty;
    println!("Size of Empty: {}", mem::size_of::<Empty>());  // 0

    struct AlsoEmpty {}
    println!("Size of AlsoEmpty: {}", mem::size_of::<AlsoEmpty>()); // 0
}
```

Zero bytes. No allocation. No runtime cost. The compiler knows about them, uses them for type checking, and then erases them completely during code generation.

This might seem useless, but it's actually the foundation for some seriously elegant patterns.

## The Unit Type: More Than "Nothing"

People treat `()` like "void" from C. It's not. `()` is a *value* — the unique value of the unit type. You can store it in variables, put it in collections, return it from functions. It just happens to carry zero information.

This matters because Rust's type system is expression-based. Every expression evaluates to a value. When a function "returns nothing," it actually returns `()`. This consistency is what makes Rust's type system so composable:

```rust
use std::collections::HashMap;

// Using () as a value in HashSet's implementation
// HashSet<T> is literally HashMap<T, ()> under the hood
fn demonstrate_unit_as_value() {
    let mut set: HashMap<String, ()> = HashMap::new();
    set.insert("hello".to_string(), ());
    set.insert("world".to_string(), ());

    // The () values take zero space — you only pay for the keys
    println!("Contains hello: {}", set.contains_key("hello"));
}
```

This is exactly how `HashSet` works in the standard library. It's not a separate data structure — it's a `HashMap` where the values are `()`. Because `()` is zero-sized, you don't pay any memory overhead for the "value" side. That's beautiful.

## Marker Structs: Types That Mean Something

Empty structs are ZSTs too, and they're incredibly useful as markers — types that carry meaning purely through the type system:

```rust
struct Meters;
struct Feet;

struct Distance<Unit> {
    value: f64,
    _unit: std::marker::PhantomData<Unit>,
}

impl<Unit> Distance<Unit> {
    fn new(value: f64) -> Self {
        Distance {
            value,
            _unit: std::marker::PhantomData,
        }
    }
}

impl Distance<Meters> {
    fn to_feet(self) -> Distance<Feet> {
        Distance::new(self.value * 3.28084)
    }
}

impl Distance<Feet> {
    fn to_meters(self) -> Distance<Meters> {
        Distance::new(self.value / 3.28084)
    }
}

fn main() {
    let runway = Distance::<Meters>::new(3000.0);
    let runway_ft = runway.to_feet();
    println!("Runway: {} feet", runway_ft.value);

    // This won't compile — you can't add meters and feet
    // let wrong = runway.value + runway_ft.value; // Different types!
}
```

`Meters` and `Feet` take up zero bytes. `Distance<Meters>` and `Distance<Feet>` are both exactly 8 bytes — just the `f64`. But the compiler treats them as completely different types. You literally *cannot* mix them up. The Mars Climate Orbiter crash — caused by mixing metric and imperial units — would've been a compile error in this system.

## PhantomData: The Type System's Secret Weapon

`PhantomData` is a ZST that tells the compiler "pretend this struct uses type `T`, even though it doesn't store a `T` at runtime." Why would you need this?

### Reason 1: Ownership semantics

When you have a raw pointer, the compiler doesn't know if you *own* the pointed-to data or just reference it. `PhantomData` lets you express that:

```rust
use std::marker::PhantomData;

struct MyVec<T> {
    ptr: *mut T,
    len: usize,
    cap: usize,
    _marker: PhantomData<T>,  // "I own T values"
}
```

Without that `PhantomData<T>`, the compiler wouldn't know that `MyVec<T>` logically owns `T` values. This matters for the drop checker — the compiler needs to know that when `MyVec` is dropped, `T` values might also be accessed or dropped. Without `PhantomData`, the drop checker could allow unsound programs.

### Reason 2: Lifetime relationships

This is where `PhantomData` really shines. Sometimes you have a struct that logically borrows data, but the borrow isn't represented in any field:

```rust
use std::marker::PhantomData;

struct Iter<'a, T> {
    ptr: *const T,
    end: *const T,
    _lifetime: PhantomData<&'a T>,  // "I borrow from something with lifetime 'a"
}

impl<'a, T> Iter<'a, T> {
    fn new(slice: &'a [T]) -> Self {
        let ptr = slice.as_ptr();
        let end = unsafe { ptr.add(slice.len()) };
        Iter {
            ptr,
            end,
            _lifetime: PhantomData,
        }
    }
}

impl<'a, T> Iterator for Iter<'a, T> {
    type Item = &'a T;

    fn next(&mut self) -> Option<Self::Item> {
        if self.ptr == self.end {
            None
        } else {
            let current = self.ptr;
            self.ptr = unsafe { self.ptr.add(1) };
            Some(unsafe { &*current })
        }
    }
}
```

The raw pointers `ptr` and `end` don't carry lifetime information. But `PhantomData<&'a T>` tells the compiler "this struct borrows `T` values for lifetime `'a`." Without it, nothing would prevent you from using the iterator after the original slice is freed.

### Reason 3: Variance control

This is the subtle one. `PhantomData` controls how your type relates to subtyping. I'll cover variance in detail in Lesson 6, but here's a preview:

```rust
use std::marker::PhantomData;

// PhantomData<T> makes MyType covariant in T
struct Covariant<T> {
    _marker: PhantomData<T>,
}

// PhantomData<fn(T)> makes MyType contravariant in T
struct Contravariant<T> {
    _marker: PhantomData<fn(T)>,
}

// PhantomData<fn(T) -> T> makes MyType invariant in T
struct Invariant<T> {
    _marker: PhantomData<fn(T) -> T>,
}
```

The type you put inside `PhantomData` isn't just documentation — it directly controls the compiler's subtyping rules for your struct. This matters when you're writing unsafe code and need precise control over lifetime variance.

## ZSTs in Closures

Here's something that surprised me: closures that don't capture any state are zero-sized.

```rust
fn apply<F: Fn(i32) -> i32>(f: F, x: i32) -> i32 {
    f(x)
}

fn main() {
    let double = |x: i32| x * 2;

    // This closure captures nothing — it's a ZST
    println!("Size of closure: {}", std::mem::size_of_val(&double)); // 0

    // Still works perfectly
    println!("{}", apply(double, 21)); // 42
}
```

When the compiler monomorphizes `apply` with this closure, there's no function pointer indirection, no allocation for captured state — the closure's code gets inlined directly. Zero overhead. This is why Rust closures in iterators are so fast.

## The Fn Trait ZST Pattern

You can use ZST structs to create named function objects — useful when you need a type name for a function:

```rust
struct Double;

impl Fn<(i32,)> for Double {
    extern "rust-call" fn call(&self, (x,): (i32,)) -> i32 {
        x * 2
    }
}
// (This requires nightly — on stable, use a different approach)

// Stable alternative: just use a unit struct with a method
struct Doubler;

impl Doubler {
    fn apply(&self, x: i32) -> i32 {
        x * 2
    }
}

fn main() {
    let d = Doubler;
    println!("Size: {}", std::mem::size_of_val(&d)); // 0
    println!("Result: {}", d.apply(21)); // 42
}
```

## ZSTs and Allocations

One of the coolest things about ZSTs: allocating them does nothing. When you create a `Vec<()>`, no heap allocation happens for the elements — only the Vec's metadata (pointer, length, capacity) exists on the stack:

```rust
fn main() {
    let mut v: Vec<()> = Vec::new();
    for _ in 0..1_000_000 {
        v.push(());
    }
    // This "vector" of a million elements uses no heap memory for elements
    // It's essentially just a counter
    println!("Length: {}", v.len()); // 1000000
}
```

This isn't a special case — it falls out naturally from the allocator. When you ask for an allocation of size 0, the allocator returns a dangling pointer (specifically, the type's alignment as a pointer). No actual memory is allocated. The compiler handles all of this transparently.

## Practical Pattern: Builder with ZST State

Here's a pattern I use all the time — combining ZSTs with the typestate pattern:

```rust
use std::marker::PhantomData;

struct Unconfigured;
struct Configured;

struct ServerBuilder<State> {
    host: Option<String>,
    port: Option<u16>,
    _state: PhantomData<State>,
}

impl ServerBuilder<Unconfigured> {
    fn new() -> Self {
        ServerBuilder {
            host: None,
            port: None,
            _state: PhantomData,
        }
    }

    fn host(mut self, host: &str) -> Self {
        self.host = Some(host.to_string());
        self
    }

    fn port(mut self, port: u16) -> Self {
        self.port = Some(port);
        self
    }

    fn configure(self) -> ServerBuilder<Configured> {
        assert!(self.host.is_some(), "host is required");
        assert!(self.port.is_some(), "port is required");
        ServerBuilder {
            host: self.host,
            port: self.port,
            _state: PhantomData,
        }
    }
}

impl ServerBuilder<Configured> {
    fn build(self) -> Server {
        Server {
            host: self.host.unwrap(),
            port: self.port.unwrap(),
        }
    }
}

struct Server {
    host: String,
    port: u16,
}

fn main() {
    // This works:
    let server = ServerBuilder::new()
        .host("localhost")
        .port(8080)
        .configure()
        .build();

    // This won't compile — can't call build() on Unconfigured:
    // let bad = ServerBuilder::new().build();
}
```

The `_state: PhantomData<State>` field is zero-sized. `ServerBuilder<Unconfigured>` and `ServerBuilder<Configured>` have identical memory layouts. But the compiler enforces that you can only call `.build()` on a `Configured` builder. Zero runtime cost, full compile-time safety.

## When to Reach for ZSTs

I use zero-sized types whenever I need to:

- **Distinguish things at the type level** without adding runtime overhead (units of measurement, states, permissions)
- **Express ownership or borrowing** in unsafe code (`PhantomData`)
- **Control variance** for generic types
- **Implement sets** using map data structures (the `HashSet` trick)

The mental shift is this: in Rust, types aren't just about data layout. They're about *meaning*. A zero-sized type carries zero data but can carry infinite meaning — and the compiler will enforce that meaning for you, for free.

That's what makes Rust's type system different. It's not just checking that your data fits in memory. It's checking that your *logic* is coherent. ZSTs are the purest expression of this idea: types that exist solely to make your program correct.

Next up, we'll take this idea much further with advanced typestate patterns — encoding entire state machines in the type system.
