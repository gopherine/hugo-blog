---
title: "Lesson 1: Builder Pattern — Typestate builders and compile-time validation"
author: Atharva Pandey
series: "Rust Design Patterns"
lesson: 1
keywords: [Rust, rust, design patterns, builder pattern, typestate, compile-time validation, type-level programming]
tags: [Rust tutorial, rust, design-patterns, architecture]
date: '2025-10-01T08:30:00.000Z'
---

I once spent three days debugging a production outage caused by a builder that silently accepted a missing `host` field and defaulted to `localhost`. In Java. The builder compiled fine, the tests passed — because they ran against localhost — and the deployment connected to nothing. That was the day I stopped trusting optional fields in builders.

Rust's type system lets you make that entire category of bug impossible. Not at runtime. Not with validation methods. At *compile time*.

## The Builder Pattern, Briefly

If you've done any Java or C#, you know the Builder pattern. You create a mutable builder object, chain setter methods on it, and call `.build()` at the end. It's everywhere — `StringBuilder`, `HttpRequestBuilder`, every ORM's query builder.

The problem? In most languages, builders can't enforce that required fields are set. You either throw a runtime exception in `.build()`, return an `Option`/`Result`, or — worse — silently use defaults. Every approach shifts validation to runtime.

Rust can do better.

## The Naive Builder

Let's start with the straightforward approach — what you'd write if you ported a Java builder directly:

```rust
pub struct ServerConfig {
    host: String,
    port: u16,
    max_connections: usize,
    tls_cert: Option<String>,
}

pub struct ServerConfigBuilder {
    host: Option<String>,
    port: Option<u16>,
    max_connections: Option<usize>,
    tls_cert: Option<String>,
}

impl ServerConfigBuilder {
    pub fn new() -> Self {
        Self {
            host: None,
            port: None,
            max_connections: None,
            tls_cert: None,
        }
    }

    pub fn host(mut self, host: impl Into<String>) -> Self {
        self.host = Some(host.into());
        self
    }

    pub fn port(mut self, port: u16) -> Self {
        self.port = Some(port);
        self
    }

    pub fn max_connections(mut self, max: usize) -> Self {
        self.max_connections = Some(max);
        self
    }

    pub fn tls_cert(mut self, cert: impl Into<String>) -> Self {
        self.tls_cert = Some(cert.into());
        self
    }

    pub fn build(self) -> Result<ServerConfig, String> {
        Ok(ServerConfig {
            host: self.host.ok_or("host is required")?,
            port: self.port.ok_or("port is required")?,
            max_connections: self.max_connections.unwrap_or(100),
            tls_cert: self.tls_cert,
        })
    }
}
```

This works. It's fine. But you don't find out about missing fields until you call `.build()` — at runtime. And the error is a string. In Rust, we can do *much* better.

## Enter Typestate Builders

The idea behind typestate is simple: encode the state of your builder *in the type system*. A builder that hasn't set `host` should be a different type than one that has. The `.build()` method should only exist on builders where all required fields are set.

Here's the trick — we use phantom type parameters to track which fields have been filled:

```rust
use std::marker::PhantomData;

// Marker types for tracking field state
pub struct Missing;
pub struct Set;

pub struct ServerConfigBuilder<HostState, PortState> {
    host: Option<String>,
    port: Option<u16>,
    max_connections: usize,
    tls_cert: Option<String>,
    _host: PhantomData<HostState>,
    _port: PhantomData<PortState>,
}

impl ServerConfigBuilder<Missing, Missing> {
    pub fn new() -> Self {
        Self {
            host: None,
            port: None,
            max_connections: 100,
            tls_cert: None,
            _host: PhantomData,
            _port: PhantomData,
        }
    }
}
```

Now for the magic. When you call `.host()`, the builder *changes type*. The `HostState` parameter flips from `Missing` to `Set`:

```rust
impl<P> ServerConfigBuilder<Missing, P> {
    pub fn host(self, host: impl Into<String>) -> ServerConfigBuilder<Set, P> {
        ServerConfigBuilder {
            host: Some(host.into()),
            port: self.port,
            max_connections: self.max_connections,
            tls_cert: self.tls_cert,
            _host: PhantomData,
            _port: PhantomData,
        }
    }
}

impl<H> ServerConfigBuilder<H, Missing> {
    pub fn port(self, port: u16) -> ServerConfigBuilder<H, Set> {
        ServerConfigBuilder {
            host: self.host,
            port: Some(port),
            max_connections: self.max_connections,
            tls_cert: self.tls_cert,
            _host: PhantomData,
            _port: PhantomData,
        }
    }
}

// Optional fields work on any state
impl<H, P> ServerConfigBuilder<H, P> {
    pub fn max_connections(mut self, max: usize) -> Self {
        self.max_connections = max;
        self
    }

    pub fn tls_cert(mut self, cert: impl Into<String>) -> Self {
        self.tls_cert = Some(cert.into());
        self
    }
}
```

And `.build()` only exists when *both* required fields are set:

```rust
pub struct ServerConfig {
    pub host: String,
    pub port: u16,
    pub max_connections: usize,
    pub tls_cert: Option<String>,
}

impl ServerConfigBuilder<Set, Set> {
    pub fn build(self) -> ServerConfig {
        ServerConfig {
            host: self.host.unwrap(), // safe — type guarantees it's set
            port: self.port.unwrap(), // safe — type guarantees it's set
            max_connections: self.max_connections,
            tls_cert: self.tls_cert,
        }
    }
}
```

Now try building without setting both required fields:

```rust
fn main() {
    // This compiles:
    let config = ServerConfigBuilder::new()
        .host("api.example.com")
        .port(8443)
        .tls_cert("/etc/ssl/cert.pem")
        .build();

    // This does NOT compile:
    // let config = ServerConfigBuilder::new()
    //     .host("api.example.com")
    //     .build();
    // Error: no method named `build` found for
    // `ServerConfigBuilder<Set, Missing>`
}
```

The compiler error message even tells you *exactly* which field is missing. `ServerConfigBuilder<Set, Missing>` — host is set, port is missing. That's not a runtime error with a string message. That's the type system catching your mistake before a single line of code runs.

## Why Those .unwrap() Calls Are Safe

You might look at the `.build()` method and think "those unwraps could panic!" They can't. The method only exists on `ServerConfigBuilder<Set, Set>`, and the only way to transition to `Set` is through methods that populate the corresponding `Option`. The type system creates a proof that the values exist. Those unwraps are just unwinding the `Option` wrapper — they're formalities.

This is a pattern you'll see a lot in advanced Rust: using types to create *proofs* about your program's state, then using those proofs to do operations that would otherwise be unsafe.

## Scaling: The Macro Approach

The typestate builder works beautifully for two or three required fields. But what happens when you have eight? You'd need type parameters for each one, and the combinatorial explosion gets ugly. `ServerConfigBuilder<Set, Missing, Set, Missing, Set, Set, Missing, Set>` isn't readable.

For real-world code, I reach for the `derive_builder` or `bon` crates. But if you want to stay dependency-free, there's a middle ground — use a const generic bool instead of separate marker types:

```rust
pub struct Builder<const HOST: bool, const PORT: bool, const DB: bool> {
    host: Option<String>,
    port: Option<u16>,
    database_url: Option<String>,
}

impl Builder<false, false, false> {
    pub fn new() -> Self {
        Self {
            host: None,
            port: None,
            database_url: None,
        }
    }
}

impl<const P: bool, const D: bool> Builder<false, P, D> {
    pub fn host(self, host: impl Into<String>) -> Builder<true, P, D> {
        Builder {
            host: Some(host.into()),
            port: self.port,
            database_url: self.database_url,
        }
    }
}

impl<const H: bool, const D: bool> Builder<H, false, D> {
    pub fn port(self, port: u16) -> Builder<H, true, D> {
        Builder {
            host: self.host,
            port: Some(port),
            database_url: self.database_url,
        }
    }
}

impl<const H: bool, const P: bool> Builder<H, P, false> {
    pub fn database_url(self, url: impl Into<String>) -> Builder<H, P, true> {
        Builder {
            host: self.host,
            port: self.port,
            database_url: Some(url.into()),
        }
    }
}

impl Builder<true, true, true> {
    pub fn build(self) -> Config {
        Config {
            host: self.host.unwrap(),
            port: self.port.unwrap(),
            database_url: self.database_url.unwrap(),
        }
    }
}
```

Same idea, slightly more compact syntax. Const generics make this ergonomic enough for production code.

## When NOT to Use Typestate Builders

I want to be honest here — typestate builders are powerful, but they're not always the right call.

**Don't use them when all fields are optional.** If your struct has sensible defaults for everything, a simple `Default` impl and struct update syntax works better:

```rust
let config = Config {
    port: 9090,
    ..Config::default()
};
```

**Don't use them when the builder is constructed dynamically.** If your fields come from a config file or user input at runtime, you can't leverage compile-time checks anyway. You need runtime validation. The naive builder with a `Result` return is the right tool.

**Don't use them for internal-only types.** If the builder is used in three places within your own crate and you control all call sites, the ceremony isn't worth it. Typestate builders shine at API boundaries — library code that other people consume.

## The Real-World Pattern

In production Rust, I've found the sweet spot is combining typestate with good defaults and `Into` bounds for ergonomics:

```rust
impl ServerConfigBuilder<Set, Set> {
    pub fn build(self) -> ServerConfig {
        ServerConfig {
            host: self.host.unwrap(),
            port: self.port.unwrap(),
            max_connections: self.max_connections,
            tls_cert: self.tls_cert,
        }
    }
}

// Usage reads almost like natural language
let config = ServerConfigBuilder::new()
    .host("prod.api.internal")
    .port(8443)
    .max_connections(500)
    .build();
```

No `Result`, no `unwrap` at the call site, no runtime validation. If it compiles, it's correct. That's the Rust promise, and typestate builders are one of the most satisfying ways to deliver on it.

## Key Takeaways

The Builder pattern in Rust isn't just a port of the Java/C# version. Typestate turns runtime validation into compile-time guarantees. You can make illegal states unrepresentable — and that phrase isn't just a slogan. It's a concrete engineering technique that prevents real bugs in real production systems.

Start with the naive builder if you're prototyping. Graduate to typestate when you're building APIs that other people — or future-you — will consume. The type system is your best documentation, and it never goes out of date.
