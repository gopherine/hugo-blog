---
title: "Lesson 2: Move Semantics — Why Assignment Transfers Ownership"
author: Atharva Pandey
series: "Rust Ownership & Lifetimes Masterclass"
lesson: 2
keywords: [Rust, rust, ownership, lifetimes, move semantics, transfer ownership]
tags: [Rust tutorial, rust, ownership, lifetimes]
date: '2024-05-17T14:45:00.000Z'
---

The first time Rust told me I couldn't use a variable after assigning it to another one, I stared at my screen for a solid minute. In what universe does `let b = a` make `a` invalid?

This universe. Rust's universe. And honestly — it's the only sane default.

## What a Move Actually Is

In most languages, `let b = a` copies the data or copies a reference. In Rust, for heap-allocated types, it *moves* the ownership. The bits get copied (the stack portion — pointer, length, capacity), but the old variable is invalidated.

```rust
fn main() {
    let s1 = String::from("hello");
    let s2 = s1;  // s1's data is moved to s2

    // println!("{}", s1);  // ERROR: value used after move
    println!("{}", s2);     // fine — s2 owns it now
}
```

Why? Think about what would happen without moves. If both `s1` and `s2` pointed to the same heap data, when they both go out of scope, Rust would call `drop()` twice on the same memory. Double free. That's undefined behavior in C — Rust refuses to let it happen.

## The Problem: Double Free

Picture this scenario without ownership:

```rust
// HYPOTHETICAL — this is what Rust prevents
fn main() {
    let s1 = String::from("hello");
    let s2 = s1;  // both point to same heap data

    // end of scope:
    // drop(s2) — frees the heap memory
    // drop(s1) — frees the SAME memory again
    // 💥 double free
}
```

C++ "solves" this with copy constructors — it deep-copies the heap data so each variable has its own copy. But that's expensive and happens implicitly. You might not even realize you're copying a megabyte of data.

Rust's solution: make the old variable invalid. No double free possible. No implicit expensive copies. The compiler just marks `s1` as moved and refuses to let you use it.

## Moves Happen Everywhere

It's not just assignment. Moves happen any time ownership transfers:

### Function arguments

```rust
fn take_ownership(s: String) {
    println!("I own: {}", s);
    // s is dropped here
}

fn main() {
    let name = String::from("Atharva");
    take_ownership(name);
    // name is no longer valid here
}
```

### Return values

```rust
fn give_ownership() -> String {
    let s = String::from("here you go");
    s  // ownership moves to the caller
}

fn main() {
    let received = give_ownership();
    println!("{}", received);  // received owns it
}
```

### Collecting into containers

```rust
fn main() {
    let s = String::from("hello");
    let mut v = Vec::new();
    v.push(s);  // s moved into the vector

    // println!("{}", s);  // ERROR: s was moved
    println!("{:?}", v);   // the vector owns it now
}
```

### Struct construction

```rust
struct User {
    name: String,
    email: String,
}

fn main() {
    let name = String::from("Atharva");
    let email = String::from("atharva@example.com");

    let user = User { name, email };
    // Both name and email have been moved into the struct

    // println!("{}", name);   // ERROR: moved
    println!("{}", user.name); // fine — access through the struct
}
```

## The Workaround: Give It Back

Early on, you might find yourself doing this awkward dance:

```rust
fn calculate_length(s: String) -> (String, usize) {
    let length = s.len();
    (s, length)  // give ownership back along with the result
}

fn main() {
    let s = String::from("hello");
    let (s, len) = calculate_length(s);
    println!("'{}' has length {}", s, len);
}
```

This works but it's ugly. Passing ownership back and forth is like lending someone your car and making them drive it back to you after every errand. There's a better way — borrowing — and we'll get there in lesson 4. For now, just understand why moves exist.

## What About Integers?

You might have noticed this works fine:

```rust
fn main() {
    let x = 42;
    let y = x;
    println!("{} {}", x, y);  // both valid!
}
```

