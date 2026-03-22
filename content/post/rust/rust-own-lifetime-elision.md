---
title: "Lesson 6: Lifetime Elision Rules — Why You Usually Don't Write 'a"
author: Atharva Pandey
series: "Rust Ownership & Lifetimes Masterclass"
lesson: 6
keywords: [Rust, rust, ownership, lifetimes, lifetime elision, elision rules]
tags: [Rust tutorial, rust, ownership, lifetimes]
date: '2024-05-25T19:08:00.000Z'
---

If lifetimes are so important, why don't you see `'a` plastered all over most Rust code? Because the compiler is smart enough to figure it out 90% of the time.

Early Rust required explicit lifetime annotations on every function that dealt with references. The community quickly realized that the same patterns showed up over and over. So the Rust team codified those patterns into "elision rules" — three rules that let the compiler infer lifetimes automatically.

## The Three Rules

The compiler applies these rules in order to function signatures. If it can assign lifetimes to all output references after applying all three rules, you don't need to write them. If it can't, it asks you to.

### Rule 1: Each input reference gets its own lifetime parameter

```rust
// What you write:
fn foo(x: &str) -> ...
// What the compiler sees:
fn foo<'a>(x: &'a str) -> ...

// What you write:
fn bar(x: &str, y: &str) -> ...
// What the compiler sees:
fn bar<'a, 'b>(x: &'a str, y: &'b str) -> ...
```

Every reference parameter gets a distinct lifetime. This is just the starting point — the compiler then applies rules 2 and 3 to figure out the output lifetimes.

### Rule 2: If there's exactly one input lifetime, all output references get that lifetime

```rust
// What you write:
fn first_word(s: &str) -> &str
// After rule 1:
fn first_word<'a>(s: &'a str) -> &str
// After rule 2 (one input lifetime → output gets it):
fn first_word<'a>(s: &'a str) -> &'a str

// Done! No annotation needed.
```

This covers the vast majority of functions. One input reference, one output reference — the output obviously borrows from the input.

### Rule 3: If one of the inputs is `&self` or `&mut self`, the output gets `self`'s lifetime

```rust
impl MyStruct {
    // What you write:
    fn name(&self) -> &str
    // After rule 1:
    fn name<'a>(&'a self) -> &str
    // After rule 3 (method with &self → output gets self's lifetime):
    fn name<'a>(&'a self) -> &'a str

    // Done! No annotation needed.
}
```

This rule exists because methods almost always return references into `self`. It's the common case.

## When Elision Works

Most functions fall neatly into one of these patterns:

```rust
// Single reference in, reference out — Rule 2
fn trim(s: &str) -> &str {
    s.trim()
}

// Method returning reference into self — Rule 3
struct Config {
    name: String,
}

impl Config {
    fn name(&self) -> &str {
        &self.name
    }

    fn name_or_default(&self, _default: &str) -> &str {
        // Rule 3: output gets self's lifetime
        // _default's lifetime doesn't matter for the return
        &self.name
    }
}
```

No lifetime annotations anywhere. The compiler handles it.

## When Elision Fails

The rules fail when the compiler genuinely can't figure out the relationship:

```rust
// Two input references, non-method — rules can't determine output lifetime
// fn longest(x: &str, y: &str) -> &str  // ERROR

// Fix: annotate manually
fn longest<'a>(x: &'a str, y: &'a str) -> &'a str {
    if x.len() > y.len() { x } else { y }
}
```

After rule 1, the compiler has `fn longest<'a, 'b>(x: &'a str, y: &'b str) -> &str`. Rule 2 doesn't apply (two input lifetimes, not one). Rule 3 doesn't apply (no `self`). So it can't determine the output lifetime and asks you to annotate.

## Walking Through the Rules Step by Step

Here's a more complex example:

```rust
struct Document {
    title: String,
    body: String,
}

impl Document {
    // Let's trace the elision rules:
    // Written: fn summary(&self, max_len: &usize) -> &str
    // Rule 1: fn summary<'a, 'b>(&'a self, max_len: &'b usize) -> &str
    // Rule 2: doesn't apply (two input lifetimes)
    // Rule 3: applies (&self present) → output gets 'a
    // Result: fn summary<'a, 'b>(&'a self, max_len: &'b usize) -> &'a str

    fn summary(&self, max_len: &usize) -> &str {
        if self.body.len() > *max_len {
            &self.body[..*max_len]
        } else {
            &self.body
        }
    }
}
```

Rule 3 saves the day here. Without it, you'd need explicit annotations.

## Elision in Closures

Closures don't get lifetime elision the same way functions do. This sometimes forces you to use function pointers or explicit types:

```rust
fn main() {
    // This closure works fine — types are inferred
    let to_upper = |s: &str| -> String {
        s.to_uppercase()
    };

    // But returning a reference from a closure can be tricky
    let items = vec![String::from("hello"), String::from("world")];

    // This works because the closure's return borrows from the iterator
    let first_chars: Vec<&str> = items.iter().map(|s| &s[..1]).collect();
    println!("{:?}", first_chars);
}
```

## Elision in Trait Implementations

Trait methods follow the same elision rules:

```rust
trait Describable {
    fn describe(&self) -> &str;
    // Elided: fn describe<'a>(&'a self) -> &'a str
}

struct Widget {
    label: String,
}

impl Describable for Widget {
    fn describe(&self) -> &str {
        &self.label
    }
}
```

## Elision in Type Definitions

Struct definitions never get elision — you must always annotate lifetimes in structs:

```rust
// This won't compile — struct needs explicit lifetime
// struct Excerpt {
//     content: &str,  // ERROR: missing lifetime
// }

// Must annotate:
struct Excerpt<'a> {
    content: &'a str,
}
```

The reasoning: a function signature has clear inputs and outputs, so the compiler can apply rules. A struct definition has no such context — the compiler can't guess how long the referenced data should live.

## The "Just Add 'a" Trap

When elision fails, beginners often just slap `'a` on everything until it compiles:

```rust
// DON'T: meaningless annotation — constrains things unnecessarily
fn process<'a>(x: &'a str, y: &'a str, z: &'a str) -> &'a str {
    // now x, y, and z must ALL live as long as the return value
    // even if you only return from x
    x
}

// DO: only tie the lifetimes that actually relate
fn process<'a>(x: &'a str, y: &str, z: &str) -> &'a str {
    // only x needs to live as long as the return value
    x
}
```

Over-constraining lifetimes doesn't cause bugs, but it limits how callers can use your function. If `y` and `z` don't relate to the output, don't bind them with the same lifetime.

## Practical Heuristic

When in doubt, start without annotations. If the compiler complains:

1. Count your input references
2. If there's one input ref, the output borrows from it (Rule 2 should handle it — check for other issues)
3. If it's a method, the output borrows from `self` (Rule 3)
4. If there are multiple non-self input refs and you return a reference, ask: "which input does the output borrow from?" Annotate accordingly

The rules aren't magic. They're just pattern recognition that the compiler does for you. Understanding what's being inferred helps you reason about what you're doing when you need to write annotations manually.

Next up: lifetimes in structs — when your types hold references instead of owning data.
