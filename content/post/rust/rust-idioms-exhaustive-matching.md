---
title: "Lesson 15: Exhaustive Matching — Let the compiler help"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 15
keywords: [Rust, rust, idiomatic, pattern matching, exhaustive, match, enums]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-05-03T22:08:00.000Z'
---

A teammate once added a new payment method to our system — `CryptoCurrency` — and forgot to update the fee calculation logic. In production. For three weeks. Nobody noticed because the switch statement in Java had a `default` case that silently applied a 0% fee. Free crypto transfers for everyone.

In Rust, the compiler would have caught this the moment the new variant was added. That's exhaustive matching — and it's one of Rust's most underappreciated features.

---

## What Exhaustive Matching Means

When you `match` on an enum, Rust requires you to handle every variant. Miss one, and it won't compile.

```rust
enum PaymentMethod {
    CreditCard,
    DebitCard,
    BankTransfer,
    PayPal,
}

fn calculate_fee(method: &PaymentMethod) -> f64 {
    match method {
        PaymentMethod::CreditCard => 0.029,
        PaymentMethod::DebitCard => 0.015,
        PaymentMethod::BankTransfer => 0.005,
        PaymentMethod::PayPal => 0.034,
    }
}
```

Now someone adds `CryptoCurrency`:

```rust
enum PaymentMethod {
    CreditCard,
    DebitCard,
    BankTransfer,
    PayPal,
    CryptoCurrency, // NEW
}
```

Every `match` on `PaymentMethod` that doesn't handle `CryptoCurrency` immediately becomes a compile error. Not a runtime error. Not a silent bug. A *compile error* that points you to every location that needs updating.

---

## The Wildcard Trap

Here's where people shoot themselves in the foot:

```rust
fn calculate_fee(method: &PaymentMethod) -> f64 {
    match method {
        PaymentMethod::CreditCard => 0.029,
        PaymentMethod::DebitCard => 0.015,
        _ => 0.01, // "everything else pays 1%"
    }
}
```

That `_` wildcard silently swallows every new variant. When you add `CryptoCurrency`, this match still compiles — and crypto payments silently get a 1% fee without anyone explicitly deciding that's the right fee.

**My rule: avoid wildcards in match arms unless you genuinely mean "everything else, including variants that don't exist yet."**

When is `_` okay?

```rust
fn is_card_payment(method: &PaymentMethod) -> bool {
    match method {
        PaymentMethod::CreditCard | PaymentMethod::DebitCard => true,
        _ => false, // This is fine — "not a card" is the correct default for future variants
    }
}
```

Here, the semantics of `_` are genuinely "everything that's not a card." If someone adds `Wire` or `Crypto`, the answer is still `false`. The wildcard is intentional and correct.

But for things like fee calculations, error messages, or display logic? Always be explicit.

---

## Pattern Matching With Data

Exhaustive matching is even more powerful when your enums carry data:

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
            format!("rectangle {}x{}", width, height)
        }
        Shape::Triangle { base, height } => {
            format!("triangle with base {:.1} and height {:.1}", base, height)
        }
    }
}

fn main() {
    let shapes = vec![
        Shape::Circle { radius: 5.0 },
        Shape::Rectangle { width: 4.0, height: 4.0 },
        Shape::Triangle { base: 3.0, height: 6.0 },
    ];

    for shape in &shapes {
        println!("{}: area = {:.2}", describe(shape), area(shape));
    }
}
```

Notice the guard clause (`if (width - height).abs() < f64::EPSILON`) — you can add conditions to match arms for more nuanced matching. The guard doesn't change exhaustiveness — all variants are still covered.

---

## Nested Pattern Matching

Patterns can destructure nested structures:

```rust
#[derive(Debug)]
enum Expr {
    Literal(f64),
    Add(Box<Expr>, Box<Expr>),
    Mul(Box<Expr>, Box<Expr>),
    Neg(Box<Expr>),
}

fn compute(expr: &Expr) -> f64 {
    match expr {
        Expr::Literal(n) => *n,
        Expr::Add(a, b) => compute(a) + compute(b),
        Expr::Mul(a, b) => compute(a) * compute(b),
        Expr::Neg(inner) => -compute(inner),
    }
}

