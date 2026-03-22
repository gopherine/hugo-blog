---
title: "Lesson 5: Iterator Chains Over Manual Loops — Functional Rust"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 5
keywords: [Rust, rust, idiomatic, iterators, functional programming, map, filter, collect]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-04-18T16:03:00.000Z'
---

I used to write Rust like I was still writing C — `for` loops everywhere, mutable accumulators, index variables. The code worked, but it was *loud*. Five lines to express what should be one. Then I started using iterator chains, and honestly? I'm never going back.

Iterator chains aren't just syntactic sugar. They're often *faster* than hand-written loops because the compiler can optimize them better. Zero-cost abstractions in action.

---

## The Imperative Way vs The Idiomatic Way

Here's a task: given a list of strings, find all that start with "error:", strip the prefix, trim whitespace, and collect the results.

The imperative version:

```rust
fn extract_errors_imperative(lines: &[String]) -> Vec<String> {
    let mut result = Vec::new();
    for line in lines {
        if line.starts_with("error:") {
            let msg = line.strip_prefix("error:").unwrap();
            let trimmed = msg.trim().to_string();
            result.push(trimmed);
        }
    }
    result
}
```

The iterator chain version:

```rust
fn extract_errors(lines: &[String]) -> Vec<String> {
    lines.iter()
        .filter_map(|line| line.strip_prefix("error:"))
        .map(|msg| msg.trim().to_string())
        .collect()
}
```

Same result. But the iterator version declares *what* you want, not *how* to do it step by step. No mutable accumulator. No temporary variables. Just a pipeline of transformations.

---

## The Core Iterator Methods

You need about 10 methods to handle 95% of cases. Here they are.

### `map` — Transform each element

```rust
fn main() {
    let numbers = vec![1, 2, 3, 4, 5];
    let doubled: Vec<i32> = numbers.iter().map(|n| n * 2).collect();
    println!("{:?}", doubled); // [2, 4, 6, 8, 10]
}
```

### `filter` — Keep elements matching a predicate

```rust
fn main() {
    let numbers = vec![1, 2, 3, 4, 5, 6, 7, 8];
    let evens: Vec<&i32> = numbers.iter().filter(|n| *n % 2 == 0).collect();
    println!("{:?}", evens); // [2, 4, 6, 8]
}
```

### `filter_map` — Filter and transform in one step

This is one of my favorites. It takes a closure that returns `Option<T>` — `Some` keeps the element, `None` drops it.

```rust
fn main() {
    let inputs = vec!["42", "hello", "17", "world", "99"];
    let numbers: Vec<i32> = inputs.iter()
        .filter_map(|s| s.parse::<i32>().ok())
        .collect();
    println!("{:?}", numbers); // [42, 17, 99]
}
```

Without `filter_map`, you'd need a `filter` then a `map` — two passes where one suffices.

### `flat_map` — Map and flatten

```rust
fn main() {
    let sentences = vec!["hello world", "foo bar baz"];
    let words: Vec<&str> = sentences.iter()
        .flat_map(|s| s.split_whitespace())
        .collect();
    println!("{:?}", words); // ["hello", "world", "foo", "bar", "baz"]
}
```

### `fold` — Reduce to a single value

```rust
fn main() {
    let numbers = vec![1, 2, 3, 4, 5];

    let sum = numbers.iter().fold(0, |acc, n| acc + n);
    println!("Sum: {}", sum); // 15

    let product = numbers.iter().fold(1, |acc, n| acc * n);
    println!("Product: {}", product); // 120

    // Building a string
    let csv = numbers.iter()
        .map(|n| n.to_string())
        .collect::<Vec<_>>()
        .join(", ");
    println!("CSV: {}", csv); // "1, 2, 3, 4, 5"
}
```

### `enumerate` — Get index alongside element

```rust
fn main() {
    let fruits = vec!["apple", "banana", "cherry"];
    for (i, fruit) in fruits.iter().enumerate() {
        println!("{}. {}", i + 1, fruit);
    }
}
```

### `zip` — Pair up two iterators

```rust
fn main() {
    let names = vec!["Alice", "Bob", "Charlie"];
    let scores = vec![92, 87, 95];

    let results: Vec<_> = names.iter().zip(scores.iter()).collect();
    println!("{:?}", results);
    // [("Alice", 92), ("Bob", 87), ("Charlie", 95)]
}
```

### `take` and `skip` — Slice the iterator

```rust
fn main() {
    let numbers: Vec<i32> = (1..=100).take(5).collect();
    println!("{:?}", numbers); // [1, 2, 3, 4, 5]

    let numbers: Vec<i32> = (1..=100).skip(95).collect();
    println!("{:?}", numbers); // [96, 97, 98, 99, 100]
}
```

### `any` and `all` — Boolean checks

```rust
fn main() {
    let numbers = vec![2, 4, 6, 8];

    let all_even = numbers.iter().all(|n| n % 2 == 0);
    let has_negative = numbers.iter().any(|n| *n < 0);

    println!("All even: {}, Has negative: {}", all_even, has_negative);
    // All even: true, Has negative: false
}
```

### `find` — First matching element

```rust
fn main() {
    let numbers = vec![1, 3, 5, 8, 9, 10];
    let first_even = numbers.iter().find(|n| *n % 2 == 0);
    println!("{:?}", first_even); // Some(8)
}
```

