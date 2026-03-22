---
title: "Lesson 3: Macro Pattern Matching — Repetition, fragments, and captures"
author: Atharva Pandey
series: "Rust Macros & Metaprogramming"
lesson: 3
keywords: [Rust, rust, macros, pattern matching, macro_rules, repetition, fragments, token trees]
tags: [Rust tutorial, rust, macros, metaprogramming]
date: '2025-02-10T08:30:00.000Z'
---

There's a point when writing declarative macros where the syntax stops feeling like Rust and starts feeling like regex for code. You're stacking repetition operators, nesting captures inside captures, and using `tt` munching to parse things the macro system was never designed to parse. It's weird, it's powerful, and once it clicks, you'll wonder why you ever wrote boilerplate by hand.

## Repetition Operators Deep Dive

We touched on repetition in the last lesson. Now let's get into the mechanics that actually matter when you're building non-trivial macros.

The three operators:

- `$( ... )*` — zero or more
- `$( ... )+` — one or more
- `$( ... )?` — zero or one (optional)

The key rule: **every metavariable captured inside a repetition must be used inside a repetition of the same depth in the expansion.** If you capture `$x` inside a `$( ... )*`, you must use `$x` inside a `$( ... )*` in the expansion. The compiler enforces this.

```rust
macro_rules! list_items {
    ($($item:expr),*) => {
        // CORRECT: $item used inside repetition
        $( println!("- {}", $item); )*
    };
}

fn main() {
    list_items!("apple", "banana", "cherry");
}
```

What happens if you try to use `$item` outside the repetition? Compile error. The macro system doesn't know which of the captured values you want.

### Nested Repetition

This is where things get interesting. You can nest repetitions, and each level gets its own set of captured variables:

```rust
macro_rules! matrix {
    ( $( [ $($val:expr),* ] ),* ) => {
        vec![ $( vec![ $($val),* ] ),* ]
    };
}

fn main() {
    let m = matrix![
        [1, 2, 3],
        [4, 5, 6],
        [7, 8, 9]
    ];

    for row in &m {
        println!("{:?}", row);
    }
}
```

The outer `$( ... ),*` iterates over rows. The inner `$( ... ),*` iterates over values within each row. In the expansion, the nesting mirrors the capture nesting — `vec![ $( vec![ $($val),* ] ),* ]` produces a `Vec<Vec<_>>`.

### Separators

Separators go between the closing `)` and the repetition operator. They can be any single token except delimiters and `$`:

```rust
macro_rules! joined {
    // comma-separated
    (comma: $($x:expr),*) => { vec![$($x),*] };
    // semicolon-separated
    (semi: $($x:expr);*) => { vec![$($x),*] };
    // pipe-separated
    (pipe: $($x:expr)|*) => { vec![$($x),*] };
    // no separator
    (raw: $($x:expr)*) => { vec![$($x),*] };
}

fn main() {
    let a = joined!(comma: 1, 2, 3);
    let b = joined!(semi: 4; 5; 6);
    let c = joined!(pipe: 7 | 8 | 9);
    println!("{:?} {:?} {:?}", a, b, c);
}
```

The separator in the pattern determines what you write at the call site. The separator in the expansion can be different. This lets you define DSL-like syntax — accept `|` as input but generate comma-separated code.

## Advanced Fragment Specifiers

### The `tt` Escape Hatch

When you don't know what kind of syntax you'll receive, or when the other fragment specifiers are too restrictive, use `tt`:

```rust
macro_rules! pass_through {
    ($($tokens:tt)*) => {
        $($tokens)*
    };
}

fn main() {
    pass_through! {
        let x = 42;
        println!("x = {}", x);
    }
}
```

`$($tokens:tt)*` captures *everything* as a sequence of token trees. This is the most flexible pattern and forms the basis of "tt munching" — a technique we'll get to shortly.

### `ident` for Name Generation

`ident` captures identifiers, which lets you generate named items:

```rust
macro_rules! make_functions {
    ($($name:ident => $val:expr),* $(,)?) => {
        $(
            fn $name() -> i32 {
                $val
            }
        )*
    };
}

make_functions! {
    get_zero => 0,
    get_one => 1,
    get_answer => 42,
}

fn main() {
    println!("{} {} {}", get_zero(), get_one(), get_answer());
}
```

