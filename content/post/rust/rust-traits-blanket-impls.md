---
title: "Lesson 9: Blanket Implementations — Implementing for all T"
author: Atharva Pandey
series: "Rust Traits & Generics Masterclass"
lesson: 9
keywords: [Rust, rust, traits, generics, blanket implementation, impl for T, generic impl]
tags: [Rust tutorial, rust, traits, generics]
date: '2024-06-28T10:20:00.000Z'
---

The moment blanket implementations clicked for me, I felt like I'd been handed a cheat code. You write `impl<T: Display> ToString for T` and suddenly *every single type* that implements `Display` automatically gets `ToString`. One line of implementation logic, infinite types covered. This is how the standard library builds massive capability trees from small pieces.

## What's a Blanket Implementation?

A blanket implementation implements a trait for all types matching a bound, rather than for a specific type:

```rust
use std::fmt::Display;

trait Shout {
    fn shout(&self) -> String;
}

// Blanket impl: anything that is Display can Shout
impl<T: Display> Shout for T {
    fn shout(&self) -> String {
        format!("{}!!!", self).to_uppercase()
    }
}

fn main() {
    println!("{}", 42.shout());           // "42!!!"
    println!("{}", "hello".shout());       // "HELLO!!!"
    println!("{}", 3.14f64.shout());       // "3.14!!!"
    println!("{}", true.shout());          // "TRUE!!!"
}
```

I didn't implement `Shout` for `i32`, `&str`, `f64`, or `bool`. The blanket impl covers all of them — and every future type that implements `Display`. That's the power.

## How the Standard Library Uses This

The most famous blanket impl in Rust's standard library:

```rust
// In std (simplified):
// impl<T: Display> ToString for T {
//     fn to_string(&self) -> String {
//         format!("{}", self)
//     }
// }
```

This is why you can call `.to_string()` on anything that implements `Display`. You've been using blanket implementations every time you write `42.to_string()`.

Another critical one:

```rust
// In std (simplified):
// impl<T> From<T> for T {
//     fn from(t: T) -> T { t }
//     // Every type can be converted from itself
// }
```

And the `Into` blanket:

```rust
// impl<T, U> Into<U> for T where U: From<T> {
//     fn into(self) -> U { U::from(self) }
// }
```

This is why you only need to implement `From` and get `Into` for free. One trait implementation, two conversion directions.

## Writing Your Own Blanket Implementations

Here's a practical example — a logging wrapper:

```rust
use std::fmt::{Display, Debug};

trait Loggable {
    fn log_info(&self);
    fn log_debug(&self);
}

impl<T> Loggable for T
where
    T: Display + Debug,
{
    fn log_info(&self) {
        println!("[INFO] {}", self);
    }

    fn log_debug(&self) {
        println!("[DEBUG] {:?}", self);
    }
}

fn main() {
    // All these types get Loggable for free
    42.log_info();
    "server started".log_info();
    3.14.log_debug();

    // Custom types too, if they have Display + Debug
    #[derive(Debug)]
    struct Request {
        path: String,
        method: String,
    }

    impl Display for Request {
        fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            write!(f, "{} {}", self.method, self.path)
        }
    }

    let req = Request {
        path: String::from("/api/users"),
        method: String::from("GET"),
    };
    req.log_info();   // "[INFO] GET /api/users"
    req.log_debug();  // "[DEBUG] Request { path: "/api/users", method: "GET" }"
}
```

Every type with `Display + Debug` — past, present, and future — gets `log_info` and `log_debug`. You don't touch the original types.

## Blanket Impls with Associated Types

You can write blankets that bridge traits with different associated types:

```rust
trait Measurable {
    type Unit;
    fn measure(&self) -> f64;
    fn unit(&self) -> Self::Unit;
}

trait Reportable {
    fn report(&self) -> String;
}

// Blanket: anything Measurable with a Display unit is Reportable
impl<T> Reportable for T
where
    T: Measurable,
    T::Unit: std::fmt::Display,
{
    fn report(&self) -> String {
        format!("{:.2} {}", self.measure(), self.unit())
    }
}

struct Distance {
    meters: f64,
}

impl Measurable for Distance {
    type Unit = &'static str;

    fn measure(&self) -> f64 {
        self.meters
    }

    fn unit(&self) -> &'static str {
        "m"
    }
}

struct Temperature {
    celsius: f64,
}

impl Measurable for Temperature {
    type Unit = &'static str;

    fn measure(&self) -> f64 {
        self.celsius
    }

    fn unit(&self) -> &'static str {
        "°C"
    }
}

fn main() {
    let d = Distance { meters: 42.195 };
    let t = Temperature { celsius: 36.6 };

    // Both get Reportable for free via the blanket
    println!("{}", d.report()); // "42.20 m"
    println!("{}", t.report()); // "36.60 °C"
}
```

