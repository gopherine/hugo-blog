---
title: "Lesson 1: Trait Fundamentals — Defining shared behavior"
author: Atharva Pandey
series: "Rust Traits & Generics Masterclass"
lesson: 1
keywords: [Rust, rust, traits, generics, trait definition, shared behavior, impl trait]
tags: [Rust tutorial, rust, traits, generics]
date: '2024-06-10T09:22:00.000Z'
---

I spent my first month in Rust writing `impl` blocks that looked suspiciously like Java interfaces. Copy-paste, copy-paste, tweak one method, ship it. Then I hit a wall — a refactor where I needed to swap out a storage backend, and every single call site had hardcoded the concrete type. That's when traits stopped being "a feature I should learn" and became "the thing saving me from rewriting 4,000 lines."

Traits are Rust's answer to shared behavior. Not inheritance. Not duck typing. Something more precise.

## What a Trait Actually Is

A trait defines a contract: "if you implement me, you promise these methods exist with these signatures." That's it. No data. No state. Just a set of capabilities a type agrees to provide.

```rust
trait Summary {
    fn summarize(&self) -> String;
}
```

Any type that implements `Summary` *must* provide a `summarize` method returning a `String`. The compiler enforces this — not tests, not documentation, not code review. The compiler.

This matters more than it sounds. In dynamically typed languages, you discover missing method implementations at runtime. In Go, the implicit interface satisfaction is nice but can lead to accidental implementations. Rust makes you opt in explicitly.

## Implementing a Trait

Here's the basic shape:

```rust
trait Summary {
    fn summarize(&self) -> String;
}

struct Article {
    title: String,
    author: String,
    content: String,
}

struct Tweet {
    username: String,
    body: String,
    reply: bool,
}

impl Summary for Article {
    fn summarize(&self) -> String {
        format!("{} by {} — {}...", self.title, self.author, &self.content[..50.min(self.content.len())])
    }
}

impl Summary for Tweet {
    fn summarize(&self) -> String {
        format!("@{}: {}", self.username, self.body)
    }
}

fn main() {
    let article = Article {
        title: String::from("Rust in Production"),
        author: String::from("Atharva"),
        content: String::from("After running Rust in production for two years, here's what I learned about the borrow checker..."),
    };

    let tweet = Tweet {
        username: String::from("rustlang"),
        body: String::from("Rust 1.79 is out!"),
        reply: false,
    };

    println!("{}", article.summarize());
    println!("{}", tweet.summarize());
}
```

Two completely different types. Same interface. The `impl Trait for Type` syntax is explicit — you're telling the compiler "I am deliberately satisfying this contract."

## Traits as Function Parameters

This is where traits start earning their keep. Instead of writing a function that takes a concrete type, you can accept anything implementing a trait:

```rust
trait Summary {
    fn summarize(&self) -> String;
}

struct Article {
    title: String,
}

impl Summary for Article {
    fn summarize(&self) -> String {
        format!("Article: {}", self.title)
    }
}

fn notify(item: &impl Summary) {
    println!("Breaking: {}", item.summarize());
}

fn main() {
    let a = Article { title: String::from("Big News") };
    notify(&a);
}
```

The `&impl Summary` syntax says "I don't care what type you give me, as long as it implements `Summary`." This is *syntactic sugar* for a trait bound, which I'll cover properly in Lesson 3. For now, just know that this is the idiomatic way to write generic-ish functions without committing to a specific type.

## Traits as Return Types

You can also return `impl Trait` from functions:

```rust
trait Summary {
    fn summarize(&self) -> String;
}

struct Tweet {
    username: String,
    body: String,
}

impl Summary for Tweet {
    fn summarize(&self) -> String {
        format!("@{}: {}", self.username, self.body)
    }
}

fn create_default_summary() -> impl Summary {
    Tweet {
        username: String::from("system"),
        body: String::from("Nothing to report."),
    }
}

fn main() {
    let s = create_default_summary();
    println!("{}", s.summarize());
}
```

