---
title: "Lesson 3: move Closures — Sending data to threads"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 3
keywords: [Rust, rust, concurrency, move closures, ownership, threads, closure capture]
tags: [Rust tutorial, rust, concurrency]
date: '2024-11-09T14:20:00.000Z'
---

When I first started writing threaded Rust code, I hit the same compiler error about forty times in one afternoon. Something about closures borrowing values that might be dropped. I kept slapping `move` on closures until things compiled, without really understanding what was happening.

That's a terrible way to learn. So here's the actual explanation I wish I'd had.

## The Problem: Closures and Thread Lifetimes

When you pass a closure to `thread::spawn`, the new thread might run for an arbitrary amount of time. It could outlive the function that spawned it. It could outlive the variables it references.

This is a problem:

```rust
use std::thread;

fn main() {
    let name = String::from("Atharva");

    // THIS WON'T COMPILE
    let handle = thread::spawn(|| {
        println!("Hello, {}", name);
    });

    handle.join().unwrap();
}
```

```
error[E0373]: closure may outlive the current function, but it borrows `name`,
which is owned by the current function
```

The closure *borrows* `name`. But `thread::spawn` requires the closure to be `'static` — it must own all its data or only reference things that live forever. Why? Because the compiler can't prove that the spawned thread will finish before `name` is dropped.

In this toy example, we know the thread finishes before `main` exits because we call `join()`. But the *type signature* of `thread::spawn` doesn't encode that guarantee. The signature says: "give me a closure that's `'static + Send`." A borrowed reference to a local variable isn't `'static`.

## The Solution: move

The `move` keyword forces the closure to take ownership of everything it captures:

```rust
use std::thread;

fn main() {
    let name = String::from("Atharva");

    let handle = thread::spawn(move || {
        println!("Hello, {}", name);
        // name is owned by this closure now
    });

    // Can't use name here — ownership moved into the closure
    // println!("{}", name); // ERROR: value used after move

    handle.join().unwrap();
}
```

With `move`, the closure doesn't borrow `name` — it *takes* it. The closure owns the `String`, so it's `'static`. The thread can run as long as it wants.

The trade-off is obvious: you can't use `name` in the main thread after the `move`. Ownership transferred.

## What move Actually Does

Without `move`, Rust closures capture variables by the *least invasive* mechanism needed:

- `&T` (shared reference) if the closure only reads the value
- `&mut T` (mutable reference) if the closure mutates the value
- `T` (ownership) if the closure consumes the value (moves it or drops it)

With `move`, every captured variable is taken by ownership, regardless of how it's used in the closure body.

```rust
fn main() {
    let x = 5;        // i32, which is Copy
    let s = String::from("hello"); // String, which is not Copy

    let closure = move || {
        println!("{} {}", x, s);
    };

    // x is Copy, so it was copied into the closure. Still usable here.
    println!("x is still {}", x);

    // s is not Copy, so it was moved. Can't use it anymore.
    // println!("{}", s); // ERROR: value used after move

    closure();
}
```

Key insight: `move` means "take ownership." For `Copy` types like integers and bools, that means copying. For non-`Copy` types like `String`, `Vec`, and anything heap-allocated, that means actually moving.

## Cloning Before Moving

What if you need the data in both the main thread and the spawned thread? Clone it:

```rust
use std::thread;

fn main() {
    let data = vec![1, 2, 3, 4, 5];

    let data_clone = data.clone();
    let handle = thread::spawn(move || {
        let sum: i32 = data_clone.iter().sum();
        println!("Sum in thread: {}", sum);
    });

    // data is still available here
    println!("Original data: {:?}", data);

    handle.join().unwrap();
}
```

This pattern — clone, then move the clone — is the bread and butter of sending data to threads. Yes, cloning costs something. But it gives you clear ownership semantics: the main thread owns `data`, the spawned thread owns `data_clone`. No sharing, no races, no problems.

## Multiple Threads, Multiple Clones

When spawning several threads that need the same data:

```rust
use std::thread;

fn main() {
    let config = vec![
        String::from("host=localhost"),
        String::from("port=5432"),
        String::from("db=myapp"),
    ];

    let mut handles = vec![];

    for i in 0..4 {
        let my_config = config.clone(); // each thread gets its own copy
        let handle = thread::spawn(move || {
            println!("Thread {} using config: {:?}", i, my_config);
            // do work with my_config
        });
        handles.push(handle);
    }

    for h in handles {
        h.join().unwrap();
    }
}
```

