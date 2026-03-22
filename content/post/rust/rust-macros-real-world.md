---
title: "Lesson 11: Real-World Macro Patterns — serde, clap, sqlx under the hood"
author: Atharva Pandey
series: "Rust Macros & Metaprogramming"
lesson: 11
keywords: [Rust, rust, macros, serde, clap, sqlx, real-world, proc-macro, derive]
tags: [Rust tutorial, rust, macros, metaprogramming]
date: '2025-03-01T15:40:00.000Z'
---

I used `#[derive(Serialize)]` for two years before I actually looked at what it generates. When I finally ran `cargo expand` on a struct with five fields, I got 150 lines of serialization code — visitor patterns, generic bounds, field-by-field traversal, error handling. All generated from a single line. Understanding how production crates use macros changed how I think about API design. These aren't academic exercises — they're the patterns behind the most downloaded crates in the ecosystem.

## serde: The Gold Standard

`serde` is the most depended-upon crate in Rust's ecosystem. Its macro system is also one of the most sophisticated. Let's unpack what `#[derive(Serialize)]` actually does.

### What Gets Generated

Given this struct:

```rust
use serde::Serialize;

#[derive(Serialize)]
struct User {
    name: String,
    #[serde(rename = "user_age")]
    age: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    email: Option<String>,
}
```

The derive macro generates an `impl Serialize for User` that:

1. Calls `serializer.serialize_struct("User", N)` where N is the number of non-skipped fields
2. For each field, calls `state.serialize_field("field_name", &self.field)`
3. Respects attributes — `rename` changes the serialized field name, `skip_serializing_if` adds a conditional check
4. Handles `Option` fields, default values, flattening, and dozens of other configurations

The expanded code looks roughly like this (simplified):

```rust
impl serde::Serialize for User {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        use serde::ser::SerializeStruct;

        // Count non-skipped fields at runtime
        let mut field_count = 2; // name and age are always present
        if self.email.is_some() {
            field_count += 1;
        }

        let mut state = serializer.serialize_struct("User", field_count)?;
        state.serialize_field("name", &self.name)?;
        state.serialize_field("user_age", &self.age)?; // renamed!
        if let Some(ref value) = self.email {
            state.serialize_field("email", value)?;
        }
        state.end()
    }
}
```

### How serde's Macro Architecture Works

`serde` uses a clever two-crate pattern:

- **`serde`** — the core crate with the `Serialize`/`Deserialize` traits
- **`serde_derive`** — the proc-macro crate with the derive implementations

When you write `#[derive(Serialize)]`, Rust looks for the proc macro. `serde` re-exports it using `#[cfg_attr]`:

```rust
// In serde's lib.rs
#[cfg(feature = "derive")]
#[allow(unused_imports)]
#[macro_use]
extern crate serde_derive;
#[cfg(feature = "derive")]
pub use serde_derive::*;
```

This means users only depend on `serde` with `features = ["derive"]` — they don't need to know about `serde_derive` at all.

### The Attribute System

serde supports over 50 container-level and field-level attributes. The macro parses these from the field's `attrs` in `syn`:

```rust
// Simplified version of how serde parses attributes
fn parse_serde_attrs(attrs: &[Attribute]) -> FieldConfig {
    let mut config = FieldConfig::default();
    for attr in attrs {
        if attr.path().is_ident("serde") {
            attr.parse_nested_meta(|meta| {
                if meta.path.is_ident("rename") {
                    let value = meta.value()?;
                    let s: LitStr = value.parse()?;
                    config.rename = Some(s.value());
                } else if meta.path.is_ident("skip") {
                    config.skip = true;
                } else if meta.path.is_ident("default") {
                    config.has_default = true;
                }
                // ... dozens more
                Ok(())
            })?;
        }
    }
    config
}
```

The key insight: serde's macro doesn't just generate code — it maintains a rich intermediate representation of the struct's serialization behavior, configured through attributes, and then generates the appropriate code from that representation. The parsing and generation phases are cleanly separated.