Each identifier becomes a function name. You can also use captured idents inside string formatting with `stringify!`:

```rust
macro_rules! named_value {
    ($name:ident = $val:expr) => {
        println!("{} = {:?}", stringify!($name), $val);
    };
}

fn main() {
    named_value!(x = 42);       // x = 42
    named_value!(name = "Atharva"); // name = "Atharva"
}
```

### `path` and `ty` for Type Manipulation

```rust
macro_rules! impl_display {
    ($t:ty, $fmt:expr) => {
        impl std::fmt::Display for $t {
            fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                write!(f, $fmt, self.0)
            }
        }
    };
}

struct Meters(f64);
struct Kilograms(f64);

impl_display!(Meters, "{} m");
impl_display!(Kilograms, "{} kg");

fn main() {
    println!("{}", Meters(3.14));    // 3.14 m
    println!("{}", Kilograms(75.0)); // 75 kg
}
```

The `ty` specifier captures full type paths including generics. `path` captures module paths without generic arguments.

## Token Tree Munching

Here's the technique that separates simple macros from powerful ones. TT munching processes input one token (or group) at a time using recursive macro calls:

```rust
macro_rules! count {
    () => { 0usize };
    ($head:tt $($tail:tt)*) => {
        1usize + count!($($tail)*)
    };
}

fn main() {
    let n = count!(a b c d e);
    println!("count: {}", n); // count: 5
}
```

Each recursive call peels off one token tree from the front and processes the rest. The base case (empty input) returns 0. Each recursive step adds 1.

This works but it's not great for large inputs — each level of recursion is a macro expansion, and Rust has a default recursion limit of 128. You can raise it with `#![recursion_limit = "256"]`, but that's a code smell.

A more efficient counting technique uses nested repetition:

```rust
macro_rules! count_fast {
    ($($x:tt)*) => {
        <[()]>::len(&[$(count_fast!(@replace $x ())),*])
    };
    (@replace $_t:tt $sub:expr) => { $sub };
}

fn main() {
    let n = count_fast!(a b c d e f g h i j);
    println!("count: {}", n); // count: 10
}
```

The `@replace` arm is an internal rule — a pattern that starts with a literal `@` token to distinguish it from the public API. This is a common convention for macro-internal dispatch.

## Internal Rules with `@`

When macros get complex, you often need helper rules that shouldn't be called directly by users. The convention is to prefix them with `@`:

```rust
macro_rules! csv_row {
    // Public API
    ($($field:expr),* $(,)?) => {
        csv_row!(@build String::new(), $($field),*)
    };
    // Internal: build the string incrementally
    (@build $acc:expr, $head:expr, $($tail:expr),+) => {
        csv_row!(@build format!("{},{}", $acc, $head), $($tail),+)
    };
    (@build $acc:expr, $last:expr) => {
        format!("{},{}", $acc, $last)
    };
    (@build $acc:expr,) => {
        $acc
    };
}

fn main() {
    let row = csv_row!("Alice", 30, "alice@example.com");
    println!("{}", row); // ,Alice,30,alice@example.com
}
```

The `@build` rules are implementation details. Users call `csv_row!(...)` and the macro internally dispatches to `@build` for recursive processing.

## Matching Specific Tokens

Macros can match literal tokens, not just captured variables. This lets you create keyword-based DSLs:

```rust
macro_rules! query {
    (SELECT $($field:ident),+ FROM $table:ident WHERE $col:ident = $val:expr) => {
        {
            let fields = vec![$(stringify!($field)),+];
            let table = stringify!($table);
            let column = stringify!($col);
            format!(
                "SELECT {} FROM {} WHERE {} = '{}'",
                fields.join(", "),
                table,
                column,
                $val,
            )
        }
    };
}

fn main() {
    let sql = query!(SELECT name, email, age FROM users WHERE id = 42);
    println!("{}", sql);
    // SELECT name, email, age FROM users WHERE id = '42'
}
```

