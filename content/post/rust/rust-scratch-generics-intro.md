---
title: "Lesson 20: Generics — Writing code that works for any type"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 20
keywords: [Rust, rust, generics, type parameters, generic functions, generic structs, monomorphization]
tags: [Rust tutorial, rust, beginner]
date: '2024-04-08T20:00:00.000Z'
---

I used to think generics were fancy academic stuff that you'd rarely need in practice. Then I wrote my third function that was identical to a previous one except it operated on `f64` instead of `i32`. Generics aren't luxury features — they're how you stop writing the same function five times for five different types.

## The Problem Generics Solve

Without generics, you write duplicate code:

```rust
fn largest_i32(list: &[i32]) -> &i32 {
    let mut largest = &list[0];
    for item in list {
        if item > largest {
            largest = item;
        }
    }
    largest
}

fn largest_f64(list: &[f64]) -> &f64 {
    let mut largest = &list[0];
    for item in list {
        if item > largest {
            largest = item;
        }
    }
    largest
}

fn main() {
    let ints = vec![34, 50, 25, 100, 65];
    let floats = vec![3.4, 5.0, 2.5, 10.0, 6.5];

    println!("Largest int: {}", largest_i32(&ints));
    println!("Largest float: {}", largest_f64(&floats));
}
```

These functions are identical except for the type. Generics let you write it once:

```rust
fn largest<T: PartialOrd>(list: &[T]) -> &T {
    let mut largest = &list[0];
    for item in list {
        if item > largest {
            largest = item;
        }
    }
    largest
}

fn main() {
    let ints = vec![34, 50, 25, 100, 65];
    let floats = vec![3.4, 5.0, 2.5, 10.0, 6.5];
    let chars = vec!['z', 'a', 'm', 'b'];

    println!("Largest int: {}", largest(&ints));
    println!("Largest float: {}", largest(&floats));
    println!("Largest char: {}", largest(&chars));
}
```

`T: PartialOrd` is a *trait bound* — it says "T can be any type, as long as it supports comparison." Without this bound, the `>` operator wouldn't work on generic `T`.

## Generic Functions

The basic syntax:

```rust
fn first<T>(list: &[T]) -> Option<&T> {
    list.first()
}

fn repeat<T: Clone>(item: &T, times: usize) -> Vec<T> {
    vec![item.clone(); times]
}

fn main() {
    let nums = vec![1, 2, 3];
    let strs = vec!["hello", "world"];

    println!("First num: {:?}", first(&nums));
    println!("First str: {:?}", first(&strs));

    let repeated = repeat(&42, 5);
    println!("Repeated: {:?}", repeated);

    let repeated = repeat(&String::from("ha"), 3);
    println!("Repeated: {:?}", repeated);
}
```

The type parameter `<T>` goes after the function name. You can have multiple type parameters: `<T, U, V>`. Convention is to use single uppercase letters, starting with `T`.

## Generic Structs

```rust
#[derive(Debug)]
struct Point<T> {
    x: T,
    y: T,
}

impl<T> Point<T> {
    fn new(x: T, y: T) -> Self {
        Point { x, y }
    }
}

// Methods only available when T supports addition and copying
impl<T: std::ops::Add<Output = T> + Copy> Point<T> {
    fn translate(&self, dx: T, dy: T) -> Point<T> {
        Point {
            x: self.x + dx,
            y: self.y + dy,
        }
    }
}

fn main() {
    let int_point = Point::new(5, 10);
    let float_point = Point::new(1.5, 2.7);

    println!("{:?}", int_point);
    println!("{:?}", float_point);

    let moved = int_point.translate(3, 4);
    println!("Translated: {:?}", moved);
}
```

Notice that `x` and `y` must be the same type `T`. If you want different types:

```rust
#[derive(Debug)]
struct Pair<A, B> {
    first: A,
    second: B,
}

impl<A, B> Pair<A, B> {
    fn new(first: A, second: B) -> Self {
        Pair { first, second }
    }

    fn swap(self) -> Pair<B, A> {
        Pair {
            first: self.second,
            second: self.first,
        }
    }
}

fn main() {
    let pair = Pair::new("hello", 42);
    println!("{:?}", pair);

    let swapped = pair.swap();
    println!("{:?}", swapped);
}
```

## Generic Enums

You've been using generic enums all along:

```rust
// Option<T> is a generic enum
// enum Option<T> {
//     Some(T),
//     None,
// }

// Result<T, E> is a generic enum with two type parameters
// enum Result<T, E> {
//     Ok(T),
//     Err(E),
// }
```

You can define your own:

```rust
#[derive(Debug)]
enum Tree<T> {
    Leaf(T),
    Branch {
        left: Box<Tree<T>>,
        right: Box<Tree<T>>,
    },
}

impl<T: std::fmt::Display> Tree<T> {
    fn depth(&self) -> usize {
        match self {
            Tree::Leaf(_) => 1,
            Tree::Branch { left, right } => {
                1 + left.depth().max(right.depth())
            }
        }
    }

    fn print_leaves(&self) {
        match self {
            Tree::Leaf(value) => print!("{value} "),
            Tree::Branch { left, right } => {
                left.print_leaves();
                right.print_leaves();
            }
        }
    }
}

fn main() {
    let tree = Tree::Branch {
        left: Box::new(Tree::Branch {
            left: Box::new(Tree::Leaf(1)),
            right: Box::new(Tree::Leaf(2)),
        }),
        right: Box::new(Tree::Leaf(3)),
    };

    println!("Depth: {}", tree.depth());
    print!("Leaves: ");
    tree.print_leaves();
    println!();
}
```

