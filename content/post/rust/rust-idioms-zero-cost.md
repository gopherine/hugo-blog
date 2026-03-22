---
title: "Lesson 17: Zero-Cost Abstractions — What it actually means"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 17
keywords: [Rust, rust, idiomatic, zero-cost abstractions, performance, monomorphization]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-05-06T14:52:00.000Z'
---

"Zero-cost abstractions" is Rust's most-repeated promise and its most-misunderstood concept. People hear "zero cost" and think "free." It doesn't mean free. It means: **you don't pay for what you don't use, and what you do use, you couldn't hand-code any better.**

That's a Bjarne Stroustrup quote, originally about C++. But Rust actually delivers on it in ways C++ often doesn't.

---

## What Zero-Cost Actually Means

Consider iterators. In Python, iterator chains create intermediate objects. In Java, streams can have overhead from boxing and virtual dispatch. In Rust:

```rust
fn sum_of_even_squares(numbers: &[i32]) -> i32 {
    numbers.iter()
        .filter(|&&n| n % 2 == 0)
        .map(|&n| n * n)
        .sum()
}

fn sum_of_even_squares_manual(numbers: &[i32]) -> i32 {
    let mut total = 0;
    for &n in numbers {
        if n % 2 == 0 {
            total += n * n;
        }
    }
    total
}

fn main() {
    let nums = vec![1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
    assert_eq!(sum_of_even_squares(&nums), sum_of_even_squares_manual(&nums));
    println!("Sum: {}", sum_of_even_squares(&nums)); // 220
}
```

These two functions compile to identical machine code. Not "similar." Not "close enough." *Identical.* The iterator chain creates no intermediate collections, no heap allocations, no virtual dispatch. The compiler inlines everything and produces a tight loop.

That's zero-cost abstraction: the high-level version performs exactly as well as the hand-written low-level version.

---

## How It Works: Monomorphization

The key mechanism is monomorphization. When you use a generic function or trait, the compiler generates specialized versions for each concrete type used.

```rust
fn largest<T: PartialOrd>(list: &[T]) -> &T {
    let mut largest = &list[0];
    for item in &list[1..] {
        if item > largest {
            largest = item;
        }
    }
    largest
}

fn main() {
    let numbers = vec![34, 50, 25, 100, 65];
    let chars = vec!['y', 'm', 'a', 'q'];

    println!("Largest number: {}", largest(&numbers));
    println!("Largest char: {}", largest(&chars));
}
```

The compiler generates two versions: `largest::<i32>` and `largest::<char>`. Each is optimized for its specific type. No runtime type checks, no vtable lookups, no dynamic dispatch. The generic code runs exactly as fast as if you'd written separate functions for each type.

The trade-off: binary size increases (more generated code). But runtime performance is optimal.

---

## Traits: Static vs Dynamic Dispatch

This is where the zero-cost concept gets interesting. Rust gives you *two* ways to use traits, with different performance characteristics.

### Static dispatch (`impl Trait` and generics) — zero cost

```rust
fn print_area(shape: &impl Shape) {
    println!("Area: {:.2}", shape.area());
}

// or equivalently:
fn print_area_generic<T: Shape>(shape: &T) {
    println!("Area: {:.2}", shape.area());
}

trait Shape {
    fn area(&self) -> f64;
}

struct Circle { radius: f64 }
struct Square { side: f64 }

impl Shape for Circle {
    fn area(&self) -> f64 {
        std::f64::consts::PI * self.radius * self.radius
    }
}

impl Shape for Square {
    fn area(&self) -> f64 {
        self.side * self.side
    }
}

fn main() {
    let c = Circle { radius: 5.0 };
    let s = Square { side: 4.0 };
    print_area(&c); // Compiler knows this calls Circle::area — inlined
    print_area(&s); // Compiler knows this calls Square::area — inlined
}
```

The compiler generates `print_area::<Circle>` and `print_area::<Square>`. Each call is direct — no indirection, no vtable. The trait abstraction has zero runtime cost.

### Dynamic dispatch (`dyn Trait`) — small cost

```rust
trait Shape {
    fn area(&self) -> f64;
}

struct Circle { radius: f64 }
struct Square { side: f64 }

impl Shape for Circle {
    fn area(&self) -> f64 { std::f64::consts::PI * self.radius * self.radius }
}

impl Shape for Square {
    fn area(&self) -> f64 { self.side * self.side }
}

fn print_areas(shapes: &[Box<dyn Shape>]) {
    for shape in shapes {
        println!("Area: {:.2}", shape.area()); // vtable lookup at runtime
    }
}

fn main() {
    let shapes: Vec<Box<dyn Shape>> = vec![
        Box::new(Circle { radius: 5.0 }),
        Box::new(Square { side: 4.0 }),
    ];
    print_areas(&shapes);
}
```

