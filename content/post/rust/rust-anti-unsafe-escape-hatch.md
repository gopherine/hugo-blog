---
title: "Lesson 10: Using unsafe to Escape the Borrow Checker — The wrong reason"
author: Atharva Pandey
series: "Rust Anti-Patterns & Code Smells"
lesson: 10
keywords: [Rust, rust, anti-patterns, code quality, unsafe, borrow checker, undefined behavior, soundness]
tags: [Rust tutorial, rust, anti-patterns, code-quality]
date: '2025-04-24T09:30:00.000Z'
---

I found this in a production codebase:

```rust
fn get_or_insert(&mut self, key: &str) -> &mut Value {
    if !self.map.contains_key(key) {
        self.map.insert(key.to_string(), Value::default());
    }
    // "The borrow checker is being stupid, we know the key exists"
    unsafe { &mut *(self.map.get_mut(key).unwrap() as *mut Value) }
}
```

The comment says it all. The developer hit a borrow checker error — a legitimate one about borrowing `self.map` mutably twice — and instead of restructuring the code, they cast through a raw pointer to silence the compiler. This is undefined behavior. The compiler is allowed to assume the mutable references don't alias, and it *will* optimize based on that assumption. When it does, your program does something you didn't write.

This is the worst anti-pattern in Rust, because it doesn't just produce wrong results — it erases every safety guarantee that makes Rust worth using.

## The Smell

Unsafe-as-escape-hatch shows up in predictable patterns:

### Pattern 1: Bypassing the borrow checker for self-referential access

```rust
struct Document {
    content: String,
    // "I need a reference into content, so I'll use a raw pointer"
    title_start: *const u8,
    title_len: usize,
}

impl Document {
    fn new(content: String) -> Self {
        let title_start = content.as_ptr();
        let title_len = content.find('\n').unwrap_or(content.len());
        Document { content, title_start, title_len }
    }

    fn title(&self) -> &str {
        unsafe {
            let slice = std::slice::from_raw_parts(self.title_start, self.title_len);
            std::str::from_utf8_unchecked(slice)
        }
    }
}
```

This is UB. When `Document` is moved (which Rust does freely — moves are memcpy), `content` moves to a new address, but `title_start` still points to the old address. Dangling pointer. Use-after-free. The exact class of bug Rust was designed to prevent.

### Pattern 2: "I know this is safe" transmutes

```rust
fn str_to_static(s: &str) -> &'static str {
    unsafe { std::mem::transmute(s) }
}

// Used like:
fn process(input: &str) -> Result<&'static str> {
    let result = parse(input)?;
    // "The result outlives the function because it's stored in a global cache"
    Ok(str_to_static(result))
}
```

`transmute` to extend a lifetime is almost always wrong. You're telling the compiler "trust me, this lives forever" when it doesn't. The moment the original data is deallocated, every `&'static str` pointing into it becomes a dangling reference.

### Pattern 3: Interior mutability via raw pointers

```rust
struct Cache {
    data: HashMap<String, String>,
}

impl Cache {
    fn get_or_compute(&self, key: &str) -> &str {
        // Can't mutate through &self, so let's cheat
        let this = unsafe { &mut *(self as *const Cache as *mut Cache) };
        this.data.entry(key.to_string())
            .or_insert_with(|| expensive_compute(key))
    }
}
```

This bypasses `&self` to get `&mut self`. It's UB if anything else holds a reference to the `Cache`. It violates Rust's aliasing rules. And there's a perfectly safe solution — `RefCell` or `RwLock`, depending on whether you need thread safety.

### Pattern 4: Avoiding lifetimes with raw pointers

```rust
struct Parser {
    // "I don't want to deal with lifetime parameters"
    source: *const str,
}

impl Parser {
    fn new(source: &str) -> Self {
        Parser { source: source as *const str }
    }

    fn parse(&self) -> Result<Ast> {
        let source = unsafe { &*self.source };
        // ... parse the source ...
    }
}
```

The developer didn't want to write `Parser<'a>` and track the lifetime. So they used a raw pointer, which means the compiler can't verify that the source string outlives the parser. If it doesn't, you get a dangling pointer.

## Why It's Actually Bad

Let me be blunt about this: **using `unsafe` to bypass the borrow checker is undefined behavior, and undefined behavior means your program can do literally anything.**

Not "anything within reason." *Anything.* The compiler is allowed to assume that `unsafe` code upholds Rust's invariants. When it doesn't, the compiler's optimizations — which are based on those invariants — produce incorrect machine code. The result might be:

- Returning stale data from a cache
- Silently corrupting memory
- Working perfectly in debug mode and crashing in release mode
- Working on your machine and crashing on CI
- Working today and breaking with the next compiler update

The last one is especially important. Rustc and LLVM are constantly improving their optimizations. Code that happens to work today despite containing UB can break tomorrow with a compiler update, because the optimizer found a new way to exploit the invariant you violated. This isn't theoretical — it happens regularly.

**Miri catches this stuff.** Run `cargo +nightly miri test` and watch it flag every piece of UB in your test suite. I've seen teams discover dozens of lurking UB bugs this way.

## The Fix

For every common "escape hatch" pattern, there's a safe alternative.

### Fix for Pattern 1: Self-referential structs

Use indices instead of pointers:

```rust
struct Document {
    content: String,
    title_end: usize, // index, not pointer — survives moves
}

