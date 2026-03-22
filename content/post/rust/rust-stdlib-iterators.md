---
title: "Lesson 2: Iterator Trait and Adapters — The full picture"
author: Atharva Pandey
series: "Rust Standard Library Mastery"
lesson: 2
keywords: [Rust, rust, standard library, iterators, iterator trait, adapters, map, filter, collect]
tags: [Rust tutorial, rust, stdlib]
date: '2024-09-17T14:45:00.000Z'
---

The moment Rust iterators clicked for me was when I realized they're not loops with extra steps — they're a completely different way of expressing data transformations. I'd been writing imperative loops for fifteen years, and the first time I refactored a gnarly nested-loop function into an iterator chain, the result was half the lines and twice as readable.

## The Iterator Trait

Everything starts here. The `Iterator` trait is shockingly simple:

```rust
trait Iterator {
    type Item;
    fn next(&mut self) -> Option<Self::Item>;
}
```

That's it. Implement `next()`, return `Some(value)` when there's data, `None` when you're done. Every single adapter — `map`, `filter`, `take`, `zip`, all of them — is built on top of this one method.

```rust
struct Countdown {
    value: u32,
}

impl Countdown {
    fn new(start: u32) -> Self {
        Countdown { value: start }
    }
}

impl Iterator for Countdown {
    type Item = u32;

    fn next(&mut self) -> Option<u32> {
        if self.value == 0 {
            None
        } else {
            let current = self.value;
            self.value -= 1;
            Some(current)
        }
    }
}

fn main() {
    let countdown = Countdown::new(5);
    let values: Vec<u32> = countdown.collect();
    println!("{values:?}"); // [5, 4, 3, 2, 1]
}
```

## Laziness Is the Whole Point

Iterators in Rust are lazy. When you write `.map(|x| x * 2).filter(|x| x > 5)`, nothing happens until you consume the iterator. This is different from, say, JavaScript's array methods which eagerly produce intermediate arrays.

```rust
fn main() {
    let data = vec![1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

    // Nothing happens here — no computation, no allocation
    let pipeline = data.iter()
        .map(|&x| {
            println!("  mapping {x}");
            x * 2
        })
        .filter(|&x| {
            println!("  filtering {x}");
            x > 10
        });

    println!("Pipeline created, but nothing executed yet.");
    println!("Now consuming:");

    // NOW each element flows through the entire chain, one at a time
    let result: Vec<i32> = pipeline.collect();
    println!("Result: {result:?}");
}
```

Run this and watch the output. You'll see that each element goes through `map` then `filter` before the next element starts. There's no intermediate `Vec` holding the mapped values — each item flows through the whole pipeline individually. This means you can process a million-element iterator chain with constant memory overhead.

## Essential Adapters

These are the adapters I use daily. Not all of them — just the ones that show up in real code constantly.

### map and filter

The bread and butter. `map` transforms each element, `filter` keeps only the ones that match a predicate.

```rust
fn main() {
    let names = vec!["Alice", "Bob", "Charlie", "Diana", "Eve"];

    let long_names_upper: Vec<String> = names.iter()
        .filter(|name| name.len() > 3)
        .map(|name| name.to_uppercase())
        .collect();

    println!("{long_names_upper:?}");
    // ["ALICE", "CHARLIE", "DIANA"]
}
```

### filter_map — Two Steps in One

When your transformation might fail or might produce `None`, `filter_map` combines filtering and mapping:

```rust
fn main() {
    let strings = vec!["42", "not_a_number", "17", "also_bad", "99"];

    // Parse strings to numbers, discarding failures
    let numbers: Vec<i32> = strings.iter()
        .filter_map(|s| s.parse().ok())
        .collect();

    println!("{numbers:?}"); // [42, 17, 99]

    // Equivalent without filter_map — more verbose
    let numbers_verbose: Vec<i32> = strings.iter()
        .map(|s| s.parse::<i32>())
        .filter(|r| r.is_ok())
        .map(|r| r.unwrap())
        .collect();

    assert_eq!(numbers, numbers_verbose);
}
```

