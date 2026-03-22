---
title: "Lesson 7: Drop Order — Deterministic destruction and why it matters"
author: Atharva Pandey
series: "Rust Memory Model & Internals"
lesson: 7
keywords: [Rust, rust, memory, internals, drop order, Drop trait, RAII, destructor, resource management]
tags: [Rust tutorial, rust, internals]
date: '2025-03-13T07:20:00.000Z'
---

I once had a deadlock in a Rust program that only manifested during shutdown. A mutex guard and a database connection were being dropped in the wrong order — the connection's destructor tried to acquire the mutex that was already locked by the guard that hadn't been dropped yet. Rust's drop order is deterministic, but "deterministic" doesn't mean "obvious." Knowing the rules saved me hours of debugging.

## The Drop Trait

In Rust, cleanup logic is implemented through the `Drop` trait. When a value goes out of scope, the compiler calls `drop()` on it automatically. This is RAII — Resource Acquisition Is Initialization — borrowed from C++ but made more reliable by the ownership system.

```rust
struct FileHandle {
    name: String,
}

impl Drop for FileHandle {
    fn drop(&mut self) {
        println!("Closing file: {}", self.name);
    }
}

fn main() {
    let _a = FileHandle { name: String::from("config.toml") };
    let _b = FileHandle { name: String::from("data.csv") };
    println!("Both files open");
}
// Output:
// Both files open
// Closing file: data.csv
// Closing file: config.toml
```

Notice the order: `_b` is dropped before `_a`. Variables are dropped in **reverse declaration order**. Last in, first out — like a stack. This makes sense intuitively: later variables might depend on earlier ones, so you tear down the newer ones first.

## The Five Rules of Drop Order

Rust's drop order follows precise rules. Here they are.

### Rule 1: Local Variables Drop in Reverse Declaration Order

```rust
struct Named(&'static str);

impl Drop for Named {
    fn drop(&mut self) {
        println!("Dropping {}", self.0);
    }
}

fn main() {
    let _first = Named("first");
    let _second = Named("second");
    let _third = Named("third");
}
// Output:
// Dropping third
// Dropping second
// Dropping first
```

Straightforward. Reverse order of declaration.

### Rule 2: Struct Fields Drop in Declaration Order

This one trips people up. Unlike local variables, struct fields are dropped in the **same order** they're declared — first to last, not reversed:

```rust
struct Container {
    first: Named,
    second: Named,
    third: Named,
}

impl Drop for Container {
    fn drop(&mut self) {
        println!("Dropping Container itself");
    }
}

fn main() {
    let _c = Container {
        first: Named("field-first"),
        second: Named("field-second"),
        third: Named("field-third"),
    };
}
// Output:
// Dropping Container itself
// Dropping field-first
// Dropping field-second
// Dropping field-third
```

The container's own `Drop` implementation runs first, then its fields are dropped in declaration order. Why declaration order and not reverse? The Rust Reference doesn't give a deep rationale — it was a design decision. The important thing is that it's consistent and deterministic.

### Rule 3: Tuple and Array Elements Drop in Order

Tuples drop their elements left to right (index 0 first). Arrays drop elements from index 0 to the last:

```rust
fn main() {
    let _tuple = (Named("tuple.0"), Named("tuple.1"), Named("tuple.2"));
}
// Output:
// Dropping tuple.0
// Dropping tuple.1
// Dropping tuple.2
```

```rust
fn main() {
    let _array = [Named("arr[0]"), Named("arr[1]"), Named("arr[2]")];
}
// Output:
// Dropping arr[0]
// Dropping arr[1]
// Dropping arr[2]
```

### Rule 4: Enum Drops the Active Variant

An enum only drops the variant that's currently active. The other variants don't exist in memory:

```rust
enum Resource {
    File(Named),
    Network(Named),
    None,
}

impl Drop for Resource {
    fn drop(&mut self) {
        println!("Dropping Resource enum");
    }
}

fn main() {
    let _r = Resource::File(Named("my-file"));
}
// Output:
// Dropping Resource enum
// Dropping my-file
```

Only `File`'s inner `Named` is dropped, because `Network` was never constructed.

### Rule 5: Temporary Values Drop at End of Statement

Temporaries created within an expression are dropped at the end of the statement, unless they're bound to a variable:

```rust
fn make_named() -> Named {
    Named("temporary")
}

fn main() {
    println!("Before");
    let _result = {
        let _temp = make_named(); // lives until end of block
        println!("Inside block");
        42
    };
    println!("After block");
    // _temp is dropped here (end of block)
    println!("After everything");
}
```

But watch out for this subtle case:

```rust
fn main() {
    // The temporary String is dropped at the end of this statement
    let s: &str = &String::from("hello"); // ERROR! temporary dropped while borrowed

    // Fix: bind the String to a variable
    let string = String::from("hello");
    let s: &str = &string; // fine — string lives long enough
}
```

The temporary `String` would be dropped at the semicolon, but `s` would still reference it. The borrow checker catches this.

## std::mem::drop — Explicit Early Drop

Sometimes you need to drop a value before its scope ends. You can't call `.drop()` directly — the compiler forbids it because the value would be in a partially-valid state. Instead, use `std::mem::drop`:

```rust
use std::sync::Mutex;

fn process_data(mutex: &Mutex<Vec<u32>>) {
    let mut guard = mutex.lock().unwrap();
    guard.push(42);

    // Release the lock early — don't hold it during expensive_computation
    drop(guard);

    expensive_computation();
}

fn expensive_computation() {
    println!("Doing expensive work without holding the lock");
}
```

`std::mem::drop` is literally an empty function: `pub fn drop<T>(_x: T) {}`. It takes ownership of the value, and since the value goes out of scope at the end of the function (immediately), it gets dropped. It's a clever use of the ownership system — no special language support needed.

You can also use a block to limit scope:

```rust
fn process_data_v2(mutex: &std::sync::Mutex<Vec<u32>>) {
    {
        let mut guard = mutex.lock().unwrap();
        guard.push(42);
    } // guard dropped here

    expensive_computation();
}
```

Both approaches work. I find explicit `drop()` more readable when the intent is "release this resource now," and blocks more readable when there's a logical grouping of operations.

## ManuallyDrop — Opting Out of Automatic Drop

`ManuallyDrop<T>` wraps a value and prevents it from being dropped automatically. You become responsible for dropping it (or not dropping it at all):

```rust
use std::mem::ManuallyDrop;

fn main() {
    let mut val = ManuallyDrop::new(Named("manual"));
    println!("Created");

    // Do stuff with val — access it through Deref
    println!("Value: {}", val.0);

    // Manually drop it when you're ready
    unsafe { ManuallyDrop::drop(&mut val); }
    println!("After manual drop");

    // DON'T use val after this — it's been dropped
}
```

Why would you want this? A few scenarios:

**Union fields.** Rust unions can have fields with `Drop` implementations, but only one field is valid at a time. `ManuallyDrop` prevents the compiler from dropping a field that might not be the active one:

```rust
use std::mem::ManuallyDrop;

union StringOrInt {
    s: ManuallyDrop<String>,
    i: u64,
}

impl Drop for StringOrInt {
    fn drop(&mut self) {
        // You need to know which field is active
        // and drop it manually if it's the String
    }
}
```

**FFI ownership transfer.** When you pass ownership to C code, you don't want Rust to drop the value:

```rust
use std::mem::ManuallyDrop;

fn give_to_c(data: Vec<u8>) {
    let mut data = ManuallyDrop::new(data);
    let ptr = data.as_mut_ptr();
    let len = data.len();
    let cap = data.capacity();

    unsafe {
        // C code takes ownership — it will call free() later
        c_take_buffer(ptr, len, cap);
    }
    // data is NOT dropped — C owns it now
}

extern "C" { fn c_take_buffer(ptr: *mut u8, len: usize, cap: usize); }
```

## Drop Order Pitfalls

### Pitfall 1: Lock Ordering

The most common real-world issue with drop order involves locks:

```rust
use std::sync::Mutex;

struct Database {
    conn: Mutex<Connection>,
    cache: Mutex<Cache>,
}

impl Database {
    fn update(&self) {
        let conn_guard = self.conn.lock().unwrap();
        let cache_guard = self.cache.lock().unwrap();

        // ... do work ...

        // Dropped in reverse order: cache_guard first, then conn_guard
        // This is fine — but if another thread locks in the opposite order,
        // you have a deadlock risk.
    }
}

struct Connection;
struct Cache;
```

If another function locks `cache` first and then `conn`, you have a classic lock ordering deadlock. Drop order won't save you — you need to establish and follow a global lock ordering convention.

### Pitfall 2: Self-Referential Drop Issues

When a struct's `Drop` implementation accesses fields that have already been partially moved or contain dangling references:

```rust
struct Logger {
    buffer: Vec<String>,
    file: std::fs::File,
}

impl Drop for Logger {
    fn drop(&mut self) {
        // This is fine — we can access all fields in drop()
        // Fields haven't been dropped yet when our drop() runs
        for msg in &self.buffer {
            use std::io::Write;
            let _ = writeln!(self.file, "{}", msg);
        }
    }
}
```

