---
title: "Lesson 13: Weak References — Breaking Cycles"
author: Atharva Pandey
series: "Rust Ownership & Lifetimes Masterclass"
lesson: 13
keywords: [Rust, rust, ownership, lifetimes, Weak, reference cycles, memory leak]
tags: [Rust tutorial, rust, ownership, lifetimes]
date: '2024-06-10T09:15:00.000Z'
---

Reference counting has a fatal flaw: cycles. If A owns B and B owns A, neither reference count hits zero. The memory is leaked — not freed when it should be, not freed ever. In a garbage-collected language, the GC would detect this cycle and clean it up. Rust's `Rc`/`Arc` can't do that.

`Weak` references are the solution. And honestly, if you understand why they exist, you understand half of what makes garbage collectors complex.

## The Problem: Reference Cycles

```rust
use std::cell::RefCell;
use std::rc::Rc;

#[derive(Debug)]
struct Node {
    value: i32,
    next: Option<Rc<RefCell<Node>>>,
}

fn main() {
    let a = Rc::new(RefCell::new(Node { value: 1, next: None }));
    let b = Rc::new(RefCell::new(Node { value: 2, next: None }));

    // a -> b
    a.borrow_mut().next = Some(Rc::clone(&b));
    // b -> a  (CYCLE!)
    b.borrow_mut().next = Some(Rc::clone(&a));

    println!("a count: {}", Rc::strong_count(&a));  // 2 (a + b's reference)
    println!("b count: {}", Rc::strong_count(&b));  // 2 (b + a's reference)

    // When main ends:
    // drop(b) → b's count goes from 2 to 1 (still alive)
    // drop(a) → a's count goes from 2 to 1 (still alive)
    // Neither reaches 0. Memory leaked. Drop never called.
}
```

This is a genuine memory leak. No dangling pointers, no crashes — the data just sits in memory forever, unreachable and unfreeable. In a long-running server, these add up.

## Weak<T>: Non-Owning References

`Weak<T>` is a reference that doesn't increment the strong count. It doesn't keep the data alive. If all strong references are dropped, the data is freed — even if weak references still exist.

```rust
use std::rc::{Rc, Weak};

fn main() {
    let strong = Rc::new(String::from("hello"));
    let weak: Weak<String> = Rc::downgrade(&strong);

    println!("Strong count: {}", Rc::strong_count(&strong));  // 1
    println!("Weak count: {}", Rc::weak_count(&strong));      // 1

    // Upgrade weak to access the data
    if let Some(value) = weak.upgrade() {
        println!("Value: {}", value);
    }

    drop(strong);  // strong count → 0, data is freed

    // Weak reference is now dangling — upgrade returns None
    assert!(weak.upgrade().is_none());
    println!("Data was freed, weak reference expired");
}
```

The key API:
- `Rc::downgrade(&rc)` — creates a `Weak` from an `Rc`
- `weak.upgrade()` — returns `Option<Rc<T>>`. `Some` if data still alive, `None` if freed.

## Breaking the Cycle

Back to our cycle problem. The fix: make one direction of the cycle use `Weak`:

```rust
use std::cell::RefCell;
use std::rc::{Rc, Weak};

#[derive(Debug)]
struct Node {
    value: i32,
    children: Vec<Rc<RefCell<Node>>>,
    parent: Option<Weak<RefCell<Node>>>,  // Weak! Doesn't prevent parent from being freed
}

impl Node {
    fn new(value: i32) -> Rc<RefCell<Self>> {
        Rc::new(RefCell::new(Node {
            value,
            children: Vec::new(),
            parent: None,
        }))
    }
}

fn main() {
    let root = Node::new(1);
    let child = Node::new(2);

    // Parent → child: strong reference (parent owns child)
    root.borrow_mut().children.push(Rc::clone(&child));

    // Child → parent: weak reference (child doesn't own parent)
    child.borrow_mut().parent = Some(Rc::downgrade(&root));

    // Child can access parent through upgrade
    if let Some(parent) = child.borrow().parent.as_ref().and_then(|w| w.upgrade()) {
        println!("Child's parent value: {}", parent.borrow().value);
    }

    println!("Root strong count: {}", Rc::strong_count(&root));   // 1
    println!("Child strong count: {}", Rc::strong_count(&child));  // 2

    // No cycle! When root is dropped, its strong count hits 0.
    // The child in root.children is dropped too (cascading).
}
```

The rule of thumb: **parent-to-child is strong, child-to-parent is weak.** The parent owns the children. Children have a non-owning reference back to the parent.

## A Tree Implementation

Here's a complete tree with proper weak parent references:

```rust
use std::cell::RefCell;
use std::rc::{Rc, Weak};

type NodeRef = Rc<RefCell<TreeNode>>;
type WeakNodeRef = Weak<RefCell<TreeNode>>;

struct TreeNode {
    value: String,
    parent: Option<WeakNodeRef>,
    children: Vec<NodeRef>,
}

impl TreeNode {
    fn new(value: &str) -> NodeRef {
        Rc::new(RefCell::new(TreeNode {
            value: value.to_string(),
            parent: None,
            children: Vec::new(),
        }))
    }

    fn add_child(parent: &NodeRef, child: &NodeRef) {
        child.borrow_mut().parent = Some(Rc::downgrade(parent));
        parent.borrow_mut().children.push(Rc::clone(child));
    }

    fn path_to_root(node: &NodeRef) -> Vec<String> {
        let mut path = vec![node.borrow().value.clone()];
        let mut current = node.borrow().parent.as_ref().and_then(|w| w.upgrade());

        while let Some(parent) = current {
            path.push(parent.borrow().value.clone());
            current = parent.borrow().parent.as_ref().and_then(|w| w.upgrade());
        }

        path.reverse();
        path
    }
}

fn main() {
    let root = TreeNode::new("root");
    let documents = TreeNode::new("documents");
    let projects = TreeNode::new("projects");
    let rust_project = TreeNode::new("my-rust-app");

    TreeNode::add_child(&root, &documents);
    TreeNode::add_child(&root, &projects);
    TreeNode::add_child(&projects, &rust_project);

    let path = TreeNode::path_to_root(&rust_project);
    println!("Path: {}", path.join(" / "));
    // Path: root / projects / my-rust-app

    println!("Root refs: {}", Rc::strong_count(&root));  // 1
    // No cycles — everything cleans up properly
}
```

## Observer Pattern with Weak References

Another classic use case — observers that shouldn't prevent the subject from being freed:

```rust
use std::cell::RefCell;
use std::rc::{Rc, Weak};

trait Observer {
    fn on_event(&self, event: &str);
}

struct EventEmitter {
    observers: RefCell<Vec<Weak<dyn Observer>>>,
}

impl EventEmitter {
    fn new() -> Self {
        EventEmitter {
            observers: RefCell::new(Vec::new()),
        }
    }

    fn subscribe(&self, observer: &Rc<dyn Observer>) {
        self.observers.borrow_mut().push(Rc::downgrade(observer));
    }

    fn emit(&self, event: &str) {
        let mut observers = self.observers.borrow_mut();

        // Clean up dead observers while we're at it
        observers.retain(|weak| weak.upgrade().is_some());

        for weak in observers.iter() {
            if let Some(observer) = weak.upgrade() {
                observer.on_event(event);
            }
        }
    }
}

struct Logger {
    prefix: String,
}

impl Observer for Logger {
    fn on_event(&self, event: &str) {
        println!("[{}] {}", self.prefix, event);
    }
}

fn main() {
    let emitter = EventEmitter::new();

    let logger1: Rc<dyn Observer> = Rc::new(Logger {
        prefix: "LOG1".to_string(),
    });
    let logger2: Rc<dyn Observer> = Rc::new(Logger {
        prefix: "LOG2".to_string(),
    });

    emitter.subscribe(&logger1);
    emitter.subscribe(&logger2);

    emitter.emit("app started");
    // [LOG1] app started
    // [LOG2] app started

    drop(logger1);  // logger1 is freed

    emitter.emit("user logged in");
    // [LOG2] user logged in
    // logger1's weak reference is cleaned up automatically
}
```

Weak references let the observer pattern work without preventing cleanup. When a logger is dropped, its weak reference expires, and the emitter cleans it up on the next emit.

## Weak with Arc (Thread-Safe)

Same pattern, but for multithreaded code — use `Arc` and `sync::Weak`:

```rust
use std::sync::{Arc, Weak, Mutex};
use std::thread;

struct SharedCache {
    data: Mutex<Vec<String>>,
}

fn spawn_worker(cache: Weak<SharedCache>, id: usize) -> thread::JoinHandle<()> {
    thread::spawn(move || {
        // Try to upgrade — cache might have been dropped
        match cache.upgrade() {
            Some(cache) => {
                let mut data = cache.data.lock().unwrap();
                data.push(format!("worker-{}", id));
                println!("Worker {} added data", id);
            }
            None => {
                println!("Worker {} — cache already gone", id);
            }
        }
    })
}

fn main() {
    let cache = Arc::new(SharedCache {
        data: Mutex::new(Vec::new()),
    });

    let handles: Vec<_> = (0..5)
        .map(|i| spawn_worker(Arc::downgrade(&cache), i))
        .collect();

    for h in handles {
        h.join().unwrap();
    }

    println!("Cache contents: {:?}", cache.data.lock().unwrap());
}
```

Workers hold weak references to the cache. If the main owner drops the cache, workers gracefully discover it's gone instead of preventing cleanup.

## When to Use Weak

Use `Weak` when:
- **Breaking cycles**: parent-child, graph edges that would create cycles
- **Observer/callback patterns**: observers shouldn't keep the subject alive
- **Caches**: cached data shouldn't prevent the source from being freed
- **Optional back-references**: "I know about this thing, but I don't own it"

Don't use `Weak` as a general "I don't want ownership" tool. For that, plain references (`&T`) are simpler and have zero runtime overhead. `Weak` is specifically for when you need a reference that can outlive the data — where `upgrade()` returning `None` is a valid outcome.

## The Cost

`Weak` isn't free. The allocation for an `Rc`/`Arc` isn't freed until both the strong count AND the weak count reach zero. The data is dropped when the strong count hits zero, but the allocation metadata (the counts themselves) sticks around for the weak references.

This is usually negligible. But in data structures with millions of nodes, each holding a `Weak` back-reference, the overhead adds up. Consider whether indices or keys into a central store would work better.

The golden rule: `Weak` is a tool for ownership graphs that have cycles. If your data is a tree or DAG, you might not need it at all. Structure your ownership as a tree — strong references flowing down, weak references flowing up — and cycles disappear.
