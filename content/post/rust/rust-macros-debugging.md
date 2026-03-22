---
title: "Lesson 5: Debugging Macros — cargo-expand and trace_macros"
author: Atharva Pandey
series: "Rust Macros & Metaprogramming"
lesson: 5
keywords: [Rust, rust, macros, debugging, cargo-expand, trace_macros, macro expansion]
tags: [Rust tutorial, rust, macros, metaprogramming]
date: '2025-02-14T11:05:00.000Z'
---

You will write a macro that compiles, runs, and produces the wrong output. You'll stare at the macro definition, convinced it's correct. You'll re-read the pattern matching rules, check the repetition operators, verify the fragment specifiers — everything looks right. And then you'll expand the macro and realize it's generating something completely different from what you imagined. This has happened to me more times than I'm willing to admit.

Macro debugging is a different skill from regular debugging. You can't set breakpoints in macro expansion. You can't step through it. You have to *see* the generated code, and then reason backwards from there to figure out where the pattern matching went wrong.

## cargo-expand: Your Most Important Tool

`cargo-expand` is a Cargo subcommand that shows you the fully expanded source code of your project. Every macro call replaced with its output. This is, without question, the single most useful tool for macro development.

Install it:

```bash
cargo install cargo-expand
```

It requires a nightly toolchain (it uses the `--pretty=expanded` compiler flag internally):

```bash
rustup install nightly
```

You don't need to switch your project to nightly. `cargo-expand` uses nightly automatically.

### Basic Usage

Given this code:

```rust
macro_rules! make_adder {
    ($name:ident, $amount:expr) => {
        fn $name(x: i32) -> i32 {
            x + $amount
        }
    };
}

make_adder!(add_five, 5);
make_adder!(add_ten, 10);

fn main() {
    println!("{}", add_five(3));
    println!("{}", add_ten(3));
}
```

Running `cargo expand` produces:

```rust
#![feature(prelude_import)]
#[prelude_import]
use std::prelude::rust_2021::*;
#[macro_use]
extern crate std;

fn add_five(x: i32) -> i32 {
    x + 5
}
fn add_ten(x: i32) -> i32 {
    x + 10
}

fn main() {
    {
        ::std::io::_print(format_args!("{0}\n", add_five(3)));
    };
    {
        ::std::io::_print(format_args!("{0}\n", add_ten(3)));
    };
}
```

Now you can see exactly what your macro produced. The `make_adder!` calls are gone, replaced by the two function definitions. And `println!` expanded into `_print(format_args!(...))` calls.

### Expanding Specific Items

In larger projects, `cargo expand` dumps *everything*, which is overwhelming. You can narrow it:

```bash
# Expand only a specific module
cargo expand module_name

# Expand a specific function (requires it to be a module path)
cargo expand main
```

### Expanding in Tests

If your macros are used in tests:

```bash
cargo expand --tests
```

This expands test modules too, which is useful when your test helpers are macros.

## trace_macros!: Watching Expansion Step by Step

`trace_macros!` is a nightly-only built-in macro that prints each macro invocation and its result during compilation. It's noisier than `cargo expand` but shows you the *process*, not just the result.

```rust
#![feature(trace_macros)]

macro_rules! double {
    ($x:expr) => { $x * 2 };
}

macro_rules! quad {
    ($x:expr) => { double!(double!($x)) };
}

fn main() {
    trace_macros!(true);
    let val = quad!(5);
    trace_macros!(false);

    println!("{}", val);
}
```

Compile with nightly:

```bash
cargo +nightly run
```

The compiler output will include lines like:

```
note: trace_macro
  --> src/main.rs:12:15
   |
12 |     let val = quad!(5);
   |               ^^^^^^^^
   |
   = note: expanding `quad! { 5 }`
   = note: to `double!(double!(5))`
   = note: expanding `double! { double!(5) }`
   = note: to `double!(5) * 2`
   = note: expanding `double! { 5 }`
   = note: to `5 * 2`
```