The struct's own `Drop` always runs **before** its fields are dropped. So inside `drop()`, you can safely access all fields. This guarantee is what makes RAII patterns like "flush on drop" work correctly.

### Pitfall 3: Panics During Drop

If a `drop()` implementation panics while another panic is already unwinding the stack, the program aborts immediately. This is called a "double panic":

```rust
struct PanickingDrop;

impl Drop for PanickingDrop {
    fn drop(&mut self) {
        panic!("panic in drop!"); // BAD — can cause double panic
    }
}

fn main() {
    // If main panics, and PanickingDrop's drop also panics → abort
    let _pd = PanickingDrop;
    panic!("main panicking"); // This will abort, not just panic
}
```

Rule of thumb: **never panic in `Drop`**. If your cleanup can fail, log the error and move on. Use `catch_unwind` as a last resort:

```rust
impl Drop for SafeCleanup {
    fn drop(&mut self) {
        if let Err(e) = self.try_cleanup() {
            eprintln!("Cleanup failed: {}", e);
            // Don't panic — just log and continue
        }
    }
}
```

## Drop Check and PhantomData

The compiler performs "drop check" to ensure that when a type is dropped, any references it holds are still valid. This interacts with `PhantomData`:

```rust
use std::marker::PhantomData;

struct Inspector<'a> {
    // This tells the compiler: "I logically hold a &'a str,
    // so 'a must outlive me"
    _marker: PhantomData<&'a str>,
    name: String,
}

impl<'a> Drop for Inspector<'a> {
    fn drop(&mut self) {
        println!("Inspector {} dropping", self.name);
    }
}

fn main() {
    let s = String::from("hello");
    let inspector = Inspector {
        _marker: PhantomData,
        name: String::from("i1"),
    };
    // s must outlive inspector because of PhantomData<&'a str>
    // Since inspector is declared after s... wait, that's wrong.
    // inspector is dropped first (reverse order), then s.
    // So s outlives inspector. This is fine.
    drop(inspector);
    println!("{}", s);
}
```

`PhantomData` is how you tell the compiler about ownership and lifetime relationships that aren't reflected in the actual fields. It's essential for unsafe code that holds raw pointers — without it, the compiler might allow the referenced data to be dropped too early.

## Practical Patterns

### RAII Guards

The most common use of `Drop` in real code is RAII guards — objects that acquire a resource on creation and release it on destruction:

```rust
struct Timer {
    label: &'static str,
    start: std::time::Instant,
}

impl Timer {
    fn new(label: &'static str) -> Self {
        Timer {
            label,
            start: std::time::Instant::now(),
        }
    }
}

impl Drop for Timer {
    fn drop(&mut self) {
        println!("{}: {:?}", self.label, self.start.elapsed());
    }
}

fn main() {
    let _total = Timer::new("total");

    {
        let _parse = Timer::new("parsing");
        // simulate work
        std::thread::sleep(std::time::Duration::from_millis(50));
    } // "parsing: 50ms" printed here

    {
        let _process = Timer::new("processing");
        std::thread::sleep(std::time::Duration::from_millis(100));
    } // "processing: 100ms" printed here

} // "total: 150ms" printed here
```

This pattern is everywhere in Rust: `MutexGuard`, `RwLockReadGuard`, `File`, `TcpStream`, `tempfile::TempDir`. The deterministic drop order means you always know when resources are released.

### Scopeguard for Ad-Hoc Cleanup

The `scopeguard` crate provides a convenient way to run arbitrary code on scope exit:

```rust
// Using the scopeguard crate:
// use scopeguard::defer;
//
// fn process() {
//     let resource = acquire_resource();
//     defer! { release_resource(resource); }
//
//     // ... do work ...
//     // resource is released when this scope exits, no matter what
// }

// DIY version:
struct ScopeGuard<F: FnOnce()>(Option<F>);

impl<F: FnOnce()> Drop for ScopeGuard<F> {
    fn drop(&mut self) {
        if let Some(f) = self.0.take() {
            f();
        }
    }
}

fn on_exit<F: FnOnce()>(f: F) -> ScopeGuard<F> {
    ScopeGuard(Some(f))
}

fn main() {
    let _guard = on_exit(|| println!("Cleanup on exit!"));
    println!("Doing work...");
}
// Output:
// Doing work...
// Cleanup on exit!
```

## What's Next

Drop semantics tell you when memory is freed, but they don't tell you *how*. By default, Rust uses the system allocator (`malloc`/`free`), but you can replace it entirely. In Lesson 8, we'll explore `GlobalAlloc`, custom allocators, and why you might want to swap out the allocator for arena allocation, memory pools, or debugging tools like tracking allocation counts.
