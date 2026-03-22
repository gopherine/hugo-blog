---
title: "Lesson 6: FFI — Calling C from Rust"
author: Atharva Pandey
series: "Rust Unsafe & FFI"
lesson: 6
keywords: [Rust, rust, unsafe, FFI, foreign function interface, C interop, bindgen, libc]
tags: [Rust tutorial, rust, unsafe, ffi]
date: '2025-06-23T11:55:00.000Z'
---

My first real FFI project was binding to SQLite. I thought "how hard can it be — it's just calling C functions." Three days later I was debugging a segfault caused by a string lifetime issue where Rust freed a `CString` while SQLite was still reading from the pointer. That experience taught me more about unsafe Rust than any tutorial ever could.

Calling C from Rust is the most common FFI scenario. Every operating system API is C. Most high-performance libraries — OpenSSL, zlib, SQLite, libcurl — are C. If you're writing systems software in Rust, you'll need this skill.

## The Basics: extern "C" and #[link]

At the most fundamental level, calling C from Rust requires three things: declaring the foreign function, linking to the library, and calling it in an `unsafe` block.

```rust
// Declare the C functions you want to call
extern "C" {
    fn abs(x: i32) -> i32;
    fn strlen(s: *const std::ffi::c_char) -> usize;
    fn printf(format: *const std::ffi::c_char, ...) -> i32;
}

fn main() {
    // Every call to a foreign function is unsafe
    unsafe {
        let result = abs(-42);
        println!("abs(-42) = {}", result);  // 42
    }
}
```

Why is every FFI call `unsafe`? Because Rust can't verify anything about the C code:
- It might dereference null pointers
- It might have buffer overflows
- It might modify memory through supposedly const pointers
- It might not be thread-safe
- The function signature you declared might not match the actual C function

You're trusting that your declaration matches reality. If it doesn't — wrong argument types, wrong calling convention, wrong return type — you get undefined behavior.

## Type Mapping: Rust ↔ C

Getting the types right is everything. Here's the mapping:

| C Type | Rust Type | Notes |
|--------|-----------|-------|
| `int` | `c_int` (`i32`) | Use `std::ffi::c_int` for portability |
| `unsigned int` | `c_uint` (`u32`) | |
| `long` | `c_long` | Platform-dependent size! |
| `size_t` | `usize` | |
| `char` | `c_char` | Might be signed or unsigned |
| `char *` | `*mut c_char` | |
| `const char *` | `*const c_char` | |
| `void *` | `*mut c_void` | |
| `bool` / `_Bool` | `bool` | |
| `float` | `f32` | |
| `double` | `f64` | |
| `int32_t` | `i32` | Fixed-size, always safe |
| `uint8_t` | `u8` | Fixed-size, always safe |

A critical gotcha: `c_long` is 32 bits on 64-bit Windows but 64 bits on 64-bit Linux/macOS. If you use `i64` instead of `c_long`, your code will break on Windows. Always use the `std::ffi` types.

```rust
use std::ffi::{c_int, c_long, c_char, c_void, CString, CStr};
```

## Strings: The Hard Part

Strings are where most FFI bugs live. C strings are null-terminated byte arrays. Rust strings are UTF-8 byte slices with a length. These are fundamentally different representations.

```rust
use std::ffi::{CString, CStr, c_char};

fn string_ffi_patterns() {
    // Rust → C: Use CString
    // CString adds a null terminator and ensures no interior null bytes
    let rust_string = "Hello, C world!";
    let c_string = CString::new(rust_string).expect("no null bytes");
    let c_ptr: *const c_char = c_string.as_ptr();

    // CRITICAL: c_ptr is only valid as long as c_string is alive!
    // This is the bug I hit with SQLite.

    extern "C" {
        fn puts(s: *const c_char) -> c_int;
    }

    unsafe {
        puts(c_ptr); // Safe: c_string is still alive
    }
    // c_string is dropped here — c_ptr is now dangling

    // C → Rust: Use CStr
    extern "C" {
        fn getenv(name: *const c_char) -> *const c_char;
    }

    let var_name = CString::new("HOME").unwrap();
    unsafe {
        let result = getenv(var_name.as_ptr());
        if !result.is_null() {
            // CStr::from_ptr borrows the C string — doesn't copy
            let c_str = CStr::from_ptr(result);
            // Convert to Rust &str (might fail if not valid UTF-8)
            match c_str.to_str() {
                Ok(s) => println!("HOME = {}", s),
                Err(_) => println!("HOME contains invalid UTF-8"),
            }
        }
    }
}
```

