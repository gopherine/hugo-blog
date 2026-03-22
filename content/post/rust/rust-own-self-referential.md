---
title: "Lesson 10: Self-Referential Structs — The Problem and Solutions"
author: Atharva Pandey
series: "Rust Ownership & Lifetimes Masterclass"
lesson: 10
keywords: [Rust, rust, ownership, lifetimes, self-referential, Pin, ouroboros]
tags: [Rust tutorial, rust, ownership, lifetimes]
date: '2024-06-02T15:40:00.000Z'
---

Every Rust developer eventually tries to build this: a struct that owns some data AND holds a reference into that data. It seems perfectly reasonable. It's also the single thing Rust refuses to let you do safely — and for very good reasons.

I wasted two days on this in my second month of Rust. Let me save you those two days.

## The Desire

You want a struct like this:

```rust
// THIS DOES NOT COMPILE
struct SelfRef {
    data: String,
    slice: &str,  // points into data
}
```

Where `slice` is a reference into `data`. The struct owns the string and also has a view into it. Makes total sense in C. Makes total sense conceptually. Rust says no.

## Why It's Impossible (Naively)

First, the lifetime problem:

```rust
// Attempt 1: add a lifetime
struct SelfRef<'a> {
    data: String,
    slice: &'a str,  // 'a must be the lifetime of... data? But data is owned!
}
```

What lifetime do you give `slice`? It needs to reference `data`, which lives as long as the struct itself. But you can't create a reference to something that hasn't been fully constructed yet — you'd need to set `data` first, then set `slice` to point into `data`. But the struct must be constructed all at once.

Second, the move problem. Even if you could construct it:

```rust
// HYPOTHETICAL — imagine this worked
let s = SelfRef {
    data: String::from("hello"),
    slice: /* points to s.data somehow */,
};

let s2 = s;  // MOVE: s's data moves to a new memory location
             // slice still points at the OLD location
             // DANGLING REFERENCE
```

When you move a struct, its fields get memcpy'd to a new location. The `data` field moves. But `slice` still points to where `data` *used to be*. Classic dangling pointer.

This is the fundamental issue: **Rust moves are memcpy**, and self-references become invalid after memcpy.

## Solution 1: Separate the Owner and the Borrow

The simplest fix — stop trying to be self-referential:

```rust
struct Parsed {
    raw: String,
    word_offsets: Vec<(usize, usize)>,  // store offsets, not references
}

impl Parsed {
    fn new(input: &str) -> Self {
        let raw = input.to_string();
        let word_offsets: Vec<(usize, usize)> = raw
            .split_whitespace()
            .map(|word| {
                let start = word.as_ptr() as usize - raw.as_ptr() as usize;
                (start, start + word.len())
            })
            .collect();

        Parsed { raw, word_offsets }
    }

    fn words(&self) -> Vec<&str> {
        self.word_offsets
            .iter()
            .map(|(start, end)| &self.raw[*start..*end])
            .collect()
    }
}

fn main() {
    let parsed = Parsed::new("hello beautiful world");
    println!("{:?}", parsed.words());
    // ["hello", "beautiful", "world"]
}
```

Store indices or offsets instead of references. Reconstruct the references on demand through methods. This is the most common solution and usually the right one.

## Solution 2: Two Separate Allocations

Keep the data in one place and the references in another:

```rust
fn main() {
    let data = String::from("hello world");
    let words: Vec<&str> = data.split_whitespace().collect();

    // data and words are separate variables
    // words borrows from data — standard Rust borrowing
    println!("{:?}", words);
    println!("Original: {}", data);
}
```

Or with a helper struct that borrows:

```rust
struct RawData {
    content: String,
}

struct ParsedView<'a> {
    source: &'a RawData,
    words: Vec<&'a str>,
}

impl RawData {
    fn parse(&self) -> ParsedView<'_> {
        let words = self.content.split_whitespace().collect();
        ParsedView {
            source: self,
            words,
        }
    }
}

fn main() {
    let raw = RawData {
        content: String::from("the quick brown fox"),
    };

    let parsed = raw.parse();
    println!("Words: {:?}", parsed.words);
    println!("Source length: {}", parsed.source.content.len());
}
```

## Solution 3: Use String Indices

The `std::ops::Range<usize>` approach — my personal favorite for parsers:

```rust
#[derive(Debug)]
struct Token {
    kind: TokenKind,
    span: std::ops::Range<usize>,
}

#[derive(Debug)]
enum TokenKind {
    Word,
    Number,
    Whitespace,
}

struct Document {
    source: String,
    tokens: Vec<Token>,
}

impl Document {
    fn new(source: String) -> Self {
        let mut tokens = Vec::new();
        let mut pos = 0;
        let bytes = source.as_bytes();

        while pos < bytes.len() {
            let start = pos;
            let kind = if bytes[pos].is_ascii_alphabetic() {
                while pos < bytes.len() && bytes[pos].is_ascii_alphabetic() {
                    pos += 1;
                }
                TokenKind::Word
            } else if bytes[pos].is_ascii_digit() {
                while pos < bytes.len() && bytes[pos].is_ascii_digit() {
                    pos += 1;
                }
                TokenKind::Number
            } else {
                pos += 1;
                TokenKind::Whitespace
            };

            tokens.push(Token {
                kind,
                span: start..pos,
            });
        }

        Document { source, tokens }
    }

    fn token_text(&self, token: &Token) -> &str {
        &self.source[token.span.clone()]
    }

    fn words(&self) -> Vec<&str> {
        self.tokens
            .iter()
            .filter(|t| matches!(t.kind, TokenKind::Word))
            .map(|t| self.token_text(t))
            .collect()
    }
}

fn main() {
    let doc = Document::new("hello 42 world".to_string());
    println!("Words: {:?}", doc.words());
    println!("All tokens: {:?}", doc.tokens);
}
```

No self-references. No lifetime parameters. The `Document` owns the source string and stores indices into it. Methods reconstruct references on the fly. Clean, efficient, and the borrow checker is happy.

## Solution 4: Pin + Unsafe (When You Really Need It)

Sometimes — rarely — you genuinely need a self-referential struct. Async runtimes create them under the hood for futures. If you're in this territory, `Pin` is the primitive:

```rust
use std::pin::Pin;
use std::marker::PhantomPinned;

struct SelfRef {
    data: String,
    slice_ptr: *const str,  // raw pointer — no lifetime!
    _pin: PhantomPinned,
}

impl SelfRef {
    fn new(data: String) -> Pin<Box<Self>> {
        let s = SelfRef {
            data,
            slice_ptr: std::ptr::null(),
            _pin: PhantomPinned,
        };
        let mut boxed = Box::pin(s);

        // Safety: we're setting up the self-reference before
        // anyone can move the struct (it's pinned)
        let slice_ptr: *const str = &boxed.data[..];
        unsafe {
            let mut_ref = Pin::as_mut(&mut boxed);
            Pin::get_unchecked_mut(mut_ref).slice_ptr = slice_ptr;
        }

        boxed
    }

    fn slice(&self) -> &str {
        // Safety: slice_ptr was set to point at data, which hasn't moved
        // because we're pinned
        unsafe { &*self.slice_ptr }
    }
}

fn main() {
    let s = SelfRef::new(String::from("hello world"));
    println!("Slice: {}", s.slice());
    println!("Data: {}", s.data);
}
```

This works but requires `unsafe`. The `PhantomPinned` marker prevents the type from implementing `Unpin`, which means once it's pinned, it can't be moved. That protects the self-reference.

I do not recommend this for application code. It's the kind of thing you write once in a library and encapsulate behind a safe API.

## Solution 5: The ouroboros Crate

If you really need self-referential structs without writing unsafe yourself:

```rust
// In Cargo.toml: ouroboros = "0.18"

/*
use ouroboros::self_referencing;

#[self_referencing]
struct Parsed {
    source: String,
    #[borrows(source)]
    #[covariant]
    words: Vec<&'this str>,
}

fn main() {
    let parsed = ParsedBuilder {
        source: "hello world foo bar".to_string(),
        words_builder: |source: &str| source.split_whitespace().collect(),
    }.build();

    parsed.with_words(|words| {
        println!("{:?}", words);
    });
}
*/
```

`ouroboros` generates the unsafe code for you with a safe API. The `#[borrows(source)]` annotation tells it which field the reference points into. It handles pinning and lifetime erasure internally.

But honestly? 95% of the time, solution 1 (store indices) or solution 2 (separate owner and view) is the right answer. Self-referential structs are a code smell that usually indicates a design issue.

## Why Rust Makes This Hard

This isn't an oversight. Rust deliberately makes self-referential structs difficult because they're fundamentally at odds with move semantics. In a language where values can be memcpy'd freely, internal pointers break.

C++ gets away with it (sort of) because it has move constructors and move assignment operators that can update internal pointers. Rust's moves are always bitwise copies — no custom move logic. That's a deliberate design choice that makes the language simpler and faster, at the cost of making self-references hard.

The tradeoff is worth it. The workarounds are straightforward, and you rarely need actual self-references once you've internalized the index-based pattern.

Stop fighting the borrow checker on this one. Store indices. It's cleaner anyway.
