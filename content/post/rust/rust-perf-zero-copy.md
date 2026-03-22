---
title: "Lesson 12: Zero-Copy Parsing — bytes, nom, winnow"
author: Atharva Pandey
series: "Rust Performance Engineering"
lesson: 12
keywords: [Rust, rust, performance, zero-copy, parsing, nom, winnow, bytes, serde, borrowed data]
tags: [Rust tutorial, rust, performance]
date: '2025-04-12T11:30:00.000Z'
---

I once had to parse 2GB of log files per hour on a machine with 4GB of RAM. The naive approach — read line, split into fields, store as `String` — peaked at 6GB memory usage and fell over. The data was being duplicated everywhere: once in the read buffer, once in each split `String`, once in the output struct. Three copies of every byte.

The zero-copy version peaked at 2.1GB — basically the file size plus a thin layer of parsed references. Same output, same correctness, one-third the memory, twice the throughput. Zero-copy parsing is one of the most powerful techniques in Rust's performance toolkit, and the ownership system makes it uniquely natural here.

## What "Zero-Copy" Means

Zero-copy parsing means your parsed output refers back to the original input data instead of copying it. Instead of extracting substrings into new `String` allocations, you extract `&str` slices that point into the original buffer.

```rust
// COPYING: each field is a new heap allocation
struct LogEntryCopied {
    timestamp: String,   // owns its data
    level: String,       // owns its data
    message: String,     // owns its data
}

// ZERO-COPY: each field borrows from the input
struct LogEntryBorrowed<'a> {
    timestamp: &'a str,  // borrows from input
    level: &'a str,      // borrows from input
    message: &'a str,    // borrows from input
}
```

The borrowed version creates zero allocations per log entry. The `&str` slices are just a pointer and a length — 16 bytes each, pointing into the original input buffer.

```rust
fn parse_log_line(input: &str) -> Option<LogEntryBorrowed<'_>> {
    let mut parts = input.splitn(3, ' ');
    Some(LogEntryBorrowed {
        timestamp: parts.next()?,
        level: parts.next()?,
        message: parts.next()?,
    })
}

// Usage:
let line = "2025-04-12T11:30:00Z INFO User logged in from 10.0.0.1";
let entry = parse_log_line(line).unwrap();
// entry.timestamp is a &str pointing into `line` — no allocation
```

## The Performance Difference

Let's benchmark the two approaches:

```rust
use criterion::{black_box, criterion_group, criterion_main, Criterion};

fn parse_copied(input: &str) -> Vec<(String, String, String)> {
    input.lines()
        .filter_map(|line| {
            let mut parts = line.splitn(3, ' ');
            Some((
                parts.next()?.to_string(),
                parts.next()?.to_string(),
                parts.next()?.to_string(),
            ))
        })
        .collect()
}

fn parse_borrowed(input: &str) -> Vec<(&str, &str, &str)> {
    input.lines()
        .filter_map(|line| {
            let mut parts = line.splitn(3, ' ');
            Some((
                parts.next()?,
                parts.next()?,
                parts.next()?,
            ))
        })
        .collect()
}

fn bench_parsing(c: &mut Criterion) {
    let input: String = (0..10_000)
        .map(|i| format!("2025-04-12T{:02}:00:00Z INFO Message number {}\n", i % 24, i))
        .collect();

    c.bench_function("parse_copied_10k", |b| {
        b.iter(|| parse_copied(black_box(&input)))
    });

    c.bench_function("parse_borrowed_10k", |b| {
        b.iter(|| parse_borrowed(black_box(&input)))
    });
}

criterion_group!(benches, bench_parsing);
criterion_main!(benches);

// Typical results:
// parse_copied_10k:   ~1.8 ms  (30,000 allocations)
// parse_borrowed_10k: ~0.3 ms  (1 allocation for the Vec)
```

6x faster. And the gap widens with more data because allocation pressure grows linearly while borrowing stays constant.

## Zero-Copy with serde

serde supports zero-copy deserialization through `Cow` and borrowed `&str`:

```rust
use serde::Deserialize;
use std::borrow::Cow;

// This allocates a new String for every field
#[derive(Deserialize)]
struct ConfigOwned {
    name: String,
    version: String,
    description: String,
}

// This borrows directly from the JSON input
#[derive(Deserialize)]
struct ConfigBorrowed<'a> {
    #[serde(borrow)]
    name: &'a str,
    #[serde(borrow)]
    version: &'a str,
    #[serde(borrow)]
    description: &'a str,
}

// Or use Cow for flexible ownership
#[derive(Deserialize)]
struct ConfigCow<'a> {
    #[serde(borrow)]
    name: Cow<'a, str>,
    #[serde(borrow)]
    version: Cow<'a, str>,
    #[serde(borrow)]
    description: Cow<'a, str>,
}
```