### flat_map — Flatten Nested Structures

`flat_map` applies a function that returns an iterator, then flattens all the results into a single sequence. This one's a game-changer for working with nested data.

```rust
fn main() {
    let sentences = vec![
        "hello world",
        "rust is great",
        "iterators are powerful",
    ];

    let words: Vec<&str> = sentences.iter()
        .flat_map(|s| s.split_whitespace())
        .collect();

    println!("{words:?}");
    // ["hello", "world", "rust", "is", "great", "iterators", "are", "powerful"]

    // Generating multiple outputs per input
    let ranges: Vec<i32> = vec![3, 1, 4]
        .into_iter()
        .flat_map(|n| 0..n)
        .collect();

    println!("{ranges:?}"); // [0, 1, 2, 0, 0, 1, 2, 3]
}
```

### enumerate, zip, and chain

Position tracking, pairing, and concatenation:

```rust
fn main() {
    let fruits = vec!["apple", "banana", "cherry"];

    // enumerate gives you (index, value) pairs
    for (i, fruit) in fruits.iter().enumerate() {
        println!("{i}: {fruit}");
    }

    // zip pairs two iterators element-by-element
    let keys = vec!["name", "age", "city"];
    let values = vec!["Alice", "30", "Portland"];

    let pairs: Vec<(&&str, &&str)> = keys.iter().zip(values.iter()).collect();
    println!("{pairs:?}");

    // chain concatenates iterators
    let first = vec![1, 2, 3];
    let second = vec![4, 5, 6];

    let combined: Vec<&i32> = first.iter().chain(second.iter()).collect();
    println!("{combined:?}"); // [1, 2, 3, 4, 5, 6]
}
```

### take, skip, take_while, skip_while

Slicing iterators without allocating:

```rust
fn main() {
    let data: Vec<i32> = (1..=20).collect();

    // First 5
    let first_five: Vec<&i32> = data.iter().take(5).collect();
    println!("First 5: {first_five:?}");

    // Skip 15, take the rest
    let last_five: Vec<&i32> = data.iter().skip(15).collect();
    println!("Last 5: {last_five:?}");

    // take_while stops at the first false
    let ascending = vec![1, 3, 5, 7, 2, 9, 11];
    let prefix: Vec<&i32> = ascending.iter()
        .take_while(|&&x| x < 6)
        .collect();
    println!("Ascending prefix: {prefix:?}"); // [1, 3, 5]

    // Pagination pattern
    let page = 3;
    let page_size = 5;
    let page_items: Vec<&i32> = data.iter()
        .skip((page - 1) * page_size)
        .take(page_size)
        .collect();
    println!("Page {page}: {page_items:?}"); // [11, 12, 13, 14, 15]
}
```

## Consuming Adapters

These are the methods that actually *run* the iterator and produce a final result.

### collect — The Swiss Army Knife

`collect()` is polymorphic — it can produce different collection types depending on what you ask for:

```rust
use std::collections::{HashMap, HashSet, BTreeMap};

fn main() {
    let data = vec![("alice", 95), ("bob", 87), ("charlie", 92)];

    // Collect into HashMap
    let scores: HashMap<&str, i32> = data.iter().copied().collect();
    println!("{scores:?}");

    // Collect into BTreeMap (sorted)
    let sorted_scores: BTreeMap<&str, i32> = data.iter().copied().collect();
    println!("{sorted_scores:?}");

    // Collect into HashSet (just keys)
    let names: HashSet<&str> = data.iter().map(|(name, _)| *name).collect();
    println!("{names:?}");

    // Collect Results — short-circuits on first error
    let strings = vec!["1", "2", "three", "4"];
    let parsed: Result<Vec<i32>, _> = strings.iter()
        .map(|s| s.parse::<i32>())
        .collect();
    println!("{parsed:?}"); // Err(...)

    let good_strings = vec!["1", "2", "3", "4"];
    let parsed: Result<Vec<i32>, _> = good_strings.iter()
        .map(|s| s.parse::<i32>())
        .collect();
    println!("{parsed:?}"); // Ok([1, 2, 3, 4])
}
```

