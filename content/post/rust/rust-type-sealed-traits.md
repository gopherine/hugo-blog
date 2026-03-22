---
title: "Lesson 8: Sealed Traits — Closing extension points"
author: Atharva Pandey
series: "Rust Type System Wizardry"
lesson: 8
keywords: [Rust, rust, type system, sealed traits, private trait, API design, extension points]
tags: [Rust tutorial, rust, type-system, advanced]
date: '2025-09-16T09:38:00.000Z'
---

I was designing a public API for a parser library when I realized I had a problem. I wanted users to *use* my trait — call its methods, pass it as a bound — but I did *not* want them implementing it for their own types. Every new implementation would need to maintain invariants that I couldn't enforce through the trait interface alone. If someone implemented it wrong, they'd get subtly broken behavior with no good error message.

What I needed was a trait that's public but un-implementable from outside my crate. That's a sealed trait.

## The Problem: Open Traits Are Too Open

By default, any public trait in Rust can be implemented by anyone:

```rust
// In your library crate
pub trait Serialize {
    fn to_bytes(&self) -> Vec<u8>;
}

// In some user's crate
struct MyType;
impl your_lib::Serialize for MyType {
    fn to_bytes(&self) -> Vec<u8> {
        // Maybe this implementation is wrong
        // Maybe it violates assumptions your library makes
        // You have no way to prevent this
        vec![]
    }
}
```

This is fine for traits like `Display` or `Iterator` where any implementation is valid. But some traits have invariants that can't be expressed in the type signature. Things like "the bytes returned by `to_bytes` must be valid UTF-8" or "this method must be called before that method" or "the implementation must be consistent with `Eq`."

## The Seal Pattern

The solution is elegant. You add a supertrait that lives in a *private* module:

```rust
// In your library crate
mod private {
    pub trait Sealed {}
}

pub trait MyTrait: private::Sealed {
    fn do_something(&self);
}

// You can implement Sealed for your types inside your crate:
impl private::Sealed for String {}
impl MyTrait for String {
    fn do_something(&self) {
        println!("String: {}", self);
    }
}

impl private::Sealed for i32 {}
impl MyTrait for i32 {
    fn do_something(&self) {
        println!("i32: {}", self);
    }
}
```

The trick: `private::Sealed` is a public trait (so it can appear in public signatures), but it lives in a private module. External crates can *see* that `MyTrait` requires `Sealed`, but they can't import `Sealed` to implement it. Therefore, they can't implement `MyTrait`.

```rust
// In a user's crate:
use your_lib::MyTrait;

// This works — using the trait
fn process(value: &dyn MyTrait) {
    value.do_something();
}

// This DOESN'T work — can't implement MyTrait
// struct MyType;
// impl MyTrait for MyType { // ERROR: MyType doesn't implement Sealed
//     fn do_something(&self) { }
// }
// And they can't implement Sealed because the module is private!
```

## Why Seal Traits?

### Reason 1: You Want to Add Methods Later

If a trait is unsealed and you add a new required method, every downstream implementor breaks. That's a semver-breaking change. With a sealed trait, you know exactly which types implement it — all in your crate — so you can add methods freely:

```rust
mod private {
    pub trait Sealed {}
}

pub trait Parser: private::Sealed {
    fn parse(&self, input: &str) -> Result<(), String>;

    // Added in v1.1 — not a breaking change because only we implement this
    fn parse_with_context(&self, input: &str, ctx: &Context) -> Result<(), String> {
        // Default implementation for backward compatibility
        self.parse(input)
    }
}

pub struct Context;
```

The standard library uses this pattern extensively. Many traits in `std` are sealed specifically so the stdlib team can evolve them without breaking the ecosystem.

### Reason 2: Invariants You Can't Express

Some traits have contracts that go beyond what the type system can enforce:

```rust
mod private {
    pub trait Sealed {}
}

/// A numeric type that guarantees:
/// - add is associative and commutative
/// - zero() is the additive identity
/// - These properties have been tested for this specific type
pub trait SafeNumeric: private::Sealed {
    fn zero() -> Self;
    fn add(self, other: Self) -> Self;
}

impl private::Sealed for f64 {}
impl SafeNumeric for f64 {
    fn zero() -> Self { 0.0 }
    fn add(self, other: Self) -> Self { self + other }
}

impl private::Sealed for i64 {}
impl SafeNumeric for i64 {
    fn zero() -> Self { 0 }
    fn add(self, other: Self) -> Self { self + other }
}

// We deliberately DON'T implement this for f32
// because we haven't tested the precision characteristics
```

By sealing `SafeNumeric`, you guarantee that only types you've vetted can participate. An external crate can't add `impl SafeNumeric for MySketchyFloat` and break your invariants.

### Reason 3: Exhaustive Pattern Matching

Sealed traits enable a form of exhaustive matching through visitor patterns:

```rust
mod private {
    pub trait Sealed {}
}

pub trait Expr: private::Sealed {
    fn compute(&self) -> f64;
}

pub struct Literal(pub f64);
pub struct Add(pub Box<dyn Expr>, pub Box<dyn Expr>);
pub struct Mul(pub Box<dyn Expr>, pub Box<dyn Expr>);

impl private::Sealed for Literal {}
impl private::Sealed for Add {}
impl private::Sealed for Mul {}

impl Expr for Literal {
    fn compute(&self) -> f64 { self.0 }
}

impl Expr for Add {
    fn compute(&self) -> f64 { self.0.compute() + self.1.compute() }
}

impl Expr for Mul {
    fn compute(&self) -> f64 { self.0.compute() * self.1.compute() }
}
```