impl Document {
    fn new(content: String) -> Self {
        let title_end = content.find('\n').unwrap_or(content.len());
        Document { content, title_end }
    }

    fn title(&self) -> &str {
        &self.content[..self.title_end]
    }
}
```

Indices are just numbers. They don't dangle when the struct moves. This is the standard pattern in Rust for self-referential data — store offsets, not pointers.

For more complex cases, consider the `ouroboros` or `self_cell` crates, which provide safe self-referential struct abstractions.

### Fix for Pattern 2: Lifetime extension

Don't extend lifetimes — restructure ownership:

```rust
// If you need the result to outlive the input, clone it
fn process(input: &str) -> Result<String> {
    let result = parse(input)?;
    Ok(result.to_string()) // owned, no lifetime issues
}

// If you're caching, use owned values in the cache
struct Cache {
    entries: HashMap<String, String>, // owns both keys and values
}

impl Cache {
    fn get_or_insert(&mut self, key: &str, compute: impl FnOnce() -> String) -> &str {
        self.entries.entry(key.to_string())
            .or_insert_with(compute)
    }
}
```

If you genuinely need `&'static str`, use `Box::leak` — which *actually* creates a static reference by intentionally leaking memory. This is appropriate for things like interned strings that live for the program's duration:

```rust
fn intern(s: &str) -> &'static str {
    // This genuinely leaks memory — only use for long-lived values
    Box::leak(s.to_string().into_boxed_str())
}
```

### Fix for Pattern 3: Interior mutability

Use the tools Rust gives you:

```rust
use std::cell::RefCell;

struct Cache {
    data: RefCell<HashMap<String, String>>,
}

impl Cache {
    fn get_or_compute(&self, key: &str) -> String {
        let mut data = self.data.borrow_mut();
        data.entry(key.to_string())
            .or_insert_with(|| expensive_compute(key))
            .clone()
    }
}
```

For thread-safe interior mutability:

```rust
use std::sync::RwLock;

struct Cache {
    data: RwLock<HashMap<String, String>>,
}

impl Cache {
    fn get_or_compute(&self, key: &str) -> String {
        // Try read lock first
        if let Some(value) = self.data.read().unwrap().get(key) {
            return value.clone();
        }

        // Upgrade to write lock
        let mut data = self.data.write().unwrap();
        data.entry(key.to_string())
            .or_insert_with(|| expensive_compute(key))
            .clone()
    }
}
```

### Fix for Pattern 4: Structs with references

Just add the lifetime parameter:

```rust
struct Parser<'a> {
    source: &'a str,
}

impl<'a> Parser<'a> {
    fn new(source: &'a str) -> Self {
        Parser { source }
    }

    fn parse(&self) -> Result<Ast> {
        // ... parse self.source ...
    }
}
```

Yes, lifetime parameters add syntactic overhead. But that overhead is the compiler tracking, for you, whether the source string outlives the parser. That tracking is *the entire point of Rust*. If you don't want it, you don't want Rust — and you definitely shouldn't circumvent it with `unsafe`.

### Fix for the original get_or_insert

The HashMap entry API solves this cleanly:

```rust
fn get_or_insert(&mut self, key: &str) -> &mut Value {
    self.map.entry(key.to_string())
        .or_insert_with(Value::default)
}
```

One line. No unsafe. No raw pointers. The entry API exists specifically because this pattern is common and the naive approach (check then insert) runs into borrow checker issues. When the borrow checker complains, the first thing to check is whether there's an API designed for your exact pattern. Usually there is.

## When unsafe Is Legitimate

Real `unsafe` use cases exist, but they're narrow:

- **FFI.** Calling C libraries requires `unsafe`. There's no way around it.
- **Implementing low-level data structures.** `Vec`, `HashMap`, `LinkedList` — these need `unsafe` internally. But they expose safe APIs.
- **Performance-critical code after profiling.** Sometimes `get_unchecked` or manual SIMD is necessary. But only after benchmarks prove the bounds check matters.
- **Interacting with hardware.** Memory-mapped I/O, MMIO registers, DMA.

In all these cases, the `unsafe` block should be:
1. As small as possible
2. Encapsulated behind a safe API
3. Documented with a `// SAFETY:` comment explaining why the invariants are upheld
4. Tested with Miri

```rust
/// Returns the element at `index` without bounds checking.
///
/// # Safety
/// `index` must be less than `self.len()`.
unsafe fn get_unchecked(&self, index: usize) -> &T {
    // SAFETY: caller guarantees index < self.len()
    debug_assert!(index < self.len());
    &*self.ptr.add(index)
}
```

## The Bottom Line

Every time you write `unsafe` to bypass the borrow checker, you're saying: "I know better than the compiler, and I'm willing to bet the correctness of my program on it." That's a bet you'll lose eventually.

The borrow checker errors you're hitting aren't bugs in the compiler. They're the compiler telling you about a real problem with your data flow. The solution is never to silence the warning — it's to restructure the code so the problem doesn't exist.

Learn the entry API. Learn `RefCell` and `RwLock`. Learn `Cow`. Learn to use indices instead of self-referential pointers. Learn `Rc` and `Arc`. These are the tools Rust gives you to solve the problems that `unsafe` "solves" by sweeping them under the rug.

The entire value proposition of Rust is "if it compiles, it's memory safe." Every `unsafe` block you write is an asterisk on that promise. Make sure each one is justified, documented, and reviewed — not a lazy workaround for a borrow checker error you didn't feel like understanding.
