---
title: "Lesson 12: Rc and Arc — Shared Ownership"
author: Atharva Pandey
series: "Rust Ownership & Lifetimes Masterclass"
lesson: 12
keywords: [Rust, rust, ownership, lifetimes, Rc, Arc, shared ownership, reference counting]
tags: [Rust tutorial, rust, ownership, lifetimes]
date: '2024-06-07T17:50:00.000Z'
---

Rust's ownership model says every value has one owner. But what happens when multiple parts of your program genuinely need to own the same data? Trees with shared nodes. Graphs with cycles. Observer patterns. Caches shared between subsystems.

Single ownership doesn't fit everything. `Rc` and `Arc` are Rust's answer — reference-counted smart pointers that enable shared ownership with deterministic cleanup.

## The Problem: Multiple Owners

```rust
// This doesn't work — who owns the shared config?
struct ServiceA {
    config: AppConfig,  // owns it
}

struct ServiceB {
    config: AppConfig,  // also owns a copy? expensive clone
}
```

You could pass references, but then you're dealing with lifetimes everywhere. And if the config needs to outlive any individual service, you need `'static` or owned data.

```rust
// References work but add lifetime complexity
struct ServiceA<'a> {
    config: &'a AppConfig,
}

struct ServiceB<'a> {
    config: &'a AppConfig,
}
// Now everything that holds a service needs lifetime parameters too
// It cascades...
```

## Rc: Reference Counted Pointer (Single-Threaded)

`Rc<T>` (Reference Counted) lets multiple owners share the same heap-allocated data. When the last owner is dropped, the data is cleaned up.

```rust
use std::rc::Rc;

#[derive(Debug)]
struct AppConfig {
    database_url: String,
    max_connections: u32,
}

struct ServiceA {
    config: Rc<AppConfig>,
}

struct ServiceB {
    config: Rc<AppConfig>,
}

fn main() {
    let config = Rc::new(AppConfig {
        database_url: String::from("postgres://localhost/mydb"),
        max_connections: 10,
    });

    println!("Reference count: {}", Rc::strong_count(&config));  // 1

    let service_a = ServiceA {
        config: Rc::clone(&config),
    };
    println!("Reference count: {}", Rc::strong_count(&config));  // 2

    let service_b = ServiceB {
        config: Rc::clone(&config),
    };
    println!("Reference count: {}", Rc::strong_count(&config));  // 3

    println!("A uses: {}", service_a.config.database_url);
    println!("B uses: {}", service_b.config.database_url);

    drop(service_a);
    println!("After dropping A: {}", Rc::strong_count(&config));  // 2

    drop(service_b);
    println!("After dropping B: {}", Rc::strong_count(&config));  // 1

    // config is still valid — last remaining Rc
    println!("Config still alive: {}", config.database_url);
}
```

`Rc::clone` doesn't deep-copy the data. It increments the reference count and returns a new `Rc` pointing to the same allocation. Cheap — just an integer increment.

## Rc Is Not Thread-Safe

This is the critical constraint. `Rc` uses non-atomic reference counting. If two threads tried to increment/decrement the count simultaneously, you'd get a data race. Rust prevents this at compile time:

```rust
use std::rc::Rc;

fn main() {
    let data = Rc::new(42);

    // ERROR: Rc cannot be sent between threads safely
    // std::thread::spawn(move || {
    //     println!("{}", data);
    // });
}
```

The compiler knows `Rc` isn't `Send` and refuses to let it cross thread boundaries.

## Arc: Atomic Reference Counted (Thread-Safe)

`Arc<T>` is the thread-safe version. It uses atomic operations to update the reference count:

```rust
use std::sync::Arc;
use std::thread;

fn main() {
    let config = Arc::new(vec![1, 2, 3, 4, 5]);

    let mut handles = vec![];

    for i in 0..5 {
        let config = Arc::clone(&config);
        let handle = thread::spawn(move || {
            let sum: i32 = config.iter().sum();
            println!("Thread {}: sum = {}", i, sum);
        });
        handles.push(handle);
    }

    for handle in handles {
        handle.join().unwrap();
    }

    println!("Original still alive: {:?}", config);
}
```

`Arc::clone` is slightly more expensive than `Rc::clone` — atomic operations have overhead. That's why both exist. Use `Rc` when you know you're single-threaded. Use `Arc` when you need cross-thread sharing.

## Rc and Arc Are Immutable

Both `Rc` and `Arc` give you shared ownership, but the data is immutable through the smart pointer:

```rust
use std::rc::Rc;

fn main() {
    let data = Rc::new(String::from("hello"));

    // data.push_str(" world");  // ERROR: Rc<String> doesn't have push_str
    // You'd need &mut String, but Rc only gives you &String
}
```

Why? Because if multiple owners could mutate the data, you'd have aliased mutable references — exactly what Rust's borrow rules prevent.

For shared mutable data, combine with interior mutability:

```rust
use std::rc::Rc;
use std::cell::RefCell;

fn main() {
    let data = Rc::new(RefCell::new(vec![1, 2, 3]));

    let data2 = Rc::clone(&data);

    data.borrow_mut().push(4);
    data2.borrow_mut().push(5);

    println!("{:?}", data.borrow());  // [1, 2, 3, 4, 5]
}
```

For threads, use `Arc<Mutex<T>>`:

```rust
use std::sync::{Arc, Mutex};
use std::thread;

