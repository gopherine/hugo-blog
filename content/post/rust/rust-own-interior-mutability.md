---
title: "Lesson 11: Interior Mutability — Cell, RefCell, and the Rules"
author: Atharva Pandey
series: "Rust Ownership & Lifetimes Masterclass"
lesson: 11
keywords: [Rust, rust, ownership, lifetimes, interior mutability, Cell, RefCell, UnsafeCell]
tags: [Rust tutorial, rust, ownership, lifetimes]
date: '2024-06-04T11:30:00.000Z'
---

Rust's borrowing rules say you can't mutate through a shared reference. Period. Except — sometimes you need to. Caching, reference counting, lazy initialization, mock objects in tests. Legitimate use cases where the "no mutation through `&T`" rule is too restrictive.

Interior mutability is the escape hatch. It moves the borrow check from compile time to runtime. And yes, it can panic if you get it wrong. That's the tradeoff.

## The Problem: Mutation Through &self

You're building a struct with a method that logically doesn't modify the struct's public state but needs to update some internal bookkeeping:

```rust
struct Cache {
    data: Vec<String>,
    access_count: u64,  // want to increment this on every read
}

impl Cache {
    // Can't increment access_count through &self
    fn get(&self, index: usize) -> Option<&String> {
        // self.access_count += 1;  // ERROR: can't mutate through &self
        self.data.get(index)
    }
}
```

Making `get` take `&mut self` would work, but then callers can't hold multiple references to the cache. That's overly restrictive — reading is conceptually shared.

## Cell<T>: Copy-Based Interior Mutability

`Cell<T>` works for `Copy` types. It lets you get and set the value through a shared reference, but you can never get a reference to the inner value — only copies.

```rust
use std::cell::Cell;

struct Cache {
    data: Vec<String>,
    access_count: Cell<u64>,
}

impl Cache {
    fn new(data: Vec<String>) -> Self {
        Cache {
            data,
            access_count: Cell::new(0),
        }
    }

    fn get(&self, index: usize) -> Option<&String> {
        self.access_count.set(self.access_count.get() + 1);
        self.data.get(index)
    }

    fn accesses(&self) -> u64 {
        self.access_count.get()
    }
}

fn main() {
    let cache = Cache::new(vec![
        String::from("alpha"),
        String::from("beta"),
        String::from("gamma"),
    ]);

    println!("{:?}", cache.get(0));
    println!("{:?}", cache.get(1));
    println!("{:?}", cache.get(0));
    println!("Total accesses: {}", cache.accesses());
    // Total accesses: 3
}
```

`Cell` is zero-cost at runtime — no reference counting, no locking. The constraint is that you can only copy values in and out. No borrowing the inner value. That's why it only works well with small `Copy` types like integers, booleans, and `Option<NonNull<T>>`.

## RefCell<T>: Runtime Borrow Checking

`RefCell<T>` is the heavy hitter. It works with any type and lets you borrow the inner value — but it checks the borrow rules at runtime instead of compile time.

```rust
use std::cell::RefCell;

struct Document {
    content: String,
    render_cache: RefCell<Option<String>>,
}

impl Document {
    fn new(content: String) -> Self {
        Document {
            content,
            render_cache: RefCell::new(None),
        }
    }

    fn render(&self) -> String {
        // Check if we have a cached render
        if let Some(cached) = self.render_cache.borrow().as_ref() {
            return cached.clone();
        }

        // Expensive rendering
        let rendered = format!("<p>{}</p>", self.content);

        // Cache the result — mutating through &self!
        *self.render_cache.borrow_mut() = Some(rendered.clone());

        rendered
    }
}

fn main() {
    let doc = Document::new(String::from("Hello world"));

    println!("{}", doc.render());  // computes and caches
    println!("{}", doc.render());  // returns cached version
}
```

`borrow()` returns a `Ref<T>` — like `&T` but runtime-checked. `borrow_mut()` returns a `RefMut<T>` — like `&mut T` but runtime-checked.

## The Runtime Panic

The borrow rules still apply — they're just checked at runtime. Violate them and you get a panic, not a compile error:

```rust
use std::cell::RefCell;

fn main() {
    let data = RefCell::new(vec![1, 2, 3]);

    let borrow1 = data.borrow();     // immutable borrow — ok
    let borrow2 = data.borrow();     // second immutable borrow — ok

    // let mut_borrow = data.borrow_mut();  // PANIC: already borrowed immutably

    println!("{:?} {:?}", borrow1, borrow2);
    // borrow1 and borrow2 dropped here

    let mut_borrow = data.borrow_mut();  // now it's fine
    println!("{:?}", mut_borrow);
}
```

To avoid panics, you can use `try_borrow()` and `try_borrow_mut()`:

```rust
use std::cell::RefCell;

fn main() {
    let data = RefCell::new(42);

    let borrow = data.borrow();

    match data.try_borrow_mut() {
        Ok(mut val) => *val += 1,
        Err(_) => println!("Can't borrow mutably — already borrowed"),
    }

    drop(borrow);

    // Now we can borrow mutably
    *data.borrow_mut() += 1;
    println!("{}", data.borrow());  // 43
}
```

## The Classic Pattern: Rc<RefCell<T>>

You'll see this combination everywhere. `Rc` for shared ownership, `RefCell` for interior mutability:

```rust
use std::cell::RefCell;
use std::rc::Rc;

#[derive(Debug)]
struct Node {
    value: i32,
    children: Vec<Rc<RefCell<Node>>>,
}

impl Node {
    fn new(value: i32) -> Rc<RefCell<Self>> {
        Rc::new(RefCell::new(Node {
            value,
            children: Vec::new(),
        }))
    }
}

fn main() {
    let root = Node::new(1);
    let child_a = Node::new(2);
    let child_b = Node::new(3);

    // Add children to root — mutating through shared references
    root.borrow_mut().children.push(Rc::clone(&child_a));
    root.borrow_mut().children.push(Rc::clone(&child_b));

    // Add a child to child_a
    child_a.borrow_mut().children.push(Node::new(4));

    // Read the tree
    println!("Root: {}", root.borrow().value);
    for child in &root.borrow().children {
        println!("  Child: {}", child.borrow().value);
        for grandchild in &child.borrow().children {
            println!("    Grandchild: {}", grandchild.borrow().value);
        }
    }
}
```

This is Rust's equivalent of a garbage-collected mutable object graph. It works, but it's not zero-cost — there's reference counting overhead and runtime borrow checking. If you find yourself reaching for `Rc<RefCell<T>>` a lot, consider whether a different data structure would work.

## Cell vs RefCell: When to Use Which

**Use `Cell<T>` when:**
- `T` is `Copy`
- You only need get/set, not borrowing
- You want zero overhead
- Common for: counters, flags, `Option<NonNull<T>>` in linked data structures

**Use `RefCell<T>` when:**
- `T` is not `Copy`
- You need to borrow the inner value
- You're okay with runtime panics on borrow violations
- Common for: caches, lazy values, mutable fields in immutably-borrowed structs

## OnceCell and LazyCell

For one-time initialization — a very common interior mutability pattern:

```rust
use std::cell::OnceCell;

struct Config {
    raw: String,
    parsed: OnceCell<Vec<(String, String)>>,
}

impl Config {
    fn new(raw: String) -> Self {
        Config {
            raw,
            parsed: OnceCell::new(),
        }
    }

    fn entries(&self) -> &Vec<(String, String)> {
        self.parsed.get_or_init(|| {
            // Parse on first access, cache the result
            self.raw
                .lines()
                .filter_map(|line| {
                    let mut parts = line.splitn(2, '=');
                    Some((
                        parts.next()?.trim().to_string(),
                        parts.next()?.trim().to_string(),
                    ))
                })
                .collect()
        })
    }
}

fn main() {
    let config = Config::new("host = localhost\nport = 8080".to_string());

    // First call: parses
    println!("{:?}", config.entries());
    // Second call: returns cached
    println!("{:?}", config.entries());
}
```

`OnceCell` is like a `RefCell` that can only be written once. No panic risk after initialization.

## UnsafeCell: The Foundation

Under the hood, `Cell`, `RefCell`, and every other interior mutability type is built on `UnsafeCell<T>`. It's the only way to get a mutable pointer through a shared reference in Rust — everything else is undefined behavior.

```rust
// You almost never write this directly
use std::cell::UnsafeCell;

struct MyCell<T> {
    value: UnsafeCell<T>,
}

impl<T: Copy> MyCell<T> {
    fn new(value: T) -> Self {
        MyCell {
            value: UnsafeCell::new(value),
        }
    }

    fn get(&self) -> T {
        unsafe { *self.value.get() }
    }

    fn set(&self, value: T) {
        unsafe { *self.value.get() = value; }
    }
}
```

Don't write your own `UnsafeCell` wrappers unless you're building a synchronization primitive. Use `Cell` and `RefCell` — they're safe and correct.

## The Design Principle

Interior mutability isn't a workaround — it's a deliberate tool. The borrow checker's compile-time rules are conservative. They reject some valid programs. Interior mutability lets you opt into runtime checking for the specific cases where compile-time checking is too strict.

But treat it as a scalpel, not a sledgehammer. If every field in your struct is wrapped in `RefCell`, you've essentially turned off the borrow checker. At that point you're writing JavaScript with extra steps.

Use it for caching, lazy initialization, and the occasional shared mutable state. Keep the rest of your code under the borrow checker's watchful eye. That's where the real safety comes from.
