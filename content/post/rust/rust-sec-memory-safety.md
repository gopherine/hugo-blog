---
title: "Lesson 1: Memory Safety — What Rust gives you for free"
author: Atharva Pandey
series: "Rust Security in Production"
lesson: 1
keywords: [Rust, rust, security, memory safety, buffer overflow, use-after-free, data races]
tags: [Rust tutorial, rust, security]
date: '2025-05-01T09:34:00.000Z'
---

Last year I inherited a C++ service that had been "battle-tested" in production for three years. Within a week of digging through crash dumps, I found two use-after-free bugs, a buffer overread that leaked heap data into API responses, and a data race in the connection pool that only triggered under load. Three years. Battle-tested. Right.

That experience is what finally pushed me from "Rust is interesting" to "Rust is non-negotiable for anything touching the network." The memory safety guarantees aren't just a nice-to-have — they're the single biggest security win you get by choosing Rust.

## The Classes of Bugs That Simply Don't Exist

Let me be blunt: something like 70% of security vulnerabilities in large C and C++ codebases are memory safety issues. Microsoft has published data on this. Google has published data on this. The numbers are consistent across decades. Buffer overflows, use-after-free, double-free, uninitialized memory reads, data races — these are the bread and butter of exploitation.

In safe Rust, every single one of these is a compile-time error. Not a runtime check. Not a sanitizer flag you hope someone remembers to turn on in CI. A hard compiler error that blocks your build.

Let's look at what that actually means in practice.

### Buffer Overflows — Gone

In C, this is how you get a CVE:

```c
void process_input(char *buf, size_t len) {
    char local[256];
    memcpy(local, buf, len);  // len > 256? congrats, you have a vuln
}
```

In Rust, bounds checking is automatic and enforced:

```rust
fn process_input(buf: &[u8]) {
    let mut local = [0u8; 256];

    // This panics at runtime if buf.len() > 256
    // But more importantly, the compiler nudges you toward safe patterns:
    let copy_len = buf.len().min(local.len());
    local[..copy_len].copy_from_slice(&buf[..copy_len]);

    // Or even better — just use a Vec and let it grow:
    let local: Vec<u8> = buf.to_vec();
    process_bytes(&local);
}

fn process_bytes(data: &[u8]) {
    // work with the slice — can't go out of bounds
    for &byte in data {
        // ...
    }
}
```

The key insight: slices in Rust carry their length. You can't "forget" to pass the size. You can't accidentally read past the end. The type system makes the safe path the easy path.

### Use-After-Free — Gone

This is the big one. Use-after-free is behind some of the nastiest exploits in history — browser zero-days, kernel privilege escalations, remote code execution chains.

In C++:

```cpp
std::string* make_greeting(const std::string& name) {
    std::string greeting = "Hello, " + name;
    return &greeting;  // dangling pointer to stack memory
}
```

In Rust, the borrow checker catches this at compile time:

```rust
// This doesn't compile. Period.
fn make_greeting(name: &str) -> &str {
    let greeting = format!("Hello, {}", name);
    &greeting  // ERROR: cannot return reference to local variable
}

// The correct version — return an owned value:
fn make_greeting(name: &str) -> String {
    format!("Hello, {}", name)
}
```

But it goes deeper than just returning dangling pointers. Consider this pattern that shows up constantly in event-driven systems:

```rust
use std::collections::HashMap;

struct ConnectionPool {
    connections: HashMap<u64, Connection>,
}

struct Connection {
    id: u64,
    buffer: Vec<u8>,
}

impl ConnectionPool {
    fn process_and_remove(&mut self, id: u64) {
        // You can't hold a reference to the connection
        // and simultaneously modify the HashMap.
        // The borrow checker prevents the entire class of
        // iterator-invalidation bugs.

        if let Some(conn) = self.connections.remove(&id) {
            // conn is now owned — we can use it freely
            println!("Processing connection {}: {} bytes", conn.id, conn.buffer.len());
            // conn is dropped here — no dangling reference possible
        }
    }
}
```

