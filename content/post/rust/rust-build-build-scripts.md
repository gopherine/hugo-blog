---
title: "Lesson 2: build.rs — Code generation at compile time"
author: Atharva Pandey
series: "Rust Compiler Plugins & Build System"
lesson: 2
keywords: [Rust, rust, cargo, build system, build.rs, build scripts, code generation, compile time]
tags: [Rust tutorial, rust, cargo, build-system]
date: '2025-08-03T14:30:00.000Z'
---

The first time I needed `build.rs`, I was wrapping a C library that had about 200 constants defined in a header file. I could've copied them all by hand into Rust `const` declarations. Instead, I wrote a build script that parsed the header and generated the constants automatically. Took 30 minutes to write the build script, and it saved me from maintaining a manual mapping that would've drifted out of sync within a month.

## What Is build.rs?

A build script is a Rust source file named `build.rs` that sits at the root of your crate (next to `Cargo.toml`). Cargo compiles and runs it *before* compiling your crate. Whatever it prints to stdout using special `cargo:` directives tells Cargo what to do — set environment variables, link native libraries, rerun when files change, or generate source files.

```
my-crate/
├── Cargo.toml
├── build.rs          ← compiled and run first
└── src/
    └── lib.rs        ← compiled second, can use build script output
```

The simplest possible build script:

```rust
// build.rs
fn main() {
    println!("cargo:rerun-if-changed=build.rs");
}
```

That `rerun-if-changed` directive tells Cargo: "Only re-run this build script if `build.rs` itself changes." Without it, Cargo runs your build script on every single build, which can slow things down.

## Cargo Directives

Build scripts communicate with Cargo through `println!` statements with specific prefixes. Here are the ones you'll actually use:

```rust
fn main() {
    // Set an environment variable accessible via env!() in your code
    println!("cargo:rustc-env=BUILD_TIMESTAMP={}", chrono::Utc::now());

    // Tell rustc to link a native library
    println!("cargo:rustc-link-lib=sqlite3");

    // Add a directory to the native library search path
    println!("cargo:rustc-link-search=native=/usr/local/lib");

    // Pass a cfg flag — usable with #[cfg(flag_name)]
    println!("cargo:rustc-cfg=has_avx2");

    // Only rerun if these files change
    println!("cargo:rerun-if-changed=wrapper.h");
    println!("cargo:rerun-if-changed=src/generated/");

    // Rerun if an environment variable changes
    println!("cargo:rerun-if-env-changed=DATABASE_URL");

    // Emit a compiler warning
    println!("cargo:warning=Using legacy configuration format");
}
```

The `rerun-if-changed` directive is critical for build performance. If you don't specify it, Cargo assumes your build script depends on everything and reruns it constantly. If you specify even one `rerun-if-changed`, Cargo switches to "only run when these specific things change" mode.

## Injecting Build Metadata

The most common use case I see — and the one I'd recommend starting with — is injecting build-time information into your binary.

```rust
// build.rs
use std::process::Command;

fn main() {
    println!("cargo:rerun-if-changed=.git/HEAD");
    println!("cargo:rerun-if-changed=.git/refs/");

    // Git commit hash
    let output = Command::new("git")
        .args(["rev-parse", "--short", "HEAD"])
        .output()
        .expect("failed to execute git");
    let git_hash = String::from_utf8(output.stdout)
        .unwrap()
        .trim()
        .to_string();
    println!("cargo:rustc-env=GIT_HASH={git_hash}");

    // Git branch
    let output = Command::new("git")
        .args(["rev-parse", "--abbrev-ref", "HEAD"])
        .output()
        .expect("failed to execute git");
    let git_branch = String::from_utf8(output.stdout)
        .unwrap()
        .trim()
        .to_string();
    println!("cargo:rustc-env=GIT_BRANCH={git_branch}");

    // Build timestamp
    let timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    println!("cargo:rustc-env=BUILD_TIMESTAMP={timestamp}");

    // Rust compiler version
    let output = Command::new("rustc")
        .arg("--version")
        .output()
        .expect("failed to get rustc version");
    let rustc_version = String::from_utf8(output.stdout)
        .unwrap()
        .trim()
        .to_string();
    println!("cargo:rustc-env=RUSTC_VERSION={rustc_version}");
}
```

Then in your application:

```rust
fn print_build_info() {
    println!("Version: {} ({})", env!("CARGO_PKG_VERSION"), env!("GIT_HASH"));
    println!("Branch: {}", env!("GIT_BRANCH"));
    println!("Built with: {}", env!("RUSTC_VERSION"));
    println!("Build timestamp: {}", env!("BUILD_TIMESTAMP"));
}

// Or use it in a CLI --version flag
fn version_string() -> String {
    format!(
        "{} {} ({} {})",
        env!("CARGO_PKG_NAME"),
        env!("CARGO_PKG_VERSION"),
        env!("GIT_HASH"),
        env!("GIT_BRANCH"),
    )
}
```

