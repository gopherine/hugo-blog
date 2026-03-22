---
title: "Lesson 3: Trait Bounds — Constraining generic types"
author: Atharva Pandey
series: "Rust Traits & Generics Masterclass"
lesson: 3
keywords: [Rust, rust, traits, generics, trait bounds, generic constraints, type parameters]
tags: [Rust tutorial, rust, traits, generics]
date: '2024-06-14T21:10:00.000Z'
---

I once wrote a generic function that looked perfectly reasonable — accepted any `T`, did some work, returned a result. It compiled. Then I tried to actually *use* it with a type that didn't have `Clone`, and suddenly the compiler was screaming at me with errors pointing at the function internals rather than the call site. That's backwards. The fix? Trait bounds. Declare what you need upfront so the errors land where they belong.

Trait bounds are how you tell the compiler: "this generic type parameter isn't just *any* type — it's any type that can do *these specific things*."

## The Syntax

There are two ways to write trait bounds, and they mean exactly the same thing:

```rust
use std::fmt::Display;

// Shorthand — impl Trait syntax
fn print_it(item: &impl Display) {
    println!("{}", item);
}

// Full form — trait bound syntax
fn print_it_full<T: Display>(item: &T) {
    println!("{}", item);
}

fn main() {
    print_it(&42);
    print_it_full(&"hello");
}
```

The `impl Trait` syntax from Lesson 1 is actually sugar for the full generic form. When you write `item: &impl Display`, the compiler desugars it to `<T: Display>(item: &T)`.

So why would you ever use the verbose form? Because sometimes you need the type parameter to appear in multiple places:

```rust
use std::fmt::Display;

// Both arguments must be the SAME type
fn compare_and_print<T: Display + PartialOrd>(a: &T, b: &T) {
    if a > b {
        println!("{} wins", a);
    } else {
        println!("{} wins", b);
    }
}

fn main() {
    compare_and_print(&10, &20);
    compare_and_print(&"alpha", &"beta");
}
```

With `impl Trait`, each parameter could be a *different* type. With `<T: ...>`, you're saying both `a` and `b` are the same `T`.

## Multiple Bounds with `+`

A single bound is often not enough. Real functions need types that satisfy multiple constraints:

```rust
use std::fmt::{Display, Debug};

fn log_and_display<T: Display + Debug>(item: &T) {
    println!("Display: {}", item);
    println!("Debug: {:?}", item);
}

fn main() {
    log_and_display(&42);
    log_and_display(&"test");
}
```

The `+` syntax reads naturally: `T` must implement `Display` *and* `Debug`. You can chain as many as you need, though if you're past three or four bounds, consider a `where` clause (Lesson 4).

## Trait Bounds on Struct Definitions

Bounds aren't limited to functions. You can constrain struct type parameters too:

```rust
use std::fmt::Display;

struct Wrapper<T: Display> {
    value: T,
}

impl<T: Display> Wrapper<T> {
    fn show(&self) {
        println!("Wrapped: {}", self.value);
    }
}

fn main() {
    let w = Wrapper { value: 42 };
    w.show();

    // This won't compile — Vec<i32> doesn't implement Display:
    // let w2 = Wrapper { value: vec![1, 2, 3] };
}
```

A word of caution though — putting bounds on struct definitions is sometimes debated. Some Rustaceans prefer keeping structs unconstrained and only adding bounds on the `impl` blocks that need them. The reasoning: you might want to *store* a type in the struct even if you don't use its trait methods everywhere. I lean toward bounds on the `impl` blocks rather than the struct, unless the struct literally can't make sense without the bound.

## Conditional Method Implementations

This is one of my favorite patterns. You can implement methods *only* for specific bound combinations:

```rust
use std::fmt::Display;

struct Pair<T> {
    first: T,
    second: T,
}

impl<T> Pair<T> {
    fn new(first: T, second: T) -> Self {
        Pair { first, second }
    }
}

// This method only exists when T is Display + PartialOrd
impl<T: Display + PartialOrd> Pair<T> {
    fn larger_display(&self) {
        if self.first >= self.second {
            println!("The larger one: {}", self.first);
        } else {
            println!("The larger one: {}", self.second);
        }
    }
}

fn main() {
    let pair = Pair::new(5, 10);
    pair.larger_display(); // Works — i32 is Display + PartialOrd

    let pair2 = Pair::new(String::from("a"), String::from("b"));
    pair2.larger_display(); // Works — String is Display + PartialOrd

    let pair3 = Pair::new(vec![1], vec![2]);
    // pair3.larger_display(); // Won't compile — Vec doesn't impl Display
}
```

