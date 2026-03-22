---
title: "Lesson 7: dyn Trait — Runtime polymorphism and its cost"
author: Atharva Pandey
series: "Rust Traits & Generics Masterclass"
lesson: 7
keywords: [Rust, rust, traits, generics, dyn trait, dynamic dispatch, trait objects, vtable]
tags: [Rust tutorial, rust, traits, generics]
date: '2024-06-23T19:15:00.000Z'
---

Every Rust programmer hits this wall eventually. You have a `Vec` and you want to put different types in it — all implementing the same trait, but different concrete types. You try `Vec<impl Trait>` and the compiler says no. You try `Vec<T>` with generics and realize `T` can only be one type at a time. That's when you discover `dyn Trait`, and the first real tradeoff in Rust's type system: static dispatch vs dynamic dispatch.

I remember the exact moment — I was building a pipeline with different processing stages, each a different struct implementing `Stage`. I needed to store them in a `Vec` and iterate. Generics couldn't do it. `dyn Trait` could.

## The Problem Static Dispatch Can't Solve

With generics, the type is determined at compile time:

```rust
trait Processor {
    fn process(&self, input: &str) -> String;
}

struct Uppercase;
struct Trim;

impl Processor for Uppercase {
    fn process(&self, input: &str) -> String {
        input.to_uppercase()
    }
}

impl Processor for Trim {
    fn process(&self, input: &str) -> String {
        input.trim().to_string()
    }
}

// This works — but both elements must be the same type
fn process_all_generic<T: Processor>(processors: &[T], input: &str) -> String {
    let mut result = input.to_string();
    for p in processors {
        result = p.process(&result);
    }
    result
}

fn main() {
    let trimmers = [Trim, Trim]; // Fine — all Trim
    println!("{}", process_all_generic(&trimmers, "  hello  "));

    // But you can't mix types:
    // let mixed = [Uppercase, Trim]; // ERROR: mismatched types
}
```

You can't have `[Uppercase, Trim]` because those are different types. A `[T]` can only hold one `T`.

## Enter `dyn Trait`

`dyn Trait` erases the concrete type and works through a vtable — a lookup table of function pointers. You always use it behind a pointer: `&dyn Trait`, `Box<dyn Trait>`, or `Arc<dyn Trait>`.

```rust
trait Processor {
    fn process(&self, input: &str) -> String;
}

struct Uppercase;
struct Trim;
struct Append(String);

impl Processor for Uppercase {
    fn process(&self, input: &str) -> String {
        input.to_uppercase()
    }
}

impl Processor for Trim {
    fn process(&self, input: &str) -> String {
        input.trim().to_string()
    }
}

impl Processor for Append {
    fn process(&self, input: &str) -> String {
        format!("{}{}", input, self.0)
    }
}

fn run_pipeline(processors: &[Box<dyn Processor>], input: &str) -> String {
    let mut result = input.to_string();
    for p in processors {
        result = p.process(&result);
    }
    result
}

fn main() {
    let pipeline: Vec<Box<dyn Processor>> = vec![
        Box::new(Trim),
        Box::new(Uppercase),
        Box::new(Append(String::from("!!!"))),
    ];

    let result = run_pipeline(&pipeline, "  hello world  ");
    println!("{}", result); // "HELLO WORLD!!!"
}
```

Three different types, one `Vec`, processed uniformly. This is runtime polymorphism.

## How It Works: The Vtable

When you create a `Box<dyn Processor>`, Rust stores two pointers:

1. A pointer to the data (the actual `Trim`/`Uppercase`/`Append` value on the heap)
2. A pointer to the vtable — a table of function pointers for that type's implementation

When you call `p.process(input)`, Rust looks up the `process` function pointer in the vtable and calls it. This is an indirect function call — the CPU follows a pointer to find what code to execute, rather than jumping directly to a known address.

The cost:
- **One heap allocation** per `Box<dyn Trait>` (the data)
- **One pointer indirection** per method call (vtable lookup)
- **No inlining** — the compiler can't inline a function it doesn't know at compile time

Is this expensive? For most applications, no. For hot loops processing millions of items per second, maybe. Profile first.

## `&dyn Trait` vs `Box<dyn Trait>`

You have choices about how to hold trait objects:

```rust
trait Drawable {
    fn draw(&self);
}

struct Circle { radius: f64 }
struct Square { side: f64 }

impl Drawable for Circle {
    fn draw(&self) { println!("Drawing circle (r={})", self.radius); }
}

impl Drawable for Square {
    fn draw(&self) { println!("Drawing square (s={})", self.side); }
}

// Borrowed reference — no heap allocation, but limited lifetime
fn draw_ref(item: &dyn Drawable) {
    item.draw();
}

// Owned — heap allocated, no lifetime concerns
fn draw_owned(item: Box<dyn Drawable>) {
    item.draw();
}

// Stored in a collection — must be Box or Arc
fn draw_all(items: &[Box<dyn Drawable>]) {
    for item in items {
        item.draw();
    }
}

fn main() {
    let c = Circle { radius: 5.0 };
    let s = Square { side: 3.0 };

    // Borrowed
    draw_ref(&c);
    draw_ref(&s);

    // Owned
    let items: Vec<Box<dyn Drawable>> = vec![
        Box::new(Circle { radius: 10.0 }),
        Box::new(Square { side: 7.0 }),
    ];
    draw_all(&items);

    // Move ownership
    draw_owned(Box::new(Circle { radius: 2.0 }));
}
```

