---
title: "Lesson 4: Custom Lints with clippy and dylint — Your team's rules"
author: Atharva Pandey
series: "Rust Compiler Plugins & Build System"
lesson: 4
keywords: [Rust, rust, cargo, build system, clippy, dylint, custom lints, static analysis, code quality]
tags: [Rust tutorial, rust, cargo, build-system]
date: '2025-08-07T16:20:00.000Z'
---

We had this rule on my old team: never use `.unwrap()` on database query results. We wrote it in our contributing guide. We mentioned it in code reviews. We added it to the onboarding doc. And yet, every single sprint, someone would push an `.unwrap()` on a `sqlx::Result` that would blow up in production at 2 AM. That's when I decided to make the compiler enforce our rules instead of relying on humans to remember them.

## Clippy: Your First Line of Defense

Before writing custom lints, you should squeeze everything you can out of Clippy. Most teams aren't using even half of what it offers.

Clippy has over 700 lints organized into groups:

```bash
cargo clippy                                    # default lints
cargo clippy -- -W clippy::pedantic             # enable pedantic lints
cargo clippy -- -W clippy::nursery              # experimental but useful
cargo clippy -- -D warnings                     # treat all warnings as errors
cargo clippy -- -D clippy::unwrap_used          # ban unwrap specifically
```

### Configuring Clippy Project-Wide

Instead of passing flags every time, configure Clippy in your `Cargo.toml` or a `clippy.toml` file:

```toml
# Cargo.toml — lint configuration (Rust 1.74+)
[lints.clippy]
# Deny these — they're always wrong in our codebase
unwrap_used = "deny"
expect_used = "deny"
panic = "deny"
todo = "deny"
dbg_macro = "deny"
unimplemented = "deny"
print_stdout = "deny"      # use tracing, not println
print_stderr = "deny"

# Warn on these — fix when you can
large_enum_variant = "warn"
needless_pass_by_value = "warn"
redundant_clone = "warn"
inefficient_to_string = "warn"
manual_string_new = "warn"

# Enable pedantic group, but allow specific noisy ones
pedantic = { level = "warn", priority = -1 }
module_name_repetitions = "allow"
must_use_candidate = "allow"
missing_errors_doc = "allow"
```

```toml
# clippy.toml — fine-grained configuration
# Maximum number of lines in a function before clippy warns
too-many-lines-threshold = 100

# Maximum cognitive complexity
cognitive-complexity-threshold = 25

# Types that are considered expensive to clone
avoid-breaking-exported-api = true

# Maximum number of arguments
too-many-arguments-threshold = 7

# Minimum number of struct fields for a "large struct"
max-struct-bools = 3
```

### Workspace-Wide Lint Configuration

For workspaces, you can set lints at the workspace level:

```toml
# Root Cargo.toml
[workspace.lints.clippy]
unwrap_used = "deny"
expect_used = "deny"
panic = "deny"

[workspace.lints.rust]
unsafe_code = "deny"
missing_docs = "warn"
```

```toml
# Member Cargo.toml
[lints]
workspace = true
```

Every crate in the workspace inherits the same lint rules. No more inconsistency between crates.

### Clippy Allow Annotations

When you need to override a lint, always explain why:

```rust
// Good: explains the reasoning
#[allow(clippy::unwrap_used)] // Config is validated at startup; this can't fail
let port = config.port.parse::<u16>().unwrap();

// Bad: no explanation
#[allow(clippy::unwrap_used)]
let port = config.port.parse::<u16>().unwrap();
```

You can even require explanations with:

```toml
[lints.clippy]
allow_attributes_without_reason = "warn"
```

Now Clippy warns on any `#[allow(...)]` that doesn't have a reason comment.

## When Clippy Isn't Enough

Clippy covers generic Rust best practices. But every codebase has its own rules:

- "Never call `database::query` outside of a `Repository` impl"
- "All public API types must implement `Debug`"
- "Don't use `String` for user IDs — use `UserId`"
- "Async functions must have a timeout"
- "Never construct `HttpResponse` directly — use our response builder"

These are domain-specific rules that Clippy can't know about. That's where custom lints come in.

## dylint: Custom Lints Without Forking Clippy

Dylint is a tool for running custom Rust lints packaged as dynamic libraries. You write a lint as a separate crate, compile it to a `.so`/`.dylib`, and dylint loads it at analysis time.

### Setting Up a dylint Lint Library

First, install dylint:

```bash
cargo install cargo-dylint dylint-link
```

Create a new lint library:

```bash
cargo new --lib my-lints
cd my-lints
```

