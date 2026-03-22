---
title: "Lesson 2: Borrow Strategically — &T, &mut T, and when to clone"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 2
keywords: [Rust, rust, idiomatic, borrowing, references, clone, borrow checker]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-04-12T14:37:00.000Z'
---

A colleague once showed me their Rust code where every function parameter was `String` — never `&str`, never `&String`. When I asked why, they said "the borrow checker kept yelling at me, so I just take ownership of everything." Classic.

I get it. The borrow checker can feel like an overzealous hall monitor. But once you understand the borrowing rules — really understand them — you'll realize it's not restricting you. It's showing you a better design.

---

## The Two Borrowing Rules

Rust has exactly two borrowing rules. Not ten. Not fifty. Two.

1. **You can have any number of immutable references (`&T`) at the same time.**
2. **You can have exactly one mutable reference (`&mut T`), and no immutable references at the same time.**

That's the entire rulebook. Everything the borrow checker does is enforcing these two rules. And these rules exist for one reason: **preventing data races at compile time**.

```rust
fn main() {
    let mut data = vec![1, 2, 3];

    // Multiple immutable borrows — perfectly fine
    let r1 = &data;
    let r2 = &data;
    println!("{:?} {:?}", r1, r2);

    // Mutable borrow — fine, since r1 and r2 are done
    data.push(4);

    // But this won't compile:
    // let r3 = &data;
    // data.push(5); // ERROR: can't mutate while r3 exists
    // println!("{:?}", r3);
}
```

The key insight I missed for months: **borrows are lexically scoped in modern Rust** (since the 2018 edition, with Non-Lexical Lifetimes). A borrow ends at its *last use*, not at the end of the block. This makes things much more ergonomic than they used to be.

```rust
fn main() {
    let mut data = vec![1, 2, 3];

    let r1 = &data;
    println!("{:?}", r1); // r1's borrow ends here (last use)

    data.push(4); // Fine! r1 is no longer borrowed
    println!("{:?}", data);
}
```

---

## &T — The Default Choice

My rule of thumb: **start with `&T` for every function parameter**. Only escalate to `&mut T` or owned `T` when you have a reason.

```rust
// Good: takes a reference, doesn't need ownership
fn word_count(text: &str) -> usize {
    text.split_whitespace().count()
}

// Good: takes a slice reference, works with Vec, array, or slice
fn average(numbers: &[f64]) -> f64 {
    let sum: f64 = numbers.iter().sum();
    sum / numbers.len() as f64
}

// Good: takes a reference to a struct
fn print_user(user: &User) {
    println!("{}: {}", user.name, user.email);
}
```

Notice I'm using `&str` rather than `&String`, and `&[f64]` rather than `&Vec<f64>`. This is deliberate and important — by taking the more general type, your function works with more inputs. A `&String` automatically coerces to `&str`, and a `&Vec<f64>` coerces to `&[f64]`. But the reverse isn't true.

```rust
fn accepts_str(s: &str) {
    println!("{}", s);
}

fn main() {
    let owned = String::from("hello");
    let literal = "world";

    accepts_str(&owned);   // &String → &str ✓
    accepts_str(literal);  // &str → &str ✓

    // If the function took &String, this wouldn't work:
    // accepts_string_ref(literal); // ERROR: expected &String, got &str
}
```

---

## &mut T — When You Need to Modify

Use `&mut T` when the function needs to modify the data *in place*. This is less common than you'd think — many operations can return a new value instead.

```rust
// Modifying in place
fn normalize(values: &mut Vec<f64>) {
    let max = values.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    if max != 0.0 {
        for v in values.iter_mut() {
            *v /= max;
        }
    }
}

fn main() {
    let mut data = vec![2.0, 4.0, 6.0, 8.0, 10.0];
    normalize(&mut data);
    println!("{:?}", data); // [0.2, 0.4, 0.6, 0.8, 1.0]
}
```

But ask yourself: would it be cleaner to return a new vector?

```rust
fn normalized(values: &[f64]) -> Vec<f64> {
    let max = values.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    if max == 0.0 {
        return values.to_vec();
    }
    values.iter().map(|v| v / max).collect()
}

fn main() {
    let data = vec![2.0, 4.0, 6.0, 8.0, 10.0];
    let result = normalized(&data);
    println!("{:?}", result);
    // `data` is still available and unchanged
}
```

Both are valid. The `&mut` version avoids allocation. The return-new-value version is easier to reason about. I lean toward the functional style unless performance profiling shows the allocation matters.

---

## The Clone Question

So when is `.clone()` actually okay? More often than the Rust purists would have you believe.

Here's my framework:

**Clone freely when:**
- The data is small (a few hundred bytes or less)
- You're in application code, not a hot loop
- The alternative makes the code significantly harder to read
- You're prototyping and want to move fast

**Think twice about cloning when:**
- You're in a performance-critical path
- The data is large (large strings, big vectors)
- You're cloning in a loop
- You're writing a library that others will use in unknown contexts

