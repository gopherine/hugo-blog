---
title: "Lesson 19: Traits — Your first abstraction"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 19
keywords: [Rust, rust, traits, trait implementation, impl, abstraction, polymorphism, Display, Debug]
tags: [Rust tutorial, rust, beginner]
date: '2024-04-07T15:45:00.000Z'
---

Traits are the mechanism I miss most when I leave Rust. They're interfaces without inheritance, type classes without the math, and the foundation of every abstraction in the language. If structs define what data *is*, traits define what data *does*.

## What Is a Trait?

A trait defines a set of methods that a type can implement:

```rust
trait Greet {
    fn hello(&self) -> String;
}

struct Person {
    name: String,
}

struct Robot {
    id: u32,
}

impl Greet for Person {
    fn hello(&self) -> String {
        format!("Hi, I'm {}!", self.name)
    }
}

impl Greet for Robot {
    fn hello(&self) -> String {
        format!("UNIT-{} OPERATIONAL", self.id)
    }
}

fn main() {
    let person = Person { name: String::from("Alice") };
    let robot = Robot { id: 42 };

    println!("{}", person.hello());
    println!("{}", robot.hello());
}
```

The trait `Greet` declares that any implementing type must have a `hello` method. `Person` and `Robot` each provide their own implementation. Different types, same interface.

## Default Implementations

Traits can provide default method implementations:

```rust
trait Summary {
    fn title(&self) -> &str;

    fn summarize(&self) -> String {
        format!("{} (read more...)", self.title())
    }
}

struct Article {
    title: String,
    content: String,
}

struct Tweet {
    username: String,
    text: String,
}

impl Summary for Article {
    fn title(&self) -> &str {
        &self.title
    }

    // Uses the default summarize()
}

impl Summary for Tweet {
    fn title(&self) -> &str {
        &self.username
    }

    // Override the default
    fn summarize(&self) -> String {
        format!("@{}: {}", self.username, self.text)
    }
}

fn main() {
    let article = Article {
        title: String::from("Rust Is Great"),
        content: String::from("Long article content..."),
    };

    let tweet = Tweet {
        username: String::from("rustlang"),
        text: String::from("Rust 1.75 is out!"),
    };

    println!("{}", article.summarize());  // uses default
    println!("{}", tweet.summarize());    // uses override
}
```

Default implementations can call other methods in the same trait — even ones without defaults. This lets you build complex behavior from a small number of required methods.

## Traits as Parameters

Here's where traits become powerful. You can write functions that accept *any type* implementing a trait:

```rust
trait Area {
    fn area(&self) -> f64;
}

struct Circle {
    radius: f64,
}

struct Rectangle {
    width: f64,
    height: f64,
}

impl Area for Circle {
    fn area(&self) -> f64 {
        std::f64::consts::PI * self.radius * self.radius
    }
}

impl Area for Rectangle {
    fn area(&self) -> f64 {
        self.width * self.height
    }
}

// Accept any type that implements Area
fn print_area(shape: &impl Area) {
    println!("Area: {:.2}", shape.area());
}

fn main() {
    let circle = Circle { radius: 5.0 };
    let rect = Rectangle { width: 4.0, height: 6.0 };

    print_area(&circle);
    print_area(&rect);
}
```

`&impl Area` means "a reference to any type that implements the Area trait." The compiler generates specialized code for each concrete type — this is called *monomorphization*. Zero runtime cost.

### The Longer Syntax: Trait Bounds

`&impl Area` is sugar for a trait bound:

```rust
fn print_area<T: Area>(shape: &T) {
    println!("Area: {:.2}", shape.area());
}
```

Both are equivalent. Use `impl Trait` for simple cases, trait bounds for complex ones. You need trait bounds when:

```rust
// Two parameters must be the SAME type:
fn compare<T: Area>(a: &T, b: &T) -> bool {
    a.area() > b.area()
}

// Two parameters can be DIFFERENT types:
fn print_both(a: &impl Area, b: &impl Area) {
    println!("{:.2} and {:.2}", a.area(), b.area());
}
```

### Multiple Trait Bounds

```rust
use std::fmt;

fn print_and_measure(item: &(impl Area + fmt::Display)) {
    println!("{}: area = {:.2}", item, item.area());
}

// Equivalent with where clause (cleaner for complex bounds):
fn print_and_measure_v2<T>(item: &T)
where
    T: Area + fmt::Display,
{
    println!("{}: area = {:.2}", item, item.area());
}
```

The `where` clause is more readable when you have multiple type parameters with multiple bounds.

## Standard Library Traits

Rust's standard library defines traits you'll use constantly. Here are the most important ones.

### Display — Human-Readable Output

```rust
use std::fmt;

struct Point {
    x: f64,
    y: f64,
}

impl fmt::Display for Point {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "({}, {})", self.x, self.y)
    }
}

fn main() {
    let p = Point { x: 3.0, y: 4.0 };
    println!("{p}");         // uses Display
    println!("{}", p);       // same thing
    let s = p.to_string();  // Display gives you to_string() for free
    println!("{s}");
}
```

`Display` is what `{}` uses in format strings. If your type should be printable by users (not just developers), implement Display.

### Debug — Developer Output

```rust
#[derive(Debug)]
struct Config {
    host: String,
    port: u16,
    debug: bool,
}

fn main() {
    let config = Config {
        host: String::from("localhost"),
        port: 8080,
        debug: true,
    };

    println!("{:?}", config);   // compact debug
    println!("{:#?}", config);  // pretty debug
}
```

You almost always derive `Debug` rather than implementing it manually. `{:?}` is for developers; `{}` is for users.

### Clone and Copy

```rust
#[derive(Debug, Clone)]
struct Document {
    title: String,
    pages: u32,
}

fn main() {
    let doc1 = Document {
        title: String::from("Rust Guide"),
        pages: 100,
    };

    let doc2 = doc1.clone();  // explicit deep copy
    println!("{:?}", doc1);    // doc1 still valid
    println!("{:?}", doc2);
}
```

`Clone` requires an explicit `.clone()` call. `Copy` makes cloning implicit (for small, stack-only types like integers). Don't implement `Copy` on types with heap data — it should be reserved for types where copying is genuinely cheap.

### PartialEq and Eq

```rust
#[derive(Debug, PartialEq)]
struct Color {
    r: u8,
    g: u8,
    b: u8,
}

fn main() {
    let a = Color { r: 255, g: 0, b: 0 };
    let b = Color { r: 255, g: 0, b: 0 };
    let c = Color { r: 0, g: 255, b: 0 };

    println!("a == b: {}", a == b);  // true
    println!("a == c: {}", a == c);  // false
}
```

### From and Into — Type Conversions

```rust
struct Celsius(f64);
struct Fahrenheit(f64);

impl From<Celsius> for Fahrenheit {
    fn from(c: Celsius) -> Self {
        Fahrenheit(c.0 * 9.0 / 5.0 + 32.0)
    }
}

// Implementing From<Celsius> for Fahrenheit automatically gives you
// Into<Fahrenheit> for Celsius

fn main() {
    let boiling = Celsius(100.0);
    let f: Fahrenheit = boiling.into();  // uses Into (derived from From)
    println!("{}F", f.0);

    let freezing = Celsius(0.0);
    let f = Fahrenheit::from(freezing);  // uses From directly
    println!("{}F", f.0);
}
```

Always implement `From`, not `Into`. You get `Into` for free. This is a strong convention — clippy warns you if you implement `Into` directly.

## Trait Objects — Dynamic Dispatch

Sometimes you need a collection of different types that share a trait:

```rust
trait Drawable {
    fn draw(&self);
}

struct Circle {
    radius: f64,
}

struct Square {
    side: f64,
}

impl Drawable for Circle {
    fn draw(&self) {
        println!("Drawing circle with radius {}", self.radius);
    }
}

impl Drawable for Square {
    fn draw(&self) {
        println!("Drawing square with side {}", self.side);
    }
}

fn main() {
    // Vec of trait objects — different concrete types in one collection
    let shapes: Vec<Box<dyn Drawable>> = vec![
        Box::new(Circle { radius: 5.0 }),
        Box::new(Square { side: 3.0 }),
        Box::new(Circle { radius: 2.0 }),
    ];

    for shape in &shapes {
        shape.draw();
    }
}
```

`Box<dyn Drawable>` is a *trait object*. `dyn` means dynamic dispatch — the method to call is determined at runtime, not compile time. This has a small performance cost (virtual function call) but gives you runtime polymorphism.

Use `impl Trait` (static dispatch) when you can, `dyn Trait` (dynamic dispatch) when you need heterogeneous collections or runtime flexibility.

## A Practical Example: Pluggable Formatters

```rust
use std::fmt;

trait Formatter {
    fn format(&self, data: &[(&str, &str)]) -> String;
}

struct JsonFormatter;
struct CsvFormatter;
struct TableFormatter;

impl Formatter for JsonFormatter {
    fn format(&self, data: &[(&str, &str)]) -> String {
        let entries: Vec<String> = data.iter()
            .map(|(k, v)| format!("  \"{k}\": \"{v}\""))
            .collect();
        format!("{{\n{}\n}}", entries.join(",\n"))
    }
}

impl Formatter for CsvFormatter {
    fn format(&self, data: &[(&str, &str)]) -> String {
        let mut output = String::from("key,value\n");
        for (k, v) in data {
            output.push_str(&format!("{k},{v}\n"));
        }
        output
    }
}

impl Formatter for TableFormatter {
    fn format(&self, data: &[(&str, &str)]) -> String {
        let max_key = data.iter().map(|(k, _)| k.len()).max().unwrap_or(0);
        let mut output = String::new();
        for (k, v) in data {
            output.push_str(&format!("{:<width$} | {v}\n", k, width = max_key));
        }
        output
    }
}

fn print_report(formatter: &dyn Formatter) {
    let data = vec![
        ("name", "Alice"),
        ("role", "Engineer"),
        ("team", "Platform"),
    ];

    println!("{}", formatter.format(&data));
}

fn main() {
    println!("=== JSON ===");
    print_report(&JsonFormatter);

    println!("=== CSV ===");
    print_report(&CsvFormatter);

    println!("=== Table ===");
    print_report(&TableFormatter);
}
```

This is the strategy pattern, implemented cleanly without inheritance hierarchies. Adding a new format is one struct and one `impl` block. Nothing else changes.

## Supertraits

A trait can require another trait:

```rust
use std::fmt;

trait Printable: fmt::Display {
    fn print(&self) {
        println!("{self}");
    }
}

struct Name(String);

impl fmt::Display for Name {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl Printable for Name {}  // uses default print() method

fn main() {
    let name = Name(String::from("Alice"));
    name.print();
}
```

`Printable: fmt::Display` means "to implement Printable, you must also implement Display." This is how you build trait hierarchies — without class inheritance.

## Key Takeaways

1. Traits define shared behavior across types
2. `impl Trait` in parameters gives you zero-cost generic functions
3. `dyn Trait` gives you runtime polymorphism when you need it
4. Implement `From` for conversions, `Display` for printing, `Debug` for debugging
5. Derive standard traits when you can, implement manually when you must
6. Prefer composition (traits) over inheritance (Rust doesn't have inheritance)

Traits are the backbone of Rust abstraction. Every generic function, every library API, every collection method — they all work through traits. Master them and you've mastered Rust's approach to polymorphism.

Next: generics — writing code that works for any type.
