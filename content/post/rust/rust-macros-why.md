---
title: "Lesson 1: Why Macros — When functions aren't enough"
author: Atharva Pandey
series: "Rust Macros & Metaprogramming"
lesson: 1
keywords: [Rust, rust, macros, metaprogramming, code generation, DRY]
tags: [Rust tutorial, rust, macros, metaprogramming]
date: '2025-02-05T10:22:00.000Z'
---

I spent three hours once writing nearly identical `impl` blocks for sixteen different numeric types. Copy, paste, change `i32` to `i64`, change `i64` to `u32`, repeat. Halfway through I started making typos. By the end I had a bug in the `u128` variant that took another hour to find. That's the day I actually sat down and learned macros properly.

## The Gap Between Functions and Macros

Functions are great. You take some inputs, do some work, return a result. But functions operate on *values*. They can't generate new struct definitions. They can't implement traits for you. They can't vary the number or types of arguments they accept. They can't produce different code depending on what you pass them at compile time.

Consider something as basic as `println!`. You call it with one argument, two arguments, ten arguments — and each call generates completely different code. You can't write a regular function that does this. Rust functions have fixed signatures. A function that takes one `&str` can't also take a `&str` and three integers.

```rust
// This is a function. Fixed signature. Fixed behavior.
fn add(a: i32, b: i32) -> i32 {
    a + b
}

// This is a macro invocation. Variable arguments. Code generation.
println!("sum: {} + {} = {}", 2, 3, add(2, 3));
```

That `println!` call expands into a chain of formatting operations, buffer writes, and type-specific `Display` trait calls — all resolved at compile time. No runtime reflection, no dynamic dispatch, no overhead. A function simply cannot do this.

## What Macros Actually Are

A macro is a program that writes programs. That's it. You give it some input — could be tokens, types, expressions, entire blocks of code — and it produces new Rust code as output. The compiler then compiles that generated code as if you'd written it by hand.

There are two flavors in Rust:

**Declarative macros** (`macro_rules!`) — pattern matching on syntax. You define rules like "if you see this pattern of tokens, produce this code." They're the simpler kind and handle most use cases.

**Procedural macros** — actual Rust programs that run at compile time. They receive a stream of tokens and return a stream of tokens. Three sub-types: derive macros, attribute macros, and function-like macros. These are more powerful but more complex.

We'll cover both in depth throughout this course. For now, the key distinction: declarative macros match patterns, procedural macros execute arbitrary code.

## When Functions Fall Short

Here are the concrete situations where I reach for macros instead of functions.

### Reducing Boilerplate Across Types

Say you're building a math library and need to implement an `Abs` trait for every numeric type:

```rust
trait Abs {
    fn abs(self) -> Self;
}

// Without macros: write this for i8, i16, i32, i64, i128, f32, f64...
impl Abs for i32 {
    fn abs(self) -> Self {
        if self < 0 { -self } else { self }
    }
}

impl Abs for i64 {
    fn abs(self) -> Self {
        if self < 0 { -self } else { self }
    }
}

// ... five more identical blocks
```

With a macro, you write the pattern once:

```rust
macro_rules! impl_abs_for_signed {
    ($($t:ty),*) => {
        $(
            impl Abs for $t {
                fn abs(self) -> Self {
                    if self < 0 { -self } else { self }
                }
            }
        )*
    };
}

impl_abs_for_signed!(i8, i16, i32, i64, i128);
```

Five implementations, one block of code. And when you need to change the logic, you change it in one place. This is exactly what the standard library does for dozens of trait implementations.

### Variadic Interfaces

Rust doesn't have variadic functions. Period. If you want something like Python's `*args`, macros are your only option.

```rust
macro_rules! sum {
    ($($x:expr),*) => {
        {
            let mut total = 0;
            $(total += $x;)*
            total
        }
    };
}

fn main() {
    let a = sum!(1, 2, 3);
    let b = sum!(10, 20, 30, 40, 50);
    println!("{a}, {b}"); // 6, 150
}
```

No function signature lets you accept "any number of integer expressions." The macro rewrites each call into the exact additions needed.

### Domain-Specific Syntax

Sometimes you want code that reads like a mini-language rather than a chain of method calls. Test frameworks use this heavily:

```rust
macro_rules! assert_between {
    ($val:expr, $low:expr, $high:expr) => {
        {
            let v = $val;
            let lo = $low;
            let hi = $high;
            assert!(
                v >= lo && v <= hi,
                "{} = {} is not between {} and {}",
                stringify!($val), v, lo, hi
            );
        }
    };
}

fn main() {
    let score = 85;
    assert_between!(score, 0, 100); // passes
    // assert_between!(score, 90, 100); // panics with a useful message
}
```