That `Result<Vec<T>, E>` collection pattern is incredibly useful. It turns a sequence of `Result`s into either `Ok(vec_of_values)` or `Err(first_error)`. I use this every time I'm parsing a batch of inputs.

### fold and reduce

`fold` carries an accumulator through the iteration. `reduce` is similar but uses the first element as the initial accumulator.

```rust
fn main() {
    let numbers = vec![1, 2, 3, 4, 5];

    // fold: explicit initial value
    let sum = numbers.iter().fold(0, |acc, &x| acc + x);
    println!("Sum: {sum}");

    // Building a string with fold
    let csv = numbers.iter()
        .fold(String::new(), |mut acc, &x| {
            if !acc.is_empty() {
                acc.push(',');
            }
            acc.push_str(&x.to_string());
            acc
        });
    println!("CSV: {csv}");

    // reduce: first element becomes the accumulator
    let max = numbers.iter().copied().reduce(|a, b| if a > b { a } else { b });
    println!("Max: {max:?}"); // Some(5)

    // reduce on empty iterator returns None
    let empty: Vec<i32> = vec![];
    let result = empty.iter().copied().reduce(|a, b| a + b);
    println!("Empty reduce: {result:?}"); // None
}
```

### Other Important Consumers

```rust
fn main() {
    let data = vec![3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5];

    // any and all — short-circuit on first decisive element
    let has_big = data.iter().any(|&x| x > 8);
    let all_positive = data.iter().all(|&x| x > 0);
    println!("Has element > 8: {has_big}");
    println!("All positive: {all_positive}");

    // find — returns first match
    let first_even = data.iter().find(|&&x| x % 2 == 0);
    println!("First even: {first_even:?}");

    // position — returns index of first match
    let pos = data.iter().position(|&x| x == 9);
    println!("Position of 9: {pos:?}");

    // sum and product
    let total: i32 = data.iter().sum();
    let product: i64 = vec![1i64, 2, 3, 4, 5].iter().product();
    println!("Sum: {total}, Product: {product}");

    // min, max, min_by_key, max_by_key
    println!("Min: {:?}", data.iter().min());
    println!("Max: {:?}", data.iter().max());

    // count
    let even_count = data.iter().filter(|&&x| x % 2 == 0).count();
    println!("Even numbers: {even_count}");
}
```

## The Three Iterator Types

This trips people up. There are three ways to iterate over a collection, and they have different ownership semantics:

```rust
fn main() {
    let names = vec![
        String::from("Alice"),
        String::from("Bob"),
        String::from("Charlie"),
    ];

    // .iter() borrows — yields &T
    for name in names.iter() {
        println!("{name}"); // name is &String
    }
    // names is still usable here

    // .iter_mut() borrows mutably — yields &mut T
    let mut scores = vec![85, 92, 78];
    for score in scores.iter_mut() {
        *score += 5; // Curve the grades
    }
    println!("{scores:?}");

    // .into_iter() takes ownership — yields T
    let owned_names = vec![
        String::from("Alice"),
        String::from("Bob"),
    ];
    for name in owned_names.into_iter() {
        println!("{name}"); // name is String, we own it
    }
    // owned_names is GONE — moved

    // The for loop sugar:
    // for x in &collection     → calls .iter()
    // for x in &mut collection → calls .iter_mut()
    // for x in collection      → calls .into_iter()
}
```

Understanding this distinction is critical. If you're building an iterator chain and you need ownership of the elements (because you're transforming them into a different type), use `into_iter()`. If you just need to read them, use `iter()`. Getting this wrong leads to confusing borrow checker errors.

## Building Your Own Iterators

Custom iterators are one of Rust's superpowers. Once you implement `Iterator`, you get all the adapters for free.

