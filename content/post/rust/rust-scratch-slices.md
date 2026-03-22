---
title: "Lesson 9: Slices — Views into data"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 9
keywords: [Rust, rust, slices, string slices, array slices, "&str", "&[T]", slice types]
tags: [Rust tutorial, rust, beginner]
date: '2024-03-20T10:45:00.000Z'
---

Slices clicked for me when I stopped thinking of them as a language feature and started thinking of them as a design pattern: "here's a window into someone else's data." They're one of Rust's most elegant ideas, and you'll use them everywhere.

## What Is a Slice?

A slice is a reference to a contiguous sequence of elements in a collection. It doesn't own the data — it's a view into data owned by something else.

```rust
fn main() {
    let numbers = [1, 2, 3, 4, 5];

    let slice = &numbers[1..4];  // elements at index 1, 2, 3
    println!("{:?}", slice);     // [2, 3, 4]
}
```

Under the hood, a slice is two things: a pointer to the start of the data and a length. That's it. No allocation, no copying.

## Slice Syntax

The range syntax should feel familiar from Lesson 6:

```rust
fn main() {
    let data = [10, 20, 30, 40, 50, 60, 70];

    let a = &data[1..4];   // [20, 30, 40] — exclusive end
    let b = &data[1..=4];  // [20, 30, 40, 50] — inclusive end
    let c = &data[..3];    // [10, 20, 30] — from start
    let d = &data[4..];    // [50, 60, 70] — to end
    let e = &data[..];     // [10, 20, 30, 40, 50, 60, 70] — everything

    println!("a: {:?}", a);
    println!("b: {:?}", b);
    println!("c: {:?}", c);
    println!("d: {:?}", d);
    println!("e: {:?}", e);
}
```

## Array Slices: &[T]

The type of a slice over an array of `i32` values is `&[i32]`. This is the idiomatic way to accept a "list of things" as a function parameter:

```rust
fn sum(values: &[i32]) -> i32 {
    let mut total = 0;
    for v in values {
        total += v;
    }
    total
}

fn average(values: &[f64]) -> f64 {
    let total: f64 = values.iter().sum();
    total / values.len() as f64
}

fn main() {
    let arr = [1, 2, 3, 4, 5];
    let vec = vec![10, 20, 30];

    // Both work with &[i32]
    println!("Array sum: {}", sum(&arr));
    println!("Vec sum: {}", sum(&vec));
    println!("Partial sum: {}", sum(&arr[1..4]));

    let grades = vec![92.5, 87.0, 95.5, 78.0, 88.5];
    println!("Average: {:.1}", average(&grades));
}
```

This is why slices matter for API design: `&[T]` works with arrays, vectors, and other slices. If your function takes `&Vec<i32>`, it only works with vectors. If it takes `&[i32]`, it works with everything. Always prefer `&[T]` over `&Vec<T>` in function parameters. Clippy will yell at you if you don't.

## String Slices: &str

You've seen `&str` already — it's a string slice. The relationship between `String` and `&str` mirrors the relationship between `Vec<T>` and `&[T]`:

```rust
fn main() {
    let s = String::from("hello world");

    let hello: &str = &s[0..5];
    let world: &str = &s[6..11];

    println!("{hello} {world}");
}
```

String literals are also `&str`:

```rust
fn main() {
    let literal: &str = "hello";  // stored in the binary, not on the heap
    println!("{literal}");
}
```

We'll cover strings in much more depth in the next lesson. For now, just know that `&str` is a slice into string data.

## Mutable Slices

Slices can be mutable:

```rust
fn zero_out(slice: &mut [i32]) {
    for item in slice.iter_mut() {
        *item = 0;
    }
}

fn main() {
    let mut numbers = [1, 2, 3, 4, 5];
    println!("Before: {:?}", numbers);

    zero_out(&mut numbers[1..4]);
    println!("After: {:?}", numbers);  // [1, 0, 0, 0, 5]
}
```

The same borrowing rules apply: you can have either one `&mut [T]` or many `&[T]`, not both.

## Useful Slice Methods

Slices come with a rich set of methods:

```rust
fn main() {
    let data = [3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5];

    // Basic info
    println!("Length: {}", data.len());
    println!("Empty: {}", data.is_empty());

    // Access
    println!("First: {:?}", data.first());      // Some(3)
    println!("Last: {:?}", data.last());         // Some(5)
    println!("Get(2): {:?}", data.get(2));       // Some(4)
    println!("Get(99): {:?}", data.get(99));     // None (safe!)

    // Search
    println!("Contains 9: {}", data.contains(&9));
    println!("Starts with [3,1]: {}", data.starts_with(&[3, 1]));

    // Subslices
    let (left, right) = data.split_at(5);
    println!("Left: {:?}", left);    // [3, 1, 4, 1, 5]
    println!("Right: {:?}", right);  // [9, 2, 6, 5, 3, 5]

    // Chunks
    for chunk in data.chunks(3) {
        println!("Chunk: {:?}", chunk);
    }

    // Windows (sliding window)
    for window in data.windows(3) {
        println!("Window: {:?}", window);
    }
}
```