### Lessons from serde

1. **Rich attribute DSL.** Don't make users write code when an attribute will do. `#[serde(rename = "name")]` is cleaner than implementing a trait method.
2. **Separate crate for the derive.** The `serde` + `serde_derive` split is the standard pattern. It lets the core crate be light and fast to compile when macros aren't needed.
3. **Exhaustive documentation.** serde documents every attribute with examples. If your macro has configuration options, document them thoroughly.

## clap: Declarative CLI Parsing

`clap` takes a different approach — it uses derive macros to turn a struct into a CLI argument parser.

### What It Generates

```rust
use clap::Parser;

#[derive(Parser, Debug)]
#[command(name = "myapp", about = "A demo application")]
struct Args {
    /// The name to greet
    #[arg(short, long)]
    name: String,

    /// Number of times to greet
    #[arg(short, long, default_value_t = 1)]
    count: u32,

    /// Whether to use uppercase
    #[arg(long)]
    uppercase: bool,
}

fn main() {
    let args = Args::parse();
    for _ in 0..args.count {
        if args.uppercase {
            println!("HELLO, {}!", args.name.to_uppercase());
        } else {
            println!("Hello, {}!", args.name);
        }
    }
}
```

The `#[derive(Parser)]` macro generates:

1. An `impl clap::FromArgMatches for Args` that extracts values from parsed CLI arguments
2. An `impl clap::Args for Args` that defines the arguments
3. An `impl clap::CommandFactory for Args` that builds the clap `Command` with all the metadata
4. The `parse()` method on `Args` that ties it all together

### clap's Attribute Pattern

clap demonstrates a different attribute approach from serde. It uses multiple attribute namespaces:

- `#[command(...)]` — container-level settings (app name, version, about)
- `#[arg(...)]` — field-level settings (short, long, default, help)
- `#[group(...)]` — argument grouping

Each namespace has its own set of valid keys. The macro validates these at compile time — use an invalid key and you get a clear error.

```rust
#[derive(Parser)]
struct Config {
    /// Database connection URL
    #[arg(long, env = "DATABASE_URL")]
    database_url: String,

    /// Server port
    #[arg(short = 'p', long, default_value_t = 8080, value_parser = clap::value_parser!(u16).range(1..))]
    port: u16,

    /// Log level
    #[arg(long, value_enum, default_value_t = LogLevel::Info)]
    log_level: LogLevel,
}

#[derive(Clone, Debug, clap::ValueEnum)]
enum LogLevel {
    Debug,
    Info,
    Warn,
    Error,
}
```

### Lessons from clap

1. **Use doc comments as documentation.** clap uses `///` doc comments as help text for CLI arguments. This is brilliant — the same comments that help developers reading the code also appear in `--help` output.
2. **Separate namespaces for attributes.** Instead of cramming everything into `#[clap(...)]`, clap uses `#[command]`, `#[arg]`, and `#[group]`. This makes each attribute's purpose clear.
3. **ValueEnum derive for enums.** clap provides a separate derive macro that turns an enum into valid CLI values. Composing multiple small derive macros is cleaner than one monolithic macro.

## sqlx: Compile-Time SQL

`sqlx` does something that still amazes me every time I think about it. Its `query!` macro connects to your actual database during compilation to validate SQL and generate type-safe code.

### How query! Works

```rust
let users = sqlx::query_as!(
    User,
    "SELECT id, name, email FROM users WHERE age > $1",
    21
)
.fetch_all(&pool)
.await?;
```

At compile time, this macro:

1. Connects to the database specified by `DATABASE_URL` environment variable
2. Sends the query to the database's `PREPARE` endpoint
3. Gets back the parameter types and result column types
4. Verifies that `$1` (the parameter) matches the type of `21`
5. Verifies that the `User` struct has fields matching the result columns
6. Generates a type-safe query execution function

If you mistype a column name, you get a *compile error*, not a runtime error. If the column types don't match your struct fields, you get a *compile error*.