The single most common FFI bug I see:

```rust
// THIS IS A BUG
fn get_c_string() -> *const c_char {
    let s = CString::new("hello").unwrap();
    s.as_ptr()
    // s is dropped here — the returned pointer is dangling!
}

// FIX: Keep the CString alive
fn use_c_string_correctly() {
    let s = CString::new("hello").unwrap();
    let ptr = s.as_ptr();
    unsafe {
        // Use ptr here, while s is still alive
        puts(ptr);
    }
    // s is dropped after we're done with ptr
}
```

## Structs: repr(C) Is Non-Negotiable

Rust's default struct layout is unspecified — the compiler can reorder fields, add padding wherever it wants. For FFI, you must use `#[repr(C)]` to get C-compatible layout:

```rust
// This C struct:
// struct Point {
//     double x;
//     double y;
//     int color;
// };

#[repr(C)]
#[derive(Debug, Clone, Copy)]
struct Point {
    x: f64,
    y: f64,
    color: i32,
    // Note: C will add 4 bytes of padding here on most platforms
    // to align the struct size to 8 bytes. Rust's repr(C) does the same.
}

extern "C" {
    fn distance(a: *const Point, b: *const Point) -> f64;
    fn create_point(x: f64, y: f64, color: i32) -> Point;
}

fn struct_ffi() {
    let a = Point { x: 0.0, y: 0.0, color: 1 };
    let b = Point { x: 3.0, y: 4.0, color: 2 };

    unsafe {
        let d = distance(&a, &b);
        println!("Distance: {}", d);  // 5.0

        let p = create_point(1.0, 2.0, 3);
        println!("Point: {:?}", p);
    }
}
```

Without `#[repr(C)]`, the field layout is random from C's perspective. Your program will read garbage values from wrong offsets. This won't crash immediately — it'll just produce wrong results silently. Extremely fun to debug.

## Linking: build.rs and pkg-config

Real C libraries need to be linked. Rust's build system handles this through `build.rs`:

```rust
// build.rs
fn main() {
    // Link to a system library
    println!("cargo:rustc-link-lib=z");  // libz (zlib)

    // Or a static library
    println!("cargo:rustc-link-lib=static=mylib");

    // Search path for libraries
    println!("cargo:rustc-link-search=native=/usr/local/lib");

    // Or use pkg-config for proper cross-platform support
    pkg_config::Config::new()
        .atleast_version("1.2.11")
        .probe("zlib")
        .expect("zlib not found");
}
```

```toml
# Cargo.toml
[build-dependencies]
pkg-config = "0.3"
```

For a complete zlib binding example:

```rust
// src/lib.rs
use std::ffi::c_int;

// zlib constants
const Z_OK: c_int = 0;
const Z_BEST_COMPRESSION: c_int = 9;

extern "C" {
    fn compress2(
        dest: *mut u8,
        dest_len: *mut std::ffi::c_ulong,
        source: *const u8,
        source_len: std::ffi::c_ulong,
        level: c_int,
    ) -> c_int;

    fn uncompress(
        dest: *mut u8,
        dest_len: *mut std::ffi::c_ulong,
        source: *const u8,
        source_len: std::ffi::c_ulong,
    ) -> c_int;
}

/// Compress data using zlib.
pub fn zlib_compress(input: &[u8]) -> Result<Vec<u8>, String> {
    // zlib recommends dest buffer = source_len * 1.001 + 12
    let mut dest_len = (input.len() as f64 * 1.1 + 12.0) as std::ffi::c_ulong;
    let mut dest = vec![0u8; dest_len as usize];

    // SAFETY: dest is a valid, writable buffer of dest_len bytes.
    // input is a valid, readable buffer. compress2 writes at most
    // dest_len bytes and updates dest_len with actual size.
    let result = unsafe {
        compress2(
            dest.as_mut_ptr(),
            &mut dest_len,
            input.as_ptr(),
            input.len() as std::ffi::c_ulong,
            Z_BEST_COMPRESSION,
        )
    };

    if result != Z_OK {
        return Err(format!("zlib compress failed with code {}", result));
    }

    dest.truncate(dest_len as usize);
    Ok(dest)
}

/// Decompress zlib-compressed data.
pub fn zlib_decompress(input: &[u8], expected_size: usize) -> Result<Vec<u8>, String> {
    let mut dest_len = expected_size as std::ffi::c_ulong;
    let mut dest = vec![0u8; expected_size];

    // SAFETY: Same argument as compress — valid buffers,
    // proper lengths, uncompress won't write past dest_len.
    let result = unsafe {
        uncompress(
            dest.as_mut_ptr(),
            &mut dest_len,
            input.as_ptr(),
            input.len() as std::ffi::c_ulong,
        )
    };

    if result != Z_OK {
        return Err(format!("zlib decompress failed with code {}", result));
    }

    dest.truncate(dest_len as usize);
    Ok(dest)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip() {
        let original = b"Hello, zlib from Rust! ".repeat(100);
        let compressed = zlib_compress(&original).unwrap();
        let decompressed = zlib_decompress(&compressed, original.len()).unwrap();
        assert_eq!(original.as_slice(), decompressed.as_slice());
        println!(
            "Compressed {} bytes to {} bytes",
            original.len(),
            compressed.len()
        );
    }
}
```

## bindgen: Auto-generating Bindings

Writing `extern "C"` blocks by hand is tedious and error-prone. `bindgen` reads C header files and generates Rust bindings automatically:

```toml
# Cargo.toml
[build-dependencies]
bindgen = "0.70"
```

```rust
// build.rs
fn main() {
    println!("cargo:rustc-link-lib=mylib");

    let bindings = bindgen::Builder::default()
        .header("wrapper.h")
        // Only generate bindings for these functions
        .allowlist_function("mylib_.*")
        // And these types
        .allowlist_type("mylib_.*")
        // Use core instead of std for no_std compatibility
        .use_core()
        // Generate Debug impls for structs
        .derive_debug(true)
        .derive_default(true)
        .generate()
        .expect("Unable to generate bindings");

    let out_dir = std::env::var("OUT_DIR").unwrap();
    bindings
        .write_to_file(std::path::Path::new(&out_dir).join("bindings.rs"))
        .expect("Couldn't write bindings");
}
```

```c
// wrapper.h
#include "mylib/core.h"
#include "mylib/utils.h"
```

```rust
// src/lib.rs
#![allow(non_upper_case_globals)]
#![allow(non_camel_case_types)]
#![allow(non_snake_case)]

include!(concat!(env!("OUT_DIR"), "/bindings.rs"));
```

The `include!` macro pastes the generated code inline. The `allow` attributes suppress warnings about C-style naming conventions.

I use bindgen for any library with more than a handful of functions. For small APIs (3-5 functions), writing the `extern` block by hand is often cleaner.

## Callbacks: C Calling Back into Rust

Many C APIs use callbacks — you pass a function pointer, and C calls it. This requires `extern "C"` functions on the Rust side:

```rust
use std::ffi::c_void;

// C API we're binding to:
// typedef void (*callback_fn)(int value, void* user_data);
// void process_array(int* data, int len, callback_fn cb, void* user_data);

type CallbackFn = extern "C" fn(value: i32, user_data: *mut c_void);

extern "C" {
    fn process_array(
        data: *const i32,
        len: i32,
        cb: CallbackFn,
        user_data: *mut c_void,
    );
}

// The callback function — must be extern "C"
extern "C" fn my_callback(value: i32, user_data: *mut c_void) {
    // SAFETY: We know user_data is a valid *mut Vec<i32>
    // because we passed it ourselves in call_with_callback.
    let results = unsafe { &mut *(user_data as *mut Vec<i32>) };
    results.push(value * 2);
}

fn call_with_callback() {
    let data = [1, 2, 3, 4, 5];
    let mut results: Vec<i32> = Vec::new();

    // SAFETY: data is a valid i32 array, len matches.
    // user_data points to results, which lives until after process_array returns.
    // my_callback correctly interprets user_data as *mut Vec<i32>.
    unsafe {
        process_array(
            data.as_ptr(),
            data.len() as i32,
            my_callback,
            &mut results as *mut Vec<i32> as *mut c_void,
        );
    }

    println!("Results: {:?}", results);
}
```

The `void*` user data pattern is C's version of closures. You pass a pointer to your state, and the callback casts it back. It works, but you've got to be meticulous about lifetimes — if `results` were dropped before the callback fires, you'd be writing to freed memory.

## Error Handling: Bridging Two Worlds

C reports errors through return codes, errno, or output parameters. Rust uses `Result`. You need to bridge them:

```rust
use std::io;

extern "C" {
    fn open(path: *const std::ffi::c_char, flags: i32) -> i32;
    fn close(fd: i32) -> i32;
    fn read(fd: i32, buf: *mut c_void, count: usize) -> isize;
}

const O_RDONLY: i32 = 0;

fn safe_open(path: &str) -> io::Result<i32> {
    let c_path = std::ffi::CString::new(path)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "path contains null byte"))?;

    // SAFETY: c_path is a valid null-terminated string,
    // and O_RDONLY is a valid flag.
    let fd = unsafe { open(c_path.as_ptr(), O_RDONLY) };

    if fd < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(fd)
    }
}

fn safe_close(fd: i32) -> io::Result<()> {
    // SAFETY: fd is assumed to be a valid file descriptor.
    let result = unsafe { close(fd) };
    if result < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

// Even better: wrap in a RAII type
struct FileDescriptor(i32);

impl FileDescriptor {
    fn open(path: &str) -> io::Result<Self> {
        safe_open(path).map(FileDescriptor)
    }

    fn read(&self, buf: &mut [u8]) -> io::Result<usize> {
        // SAFETY: self.0 is a valid fd (from open()),
        // buf is a valid writable buffer.
        let n = unsafe {
            read(self.0, buf.as_mut_ptr() as *mut c_void, buf.len())
        };
        if n < 0 {
            Err(io::Error::last_os_error())
        } else {
            Ok(n as usize)
        }
    }
}

impl Drop for FileDescriptor {
    fn drop(&mut self) {
        let _ = safe_close(self.0);
    }
}
```

This is the encapsulation pattern from lesson 5, applied to FFI. The raw C API is `unsafe` and error-prone. The Rust wrapper is safe, returns `Result`, and uses RAII to prevent resource leaks.

## The Checklist

Before shipping any FFI binding:

1. Every `extern "C"` declaration uses the correct types (check the C headers)
2. Every string conversion handles null bytes and encoding
3. Every pointer's lifetime is explicitly managed
4. Every C error code maps to a Rust error type
5. Resources are cleaned up via `Drop`
6. The public API contains zero `unsafe` — all unsafety is encapsulated
7. Tests run under Miri where possible (some FFI can't run under Miri since it calls actual C code — test the safe wrapper logic separately)

Next lesson, we flip it around: exposing Rust code to C callers.
