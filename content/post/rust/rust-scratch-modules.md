---
title: "Lesson 17: Modules — Organizing your code"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 17
keywords: [Rust, rust, modules, mod, pub, use, visibility, code organization, rust modules]
tags: [Rust tutorial, rust, beginner]
date: '2024-04-03T14:00:00.000Z'
---

Rust's module system confused me for longer than I'd like to admit. I came from Go, where packages map directly to directories. Rust's module system is more flexible — and more confusing as a result. But once you understand the mental model, it's actually quite elegant. The key insight: the file system doesn't define the module tree. You do.

## Modules in a Single File

The simplest case — modules defined inline:

```rust
mod math {
    pub fn add(a: i32, b: i32) -> i32 {
        a + b
    }

    pub fn multiply(a: i32, b: i32) -> i32 {
        a * b
    }

    fn internal_helper() -> i32 {
        42
    }
}

fn main() {
    println!("2 + 3 = {}", math::add(2, 3));
    println!("4 * 5 = {}", math::multiply(4, 5));

    // math::internal_helper(); // ERROR: function is private
}
```

`mod` declares a module. `pub` makes items public — visible outside the module. Without `pub`, items are private to the module. This is the default, and it's the right default.

## Visibility Rules

Rust's visibility rules are simple:

1. Everything is private by default
2. `pub` makes it visible to the parent module and beyond
3. Child modules can see everything in parent modules
4. Parent modules can only see `pub` items in child modules

```rust
mod outer {
    pub fn public_function() {
        println!("I'm public");
        private_function();  // parent can call its own private functions
    }

    fn private_function() {
        println!("I'm private to outer");
    }

    pub mod inner {
        pub fn inner_public() {
            println!("I'm inner and public");
            super::private_function();  // child CAN see parent's private items
        }

        fn inner_private() {
            println!("I'm inner and private");
        }
    }
}

fn main() {
    outer::public_function();
    outer::inner::inner_public();

    // outer::private_function();        // ERROR: private
    // outer::inner::inner_private();    // ERROR: private
}
```

`super` refers to the parent module. Like `..` in file paths. You'll use it when a child module needs to reference something in its parent.

## Modules in Separate Files

For real projects, you'll split modules into separate files. Here's how.

### The Old Way (pre-2018)

Don't use this. But you'll see it in older code:

```
src/
├── main.rs
├── math.rs
└── math/
    └── advanced.rs
```

### The New Way (Rust 2018+)

Two approaches. Both work. I have a strong preference.

**Approach 1: File per module**

```
src/
├── main.rs
└── math.rs
```

`src/main.rs`:
```rust
mod math;  // tells Rust to look for math.rs or math/mod.rs

fn main() {
    println!("{}", math::add(2, 3));
}
```

`src/math.rs`:
```rust
pub fn add(a: i32, b: i32) -> i32 {
    a + b
}

pub fn multiply(a: i32, b: i32) -> i32 {
    a * b
}
```

**Approach 2: Directory with mod.rs**

```
src/
├── main.rs
└── math/
    ├── mod.rs
    └── advanced.rs
```

`src/math/mod.rs`:
```rust
pub mod advanced;  // declares the advanced submodule

pub fn add(a: i32, b: i32) -> i32 {
    a + b
}
```

`src/math/advanced.rs`:
```rust
pub fn power(base: f64, exp: u32) -> f64 {
    (0..exp).fold(1.0, |acc, _| acc * base)
}
```

`src/main.rs`:
```rust
mod math;

fn main() {
    println!("{}", math::add(2, 3));
    println!("{}", math::advanced::power(2.0, 10));
}
```

**My recommendation:** For modules with submodules, use the directory approach. For simple modules, use a single file. Don't overthink it.

## The use Keyword

Typing `math::advanced::power` every time is verbose. `use` brings items into scope:

```rust
mod math {
    pub mod trig {
        pub fn sin(x: f64) -> f64 {
            x.sin()
        }

        pub fn cos(x: f64) -> f64 {
            x.cos()
        }
    }
}

// Bring specific items into scope
use math::trig::sin;
use math::trig::cos;

// Or use a group
// use math::trig::{sin, cos};

// Or bring the module itself
// use math::trig;
// then call trig::sin(), trig::cos()

fn main() {
    let angle = std::f64::consts::PI / 4.0;
    println!("sin(pi/4) = {:.4}", sin(angle));
    println!("cos(pi/4) = {:.4}", cos(angle));
}
```

### use Conventions

There are community conventions about what to `use`:

```rust
use std::collections::HashMap;  // types — import the type directly
use std::io;                    // modules — import the module, use io::Read, io::Write
use std::io::Read;              // traits — import traits directly (needed for method resolution)
```

For functions, there's a style choice. I prefer importing the parent module to keep it clear where the function comes from:

```rust
// I prefer this:
use std::fs;
let content = fs::read_to_string("file.txt");

// Over this (though it works):
use std::fs::read_to_string;
let content = read_to_string("file.txt");
```

With the module prefix, you always know where `read_to_string` comes from. Without it, you'd have to scroll up to the imports.

### Renaming with as

```rust
use std::collections::HashMap as Map;
use std::io::Result as IoResult;

fn main() {
    let mut m: Map<String, i32> = Map::new();
    m.insert("key".to_string(), 42);
    println!("{:?}", m);
}
```

Useful for avoiding name conflicts or shortening long names.

## Re-exports with pub use

You can re-export items from submodules to create a cleaner public API:

```rust
mod internal {
    pub mod database {
        pub struct Connection {
            pub host: String,
        }

        impl Connection {
            pub fn new(host: &str) -> Self {
                Connection { host: host.to_string() }
            }
        }
    }
}

// Re-export so users don't need to know the internal structure
pub use internal::database::Connection;

fn main() {
    // Users can do this:
    let conn = Connection::new("localhost");
    // Instead of this:
    // let conn = internal::database::Connection::new("localhost");

    println!("Connected to: {}", conn.host);
}
```

This is a powerful technique for library design. Your internal module structure can be as deep and organized as you want, but you present a flat, clean API to users.

## Struct Visibility

Struct fields have their own visibility, independent of the struct itself:

```rust
mod user {
    pub struct User {
        pub name: String,      // public — anyone can read/set
        email: String,         // private — only this module
        password_hash: String, // private — definitely private
    }

    impl User {
        pub fn new(name: &str, email: &str, password: &str) -> Self {
            User {
                name: name.to_string(),
                email: email.to_string(),
                password_hash: format!("hashed:{password}"),
            }
        }

        pub fn email(&self) -> &str {
            &self.email
        }
    }
}

fn main() {
    let u = user::User::new("Alice", "alice@example.com", "secret123");

    println!("Name: {}", u.name);       // OK — name is pub
    println!("Email: {}", u.email());   // OK — through public method
    // println!("{}", u.email);          // ERROR — field is private
    // println!("{}", u.password_hash);  // ERROR — field is private
}
```

This is how you enforce encapsulation. The struct is public, but not all fields are. Users must go through methods to access private data. This is better than making everything public — it lets you change the internal representation without breaking callers.

## A Real Project Structure

Here's a typical small-to-medium Rust project:

```
my_app/
├── Cargo.toml
└── src/
    ├── main.rs
    ├── config.rs
    ├── error.rs
    ├── models/
    │   ├── mod.rs
    │   ├── user.rs
    │   └── post.rs
    └── handlers/
        ├── mod.rs
        ├── auth.rs
        └── api.rs
```

`src/main.rs`:
```rust
mod config;
mod error;
mod models;
mod handlers;

fn main() {
    let cfg = config::load();
    println!("Starting server on port {}", cfg.port);
}
```

`src/config.rs`:
```rust
pub struct Config {
    pub port: u16,
    pub database_url: String,
}

pub fn load() -> Config {
    Config {
        port: 8080,
        database_url: String::from("postgres://localhost/mydb"),
    }
}
```

`src/models/mod.rs`:
```rust
pub mod user;
pub mod post;

// Re-export commonly used types
pub use user::User;
pub use post::Post;
```

Each `mod` declaration in `main.rs` tells the compiler about a module. The compiler finds the source based on the module name and the file system layout.

## Common Mistakes

**Mistake 1: Forgetting `mod` in main.rs**

Creating a file `src/utils.rs` doesn't make it part of your project. You must declare `mod utils;` in `main.rs` (or in another module that's already declared).

**Mistake 2: `pub` on the module but not on the items**

```rust
pub mod helpers {
    fn not_actually_public() {}  // still private!
    pub fn actually_public() {}
}
```

Making the module `pub` lets you *see* the module. But items inside still need their own `pub` to be accessible.

**Mistake 3: Circular dependencies**

Module A can't use module B while module B uses module A. If you find yourself needing this, restructure — extract the shared code into a third module that both can depend on.

## The Prelude

You might wonder why you can use `Vec`, `String`, `Option`, `Result`, and `println!` without importing them. Rust has a *prelude* — a set of items automatically imported into every module. It includes the most commonly used types and traits. You can see the full list in the [standard library docs](https://doc.rust-lang.org/std/prelude/).

If you want to import everything from a module (useful for tests or internal modules):

```rust
use std::collections::*;  // glob import — imports everything public
```

Avoid glob imports in production code — they make it unclear where names come from. The exception is in tests, where convenience matters more than clarity.

Next: crates, Cargo.toml, and dependencies — the Rust ecosystem.
