---
title: "Lesson 2: wasm-bindgen — Bridging Rust and JavaScript"
author: Atharva Pandey
series: "Rust WebAssembly"
lesson: 2
keywords: [Rust, rust, WebAssembly, WASM, wasm-bindgen, JsValue, closures, JavaScript interop]
tags: [Rust tutorial, rust, webassembly, wasm]
date: '2025-07-03T14:12:05.000Z'
---

The first time I looked at what `#[wasm_bindgen]` actually generates, I was equal parts impressed and horrified. Impressed because it seamlessly bridges two fundamentally different type systems. Horrified because the generated code is a labyrinth of pointer arithmetic, descriptor tables, and heap management. But here's the thing — you don't need to understand every line of generated code. You do need to understand the *model*, because when things go wrong (and they will), the model is what helps you debug.

## The Fundamental Problem

Rust and JavaScript don't agree on anything at the type level.

JavaScript has dynamic types: numbers are all `f64`, strings are UTF-16, objects are hashmaps, arrays can hold anything. Rust has a static type system with strict memory layout guarantees. WebAssembly itself only understands four numeric types: `i32`, `i64`, `f32`, `f64`. That's it. No strings, no objects, no arrays.

So how does `wasm-bindgen` let you pass a Rust `String` to JavaScript? It doesn't — not directly. It's way more clever than that.

## How Data Actually Crosses the Boundary

When you write this:

```rust
use wasm_bindgen::prelude::*;

#[wasm_bindgen]
pub fn greet(name: &str) -> String {
    format!("Hello, {}!", name)
}
```

Here's what `wasm-bindgen` generates (conceptually):

1. The Rust function gets rewritten to accept a pointer and length (both `i32`) instead of `&str`
2. The JavaScript glue code encodes the JS string into UTF-8, writes it into WASM linear memory, and passes the pointer + length
3. The return `String` gets written into WASM memory, and the pointer + length get passed back to JS
4. The JS glue code reads the bytes from WASM memory and decodes them back to a JS string
5. The memory gets freed

Every string crossing involves an allocation, a copy, and an encoding conversion (UTF-16 ↔ UTF-8). This is why I keep saying the boundary has real cost.

## The Type Mapping

Let me lay out exactly which types can cross the boundary and how:

### Zero-cost types (passed directly as WASM primitives)

```rust
#[wasm_bindgen]
pub fn add(a: i32, b: i32) -> i32 { a + b }

#[wasm_bindgen]
pub fn multiply(a: f64, b: f64) -> f64 { a * b }

#[wasm_bindgen]
pub fn is_valid(x: bool) -> bool { !x }
```

These are free. `i32`, `u32`, `f32`, `f64` map directly to WASM types. `bool` maps to `i32`. No copies, no allocations.

### Copied types (serialized across the boundary)

```rust
#[wasm_bindgen]
pub fn reverse_string(s: &str) -> String {
    s.chars().rev().collect()
}

#[wasm_bindgen]
pub fn process_bytes(data: &[u8]) -> Vec<u8> {
    data.iter().map(|b| b.wrapping_add(1)).collect()
}
```

Strings and byte slices get copied. `&str` comes in as a borrowed view of JS-allocated data in WASM memory. `String` and `Vec<u8>` going out involve writing to memory and handing the pointer to JS.

### JsValue — The Escape Hatch

When you need to work with arbitrary JavaScript values:

```rust
use wasm_bindgen::prelude::*;

#[wasm_bindgen]
pub fn log_value(val: &JsValue) {
    web_sys::console::log_1(val);
}

#[wasm_bindgen]
pub fn create_object() -> JsValue {
    let obj = js_sys::Object::new();
    js_sys::Reflect::set(
        &obj,
        &JsValue::from_str("name"),
        &JsValue::from_str("Rust"),
    ).unwrap();
    obj.into()
}
```