```rust
/// Generates Fibonacci numbers
struct Fibonacci {
    a: u64,
    b: u64,
}

impl Fibonacci {
    fn new() -> Self {
        Fibonacci { a: 0, b: 1 }
    }
}

impl Iterator for Fibonacci {
    type Item = u64;

    fn next(&mut self) -> Option<u64> {
        let result = self.a;
        let new_b = self.a + self.b;
        self.a = self.b;
        self.b = new_b;
        Some(result) // Infinite iterator — never returns None
    }
}

fn main() {
    // First 10 Fibonacci numbers
    let fibs: Vec<u64> = Fibonacci::new().take(10).collect();
    println!("{fibs:?}");

    // Sum of Fibonacci numbers below 1000
    let sum: u64 = Fibonacci::new()
        .take_while(|&n| n < 1000)
        .sum();
    println!("Sum of fibs below 1000: {sum}");

    // Even Fibonacci numbers, first 5
    let even_fibs: Vec<u64> = Fibonacci::new()
        .filter(|n| n % 2 == 0)
        .take(5)
        .collect();
    println!("First 5 even fibs: {even_fibs:?}");
}
```

Notice how we get `take`, `take_while`, `filter`, `sum`, and `collect` for free just by implementing `next()`. That's the power of the trait system combined with iterators.

## Performance: Zero-Cost Abstraction in Practice

People worry that iterator chains are slower than hand-written loops. They're not. The compiler inlines and optimizes iterator adapters aggressively — the generated machine code is typically identical to a hand-written loop.

```rust
fn sum_of_squares_loop(data: &[i32]) -> i64 {
    let mut sum: i64 = 0;
    for &x in data {
        if x > 0 {
            sum += (x as i64) * (x as i64);
        }
    }
    sum
}

fn sum_of_squares_iter(data: &[i32]) -> i64 {
    data.iter()
        .filter(|&&x| x > 0)
        .map(|&x| (x as i64) * (x as i64))
        .sum()
}

fn main() {
    let data: Vec<i32> = (-1000..1000).collect();

    let a = sum_of_squares_loop(&data);
    let b = sum_of_squares_iter(&data);
    assert_eq!(a, b);

    println!("Both produce: {a}");
}
```

These two functions compile to essentially the same assembly. The iterator version is easier to read, easier to modify, and just as fast. That's the deal Rust offers: you don't pay for abstraction.

## Patterns I Use Constantly

```rust
use std::collections::HashMap;

fn main() {
    // Group by
    let words = vec!["apple", "avocado", "banana", "blueberry", "cherry", "cranberry"];
    let grouped: HashMap<char, Vec<&&str>> = words.iter()
        .fold(HashMap::new(), |mut acc, word| {
            let first_char = word.chars().next().unwrap();
            acc.entry(first_char).or_insert_with(Vec::new).push(word);
            acc
        });

    for (letter, group) in &grouped {
        println!("{letter}: {group:?}");
    }

    // Unzip
    let pairs = vec![(1, "one"), (2, "two"), (3, "three")];
    let (numbers, words): (Vec<i32>, Vec<&str>) = pairs.into_iter().unzip();
    println!("Numbers: {numbers:?}");
    println!("Words: {words:?}");

    // Windows for pairwise comparison
    let temps = vec![72.0, 74.5, 71.2, 68.9, 73.1];
    let changes: Vec<f64> = temps.windows(2)
        .map(|w| w[1] - w[0])
        .collect();
    println!("Temperature changes: {changes:?}");

    // Scan — like fold but yields intermediate results
    let running_total: Vec<i32> = vec![1, 2, 3, 4, 5]
        .iter()
        .scan(0, |state, &x| {
            *state += x;
            Some(*state)
        })
        .collect();
    println!("Running total: {running_total:?}"); // [1, 3, 6, 10, 15]
}
```

The iterator system is probably Rust's most elegant feature. It gives you the expressiveness of a functional language with the performance of hand-optimized C. Once you internalize these patterns, you'll find yourself reaching for iterator chains instinctively — and your code will be better for it.
