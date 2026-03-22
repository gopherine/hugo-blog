---
title: "Lesson 9: Send and Sync — The traits behind thread safety"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 9
keywords: [Rust, rust, concurrency, Send, Sync, thread safety, marker traits, auto traits]
tags: [Rust tutorial, rust, concurrency]
date: '2024-11-21T09:40:00.000Z'
---

There's a moment in every Rust developer's life when they try to send an `Rc<RefCell<Vec<String>>>` to another thread and get a wall of compiler errors. They Google the error, see something about `Send` and `Sync`, patch the type to `Arc<Mutex<Vec<String>>>`, and move on without understanding why.

I did exactly that for six months. Then I needed to write a custom type that crossed thread boundaries, and I had to actually learn what these traits mean. Turns out they're beautifully simple once you see the design.

## What Are Send and Sync?

They're marker traits — they have no methods. They exist only to tell the compiler what's allowed to cross thread boundaries.

```rust
// From std::marker (simplified)
pub unsafe auto trait Send { }
pub unsafe auto trait Sync { }
```

Three important words there: `unsafe`, `auto`, and `trait`.

- **`auto`**: The compiler automatically implements these for types that qualify. You don't write `impl Send for MyStruct` by hand (usually).
- **`unsafe`**: Implementing them manually requires `unsafe` because getting it wrong can cause data races.
- **marker trait**: No methods. Pure type-level information.

### Send

A type `T` is `Send` if ownership of a `T` value can be safely transferred to another thread.

Almost everything is `Send`. The main exception: `Rc<T>`. Because `Rc` uses non-atomic reference counting, moving it to another thread means two threads could modify the reference count simultaneously — a data race.

### Sync

A type `T` is `Sync` if a `&T` (shared reference) can be safely used from multiple threads simultaneously.

Equivalently: `T` is `Sync` if and only if `&T` is `Send`.

Types that allow interior mutability without synchronization — like `Cell<T>` and `RefCell<T>` — are not `Sync`. Multiple threads holding `&RefCell<T>` could call `borrow_mut()` simultaneously, breaking the single-mutable-reference invariant.

## The Rules

| Type | Send? | Sync? | Why |
|------|-------|-------|-----|
| `i32`, `String`, `Vec<T>` | Yes | Yes | Plain data, no special semantics |
| `Rc<T>` | **No** | **No** | Non-atomic reference counting |
| `Arc<T>` (where T: Send + Sync) | Yes | Yes | Atomic reference counting |
| `Mutex<T>` (where T: Send) | Yes | Yes | Lock provides synchronization |
| `RwLock<T>` (where T: Send + Sync) | Yes | Yes | Lock provides synchronization |
| `Cell<T>` | Yes | **No** | Interior mutability without synchronization |
| `RefCell<T>` | Yes | **No** | Runtime borrow checking, not thread-safe |
| `MutexGuard<T>` | **No** | Yes | Must be unlocked on the same thread (on some platforms) |
| Raw pointers (`*const T`, `*mut T`) | **No** | **No** | No safety guarantees |

## How the Compiler Uses These

`thread::spawn` requires the closure to be `Send`:

```rust
// Simplified signature
pub fn spawn<F, T>(f: F) -> JoinHandle<T>
where
    F: FnOnce() -> T + Send + 'static,
    T: Send + 'static,
```

Both the closure `F` and its return type `T` must be `Send`. This means everything the closure captures (via `move`) must be `Send`, and whatever it returns must be `Send`.

The compiler derives `Send` automatically for your types. A struct is `Send` if all its fields are `Send`. If one field isn't `Send`, the whole struct isn't:

```rust
use std::rc::Rc;

struct MyStruct {
    name: String,         // Send + Sync
    count: Rc<u32>,       // NOT Send, NOT Sync
}

// MyStruct is NOT Send because it contains an Rc
// thread::spawn(move || { use_my_struct(my_struct); }); // WON'T COMPILE
```

## Demonstrating the Errors

### Rc is not Send

```rust
use std::rc::Rc;
use std::thread;

fn main() {
    let data = Rc::new(42);

    // WON'T COMPILE
    thread::spawn(move || {
        println!("{}", data);
    });
}
```

```
error[E0277]: `Rc<i32>` cannot be sent between threads safely
  --> src/main.rs:7:5
   |
   = help: the trait `Send` is not implemented for `Rc<i32>`
```

Fix: use `Arc` instead.

### RefCell is not Sync

```rust
use std::cell::RefCell;
use std::thread;

fn main() {
    let data = RefCell::new(vec![1, 2, 3]);

    thread::scope(|s| {
        // WON'T COMPILE — &RefCell is not Send (because RefCell is not Sync)
        s.spawn(|| {
            let d = data.borrow();
            println!("{:?}", *d);
        });
    });
}
```