`JsValue` is a handle — an index into a table of JavaScript objects maintained by the glue code. The actual JS object never enters WASM memory. Instead, WASM holds an integer index, and the JS side uses that index to look up the real object. This is efficient for opaque handles but means you can't inspect the object from the Rust side without calling back into JS.

## Structs and Classes

This is where it gets interesting. You can expose Rust structs as JavaScript classes:

```rust
use wasm_bindgen::prelude::*;

#[wasm_bindgen]
pub struct ImageProcessor {
    width: u32,
    height: u32,
    pixels: Vec<u8>,
}

#[wasm_bindgen]
impl ImageProcessor {
    #[wasm_bindgen(constructor)]
    pub fn new(width: u32, height: u32) -> ImageProcessor {
        let pixels = vec![0u8; (width * height * 4) as usize];
        ImageProcessor { width, height, pixels }
    }

    pub fn width(&self) -> u32 {
        self.width
    }

    pub fn height(&self) -> u32 {
        self.height
    }

    pub fn set_pixel(&mut self, x: u32, y: u32, r: u8, g: u8, b: u8, a: u8) {
        let idx = ((y * self.width + x) * 4) as usize;
        if idx + 3 < self.pixels.len() {
            self.pixels[idx] = r;
            self.pixels[idx + 1] = g;
            self.pixels[idx + 2] = b;
            self.pixels[idx + 3] = a;
        }
    }

    pub fn invert(&mut self) {
        for chunk in self.pixels.chunks_exact_mut(4) {
            chunk[0] = 255 - chunk[0]; // R
            chunk[1] = 255 - chunk[1]; // G
            chunk[2] = 255 - chunk[2]; // B
            // Alpha stays the same
        }
    }

    pub fn grayscale(&mut self) {
        for chunk in self.pixels.chunks_exact_mut(4) {
            let gray = (0.299 * chunk[0] as f64
                + 0.587 * chunk[1] as f64
                + 0.114 * chunk[2] as f64) as u8;
            chunk[0] = gray;
            chunk[1] = gray;
            chunk[2] = gray;
        }
    }

    /// Returns a pointer to the pixel buffer for direct JS access
    pub fn pixels_ptr(&self) -> *const u8 {
        self.pixels.as_ptr()
    }

    pub fn pixels_len(&self) -> usize {
        self.pixels.len()
    }
}
```

On the JavaScript side, this becomes:

```javascript
import init, { ImageProcessor } from './pkg/image_processor.js';

async function run() {
    const wasm = await init();

    const proc = new ImageProcessor(800, 600);
    proc.set_pixel(0, 0, 255, 0, 0, 255);
    proc.invert();

    // Direct memory access for bulk operations
    const ptr = proc.pixels_ptr();
    const len = proc.pixels_len();
    const pixels = new Uint8Array(wasm.memory.buffer, ptr, len);

    // `pixels` is now a live view into WASM memory
    // You can pass it directly to canvas putImageData

    proc.free(); // IMPORTANT: manual cleanup
}
```

There's a critical detail here: **you must call `.free()` on Rust structs from JavaScript.** There's no garbage collector linking the two worlds. When the JS wrapper object gets GC'd, the Rust memory doesn't automatically get freed. This is a memory leak waiting to happen. In practice, `FinalizationRegistry` can help, but don't rely on it — be explicit.

## Importing JavaScript Functions

`wasm-bindgen` works in both directions. You can call JS from Rust:

```rust
use wasm_bindgen::prelude::*;

#[wasm_bindgen]
extern "C" {
    // Import console.log
    #[wasm_bindgen(js_namespace = console)]
    fn log(s: &str);

    // Import a custom JS function
    #[wasm_bindgen(js_name = "fetch")]
    fn js_fetch(url: &str) -> js_sys::Promise;

    // Import a JS class
    type HTMLElement;

    #[wasm_bindgen(method, getter)]
    fn id(this: &HTMLElement) -> String;

    #[wasm_bindgen(method, setter)]
    fn set_id(this: &HTMLElement, val: &str);
}

#[wasm_bindgen]
pub fn do_something() {
    log("Called from Rust!");
}
```

