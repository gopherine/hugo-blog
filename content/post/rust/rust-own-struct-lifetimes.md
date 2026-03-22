---
title: "Lesson 7: Lifetimes in Structs — References That Live in Types"
author: Atharva Pandey
series: "Rust Ownership & Lifetimes Masterclass"
lesson: 7
keywords: [Rust, rust, ownership, lifetimes, struct lifetimes, references in structs]
tags: [Rust tutorial, rust, ownership, lifetimes]
date: '2024-05-27T10:42:00.000Z'
---

Putting a reference inside a struct is where lifetimes go from "mildly confusing" to "wait, what?" for most people. I remember spending an entire afternoon trying to make a struct hold a `&str` and wondering why the compiler kept yelling at me.

The fundamental tension: structs outlive function calls. References might not. The compiler needs you to prove the reference won't dangle.

## The Problem

```rust
// This won't compile
struct Excerpt {
    content: &str,
}
```

```
error[E0106]: missing lifetime specifier
 --> src/main.rs:2:14
  |
2 |     content: &str,
  |              ^ expected named lifetime parameter
```

Why? Because the compiler needs to know: how long does the thing `content` points to live? Without that information, it can't guarantee the reference is valid for the struct's entire lifetime.

## The Solution: Lifetime Parameters on Structs

```rust
struct Excerpt<'a> {
    content: &'a str,
}

fn main() {
    let novel = String::from("Call me Ishmael. Some years ago...");
    let first_sentence = novel.split('.').next().unwrap();

    let excerpt = Excerpt {
        content: first_sentence,
    };

    println!("Excerpt: {}", excerpt.content);
}
```

The `'a` on the struct says: "an `Excerpt` can't outlive the data it references." The struct borrows data — it doesn't own it. And the compiler enforces that the borrowed data lives long enough.

## When It Goes Wrong

```rust
struct Excerpt<'a> {
    content: &'a str,
}

fn main() {
    let excerpt;

    {
        let text = String::from("hello world");
        excerpt = Excerpt { content: &text };
    }
    // text is dropped here

    // println!("{}", excerpt.content);  // ERROR: text doesn't live long enough
}
```

The compiler catches this. `excerpt` would hold a dangling reference to `text` which has been dropped. Exactly the kind of bug that plagues C and C++ code — except here it's a compile error, not a segfault at 3am.

## Multiple Lifetime Parameters in Structs

Sometimes a struct borrows from multiple sources with different lifetimes:

```rust
struct Comparison<'a, 'b> {
    left: &'a str,
    right: &'b str,
}

fn main() {
    let left_text = String::from("hello");

    let comp;
    {
        let right_text = String::from("world");
        comp = Comparison {
            left: &left_text,
            right: &right_text,
        };
        println!("{} vs {}", comp.left, comp.right);
    }
    // right_text dropped — comp is no longer valid

    // But if we used a single lifetime 'a for both fields,
    // the compiler would constrain both to the shorter lifetime
    // anyway. Separate lifetimes give more flexibility.
}
```

In practice, most structs use a single lifetime parameter. Multiple lifetimes are useful when you explicitly want different fields to have different lifetime constraints — like a parser that borrows from both input and configuration.

## Methods on Structs with Lifetimes

Implementing methods is straightforward — you declare the lifetime on the `impl` block:

```rust
struct TextWindow<'a> {
    text: &'a str,
    start: usize,
    end: usize,
}

impl<'a> TextWindow<'a> {
    fn new(text: &'a str) -> Self {
        TextWindow {
            text,
            start: 0,
            end: text.len(),
        }
    }

    fn visible(&self) -> &str {
        &self.text[self.start..self.end]
    }

    fn narrow(&mut self, by: usize) {
        self.start += by;
        self.end -= by;
    }

    // Return type borrows from self (which borrows from 'a)
    // Lifetime elision handles this — Rule 3
    fn first_word(&self) -> &str {
        self.visible().split_whitespace().next().unwrap_or("")
    }
}

fn main() {
    let source = String::from("the quick brown fox jumps over");
    let mut window = TextWindow::new(&source);

    println!("Full: {}", window.visible());
    window.narrow(4);
    println!("Narrowed: {}", window.visible());
    println!("First word: {}", window.first_word());
}
```