Every production binary I ship includes this. When someone reports a bug, the first thing I ask is "what does `--version` say?" Having the exact git hash right there saves enormous debugging time.

## Generating Code

This is where build scripts get really powerful. You can generate Rust source files that get compiled into your crate.

The convention is to write generated files into `OUT_DIR` — an environment variable Cargo sets to a build-specific temporary directory.

```rust
// build.rs
use std::env;
use std::fs;
use std::path::Path;

fn main() {
    let out_dir = env::var("OUT_DIR").unwrap();
    let dest_path = Path::new(&out_dir).join("error_codes.rs");

    // Generate error code constants from a CSV or config file
    let error_definitions = vec![
        ("NOT_FOUND", 404, "Resource not found"),
        ("UNAUTHORIZED", 401, "Authentication required"),
        ("FORBIDDEN", 403, "Insufficient permissions"),
        ("CONFLICT", 409, "Resource conflict"),
        ("RATE_LIMITED", 429, "Too many requests"),
        ("INTERNAL", 500, "Internal server error"),
    ];

    let mut code = String::new();
    code.push_str("/// Auto-generated error codes. Do not edit.\n\n");

    for (name, status, description) in &error_definitions {
        code.push_str(&format!(
            r#"pub const ERR_{name}: ErrorCode = ErrorCode {{
    name: "{name}",
    status: {status},
    description: "{description}",
}};
"#
        ));
    }

    // Generate an array of all error codes
    let names: Vec<String> = error_definitions
        .iter()
        .map(|(name, _, _)| format!("ERR_{name}"))
        .collect();
    code.push_str(&format!(
        "\npub const ALL_ERROR_CODES: &[ErrorCode] = &[{}];\n",
        names.join(", ")
    ));

    fs::write(&dest_path, code).unwrap();

    println!("cargo:rerun-if-changed=build.rs");
}
```

Then include the generated file:

```rust
// src/errors.rs

pub struct ErrorCode {
    pub name: &'static str,
    pub status: u16,
    pub description: &'static str,
}

// Include the generated code
include!(concat!(env!("OUT_DIR"), "/error_codes.rs"));

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn error_codes_exist() {
        assert_eq!(ERR_NOT_FOUND.status, 404);
        assert_eq!(ERR_UNAUTHORIZED.status, 401);
        assert!(ALL_ERROR_CODES.len() >= 6);
    }
}
```

That `include!` macro is the bridge between generated code and your crate. It literally inserts the contents of the file at that point in your source.

## Generating Code from External Schemas

A more realistic scenario — generating Rust types from a protobuf schema, a JSON schema, or a SQL migration:

```rust
// build.rs
use std::env;
use std::fs;
use std::path::Path;

fn main() {
    let out_dir = env::var("OUT_DIR").unwrap();

    // Read a schema definition file
    let schema = fs::read_to_string("schema/api.json")
        .expect("Failed to read schema file");

    let schema: serde_json::Value = serde_json::from_str(&schema)
        .expect("Failed to parse schema");

    let mut generated = String::new();
    generated.push_str("use serde::{Serialize, Deserialize};\n\n");

    // Generate structs from JSON schema definitions
    if let Some(definitions) = schema["definitions"].as_object() {
        for (name, def) in definitions {
            generated.push_str(&format!(
                "#[derive(Debug, Clone, Serialize, Deserialize)]\n"
            ));
            generated.push_str(&format!("pub struct {name} {{\n"));

            if let Some(properties) = def["properties"].as_object() {
                for (field, field_def) in properties {
                    let rust_type = json_type_to_rust(
                        field_def["type"].as_str().unwrap_or("string")
                    );
                    generated.push_str(&format!("    pub {field}: {rust_type},\n"));
                }
            }

            generated.push_str("}\n\n");
        }
    }

    let dest = Path::new(&out_dir).join("schema_types.rs");
    fs::write(dest, generated).unwrap();

    println!("cargo:rerun-if-changed=schema/api.json");
}

fn json_type_to_rust(json_type: &str) -> &str {
    match json_type {
        "string" => "String",
        "integer" => "i64",
        "number" => "f64",
        "boolean" => "bool",
        _ => "serde_json::Value",
    }
}
```

## Linking Native Libraries

If you're wrapping a C or C++ library, build scripts handle the linking configuration:

```rust
// build.rs
fn main() {
    // Link against a system library
    println!("cargo:rustc-link-lib=ssl");
    println!("cargo:rustc-link-lib=crypto");

    // For static linking
    println!("cargo:rustc-link-lib=static=mylib");
    println!("cargo:rustc-link-search=native=vendor/lib");

    // Use pkg-config for system libraries (much more robust)
    // Requires the `pkg-config` crate as a build dependency
    let lib = pkg_config::Config::new()
        .atleast_version("1.1.0")
        .probe("openssl")
        .expect("OpenSSL not found");

    for path in lib.include_paths {
        println!("cargo:include={}", path.display());
    }

    println!("cargo:rerun-if-changed=build.rs");
}
```

