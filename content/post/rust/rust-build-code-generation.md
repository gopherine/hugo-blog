---
title: "Lesson 5: Code Generation — proc macros, build.rs, xtask"
author: Atharva Pandey
series: "Rust Compiler Plugins & Build System"
lesson: 5
keywords: [Rust, rust, cargo, build system, code generation, proc macros, xtask, build.rs, derive macros]
tags: [Rust tutorial, rust, cargo, build-system]
date: '2025-08-10T08:55:00.000Z'
---

I once inherited a codebase where someone had written a Python script that generated 4,000 lines of Rust from a YAML spec. The script ran outside of Cargo, the generated file was checked into git, and nobody remembered to re-run it when the spec changed. By the time I found it, the generated code and the spec had diverged in twelve places. That experience shaped how I think about code generation in Rust — it needs to be integrated into the build, not bolted on the side.

Rust gives you three main approaches to code generation, each with different tradeoffs. Let's walk through all of them.

## Approach 1: Procedural Macros

Proc macros run at compile time as part of the Rust compiler pipeline. They receive tokens and produce tokens. This makes them the most integrated option — they work with rust-analyzer, they produce proper error messages, and they're invisible to users of your code.

### Derive Macros

The most common kind. You've used these — `#[derive(Debug, Clone, Serialize)]`. Let's write one.

Say you want a `Builder` derive macro that generates a builder pattern for any struct:

```rust
// builder-derive/Cargo.toml
[package]
name = "builder-derive"
version = "0.1.0"
edition = "2021"

[lib]
proc-macro = true

[dependencies]
syn = { version = "2", features = ["full"] }
quote = "1"
proc-macro2 = "1"
```

```rust
// builder-derive/src/lib.rs
use proc_macro::TokenStream;
use quote::quote;
use syn::{parse_macro_input, DeriveInput, Data, Fields};

#[proc_macro_derive(Builder)]
pub fn derive_builder(input: TokenStream) -> TokenStream {
    let input = parse_macro_input!(input as DeriveInput);
    let name = &input.ident;
    let builder_name = syn::Ident::new(
        &format!("{}Builder", name),
        name.span(),
    );

    let fields = match &input.data {
        Data::Struct(data) => match &data.fields {
            Fields::Named(fields) => &fields.named,
            _ => panic!("Builder only supports named fields"),
        },
        _ => panic!("Builder only supports structs"),
    };

    // Generate Option<T> fields for the builder
    let builder_fields = fields.iter().map(|f| {
        let name = &f.ident;
        let ty = &f.ty;
        quote! { #name: Option<#ty> }
    });

    // Generate setter methods
    let setters = fields.iter().map(|f| {
        let name = &f.ident;
        let ty = &f.ty;
        quote! {
            pub fn #name(mut self, value: #ty) -> Self {
                self.#name = Some(value);
                self
            }
        }
    });

    // Generate the build method
    let build_fields = fields.iter().map(|f| {
        let name = &f.ident;
        let name_str = name.as_ref().unwrap().to_string();
        quote! {
            #name: self.#name.ok_or_else(|| {
                format!("field '{}' is required", #name_str)
            })?
        }
    });

    // Generate builder initialization (all None)
    let none_fields = fields.iter().map(|f| {
        let name = &f.ident;
        quote! { #name: None }
    });

    let expanded = quote! {
        pub struct #builder_name {
            #(#builder_fields,)*
        }

        impl #name {
            pub fn builder() -> #builder_name {
                #builder_name {
                    #(#none_fields,)*
                }
            }
        }

        impl #builder_name {
            #(#setters)*

            pub fn build(self) -> Result<#name, String> {
                Ok(#name {
                    #(#build_fields,)*
                })
            }
        }
    };

    TokenStream::from(expanded)
}
```

Usage:

