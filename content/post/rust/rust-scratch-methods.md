---
title: "Lesson 14: Methods and Associated Functions — impl blocks explained"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 14
keywords: [Rust, rust, methods, impl, associated functions, self, constructor, method syntax]
tags: [Rust tutorial, rust, beginner]
date: '2024-03-28T17:30:00.000Z'
---

In most object-oriented languages, data and behavior are bundled together inside classes. Rust separates them — you define data with `struct` (or `enum`) and attach behavior with `impl` blocks. This separation is cleaner than it sounds. You can add methods to a type from anywhere, not just its original definition. And there's no inheritance tax.

## Your First impl Block

```rust
#[derive(Debug)]
struct Rectangle {
    width: f64,
    height: f64,
}

impl Rectangle {
    fn area(&self) -> f64 {
        self.width * self.height
    }

    fn perimeter(&self) -> f64 {
        2.0 * (self.width + self.height)
    }

    fn is_square(&self) -> bool {
        (self.width - self.height).abs() < f64::EPSILON
    }
}

fn main() {
    let rect = Rectangle { width: 10.0, height: 5.0 };

    println!("Area: {}", rect.area());
    println!("Perimeter: {}", rect.perimeter());
    println!("Is square: {}", rect.is_square());
}
```

The `impl Rectangle` block defines methods for the `Rectangle` type. Inside the block, `&self` is a reference to the instance the method is called on. It's equivalent to `self: &Self`, where `Self` is an alias for `Rectangle`.

## The Three Flavors of self

Every method's first parameter determines how it accesses the instance:

```rust
#[derive(Debug)]
struct Counter {
    value: i32,
}

impl Counter {
    // Borrows self immutably — can read but not modify
    fn get(&self) -> i32 {
        self.value
    }

    // Borrows self mutably — can read and modify
    fn increment(&mut self) {
        self.value += 1;
    }

    // Takes ownership of self — consumes the instance
    fn into_inner(self) -> i32 {
        self.value
    }
}

fn main() {
    let mut counter = Counter { value: 0 };

    println!("Value: {}", counter.get());      // &self
    counter.increment();                        // &mut self
    counter.increment();
    println!("Value: {}", counter.get());

    let final_value = counter.into_inner();     // self — counter is consumed
    println!("Final: {final_value}");
    // println!("{}", counter.get());  // ERROR: counter was moved
}
```

**`&self`** — the method reads the instance. Use this for getters and computed properties.

**`&mut self`** — the method modifies the instance. Use this for setters and state changes.

**`self`** — the method consumes the instance. Use this for conversions and finalizers. After calling a `self` method, the original value is gone.

My recommendation: start with `&self`. Only use `&mut self` when you need to modify. Use `self` (consuming) rarely and deliberately.

## Associated Functions (Constructors)

Functions in an `impl` block that don't take `self` are called *associated functions*. They're called with `Type::function()` syntax — like static methods in other languages.

```rust
#[derive(Debug)]
struct Color {
    r: u8,
    g: u8,
    b: u8,
}

impl Color {
    // Constructor — the conventional name is "new"
    fn new(r: u8, g: u8, b: u8) -> Self {
        Color { r, g, b }
    }

    // Named constructors for common values
    fn red() -> Self {
        Color { r: 255, g: 0, b: 0 }
    }

    fn green() -> Self {
        Color { r: 0, g: 255, b: 0 }
    }

    fn blue() -> Self {
        Color { r: 0, g: 0, b: 255 }
    }

    fn white() -> Self {
        Color { r: 255, g: 255, b: 255 }
    }

    fn black() -> Self {
        Color { r: 0, g: 0, b: 0 }
    }

    // Method — takes &self
    fn brightness(&self) -> f64 {
        (self.r as f64 * 0.299 + self.g as f64 * 0.587 + self.b as f64 * 0.114) / 255.0
    }

    fn hex(&self) -> String {
        format!("#{:02x}{:02x}{:02x}", self.r, self.g, self.b)
    }
}

fn main() {
    let coral = Color::new(255, 127, 80);
    let red = Color::red();

    println!("Coral: {} (brightness: {:.2})", coral.hex(), coral.brightness());
    println!("Red: {} (brightness: {:.2})", red.hex(), red.brightness());
}
```

