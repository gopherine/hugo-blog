---
title: "Lesson 2: Declarative Macros — macro_rules! from zero"
author: Atharva Pandey
series: "Rust Macros & Metaprogramming"
lesson: 2
keywords: [Rust, rust, macros, macro_rules, declarative macros, metaprogramming]
tags: [Rust tutorial, rust, macros, metaprogramming]
date: '2025-02-07T14:45:00.000Z'
---

The first macro I ever wrote was a disaster. I wanted a shorthand for creating `HashMap`s — something like `map!{ "a" => 1, "b" => 2 }`. The macro compiled. The expansion was garbage. I didn't understand fragment specifiers, I didn't understand repetition, and I definitely didn't understand why the compiler kept telling me "unexpected token." Took me a full weekend to get it right. Let me save you that weekend.

## The Anatomy of macro_rules!

Every declarative macro has the same structure:

```rust
macro_rules! name {
    (pattern) => { expansion };
}
```

The `pattern` is what you write at the call site. The `expansion` is the code that gets generated. The macro system matches your input against the pattern, captures pieces of it, and substitutes them into the expansion. That's the whole idea.

Here's the simplest possible macro:

```rust
macro_rules! say_hello {
    () => {
        println!("hello");
    };
}

fn main() {
    say_hello!(); // expands to: println!("hello");
}
```

No inputs, no captures. `say_hello!()` gets replaced with `println!("hello")` before the compiler ever sees it. Not very useful, but it compiles and it runs.

## Capturing Inputs

Macros get interesting when you capture parts of the input. You use `$name:specifier` syntax to grab tokens:

```rust
macro_rules! greet {
    ($name:expr) => {
        println!("hello, {}", $name);
    };
}

fn main() {
    greet!("Atharva");         // hello, Atharva
    greet!(String::from("world")); // hello, world

    let user = "reader";
    greet!(user);              // hello, reader
}
```

`$name:expr` means "capture any expression and call it `$name`." In the expansion, `$name` gets replaced with whatever was captured.

## Fragment Specifiers

The `:expr` part is a *fragment specifier*. It tells the macro what kind of syntax to expect. Here are the ones you'll use constantly:

| Specifier | Matches | Example |
|-----------|---------|---------|
| `expr` | Any expression | `1 + 2`, `foo()`, `vec![1,2]` |
| `ty` | A type | `i32`, `Vec<String>`, `&str` |
| `ident` | An identifier | `foo`, `my_var`, `MyStruct` |
| `pat` | A pattern | `Some(x)`, `_`, `1..=5` |
| `stmt` | A statement | `let x = 5`, `x += 1` |
| `block` | A block | `{ println!("hi"); 42 }` |
| `item` | An item | `fn foo() {}`, `struct Bar;` |
| `literal` | A literal value | `42`, `"hello"`, `true` |
| `path` | A path | `std::collections::HashMap` |
| `tt` | A single token tree | literally anything |
| `meta` | Attribute content | `derive(Debug)`, `cfg(test)` |

The most important ones to memorize: `expr`, `ty`, `ident`, and `tt`. You'll use these in 90% of your macros.

`tt` deserves special attention. A "token tree" is either a single token (like `+` or `foo` or `42`) or a group of tokens inside matching delimiters (like `(a, b)` or `{ x + 1 }`). When you don't know what kind of syntax you'll receive, `tt` accepts anything. It's the escape hatch.

## Multiple Rules

Macros can have multiple matching arms, separated by semicolons. The macro system tries them top to bottom and uses the first match:

```rust
macro_rules! describe {
    ($val:expr) => {
        println!("value: {:?}", $val);
    };
    ($label:expr, $val:expr) => {
        println!("{}: {:?}", $label, $val);
    };
}

fn main() {
    describe!(42);             // value: 42
    describe!("score", 100);   // score: 100
}
```

This is how macros fake function overloading. Different call patterns dispatch to different expansions. The `println!` macro itself uses this — it has separate arms for the format-string-only case and the format-with-arguments case.

Order matters. If you put a more general pattern before a specific one, the specific one will never match:

```rust
macro_rules! bad_order {
    ($($tt:tt)*) => { "catch-all matched" };
    ($x:expr) => { "specific matched" }; // dead arm — never reached
}
```

Always put the most specific patterns first.

## Your First Useful Macro: HashMap Literal

Let's build that `map!` macro I failed at years ago. We want this syntax:

```rust
let scores = map!{
    "alice" => 95,
    "bob" => 87,
    "carol" => 92,
};
```

Here's the macro:

```rust
use std::collections::HashMap;

macro_rules! map {
    ($($key:expr => $value:expr),* $(,)?) => {
        {
            let mut m = HashMap::new();
            $(m.insert($key, $value);)*
            m
        }
    };
}

fn main() {
    let scores = map!{
        "alice" => 95,
        "bob" => 87,
        "carol" => 92,
    };

    for (name, score) in &scores {
        println!("{name}: {score}");
    }
}
```

Let me break down every piece.

`$($key:expr => $value:expr),*` — this is a repetition. The `$( ... ),*` means "match this pattern zero or more times, separated by commas." Inside the repetition, `$key:expr => $value:expr` captures a key expression, the literal `=>` token, and a value expression.

`$(,)?` — this handles the optional trailing comma. The `?` means "zero or one times." Without it, `map!{ "a" => 1, }` would fail because of the trailing comma after the last pair.