`SELECT`, `FROM`, and `WHERE` are matched as literal tokens — if the call site doesn't include them in exactly those positions, the macro won't match. The identifiers between them get captured.

Is this a good idea? Usually not — it's fragile and hard to extend. But it demonstrates what the pattern matching system can do, and production crates like `sqlx` use similar techniques (via proc macros) to parse SQL at compile time.

## Combining Multiple Repetitions

Sometimes you need to capture separate lists and combine them:

```rust
macro_rules! zip_print {
    ([$($a:expr),*], [$($b:expr),*]) => {
        {
            let left = vec![$($a),*];
            let right = vec![$($b),*];
            for (l, r) in left.iter().zip(right.iter()) {
                println!("{} -> {}", l, r);
            }
        }
    };
}

fn main() {
    zip_print!(
        ["alice", "bob", "carol"],
        [95, 87, 92]
    );
}
```

Each `$( ... ),*` is independent. They don't need to have the same number of elements (the `zip` will stop at the shorter one). This pattern shows up when you need to accept structured input with multiple distinct sections.

## Recursive Macro Patterns

Beyond simple tt munching, recursion lets you build accumulator patterns:

```rust
macro_rules! reverse {
    // Base case: empty input, output the accumulator
    (@acc [$($acc:tt)*]) => {
        vec![$($acc),*]
    };
    // Recursive case: move head to front of accumulator
    (@acc [$($acc:tt)*] $head:tt $($tail:tt)*) => {
        reverse!(@acc [$head $($acc)*] $($tail)*)
    };
    // Entry point
    ($($all:tt)*) => {
        reverse!(@acc [] $($all)*)
    };
}

fn main() {
    let reversed = reverse!(1 2 3 4 5);
    println!("{:?}", reversed); // [5, 4, 3, 2, 1]
}
```

The accumulator `[$($acc:tt)*]` builds up results as the macro recurses. The `@acc` internal rule handles the recursion, and the public entry point bootstraps it with an empty accumulator.

## Practical Pattern: Enum with Methods

Here's a pattern I use regularly — generating an enum with `Display`, `FromStr`, and a list of all variants:

```rust
macro_rules! string_enum {
    ($name:ident { $($variant:ident),* $(,)? }) => {
        #[derive(Debug, Clone, Copy, PartialEq, Eq)]
        enum $name {
            $($variant),*
        }

        impl $name {
            fn all() -> &'static [Self] {
                &[$(Self::$variant),*]
            }
        }

        impl std::fmt::Display for $name {
            fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                match self {
                    $(Self::$variant => write!(f, "{}", stringify!($variant)),)*
                }
            }
        }

        impl std::str::FromStr for $name {
            type Err = String;
            fn from_str(s: &str) -> Result<Self, String> {
                match s {
                    $(stringify!($variant) => Ok(Self::$variant),)*
                    _ => Err(format!("unknown variant: {}", s)),
                }
            }
        }
    };
}

string_enum!(Color { Red, Green, Blue, Yellow });

fn main() {
    // Display
    println!("{}", Color::Red); // Red

    // FromStr
    let c: Color = "Blue".parse().unwrap();
    println!("{:?}", c); // Blue

    // All variants
    for color in Color::all() {
        println!("  {}", color);
    }
}
```

One macro call generates the enum, three trait implementations, and a utility method. Adding a variant means typing one word. This is the kind of macro that pays for itself on the first use.

## What Declarative Macros Can't Do

Before you try to build everything with `macro_rules!`, here's what won't work:

- **Generating new identifiers.** You can't concatenate identifiers. If you capture `foo` and want to create `foo_handler`, you're stuck. (There's an unstable `concat_idents!` macro, but it's been unstable for years.)
- **Complex parsing.** Pattern matching is all-or-nothing per arm. You can't do partial matches or backtrack.
- **Accessing type information.** Macros see tokens, not types. You can't branch based on whether something is a `String` or an `i32`.
- **External data.** No reading files, no network access, no environment beyond `env!()`.

When you hit these limits, that's when you graduate to procedural macros — which we'll start in lesson 6.

Next lesson: macro hygiene. Why your macros might silently shadow variables, how scoping works inside expansions, and the naming pitfalls that catch everyone at least once.