`dyn Trait` uses a vtable (virtual dispatch table) — a pointer to the method implementation is looked up at runtime. This has a small cost: an indirection per method call, and the compiler can't inline the method.

But here's the thing: **this is the same cost as virtual methods in C++ or interface dispatch in Java**. You're not paying *more* than you would in other languages. You're just *aware* of the cost because Rust makes you choose between `impl Trait` (static) and `dyn Trait` (dynamic) explicitly.

---

## Newtypes Are Zero-Cost

Remember the newtype pattern from Lesson 7? It's genuinely zero-cost:

```rust
#[derive(Debug)]
struct Meters(f64);

#[derive(Debug)]
struct Seconds(f64);

fn speed(distance: Meters, time: Seconds) -> f64 {
    distance.0 / time.0
}

fn main() {
    let d = Meters(100.0);
    let t = Seconds(9.58);
    println!("Speed: {:.2} m/s", speed(d, t));
}
```

`Meters(f64)` has exactly the same memory layout as `f64`. The wrapper is erased at compile time. The type safety is free.

---

## Closures Are (Usually) Zero-Cost

Closures in Rust are compiled to anonymous structs that implement `Fn`/`FnMut`/`FnOnce`. When used with generics, they're monomorphized and inlined:

```rust
fn apply_twice<F: Fn(i32) -> i32>(f: F, x: i32) -> i32 {
    f(f(x))
}

fn main() {
    let double = |x| x * 2;
    let result = apply_twice(double, 5);
    println!("{}", result); // 20

    // The closure is inlined — no function pointer overhead
}
```

The compiler sees through the closure and inlines `x * 2` directly. No heap allocation, no dynamic dispatch, no function pointer.

The exception: `Box<dyn Fn(...)>` — that's dynamic dispatch and heap allocation. But you'd only use that when you need to store heterogeneous closures.

---

## What's NOT Zero-Cost

Not everything is free. Be honest about what has costs:

**`Box<dyn Trait>` — heap allocation + vtable lookup:**
```rust
trait Processor {
    fn process(&self, data: &str) -> String;
}

// Each Box allocates on the heap and uses dynamic dispatch
fn build_pipeline() -> Vec<Box<dyn Processor>> {
    vec![
        // Box::new(UpperCaser),
        // Box::new(Trimmer),
    ]
}
```

**`String` and `Vec` — heap allocation:**
These are zero-cost in the sense that you can't implement a growable buffer without heap allocation. But they're not "free" — allocation and deallocation have real costs.

**`Arc`/`Mutex` — atomic operations and locking:**
Thread-safe reference counting and mutual exclusion aren't free. But they're the minimum cost for safe shared-state concurrency.

The point of zero-cost abstractions isn't that everything is free. It's that *the abstraction itself* doesn't add overhead beyond what the underlying operation requires. `Vec` is as efficient as a manually managed buffer. `Arc` is as efficient as a hand-rolled atomic reference count. You can't do better by dropping down to raw pointers and manual memory management.

---

## Proving It: Looking at the Assembly

If you ever want to verify that an abstraction is truly zero-cost, check the assembly output. Cargo makes this easy:

```rust
// Put this in its own file and compile with:
// cargo rustc --release -- --emit asm

pub fn iter_sum(data: &[i32]) -> i32 {
    data.iter().sum()
}

pub fn loop_sum(data: &[i32]) -> i32 {
    let mut total = 0;
    for &n in data {
        total += n;
    }
    total
}
```

In release mode, these produce identical assembly. The iterator version doesn't create any iterator objects at runtime — it's all compiled away.

You can also use [Compiler Explorer](https://godbolt.org/) to check this interactively.

---

## The Practical Implication

The zero-cost abstraction guarantee means you should write idiomatic Rust without guilt. Don't avoid iterators because you think a `for` loop is faster. Don't avoid generics because you think they add overhead. Don't avoid newtypes because you think the wrapper costs something.

Write clean, high-level code. Trust the compiler. Profile when performance matters. And almost always, the idiomatic version is exactly as fast as the "clever" low-level version — with the benefit of being safer and more readable.

---

## Key Takeaways

- Zero-cost means "you can't hand-code it better" — not "it's free."
- Monomorphization specializes generic code for each concrete type, eliminating runtime dispatch.
- `impl Trait` (static dispatch) is zero-cost. `dyn Trait` (dynamic dispatch) has a small vtable overhead.
- Iterators, closures, newtypes, and generic functions are all zero-cost when used with static dispatch.
- Trust the compiler and write idiomatic code. Profile before optimizing. The abstraction penalty is almost always zero.