```rust
use builder_derive::Builder;

#[derive(Builder, Debug)]
struct ServerConfig {
    host: String,
    port: u16,
    workers: usize,
    tls_cert: String,
}

fn main() {
    let config = ServerConfig::builder()
        .host("0.0.0.0".into())
        .port(8080)
        .workers(4)
        .tls_cert("/etc/ssl/cert.pem".into())
        .build()
        .unwrap();

    println!("{config:?}");
}
```

### Attribute Macros

Attribute macros transform the item they're attached to. They're more flexible than derive macros because they can modify the original item, not just add to it.

```rust
// A macro that wraps a function with timing instrumentation
#[proc_macro_attribute]
pub fn timed(attr: TokenStream, item: TokenStream) -> TokenStream {
    let input = parse_macro_input!(item as syn::ItemFn);
    let fn_name = &input.sig.ident;
    let fn_name_str = fn_name.to_string();
    let block = &input.block;
    let sig = &input.sig;
    let attrs = &input.attrs;
    let vis = &input.vis;

    let expanded = quote! {
        #(#attrs)*
        #vis #sig {
            let __start = std::time::Instant::now();
            let __result = (|| #block)();
            let __elapsed = __start.elapsed();
            eprintln!("[TIMING] {} took {:?}", #fn_name_str, __elapsed);
            __result
        }
    };

    TokenStream::from(expanded)
}
```

```rust
#[timed]
fn process_data(input: &[u8]) -> Vec<u8> {
    // Your code here
    input.to_vec()
}
// Prints: [TIMING] process_data took 42.3µs
```

### Function-Like Macros

These look like function calls but with a `!`. They're the most flexible — they can accept arbitrary syntax:

```rust
#[proc_macro]
pub fn sql_table(input: TokenStream) -> TokenStream {
    // Parse custom DSL and generate Rust structs + SQL queries
    let input_str = input.to_string();
    // ... parsing logic ...
    todo!()
}
```

```rust
sql_table! {
    users {
        id: uuid primary_key,
        email: text unique not_null,
        name: text not_null,
        created_at: timestamp default_now,
    }
}
// Generates: struct User, UserRow, insert/update/delete functions, etc.
```

### When to Use Proc Macros

**Good for:**
- Deriving trait implementations
- Reducing repetitive struct/impl boilerplate
- DSLs that need to produce Rust types and functions
- Anything where IDE integration matters (rust-analyzer understands proc macros)

**Bad for:**
- Code generated from external files (use build.rs instead)
- Anything that needs to read the filesystem
- Complex multi-step transformations (proc macros can get very hard to debug)

### The proc-macro Debugging Tax

Proc macros are notoriously hard to debug. Your code runs inside the compiler, you can't use `println!` normally, and error messages from malformed output are cryptic.

Tips that save my sanity:

```rust
// Use cargo-expand to see what your macro generates
// cargo install cargo-expand
// cargo expand

// In your proc macro, use compile_error! for debugging:
#[proc_macro_derive(MyDerive)]
pub fn derive_my(input: TokenStream) -> TokenStream {
    let input = parse_macro_input!(input as DeriveInput);

    // Temporarily dump the parsed input during development
    eprintln!("Parsed: {:#?}", input);

    // ... rest of implementation
}

// Use syn's Error type for proper error messages
fn validate_field(field: &syn::Field) -> syn::Result<()> {
    if field.ident.is_none() {
        return Err(syn::Error::new_spanned(
            field,
            "Builder requires named fields, not tuple fields",
        ));
    }
    Ok(())
}
```

## Approach 2: build.rs Code Generation

We covered this in Lesson 2, but let's compare it directly to proc macros.

build.rs is better when:
- You're generating code from external files (protobuf schemas, SQL files, config files)
- You need to read the filesystem or run external tools
- The generation logic doesn't depend on Rust syntax — you're working with strings

