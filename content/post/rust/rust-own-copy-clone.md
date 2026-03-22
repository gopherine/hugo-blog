---
title: "Lesson 3: Copy vs Clone — When Data Gets Duplicated"
author: Atharva Pandey
series: "Rust Ownership & Lifetimes Masterclass"
lesson: 3
keywords: [Rust, rust, ownership, lifetimes, Copy trait, Clone trait, duplication]
tags: [Rust tutorial, rust, ownership, lifetimes]
date: '2024-05-19T11:10:00.000Z'
---

A coworker once asked me: "If Rust moves everything, how do I ever use a value twice?" Fair question. The answer is two traits that look similar but behave very differently — `Copy` and `Clone`.

Getting these confused will either tank your performance or confuse the hell out of you. Probably both.

## Copy: Implicit, Cheap, and Bitwise

`Copy` is a marker trait. It tells the compiler: "this type is so cheap to duplicate that you should do it automatically on assignment instead of moving."

```rust
fn main() {
    let x: i32 = 42;
    let y = x;  // x is copied, not moved
    println!("{} {}", x, y);  // both valid

    let a = 3.14_f64;
    let b = a;  // copied
    println!("{} {}", a, b);  // both valid

    let flag = true;
    let other = flag;  // copied
    println!("{} {}", flag, other);  // both valid
}
```

No method call. No explicit syntax. It just happens. That's the whole point of `Copy` — it's invisible.

### What types are Copy?

All the primitive, stack-only types:

- Integers: `i8`, `i16`, `i32`, `i64`, `i128`, `isize`
- Unsigned: `u8`, `u16`, `u32`, `u64`, `u128`, `usize`
- Floats: `f32`, `f64`
- `bool`, `char`
- Tuples of `Copy` types: `(i32, f64, bool)`
- Fixed-size arrays of `Copy` types: `[i32; 5]`
- References: `&T` (but not `&mut T`)
- Function pointers

### What types are NOT Copy?

Anything that owns heap memory or has a custom `Drop` implementation:

- `String` (owns heap-allocated bytes)
- `Vec<T>` (owns heap-allocated buffer)
- `Box<T>` (owns heap-allocated value)
- `HashMap<K, V>`
- Any type that implements `Drop`

The rule is logical: if copying the bits would result in two owners of the same heap allocation, it can't be `Copy`. You'd get a double free.

## Clone: Explicit and Potentially Expensive

`Clone` is for when you genuinely want a deep copy and you're willing to pay for it.

```rust
fn main() {
    let s1 = String::from("hello");
    let s2 = s1.clone();  // explicit deep copy

    println!("{}", s1);  // still valid
    println!("{}", s2);  // independent copy
}
```

`.clone()` allocates new heap memory, copies the bytes over, and gives you a completely independent value. For a `String` with a megabyte of text, that's a megabyte of allocation and copying. Not free.

The explicit `.clone()` call is a feature, not a limitation. It's a neon sign in your code that says "ALLOCATION HAPPENING HERE." In C++, this might happen silently in a copy constructor you forgot about. In Rust, you asked for it.

## The Problem: Hidden Performance Traps

Here's the naive code that'll kill your performance:

```rust
fn process_name(name: String) -> String {
    format!("Hello, {}!", name)
}

fn main() {
    let names = vec![
        String::from("Alice"),
        String::from("Bob"),
        String::from("Charlie"),
    ];

    // BAD: cloning every string just to print it
    for name in &names {
        let greeting = process_name(name.clone());
        println!("{}", greeting);
    }
}
```

Every `.clone()` is a heap allocation. Three names, three unnecessary allocations. The fix? Take a reference instead:

```rust
fn process_name(name: &str) -> String {
    format!("Hello, {}!", name)
}

fn main() {
    let names = vec![
        String::from("Alice"),
        String::from("Bob"),
        String::from("Charlie"),
    ];

    // GOOD: borrowing — zero allocations for the names
    for name in &names {
        let greeting = process_name(name);
        println!("{}", greeting);
    }
}
```

If you're scattering `.clone()` calls everywhere to make the borrow checker happy, that's a design smell. You're paying runtime cost to avoid compile-time thinking.

## Implementing Copy and Clone for Your Types

You can derive both:

