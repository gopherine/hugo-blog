---
title: "Lesson 7: Exposing Rust to C — cdylib and cbindgen"
author: Atharva Pandey
series: "Rust Unsafe & FFI"
lesson: 7
keywords: [Rust, rust, unsafe, FFI, cdylib, cbindgen, C ABI, expose rust, shared library]
tags: [Rust tutorial, rust, unsafe, ffi]
date: '2025-06-25T15:40:00.000Z'
---

A team I was advising had a massive C codebase — about 400,000 lines of networking code. They wanted to rewrite their TLS handling in Rust but couldn't justify a full rewrite. The solution: build Rust as a shared library, expose a C-compatible API, and link it into the existing build. Took a week to get the first version working. The memory safety bugs in that module dropped to zero.

This is one of Rust's killer features: you can drop it into any project that consumes C libraries. Write performance-critical or safety-critical components in Rust, expose them through the C ABI, and the rest of the codebase doesn't even know Rust is involved.

## The Setup: cdylib

To produce a C-compatible shared library from Rust, set the crate type in your `Cargo.toml`:

```toml
[package]
name = "mylib"
version = "0.1.0"
edition = "2021"

[lib]
crate-type = ["cdylib"]  # Produces .so (Linux), .dylib (macOS), .dll (Windows)
# You can also add "rlib" to still use it as a normal Rust dependency:
# crate-type = ["cdylib", "rlib"]
```

`cdylib` tells the Rust compiler to produce a dynamic library with C-compatible symbol naming. No name mangling, no Rust-specific metadata in the binary. From C's perspective, it's just another `.so` or `.dll`.

If you need a static library instead (a `.a` file), use `"staticlib"`:

```toml
[lib]
crate-type = ["staticlib"]  # Produces .a (Unix) or .lib (Windows)
```

## Exporting Functions

To make a Rust function callable from C, you need two things: `extern "C"` for the calling convention and `#[no_mangle]` to prevent symbol name mangling.

```rust
use std::ffi::{c_char, c_int, CStr, CString};
use std::ptr;

/// Add two integers. The simplest possible FFI export.
#[no_mangle]
pub extern "C" fn mylib_add(a: c_int, b: c_int) -> c_int {
    a + b
}

/// Process a C string and return a new one.
/// Caller must free the returned string with mylib_free_string.
#[no_mangle]
pub extern "C" fn mylib_uppercase(input: *const c_char) -> *mut c_char {
    if input.is_null() {
        return ptr::null_mut();
    }

    // SAFETY: Caller guarantees input is a valid null-terminated string.
    let c_str = unsafe { CStr::from_ptr(input) };

    let rust_str = match c_str.to_str() {
        Ok(s) => s,
        Err(_) => return ptr::null_mut(), // Invalid UTF-8
    };

    let uppercased = rust_str.to_uppercase();

    match CString::new(uppercased) {
        Ok(c_string) => c_string.into_raw(), // Transfer ownership to C
        Err(_) => ptr::null_mut(),
    }
}

/// Free a string that was allocated by this library.
/// Must be called for every non-null string returned by mylib_uppercase.
#[no_mangle]
pub extern "C" fn mylib_free_string(s: *mut c_char) {
    if s.is_null() {
        return;
    }
    // SAFETY: s was allocated by CString::into_raw in mylib_uppercase.
    // We're taking ownership back and dropping it.
    unsafe {
        let _ = CString::from_raw(s);
    }
}
```

Crucial rule: **memory must be freed by the allocator that created it.** If Rust allocates a string, Rust must free it. If C allocates memory, C must free it. Mixing allocators is undefined behavior — the Rust allocator and the C allocator manage different heaps.

That's why `mylib_free_string` exists. C code calls `mylib_uppercase` to get a string, uses it, then calls `mylib_free_string` to clean up. Never `free()`.

## Opaque Types: The Right Pattern

For non-trivial types, don't expose Rust struct layouts to C. Use opaque pointers instead — C sees a `void*` (or a pointer to a forward-declared struct), and all operations go through your API:

```rust
use std::ffi::{c_char, c_int, CStr};
use std::ptr;

/// A Rust-managed hash map exposed to C as an opaque handle.
pub struct KeyValueStore {
    map: std::collections::HashMap<String, String>,
}

/// Create a new store. Returns null on failure.
#[no_mangle]
pub extern "C" fn kvstore_new() -> *mut KeyValueStore {
    let store = Box::new(KeyValueStore {
        map: std::collections::HashMap::new(),
    });
    Box::into_raw(store)
}

/// Destroy a store. Must be called exactly once per kvstore_new.
#[no_mangle]
pub extern "C" fn kvstore_free(store: *mut KeyValueStore) {
    if store.is_null() {
        return;
    }
    // SAFETY: store was created by kvstore_new via Box::into_raw.
    // Caller guarantees no other references exist and this is
    // called exactly once.
    unsafe {
        let _ = Box::from_raw(store);
    }
}

/// Insert a key-value pair. Returns 0 on success, -1 on error.
#[no_mangle]
pub extern "C" fn kvstore_insert(
    store: *mut KeyValueStore,
    key: *const c_char,
    value: *const c_char,
) -> c_int {
    if store.is_null() || key.is_null() || value.is_null() {
        return -1;
    }

    // SAFETY: store came from kvstore_new and hasn't been freed.
    // key and value are valid null-terminated strings (caller guarantee).
    let store = unsafe { &mut *store };
    let key = unsafe { CStr::from_ptr(key) };
    let value = unsafe { CStr::from_ptr(value) };

    let key = match key.to_str() {
        Ok(s) => s.to_owned(),
        Err(_) => return -1,
    };
    let value = match value.to_str() {
        Ok(s) => s.to_owned(),
        Err(_) => return -1,
    };

    store.map.insert(key, value);
    0
}

/// Look up a key. Returns null if not found.
/// The returned pointer is valid until the next insert or remove
/// operation on the same store.
#[no_mangle]
pub extern "C" fn kvstore_get(
    store: *const KeyValueStore,
    key: *const c_char,
) -> *const c_char {
    if store.is_null() || key.is_null() {
        return ptr::null();
    }

    let store = unsafe { &*store };
    let key = unsafe { CStr::from_ptr(key) };

    let key = match key.to_str() {
        Ok(s) => s,
        Err(_) => return ptr::null(),
    };

    match store.map.get(key) {
        Some(value) => {
            // Return a pointer into the HashMap's storage.
            // This is valid as long as the entry isn't removed.
            value.as_ptr() as *const c_char
        }
        None => ptr::null(),
    }
}

/// Get the number of entries.
#[no_mangle]
pub extern "C" fn kvstore_len(store: *const KeyValueStore) -> usize {
    if store.is_null() {
        return 0;
    }
    let store = unsafe { &*store };
    store.map.len()
}
```

From C's perspective:

```c
// mylib.h (you'd write this by hand or generate with cbindgen)
typedef struct KeyValueStore KeyValueStore;

KeyValueStore* kvstore_new(void);
void kvstore_free(KeyValueStore* store);
int kvstore_insert(KeyValueStore* store, const char* key, const char* value);
const char* kvstore_get(const KeyValueStore* store, const char* key);
size_t kvstore_len(const KeyValueStore* store);

// Usage:
// KeyValueStore* store = kvstore_new();
// kvstore_insert(store, "name", "Atharva");
// printf("%s\n", kvstore_get(store, "name"));
// kvstore_free(store);
```

The C side sees a typedef'd struct pointer — completely opaque. It can't access the `HashMap` directly, can't corrupt the internal state, can't even see the struct definition. All the safety invariants are maintained by your Rust API.

## cbindgen: Generating the Header

Writing C headers by hand is tedious and error-prone. `cbindgen` reads your Rust code and generates a matching header file:

```toml
# Cargo.toml
[build-dependencies]
cbindgen = "0.27"
```

```rust
// build.rs
fn main() {
    let crate_dir = std::env::var("CARGO_MANIFEST_DIR").unwrap();

    cbindgen::Builder::new()
        .with_crate(&crate_dir)
        .with_language(cbindgen::Language::C)
        .with_include_guard("MYLIB_H")
        .with_documentation(true)
        .with_autogen_warning(
            "/* Warning: this file is auto-generated by cbindgen. Do not edit. */"
        )
        .generate()
        .expect("Unable to generate C header")
        .write_to_file("include/mylib.h");
}
```

Add a `cbindgen.toml` for configuration:

```toml
# cbindgen.toml
language = "C"
include_guard = "MYLIB_H"
autogen_warning = "/* Auto-generated by cbindgen. Do not modify. */"
include_version = true
braces = "SameLine"
tab_width = 4
documentation = true

[export]
prefix = "mylib_"

[export.rename]
"KeyValueStore" = "mylib_kvstore_t"
```

Now `cargo build` generates the header automatically. Every time your Rust API changes, the header stays in sync.

## Panic Safety: The Silent Killer

Here's something most tutorials don't mention: **if a Rust function panics across an FFI boundary, it's undefined behavior.** Always catch panics at the boundary.

```rust
use std::ffi::c_int;

/// Divide two integers. Returns -1 and sets *error to 1 on failure.
#[no_mangle]
pub extern "C" fn mylib_divide(a: c_int, b: c_int, error: *mut c_int) -> c_int {
    let result = std::panic::catch_unwind(|| {
        if b == 0 {
            panic!("division by zero"); // This panic must not escape to C
        }
        a / b
    });

    match result {
        Ok(value) => {
            if !error.is_null() {
                unsafe { *error = 0; }
            }
            value
        }
        Err(_) => {
            if !error.is_null() {
                unsafe { *error = 1; }
            }
            -1
        }
    }
}
```