The `.get()` method is worth highlighting. Unlike indexing with `[]`, which panics on out-of-bounds access, `.get()` returns `Option<&T>` — `Some` if the index is valid, `None` if it isn't. Use `.get()` when the index might be invalid.

## Sorting with Mutable Slices

```rust
fn main() {
    let mut numbers = vec![38, 27, 43, 3, 9, 82, 10];

    numbers.sort();
    println!("Sorted: {:?}", numbers);

    numbers.sort_by(|a, b| b.cmp(a));  // reverse sort
    println!("Reverse: {:?}", numbers);

    let mut names = vec!["Charlie", "Alice", "Bob"];
    names.sort();
    println!("Names: {:?}", names);
}
```

`.sort()` works on mutable slices (and `Vec` derefs to a mutable slice). It sorts in-place, no allocation.

## Slices and the Borrow Checker

Slices interact with ownership in important ways:

```rust
fn main() {
    let mut s = String::from("hello world");

    let word = first_word(&s);  // immutable borrow via slice

    // s.clear(); // ERROR: can't mutably borrow while slice exists
    println!("First word: {word}");

    // After the last use of `word`, we can mutate again
    s.clear();
    println!("Cleared: '{s}'");
}

fn first_word(s: &str) -> &str {
    let bytes = s.as_bytes();
    for (i, &byte) in bytes.iter().enumerate() {
        if byte == b' ' {
            return &s[..i];
        }
    }
    s
}
```

The slice `word` borrows from `s`. As long as `word` exists, you can't mutate `s`. This prevents the classic bug where you hold a pointer into a string and then modify the string, invalidating the pointer. In C, this is a dangling pointer. In Rust, it's a compile error.

## Splitting Mutable Slices

You can split a mutable slice into non-overlapping parts and modify them independently:

```rust
fn main() {
    let mut data = [1, 2, 3, 4, 5, 6];

    let (left, right) = data.split_at_mut(3);
    // left: &mut [1, 2, 3]
    // right: &mut [4, 5, 6]

    left[0] = 10;
    right[0] = 40;

    println!("{:?}", data);  // [10, 2, 3, 40, 5, 6]
}
```

`split_at_mut` gives you two non-overlapping mutable slices. Since they don't overlap, there's no aliasing — modifying one can't affect the other. The borrow checker allows this because the compiler can prove safety.

## Patterns I Use Constantly

### Processing pairs

```rust
fn main() {
    let data = vec![1, 2, 3, 4, 5, 6, 7, 8];

    // Process pairs
    for pair in data.chunks(2) {
        match pair {
            [a, b] => println!("{a} + {b} = {}", a + b),
            [a] => println!("leftover: {a}"),
            _ => unreachable!(),
        }
    }
}
```

### Finding patterns

```rust
fn has_consecutive_duplicates(data: &[i32]) -> bool {
    data.windows(2).any(|w| w[0] == w[1])
}

fn main() {
    println!("{}", has_consecutive_duplicates(&[1, 2, 3, 3, 4]));  // true
    println!("{}", has_consecutive_duplicates(&[1, 2, 3, 4, 5]));  // false
}
```

### Binary search

```rust
fn main() {
    let sorted = [2, 5, 8, 12, 16, 23, 38, 56, 72, 91];

    match sorted.binary_search(&23) {
        Ok(index) => println!("Found 23 at index {index}"),
        Err(index) => println!("23 not found, would insert at {index}"),
    }

    match sorted.binary_search(&24) {
        Ok(index) => println!("Found 24 at index {index}"),
        Err(index) => println!("24 not found, would insert at {index}"),
    }
}
```

## The Mental Model

Think of slices as fat pointers. A regular reference `&T` is a pointer — it tells you *where* the data is. A slice `&[T]` is a fat pointer — it tells you where the data *starts* and how *long* it is. Same idea, more information.

This is why slices don't need to know the total size of the underlying collection. A `&[i32]` doesn't know if it's looking at 3 elements of a 5-element array or 3 elements of a 1000-element vector. It doesn't care. It knows its start and its length, and that's all it needs.

This is also why slices are great for function parameters. Your function doesn't need to know — or care — what kind of collection the data came from. It just needs a view into contiguous memory.

Next lesson: strings. The topic every Rust beginner dreads. It's not as bad as people make it out to be — but there are genuine gotchas.
