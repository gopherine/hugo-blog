---
title: "Lesson 8: 'static — Not What You Think"
author: Atharva Pandey
series: "Rust Ownership & Lifetimes Masterclass"
lesson: 8
keywords: [Rust, rust, ownership, lifetimes, static lifetime, static references]
tags: [Rust tutorial, rust, ownership, lifetimes]
date: '2024-05-29T13:25:00.000Z'
---

`'static` is the most misunderstood lifetime in Rust. Most people think it means "lives forever" or "global variable." It kind of does — but also doesn't. And the way it interacts with trait bounds will surprise you.

I've seen experienced Rust developers get this wrong. So don't feel bad if it's been confusing.

## What 'static Actually Means

`'static` means: "this reference is valid for the entire duration of the program."

Two things satisfy `'static`:

1. **References to data baked into the binary** — string literals, const values
2. **Owned data** — yes, really. A `String` satisfies `T: 'static`. Not `&'static String` — just the bound `T: 'static`.

That second point is where the confusion lives.

## String Literals Are 'static

```rust
fn main() {
    let s: &'static str = "hello world";
    // This string literal is embedded in the binary
    // It exists for the entire program — truly 'static
    println!("{}", s);
}
```

The bytes `"hello world"` live in the read-only data section of your compiled binary. They're not on the stack, not on the heap. They exist from the moment the program starts until it ends. That's as `'static` as it gets.

## Owned Data Satisfies 'static Bounds

This is the mind-bender. Consider:

```rust
fn takes_static<T: 'static>(value: T) {
    // T is owned, not a reference
    // 'static here means "T contains no non-'static references"
    println!("got a value");
}

fn main() {
    let s = String::from("hello");
    takes_static(s);  // works! String: 'static

    let n: i32 = 42;
    takes_static(n);  // works! i32: 'static

    let v = vec![1, 2, 3];
    takes_static(v);  // works! Vec<i32>: 'static
}
```

Wait — `String` is `'static`? It lives on the heap and gets dropped!

Yes. The bound `T: 'static` doesn't mean "T lives forever." It means "T can live as long as needed — it doesn't borrow from anything with a limited lifetime." An owned `String` satisfies this because it owns its data. It *could* live forever if you wanted it to.

## 'static Reference vs 'static Bound

These are different things:

```rust
// 'static REFERENCE: the data behind this reference lives forever
fn takes_static_ref(s: &'static str) {
    println!("{}", s);
}

// 'static BOUND: T doesn't contain non-'static references
fn takes_static_bound<T: 'static>(value: T) {
    println!("got a value");
}

fn main() {
    // 'static reference — only string literals (and leaked data) work
    takes_static_ref("hello");  // fine — literal is 'static

    let s = String::from("hello");
    // takes_static_ref(&s);  // ERROR: &s is not 'static

    // 'static bound — owned data works
    takes_static_bound(String::from("hello"));  // fine
    takes_static_bound(42_i32);                  // fine
    takes_static_bound(vec![1, 2, 3]);           // fine
}
```

## Why This Matters: Thread Spawning

The most common place you'll encounter `'static` bounds is `std::thread::spawn`:

```rust
use std::thread;

fn main() {
    let data = String::from("hello from thread");

    // thread::spawn requires F: 'static
    // The closure must own its data — can't borrow from the stack
    let handle = thread::spawn(move || {
        println!("{}", data);
    });

    handle.join().unwrap();
}
```

Why `'static`? Because a spawned thread might outlive the function that created it. If the closure borrowed stack data, that data could be gone while the thread is still running. The `'static` bound prevents this — the closure must own everything it captures (via `move`) or only reference `'static` data.

## The Problem: Accidental 'static Requirements

```rust
struct Config<'a> {
    name: &'a str,
}

fn spawn_worker(config: Config<'_>) {
    // ERROR: Config borrows data, doesn't satisfy 'static
    // std::thread::spawn(move || {
    //     println!("Worker: {}", config.name);
    // });
}
```

