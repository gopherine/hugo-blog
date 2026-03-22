---
title: "Lesson 15: GATs — Generic associated types explained"
author: Atharva Pandey
series: "Rust Traits & Generics Masterclass"
lesson: 15
keywords: [Rust, rust, traits, generics, GATs, generic associated types, higher-kinded types, lending iterator]
tags: [Rust tutorial, rust, traits, generics]
date: '2024-07-12T18:00:00.000Z'
---

GATs (Generic Associated Types) took *seven years* from proposal to stabilization. That's not because Rust's team is slow — it's because GATs are genuinely hard to get right, and they unlock patterns that were previously impossible without unsafe code or painful workarounds. When they finally landed in Rust 1.65, I immediately rewrote a chunk of a database abstraction layer that had been haunting me for months.

The one-liner: GATs let associated types have their own generic parameters, including lifetimes.

## The Problem GATs Solve

Imagine you want a trait for "something that can lend you a reference to its data":

```rust
// WITHOUT GATs — this doesn't work the way you want
trait Container {
    type Item;
    fn get(&self, index: usize) -> Option<&Self::Item>;
}
```

This works for owned data. But what if the returned reference's lifetime depends on the borrowing context? What if `Item` itself needs to be generic over a lifetime? Before GATs, you were stuck.

The classic example is a "lending iterator" — an iterator where each item borrows from the iterator itself, not from some underlying collection:

```rust
// WITHOUT GATs — IMPOSSIBLE to express
// trait LendingIterator {
//     type Item<'a>;  // <-- This syntax didn't exist before GATs
//     fn next(&mut self) -> Option<Self::Item<'_>>;
// }
```

You couldn't write `type Item<'a>` — associated types couldn't have generic parameters. GATs fix that.

## GATs: The Syntax

```rust
trait LendingIterator {
    type Item<'a> where Self: 'a;

    fn next(&mut self) -> Option<Self::Item<'_>>;
}
```

`type Item<'a>` is a generic associated type. It's an associated type that takes a lifetime parameter. The `where Self: 'a` bound ensures the iterator lives at least as long as the borrow.

Here's a concrete implementation:

```rust
trait LendingIterator {
    type Item<'a> where Self: 'a;
    fn next(&mut self) -> Option<Self::Item<'_>>;
}

struct WindowIter<'data> {
    data: &'data [i32],
    pos: usize,
    window_size: usize,
}

impl<'data> WindowIter<'data> {
    fn new(data: &'data [i32], window_size: usize) -> Self {
        WindowIter { data, pos: 0, window_size }
    }
}

impl<'data> LendingIterator for WindowIter<'data> {
    type Item<'a> = &'a [i32] where Self: 'a;

    fn next(&mut self) -> Option<&[i32]> {
        if self.pos + self.window_size > self.data.len() {
            return None;
        }
        let window = &self.data[self.pos..self.pos + self.window_size];
        self.pos += 1;
        Some(window)
    }
}

fn main() {
    let data = vec![1, 2, 3, 4, 5, 6, 7];
    let mut iter = WindowIter::new(&data, 3);

    while let Some(window) = iter.next() {
        println!("{:?}", window);
    }
    // [1, 2, 3]
    // [2, 3, 4]
    // [3, 4, 5]
    // [4, 5, 6]
    // [5, 6, 7]
}
```

Each window borrows from the iterator's underlying data. The lifetime of the returned slice is tied to the iterator's borrow — that's what `type Item<'a> where Self: 'a` expresses.

## Why Regular Associated Types Can't Do This

With a regular `type Item`, the associated type is fixed for the entire lifetime of the trait implementation. You can't say "the item's lifetime depends on how long you borrow the iterator."

Standard `Iterator` works around this because items are typically owned or borrow from an external source (not the iterator itself). But for patterns where the *iterator* owns the data and lends references to it, you need GATs.

## GATs with Type Parameters

GATs aren't limited to lifetimes. They can have type parameters too:

```rust
trait Collection {
    type Item;
    type Iter<'a>: Iterator<Item = &'a Self::Item> where Self: 'a;

    fn iter(&self) -> Self::Iter<'_>;
    fn len(&self) -> usize;
}

struct VecWrapper<T> {
    data: Vec<T>,
}

impl<T> VecWrapper<T> {
    fn new(data: Vec<T>) -> Self {
        VecWrapper { data }
    }
}

impl<T: 'static> Collection for VecWrapper<T> {
    type Item = T;
    type Iter<'a> = std::slice::Iter<'a, T> where Self: 'a;

    fn iter(&self) -> std::slice::Iter<'_, T> {
        self.data.iter()
    }

    fn len(&self) -> usize {
        self.data.len()
    }
}

fn print_all<C: Collection>(collection: &C)
where
    C::Item: std::fmt::Debug,
{
    print!("[");
    for (i, item) in collection.iter().enumerate() {
        if i > 0 { print!(", "); }
        print!("{:?}", item);
    }
    println!("] ({} items)", collection.len());
}

fn main() {
    let v = VecWrapper::new(vec![1, 2, 3, 4, 5]);
    print_all(&v);

    let s = VecWrapper::new(vec!["hello", "world"]);
    print_all(&s);
}
```

The key line is `type Iter<'a>: Iterator<Item = &'a Self::Item> where Self: 'a`. This says: "the iterator type is parameterized by a lifetime and must yield references to our items." Different collections can return different iterator types, but they all satisfy the same interface.

## Real-World Pattern: Generic Storage Backend

This is the pattern that made me rewrite my database layer. Before GATs, I couldn't express "a storage backend that returns references tied to the query lifetime":

```rust
use std::collections::HashMap;

trait Storage {
    type Value;
    type Ref<'a>: AsRef<Self::Value> where Self: 'a;

    fn get<'a>(&'a self, key: &str) -> Option<Self::Ref<'a>>;
    fn insert(&mut self, key: String, value: Self::Value);
}

// In-memory storage — returns direct references
struct MemoryStorage {
    data: HashMap<String, String>,
}

impl MemoryStorage {
    fn new() -> Self {
        MemoryStorage { data: HashMap::new() }
    }
}

impl Storage for MemoryStorage {
    type Value = String;
    type Ref<'a> = &'a String;

    fn get<'a>(&'a self, key: &str) -> Option<&'a String> {
        self.data.get(key)
    }

    fn insert(&mut self, key: String, value: String) {
        self.data.insert(key, value);
    }
}

// Cached storage — returns references into the cache
struct CachedStorage {
    primary: HashMap<String, String>,
    cache: HashMap<String, String>,
}

impl CachedStorage {
    fn new() -> Self {
        CachedStorage {
            primary: HashMap::new(),
            cache: HashMap::new(),
        }
    }
}

impl Storage for CachedStorage {
    type Value = String;
    type Ref<'a> = &'a String;

    fn get<'a>(&'a self, key: &str) -> Option<&'a String> {
        self.cache.get(key).or_else(|| self.primary.get(key))
    }

    fn insert(&mut self, key: String, value: String) {
        self.cache.insert(key.clone(), value.clone());
        self.primary.insert(key, value);
    }
}

fn lookup<S: Storage>(store: &S, key: &str)
where
    S::Value: std::fmt::Display,
{
    match store.get(key) {
        Some(val) => println!("{} = {}", key, val.as_ref()),
        None => println!("{} not found", key),
    }
}

fn main() {
    let mut mem = MemoryStorage::new();
    mem.insert(String::from("name"), String::from("Atharva"));
    mem.insert(String::from("lang"), String::from("Rust"));
    lookup(&mem, "name");
    lookup(&mem, "missing");

    let mut cached = CachedStorage::new();
    cached.insert(String::from("host"), String::from("localhost"));
    lookup(&cached, "host");
}
```

Without GATs, `type Ref<'a>` would need to be a concrete type, and you couldn't tie it to the storage's lifetime. You'd be forced to return owned data (cloning) or use `Box<dyn>` everywhere.

## GATs for Async Traits (The Motivator)

One of the biggest motivations for GATs was async traits. Before GATs and the `async_fn_in_trait` feature, you couldn't have async methods in traits because the returned future's lifetime depended on `&self`:

```rust
// This pattern is what GATs enable under the hood for async traits
use std::future::Future;

trait AsyncProcessor {
    type ProcessFut<'a>: Future<Output = String> where Self: 'a;

    fn process<'a>(&'a self, input: &'a str) -> Self::ProcessFut<'a>;
}

struct UpperProcessor;

impl AsyncProcessor for UpperProcessor {
    type ProcessFut<'a> = std::pin::Pin<Box<dyn Future<Output = String> + 'a>>;

    fn process<'a>(&'a self, input: &'a str) -> Self::ProcessFut<'a> {
        Box::pin(async move {
            // simulate async work
            input.to_uppercase()
        })
    }
}

#[tokio::main]
async fn main() {
    let processor = UpperProcessor;
    let result = processor.process("hello world").await;
    println!("{}", result);
}
```

Note: with modern Rust (1.75+), you can just write `async fn` in traits directly. But GATs are what made that possible under the surface.

Here's the simpler modern version without needing explicit GATs:

```rust
// Modern Rust — async fn in traits (uses GATs internally)
trait AsyncProcessor {
    async fn process(&self, input: &str) -> String;
}

struct UpperProcessor;

impl AsyncProcessor for UpperProcessor {
    async fn process(&self, input: &str) -> String {
        input.to_uppercase()
    }
}

// But for dyn dispatch, you still need the GAT form or use
// the async-trait crate
```

## Pattern: Family of Types

GATs let you express "type families" — related types that share a pattern:

```rust
trait Pointer {
    type Ref<'a, T: 'a>: std::ops::Deref<Target = T>;
}

struct Owned;
struct Borrowed;

impl Pointer for Owned {
    type Ref<'a, T: 'a> = Box<T>;
}

impl Pointer for Borrowed {
    type Ref<'a, T: 'a> = &'a T;
}

fn create_ref<'a, P: Pointer>(value: &'a i32) -> P::Ref<'a, i32>
where
    P::Ref<'a, i32>: From<&'a i32>,
{
    P::Ref::from(value)
}

// Usage depends on which Pointer type you choose
fn main() {
    let val = 42;

    // With Borrowed, you get &i32
    let borrowed: &i32 = create_ref::<Borrowed>(&val);
    println!("Borrowed: {}", borrowed);
}
```

## When You Actually Need GATs

Be honest — most Rust code doesn't need GATs. They solve specific problems:

1. **Lending iterators** — when items borrow from the iterator itself
2. **Generic collection traits** — when iterator types depend on borrow lifetimes
3. **Storage abstractions** — when returned references are tied to the backend's lifetime
4. **Async trait patterns** — when futures borrow from `self`
5. **Higher-kinded type emulation** — when you need to parameterize over type constructors

If you're not hitting one of these walls, regular associated types and generic parameters are simpler and sufficient.

## The Mental Model

Think of GATs as "associated types that are type constructors rather than concrete types."

Regular associated type: `type Item = String;` — it's a specific type.
GAT: `type Item<'a> = &'a str;` — it's a *function from lifetime to type*. Give me a lifetime, I'll give you a type.

This is what type theorists call "higher-kinded types" — types that take parameters to produce other types. Rust doesn't have full HKT support, but GATs cover the most common use cases.

## Key Takeaways

GATs let associated types have their own generic parameters (lifetimes or types). They enable lending iterators, generic collection traits, and lifetime-dependent return types in traits. They're the foundation for async fn in traits. Use them when regular associated types can't express the relationship between lifetimes and types. For everything else, keep it simple.

---

That wraps the Rust Traits & Generics Masterclass. Fifteen lessons from basic trait definitions to the bleeding edge of the type system. The progression was deliberate — traits as contracts, then bounds as constraints, then dispatch as a runtime/compile-time tradeoff, then the standard library traits that make everything click, and finally the advanced machinery (blanket impls, orphan rules, monomorphization, const generics, GATs) that lets you build truly generic, zero-cost abstractions.

The traits system is Rust's most important feature. Not the borrow checker — that prevents bugs. Traits enable *design*. Master them and you'll write code that's both flexible and fast, without compromise.