fn main() {
    // (3 + 4) * 2
    let expr = Expr::Mul(
        Box::new(Expr::Add(
            Box::new(Expr::Literal(3.0)),
            Box::new(Expr::Literal(4.0)),
        )),
        Box::new(Expr::Literal(2.0)),
    );
    println!("Result: {}", compute(&expr)); // 14.0
}
```

---

## The `matches!` Macro

When you need a boolean check against a pattern, `matches!` is cleaner than a full `match`:

```rust
#[derive(Debug)]
enum Status {
    Active,
    Suspended { reason: String },
    Deleted,
}

fn main() {
    let status = Status::Suspended { reason: "TOS violation".into() };

    // Instead of:
    let _is_active = match &status {
        Status::Active => true,
        _ => false,
    };

    // Use matches!:
    let is_active = matches!(status, Status::Active);
    let is_suspended = matches!(status, Status::Suspended { .. });

    println!("Active: {}, Suspended: {}", is_active, is_suspended);

    // With guards:
    let items = vec![1, 2, 3, 15, 20, 25];
    let big_items: Vec<_> = items.iter().filter(|&&x| matches!(x, 10..=100)).collect();
    println!("Big items: {:?}", big_items); // [15, 20, 25]
}
```

---

## Matching on References

A subtle gotcha: when you match on a reference, the patterns need to account for it:

```rust
fn process(value: &Option<String>) {
    // Pattern matches on &Option<String>
    match value {
        Some(s) => println!("Got: {}", s),    // s is &String
        None => println!("Nothing"),
    }

    // Equivalent explicit form:
    match value {
        &Some(ref s) => println!("Got: {}", s), // also &String
        &None => println!("Nothing"),
    }
}

fn main() {
    let val = Some(String::from("hello"));
    process(&val);
    println!("Still have: {:?}", val); // val wasn't consumed
}
```

Modern Rust is smart about this — it automatically adds `ref` when matching on a reference. But knowing what's happening under the hood helps when you hit edge cases.

---

## Using `#[non_exhaustive]` for Library Enums

If you're writing a library and want to add variants in the future without breaking downstream code:

```rust
#[non_exhaustive]
#[derive(Debug)]
pub enum DatabaseError {
    ConnectionFailed,
    QueryFailed(String),
    Timeout,
}
```

With `#[non_exhaustive]`, code *outside* the crate must always have a wildcard arm:

```rust
// In consumer code:
fn handle_error(err: &DatabaseError) {
    match err {
        DatabaseError::ConnectionFailed => println!("Can't connect"),
        DatabaseError::QueryFailed(q) => println!("Bad query: {}", q),
        DatabaseError::Timeout => println!("Timed out"),
        _ => println!("Unknown database error"), // REQUIRED for non_exhaustive
    }
}
```

Within the defining crate, exhaustive matching still works normally. This is the right trade-off for library types that might grow.

---

## Exhaustiveness Beyond Enums

Pattern matching in Rust extends beyond enums. You get exhaustiveness checks on booleans, integer ranges, tuples, and nested patterns:

```rust
fn describe_number(n: i32) -> &'static str {
    match n {
        i32::MIN..=-1 => "negative",
        0 => "zero",
        1..=i32::MAX => "positive",
    }
}

fn describe_pair(pair: (bool, bool)) -> &'static str {
    match pair {
        (true, true) => "both true",
        (true, false) => "first true",
        (false, true) => "second true",
        (false, false) => "both false",
    }
    // All four combinations covered — no wildcard needed
}

fn main() {
    println!("{}", describe_number(-5));
    println!("{}", describe_number(0));
    println!("{}", describe_number(42));
    println!("{}", describe_pair((true, false)));
}
```

---

## Key Takeaways

- Exhaustive matching means the compiler forces you to handle every enum variant — new variants cause compile errors at every match site.
- Avoid `_` wildcards unless "everything else" is genuinely the correct semantic.
- Use `matches!()` for boolean pattern checks — it's cleaner than a match-with-wildcard.
- `#[non_exhaustive]` lets library authors add variants without breaking consumers.
- Exhaustiveness works on booleans, integer ranges, tuples, and nested patterns — not just enums.
- This is one of Rust's most powerful safety features. Lean into it — let the compiler catch variant-handling bugs for you.
