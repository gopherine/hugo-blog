---
title: "Lesson 9: From and Into — Seamless type conversions"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 9
keywords: [Rust, rust, idiomatic, From, Into, type conversion, traits]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-04-24T07:55:00.000Z'
---

The first time I saw `.into()` in Rust code, I was baffled. "Into *what*?" There was no type annotation, no explicit conversion function, just `.into()` hanging off a value like it knew exactly what to become. And somehow the compiler figured it out.

That's the `From`/`Into` trait pair. It's one of the most-used patterns in idiomatic Rust, and once you understand it, your APIs will feel dramatically smoother.

---

## The Basics: `From` and `Into` Are Mirror Images

```rust
// If you implement From<A> for B...
impl From<i32> for MyType {
    fn from(value: i32) -> Self {
        MyType(value)
    }
}

// ...you get Into<B> for A for free
let x: MyType = 42.into();
```

**Always implement `From`, never `Into` directly.** The standard library provides a blanket implementation: if `From<A>` exists for `B`, then `Into<B>` is automatically available for `A`. Implementing `Into` directly doesn't give you the reverse.

```rust
#[derive(Debug)]
struct Meters(f64);

#[derive(Debug)]
struct Kilometers(f64);

impl From<Kilometers> for Meters {
    fn from(km: Kilometers) -> Self {
        Meters(km.0 * 1000.0)
    }
}

fn main() {
    let distance = Kilometers(5.0);

    // Using From explicitly
    let m = Meters::from(Kilometers(5.0));
    println!("{:?}", m); // Meters(5000.0)

    // Using Into (automatically derived from From)
    let m: Meters = distance.into();
    println!("{:?}", m); // Meters(5000.0)
}
```

---

## Why This Matters: Flexible Function Signatures

The real power of `From`/`Into` shows up in function signatures. Instead of forcing callers to convert types manually, you accept anything that can be converted:

```rust
#[derive(Debug)]
struct Color {
    r: u8,
    g: u8,
    b: u8,
}

impl From<(u8, u8, u8)> for Color {
    fn from(tuple: (u8, u8, u8)) -> Self {
        Color { r: tuple.0, g: tuple.1, b: tuple.2 }
    }
}

impl From<u32> for Color {
    fn from(hex: u32) -> Self {
        Color {
            r: ((hex >> 16) & 0xFF) as u8,
            g: ((hex >> 8) & 0xFF) as u8,
            b: (hex & 0xFF) as u8,
        }
    }
}

impl From<&str> for Color {
    fn from(name: &str) -> Self {
        match name {
            "red" => Color { r: 255, g: 0, b: 0 },
            "green" => Color { r: 0, g: 255, b: 0 },
            "blue" => Color { r: 0, g: 0, b: 255 },
            _ => Color { r: 0, g: 0, b: 0 },
        }
    }
}

// Accept anything convertible to Color
fn set_background(color: impl Into<Color>) {
    let c: Color = color.into();
    println!("Background: rgb({}, {}, {})", c.r, c.g, c.b);
}

fn main() {
    set_background((255u8, 128u8, 0u8));   // From tuple
    set_background(0xFF8000u32);             // From hex
    set_background("red");                   // From name
    set_background(Color { r: 42, g: 42, b: 42 }); // Already a Color (From<T> for T is blanket-implemented)
}
```

The caller doesn't have to think about conversion. They just pass whatever they have — a tuple, a hex value, a color name — and the compiler handles the rest.

---

## `From` for Error Conversion

This is where `From` becomes essential. Remember the `?` operator? It automatically calls `From` to convert error types.

```rust
use std::num::ParseIntError;
use std::io;

#[derive(Debug)]
enum AppError {
    Io(io::Error),
    Parse(ParseIntError),
    Custom(String),
}

impl From<io::Error> for AppError {
    fn from(e: io::Error) -> Self {
        AppError::Io(e)
    }
}

impl From<ParseIntError> for AppError {
    fn from(e: ParseIntError) -> Self {
        AppError::Parse(e)
    }
}

impl std::fmt::Display for AppError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            AppError::Io(e) => write!(f, "IO error: {}", e),
            AppError::Parse(e) => write!(f, "Parse error: {}", e),
            AppError::Custom(msg) => write!(f, "{}", msg),
        }
    }
}

fn read_number_from_file(path: &str) -> Result<i64, AppError> {
    let content = std::fs::read_to_string(path)?;  // io::Error → AppError via From
    let number: i64 = content.trim().parse()?;       // ParseIntError → AppError via From
    Ok(number)
}

fn main() {
    match read_number_from_file("number.txt") {
        Ok(n) => println!("Got: {}", n),
        Err(e) => eprintln!("Error: {}", e),
    }
}
```

