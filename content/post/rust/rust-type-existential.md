---
title: "Lesson 5: Existential Types — impl Trait in depth"
author: Atharva Pandey
series: "Rust Type System Wizardry"
lesson: 5
keywords: [Rust, rust, type system, existential types, impl trait, opaque types, RPIT, APIT]
tags: [Rust tutorial, rust, type-system, advanced]
date: '2025-09-09T07:28:00.000Z'
---

I used `impl Trait` for months thinking it was just syntactic sugar for generics. "It's the same as a type parameter, right? Just shorter?" No. It's fundamentally different, and understanding *how* it's different unlocks patterns that are genuinely impossible with plain generics.

Let me show you.

## Two Positions, Two Meanings

`impl Trait` means completely different things depending on where you use it:

```rust
// Argument position: "I accept any type that implements Iterator"
fn count_items(iter: impl Iterator<Item = i32>) -> usize {
    iter.count()
}

// Return position: "I return some specific type that implements Iterator,
// but I'm not telling you which one"
fn make_numbers() -> impl Iterator<Item = i32> {
    (0..10).filter(|x| x % 2 == 0)
}
```

In argument position, the *caller* chooses the concrete type. In return position, the *function* chooses — and the caller can't see what it picked. These are fundamentally different concepts from type theory.

## Argument Position: Universal Types

`impl Trait` in argument position is syntactic sugar for a generic parameter:

```rust
// These are equivalent:
fn process_a(value: impl Display) { /* ... */ }
fn process_b<T: Display>(value: T) { /* ... */ }
```

Both accept *any* type that implements `Display`. The compiler monomorphizes — generates a separate copy of the function for each concrete type used. Universal quantification: "for all types T that implement Display."

The generic version is more flexible — you can reference `T` multiple times, add where clauses, etc. The `impl Trait` version is terser when you don't need that flexibility.

```rust
use std::fmt::Display;

// impl Trait can't do this:
fn same_type<T: Display>(a: T, b: T) { /* a and b are the same type */ }

// With impl Trait, these could be different types:
fn different_types(a: impl Display, b: impl Display) {
    // a might be String, b might be i32 — different types!
}
```

That's an important gotcha. Each `impl Trait` in argument position is a *separate* anonymous type parameter. They don't constrain each other.

## Return Position: Existential Types

Return-position `impl Trait` is where things get interesting. It's an *existential type* — "there exists some type that implements this trait, and I'm giving you a value of that type, but I'm not telling you which type it is."

```rust
fn make_greeting() -> impl std::fmt::Display {
    "hello world"  // The concrete type is &str, but callers don't know that
}

fn main() {
    let greeting = make_greeting();
    println!("{}", greeting);  // Works — Display is available

    // But you can't do &str-specific things:
    // let len = greeting.len(); // ERROR: impl Display doesn't have len()
}
```

The caller only knows the trait interface. The concrete type is *opaque* — hidden behind the `impl Trait` boundary.

### Why This Matters

Opaque return types let you:

1. **Return complex types without naming them**
2. **Hide implementation details**
3. **Return closures and iterators without boxing**

The third one is the killer feature. Before `impl Trait`, returning an iterator adapter chain required boxing:

```rust
// Before impl Trait: you had to box or name the type
fn evens_old(limit: i32) -> Box<dyn Iterator<Item = i32>> {
    Box::new((0..limit).filter(|x| x % 2 == 0))
}

// After impl Trait: zero-cost, no allocation
fn evens_new(limit: i32) -> impl Iterator<Item = i32> {
    (0..limit).filter(|x| x % 2 == 0)
}
```

The `impl Iterator` version has no heap allocation, no virtual dispatch, and the compiler can inline the entire iterator chain. It's as fast as hand-written loop code.

## The Type Is Still Concrete

This is crucial to understand: `impl Trait` in return position isn't dynamic dispatch. The compiler knows the concrete type — it just hides it from the caller.

