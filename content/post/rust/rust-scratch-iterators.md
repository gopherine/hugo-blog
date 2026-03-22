---
title: "Lesson 22: Iterators — Lazy, composable data processing"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 22
keywords: [Rust, rust, iterators, Iterator trait, map, filter, collect, lazy evaluation, iterator adapters]
tags: [Rust tutorial, rust, beginner]
date: '2024-04-12T09:30:00.000Z'
---

I once profiled a Go service and found it was spending 40% of CPU time allocating intermediate slices in a data pipeline. Each transformation created a new slice, copied data, processed it, then created another. In Rust, iterator chains do the same transformation with zero intermediate allocations. The data flows through the pipeline element by element, transformed in place. That's not just elegant — it's measurably faster.

## The Iterator Trait

At its core, an iterator is any type that implements the `Iterator` trait:

```rust
// Simplified — the real trait has more methods
// trait Iterator {
//     type Item;
//     fn next(&mut self) -> Option<Self::Item>;
// }
```

That's it. One required method: `next()`. It returns `Some(item)` for each element and `None` when the iterator is exhausted. Everything else — `map`, `filter`, `fold`, `collect` — is built on top of `next()`.

```rust
fn main() {
    let v = vec![1, 2, 3];
    let mut iter = v.iter();

    println!("{:?}", iter.next());  // Some(1)
    println!("{:?}", iter.next());  // Some(2)
    println!("{:?}", iter.next());  // Some(3)
    println!("{:?}", iter.next());  // None
}
```

## Creating Iterators

Multiple ways to get an iterator:

```rust
fn main() {
    let v = vec![10, 20, 30];

    // .iter() — yields &T (borrows)
    for item in v.iter() {
        println!("borrowed: {item}");
    }
    println!("v still exists: {:?}", v);

    // .iter_mut() — yields &mut T (mutable borrows)
    let mut v2 = vec![1, 2, 3];
    for item in v2.iter_mut() {
        *item *= 10;
    }
    println!("mutated: {:?}", v2);

    // .into_iter() — yields T (takes ownership)
    let v3 = vec![String::from("a"), String::from("b")];
    for item in v3.into_iter() {
        println!("owned: {item}");
    }
    // v3 is consumed — can't use it anymore

    // for loop uses .into_iter() by default
    let v4 = vec![1, 2, 3];
    for item in &v4 { /* same as v4.iter() */ }
    for item in &mut v4.clone() { /* same as v4.iter_mut() */ }
}
```

### Iterators from Other Sources

```rust
fn main() {
    // Ranges
    for i in 0..5 {
        print!("{i} ");
    }
    println!();

    // Characters in a string
    for c in "hello".chars() {
        print!("{c} ");
    }
    println!();

    // Lines in a string
    let text = "line 1\nline 2\nline 3";
    for line in text.lines() {
        println!("  {line}");
    }

    // Repeat
    let fives: Vec<i32> = std::iter::repeat(5).take(3).collect();
    println!("Fives: {:?}", fives);

    // Once
    let one: Vec<i32> = std::iter::once(42).collect();
    println!("One: {:?}", one);

    // Empty
    let nothing: Vec<i32> = std::iter::empty().collect();
    println!("Nothing: {:?}", nothing);
}
```

## Iterator Adapters — The Power Tools

Adapters transform iterators into new iterators. They're *lazy* — they don't do anything until consumed.

### map — Transform Each Element

```rust
fn main() {
    let numbers = vec![1, 2, 3, 4, 5];

    let doubled: Vec<i32> = numbers.iter().map(|&x| x * 2).collect();
    println!("Doubled: {:?}", doubled);

    let names = vec!["alice", "bob", "charlie"];
    let upper: Vec<String> = names.iter().map(|s| s.to_uppercase()).collect();
    println!("Upper: {:?}", upper);
}
```

### filter — Keep Only Matching Elements

```rust
fn main() {
    let numbers = vec![1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

    let evens: Vec<&i32> = numbers.iter().filter(|&&x| x % 2 == 0).collect();
    println!("Evens: {:?}", evens);

    let big: Vec<&i32> = numbers.iter().filter(|&&x| x > 5).collect();
    println!("Big: {:?}", big);
}
```

### filter_map — Filter and Transform in One Step

```rust
fn main() {
    let strings = vec!["1", "two", "3", "four", "5"];

    let numbers: Vec<i32> = strings.iter()
        .filter_map(|s| s.parse().ok())
        .collect();

    println!("Parsed: {:?}", numbers);  // [1, 3, 5]
}
```