The `extern "C"` block with `#[wasm_bindgen]` generates import declarations in the WASM module. At instantiation time, the JS glue code provides these functions.

## Closures — Here Be Dragons

Passing closures between Rust and JavaScript is where `wasm-bindgen` earns its paycheck — and where most people hit confusing errors.

```rust
use wasm_bindgen::prelude::*;

#[wasm_bindgen]
pub fn set_timeout_from_rust(ms: i32) {
    // A closure that doesn't capture anything — straightforward
    let closure = Closure::wrap(Box::new(|| {
        web_sys::console::log_1(&"Timer fired!".into());
    }) as Box<dyn Fn()>);

    web_sys::window()
        .unwrap()
        .set_timeout_with_callback_and_timeout_and_arguments_0(
            closure.as_ref().unchecked_ref(),
            ms,
        )
        .unwrap();

    // This is the tricky part
    closure.forget(); // Leak the closure so it stays alive
}
```

Why `.forget()`? Because Rust's ownership rules say the `Closure` will be dropped at the end of the function. But JavaScript's `setTimeout` needs the callback to survive until the timer fires. `.forget()` intentionally leaks the memory, which is fine for one-shot callbacks but terrible for repeated ones.

For long-lived closures, use `Closure::once` or manage the lifetime explicitly:

```rust
use wasm_bindgen::prelude::*;
use std::cell::RefCell;
use std::rc::Rc;

#[wasm_bindgen]
pub struct AnimationLoop {
    closure: Option<Closure<dyn FnMut()>>,
    counter: Rc<RefCell<u32>>,
}

#[wasm_bindgen]
impl AnimationLoop {
    #[wasm_bindgen(constructor)]
    pub fn new() -> AnimationLoop {
        AnimationLoop {
            closure: None,
            counter: Rc::new(RefCell::new(0)),
        }
    }

    pub fn start(&mut self) {
        let counter = self.counter.clone();

        let closure = Closure::wrap(Box::new(move || {
            let mut count = counter.borrow_mut();
            *count += 1;
            web_sys::console::log_1(
                &format!("Frame {}", *count).into()
            );
        }) as Box<dyn FnMut()>);

        // Store the closure so it doesn't get dropped
        self.closure = Some(closure);
    }

    pub fn stop(&mut self) {
        // Dropping the closure cleans up the callback
        self.closure = None;
    }

    pub fn frame_count(&self) -> u32 {
        *self.counter.borrow()
    }
}
```

The pattern here — storing closures in the struct that manages their lifetime — is the standard approach. It's verbose, but it's correct. You'll see this pattern everywhere in Rust WASM code.

## serde-wasm-bindgen: Structured Data

For passing complex data structures, `serde-wasm-bindgen` is significantly better than manual `JsValue` manipulation:

```rust
use serde::{Deserialize, Serialize};
use wasm_bindgen::prelude::*;

#[derive(Serialize, Deserialize)]
pub struct Config {
    pub theme: String,
    pub font_size: u32,
    pub features: Vec<String>,
    pub debug: bool,
}

#[derive(Serialize, Deserialize)]
pub struct ProcessingResult {
    pub success: bool,
    pub items_processed: usize,
    pub errors: Vec<String>,
    pub duration_ms: f64,
}

#[wasm_bindgen]
pub fn process_with_config(config_val: JsValue) -> Result<JsValue, JsValue> {
    // Deserialize from JS object to Rust struct
    let config: Config = serde_wasm_bindgen::from_value(config_val)
        .map_err(|e| JsValue::from_str(&format!("Invalid config: {}", e)))?;

    let start = js_sys::Date::now();

    // Do actual work...
    let result = ProcessingResult {
        success: true,
        items_processed: 42,
        errors: vec![],
        duration_ms: js_sys::Date::now() - start,
    };

    // Serialize back to JS object
    serde_wasm_bindgen::to_value(&result)
        .map_err(|e| JsValue::from_str(&format!("Serialization error: {}", e)))
}
```