```rust
fn make_thing() -> impl Clone {
    42i32  // The compiler knows this is i32
}

fn main() {
    let a = make_thing();
    let b = a.clone();
    // The compiler monomorphizes clone() for i32
    // No virtual dispatch happens
}
```

At the machine code level, this is identical to returning `i32`. The opacity is purely a source-level abstraction.

## One Concrete Type Per Call Site

A function with `-> impl Trait` must return the *same* concrete type from all return paths:

```rust
fn make_iter(use_range: bool) -> impl Iterator<Item = i32> {
    if use_range {
        (0..10).into_iter()  // This is Range<i32>
    } else {
        vec![1, 2, 3].into_iter()  // This is vec::IntoIter<i32>
        // COMPILE ERROR: different types!
    }
}
```

This doesn't work because `Range<i32>` and `vec::IntoIter<i32>` are different types. `impl Trait` means "one specific type" — the compiler just doesn't tell the caller which one.

If you need to return different types, you have two options:

```rust
// Option 1: Box it (dynamic dispatch)
fn make_iter_boxed(use_range: bool) -> Box<dyn Iterator<Item = i32>> {
    if use_range {
        Box::new(0..10)
    } else {
        Box::new(vec![1, 2, 3].into_iter())
    }
}

// Option 2: Use an enum wrapper (static dispatch)
enum EitherIter<A, B> {
    Left(A),
    Right(B),
}

impl<A: Iterator<Item = I>, B: Iterator<Item = I>, I> Iterator for EitherIter<A, B> {
    type Item = I;
    fn next(&mut self) -> Option<I> {
        match self {
            EitherIter::Left(a) => a.next(),
            EitherIter::Right(b) => b.next(),
        }
    }
}

fn make_iter_enum(use_range: bool) -> impl Iterator<Item = i32> {
    if use_range {
        EitherIter::Left(0..10)
    } else {
        EitherIter::Right(vec![1, 2, 3].into_iter())
    }
}
```

The enum approach keeps static dispatch and `impl Trait` — one concrete type (`EitherIter<Range<i32>, vec::IntoIter<i32>>`) regardless of the branch.

## Capturing Lifetimes

`impl Trait` in return position captures lifetimes from the function's inputs:

```rust
fn first_word(s: &str) -> impl Iterator<Item = char> + '_ {
    s.chars().take_while(|c| !c.is_whitespace())
}
```

The `+ '_` means "this return type borrows from `s`." Without it, the compiler would assume the return type is `'static`. Since the iterator holds a reference to `s`, you need to annotate the lifetime.

This is actually one of the more confusing parts of `impl Trait`. The rules for when lifetimes are captured and when they aren't have changed across editions:

```rust
// In Rust 2021, return-position impl Trait captures lifetimes
// from all in-scope generic parameters.
fn example<'a>(s: &'a str) -> impl Display + 'a {
    s
}

// You can opt out with a lifetime bound:
// -> impl Display + 'static  // "I promise this doesn't borrow from inputs"
```

## Type-Alias impl Trait (TAIT)

One of the most awaited features is the ability to name opaque types:

```rust
#![feature(type_alias_impl_trait)]

// Nightly only (as of writing)
type MyIter = impl Iterator<Item = i32>;

fn make_iter() -> MyIter {
    (0..10).filter(|x| x % 2 == 0)
}

fn use_iter() -> MyIter {
    // Must return the SAME concrete type as make_iter
    (0..10).filter(|x| x % 2 == 0)
}
```

This lets you name the opaque type and use it in multiple places — struct fields, trait implementations, etc. It's still opaque to consumers, but you can refer to it by name. This feature is progressing through stabilization and parts of it are available on nightly.

## impl Trait in Trait Definitions

Since Rust 1.75, you can use `impl Trait` in trait method return positions:

```rust
trait Container {
    fn items(&self) -> impl Iterator<Item = &str>;
}

struct MyList {
    data: Vec<String>,
}

impl Container for MyList {
    fn items(&self) -> impl Iterator<Item = &str> {
        self.data.iter().map(|s| s.as_str())
    }
}
```

This was a *huge* ergonomic win. Before this, returning iterators from trait methods required either boxing or associated types with named iterator types.

But there's a catch: you can't use `dyn Container` with this. The return type is opaque and different for each implementor, so the compiler can't do dynamic dispatch on the return value. If you need `dyn Container`, you still need `-> Box<dyn Iterator<...>>`.

## impl Trait vs dyn Trait

People confuse these all the time. Here's the mental model:

| | `impl Trait` | `dyn Trait` |
|---|---|---|
| **Dispatch** | Static (monomorphized) | Dynamic (vtable) |
| **Size** | Known at compile time | Needs pointer (`Box`, `&`) |
| **Type** | One concrete type (opaque) | Any type implementing trait |
| **Performance** | Zero overhead | Pointer indirection |
| **Flexibility** | One type per position | Multiple types at runtime |

Use `impl Trait` when you know the concrete type at compile time and want zero-overhead abstraction. Use `dyn Trait` when you need heterogeneous collections or runtime polymorphism.

```rust
use std::fmt::Display;

// Static dispatch — compiler generates code for the specific type
fn print_static(value: impl Display) {
    println!("{}", value);
}

// Dynamic dispatch — works with any Display type through vtable
fn print_dynamic(value: &dyn Display) {
    println!("{}", value);
}

fn main() {
    print_static(42);        // Monomorphized for i32
    print_static("hello");   // Monomorphized for &str

    print_dynamic(&42);      // Single function, virtual dispatch
    print_dynamic(&"hello"); // Same function, different vtable

    // Dynamic dispatch lets you do this:
    let things: Vec<Box<dyn Display>> = vec![Box::new(42), Box::new("hello")];
    // impl Trait can't do heterogeneous collections
}
```

## Practical Patterns

### Pattern 1: Returning Closures

```rust
fn make_adder(x: i32) -> impl Fn(i32) -> i32 {
    move |y| x + y
}

fn main() {
    let add_five = make_adder(5);
    println!("{}", add_five(3)); // 8
}
```

Each closure has a unique, unnameable type. `impl Fn` is the only way to return one without boxing.

### Pattern 2: Composing Iterator Chains

```rust
fn process_data(data: &[i32]) -> impl Iterator<Item = String> + '_ {
    data.iter()
        .filter(|&&x| x > 0)
        .map(|&x| x * 2)
        .take(10)
        .map(|x| format!("item: {}", x))
}
```

The actual return type here is something like `Map<Take<Map<Filter<Iter<'_, i32>, ...>, ...>, ...>, ...>`. Good luck naming that. `impl Iterator` is a godsend.

### Pattern 3: Hiding Implementation Details

```rust
pub struct Database { /* ... */ }

impl Database {
    // Callers can't depend on the specific iterator type
    // You're free to change the implementation later
    pub fn active_users(&self) -> impl Iterator<Item = &str> {
        // Could change from Vec to BTreeMap internally
        // without breaking the API
        ["alice", "bob"].iter().copied()
    }
}
```

This is API design gold. By returning `impl Iterator` instead of `std::slice::Iter`, you can completely restructure your internals without changing your public interface.

## The Mental Model

Think of `impl Trait` as a one-way mirror:

- **Inside the function**: You can see the concrete type, call its methods, match on it.
- **Outside the function**: You only see the trait interface. The concrete type is hidden.

This asymmetry is the whole point. It gives you the performance of concrete types with the abstraction of traits. No allocation, no indirection, but a clean interface boundary.

And that's what existential types are fundamentally about — abstracting away implementation details while keeping full type information at compile time. It's one of Rust's most elegant features, and it keeps getting more powerful with each edition.

Next up: variance. The part of the type system that makes even experienced Rustaceans nervous.