```toml
# my-lints/Cargo.toml
[package]
name = "my-lints"
version = "0.1.0"
edition = "2021"

[lib]
crate-type = ["cdylib"]

[dependencies]
clippy_utils = "0.1"
dylint_linting = "3.0"

[dev-dependencies]
dylint_testing = "3.0"

[package.metadata.rust-analyzer]
rustc_private = true
```

### Writing Your First Custom Lint

Let's implement that "no `.unwrap()` on database results" rule:

```rust
// my-lints/src/lib.rs

#![feature(rustc_private)]

extern crate rustc_ast;
extern crate rustc_hir;
extern crate rustc_lint;
extern crate rustc_middle;
extern crate rustc_span;

use clippy_utils::diagnostics::span_lint_and_help;
use clippy_utils::ty::match_type;
use rustc_hir::{Expr, ExprKind};
use rustc_lint::{LateContext, LateLintPass, LintArray, LintPass};
use rustc_middle::ty;

dylint_linting::declare_late_lint! {
    /// ### What it does
    /// Detects `.unwrap()` calls on `sqlx::Result` types.
    ///
    /// ### Why is this bad?
    /// Database operations can fail for many reasons (connection issues,
    /// constraint violations, timeouts). Unwrapping these results causes
    /// panics in production.
    ///
    /// ### Known issues
    /// May have false positives on non-sqlx Result types named similarly.
    ///
    /// ### Example
    /// ```rust
    /// // Bad
    /// let user = sqlx::query_as!(User, "SELECT * FROM users WHERE id = $1", id)
    ///     .fetch_one(&pool)
    ///     .await
    ///     .unwrap();
    ///
    /// // Good
    /// let user = sqlx::query_as!(User, "SELECT * FROM users WHERE id = $1", id)
    ///     .fetch_one(&pool)
    ///     .await?;
    /// ```
    pub NO_DB_UNWRAP,
    Warn,
    "disallow .unwrap() on database query results"
}

impl<'tcx> LateLintPass<'tcx> for NoDbUnwrap {
    fn check_expr(&mut self, cx: &LateContext<'tcx>, expr: &'tcx Expr<'_>) {
        // Look for method calls named "unwrap"
        if let ExprKind::MethodCall(method, receiver, _, span) = expr.kind {
            if method.ident.name.as_str() != "unwrap" {
                return;
            }

            // Check if the receiver type is a Result from sqlx
            let ty = cx.typeck_results().expr_ty(receiver);
            if let ty::Adt(adt_def, substs) = ty.kind() {
                let type_name = format!("{}", ty);
                if type_name.contains("sqlx") || type_name.contains("Result<") {
                    // Check if the error type is from sqlx
                    if let Some(err_ty) = substs.types().nth(1) {
                        let err_name = format!("{}", err_ty);
                        if err_name.contains("sqlx::Error")
                            || err_name.contains("sqlx::error")
                        {
                            span_lint_and_help(
                                cx,
                                NO_DB_UNWRAP,
                                span,
                                "calling `.unwrap()` on a database result",
                                None,
                                "use `?` operator or handle the error explicitly \
                                 with `.map_err()` or `match`",
                            );
                        }
                    }
                }
            }
        }
    }
}

#[test]
fn ui() {
    dylint_testing::ui_test(env!("CARGO_PKG_NAME"), "ui");
}
```

### Testing Custom Lints

Dylint uses UI tests — you write example code and the expected compiler output:

```rust
// my-lints/ui/db_unwrap.rs
// This file should trigger the lint

use sqlx::PgPool;

async fn bad_query(pool: &PgPool) {
    let row = sqlx::query("SELECT 1")
        .fetch_one(pool)
        .await
        .unwrap(); //~ ERROR: calling `.unwrap()` on a database result
}

async fn good_query(pool: &PgPool) -> Result<(), sqlx::Error> {
    let row = sqlx::query("SELECT 1")
        .fetch_one(pool)
        .await?;
    Ok(())
}
```

Run the tests:

```bash
cargo test
```

### Using dylint Lints in Your Project

Add the lint library to your project's configuration:

```toml
# Cargo.toml or .dylint.toml
[workspace.metadata.dylint]
libraries = [
    { git = "https://github.com/your-org/rust-lints", pattern = "my-lints" },
    # Or from a local path during development:
    # { path = "../my-lints" },
]
```

Then run:

```bash
cargo dylint my-lints -- --all-targets
cargo dylint --all -- --all-targets  # run all configured lint libraries
```

## Practical Custom Lint: Enforcing Type Safety

Here's another lint I've found incredibly valuable — catching raw string usage where newtype wrappers should be used:

```rust
dylint_linting::declare_late_lint! {
    /// ### What it does
    /// Detects function parameters named `user_id`, `order_id`, etc.
    /// that use `String` or `&str` instead of proper newtype wrappers.
    ///
    /// ### Why is this bad?
    /// Using raw strings for IDs allows mixing up different kinds of IDs.
    /// A `UserId` should not be accidentally passed where an `OrderId`
    /// is expected.
    pub STRINGLY_TYPED_IDS,
    Warn,
    "use newtype wrappers for ID types instead of raw strings"
}

