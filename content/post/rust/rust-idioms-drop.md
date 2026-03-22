---
title: "Lesson 18: Drop and RAII — Deterministic cleanup"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 18
keywords: [Rust, rust, idiomatic, Drop, RAII, resource management, cleanup, destructor]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-05-08T11:30:00.000Z'
---

In Go, you write `defer f.Close()` and hope you didn't forget one. In Python, you use `with` blocks and hope the context manager is implemented correctly. In Java, you use try-with-resources and hope `AutoCloseable.close()` doesn't throw. In C, you write `free()` and pray.

In Rust, cleanup happens automatically when a value goes out of scope. Always. No exceptions. No forgetting. That's RAII — Resource Acquisition Is Initialization — and the `Drop` trait is how Rust implements it.

---

## How Drop Works

When a value goes out of scope, Rust calls its `Drop::drop` method (if implemented), then deallocates the memory. This happens deterministically — you know exactly when cleanup occurs.

```rust
struct DatabaseConnection {
    url: String,
}

impl DatabaseConnection {
    fn new(url: &str) -> Self {
        println!("  CONNECTING to {}", url);
        DatabaseConnection { url: url.to_string() }
    }

    fn query(&self, sql: &str) {
        println!("  QUERY on {}: {}", self.url, sql);
    }
}

impl Drop for DatabaseConnection {
    fn drop(&mut self) {
        println!("  DISCONNECTING from {}", self.url);
    }
}

fn main() {
    println!("Start");
    {
        let conn = DatabaseConnection::new("postgres://localhost/mydb");
        conn.query("SELECT * FROM users");
        println!("About to leave scope...");
    } // conn.drop() called here — automatically
    println!("End");
}
// Output:
// Start
//   CONNECTING to postgres://localhost/mydb
//   QUERY on postgres://localhost/mydb: SELECT * FROM users
//   About to leave scope...
//   DISCONNECTING from postgres://localhost/mydb
// End
```

No `defer`. No `try/finally`. No `with`. The cleanup is attached to the value's lifetime and happens automatically.

---

## Drop Order

Values are dropped in reverse order of declaration. Struct fields are dropped in declaration order. This matters when resources depend on each other.

```rust
struct Resource {
    name: String,
}

impl Resource {
    fn new(name: &str) -> Self {
        println!("  Creating {}", name);
        Resource { name: name.to_string() }
    }
}

impl Drop for Resource {
    fn drop(&mut self) {
        println!("  Dropping {}", self.name);
    }
}

fn main() {
    println!("--- Variable drop order (reverse) ---");
    let a = Resource::new("A");
    let b = Resource::new("B");
    let c = Resource::new("C");
    println!("  All created");
    drop(c); // explicit early drop
    println!("  After dropping C");
    // a and b dropped here, in reverse order: B then A
}
// Creating A, Creating B, Creating C
// All created
// Dropping C
// After dropping C
// Dropping B
// Dropping A
```

---

## RAII: Resources Are Values

The RAII pattern ties resource management to object lifetime. Acquire the resource in the constructor, release it in `Drop`. The result: resources can't leak (barring `mem::forget`, which is safe but unusual).

### File locking

```rust
use std::sync::Mutex;

struct Config {
    data: Mutex<String>,
}

impl Config {
    fn new() -> Self {
        Config {
            data: Mutex::new(String::from("initial")),
        }
    }

    fn update(&self, new_value: &str) {
        let mut guard = self.data.lock().unwrap();
        // MutexGuard implements Drop — unlocks when it goes out of scope
        *guard = new_value.to_string();
        println!("Updated to: {}", *guard);
        // guard dropped here — mutex automatically unlocked
    }

    fn read(&self) -> String {
        let guard = self.data.lock().unwrap();
        guard.clone()
        // guard dropped here — mutex unlocked
    }
}

fn main() {
    let config = Config::new();
    config.update("production");
    println!("Current: {}", config.read());
}
```

The `MutexGuard` is the RAII pattern in action. You can't forget to unlock the mutex — it happens automatically when the guard is dropped. You can't use the data without locking — the guard *is* your access handle.

### Temporary files

```rust
struct TempFile {
    path: String,
}

impl TempFile {
    fn new(prefix: &str) -> Self {
        let path = format!("/tmp/{}_{}", prefix, std::process::id());
        std::fs::write(&path, "").unwrap_or(());
        println!("Created temp file: {}", path);
        TempFile { path }
    }

    fn path(&self) -> &str {
        &self.path
    }

    fn write(&self, content: &str) {
        std::fs::write(&self.path, content).unwrap_or(());
    }
}

impl Drop for TempFile {
    fn drop(&mut self) {
        println!("Cleaning up temp file: {}", self.path);
        let _ = std::fs::remove_file(&self.path);
    }
}

fn process_data() {
    let tmp = TempFile::new("data");
    tmp.write("some intermediate data");
    // ... do work with tmp.path() ...
    println!("Processing with: {}", tmp.path());
} // tmp is dropped, file is deleted

fn main() {
    process_data();
    println!("Temp file is already gone");
}
```

