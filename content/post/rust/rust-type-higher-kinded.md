---
title: "Lesson 4: Simulating Higher-Kinded Types in Rust — The workarounds"
author: Atharva Pandey
series: "Rust Type System Wizardry"
lesson: 4
keywords: [Rust, rust, type system, higher-kinded types, HKT, GAT, generic associated types, functors]
tags: [Rust tutorial, rust, type-system, advanced]
date: '2025-09-07T19:03:00.000Z'
---

Every time someone on Reddit says "Rust can't do higher-kinded types," a part of me wants to respond with a 200-line code block that proves them... well, partially wrong. Rust doesn't have *native* HKTs, that's true. But the workarounds are surprisingly expressive, and with GATs (generic associated types) now stable, we can get *most* of what you'd want from HKTs in practice.

Let me walk you through the problem, why it matters, and the patterns that let you work around it.

## What Are Higher-Kinded Types?

In Rust, you can be generic over a *type*: `Vec<T>`, `Option<T>`, `Result<T, E>`. The parameter `T` is a concrete type — `i32`, `String`, whatever.

But what if you want to be generic over the *container itself*? Not "a Vec of something" but "any container that wraps a single value" — `Option`, `Vec`, `Box`, etc.

In Haskell, you'd write:

```haskell
class Functor f where
    fmap :: (a -> b) -> f a -> f b
```

Here, `f` isn't a type — it's a *type constructor*. It takes a type parameter. `f` has kind `* -> *` (takes one type, produces one type). That's a higher-kinded type.

Rust's trait system can't express this directly:

```rust
// This is what we WANT to write, but can't:
// trait Functor<F<_>> {
//     fn fmap<A, B>(self: F<A>, f: impl Fn(A) -> B) -> F<B>;
// }
```

There's no syntax for `F<_>` — a type that itself takes a parameter.

## Why Does This Matter?

Without HKTs, you can't write truly generic abstractions over containers. Consider: you want a function that doubles every element in any container. With HKTs, you'd write one function. Without them, you write separate implementations for `Vec`, `Option`, `Result`, and every other container.

More practically, HKTs are needed for:
- **Monad transformers** (stacking effects)
- **Generic traversals** (mapping over any container structure)
- **Abstract interpreters** (swapping between `Option`, `Result`, `Future`, etc.)

## Workaround 1: The "Family" Pattern

The classic Rust workaround uses a trait with associated types to simulate a type constructor:

```rust
// A "type family" — something that maps types to types
trait TypeFamily {
    type Of<T>;
}

// Option is a type family
struct OptionFamily;
impl TypeFamily for OptionFamily {
    type Of<T> = Option<T>;
}

// Vec is a type family
struct VecFamily;
impl TypeFamily for VecFamily {
    type Of<T> = Vec<T>;
}

// Result<_, E> is a type family (partially applied)
struct ResultFamily<E>(std::marker::PhantomData<E>);
impl<E> TypeFamily for ResultFamily<E> {
    type Of<T> = Result<T, E>;
}
```

Now `OptionFamily` acts like the type constructor `Option` — you apply it to a type using `<OptionFamily as TypeFamily>::Of<i32>`, which gives you `Option<i32>`.

This uses GATs (the `type Of<T>` with a generic parameter on the associated type), which are stable since Rust 1.65.

## Workaround 2: Functor Trait

With the family pattern in place, we can define a `Functor`:

```rust
trait TypeFamily {
    type Of<T>;
}

trait Functor: TypeFamily {
    fn fmap<A, B>(value: Self::Of<A>, f: impl Fn(A) -> B) -> Self::Of<B>;
}

struct OptionFamily;
impl TypeFamily for OptionFamily {
    type Of<T> = Option<T>;
}

impl Functor for OptionFamily {
    fn fmap<A, B>(value: Option<A>, f: impl Fn(A) -> B) -> Option<B> {
        value.map(f)
    }
}

struct VecFamily;
impl TypeFamily for VecFamily {
    type Of<T> = Vec<T>;
}

impl Functor for VecFamily {
    fn fmap<A, B>(value: Vec<A>, f: impl Fn(A) -> B) -> Vec<B> {
        value.into_iter().map(f).collect()
    }
}

// Now we can write generic code over any Functor:
fn double_all<F: Functor>(values: F::Of<i32>) -> F::Of<i32>
where
    F::Of<i32>: std::fmt::Debug,
{
    F::fmap(values, |x| x * 2)
}

fn main() {
    let opt_result = double_all::<OptionFamily>(Some(21));
    println!("{:?}", opt_result); // Some(42)

    let vec_result = double_all::<VecFamily>(vec![1, 2, 3]);
    println!("{:?}", vec_result); // [2, 4, 6]
}
```

