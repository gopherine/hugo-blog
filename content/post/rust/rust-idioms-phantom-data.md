---
title: "Lesson 19: PhantomData — Tagging types without runtime cost"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 19
keywords: [Rust, rust, idiomatic, PhantomData, generics, type parameters, zero-sized]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-05-10T09:15:00.000Z'
---

When I first saw `PhantomData` in a codebase, I thought it was some kind of hack. A zero-sized field that exists only to satisfy the compiler? It felt like a workaround for a language limitation. But the more I used it, the more I realized it's actually a precision tool — it lets you encode information in the type system without any runtime cost.

We already used it in the typestate lesson. Now let's understand it properly.

---

## The Problem PhantomData Solves

Imagine you have a generic struct, but the type parameter isn't used in any field:

```rust
// This won't compile:
// struct Wrapper<T> {
//     id: u64,
// }
// ERROR: parameter `T` is never used
```

Rust complains because an unused type parameter creates ambiguity. If `T` isn't used, then `Wrapper<String>` and `Wrapper<i32>` would have identical layouts — so why have the parameter at all?

But sometimes you *want* the parameter for type-level information, even if it doesn't affect the runtime layout. That's what `PhantomData` is for.

```rust
use std::marker::PhantomData;

struct Wrapper<T> {
    id: u64,
    _marker: PhantomData<T>,
}

fn main() {
    let a: Wrapper<String> = Wrapper { id: 1, _marker: PhantomData };
    let b: Wrapper<i32> = Wrapper { id: 2, _marker: PhantomData };

    // a and b are different types — you can't mix them up
    // Even though they have identical runtime layouts
}
```

`PhantomData<T>` is a zero-sized type. It takes zero bytes of memory. It exists purely to tell the compiler "this struct is parameterized by `T`, even though `T` doesn't appear in any real field."

---

## Use Case 1: Type-Safe IDs

The most practical use case I've seen — IDs that are parameterized by what they identify:

```rust
use std::marker::PhantomData;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
struct Id<T> {
    value: u64,
    _entity: PhantomData<T>,
}

impl<T> Id<T> {
    fn new(value: u64) -> Self {
        Id { value, _entity: PhantomData }
    }
}

struct User {
    id: Id<User>,
    name: String,
}

struct Order {
    id: Id<Order>,
    user_id: Id<User>,
    total: f64,
}

fn find_user(id: Id<User>) -> Option<User> {
    println!("Looking up user {}", id.value);
    Some(User { id, name: "Atharva".into() })
}

fn find_order(id: Id<Order>) -> Option<Order> {
    println!("Looking up order {}", id.value);
    None
}

fn main() {
    let user_id = Id::<User>::new(42);
    let order_id = Id::<Order>::new(42);

    find_user(user_id);   // OK
    find_order(order_id); // OK

    // find_user(order_id); // ERROR: expected Id<User>, found Id<Order>
    // Can't mix up user IDs and order IDs, even though they're both u64 internally
}
```

Both `Id<User>` and `Id<Order>` are `u64` at runtime. But at compile time, they're distinct types. You can't pass an order ID where a user ID is expected. Zero cost. Full type safety.

---

## Use Case 2: Unit Markers for Measurements

```rust
use std::marker::PhantomData;
use std::ops::Add;

struct Meters;
struct Feet;

#[derive(Debug, Clone, Copy)]
struct Distance<Unit> {
    value: f64,
    _unit: PhantomData<Unit>,
}

impl<Unit> Distance<Unit> {
    fn new(value: f64) -> Self {
        Distance { value, _unit: PhantomData }
    }
}

impl<Unit> Add for Distance<Unit> {
    type Output = Self;
    fn add(self, rhs: Self) -> Self {
        Distance::new(self.value + rhs.value)
    }
}

// Conversion only between specific unit pairs
impl Distance<Meters> {
    fn to_feet(self) -> Distance<Feet> {
        Distance::new(self.value * 3.28084)
    }
}

impl Distance<Feet> {
    fn to_meters(self) -> Distance<Meters> {
        Distance::new(self.value / 3.28084)
    }
}

fn main() {
    let d1 = Distance::<Meters>::new(100.0);
    let d2 = Distance::<Meters>::new(50.0);
    let total = d1 + d2; // OK: same units
    println!("Total: {:.1} meters", total.value);

    let in_feet = total.to_feet();
    println!("In feet: {:.1}", in_feet.value);

    // let invalid = d1 + in_feet; // ERROR: can't add Meters and Feet
}
```

---

## Use Case 3: Typestate (Revisited)

