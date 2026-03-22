---
title: "Lesson 21: Closures — Functions that capture"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 21
keywords: [Rust, rust, closures, lambda, anonymous functions, Fn, FnMut, FnOnce, closure traits]
tags: [Rust tutorial, rust, beginner]
date: '2024-04-10T12:15:00.000Z'
---

Closures are where Rust stops feeling like a systems language and starts feeling like a functional one. You've already been using them — every time you passed `|x| x * 2` to `.map()` or `|a, b| a.cmp(b)` to `.sort_by()`, that was a closure. Time to understand what's actually happening under the hood.

## What Is a Closure?

A closure is an anonymous function that can capture variables from its surrounding scope:

```rust
fn main() {
    let multiplier = 3;

    // This is a closure — it captures `multiplier`
    let multiply = |x: i32| x * multiplier;

    println!("{}", multiply(5));   // 15
    println!("{}", multiply(10));  // 30
}
```

Compare with a regular function:

```rust
// A function can't access `multiplier` from an outer scope
// fn multiply(x: i32) -> i32 {
//     x * multiplier  // ERROR: not found in this scope
// }
```

The closure *captures* `multiplier` from its environment. A regular function can't do this — it can only access its parameters and global/static items.

## Closure Syntax

```rust
fn main() {
    // Full syntax
    let add = |a: i32, b: i32| -> i32 { a + b };

    // Type inference (most common)
    let add = |a, b| a + b;

    // Multi-line body
    let complex = |x: i32| {
        let doubled = x * 2;
        let offset = doubled + 10;
        offset  // last expression is returned, no semicolon
    };

    // No parameters
    let greet = || println!("hello!");

    // Single expression, no braces needed
    let square = |x: i32| x * x;

    println!("{}", add(3, 4));
    println!("{}", complex(5));
    greet();
    println!("{}", square(6));
}
```

Type annotations are optional on closures — the compiler infers them from usage. But once a closure is used with specific types, it's locked to those types:

```rust
fn main() {
    let identity = |x| x;

    let s = identity("hello");  // infers x: &str
    // let n = identity(42);    // ERROR: expected &str, found i32
    println!("{s}");
}
```

## How Closures Capture

Closures capture variables in three ways, matching the ownership model:

### Borrowing (Immutable)

```rust
fn main() {
    let name = String::from("Alice");

    let greet = || println!("Hello, {name}!");  // borrows name

    greet();
    greet();
    println!("Name is still: {name}");  // name is still valid
}
```

### Borrowing (Mutable)

```rust
fn main() {
    let mut count = 0;

    let mut increment = || {
        count += 1;  // mutably borrows count
        println!("Count: {count}");
    };

    increment();
    increment();
    increment();

    // Can't use `count` while `increment` holds a mutable borrow
    // But after the last use of increment, count is available again
    println!("Final count: {count}");
}
```

Notice the closure itself must be `mut` — because calling it modifies the captured state.

### Taking Ownership (Move)

```rust
fn main() {
    let name = String::from("Alice");

    let greet = move || {
        println!("Hello, {name}!");  // name is moved into the closure
    };

    greet();
    // println!("{name}");  // ERROR: name was moved
}
```

The `move` keyword forces the closure to take ownership of captured variables. This is essential when the closure needs to outlive the scope where it was created — for example, when passing closures to threads.

## The Three Closure Traits

Every closure implements one or more of three traits. Understanding these is the key to using closures fluently.

### `Fn` — Borrows Immutably

```rust
fn apply_twice<F: Fn(i32) -> i32>(f: F, x: i32) -> i32 {
    f(f(x))
}

fn main() {
    let double = |x| x * 2;
    let result = apply_twice(double, 3);  // double(double(3)) = 12
    println!("{result}");
}
```

`Fn` closures don't modify their captured state. They can be called multiple times, concurrently, whatever. Most closures you write are `Fn`.

### `FnMut` — Borrows Mutably

```rust
fn apply_n_times<F: FnMut()>(mut f: F, n: usize) {
    for _ in 0..n {
        f();
    }
}

fn main() {
    let mut total = 0;

    apply_n_times(|| {
        total += 1;
    }, 5);

    println!("Total: {total}");  // 5
}
```

`FnMut` closures modify their captured state. They can be called multiple times but not concurrently (since they need exclusive access to their captures).

### `FnOnce` — Takes Ownership

```rust
fn consume<F: FnOnce() -> String>(f: F) -> String {
    f()  // can only call once — f is consumed
}

fn main() {
    let name = String::from("Alice");

    let greeting = move || {
        format!("Goodbye, {name}!")  // name is consumed
    };

    let result = consume(greeting);
    println!("{result}");
    // consume(greeting);  // ERROR: closure already consumed
}
```

`FnOnce` closures consume their captures. They can only be called once. Every closure is at least `FnOnce`.

### The Hierarchy

```
FnOnce   — most general, every closure implements this
  ↑
FnMut    — subset of FnOnce, closures that can be called multiple times
  ↑
Fn       — subset of FnMut, closures that don't modify captures
```

If a function accepts `FnOnce`, you can pass any closure. If it accepts `Fn`, you can only pass closures that don't modify their captures. Always use the most general trait that your function needs.

## Closures as Parameters