This actually works! We wrote `double_all` once and it works with both `Option` and `Vec`. The `F: Functor` bound is essentially our "higher-kinded" abstraction.

## Workaround 3: Monad (Yes, Really)

If we can do Functor, can we do Monad? Yes:

```rust
trait TypeFamily {
    type Of<T>;
}

trait Functor: TypeFamily {
    fn fmap<A, B>(value: Self::Of<A>, f: impl Fn(A) -> B) -> Self::Of<B>;
}

trait Monad: Functor {
    fn pure<A>(value: A) -> Self::Of<A>;
    fn bind<A, B>(value: Self::Of<A>, f: impl Fn(A) -> Self::Of<B>) -> Self::Of<B>;
}

struct OptionFamily;
impl TypeFamily for OptionFamily {
    type Of<T> = Option<T>;
}

impl Functor for OptionFamily {
    fn fmap<A, B>(value: Option<A>, f: impl Fn(A) -> B) -> Option<B> {
        value.map(f)
    }
}

impl Monad for OptionFamily {
    fn pure<A>(value: A) -> Option<A> {
        Some(value)
    }

    fn bind<A, B>(value: Option<A>, f: impl Fn(A) -> Option<B>) -> Option<B> {
        value.and_then(f)
    }
}

struct VecFamily;
impl TypeFamily for VecFamily {
    type Of<T> = Vec<T>;
}

impl Functor for VecFamily {
    fn fmap<A, B>(value: Vec<A>, f: impl Fn(A) -> B) -> Vec<B> {
        value.into_iter().map(f).collect()
    }
}

impl Monad for VecFamily {
    fn pure<A>(value: A) -> Vec<A> {
        vec![value]
    }

    fn bind<A, B>(value: Vec<A>, f: impl Fn(A) -> Vec<B>) -> Vec<B> {
        value.into_iter().flat_map(f).collect()
    }
}

// Generic monadic computation!
fn safe_divide<M: Monad>(x: i32, y: i32) -> M::Of<i32>
where
    M: Monad,
{
    if y == 0 {
        // We can't represent "failure" generically without more machinery,
        // but we can use pure for the success case
        M::pure(0) // placeholder
    } else {
        M::pure(x / y)
    }
}

// A more useful example: chaining monadic operations
fn pipeline<M: Monad>(input: M::Of<i32>) -> M::Of<String> {
    let doubled = M::bind(input, |x| M::pure(x * 2));
    let as_string = M::bind(doubled, |x| M::pure(format!("Result: {}", x)));
    as_string
}

fn main() {
    let opt = pipeline::<OptionFamily>(Some(21));
    println!("{:?}", opt); // Some("Result: 42")

    let vec = pipeline::<VecFamily>(vec![1, 2, 3]);
    println!("{:?}", vec); // ["Result: 2", "Result: 4", "Result: 6"]
}
```

We just wrote monadic code in Rust. It's not as clean as Haskell's `do` notation, sure. But it works. The types check out. The abstraction is real.

## The Defunctionalization Approach

Another technique is defunctionalization — representing type-level functions as data. Instead of passing a type constructor, you pass a "recipe" for how to construct a type:

```rust
use std::marker::PhantomData;

// A "type-level function" from types to types
trait Apply {
    type Result<T>;
}

// "Apply Option to T"
struct ApplyOption;
impl Apply for ApplyOption {
    type Result<T> = Option<T>;
}

// "Apply Vec to T"
struct ApplyVec;
impl Apply for ApplyVec {
    type Result<T> = Vec<T>;
}

// "Apply Box to T"
struct ApplyBox;
impl Apply for ApplyBox {
    type Result<T> = Box<T>;
}

// Now we can write functions generic over the "constructor"
fn wrap_in<F: Apply>(value: i32) -> F::Result<i32>
where
    F::Result<i32>: From<i32>,
{
    // This requires that F::Result<i32> implements From<i32>
    F::Result::<i32>::from(value)
}
```

This is essentially the same as the family pattern, just with different naming. The key realization is that both approaches are encoding the same thing: a type-level function.

## GATs: The Game Changer

Generic Associated Types made all of this much more ergonomic. Before GATs stabilized, you'd need truly horrific workarounds:

```rust
// PRE-GAT HORROR: encoding HKT without GATs
// Don't do this anymore.
trait FunctorOld {
    type Unwrapped;
    type Wrapped<T>: FunctorOld;
    // This didn't work because Wrapped couldn't have a generic parameter
    // before GATs. You'd need something like:
}

// The old workaround used separate traits for each type:
trait FunctorOldWorkaround {
    type Unwrapped;
    type Rewrapped;  // Can't parameterize this!
}
// Which meant you couldn't actually write generic fmap.
// You'd need a separate FunctorMap<Target> trait. Nightmare.
```