`Self` (capital S) is an alias for the type being implemented. Inside `impl Color`, `Self` means `Color`. Use `Self` — it's shorter and refactoring-proof.

## Builder Pattern

For structs with many optional fields, the builder pattern is idiomatic:

```rust
#[derive(Debug)]
struct HttpRequest {
    method: String,
    url: String,
    headers: Vec<(String, String)>,
    body: Option<String>,
    timeout_ms: u64,
}

impl HttpRequest {
    fn get(url: &str) -> Self {
        HttpRequest {
            method: String::from("GET"),
            url: url.to_string(),
            headers: Vec::new(),
            body: None,
            timeout_ms: 30_000,
        }
    }

    fn post(url: &str) -> Self {
        HttpRequest {
            method: String::from("POST"),
            url: url.to_string(),
            headers: Vec::new(),
            body: None,
            timeout_ms: 30_000,
        }
    }

    fn header(mut self, key: &str, value: &str) -> Self {
        self.headers.push((key.to_string(), value.to_string()));
        self
    }

    fn body(mut self, body: &str) -> Self {
        self.body = Some(body.to_string());
        self
    }

    fn timeout(mut self, ms: u64) -> Self {
        self.timeout_ms = ms;
        self
    }
}

fn main() {
    let request = HttpRequest::post("https://api.example.com/users")
        .header("Content-Type", "application/json")
        .header("Authorization", "Bearer token123")
        .body(r#"{"name": "Alice"}"#)
        .timeout(5000);

    println!("{:#?}", request);
}
```

Notice how builder methods take `self` (not `&mut self`) and return `Self`. This enables method chaining. Each method consumes the builder and returns a modified version. It's clean, readable, and the compiler ensures you can't use a half-built builder by accident.

## Multiple impl Blocks

You can split methods across multiple `impl` blocks:

```rust
#[derive(Debug)]
struct Player {
    name: String,
    health: i32,
    max_health: i32,
    attack: i32,
    defense: i32,
}

// Construction
impl Player {
    fn new(name: &str) -> Self {
        Player {
            name: name.to_string(),
            health: 100,
            max_health: 100,
            attack: 10,
            defense: 5,
        }
    }
}

// Combat methods
impl Player {
    fn take_damage(&mut self, amount: i32) {
        let actual = (amount - self.defense).max(0);
        self.health = (self.health - actual).max(0);
    }

    fn is_alive(&self) -> bool {
        self.health > 0
    }

    fn attack_power(&self) -> i32 {
        self.attack
    }
}

// Display methods
impl Player {
    fn status(&self) -> String {
        format!(
            "{}: {}/{} HP (ATK:{} DEF:{})",
            self.name, self.health, self.max_health, self.attack, self.defense
        )
    }
}

fn main() {
    let mut warrior = Player::new("Warrior");
    println!("{}", warrior.status());

    warrior.take_damage(15);
    println!("After hit: {}", warrior.status());

    warrior.take_damage(200);
    println!("After massive hit: {}", warrior.status());
    println!("Alive: {}", warrior.is_alive());
}
```

Multiple `impl` blocks are useful for organization. In practice, you'll mostly see them when different blocks implement different traits (Lesson 19).

## Methods on Enums

Enums get methods too — same syntax:

```rust
#[derive(Debug)]
enum TrafficLight {
    Red,
    Yellow,
    Green,
}

impl TrafficLight {
    fn duration_secs(&self) -> u32 {
        match self {
            TrafficLight::Red => 60,
            TrafficLight::Yellow => 5,
            TrafficLight::Green => 45,
        }
    }

    fn next(&self) -> TrafficLight {
        match self {
            TrafficLight::Red => TrafficLight::Green,
            TrafficLight::Yellow => TrafficLight::Red,
            TrafficLight::Green => TrafficLight::Yellow,
        }
    }

    fn can_go(&self) -> bool {
        matches!(self, TrafficLight::Green)
    }
}

fn main() {
    let mut light = TrafficLight::Red;

    for _ in 0..6 {
        println!("{:?}: {} secs, can go: {}", light, light.duration_secs(), light.can_go());
        light = light.next();
    }
}
```