Each thread gets an independent clone. This is simple but wasteful if the data is large and read-only. For shared read-only data, you'll want `Arc` — which we'll cover in lesson 6.

## The Subtlety: Partial Moves

Sometimes you want to move *some* variables but not others. This trips people up:

```rust
use std::thread;

fn main() {
    let name = String::from("worker");
    let id = 42u32;
    let data = vec![1, 2, 3];

    // move captures ALL variables used in the closure
    let handle = thread::spawn(move || {
        println!("{}-{}: {:?}", name, id, data);
    });

    // id was copied (it's Copy), but name and data were moved
    println!("id is still: {}", id); // fine
    // println!("{}", name); // ERROR
    // println!("{:?}", data); // ERROR

    handle.join().unwrap();
}
```

You can't selectively `move` some and borrow others in the same closure. It's all or nothing. If you need a variable both inside and outside the thread, clone the ones you need to keep:

```rust
use std::thread;

fn main() {
    let name = String::from("worker");
    let data = vec![1, 2, 3];

    let thread_name = name.clone(); // clone what we want to keep
    let handle = thread::spawn(move || {
        println!("{}: {:?}", thread_name, data);
    });

    println!("Original name: {}", name); // still works
    // data was moved, can't use it here

    handle.join().unwrap();
}
```

## move with Scoped Threads

Remember `thread::scope` from last lesson? It doesn't require `'static` closures, so `move` is often unnecessary:

```rust
use std::thread;

fn main() {
    let data = vec![1, 2, 3, 4, 5];

    thread::scope(|s| {
        // No move needed — scope guarantees threads finish before data is dropped
        s.spawn(|| {
            println!("Thread sees: {:?}", data);
        });

        s.spawn(|| {
            println!("This thread too: {:?}", data);
        });
    });

    println!("Data still here: {:?}", data);
}
```

This is why scoped threads are so nice — you avoid the clone dance entirely for read-only shared data.

But if you want to *move* data into a scoped thread (maybe to mutate it exclusively), `move` still works:

```rust
use std::thread;

fn main() {
    let data = vec![1, 2, 3];

    thread::scope(|s| {
        s.spawn(move || {
            // data is moved into this thread exclusively
            let sum: i32 = data.iter().sum();
            println!("Sum: {}", sum);
            // data is dropped here
        });

        // Can't access data here anymore — it was moved
    });
}
```

## Common Patterns

**Pattern 1: Producer-consumer setup**

```rust
use std::thread;
use std::sync::mpsc;

fn main() {
    let (tx, rx) = mpsc::channel();
    let data = vec![1, 2, 3, 4, 5];

    // Producer: move data and sender into the thread
    let producer = thread::spawn(move || {
        for item in data {
            tx.send(item * 2).unwrap();
        }
        // tx is dropped here, closing the channel
    });

    // Consumer: move receiver into the thread
    let consumer = thread::spawn(move || {
        let mut results = vec![];
        for val in rx {
            results.push(val);
        }
        results
    });

    producer.join().unwrap();
    let results = consumer.join().unwrap();
    println!("Results: {:?}", results); // [2, 4, 6, 8, 10]
}
```

**Pattern 2: Thread returning owned results**

```rust
use std::thread;

fn process_chunk(chunk: Vec<u32>) -> Vec<u32> {
    chunk.into_iter().map(|x| x * x).collect()
}

fn main() {
    let data: Vec<u32> = (0..100).collect();
    let chunk_size = 25;
    let mut handles = vec![];

    for chunk in data.chunks(chunk_size) {
        let owned_chunk = chunk.to_vec(); // create owned copy
        handles.push(thread::spawn(move || {
            process_chunk(owned_chunk) // move into thread, return result
        }));
    }

    let mut all_results = vec![];
    for h in handles {
        all_results.extend(h.join().unwrap());
    }

    println!("Processed {} items", all_results.len());
}
```

## The Mental Model

Here's how I think about `move` closures with threads:

1. `thread::spawn` needs `'static` — the thread might outlive everything
2. `move` transfers ownership into the closure, making it self-contained
3. `Copy` types get copied, non-`Copy` types get moved (consumed)
4. Clone before the `move` if you need the data in both places
5. Scoped threads (`thread::scope`) let you skip `move` entirely for borrowing

Once this clicks, you stop fighting the compiler and start seeing its errors as design guidance. "You're trying to share this data across threads — how do you want to handle ownership?" That's all it's asking.

---

Next up — channels. Because sometimes threads need to talk to each other, and message passing is the cleanest way to do it.