The fix: make Config own its data if it needs to cross thread boundaries.

```rust
struct Config {
    name: String,
}

fn spawn_worker(config: Config) {
    std::thread::spawn(move || {
        println!("Worker: {}", config.name);
    });
}

fn main() {
    let config = Config {
        name: String::from("worker-1"),
    };
    spawn_worker(config);
    std::thread::sleep(std::time::Duration::from_millis(100));
}
```

## Creating 'static References at Runtime

Sometimes you genuinely need a `&'static` reference to runtime data. There are a few ways:

### Box::leak

```rust
fn main() {
    let s: &'static str = Box::leak(String::from("leaked").into_boxed_str());
    println!("{}", s);
    // Memory is never freed — use sparingly!
}
```

`Box::leak` intentionally leaks heap memory, giving you a `'static` reference. The memory is never freed. This is useful for one-time initialization of global-ish data, but obviously don't do it in a loop.

### lazy_static / once_cell / std::sync::LazyLock

```rust
use std::sync::LazyLock;

static CONFIG: LazyLock<String> = LazyLock::new(|| {
    // Expensive initialization — runs once
    String::from("production")
});

fn main() {
    println!("Config: {}", *CONFIG);
    println!("Config: {}", *CONFIG);  // reuses the same value
}
```

`LazyLock` (stabilized in Rust 1.80) gives you a `'static` reference to lazily-initialized data. The initialization runs exactly once, thread-safely.

## 'static in Trait Objects

Trait objects often carry a `'static` bound:

```rust
// This is common
fn process(handler: Box<dyn Fn() + 'static>) {
    handler();
}

// Without 'static, the trait object might borrow short-lived data
fn process_any<'a>(handler: Box<dyn Fn() + 'a>) {
    handler();
}

fn main() {
    let name = String::from("hello");

    // This works — closure owns the cloned string
    process(Box::new({
        let name = name.clone();
        move || println!("{}", name)
    }));

    // This also works — closure borrows, but has a limited lifetime
    process_any(Box::new(|| println!("{}", name)));
}
```

When you see `dyn Trait + 'static`, it means "the concrete type behind this trait object doesn't borrow from anything with a limited lifetime." This is the default for `Box<dyn Trait>` actually — the `'static` is implied.

## Common Mistakes

### Mistake 1: Thinking 'static means immutable

```rust
use std::sync::Mutex;
use std::sync::LazyLock;

static COUNTER: LazyLock<Mutex<u32>> = LazyLock::new(|| Mutex::new(0));

fn main() {
    *COUNTER.lock().unwrap() += 1;
    *COUNTER.lock().unwrap() += 1;
    println!("Counter: {}", COUNTER.lock().unwrap());
    // Output: Counter: 2
    // 'static doesn't mean immutable — you can mutate through Mutex
}
```

### Mistake 2: Using 'static when you mean "any lifetime"

```rust
// DON'T: overly restrictive
fn first_char(s: &'static str) -> char {
    s.chars().next().unwrap()
}

// DO: accept any lifetime
fn first_char(s: &str) -> char {
    s.chars().next().unwrap()
}
```

Requiring `&'static str` means you can only pass string literals and leaked strings. That's almost never what you want.

### Mistake 3: Fighting 'static bounds instead of changing data ownership

When the compiler says something needs to be `'static`, the answer is usually "own your data" rather than "figure out how to make a `'static` reference." Move the data. Clone if needed. Stop borrowing.

## The Mental Model

Think of `'static` as "self-contained." A `'static` type doesn't depend on any borrowed data that could disappear. It stands on its own.

- `String` is self-contained — it owns its bytes
- `i32` is self-contained — it's just a number
- `&'static str` is self-contained — the data lives in the binary
- `&str` (non-static) is NOT self-contained — it points to someone else's data

When a function requires `T: 'static`, it's saying: "give me something self-contained. I might hold onto it for a while, and I need to know it won't disappear."

That's less scary than "'static" sounds. It's just ownership by another name.