Notice `stringify!($val)` — that's a built-in macro that turns the *expression itself* into a string. A function can't do this. By the time a function receives a value, the original expression text is gone. The macro sees it before evaluation.

### Compile-Time Code Generation

This is the big one. Derive macros like `#[derive(Debug, Clone, Serialize)]` inspect your struct definition at compile time and generate the implementation code automatically. No runtime reflection, no performance cost.

```rust
use serde::{Serialize, Deserialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
struct User {
    name: String,
    email: String,
    age: u32,
}
```

That `#[derive(Serialize)]` generates hundreds of lines of serialization code — field-by-field, type-aware, with error handling. Writing it by hand for every struct in your codebase would be absolute misery.

## The Cost of Macros

I'm not going to pretend macros are free. They come with real downsides.

**Readability suffers.** Macro definitions use a syntax that looks nothing like regular Rust. The `$( ... )*` repetition syntax, the fragment specifiers (`expr`, `ty`, `ident`), the nested layers of expansion — it's a different mental model. New team members will stare at complex macros for a while before they click.

**Error messages get worse.** When a macro expansion fails, the compiler often points at the macro definition rather than the call site. Or it shows you an error in generated code you never wrote. Rust has gotten better at this over the years, but it's still a pain point.

**Debugging is harder.** You can't step through macro expansion in a debugger. You can inspect what a macro produces with `cargo expand`, but it's an extra step. With functions, you just read the code.

**Compile times increase.** Procedural macros in particular run as part of compilation. A complex proc macro that parses and generates code adds directly to your build time. In large projects with heavy macro usage — think anything using `serde` and `sqlx` together — this adds up.

## The Decision Framework

Over the years I've developed a simple rule: **use a function unless you can't.** Then reach for a declarative macro. Only go to procedural macros when declarative ones aren't expressive enough.

Specifically, use macros when you need:

- **Type-level code generation** — implementing traits for many types
- **Variadic arguments** — accepting different numbers of inputs
- **Syntax manipulation** — working with code as data (stringify, concat_idents)
- **Compile-time validation** — checking things that can't be checked at runtime
- **Boilerplate elimination** — when the pattern is mechanical and repeating it is error-prone

Use functions when you need:

- **Regular computation** — transforming values
- **Runtime behavior** — anything that depends on user input
- **Clear debugging** — when traceability matters more than brevity
- **Trait-based polymorphism** — generics with trait bounds handle most cases

The standard library itself is a good guide. Look at how many macros it exports: `vec!`, `println!`, `assert!`, `cfg!`, `include!`, `todo!`, `unimplemented!`. Each one does something that genuinely cannot be a function. That's the bar.

## A Quick Tour of Built-in Macros

Before we start writing our own, it's worth knowing what ships with the language:

```rust
fn main() {
    // vec! — creates a Vec with initial values
    let nums = vec![1, 2, 3, 4, 5];

    // format! — string formatting without printing
    let msg = format!("found {} items", nums.len());

    // dbg! — debug print that shows the expression and its value
    let doubled = dbg!(nums.len() * 2); // prints: [src/main.rs:8] nums.len() * 2 = 10

    // todo! — marks unfinished code, panics if reached
    // todo!("implement sorting");

    // cfg! — compile-time configuration check
    if cfg!(target_os = "linux") {
        println!("running on linux");
    }

    // concat! — concatenates string literals at compile time
    let version = concat!("v", "1", ".", "0");

    // stringify! — turns tokens into a string literal
    let code = stringify!(1 + 2 * 3);

    // env! — reads environment variables at compile time
    let pkg = env!("CARGO_PKG_NAME");

    println!("{msg}, doubled: {doubled}, version: {version}");
    println!("code: {code}, package: {pkg}");
}
```

Each of these would be impossible as a regular function. `vec!` needs to accept any number of elements of any type. `dbg!` needs access to the source expression text. `cfg!` resolves at compile time. `env!` reads from the build environment, not runtime. `concat!` and `stringify!` operate on tokens, not values.

## What's Coming

Over the next eleven lessons, we'll go from `macro_rules!` basics through advanced procedural macros. By the end you'll be able to read and write the kind of macros you see in production crates like `serde`, `clap`, and `sqlx`.

The progression looks like this: declarative macros first (lessons 2–5), then procedural macros (lessons 6–10), then real-world patterns and anti-patterns (lessons 11–12). Each lesson builds on the previous one, so don't skip ahead — the mental model compounds.

Next up: `macro_rules!` from zero. We'll write our first declarative macro and understand every piece of the syntax.
