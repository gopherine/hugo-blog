---
title: "Lesson 11: Essential Std Traits — Iterator, Display, From, Default"
author: Atharva Pandey
series: "Rust Traits & Generics Masterclass"
lesson: 11
keywords: [Rust, rust, traits, generics, Iterator, Display, From, Into, Default, standard library]
tags: [Rust tutorial, rust, traits, generics]
date: '2024-07-02T08:30:00.000Z'
---

There's a tier list of Rust traits. Some you'll implement once in your career. Some you'll implement weekly. And then there are the ones you'll implement so often they become muscle memory — `Display`, `From`, `Default`, `Iterator`. These four (plus a few friends) are the backbone of idiomatic Rust. If you internalize them, your types will feel native. If you skip them, your types will feel like second-class citizens in the ecosystem.

## Display and Debug

`Debug` is for developers. `Display` is for users. Implement both.

```rust
use std::fmt;

#[derive(Debug)] // Debug can almost always be derived
struct HttpResponse {
    status: u16,
    body: String,
    headers: Vec<(String, String)>,
}

// Display needs manual implementation — you choose the format
impl fmt::Display for HttpResponse {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "HTTP {} ({} bytes)", self.status, self.body.len())
    }
}

fn main() {
    let resp = HttpResponse {
        status: 200,
        body: String::from(r#"{"ok": true}"#),
        headers: vec![
            (String::from("Content-Type"), String::from("application/json")),
        ],
    };

    println!("{}", resp);   // Display: "HTTP 200 (13 bytes)"
    println!("{:?}", resp); // Debug: full struct dump
    println!("{:#?}", resp); // Pretty debug: indented struct dump
}
```

`Debug` is derivable on almost anything. `Display` is intentional — it's the human-readable representation you want users to see. Don't derive what should be designed.

A useful trick: implementing `Display` gives you `ToString` for free (via a blanket impl in std). So `resp.to_string()` works automatically.

## From and Into

`From` is the idiomatic conversion trait. Implement `From`, get `Into` free:

```rust
#[derive(Debug)]
struct UserId(u64);

#[derive(Debug)]
struct Username(String);

// From u64
impl From<u64> for UserId {
    fn from(id: u64) -> Self {
        UserId(id)
    }
}

// From i32 (with conversion)
impl From<i32> for UserId {
    fn from(id: i32) -> Self {
        UserId(id as u64)
    }
}

// From &str
impl From<&str> for Username {
    fn from(name: &str) -> Self {
        Username(name.to_string())
    }
}

// From String
impl From<String> for Username {
    fn from(name: String) -> Self {
        Username(name)
    }
}

fn greet(user_id: impl Into<UserId>, name: impl Into<Username>) {
    let id = user_id.into();
    let username = name.into();
    println!("Hello {:?} (ID: {:?})", username, id);
}

fn main() {
    // All of these work because of From impls:
    greet(42u64, "Atharva");
    greet(7i32, String::from("Alice"));

    // Direct conversion
    let id: UserId = 100u64.into();
    let id2 = UserId::from(200u64);
    println!("{:?}, {:?}", id, id2);
}
```

The `impl Into<UserId>` parameter pattern is extremely common in library APIs. It lets callers pass any type that converts to `UserId` — flexible for the caller, specific internally.

### TryFrom for Fallible Conversions

When conversion might fail, use `TryFrom`:

```rust
use std::fmt;

#[derive(Debug)]
struct Port(u16);

#[derive(Debug)]
struct PortError(String);

impl fmt::Display for PortError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl TryFrom<i32> for Port {
    type Error = PortError;

    fn try_from(value: i32) -> Result<Self, Self::Error> {
        if value < 0 {
            Err(PortError(format!("Port cannot be negative: {}", value)))
        } else if value > 65535 {
            Err(PortError(format!("Port too large: {}", value)))
        } else {
            Ok(Port(value as u16))
        }
    }
}

impl TryFrom<&str> for Port {
    type Error = PortError;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        let num: i32 = value
            .parse()
            .map_err(|_| PortError(format!("Not a number: {}", value)))?;
        Port::try_from(num)
    }
}

fn main() {
    println!("{:?}", Port::try_from(8080));    // Ok(Port(8080))
    println!("{:?}", Port::try_from(-1));       // Err
    println!("{:?}", Port::try_from(70000));    // Err
    println!("{:?}", Port::try_from("443"));    // Ok(Port(443))
    println!("{:?}", Port::try_from("abc"));    // Err
}
```

