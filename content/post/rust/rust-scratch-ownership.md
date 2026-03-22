---
title: "Lesson 7: Ownership — The rule that changes everything"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 7
keywords: [Rust, rust, ownership, move semantics, drop, memory management, stack, heap]
tags: [Rust tutorial, rust, beginner]
date: '2024-03-16T08:30:00.000Z'
---

I spent three hours fighting the borrow checker on my first real Rust project. I was furious. Then I realized every single error the compiler flagged was a genuine bug — a use-after-free, a data race, a dangling reference. The compiler wasn't being difficult. It was saving me from myself.

## Why Ownership Exists

In C, you allocate memory and free it manually. Forget to free? Memory leak. Free twice? Undefined behavior. Use after free? Crash (if you're lucky) or silent corruption (if you're not).

In Java, Python, and Go, a garbage collector handles memory. You never free anything — the runtime figures it out. This is convenient but has costs: GC pauses, unpredictable latency, higher memory usage.

Rust takes a third path: the compiler tracks who owns each piece of data and inserts cleanup code automatically. No manual free, no garbage collector. Memory is managed by rules enforced at compile time.

These rules are called *ownership*.

## The Three Rules

Here they are. Memorize them.

1. **Each value in Rust has exactly one owner.**
2. **There can only be one owner at a time.**
3. **When the owner goes out of scope, the value is dropped (freed).**

That's it. Everything else — borrowing, lifetimes, the borrow checker — is built on top of these three rules.

## Stack vs. Heap

Before we go further, you need to understand where data lives.

**The stack** is fast. Fixed-size data goes here: integers, floats, booleans, fixed-size arrays, tuples of stack types. Allocation and deallocation is instantaneous — just move the stack pointer.

**The heap** is flexible. Variable-size data goes here: strings, vectors, anything whose size isn't known at compile time. Allocation involves asking the operating system for memory, which is slower. Deallocation must be explicit (or handled by GC, or by ownership rules).

```rust
fn main() {
    let x = 42;                    // stack — i32 has a fixed size
    let s = String::from("hello"); // heap — String's content can grow

    println!("{x} {s}");
} // s is dropped here (heap memory freed), x is popped off the stack
```

## Move Semantics

When you assign a heap-allocated value to another variable, Rust *moves* it:

```rust
fn main() {
    let s1 = String::from("hello");
    let s2 = s1;  // s1 is MOVED to s2

    // println!("{s1}"); // ERROR: value used after move
    println!("{s2}");     // OK
}
```

After `let s2 = s1`, the variable `s1` is no longer valid. It doesn't exist. Rust doesn't copy the data (that would be expensive for a large string), and it doesn't create a second pointer to the same data (that would create a double-free risk). Instead, it *transfers ownership* from `s1` to `s2`.

This is fundamentally different from what happens in most languages. In Python, `s2 = s1` creates two references to the same object. In C, it copies the struct (including the pointer), creating a double-free waiting to happen. In Rust, it's a clean transfer.

### What a Move Actually Does

Under the hood, a move is a shallow copy — the bytes of the variable are copied, but the heap data isn't duplicated. Then the original variable is invalidated. The result: one owner, one copy of the data, no ambiguity about who should free it.

```rust
fn main() {
    let s1 = String::from("hello");
    // s1 is a struct on the stack: { ptr, len: 5, capacity: 5 }
    // ptr points to heap memory containing "hello"

    let s2 = s1;
    // s2 gets the same struct: { ptr, len: 5, capacity: 5 }
    // s1 is invalidated
    // The heap memory still exists exactly once

    println!("{s2}");
} // s2 is dropped, heap memory is freed exactly once
```

## Copy Types

Not everything moves. Simple, stack-only types implement the `Copy` trait, which means they're duplicated instead of moved:

```rust
fn main() {
    let x = 42;
    let y = x;  // x is COPIED, not moved

    println!("x: {x}, y: {y}");  // both valid!
}
```

Copy types include:
- All integer types (`i32`, `u8`, etc.)
- All floating-point types (`f32`, `f64`)
- `bool`
- `char`
- Tuples of Copy types: `(i32, f64)` is Copy, but `(i32, String)` is not

The rule is simple: if a type is small and stack-allocated with no heap resources, it's probably Copy. If it manages heap memory, it moves.

## Ownership and Functions

Passing a value to a function moves it (for non-Copy types):