```rust
let json = r#"{"name":"my-app","version":"1.0","description":"A cool app"}"#;

// Zero-copy — `config.name` points directly into `json`
let config: ConfigBorrowed = serde_json::from_str(json).unwrap();

// Important: `config` cannot outlive `json`!
// The borrow checker enforces this at compile time.
```

The `#[serde(borrow)]` attribute tells serde to borrow from the input instead of allocating. This works with `serde_json::from_str` (which takes `&str`) but NOT with `serde_json::from_reader` (which reads from a stream and can't guarantee the buffer stays alive).

## The bytes Crate

For binary protocols and network data, the `bytes` crate provides zero-copy byte buffers with reference counting:

```rust
use bytes::{Bytes, BytesMut, Buf, BufMut};

// BytesMut is a mutable buffer you write into
let mut buf = BytesMut::with_capacity(1024);
buf.put_slice(b"Hello, ");
buf.put_slice(b"world!");

// Freeze into an immutable Bytes — zero-copy
let frozen: Bytes = buf.freeze();

// Slice creates a new Bytes that shares the same memory
let hello: Bytes = frozen.slice(0..7);   // "Hello, " — no copy
let world: Bytes = frozen.slice(7..13);  // "world!" — no copy

// Both `hello` and `world` point into the same underlying buffer
// Reference counted — freed when all references are dropped
```

`Bytes` is reference-counted and cheaply cloneable. Cloning a `Bytes` just increments a counter — it doesn't copy the data. This is how high-performance networking code in Rust (hyper, tonic, axum) passes data around without copying.

```rust
use bytes::Bytes;

struct HttpResponse {
    headers: Vec<(Bytes, Bytes)>,  // header names and values
    body: Bytes,                     // response body
}

// Parsing an HTTP response can slice the input buffer
// into headers and body without any copying
fn parse_response(raw: Bytes) -> HttpResponse {
    // Find header/body boundary
    let header_end = find_double_crlf(&raw).unwrap();

    let header_bytes = raw.slice(..header_end);
    let body = raw.slice(header_end + 4..);

    let headers = parse_headers(header_bytes); // slices, not copies

    HttpResponse { headers, body }
}
```

## Parser Combinators: nom

`nom` is the most popular parser combinator library in Rust. It's designed for zero-copy parsing of both text and binary data:

```rust
use nom::{
    bytes::complete::{tag, take_while1},
    character::complete::{char, space0},
    combinator::map,
    sequence::{delimited, separated_pair, tuple},
    IResult,
};

// Parse a key-value pair like "name = Alice"
fn parse_kv(input: &str) -> IResult<&str, (&str, &str)> {
    separated_pair(
        take_while1(|c: char| c.is_alphanumeric() || c == '_'),
        delimited(space0, char('='), space0),
        take_while1(|c: char| c != '\n'),
    )(input)
}

// Returns (&str, (&str, &str)) — remaining input + parsed key-value
// Both key and value are &str slices into the original input
let (remaining, (key, value)) = parse_kv("name = Alice\nage = 30").unwrap();
assert_eq!(key, "name");
assert_eq!(value, "Alice");
assert_eq!(remaining, "\nage = 30");
```

nom parsers return `&str` slices by default — zero-copy. They compose beautifully:

```rust
use nom::{
    bytes::complete::{tag, take_while1},
    character::complete::{char, digit1, space0, space1},
    combinator::{map_res, opt},
    multi::separated_list0,
    sequence::{delimited, preceded, tuple},
    IResult,
};

#[derive(Debug)]
struct LogEntry<'a> {
    timestamp: &'a str,
    level: &'a str,
    module: &'a str,
    message: &'a str,
}

fn parse_timestamp(input: &str) -> IResult<&str, &str> {
    take_while1(|c: char| c != ' ')(input)
}

fn parse_level(input: &str) -> IResult<&str, &str> {
    delimited(
        char('['),
        take_while1(|c: char| c.is_alphabetic()),
        char(']'),
    )(input)
}

fn parse_log_entry(input: &str) -> IResult<&str, LogEntry<'_>> {
    let (input, timestamp) = parse_timestamp(input)?;
    let (input, _) = space1(input)?;
    let (input, level) = parse_level(input)?;
    let (input, _) = space1(input)?;
    let (input, module) = take_while1(|c: char| c != ':')(input)?;
    let (input, _) = tag(": ")(input)?;
    let (input, message) = take_while1(|c: char| c != '\n')(input)?;

    Ok((input, LogEntry { timestamp, level, module, message }))
}

// Usage:
let line = "2025-04-12T11:30:00Z [INFO] server: Request handled in 45ms";
let (_, entry) = parse_log_entry(line).unwrap();
// Every field in `entry` is a &str slice into `line`
```

## winnow — The Modern Alternative

`winnow` is a fork of nom with a cleaner API and better error messages. If you're starting a new project, I'd reach for winnow:

```rust
use winnow::{
    ascii::{alpha1, space0, space1},
    combinator::{delimited, separated_pair},
    token::take_while,
    PResult, Parser,
};

fn parse_header<'a>(input: &mut &'a str) -> PResult<(&'a str, &'a str)> {
    separated_pair(
        take_while(1.., |c: char| c != ':'),
        (space0, ':', space0),
        take_while(1.., |c: char| c != '\r' && c != '\n'),
    )
    .parse_next(input)
}
```

winnow's key difference: parsers take `&mut &str` instead of returning `(&str, T)`. This means less tuple unpacking and more natural composition. Performance is identical — both compile down to efficient state machines.

## Zero-Copy Patterns in Practice

### Pattern 1: Parse Then Reference

```rust
struct Document<'a> {
    source: String,              // owns the data
    sections: Vec<Section<'a>>,  // borrows from source
}

struct Section<'a> {
    title: &'a str,
    body: &'a str,
}

// This is tricky because of self-referential borrowing.
// The Document can't own both the String and references to it
// because moving the String would invalidate the references.

// Solution: use indices instead of references
struct DocumentOwned {
    source: String,
    sections: Vec<SectionIndex>,
}

struct SectionIndex {
    title_start: usize,
    title_end: usize,
    body_start: usize,
    body_end: usize,
}

impl DocumentOwned {
    fn get_title(&self, section: &SectionIndex) -> &str {
        &self.source[section.title_start..section.title_end]
    }

    fn get_body(&self, section: &SectionIndex) -> &str {
        &self.source[section.body_start..section.body_end]
    }
}
```

Using byte indices instead of references sidesteps the self-referential borrow problem entirely. The trade-off is slightly less ergonomic access, but zero-copy and fully owned.

### Pattern 2: Streaming Zero-Copy

For large files, read chunks and parse within each chunk:

```rust
use std::io::{BufRead, BufReader};
use std::fs::File;

fn process_large_file(path: &str) -> std::io::Result<u64> {
    let file = File::open(path)?;
    let reader = BufReader::with_capacity(64 * 1024, file); // 64KB buffer
    let mut count = 0u64;

    for line in reader.lines() {
        let line = line?;
        // Parse borrows from `line` — which is dropped each iteration
        // This is fine because we extract the data we need immediately
        if let Some(entry) = parse_log_line(&line) {
            if entry.level == "ERROR" {
                count += 1;
            }
        }
    }

    Ok(count)
}
```

### Pattern 3: Memory-Mapped Zero-Copy

For maximum performance with large files, memory-map the file and parse directly from the mapped region:

```rust
use memmap2::Mmap;
use std::fs::File;

fn parse_mmapped_file(path: &str) -> std::io::Result<Vec<LogEntryBorrowed<'_>>> {
    let file = File::open(path)?;
    let mmap = unsafe { Mmap::map(&file)? };
    let content = std::str::from_utf8(&mmap)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;

    // Parse directly from the memory-mapped buffer
    // No read() calls, no buffer copies
    let entries: Vec<LogEntryBorrowed<'_>> = content
        .lines()
        .filter_map(parse_log_line)
        .collect();

    Ok(entries)
}
```

Memory mapping lets the OS manage which parts of the file are in RAM. You get zero-copy access to the entire file without reading it all into a buffer first.

## When Zero-Copy Gets Complicated

Zero-copy isn't free in terms of complexity. The lifetime annotations get heavy:

```rust
// This is manageable
struct Simple<'a> {
    name: &'a str,
}

// This starts getting annoying
struct Nested<'a> {
    headers: Vec<(&'a str, &'a str)>,
    body: &'a [u8],
    metadata: HashMap<&'a str, Vec<&'a str>>,
}

// This is a sign you should reconsider
struct Deep<'a, 'b, 'c> {
    outer: &'a Nested<'b>,
    inner: &'c [Simple<'b>],
}
```

Rules of thumb:
- If the parsed data lives shorter than the input (process and discard), zero-copy is perfect
- If the parsed data needs to outlive the input, you need owned types (or `Cow`)
- If the lifetime annotations make your API unusable, consider using indices or owned types for the public API and zero-copy internally

## The Takeaway

Zero-copy parsing is Rust's secret weapon. The borrow checker, which is usually seen as a burden, is exactly what makes zero-copy parsing safe and practical. In C, zero-copy parsing is a minefield of dangling pointers. In Rust, the compiler guarantees your references are valid.

Use zero-copy when:
- Processing large volumes of data
- Memory is constrained
- Parsed data doesn't need to outlive the input
- You're building parsers, network protocols, or data pipelines

The tools: `&str` slices for text, `bytes::Bytes` for binary data, `nom` or `winnow` for complex grammars, and `serde` with `#[serde(borrow)]` for structured formats.

That wraps up the Rust Performance Engineering course. We started with philosophy — measure, don't guess — and worked through every layer of the performance stack. The theme throughout: profile first, understand the bottleneck, apply the right tool, and measure again. Rust gives you the control to optimize anything. The discipline is knowing when to stop.
