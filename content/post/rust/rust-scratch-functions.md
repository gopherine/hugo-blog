---
title: "Lesson 5: Functions, Expressions, and Statements — Everything is an expression"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 5
keywords: [Rust, rust, functions, expressions, statements, return values, rust functions]
tags: [Rust tutorial, rust, beginner]
date: '2024-03-13T10:20:00.000Z'
---

When I first read that "everything in Rust is an expression," I thought it was marketing fluff. It's not. It's a genuine design principle that affects how you write code every single day, and once you internalize it, going back to statement-heavy languages feels clunky.

## Defining Functions

```rust
fn add(a: i32, b: i32) -> i32 {
    a + b
}

fn main() {
    let result = add(3, 7);
    println!("3 + 7 = {result}");
}
```

A few things to notice:

- **Type annotations are mandatory on parameters.** No inference here. Function signatures are contracts — they tell you exactly what goes in and what comes out. This is intentional.
- **The return type goes after `->`.** If there's no `->`, the function returns `()` (unit).
- **No `return` keyword.** The last expression in the function body is implicitly returned. No semicolon on that last line — that's important.

That last point is worth drilling into.

## Expressions vs. Statements

This is the most important concept in this lesson.

An **expression** evaluates to a value. A **statement** performs an action but doesn't produce a value.

```rust
fn main() {
    // These are expressions (they produce values):
    // 5
    // 5 + 3
    // x > 0
    // { let a = 5; a + 1 }

    // These are statements (they don't produce values):
    // let x = 5;
    // println!("hello");

    let x = 5;      // statement — the `let` binding itself doesn't produce a value
    let y = x + 1;  // x + 1 is an expression; wrapping it with let makes it a statement

    println!("{x} {y}");
}
```

In Rust, almost everything is an expression. `if` is an expression. Blocks are expressions. `match` is an expression. The only true statements are `let` bindings and item declarations (like `fn`, `struct`, etc.).

## The Semicolon Rule

Here's where it clicks. A semicolon turns an expression into a statement by discarding its value.

```rust
fn returns_five() -> i32 {
    5       // expression — this value is returned
}

fn returns_unit() {
    5;      // statement — the value is discarded, returns ()
}

fn main() {
    let a = returns_five();
    let b = returns_unit();
    println!("a: {a}");
    println!("b: {b:?}");  // prints "()"
}
```

This is the #1 beginner mistake in Rust: adding a semicolon to the last line of a function and wondering why the return type doesn't match. The compiler error is helpful — it'll tell you "expected `i32`, found `()`" — but it's still confusing the first time.

**My rule: if a function returns a value, the last expression should NOT have a semicolon.** If a function returns `()`, end with a semicolon (or just have nothing to return).

## The return Keyword

You *can* use `return` for early returns:

```rust
fn absolute_value(x: i32) -> i32 {
    if x < 0 {
        return -x;
    }
    x
}

fn main() {
    println!("{}", absolute_value(-5));   // 5
    println!("{}", absolute_value(3));    // 3
}
```

But for the final value, use the implicit return. It's idiomatic and cleaner. Using `return` at the end of a function will get flagged by clippy — it's a code smell in Rust.

## Blocks Are Expressions

This is where things get powerful. A block `{ ... }` is an expression that evaluates to its last expression.

```rust
fn main() {
    let y = {
        let x = 3;
        x + 1       // no semicolon — this is the block's value
    };

    println!("y: {y}");  // y: 4
}
```

The block creates a scope. `x` only exists inside it. The block evaluates to `x + 1`, which is `4`. That value gets bound to `y`.

This is genuinely useful. You can use blocks to compute complex values while keeping temporary variables scoped:

```rust
fn main() {
    let area = {
        let width = 10.0_f64;
        let height = 5.5;
        width * height
    };

    // width and height don't exist here — they're scoped to the block
    println!("Area: {area}");
}
```

## if as an Expression

Since `if` is an expression, you can use it on the right side of a `let`:

```rust
fn main() {
    let condition = true;
    let number = if condition { 5 } else { 6 };
    println!("number: {number}");
}
```

This is Rust's ternary operator. There's no `? :` syntax because there doesn't need to be — `if/else` already works as an expression.

Both branches must return the same type:

```rust
fn main() {
    let condition = true;
    // let number = if condition { 5 } else { "six" }; // ERROR: types don't match
    let number = if condition { 5 } else { 6 };
    println!("{number}");
}
```

## Function Parameters and Ownership

Function parameters work like `let` bindings — they can be immutable or mutable:

```rust
fn print_and_increment(mut count: i32) {
    println!("Before: {count}");
    count += 1;
    println!("After: {count}");
}

fn main() {
    let x = 5;
    print_and_increment(x);
    println!("x is still: {x}");  // x is unchanged — i32 is Copy
}
```

The `mut` on the parameter means the function can modify its *local copy*. For primitive types like `i32`, the value is copied when passed to the function. We'll get into ownership in Lesson 7, where this distinction becomes much more important.

## Multiple Return Values (Sort Of)

Rust doesn't have multiple return values, but tuples do the job:

```rust
fn divide(a: f64, b: f64) -> (f64, f64) {
    let quotient = a / b;
    let remainder = a % b;
    (quotient, remainder)
}

fn main() {
    let (q, r) = divide(17.0, 5.0);
    println!("17 / 5 = {q} remainder {r}");
}
```

Destructuring the tuple at the call site keeps things clean. If you find yourself returning tuples with more than 3 elements, that's a sign you should use a struct instead.

## Functions That Don't Return (Diverging Functions)

Some functions never return. Rust has a type for this: `!` (called "never").

```rust
fn forever() -> ! {
    loop {
        // runs forever
    }
}
```

You won't write these often, but they exist in the standard library. `std::process::exit()` returns `!`, and so does `panic!()`. The `!` type tells the compiler "this function never completes normally," which is useful for type checking.

## Nested Functions

You can define functions inside other functions:

```rust
fn main() {
    fn square(x: i32) -> i32 {
        x * x
    }

    println!("5 squared: {}", square(5));
}
```

Inner functions can't access variables from the outer function, though. If you need that, you want closures — Lesson 21.

## A Practical Example

Let's put it all together with something useful — a temperature converter:

```rust
fn celsius_to_fahrenheit(c: f64) -> f64 {
    c * 9.0 / 5.0 + 32.0
}

fn fahrenheit_to_celsius(f: f64) -> f64 {
    (f - 32.0) * 5.0 / 9.0
}

fn format_temp(value: f64, unit: char) -> String {
    format!("{value:.1}{unit}")
}

fn main() {
    let temps_celsius = [0.0, 20.0, 37.0, 100.0];

    for c in temps_celsius {
        let f = celsius_to_fahrenheit(c);
        println!(
            "{} = {}",
            format_temp(c, 'C'),
            format_temp(f, 'F')
        );
    }

    println!("---");

    let body_temp_f = 98.6;
    let body_temp_c = fahrenheit_to_celsius(body_temp_f);
    println!(
        "Body temp: {} = {}",
        format_temp(body_temp_f, 'F'),
        format_temp(body_temp_c, 'C')
    );
}
```

Output:

```
0.0C = 32.0F
20.0C = 68.0F
37.0C = 98.6F
100.0C = 212.0F
---
Body temp: 98.6F = 37.0C
```

Notice `format_temp` returns `String` (not `&str`). The `format!` macro creates a new String, and the function transfers ownership of that String to the caller. This ownership transfer is a preview of what's coming in Lessons 7 and 8.

## Style Notes

A few conventions the Rust community follows:

- Function names are `snake_case`. Always. No exceptions.
- Keep functions short. If a function is longer than your screen, consider splitting it.
- Put the `main` function at the bottom of the file. Helper functions go above it. (This is a convention, not a requirement — Rust doesn't care about order.)
- Document public functions with `///` doc comments.

```rust
/// Converts a temperature from Celsius to Fahrenheit.
///
/// # Examples
///
/// ```
/// let f = celsius_to_fahrenheit(100.0);
/// assert_eq!(f, 212.0);
/// ```
fn celsius_to_fahrenheit(c: f64) -> f64 {
    c * 9.0 / 5.0 + 32.0
}

fn main() {
    println!("{}", celsius_to_fahrenheit(100.0));
}
```

Those doc examples? They're actually compiled and run by `cargo test`. Documentation that's always correct because the compiler enforces it. Beautiful.

Next up: control flow — if, loop, while, for, and why Rust doesn't have a ternary operator (spoiler: it doesn't need one).
