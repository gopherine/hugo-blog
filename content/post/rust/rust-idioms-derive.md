---
title: "Lesson 14: derive Is Your Best Friend — The macros you should always use"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 14
keywords: [Rust, rust, idiomatic, derive, macros, traits, Clone, Debug, PartialEq]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-05-02T10:30:00.000Z'
---

I review a lot of Rust code, and one of my biggest pet peeves is types without `#[derive(Debug)]`. You hit an error, you try to print the value, and you get that lovely message: "MyStruct doesn't implement Debug." Then you have to go add it, recompile, and try again.

Just derive it from the start. Derive liberally. Your future self will thank you.

---

## The Derives You Should Almost Always Use

Here's my standard starting point for any struct or enum:

```rust
#[derive(Debug, Clone, PartialEq)]
struct MyType {
    // ...
}
```

Those three cover 80% of cases. Let me break down when to use each one, and when to add more.

### Debug — Always

I covered this in the previous lesson. Derive `Debug` on everything. It costs nothing at runtime, and it makes error messages, test assertions, and debugging actually possible.

```rust
#[derive(Debug)]
struct Config {
    host: String,
    port: u16,
}

fn main() {
    let c = Config { host: "localhost".into(), port: 8080 };
    println!("{:?}", c); // Config { host: "localhost", port: 8080 }
}
```

### Clone — Almost Always

`Clone` lets you explicitly duplicate a value. If your type holds only clonable data, derive it.

```rust
#[derive(Debug, Clone)]
struct Point {
    x: f64,
    y: f64,
}

fn main() {
    let p1 = Point { x: 1.0, y: 2.0 };
    let p2 = p1.clone();
    println!("{:?} {:?}", p1, p2);
}
```

**When NOT to derive Clone:**
- Types that represent unique resources (file handles, database connections, mutex guards)
- Types where cloning would be semantically wrong (a unique ID generator shouldn't be cloned)
- Types with interior mutability where cloning could cause confusion

### PartialEq — Usually

`PartialEq` enables `==` and `!=` comparisons. You need this for assertions, collection lookups, and general equality checks.

```rust
#[derive(Debug, Clone, PartialEq)]
struct User {
    name: String,
    email: String,
}

fn main() {
    let u1 = User { name: "Atharva".into(), email: "a@b.com".into() };
    let u2 = User { name: "Atharva".into(), email: "a@b.com".into() };
    assert_eq!(u1, u2); // Works because PartialEq is derived
}
```

---

## The Extended Set

### Eq — When PartialEq Is Total

`Eq` is a marker trait that says "my PartialEq implementation is a true equivalence relation" — reflexive, symmetric, and transitive. Most types satisfy this. The notable exception: floating-point numbers (`NaN != NaN`).

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
struct UserId(u64);
```

**Rule:** If your type has no `f32`/`f64` fields, derive `Eq` alongside `PartialEq`. You need `Eq` to use your type as a key in `HashMap`/`HashSet`.

### Hash — For Collection Keys

```rust
use std::collections::HashSet;

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct Tag(String);

fn main() {
    let mut tags: HashSet<Tag> = HashSet::new();
    tags.insert(Tag("rust".into()));
    tags.insert(Tag("idiomatic".into()));
    tags.insert(Tag("rust".into())); // duplicate — not inserted
    println!("{:?}", tags); // Two elements
}
```

You need `Hash` (plus `Eq`) for `HashMap` keys and `HashSet` elements. If you derive `PartialEq` and `Eq`, you should probably derive `Hash` too.

### Copy — For Small, Stack-Only Types

`Copy` means the type can be implicitly duplicated by simple memory copy. This changes the default behavior from "move" to "copy."

```rust
#[derive(Debug, Clone, Copy, PartialEq)]
struct Point {
    x: f64,
    y: f64,
}

fn main() {
    let p1 = Point { x: 1.0, y: 2.0 };
    let p2 = p1; // COPY, not move — p1 is still valid
    println!("{:?} {:?}", p1, p2);
}
```

**Derive Copy when:**
- All fields are `Copy` (no `String`, no `Vec`, no heap allocations)
- The type is small (a few words or less)
- Implicit copying won't surprise users

**Don't derive Copy when:**
- The type has heap data (`String`, `Vec`, `Box`)
- The type represents a resource with identity
- Implicit copying might hide performance issues (large arrays)

Note: `Copy` requires `Clone` — you always derive both.

### PartialOrd and Ord — For Ordering

```rust
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
struct Priority(u8);

fn main() {
    let mut priorities = vec![Priority(3), Priority(1), Priority(2)];
    priorities.sort(); // Works because Ord is derived
    println!("{:?}", priorities); // [Priority(1), Priority(2), Priority(3)]
}
```

`PartialOrd` gives you `<`, `>`, `<=`, `>=`. `Ord` gives you total ordering (required for `sort()` and `BTreeMap` keys). Like `Eq`, don't derive `Ord` if you have floating-point fields.

### Default — For Sensible Defaults

```rust
#[derive(Debug, Default)]
struct Config {
    host: String,      // defaults to ""
    port: u16,         // defaults to 0
    verbose: bool,     // defaults to false
    retries: usize,    // defaults to 0
}

fn main() {
    let c = Config::default();
    println!("{:?}", c);
    // Config { host: "", port: 0, verbose: false, retries: 0 }
}
```

Derived `Default` uses the `Default` value for each field. This is great when the zero/empty value is sensible. If you need custom defaults (like port 8080), implement `Default` manually instead.

---

## The Derive Cheat Sheet

| Derive | What It Does | When to Use |
|--------|-------------|-------------|
| `Debug` | `{:?}` formatting | Always |
| `Clone` | Explicit `.clone()` | Almost always |
| `PartialEq` | `==` and `!=` | Usually |
| `Eq` | Marker for total equality | When no floats |
| `Hash` | Hashing for collections | With Eq, for map/set keys |
| `Copy` | Implicit duplication | Small, stack-only types |
| `PartialOrd` | `<`, `>` comparisons | When ordering makes sense |
| `Ord` | Total ordering, `sort()` | With Eq, for BTreeMap keys |
| `Default` | `Type::default()` | When zero values are sensible |

---

## Common Derive Combinations

Here are the combinations I use most:

```rust
// Data transfer object / value type
#[derive(Debug, Clone, PartialEq)]
struct Response {
    status: u16,
    body: String,
}

// ID type / newtype
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
struct UserId(u64);

// Enum with variants
#[derive(Debug, Clone, PartialEq, Eq)]
enum Status {
    Active,
    Inactive,
    Suspended,
}

// Config with defaults
#[derive(Debug, Clone, Default)]
struct AppConfig {
    debug: bool,
    workers: usize,
    log_path: String,
}

// Small numeric type
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
struct Score(u32);

// Error type
#[derive(Debug, Clone, PartialEq)]
enum AppError {
    NotFound(String),
    Unauthorized,
    Internal(String),
}
```

---

## When Derive Doesn't Work

Sometimes a field doesn't implement the trait you want to derive:

```rust
// This won't compile — closures don't implement Debug
// #[derive(Debug)]
// struct Handler {
//     callback: Box<dyn Fn(i32) -> i32>,
// }

// Solution: implement Debug manually
struct Handler {
    callback: Box<dyn Fn(i32) -> i32>,
}

impl std::fmt::Debug for Handler {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Handler")
            .field("callback", &"<closure>")
            .finish()
    }
}

