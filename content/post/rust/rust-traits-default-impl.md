---
title: "Lesson 2: Default Implementations and Selective Overrides — Don't repeat yourself"
author: Atharva Pandey
series: "Rust Traits & Generics Masterclass"
lesson: 2
keywords: [Rust, rust, traits, generics, default implementation, trait methods, override]
tags: [Rust tutorial, rust, traits, generics]
date: '2024-06-12T14:45:00.000Z'
---

Here's something that bugged me when I first started with traits: I had six different types all implementing the same trait, and five of them had *identical* method bodies. I was copying the same three lines into five `impl` blocks like a human xerox machine. There had to be a better way.

There is. Default implementations.

## The Basics

A trait can provide a default body for any of its methods. Implementors can then choose to override it — or just accept the default:

```rust
trait Summary {
    fn summarize_author(&self) -> String;

    fn summarize(&self) -> String {
        format!("(Read more from {}...)", self.summarize_author())
    }
}

struct Article {
    author: String,
    title: String,
}

struct Tweet {
    username: String,
    body: String,
}

impl Summary for Article {
    fn summarize_author(&self) -> String {
        self.author.clone()
    }
    // summarize() uses the default
}

impl Summary for Tweet {
    fn summarize_author(&self) -> String {
        format!("@{}", self.username)
    }

    // Override the default
    fn summarize(&self) -> String {
        format!("{}: {}", self.summarize_author(), self.body)
    }
}

fn main() {
    let article = Article {
        author: String::from("Atharva"),
        title: String::from("Traits Rule"),
    };

    let tweet = Tweet {
        username: String::from("rustlang"),
        body: String::from("New release!"),
    };

    println!("{}", article.summarize()); // "(Read more from Atharva...)"
    println!("{}", tweet.summarize());   // "@rustlang: New release!"
}
```

`Article` gets the default `summarize()`. `Tweet` overrides it. Both must provide `summarize_author()` because it has no default body.

## Default Methods Can Call Other Trait Methods

This is the part that makes defaults genuinely powerful — not just convenient. A default method can call other methods defined in the same trait, even ones without defaults:

```rust
trait Report {
    fn title(&self) -> &str;
    fn body(&self) -> &str;
    fn author(&self) -> &str;

    fn header(&self) -> String {
        format!("=== {} ===\nBy: {}", self.title(), self.author())
    }

    fn full_report(&self) -> String {
        format!("{}\n\n{}", self.header(), self.body())
    }
}

struct BugReport {
    name: String,
    description: String,
    reporter: String,
}

impl Report for BugReport {
    fn title(&self) -> &str {
        &self.name
    }

    fn body(&self) -> &str {
        &self.description
    }

    fn author(&self) -> &str {
        &self.reporter
    }

    // header() and full_report() come for free
}

fn main() {
    let bug = BugReport {
        name: String::from("Segfault on login"),
        description: String::from("Users hitting a segfault when logging in with SSO."),
        reporter: String::from("Atharva"),
    };

    println!("{}", bug.full_report());
}
```

You implement the leaf methods (`title`, `body`, `author`), and the composed methods (`header`, `full_report`) build themselves from those pieces. This is the template method pattern, but enforced at compile time.

## Selective Overrides

The real power shows up when you override *some* defaults but not others. Maybe most of your types are fine with the standard `header()` but one type needs custom formatting:

```rust
trait Report {
    fn title(&self) -> &str;
    fn body(&self) -> &str;
    fn author(&self) -> &str;

    fn header(&self) -> String {
        format!("=== {} ===\nBy: {}", self.title(), self.author())
    }

    fn full_report(&self) -> String {
        format!("{}\n\n{}", self.header(), self.body())
    }
}

struct SecurityReport {
    name: String,
    details: String,
    analyst: String,
    severity: u8,
}

impl Report for SecurityReport {
    fn title(&self) -> &str {
        &self.name
    }

    fn body(&self) -> &str {
        &self.details
    }

    fn author(&self) -> &str {
        &self.analyst
    }

    // Override just the header
    fn header(&self) -> String {
        format!(
            "🚨 SEVERITY {} 🚨\n=== {} ===\nAnalyst: {}",
            self.severity,
            self.title(),
            self.author()
        )
    }

    // full_report() still uses the default, which now calls OUR header()
}

fn main() {
    let sec = SecurityReport {
        name: String::from("SQL Injection in /api/users"),
        details: String::from("Unsanitized input passed directly to query builder."),
        analyst: String::from("Atharva"),
        severity: 9,
    };

    println!("{}", sec.full_report());
}
```

Notice something subtle: `full_report()` is still the default implementation, but it calls `self.header()`, which *we overrode*. The default method dispatches through `self`, so overrides compose naturally. This is dynamic dispatch within static dispatch — the compiler resolves which `header()` to call based on the concrete type.

## The Gotcha: You Can't Call the Default After Overriding

One thing Rust *doesn't* let you do is call the default implementation from within your override. There's no `super.header()` equivalent:

```rust
// THIS DOES NOT WORK
impl Report for SecurityReport {
    fn header(&self) -> String {
        let base = Report::header(self); // Nope — this calls OUR override, infinite recursion
        format!("SECURITY: {}", base)
    }
}
```

