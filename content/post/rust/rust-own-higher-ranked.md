---
title: "Lesson 9: Higher-Ranked Trait Bounds — for<'a> Explained"
author: Atharva Pandey
series: "Rust Ownership & Lifetimes Masterclass"
lesson: 9
keywords: [Rust, rust, ownership, lifetimes, HRTB, higher-ranked trait bounds, for lifetime]
tags: [Rust tutorial, rust, ownership, lifetimes]
date: '2024-05-31T21:15:00.000Z'
---

If regular lifetime annotations are Rust's intermediate boss, `for<'a>` is the final boss. Higher-Ranked Trait Bounds (HRTBs) look terrifying in signatures, but they solve a very specific and real problem.

I avoided understanding HRTBs for over a year. Then I tried to write a function that takes a closure accepting references with *any* lifetime, and suddenly I had no choice. Let me save you that year.

## The Problem

Say you want a function that accepts a closure. The closure takes a `&str` and returns something:

```rust
fn apply_to_name(f: impl Fn(&str) -> usize) -> usize {
    let name = String::from("Atharva");
    f(&name)
}

fn main() {
    let result = apply_to_name(|s| s.len());
    println!("Length: {}", result);
}
```

This works. But what's the lifetime of that `&str` parameter? The compiler uses elision and everything is fine. Now consider something trickier:

```rust
fn apply_to_both(f: impl Fn(&str) -> &str) -> String {
    let a = String::from("hello");
    let b = String::from("world");

    let result_a = f(&a);
    let result_b = f(&b);

    format!("{} {}", result_a, result_b)
}
```

What lifetime does the `&str` return value have? It borrows from the input — but the input could be `&a` or `&b`, which have different lifetimes. The closure needs to work for *any* lifetime, not a specific one.

This is exactly what `for<'a>` expresses.

## What for<'a> Means

```rust
fn apply_to_both(f: impl for<'a> Fn(&'a str) -> &'a str) -> String {
    let a = String::from("hello");
    let b = String::from("world");

    let result_a = f(&a);
    let result_b = f(&b);

    format!("{} {}", result_a, result_b)
}

fn main() {
    let result = apply_to_both(|s| s.trim());
    println!("{}", result);
}
```

`for<'a> Fn(&'a str) -> &'a str` means: "this function works for *any* lifetime `'a`." It's not tied to a specific lifetime chosen by the caller — it must work universally.

Compare:
- `Fn(&'a str) -> &'a str` where `'a` is from the outer scope — the closure works for one specific lifetime
- `for<'a> Fn(&'a str) -> &'a str` — the closure works for every possible lifetime

## You've Been Using HRTBs All Along

Here's the thing — the compiler desugars closure traits using `for<'a>` automatically. When you write:

```rust
fn takes_closure(f: impl Fn(&str) -> &str) {
    // ...
}
```

The compiler actually sees:

```rust
fn takes_closure(f: impl for<'a> Fn(&'a str) -> &'a str) {
    // ...
}
```

Lifetime elision in closure traits defaults to higher-ranked bounds. So you've been using HRTBs every time you pass a closure that takes references. You just didn't know it.

## When You Need to Write for<'a> Explicitly

Explicit HRTBs show up when the compiler can't elide them, which is usually in generic bounds or trait objects:

### With where clauses

```rust
fn process<F>(func: F) -> String
where
    F: for<'a> Fn(&'a str) -> &'a str,
{
    let data = String::from("  hello  ");
    let result = func(&data);
    result.to_string()
}

fn main() {
    let trimmed = process(|s| s.trim());
    println!("'{}'", trimmed);
}
```

### With trait objects

```rust
fn apply_transform(
    data: &str,
    transforms: &[Box<dyn for<'a> Fn(&'a str) -> &'a str>],
) -> String {
    let mut result = data.to_string();
    for t in transforms {
        result = t(&result).to_string();
    }
    result
}

fn main() {
    let transforms: Vec<Box<dyn for<'a> Fn(&'a str) -> &'a str>> = vec![
        Box::new(|s| s.trim()),
        Box::new(|s| {
            if s.len() > 5 { &s[..5] } else { s }
        }),
    ];

    let result = apply_transform("  hello world  ", &transforms);
    println!("Result: '{}'", result);
}
```

### In custom traits

```rust
trait Transform {
    fn apply<'a>(&self, input: &'a str) -> &'a str;
}

struct Trimmer;

impl Transform for Trimmer {
    fn apply<'a>(&self, input: &'a str) -> &'a str {
        input.trim()
    }
}

