---
title: "Lesson 10: Deref Coercion — Why &String works as &str"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 10
keywords: [Rust, rust, idiomatic, Deref, coercion, smart pointers, str, String]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-04-25T21:10:00.000Z'
---

Here's something that confused me for weeks when I started Rust: I'd write a function that takes `&str`, and I could pass it a `&String`. I'd write a function that takes `&[i32]`, and I could pass it a `&Vec<i32>`. I'd use `*` to dereference a `Box<T>` and get a `T`.

How? Why? The answer is `Deref` coercion — one of Rust's most elegant features, and one of the most poorly explained.

---

## What Deref Coercion Actually Does

When you have a reference to type `A`, and the compiler expects a reference to type `B`, it checks: "Does `A` implement `Deref<Target = B>`?" If yes, the compiler automatically converts `&A` to `&B`.

That's it. The compiler inserts the conversion for you.

```rust
fn print_length(s: &str) {
    println!("Length: {}", s.len());
}

fn main() {
    let owned = String::from("hello");
    print_length(&owned); // &String → &str via Deref coercion

    let boxed = Box::new(String::from("world"));
    print_length(&boxed); // &Box<String> → &String → &str (two coercions!)
}
```

The compiler chains `Deref` implementations automatically. `Box<String>` derefs to `String`, which derefs to `str`. So `&Box<String>` becomes `&str` through two hops — and it all happens at zero runtime cost.

---

## The Deref Trait

```rust
use std::ops::Deref;

// This is what String implements (simplified)
// impl Deref for String {
//     type Target = str;
//     fn deref(&self) -> &str {
//         // returns a reference to the underlying str
//     }
// }
```

The standard library has these key `Deref` implementations:

| Type | Deref Target |
|------|-------------|
| `String` | `str` |
| `Vec<T>` | `[T]` |
| `Box<T>` | `T` |
| `Arc<T>` | `T` |
| `Rc<T>` | `T` |
| `Cow<'_, B>` | `B` |
| `PathBuf` | `Path` |
| `OsString` | `OsStr` |

This is why the Rust standard library feels cohesive — all the "owned" types seamlessly coerce to their "borrowed" counterparts.

---

## Why This Matters for API Design

When you write a function, take the *borrowed* type, not the *owned* type:

```rust
// BAD: requires &String specifically
fn bad_greet(name: &String) {
    println!("Hello, {}", name);
}

// GOOD: accepts &str — works with String, &str, Box<String>, etc.
fn good_greet(name: &str) {
    println!("Hello, {}", name);
}

// BAD: requires &Vec<i32> specifically
fn bad_sum(numbers: &Vec<i32>) -> i32 {
    numbers.iter().sum()
}

// GOOD: accepts &[i32] — works with Vec, arrays, slices
fn good_sum(numbers: &[i32]) -> i32 {
    numbers.iter().sum()
}

fn main() {
    let s = String::from("Atharva");
    good_greet(&s);        // &String → &str ✓
    good_greet("literal"); // &str → &str ✓

    let v = vec![1, 2, 3];
    good_sum(&v);           // &Vec<i32> → &[i32] ✓
    good_sum(&[4, 5, 6]);   // &[i32] → &[i32] ✓
}
```

Clippy will actually warn you about taking `&String` or `&Vec<T>` — it's a code smell. The slice/str version is strictly more general.

---

## Implementing Deref for Your Own Types

You can implement `Deref` for your types, but be careful — it's meant for smart pointer-like types, not for general-purpose conversion.

```rust
use std::ops::Deref;

struct SortedVec<T> {
    inner: Vec<T>,
}

impl<T: Ord> SortedVec<T> {
    fn new() -> Self {
        SortedVec { inner: Vec::new() }
    }

    fn insert(&mut self, value: T) {
        let pos = self.inner.binary_search(&value).unwrap_or_else(|e| e);
        self.inner.insert(pos, value);
    }
}

// Deref to &[T] — allows all slice methods (searching, iterating, etc.)
impl<T> Deref for SortedVec<T> {
    type Target = [T];

    fn deref(&self) -> &[T] {
        &self.inner
    }
}

fn print_items(items: &[i32]) {
    for item in items {
        print!("{} ", item);
    }
    println!();
}

fn main() {
    let mut sv = SortedVec::new();
    sv.insert(3);
    sv.insert(1);
    sv.insert(4);
    sv.insert(1);
    sv.insert(5);

    // Deref coercion: &SortedVec<i32> → &[i32]
    print_items(&sv);         // 1 1 3 4 5

    // All slice methods are available
    println!("Contains 3: {}", sv.contains(&3));
    println!("Length: {}", sv.len());
    println!("First: {:?}", sv.first());
}
```

