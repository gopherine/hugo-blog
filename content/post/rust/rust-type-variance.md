---
title: "Lesson 6: Variance — Covariance, contravariance, invariance"
author: Atharva Pandey
series: "Rust Type System Wizardry"
lesson: 6
keywords: [Rust, rust, type system, variance, covariance, contravariance, invariance, lifetimes, subtyping]
tags: [Rust tutorial, rust, type-system, advanced]
date: '2025-09-11T16:42:00.000Z'
---

Variance is the topic that made me realize I didn't actually understand Rust's type system as well as I thought I did. I'd been writing Rust for over a year, had shipped production code, even written some unsafe blocks — and then I hit a lifetime error that I could not explain. The borrow checker was rejecting code that looked perfectly fine. Turns out, variance was the reason.

If you've ever had a lifetime error that made no sense, variance might be the missing piece.

## What Is Variance?

Variance describes how subtyping relationships between types are affected by type constructors. That's a mouthful, so let me make it concrete.

In Rust, the only form of subtyping is through lifetimes. If `'a: 'b` (lifetime `'a` outlives lifetime `'b`), then `&'a T` is a subtype of `&'b T`. A longer-lived reference can be used wherever a shorter-lived one is expected — that's safe because the data is guaranteed to live at least as long.

```rust
fn example<'long: 'short, 'short>(long_ref: &'long str) {
    // This is fine — 'long outlives 'short, so &'long str is a subtype of &'short str
    let short_ref: &'short str = long_ref;
}
```

Variance is the question: if `'a` is a subtype of `'b`, what's the relationship between `Container<'a>` and `Container<'b>`?

There are three answers:

- **Covariant**: `Container<'a>` is a subtype of `Container<'b>` (preserves the direction)
- **Contravariant**: `Container<'b>` is a subtype of `Container<'a>` (reverses the direction)
- **Invariant**: No subtyping relationship at all

## Covariance: The Intuitive Case

Most types in Rust are covariant in their lifetime parameters. This means "if the inner lifetime gets bigger, the outer type is still valid."

```rust
fn covariance_example<'long: 'short, 'short>(long_ref: &'long String) {
    // &'long String -> &'short String: covariant in 'long
    let short_ref: &'short String = long_ref; // OK

    // Vec<&'long str> -> Vec<&'short str>: Vec is covariant in its element type
    let long_vec: Vec<&'long str> = vec!["hello"];
    let _short_vec: Vec<&'short str> = long_vec; // OK
}
```

`&'a T` is covariant in `'a`. `Vec<T>` is covariant in `T`. `Box<T>` is covariant in `T`. Most containers that just "hold" values are covariant.

Why is this safe? Because if you have a `Vec<&'long str>`, every element lives for `'long`. Since `'long: 'short`, they all also live for `'short`. So treating the vec as a `Vec<&'short str>` is perfectly valid — you're just forgetting that the references live longer than needed.

## Invariance: The Dangerous Case

`&'a mut T` is **invariant** in `T`. This means you can't substitute types at all.

```rust
fn invariance_example<'long: 'short, 'short>(
    long_ref: &'long str,
    short_ref: &'short str,
) {
    // This is fine — covariant
    let _: &'short str = long_ref;

    // But mutable references are invariant:
    let mut s = String::from("hello");
    let mut_ref: &mut String = &mut s;
    // You CANNOT treat &mut String as &mut dyn Display, for example
    // The mutability makes variance dangerous
}
```

Why is `&mut T` invariant in `T`? Consider this hypothetical:

```rust
fn evil<'long: 'short, 'short>(
    long_ref: &'long str,
    slot: &mut &'short str, // Mutable reference to a short-lived reference
) {
    // If &mut T were covariant in T, we could do:
    // *slot = long_ref; // Store a long-lived ref where a short one is expected
    // But also:
    // *slot = short_ref; // This is fine
    //
    // The problem is: the caller might read *slot and assume it lives for 'long.
    // But we just wrote a 'short reference into it. Use-after-free!
}
```

Wait, actually the danger goes the other way. Let me show you the classic example:

```rust
fn bad_if_covariant<'long: 'short, 'short>(
    slot: &mut &'long str,
    short_lived: &'short str,
) {
    // If &mut &'long str were a subtype of &mut &'short str (covariant),
    // then we could pass our &mut &'long str where &mut &'short str is expected.
    // The receiver could then write a 'short reference into our 'long slot.
    // When we read *slot later, we'd think it's valid for 'long, but it's not.
    // That's a dangling reference. Unsound!

    // This is why &mut T is INVARIANT in T:
    // *slot = short_lived; // ERROR: lifetime mismatch
}
```

Invariance prevents this. `&mut &'long str` cannot be used as `&mut &'short str`, even though `&'long str` is a subtype of `&'short str`. The mutability breaks the covariance.

## Contravariance: The Rare Case

Contravariance reverses the subtyping relationship. In Rust, it shows up in function arguments:

```rust
// fn(&'a str) is contravariant in 'a
// A function that accepts &'short str can be used where
// a function that accepts &'long str is expected

fn contravariance_example<'long: 'short, 'short>() {
    // A function that can handle any &'short str
    let short_handler: fn(&'short str) = |_s| {};

    // Can be used where fn(&'long str) is expected
    // Because if it can handle any &'short str, it can certainly
    // handle a &'long str (which is a subtype of &'short str)
    let _long_handler: fn(&'long str) = short_handler;
}
```

This makes sense intuitively: if a function accepts "any reference that lives for at least `'short`," then it can certainly handle a reference that lives for `'long` (since `'long` outlives `'short`). The function has *fewer* requirements than what you're giving it.

Contravariance is rare in Rust. You mainly encounter it with function pointer types and `PhantomData<fn(T)>`.

## The Variance Table

Here's the complete variance table for Rust's built-in types:

| Type | Variance in `'a` | Variance in `T` |
|---|---|---|
| `&'a T` | covariant | covariant |
| `&'a mut T` | covariant | **invariant** |
| `Box<T>` | — | covariant |
| `Vec<T>` | — | covariant |
| `Cell<T>` | — | **invariant** |
| `UnsafeCell<T>` | — | **invariant** |
| `*const T` | — | covariant |
| `*mut T` | — | **invariant** |
| `fn(T) -> U` | — | **contravariant** in `T`, covariant in `U` |
| `PhantomData<T>` | — | covariant |
| `PhantomData<fn(T)>` | — | **contravariant** |
| `PhantomData<fn(T) -> T>` | — | **invariant** |

Notice the pattern: anything that involves mutation or interior mutability is invariant. Immutable access is covariant. Function arguments are contravariant.

## How Variance Affects Your Code

Most of the time, variance works behind the scenes and you don't think about it. But it surfaces in a few common situations.

### Situation 1: Storing References in Structs

```rust
struct Parser<'input> {
    source: &'input str,
    position: usize,
}

// Parser is covariant in 'input (because &'input str is covariant)
// This means Parser<'long> can be used where Parser<'short> is expected

fn parse<'a>(parser: Parser<'a>) -> &'a str {
    &parser.source[parser.position..]
}
```

Covariance here is what you want. A parser bound to a long-lived input can be used where a short-lived one is expected.

### Situation 2: Mutable References in Structs

```rust
struct Appender<'buf> {
    buffer: &'buf mut Vec<String>,
}

// Appender is INVARIANT in 'buf (because &'buf mut T is invariant in T)
// This means you can't shorten or lengthen the lifetime

fn append<'a>(appender: &mut Appender<'a>, value: &'a str) {
    appender.buffer.push(value.to_string());
}
```

Invariance here protects you from writing a short-lived value into a buffer that's expected to contain long-lived values.

### Situation 3: PhantomData Variance Control

When you're writing unsafe code, you *choose* the variance through `PhantomData`:

```rust
use std::marker::PhantomData;

// If your type logically owns T, use PhantomData<T> — covariant
struct MyBox<T> {
    ptr: *mut T,
    _marker: PhantomData<T>,  // covariant in T
}

// If your type mutably borrows T, use PhantomData<&'a mut T> or
// PhantomData<fn(T)> — invariant
struct MyCell<T> {
    value: std::cell::UnsafeCell<T>,
    _marker: PhantomData<fn(T) -> T>,  // invariant in T
}

// If your type is like a callback receiver, use PhantomData<fn(T)> — contravariant
struct Handler<T> {
    _marker: PhantomData<fn(T)>,  // contravariant in T
}
```

