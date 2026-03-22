---
title: "Lesson 6: String Performance — SmartString, CompactStr, and when to care"
author: Atharva Pandey
series: "Rust Performance Engineering"
lesson: 6
keywords: [Rust, rust, performance, strings, SmartString, CompactStr, small string optimization, SSO]
tags: [Rust tutorial, rust, performance]
date: '2025-03-25T11:50:00.000Z'
---

I was building an in-memory index that stored about 2 million tag strings. Most were short — "rust", "go", "api", "v2" — averaging 6 bytes. But each `String` carries 24 bytes of overhead (pointer + length + capacity) plus the heap allocation for the actual data. That's 24 bytes of bookkeeping to store 6 bytes of useful information. Plus 2 million separate allocations hammering the allocator.

Switching to `CompactStr` cut memory usage by 60% and index-building time by 40%. Strings matter more than you think.

## How String Works Under the Hood

A `String` in Rust is three machine words on the stack:

```
String layout (on 64-bit):
┌──────────────────┐
│ ptr:      8 bytes │ → points to heap allocation
│ len:      8 bytes │ → current length
│ capacity: 8 bytes │ → allocated capacity
└──────────────────┘
Total stack: 24 bytes
Plus heap: len bytes (at minimum)
```

So a String containing "hello" uses 24 bytes on the stack + 5 bytes on the heap (or more, depending on capacity). For short strings, the overhead dwarfs the data.

`&str` is better — just 16 bytes (pointer + length), no heap allocation of its own. But you need a `String` somewhere to own the data.

## The Problem with Short Strings

In most real-world applications, strings are short. HTTP headers, JSON keys, enum-like tags, identifiers. Studies of production workloads consistently show that the median string length is under 20 bytes.

For each of these short strings, you're paying:
- 24 bytes of stack overhead
- One heap allocation (~30ns)
- One cache miss when dereferencing the pointer
- Fragmented memory that hurts cache performance

What if strings below a certain length could just... live inline, right there on the stack, no heap involved?

## Small String Optimization (SSO)

C++'s `std::string` has done SSO for decades — short strings are stored directly inside the string object, no heap allocation. Rust's standard `String` doesn't do this. But several crates fill that gap.

### CompactStr

`CompactStr` is my go-to. It's 24 bytes (same size as `String`) but stores strings up to 24 bytes inline on the stack. Only spills to the heap for longer strings.

```rust
use compact_str::CompactString;

// No heap allocation — stored inline
let short = CompactString::from("hello");

// Also inline — up to 24 bytes on 64-bit
let medium = CompactString::from("this fits inline!!");

// Spills to heap — same as String
let long = CompactString::from("this string is too long to fit inline so it goes to the heap");
```

The magic: `CompactString` uses the same 24 bytes that `String` uses for ptr/len/capacity, but repurposes them as inline storage when the string is short enough. A discriminant bit tells it whether to read the bytes as inline data or as a heap pointer.

```rust
use compact_str::CompactString;

#[divan::bench]
fn create_string() -> String {
    String::from("rust_perf")
}

#[divan::bench]
fn create_compact() -> CompactString {
    CompactString::from("rust_perf")
}

// Typical results:
// create_string:  ~28 ns (heap allocation)
// create_compact: ~3 ns  (inline, no allocation)
```

9x faster for a common operation. And this compounds — create a million of them and you save 25 seconds.

### SmartString

`SmartString` is similar but takes a different approach to the inline threshold:

```rust
use smartstring::alias::String as SmartString;

let s = SmartString::from("short"); // inline, no alloc
let s2: SmartString = "also inline".into();
```

SmartString stores up to 23 bytes inline on 64-bit platforms (one byte reserved for the discriminant). It's API-compatible with `std::string::String` for most operations.

### CompactStr vs SmartString

| Feature | CompactStr | SmartString |
|---------|-----------|-------------|
| Inline capacity (64-bit) | 24 bytes | 23 bytes |
| Size of type | 24 bytes | 24 bytes |
| `Clone` when inline | memcpy (fast) | memcpy (fast) |
| Heap fallback | Standard allocator | Standard allocator |
| API compatibility | Good | Excellent |
| `serde` support | Yes | Yes |

I lean toward CompactStr because that extra byte of inline capacity actually matters — "Content-Type" (12 bytes) and "Authorization" (13 bytes) both fit inline, and those are extremely common in web services.

## When SSO Matters (and When It Doesn't)

### It Matters When:

**You have lots of short strings.** Millions of identifiers, tags, keys. The allocation savings compound.

```rust
use compact_str::CompactString;

// 1 million tags, most under 20 bytes
// With String: 1M heap allocations, ~30MB overhead
// With CompactString: 0 heap allocations, ~24MB total
let tags: Vec<CompactString> = raw_tags.iter()
    .map(|t| CompactString::from(t.as_str()))
    .collect();
```

**You clone strings frequently.** Cloning an inline CompactString is a 24-byte memcpy. Cloning a heap-allocated String involves a malloc + memcpy.

```rust
// Cloning a String: ~30ns (malloc + memcpy)
// Cloning a CompactString (inline): ~2ns (stack memcpy)

#[divan::bench]
fn clone_string() -> String {
    let s = String::from("api_key");
    divan::black_box(s.clone())
}

#[divan::bench]
fn clone_compact() -> CompactString {
    let s = CompactString::from("api_key");
    divan::black_box(s.clone())
}
```