Each `?` checks: "Is there a `From` implementation that converts this error type into the function's return error type?" If yes, it converts and returns. If no, it's a compile error. This is why implementing `From` for your error types is so important — it makes `?` work seamlessly.

---

## `From` for Constructors

A common idiom: use `From` instead of (or alongside) `new()` for simple constructors:

```rust
#[derive(Debug)]
struct Username(String);

impl From<String> for Username {
    fn from(s: String) -> Self {
        Username(s)
    }
}

impl From<&str> for Username {
    fn from(s: &str) -> Self {
        Username(s.to_string())
    }
}

fn main() {
    let u1 = Username::from("atharva");
    let u2: Username = String::from("bob").into();
    let u3: Username = "charlie".into();

    println!("{:?}, {:?}, {:?}", u1, u2, u3);
}
```

This is especially nice when combined with `impl Into<T>` parameters — callers can pass either `&str` or `String` without you needing two separate methods.

---

## `TryFrom` — When Conversion Can Fail

`From` is for infallible conversions. When conversion might fail, use `TryFrom`:

```rust
use std::convert::TryFrom;

#[derive(Debug)]
struct PortNumber(u16);

impl TryFrom<i32> for PortNumber {
    type Error = String;

    fn try_from(value: i32) -> Result<Self, Self::Error> {
        if value < 0 {
            Err(format!("Port cannot be negative: {}", value))
        } else if value > 65535 {
            Err(format!("Port too large: {}", value))
        } else {
            Ok(PortNumber(value as u16))
        }
    }
}

fn main() {
    let port = PortNumber::try_from(8080);
    println!("{:?}", port); // Ok(PortNumber(8080))

    let bad = PortNumber::try_from(-1);
    println!("{:?}", bad); // Err("Port cannot be negative: -1")

    let too_big = PortNumber::try_from(70000);
    println!("{:?}", too_big); // Err("Port too large: 70000")

    // Works with ? in functions returning Result
    // let p = PortNumber::try_from(user_input)?;
}
```

`TryFrom` gives you `TryInto` for free, just like `From` gives you `Into`.

---

## Standard Library Conversions You Should Know

The stdlib already implements `From` in tons of places:

```rust
fn main() {
    // String conversions
    let s: String = String::from("hello");     // &str → String
    let s: String = "hello".to_string();       // same thing via ToString
    let s: String = "hello".into();            // &str → String via Into

    // Number widening — always succeeds
    let big: i64 = i32::from(42i32);           // wait, that's wrong
    let big: i64 = 42i32.into();               // i32 → i64 ✓
    let big = i64::from(42i32);                // i32 → i64 ✓

    // Vec from array
    let v: Vec<i32> = Vec::from([1, 2, 3]);

    // PathBuf from String
    let path = std::path::PathBuf::from("/tmp/data.txt");

    // Box from value
    let boxed: Box<i32> = Box::from(42);

    println!("{}, {:?}, {:?}, {:?}", s, v, path, boxed);
}
```

---

## The `into()` Inference Pattern

One thing that trips people up: `.into()` needs the compiler to know what type you're converting *to*. It figures this out from context — variable type, function parameter type, or return type.

```rust
fn takes_string(s: String) {
    println!("{}", s);
}

fn main() {
    // Context from variable type
    let s: String = "hello".into();

    // Context from function parameter
    takes_string("hello".into());

    // Context from return type
    fn make_greeting() -> String {
        "hello world".into()
    }

    // This WON'T work — no context for the compiler
    // let s = "hello".into(); // ERROR: type annotations needed

    println!("{}", make_greeting());
}
```

If the compiler can't figure out the target type, it'll ask you to annotate. That's fine — just add a type annotation and move on.

---

## My Rules for `From`/`Into`

1. **Implement `From`, not `Into`.** You get `Into` for free.
2. **Use `impl Into<T>` in function parameters** to accept multiple source types.
3. **Implement `From` for error types** to make `?` work with your custom errors.
4. **Use `TryFrom` when conversion can fail** — don't panic in `From`.
5. **Don't implement `From` for lossy conversions.** If converting `f64` to `i32` truncates, that should be an explicit `as` cast or `TryFrom`, not a silent `From`.

The guiding principle: `From` should be unsurprising. If someone writes `let x: MyType = value.into()`, the result should be obvious and lossless. If there's any ambiguity or potential for data loss, make it explicit with a named method or `TryFrom`.

---

## Key Takeaways

- `From<A> for B` means "I can infallibly create a B from an A." Implement this, and you get `Into<B> for A` for free.
- `TryFrom` is for fallible conversions — returns `Result`.
- `From` implementations enable automatic error conversion with `?`.
- Use `impl Into<T>` in function parameters for flexible APIs.
- `From` should never panic or lose data — use `TryFrom` or explicit methods for those cases.