struct Prefix {
    // Can't return &str with prefix because we'd need new allocation
    // So this demonstrates the limits of borrowing transforms
}

fn apply_all<'a>(input: &'a str, transforms: &[&dyn Transform]) -> &'a str {
    let mut result = input;
    for t in transforms {
        result = t.apply(result);
    }
    result
}

fn main() {
    let text = "  hello world  ";
    let trimmer = Trimmer;
    let result = apply_all(text, &[&trimmer]);
    println!("'{}'", result);
}
```

## The Contrast: With and Without HRTB

Let me show why the non-HRTB version fails:

```rust
// WITHOUT HRTB — broken
// fn apply_twice<'a>(f: impl Fn(&'a str) -> &'a str) -> String {
//     let a = String::from("hello");
//     let b = String::from("world");
//     // 'a was fixed by the caller — it might not match
//     // the lifetime of our local strings
//     let ra = f(&a);  // ERROR: 'a doesn't match local lifetime
//     let rb = f(&b);
//     format!("{} {}", ra, rb)
// }

// WITH HRTB — works
fn apply_twice(f: impl for<'a> Fn(&'a str) -> &'a str) -> String {
    let a = String::from("hello");
    let b = String::from("world");
    let ra = f(&a);
    let rb = f(&b);
    format!("{} {}", ra, rb)
}

fn main() {
    let result = apply_twice(|s| s.trim());
    println!("{}", result);
}
```

Without `for<'a>`, the lifetime `'a` is chosen by the caller. It might be the lifetime of some external data. Inside the function, our local `String`s have their own lifetimes that don't match. With `for<'a>`, the closure promises to work with *any* lifetime — including the local ones inside our function.

## HRTBs with Fn Traits: The Full Picture

The three closure traits have HRTB versions:

```rust
// Fn — can be called multiple times, borrows captures immutably
fn repeat_fn(f: impl for<'a> Fn(&'a str) -> &'a str) {
    let s = String::from("test");
    println!("{}", f(&s));
    println!("{}", f(&s));  // called twice — fine with Fn
}

// FnMut — can be called multiple times, borrows captures mutably
fn repeat_fn_mut(mut f: impl for<'a> FnMut(&'a str) -> &'a str) {
    let s = String::from("test");
    println!("{}", f(&s));
    println!("{}", f(&s));
}

// FnOnce — can only be called once, might consume captures
// Less common with HRTBs since you usually want to call multiple times
fn once_fn(f: impl for<'a> FnOnce(&'a str) -> &'a str) {
    let s = String::from("test");
    println!("{}", f(&s));
}
```

## When to Reach for HRTBs

You need explicit `for<'a>` when:

1. **The lifetime can't be determined from context** — the function creates data internally and passes references to the closure
2. **Trait objects with lifetime-polymorphic methods** — `Box<dyn for<'a> Fn(&'a str) -> &'a str>`
3. **Generic bounds where elision doesn't cover you** — complex `where` clauses

You don't need explicit `for<'a>` when:

1. **Closure parameters in function signatures** — elision handles it
2. **The lifetime comes from the caller** — regular lifetime parameters work
3. **You're not returning references from closures** — no lifetime relationship to express

## A Practical Example: Custom Iterator Adapter

```rust
struct FilterMap<'a, T, U> {
    iter: std::slice::Iter<'a, T>,
    func: Box<dyn for<'b> Fn(&'b T) -> Option<U> + 'a>,
}

impl<'a, T, U> Iterator for FilterMap<'a, T, U> {
    type Item = U;

    fn next(&mut self) -> Option<U> {
        loop {
            let item = self.iter.next()?;
            if let Some(result) = (self.func)(item) {
                return Some(result);
            }
        }
    }
}

fn main() {
    let numbers = vec![1, 2, 3, 4, 5, 6];

    let filter_map = FilterMap {
        iter: numbers.iter(),
        func: Box::new(|&n| {
            if n % 2 == 0 { Some(n * 10) } else { None }
        }),
    };

    let results: Vec<i32> = filter_map.collect();
    println!("{:?}", results);  // [20, 40, 60]
}
```

## The Bottom Line

HRTBs are lifetime generics for function parameters. Regular generics say "this works for any type T." HRTBs say "this works for any lifetime 'a."

Most of the time, Rust figures this out for you through elision. When it can't, `for<'a>` is your tool. It looks intimidating but it's fundamentally simple: "this must work for every possible lifetime."

Don't memorize the syntax — understand the concept. When a closure needs to accept references with different lifetimes across different calls, that's a higher-ranked bound.
