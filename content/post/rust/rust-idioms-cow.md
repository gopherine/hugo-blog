---
title: "Lesson 11: Cow — Clone on write for flexible APIs"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 11
keywords: [Rust, rust, idiomatic, Cow, clone on write, borrowing, performance]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-04-27T15:33:00.000Z'
---

There's a function signature dilemma that every Rust developer hits: should this function return `&str` or `String`? If you return `&str`, you avoid allocation — but sometimes you *need* to create a new string. If you return `String`, you allocate every time — even when you don't need to.

`Cow` says: "Why not both?"

The name stands for "Clone on Write," and it's one of those types that seems weird until you use it — and then you wonder how you lived without it.

---

## The Dilemma

Here's a function that normalizes whitespace. Sometimes the input is already fine (no changes needed), and sometimes it needs modification:

```rust
// Option A: Always allocate
fn normalize_v1(input: &str) -> String {
    input.split_whitespace().collect::<Vec<&str>>().join(" ")
}

// This allocates a new String even when the input is already normalized.
// For "hello world" → allocates "hello world" (identical copy)
// For "hello   world" → allocates "hello world" (needed)
```

```rust
// Option B: Return a reference... but what about the modified case?
// fn normalize_v2(input: &str) -> &str {
//     // Can't return a reference to locally created data!
//     // If we need to modify, we have to create a String,
//     // but we can't return a reference to it.
// }
```

Option A wastes allocations. Option B is impossible. Enter `Cow`.

---

## What Cow Actually Is

```rust
use std::borrow::Cow;

// Simplified definition:
// enum Cow<'a, B: ToOwned + ?Sized> {
//     Borrowed(&'a B),
//     Owned(B::Owned),
// }

// For strings, this becomes:
// Cow<'a, str> = either &'a str or String
```

`Cow<'a, str>` is either a borrowed `&str` or an owned `String`. It's an enum that defers allocation until mutation is needed. If you only read, you borrow. If you need to modify, you clone into an owned value.

```rust
use std::borrow::Cow;

fn normalize(input: &str) -> Cow<'_, str> {
    if input.contains("  ") || input.starts_with(' ') || input.ends_with(' ') {
        // Input needs modification → allocate
        Cow::Owned(input.split_whitespace().collect::<Vec<&str>>().join(" "))
    } else {
        // Input is fine → just borrow
        Cow::Borrowed(input)
    }
}

fn main() {
    let clean = normalize("hello world");
    let dirty = normalize("  hello   world  ");

    println!("Clean: {} (borrowed: {})", clean, matches!(clean, Cow::Borrowed(_)));
    println!("Dirty: {} (borrowed: {})", dirty, matches!(dirty, Cow::Borrowed(_)));
    // Clean: hello world (borrowed: true)
    // Dirty: hello world (borrowed: false)
}
```

No allocation for already-clean inputs. Allocation only when modification is needed.

---

## Cow Derefs to the Borrowed Type

`Cow<'_, str>` implements `Deref<Target = str>`, so you can use it anywhere a `&str` is expected:

```rust
use std::borrow::Cow;

fn print_greeting(name: &str) {
    println!("Hello, {}!", name);
}

fn get_name(custom: Option<&str>) -> Cow<'_, str> {
    match custom {
        Some(name) => Cow::Borrowed(name),
        None => Cow::Owned(String::from("stranger")),
    }
}

fn main() {
    let name = get_name(Some("Atharva"));
    print_greeting(&name); // Cow<str> derefs to &str

    let name = get_name(None);
    print_greeting(&name); // Still works
}
```

---

## Real-World Uses

### Config/Environment Processing

```rust
use std::borrow::Cow;

fn resolve_env(value: &str) -> Cow<'_, str> {
    if value.starts_with("${") && value.ends_with('}') {
        let var_name = &value[2..value.len() - 1];
        match std::env::var(var_name) {
            Ok(resolved) => Cow::Owned(resolved),
            Err(_) => Cow::Borrowed(value), // keep the original
        }
    } else {
        Cow::Borrowed(value)
    }
}

fn main() {
    std::env::set_var("APP_PORT", "8080");

    let val1 = resolve_env("${APP_PORT}");
    let val2 = resolve_env("literal_value");

    println!("{}", val1); // "8080"
    println!("{}", val2); // "literal_value" (no allocation)
}
```

### Escaping/Encoding

This is perhaps the most common use case — escaping strings where most inputs don't need escaping:

```rust
use std::borrow::Cow;

fn escape_html(input: &str) -> Cow<'_, str> {
    if input.contains('&') || input.contains('<') || input.contains('>') || input.contains('"') {
        let mut output = String::with_capacity(input.len());
        for c in input.chars() {
            match c {
                '&' => output.push_str("&amp;"),
                '<' => output.push_str("&lt;"),
                '>' => output.push_str("&gt;"),
                '"' => output.push_str("&quot;"),
                other => output.push(other),
            }
        }
        Cow::Owned(output)
    } else {
        Cow::Borrowed(input)
    }
}

fn main() {
    let safe = escape_html("Hello, world!");
    let unsafe_html = escape_html("<script>alert('xss')</script>");

    println!("{}", safe);       // No allocation
    println!("{}", unsafe_html); // Allocated only because escaping was needed
}
```