```rust
// build.rs — generate API client from OpenAPI spec
fn main() {
    let spec = std::fs::read_to_string("openapi.yaml").unwrap();
    let api: OpenApiSpec = serde_yaml::from_str(&spec).unwrap();

    let mut code = String::new();
    code.push_str("use reqwest::Client;\nuse serde::{Serialize, Deserialize};\n\n");

    for (path, methods) in &api.paths {
        for (method, operation) in methods {
            let fn_name = operation.operation_id.to_snake_case();
            let response_type = &operation.response_type;

            code.push_str(&format!(
                r#"pub async fn {fn_name}(client: &Client) -> Result<{response_type}, reqwest::Error> {{
    client.{method}("{path}")
        .send()
        .await?
        .json()
        .await
}}

"#
            ));
        }
    }

    let out_dir = std::env::var("OUT_DIR").unwrap();
    std::fs::write(
        std::path::Path::new(&out_dir).join("api_client.rs"),
        code,
    ).unwrap();

    println!("cargo:rerun-if-changed=openapi.yaml");
}
```

The downside: generated code in `OUT_DIR` is invisible to your IDE. No autocomplete, no type checking until you compile.

## Approach 3: xtask — The Best of Both Worlds

`xtask` is a pattern (not a tool) where you write your build automation as a Rust binary within your workspace. It's a crate called `xtask` that you run via `cargo xtask <command>`.

The idea: instead of shell scripts, Makefiles, or Python scripts, write your build tasks in Rust. It's cross-platform, type-checked, and uses the same language your team already knows.

### Setting Up xtask

```
my-project/
├── Cargo.toml          # workspace root
├── xtask/
│   ├── Cargo.toml
│   └── src/
│       └── main.rs
├── core/
│   ├── Cargo.toml
│   └── src/
├── server/
│   ├── Cargo.toml
│   └── src/
└── .cargo/
    └── config.toml     # alias: xtask = "run --package xtask --"
```

```toml
# .cargo/config.toml
[alias]
xtask = "run --package xtask --"
```

```toml
# xtask/Cargo.toml
[package]
name = "xtask"
version = "0.1.0"
edition = "2021"

# xtask should never be published
publish = false

[dependencies]
clap = { version = "4", features = ["derive"] }
anyhow = "1.0"
xshell = "0.2"    # Ergonomic shell commands
serde = { version = "1", features = ["derive"] }
serde_yaml = "0.9"
```