We covered typestate in Lesson 6. `PhantomData` is the mechanism that makes it work:

```rust
use std::marker::PhantomData;

struct Locked;
struct Unlocked;

struct Door<State> {
    name: String,
    _state: PhantomData<State>,
}

impl Door<Locked> {
    fn new(name: &str) -> Self {
        Door { name: name.to_string(), _state: PhantomData }
    }

    fn unlock(self, _key: &str) -> Door<Unlocked> {
        println!("Unlocking {}", self.name);
        Door { name: self.name, _state: PhantomData }
    }
}

impl Door<Unlocked> {
    fn open(&self) {
        println!("Opening {}", self.name);
    }

    fn lock(self) -> Door<Locked> {
        println!("Locking {}", self.name);
        Door { name: self.name, _state: PhantomData }
    }
}

fn main() {
    let door = Door::<Locked>::new("Front door");
    // door.open(); // ERROR: no method `open` on Door<Locked>

    let door = door.unlock("secret");
    door.open(); // OK

    let door = door.lock();
    // door.open(); // ERROR again: Door<Locked>
}
```

---

## Use Case 4: Lifetime Markers

`PhantomData` can also carry lifetime information, telling the compiler that your struct borrows data even if it doesn't hold a direct reference:

```rust
use std::marker::PhantomData;

struct Iter<'a, T> {
    ptr: *const T,
    end: *const T,
    _lifetime: PhantomData<&'a T>,
}

impl<'a, T> Iter<'a, T> {
    fn from_slice(slice: &'a [T]) -> Self {
        let ptr = slice.as_ptr();
        let end = unsafe { ptr.add(slice.len()) };
        Iter { ptr, end, _lifetime: PhantomData }
    }
}

impl<'a, T> Iterator for Iter<'a, T> {
    type Item = &'a T;

    fn next(&mut self) -> Option<Self::Item> {
        if self.ptr == self.end {
            None
        } else {
            let current = self.ptr;
            self.ptr = unsafe { self.ptr.add(1) };
            Some(unsafe { &*current })
        }
    }
}

fn main() {
    let data = vec![10, 20, 30, 40];
    let iter = Iter::from_slice(&data);
    for item in iter {
        println!("{}", item);
    }
}
```

Without `PhantomData<&'a T>`, the compiler wouldn't know that `Iter` borrows from the slice. The raw pointers don't carry lifetime information. `PhantomData` tells the compiler: "treat this struct as if it holds a `&'a T`, even though the actual data is behind raw pointers."

---

## PhantomData Conventions

The naming convention is to prefix with `_`:

```rust
use std::marker::PhantomData;

struct MyType<T> {
    data: u64,
    _marker: PhantomData<T>,      // common
    _phantom: PhantomData<T>,     // also common
    _type: PhantomData<T>,        // less common but fine
}
```

The underscore prefix tells readers (and the compiler) that the field is intentionally unused at runtime.

---

## What PhantomData Variants Mean

The type inside `PhantomData` matters for variance and auto-traits:

| PhantomData variant | Meaning |
|---|---|
| `PhantomData<T>` | "I own a T" — affects drop check |
| `PhantomData<&'a T>` | "I borrow a T for lifetime 'a" |
| `PhantomData<*const T>` | "I have a raw pointer to T" (covariant, no ownership) |
| `PhantomData<fn(T)>` | "I consume T" (contravariant) |
| `PhantomData<fn() -> T>` | "I produce T" (covariant) |

For most use cases, `PhantomData<T>` is what you want. The variance stuff matters for advanced generic library design — you probably won't need to worry about it until you're writing a custom container or smart pointer.

---

## When You Don't Need PhantomData

If the type parameter appears in an actual field, you don't need `PhantomData`:

```rust
// No PhantomData needed — T is used in the field
struct Container<T> {
    items: Vec<T>,
}

// No PhantomData needed — T is used in the field
struct Pair<A, B> {
    first: A,
    second: B,
}
```

`PhantomData` is only needed when a type parameter exists for compile-time information but doesn't appear in any actual field.

---

## Key Takeaways

- `PhantomData<T>` is a zero-sized type that tells the compiler your struct is parameterized by `T`.
- Use it for type-safe IDs, unit markers, typestate patterns, and lifetime tracking with raw pointers.
- Zero runtime cost — `PhantomData` occupies no memory and generates no code.
- Convention: name the field `_marker` or `_phantom` with an underscore prefix.
- You only need `PhantomData` when the type parameter isn't used in any other field.
- For most use cases, `PhantomData<T>` is the right choice. Variance details matter for advanced library design.
