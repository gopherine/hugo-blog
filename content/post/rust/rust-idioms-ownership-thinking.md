---
title: "Lesson 1: Think in Ownership — Not pointers"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 1
keywords: [Rust, rust, idiomatic, ownership, move semantics, borrow checker]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-04-10T09:14:00.000Z'
---

I spent three months fighting the borrow checker before I realized the problem wasn't the borrow checker — it was me. I kept thinking in C++ pointers and Java references. Every `String` was just "data on the heap." Every function call was "passing a reference." And every compiler error felt like Rust was being unreasonable.

It wasn't. I was just thinking about memory the wrong way.

---

## The C++/Java Mental Model (And Why It Fails)

If you come from C++, your brain maps everything to raw pointers, smart pointers, or references. If you come from Java/Python/Go, everything is a reference behind the scenes — you never think about who owns what, because the GC handles it.

Both mental models will sabotage you in Rust.

Here's what I mean. A C++ developer writes something like this:

```rust
fn process(data: Vec<u32>) {
    println!("Processing {} items", data.len());
}

fn main() {
    let numbers = vec![1, 2, 3, 4, 5];
    process(numbers);
    // "Why can't I use numbers here? I just passed it to a function!"
    println!("{:?}", numbers); // ERROR: value used after move
}
```

The instinct says: "I passed `numbers` to the function, the function used it, now I want to use it again." In C++ with a vector, you'd get a copy. In Java with an ArrayList, you'd still have your reference. In Rust? You just *gave away* your data.

That's the fundamental shift. **In Rust, assignment and function calls transfer ownership by default.**

---

## Ownership Is Not a Pointer — It's a Responsibility

Here's how I finally got it. Stop thinking of variables as arrows pointing to data. Instead, think of them as *responsible adults*.

When you write `let numbers = vec![1, 2, 3]`, `numbers` isn't pointing to a vector. `numbers` *is the owner* of that vector. It's responsible for cleaning it up when it goes out of scope. And there can only be one owner at a time.

When you pass `numbers` to a function, you're not copying a pointer. You're *handing over responsibility*. The function now owns that data. You don't anymore. You can't use it, and you shouldn't want to — it's not yours.

```rust
fn main() {
    let name = String::from("Atharva");
    let greeting = format!("Hello, {}", name);
    // `name` is still valid — format! only borrows
    println!("{}", name);    // Fine
    println!("{}", greeting); // Fine

    let name2 = name;
    // `name` is GONE. `name2` owns the string now.
    // println!("{}", name); // ERROR: value used after move
    println!("{}", name2);   // Fine
}
```

This is not Rust being pedantic. This is Rust enforcing a rule that prevents an entire *category* of bugs: use-after-free, double-free, dangling pointers, data races. All of them, gone. Not by runtime checks. Not by garbage collection. By *making you think about who owns what*.

---

## The Three Rules

Ownership in Rust boils down to three rules. Memorize them:

1. **Every value has exactly one owner.**
2. **When the owner goes out of scope, the value is dropped (freed).**
3. **Ownership can be transferred (moved), but the previous owner can't use the value anymore.**

That's it. Everything else — borrowing, lifetimes, `Rc`, `Arc` — is built on top of these three rules.

```rust
fn takes_ownership(s: String) {
    println!("Got: {}", s);
} // `s` is dropped here — the String is freed

fn makes_copy(n: i32) {
    println!("Got: {}", n);
} // `n` goes out of scope, but i32 is Copy, so nothing special happens

fn main() {
    let s = String::from("hello");
    takes_ownership(s);
    // s is gone

    let n = 42;
    makes_copy(n);
    // n is still valid — i32 implements Copy
    println!("{}", n); // Fine!
}
```

Notice the difference: `String` gets moved, but `i32` gets copied. Why? Because `i32` implements the `Copy` trait — it's cheap to duplicate (it's just a few bytes on the stack). `String` doesn't implement `Copy` because it owns heap memory, and implicitly duplicating heap data would be expensive and surprising.

---

## The Wrong Reflex: `.clone()` Everywhere

When newcomers hit ownership errors, the first instinct is to `.clone()` everything. I did this. My early Rust code was littered with `.clone()` calls like confetti at a parade.

