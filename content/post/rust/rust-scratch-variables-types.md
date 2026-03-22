---
title: "Lesson 4: Variables, Mutability, and Primitive Types — Let, let mut, and the type system"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 4
keywords: [Rust, rust, variables, mutability, let, let mut, primitive types, type inference, shadowing]
tags: [Rust tutorial, rust, beginner]
date: '2024-03-11T16:45:00.000Z'
---

Coming from JavaScript, I was stunned the first time Rust refused to compile because I tried to reassign a variable. "What do you mean it's immutable by default? Who designs a language like that?" Turns out — people who've debugged enough mutable state to know better.

## Variables with let

```rust
fn main() {
    let x = 5;
    println!("x is {x}");
}
```

`let` creates a variable binding. It binds a name to a value. By default, that binding is **immutable** — you can't change it.

```rust
fn main() {
    let x = 5;
    x = 10; // ERROR: cannot assign twice to immutable variable
}
```

This is a compile-time error, not a runtime error. The compiler catches it before your code ever runs.

Why immutable by default? Because most variables don't need to change. When you look at a typical program, the majority of bindings are assigned once and read many times. Making immutability the default means you only opt into mutability when you *actually need it*, which makes your intent clearer to anyone reading the code (including future you).

## Mutable Variables with let mut

When you do need to change a value, say so explicitly:

```rust
fn main() {
    let mut count = 0;
    println!("count: {count}");

    count = 1;
    println!("count: {count}");

    count += 1;
    println!("count: {count}");
}
```

`let mut` marks the binding as mutable. Now you can reassign it. The `mut` keyword is a signal — it tells you (and anyone reading your code) that this value is going to change.

I've found that explicitly marking mutability makes bugs easier to find. When I'm debugging, I can immediately see which values might change and which are fixed. In languages where everything is mutable by default, you don't get that signal.

## Type Inference

You might have noticed we didn't specify types in the examples above. Rust has type inference — the compiler can figure out types from context.

```rust
fn main() {
    let x = 5;          // compiler infers i32
    let y = 3.14;       // compiler infers f64
    let name = "Rust";  // compiler infers &str
    let active = true;  // compiler infers bool

    println!("{x} {y} {name} {active}");
}
```

You *can* annotate types explicitly:

```rust
fn main() {
    let x: i32 = 5;
    let y: f64 = 3.14;
    let name: &str = "Rust";
    let active: bool = true;

    println!("{x} {y} {name} {active}");
}
```

Both versions compile to the same code. Use type annotations when they make the code clearer, or when the compiler can't infer the type (which happens sometimes with more complex code). Don't annotate every variable — it's noise.

My rule of thumb: annotate function signatures always, local variables rarely.

## Primitive Types

Rust's primitive types are explicit about their size. No ambiguity, no platform-dependent surprises.

### Integers

| Type | Size | Range |
|------|------|-------|
| `i8` | 8 bits | -128 to 127 |
| `i16` | 16 bits | -32,768 to 32,767 |
| `i32` | 32 bits | -2.1 billion to 2.1 billion |
| `i64` | 64 bits | Really big |
| `i128` | 128 bits | Astronomically big |
| `isize` | Platform-dependent | Pointer-sized signed |
| `u8` | 8 bits | 0 to 255 |
| `u16` | 16 bits | 0 to 65,535 |
| `u32` | 32 bits | 0 to 4.2 billion |
| `u64` | 64 bits | Really big |
| `u128` | 128 bits | Astronomically big |
| `usize` | Platform-dependent | Pointer-sized unsigned |

`i32` is the default for integer literals. Use `usize` for indexing into collections — that's what Rust expects.

```rust
fn main() {
    let a: i32 = 42;
    let b: u8 = 255;
    let c: i64 = 1_000_000_000;  // underscores for readability
    let d: usize = 100;

    println!("{a} {b} {c} {d}");
}
```

Those underscores in `1_000_000_000` are purely visual — the compiler ignores them. Use them freely for readability.

Integer literals can also specify their type with a suffix:

```rust
fn main() {
    let a = 42i32;
    let b = 255u8;
    let c = 1_000_000_000i64;

    println!("{a} {b} {c}");
}
```

### Integer Overflow

In debug mode, integer overflow panics (crashes). In release mode, it wraps around silently. This catches a lot of bugs in development.

```rust
fn main() {
    let x: u8 = 255;
    let y = x + 1; // PANIC in debug mode, wraps to 0 in release mode
    println!("{y}");
}
```

If you *want* wrapping behavior, Rust gives you explicit methods:

```rust
fn main() {
    let x: u8 = 255;

    // These are explicit about what happens on overflow
    let a = x.wrapping_add(1);    // 0 — wraps around
    let b = x.checked_add(1);     // None — returns Option
    let c = x.saturating_add(1);  // 255 — clamps to max
    let (d, overflow) = x.overflowing_add(1);  // (0, true)

    println!("wrapping: {a}");
    println!("checked: {b:?}");
    println!("saturating: {c}");
    println!("overflowing: {d}, overflow: {overflow}");
}
```

This is one of those things I love about Rust. Overflow isn't hidden. You choose the behavior you want.

### Floating Point

Two floating-point types: `f32` (32 bits) and `f64` (64 bits). Default is `f64`.