---

## `std::mem::drop` — Explicit Early Drop

Sometimes you need to release a resource before the end of the scope:

```rust
use std::sync::Mutex;

fn transfer(from: &Mutex<i64>, to: &Mutex<i64>, amount: i64) {
    {
        let mut from_guard = from.lock().unwrap();
        *from_guard -= amount;
        // from_guard dropped here — lock released before acquiring `to`
    }
    {
        let mut to_guard = to.lock().unwrap();
        *to_guard += amount;
        // to_guard dropped here
    }
}

fn main() {
    let account_a = Mutex::new(1000i64);
    let account_b = Mutex::new(500i64);
    transfer(&account_a, &account_b, 200);
    println!("A: {}, B: {}", account_a.lock().unwrap(), account_b.lock().unwrap());
}
```

Or use `drop()` explicitly:

```rust
fn example() {
    let big_data = vec![0u8; 100_000_000]; // 100MB
    // ... use big_data ...
    println!("Using big data, len: {}", big_data.len());

    drop(big_data); // free the 100MB NOW, don't wait for end of function

    // ... do more work that doesn't need big_data ...
    println!("Big data freed, continuing...");
}

fn main() {
    example();
}
```

`drop()` is just a function that takes ownership and does nothing — the value is dropped because it goes out of scope at the end of `drop()`. Elegant.

---

## You Can't Call `drop` Directly

A common misconception: you can't call `value.drop()` directly. Rust prevents it because it would lead to double-free — the value would be dropped by your explicit call AND by the automatic drop at end of scope.

```rust
struct Foo;

impl Drop for Foo {
    fn drop(&mut self) {
        println!("Dropped!");
    }
}

fn main() {
    let f = Foo;
    // f.drop(); // ERROR: explicit use of destructor method
    drop(f);    // OK: this takes ownership, value is consumed
    // f is moved into drop(), so there's no double-free
}
```

---

## Drop and Ownership: A Powerful Combination

RAII in C++ has a subtle problem: copies. If you copy a RAII object, which copy is responsible for cleanup? C++ uses the Rule of Three/Five to handle this, but it's error-prone.

Rust doesn't have this problem. Values have a single owner. When the owner goes out of scope, the value is dropped. If you move the value, the original owner is invalidated — no double-free possible.

```rust
struct Connection {
    id: u32,
}

impl Drop for Connection {
    fn drop(&mut self) {
        println!("Closing connection {}", self.id);
    }
}

fn use_connection(conn: Connection) {
    println!("Using connection {}", conn.id);
    // conn is dropped here — not in the caller
}

fn main() {
    let c = Connection { id: 1 };
    use_connection(c);
    // c has been moved — it won't be dropped again here
    // No double-free!
}
```

---

## Drop Guards for Cleanup on Panic

`Drop` runs even during stack unwinding (panics), making it perfect for cleanup that must happen regardless of success or failure:

```rust
struct Timer {
    name: String,
    start: std::time::Instant,
}

impl Timer {
    fn new(name: &str) -> Self {
        Timer {
            name: name.to_string(),
            start: std::time::Instant::now(),
        }
    }
}

impl Drop for Timer {
    fn drop(&mut self) {
        let elapsed = self.start.elapsed();
        println!("{}: {:.2?}", self.name, elapsed);
    }
}

fn expensive_operation() {
    let _timer = Timer::new("expensive_operation");
    // ... do work ...
    std::thread::sleep(std::time::Duration::from_millis(100));
    // Timer prints elapsed time when dropped, even if we panic
}

fn main() {
    expensive_operation();
}
```

This timer pattern is incredibly useful for profiling. The timing measurement always happens, even if the function panics partway through.

---

## When NOT to Implement Drop

Most types don't need a custom `Drop` implementation. Rust automatically drops all fields — `String` frees its buffer, `Vec` frees its elements and buffer, `Box` frees its heap allocation. You only need `Drop` when:

- You own a raw resource (file descriptor, network socket, FFI pointer)
- You need side effects on cleanup (logging, metrics, notifications)
- You're implementing a smart pointer or resource wrapper

If your struct only contains other Rust types, the automatically-generated drop behavior is correct. Don't implement `Drop` unless you have a specific reason.

---

## Key Takeaways

- `Drop` runs automatically when a value goes out of scope — deterministic, guaranteed cleanup.
- RAII ties resource management to value lifetime: acquire in constructor, release in `Drop`.
- Drop order: variables drop in reverse declaration order. Struct fields drop in declaration order.
- Use `drop(value)` for explicit early cleanup. You can't call `.drop()` directly.
- `Drop` runs during panics too — making it reliable for cleanup guards.
- Most types don't need custom `Drop` — only implement it for raw resources or side-effect cleanup.
- Ownership + Drop = no double-free, no resource leaks, no forgotten cleanup.
