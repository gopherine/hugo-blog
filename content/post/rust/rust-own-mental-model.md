---
title: "Lesson 1: The Mental Model — Stack, Heap, and Ownership"
author: Atharva Pandey
series: "Rust Ownership & Lifetimes Masterclass"
lesson: 1
keywords: [Rust, rust, ownership, lifetimes, stack, heap, memory model]
tags: [Rust tutorial, rust, ownership, lifetimes]
date: '2024-05-15T09:22:00.000Z'
---

I spent my first three months in Rust confused about ownership. Not because the concept is hard — it's genuinely simple — but because every tutorial I read explained it through the lens of "rules to memorize." Three rules. Memorize them. Move on.

That's backwards. You don't learn ownership by memorizing rules. You learn it by understanding where your data actually lives.

## Where Data Lives Changes Everything

Every value in your program sits in one of two places: the stack or the heap. If you've done any C or C++, this is familiar territory. If you haven't — don't worry, this isn't as scary as systems programmers make it sound.

The **stack** is fast, ordered, and predictable. It works like a stack of plates — last in, first out. Every function call pushes a "frame" onto the stack, and when the function returns, that frame gets popped off. Gone. Cleaned up automatically.

```rust
fn main() {
    let x: i32 = 42;       // lives on the stack
    let y: f64 = 3.14;     // also stack
    let flag: bool = true;  // stack again

    do_something(x);
    // x is still valid here — it was copied
}

fn do_something(val: i32) {
    // val lives in this function's stack frame
    println!("{}", val);
    // val is cleaned up when this frame pops
}
```

Stack allocation is essentially free. The compiler knows exactly how much space each type needs at compile time, so it just bumps a pointer. No allocation overhead. No garbage collector. No reference counting.

The **heap** is different. It's a big pool of memory you allocate from at runtime. Slower to allocate, slower to access (no cache locality guarantees), but it's flexible — you can put data there that outlives the current function, or data whose size you don't know at compile time.

```rust
fn main() {
    let name = String::from("Atharva");  // heap-allocated
    let numbers = vec![1, 2, 3, 4, 5];   // also heap

    // Both `name` and `numbers` store a pointer on the stack
    // that points to data on the heap
}
```

Here's the thing most people miss: a `String` is actually three values on the stack — a pointer, a length, and a capacity. The actual character data? That's on the heap. The stack portion is like a TV remote; the heap portion is the TV.

## The Problem C and C++ Have

In C, you allocate heap memory with `malloc` and free it with `free`. Forget to free? Memory leak. Free it twice? Undefined behavior. Use it after freeing? Security vulnerability.

```c
// C code — DON'T do this, but it compiles
char* name = malloc(100);
strcpy(name, "Atharva");
free(name);
printf("%s\n", name);  // use-after-free — boom
```

C++ introduced RAII — the idea that destructors clean up resources when objects go out of scope. Smart move. But C++ still lets you create dangling pointers, still lets you alias mutable data across threads, still lets you shoot yourself in the foot in a hundred creative ways.

## Ownership: One Value, One Owner

Rust's answer is ownership. The rule is dead simple:

**Every value has exactly one owner. When the owner goes out of scope, the value is dropped.**

That's it. That's the whole model. Everything else — moves, borrows, lifetimes — is a consequence of this single idea.

```rust
fn main() {
    let s = String::from("hello");  // s owns this String

    // when main() ends, s goes out of scope
    // Rust calls drop() on the String
    // the heap memory is freed
    // no garbage collector needed
}
```

Rust inserts a call to `drop()` at the end of the scope. It's deterministic — you know exactly when memory gets freed. Not "whenever the GC gets around to it." Not "when the reference count hits zero." Right there, at the closing brace.

## Scope Is the Key

Watch what happens with nested scopes:

```rust
fn main() {
    let outer = String::from("I live longer");

    {
        let inner = String::from("I'm temporary");
        println!("{} and {}", outer, inner);
        // inner is dropped here — end of inner scope
    }

    println!("{}", outer);  // fine — outer is still alive
    // println!("{}", inner);  // ERROR: inner doesn't exist anymore

    // outer is dropped here — end of main
}
```

This is the compiler enforcing the mental model. `inner` was created inside a block, so it gets cleaned up when that block ends. You can't use it after that. The compiler won't let you. Not a runtime error — a compile-time guarantee.

## What Does "Drop" Actually Do?

When Rust drops a value, it calls the `Drop` trait's `drop` method. For a `String`, that means deallocating the heap buffer. For a `Vec`, same thing. For a `File`, it closes the file handle. For your custom types, you can implement `Drop` yourself:

```rust
struct DatabaseConnection {
    url: String,
    connected: bool,
}

impl Drop for DatabaseConnection {
    fn drop(&mut self) {
        if self.connected {
            println!("Closing connection to {}", self.url);
            // actual cleanup would happen here
        }
    }
}

fn main() {
    let conn = DatabaseConnection {
        url: String::from("postgres://localhost/mydb"),
        connected: true,
    };

    println!("Doing database work...");

    // conn is dropped here automatically
    // "Closing connection to postgres://localhost/mydb" prints
}
```

This is RAII done right. No try-finally blocks. No defer statements. No chance of forgetting to close that connection. The language handles it.

## The Stack vs. Heap Decision

How does Rust decide where to put things? It's simpler than you'd think:

- **Known size at compile time + relatively small?** Stack.
- **Unknown size at compile time OR needs to outlive current scope?** Heap (usually via `Box`, `Vec`, `String`, etc.)

```rust
fn main() {
    // Stack: size known at compile time
    let a: i32 = 10;
    let b: [i32; 5] = [1, 2, 3, 4, 5];  // fixed-size array
    let c: (f64, bool) = (3.14, true);

    // Heap: size determined at runtime
    let d: Vec<i32> = vec![1, 2, 3];  // could grow
    let e: String = String::from("dynamic");  // could grow
    let f: Box<i32> = Box::new(42);  // explicitly heap-allocated
}
```

`Box<T>` is interesting — it puts a known-size value on the heap anyway. Why would you do that? Recursive types (like linked lists) and trait objects are the main reasons. We'll get there.

## Building Your Mental Model

Here's how I think about ownership now, after years of writing Rust:

1. **Every value is a physical thing.** It exists somewhere in memory. Stack or heap.
2. **Every value has a single owner.** That owner is a variable binding, a struct field, or a collection element.
3. **When the owner dies, the value dies.** Period. No exceptions.
4. **Passing a value to a function or assigning it to another variable might transfer ownership.** This is called a "move," and it's what the next lesson covers.

The beauty of this model is that it makes memory management a compile-time concern, not a runtime one. The compiler sees your ownership graph, verifies it makes sense, and generates the `drop` calls for you. Zero overhead. Zero runtime cost.

## A Common First Mistake

Here's what bit me early on:

```rust
fn main() {
    let greeting = String::from("hello");
    print_string(greeting);
    println!("{}", greeting);  // ERROR: value used after move
}

fn print_string(s: String) {
    println!("{}", s);
}
```

If you're coming from Python, Java, JavaScript — basically any garbage-collected language — this is baffling. "I just passed it to a function. Why can't I use it anymore?"

Because `print_string` took ownership. `greeting` was *moved* into the function parameter `s`. After that move, `greeting` is no longer valid. It's gone. The compiler tells you so.

This feels restrictive at first. It's actually liberating. Once you internalize the mental model — one owner, always — you stop thinking about it. You start designing your data flow around ownership, and suddenly memory bugs just... don't happen.

The next lesson digs into exactly how and why moves work the way they do.
