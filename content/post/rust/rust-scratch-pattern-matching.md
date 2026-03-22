---
title: "Lesson 13: Pattern Matching — The match superpower"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 13
keywords: [Rust, rust, pattern matching, match, if let, while let, destructuring, guards]
tags: [Rust tutorial, rust, beginner]
date: '2024-03-26T09:45:00.000Z'
---

Pattern matching ruined switch statements for me. After using `match` in Rust for a few months, going back to C-style switch/case feels like using a butter knife to do surgery. The compiler checks that you've covered every case. It destructures data inline. It does not fall through. It is, hands down, the most elegant control flow construct I've ever used.

## Basic match

```rust
fn main() {
    let x = 3;

    match x {
        1 => println!("one"),
        2 => println!("two"),
        3 => println!("three"),
        _ => println!("something else"),
    }
}
```

`_` is the catch-all pattern — it matches anything. Think of it as the `default` case in a switch statement, except it's mandatory when the other arms don't cover all possibilities.

## match Is Exhaustive

This is the killer feature. The compiler ensures you handle *every* possible value:

```rust
#[derive(Debug)]
enum Coin {
    Penny,
    Nickel,
    Dime,
    Quarter,
}

fn value_in_cents(coin: &Coin) -> u32 {
    match coin {
        Coin::Penny => 1,
        Coin::Nickel => 5,
        Coin::Dime => 10,
        Coin::Quarter => 25,
        // If you remove any arm, the compiler errors out
    }
}

fn main() {
    let coins = [Coin::Penny, Coin::Dime, Coin::Quarter, Coin::Nickel];
    let total: u32 = coins.iter().map(value_in_cents).sum();
    println!("Total: {total} cents");
}
```

Now imagine you add a `HalfDollar` variant to the enum. Every `match` on `Coin` anywhere in your codebase will fail to compile until you handle the new variant. The compiler finds every place that needs updating. In a language with switch statements, adding a new case means *hoping* you found every switch. In Rust, the compiler tells you.

## match Is an Expression

Like everything else in Rust, `match` produces a value:

```rust
fn main() {
    let x = 42;

    let description = match x {
        0 => "zero",
        1..=9 => "single digit",
        10..=99 => "double digit",
        100..=999 => "triple digit",
        _ => "big number",
    };

    println!("{x} is a {description}");
}
```

All arms must return the same type. The compiler checks this.

## Destructuring

Pattern matching shines when you pull apart complex data:

### Tuples

```rust
fn main() {
    let point = (3, -5);

    match point {
        (0, 0) => println!("at the origin"),
        (x, 0) => println!("on the x-axis at {x}"),
        (0, y) => println!("on the y-axis at {y}"),
        (x, y) => println!("at ({x}, {y})"),
    }
}
```

### Structs

```rust
struct Point {
    x: i32,
    y: i32,
}

fn main() {
    let p = Point { x: 0, y: 7 };

    match p {
        Point { x: 0, y } => println!("on the y-axis at y={y}"),
        Point { x, y: 0 } => println!("on the x-axis at x={x}"),
        Point { x, y } => println!("at ({x}, {y})"),
    }
}
```

### Enums with Data

```rust
#[derive(Debug)]
enum Message {
    Quit,
    Text(String),
    Move { x: i32, y: i32 },
    Color(u8, u8, u8),
}

fn process(msg: &Message) {
    match msg {
        Message::Quit => println!("Quitting"),
        Message::Text(s) => println!("Text: {s}"),
        Message::Move { x, y } => println!("Move to ({x}, {y})"),
        Message::Color(r, g, b) => println!("Color: ({r}, {g}, {b})"),
    }
}

fn main() {
    let messages = vec![
        Message::Text(String::from("hello")),
        Message::Move { x: 10, y: 20 },
        Message::Color(255, 0, 128),
        Message::Quit,
    ];

    for msg in &messages {
        process(msg);
    }
}
```

### Nested Destructuring

You can go as deep as you need:

```rust
#[derive(Debug)]
enum Shape {
    Circle { center: (f64, f64), radius: f64 },
    Rect { top_left: (f64, f64), bottom_right: (f64, f64) },
}

fn describe(shape: &Shape) {
    match shape {
        Shape::Circle { center: (cx, cy), radius } => {
            println!("Circle at ({cx}, {cy}) with radius {radius}");
        }
        Shape::Rect { top_left: (x1, y1), bottom_right: (x2, y2) } => {
            let width = x2 - x1;
            let height = y1 - y2;
            println!("Rectangle {}x{} at ({x1}, {y1})", width, height);
        }
    }
}

fn main() {
    let shapes = vec![
        Shape::Circle { center: (0.0, 0.0), radius: 5.0 },
        Shape::Rect { top_left: (1.0, 10.0), bottom_right: (6.0, 2.0) },
    ];

    for s in &shapes {
        describe(s);
    }
}
```