In the expansion, `$(m.insert($key, $value);)*` repeats the insert call once for each captured key-value pair. The `*` mirrors the `*` in the pattern.

The outer `{ ... }` block makes the whole expansion an expression that returns `m`.

## Repetition in Detail

Repetition is the core power of declarative macros. The syntax:

```
$( content ),*    zero or more, comma-separated
$( content ),+    one or more, comma-separated
$( content )?     zero or one (optional)
$( content );*    zero or more, semicolon-separated
$( content )*     zero or more, no separator
```

The separator (`,`, `;`, or nothing) goes between the closing `)` and the `*`/`+`.

Here's a macro that generates struct definitions:

```rust
macro_rules! define_structs {
    ($($name:ident { $($field:ident : $ty:ty),* $(,)? })*) => {
        $(
            #[derive(Debug)]
            struct $name {
                $($field: $ty,)*
            }
        )*
    };
}

define_structs! {
    Point { x: f64, y: f64 }
    Color { r: u8, g: u8, b: u8, a: u8 }
}

fn main() {
    let p = Point { x: 1.0, y: 2.0 };
    let c = Color { r: 255, g: 128, b: 0, a: 255 };
    println!("{:?}", p);
    println!("{:?}", c);
}
```

Nested repetition: the outer `$( ... )*` repeats per struct, the inner `$( ... ),*` repeats per field. Each level of `$()` creates its own scope of captured variables. `$name` is bound once per struct. `$field` and `$ty` are bound once per field within each struct.

## Invocation Brackets

You can call macros with `()`, `[]`, or `{}`:

```rust
macro_rules! my_macro {
    ($x:expr) => { $x + 1 };
}

fn main() {
    let a = my_macro!(5);   // parentheses
    let b = my_macro![5];   // brackets
    let c = my_macro!{5};   // braces
    assert_eq!(a, b);
    assert_eq!(b, c);
}
```

They all do the same thing. By convention:
- `()` for function-like calls: `println!()`, `assert!()`
- `[]` for collection-like things: `vec![]`
- `{}` for block-like things: `map!{}`

This is just convention — the compiler doesn't care.

## A Practical Example: Builder Pattern

Here's something I've actually used in production. A macro that generates builder structs:

```rust
macro_rules! builder {
    ($name:ident { $($field:ident : $ty:ty),* $(,)? }) => {
        #[derive(Debug, Default)]
        struct $name {
            $($field: Option<$ty>,)*
        }

        impl $name {
            fn new() -> Self {
                Self::default()
            }

            $(
                fn $field(mut self, value: $ty) -> Self {
                    self.$field = Some(value);
                    self
                }
            )*
        }
    };
}

builder!(Config {
    host: String,
    port: u16,
    debug: bool,
});

fn main() {
    let cfg = Config::new()
        .host("localhost".into())
        .port(8080)
        .debug(true);

    println!("{:?}", cfg);
    // Config { host: Some("localhost"), port: Some(8080), debug: Some(true) }
}
```

One macro invocation generates the struct with `Option`-wrapped fields, a constructor, and a fluent setter for every field. Adding a new field to the config means adding one line, not three method implementations.

## Common Pitfalls

A few things that tripped me up early on.

**Expressions vs. statements.** If your expansion contains `let` bindings followed by an expression, you need a block:

```rust
macro_rules! double {
    ($x:expr) => {
        {
            let val = $x;
            val * 2
        }
    };
}
```

Without the outer `{}`, the `let` and the multiplication would be separate statements, and the macro couldn't be used as an expression.

**Comma handling.** Trailing commas trip up macros constantly. Always add `$(,)?` at the end of your repetition patterns unless you have a specific reason not to.

**Type ambiguity.** Once something is captured as `$x:expr`, the macro system treats it as an opaque expression. You can't further parse it or decompose it. If you need to match specific structure within an expression, use `tt` and match more explicitly.

## Exporting Macros

By default, macros are only visible in the module where they're defined (and child modules, if defined before the child). To make a macro available across your crate:

```rust
// In lib.rs or any module
#[macro_export]
macro_rules! my_macro {
    () => { 42 };
}
```

`#[macro_export]` puts the macro at the crate root, regardless of where you define it. External crates can then use it after adding your crate as a dependency.

Within the same crate, there's a subtlety: `macro_rules!` macros are available only *after* their definition in the source order. If module A defines a macro and module B uses it, B must come after A in your `mod` declarations.

```rust
// lib.rs
mod macros;  // defines the macros
mod utils;   // can use macros from above
mod core;    // can also use them
```

This ordering requirement is one of the reasons people put all their macros in a dedicated module at the top.

## Macro Debugging Preview

When your macro doesn't work (and it won't, at first), you need to see what it expands to. The fastest way:

```bash
cargo install cargo-expand
cargo expand
```

This shows you the fully expanded source code — every macro replaced with its output. We'll cover debugging techniques properly in lesson 5, but knowing `cargo expand` exists will save you sanity in the meantime.

## Wrapping Up

Declarative macros are pattern matchers for syntax. You define rules — "when you see this pattern, produce this code" — and the compiler handles the rest. The core concepts: fragment specifiers tell the macro what to capture, repetitions handle variable-length inputs, and multiple arms provide overloaded call signatures.

Next lesson we'll go deeper on pattern matching within macros — the repetition combinators, nested captures, and the tricks that make complex macros work.