```rust
fn takes_ownership(s: String) {
    println!("Got: {s}");
} // s is dropped here — the String is freed

fn makes_copy(x: i32) {
    println!("Got: {x}");
} // x is just a stack value, nothing special happens

fn main() {
    let s = String::from("hello");
    takes_ownership(s);
    // println!("{s}"); // ERROR: s was moved into the function

    let x = 42;
    makes_copy(x);
    println!("{x}");  // OK: i32 is Copy
}
```

This is where people first get frustrated. You pass a string to a function and then you can't use it anymore? That seems ridiculous.

It's not. The function took ownership. It's responsible for the data now. When the function returns, it cleans up. This is *exactly* what should happen — one owner, clear responsibility.

But obviously, you often want to let a function *use* data without taking ownership. That's borrowing — next lesson.

## Returning Ownership

Functions can give ownership back:

```rust
fn create_greeting(name: &str) -> String {
    format!("Hello, {name}!")
}

fn main() {
    let greeting = create_greeting("Rustacean");
    println!("{greeting}");
}
```

The function creates a String and returns it. Ownership transfers to the caller. No copy, no garbage collection — just a clean handoff.

You can also take ownership and give it back:

```rust
fn add_exclamation(mut s: String) -> String {
    s.push('!');
    s
}

fn main() {
    let s = String::from("hello");
    let s = add_exclamation(s);  // s moved in, modified, moved back
    println!("{s}");  // "hello!"
}
```

This pattern — taking ownership and returning it — works but is awkward. It's a sign that you should be using references instead. We'll fix this in the next lesson.

## Clone — Explicit Deep Copy

When you actually want a full copy of heap data, use `.clone()`:

```rust
fn main() {
    let s1 = String::from("hello");
    let s2 = s1.clone();  // deep copy — both s1 and s2 are valid

    println!("s1: {s1}");
    println!("s2: {s2}");
}
```

`.clone()` allocates new heap memory and copies the data. It's explicit and potentially expensive. That's the point — Rust makes expensive operations visible. If you see `.clone()` in code, you know a deep copy is happening. In other languages, this might happen silently behind your back.

**My opinion on clone**: don't be afraid of it, but be aware of it. Cloning a small string? Fine. Cloning a 10MB vector in a tight loop? That's a performance problem. The fact that Rust makes the clone explicit means you can spot these issues in code review.

## Drop — Cleanup on Scope Exit

When a value goes out of scope, Rust calls its `drop` method. For types like String, this frees heap memory. For files, this closes the file handle. For network connections, this closes the socket.

```rust
fn main() {
    {
        let s = String::from("hello");
        println!("{s}");
    } // s is dropped here — memory freed

    // s doesn't exist out here
}
```

This is deterministic. You know *exactly* when cleanup happens — at the closing brace. No GC cycle, no finalizer queue, no uncertainty. In C++, this is called RAII (Resource Acquisition Is Initialization). Rust adopted the pattern and made it mandatory.

## The Ownership Mindset

Here's the mental model that made ownership click for me:

Think of values like physical objects. A book, a set of keys, a laptop. At any moment, each object has exactly one person holding it. You can hand it to someone else (move), you can let someone look at it (borrow), or you can make a photocopy (clone). But there's always exactly one person responsible for it.

When you leave a room, you take your stuff with you or leave it behind (drop). You can't be in two rooms holding the same laptop. You can't hand your keys to two people simultaneously.

This metaphor breaks down eventually (references complicate it), but it's a good starting point.

## Common Mistakes

**Mistake 1: Using a value after moving it**

```rust
fn main() {
    let names = vec!["Alice", "Bob"];
    let also_names = names;  // moved
    // println!("{:?}", names); // ERROR
    println!("{:?}", also_names);
}
```

Fix: use `.clone()` if you need both, or restructure your code.

**Mistake 2: Passing to a function and then using it**

```rust
fn print_vec(v: Vec<i32>) {
    println!("{:?}", v);
}

fn main() {
    let numbers = vec![1, 2, 3];
    print_vec(numbers);
    // println!("{:?}", numbers); // ERROR: moved into print_vec
}
```

Fix: pass a reference instead (next lesson).

**Mistake 3: Thinking everything moves**

```rust
fn main() {
    let x: i32 = 42;
    let y = x;
    println!("{x} {y}");  // Both valid! i32 is Copy.
}
```

Copy types don't move. Know which types are Copy.

## Why This Matters

Ownership isn't just a memory management trick. It's a design philosophy. When you write Rust, you're always thinking: who owns this data? Who's responsible for it? When does it get cleaned up?

These are questions you should be asking in *any* language. Rust just forces you to answer them explicitly. That explicitness is what makes Rust programs reliable.

Next lesson: borrowing. It's how you use data without taking ownership, and it's the key to writing practical Rust code.