A cleaner pattern is to use a macro for all your exported functions:

```rust
macro_rules! ffi_export {
    ($name:ident, $body:expr, $error_val:expr) => {
        match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| $body)) {
            Ok(val) => val,
            Err(_) => {
                eprintln!(concat!("Panic in FFI function: ", stringify!($name)));
                $error_val
            }
        }
    };
}

#[no_mangle]
pub extern "C" fn mylib_process(data: *const u8, len: usize) -> c_int {
    ffi_export!(mylib_process, {
        if data.is_null() {
            return -1;
        }
        let slice = unsafe { std::slice::from_raw_parts(data, len) };
        // ... process data ...
        slice.len() as c_int
    }, -1)
}
```

## Thread Safety

If C code will call your library from multiple threads, your exported types need to be thread-safe:

```rust
use std::sync::Mutex;

pub struct ThreadSafeStore {
    map: Mutex<std::collections::HashMap<String, String>>,
}

#[no_mangle]
pub extern "C" fn ts_store_new() -> *mut ThreadSafeStore {
    Box::into_raw(Box::new(ThreadSafeStore {
        map: Mutex::new(std::collections::HashMap::new()),
    }))
}

#[no_mangle]
pub extern "C" fn ts_store_insert(
    store: *mut ThreadSafeStore,
    key: *const c_char,
    value: *const c_char,
) -> c_int {
    if store.is_null() || key.is_null() || value.is_null() {
        return -1;
    }

    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let store = unsafe { &*store };
        let key = unsafe { CStr::from_ptr(key) }.to_str().ok()?;
        let value = unsafe { CStr::from_ptr(value) }.to_str().ok()?;

        let mut map = store.map.lock().ok()?;
        map.insert(key.to_owned(), value.to_owned());
        Some(0)
    }));

    match result {
        Ok(Some(code)) => code,
        _ => -1,
    }
}
```

Document your thread-safety guarantees in the header file. C developers need to know whether they can share your types across threads.

## Building and Using the Library

Here's the complete workflow from Rust source to C consumption:

```bash
# Build the shared library
cargo build --release

# On Linux, this produces:
# target/release/libmylib.so

# On macOS:
# target/release/libmylib.dylib

# On Windows:
# target/release/mylib.dll + mylib.dll.lib
```

Compiling the C consumer:

```bash
# Linux
gcc -o app main.c -L target/release -lmylib -Iinclude

# macOS
clang -o app main.c -L target/release -lmylib -Iinclude

# Run (Linux — set library path)
LD_LIBRARY_PATH=target/release ./app

# Run (macOS)
DYLD_LIBRARY_PATH=target/release ./app
```

A minimal C program using our library:

```c
#include <stdio.h>
#include "mylib.h"

int main(void) {
    // Create a store
    KeyValueStore* store = kvstore_new();
    if (!store) {
        fprintf(stderr, "Failed to create store\n");
        return 1;
    }

    // Insert some values
    kvstore_insert(store, "language", "Rust");
    kvstore_insert(store, "year", "2015");

    // Look up a value
    const char* lang = kvstore_get(store, "language");
    if (lang) {
        printf("Language: %s\n", lang);
    }

    printf("Store has %zu entries\n", kvstore_len(store));

    // Clean up
    kvstore_free(store);
    return 0;
}
```

## API Design Guidelines

After building several production FFI libraries, here are the patterns I always follow:

**Prefix everything.** C has no namespaces. Every exported symbol needs a unique prefix: `mylib_create`, `mylib_destroy`, `mylib_process`. Without this, you'll collide with some other library eventually.

**Constructor/destructor pairs.** Every `_new` or `_create` function gets a matching `_free` or `_destroy`. Document this relationship in the header.

**Null checks on every entry point.** C code passes null pointers. It just does. Check every pointer parameter and return an error code instead of crashing.

**Error codes, not exceptions.** Return `0` for success, negative numbers for errors. Optionally provide an error message function (`mylib_last_error`) for detailed diagnostics.

**Version function.** Export a `mylib_version()` function so C code can verify it's linked against the expected version.

```rust
#[no_mangle]
pub extern "C" fn mylib_version() -> *const c_char {
    // Static string — lives for the entire program
    concat!(env!("CARGO_PKG_VERSION"), "\0").as_ptr() as *const c_char
}
```

In the next lesson, we'll use these FFI fundamentals to build Python extensions with PyO3 — same principles, nicer ergonomics.