### The Offline Mode

Running a database during compilation isn't always practical (CI, offline development). sqlx solves this with "offline mode":

```bash
cargo sqlx prepare
```

This runs all your queries, caches the type information in a JSON file (`.sqlx/`), and subsequent builds use the cached data instead of a live connection.

```rust
// .sqlx/query-a1b2c3d4.json
{
  "query": "SELECT id, name, email FROM users WHERE age > $1",
  "describe": {
    "columns": [
      { "name": "id", "type_info": "INT4" },
      { "name": "name", "type_info": "TEXT" },
      { "name": "email", "type_info": "TEXT" }
    ],
    "parameters": ["INT4"]
  }
}
```

### Lessons from sqlx

1. **Compile-time external validation is possible** — and sometimes worth the complexity. Catching SQL errors before your code runs saves hours of debugging.
2. **Provide fallback modes.** The offline mode makes the macro practical in environments where a database isn't available.
3. **Function-like macros for DSLs.** SQL isn't Rust syntax, so a function-like macro is the right choice. The input gets parsed by the macro, not by the Rust parser.

## thiserror and anyhow: Error Macros

`thiserror` uses derive macros to generate `Display` and `Error` implementations:

```rust
use thiserror::Error;

#[derive(Error, Debug)]
enum AppError {
    #[error("database error: {0}")]
    Database(#[from] sqlx::Error),

    #[error("not found: {name}")]
    NotFound { name: String },

    #[error("unauthorized")]
    Unauthorized,
}
```

The `#[error("...")]` attribute uses a format-string-like syntax. The macro parses it, extracts the interpolation points (`{0}`, `{name}`), and generates the corresponding `Display` implementation:

```rust
// Generated (simplified)
impl std::fmt::Display for AppError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            AppError::Database(e) => write!(f, "database error: {}", e),
            AppError::NotFound { name } => write!(f, "not found: {}", name),
            AppError::Unauthorized => write!(f, "unauthorized"),
        }
    }
}
```

The `#[from]` attribute generates a `From` implementation, so `sqlx::Error` automatically converts to `AppError::Database`.

### Pattern: Mini-Language in Attributes

`thiserror`'s `#[error("...")]` is a pattern you see across the ecosystem — a mini-language embedded in a string literal attribute. The derive macro parses the string at compile time and generates code based on its contents.

Other examples:
- `#[serde(deserialize_with = "...")]` — function path in a string
- `#[clap(value_parser = ...)]` — expression in an attribute
- `#[sqlx(rename_all = "snake_case")]` — enum value in a string

## tokio: The #[tokio::main] Attribute

```rust
#[tokio::main]
async fn main() {
    // async code here
}
```

This attribute macro transforms your async main function into:

```rust
fn main() {
    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .unwrap()
        .block_on(async {
            // your async code here
        })
}
```

It takes your async function body, wraps it in a runtime builder, and produces a synchronous `main` that drives the runtime. Simple concept, but it eliminates the boilerplate that every async Rust program would otherwise need.

## Patterns Worth Stealing

After studying these crates, here are the patterns I use in my own macros:

**The trait + derive crate split.** Define your trait in crate A, the derive macro in crate A-derive, and re-export from A. Users see one dependency.

**Attribute-driven configuration.** Instead of complex macro syntax, use attributes on fields and items. They're familiar to Rust developers and well-supported by IDEs.

**Error messages as a feature.** Invest time in producing clear, well-spanned error messages. A macro with bad error messages is worse than no macro — users will waste time debugging the tool instead of their code.

**Compile-time validation.** If you can check something at compile time, do it. This is the entire point of macros — moving work from runtime to compile time.

**Documentation with examples.** Every public macro attribute should have a doc comment with a working example. Users learn by copying, and if your examples don't compile, nobody will use your macro.

Next and final lesson: macro anti-patterns. When not to reach for macros, and the mistakes I've seen (and made) that turned macro-heavy codebases into maintenance nightmares.