### Data Races — Gone

This one's particularly interesting because data races aren't just a correctness issue — they're a security issue. A data race on a pointer or length field can create a window for exploitation that's invisible in testing.

Rust's ownership model prevents data races at compile time through the rule: you can have either one mutable reference OR any number of immutable references, but never both simultaneously.

```rust
use std::sync::{Arc, Mutex};
use std::thread;

struct SharedState {
    auth_token: String,
    is_authenticated: bool,
}

fn main() {
    let state = Arc::new(Mutex::new(SharedState {
        auth_token: String::new(),
        is_authenticated: false,
    }));

    let handles: Vec<_> = (0..4)
        .map(|i| {
            let state = Arc::clone(&state);
            thread::spawn(move || {
                // You MUST lock the mutex to access the data.
                // The compiler enforces this — not your discipline.
                let mut guard = state.lock().unwrap();
                guard.auth_token = format!("token-{}", i);
                guard.is_authenticated = true;
                // Lock is released when guard goes out of scope
            })
        })
        .collect();

    for handle in handles {
        handle.join().unwrap();
    }
}
```

You literally cannot have a data race in safe Rust. Not "it's discouraged." Not "the linter warns you." It does not compile.

## The `unsafe` Escape Hatch — And Why It's Actually Good

Here's where people get confused. They hear "Rust has `unsafe`" and think the safety guarantees are meaningless. That's wrong — and I'll tell you why `unsafe` is actually *better* than having no safety system at all.

```rust
/// # Safety
/// `ptr` must point to a valid, aligned `u32` that lives for at least as long
/// as the returned reference.
unsafe fn read_sensor_register(ptr: *const u32) -> u32 {
    // This is an unsafe operation — dereferencing a raw pointer
    *ptr
}

fn process_sensor_data(registers: &[u32]) -> u32 {
    // This is completely safe — the slice carries bounds information
    registers.iter().sum()
}
```

The critical insight: `unsafe` blocks are *grep-able*. When something goes wrong, you know exactly where to look. In a C codebase, *everything* is effectively unsafe — there's no way to narrow your audit scope. In Rust, you can audit the 2% of code that's in `unsafe` blocks and have high confidence the other 98% is memory-safe.

Here's how I structure `unsafe` in production code:

```rust
mod ffi_bindings {
    //! All FFI calls to the sensor library live here.
    //! This module is the unsafe boundary — nothing outside
    //! this module should use raw pointers.

    extern "C" {
        fn sensor_init(config: *const u8, len: usize) -> i32;
        fn sensor_read(buffer: *mut u8, capacity: usize) -> i32;
        fn sensor_shutdown() -> i32;
    }

    pub struct Sensor {
        initialized: bool,
    }

    impl Sensor {
        pub fn new(config: &[u8]) -> Result<Self, SensorError> {
            let result = unsafe { sensor_init(config.as_ptr(), config.len()) };
            if result == 0 {
                Ok(Sensor { initialized: true })
            } else {
                Err(SensorError::InitFailed(result))
            }
        }

        pub fn read(&self) -> Result<Vec<u8>, SensorError> {
            let mut buffer = vec![0u8; 4096];
            let bytes_read =
                unsafe { sensor_read(buffer.as_mut_ptr(), buffer.capacity()) };
            if bytes_read < 0 {
                return Err(SensorError::ReadFailed(bytes_read));
            }
            buffer.truncate(bytes_read as usize);
            Ok(buffer)
        }
    }

    impl Drop for Sensor {
        fn drop(&mut self) {
            if self.initialized {
                unsafe { sensor_shutdown(); }
            }
        }
    }

    #[derive(Debug)]
    pub enum SensorError {
        InitFailed(i32),
        ReadFailed(i32),
    }
}

// Everything outside this module is safe:
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let config = b"baudrate=115200";
    let sensor = ffi_bindings::Sensor::new(config)
        .map_err(|e| format!("sensor init failed: {:?}", e))?;

    let data = sensor.read()
        .map_err(|e| format!("sensor read failed: {:?}", e))?;

    println!("Read {} bytes from sensor", data.len());
    Ok(())
}
```