`filter_map` is `filter` + `map` combined. The closure returns `Option<T>` — `Some` to keep and transform, `None` to skip.

### flat_map — Map Then Flatten

```rust
fn main() {
    let sentences = vec!["hello world", "foo bar baz"];

    let words: Vec<&str> = sentences.iter()
        .flat_map(|s| s.split_whitespace())
        .collect();

    println!("Words: {:?}", words);  // ["hello", "world", "foo", "bar", "baz"]
}
```

### enumerate — Add Index

```rust
fn main() {
    let colors = vec!["red", "green", "blue"];

    for (i, color) in colors.iter().enumerate() {
        println!("{i}: {color}");
    }
}
```

### zip — Pair Elements from Two Iterators

```rust
fn main() {
    let names = vec!["Alice", "Bob", "Charlie"];
    let scores = vec![95, 87, 92];

    let results: Vec<(&str, &i32)> = names.iter()
        .copied()
        .zip(scores.iter())
        .collect();

    println!("{:?}", results);

    // Zip stops at the shorter iterator
    let a = vec![1, 2, 3];
    let b = vec!["a", "b"];
    let zipped: Vec<_> = a.iter().zip(b.iter()).collect();
    println!("{:?}", zipped);  // [(1, "a"), (2, "b")]
}
```

### take and skip

```rust
fn main() {
    let numbers: Vec<i32> = (1..=100).collect();

    let first_five: Vec<&i32> = numbers.iter().take(5).collect();
    println!("First 5: {:?}", first_five);

    let after_five: Vec<&i32> = numbers.iter().skip(95).collect();
    println!("Last 5: {:?}", after_five);

    let middle: Vec<&i32> = numbers.iter().skip(10).take(5).collect();
    println!("11-15: {:?}", middle);
}
```

### take_while and skip_while

```rust
fn main() {
    let data = vec![2, 4, 6, 7, 8, 10];

    let evens: Vec<&i32> = data.iter().take_while(|&&x| x % 2 == 0).collect();
    println!("Leading evens: {:?}", evens);  // [2, 4, 6]

    let after_odd: Vec<&i32> = data.iter().skip_while(|&&x| x % 2 == 0).collect();
    println!("From first odd: {:?}", after_odd);  // [7, 8, 10]
}
```

### chain — Concatenate Iterators

```rust
fn main() {
    let a = vec![1, 2, 3];
    let b = vec![4, 5, 6];

    let combined: Vec<&i32> = a.iter().chain(b.iter()).collect();
    println!("{:?}", combined);  // [1, 2, 3, 4, 5, 6]
}
```

### peekable — Look Ahead

```rust
fn main() {
    let data = vec![1, 2, 3];
    let mut iter = data.iter().peekable();

    // Peek without consuming
    println!("Next will be: {:?}", iter.peek());  // Some(1)
    println!("Still: {:?}", iter.next());          // Some(1)
    println!("Now next: {:?}", iter.peek());       // Some(2)
}
```

## Consuming Adapters

These adapters *consume* the iterator and produce a final result:

```rust
fn main() {
    let numbers = vec![1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

    // collect — into a collection
    let doubled: Vec<i32> = numbers.iter().map(|&x| x * 2).collect();

    // sum / product
    let sum: i32 = numbers.iter().sum();
    let product: i32 = numbers.iter().product();
    println!("Sum: {sum}, Product: {product}");

    // count
    let even_count = numbers.iter().filter(|&&x| x % 2 == 0).count();
    println!("Even count: {even_count}");

    // min / max
    println!("Min: {:?}", numbers.iter().min());
    println!("Max: {:?}", numbers.iter().max());

    // any / all
    println!("Any > 5: {}", numbers.iter().any(|&x| x > 5));
    println!("All > 0: {}", numbers.iter().all(|&x| x > 0));

    // find
    let first_even = numbers.iter().find(|&&x| x % 2 == 0);
    println!("First even: {:?}", first_even);

    // position
    let pos = numbers.iter().position(|&x| x == 5);
    println!("Position of 5: {:?}", pos);

    // fold — the most general consumer
    let sum = numbers.iter().fold(0, |acc, &x| acc + x);
    println!("Fold sum: {sum}");

    // for_each — like a for loop
    numbers.iter().for_each(|x| print!("{x} "));
    println!();
}
```

### fold — The Swiss Army Knife

`fold` is the most powerful consumer. You can implement almost any other consumer with it:

```rust
fn main() {
    let numbers = vec![1, 2, 3, 4, 5];

    // Sum
    let sum = numbers.iter().fold(0, |acc, &x| acc + x);

    // Count
    let count = numbers.iter().fold(0, |acc, _| acc + 1);

    // Max
    let max = numbers.iter().fold(i32::MIN, |acc, &x| acc.max(x));

    // Join strings
    let words = vec!["hello", "beautiful", "world"];
    let sentence = words.iter().fold(String::new(), |mut acc, &word| {
        if !acc.is_empty() {
            acc.push(' ');
        }
        acc.push_str(word);
        acc
    });

    println!("Sum: {sum}, Count: {count}, Max: {max}");
    println!("Sentence: {sentence}");
}
```

## Laziness Matters

Iterator adapters are lazy — they build up a chain of transformations but don't execute until consumed:

```rust
fn main() {
    let v = vec![1, 2, 3, 4, 5];

    // This does NOTHING — no consumer
    v.iter().map(|x| {
        println!("processing {x}");  // never prints!
        x * 2
    });

    println!("--- now with collect ---");

    // This actually executes
    let result: Vec<i32> = v.iter().map(|&x| {
        println!("processing {x}");
        x * 2
    }).collect();

    println!("Result: {:?}", result);
}
```

This laziness is why iterator chains are efficient. In a chain like `.filter().map().take(5)`, each element flows through the entire pipeline one at a time. If `take(5)` has collected 5 elements, it stops — even if there are millions more in the original iterator.

## Chaining Operations

The real power comes from composing adapters:

```rust
fn main() {
    let data = vec![
        ("Alice", 92),
        ("Bob", 67),
        ("Charlie", 85),
        ("Diana", 94),
        ("Eve", 73),
        ("Frank", 88),
    ];

    // Find names of students who passed (>= 70), sorted by score descending
    let mut honor_roll: Vec<(&str, i32)> = data.iter()
        .filter(|(_, score)| *score >= 80)
        .copied()
        .collect();

    honor_roll.sort_by(|a, b| b.1.cmp(&a.1));

    println!("Honor Roll:");
    for (name, score) in &honor_roll {
        println!("  {name}: {score}");
    }

    // Compute statistics
    let scores: Vec<i32> = data.iter().map(|(_, s)| *s).collect();
    let avg = scores.iter().sum::<i32>() as f64 / scores.len() as f64;
    let max = scores.iter().max().unwrap();
    let min = scores.iter().min().unwrap();

    println!("\nStats: avg={avg:.1}, max={max}, min={min}");
}
```

## Implementing Iterator for Custom Types

```rust
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

    fn next(&mut self) -> Option<Self::Item> {
        let result = self.a;
        let new_b = self.a + self.b;
        self.a = self.b;
        self.b = new_b;
        Some(result)  // infinite iterator — always returns Some
    }
}

fn main() {
    // First 15 Fibonacci numbers
    let fibs: Vec<u64> = Fibonacci::new().take(15).collect();
    println!("Fibonacci: {:?}", fibs);

    // Fibonacci numbers under 1000
    let under_1000: Vec<u64> = Fibonacci::new()
        .take_while(|&n| n < 1000)
        .collect();
    println!("Under 1000: {:?}", under_1000);

    // Sum of even Fibonacci numbers under 4 million
    let sum: u64 = Fibonacci::new()
        .take_while(|&n| n < 4_000_000)
        .filter(|n| n % 2 == 0)
        .sum();
    println!("Sum of even fibs < 4M: {sum}");
}
```

## Performance

Iterator chains are not just convenient — they're fast. The compiler fuses the chain into a single loop through monomorphization and inlining. A chain of `.filter().map().collect()` generates roughly the same machine code as a hand-written loop with an if-statement.

```rust
fn main() {
    let data: Vec<i32> = (0..1_000_000).collect();

    // These produce equivalent machine code:

    // Iterator chain
    let result1: Vec<i32> = data.iter()
        .filter(|&&x| x % 2 == 0)
        .map(|&x| x * 3)
        .collect();

    // Manual loop
    let mut result2 = Vec::new();
    for &x in &data {
        if x % 2 == 0 {
            result2.push(x * 3);
        }
    }

    assert_eq!(result1, result2);
    println!("Both produce {} elements", result1.len());
}
```

Don't write manual loops thinking they'll be faster. They won't be. The iterator version is both more readable and equally performant.

Iterators are one of Rust's superpowers. They combine the expressiveness of functional programming with the performance of hand-tuned loops. Once you're comfortable chaining adapters, you'll wonder how you ever wrote data processing code without them.

Next: testing — because code without tests is just a hypothesis.
