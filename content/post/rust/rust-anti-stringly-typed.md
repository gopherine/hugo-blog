---
title: "Lesson 3: Stringly Typed APIs — Use enums, not strings"
author: Atharva Pandey
series: "Rust Anti-Patterns & Code Smells"
lesson: 3
keywords: [Rust, rust, anti-patterns, code quality, stringly typed, enums, type safety, newtype]
tags: [Rust tutorial, rust, anti-patterns, code-quality]
date: '2025-04-09T08:47:00.000Z'
---

I inherited a Rust codebase once where the entire state machine was driven by string comparisons. The order status could be `"pending"`, `"processing"`, `"shipped"`, `"delivered"`, or `"cancelled"`. Except sometimes it was `"Pending"` with a capital P. And there was one code path that set it to `"canceled"` — one L, American spelling. And another that used `"CANCELLED"`. The bug lived in production for weeks because nobody could figure out why some orders were getting stuck in a phantom state that didn't match any of the `if status == "cancelled"` checks scattered across thirty files.

This is what happens when you use strings where you should use enums. You're writing Rust — a language with one of the best type systems ever designed — and you're encoding business logic in `String`. That's like buying a Ferrari and pedaling it with your feet.

## The Smell

Stringly typed code comes in a few flavors. Here's the greatest hits:

```rust
// Status as a string
fn process_order(order: &mut Order) {
    match order.status.as_str() {
        "pending" => {
            validate_payment(order);
            order.status = "processing".to_string();
        }
        "processing" => {
            ship_order(order);
            order.status = "shipped".to_string();
        }
        "shipped" => {
            order.status = "delivered".to_string();
        }
        _ => {
            log::warn!("Unknown status: {}", order.status);
        }
    }
}

// Configuration keys as strings
fn get_setting(config: &HashMap<String, String>, key: &str) -> String {
    config.get(key).cloned().unwrap_or_default()
}

let timeout = get_setting(&config, "timout"); // typo — returns empty string silently

// Event types as strings
fn handle_event(event_type: &str, payload: &str) {
    if event_type == "user_signup" {
        // ...
    } else if event_type == "user_signUp" { // wait, is it this one?
        // ...
    }
}
```

Every one of these is a bug waiting to happen. And the worst part? The compiler can't help you. It sees `String`, it's happy. It can't tell you that `"timout"` isn't a valid config key or that `"canceled"` with one L doesn't match your `"cancelled"` check.

## Why It's Actually Bad

**No exhaustiveness checking.** When you match on an enum, the compiler forces you to handle every variant. When you match on a string, the `_` arm silently swallows anything unexpected. New status added by a teammate? The compiler won't tell you. You'll find out from a customer complaint.

**Typos are silent bugs.** `"recieved"` vs `"received"`. `"cancelled"` vs `"canceled"`. `"colour"` vs `"color"`. These are invisible in code review and immune to any type checking. They pass every test that doesn't specifically check for them.

**No IDE support.** You can't autocomplete a string. You can't "go to definition" on `"pending"`. You can't refactor-rename across a codebase. With enums, your IDE knows every variant and can navigate, complete, and refactor them.

**Stringly typed functions accept garbage.** A function that takes `status: &str` happily accepts `"banana"`. A function that takes `status: OrderStatus` only accepts valid statuses. This isn't pedantic — it's the difference between catching bugs at compile time and catching them in production.

**Performance.** String comparisons are O(n) in string length and involve memory indirection. Enum comparisons are a single integer comparison. This rarely matters for correctness, but it's insult on top of injury.

## The Fix

### Step 1: Define an enum for every set of known values

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
enum OrderStatus {
    Pending,
    Processing,
    Shipped,
    Delivered,
    Cancelled,
}
```

Now the compiler knows every possible status. You can't typo a variant — `OrderStatus::Canceld` is a compile error, not a runtime mystery.

### Step 2: Implement Display and FromStr for serialization boundaries

You still need to convert to/from strings at the edges — reading from a database, parsing JSON, displaying to users. Do it once, in one place:

```rust
use std::fmt;
use std::str::FromStr;