```rust
// This clone is fine — it's a short string, called once
fn greet(name: &str) -> String {
    format!("Hello, {}!", name)
}

// This clone is suspicious — cloning inside a loop
fn bad_dedup(items: &[String]) -> Vec<String> {
    let mut seen = Vec::new();
    let mut result = Vec::new();
    for item in items {
        if !seen.contains(item) {
            seen.push(item.clone());     // clone in a loop — yikes
            result.push(item.clone());   // another clone!
        }
    }
    result
}

// Better: use references where possible
use std::collections::HashSet;

fn better_dedup(items: &[String]) -> Vec<&String> {
    let mut seen = HashSet::new();
    let mut result = Vec::new();
    for item in items {
        if seen.insert(item) {
            result.push(item); // just references — no cloning
        }
    }
    result
}
```

---

## Common Borrowing Patterns

### Pattern 1: Borrow for reads, own for storage

```rust
struct Cache {
    entries: Vec<String>,
}

impl Cache {
    fn contains(&self, key: &str) -> bool {
        self.entries.iter().any(|e| e == key)
    }

    fn insert(&mut self, key: String) {
        // Takes ownership — the Cache needs to store it
        if !self.contains(&key) {
            self.entries.push(key);
        }
    }
}
```

`contains` borrows `&str` because it just needs to look. `insert` takes `String` because the cache needs to own the data long-term.

### Pattern 2: Return references from methods

```rust
struct Config {
    db_url: String,
    app_name: String,
}

impl Config {
    fn db_url(&self) -> &str {
        &self.db_url
    }

    fn app_name(&self) -> &str {
        &self.app_name
    }
}
```

The methods return `&str` — borrowed from `self`. The caller gets read access without any allocation. This is extremely common and idiomatic.

### Pattern 3: Temporary mutable access

```rust
fn main() {
    let mut scores: Vec<(String, i32)> = vec![
        ("Alice".into(), 85),
        ("Bob".into(), 92),
        ("Charlie".into(), 78),
    ];

    // Sort by score descending
    scores.sort_by(|a, b| b.1.cmp(&a.1));

    // Now use immutably
    for (name, score) in &scores {
        println!("{}: {}", name, score);
    }
}
```

You mutate briefly (to sort), then borrow immutably for the rest. Rust's borrow checker handles the transition naturally.

---

## The Reborrowing Trick

Something that confused me early on: you can pass `&mut T` where `&T` is expected. Rust automatically "reborrows" — it creates an immutable reference from the mutable one.

```rust
fn read_data(data: &Vec<i32>) {
    println!("Length: {}", data.len());
}

fn modify_data(data: &mut Vec<i32>) {
    read_data(data); // &mut Vec<i32> automatically reborrows as &Vec<i32>
    data.push(42);
}

fn main() {
    let mut v = vec![1, 2, 3];
    modify_data(&mut v);
    println!("{:?}", v);
}
```

This works because if you have exclusive access (`&mut T`), you trivially also have shared access (`&T`). The compiler inserts the reborrow for you.

---

## The Dreaded "Cannot Borrow as Mutable Because It Is Also Borrowed as Immutable"

This is the error message that launches a thousand Stack Overflow questions. And it almost always means the same thing: you're trying to read and write the same data simultaneously.

```rust
fn main() {
    let mut v = vec![1, 2, 3, 4, 5];

    // This won't compile:
    // for &item in &v {
    //     if item > 3 {
    //         v.push(item * 2); // ERROR: can't mutate while iterating
    //     }
    // }

    // Solution: collect first, then mutate
    let additions: Vec<i32> = v.iter()
        .filter(|&&x| x > 3)
        .map(|&x| x * 2)
        .collect();

    v.extend(additions);
    println!("{:?}", v); // [1, 2, 3, 4, 5, 8, 10]
}
```

The fix is almost always the same: separate the reading phase from the writing phase. Collect intermediate results, then apply mutations. It feels annoying until you realize that modifying a collection while iterating over it is a bug in *any* language — Rust just catches it at compile time.

---

## A Decision Framework

When you're writing a function signature, use this checklist:

| I need to... | Use |
|---|---|
| Read the data | `&T` |
| Modify the data in place | `&mut T` |
| Store the data in a struct | `T` (take ownership) |
| Return the data to the caller | `T` (return owned) |
| Read string-like data | `&str` |
| Read list-like data | `&[T]` |
| Unsure and prototyping | `.clone()` and move on |

The last row is important. **Don't let borrowing analysis paralyze you.** Clone, ship your prototype, then optimize the clones away later. The compiler will always tell you where you can remove unnecessary clones — it'll never let you introduce a memory bug by doing so.

---

## Key Takeaways

- Default to `&T` for function parameters. Escalate to `&mut T` or owned `T` only when needed.
- Prefer `&str` over `&String`, and `&[T]` over `&Vec<T>` — they're more flexible.
- `.clone()` is not evil. Use it when it makes code clearer and the data is small.
- Separate reading from writing when the borrow checker complains.
- Non-Lexical Lifetimes mean borrows end at their last use, not at the end of the scope.

Next: `Option` and `Result` — Rust's way of handling absence and errors without sentinel values or exceptions.