impl<'tcx> LateLintPass<'tcx> for StringlyTypedIds {
    fn check_fn(
        &mut self,
        cx: &LateContext<'tcx>,
        _kind: rustc_hir::intravisit::FnKind<'tcx>,
        _decl: &'tcx rustc_hir::FnDecl<'_>,
        body: &'tcx rustc_hir::Body<'_>,
        _span: rustc_span::Span,
        _def_id: rustc_hir::def_id::LocalDefId,
    ) {
        for param in body.params {
            let param_name = if let rustc_hir::PatKind::Binding(_, _, ident, _) = param.pat.kind {
                ident.name.as_str().to_string()
            } else {
                continue;
            };

            // Check if parameter name looks like an ID
            let id_patterns = ["_id", "user_id", "order_id", "account_id", "session_id"];
            let is_id_param = id_patterns.iter().any(|p| param_name.ends_with(p));

            if !is_id_param {
                continue;
            }

            // Check if the type is String or &str
            let ty = cx.typeck_results().pat_ty(param.pat);
            let type_str = format!("{}", ty);

            if type_str == "String"
                || type_str == "&str"
                || type_str == "std::string::String"
            {
                span_lint_and_help(
                    cx,
                    STRINGLY_TYPED_IDS,
                    param.span,
                    &format!(
                        "parameter `{}` uses `{}` — consider a newtype wrapper",
                        param_name, type_str
                    ),
                    None,
                    "define a newtype like `struct UserId(String)` for type safety",
                );
            }
        }
    }
}
```

## A Simpler Alternative: Marker-Based Linting with Attributes

If writing compiler plugins feels too heavy, there's a lighter approach using Clippy's `disallowed_methods` and `disallowed_types`:

```toml
# clippy.toml

# Ban specific methods
disallowed-methods = [
    { path = "std::thread::sleep", reason = "Use tokio::time::sleep in async code" },
    { path = "std::env::var", reason = "Use our config module instead" },
    { path = "reqwest::get", reason = "Use HttpClient from our client module" },
    { path = "std::process::exit", reason = "Return from main instead" },
]

# Ban specific types
disallowed-types = [
    { path = "std::collections::HashMap", reason = "Use IndexMap for deterministic iteration" },
    { path = "std::sync::Mutex", reason = "Use parking_lot::Mutex" },
    { path = "std::sync::RwLock", reason = "Use parking_lot::RwLock" },
]
```

This is surprisingly powerful. You don't need to write any lint code — just list the methods and types you want to ban, with reasons. Clippy handles the rest.

```bash
$ cargo clippy
error: use of a disallowed method `std::thread::sleep`
  --> src/worker.rs:42:5
   |
42 |     std::thread::sleep(Duration::from_secs(5));
   |     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
   |
   = note: Use tokio::time::sleep in async code
```

## Integrating Lints into CI

All of this is useless if it doesn't run automatically. Here's my standard CI lint configuration:

```yaml
# .github/workflows/lint.yml
name: Lint
on: [push, pull_request]

jobs:
  clippy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
        with:
          components: clippy
      - run: cargo clippy --workspace --all-features --all-targets -- -D warnings

  dylint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@nightly
        with:
          components: rustc-dev, llvm-tools-preview
      - run: cargo install cargo-dylint dylint-link
      - run: cargo dylint --all -- --all-targets

  deny:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: EmbarkStudios/cargo-deny-action@v1
```

I also recommend `cargo-deny` for dependency auditing — it checks for security advisories, license violations, and duplicate dependencies. Not technically a lint, but it belongs in the same pipeline.

## Building a Lint Culture

The tooling is the easy part. The hard part is getting your team to actually adopt it.

My approach: start permissive, tighten gradually. Begin with `warn` on everything, fix the easy violations, then promote to `deny` once the codebase is clean. If you start with `deny` on day one, people will just add `#[allow(...)]` everywhere and resent the tooling.

Also, write good lint messages. "calling `.unwrap()` on a database result" is okay. "calling `.unwrap()` on a database result — use `?` or handle the error explicitly. Database connections can fail due to timeouts, pool exhaustion, or network issues" is much better. When the compiler explains *why* something is wrong, people learn instead of just complying.

Custom lints turn your team's collective knowledge into automated enforcement. Every bug you catch with a lint is a bug you'll never ship again.

Next, we'll look at code generation strategies — proc macros, build scripts, and xtask patterns for when you need to generate more than just a few constants.
