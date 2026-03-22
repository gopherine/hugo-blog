---
title: "Lesson 6: Supertraits — Building trait hierarchies"
author: Atharva Pandey
series: "Rust Traits & Generics Masterclass"
lesson: 6
keywords: [Rust, rust, traits, generics, supertraits, trait hierarchy, trait inheritance]
tags: [Rust tutorial, rust, traits, generics]
date: '2024-06-21T08:50:00.000Z'
---

I had a trait called `Serialize` (homebrew, pre-serde days) that needed to format things as strings. I kept calling `.to_string()` inside default methods and wondering why the compiler complained. The type implementing my trait didn't necessarily implement `Display`. The fix was obvious in hindsight — make `Display` a *supertrait* of `Serialize`. If you want to serialize, you must be displayable. Period.

Supertraits let you build trait hierarchies where implementing one trait *requires* implementing another.

## The Basics

The syntax is a colon after the trait name:

```rust
use std::fmt;

trait Printable: fmt::Display {
    fn print(&self) {
        println!("{}", self); // This works because Self: Display is guaranteed
    }
}

struct Point {
    x: f64,
    y: f64,
}

impl fmt::Display for Point {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "({}, {})", self.x, self.y)
    }
}

impl Printable for Point {}

fn main() {
    let p = Point { x: 3.0, y: 4.0 };
    p.print(); // "(3, 4)"
}
```

`Printable: fmt::Display` means "to implement `Printable`, you must first implement `Display`." This isn't inheritance in the OOP sense — there's no data being inherited. It's a constraint: any type claiming to be `Printable` is *also* guaranteed to be `Display`.

If you try to implement `Printable` without `Display`, the compiler stops you cold:

```rust
// This will NOT compile
struct Opaque;
impl Printable for Opaque {} // ERROR: `Opaque` doesn't implement `Display`
```

## Multiple Supertraits

You can require multiple traits:

```rust
use std::fmt::{Display, Debug};

trait Loggable: Display + Debug {
    fn log(&self, level: &str) {
        println!("[{}] {} (debug: {:?})", level, self, self);
    }
}

#[derive(Debug)]
struct Event {
    name: String,
    timestamp: u64,
}

impl Display for Event {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Event '{}' at {}", self.name, self.timestamp)
    }
}

impl Loggable for Event {}

fn main() {
    let e = Event {
        name: String::from("user_login"),
        timestamp: 1718960000,
    };
    e.log("INFO");
}
```

`Loggable: Display + Debug` — you must be both displayable and debuggable. The `+` syntax works the same as trait bounds.

## Supertraits in Function Bounds

When you use a trait with supertraits as a bound, you automatically get access to all the supertrait methods:

```rust
use std::fmt::{Display, Debug};

trait Loggable: Display + Debug {
    fn severity(&self) -> u8;
}

fn handle_critical<T: Loggable>(item: &T) {
    if item.severity() > 8 {
        // Can use Display (from supertrait)
        eprintln!("CRITICAL: {}", item);
        // Can use Debug (from supertrait)
        eprintln!("Details: {:?}", item);
    }
}

#[derive(Debug)]
struct Alert {
    message: String,
    level: u8,
}

impl Display for Alert {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "ALERT: {}", self.message)
    }
}

impl Loggable for Alert {
    fn severity(&self) -> u8 {
        self.level
    }
}

fn main() {
    let alert = Alert {
        message: String::from("Database connection pool exhausted"),
        level: 9,
    };
    handle_critical(&alert);
}
```

The bound `T: Loggable` implicitly includes `T: Display + Debug`. You don't need to write `T: Loggable + Display + Debug` — that would be redundant.

## Building a Real Hierarchy

Here's a pattern I've used in production for a plugin system:

```rust
use std::fmt::{Display, Debug};

// Base trait — everything needs an identity
trait Named: Display + Debug {
    fn name(&self) -> &str;
}

// Plugins must be Named and also versioned
trait Plugin: Named {
    fn version(&self) -> (u32, u32, u32);

    fn version_string(&self) -> String {
        let (major, minor, patch) = self.version();
        format!("{} v{}.{}.{}", self.name(), major, minor, patch)
    }
}

// Runnable plugins can be executed
trait Runnable: Plugin {
    fn run(&self) -> Result<String, String>;

    fn safe_run(&self) -> String {
        match self.run() {
            Ok(output) => {
                println!("[{}] Success: {}", self.name(), output);
                output
            }
            Err(e) => {
                eprintln!("[{}] Failed: {}", self.name(), e);
                String::new()
            }
        }
    }
}

#[derive(Debug)]
struct Linter {
    plugin_name: String,
}

impl Display for Linter {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Linter({})", self.plugin_name)
    }
}

impl Named for Linter {
    fn name(&self) -> &str {
        &self.plugin_name
    }
}

impl Plugin for Linter {
    fn version(&self) -> (u32, u32, u32) {
        (1, 3, 0)
    }
}

impl Runnable for Linter {
    fn run(&self) -> Result<String, String> {
        Ok(String::from("No issues found"))
    }
}

fn execute_plugins(plugins: &[&dyn Runnable]) {
    for plugin in plugins {
        println!("Running {}", plugin.version_string());
        plugin.safe_run();
        println!();
    }
}

fn main() {
    let linter = Linter {
        plugin_name: String::from("style-checker"),
    };

    println!("{}", linter); // Display
    println!("{:?}", linter); // Debug
    println!("{}", linter.version_string()); // Plugin method using Named method

    execute_plugins(&[&linter]);
}
```

The hierarchy is `Display + Debug` → `Named` → `Plugin` → `Runnable`. Each level adds capabilities while requiring the previous level's contract. A `Runnable` is always a `Plugin`, which is always `Named`, which is always `Display + Debug`.

## The Subtlety: Supertraits Are Not Inheritance

I want to be very clear about this because OOP habits die hard.

Supertraits do NOT mean:
- `Runnable` types *are* `Plugin`s (they *implement* `Plugin`)
- `Plugin` data is inherited (there's no data to inherit)
- You can upcast `&dyn Runnable` to `&dyn Plugin` (you actually can't do this easily — more in Lesson 7)

Supertraits mean ONE thing: implementing the subtrait requires implementing the supertrait. It's a constraint, not a hierarchy of classes.

```rust
use std::fmt::{Display, Debug};

trait Base: Display + Debug {
    fn base_method(&self) -> &str;
}

trait Extended: Base {
    fn extended_method(&self) -> i32;
}

// This function accepts anything that is Extended
// and can use ALL methods from Base, Display, and Debug
fn do_everything<T: Extended>(item: &T) {
    println!("Display: {}", item);        // from Display
    println!("Debug: {:?}", item);        // from Debug
    println!("Base: {}", item.base_method());  // from Base
    println!("Ext: {}", item.extended_method()); // from Extended
}

#[derive(Debug)]
struct Thing(String);

impl Display for Thing {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Thing({})", self.0)
    }
}

impl Base for Thing {
    fn base_method(&self) -> &str {
        &self.0
    }
}

impl Extended for Thing {
    fn extended_method(&self) -> i32 {
        self.0.len() as i32
    }
}

fn main() {
    let t = Thing(String::from("hello"));
    do_everything(&t);
}
```

## When to Use Supertraits

**Good uses:**
- A default method calls another trait's methods (the motivating example)
- Logically, one capability implies another (serializable implies displayable)
- You want to guarantee a baseline of functionality for all implementors

**Avoid when:**
- You're just piling up requirements without a logical relationship
- The supertrait is only needed by some implementors, not all
- You're recreating a class hierarchy out of habit

The litmus test: does *every* type implementing this trait genuinely need the supertrait? If the answer is "well, most of them," that's a `where` clause on specific methods, not a supertrait.

```rust
use std::fmt::Display;

// DON'T do this if only some methods need Display
trait BadDesign: Display {
    fn compute(&self) -> i32;
    fn print_result(&self) { println!("{}: {}", self, self.compute()); }
}

// DO this instead
trait GoodDesign {
    fn compute(&self) -> i32;
}

// Add display capability only where needed
fn print_result<T: GoodDesign + Display>(item: &T) {
    println!("{}: {}", item, item.compute());
}

struct Calculator(i32);

impl Display for Calculator {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Calc({})", self.0)
    }
}

impl GoodDesign for Calculator {
    fn compute(&self) -> i32 { self.0 * 2 }
}

fn main() {
    let c = Calculator(21);
    print_result(&c);
}
```

## Key Takeaways

Supertraits create requirements: implementing trait A requires implementing trait B first. They're constraints, not inheritance. Use them when default methods need supertrait capabilities, or when one trait logically implies another. Don't use them just to pile up requirements — `where` clauses on individual methods are often more flexible.

Next — `dyn Trait`, where we leave the static world and enter runtime polymorphism.