```rust
fn main() {
    let pi: f64 = 3.14159265358979;
    let e: f32 = 2.71828;

    println!("pi: {pi}");
    println!("e: {e}");
    println!("pi * 2: {}", pi * 2.0);
}
```

Rust won't let you mix integer and floating-point arithmetic without explicit conversion:

```rust
fn main() {
    let x: i32 = 5;
    let y: f64 = 3.14;

    // let z = x + y; // ERROR: can't add i32 and f64
    let z = x as f64 + y; // OK — explicit conversion
    println!("{z}");
}
```

The `as` keyword does type casting. Be careful with it — casting a large integer to a smaller type truncates, and casting a float to an integer drops the decimal. Rust won't warn you about these lossy conversions.

### Booleans

```rust
fn main() {
    let is_active: bool = true;
    let is_deleted = false;

    println!("active: {is_active}, deleted: {is_deleted}");
    println!("active AND not deleted: {}", is_active && !is_deleted);
}
```

Standard boolean operators: `&&` (and), `||` (or), `!` (not). Short-circuit evaluation, same as every other language.

### Characters

Rust `char` is a Unicode scalar value — 4 bytes, not 1.

```rust
fn main() {
    let letter: char = 'A';
    let emoji: char = '🦀';
    let kanji: char = '漢';

    println!("{letter} {emoji} {kanji}");
    println!("Size of char: {} bytes", std::mem::size_of::<char>());
}
```

This is important: a Rust `char` is always 4 bytes because it represents a full Unicode scalar value. This is different from C's `char` (1 byte) or Java's `char` (2 bytes, UTF-16 code unit). Rust made the right call here — Unicode is the standard, and pretending otherwise creates bugs.

### Tuples

Tuples group multiple values of different types:

```rust
fn main() {
    let point: (f64, f64) = (3.5, 7.2);
    let mixed = (42, "hello", true);

    // Access by index
    println!("x: {}, y: {}", point.0, point.1);

    // Destructuring
    let (x, y) = point;
    println!("x: {x}, y: {y}");

    let (num, text, flag) = mixed;
    println!("{num} {text} {flag}");
}
```

The unit type `()` is actually an empty tuple. It's Rust's way of saying "no meaningful value."

### Arrays

Fixed-size arrays — the size is part of the type:

```rust
fn main() {
    let numbers: [i32; 5] = [1, 2, 3, 4, 5];
    let zeros = [0; 10]; // ten zeros

    println!("First: {}", numbers[0]);
    println!("Length: {}", numbers.len());
    println!("Zeros: {:?}", zeros);
}
```

Arrays in Rust are stack-allocated and fixed-size. `[i32; 5]` and `[i32; 3]` are different types. For a dynamic-size collection, you'll want `Vec<T>` — that comes in Lesson 15.

Array bounds are checked at runtime. If you try to access an out-of-bounds index, Rust panics instead of reading garbage memory:

```rust
fn main() {
    let numbers = [1, 2, 3, 4, 5];
    // let x = numbers[10]; // PANIC: index out of bounds
    println!("{:?}", numbers);
}
```

## Shadowing

This one surprises people. You can declare a new variable with the same name as an existing one:

```rust
fn main() {
    let x = 5;
    let x = x + 1;         // shadows the first x
    let x = x * 2;         // shadows the second x
    println!("x is {x}");  // prints 12
}
```

This isn't mutation — it's creating a brand new binding that happens to have the same name. The old binding still existed; you just can't access it anymore.

Shadowing is especially useful for type transformations:

```rust
fn main() {
    let input = "42";          // &str
    let input: i32 = input.parse().unwrap();  // i32 — same name, different type
    let input = input * 2;     // still i32
    println!("{input}");       // 84
}
```

With `let mut`, you can change the *value* but not the *type*. With shadowing, you can change both. I use shadowing all the time for parsing and transformation pipelines where the conceptual "thing" stays the same but the type changes.

## Constants

For values that truly never change and are known at compile time:

```rust
const MAX_CONNECTIONS: u32 = 100;
const PI: f64 = 3.14159265358979;

fn main() {
    println!("Max connections: {MAX_CONNECTIONS}");
    println!("Pi: {PI}");
}
```

Constants must have their type annotated — no inference. They must be set to a constant expression — no function calls. They're inlined everywhere they're used. Convention is `SCREAMING_SNAKE_CASE`.

The difference between `const` and `let` with an immutable binding: `const` is evaluated at compile time and can be defined outside functions. `let` is evaluated at runtime and is always local to a scope.

## Static Variables

Similar to constants but with a fixed memory location:

```rust
static VERSION: &str = "1.0.0";

fn main() {
    println!("Version: {VERSION}");
}
```

You'll rarely need `static` as a beginner. The main use case is when you need a globally accessible value with a fixed memory address. `const` is almost always what you want instead.

## The Type System Philosophy

Rust's type system has a clear philosophy: be explicit, be precise, prevent mistakes. No implicit conversions. No ambiguous sizes. No hidden behavior.

This means more typing upfront. It also means fewer bugs. Every time the Rust compiler forces you to be explicit about a type conversion or a mutability marker, it's preventing a class of bugs that other languages let slip through.

Trust the type system. Work with it, not against it. It's one of the strongest tools in the language.

Next lesson: functions — and why "everything is an expression" might be the most important idea in Rust.