fn main() {
    let h = Handler { callback: Box::new(|x| x * 2) };
    println!("{:?}", h); // Handler { callback: "<closure>" }
}
```

Another common case: generic types where the inner type might not implement the trait:

```rust
// This works only if T: Debug
#[derive(Debug)]
struct Wrapper<T: std::fmt::Debug> {
    value: T,
}

// Or, more flexibly, add a bound only on the Debug impl:
struct FlexWrapper<T> {
    value: T,
}

impl<T: std::fmt::Debug> std::fmt::Debug for FlexWrapper<T> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("FlexWrapper")
            .field("value", &self.value)
            .finish()
    }
}
```

---

## Serde Derives

While not in the standard library, `serde`'s `Serialize` and `Deserialize` are probably the most-used derives in the Rust ecosystem:

```rust
// In Cargo.toml: serde = { version = "1", features = ["derive"] }

use serde::{Serialize, Deserialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ApiResponse {
    status: String,
    data: Vec<Item>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Item {
    id: u64,
    name: String,
}
```

If your type crosses a serialization boundary (JSON, TOML, databases), you'll want these. They're the `Debug` of the data interchange world — just derive them and move on.

---

## My Approach

When I create a new type, I follow this flow:

1. Start with `#[derive(Debug, Clone, PartialEq)]` — covers most needs.
2. No heap data? Add `Copy`.
3. Need it as a collection key? Add `Eq, Hash`.
4. Need sorting? Add `PartialOrd, Ord`.
5. Sensible zero-values? Add `Default`.
6. Crosses a serialization boundary? Add `Serialize, Deserialize`.

And whenever the compiler says "trait X is not implemented," check if you can just add it to the derive list. Nine times out of ten, you can.

---

## Key Takeaways

- Derive `Debug` on everything. No exceptions.
- `#[derive(Debug, Clone, PartialEq)]` is a sensible starting point for most types.
- `Copy` is for small, stack-only types — derive it alongside `Clone`.
- `Eq + Hash` is needed for `HashMap`/`HashSet` keys.
- When a field blocks derivation, implement the trait manually for that specific type.
- Don't derive traits you won't use — but err on the side of deriving more rather than less.