Because `Expr` is sealed, you know the only implementors are `Literal`, `Add`, and `Mul`. If you downcast, you can handle all cases exhaustively. Without sealing, a user could add new variants that your code doesn't handle.

## The `#[sealed]` Macro

Writing the boilerplate for sealed traits gets tedious. The `sealed` crate provides a macro:

```rust
use sealed::sealed;

#[sealed]
pub trait MyTrait {
    fn method(&self);
}

#[sealed]
impl MyTrait for String {
    fn method(&self) {
        println!("{}", self);
    }
}
```

The macro generates the private module and `Sealed` trait automatically. Cleaner, same effect.

## Partial Sealing

Sometimes you want to seal *some* methods but leave others implementable. You can do this with a combination of sealed and unsealed traits:

```rust
mod private {
    pub trait Sealed {}
}

// This trait is sealed — users can't implement it
pub trait CoreBehavior: private::Sealed {
    fn internal_step(&self) -> Vec<u8>;
}

// This trait is open — users CAN implement it
// But it doesn't depend on CoreBehavior, so anyone can implement it
pub trait Customizable {
    fn custom_format(&self, data: &[u8]) -> String;
}

// Your types implement both:
pub struct StandardProcessor;
impl private::Sealed for StandardProcessor {}
impl CoreBehavior for StandardProcessor {
    fn internal_step(&self) -> Vec<u8> { vec![1, 2, 3] }
}
impl Customizable for StandardProcessor {
    fn custom_format(&self, data: &[u8]) -> String {
        format!("{:?}", data)
    }
}
```

The pattern: sealed traits for the invariant-heavy core, open traits for the customization points.

## Sealed Traits in the Standard Library

The standard library uses sealed traits more than you might think:

- **`std::slice::SliceIndex`** — Sealed so that the stdlib controls which types can index slices
- **`std::str::pattern::Pattern`** — Sealed to control string search implementations
- **Various internal traits** — Used throughout to maintain the freedom to add methods

The pattern `std::str::pattern::Searcher` is a great example. It's the internal mechanism for how `str::find`, `str::contains`, `str::replace`, etc. work. It's sealed because the implementation has complex invariants about how search state is maintained.

## Sealed Trait + Blanket Impl

A powerful combination: seal a trait and provide a blanket implementation:

```rust
mod private {
    pub trait Sealed {}

    // Blanket impl: anything that's Debug + Clone is Sealed
    impl<T: std::fmt::Debug + Clone> Sealed for T {}
}

pub trait Inspect: private::Sealed {
    fn inspect_value(&self) -> String;
}

// Blanket impl for everything that's Sealed (= Debug + Clone)
impl<T: std::fmt::Debug + Clone> Inspect for T {
    fn inspect_value(&self) -> String {
        format!("{:?}", self)
    }
}

fn main() {
    // Works for anything Debug + Clone
    println!("{}", 42.inspect_value());
    println!("{}", "hello".inspect_value());
    println!("{}", vec![1, 2, 3].inspect_value());

    // But nobody can override the implementation
    // The sealed bound prevents custom impls
}
```

This gives you a universal method that nobody can override. The implementation is yours and yours alone.

## The Newtype Seal

There's another sealing technique using newtypes:

```rust
// Instead of a sealed supertrait, use an unconstructable type in the signature

mod private {
    pub struct Token(());

    impl Token {
        pub(crate) fn new() -> Self {
            Token(())
        }
    }
}

pub trait MyTrait {
    // External crates can't call this because they can't construct Token
    fn init(&self, token: private::Token) -> bool;

    // But they could still implement the trait with a dummy body
    // The sealed supertrait pattern is stronger
}
```

This is weaker than the supertrait approach — someone could implement the trait with a method body that panics. The supertrait approach prevents implementation entirely.

## Design Guidelines

From my experience, here's when sealing makes sense:

**Seal when:**
- The trait has invariants not expressible in the type system
- You want the freedom to add methods in future versions
- The set of implementors should be closed (enum-like but using traits)
- You're building a plugin system where the core interface is fixed

**Don't seal when:**
- The trait is meant for users to implement (like `Iterator`, `Display`)
- The trait's contract is fully captured by its type signatures
- You *want* extensibility — that's the whole point of traits

**Document it when:**
- Always. If a trait is sealed, say so in the docs. Users will try to implement it, fail with a confusing error about a `Sealed` trait they can't find, and get frustrated. A doc comment saying "This trait is sealed and cannot be implemented outside this crate" saves everyone time.

```rust
/// A sealed trait for internal node types.
///
/// This trait is sealed and cannot be implemented outside this crate.
/// See the [`Literal`], [`Add`], and [`Mul`] types for available implementations.
pub trait Expr: private::Sealed {
    fn compute(&self) -> f64;
}
```

Sealed traits are a simple pattern with outsized impact. They're the difference between a library that can evolve without breaking changes and one that's permanently frozen by its public API surface. If you're designing a library, sealed traits should be in your toolkit.

Next lesson: proof witnesses — using types as mathematical proofs.
