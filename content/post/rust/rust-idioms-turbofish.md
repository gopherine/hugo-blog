---
title: "Lesson 21: The Turbofish ::<> — Explicit type parameters"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 21
keywords: [Rust, rust, idiomatic, turbofish, type parameters, generics, type inference]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-05-14T08:22:00.000Z'
---

The first time I saw `::<>` in Rust code, I thought someone was having a stroke at the keyboard. `collect::<Vec<_>>()`? What is that colon-colon-angle-bracket monstrosity?

It's called the turbofish. Named by the community because `::<>` looks like a fish (`::<>` — see the eyes and the mouth?). It's goofy. It's lovable. And once you understand it, you'll use it constantly.

---

## What the Turbofish Does

The turbofish provides explicit type parameters to generic functions or methods when the compiler can't infer them.

```rust
fn main() {
    // The compiler can't figure out what type to parse into
    // let x = "42".parse(); // ERROR: type annotations needed

    // Solution 1: annotate the variable
    let x: i32 = "42".parse().unwrap();

    // Solution 2: turbofish
    let x = "42".parse::<i32>().unwrap();

    println!("{}", x);
}
```

Both are equivalent. The turbofish puts the type *at the point of use* rather than on the variable. This is often more readable, especially in chains.

---

## Where You'll Use It Most

### `collect()` — The Classic

`collect()` is generic over its return type. It can produce a `Vec`, a `HashSet`, a `BTreeMap`, a `String`, and more. The compiler needs to know which one.

```rust
fn main() {
    let numbers = vec![1, 2, 3, 4, 5];

    // Turbofish on collect — tells it to produce a Vec
    let doubled = numbers.iter().map(|n| n * 2).collect::<Vec<_>>();

    // The _ lets the compiler infer the element type
    // Vec<_> means "a Vec of whatever the iterator yields"

    println!("{:?}", doubled);
}
```

The `_` placeholder is your friend. You don't always need to specify the full type — just enough for the compiler to figure out the rest.

```rust
use std::collections::HashMap;

fn main() {
    let pairs = vec![("a", 1), ("b", 2), ("c", 3)];

    // Turbofish with partial inference
    let map = pairs.into_iter().collect::<HashMap<_, _>>();
    // HashMap<_, _> means "a HashMap, you figure out the key and value types"

    println!("{:?}", map);
}
```

### `parse()` — String to Anything

```rust
fn main() {
    let port = "8080".parse::<u16>().unwrap();
    let pi = "3.14159".parse::<f64>().unwrap();
    let flag = "true".parse::<bool>().unwrap();

    println!("port={}, pi={}, flag={}", port, pi, flag);
}
```

### Channel creation

```rust
use std::sync::mpsc;

fn main() {
    // Without turbofish:
    let (tx, rx): (mpsc::Sender<String>, mpsc::Receiver<String>) = mpsc::channel();

    // With turbofish — much cleaner:
    let (tx, rx) = mpsc::channel::<String>();

    tx.send("hello".into()).unwrap();
    println!("{}", rx.recv().unwrap());
}
```

### Default values

```rust
fn main() {
    let empty_vec = Vec::<i32>::new();
    let empty_string = String::new(); // no turbofish needed — String isn't generic

    println!("{:?}, {:?}", empty_vec, empty_string);
}
```

---

## Turbofish vs Type Annotation: When to Use Which

My rule: use whichever makes the code read more naturally.

```rust
fn main() {
    // Type annotation reads better when the variable is used later
    let config: HashMap<String, String> = load_config();

    // Turbofish reads better in chains
    let ports = config.values()
        .filter_map(|v| v.parse::<u16>().ok())
        .collect::<Vec<_>>();

    println!("{:?}", ports);
}

fn load_config() -> std::collections::HashMap<String, String> {
    let mut m = std::collections::HashMap::new();
    m.insert("port".into(), "8080".into());
    m.insert("name".into(), "app".into());
    m
}

use std::collections::HashMap;
```

In a chain, turbofish keeps the type close to the operation that needs it. A type annotation on a let binding would be far away from the `collect()` call.

---

## Multiple Type Parameters

Some functions have multiple type parameters. The turbofish handles them in order:

```rust
fn convert<From, To>(value: From) -> To
where
    From: std::fmt::Display,
    To: std::str::FromStr,
    To::Err: std::fmt::Debug,
{
    let s = value.to_string();
    s.parse::<To>().expect("conversion failed")
}

fn main() {
    // Explicit both parameters
    let result = convert::<i32, f64>(42);
    println!("{}", result); // 42.0

    // Often the compiler can infer From, so you only need To
    // But you can't skip parameters in turbofish — it's all or nothing
}
```

---

## When the Turbofish Isn't Needed

If the compiler can infer the type from context, skip the turbofish:

```rust
fn add_to_vec(v: &mut Vec<i32>, s: &str) {
    // No turbofish needed — the compiler knows we want i32 from the Vec type
    if let Ok(n) = s.parse() {
        v.push(n);
    }
}

fn main() {
    let mut v = Vec::new(); // type inferred from first push
    v.push(42i32);          // now the compiler knows it's Vec<i32>

    add_to_vec(&mut v, "100");
    println!("{:?}", v);
}
```

The compiler is smart about inference. Let it work. Add turbofish only when it asks for help (with a "type annotations needed" error).

---

## Turbofish on Struct Methods

You can turbofish on associated functions and methods too:

```rust
#[derive(Debug)]
struct Container<T> {
    items: Vec<T>,
}

impl<T> Container<T> {
    fn new() -> Self {
        Container { items: Vec::new() }
    }

    fn with_capacity(cap: usize) -> Self {
        Container { items: Vec::with_capacity(cap) }
    }
}

fn main() {
    // Turbofish on the struct's associated function
    let c = Container::<String>::new();
    let c2 = Container::<i32>::with_capacity(100);

    println!("{:?}, {:?}", c, c2);
}
```

---

## The Turbofish and Closures

A common pattern: turbofish on the method that consumes the closure's return value:

```rust
fn main() {
    let input = "1,2,three,4,five,6";

    let numbers: Vec<i32> = input.split(',')
        .filter_map(|s| s.parse::<i32>().ok())
        .collect();
    // Turbofish on parse, type annotation on the variable for collect

    // Or turbofish on both:
    let numbers = input.split(',')
        .filter_map(|s| s.parse::<i32>().ok())
        .collect::<Vec<_>>();

    println!("{:?}", numbers); // [1, 2, 4, 6]
}
```

---

## A Fun Edge Case: Turbofish in Comparisons

There's a parsing ambiguity in Rust that the turbofish resolves. Without it, `<` could be mistaken for a less-than operator:

```rust
fn main() {
    // This is unambiguous — turbofish clearly marks type parameters
    let x = 42.to_string();

    // But in some expressions, the parser needs turbofish to distinguish
    // type parameters from comparison operators
    let items = vec![1, 2, 3];
    let result = items.iter().collect::<Vec<_>>();

    println!("{:?}", result);
}
```

This is actually why the turbofish exists — it's not just a style choice. The `::` disambiguates `<` as "start of type parameters" versus "less-than operator." Without those colons, the parser would be confused.

---

## Turbofish Style Guide

My personal conventions:

1. **Use turbofish on `collect()`** — almost always clearer than a type annotation
2. **Use turbofish on `parse()`** — puts the target type right at the conversion site
3. **Use type annotation for `let` bindings** — when the variable name is more prominent than the method
4. **Use `_` liberally** — let the compiler fill in what it can
5. **Don't turbofish when inference works** — unnecessary turbofish is noise

```rust
use std::collections::HashSet;

fn main() {
    // Good: turbofish on collect
    let unique = vec![1, 2, 2, 3, 3, 3].into_iter().collect::<HashSet<_>>();

    // Good: type annotation when it reads better
    let count: usize = "42".parse().unwrap();

    // Bad: unnecessary turbofish — compiler can infer from push
    let mut v = Vec::<i32>::new(); // just use Vec::new() and let push infer
    v.push(1);

    println!("{:?}, {}", unique, count);
}
```

---

## Key Takeaways

- The turbofish `::<>` provides explicit type parameters to generic functions and methods.
- Most commonly used with `collect()`, `parse()`, `channel()`, and `Vec::new()`.
- Use `_` as a placeholder to let the compiler infer part of the type: `collect::<Vec<_>>()`.
- The `::` in turbofish disambiguates `<` from the less-than operator — it's a syntactic necessity, not just style.
- Use turbofish when it puts the type closer to where it matters. Use type annotations when the variable name is more important.
- Don't add turbofish when the compiler can infer — less noise is better.
