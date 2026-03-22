---
title: "Lesson 6: Procedural Macros — The three kinds"
author: Atharva Pandey
series: "Rust Macros & Metaprogramming"
lesson: 6
keywords: [Rust, rust, macros, procedural macros, proc-macro, derive, attribute, function-like]
tags: [Rust tutorial, rust, macros, metaprogramming]
date: '2025-02-17T09:40:00.000Z'
---

The moment I realized `macro_rules!` couldn't generate new identifier names from captured inputs, I knew I needed something more powerful. I was trying to auto-generate a `_builder` suffix for struct names — take `Config` and produce `ConfigBuilder`. Declarative macros can't do string manipulation on identifiers. Period. That's what pushed me into procedural macros, and honestly, it felt like unlocking a completely different layer of the language.

## What Procedural Macros Actually Are

A procedural macro is a Rust function that runs at compile time. It receives a stream of tokens as input, does whatever processing it wants — parsing, analyzing, transforming — and returns a new stream of tokens as output. The compiler then compiles the returned tokens as if they were regular source code.

Unlike `macro_rules!` which matches patterns, proc macros execute arbitrary code. You can do string manipulation, read files, perform complex logic, even call external tools (though you probably shouldn't). They're real Rust programs, compiled separately and loaded as compiler plugins.

## The Three Kinds

Rust has exactly three types of procedural macros:

### 1. Derive Macros

You've already used these. Every time you write `#[derive(Debug)]` or `#[derive(Clone)]`, a derive macro runs. It receives the struct/enum definition and generates additional code — typically trait implementations.

```rust
#[derive(Debug, Clone, MyCustomTrait)]
struct User {
    name: String,
    age: u32,
}
```

Derive macros *add* code — they can't modify the original struct. The struct definition passes through unchanged, and the macro's output is appended alongside it.

### 2. Attribute Macros

These attach to items with `#[my_attribute]` syntax. Unlike derive macros, attribute macros *replace* the annotated item. They receive the item's tokens and must return the full replacement (typically the original item plus additional code).

```rust
#[route(GET, "/users")]
fn list_users() -> Vec<User> {
    // ...
}
```

Web frameworks like Actix and Rocket use attribute macros heavily. The `#[route(...)]` macro sees the function definition, generates routing logic, and emits both the original function and the routing registration code.

### 3. Function-Like Macros

These look like function calls with a `!`: `my_macro!(...)`. They're similar in appearance to `macro_rules!` macros but can do arbitrary processing. The input tokens can be anything — they don't need to be valid Rust.

```rust
let query = sql!(SELECT * FROM users WHERE age > 21);
```

The `sql!` macro receives the SQL text as tokens, validates it at compile time, and generates type-safe query code. The input isn't valid Rust syntax, but it doesn't need to be — the macro parses it however it wants.

## Project Structure

Here's the key constraint: **procedural macros must live in their own crate.** The crate must have `proc-macro = true` in its `Cargo.toml`. This is non-negotiable — it's how the compiler knows to load and execute the code at compile time.

A typical project structure:

```
my_project/
├── Cargo.toml
├── src/
│   └── main.rs
└── my_macros/
    ├── Cargo.toml
    └── src/
        └── lib.rs
```

The macro crate's `Cargo.toml`:

```toml
[package]
name = "my_macros"
version = "0.1.0"
edition = "2021"

[lib]
proc-macro = true

[dependencies]
syn = { version = "2", features = ["full"] }
quote = "1"
proc-macro2 = "1"
```

The main project's `Cargo.toml`:

```toml
[package]
name = "my_project"
version = "0.1.0"
edition = "2021"

[dependencies]
my_macros = { path = "./my_macros" }
```

If you're working with a workspace, you can add the macro crate as a workspace member:

```toml
[workspace]
members = [".", "my_macros"]
```

## The proc_macro Crate

Every proc macro function uses types from the `proc_macro` crate, which ships with the compiler. The main type is `TokenStream` — a sequence of tokens that represents source code.

```rust
use proc_macro::TokenStream;

#[proc_macro_derive(MyTrait)]
pub fn derive_my_trait(input: TokenStream) -> TokenStream {
    // input: the struct/enum definition
    // return: additional code to generate
    TokenStream::new() // empty — generates nothing
}
```

The function signature tells the compiler what kind of proc macro this is:

- `#[proc_macro_derive(Name)]` — derive macro
- `#[proc_macro_attribute]` — attribute macro
- `#[proc_macro]` — function-like macro

Each has a slightly different signature. We'll cover the details in the next three lessons.

## A Minimal Derive Macro

Let's build the simplest possible derive macro — one that implements a `HelloMacro` trait:

First, create the trait in a shared crate (or your main crate):

```rust
// In your main crate or a shared crate
pub trait HelloMacro {
    fn hello_macro();
}
```

Now the macro crate:

```rust
// my_macros/src/lib.rs
use proc_macro::TokenStream;

#[proc_macro_derive(HelloMacro)]
pub fn hello_macro_derive(input: TokenStream) -> TokenStream {
    let input_string = input.to_string();

    // Very crude parsing — just grab the struct name
    // In practice, use syn for this
    let name = input_string
        .split_whitespace()
        .find(|w| w.chars().next().map_or(false, |c| c.is_uppercase()))
        .unwrap_or("Unknown");

    // Remove any trailing characters like { or ;
    let name = name.trim_end_matches(|c: char| !c.is_alphanumeric());

    let output = format!(
        r#"
        impl HelloMacro for {name} {{
            fn hello_macro() {{
                println!("Hello from {{}}!", stringify!({name}));
            }}
        }}
        "#
    );

    output.parse().unwrap()
}
```

Using it:

```rust
use my_macros::HelloMacro;

trait HelloMacro {
    fn hello_macro();
}

#[derive(HelloMacro)]
struct Pancake;

fn main() {
    Pancake::hello_macro(); // Hello from Pancake!
}
```

This works but it's terrible. String-based token manipulation is fragile, hard to maintain, and produces awful error messages. This is why every serious proc macro uses `syn` and `quote` — which we'll cover in detail in lesson 10.

## A Minimal Attribute Macro

```rust
// my_macros/src/lib.rs
use proc_macro::TokenStream;

#[proc_macro_attribute]
pub fn log_call(attr: TokenStream, item: TokenStream) -> TokenStream {
    let item_str = item.to_string();

    // Extract function name (crude)
    let fn_name = item_str
        .split("fn ")
        .nth(1)
        .and_then(|s| s.split('(').next())
        .unwrap_or("unknown")
        .trim();

    let output = format!(
        r#"
        fn {fn_name}() {{
            println!("[LOG] calling {fn_name}");
            fn __inner() {{ {body} }}
            __inner()
        }}
        "#,
        body = item_str
            .split('{')
            .skip(1)
            .collect::<Vec<_>>()
            .join("{")
            .trim_end_matches('}'),
    );

    output.parse().unwrap_or_else(|_| item)
}
```

Usage:

```rust
use my_macros::log_call;

#[log_call]
fn do_stuff() {
    println!("doing stuff");
}

fn main() {
    do_stuff();
    // [LOG] calling do_stuff
    // doing stuff
}
```

Again — don't actually parse Rust this way. This is just to show the mechanics. `syn` handles all the parsing properly.

## A Minimal Function-Like Macro

```rust
// my_macros/src/lib.rs
use proc_macro::TokenStream;

#[proc_macro]
pub fn make_constant(input: TokenStream) -> TokenStream {
    let input_str = input.to_string();
    let parts: Vec<&str> = input_str.split('=').collect();

    if parts.len() != 2 {
        return "compile_error!(\"expected NAME = VALUE\");".parse().unwrap();
    }

    let name = parts[0].trim();
    let value = parts[1].trim();

    format!("const {}: i32 = {};", name, value)
        .parse()
        .unwrap()
}
```

Usage:

```rust
use my_macros::make_constant;

make_constant!(ANSWER = 42);
make_constant!(MAX_RETRIES = 3);

fn main() {
    println!("answer: {}, retries: {}", ANSWER, MAX_RETRIES);
}
```

## When to Use Which

**Derive macros** when you want to auto-implement a trait for structs/enums. This is the most common kind. If you find yourself writing the same `impl` block repeatedly with minor variations — that's a derive macro.

**Attribute macros** when you want to transform or augment arbitrary items — functions, structs, modules. Web frameworks use these for routing. Test frameworks use these for setup/teardown. Serialization libraries use these for field-level configuration.

**Function-like macros** when you want to parse custom syntax that isn't valid Rust. SQL queries, HTML templates, configuration DSLs. Also useful when you need the full power of proc macros but the invocation is a simple call rather than an annotation.

## The Cost of Proc Macros

Proc macros are powerful but they come with overhead:

**Compile time.** Each proc macro crate is compiled as a separate binary and loaded by the compiler. The macro code itself runs during compilation. Complex macros that parse large inputs add measurably to build times. This is why `serde` and `syn` show up in every "why is my Rust slow to compile" discussion.

**Separate crate requirement.** You can't define a proc macro in the same crate that uses it. This means an extra crate in your workspace, extra `Cargo.toml` maintenance, and a more complex project structure.

**Debugging difficulty.** When a proc macro generates bad code, the error messages reference generated code that doesn't exist in your source files. `cargo expand` helps, but the feedback loop is slower than with `macro_rules!`.

**Binary size.** Proc macro crates are compiled into the compiler's address space. They don't affect your final binary size (their code runs at compile time, not runtime), but they do add dependencies that affect compile time.

## The Essential Dependencies

Almost every proc macro crate uses these three dependencies:

- **`syn`** — parses Rust source code into a syntax tree. It understands Rust's grammar and gives you structured data to work with.
- **`quote`** — generates Rust source code from a template syntax. You write quasi-quoted Rust with `#variable` interpolation and it produces `TokenStream`s.
- **`proc-macro2`** — a bridge type that makes `TokenStream` work in unit tests and other contexts where the compiler's `proc_macro` crate isn't available.

We'll learn `syn` and `quote` properly in lesson 10. For now, just know they exist and that writing proc macros without them is like writing web apps without a framework — technically possible, practically painful.

## Setting Up Your First Proc Macro Crate

Here's the full setup sequence if you want to follow along:

```bash
# Create your project
cargo new macro_demo
cd macro_demo

# Create the proc macro crate
cargo new macro_demo_macros --lib

# Edit macro_demo_macros/Cargo.toml to add:
# [lib]
# proc-macro = true
#
# [dependencies]
# syn = { version = "2", features = ["full"] }
# quote = "1"

# Edit macro_demo/Cargo.toml to add:
# [dependencies]
# macro_demo_macros = { path = "./macro_demo_macros" }
```

For workspace setup, add a root `Cargo.toml`:

```toml
[workspace]
members = ["macro_demo", "macro_demo_macros"]
```

This structure will serve us for the next four lessons as we build real derive, attribute, and function-like macros.

Next up: derive macros in depth. We'll build a custom `#[derive(Builder)]` that generates the builder pattern automatically, using `syn` to parse the struct and `quote` to generate the implementation.