```rust
#[derive(Debug, Copy, Clone)]
struct Point {
    x: f64,
    y: f64,
}

fn main() {
    let p1 = Point { x: 1.0, y: 2.0 };
    let p2 = p1;  // copied implicitly (Copy)
    let p3 = p1.clone();  // also works (Clone)

    println!("{:?} {:?} {:?}", p1, p2, p3);
}
```

But you can only derive `Copy` if *all* fields are `Copy`:

```rust
// This WON'T compile — String is not Copy
// #[derive(Copy, Clone)]
// struct User {
//     name: String,  // not Copy!
//     age: u32,
// }

// This works — only Clone, not Copy
#[derive(Debug, Clone)]
struct User {
    name: String,
    age: u32,
}

fn main() {
    let u1 = User {
        name: String::from("Atharva"),
        age: 28,
    };

    let u2 = u1.clone();  // must be explicit
    println!("{:?}", u1);
    println!("{:?}", u2);
}
```

## Custom Clone Implementations

Sometimes the derived `Clone` isn't what you want. Maybe you need to reset some fields, or do a partial clone:

```rust
#[derive(Debug)]
struct CacheEntry {
    key: String,
    value: String,
    access_count: u64,
}

impl Clone for CacheEntry {
    fn clone(&self) -> Self {
        CacheEntry {
            key: self.key.clone(),
            value: self.value.clone(),
            access_count: 0,  // reset on clone — fresh copy shouldn't inherit count
        }
    }
}

fn main() {
    let entry = CacheEntry {
        key: String::from("user:123"),
        value: String::from("cached data"),
        access_count: 42,
    };

    let fresh = entry.clone();
    println!("Original count: {}", entry.access_count);  // 42
    println!("Clone count: {}", fresh.access_count);      // 0
}
```

## The Copy/Clone Relationship

Here's a rule that trips people up: **every `Copy` type must also implement `Clone`**, but not every `Clone` type is `Copy`.

`Copy` is a subtrait of `Clone`:

```rust
pub trait Copy: Clone { }
```

That means if you implement `Copy`, you must also implement `Clone`. But the reverse isn't true — you can be `Clone` without being `Copy`.

Think of it as: `Copy` is the "automatic, cheap" version. `Clone` is the "manual, maybe expensive" version. If something can be auto-copied, it can certainly be manually cloned too.

## When to Derive Copy

Derive `Copy` when:

- Your type is small and stack-only (like `Point { x: f64, y: f64 }`)
- All fields are `Copy`
- Implicit duplication is the expected behavior
- You have no custom `Drop` implementation

Don't derive `Copy` when:

- Your type manages a resource (file handle, connection, lock)
- Implicit copying would be surprising or expensive
- You want to enforce single ownership semantics

```rust
// Good candidate for Copy — small, value-like
#[derive(Copy, Clone, Debug)]
struct Color {
    r: u8,
    g: u8,
    b: u8,
    a: u8,
}

// Bad candidate for Copy — even if it were technically possible
// A mutex guard should NOT be implicitly copied
// (And it can't be anyway, since it has Drop)
```

## Clone Is Not Free — Measure It

I benchmarked this in a real project once. We had a function that was cloning a `Vec<String>` with 10,000 elements on every call. Switching to a reference cut that function's runtime by 80%. Eighty percent. All from removing one `.clone()`.

```rust
// BEFORE: cloning a large vec every call
fn analyze(data: Vec<String>) -> usize {
    data.iter().filter(|s| s.len() > 10).count()
}

// AFTER: borrowing instead
fn analyze(data: &[String]) -> usize {
    data.iter().filter(|s| s.len() > 10).count()
}
```

Same result. Radically different performance. The compiler couldn't optimize away that clone because it can't prove the original won't be modified.

## The Decision Tree

When you need to use a value more than once:

1. **Can you borrow it?** Use `&T`. Zero cost. This should be your default.
2. **Do you actually need a separate owned copy?** Use `.clone()`.
3. **Is the type small and stack-only?** Consider implementing `Copy` so it happens automatically.
4. **Are you cloning to satisfy the borrow checker?** Rethink your data structures. You might be fighting the wrong battle.

`.clone()` is a tool, not a crutch. Use it when you genuinely need independent copies. Don't use it to paper over ownership problems — fix those problems at the design level instead.

Next up: the borrow checker itself — what it's actually doing and why it rejects your code.
