---
title: "Lesson 8: Borrowing and References — Sharing without giving"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 8
keywords: [Rust, rust, borrowing, references, borrow checker, mutable references, immutable references]
tags: [Rust tutorial, rust, beginner]
date: '2024-03-18T13:00:00.000Z'
---

After the last lesson, you might be thinking "so every function I call takes my data and I never see it again?" That would be terrible. Borrowing is the answer — it lets you share data without transferring ownership, and it's where Rust's safety guarantees really shine.

## References with &

A reference lets you refer to a value without owning it:

```rust
fn print_length(s: &String) {
    println!("Length of '{}': {}", s, s.len());
}

fn main() {
    let s = String::from("hello");
    print_length(&s);  // lend s to the function
    println!("I still own: {s}");  // s is still valid
}
```

The `&` creates a reference. `&s` doesn't move `s` — it creates a pointer to `s` that the function can use. When the function returns, the reference goes away but `s` remains untouched.

Think of it like lending someone a book. They can read it, but you still own it. When they're done, the book comes back to your shelf.

The function parameter `s: &String` says "I'm borrowing a String, not taking it." This is called an *immutable reference* — you can read through it, but you can't modify the data.

## The Borrowing Rules

Two rules. That's all.

1. **You can have either one mutable reference OR any number of immutable references.** Not both at the same time.
2. **References must always be valid.** No dangling pointers.

Rule 1 is the big one. It prevents data races at compile time. A data race happens when two pointers access the same data simultaneously and at least one is writing. By ensuring you either have *one writer* or *many readers* (but never both), Rust makes data races impossible.

## Immutable References

You can have multiple immutable references simultaneously:

```rust
fn main() {
    let s = String::from("hello");

    let r1 = &s;
    let r2 = &s;
    let r3 = &s;

    println!("{r1}, {r2}, {r3}");  // all fine — multiple readers
}
```

No problem. Everyone's just reading. No conflict possible.

## Mutable References with &mut

To modify borrowed data, you need a mutable reference:

```rust
fn add_world(s: &mut String) {
    s.push_str(", world");
}

fn main() {
    let mut s = String::from("hello");
    add_world(&mut s);
    println!("{s}");  // "hello, world"
}
```

Three things must align:
1. The variable must be declared `mut` (`let mut s`)
2. The reference must be `&mut` (`&mut s`)
3. The function parameter must accept `&mut` (`s: &mut String`)

All three. If any is missing, the compiler rejects it. This triple-opt-in is intentional — mutation should be visible and explicit at every level.

## The Exclusivity Rule

You can't have a mutable reference while immutable references exist:

```rust
fn main() {
    let mut s = String::from("hello");

    let r1 = &s;      // immutable borrow
    let r2 = &s;      // another immutable borrow — fine

    // let r3 = &mut s; // ERROR: can't borrow as mutable while immutable borrows exist

    println!("{r1} {r2}");
}
```

And you can only have one mutable reference at a time:

```rust
fn main() {
    let mut s = String::from("hello");

    let r1 = &mut s;
    // let r2 = &mut s; // ERROR: can't have two mutable borrows

    r1.push_str("!");
    println!("{r1}");
}
```

## Non-Lexical Lifetimes (NLL)

Here's something that confuses people: the scope of a reference ends at its *last use*, not at the closing brace. The Rust compiler is smart about this.

```rust
fn main() {
    let mut s = String::from("hello");

    let r1 = &s;
    let r2 = &s;
    println!("{r1} {r2}");
    // r1 and r2 are no longer used after this point

    let r3 = &mut s;  // OK! the immutable borrows are done
    r3.push_str(" world");
    println!("{r3}");
}
```

This works because the compiler sees that `r1` and `r2` are never used after the first `println!`. Their borrows end there, so the mutable borrow of `r3` is allowed.

This is called NLL — Non-Lexical Lifetimes. Older versions of Rust were stricter (borrows lasted until the end of the scope), but modern Rust is smart about it.

## Dangling References

Rust prevents you from creating references that outlive their data:

```rust
// This won't compile:
// fn dangle() -> &String {
//     let s = String::from("hello");
//     &s  // ERROR: s is dropped at end of function, reference would dangle
// }

// Instead, return the owned value:
fn no_dangle() -> String {
    let s = String::from("hello");
    s  // ownership is transferred to the caller
}

fn main() {
    let s = no_dangle();
    println!("{s}");
}
```

The compiler catches dangling references at compile time. In C, returning a pointer to a local variable is a classic bug — the pointer points to stack memory that's been reclaimed. Rust simply won't let you do it.