```
error[E0277]: `RefCell<Vec<i32>>` cannot be shared between threads safely
   = help: the trait `Sync` is not implemented for `RefCell<Vec<i32>>`
```

Fix: use `Mutex` for interior mutability across threads.

## Building Send and Sync Types

When you create your own types, `Send` and `Sync` are derived automatically:

```rust
use std::sync::{Arc, Mutex};

// Automatically Send + Sync because all fields are Send + Sync
struct AppState {
    config: Arc<String>,
    counter: Mutex<u64>,
    name: String,
}

// Automatically Send but NOT Sync because Vec<Cell<T>> isn't Sync
// Actually wait — Cell is Send, and Vec<Cell<T>> is also Send
// But Vec<Cell<T>> is NOT Sync because Cell<T> is not Sync

use std::cell::Cell;

struct PerThreadState {
    values: Vec<Cell<i32>>, // Send but not Sync
}
// PerThreadState is Send but not Sync
```

## Manually Implementing Send/Sync

Sometimes you wrap a raw pointer and need to tell the compiler it's safe. This requires `unsafe`:

```rust
struct SharedBuffer {
    ptr: *mut u8,
    len: usize,
}

// "I promise this pointer is safe to send between threads"
unsafe impl Send for SharedBuffer {}

// "I promise this pointer is safe to reference from multiple threads"
unsafe impl Sync for SharedBuffer {}
```

**Only do this if you actually know what you're doing.** You're telling the compiler "trust me" — and if you're wrong, you get undefined behavior. The compiler's safety guarantees are only as strong as your `unsafe impl`.

Legitimate reasons to implement `Send`/`Sync` manually:

- Wrapping a C library pointer that you know is thread-safe
- Building a custom synchronization primitive
- FFI types where the Rust compiler can't infer thread safety

## Negative Implementations

You can also explicitly *opt out* of `Send` or `Sync`:

```rust
struct NotSendable {
    data: i32,
    _marker: std::marker::PhantomData<*const ()>,
    // *const () is not Send or Sync
    // PhantomData makes the struct act as if it contains one
}

// NotSendable is now NOT Send and NOT Sync
// even though its "real" data (i32) would normally be both
```

The `PhantomData<*const ()>` trick is the standard way to make a type non-`Send`/non-`Sync` without actually storing a raw pointer. You'll see this in `MutexGuard` and similar types that must be used on a specific thread.

## The Relationship Between Send and Sync

There's an elegant relationship:

- `T: Sync` implies `&T: Send` — if sharing a reference is safe, sending a reference is safe
- `T: Send` does NOT imply `T: Sync` — ownership transfer is safe, but sharing might not be (e.g., `Cell<T>`)

And for `Mutex<T>`:

```rust
// Mutex<T> is Sync if T is Send
// Why? Because Mutex ensures only one thread accesses T at a time
// So sharing the Mutex (Sync) is safe as long as T can be sent to
// whichever thread acquires the lock (Send)
```

This is why `Mutex<T>` only requires `T: Send`, not `T: Send + Sync`. The mutex *provides* the synchronization — the inner type just needs to be transferable.

## Practical Implications

### Your struct has all Send+Sync fields? You're done.

```rust
use std::sync::{Arc, Mutex};

struct Server {
    host: String,
    port: u16,
    connections: Arc<Mutex<Vec<String>>>,
}

// Server is automatically Send + Sync
// You can share it across threads freely
```

### One non-Send field ruins everything

```rust
use std::rc::Rc;

struct BadServer {
    host: String,
    port: u16,
    cache: Rc<Vec<String>>, // not Send
}

// BadServer is NOT Send. Can't cross thread boundaries.
// Fix: change Rc to Arc
```

### The compiler gives clear errors

This is where Rust shines compared to, say, C++. In C++, you can pass anything to a thread — the language doesn't care. You find out it's broken at 3 AM when production is on fire.

In Rust, if your type can't safely cross thread boundaries, you know at compile time. The error message tells you exactly which field is the problem. Fix it or restructure — before any code runs.

## The Bigger Picture

`Send` and `Sync` are what make "fearless concurrency" possible. They're the mechanism by which Rust's type system encodes thread safety. Without them, Rust would need to either:

1. Make everything thread-safe (expensive — every reference count would need to be atomic)
2. Make nothing thread-safe by default (dangerous — like C++)

Instead, Rust tracks thread safety per-type. You use `Rc` for single-threaded scenarios (cheaper), `Arc` for multi-threaded scenarios (safe). The compiler ensures you don't mix them up.

---

Next — Rayon, where data parallelism becomes a one-line change to your existing iterator code.