```rust
fn process(data: Vec<u32>) -> u32 {
    data.iter().sum()
}

fn main() {
    let numbers = vec![1, 2, 3, 4, 5];
    let sum = process(numbers.clone()); // clone to keep our copy
    println!("Sum: {}, Original: {:?}", sum, numbers);
}
```

This works. But it's wasteful — you're allocating a whole new vector just because you didn't want to think about ownership. The idiomatic solution? **Borrow instead.**

```rust
fn process(data: &[u32]) -> u32 {
    data.iter().sum()
}

fn main() {
    let numbers = vec![1, 2, 3, 4, 5];
    let sum = process(&numbers); // borrow — no allocation
    println!("Sum: {}, Original: {:?}", sum, numbers);
}
```

The function takes `&[u32]` — a slice reference. It doesn't need to own the data; it just needs to read it. This is the core insight: **most functions don't need ownership. They need access.**

---

## When to Actually Transfer Ownership

So when *do* you move data? Three scenarios:

**1. When the function needs to store the data long-term:**

```rust
struct Config {
    name: String,
}

impl Config {
    fn new(name: String) -> Config {
        Config { name } // Config now owns the string
    }
}
```

**2. When you're done with the data and the callee should handle cleanup:**

```rust
fn send_over_network(data: Vec<u8>) {
    // send data...
    // data is dropped when this function ends
}
```

**3. When transferring between threads:**

```rust
use std::thread;

fn main() {
    let data = vec![1, 2, 3];
    let handle = thread::spawn(move || {
        // This thread now owns `data`
        println!("Thread got: {:?}", data);
    });
    handle.join().unwrap();
}
```

The `move` keyword in the closure is explicit — you're telling Rust "yes, I want this thread to take ownership." No ambiguity. No accidental sharing.

---

## Thinking in Ownership: A Practical Example

Here's a real-world scenario. You're building a user registration system. The naive approach:

```rust
// BAD: takes ownership unnecessarily
fn validate_email(email: String) -> bool {
    email.contains('@') && email.contains('.')
}

fn create_user(email: String, name: String) {
    if validate_email(email.clone()) { // forced to clone!
        println!("Creating user {} with email {}", name, email);
    }
}
```

The idiomatic approach:

```rust
// GOOD: borrows what it only needs to inspect
fn validate_email(email: &str) -> bool {
    email.contains('@') && email.contains('.')
}

struct User {
    email: String,
    name: String,
}

fn create_user(email: String, name: String) -> Option<User> {
    if validate_email(&email) { // borrow for validation
        Some(User { email, name }) // move into the struct
    } else {
        None
    }
}

fn main() {
    let user = create_user(
        String::from("atharva@example.com"),
        String::from("Atharva"),
    );
    if let Some(u) = user {
        println!("Created: {} <{}>", u.name, u.email);
    }
}
```

See the difference? `validate_email` only needs to *read* the email — it borrows a `&str`. `create_user` takes ownership of both strings because it needs to store them in the `User` struct. The ownership flow makes the *intent* crystal clear.

---

## The Mental Shift

Here's what I wish someone told me on day one:

**Stop thinking "where does this pointer point?" Start thinking "who is responsible for this data?"**

Every time you write a function signature, ask yourself:
- Does this function need to *own* the data? → Take it by value.
- Does this function just need to *read* the data? → Take a `&T`.
- Does this function need to *modify* the data? → Take a `&mut T`.

That's the ownership thinking framework. Once it clicks, the borrow checker stops being an obstacle and starts being a *design tool*. You'll start noticing bugs in other languages that Rust would've caught at compile time. You'll find yourself asking "who owns this?" when reading Python code.

And that's when you know — you're thinking in Rust.

---

## Key Takeaways

- Variables in Rust aren't pointers — they're *owners*.
- Moving data transfers ownership; the original variable becomes invalid.
- Don't clone reflexively — borrow first, clone only when you actually need a separate copy.
- Function signatures should reflect *intent*: own, read, or modify.
- Types that are cheap to copy (integers, booleans, etc.) implement `Copy` and are duplicated automatically.

Next up, we'll go deeper into borrowing — when to use `&T` vs `&mut T`, and the subtle art of knowing when `.clone()` is actually the right call.
