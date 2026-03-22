---
title: "Lesson 8: Object Safety — Why some traits can't be dyn"
author: Atharva Pandey
series: "Rust Traits & Generics Masterclass"
lesson: 8
keywords: [Rust, rust, traits, generics, object safety, dyn trait, trait objects, Sized]
tags: [Rust tutorial, rust, traits, generics]
date: '2024-06-25T13:40:00.000Z'
---

The first time I got the error "the trait `Clone` cannot be made into an object," I stared at it for a solid minute. Clone is one of the most fundamental traits in Rust. How can it not work with `dyn`? The answer is *object safety* — a set of rules that determine which traits can be used as trait objects. It's one of those things that seems arbitrary until you understand *why* the rules exist.

## The Core Issue

When you use `dyn Trait`, the compiler erases the concrete type. All it has is a vtable of function pointers. For that vtable to work, every method in the trait must be callable *without knowing the concrete type*. Some method signatures make that impossible.

## The Rules

A trait is object-safe if all its methods satisfy these conditions:

1. The method's receiver is `&self`, `&mut self`, `self: Box<Self>`, `self: Pin<&Self>`, etc. — some form of `self`.
2. The method does NOT use `Self` in argument or return position (except as the receiver).
3. The method does NOT have generic type parameters.
4. The trait does NOT require `Self: Sized`.

Let me show each violation and why it breaks things.

## Violation 1: `Self` in Return Position

```rust
trait Clonable {
    fn clone_it(&self) -> Self; // Returns Self — NOT object safe
}
```

Why? If you have a `&dyn Clonable`, the vtable needs to know the return type's size to allocate stack space. But `Self` is erased — you don't know if it's a 1-byte `bool` or a 1KB struct. The compiler can't generate the function call.

This is exactly why `Clone` isn't object-safe. Its signature is `fn clone(&self) -> Self`.

```rust
// This will NOT compile:
// fn do_something(item: &dyn Clone) { ... }
```

### The Workaround: Return `Box<dyn Trait>`

```rust
trait CloneBox {
    fn clone_box(&self) -> Box<dyn CloneBox>;
}

#[derive(Clone)]
struct Data {
    value: i32,
}

impl CloneBox for Data {
    fn clone_box(&self) -> Box<dyn CloneBox> {
        Box::new(self.clone())
    }
}

fn duplicate(item: &dyn CloneBox) -> Box<dyn CloneBox> {
    item.clone_box()
}

fn main() {
    let original = Data { value: 42 };
    let copy = duplicate(&original);
    // We can't easily access .value through dyn, but the clone exists
    let another = copy.clone_box();
    println!("Cloned successfully");
}
```

By returning `Box<dyn CloneBox>` instead of `Self`, the size is known — it's always a pointer. Object safety restored.

## Violation 2: Generic Methods

```rust
trait Converter {
    fn convert<T>(&self) -> T; // Generic method — NOT object safe
}
```

Why? The vtable is built once per trait implementation. But a generic method means *infinite* possible method instantiations — one for each `T`. You'd need an infinitely large vtable.

```rust
// This will NOT compile:
// fn use_converter(c: &dyn Converter) { ... }
```

### The Workaround: Use Associated Types or Concrete Types

```rust
trait Converter {
    type Output;
    fn convert(&self) -> Self::Output;
}

struct Celsius(f64);

impl Converter for Celsius {
    type Output = f64;
    fn convert(&self) -> f64 {
        self.0 * 9.0 / 5.0 + 32.0
    }
}

// But wait — this is STILL not fully object-safe for heterogeneous use
// because different implementors have different Output types.
// You'd need to fix the associated type:
fn use_converter(c: &dyn Converter<Output = f64>) {
    println!("Converted: {}", c.convert());
}

fn main() {
    let temp = Celsius(100.0);
    use_converter(&temp);
}
```

## Violation 3: `Self` in Argument Position

```rust
trait Comparable {
    fn same_as(&self, other: &Self) -> bool; // Self in args — NOT object safe
}
```

Why? If you have `&dyn Comparable`, the `other` parameter's type must also be `Self`. But `Self` is erased — you could be comparing a `Dog` with a `Cat` and there's no way for the vtable to enforce they're the same type.

### The Workaround: Use Concrete Types or `dyn Trait`

```rust
use std::fmt::Debug;

trait Comparable: Debug {
    fn same_as(&self, other: &dyn Comparable) -> bool;
    fn id(&self) -> u64;
}

#[derive(Debug)]
struct User { id: u64 }

#[derive(Debug)]
struct Post { id: u64 }

impl Comparable for User {
    fn same_as(&self, other: &dyn Comparable) -> bool {
        self.id() == other.id()
    }
    fn id(&self) -> u64 { self.id }
}

impl Comparable for Post {
    fn same_as(&self, other: &dyn Comparable) -> bool {
        self.id() == other.id()
    }
    fn id(&self) -> u64 { self.id }
}

fn main() {
    let u = User { id: 1 };
    let p = Post { id: 1 };
    println!("Same ID? {}", u.same_as(&p)); // true — same ID, different types
}
```