## Match Guards

Add conditions to patterns with `if`:

```rust
fn main() {
    let num = Some(4);

    match num {
        Some(x) if x < 0 => println!("{x} is negative"),
        Some(x) if x == 0 => println!("zero"),
        Some(x) if x < 10 => println!("{x} is a small positive"),
        Some(x) => println!("{x} is a large positive"),
        None => println!("nothing"),
    }
}
```

The guard (`if x < 0`) adds a condition beyond what the pattern alone can express. The arm only matches if both the pattern and the guard are true.

## Multiple Patterns with |

Match multiple patterns in one arm:

```rust
fn main() {
    let x = 3;

    match x {
        1 | 2 => println!("one or two"),
        3 | 4 => println!("three or four"),
        5..=10 => println!("five through ten"),
        _ => println!("something else"),
    }
}
```

## Binding with @

Bind a value while also testing it against a pattern:

```rust
fn main() {
    let x = 15;

    match x {
        n @ 1..=12 => println!("month {n}"),
        n @ 13..=24 => println!("{n} is in the second year"),
        n => println!("{n} is way out there"),
    }
}
```

The `@` operator binds the matched value to a variable while simultaneously checking it against a range. Without `@`, you'd need a guard and a separate binding.

## Ignoring Values

Use `_` to ignore values you don't care about:

```rust
fn main() {
    let point = (1, 2, 3);

    match point {
        (x, _, z) => println!("x={x}, z={z}"),  // ignore y
    }

    let numbers = (1, 2, 3, 4, 5);
    match numbers {
        (first, .., last) => println!("first={first}, last={last}"),  // ignore middle
    }
}
```

The `..` syntax ignores multiple values in the middle of a tuple or struct pattern.

## let Patterns

You've been using patterns all along — `let` is a pattern:

```rust
fn main() {
    // These are all pattern bindings:
    let x = 5;
    let (a, b) = (1, 2);
    let (first, _, third) = (10, 20, 30);

    println!("{x} {a} {b} {first} {third}");
}
```

Function parameters are patterns too:

```rust
fn print_point(&(x, y): &(i32, i32)) {
    println!("({x}, {y})");
}

fn main() {
    let point = (3, 5);
    print_point(&point);
}
```

## A Practical Example: Simple Calculator

```rust
#[derive(Debug)]
enum Token {
    Number(f64),
    Plus,
    Minus,
    Star,
    Slash,
}

fn calculate(left: f64, op: &Token, right: f64) -> Option<f64> {
    match op {
        Token::Plus => Some(left + right),
        Token::Minus => Some(left - right),
        Token::Star => Some(left * right),
        Token::Slash if right != 0.0 => Some(left / right),
        Token::Slash => None,  // division by zero
        Token::Number(_) => None,  // not an operator
    }
}

fn main() {
    let operations: Vec<(f64, Token, f64)> = vec![
        (10.0, Token::Plus, 5.0),
        (10.0, Token::Minus, 3.0),
        (6.0, Token::Star, 7.0),
        (15.0, Token::Slash, 4.0),
        (10.0, Token::Slash, 0.0),
    ];

    for (left, op, right) in &operations {
        match calculate(*left, op, *right) {
            Some(result) => println!("{left} {:?} {right} = {result}", op),
            None => println!("{left} {:?} {right} = ERROR", op),
        }
    }
}
```

## Another Example: Parsing Configuration

```rust
fn parse_config_line(line: &str) -> Option<(&str, &str)> {
    let line = line.trim();

    match line.chars().next() {
        None => None,           // empty line
        Some('#') => None,      // comment
        Some(_) => {
            match line.split_once('=') {
                Some((key, value)) => Some((key.trim(), value.trim())),
                None => None,   // no = sign
            }
        }
    }
}

fn main() {
    let config = r#"
# Database settings
host = localhost
port = 5432
# Connection pool
max_connections = 100
timeout = 30
invalid line without equals
    "#;

    println!("Parsed config:");
    for line in config.lines() {
        if let Some((key, value)) = parse_config_line(line) {
            println!("  {key} => {value}");
        }
    }
}
```

## match vs. if-else

When should you use `match` vs. a chain of `if-else`?

**Use `match` when:**
- You're matching on an enum — always use match
- You're destructuring data
- You have more than 2-3 branches
- You want the compiler to check exhaustiveness

**Use `if-else` when:**
- You have a simple boolean condition
- There are only 2 branches
- The conditions are complex and don't fit patterns

My bias: I use `match` more than most people. Exhaustiveness checking is too valuable to give up casually.

## Performance Note

`match` compiles to efficient code. The compiler generates jump tables, sequential comparisons, or binary searches depending on the patterns. It's at least as fast as a switch statement, often faster because the compiler has more information about the pattern structure.

Don't avoid `match` for performance reasons. There's no performance reason to avoid it.

Next: methods and `impl` blocks — giving your types behavior.