The pattern is: create a safe wrapper around `unsafe` internals. Expose only safe interfaces. Keep the `unsafe` surface area as small as possible.

## What Rust Doesn't Protect You From

Let me be honest — Rust's memory safety doesn't cover everything. Here's what can still go wrong:

### Logic Bugs

```rust
fn check_admin(user: &User) -> bool {
    // This compiles fine. It's also completely wrong.
    user.role == Role::User  // should be Role::Admin
}
```

The type system catches type errors, not logic errors. You can still write code that does the wrong thing — it'll just do the wrong thing without corrupting memory.

### Denial of Service

```rust
fn parse_request(data: &[u8]) -> Vec<u8> {
    // An attacker sends a request claiming it needs a 16GB allocation.
    // Rust won't buffer-overflow, but it'll happily OOM your process.
    let size = u32::from_le_bytes(data[0..4].try_into().unwrap()) as usize;
    vec![0u8; size]  // boom — OOM
}

// The fix: validate before allocating
fn parse_request_safe(data: &[u8]) -> Result<Vec<u8>, &'static str> {
    if data.len() < 4 {
        return Err("too short");
    }
    let size = u32::from_le_bytes(data[0..4].try_into().unwrap()) as usize;
    if size > 1_048_576 {  // 1MB max
        return Err("requested allocation too large");
    }
    Ok(vec![0u8; size])
}
```

### Side Channels and Timing Attacks

```rust
// This is safe from memory corruption but vulnerable to timing attacks:
fn constant_time_compare_wrong(a: &[u8], b: &[u8]) -> bool {
    a == b  // short-circuits on first mismatch — timing leak
}

// Use a constant-time comparison instead:
fn constant_time_compare(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (x, y) in a.iter().zip(b.iter()) {
        diff |= x ^ y;
    }
    diff == 0
}
```

### Unsafe Code Bugs

If you write `unsafe`, you own the invariants. The compiler can't help you inside those blocks. This is why minimizing `unsafe` surface area matters so much.

## Practical Takeaways for Production

Here's what I actually do on my teams:

**1. Ban `unsafe` outside of designated modules.** Use clippy lints:

```toml
# In Cargo.toml or .cargo/config.toml
[lints.rust]
unsafe_code = "deny"
```

Then allow it only where needed:

```rust
#[allow(unsafe_code)]
mod ffi_layer {
    // Carefully reviewed unsafe code lives here
}
```

**2. Run Miri in CI for any crate that uses `unsafe`:**

```bash
cargo +nightly miri test
```

Miri catches undefined behavior that the compiler can't. It's slow, but it's worth running on your `unsafe`-containing crates.

**3. Treat every external input as hostile.** Rust prevents memory corruption, but you still need to validate sizes, ranges, and formats before you do anything with user data.

**4. Use `#[deny(clippy::all)]` and `#[deny(clippy::pedantic)]`** in CI. Clippy catches dozens of patterns that are technically safe but practically dangerous — integer overflow, lossy conversions, unnecessary `unsafe`.

```toml
# clippy.toml
avoid-breaking-exported-api = false

# In Cargo.toml
[lints.clippy]
all = "deny"
pedantic = "deny"
```

## The Bottom Line

Rust doesn't make your code secure. It makes an enormous class of vulnerabilities — the class responsible for the majority of real-world exploits — impossible in safe code. That's not everything, but it's a massive head start.

The remaining security work — input validation, cryptography, dependency management, access control — still needs your attention. And that's exactly what the rest of this series is about.

Start with the foundation Rust gives you for free. Build the rest deliberately.