## The Restriction: Orphan Rules Apply

You can't write a blanket impl if it could conflict with existing implementations. This is tied to the orphan rules (next lesson), but the quick version:

```rust
// This will NOT compile if ToString already has a blanket impl
// (which it does — from Display)
//
// impl<T: Debug> ToString for T { ... }
// ERROR: conflicting implementation
```

If the standard library already has `impl<T: Display> ToString for T`, you can't add `impl<T: Debug> ToString for T` because some types implement *both* `Display` and `Debug`, creating ambiguity.

Your blanket impls must not overlap with existing ones. The compiler enforces this strictly.

## Pattern: Extension Traits with Blankets

This is my favorite pattern for adding methods to existing types without modifying them:

```rust
trait IteratorExt: Iterator {
    fn take_every(self, n: usize) -> TakeEvery<Self>
    where
        Self: Sized,
    {
        TakeEvery {
            iter: self,
            step: n,
            count: 0,
        }
    }
}

// Blanket impl: every Iterator gets our extension
impl<T: Iterator> IteratorExt for T {}

struct TakeEvery<I> {
    iter: I,
    step: usize,
    count: usize,
}

impl<I: Iterator> Iterator for TakeEvery<I> {
    type Item = I::Item;

    fn next(&mut self) -> Option<Self::Item> {
        loop {
            let item = self.iter.next()?;
            self.count += 1;
            if (self.count - 1) % self.step == 0 {
                return Some(item);
            }
        }
    }
}

fn main() {
    let nums: Vec<i32> = (0..20).take_every(3).collect();
    println!("{:?}", nums); // [0, 3, 6, 9, 12, 15, 18]

    let chars: Vec<char> = "abcdefghij".chars().take_every(2).collect();
    println!("{:?}", chars); // ['a', 'c', 'e', 'g', 'i']
}
```

This is how crates like `itertools` work. Define a trait with your extra methods, blanket-implement it for all `Iterator`s, and now every iterator in the ecosystem has your methods available (once imported).

## Pattern: Automatic Trait Derivation via Blanket

Sometimes you want a trait to be automatically available for references, boxes, or other wrappers:

```rust
trait Processor {
    fn process(&self, input: &str) -> String;
}

// If T is a Processor, &T is also a Processor
impl<T: Processor> Processor for &T {
    fn process(&self, input: &str) -> String {
        (**self).process(input)
    }
}

// If T is a Processor, Box<T> is also a Processor
impl<T: Processor> Processor for Box<T> {
    fn process(&self, input: &str) -> String {
        (**self).process(input)
    }
}

struct Uppercase;

impl Processor for Uppercase {
    fn process(&self, input: &str) -> String {
        input.to_uppercase()
    }
}

fn run(p: &impl Processor, input: &str) {
    println!("{}", p.process(input));
}

fn main() {
    let p = Uppercase;
    run(&p, "hello");              // &Uppercase — works via blanket for &T

    let boxed: Box<Uppercase> = Box::new(Uppercase);
    run(&*boxed, "world");          // works

    let p_ref = &&p;
    run(p_ref, "nested ref");      // &&Uppercase — also works
}
```

The standard library does this heavily. `Read` and `Write` are implemented for `&mut T where T: Read/Write`, for instance.

## When Not to Use Blanket Impls

**Don't blanket when:**
- The implementation isn't meaningful for all types matching the bound
- You'd need to special-case some types (can't — blankets cover everything)
- The blanket would conflict with specific impls users might want to write

```rust
// BAD: Not every Display type should be serializable as JSON
// impl<T: Display> Serialize for T { ... }

// The Display output isn't necessarily valid JSON!
// A specific impl per type is correct here.
```

**Do blanket when:**
- The implementation is genuinely derivable from the bounds (like ToString from Display)
- You're building an extension trait (IteratorExt pattern)
- You're implementing forwarding through wrappers (&T, Box<T>, Arc<T>)

## Key Takeaways

Blanket implementations let you implement a trait for *all* types matching a bound, covering infinite types with one `impl` block. They're the engine behind `ToString`, `Into`, and extension trait patterns. They're subject to orphan rules — no overlapping impls allowed. Use them when the implementation is genuinely derivable from the bounds, and avoid them when the logic should be type-specific.

Next — orphan rules and the newtype pattern, the coherence system that makes all of this possible without chaos.