impl fmt::Display for OrderStatus {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            OrderStatus::Pending => write!(f, "pending"),
            OrderStatus::Processing => write!(f, "processing"),
            OrderStatus::Shipped => write!(f, "shipped"),
            OrderStatus::Delivered => write!(f, "delivered"),
            OrderStatus::Cancelled => write!(f, "cancelled"),
        }
    }
}

impl FromStr for OrderStatus {
    type Err = ParseStatusError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.to_lowercase().as_str() {
            "pending" => Ok(OrderStatus::Pending),
            "processing" => Ok(OrderStatus::Processing),
            "shipped" => Ok(OrderStatus::Shipped),
            "delivered" => Ok(OrderStatus::Delivered),
            "cancelled" | "canceled" => Ok(OrderStatus::Cancelled), // handle both spellings HERE
            _ => Err(ParseStatusError(s.to_string())),
        }
    }
}

#[derive(Debug)]
struct ParseStatusError(String);

impl fmt::Display for ParseStatusError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "invalid order status: '{}'", self.0)
    }
}

impl std::error::Error for ParseStatusError {}
```

Now all the messy string normalization lives in one function. The rest of your codebase works with clean, type-safe enum values.

### Step 3: Use serde for automatic serialization

If you're working with JSON or other serialization formats, derive `Serialize` and `Deserialize`:

```rust
use serde::{Serialize, Deserialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum OrderStatus {
    Pending,
    Processing,
    Shipped,
    Delivered,
    Cancelled,
}
```

That's it. `serde` handles the string conversion. `"pending"` in JSON becomes `OrderStatus::Pending` in Rust. An unrecognized string returns a deserialization error — no silent failures.

### Step 4: Use newtypes for stringly-typed identifiers

Sometimes the value genuinely is a string — a user ID, an email, a URL. But you shouldn't use bare `String` for these either, because the compiler can't distinguish between them:

```rust
// Bad: all of these are just String
fn create_order(user_id: String, product_id: String, coupon_code: String) {
    // easy to swap arguments and the compiler won't notice
}

create_order(
    coupon_code,   // oops, this is the user_id parameter
    user_id,       // this is product_id
    product_id,    // this is coupon_code
);
```

Newtypes fix this:

```rust
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct UserId(String);

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct ProductId(String);

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct CouponCode(String);

fn create_order(user_id: UserId, product_id: ProductId, coupon: CouponCode) {
    // ...
}

// Now this is a compile error — types don't match
create_order(coupon, user_id, product_id);
```

You can add validation in the constructor too:

```rust
impl UserId {
    fn new(id: impl Into<String>) -> Result<Self, ValidationError> {
        let id = id.into();
        if id.is_empty() {
            return Err(ValidationError::empty("user_id"));
        }
        if id.len() > 64 {
            return Err(ValidationError::too_long("user_id", 64));
        }
        Ok(UserId(id))
    }

    fn as_str(&self) -> &str {
        &self.0
    }
}
```

Now invalid user IDs can't even be constructed. The type system guarantees validity.

### Step 5: Use strum for enum-string boilerplate

Implementing `Display`, `FromStr`, and iteration for every enum gets tedious. The `strum` crate generates these implementations:

```rust
use strum::{Display, EnumString, EnumIter, IntoStaticStr};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Display, EnumString, EnumIter, IntoStaticStr)]
#[strum(serialize_all = "snake_case")]
enum Permission {
    Read,
    Write,
    Admin,
    #[strum(serialize = "super_admin", serialize = "superadmin")]
    SuperAdmin,
}

// Now you get all of this for free:
let p: Permission = "write".parse().unwrap();
let s: &str = p.into();
for perm in Permission::iter() {
    println!("{perm}");
}
```

## The Deeper Pattern

Stringly typed code is really a symptom of not using Rust's type system to its full potential. Every time you pass a `String` that has a finite set of valid values, you're asking humans to enforce an invariant that the compiler could enforce for you.

Rust's enums are absurdly powerful — they can carry data, implement traits, be matched exhaustively, and compile down to efficient integer representations. Using `String` instead is like having a guard dog and then posting a "please don't rob us" sign instead.

Make invalid states unrepresentable. That's the whole game. If your system can't be in status `"banana"`, then don't use a type that allows `"banana"`. Use an enum. Let the compiler do the boring work so you can focus on the interesting problems.