## The `Sized` Requirement

Every generic type parameter has an implicit `Sized` bound. When a trait requires `Self: Sized`, it can't be a trait object because `dyn Trait` is `!Sized` (its size isn't known at compile time).

```rust
// NOT object safe — requires Sized
trait NotObjectSafe: Sized {
    fn do_thing(&self);
}
```

But you can opt out of `Sized` for individual methods using a `where Self: Sized` bound on the method. This makes the *trait* object-safe while excluding specific methods:

```rust
trait Flexible {
    fn regular_method(&self) -> String;

    // This method is excluded from the vtable
    fn sized_only(&self) -> Self
    where
        Self: Sized + Clone,
    {
        self.clone()
    }
}

#[derive(Clone)]
struct Widget {
    name: String,
}

impl Flexible for Widget {
    fn regular_method(&self) -> String {
        format!("Widget: {}", self.name)
    }
}

fn use_dyn(item: &dyn Flexible) {
    println!("{}", item.regular_method());
    // item.sized_only(); // Can't call this — excluded from vtable
}

fn use_static(item: &Widget) {
    println!("{}", item.regular_method());
    let cloned = item.sized_only(); // CAN call this — Widget is Sized + Clone
    println!("Cloned: {}", cloned.regular_method());
}

fn main() {
    let w = Widget { name: String::from("Button") };
    use_dyn(&w);
    use_static(&w);
}
```

This pattern is everywhere in the standard library. Look at `Iterator` — it's object-safe, but methods like `collect` have `where Self: Sized` because they can't work through a vtable.

## A Complete Example: Making a Non-Object-Safe Trait Object-Safe

Here's the before and after of making a trait suitable for `dyn`:

```rust
use std::fmt::Debug;

// BEFORE: Not object safe
// trait Storage {
//     fn get<T: Debug>(&self, key: &str) -> Option<T>;  // generic method
//     fn clone_storage(&self) -> Self;                     // Self in return
//     fn merge(&self, other: &Self);                       // Self in args
// }

// AFTER: Object safe
trait Storage: Debug {
    fn get_string(&self, key: &str) -> Option<String>;
    fn clone_storage(&self) -> Box<dyn Storage>;
    fn merge(&self, other: &dyn Storage);
    fn keys(&self) -> Vec<String>;
}

#[derive(Debug, Clone)]
struct MemoryStorage {
    data: std::collections::HashMap<String, String>,
}

impl MemoryStorage {
    fn new() -> Self {
        MemoryStorage {
            data: std::collections::HashMap::new(),
        }
    }

    fn set(&mut self, key: &str, value: &str) {
        self.data.insert(key.to_string(), value.to_string());
    }
}

impl Storage for MemoryStorage {
    fn get_string(&self, key: &str) -> Option<String> {
        self.data.get(key).cloned()
    }

    fn clone_storage(&self) -> Box<dyn Storage> {
        Box::new(self.clone())
    }

    fn merge(&self, other: &dyn Storage) {
        // In a real impl, you'd merge the other's data into self
        println!("Merging {:?} with {:?}", self, other);
    }

    fn keys(&self) -> Vec<String> {
        self.data.keys().cloned().collect()
    }
}

fn print_all(storage: &dyn Storage) {
    for key in storage.keys() {
        if let Some(value) = storage.get_string(&key) {
            println!("  {} = {}", key, value);
        }
    }
}

fn main() {
    let mut store = MemoryStorage::new();
    store.set("name", "Atharva");
    store.set("lang", "Rust");

    print_all(&store);

    let cloned = store.clone_storage();
    print_all(&*cloned);
}
```

The three changes:
1. Generic method `get<T>` → concrete method `get_string`
2. `-> Self` → `-> Box<dyn Storage>`
3. `other: &Self` → `other: &dyn Storage`

Each change trades some static type safety for runtime flexibility. That's the cost of object safety.

## Quick Reference: Is It Object-Safe?

| Feature | Object Safe? | Fix |
|---------|-------------|-----|
| `fn method(&self)` | Yes | — |
| `fn method(&self) -> Self` | No | Return `Box<dyn Trait>` |
| `fn method<T>(&self, x: T)` | No | Use associated type or concrete type |
| `fn method(&self, other: &Self)` | No | Use `&dyn Trait` |
| `trait Foo: Sized` | No | Remove `Sized` bound |
| `fn method(&self) where Self: Sized` | Yes | Method excluded from vtable |

## Key Takeaways

Object safety determines which traits can be used as `dyn Trait`. The rules exist because vtables need fixed-size entries with known types. Methods returning `Self`, taking `Self` as args, or having generic parameters break this. Use `where Self: Sized` to opt individual methods out of the vtable while keeping the trait object-safe.

When designing traits you expect to use as `dyn`, design for object safety from the start. Retrofitting it later is always painful.

Next — blanket implementations, where you implement a trait for *all* types satisfying a bound.
