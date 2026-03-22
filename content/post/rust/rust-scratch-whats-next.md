---
title: "Lesson 25: Where to Go from Here — Your Rust learning path"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 25
keywords: [Rust, rust, learning path, next steps, intermediate rust, advanced rust, rust projects, rust resources]
tags: [Rust tutorial, rust, beginner]
date: '2024-04-18T18:00:00.000Z'
---

You've made it through 24 lessons. You understand ownership, borrowing, structs, enums, traits, generics, error handling, closures, iterators, and file I/O. That's not nothing — that's the foundation of every Rust program ever written. But foundations are for building on. Here's where to go from here.

## What You Know Now

Take a second to appreciate what you've learned. You can:

- Set up a Rust project with Cargo
- Write functions with proper ownership and borrowing
- Model data with structs and enums
- Handle errors with Result and the `?` operator
- Use collections: Vec, HashMap, HashSet
- Write generic code with trait bounds
- Process data with iterators and closures
- Test your code with `#[test]`
- Read and write files

That's a real skill set. You can build useful things right now. Before diving into advanced topics, I strongly recommend you build something. Nothing fancy — a command-line tool, a file processor, a simple data structure. The gap between "I understand the concepts" and "I can write a program" is only bridged by practice.

## Project Ideas for Right Now

Here are projects that use *exactly* the skills you already have:

**A command-line grep clone.** Read a file line by line, search for a pattern, print matching lines. Add line numbers. Add case-insensitive search. This exercises file I/O, string processing, iterators, and error handling.

**A JSON-to-CSV converter.** Use `serde_json` to read JSON, write CSV output. Great practice for working with external crates and data transformation.

**A Markdown link checker.** Parse a Markdown file, extract URLs, check which ones are valid. Good practice for string parsing and (eventually) async HTTP.

**A simple key-value store.** Read and write a text file as a persistent HashMap. Support get, set, delete, list commands from the CLI. This exercises everything — file I/O, collections, error handling, pattern matching.

**A word frequency counter.** Read text, count word occurrences, output sorted by frequency. You've essentially done this in the lessons — now build a polished version with CLI arguments and file I/O.

## Intermediate Topics to Learn Next

### Lifetimes

You've used lifetimes implicitly — every `&str` and `&[T]` has a lifetime. The compiler has been inferring them for you (lifetime elision). When the inference isn't enough, you'll need explicit lifetime annotations:

```rust
// This won't compile without a lifetime annotation:
fn longest<'a>(s1: &'a str, s2: &'a str) -> &'a str {
    if s1.len() > s2.len() { s1 } else { s2 }
}

fn main() {
    let s1 = String::from("long string");
    let result;
    {
        let s2 = String::from("short");
        result = longest(&s1, &s2);
        println!("{result}");
    }
}
```

Lifetimes aren't new — they're making explicit what the compiler already knows. The Rust Book has an excellent chapter on this.

### Async/Await

Most real-world Rust applications need async I/O — web servers, API clients, anything network-bound. The `tokio` runtime is the de facto standard:

```rust
// [dependencies]
// tokio = { version = "1", features = ["full"] }
// reqwest = { version = "0.11", features = ["json"] }

// use reqwest;
//
// #[tokio::main]
// async fn main() -> Result<(), Box<dyn std::error::Error>> {
//     let body = reqwest::get("https://httpbin.org/ip")
//         .await?
//         .text()
//         .await?;
//     println!("{body}");
//     Ok(())
// }
```

Async Rust is powerful but has a steeper learning curve than async in other languages. Wait until you're comfortable with ownership and traits before tackling it.

### Smart Pointers

Beyond `Box`, there's `Rc` (reference counting), `Arc` (atomic reference counting for threads), `RefCell` (interior mutability), and `Mutex`/`RwLock` for thread-safe shared state. These are essential for complex data structures and concurrent programming.

### Concurrency

Rust's ownership model makes concurrent programming genuinely safe — data races are caught at compile time. Start with:

- `std::thread::spawn` for OS threads
- `Arc<Mutex<T>>` for shared state
- Channels (`std::sync::mpsc`) for message passing
- Then move to `tokio` for async concurrency

### Unsafe Rust

Sometimes you need to bypass the borrow checker — FFI with C code, performance-critical inner loops, implementing data structures the borrow checker can't reason about. `unsafe` doesn't mean "dangerous" — it means "the programmer is responsible for upholding safety invariants here."