Getting variance wrong in unsafe code is a soundness bug. This is one of the most common sources of unsoundness in Rust crates.

## The Drop Check

Variance interacts with the drop checker in subtle ways. The drop checker needs to know whether your type's destructor will access borrowed data:

```rust
use std::marker::PhantomData;

struct Inspector<'a, T: 'a> {
    data: &'a T,
}

impl<'a, T> Drop for Inspector<'a, T> {
    fn drop(&mut self) {
        // Accessing self.data in the destructor
        println!("Inspecting data before drop");
    }
}
```

Because `Inspector` accesses `data` in its `Drop` impl, the borrow must be valid at drop time. The drop checker uses variance to determine this. If you use `PhantomData` incorrectly, you might tell the compiler your type is covariant when it should be invariant, and the drop checker might allow unsound programs.

The rule of thumb: if your `Drop` implementation accesses borrowed data through a raw pointer, you need `PhantomData<&'a T>` or `PhantomData<T>` (not `PhantomData<*const T>`) to give the drop checker the right information.

## A Practical Variance Bug

Here's a real-ish bug I've seen in the wild:

```rust
use std::marker::PhantomData;

struct IterMut<'a, T> {
    ptr: *mut T,
    end: *mut T,
    _marker: PhantomData<&'a T>,  // BUG: should be &'a mut T
}
```

This says `IterMut` is covariant in `T`. But `IterMut` yields `&mut T` references — it should be invariant in `T`. With covariance, you could potentially substitute types in unsound ways.

The fix:

```rust
use std::marker::PhantomData;

struct IterMut<'a, T> {
    ptr: *mut T,
    end: *mut T,
    _marker: PhantomData<&'a mut T>,  // FIXED: invariant in T, covariant in 'a
}
```

`PhantomData<&'a mut T>` gives you exactly the right variance: covariant in `'a` (the lifetime can shrink) but invariant in `T` (the type can't change).

## How Variance Propagates

The variance of a struct is determined by the variance of its fields. If a struct has multiple fields with different variances, the most restrictive one wins:

```rust
use std::marker::PhantomData;

struct Mixed<'a, T> {
    // covariant in T (through PhantomData<T>)
    _covariant: PhantomData<T>,
    // invariant in T (through PhantomData<fn(T) -> T>)
    _invariant: PhantomData<fn(T) -> T>,
}
// Result: Mixed is INVARIANT in T (invariant wins over covariant)
```

Invariance is "sticky" — if any field makes the type invariant in some parameter, the whole type is invariant in that parameter.

## Debugging Variance Issues

When you hit a lifetime error that doesn't make sense, ask yourself:

1. **Is there a mutable reference involved?** Mutable references make things invariant. Check if you're trying to coerce a lifetime through a `&mut`.

2. **Are you passing a function or closure?** Function arguments are contravariant, which can flip the direction of subtyping.

3. **Is there a `Cell`, `RefCell`, or `UnsafeCell`?** Interior mutability implies invariance.

4. **Are you using `PhantomData` correctly?** If you're writing unsafe code, double-check that your `PhantomData` markers express the right variance.

The compiler's error messages for variance issues have gotten much better, but they still sometimes just say "lifetime mismatch" without explaining *why*. Knowing about variance lets you reason about these errors from first principles.

## The Big Picture

Variance is one of those concepts that seems overly academic until it saves you from a soundness bug. The compiler handles it automatically for safe code — you rarely need to think about it explicitly. But when you're writing unsafe code, implementing raw pointer wrappers, or building abstractions over lifetimes, variance is essential.

The key takeaways:

- **Covariant** = "longer lifetimes are fine" (immutable access)
- **Invariant** = "exact lifetime required" (mutable access)
- **Contravariant** = "shorter lifetimes are fine" (function arguments)
- **`PhantomData`** controls variance in unsafe code
- **When in doubt, invariant is the safe choice**

Next lesson, we're going to compute with types — type-level integers and compile-time arithmetic. It's going to get weird.