With GATs:

```rust
// POST-GAT BEAUTY
trait Container {
    type Item;
    type WithItem<T>: Container<Item = T>;

    fn map_items<B>(self, f: impl Fn(Self::Item) -> B) -> Self::WithItem<B>;
}

impl<A> Container for Option<A> {
    type Item = A;
    type WithItem<T> = Option<T>;

    fn map_items<B>(self, f: impl Fn(A) -> B) -> Option<B> {
        self.map(f)
    }
}

impl<A> Container for Vec<A> {
    type Item = A;
    type WithItem<T> = Vec<T>;

    fn map_items<B>(self, f: impl Fn(A) -> B) -> Vec<B> {
        self.into_iter().map(f).collect()
    }
}

// Generic function works beautifully
fn stringify_all<C: Container<Item = i32>>(container: C) -> C::WithItem<String> {
    container.map_items(|x| x.to_string())
}

fn main() {
    let opt = stringify_all(Some(42));
    println!("{:?}", opt); // Some("42")

    let vec = stringify_all(vec![1, 2, 3]);
    println!("{:?}", vec); // ["1", "2", "3"]
}
```

This is clean. Readable. And it gives us the thing we actually wanted — generic code over container types. GATs were the single biggest addition to Rust's type system since associated types themselves.

## Practical Use Case: Generic Data Layer

Here's a pattern I've actually used in production. Say you have a data layer that might return results eagerly (sync) or lazily (async). You want the business logic to be generic over both:

```rust
use std::future::Future;
use std::pin::Pin;

trait Effect {
    type Of<T>;
    fn pure<T>(value: T) -> Self::Of<T>;
    fn map<A, B>(value: Self::Of<A>, f: impl Fn(A) -> B + 'static) -> Self::Of<B>;
}

// Synchronous / identity effect
struct Sync;
impl Effect for Sync {
    type Of<T> = T;
    fn pure<T>(value: T) -> T { value }
    fn map<A, B>(value: A, f: impl Fn(A) -> B + 'static) -> B { f(value) }
}

// Async effect (simplified)
struct Async;
impl Effect for Async {
    type Of<T> = Pin<Box<dyn Future<Output = T>>>;
    fn pure<T>(value: T) -> Pin<Box<dyn Future<Output = T>>> {
        Box::pin(async move { value })
    }
    fn map<A, B>(value: Pin<Box<dyn Future<Output = A>>>, f: impl Fn(A) -> B + 'static) -> Pin<Box<dyn Future<Output = B>>> {
        Box::pin(async move { f(value.await) })
    }
}

// Business logic generic over the effect
trait UserRepository<E: Effect> {
    fn find_by_id(&self, id: u64) -> E::Of<Option<String>>;
}

// Sync implementation for testing
struct InMemoryUsers;
impl UserRepository<Sync> for InMemoryUsers {
    fn find_by_id(&self, id: u64) -> Option<String> {
        if id == 1 { Some("Alice".to_string()) } else { None }
    }
}

fn main() {
    let repo = InMemoryUsers;
    let user = repo.find_by_id(1);
    println!("{:?}", user); // Some("Alice")
}
```

The `Effect` trait is essentially a higher-kinded type — it maps `T` to `Of<T>`, which could be `T` itself (sync) or `Future<Output = T>` (async). Your business logic doesn't know which one it's using. Swap the effect type parameter, and the same code runs synchronously for tests and asynchronously in production.

## Limitations — Being Honest

The HKT workarounds have real limitations:

1. **Trait coherence**: You can't implement `Container` for `Result<T, E>` if someone else already has, and the orphan rules get tricky.

2. **Inference**: The compiler sometimes struggles to infer the right types, and you'll end up with turbofish operators everywhere.

3. **No higher-order type constructors**: You can simulate `* -> *` (one type parameter), but `* -> * -> *` (two parameters, like `Result`) requires more machinery.

4. **Verbosity**: Every "HKT" pattern requires a family struct, trait impls, and associated type annotations. Haskell gives you this with `class Functor f where fmap :: ...` — one line.

But here's my take: for 90% of real-world Rust code, you don't need full HKTs. GATs cover the practical cases. The remaining 10% is best handled by either code generation or accepting a bit of boilerplate.

Rust is pragmatic. It gives you enough type-level power to write safe, generic code without going full type-theory-PhD. And honestly? That's the right tradeoff.

Next lesson, we'll look at existential types and `impl Trait` — a feature that's deceptively simple on the surface but deep enough to trip up experienced Rustaceans.