Don't learn unsafe Rust until you deeply understand safe Rust. You should be able to explain *exactly* what invariants the borrow checker enforces before you take on the responsibility of maintaining those invariants yourself.

## Recommended Resources

### Books

- **The Rust Programming Language ("The Book")** — Free online. The definitive reference. If you haven't read it cover to cover, do that. [doc.rust-lang.org/book](https://doc.rust-lang.org/book/)
- **Rust by Example** — Learn by doing. Tons of runnable examples. [doc.rust-lang.org/rust-by-example](https://doc.rust-lang.org/rust-by-example/)
- **Programming Rust** (Blandy, Orendorff, Tindall) — Deep, technical, excellent. My favorite Rust book for intermediate learners.
- **Rust for Rustaceans** (Jon Gjengset) — For when you're past beginner. Covers idioms, design patterns, and how things work under the hood.

### Practice

- **Rustlings** — Small exercises that guide you through Rust concepts. Great for reinforcement. [github.com/rust-lang/rustlings](https://github.com/rust-lang/rustlings)
- **Exercism Rust Track** — Mentored practice problems. The Rust track is well-designed. [exercism.org/tracks/rust](https://exercism.org/tracks/rust)
- **Advent of Code** — Annual programming puzzles in December. Rust is excellent for these.
- **Project Euler** — Math + programming problems. Good for practicing iterators and number crunching.

### Community

- **r/rust** — The subreddit. Generally helpful and welcoming.
- **The Rust Users Forum** — [users.rust-lang.org](https://users.rust-lang.org). Ask questions, get thoughtful answers.
- **This Week in Rust** — Weekly newsletter. Stay current with the ecosystem.
- **Rust Discord** — Real-time help. The beginners channel is active and friendly.

## Crates to Explore

As you build real projects, you'll need these:

| Category | Crate | Description |
|----------|-------|-------------|
| CLI | `clap` | Argument parsing |
| Web | `axum` or `actix-web` | HTTP servers |
| HTTP client | `reqwest` | Making HTTP requests |
| Serialization | `serde` | The standard for data formats |
| Database | `sqlx` | Async SQL with compile-time checked queries |
| Async runtime | `tokio` | The async runtime |
| Error handling | `anyhow` + `thiserror` | App errors + library errors |
| Logging | `tracing` | Structured, async-aware logging |
| Date/time | `chrono` | Date and time handling |
| Regex | `regex` | Regular expressions |
| CLI color | `colored` | Terminal colors |
| Config | `config` | Configuration file reading |

## The Domains Where Rust Shines

Where should you apply Rust? Where it has the biggest impact:

**Command-line tools.** Rust compiles to a single static binary with zero dependencies. No runtime to install, no version conflicts. Tools like `ripgrep`, `bat`, `fd`, and `exa` are all Rust — and they're all blazingly fast.

**Web services.** Rust's performance and memory safety make it excellent for API servers, especially high-throughput ones. The `axum` framework is clean and idiomatic.

**Systems programming.** Operating system components, device drivers, embedded systems. This is Rust's home turf.

**WebAssembly.** Rust has first-class WASM support. If you need to run performance-critical code in the browser, Rust → WASM is a proven path.

**Data processing.** ETL pipelines, log processing, data transformation. Rust's iterators and zero-cost abstractions make it competitive with C for throughput while being much safer.

## My Honest Advice

**Write code every day.** Even 30 minutes. Consistency beats intensity for learning.

**Read other people's Rust.** Browse popular crates on GitHub. See how experienced developers structure their code. The standard library source is surprisingly readable.

**Don't fight the borrow checker.** When it rejects your code, it's usually right. Instead of reaching for `clone()` or `unsafe`, ask yourself: "What is the borrow checker telling me about my data's lifetime and ownership?" The answer usually points to a better design.

**Start simple, add complexity later.** Don't begin a project with async + traits + generics + lifetimes. Start with concrete types and synchronous code. Generalize when you need to.

**Ask for help.** The Rust community is genuinely welcoming. No one will judge you for a beginner question. Everyone started where you are.

---

You've got the foundation. Now build something. Break things. Fix them. Read the compiler errors — they're better than any tutor. And have fun. Rust is a demanding language, but the programs you'll write with it will be among the most reliable code you've ever shipped.

Good luck.