Notice: we did *not* implement `DerefMut`. That's intentional — we don't want users to get a `&mut [T]` and mess up the sorting invariant. `Deref` gives read access; `DerefMut` gives write access. Only implement `DerefMut` when mutation through the deref target is safe.

---

## DerefMut — The Mutable Version

```rust
use std::ops::{Deref, DerefMut};

struct Wrapper<T> {
    value: T,
    access_count: usize,
}

impl<T> Wrapper<T> {
    fn new(value: T) -> Self {
        Wrapper { value, access_count: 0 }
    }

    fn access_count(&self) -> usize {
        self.access_count
    }
}

impl<T> Deref for Wrapper<T> {
    type Target = T;
    fn deref(&self) -> &T {
        &self.value
    }
}

impl<T> DerefMut for Wrapper<T> {
    fn deref_mut(&mut self) -> &mut T {
        self.access_count += 1;
        &mut self.value
    }
}

fn main() {
    let mut w = Wrapper::new(String::from("hello"));

    // Deref: &Wrapper<String> → &String → &str
    println!("Length: {}", w.len()); // calls str::len() via deref chain

    // DerefMut: &mut Wrapper<String> → &mut String
    w.push_str(" world"); // calls String::push_str() via deref mut
    println!("Value: {}", *w);
    println!("Mutations: {}", w.access_count());
}
```

---

## The Deref Anti-Pattern: Don't Use It for Inheritance

This is my biggest warning. Newcomers from OOP backgrounds see `Deref` and think: "I can use this to simulate inheritance! Struct A wraps Struct B, implement `Deref<Target=B>`, and A gets all of B's methods!"

**Don't do this.** It causes confusion, breaks expectations, and Clippy will flag it.

```rust
// DON'T DO THIS
struct Animal {
    name: String,
}

impl Animal {
    fn speak(&self) -> &str {
        "..."
    }
}

struct Dog {
    animal: Animal,
    breed: String,
}

// This is tempting but WRONG
// impl Deref for Dog {
//     type Target = Animal;
//     fn deref(&self) -> &Animal { &self.animal }
// }

// Instead, use composition with explicit delegation
impl Dog {
    fn new(name: &str, breed: &str) -> Self {
        Dog {
            animal: Animal { name: name.to_string() },
            breed: breed.to_string(),
        }
    }

    fn name(&self) -> &str {
        &self.animal.name
    }

    fn breed(&self) -> &str {
        &self.breed
    }

    fn speak(&self) -> &str {
        "Woof!"
    }
}

fn main() {
    let dog = Dog::new("Rex", "Labrador");
    println!("{} ({}): {}", dog.name(), dog.breed(), dog.speak());
}
```

`Deref` is for smart pointers and transparent wrappers — types where "dereferencing" makes semantic sense. A `Dog` is not a smart pointer to an `Animal`.

---

## How the Compiler Resolves Method Calls

Understanding deref coercion helps you understand Rust's method resolution. When you call `x.method()`, the compiler tries:

1. `T::method(&x)` — direct method on the type
2. `<T as Deref>::Target::method(&*x)` — method on the deref target
3. `<<T as Deref>::Target as Deref>::Target::method(&**x)` — another level of deref
4. And so on...

It also tries auto-referencing: `&x`, `&mut x`, and `x` (by value).

This is why `"hello".len()` works even though `len()` is defined on `str` and `"hello"` is `&str` — the compiler finds it through auto-referencing.

```rust
fn main() {
    let boxed_string: Box<String> = Box::new(String::from("hello"));

    // All of these work through deref coercion:
    println!("{}", boxed_string.len());        // str::len()
    println!("{}", boxed_string.is_empty());   // str::is_empty()
    println!("{}", boxed_string.contains("ell")); // str::contains()

    // The compiler resolves:
    // Box<String> → deref → String → deref → str → found len()!
}
```

---

## Key Takeaways

- Deref coercion automatically converts `&A` to `&B` when `A: Deref<Target = B>`.
- The compiler chains coercions: `&Box<String>` → `&String` → `&str`.
- Take `&str` instead of `&String`, and `&[T]` instead of `&Vec<T>` in function parameters.
- Implement `Deref` for smart pointer-like types and transparent wrappers — not for OOP inheritance.
- Only implement `DerefMut` when mutable access through the deref target is safe.
- Zero runtime cost — deref coercions are resolved at compile time.
