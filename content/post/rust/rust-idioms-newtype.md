---
title: "Lesson 7: The Newtype Pattern — Type safety for free"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 7
keywords: [Rust, rust, idiomatic, newtype, wrapper types, type safety, zero cost]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-04-21T13:15:00.000Z'
---

True story: a Mars orbiter was lost because one team used pounds and another used newtons. Same numeric type, different semantic meanings. $327 million, gone.

You'd think we'd have learned. But I still see codebases where user IDs, product IDs, and order IDs are all `i64`. Where distances are `f64` whether they're meters or feet. Where an email address is just a `String` that you hope someone validated.

The newtype pattern fixes this. It gives you type safety with zero runtime cost.

---

## The Problem: Primitive Obsession

```rust
// Every ID is just an i64. What could go wrong?
fn transfer_ownership(from_user: i64, to_user: i64, product: i64) {
    println!("Transfer product {} from user {} to user {}", product, from_user, to_user);
}

fn main() {
    let user_id = 42;
    let product_id = 99;
    let other_user_id = 17;

    // Oops — swapped user_id and product_id. Compiles fine.
    transfer_ownership(product_id, user_id, other_user_id);
}
```

The compiler has no idea that `user_id` and `product_id` are semantically different. They're both `i64`. You can swap them, add them together, use a product ID where a user ID is expected — all without a single warning.

---

## The Newtype Pattern

Wrap the primitive in a single-field tuple struct:

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
struct UserId(i64);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
struct ProductId(i64);

fn transfer_ownership(from: UserId, to: UserId, product: ProductId) {
    println!("Transfer {:?} from {:?} to {:?}", product, from, to);
}

fn main() {
    let user = UserId(42);
    let product = ProductId(99);
    let other_user = UserId(17);

    transfer_ownership(user, other_user, product); // Correct

    // This won't compile:
    // transfer_ownership(product, user, other_user);
    // ERROR: expected `UserId`, found `ProductId`
}
```

That's it. One line of struct definition, and you've eliminated an entire class of bugs.

The wrapper has zero runtime overhead. Rust guarantees that a single-field tuple struct has the same memory layout as the inner type. `UserId(42)` takes exactly as much space as `42i64`.

---

## Going Beyond IDs: Domain Types

Newtypes aren't just for IDs. They're for any value that has semantic meaning beyond its primitive type.

```rust
#[derive(Debug, Clone)]
struct EmailAddress(String);

impl EmailAddress {
    fn new(email: &str) -> Result<Self, String> {
        if email.contains('@') && email.contains('.') {
            Ok(EmailAddress(email.to_string()))
        } else {
            Err(format!("Invalid email: {}", email))
        }
    }

    fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Debug, Clone)]
struct Meters(f64);

#[derive(Debug, Clone)]
struct Kilometers(f64);

impl Meters {
    fn to_kilometers(&self) -> Kilometers {
        Kilometers(self.0 / 1000.0)
    }
}

impl Kilometers {
    fn to_meters(&self) -> Meters {
        Meters(self.0 * 1000.0)
    }
}

fn calculate_fuel(distance: Kilometers, efficiency: f64) -> f64 {
    distance.0 * efficiency
}

fn main() {
    let distance = Meters(5000.0);
    let km = distance.to_kilometers();
    let fuel = calculate_fuel(km, 0.08);
    println!("Fuel needed: {:.1} liters", fuel);

    // Can't accidentally pass Meters where Kilometers is expected:
    // calculate_fuel(distance, 0.08); // ERROR
}
```

The email type is especially powerful — validation happens at construction time. Once you have an `EmailAddress`, you *know* it passed validation. No need to re-check downstream.

---

## Implementing Traits on Newtypes

The common complaint: "But now I have to implement all the traits manually!" Not really. `derive` handles most of it, and you can implement `Deref` for transparent access when appropriate.

```rust
use std::fmt;

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct Username(String);

impl Username {
    fn new(name: &str) -> Result<Self, String> {
        if name.len() >= 3 && name.len() <= 20 && name.chars().all(|c| c.is_alphanumeric() || c == '_') {
            Ok(Username(name.to_string()))
        } else {
            Err(format!("Invalid username: '{}'", name))
        }
    }
}

impl fmt::Display for Username {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "@{}", self.0)
    }
}

impl AsRef<str> for Username {
    fn as_ref(&self) -> &str {
        &self.0
    }
}

fn main() {
    let user = Username::new("atharva_p").unwrap();
    println!("{}", user); // @atharva_p

    // Can use &Username where &str is expected via AsRef
    fn greet(name: &str) {
        println!("Hello, {}", name);
    }
    greet(user.as_ref());
}
```

I prefer `AsRef` over `Deref` for newtypes. `Deref` makes the inner type's methods available directly, which can undermine the whole point of the wrapper. `AsRef` requires an explicit `.as_ref()` call — just enough friction to make the conversion intentional.

---

## Newtype for Implementing Foreign Traits

This is one of Rust's orphan rule workarounds. You can't implement a foreign trait on a foreign type, but you can wrap the foreign type in a newtype:

```rust
use std::fmt;