**You're building hash maps with string keys.** The allocation per key adds up fast.

### It Doesn't Matter When:

**Your strings are long.** If most strings are over 24 bytes, CompactString degrades to regular String behavior. No benefit, slight overhead for the discriminant check.

**You only have a few strings.** If you're dealing with 100 strings instead of 100,000, the total savings are microseconds. Not worth the dependency.

**You never clone or create strings in hot paths.** If strings are created once at startup and only read thereafter, `&str` references are the right tool.

## String Interning

For a different scenario — many duplicate strings — interning is more effective than SSO:

```rust
use std::collections::HashSet;

struct StringInterner {
    pool: HashSet<Box<str>>,
}

impl StringInterner {
    fn new() -> Self {
        Self { pool: HashSet::new() }
    }

    fn intern(&mut self, s: &str) -> &str {
        if let Some(existing) = self.pool.get(s) {
            return existing;
        }
        let boxed: Box<str> = s.into();
        let ptr = &*boxed as *const str;
        self.pool.insert(boxed);
        // Safe because the Box lives as long as the interner
        unsafe { &*ptr }
    }
}
```

If you have 1 million strings but only 500 unique values, interning reduces memory usage to ~500 allocations instead of 1 million. The `lasso` crate provides a production-quality interner:

```rust
use lasso::Rodeo;

let mut rodeo = Rodeo::default();
let key1 = rodeo.get_or_intern("hello");
let key2 = rodeo.get_or_intern("hello");
assert_eq!(key1, key2); // same key, no duplicate allocation
```

## Avoiding Unnecessary String Operations

Sometimes the best string optimization is not using strings at all.

### Use &str Instead of String

```rust
// BAD: takes ownership, forces caller to clone
fn process(name: String) { /* ... */ }

// GOOD: borrows, no allocation needed
fn process(name: &str) { /* ... */ }
```

### Use Cow<str> for Conditional Modification

```rust
use std::borrow::Cow;

fn escape_html(input: &str) -> Cow<'_, str> {
    if input.contains('&') || input.contains('<') || input.contains('>') {
        Cow::Owned(
            input
                .replace('&', "&amp;")
                .replace('<', "&lt;")
                .replace('>', "&gt;")
        )
    } else {
        Cow::Borrowed(input)
    }
}
```

If 95% of strings don't contain HTML special characters, you save 95% of allocations.

### Use write! Instead of format!

`format!` always allocates a new String. If you're building a string incrementally, write into a buffer:

```rust
use std::fmt::Write;

// BAD: allocates a new String each time
fn build_csv_bad(records: &[(u32, &str)]) -> String {
    let mut result = String::new();
    for (id, name) in records {
        result.push_str(&format!("{},{}\n", id, name)); // allocation!
    }
    result
}

// GOOD: write directly into the buffer
fn build_csv_good(records: &[(u32, &str)]) -> String {
    let mut result = String::with_capacity(records.len() * 20);
    for (id, name) in records {
        write!(&mut result, "{},{}\n", id, name).unwrap(); // no allocation
    }
    result
}
```

### Avoid to_string() in Hot Paths

```rust
// Each call allocates
let s: String = some_number.to_string(); // allocates

// If you just need to compare or format, avoid the allocation
use std::fmt::Write;
let mut buf = String::new();
write!(&mut buf, "{}", some_number).unwrap();
```

Or use `itoa` / `ryu` for fast integer/float to string conversion without going through the `fmt` machinery:

```rust
// ~3x faster than to_string() for integers
let mut buf = itoa::Buffer::new();
let s = buf.format(12345u64); // returns &str, no allocation
```

## Benchmark: String Operations at Scale

```rust
use compact_str::CompactString;
use criterion::{black_box, criterion_group, criterion_main, Criterion};
use std::collections::HashMap;

fn bench_string_map(c: &mut Criterion) {
    let keys: Vec<String> = (0..10_000)
        .map(|i| format!("key_{}", i))
        .collect();

    c.bench_function("HashMap<String, u64>", |b| {
        b.iter(|| {
            let mut map = HashMap::with_capacity(10_000);
            for (i, key) in keys.iter().enumerate() {
                map.insert(key.clone(), i as u64);
            }
            black_box(&map);
        })
    });

    c.bench_function("HashMap<CompactString, u64>", |b| {
        b.iter(|| {
            let mut map = HashMap::with_capacity(10_000);
            for (i, key) in keys.iter().enumerate() {
                map.insert(CompactString::from(key.as_str()), i as u64);
            }
            black_box(&map);
        })
    });
}

criterion_group!(benches, bench_string_map);
criterion_main!(benches);

// Typical results:
// HashMap<String, u64>:        ~850 µs
// HashMap<CompactString, u64>: ~520 µs
```

38% faster for 10K entries — entirely from avoiding heap allocations for short keys.

## The Takeaway

String performance is a real concern when you're processing thousands or millions of strings. The tools exist to handle it:

- **CompactStr/SmartString** for inline small strings (no heap allocation under 24 bytes)
- **String interning** for many duplicate strings
- **&str/Cow<str>** to avoid allocations entirely when possible
- **write! over format!** for building strings incrementally
- **itoa/ryu** for fast numeric formatting

But measure first. If your profiler doesn't show string allocation as a hot spot, don't bother. The standard `String` is perfectly fine for most code. Reach for these tools when the profiler tells you strings are the problem.
