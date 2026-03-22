---
title: "Lesson 2: Destructuring — Structs, tuples, enums, nested"
author: Atharva Pandey
series: "Rust Pattern Matching & Enums Deep Dive"
lesson: 2
keywords: [Rust, rust, pattern matching, destructuring, structs, tuples, enums, nested patterns]
tags: [Rust tutorial, rust, pattern-matching]
date: '2024-07-22T16:45:00.000Z'
---

I used to write code like `point.x`, `point.y`, `point.z` over and over in the same function. Three fields, three accesses, repeated twelve times. Then I learned destructuring and realized I'd been doing the equivalent of opening a package, looking at each item individually, and putting it back before looking at the next one.

Destructuring is opening the package and grabbing everything at once.

## The Problem With Field-by-Field Access

When you work with compound types — structs, tuples, enums with data — you constantly need to pull values out of them. In most languages, this means dot access or method calls, and you end up with verbose code that obscures the actual logic:

```rust
struct Point {
    x: f64,
    y: f64,
}

// The verbose way
fn distance_from_origin_verbose(p: &Point) -> f64 {
    let x = p.x;
    let y = p.y;
    (x * x + y * y).sqrt()
}
```

It works, but it's noisy. And with nested structures, it gets worse fast.

## The Idiomatic Way

Rust lets you destructure values directly in `let` bindings, function parameters, and `match` arms. The syntax mirrors the structure of the type — you're literally describing the shape of the data you expect.

### Struct Destructuring

```rust
struct Point {
    x: f64,
    y: f64,
}

fn distance_from_origin(p: &Point) -> f64 {
    let Point { x, y } = p;
    (x * x + y * y).sqrt()
}

// Even cleaner — destructure right in the function signature
fn midpoint(Point { x: x1, y: y1 }: &Point, Point { x: x2, y: y2 }: &Point) -> Point {
    Point {
        x: (x1 + x2) / 2.0,
        y: (y1 + y2) / 2.0,
    }
}

fn main() {
    let a = Point { x: 3.0, y: 4.0 };
    let b = Point { x: 7.0, y: 1.0 };

    println!("Distance from origin: {}", distance_from_origin(&a));

    let mid = midpoint(&a, &b);
    println!("Midpoint: ({}, {})", mid.x, mid.y);
}
```

The `Point { x, y } = p` syntax is shorthand — when the variable name matches the field name, you don't need to write `Point { x: x, y: y }`. If you want different names, use the full syntax: `Point { x: horizontal, y: vertical }`.

### Ignoring Fields

You don't always need every field. Use `..` to ignore the rest:

```rust
struct Config {
    host: String,
    port: u16,
    max_connections: u32,
    timeout_ms: u64,
    debug: bool,
}

fn connection_string(config: &Config) -> String {
    let Config { host, port, .. } = config;
    format!("{}:{}", host, port)
}

fn main() {
    let cfg = Config {
        host: "localhost".to_string(),
        port: 5432,
        max_connections: 100,
        timeout_ms: 3000,
        debug: false,
    };
    println!("{}", connection_string(&cfg));
}
```

This is a big deal for maintainability. When someone adds a new field to `Config`, functions using `..` don't need to change. Functions that destructure all fields explicitly *will* break — and sometimes that's what you want, to force a review.

### Tuple Destructuring

Tuples are even more natural to destructure since they don't have named fields:

```rust
fn swap<T: Copy>(pair: (T, T)) -> (T, T) {
    let (a, b) = pair;
    (b, a)
}

fn min_max(values: &[i32]) -> Option<(i32, i32)> {
    if values.is_empty() {
        return None;
    }
    let mut min = values[0];
    let mut max = values[0];
    for &v in &values[1..] {
        if v < min { min = v; }
        if v > max { max = v; }
    }
    Some((min, max))
}

fn main() {
    let pair = (1, 2);
    let (b, a) = swap(pair);
    println!("Swapped: ({}, {})", a, b);

    let data = vec![3, 1, 4, 1, 5, 9, 2, 6];
    if let Some((min, max)) = min_max(&data) {
        println!("Range: {} to {}", min, max);
    }
}
```

The `if let` on that last line is destructuring too — it pulls the tuple out of the `Some` variant in one step.

### Enum Destructuring

This is where destructuring really shines. Enums in Rust carry data, and matching on an enum means pulling that data out:

```rust
#[derive(Debug)]
enum Shape {
    Circle { radius: f64 },
    Rectangle { width: f64, height: f64 },
    Triangle { base: f64, height: f64 },
}

fn area(shape: &Shape) -> f64 {
    match shape {
        Shape::Circle { radius } => std::f64::consts::PI * radius * radius,
        Shape::Rectangle { width, height } => width * height,
        Shape::Triangle { base, height } => 0.5 * base * height,
    }
}

fn describe(shape: &Shape) -> String {
    match shape {
        Shape::Circle { radius } => format!("circle with radius {:.1}", radius),
        Shape::Rectangle { width, height } if (width - height).abs() < f64::EPSILON => {
            format!("square with side {:.1}", width)
        }
        Shape::Rectangle { width, height } => {
            format!("rectangle {:.1} x {:.1}", width, height)
        }
        Shape::Triangle { .. } => "triangle".to_string(),
    }
}

fn main() {
    let shapes = vec![
        Shape::Circle { radius: 5.0 },
        Shape::Rectangle { width: 4.0, height: 4.0 },
        Shape::Rectangle { width: 3.0, height: 7.0 },
        Shape::Triangle { base: 6.0, height: 3.0 },
    ];

    for shape in &shapes {
        println!("{}: area = {:.2}", describe(shape), area(shape));
    }
}
```

