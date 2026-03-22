---
title: "Lesson 2: Installing Rust — rustup, cargo, and your first build"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 2
keywords: [Rust, rust, rustup, cargo, install rust, rust toolchain, rustc]
tags: [Rust tutorial, rust, beginner]
date: '2024-03-07T14:30:00.000Z'
---

I've installed Rust on maybe fifty machines at this point — Linux servers, Macs, Windows boxes, even a Raspberry Pi. The process has gotten remarkably smooth. Rustup is one of the best toolchain managers in any language ecosystem, and I say that without hesitation.

## Installing rustup

Rustup is the official Rust toolchain installer and manager. It handles downloading the compiler, updating it, and switching between versions. One command gets you everything.

On Linux or macOS, open a terminal and run:

```rust
// Run this in your terminal (not Rust code):
// curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

On Windows, download and run the installer from [rustup.rs](https://rustup.rs).

The installer will ask you about your preferred settings. Just hit Enter for the defaults — they're good. It installs three things:

- **rustc** — the Rust compiler
- **cargo** — the build tool and package manager
- **rustup** — the toolchain manager itself

After installation, you'll need to restart your terminal (or source your shell profile) so the new commands are on your PATH. Then verify:

```rust
// Run these in your terminal:
// rustc --version
// cargo --version
// rustup --version
```

You should see version numbers for all three. If you do, you're good.

## Understanding the Toolchain

Rust releases on a six-week train schedule. There are three release channels:

- **stable** — production-ready, what you should use
- **beta** — next stable release, for testing
- **nightly** — bleeding edge, has experimental features

You installed stable by default. That's correct. Don't use nightly unless you have a specific reason — some crates require it, but fewer and fewer do. I've shipped Rust in production for years without touching nightly.

Updating is trivial:

```rust
// rustup update
```

That's it. Run it every few weeks to stay current.

## Your First cargo Project

Cargo is where you'll spend most of your time. It's the build system, package manager, test runner, and documentation generator all in one. It's opinionated about project structure, and those opinions are good.

Create a new project:

```rust
// cargo new hello_rust
// cd hello_rust
```

Cargo generates this structure:

```
hello_rust/
├── Cargo.toml
├── src/
│   └── main.rs
```

Two files. That's a complete Rust project.

### Cargo.toml

```rust
// This is TOML, not Rust:
// [package]
// name = "hello_rust"
// version = "0.1.0"
// edition = "2021"
```

The `Cargo.toml` file is your project manifest. It declares your project's name, version, and which Rust edition to use. The edition field matters — Rust introduces backwards-incompatible syntax changes through editions (2015, 2018, 2021, 2024) without breaking older code. Always use the latest edition for new projects.

### src/main.rs

```rust
fn main() {
    println!("Hello, world!");
}
```

This is what Cargo generated for you. We'll tear it apart in the next lesson. For now, let's build and run it.

## Building and Running

You have two options:

```rust
// cargo build    — compiles the project
// cargo run      — compiles AND runs the project
```

Use `cargo run` most of the time. It's smart enough to skip recompilation if nothing changed.

```rust
// $ cargo run
//    Compiling hello_rust v0.1.0 (/path/to/hello_rust)
//     Finished dev [unoptimized + debuginfo] target(s) in 0.42s
//      Running `target/debug/hello_rust`
// Hello, world!
```

Notice it says `dev [unoptimized + debuginfo]`. By default, Cargo builds in debug mode — fast compilation, slow execution, with debug symbols. For release builds:

```rust
// cargo build --release
// cargo run --release
```

Release mode enables optimizations. The compiler takes longer, but the resulting binary is *much* faster. Always benchmark with release builds. Debug mode performance is meaningless.

## The target Directory

After building, you'll see a `target/` directory. This is where Cargo puts compiled artifacts. It can get large — hundreds of megabytes for projects with many dependencies. Two things to know:

1. **Add it to .gitignore.** Cargo generates a `.gitignore` that already includes `target/`, but double-check.
2. **You can delete it anytime.** `cargo clean` removes the entire `target/` directory. Everything will be recompiled next time, but nothing is lost.

The compiled binary lives at `target/debug/hello_rust` (or `target/release/hello_rust` for release builds). You can copy this binary anywhere and run it — Rust compiles to native code, no runtime needed.

## cargo check — Your New Best Friend

Here's a command that'll save you hours:

```rust
// cargo check
```

`cargo check` runs the compiler but doesn't produce a binary. It's faster than `cargo build` because it skips code generation. Use it while developing — you want to know if your code compiles, and you want to know *fast*.

I run `cargo check` constantly. After every significant change. Some people set up their editor to run it on save. The faster your feedback loop, the faster you learn.

## Other Useful cargo Commands

```rust
// cargo test     — run your test suite
// cargo doc      — generate documentation
// cargo fmt      — format your code (install with rustup component add rustfmt)
// cargo clippy   — lint your code (install with rustup component add clippy)
```

`cargo fmt` and `cargo clippy` aren't installed by default, but you should add them immediately:

```rust
// rustup component add rustfmt clippy
```

**rustfmt** ends all formatting debates. Run it, commit the result, move on with your life. The Rust community has standardized on one formatting style, and it's wonderful.

**clippy** catches common mistakes and suggests more idiomatic code. It's like having a senior Rust developer review every line. I run clippy in CI on every project. No exceptions.

## Editor Setup

You need an editor with Rust support. Specifically, you want **rust-analyzer** — it provides autocompletion, inline type hints, go-to-definition, and real-time error checking.

- **VS Code**: Install the "rust-analyzer" extension. Done.
- **Neovim**: Use rust-analyzer with your LSP client of choice (I use nvim-lspconfig).
- **IntelliJ/CLion**: The Rust plugin is solid, though I've found rust-analyzer in VS Code to be faster.

Don't skip this step. Writing Rust without an LSP is like driving without headlights. You *can* do it, but why would you?

## A Quick Sanity Check

Let's make sure everything works end to end. Open `src/main.rs` and change it to:

```rust
fn main() {
    let name = "Rustacean";
    println!("Hello, {}! Welcome to Rust.", name);
}
```

Run it:

```rust
// $ cargo run
//    Compiling hello_rust v0.1.0 (/path/to/hello_rust)
//     Finished dev [unoptimized + debuginfo] target(s) in 0.31s
//      Running `target/debug/hello_rust`
// Hello, Rustacean! Welcome to Rust.
```

If that works, your toolchain is set up correctly. You've got the compiler, the build tool, and an editor that understands Rust. That's everything you need.

## What We Skipped

I deliberately didn't cover:
- **Cross-compilation** — you won't need it yet
- **Custom toolchains** — stable is fine
- **Workspaces** — that's a multi-crate concept for later
- **Build scripts** — advanced topic

All of these exist, and we'll touch on some later. But right now, the goal is: you can create a project, write code, compile it, and run it. Everything else builds on that foundation.

Next up — we'll take apart that Hello World program line by line.