```rust
// xtask/src/main.rs
use anyhow::Result;
use clap::{Parser, Subcommand};
use xshell::{cmd, Shell};

#[derive(Parser)]
#[command(name = "xtask", about = "Project build tasks")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Generate code from schemas
    Codegen,
    /// Run all checks (clippy, tests, formatting)
    Ci,
    /// Build release binaries for distribution
    Dist,
    /// Generate and open documentation
    Docs,
    /// Run database migrations
    Migrate,
}

fn main() -> Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Command::Codegen => codegen()?,
        Command::Ci => ci()?,
        Command::Dist => dist()?,
        Command::Docs => docs()?,
        Command::Migrate => migrate()?,
    }

    Ok(())
}

fn codegen() -> Result<()> {
    let sh = Shell::new()?;

    println!("Generating API types from schema...");
    generate_api_types()?;

    println!("Generating SQL query functions...");
    generate_sql_queries(&sh)?;

    println!("Formatting generated code...");
    cmd!(sh, "cargo fmt --all").run()?;

    println!("Done!");
    Ok(())
}

fn ci() -> Result<()> {
    let sh = Shell::new()?;

    println!("==> Checking formatting...");
    cmd!(sh, "cargo fmt --all -- --check").run()?;

    println!("==> Running clippy...");
    cmd!(sh, "cargo clippy --workspace --all-features --all-targets -- -D warnings").run()?;

    println!("==> Running tests...");
    cmd!(sh, "cargo test --workspace --all-features").run()?;

    println!("==> Running doc tests...");
    cmd!(sh, "cargo test --workspace --doc").run()?;

    println!("All checks passed!");
    Ok(())
}

fn dist() -> Result<()> {
    let sh = Shell::new()?;

    let targets = vec![
        "x86_64-unknown-linux-musl",
        "aarch64-unknown-linux-musl",
        "x86_64-apple-darwin",
        "aarch64-apple-darwin",
    ];

    for target in targets {
        println!("Building for {target}...");
        cmd!(sh, "cargo build --release --target {target} --package server").run()?;

        // Copy binary to dist/
        let binary = format!("target/{target}/release/server");
        let dest = format!("dist/server-{target}");
        cmd!(sh, "cp {binary} {dest}").run()?;
    }

    println!("Distribution binaries in dist/");
    Ok(())
}

fn docs() -> Result<()> {
    let sh = Shell::new()?;
    cmd!(sh, "cargo doc --workspace --no-deps --open").run()?;
    Ok(())
}

fn migrate() -> Result<()> {
    let sh = Shell::new()?;
    cmd!(sh, "cargo run --package migrations -- up").run()?;
    Ok(())
}

fn generate_api_types() -> Result<()> {
    let spec: serde_yaml::Value = serde_yaml::from_str(
        &std::fs::read_to_string("schemas/api.yaml")?
    )?;

    let mut output = String::from(
        "// AUTO-GENERATED — do not edit manually.\n\
         // Re-generate with: cargo xtask codegen\n\n\
         use serde::{Serialize, Deserialize};\n\n"
    );

    // ... generation logic ...

    std::fs::write("core/src/generated/api_types.rs", output)?;
    Ok(())
}

fn generate_sql_queries(sh: &Shell) -> Result<()> {
    // Use sqlc or similar to generate query functions
    cmd!(sh, "sqlc generate").run()?;
    Ok(())
}
```

Now your team uses:

```bash
cargo xtask codegen    # generate code from schemas
cargo xtask ci         # run all checks
cargo xtask dist       # build release binaries
cargo xtask docs       # generate and open docs
cargo xtask migrate    # run database migrations
```

### Why xtask Over build.rs for Complex Tasks

- **Explicit invocation.** `cargo xtask codegen` runs when you ask, not on every build. build.rs runs automatically, which is great for small things but terrible for slow operations.
- **Generated code is visible.** The xtask writes files into your source tree, so your IDE sees them. Full autocomplete, go-to-definition, the works.
- **Debuggable.** It's a normal Rust binary. You can `println!`, use a debugger, write tests for your generation logic.
- **No `OUT_DIR` nonsense.** The generated code lives where you'd expect.

### The Tradeoff

The downside: generated files need to be committed to git (or regenerated in CI). If someone updates the schema but forgets to run `cargo xtask codegen`, the generated code will be stale. You can catch this in CI:

```yaml
# CI step: verify codegen is up to date
- name: Check codegen
  run: |
    cargo xtask codegen
    git diff --exit-code -- core/src/generated/
    if [ $? -ne 0 ]; then
      echo "Generated code is out of date. Run 'cargo xtask codegen' and commit."
      exit 1
    fi
```

## Choosing the Right Approach

Here's my decision tree:

1. **Deriving trait impls or reducing struct boilerplate?** → Proc macro
2. **Generating code from external files that change infrequently?** → xtask
3. **Injecting build metadata (git hash, version)?** → build.rs
4. **Generating code from external files on every build?** → build.rs
5. **Complex build orchestration (multi-target, packaging)?** → xtask
6. **Quick one-off code generation?** → build.rs

In practice, most projects use a combination. build.rs for git metadata and small generated files, proc macros for derive implementations, and xtask for everything else.

The key insight: code generation should be boring. If your code generation process is complex, fragile, or hard to understand, the meta-complexity will slow your team down more than the boilerplate it eliminates. Start simple, automate what hurts, and only reach for the fancy tools when the simple ones aren't enough.

Next lesson: managing all of this at scale with workspace-based monorepos.