Each match arm destructures the enum variant and binds its fields to local variables. The compiler checks that you're accessing the right fields for the right variant — you can't accidentally read `radius` from a `Rectangle`.

## Nested Destructuring

Here's where things get powerful. You can destructure through multiple levels of nesting in a single pattern:

```rust
#[derive(Debug)]
struct Address {
    street: String,
    city: String,
    zip: String,
}

#[derive(Debug)]
enum ContactMethod {
    Email(String),
    Phone(String),
    Mail(Address),
}

#[derive(Debug)]
struct Customer {
    name: String,
    preferred_contact: ContactMethod,
}

fn contact_summary(customer: &Customer) -> String {
    match customer {
        Customer {
            name,
            preferred_contact: ContactMethod::Email(addr),
        } => format!("Email {} at {}", name, addr),

        Customer {
            name,
            preferred_contact: ContactMethod::Phone(number),
        } => format!("Call {} at {}", name, number),

        Customer {
            name,
            preferred_contact: ContactMethod::Mail(Address { city, .. }),
        } => format!("Mail {} in {}", name, city),
    }
}

fn main() {
    let customers = vec![
        Customer {
            name: "Alice".to_string(),
            preferred_contact: ContactMethod::Email("alice@example.com".to_string()),
        },
        Customer {
            name: "Bob".to_string(),
            preferred_contact: ContactMethod::Phone("555-0123".to_string()),
        },
        Customer {
            name: "Carol".to_string(),
            preferred_contact: ContactMethod::Mail(Address {
                street: "123 Main St".to_string(),
                city: "Portland".to_string(),
                zip: "97201".to_string(),
            }),
        },
    ];

    for c in &customers {
        println!("{}", contact_summary(c));
    }
}
```

That last arm reaches into the `Customer`, pulls out the `name`, matches the `ContactMethod::Mail` variant, and then destructures the `Address` inside it — all in one pattern. Try doing that with `if`/`else` chains and field access. It's three times as much code and far less readable.

## Destructuring References

This catches people off guard. When you match on a reference, you need to account for the reference in your pattern:

```rust
fn classify(values: &[i32]) {
    for value in values {
        // `value` is &i32 here
        match value {
            &0 => println!("zero"),
            &1..=9 => println!("{} is a single digit", value),
            _ => println!("{} is bigger", value),
        }
    }

    // Alternative: dereference in the pattern with match ergonomics
    for value in values {
        match value {
            0 => println!("zero"),
            1..=9 => println!("{} is a single digit", value),
            _ => println!("{} is bigger", value),
        }
    }
}

fn main() {
    classify(&[0, 5, 42, 1, 100]);
}
```

Rust has "match ergonomics" (stabilized in Rust 1.26) that automatically handle references in patterns. Both forms above work, but the second is more common in modern Rust. The compiler figures out the reference levels for you.

## Destructuring in `for` Loops

You can destructure right in the loop variable — this is one of my favorite patterns for iterating over collections of tuples or key-value pairs:

```rust
use std::collections::HashMap;

fn print_scores(scores: &HashMap<String, Vec<u32>>) {
    for (name, grades) in scores {
        let avg: f64 = grades.iter().map(|&g| g as f64).sum::<f64>() / grades.len() as f64;
        println!("{}: {:.1} average", name, avg);
    }
}

fn find_pairs(values: &[i32]) -> Vec<(i32, i32)> {
    let mut pairs = Vec::new();
    for (i, &a) in values.iter().enumerate() {
        for &b in &values[i + 1..] {
            if a + b == 10 {
                pairs.push((a, b));
            }
        }
    }
    pairs
}

fn main() {
    let mut scores = HashMap::new();
    scores.insert("Alice".to_string(), vec![92, 88, 95]);
    scores.insert("Bob".to_string(), vec![78, 85, 80]);
    print_scores(&scores);

    let values = vec![1, 9, 3, 7, 5, 5, 2, 8];
    for (a, b) in find_pairs(&values) {
        println!("{} + {} = 10", a, b);
    }
}
```

## When Destructuring Gets Too Deep

There's a readability limit. I've seen patterns that destructure four or five levels deep, and they become harder to read than the field access they replace. My rule of thumb: if a pattern doesn't fit on two lines, extract the inner structure into a separate `let` or a helper function.

```rust
// Too deep — hard to read
// match order {
//     Order { customer: Customer { address: Address { city, .. }, .. }, items, .. } => { ... }
// }

// Better — break it up
struct Order {
    customer: Customer,
    items: Vec<String>,
}

struct Customer {
    name: String,
    city: String,
}

fn process_order(order: &Order) {
    let Order { customer, items } = order;
    let Customer { name, city } = customer;
    println!("Shipping {} items to {} in {}", items.len(), name, city);
}

fn main() {
    let order = Order {
        customer: Customer {
            name: "Alice".to_string(),
            city: "Seattle".to_string(),
        },
        items: vec!["widget".to_string(), "gadget".to_string()],
    };
    process_order(&order);
}
```

Two levels of destructuring, each on its own line. Clear, readable, and you still get all the benefits of pattern matching.

Destructuring is the workhorse of Rust pattern matching. It's not flashy, but it's everywhere — every `match`, every `if let`, every `for` loop. Get comfortable with it now because everything else in this series builds on it.

Next up: match guards and bindings. Because sometimes the shape of the data isn't enough — you need to inspect the values too.
