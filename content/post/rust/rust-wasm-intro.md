---
title: "Lesson 1: Rust to WebAssembly — Why and how"
author: Atharva Pandey
series: "Rust WebAssembly"
lesson: 1
keywords: [Rust, rust, WebAssembly, WASM, wasm-pack, wasm-bindgen, cargo, web performance]
tags: [Rust tutorial, rust, webassembly, wasm]
date: '2025-07-01T08:45:22.000Z'
---

I was optimizing a client-side image processing pipeline last year — think heavy convolutions, histogram equalization, color space conversions. The JavaScript implementation was doing about 12 frames per second. I rewrote the core loops in Rust, compiled to WebAssembly, and hit 55 fps. Same browser. Same machine. That's the moment WebAssembly stopped being a curiosity and became a tool I actually reach for.

But let me be real: getting there wasn't a straight line. The tooling has rough edges, the mental model is different from writing server-side Rust, and half the blog posts out there show you how to add two numbers in WASM and call it a tutorial. That's not what we're doing here.

## What WebAssembly Actually Is

WebAssembly is a binary instruction format. Not a programming language. Not a framework. It's a compilation target — a virtual ISA (instruction set architecture) that runs in a sandboxed environment. Browsers implement it, but so do server-side runtimes like Wasmtime and Wasmer.

Here's what makes it interesting for Rust developers specifically:

1. **No garbage collector** — WASM doesn't have one, and neither does Rust. This is a perfect match. Languages with GCs (Go, Java) have to ship their entire runtime into the WASM binary, bloating it significantly.
2. **Linear memory model** — WASM operates on a flat chunk of memory. Rust's ownership model maps naturally to this.
3. **Predictable performance** — No JIT warmup, no deoptimization. Your code runs at near-native speed from the first call.

Languages like C and C++ can also compile to WASM, but Rust's toolchain support is significantly better. The `wasm32-unknown-unknown` target is a first-class citizen in the Rust ecosystem.

## Setting Up Your Environment

You need three things: Rust (obviously), the WASM target, and `wasm-pack`.

```bash
# Add the WebAssembly target
rustup target add wasm32-unknown-unknown

# Install wasm-pack — the build tool that ties everything together
cargo install wasm-pack

# Optional but useful: a simple HTTP server for testing
cargo install miniserve
```

`wasm-pack` is the glue between Cargo and the JavaScript ecosystem. It compiles your Rust to WASM, generates JavaScript bindings, produces a `package.json`, and can even publish to npm. You *can* do all of this manually with `cargo build --target wasm32-unknown-unknown`, but you'll end up reimplementing half of what `wasm-pack` does.

## Your First WASM Crate

Let's build something that actually does work — a Markdown parser. Not a toy "hello world" function.

```bash
cargo new --lib wasm-markdown
cd wasm-markdown
```

Your `Cargo.toml`:

```toml
[package]
name = "wasm-markdown"
version = "0.1.0"
edition = "2021"

[lib]
crate-type = ["cdylib", "rlib"]

[dependencies]
wasm-bindgen = "0.2"
pulldown-cmark = "0.10"

[profile.release]
opt-level = "s"      # Optimize for size
lto = true           # Link-time optimization
strip = true         # Strip debug info
```

Two things to note. The `crate-type = ["cdylib"]` tells Cargo to produce a dynamic library — that's what WASM needs. And `opt-level = "s"` optimizes for binary size instead of raw speed. In WASM, smaller binaries mean faster load times, and the size optimization rarely costs you meaningful performance.

Now the actual Rust code in `src/lib.rs`:

```rust
use wasm_bindgen::prelude::*;
use pulldown_cmark::{Parser, Options, html};

#[wasm_bindgen]
pub fn markdown_to_html(input: &str) -> String {
    let mut options = Options::empty();
    options.insert(Options::ENABLE_STRIKETHROUGH);
    options.insert(Options::ENABLE_TABLES);
    options.insert(Options::ENABLE_FOOTNOTES);

    let parser = Parser::new_ext(input, options);
    let mut html_output = String::new();
    html::push_html(&mut html_output, parser);
    html_output
}

#[wasm_bindgen]
pub fn word_count(input: &str) -> usize {
    input.split_whitespace().count()
}

#[wasm_bindgen]
pub fn reading_time_minutes(input: &str) -> f64 {
    let words = input.split_whitespace().count();
    (words as f64) / 238.0 // Average adult reading speed
}
```

The `#[wasm_bindgen]` attribute is doing heavy lifting here. It generates the JavaScript glue code that lets you call these Rust functions from JS. We'll dig into exactly how that works in Lesson 2.

## Building and Running

```bash
wasm-pack build --target web
```

This produces a `pkg/` directory containing:
- `wasm_markdown_bg.wasm` — the actual WASM binary
- `wasm_markdown.js` — JavaScript glue code
- `wasm_markdown.d.ts` — TypeScript type definitions
- `package.json` — so you can npm install it

Now create an `index.html` in your project root:

```html
<!DOCTYPE html>
<html>
<head>
    <title>WASM Markdown Parser</title>
    <style>
        body { font-family: system-ui; max-width: 800px; margin: 0 auto; padding: 2rem; }
        textarea { width: 100%; height: 200px; font-family: monospace; }
        #output { border: 1px solid #ccc; padding: 1rem; margin-top: 1rem; }
        #stats { color: #666; margin-top: 0.5rem; }
    </style>
</head>
<body>
    <h1>Rust-Powered Markdown</h1>
    <textarea id="input" placeholder="Type markdown here...">
# Hello from Rust!

This markdown is being parsed by **Rust** compiled to **WebAssembly**.

- No JavaScript parser involved
- Running at near-native speed
- In your browser right now
    </textarea>
    <div id="stats"></div>
    <div id="output"></div>

    <script type="module">
        import init, { markdown_to_html, word_count, reading_time_minutes }
            from './pkg/wasm_markdown.js';

        async function run() {
            await init();

            const input = document.getElementById('input');
            const output = document.getElementById('output');
            const stats = document.getElementById('stats');

            function update() {
                const md = input.value;
                const start = performance.now();
                const rendered = markdown_to_html(md);
                const elapsed = performance.now() - start;

                // Using DOMParser for safe HTML rendering
                output.textContent = '';
                const doc = new DOMParser().parseFromString(rendered, 'text/html');
                output.append(...doc.body.childNodes);

                const words = word_count(md);
                const minutes = reading_time_minutes(md);
                stats.textContent = `${words} words · ${minutes.toFixed(1)} min read · parsed in ${elapsed.toFixed(2)}ms`;
            }

            input.addEventListener('input', update);
            update();
        }

        run();
    </script>
</body>
</html>
```

Serve it:

```bash
miniserve . --index index.html
```

Open `http://localhost:8080` and start typing. You've got a real-time Markdown editor powered entirely by Rust — running in the browser.

## Understanding the Compilation Pipeline

Here's what actually happens when you run `wasm-pack build`:

```
Rust Source (.rs)
    ↓  rustc with wasm32-unknown-unknown target
WASM Binary (.wasm)
    ↓  wasm-bindgen CLI
WASM Binary + JS Glue + TypeScript Defs
    ↓  wasm-opt (optional, from binaryen)
Optimized WASM Binary
```

The `wasm32-unknown-unknown` target is key. The triple breaks down as:
- `wasm32` — 32-bit WebAssembly architecture (WASM uses 32-bit pointers by default)
- `unknown` — no specific operating system
- `unknown` — no specific environment/ABI

This is different from `wasm32-wasi`, which we'll cover in Lesson 7. The `unknown-unknown` target is what you use for browser environments.

## What You Can and Can't Do

This is the part most tutorials skip, and it matters.