You see each expansion step. `quad!(5)` becomes `double!(double!(5))`, then each `double!` expands in turn. This is invaluable for recursive macros where the expansion isn't obvious.

## log_syntax!: Printing Tokens at Compile Time

Another nightly tool. `log_syntax!` prints its arguments during compilation — it's `println!` for the compiler:

```rust
#![feature(log_syntax)]

macro_rules! debug_capture {
    ($($x:expr),*) => {
        log_syntax!("captured:", $($x),*);
        vec![$($x),*]
    };
}

fn main() {
    let v = debug_capture!(1, 2, 3);
    println!("{:?}", v);
}
```

During compilation, you'll see:

```
"captured:", 1, 2, 3
```

This shows you exactly what tokens the macro captured before expansion. Useful when you suspect the pattern matching is grabbing the wrong tokens.

## Debugging Strategies

### Strategy 1: Start Small, Expand Incrementally

Don't write a 50-line macro and then wonder why it doesn't work. Start with the simplest possible version:

```rust
// Step 1: Does the basic structure work?
macro_rules! my_macro {
    ($name:ident) => {
        struct $name;
    };
}
my_macro!(Foo);

// Step 2: Add one feature
macro_rules! my_macro {
    ($name:ident { $($field:ident : $ty:ty),* }) => {
        struct $name {
            $($field: $ty,)*
        }
    };
}
my_macro!(Foo { x: i32, y: f64 });

// Step 3: Add the next feature...
```

Expand after each step. Verify the output matches your expectations before adding complexity.

### Strategy 2: Test Pattern Matching in Isolation

When a macro has multiple arms, test each arm individually:

```rust
macro_rules! dispatch {
    (add $a:expr, $b:expr) => {
        println!("add: {}", $a + $b);
    };
    (mul $a:expr, $b:expr) => {
        println!("mul: {}", $a * $b);
    };
    ($op:ident $($rest:tt)*) => {
        compile_error!(concat!("unknown operation: ", stringify!($op)));
    };
}

fn main() {
    dispatch!(add 2, 3);   // test first arm
    dispatch!(mul 4, 5);   // test second arm
    // dispatch!(sub 1, 2); // test catch-all — compile error
}
```

The `compile_error!` macro is your friend here. It produces a clear compile-time error message. Use it as a catch-all arm during development to verify that unexpected inputs don't silently match a wrong pattern.

### Strategy 3: Use stringify! for Visibility

When you can't figure out what a captured metavariable contains, stringify it:

```rust
macro_rules! inspect {
    ($($tokens:tt)*) => {
        println!("tokens: {}", stringify!($($tokens)*));
    };
}

fn main() {
    inspect!(hello world 42 + x);
    // prints: tokens: hello world 42 + x
}
```

This shows you the raw tokens as a string. It won't tell you the fragment types, but it'll tell you if the macro is capturing what you think it's capturing.

### Strategy 4: Use Type Annotations to Catch Expansion Errors

If your macro generates expressions, force type checking by adding annotations:

```rust
macro_rules! maybe_broken {
    ($x:expr) => {
        {
            let result: i32 = $x * 2;  // explicit type forces a clear error
            result
        }
    };
}

fn main() {
    let a = maybe_broken!(5);
    println!("{}", a);
    // let b = maybe_broken!("oops");  // error: can't multiply &str by i32
}
```

Without the `i32` annotation, the error message might point at the macro internals. With it, you get a clear type mismatch at the capture point.

## Common Error Patterns and Fixes

### "no rules expected the token"

```
error: no rules expected the token `something`
```

This means none of your macro arms matched the input. Either:
- You're missing a pattern arm
- The separator in your repetition doesn't match the call site
- Token types don't match (you used `ident` but passed an expression)

Fix: add a catch-all arm with `compile_error!` to see exactly what's being passed:

```rust
macro_rules! my_macro {
    ($x:ident) => { /* ... */ };
    ($($rest:tt)*) => {
        compile_error!(concat!("unexpected input: ", stringify!($($rest)*)));
    };
}
```

### "variable is still repeating at this depth"

```
error: variable 'x' is still repeating at this depth
```

You captured `$x` inside a repetition but used it outside one (or at a different nesting depth):

```rust
// BROKEN
macro_rules! broken {
    ($($x:expr),*) => {
        println!("{}", $x);  // ERROR: $x is repeating, println isn't
    };
}

// FIXED
macro_rules! fixed {
    ($($x:expr),*) => {
        $(println!("{}", $x);)*  // $x used inside matching repetition
    };
}
```

### "recursion limit reached"

```
error: recursion limit reached while expanding `my_macro!`
```

Your recursive macro didn't terminate, or the input is too large for the default limit of 128. First, check your base case. If it's correct and you genuinely need more depth:

```rust
#![recursion_limit = "256"]
```

But consider refactoring to avoid deep recursion. The counting tricks from lesson 3 exist specifically for this reason.

### Misleading Span Information

Sometimes the compiler points at the wrong location — usually the macro definition instead of the call site. This happens because the generated code inherits spans from the macro definition.

When this happens, `cargo expand` is your only friend. Expand the code, find the error in the expanded output, then map it back to the macro definition.

## Building a Debug Macro

Here's a pattern I use during development — a debug wrapper that shows what's happening:

```rust
macro_rules! debug_macro {
    ($name:expr, $($body:tt)*) => {
        {
            #[cfg(debug_assertions)]
            eprintln!("[MACRO] {} expanding", $name);

            let __result = { $($body)* };

            #[cfg(debug_assertions)]
            eprintln!("[MACRO] {} = {:?}", $name, __result);

            __result
        }
    };
}

fn main() {
    let x = debug_macro!("calculation", {
        let a = 5;
        let b = 10;
        a + b
    });

    println!("result: {}", x);
}
```

In debug builds, this prints:
```
[MACRO] calculation expanding
[MACRO] calculation = 15
result: 15
```

In release builds, the `#[cfg(debug_assertions)]` lines are stripped entirely — zero overhead.

## IDE Support

Your editor can help too, though support varies:

**rust-analyzer** expands macros inline. In VS Code, you can use the "Expand macro recursively" command (usually `Ctrl+Shift+P` → "Expand macro"). This shows the expansion without leaving your editor.

**IntelliJ Rust** has similar functionality. Right-click a macro invocation and look for "Expand macro" in the context menu.

Both are less complete than `cargo expand` — they sometimes fail on complex macros or show partial expansions. But for quick checks during development, they're faster than switching to the terminal.

## A Systematic Debugging Workflow

When a macro isn't working, here's the process I follow:

1. **Read the error message carefully.** Rust's macro error messages have gotten much better. Often the fix is obvious from the message alone.

2. **Run `cargo expand`.** See the actual generated code. Most of the time, the bug is immediately visible in the expansion.

3. **Simplify the input.** Call the macro with the simplest possible arguments. Does it work? Add complexity until it breaks.

4. **Simplify the macro.** Comment out pattern arms and expansion code until you have a minimal version that either works or fails. Then add back piece by piece.

5. **Check pattern matching.** Use `compile_error!` catch-all arms and `stringify!` to verify what's being captured and which arm is matching.

6. **Check hygiene.** If the expansion looks correct but the behavior is wrong, you probably have a naming collision. Review the hygiene rules from lesson 4.

7. **Check paths.** Are all type and function references fully qualified? Missing `::std::` prefix is a common source of "cannot find" errors that only appear in certain call sites.

This process sounds methodical because it is. Macro debugging doesn't lend itself to intuition — you need to see the generated code. Every tool in this lesson is designed to make that generated code visible.

Next lesson: we leave `macro_rules!` behind and enter the world of procedural macros. Three kinds, more power, more complexity, and the ability to run arbitrary Rust code at compile time.
