---
title: "Lesson 10: Orphan Rules and the Newtype Workaround — Coherence in practice"
author: Atharva Pandey
series: "Rust Traits & Generics Masterclass"
lesson: 10
keywords: [Rust, rust, traits, generics, orphan rules, newtype pattern, coherence, trait implementation]
tags: [Rust tutorial, rust, traits, generics]
date: '2024-06-30T17:55:00.000Z'
---

Picture this: you're using two crates — `serde` and some `db_client` crate. You want to implement `serde::Serialize` for `db_client::Row`. Sounds reasonable. You write the `impl`, and the compiler slaps you: "only traits defined in the current crate can be implemented for types defined outside of the current crate." Welcome to the orphan rules.

My first reaction was frustration. My second reaction, after dealing with diamond dependency problems in C++ for years, was "oh, this is actually protecting me."

## The Rule

Rust's orphan rule (also called the coherence rule) states:

**You can only implement a trait for a type if at least one of them (the trait or the type) is defined in your crate.**

```rust
// In YOUR crate:

// ✅ Your trait, foreign type
trait MyTrait {
    fn do_thing(&self);
}

impl MyTrait for String {  // String is from std, MyTrait is yours — OK
    fn do_thing(&self) {
        println!("String: {}", self);
    }
}

// ✅ Foreign trait, your type
struct MyType(i32);

impl std::fmt::Display for MyType {  // Display is from std, MyType is yours — OK
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "MyType({})", self.0)
    }
}

// ❌ Foreign trait, foreign type
// impl std::fmt::Display for Vec<i32> { ... }  // NEITHER is yours — ERROR

fn main() {
    let s = String::from("hello");
    s.do_thing();

    let m = MyType(42);
    println!("{}", m);
}
```

## Why This Rule Exists

Imagine a world without it. Crate A implements `Display` for `Vec<i32>` one way. Crate B implements `Display` for `Vec<i32>` a different way. Your program depends on both. Which implementation wins? There's no good answer.

The orphan rule prevents this entirely. At most one crate can provide any given `(trait, type)` implementation. The compiler can always unambiguously resolve which `impl` to use. This is *coherence* — the guarantee that trait resolution is deterministic.

## The Newtype Pattern

So what do you do when you genuinely need to implement a foreign trait for a foreign type? You wrap it:

```rust
use std::fmt;

struct Wrapper(Vec<String>);

impl fmt::Display for Wrapper {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "[{}]", self.0.join(", "))
    }
}

fn main() {
    let w = Wrapper(vec![
        String::from("one"),
        String::from("two"),
        String::from("three"),
    ]);
    println!("{}", w); // "[one, two, three]"
}
```

`Wrapper` is *your* type. You can implement any trait on it. The inner `Vec<String>` is still accessible through `.0`.

The downside: you lose all the methods that `Vec<String>` has. You have to either:
1. Delegate manually
2. Implement `Deref`
3. Expose the inner value

## Newtype with `Deref`

The `Deref` approach gives you transparent access to the inner type's methods:

```rust
use std::fmt;
use std::ops::Deref;

struct EmailList(Vec<String>);

impl EmailList {
    fn new() -> Self {
        EmailList(Vec::new())
    }

    fn add(&mut self, email: String) {
        self.0.push(email);
    }
}

impl Deref for EmailList {
    type Target = Vec<String>;

    fn deref(&self) -> &Vec<String> {
        &self.0
    }
}

impl fmt::Display for EmailList {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        for (i, email) in self.iter().enumerate() {
            if i > 0 {
                write!(f, ", ")?;
            }
            write!(f, "{}", email)?;
        }
        Ok(())
    }
}

fn main() {
    let mut emails = EmailList::new();
    emails.add(String::from("alice@example.com"));
    emails.add(String::from("bob@example.com"));

    // Display — our custom impl
    println!("{}", emails);

    // Vec methods work through Deref
    println!("Count: {}", emails.len());
    println!("First: {:?}", emails.first());
    println!("Contains alice: {}", emails.contains(&String::from("alice@example.com")));
}
```

A word of caution: `Deref` is meant for smart pointer types. Using it just to get method delegation is controversial in the Rust community. For simple cases it's fine, but don't build deep `Deref` chains.

## Real-World Example: Validated Types

The newtype pattern isn't just a workaround — it's a genuinely useful design pattern for creating type-safe wrappers with validation:

```rust
use std::fmt;

#[derive(Debug, Clone)]
struct Email(String);

#[derive(Debug)]
struct EmailError(String);

impl fmt::Display for EmailError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Invalid email: {}", self.0)
    }
}

impl Email {
    fn new(raw: &str) -> Result<Self, EmailError> {
        if raw.contains('@') && raw.contains('.') && raw.len() > 5 {
            Ok(Email(raw.to_string()))
        } else {
            Err(EmailError(raw.to_string()))
        }
    }

    fn domain(&self) -> &str {
        self.0.split('@').nth(1).unwrap_or("")
    }

    fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for Email {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

// Now we can implement any trait we want on Email
impl PartialEq for Email {
    fn eq(&self, other: &Self) -> bool {
        self.0.to_lowercase() == other.0.to_lowercase()
    }
}

#[derive(Debug, Clone)]
struct UserId(u64);

impl UserId {
    fn new(id: u64) -> Self {
        UserId(id)
    }
}

impl fmt::Display for UserId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "user:{}", self.0)
    }
}

fn send_notification(user: &UserId, email: &Email, message: &str) {
    println!("Sending to {} at {}: {}", user, email, message);
}

fn main() {
    let email = Email::new("atharva@example.com").unwrap();
    let user_id = UserId::new(42);

    println!("Domain: {}", email.domain());
    send_notification(&user_id, &email, "Welcome!");

    // Type safety — can't mix up Email and UserId
    // send_notification(&email, &user_id, "oops"); // ERROR: wrong types

    // Case-insensitive comparison
    let email2 = Email::new("ATHARVA@example.com").unwrap();
    println!("Equal? {}", email == email2); // true
}
```

The newtype pattern gives you:
- Foreign trait implementation (orphan rule workaround)
- Type safety (can't confuse `Email` with `String`)
- Validation (the constructor enforces invariants)
- Custom trait implementations

## The Orphan Rule's Edge Cases

The rule is actually slightly more nuanced. You can implement a foreign trait for a foreign type if your local type is involved as a type parameter:

```rust
use std::fmt;

struct Meters(f64);

// This works — Vec<Meters> involves YOUR type (Meters)
impl fmt::Display for Vec<Meters> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let total: f64 = self.iter().map(|m| m.0).sum();
        write!(f, "[{} measurements, total: {:.1}m]", self.len(), total)
    }
}

fn main() {
    let measurements = vec![Meters(1.5), Meters(2.3), Meters(0.8)];
    println!("{}", measurements);
}
```

The precise rule is called the "covering" rule: your local type must appear before any type parameters in the foreign type. `Vec<Meters>` is fine because `Meters` (your type) is in the type parameter. `Vec<Vec<Meters>>` would also work. But `Vec<String>` wouldn't because no local type is involved.

## Pattern: Newtype for Serialization

A common real-world use case — custom serialization for a third-party type:

```rust
use std::fmt;
use std::collections::HashMap;

// Imagine this comes from a third-party crate
type ExternalConfig = HashMap<String, Vec<String>>;

// Newtype wrapper for custom Display
struct PrettyConfig(ExternalConfig);

impl PrettyConfig {
    fn new(config: ExternalConfig) -> Self {
        PrettyConfig(config)
    }

    fn inner(&self) -> &ExternalConfig {
        &self.0
    }

    fn into_inner(self) -> ExternalConfig {
        self.0
    }
}

impl fmt::Display for PrettyConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        for (key, values) in &self.0 {
            writeln!(f, "{}:", key)?;
            for val in values {
                writeln!(f, "  - {}", val)?;
            }
        }
        Ok(())
    }
}

impl From<ExternalConfig> for PrettyConfig {
    fn from(config: ExternalConfig) -> Self {
        PrettyConfig(config)
    }
}

impl From<PrettyConfig> for ExternalConfig {
    fn from(wrapper: PrettyConfig) -> Self {
        wrapper.0
    }
}

fn main() {
    let mut config = HashMap::new();
    config.insert(
        String::from("allowed_origins"),
        vec![String::from("localhost"), String::from("example.com")],
    );
    config.insert(
        String::from("features"),
        vec![String::from("dark_mode"), String::from("beta_api")],
    );

    let pretty = PrettyConfig::new(config);
    println!("{}", pretty);

    // Convert back when you need the original type
    let raw: ExternalConfig = pretty.into_inner();
    println!("Keys: {:?}", raw.keys().collect::<Vec<_>>());
}
```

## Zero-Cost Abstraction

Here's the beautiful part — newtypes are genuinely zero-cost. The compiler sees through the wrapper:

```rust
struct Meters(f64);
struct Seconds(f64);

impl Meters {
    fn new(val: f64) -> Self { Meters(val) }
    fn value(&self) -> f64 { self.0 }
}

impl Seconds {
    fn new(val: f64) -> Self { Seconds(val) }
    fn value(&self) -> f64 { self.0 }
}

fn speed(distance: Meters, time: Seconds) -> f64 {
    distance.value() / time.value()
}

fn main() {
    let d = Meters::new(100.0);
    let t = Seconds::new(9.58);
    println!("{:.2} m/s", speed(d, t));

    // Can't accidentally swap them:
    // speed(t, d); // ERROR: expected Meters, found Seconds
}
```

In the compiled binary, `Meters(f64)` has exactly the same representation as `f64`. No wrapper overhead. No indirection. The type safety is purely a compile-time construct.

## Key Takeaways

The orphan rule prevents ambiguous trait implementations by requiring at least one of (trait, type) to be defined in your crate. The newtype pattern works around this by wrapping the foreign type, giving you full trait implementation freedom. Newtypes are zero-cost — same memory layout as the inner type.

Beyond the workaround, newtypes are a first-class design pattern: validated types, domain-specific wrappers, and type-safe APIs all benefit from them.

Next — the essential standard library traits that every Rust developer should know cold.