Your `Cargo.toml` needs build dependencies declared separately:

```toml
[build-dependencies]
pkg-config = "0.3"
serde_json = "1.0"
cc = "1.0"
```

Note the `[build-dependencies]` section — these are dependencies for your build script, not your crate itself. They don't end up in your final binary.

## Compiling C Code with the `cc` Crate

Sometimes you need to compile C source files as part of your build. The `cc` crate makes this straightforward:

```rust
// build.rs
fn main() {
    cc::Build::new()
        .file("src/fast_hash.c")
        .file("src/simd_utils.c")
        .include("vendor/include")
        .flag("-O3")
        .flag("-mavx2")
        .compile("fast_hash");

    println!("cargo:rerun-if-changed=src/fast_hash.c");
    println!("cargo:rerun-if-changed=src/simd_utils.c");
    println!("cargo:rerun-if-changed=vendor/include/");
}
```

The `cc` crate handles cross-compilation, finds the right compiler for the target platform, and sets up all the right flags. It's vastly better than trying to shell out to `gcc` manually.

## Common Pitfalls

### Don't Do Too Much in build.rs

Build scripts run on every build (unless you're careful with `rerun-if-changed`). I've seen build scripts that download files from the internet, run database migrations, or compile entire C++ libraries from source. Every one of these was a debugging nightmare.

Keep build scripts focused. If you need complex build orchestration, use an `xtask` pattern instead (we'll cover that in Lesson 5).

### The OUT_DIR Trap

Generated files in `OUT_DIR` are in a temporary directory that changes between builds and is invisible to your IDE. This means no autocomplete on generated types, no go-to-definition, nothing. It's a real pain for large generated APIs.

One workaround: generate the files into a checked-in directory during development, and only use `OUT_DIR` in CI:

```rust
// build.rs
fn main() {
    let out_dir = if std::env::var("CI").is_ok() {
        std::env::var("OUT_DIR").unwrap()
    } else {
        "src/generated".to_string()
    };
    // Generate into the chosen directory...
}
```

### Determinism Matters

Your build script should produce the same output given the same inputs. Don't include timestamps or random values in generated code unless you explicitly need them — it breaks incremental compilation and caching.

```rust
// Bad: non-deterministic output
println!("cargo:rustc-env=BUILD_ID={}", uuid::Uuid::new_v4());

// Good: deterministic based on inputs
let content_hash = hash_file("schema/api.json");
println!("cargo:rustc-env=SCHEMA_HASH={content_hash}");
```

## A Complete, Production-Ready build.rs

Here's a build script I actually use, combining several of these patterns:

```rust
// build.rs
use std::env;
use std::fs;
use std::path::Path;
use std::process::Command;

fn main() {
    set_git_info();
    generate_sql_queries();
    set_rerun_conditions();
}

fn set_git_info() {
    let hash = run_command("git", &["rev-parse", "--short", "HEAD"]);
    let branch = run_command("git", &["rev-parse", "--abbrev-ref", "HEAD"]);
    let dirty = run_command("git", &["status", "--porcelain"]);

    let hash = if dirty.is_empty() {
        hash
    } else {
        format!("{hash}-dirty")
    };

    println!("cargo:rustc-env=GIT_HASH={hash}");
    println!("cargo:rustc-env=GIT_BRANCH={branch}");
}

fn generate_sql_queries() {
    let out_dir = env::var("OUT_DIR").unwrap();
    let queries_dir = Path::new("queries");

    if !queries_dir.exists() {
        return;
    }

    let mut generated = String::from("// Auto-generated SQL query constants\n\n");

    for entry in fs::read_dir(queries_dir).unwrap() {
        let entry = entry.unwrap();
        let path = entry.path();

        if path.extension().map_or(false, |ext| ext == "sql") {
            let name = path.file_stem().unwrap().to_str().unwrap();
            let const_name = name.to_uppercase().replace('-', "_");
            let sql = fs::read_to_string(&path).unwrap();

            generated.push_str(&format!(
                "pub const {const_name}: &str = r#\"{sql}\"#;\n\n"
            ));
        }
    }

    fs::write(Path::new(&out_dir).join("queries.rs"), generated).unwrap();
}

fn set_rerun_conditions() {
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed=queries/");
    println!("cargo:rerun-if-changed=.git/HEAD");
    println!("cargo:rerun-if-changed=.git/refs/");
}

fn run_command(cmd: &str, args: &[&str]) -> String {
    Command::new(cmd)
        .args(args)
        .output()
        .map(|o| String::from_utf8(o.stdout).unwrap_or_default().trim().to_string())
        .unwrap_or_default()
}
```

Build scripts are one of Cargo's most powerful escape hatches. They let you do things at compile time that most languages can only dream of. But with great power comes the responsibility to keep them fast, deterministic, and well-documented. Your teammates will thank you.

Next lesson, we'll dive into conditional compilation with `cfg` — the other side of the coin from features.