---

## Chaining Is Where It Gets Powerful

Individual methods are useful. Chains are where iterators truly shine.

```rust
#[derive(Debug)]
struct Transaction {
    from: String,
    to: String,
    amount: f64,
}

fn top_recipients(transactions: &[Transaction], n: usize) -> Vec<(String, f64)> {
    use std::collections::HashMap;

    let mut totals: HashMap<String, f64> = HashMap::new();
    for t in transactions {
        *totals.entry(t.to.clone()).or_insert(0.0) += t.amount;
    }

    let mut sorted: Vec<(String, f64)> = totals.into_iter().collect();
    sorted.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
    sorted.into_iter().take(n).collect()
}

fn main() {
    let txns = vec![
        Transaction { from: "Alice".into(), to: "Bob".into(), amount: 100.0 },
        Transaction { from: "Charlie".into(), to: "Bob".into(), amount: 200.0 },
        Transaction { from: "Alice".into(), to: "Dave".into(), amount: 150.0 },
        Transaction { from: "Bob".into(), to: "Dave".into(), amount: 50.0 },
    ];

    for (name, total) in top_recipients(&txns, 2) {
        println!("{}: ${:.2}", name, total);
    }
}
```

---

## Laziness — Iterators Don't Execute Until Consumed

This is crucial. Iterator adapters like `map`, `filter`, and `take` are *lazy*. They don't do anything until you call a consuming method like `collect`, `for_each`, `sum`, `count`, or `find`.

```rust
fn main() {
    let numbers = vec![1, 2, 3, 4, 5];

    // This does NOTHING — it's lazy
    let _lazy = numbers.iter().map(|n| {
        println!("Processing {}", n); // Never printed!
        n * 2
    });

    // This actually executes
    let doubled: Vec<i32> = numbers.iter().map(|n| {
        println!("Processing {}", n); // Printed!
        n * 2
    }).collect();

    println!("{:?}", doubled);
}
```

This laziness means you can chain a dozen operations and the iterator only makes one pass through the data. No intermediate collections. No wasted allocations.

---

## `.iter()` vs `.into_iter()` vs `.iter_mut()`

This trips everyone up at first.

- `.iter()` — borrows each element: yields `&T`
- `.iter_mut()` — mutably borrows each element: yields `&mut T`
- `.into_iter()` — takes ownership of each element: yields `T`

```rust
fn main() {
    let names = vec![String::from("Alice"), String::from("Bob")];

    // .iter() — borrows. `names` is still valid after.
    for name in names.iter() {
        println!("{}", name); // name is &String
    }
    println!("Still have names: {:?}", names);

    // .into_iter() — consumes. `names` is gone.
    let names = vec![String::from("Alice"), String::from("Bob")];
    let upper: Vec<String> = names.into_iter()
        .map(|mut n| { n.make_ascii_uppercase(); n })
        .collect();
    // println!("{:?}", names); // ERROR: names was consumed
    println!("{:?}", upper);
}
```

My rule: use `.iter()` by default. Use `.into_iter()` when you want to transform a collection into a new one and don't need the original. Use `.iter_mut()` when you need to modify elements in place.

---

## The Turbofish on `collect`

`collect()` is generic — it can produce different collection types. You need to tell the compiler which one:

```rust
fn main() {
    let numbers = vec![1, 2, 3, 4, 5];

    // Type annotation on the variable
    let set: std::collections::HashSet<&i32> = numbers.iter().collect();

    // Or turbofish on collect
    let set2 = numbers.iter().collect::<std::collections::HashSet<_>>();

    // _ lets the compiler infer the element type
    let vec = numbers.iter().map(|n| n * 2).collect::<Vec<_>>();

    println!("{:?}", set);
    println!("{:?}", vec);
}
```

I personally prefer the turbofish style — it keeps the type close to where it matters.

---

## When Manual Loops Are Still Better

I'm not a zealot. Some cases genuinely read better as `for` loops:

**Complex control flow with multiple breaks/continues:**

```rust
fn find_pair(numbers: &[i32], target: i32) -> Option<(i32, i32)> {
    for (i, &a) in numbers.iter().enumerate() {
        for &b in &numbers[i + 1..] {
            if a + b == target {
                return Some((a, b));
            }
        }
    }
    None
}
```

**When you need mutable state across iterations:**

```rust
fn running_average(values: &[f64]) -> Vec<f64> {
    let mut sum = 0.0;
    let mut result = Vec::with_capacity(values.len());
    for (i, &v) in values.iter().enumerate() {
        sum += v;
        result.push(sum / (i + 1) as f64);
    }
    result
}
```

Could you write these with `scan` or `fold`? Sure. But would they be *clearer*? Probably not. Use the right tool for the job.

---

## Key Takeaways

- Prefer iterator chains over manual `for` loops for data transformations.
- Iterators are lazy — nothing happens until a consuming method is called.
- `filter_map` combines `filter` and `map`. `flat_map` combines `map` and `flatten`. Use them.
- Know the difference between `.iter()`, `.into_iter()`, and `.iter_mut()`.
- Use turbofish (`::<>`) or type annotations to tell `collect()` what type you want.
- Manual loops are fine when they're clearer — don't force every loop into an iterator chain.
