---
title: "Lesson 8: The Component Model — Composable WASM modules"
author: Atharva Pandey
series: "Rust WebAssembly"
lesson: 8
keywords: [Rust, rust, WebAssembly, WASM, Component Model, WIT, interface types, composability, interop]
tags: [Rust tutorial, rust, webassembly, wasm]
date: '2025-07-18T10:41:09.000Z'
---

Here's a scenario that actually happened to me: I had a data validation library written in Rust, a business logic layer in Go, and a reporting module that a client had written in Python. Three languages, three teams, three deployment stories. Traditionally, this means three services talking over HTTP with serialization overhead, network latency, and a distributed systems headache.

With the Component Model, I compiled all three to WASM components, composed them into a single module, and ran the whole pipeline in one process. No network calls, no serialization, no containers. Function calls across language boundaries, at native speed.

That's the promise of the Component Model. And it's finally becoming real.

## What Problem Does It Solve?

Core WASM has a fundamental limitation: modules can only import and export functions that use numeric types (`i32`, `i64`, `f32`, `f64`). That's it. No strings, no records, no lists, no variants, no results. Everything else requires convention — you agree that "the first i32 is a pointer and the second is a length," and you hope both sides of the boundary implement the convention the same way.

`wasm-bindgen` solves this for Rust↔JavaScript. But what about Rust↔Go? Rust↔Python? Go↔C#? Every language pair needs its own bridge, and they're all incompatible.

The Component Model solves this by adding a rich type system on top of WASM:

```
Core WASM:       i32, i64, f32, f64
                         ↓
Component Model: string, list<T>, record, variant, result<T, E>,
                 option<T>, tuple, flags, enum, resource
```

Components define their interfaces using **WIT** (WASM Interface Types), a language-agnostic IDL. Any language that compiles to WASM can implement or consume a WIT interface.

## WIT: The Interface Language

WIT files define the contract between components. Think of them like Protocol Buffers or OpenAPI, but for WASM modules:

```wit
// validator.wit
package my:validator@1.0.0;

interface types {
    record user {
        name: string,
        email: string,
        age: u32,
    }

    record validation-error {
        field: string,
        message: string,
    }

    variant validation-result {
        ok,
        errors(list<validation-error>),
    }
}

interface validator {
    use types.{user, validation-result};

    validate-user: func(user: user) -> validation-result;
    validate-email: func(email: string) -> bool;
}

world user-validator {
    export validator;
}
```

Let me break down the WIT syntax:
- `package` — a namespaced package identifier with semver
- `interface` — a group of types and functions (like a Rust trait)
- `record` — a product type (like a struct)
- `variant` — a sum type (like an enum)
- `world` — the complete shape of a component (what it imports and exports)

## Building a Component in Rust

You need `cargo-component`, the tool that bridges Cargo and the Component Model:

```bash
cargo install cargo-component
```

Create a new component project:

```bash
cargo component new --lib user-validator
cd user-validator
```

This generates a project with a `wit/` directory. Put your WIT definition there:

```
user-validator/
├── Cargo.toml
├── src/
│   └── lib.rs
└── wit/
    └── world.wit
```

```wit
// wit/world.wit
package my:user-validator@1.0.0;

interface types {
    record user {
        name: string,
        email: string,
        age: u32,
    }

    record validation-error {
        field: string,
        message: string,
    }
}

interface validator {
    use types.{user, validation-error};

    validate-user: func(user: user) -> list<validation-error>;
    validate-email: func(email: string) -> bool;
}

world user-validator {
    export validator;
}
```

```toml
# Cargo.toml
[package]
name = "user-validator"
version = "0.1.0"
edition = "2021"

[dependencies]
wit-bindgen = "0.36"

[lib]
crate-type = ["cdylib"]
```

Now implement the component:

```rust
// src/lib.rs

// This macro generates Rust types from the WIT definitions
wit_bindgen::generate!({
    world: "user-validator",
});

struct MyValidator;

impl Guest for MyValidator {
    // The types are auto-generated from WIT
}

impl exports::my::user_validator::validator::Guest for MyValidator {
    fn validate_user(user: exports::my::user_validator::types::User) -> Vec<exports::my::user_validator::types::ValidationError> {
        let mut errors = Vec::new();

        // Name validation
        if user.name.trim().is_empty() {
            errors.push(exports::my::user_validator::types::ValidationError {
                field: "name".to_string(),
                message: "Name cannot be empty".to_string(),
            });
        } else if user.name.len() < 2 {
            errors.push(exports::my::user_validator::types::ValidationError {
                field: "name".to_string(),
                message: "Name must be at least 2 characters".to_string(),
            });
        }

        // Email validation
        if !Self::validate_email(&user.email) {
            errors.push(exports::my::user_validator::types::ValidationError {
                field: "email".to_string(),
                message: "Invalid email format".to_string(),
            });
        }

        // Age validation
        if user.age < 13 {
            errors.push(exports::my::user_validator::types::ValidationError {
                field: "age".to_string(),
                message: "Must be at least 13 years old".to_string(),
            });
        } else if user.age > 150 {
            errors.push(exports::my::user_validator::types::ValidationError {
                field: "age".to_string(),
                message: "Invalid age".to_string(),
            });
        }

        errors
    }

    fn validate_email(email: &str) -> bool {
        let parts: Vec<&str> = email.split('@').collect();
        if parts.len() != 2 {
            return false;
        }

        let local = parts[0];
        let domain = parts[1];

        !local.is_empty()
            && !domain.is_empty()
            && domain.contains('.')
            && !domain.starts_with('.')
            && !domain.ends_with('.')
    }
}

export!(MyValidator);
```