## References to Primitives

References work with all types, not just heap-allocated ones:

```rust
fn double(x: &i32) -> i32 {
    x * 2
}

fn increment(x: &mut i32) {
    *x += 1;  // dereference with * to modify the value
}

fn main() {
    let mut n = 5;

    println!("doubled: {}", double(&n));

    increment(&mut n);
    println!("incremented: {n}");
}
```

Notice the `*x` in `increment`. The `*` is the dereference operator — it reaches through the reference to the actual value. You need it when assigning to the referenced value. For reading, Rust auto-dereferences in most cases, so `x * 2` works without `*x * 2` (though both are valid).

## The Borrow Checker in Practice

Let's look at a realistic scenario. You have a list of numbers and want to find the largest one:

```rust
fn largest(list: &[i32]) -> &i32 {
    let mut biggest = &list[0];

    for item in list {
        if item > biggest {
            biggest = item;
        }
    }

    biggest
}

fn main() {
    let numbers = vec![34, 50, 25, 100, 65];
    let result = largest(&numbers);
    println!("The largest number is {result}");
    println!("The list still exists: {:?}", numbers);
}
```

The function borrows a slice (`&[i32]`) and returns a reference to one element (`&i32`). It never takes ownership. The caller keeps their data.

The parameter type `&[i32]` is a slice — a reference to a contiguous sequence of `i32` values. It's more flexible than `&Vec<i32>` because it works with arrays, vectors, and other contiguous data. We'll cover slices properly in the next lesson.

## Common Borrow Checker Fights

### Fight 1: Modifying a collection while iterating

```rust
fn main() {
    let mut v = vec![1, 2, 3, 4, 5];

    // This won't compile:
    // for item in &v {
    //     if *item > 3 {
    //         v.push(*item * 2); // ERROR: can't borrow v as mutable while iterating
    //     }
    // }

    // Fix: collect modifications first, apply after
    let additions: Vec<i32> = v.iter()
        .filter(|&&x| x > 3)
        .map(|&x| x * 2)
        .collect();

    v.extend(additions);
    println!("{:?}", v);  // [1, 2, 3, 4, 5, 8, 10]
}
```

The iterator holds an immutable borrow of `v`. You can't also mutably borrow `v` to push to it. The fix is to separate the reading phase from the writing phase. This isn't a Rust limitation — modifying a collection during iteration is genuinely dangerous in any language. Rust just catches it at compile time.

### Fight 2: Multiple fields of a struct

```rust
struct Player {
    name: String,
    health: i32,
    score: i32,
}

fn main() {
    let mut player = Player {
        name: String::from("Alice"),
        health: 100,
        score: 0,
    };

    // This works! Rust can borrow different fields independently
    let name_ref = &player.name;
    player.score += 10;
    println!("{name_ref}: {}", player.score);
}
```

Rust is smart enough to know that borrowing `player.name` and modifying `player.score` don't conflict — they're different fields. This is called "split borrowing" and it works because the compiler can see the fields are disjoint.

### Fight 3: Returning a reference to a local

```rust
// Won't compile:
// fn first_word(s: &str) -> &str {
//     let word = String::from("hello");
//     &word  // ERROR: word is dropped at end of function
// }

// Works:
fn first_word(s: &str) -> &str {
    match s.find(' ') {
        Some(pos) => &s[..pos],
        None => s,
    }
}

fn main() {
    let sentence = String::from("hello world");
    let word = first_word(&sentence);
    println!("First word: {word}");
}
```

You can return a reference that borrows from an *input* reference — the data outlives the function. You can't return a reference to something created inside the function — that data dies when the function returns.

## When to Use & vs &mut vs Owned

Here's my decision tree:

- **Does the function need to store the data permanently?** → Take ownership (no `&`)
- **Does the function need to modify the data?** → `&mut`
- **Does the function just need to read the data?** → `&`

Start with `&`. Only upgrade to `&mut` or owned when you have a reason. This is idiomatic Rust — borrow by default, own when necessary.

## The Big Picture

Borrowing is what makes Rust practical. Ownership alone would be unusable — every function call would consume your data. Borrowing lets you share data safely, and the borrow checker ensures that sharing never causes problems.

Yes, the borrow checker will reject code that you know is safe. It's conservative by design — it rejects some safe programs to guarantee it never accepts an unsafe one. The false positive rate decreases as you learn to write idiomatic Rust. After a few months, you'll rarely fight it.

Next up: slices — the generalization of references that lets you borrow parts of data structures.
