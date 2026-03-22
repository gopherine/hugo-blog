---
title: "Lesson 10: Strings — String vs &str and why it matters"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 10
keywords: [Rust, rust, strings, String, str, "&str", UTF-8, string slice, string types]
tags: [Rust tutorial, rust, beginner]
date: '2024-03-22T15:30:00.000Z'
---

Strings are the #1 source of confusion for Rust beginners. I see the same questions every week: "Why are there two string types?" "Why can't I index a string?" "Why is this so much harder than in Python?" It's not harder — it's *more honest*. Other languages hide the complexity of text. Rust makes you deal with it.

## The Two String Types

Rust has two main string types:

- **`String`** — owned, heap-allocated, growable, mutable
- **`&str`** — borrowed, a slice/view into string data, immutable (usually)

```rust
fn main() {
    let owned: String = String::from("hello");  // heap-allocated, you own it
    let borrowed: &str = "hello";               // string literal, embedded in binary

    println!("{owned} {borrowed}");
}
```

The relationship between `String` and `&str` is exactly like `Vec<u8>` and `&[u8]`. A `String` is a buffer you own. A `&str` is a view into someone else's string data (or a literal baked into your binary).

## When to Use Which

**Function parameters**: Use `&str`. It accepts both `String` (via automatic dereferencing) and `&str`.

```rust
fn greet(name: &str) {
    println!("Hello, {name}!");
}

fn main() {
    let owned = String::from("Alice");
    let borrowed = "Bob";

    greet(&owned);    // &String auto-derefs to &str
    greet(borrowed);  // &str directly
}
```

**Struct fields where you own the data**: Use `String`.

```rust
struct User {
    name: String,     // owned — the struct owns this data
    email: String,
}

fn main() {
    let user = User {
        name: String::from("Alice"),
        email: String::from("alice@example.com"),
    };
    println!("{}: {}", user.name, user.email);
}
```

**Return values where you're creating new data**: Use `String`.

```rust
fn full_name(first: &str, last: &str) -> String {
    format!("{first} {last}")
}

fn main() {
    let name = full_name("Atharva", "Pandey");
    println!("{name}");
}
```

My rule: accept `&str`, return `String`, store `String` in structs. This covers 90% of cases.

## Creating Strings

Multiple ways, each with its use case:

```rust
fn main() {
    // From a literal
    let s1 = String::from("hello");
    let s2 = "hello".to_string();
    let s3 = "hello".to_owned();

    // Empty string
    let s4 = String::new();

    // With capacity (avoids reallocation if you know the size)
    let s5 = String::with_capacity(100);

    // From format macro
    let name = "world";
    let s6 = format!("hello, {name}");

    println!("{s1} {s2} {s3} '{s4}' len:{} {s6}", s5.len());
}
```

`String::from()`, `.to_string()`, and `.to_owned()` all do the same thing for string literals. I use `String::from()` most of the time — it's the most explicit about what's happening.

## Strings Are UTF-8

This is the key insight. Rust strings are always valid UTF-8. Always. Not "usually" or "by convention" — the type system guarantees it.

```rust
fn main() {
    let hello = String::from("Hello");          // ASCII (subset of UTF-8)
    let chinese = String::from("你好世界");      // Chinese characters
    let emoji = String::from("Hello 🦀🌍");     // Emoji
    let mixed = String::from("café résumé");    // Accented characters

    println!("{hello}");
    println!("{chinese}");
    println!("{emoji}");
    println!("{mixed}");

    // Length in bytes, not characters!
    println!("'{}' is {} bytes", hello, hello.len());    // 5 bytes
    println!("'{}' is {} bytes", chinese, chinese.len()); // 12 bytes
    println!("'{}' is {} bytes", emoji, emoji.len());    // 12 bytes
}
```

Notice that `.len()` returns bytes, not characters. "你好世界" is 4 characters but 12 bytes (3 bytes per CJK character in UTF-8).

## Why You Can't Index Strings

```rust
fn main() {
    let s = String::from("hello");
    // let h = s[0]; // ERROR: String cannot be indexed by integer
    println!("{s}");
}
```

This surprises everyone. In Python, `s[0]` gives you the first character. In Rust, it's a compile error. Why?

Because indexing implies O(1) access, and O(1) access to the nth *character* of a UTF-8 string is impossible. UTF-8 is a variable-width encoding — characters can be 1 to 4 bytes. To find the 5th character, you'd have to scan from the beginning, counting characters. That's O(n), not O(1), and Rust refuses to hide that cost behind an innocent-looking `[0]`.

There are three ways to view a string:

```rust
fn main() {
    let s = "café";

    // As bytes
    println!("Bytes: {:?}", s.as_bytes());
    // [99, 97, 102, 195, 169]  — 5 bytes (é is 2 bytes in UTF-8)

    // As characters (Unicode scalar values)
    println!("Chars: {:?}", s.chars().collect::<Vec<char>>());
    // ['c', 'a', 'f', 'é']  — 4 chars

    // As grapheme clusters (what humans think of as characters)
    // Requires the `unicode-segmentation` crate — not in std
}
```

If you want the nth character, use `.chars()`:

```rust
fn main() {
    let s = "hello";
    let first: char = s.chars().nth(0).unwrap();
    println!("First char: {first}");
}
```

But be aware this is O(n) — it scans from the start. If you need random character access, convert to a `Vec<char>` first.

## String Slicing

You *can* slice strings by byte ranges:

```rust
fn main() {
    let s = String::from("hello world");
    let hello = &s[0..5];
    let world = &s[6..11];
    println!("{hello} {world}");
}
```