Build the component:

```bash
cargo component build --release
```

This produces a `.wasm` file in `target/wasm32-wasip1/release/` — but it's a *component*, not a core WASM module. It has embedded type information and interface definitions.

## Composing Components

The real power comes from composition. Say you have two components:

1. **user-validator** — validates user data (Rust)
2. **user-service** — business logic that depends on validation (could be any language)

```wit
// user-service.wit
package my:user-service@1.0.0;

interface service {
    use my:user-validator/types@1.0.0.{user, validation-error};

    record create-user-result {
        success: bool,
        user-id: option<string>,
        errors: list<validation-error>,
    }

    create-user: func(user: user) -> create-user-result;
}

world user-service {
    import my:user-validator/validator@1.0.0;
    export service;
}
```

The implementation imports and calls the validator:

```rust
// user-service/src/lib.rs
wit_bindgen::generate!({
    world: "user-service",
});

use my::user_validator::validator;
use my::user_validator::types::{User, ValidationError};

struct UserService;

impl exports::my::user_service::service::Guest for UserService {
    fn create_user(user: User) -> exports::my::user_service::service::CreateUserResult {
        // Call the imported validator component
        let errors = validator::validate_user(&user);

        if !errors.is_empty() {
            return exports::my::user_service::service::CreateUserResult {
                success: false,
                user_id: None,
                errors,
            };
        }

        // Business logic — create the user
        let user_id = format!("usr_{}", generate_id());

        exports::my::user_service::service::CreateUserResult {
            success: true,
            user_id: Some(user_id),
            errors: vec![],
        }
    }
}

fn generate_id() -> u64 {
    // In real code, use a proper ID generator
    42
}

export!(UserService);
```

Now compose them using `wac` (WebAssembly Compositions):

```bash
# Install wac
cargo install wac-cli

# Compose the two components
wac plug user-service.wasm --plug user-validator.wasm -o composed.wasm
```

The result is a single `composed.wasm` that contains both components, linked together through their WIT interfaces. Function calls between them are direct — no serialization, no IPC, no network hops.

## Running Composed Components

Use Wasmtime to run the composed component:

```rust
use wasmtime::component::*;
use wasmtime::*;
use wasmtime_wasi::WasiCtxBuilder;

// Generate host bindings from WIT
wasmtime::component::bindgen!({
    path: "wit",
    world: "user-service",
    async: false,
});

fn main() -> anyhow::Result<()> {
    let mut config = Config::new();
    config.wasm_component_model(true);

    let engine = Engine::new(&config)?;
    let component = Component::from_file(&engine, "composed.wasm")?;

    let mut linker = Linker::new(&engine);
    wasmtime_wasi::add_to_linker_sync(&mut linker)?;

    let wasi_ctx = WasiCtxBuilder::new().build();
    let mut store = Store::new(&engine, wasi_ctx);

    let instance = linker.instantiate(&mut store, &component)?;

    // Call the composed service
    let service = instance.my_user_service_service();

    let result = service.call_create_user(
        &mut store,
        &User {
            name: "Atharva".to_string(),
            email: "atharva@example.com".to_string(),
            age: 28,
        },
    )?;

    println!("Success: {}", result.success);
    println!("User ID: {:?}", result.user_id);
    println!("Errors: {:?}", result.errors);

    Ok(())
}
```

## Resources: Object-Oriented Patterns

The Component Model includes `resource` types — think of them as opaque handles with methods, similar to classes:

```wit
// database.wit
package my:database@1.0.0;

interface connection {
    resource db-connection {
        constructor(connection-string: string);
        query: func(sql: string) -> result<list<list<string>>, string>;
        execute: func(sql: string) -> result<u64, string>;
        close: func();
    }
}

world database {
    export connection;
}
```

