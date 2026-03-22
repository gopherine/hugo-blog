---
title: "Lesson 3: Hello, World — Anatomy of a Rust program"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 3
keywords: [Rust, rust, hello world, main function, println, rust basics, rust syntax]
tags: [Rust tutorial, rust, beginner]
date: '2024-03-09T11:00:00.000Z'
---

The first program I ever wrote was in BASIC on a Commodore 64 emulator. Took me twenty minutes to figure out why PRINT didn't work (I was typing PIRNT). Rust's version of Hello World looks simple — four lines — but there's a surprising amount of language design packed into those four lines.

## The Program

```rust
fn main() {
    println!("Hello, world!");
}
```

That's it. Three lines if you're counting. But each piece tells you something about how Rust thinks.

## fn — Function Declaration

```rust
fn main() {
```

`fn` declares a function. Short, clear, no ambiguity. Rust favors terse keywords — `fn` instead of `function`, `let` instead of `var`, `pub` instead of `public`. You'll type these thousands of times; brevity matters.

`main` is special. It's the entry point of every Rust binary. When you run your program, execution starts here. Every binary crate must have exactly one `main` function, and it must live at the top level of `src/main.rs`.

The empty parentheses `()` mean main takes no arguments. (There *is* a way to access command-line arguments, but it's not through main's parameters — we'll cover that later.)

No return type is specified here, which means main returns `()` — the unit type. Think of it as Rust's version of `void`, except it's an actual value, not the absence of one. You can also make main return `Result`, which is useful for error handling. But not yet.

## Curly Braces

```rust
{
    // body
}
```

Rust uses curly braces for blocks. Not indentation like Python. Not `end` like Ruby. Braces. If you're coming from C, C++, Java, Go, or JavaScript, this feels natural. If you're coming from Python, you'll adapt in about ten minutes.

One thing that *is* different: blocks are expressions in Rust. They produce a value. This is a much bigger deal than it sounds, and we'll explore it in the functions lesson.

## println! — It's a Macro

```rust
println!("Hello, world!");
```

See that exclamation mark? `println!` is not a function — it's a macro. That `!` is the giveaway. In Rust, anything with `!` at the end of its name is a macro invocation.

Why a macro instead of a function? Because `println!` needs to do things that functions can't:

1. **Variable number of arguments.** Rust functions have a fixed number of parameters. `println!` can take any number of arguments: `println!("{} + {} = {}", a, b, a + b)`. Macros can accept varying numbers of arguments; functions can't (without workarounds).

2. **Format string checking at compile time.** If you write `println!("{} {}", x)` — two placeholders but only one argument — the compiler catches it. A regular function couldn't do this.

3. **Zero-cost formatting.** The macro expands to optimized code at compile time. No runtime parsing of format strings.

You don't need to understand macros deeply right now. Just remember: `!` means macro, and macros are more powerful (but also more complex) than functions.

### Format Strings

The format string syntax is worth knowing:

```rust
fn main() {
    let name = "Atharva";
    let age = 28;

    // Basic substitution
    println!("Name: {}, Age: {}", name, age);

    // Named parameters
    println!("Name: {name}, Age: {age}");

    // Debug formatting
    println!("Debug: {:?}", (name, age));

    // Padding and alignment
    println!("{:<10} | {:>5}", "left", "right");

    // Number formatting
    println!("Hex: {:x}, Binary: {:b}, Octal: {:o}", 255, 255, 255);

    // Precision for floats
    println!("Pi: {:.4}", 3.14159265);
}
```

Output:

```
Name: Atharva, Age: 28
Name: Atharva, Age: 28
Debug: ("Atharva", 28)
left       | right
Hex: ff, Binary: 11111111, Octal: 377
Pi: 3.1416
```

The `{name}` syntax (called "captured identifiers") was added in Rust 1.58. Use it — it's cleaner than positional `{}` placeholders when you have named variables.

The `{:?}` format is the Debug format. It works for any type that implements the `Debug` trait. You'll use this constantly for debugging. There's also `{:#?}` for pretty-printed debug output — invaluable when inspecting nested structures.

## Semicolons

```rust
println!("Hello, world!");
```

That semicolon at the end is significant. In Rust, semicolons turn expressions into statements. An expression produces a value. A statement does not. This distinction matters for function return values and block expressions, but for now, the simple rule is: most lines end with a semicolon.

The exception — and this trips up every beginner — is the last expression in a function body, which you might want to return *without* a semicolon. More on this in Lesson 5.

## Comments

Rust has three kinds of comments:

```rust
fn main() {
    // Line comment — most common, use these for everything

    /* Block comment — rarely used in practice,
       but they exist and they nest properly
       (unlike C, where they don't) */

    /// Doc comment — generates documentation.
    /// Put these above functions, structs, enums.
    /// They support Markdown!
}
```

Doc comments (`///`) are special. They're processed by `cargo doc` to generate HTML documentation. Every public function, struct, and module in your crate should have doc comments. This isn't just a convention — it's how the entire Rust ecosystem documents itself.

There's also `//!` for module-level documentation, which goes at the top of a file:

```rust
//! This module handles user authentication.
//!
//! It provides functions for login, logout, and session management.
```

## A Slightly Bigger Program

Let's write something that demonstrates a few more features:

```rust
fn main() {
    // Variables
    let greeting = "Hello";
    let target = "world";

    // String formatting
    let message = format!("{greeting}, {target}!");
    println!("{message}");

    // Numbers
    let x = 42;
    let y = 3.14;
    println!("Integer: {x}, Float: {y}");

    // Boolean
    let rust_is_great = true;
    println!("Rust is great: {rust_is_great}");

    // Multiple lines of output
    println!("---");
    println!("This program compiled successfully.");
    println!("That means the types are correct,");
    println!("the syntax is valid,");
    println!("and there are no memory safety issues.");
}
```

Run this with `cargo run`. Everything should compile and print cleanly. If it does, you understand the basic structure of a Rust program.

## Things That Are Different From Other Languages

A few things that might surprise you, depending on your background:

**No header files.** If you're coming from C/C++, there's no `.h` file. The compiler analyzes the entire crate at once. Declarations and definitions live in the same place.

**No forward declarations.** You can call a function that's defined later in the file. Order doesn't matter within a module.

**No implicit type coercion.** Rust won't silently convert an integer to a float or a float to an integer. You have to be explicit. This prevents a whole class of subtle bugs.

**No null.** There's no `null`, `nil`, `None`, or `undefined` as a general-purpose "no value" marker. Rust uses `Option<T>` instead — but that's for Lesson 12.

**No exceptions.** Rust doesn't have try/catch. Errors are handled through return values using the `Result` type. Lesson 16 covers this in detail.

## The Compilation Model

When you run `cargo build`, here's what happens:

1. Cargo reads `Cargo.toml` to understand your project
2. It calls `rustc` (the Rust compiler) on your source files
3. `rustc` parses your code into an AST
4. Type checking and borrow checking happen
5. The checked code is lowered to MIR (Mid-level IR), then to LLVM IR
6. LLVM optimizes and generates native machine code
7. A native binary lands in `target/debug/` (or `target/release/`)

The key insight: steps 4 and 5 are where Rust's magic happens. The borrow checker runs *before* code generation. If it finds a problem, no binary is produced. Your code either compiles and is memory-safe, or it doesn't compile at all. There's no middle ground.

This is fundamentally different from C, where the compiler happily generates code that segfaults at runtime. Rust shifts the pain from runtime debugging to compile-time errors. Compile-time errors are annoying. Runtime crashes at 3 AM are worse.

## What's Next

We've got a working program and we understand what each piece does. Next lesson, we'll dig into variables — `let`, `let mut`, type inference, and the primitive types that form Rust's foundation.

Run the programs from this lesson. Modify them. Break them on purpose and read the error messages. The Rust compiler writes *excellent* error messages — some of the best in any programming language — and getting comfortable reading them early will save you enormous amounts of time later.