**What works great:**
- Pure computation — math, parsing, encoding/decoding, compression
- Anything CPU-bound that doesn't need OS interaction
- String manipulation (with some caveats about encoding)
- Data structure operations

**What doesn't work (in `wasm32-unknown-unknown`):**
- File system access — there is no file system
- Network calls — no sockets, no HTTP client
- Threading — not by default (we'll fix this in Lesson 6)
- `println!` — no stdout. Use `web_sys::console::log_1` or `wasm_bindgen`'s `console_log!`

**What works but requires `web-sys` or `js-sys`:**
- DOM manipulation
- `fetch()` calls
- `setTimeout` / `setInterval`
- Canvas and WebGL

This is why the Rust WASM ecosystem has those two crates. `js-sys` gives you bindings to JavaScript builtins (Array, Object, Promise, etc.). `web-sys` gives you bindings to Web APIs (Document, Element, Window, etc.). They're both auto-generated from WebIDL definitions, which means they're comprehensive but sometimes awkward to use.

## Binary Size — The Elephant in the Room

Your first WASM binary is going to be bigger than you expect. That markdown parser? Probably 200-300KB after optimization. Here's how to get it down:

```toml
# Cargo.toml
[profile.release]
opt-level = "z"      # Aggressive size optimization
lto = true
codegen-units = 1    # Slower compile, better optimization
panic = "abort"      # Don't include unwinding code
strip = true
```

You can also run `wasm-opt` separately for even more savings:

```bash
# Install binaryen first (brew install binaryen on macOS)
wasm-opt -Oz -o output.wasm input.wasm
```

Some real-world numbers from my projects:
- Simple math utilities: 15-30KB
- Markdown parser (pulldown-cmark): 180-250KB
- Image processing library: 400-600KB
- Full app with Yew framework: 1-3MB

For comparison, React is ~40KB gzipped, but then you add your application code, a state management library, a router... you're easily at 200-400KB. A Rust WASM binary that replaces all of that isn't necessarily bigger.

## When to Use WASM (and When Not To)

I've seen teams try to rewrite entire frontends in Rust/WASM. Most of the time, that's the wrong call. Here's my framework:

**Use WASM when:**
- You have a computationally intensive task (image/audio/video processing, cryptography, physics simulation)
- You need consistent, predictable performance without GC pauses
- You have existing Rust/C/C++ code you want to run in the browser
- You're building a game or interactive visualization

**Don't use WASM when:**
- You're building a typical CRUD app — JavaScript/TypeScript is just fine
- Your bottleneck is network latency, not computation
- Your team doesn't know Rust — the learning curve is real
- You need heavy DOM interaction (the JS↔WASM boundary has overhead)

The JS↔WASM boundary cost is the thing people underestimate. Every time you pass data between JavaScript and WebAssembly, there's serialization overhead. Pass a string? It gets copied. Pass a complex object? It gets serialized. Call a WASM function a million times in a tight loop? Those boundary crossings add up.

The trick is to do large chunks of work on the WASM side and minimize the number of crossings. Process an entire image, not individual pixels. Parse an entire document, not individual lines.

## What's Coming in This Course

Over the next seven lessons, we're going deep:

- **Lesson 2:** `wasm-bindgen` internals — how Rust and JS actually communicate
- **Lesson 3:** DOM manipulation from Rust — building interactive UIs without JS
- **Lesson 4:** Full-stack Rust frameworks — Leptos, Yew, and Dioxus compared
- **Lesson 5:** Performance — when WASM beats JavaScript and when it doesn't
- **Lesson 6:** Multi-threaded WASM with SharedArrayBuffer
- **Lesson 7:** WASI — taking WebAssembly beyond the browser
- **Lesson 8:** The Component Model — the future of composable WASM

Each lesson builds on the previous one. By the end, you'll understand not just how to compile Rust to WASM, but when it's the right choice and how to get the most out of it.

Let's get into the details. Lesson 2 starts with the magic behind `#[wasm_bindgen]`.