```rust
wit_bindgen::generate!({
    world: "database",
});

use exports::my::database::connection::{Guest, GuestDbConnection};

struct DatabaseComponent;

impl Guest for DatabaseComponent {}

struct DbConnection {
    // In real code: actual database connection
    connected: bool,
    connection_string: String,
}

impl GuestDbConnection for DbConnection {
    fn new(connection_string: String) -> Self {
        eprintln!("Connecting to: {}", connection_string);
        DbConnection {
            connected: true,
            connection_string,
        }
    }

    fn query(&self, sql: String) -> Result<Vec<Vec<String>>, String> {
        if !self.connected {
            return Err("Not connected".to_string());
        }
        eprintln!("Executing query: {}", sql);
        // Return mock data
        Ok(vec![
            vec!["id".to_string(), "name".to_string()],
            vec!["1".to_string(), "Alice".to_string()],
            vec!["2".to_string(), "Bob".to_string()],
        ])
    }

    fn execute(&self, sql: String) -> Result<u64, String> {
        if !self.connected {
            return Err("Not connected".to_string());
        }
        eprintln!("Executing: {}", sql);
        Ok(1)
    }

    fn close(&self) {
        eprintln!("Connection closed");
    }
}

export!(DatabaseComponent);
```

Resources give you ownership semantics across component boundaries. When the caller drops the resource handle, the component's destructor runs. It's RAII across language boundaries.

## The Bigger Picture

The Component Model isn't just about composing Rust modules. It's about composing modules written in *any* language:

- **Rust** → `cargo-component` + `wit-bindgen`
- **Go** → `wit-bindgen-go` (via TinyGo)
- **Python** → `componentize-py`
- **JavaScript** → `jco` (JavaScript Component Tools)
- **C/C++** → `wit-bindgen-c`

A validation component written in Rust can be used by a Go service, which is composed with a Python ML model. All running in the same process, all type-safe at the interface boundary, all sandboxed from each other.

## WASI Preview 2 + Component Model

WASI Preview 2 is built entirely on the Component Model. Standard interfaces like `wasi:http`, `wasi:filesystem`, and `wasi:cli` are defined as WIT interfaces:

```wit
// From the WASI spec
package wasi:http@0.2.0;

interface incoming-handler {
    use types.{incoming-request, response-outparam};

    handle: func(request: incoming-request, response-out: response-outparam);
}

world proxy {
    import wasi:logging/logging;
    import wasi:clocks/wall-clock;

    export incoming-handler;
}
```

This means every WASI capability is a component interface. You can mock file systems, inject test HTTP handlers, or swap implementations without changing your component's code. It's dependency injection at the infrastructure level.

## Current Limitations

I want to be honest about where the Component Model is today:

**What works well:**
- Defining interfaces with WIT
- Building Rust components with `cargo-component`
- Composing components with `wac`
- Running components with Wasmtime
- Cross-language interop for simple types

**What's still rough:**
- **Tooling maturity** — `cargo-component` and `wit-bindgen` have frequent breaking changes
- **Async support** — the async model in the Component Model is still being designed
- **Debugging** — stepping through composed components is painful
- **Binary size** — component overhead adds to the WASM binary
- **Language support** — Rust is the best supported; other languages are catching up
- **Documentation** — scattered across multiple repos and specifications

**What's missing:**
- **Streaming** — efficient streaming of large data between components
- **Shared memory** — components can't share memory (by design, for isolation, but it limits performance)
- **Threading** — no threading model within the Component Model yet

## When to Use the Component Model Today

**Good use cases:**
- Plugin systems where plugins can be written in multiple languages
- Edge computing platforms (Fermyon Spin, Fastly Compute)
- Composable serverless functions
- Sandboxed execution of untrusted code with typed interfaces
- Distributing portable libraries that work across language ecosystems

**Not yet ready for:**
- Performance-critical inner loops (the component boundary has overhead)
- Applications that need shared memory between modules
- Projects that can't tolerate tooling instability

## Course Wrap-Up

We've covered the full spectrum of Rust WebAssembly — from compiling your first function to the browser, through DOM manipulation, frameworks, performance optimization, multi-threading, WASI, and now the Component Model.

Here's what I think matters most:

1. **WASM is not a silver bullet.** It's a tool. Use it when you have CPU-bound work that benefits from Rust's performance characteristics. Don't rewrite your CRUD app in Rust/WASM.

2. **The boundary cost is real.** Design your WASM modules to do large chunks of work per call. Minimize crossings. Batch operations. Use shared memory views where possible.

3. **Frameworks have matured.** Leptos, Yew, and Dioxus are production-capable. If you're building a web UI in Rust, use one of them. Don't fight raw `web-sys` unless you need canvas/WebGL performance.

4. **WASI is the bigger story.** Browser WASM gets the headlines, but server-side WASM with WASI is where the industry is heading. Sub-millisecond cold starts, true sandboxing, and cross-platform portability are real competitive advantages.

5. **The Component Model is the future.** It's not fully baked yet, but the trajectory is clear. Write your libraries as components now, and you'll be ready when the ecosystem catches up.

The Rust WASM ecosystem moves fast. What I've written here will be partially outdated in six months. But the fundamentals — how WASM works, why the boundary costs what it costs, when parallelism helps, how capabilities-based security works — those don't change. Build on the fundamentals, and the API churn won't bother you.