## Default

`Default` provides a "zero value" for your type. It's used everywhere — struct initialization, `Option::unwrap_or_default()`, collection creation:

```rust
#[derive(Debug)]
struct Config {
    host: String,
    port: u16,
    max_retries: u32,
    timeout_ms: u64,
    verbose: bool,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            host: String::from("localhost"),
            port: 8080,
            max_retries: 3,
            timeout_ms: 5000,
            verbose: false,
        }
    }
}

fn main() {
    // Full default
    let config = Config::default();
    println!("{:?}", config);

    // Partial override with struct update syntax
    let prod_config = Config {
        host: String::from("0.0.0.0"),
        port: 443,
        ..Config::default()
    };
    println!("{:?}", prod_config);

    // Used with Option
    let maybe_timeout: Option<u64> = None;
    let timeout = maybe_timeout.unwrap_or_default(); // 0 for u64
    println!("Timeout: {}", timeout);
}
```

The `..Config::default()` pattern is incredibly ergonomic for "I want defaults for everything except these two fields." I use it constantly.

## Iterator

Implementing `Iterator` is the gateway to Rust's entire iterator ecosystem — `map`, `filter`, `collect`, `sum`, `any`, `all`, `zip`, `chain`, `take`, `skip`, and dozens more.

```rust
struct Fibonacci {
    a: u64,
    b: u64,
}

impl Fibonacci {
    fn new() -> Self {
        Fibonacci { a: 0, b: 1 }
    }
}

impl Iterator for Fibonacci {
    type Item = u64;

    fn next(&mut self) -> Option<u64> {
        let current = self.a;
        let next = self.a + self.b;
        self.a = self.b;
        self.b = next;
        Some(current) // infinite iterator
    }
}

fn main() {
    // First 10 fibonacci numbers
    let fibs: Vec<u64> = Fibonacci::new().take(10).collect();
    println!("{:?}", fibs);

    // Sum of first 20
    let sum: u64 = Fibonacci::new().take(20).sum();
    println!("Sum of first 20: {}", sum);

    // First fib over 1000
    let big = Fibonacci::new().find(|&n| n > 1000);
    println!("First fib > 1000: {:?}", big);

    // Every other fib, first 5
    let alternating: Vec<u64> = Fibonacci::new()
        .enumerate()
        .filter(|(i, _)| i % 2 == 0)
        .map(|(_, v)| v)
        .take(5)
        .collect();
    println!("Alternating: {:?}", alternating);
}
```

You implement ONE method — `next` — and get the entire iterator API for free. That's the power of the standard library's default methods and blanket impls working together.

### IntoIterator

`IntoIterator` lets your type work with `for` loops:

```rust
struct TaskList {
    tasks: Vec<String>,
}

impl TaskList {
    fn new() -> Self {
        TaskList { tasks: Vec::new() }
    }

    fn add(&mut self, task: &str) {
        self.tasks.push(task.to_string());
    }
}

// Consuming iterator — takes ownership
impl IntoIterator for TaskList {
    type Item = String;
    type IntoIter = std::vec::IntoIter<String>;

    fn into_iter(self) -> Self::IntoIter {
        self.tasks.into_iter()
    }
}

// Borrowing iterator
impl<'a> IntoIterator for &'a TaskList {
    type Item = &'a String;
    type IntoIter = std::slice::Iter<'a, String>;

    fn into_iter(self) -> Self::IntoIter {
        self.tasks.iter()
    }
}

fn main() {
    let mut list = TaskList::new();
    list.add("Write blog post");
    list.add("Review PR");
    list.add("Deploy to prod");

    // Borrow — list is still usable after
    for task in &list {
        println!("TODO: {}", task);
    }

    println!("Total tasks: {}", list.tasks.len());

    // Consume — list is moved
    for task in list {
        println!("Doing: {}", task);
    }
    // list is gone now
}
```