// Can't impl Display for Vec<T> — both are foreign
// But we can wrap it:
struct CommaSeparated<T>(Vec<T>);

impl<T: fmt::Display> fmt::Display for CommaSeparated<T> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        for (i, item) in self.0.iter().enumerate() {
            if i > 0 {
                write!(f, ", ")?;
            }
            write!(f, "{}", item)?;
        }
        Ok(())
    }
}

fn main() {
    let items = CommaSeparated(vec![1, 2, 3, 4, 5]);
    println!("{}", items); // 1, 2, 3, 4, 5
}
```

---

## Newtype With Operations

Sometimes you want the wrapper to support arithmetic or comparison. Derive what you can, implement what you need:

```rust
use std::ops::Add;

#[derive(Debug, Clone, Copy, PartialEq, PartialOrd)]
struct Celsius(f64);

#[derive(Debug, Clone, Copy, PartialEq, PartialOrd)]
struct Fahrenheit(f64);

impl Celsius {
    fn to_fahrenheit(self) -> Fahrenheit {
        Fahrenheit(self.0 * 9.0 / 5.0 + 32.0)
    }
}

impl Fahrenheit {
    fn to_celsius(self) -> Celsius {
        Celsius((self.0 - 32.0) * 5.0 / 9.0)
    }
}

// You CAN add two Celsius values (makes sense: temperature differences)
impl Add for Celsius {
    type Output = Celsius;
    fn add(self, rhs: Self) -> Self::Output {
        Celsius(self.0 + rhs.0)
    }
}

// But you can NOT add Celsius + Fahrenheit — that's a type error.

fn main() {
    let boiling = Celsius(100.0);
    let body_temp = Fahrenheit(98.6);

    println!("{:?} = {:?}", boiling, boiling.to_fahrenheit());
    println!("{:?} = {:?}", body_temp, body_temp.to_celsius());

    let sum = Celsius(10.0) + Celsius(20.0);
    println!("Sum: {:?}", sum); // Celsius(30.0)

    // This won't compile:
    // let invalid = boiling + body_temp; // ERROR: different types
}
```

---

## When Newtype Is Overkill

I'll be honest — not everything needs a newtype. Here's my decision framework:

**Use newtype when:**
- Two or more values share the same primitive type but mean different things (IDs, measurements, currencies)
- You want to enforce validation at construction time (emails, URLs, phone numbers)
- You need to implement a foreign trait on a foreign type
- The type crosses module or API boundaries

**Skip newtype when:**
- The value only exists locally within a single function
- There's only one value of that type in the context (no confusion possible)
- The added verbosity outweighs the safety benefit
- You're prototyping and speed matters more than correctness

---

## A Complete Example: Money

Here's a realistic newtype that I've used in production:

```rust
use std::fmt;
use std::ops::{Add, Sub};

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
struct Cents(i64);

impl Cents {
    fn from_dollars(dollars: f64) -> Self {
        Cents((dollars * 100.0).round() as i64)
    }

    fn dollars(self) -> f64 {
        self.0 as f64 / 100.0
    }

    fn is_negative(self) -> bool {
        self.0 < 0
    }
}

impl Add for Cents {
    type Output = Self;
    fn add(self, rhs: Self) -> Self {
        Cents(self.0 + rhs.0)
    }
}

impl Sub for Cents {
    type Output = Self;
    fn sub(self, rhs: Self) -> Self {
        Cents(self.0 - rhs.0)
    }
}

impl fmt::Display for Cents {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if self.0 < 0 {
            write!(f, "-${:.2}", (-self.0) as f64 / 100.0)
        } else {
            write!(f, "${:.2}", self.0 as f64 / 100.0)
        }
    }
}

fn apply_discount(price: Cents, percent: u32) -> Cents {
    Cents(price.0 * (100 - percent as i64) / 100)
}

fn main() {
    let price = Cents::from_dollars(29.99);
    let tax = Cents::from_dollars(2.40);
    let total = price + tax;
    let discounted = apply_discount(total, 10);

    println!("Price: {}", price);
    println!("Tax: {}", tax);
    println!("Total: {}", total);
    println!("After 10% discount: {}", discounted);
}
```

Money stored as `Cents(i64)` instead of `f64` avoids floating-point precision issues. The newtype makes it impossible to accidentally mix cents with other integer values. And `Display` formats it as a dollar amount. All for zero runtime cost.

---

## Key Takeaways

- The newtype pattern wraps a primitive in a single-field struct: `struct UserId(i64)`.
- Zero runtime overhead — same memory layout as the inner type.
- Prevents mixing semantically different values that share the same type.
- Validation at construction time creates invariants that downstream code can rely on.
- Use `AsRef` for explicit conversion, not `Deref` — keep the wrapper opaque.
- Derive `Debug`, `Clone`, `PartialEq`, `Hash`, etc. to get the behavior you need with minimal boilerplate.