But be careful — slicing on a non-character boundary panics:

```rust
fn main() {
    let s = String::from("café");
    // let slice = &s[0..4]; // PANIC! 'é' starts at byte 3 and is 2 bytes
    let slice = &s[0..3];     // "caf" — OK, valid boundary
    println!("{slice}");
}
```

This is a runtime panic, not a compile error. Rust can't check byte boundaries at compile time. When slicing strings, be careful about UTF-8 boundaries, or use `.char_indices()` to find safe split points.

## Modifying Strings

`String` is mutable. You can grow it, shrink it, modify it:

```rust
fn main() {
    let mut s = String::from("hello");

    // Append
    s.push(' ');           // push a single char
    s.push_str("world");   // push a string slice
    println!("{s}");        // "hello world"

    // Insert
    s.insert(5, ',');       // insert at byte position
    println!("{s}");        // "hello, world"

    // Replace
    let new_s = s.replace("world", "Rust");
    println!("{new_s}");    // "hello, Rust"

    // Remove
    let trimmed = "  hello  ".trim();
    println!("'{trimmed}'");  // 'hello'

    // Truncate
    s.truncate(5);
    println!("{s}");        // "hello"

    // Clear
    s.clear();
    println!("empty: '{s}'");  // empty: ''
}
```

## Concatenation

Several approaches, each with different trade-offs:

```rust
fn main() {
    // format! — most readable, allocates a new String
    let first = "hello";
    let second = "world";
    let result = format!("{first} {second}");
    println!("{result}");

    // push_str — mutates in place, efficient
    let mut s = String::from("hello");
    s.push_str(" world");
    println!("{s}");

    // + operator — takes ownership of left side
    let s1 = String::from("hello");
    let s2 = String::from(" world");
    let s3 = s1 + &s2;  // s1 is moved, s2 is borrowed
    // println!("{s1}"); // ERROR: s1 was moved
    println!("{s3}");

    // Building up strings
    let parts = vec!["hello", "beautiful", "world"];
    let joined = parts.join(" ");
    println!("{joined}");
}
```

My recommendation: use `format!` for readability, `push_str` for performance in loops, and `.join()` for combining collections. The `+` operator's ownership semantics are confusing — I avoid it.

## String Conversions

```rust
fn main() {
    // &str to String
    let s: String = "hello".to_string();
    let s2: String = String::from("hello");

    // String to &str
    let borrowed: &str = &s;
    let borrowed2: &str = s.as_str();

    // Number to String
    let n: String = 42.to_string();
    let pi: String = 3.14.to_string();

    // String to number
    let num: i32 = "42".parse().unwrap();
    let float: f64 = "3.14".parse().unwrap();

    // With error handling
    match "not_a_number".parse::<i32>() {
        Ok(n) => println!("Parsed: {n}"),
        Err(e) => println!("Parse error: {e}"),
    }

    println!("{s} {s2} {borrowed} {borrowed2} {n} {pi} {num} {float}");
}
```

The `.parse()` method returns a `Result` — it can fail. Always handle the error case (or at least use `unwrap()` knowingly during prototyping).

## Iterating Over Strings

```rust
fn main() {
    let s = "hello 🦀";

    // By character
    for c in s.chars() {
        println!("char: {c}");
    }

    // By byte
    for b in s.bytes() {
        println!("byte: {b}");
    }

    // Characters with their byte positions
    for (i, c) in s.char_indices() {
        println!("byte {i}: {c}");
    }

    // Split
    let csv = "one,two,three,four";
    for part in csv.split(',') {
        println!("part: {part}");
    }

    // Lines
    let text = "line one\nline two\nline three";
    for line in text.lines() {
        println!("line: {line}");
    }
}
```

## A Practical Example

Let's build a simple word counter:

```rust
use std::collections::HashMap;

fn word_count(text: &str) -> HashMap<String, usize> {
    let mut counts = HashMap::new();

    for word in text.split_whitespace() {
        let word = word.to_lowercase();
        let word = word.trim_matches(|c: char| !c.is_alphanumeric());
        if !word.is_empty() {
            *counts.entry(word).or_insert(0) += 1;
        }
    }

    counts
}

fn main() {
    let text = "the quick brown fox jumps over the lazy dog. The dog barked.";
    let counts = word_count(text);

    let mut sorted: Vec<_> = counts.iter().collect();
    sorted.sort_by(|a, b| b.1.cmp(a.1));

    for (word, count) in sorted {
        println!("{word}: {count}");
    }
}
```

Notice the function takes `&str` (borrows text) and returns `HashMap<String, usize>` (owns its data). The owned `String` keys in the HashMap are necessary because we're transforming the words (lowercasing, trimming) — we can't just reference the original text.

## The String Cheat Sheet

| Want to... | Use |
|-----------|-----|
| Store owned text in a struct | `String` |
| Accept text as a function parameter | `&str` |
| Return new text from a function | `String` |
| Write a string literal | `"hello"` (type: `&str`) |
| Concatenate strings | `format!()` or `push_str()` |
| Check string length in bytes | `.len()` |
| Count characters | `.chars().count()` |
| Find a substring | `.contains()`, `.find()` |
| Get a substring | Slice `&s[start..end]` (careful with UTF-8!) |

Strings in Rust are more work than in Python or JavaScript. That's the cost of correctness — Rust doesn't let you pretend that text is simpler than it actually is. But once you internalize the `String` / `&str` distinction and the UTF-8 rules, it becomes second nature.

Next: structs. Time to start modeling real data.