## Trait Bounds in Depth

You've seen `T: PartialOrd` and `T: Clone`. Here are the common patterns:

```rust
use std::fmt;

// Single bound
fn print_it<T: fmt::Display>(item: &T) {
    println!("{item}");
}

// Multiple bounds with +
fn print_and_debug<T: fmt::Display + fmt::Debug>(item: &T) {
    println!("Display: {item}");
    println!("Debug: {item:?}");
}

// where clause (better for complex bounds)
fn complex_function<T, U>(t: &T, u: &U) -> String
where
    T: fmt::Display + Clone,
    U: fmt::Debug + PartialEq,
{
    format!("{} / {:?}", t, u)
}

fn main() {
    print_it(&42);
    print_it(&"hello");

    print_and_debug(&42);

    println!("{}", complex_function(&"hello", &42));
}
```

Use `where` clauses when:
- You have more than one or two bounds
- The function signature gets too long
- You want the function name and parameters to be readable without scrolling

## Monomorphization — Zero-Cost Abstraction

When you write a generic function, the compiler generates a specialized version for each concrete type you use:

```rust
fn add<T: std::ops::Add<Output = T>>(a: T, b: T) -> T {
    a + b
}

fn main() {
    let x = add(1i32, 2i32);      // compiler generates add_i32
    let y = add(1.0f64, 2.0f64);  // compiler generates add_f64
    println!("{x} {y}");
}
```

This is *monomorphization*. The generic code is just as fast as hand-written specialized code. No virtual function calls, no boxing, no runtime type checks. The abstraction is genuinely free.

The trade-off: compile time and binary size increase because the compiler generates multiple versions. For most programs, this is fine. For extremely generic libraries, it can matter.

## A Practical Example: Generic Cache

```rust
use std::collections::HashMap;

struct Cache<K, V> {
    store: HashMap<K, V>,
    max_size: usize,
    access_order: Vec<K>,
}

impl<K, V> Cache<K, V>
where
    K: std::hash::Hash + Eq + Clone,
{
    fn new(max_size: usize) -> Self {
        Cache {
            store: HashMap::new(),
            max_size,
            access_order: Vec::new(),
        }
    }

    fn insert(&mut self, key: K, value: V) {
        if self.store.len() >= self.max_size && !self.store.contains_key(&key) {
            // Evict oldest
            if let Some(oldest) = self.access_order.first().cloned() {
                self.store.remove(&oldest);
                self.access_order.remove(0);
            }
        }
        self.access_order.retain(|k| k != &key);
        self.access_order.push(key.clone());
        self.store.insert(key, value);
    }

    fn get(&self, key: &K) -> Option<&V> {
        self.store.get(key)
    }

    fn len(&self) -> usize {
        self.store.len()
    }
}

fn main() {
    // String keys, i32 values
    let mut num_cache = Cache::new(3);
    num_cache.insert("one".to_string(), 1);
    num_cache.insert("two".to_string(), 2);
    num_cache.insert("three".to_string(), 3);
    num_cache.insert("four".to_string(), 4);  // evicts "one"

    println!("one: {:?}", num_cache.get(&"one".to_string()));    // None
    println!("four: {:?}", num_cache.get(&"four".to_string()));  // Some(4)
    println!("size: {}", num_cache.len());

    // i32 keys, String values — same Cache works
    let mut str_cache: Cache<i32, String> = Cache::new(2);
    str_cache.insert(1, "hello".to_string());
    str_cache.insert(2, "world".to_string());
    str_cache.insert(3, "!".to_string());  // evicts 1

    println!("1: {:?}", str_cache.get(&1));  // None
    println!("3: {:?}", str_cache.get(&3));  // Some("!")
}
```

One `Cache` implementation that works with any key and value types. The trait bounds (`Hash + Eq + Clone` on keys) tell the compiler exactly what capabilities the types need.

## When Not to Use Generics

Generics are powerful but not always the right choice:

- **Don't make something generic if it only works with one type.** Premature abstraction is a real problem.
- **Don't add trait bounds you don't need.** Each bound constrains what types can be used. Start with minimal bounds and add more only when the compiler tells you to.
- **Consider `dyn Trait` for heterogeneous collections.** Generics produce different types — `Cache<String, i32>` is a different type than `Cache<i32, String>`. If you need to store different instances in one collection, you need trait objects.

My rule: write the concrete version first. When you need the same code for a second type, extract a generic. Don't start generic.

## Generics vs. Trait Objects

Quick comparison:

| Feature | Generics (`impl Trait` / `<T>`) | Trait Objects (`dyn Trait`) |
|---------|--------------------------------|---------------------------|
| Dispatch | Static (compile time) | Dynamic (runtime) |
| Performance | Zero-cost | Small overhead (vtable) |
| Heterogeneous collections | No | Yes |
| Binary size | Larger (monomorphized) | Smaller |
| Compile time | Longer | Shorter |

Use generics by default. Switch to trait objects when you need runtime polymorphism.

Next: closures — functions that capture their environment.