On the JavaScript side:

```javascript
const result = process_with_config({
    theme: "dark",
    font_size: 14,
    features: ["syntax-highlighting", "line-numbers"],
    debug: false
});
console.log(result.items_processed); // 42
```

This is way better than the old approach of using `JsValue::from_serde()` (which was deprecated). `serde-wasm-bindgen` converts directly between Rust types and JS objects without going through JSON as an intermediate format.

## Error Handling Across the Boundary

Rust's `Result` maps to JavaScript exceptions:

```rust
use wasm_bindgen::prelude::*;

#[wasm_bindgen]
pub fn parse_json(input: &str) -> Result<JsValue, JsValue> {
    let parsed: serde_json::Value = serde_json::from_str(input)
        .map_err(|e| JsValue::from_str(&format!("Parse error: {}", e)))?;

    serde_wasm_bindgen::to_value(&parsed)
        .map_err(|e| JsValue::from_str(&format!("Conversion error: {}", e)))
}
```

When this returns `Err(JsValue)`, the JavaScript side gets an exception. You can catch it with try/catch:

```javascript
try {
    const result = parse_json("{ invalid json }");
} catch (e) {
    console.error(e); // "Parse error: expected value at line 1 column 3"
}
```

A word of caution: don't `panic!` in WASM code intended for the browser. A panic will abort the entire WASM instance. Use `console_error_panic_hook` to get useful error messages instead of cryptic "unreachable" errors:

```rust
use wasm_bindgen::prelude::*;

#[wasm_bindgen(start)]
pub fn init() {
    console_error_panic_hook::set_once();
}
```

Add to `Cargo.toml`:

```toml
[dependencies]
console_error_panic_hook = "0.1"
```

This single line has saved me hours of debugging. Without it, a panic in WASM just says "RuntimeError: unreachable executed" — completely useless. With the hook, you get the actual panic message and a stack trace.

## Performance Tips for the Boundary

After a year of shipping Rust WASM in production, here are the patterns that matter:

**1. Batch operations instead of individual calls:**

```rust
// BAD: one JS→WASM call per item
#[wasm_bindgen]
pub fn process_one(item: &str) -> String { /* ... */ }

// GOOD: one call for all items
#[wasm_bindgen]
pub fn process_batch(items: &str) -> String {
    // Accept newline-delimited input, return newline-delimited output
    items
        .lines()
        .map(|line| process_single(line))
        .collect::<Vec<_>>()
        .join("\n")
}
```

**2. Use typed arrays for bulk data:**

```rust
#[wasm_bindgen]
pub fn process_floats(data: &[f64]) -> Vec<f64> {
    data.iter().map(|x| x.sin() * x.cos()).collect()
}
```

`&[f64]` maps to `Float64Array` in JS. The data gets passed as a view into WASM memory — no element-by-element copying.

**3. Return pointers for large data:**

Instead of copying large buffers back to JS, return a pointer and let JS create a view into WASM memory. We did this with `pixels_ptr()` in the `ImageProcessor` example. It's zero-copy on the return path.

## Wrapping Up

`wasm-bindgen` is the foundation everything else builds on. `web-sys`, `js-sys`, Yew, Leptos — they all use `wasm-bindgen` under the hood. Understanding how data crosses the boundary, how closures work, and where the performance costs hide will save you from a class of bugs that are really hard to diagnose if you're treating `wasm-bindgen` as magic.

Next up, we're using these primitives to manipulate the DOM directly from Rust. No JavaScript event handlers, no JavaScript DOM queries — just Rust talking to the browser.