Three ways to accept closures:

```rust
// 1. Generic (most common) — static dispatch, inlined
fn apply<F: Fn(i32) -> i32>(f: F, x: i32) -> i32 {
    f(x)
}

// 2. impl Trait syntax (sugar for the above)
fn apply_v2(f: impl Fn(i32) -> i32, x: i32) -> i32 {
    f(x)
}

// 3. Trait object — dynamic dispatch, can store different closures
fn apply_v3(f: &dyn Fn(i32) -> i32, x: i32) -> i32 {
    f(x)
}

fn main() {
    let double = |x| x * 2;
    println!("{}", apply(double, 5));
    println!("{}", apply_v2(double, 5));
    println!("{}", apply_v3(&double, 5));
}
```

Use generic/impl Trait for most cases. Use `dyn Fn` when you need to store closures in collections or decide which closure to call at runtime.

## Returning Closures

Functions can return closures using `impl Fn`:

```rust
fn make_adder(n: i32) -> impl Fn(i32) -> i32 {
    move |x| x + n
}

fn make_multiplier(factor: f64) -> impl Fn(f64) -> f64 {
    move |x| x * factor
}

fn main() {
    let add_5 = make_adder(5);
    let add_10 = make_adder(10);

    println!("add_5(3) = {}", add_5(3));    // 8
    println!("add_10(3) = {}", add_10(3));  // 13

    let triple = make_multiplier(3.0);
    println!("triple(7) = {}", triple(7.0));  // 21
}
```

The `move` keyword is essential here — without it, the closure would try to borrow `n`, which goes out of scope when the function returns.

## Closures with Standard Library Methods

This is where closures earn their keep. The standard library uses closures everywhere:

```rust
fn main() {
    let numbers = vec![1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

    // filter
    let evens: Vec<&i32> = numbers.iter().filter(|&&x| x % 2 == 0).collect();
    println!("Evens: {:?}", evens);

    // map
    let squares: Vec<i32> = numbers.iter().map(|&x| x * x).collect();
    println!("Squares: {:?}", squares);

    // find
    let first_gt_5 = numbers.iter().find(|&&x| x > 5);
    println!("First > 5: {:?}", first_gt_5);

    // any / all
    let has_even = numbers.iter().any(|&x| x % 2 == 0);
    let all_positive = numbers.iter().all(|&x| x > 0);
    println!("Has even: {has_even}, All positive: {all_positive}");

    // fold (reduce)
    let sum = numbers.iter().fold(0, |acc, &x| acc + x);
    println!("Sum: {sum}");

    // sort_by
    let mut data = vec![3, 1, 4, 1, 5, 9, 2, 6];
    data.sort_by(|a, b| b.cmp(a));  // reverse sort
    println!("Sorted desc: {:?}", data);

    // for_each
    vec![1, 2, 3].iter().for_each(|x| print!("{x} "));
    println!();
}
```

## A Practical Example: Event System

```rust
type EventHandler = Box<dyn Fn(&str)>;

struct EventEmitter {
    handlers: Vec<(String, EventHandler)>,
}

impl EventEmitter {
    fn new() -> Self {
        EventEmitter { handlers: Vec::new() }
    }

    fn on(&mut self, event: &str, handler: impl Fn(&str) + 'static) {
        self.handlers.push((event.to_string(), Box::new(handler)));
    }

    fn emit(&self, event: &str, data: &str) {
        for (name, handler) in &self.handlers {
            if name == event {
                handler(data);
            }
        }
    }
}

fn main() {
    let mut emitter = EventEmitter::new();

    emitter.on("message", |data| {
        println!("Handler 1 got: {data}");
    });

    emitter.on("message", |data| {
        println!("Handler 2 got: {data}");
    });

    emitter.on("error", |data| {
        eprintln!("ERROR: {data}");
    });

    emitter.emit("message", "hello world");
    emitter.emit("error", "something broke");
    emitter.emit("unknown", "nobody cares");
}
```

The `'static` lifetime bound on the handler means the closure can't borrow local variables — it must own all its data (use `move` if needed). This is common when storing closures for later execution.

## Closures vs. Function Pointers

Regular functions can also be passed where closures are expected:

```rust
fn double(x: i32) -> i32 {
    x * 2
}

fn apply(f: fn(i32) -> i32, x: i32) -> i32 {
    f(x)
}

fn main() {
    // Function pointer
    println!("{}", apply(double, 5));

    // Closure that doesn't capture anything also works
    println!("{}", apply(|x| x * 3, 5));
}
```

`fn(i32) -> i32` is a *function pointer type*. It's less flexible than `impl Fn(i32) -> i32` because it can't accept closures that capture state. Use `Fn` traits unless you specifically need function pointers (e.g., for FFI).

## Key Takeaways

1. Closures are anonymous functions that capture variables from their scope
2. Three capture modes: borrow (`&`), mutable borrow (`&mut`), ownership (`move`)
3. Three traits: `Fn` (immutable), `FnMut` (mutable), `FnOnce` (consuming)
4. Use the most general trait bound your function needs
5. `move` closures take ownership — essential for threads and stored closures
6. The standard library uses closures extensively — mastering them makes iterator chains natural

Next: iterators — where closures and collections meet.