**`&dyn Trait`**: borrowed, no allocation, bound by a lifetime. Use when you're just calling methods on something temporarily.

**`Box<dyn Trait>`**: owned, heap-allocated, no lifetime concerns. Use when you need to store trait objects in collections or return them from functions.

**`Arc<dyn Trait>`**: shared ownership, thread-safe. Use when multiple threads need access.

## Returning `dyn Trait` from Functions

You can't return `dyn Trait` by value (the compiler doesn't know the size), but you can return it boxed:

```rust
trait Animal {
    fn speak(&self) -> &str;
}

struct Dog;
struct Cat;

impl Animal for Dog {
    fn speak(&self) -> &str { "Woof" }
}

impl Animal for Cat {
    fn speak(&self) -> &str { "Meow" }
}

// This is where dyn shines over impl Trait —
// we can return DIFFERENT types based on runtime logic
fn create_animal(preference: &str) -> Box<dyn Animal> {
    match preference {
        "dog" => Box::new(Dog),
        "cat" => Box::new(Cat),
        _ => Box::new(Dog), // default
    }
}

fn main() {
    let animal = create_animal("cat");
    println!("{}", animal.speak());

    let animal2 = create_animal("dog");
    println!("{}", animal2.speak());
}
```

Remember from Lesson 1 — `impl Trait` in return position only works when you return a single concrete type. `Box<dyn Trait>` works when different branches return different types. This is the key distinction.

## The Cost in Practice

I ran benchmarks on a real project — a rule engine evaluating ~100 rules per request. Static dispatch version vs `dyn` version:

- Static dispatch (monomorphized): ~45ns per rule evaluation
- Dynamic dispatch (`dyn`): ~52ns per rule evaluation

That's about 15% slower per call. For my use case (handling HTTP requests at ~1000 RPS), the difference was irrelevant — total time per request went from 4.5µs to 5.2µs for rule evaluation. Lost in the noise of network I/O.

But the `dyn` version let me load rules from config at runtime, add new rule types without recompiling, and store heterogeneous rules in a single `Vec`. Worth the tradeoff ten times over.

Here's a simplified version of that pattern:

```rust
trait Rule {
    fn name(&self) -> &str;
    fn evaluate(&self, value: i64) -> bool;
}

struct GreaterThan { threshold: i64, label: String }
struct LessThan { threshold: i64, label: String }
struct Between { low: i64, high: i64, label: String }

impl Rule for GreaterThan {
    fn name(&self) -> &str { &self.label }
    fn evaluate(&self, value: i64) -> bool { value > self.threshold }
}

impl Rule for LessThan {
    fn name(&self) -> &str { &self.label }
    fn evaluate(&self, value: i64) -> bool { value < self.threshold }
}

impl Rule for Between {
    fn name(&self) -> &str { &self.label }
    fn evaluate(&self, value: i64) -> bool { value >= self.low && value <= self.high }
}

struct RuleEngine {
    rules: Vec<Box<dyn Rule>>,
}

impl RuleEngine {
    fn new() -> Self {
        RuleEngine { rules: Vec::new() }
    }

    fn add_rule(&mut self, rule: Box<dyn Rule>) {
        self.rules.push(rule);
    }

    fn evaluate(&self, value: i64) -> Vec<&str> {
        self.rules
            .iter()
            .filter(|r| r.evaluate(value))
            .map(|r| r.name())
            .collect()
    }
}

fn main() {
    let mut engine = RuleEngine::new();
    engine.add_rule(Box::new(GreaterThan {
        threshold: 100,
        label: String::from("high_value"),
    }));
    engine.add_rule(Box::new(LessThan {
        threshold: 0,
        label: String::from("negative"),
    }));
    engine.add_rule(Box::new(Between {
        low: 50,
        high: 150,
        label: String::from("mid_range"),
    }));

    let value = 120;
    let matching = engine.evaluate(value);
    println!("Value {} matches: {:?}", value, matching);
    // "Value 120 matches: ["high_value", "mid_range"]"
}
```

## Static vs Dynamic: The Decision

| Factor | `impl Trait` / Generics | `dyn Trait` |
|--------|------------------------|-------------|
| Dispatch | Compile-time (fast) | Runtime (vtable lookup) |
| Inlining | Yes | No |
| Binary size | Larger (monomorphization) | Smaller |
| Heterogeneous collections | No | Yes |
| Runtime type selection | No | Yes |
| Memory layout | Stack or inline | Heap (via Box) |

My rule: start with generics. Reach for `dyn` when you need heterogeneous collections, runtime-determined types, or want to reduce binary size (monomorphization bloat is real in large codebases).

## Key Takeaways

`dyn Trait` enables runtime polymorphism through vtable-based dispatch. Use it behind pointers (`&dyn`, `Box<dyn>`, `Arc<dyn>`). It costs one indirection per call and prevents inlining. Use it when you need heterogeneous collections or runtime type selection. Profile before optimizing — the cost is usually negligible compared to I/O.

Next — object safety, which determines *which* traits can be used as `dyn Trait` in the first place.