In a web framework processing thousands of strings per request, this optimization is significant. Most strings don't contain HTML-special characters, so you avoid allocations for the majority of inputs.

### URL Path Normalization

```rust
use std::borrow::Cow;

fn normalize_path(path: &str) -> Cow<'_, str> {
    if path.ends_with('/') && path.len() > 1 {
        Cow::Owned(path.trim_end_matches('/').to_string())
    } else if path.is_empty() {
        Cow::Borrowed("/")
    } else {
        Cow::Borrowed(path)
    }
}

fn main() {
    println!("{}", normalize_path("/api/users/"));  // "/api/users" (allocated)
    println!("{}", normalize_path("/api/users"));    // "/api/users" (borrowed)
    println!("{}", normalize_path(""));              // "/" (borrowed)
}
```

---

## Cow in Function Parameters

You can also accept `Cow` as a parameter — though I'd say this is less common and usually `impl Into<String>` or `&str` is clearer:

```rust
use std::borrow::Cow;

struct Logger {
    prefix: String,
}

impl Logger {
    fn log(&self, message: Cow<'_, str>) {
        println!("[{}] {}", self.prefix, message);
    }
}

fn main() {
    let logger = Logger { prefix: String::from("APP") };

    // Can pass either borrowed or owned
    logger.log(Cow::Borrowed("static message"));
    logger.log(Cow::Owned(format!("dynamic: {}", 42)));

    // But honestly, this is usually cleaner:
    // fn log(&self, message: &str) { ... }
    // or
    // fn log(&self, message: impl std::fmt::Display) { ... }
}
```

Cow really shines in *return types*, not parameters. For parameters, `&str` or `impl Into<String>` are usually the better choice.

---

## Cow With Other Types

`Cow` works with any type that implements `ToOwned`. That includes slices:

```rust
use std::borrow::Cow;

fn ensure_sorted(data: &[i32]) -> Cow<'_, [i32]> {
    if data.windows(2).all(|w| w[0] <= w[1]) {
        Cow::Borrowed(data) // Already sorted — no allocation
    } else {
        let mut sorted = data.to_vec();
        sorted.sort();
        Cow::Owned(sorted) // Needed sorting — allocated
    }
}

fn main() {
    let sorted = vec![1, 2, 3, 4, 5];
    let unsorted = vec![5, 3, 1, 4, 2];

    let result1 = ensure_sorted(&sorted);
    let result2 = ensure_sorted(&unsorted);

    println!("Already sorted: {:?} (borrowed: {})", &*result1, matches!(result1, Cow::Borrowed(_)));
    println!("Was unsorted:   {:?} (borrowed: {})", &*result2, matches!(result2, Cow::Borrowed(_)));
}
```

---

## The `to_mut()` Method

If you have a `Cow` and need to mutate the value, `to_mut()` ensures the data is owned (cloning if necessary) and gives you a mutable reference:

```rust
use std::borrow::Cow;

fn ensure_trailing_newline(text: &str) -> Cow<'_, str> {
    let mut cow: Cow<'_, str> = Cow::Borrowed(text);
    if !text.ends_with('\n') {
        cow.to_mut().push('\n'); // Clones into owned String if still borrowed
    }
    cow
}

fn main() {
    let with_newline = ensure_trailing_newline("hello\n");
    let without = ensure_trailing_newline("hello");

    println!("{:?} borrowed: {}", with_newline, matches!(with_newline, Cow::Borrowed(_)));
    println!("{:?} borrowed: {}", without, matches!(without, Cow::Borrowed(_)));
}
```

---

## When NOT to Use Cow

- **When you always need to modify** → Just return `String`. Cow adds complexity for no benefit.
- **When the data is always borrowed** → Just return `&str`. Cow adds unnecessary indirection.
- **When allocation is cheap and rare** → The cognitive overhead of Cow might not be worth the optimization.
- **In function parameters** → Usually `&str` or `impl Into<String>` is clearer.

Cow is an optimization tool. Profile first. If you see that a function allocates needlessly in the common case, and the common case doesn't require modification, Cow is your answer.

---

## Key Takeaways

- `Cow<'a, str>` is either `&'a str` (borrowed) or `String` (owned) — defers allocation until mutation is needed.
- Use Cow in *return types* when a function sometimes returns borrowed data and sometimes owned data.
- Cow implements `Deref`, so it can be used anywhere the borrowed type is expected.
- Great for escaping, normalization, and config resolution where most inputs pass through unchanged.
- Don't use Cow when you always modify or always borrow — it only helps when the behavior is conditional.