## The Owned vs. Borrowed Struct Decision

Here's the question I ask on every struct I write: should this field own its data or borrow it?

**Own it** when:
- The struct needs to live independently
- The data comes from different sources with different lifetimes
- You're storing the struct in a collection for later use
- The struct crosses thread boundaries

**Borrow it** when:
- The struct is temporary (parsing, iteration, computation)
- The source data obviously outlives the struct
- You want to avoid allocations
- You're building a "view" into existing data

```rust
// Owned: lives independently, easy to store and pass around
struct User {
    name: String,
    email: String,
}

// Borrowed: lightweight view into existing data
struct UserView<'a> {
    name: &'a str,
    email: &'a str,
}

fn main() {
    let name = String::from("Atharva");
    let email = String::from("a@example.com");

    // Borrowed view — zero allocations
    let view = UserView {
        name: &name,
        email: &email,
    };
    println!("{}: {}", view.name, view.email);

    // Owned — independent copy
    let user = User {
        name: name.clone(),
        email: email.clone(),
    };
    println!("{}: {}", user.name, user.email);
}
```

## Structs with Mixed Owned and Borrowed Fields

You can mix owned and borrowed fields. The lifetime only applies to the borrowed ones:

```rust
struct LogEntry<'a> {
    message: &'a str,      // borrowed — points to existing log buffer
    timestamp: u64,         // owned — plain integer
    level: String,          // owned — the struct manages this
}

impl<'a> LogEntry<'a> {
    fn format(&self) -> String {
        format!("[{}] {}: {}", self.timestamp, self.level, self.message)
    }
}
```

## A Real-World Example: Tokenizer

Here's a pattern I use all the time — a tokenizer that borrows from the input string:

```rust
#[derive(Debug)]
struct Token<'a> {
    kind: TokenKind,
    text: &'a str,
    position: usize,
}

#[derive(Debug)]
enum TokenKind {
    Word,
    Number,
    Whitespace,
    Punctuation,
}

struct Tokenizer<'a> {
    input: &'a str,
    pos: usize,
}

impl<'a> Tokenizer<'a> {
    fn new(input: &'a str) -> Self {
        Tokenizer { input, pos: 0 }
    }

    fn next_token(&mut self) -> Option<Token<'a>> {
        let remaining = &self.input[self.pos..];
        if remaining.is_empty() {
            return None;
        }

        let first = remaining.chars().next().unwrap();
        let start = self.pos;

        let (kind, len) = if first.is_alphabetic() {
            let len = remaining.chars().take_while(|c| c.is_alphabetic()).count();
            (TokenKind::Word, len)
        } else if first.is_numeric() {
            let len = remaining.chars().take_while(|c| c.is_numeric()).count();
            (TokenKind::Number, len)
        } else if first.is_whitespace() {
            let len = remaining.chars().take_while(|c| c.is_whitespace()).count();
            (TokenKind::Whitespace, len)
        } else {
            (TokenKind::Punctuation, first.len_utf8())
        };

        self.pos += len;

        Some(Token {
            kind,
            text: &self.input[start..self.pos],
            position: start,
        })
    }
}

fn main() {
    let code = "hello 42 world!";
    let mut tokenizer = Tokenizer::new(code);

    while let Some(token) = tokenizer.next_token() {
        println!("{:?}", token);
    }

    // code is still valid — tokenizer only borrowed it
    println!("Original: {}", code);
}
```

Every `Token` holds a `&str` slice into the original input. Zero allocations for the token text. The lifetime `'a` ensures no token can outlive the input string.

## The Anti-Pattern: Storing Borrowed Data Long-Term

Don't do this:

```rust
struct AppState<'a> {
    current_user: &'a str,  // borrowed from... where?
    database_url: &'a str,  // who owns this?
}
```

If your struct represents long-lived application state, own the data. Borrowing is for temporary views, not for things that live for the program's lifetime.

```rust
// Better: own your data for long-lived state
struct AppState {
    current_user: String,
    database_url: String,
}
```

Or use `'static` if the data really does live forever (string literals, leaked allocations). But that's the next lesson's territory.

The key takeaway: lifetime parameters on structs aren't scary once you accept what they mean. "This struct borrows data and can't outlive it." That's all. The compiler does the heavy lifting of verifying that guarantee at every usage site.