fn main() {
    let counter = Arc::new(Mutex::new(0));
    let mut handles = vec![];

    for _ in 0..10 {
        let counter = Arc::clone(&counter);
        let handle = thread::spawn(move || {
            let mut num = counter.lock().unwrap();
            *num += 1;
        });
        handles.push(handle);
    }

    for handle in handles {
        handle.join().unwrap();
    }

    println!("Final count: {}", *counter.lock().unwrap());  // 10
}
```

## Building a Shared Graph

Here's a practical example — a directed graph with shared nodes:

```rust
use std::cell::RefCell;
use std::rc::Rc;

type NodeRef = Rc<RefCell<GraphNode>>;

#[derive(Debug)]
struct GraphNode {
    label: String,
    edges: Vec<NodeRef>,
}

impl GraphNode {
    fn new(label: &str) -> NodeRef {
        Rc::new(RefCell::new(GraphNode {
            label: label.to_string(),
            edges: Vec::new(),
        }))
    }

    fn add_edge(from: &NodeRef, to: &NodeRef) {
        from.borrow_mut().edges.push(Rc::clone(to));
    }
}

fn print_graph(node: &NodeRef, visited: &mut Vec<String>, depth: usize) {
    let n = node.borrow();
    let indent = "  ".repeat(depth);

    if visited.contains(&n.label) {
        println!("{}{} (already visited)", indent, n.label);
        return;
    }

    println!("{}{}", indent, n.label);
    visited.push(n.label.clone());

    for edge in &n.edges {
        print_graph(edge, visited, depth + 1);
    }
}

fn main() {
    let a = GraphNode::new("A");
    let b = GraphNode::new("B");
    let c = GraphNode::new("C");

    GraphNode::add_edge(&a, &b);
    GraphNode::add_edge(&a, &c);
    GraphNode::add_edge(&b, &c);

    println!("Graph from A:");
    print_graph(&a, &mut Vec::new(), 0);

    println!("\nReference counts:");
    println!("A: {}", Rc::strong_count(&a));  // 1
    println!("B: {}", Rc::strong_count(&b));  // 2 (a and original)
    println!("C: {}", Rc::strong_count(&c));  // 3 (a, b, and original)
}
```

## Performance Considerations

**Rc/Arc allocation:** heap allocation on creation. The reference count is stored alongside the data.

**Clone cost:** incrementing an integer (Rc) or an atomic integer (Arc). Trivial.

**Drop cost:** decrementing the counter and conditionally freeing. The last drop does the deallocation.

**Arc's atomic overhead:** atomic operations require CPU cache synchronization. In tight loops, this matters. In most application code, it doesn't.

```rust
use std::rc::Rc;
use std::sync::Arc;

fn main() {
    // Rc: ~0.5ns per clone on modern hardware
    let rc = Rc::new(42);
    let _clones: Vec<_> = (0..1000).map(|_| Rc::clone(&rc)).collect();

    // Arc: ~5ns per clone (atomic overhead)
    let arc = Arc::new(42);
    let _clones: Vec<_> = (0..1000).map(|_| Arc::clone(&arc)).collect();

    // Box: no clone — single owner, zero count overhead
    let _boxed = Box::new(42);
}
```

## Rc::make_mut — Copy on Write

`Rc` has a neat trick: `make_mut` gives you `&mut T` by either mutating in place (if the reference count is 1) or cloning the data and giving you a new unique `Rc`:

```rust
use std::rc::Rc;

fn main() {
    let mut data = Rc::new(vec![1, 2, 3]);
    println!("Count before: {}", Rc::strong_count(&data));

    let shared = Rc::clone(&data);  // count is now 2
    println!("Count after clone: {}", Rc::strong_count(&data));

    // make_mut sees count > 1, so it clones the inner data
    // and makes `data` point to the new allocation
    Rc::make_mut(&mut data).push(4);

    println!("data: {:?}", data);    // [1, 2, 3, 4]
    println!("shared: {:?}", shared); // [1, 2, 3] — unmodified

    // Now data has count 1, shared has count 1
    println!("data count: {}", Rc::strong_count(&data));    // 1
    println!("shared count: {}", Rc::strong_count(&shared)); // 1
}
```

This is copy-on-write semantics — efficient sharing until mutation is needed.

## When to Use What

| Situation | Use |
|-----------|-----|
| Single owner, single thread | `Box<T>` |
| Multiple owners, single thread | `Rc<T>` |
| Multiple owners, multi-thread, immutable | `Arc<T>` |
| Multiple owners, single thread, mutable | `Rc<RefCell<T>>` |
| Multiple owners, multi-thread, mutable | `Arc<Mutex<T>>` or `Arc<RwLock<T>>` |

Start with the simplest option that works. `Box` over `Rc`. `Rc` over `Arc`. Only add `RefCell`/`Mutex` when you need mutation.

## The Memory Leak Problem

`Rc` and `Arc` have a known weakness: reference cycles leak memory. If A references B and B references A, neither reference count ever reaches zero. Neither gets dropped. Memory leak.

That's what the next lesson covers — `Weak` references, the solution to reference cycles.