No move? No, because integers implement the `Copy` trait. Types that are `Copy` get duplicated on assignment instead of moved. This is cheap — copying 4 bytes of an `i32` is trivial. The next lesson covers `Copy` vs `Clone` in detail, but the key insight is: stack-only types that are cheap to duplicate are `Copy`. Everything else moves.

## Moves Are Just Memcpy (Plus Invalidation)

Under the hood, a move is a `memcpy` of the stack bytes. For a `String`, that's copying 24 bytes (pointer + length + capacity on 64-bit). The heap data doesn't move at all — only the "remote control" gets copied.

```rust
fn main() {
    let s1 = String::from("hello");
    // s1 on stack: [ptr=0x1234, len=5, cap=5]
    // heap at 0x1234: ['h', 'e', 'l', 'l', 'o']

    let s2 = s1;
    // s2 on stack: [ptr=0x1234, len=5, cap=5]  (same pointer!)
    // s1 is now INVALID — compiler won't let you use it
    // heap at 0x1234: ['h', 'e', 'l', 'l', 'o']  (unchanged)
}
```

The heap allocation stays put. Only the stack-level "handle" moves. This is why moves are cheap — they're always a fixed-size copy regardless of how much data is on the heap.

## Partial Moves

Rust tracks ownership at a fine granularity. You can move individual fields out of a struct:

```rust
struct Config {
    name: String,
    value: String,
}

fn main() {
    let config = Config {
        name: String::from("timeout"),
        value: String::from("30"),
    };

    let name = config.name;  // partial move — just the name field

    // println!("{}", config.name);   // ERROR: field was moved
    // println!("{:?}", config);      // ERROR: can't use partially moved struct
    println!("{}", config.value);     // fine! this field wasn't moved
    println!("{}", name);
}
```

This is actually pretty clever — the compiler tracks ownership per-field. You can still access the fields that weren't moved.

## Moves in Match and If-Let

Pattern matching can also trigger moves:

```rust
fn main() {
    let opt: Option<String> = Some(String::from("data"));

    match opt {
        Some(s) => println!("Got: {}", s),  // s takes ownership
        None => println!("Nothing"),
    }

    // println!("{:?}", opt);  // ERROR: opt was moved in the match
}
```

If you want to match without moving, you match on a reference:

```rust
fn main() {
    let opt: Option<String> = Some(String::from("data"));

    match &opt {
        Some(s) => println!("Got: {}", s),  // s is a &String
        None => println!("Nothing"),
    }

    println!("{:?}", opt);  // fine — we only borrowed
}
```

## Moves in Loops

This trips people up constantly:

```rust
fn main() {
    let s = String::from("hello");

    // ERROR: s is moved in first iteration, second iteration can't use it
    // for _ in 0..3 {
    //     let x = s;  // moves s
    //     println!("{}", x);
    // }

    // Solution 1: clone each iteration
    for _ in 0..3 {
        let x = s.clone();
        println!("{}", x);
    }

    // Solution 2: borrow instead (lesson 4)
    for _ in 0..3 {
        let x = &s;
        println!("{}", x);
    }
}
```

## Why This Design Is Good, Actually

I know it feels restrictive. "Just let me use the variable!" But think about what Rust is preventing:

1. **Double free** — impossible when only one owner exists
2. **Use after free** — the compiler invalidates moved variables
3. **Data races** — if a value moves into a thread, only that thread can touch it
4. **Implicit expensive copies** — you always know when data is being duplicated

Every other systems language makes you *think* about these problems at runtime, debugging crashes and memory corruption. Rust makes you think about them at compile time, reading error messages that tell you exactly what went wrong.

I'll take a compiler error over a segfault any day of the week.

## The Mental Shift

Stop thinking of variables as "names for values." Start thinking of them as "owners of values." Assignment doesn't copy a value — it transfers responsibility.

When you write `let s2 = s1`, you're not saying "s2 is also hello." You're saying "s2 is now responsible for this String's memory. s1, you're relieved of duty."

Once that clicks, everything else in Rust's ownership system follows naturally. Next up: the types that are exempt from all this — `Copy` and `Clone`.