The `matches!` macro is handy for simple pattern-matching boolean checks. It's cleaner than writing a full `match` that returns `true` in one arm and `false` in all others.

## Method Resolution and Auto-Dereferencing

When you call a method, Rust automatically dereferences as needed:

```rust
#[derive(Debug)]
struct Name(String);

impl Name {
    fn greet(&self) {
        println!("Hello, {}!", self.0);
    }
}

fn main() {
    let name = Name(String::from("Alice"));

    // All of these work:
    name.greet();           // direct call
    (&name).greet();        // explicit reference
    (&&name).greet();       // double reference — still works
    (&&&name).greet();      // triple reference — still works

    // Rust auto-dereferences to find the method
}
```

This auto-dereferencing is why you can call `String` methods on `&String` without explicit dereferencing. It's one of those ergonomic features that you don't notice until it's missing.

## A Practical Example: Bank Account

```rust
#[derive(Debug)]
struct BankAccount {
    owner: String,
    balance: f64,
    transactions: Vec<String>,
}

impl BankAccount {
    fn new(owner: &str, initial_balance: f64) -> Self {
        let mut account = BankAccount {
            owner: owner.to_string(),
            balance: initial_balance,
            transactions: Vec::new(),
        };
        account.transactions.push(format!("Account opened with ${:.2}", initial_balance));
        account
    }

    fn deposit(&mut self, amount: f64) -> Result<f64, String> {
        if amount <= 0.0 {
            return Err(String::from("Deposit amount must be positive"));
        }
        self.balance += amount;
        self.transactions.push(format!("Deposit: +${:.2}", amount));
        Ok(self.balance)
    }

    fn withdraw(&mut self, amount: f64) -> Result<f64, String> {
        if amount <= 0.0 {
            return Err(String::from("Withdrawal amount must be positive"));
        }
        if amount > self.balance {
            return Err(format!(
                "Insufficient funds: tried ${:.2}, have ${:.2}",
                amount, self.balance
            ));
        }
        self.balance -= amount;
        self.transactions.push(format!("Withdrawal: -${:.2}", amount));
        Ok(self.balance)
    }

    fn balance(&self) -> f64 {
        self.balance
    }

    fn statement(&self) -> String {
        let mut output = format!("=== Account: {} ===\n", self.owner);
        for t in &self.transactions {
            output.push_str(&format!("  {t}\n"));
        }
        output.push_str(&format!("  Balance: ${:.2}\n", self.balance));
        output
    }
}

fn main() {
    let mut account = BankAccount::new("Alice", 1000.0);

    match account.deposit(500.0) {
        Ok(balance) => println!("Deposited. Balance: ${:.2}", balance),
        Err(e) => println!("Error: {e}"),
    }

    match account.withdraw(200.0) {
        Ok(balance) => println!("Withdrew. Balance: ${:.2}", balance),
        Err(e) => println!("Error: {e}"),
    }

    match account.withdraw(5000.0) {
        Ok(balance) => println!("Withdrew. Balance: ${:.2}", balance),
        Err(e) => println!("Error: {e}"),
    }

    println!("\n{}", account.statement());
}
```

Notice how `deposit` and `withdraw` return `Result<f64, String>` instead of panicking. We'll cover `Result` properly in Lesson 16, but the pattern should already feel natural: operations that can fail return `Result`, and the caller handles success or failure.

## Conventions

- **`new`** — conventional name for the primary constructor. Not enforced by the language.
- **Getters** — just name them after the field: `fn name(&self) -> &str`. No `get_` prefix.
- **Setters** — use `set_` prefix if you need them: `fn set_name(&mut self, name: &str)`. But consider if you actually need setters — often a constructor or builder is better.
- **`is_`/`has_`** — prefix for boolean methods: `fn is_empty(&self) -> bool`.
- **`into_`** — prefix for consuming conversions: `fn into_inner(self) -> T`.
- **`as_`** — prefix for cheap reference conversions: `fn as_str(&self) -> &str`.

These conventions are documented in the Rust API Guidelines and are followed by the standard library. Follow them — your code will feel natural to other Rust developers.

Next: collections — Vec, HashMap, and HashSet. The data structures you'll reach for every day.