If you need to share logic between the default and an override, extract it into a free function or a helper method:

```rust
trait Report {
    fn title(&self) -> &str;
    fn author(&self) -> &str;
    fn body(&self) -> &str;

    fn make_standard_header(title: &str, author: &str) -> String {
        format!("=== {} ===\nBy: {}", title, author)
    }

    fn header(&self) -> String {
        Self::make_standard_header(self.title(), self.author())
    }

    fn full_report(&self) -> String {
        format!("{}\n\n{}", self.header(), self.body())
    }
}

struct SecurityReport {
    name: String,
    details: String,
    analyst: String,
    severity: u8,
}

impl Report for SecurityReport {
    fn title(&self) -> &str { &self.name }
    fn author(&self) -> &str { &self.analyst }
    fn body(&self) -> &str { &self.details }

    fn header(&self) -> String {
        let base = Self::make_standard_header(self.title(), self.author());
        format!("SEVERITY {}\n{}", self.severity, base)
    }
}

fn main() {
    let r = SecurityReport {
        name: String::from("XSS in comments"),
        details: String::from("Script tags not sanitized."),
        analyst: String::from("Atharva"),
        severity: 7,
    };
    println!("{}", r.full_report());
}
```

Not as elegant as `super` calls, but it's explicit and avoids the ambiguity that plagues inheritance-heavy languages.

## Real-World Pattern: Builder-Style Defaults

I use this pattern constantly for configuration-style traits where most implementors want sensible defaults:

```rust
trait ServerConfig {
    fn host(&self) -> &str {
        "0.0.0.0"
    }

    fn port(&self) -> u16 {
        8080
    }

    fn max_connections(&self) -> usize {
        1000
    }

    fn read_timeout_ms(&self) -> u64 {
        30_000
    }

    fn address(&self) -> String {
        format!("{}:{}", self.host(), self.port())
    }
}

struct DevConfig;

impl ServerConfig for DevConfig {
    // All defaults are fine for dev
}

struct ProdConfig {
    custom_port: u16,
}

impl ServerConfig for ProdConfig {
    fn host(&self) -> &str {
        "10.0.0.1"
    }

    fn port(&self) -> u16 {
        self.custom_port
    }

    fn max_connections(&self) -> usize {
        50_000
    }
}

fn main() {
    let dev = DevConfig;
    let prod = ProdConfig { custom_port: 443 };

    println!("Dev: {} (max {})", dev.address(), dev.max_connections());
    println!("Prod: {} (max {})", prod.address(), prod.max_connections());
}
```

`DevConfig` implements the trait with zero methods — every single one falls back to the default. `ProdConfig` overrides the three it cares about. This is clean, readable, and impossible to get wrong at compile time.

## When Defaults Are a Bad Idea

Not every trait should have defaults. Here are my rules:

**Good candidates for defaults:**
- Composed methods that build on other trait methods
- Configuration values with sensible fallbacks
- Display/formatting that works for 80% of cases

**Bad candidates for defaults:**
- Core behavior that differs for every implementor — just make it required
- Methods where the "obvious" default is actually wrong for most types
- Anything where a forgotten override would silently produce incorrect results

The worst kind of default is one that compiles, runs, and produces plausible-but-wrong output. If you find yourself writing a default that returns `0` or `""` just to have *something* there — stop. Make it required instead. Let the compiler catch missing implementations rather than hoping developers remember to override.

## Trait Methods with `self` vs `&self` vs `&mut self`

Quick but important — default methods follow the same receiver rules as any method:

```rust
trait Consumable {
    fn consume(self) -> String where Self: Sized {
        String::from("consumed")
    }
}

trait Readable {
    fn read(&self) -> String {
        String::from("default read")
    }
}

trait Writable {
    fn write(&mut self, data: &str) {
        println!("Writing: {}", data);
    }
}

struct Buffer {
    data: String,
}

impl Consumable for Buffer {
    fn consume(self) -> String {
        self.data // move the data out
    }
}

impl Readable for Buffer {
    fn read(&self) -> String {
        self.data.clone()
    }
}

impl Writable for Buffer {
    fn write(&mut self, data: &str) {
        self.data.push_str(data);
    }
}

fn main() {
    let mut buf = Buffer { data: String::from("hello") };
    buf.write(" world");
    println!("{}", buf.read());
    println!("{}", buf.consume()); // buf is moved here
    // buf is no longer usable
}
```

The receiver type matters for ownership semantics — `self` consumes the value, `&self` borrows immutably, `&mut self` borrows mutably. Your defaults should use the least powerful receiver that gets the job done.

## Key Takeaways

Default implementations eliminate boilerplate by letting traits provide method bodies that implementors can accept or override. They can call other trait methods, enabling composition. You can't call the "super" default from an override — extract shared logic into helper functions instead.

The pattern to internalize: required methods define *what's unique* per type, default methods define *what's shared* across types. Get that split right and your trait designs will be tight.

Next — trait bounds, where we start constraining generic types and things get really interesting.