There's a catch though — `impl Trait` in return position means "I'm returning exactly one concrete type, but I'm not telling you which one." You *cannot* conditionally return different types. This won't compile:

```rust
// THIS DOES NOT COMPILE
fn make_summary(is_tweet: bool) -> impl Summary {
    if is_tweet {
        Tweet { /* ... */ }
    } else {
        Article { /* ... */ } // ERROR: expected Tweet, found Article
    }
}
```

For that, you need `dyn Trait` — which we'll tackle in Lesson 7.

## Multiple Traits on a Single Type

A type can implement as many traits as you want. There's no limit, no special syntax for "multi-implementation."

```rust
use std::fmt;

trait Summary {
    fn summarize(&self) -> String;
}

trait Publishable {
    fn publish(&self);
}

struct Article {
    title: String,
    author: String,
}

impl Summary for Article {
    fn summarize(&self) -> String {
        format!("{} by {}", self.title, self.author)
    }
}

impl Publishable for Article {
    fn publish(&self) {
        println!("Publishing: {}", self.title);
    }
}

impl fmt::Display for Article {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "[{}] {}", self.author, self.title)
    }
}

fn main() {
    let a = Article {
        title: String::from("Traits Deep Dive"),
        author: String::from("Atharva"),
    };
    println!("{}", a.summarize());
    a.publish();
    println!("{}", a); // uses Display
}
```

This is composition over inheritance, and it's one of the things that makes Rust's type system so pleasant to work with once you internalize it.

## The Problem: Method Name Collisions

What happens when two traits define a method with the same name?

```rust
trait Pilot {
    fn fly(&self);
}

trait Wizard {
    fn fly(&self);
}

struct Human;

impl Pilot for Human {
    fn fly(&self) {
        println!("This is your captain speaking.");
    }
}

impl Wizard for Human {
    fn fly(&self) {
        println!("*levitates*");
    }
}

impl Human {
    fn fly(&self) {
        println!("*flaps arms furiously*");
    }
}

fn main() {
    let h = Human;
    h.fly(); // calls Human::fly — the inherent method

    // To call trait methods explicitly:
    Pilot::fly(&h);
    Wizard::fly(&h);
}
```

Rust resolves ambiguity with *fully qualified syntax*. The inherent method (defined directly on the type) takes priority when you just call `h.fly()`. To invoke a specific trait's version, you use `Trait::method(&instance)`.

For associated functions (no `self`), the syntax is even more explicit:

```rust
trait Animal {
    fn name() -> String;
}

struct Dog;

impl Animal for Dog {
    fn name() -> String {
        String::from("Dog")
    }
}

impl Dog {
    fn name() -> String {
        String::from("Good Boy")
    }
}

fn main() {
    println!("{}", Dog::name());            // "Good Boy" — inherent
    println!("{}", <Dog as Animal>::name()); // "Dog" — trait version
}
```

The `<Type as Trait>::method()` syntax is the fully qualified form. You won't need it often, but when you do, nothing else will work.

## When to Reach for Traits

A simple heuristic I use:

- **Two or more types need the same method signature?** Trait.
- **You want to write a function that works with multiple types?** Trait parameter.
- **You need to swap implementations (testing, different backends)?** Trait.
- **You just have one type doing one thing?** Skip the trait. Don't over-abstract.

That last point is important. I've seen codebases where every struct has a matching trait with exactly one implementor. That's Java brain leaking into Rust. Traits should emerge from actual polymorphism needs, not from habit.

## The Key Takeaways

Traits define shared behavior through explicit contracts. Types opt into them with `impl Trait for Type`. You can use traits in function parameters (`&impl Trait`), return positions (`-> impl Trait`), and combine multiple traits on a single type freely.

The mental model: traits are capabilities. A type that implements `Display` is printable. A type that implements `Iterator` is iterable. A type that implements `Summary` is summarizable. You're declaring what a type *can do*, not what it *is*.

Next up — default implementations, where traits start pulling even more weight by providing behavior you don't have to write yourself.