## Clone and Copy

`Clone` means explicit duplication. `Copy` means implicit bitwise copy. Every `Copy` type is also `Clone`, but not vice versa:

```rust
// Copy + Clone — small, simple, no heap data
#[derive(Debug, Clone, Copy)]
struct Point {
    x: f64,
    y: f64,
}

// Clone only — owns heap data
#[derive(Debug, Clone)]
struct Polygon {
    points: Vec<Point>,
    name: String,
}

fn main() {
    let p1 = Point { x: 1.0, y: 2.0 };
    let p2 = p1; // Copy — p1 is still valid
    println!("p1: {:?}, p2: {:?}", p1, p2);

    let poly1 = Polygon {
        points: vec![p1, p2],
        name: String::from("triangle"),
    };
    let poly2 = poly1.clone(); // Must explicitly clone
    println!("poly1: {:?}", poly1);
    println!("poly2: {:?}", poly2);

    // let poly3 = poly1; // This MOVES poly1, not copies
}
```

Rule of thumb: derive `Copy` for small value types without heap allocations (coordinates, colors, IDs). Use `Clone` for everything else that needs duplication.

## PartialEq, Eq, PartialOrd, Ord

The comparison traits. Usually derived:

```rust
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
struct Priority(u8);

#[derive(Debug, PartialEq)]
struct Task {
    name: String,
    priority: Priority,
}

fn main() {
    let p1 = Priority(1);
    let p2 = Priority(3);
    let p3 = Priority(1);

    println!("{}", p1 == p3); // true
    println!("{}", p1 < p2);  // true

    let mut priorities = vec![Priority(3), Priority(1), Priority(2)];
    priorities.sort(); // Requires Ord
    println!("{:?}", priorities);

    let t1 = Task { name: String::from("A"), priority: Priority(1) };
    let t2 = Task { name: String::from("A"), priority: Priority(1) };
    println!("Tasks equal: {}", t1 == t2);
}
```

`PartialEq` is for `==`. `Eq` is a marker saying equality is reflexive (NaN breaks this for floats, which is why `f64` is `PartialEq` but not `Eq`). `PartialOrd` is for `<`, `>`. `Ord` is for total ordering (required for `.sort()`).

## Hash

Needed for `HashMap` and `HashSet` keys:

```rust
use std::collections::HashMap;
use std::hash::{Hash, Hasher};

#[derive(Debug, Eq)]
struct CaseInsensitiveString(String);

impl PartialEq for CaseInsensitiveString {
    fn eq(&self, other: &Self) -> bool {
        self.0.to_lowercase() == other.0.to_lowercase()
    }
}

impl Hash for CaseInsensitiveString {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.0.to_lowercase().hash(state);
    }
}

fn main() {
    let mut map = HashMap::new();
    map.insert(CaseInsensitiveString(String::from("Content-Type")), "application/json");

    // Lookup with different casing
    let key = CaseInsensitiveString(String::from("content-type"));
    println!("{:?}", map.get(&key)); // Some("application/json")
}
```

Critical rule: if two values are `PartialEq` equal, they MUST produce the same `Hash`. Breaking this invariant leads to HashMap corruption.

## Key Takeaways

The essential std traits to know cold: `Display`/`Debug` for formatting, `From`/`Into`/`TryFrom` for conversions, `Default` for zero values, `Iterator`/`IntoIterator` for iteration, `Clone`/`Copy` for duplication, `PartialEq`/`Eq`/`PartialOrd`/`Ord` for comparison, `Hash` for hashing.

Derive when you can. Implement manually when the derived behavior isn't what you want. Every trait you implement makes your type more of a first-class citizen in the Rust ecosystem.

Next — operator overloading, where your custom types start behaving like built-in types.