`Pair::new` works for any `T`. But `larger_display` only appears when `T` satisfies both `Display` and `PartialOrd`. The compiler selectively makes methods available based on what the type parameter can do. This is incredibly powerful for library design.

## The Problem: Forgetting a Bound

Here's the mistake that tripped me up early on:

```rust
// Missing Clone bound!
fn duplicate<T>(item: T) -> (T, T) {
    (item.clone(), item) // ERROR: T doesn't implement Clone
}
```

The fix is obvious once you see it:

```rust
fn duplicate<T: Clone>(item: T) -> (T, T) {
    let copy = item.clone();
    (copy, item)
}

fn main() {
    let pair = duplicate(42);
    println!("{}, {}", pair.0, pair.1);

    let pair2 = duplicate(String::from("hello"));
    println!("{}, {}", pair2.0, pair2.1);
}
```

Every capability you use inside the function body must be declared in the bounds. Think of it as a contract with the caller: "I promise to only use these capabilities, and you promise your type has them."

## Bounds on Return Types

You can use bounds to constrain what a function returns:

```rust
fn make_pair<T: Default + Clone>() -> (T, T) {
    let val = T::default();
    (val.clone(), val)
}

fn main() {
    let pair: (i32, i32) = make_pair();
    println!("{}, {}", pair.0, pair.1); // 0, 0

    let pair2: (String, String) = make_pair();
    println!("'{}', '{}'", pair2.0, pair2.1); // '', ''

    let pair3: (f64, f64) = make_pair();
    println!("{}, {}", pair3.0, pair3.1); // 0.0, 0.0
}
```

The caller's type annotation drives inference — `make_pair::<i32>()` and `make_pair::<String>()` produce different types at zero runtime cost.

## A Real-World Example: Generic Repository

Here's a pattern I reach for in application code — a generic in-memory store constrained by trait bounds:

```rust
use std::collections::HashMap;
use std::fmt::Display;
use std::hash::Hash;

trait Entity: Display + Clone {
    type Id: Eq + Hash + Clone + Display;
    fn id(&self) -> &Self::Id;
}

struct InMemoryStore<E: Entity> {
    data: HashMap<E::Id, E>,
}

impl<E: Entity> InMemoryStore<E> {
    fn new() -> Self {
        InMemoryStore {
            data: HashMap::new(),
        }
    }

    fn insert(&mut self, entity: E) {
        let id = entity.id().clone();
        println!("Storing entity {}", id);
        self.data.insert(id, entity);
    }

    fn get(&self, id: &E::Id) -> Option<&E> {
        self.data.get(id)
    }

    fn list(&self) {
        for entity in self.data.values() {
            println!("  - {}", entity);
        }
    }
}

#[derive(Clone)]
struct User {
    id: u64,
    name: String,
}

impl Display for User {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "User({}, {})", self.id, self.name)
    }
}

impl Entity for User {
    type Id = u64;
    fn id(&self) -> &u64 {
        &self.id
    }
}

fn main() {
    let mut store = InMemoryStore::new();
    store.insert(User { id: 1, name: String::from("Atharva") });
    store.insert(User { id: 2, name: String::from("Alice") });

    if let Some(user) = store.get(&1) {
        println!("Found: {}", user);
    }

    println!("All users:");
    store.list();
}
```

The bounds cascade: `Entity` requires `Display + Clone`, and `Entity::Id` requires `Eq + Hash + Clone + Display`. Each bound exists because the implementation actually uses that capability. No speculative bounds, no "just in case" constraints.

## Common Standard Library Bounds

You'll see these over and over:

| Bound | Meaning |
|-------|---------|
| `Clone` | Can be duplicated |
| `Copy` | Can be duplicated implicitly (bitwise copy) |
| `Debug` | Can be formatted with `{:?}` |
| `Display` | Can be formatted with `{}` |
| `Default` | Has a default value |
| `PartialEq` / `Eq` | Can be compared for equality |
| `PartialOrd` / `Ord` | Can be ordered |
| `Hash` | Can be hashed (for HashMap keys) |
| `Send` | Safe to send across threads |
| `Sync` | Safe to share references across threads |
| `Sized` | Has a known size at compile time (implicit default) |

Most generic code you write will use some combination of these. Knowing them cold saves a lot of compiler-error-driven-development.

## Key Takeaways

Trait bounds constrain generic type parameters to types that implement specific traits. Use `<T: Trait>` or `impl Trait` syntax. Combine bounds with `+`. Place bounds on `impl` blocks for conditional method availability.

The mental model: bounds are a contract. The function promises to only use declared capabilities. The caller promises to provide a type that has them. The compiler enforces both sides.

Next up — `where` clauses, for when your bounds get long enough to hurt readability.
