---
title: "Lesson 10: How rustc Works — From source code to binary"
author: Atharva Pandey
series: "Rust Memory Model & Internals"
lesson: 10
keywords: [Rust, rust, memory, internals, rustc, compiler, MIR, HIR, LLVM, compilation pipeline, codegen]
tags: [Rust tutorial, rust, internals]
date: '2025-03-22T11:30:00.000Z'
---

A junior on my team once asked why Rust compiles so slowly compared to Go. I gave the standard answer about monomorphization and LLVM, but realized I couldn't actually explain the full pipeline. So I spent a weekend reading the rustc dev guide and poking at compiler internals. What I found was a surprisingly elegant six-stage pipeline, and understanding it changed how I think about Rust's design tradeoffs.

## The Big Picture

When you run `cargo build`, your source code goes through six major stages before becoming a binary:

```
Source Code (.rs)
    ↓  Lexing + Parsing
Token Stream → AST
    ↓  Lowering
HIR (High-level IR)
    ↓  Type checking, trait resolution, borrow checking
THIR → MIR (Mid-level IR)
    ↓  Optimizations, monomorphization
LLVM IR
    ↓  LLVM optimization + codegen
Machine Code (binary)
```

Each stage transforms the code into a lower-level representation, shedding syntactic sugar and making implicit behaviors explicit. Let's walk through each one.

## Stage 1: Lexing and Parsing

The first step is turning raw text into structured data. The lexer (tokenizer) breaks source code into tokens, and the parser assembles those tokens into an Abstract Syntax Tree (AST).

```rust
// Source code:
fn add(a: u32, b: u32) -> u32 {
    a + b
}
```

The lexer produces tokens:

```
[fn] [ident("add")] [(] [ident("a")] [:] [ident("u32")] [,]
[ident("b")] [:] [ident("u32")] [)] [->] [ident("u32")] [{]
[ident("a")] [+] [ident("b")] [}]
```

The parser builds an AST — a tree structure where each node represents a syntactic element:

```
FnDecl {
    name: "add",
    params: [
        Param { name: "a", ty: Path("u32") },
        Param { name: "b", ty: Path("u32") },
    ],
    return_ty: Path("u32"),
    body: BinExpr {
        op: Add,
        lhs: Ident("a"),
        rhs: Ident("b"),
    },
}
```

The AST preserves all syntactic information — macros, sugar, the exact way you wrote things. At this stage, nothing has been resolved or type-checked. `u32` is just a name, not a type.

### Macro Expansion

Macros are expanded at the AST level. This is why macros can do things that regular functions can't — they operate on the syntax tree before any semantic analysis:

```rust
// Before macro expansion:
println!("x = {}", x);

// After macro expansion (simplified):
{
    use std::io::Write;
    let mut stdout = std::io::stdout().lock();
    stdout.write_fmt(format_args!("x = {}", x)).unwrap();
}
```

Procedural macros and `macro_rules!` both work at this stage. The compiler repeatedly expands macros until there are none left, then moves on.

You can see the expanded output with:

```bash
cargo expand  # requires cargo-expand: cargo install cargo-expand
```

## Stage 2: HIR — High-level Intermediate Representation

The AST is lowered into the HIR, which is a desugared, simplified version of the syntax. Syntactic sugar is removed, and constructs are normalized:

```rust
// Source:
for x in 0..10 {
    println!("{}", x);
}

// HIR (conceptual):
{
    let mut iter = IntoIterator::into_iter(0..10);
    loop {
        match Iterator::next(&mut iter) {
            Some(x) => { /* println body */ },
            None => break,
        }
    }
}
```

Other desugarings:
- `if let` becomes `match`
- `while let` becomes `loop` + `match`
- `?` becomes `match` on `Result`/`Option` with early return
- `async`/`await` becomes state machine generators
- Method calls become function calls with explicit `self`

The HIR is what the compiler uses for name resolution — figuring out which `String` you mean, which trait method to call, which module a name belongs to.

You can dump the HIR with:

```bash
cargo rustc -- -Z unpretty=hir
```

(Requires nightly. The output is verbose but educational.)

## Stage 3: Type Checking and Borrow Checking

This is where the magic happens — and where most of Rust's compile time is spent.

### Type Inference

Rust uses Hindley-Milner type inference (extended with various Rust-specific features). The compiler infers types for variables, closures, and generic parameters:

```rust
let x = vec![1, 2, 3]; // inferred: Vec<i32>
let y = x.iter().map(|n| n * 2).collect(); // inferred: Vec<i32>
```

The compiler builds a system of type constraints and solves them. When it can't solve the system (ambiguous types), it asks you for an annotation — that's when you see "type annotations needed" errors.

### Trait Resolution

When you call a method, the compiler needs to figure out which implementation to use. This involves searching through all trait implementations, considering auto-dereferencing, and resolving associated types:

```rust
trait Display {
    fn fmt(&self, f: &mut Formatter) -> Result;
}

// When you write:
println!("{}", my_value);

// The compiler must find:
// 1. Which type is my_value?
// 2. Does that type implement Display?
// 3. Which impl block provides the fmt method?
```

Trait resolution is one of the most complex parts of the compiler. It handles coherence (the orphan rules), specialization, where clauses, higher-ranked trait bounds, and auto traits. It's also a major source of compile time — checking trait bounds for deeply generic code can be expensive.

### Borrow Checking

The borrow checker operates on the THIR/MIR and enforces Rust's ownership rules:

- Each value has exactly one owner
- References must not outlive the referent
- You can have many `&T` or one `&mut T`, but not both
- No data races through shared mutable state

The current borrow checker uses a system called NLL (Non-Lexical Lifetimes), which tracks the actual usage ranges of references rather than their lexical scopes. This was a huge ergonomic improvement — in older Rust, borrows lasted until the end of the enclosing scope, leading to unnecessary errors.

```rust
fn main() {
    let mut v = vec![1, 2, 3];

    let first = &v[0]; // immutable borrow starts
    println!("{}", first); // last use of `first`
    // NLL: immutable borrow ends here (at last use)

    v.push(4); // mutable borrow — fine, because `first` is no longer used

    // Pre-NLL, this would have been an error because `first`'s borrow
    // extended to the end of the scope
}
```

## Stage 4: MIR — Mid-level Intermediate Representation

MIR is where Rust's analysis and optimization really happens. It's a control-flow graph where each node is a "basic block" — a sequence of statements with a single entry and exit point.

```rust
fn factorial(n: u64) -> u64 {
    if n <= 1 { 1 } else { n * factorial(n - 1) }
}
```

The MIR looks something like (simplified):

```
bb0: {
    _2 = Le(copy _1, const 1_u64);
    switchInt(move _2) -> [0: bb2, otherwise: bb1];
}

bb1: {
    StorageLive(_3);
    _3 = const 1_u64;
    _0 = move _3;
    goto -> bb3;
}

bb2: {
    StorageLive(_4);
    _4 = Sub(copy _1, const 1_u64);
    _5 = factorial(move _4);
    _0 = Mul(copy _1, move _5);
    goto -> bb3;
}

bb3: {
    return;
}
```

MIR makes control flow explicit — no nested expressions, no complex control structures, just basic blocks connected by jumps. This makes it straightforward for the compiler to:

- Perform borrow checking (the borrow checker actually runs on MIR)
- Optimize (constant propagation, dead code elimination, inlining)
- Check for unsafety violations
- Run Miri (which interprets MIR directly — that's where the name comes from)

View the MIR for your code:

```bash
cargo rustc -- -Z unpretty=mir
```

### Monomorphization

One of the most important MIR-level operations is monomorphization — creating concrete copies of generic functions for each type they're used with:

```rust
fn max<T: PartialOrd>(a: T, b: T) -> T {
    if a > b { a } else { b }
}

fn main() {
    max(1i32, 2i32);       // generates max::<i32>
    max(1.0f64, 2.0f64);   // generates max::<f64>
    max("hello", "world"); // generates max::<&str>
}
```

The compiler creates three separate copies of `max`, each specialized for a specific type. This is why generics in Rust have zero runtime cost — by the time the code runs, there are no generics, just concrete functions with direct calls.

The downside: more copies means more code to compile and a larger binary. This is the primary reason Rust compiles slower than Go (which uses a different generics strategy) and why `dyn Trait` (dynamic dispatch, no monomorphization) can reduce compile times.

## Stage 5: LLVM IR

After MIR optimization, the compiler translates MIR into LLVM IR — the intermediate representation used by the LLVM compiler framework. LLVM is the same backend used by Clang (C/C++), Swift, and many other languages.

```llvm
; LLVM IR for a simple add function (simplified)
define i32 @add(i32 %a, i32 %b) {
entry:
  %result = add i32 %a, %b
  ret i32 %result
}
```

LLVM IR is lower-level than MIR — it's closer to assembly but still platform-independent. At this point, Rust-specific concepts like ownership and borrowing are gone. Everything has been verified and transformed into operations on values and memory.

You can view the LLVM IR:

```bash
cargo rustc -- --emit=llvm-ir
# Output goes to target/debug/deps/*.ll
```

## Stage 6: LLVM Optimization and Code Generation

LLVM takes its IR and runs a series of optimization passes:

- **Constant folding:** `3 + 4` becomes `7`
- **Dead code elimination:** removes unreachable code
- **Inlining:** replaces function calls with the function body
- **Loop unrolling:** duplicates loop bodies to reduce branch overhead
- **Vectorization:** converts scalar operations to SIMD
- **Register allocation:** maps variables to CPU registers

Then LLVM generates machine code for the target architecture — x86_64, aarch64, RISC-V, WASM, whatever you're targeting.

The optimization level (`-O0`, `-O1`, `-O2`, `-O3`, `-Os`) controls how aggressively LLVM optimizes:

```bash
# Debug build — minimal optimization, fast compile
cargo build

# Release build — full optimization, slow compile
cargo build --release

# View the generated assembly
cargo rustc --release -- --emit=asm
```

### Seeing the Final Assembly

Want to see what your Rust code actually compiles to? Use `cargo-show-asm` or Compiler Explorer (godbolt.org):

```rust
pub fn sum_array(data: &[u64]) -> u64 {
    data.iter().sum()
}
```

On x86_64 with `-O2`, this generates vectorized SIMD code that processes multiple elements per instruction — the same quality of assembly you'd get from hand-optimized C. The iterator chain, the `sum()` call, the bounds checks — all gone, replaced by tight SIMD loops.

```bash
cargo install cargo-show-asm
cargo asm my_crate::sum_array --release
```

## Why Rust Is Slow to Compile

Now you can see why Rust compilation takes time. Every stage does significant work:

1. **Macro expansion** — can generate a lot of code (looking at you, `derive`)
2. **Type inference and trait resolution** — solving complex constraint systems
3. **Borrow checking** — dataflow analysis on every function
4. **Monomorphization** — generating potentially many copies of generic code
5. **LLVM optimization** — LLVM's passes are thorough but not fast

The biggest contributors to compile time are typically monomorphization (generates lots of code for LLVM to process) and LLVM optimization (the optimizer is doing real work). This is why:

- Debug builds are faster than release builds (less LLVM optimization)
- Adding more generics increases compile time (more monomorphization)
- `dyn Trait` can speed up compilation (no monomorphization needed)
- Incremental compilation helps (only recompiles what changed)

## Practical Tips

### Inspecting Compiler Output

```bash
# See macro expansion
cargo expand

# See HIR
cargo rustc -- -Z unpretty=hir  # nightly

# See MIR
cargo rustc -- -Z unpretty=mir  # nightly

# See LLVM IR
cargo rustc -- --emit=llvm-ir

# See assembly
cargo rustc -- --emit=asm

# See everything about compilation timing
cargo build --timings
```

### Speeding Up Compilation

```toml
# Cargo.toml

# Use a faster linker
# On Linux:
[target.x86_64-unknown-linux-gnu]
linker = "clang"
rustflags = ["-C", "link-arg=-fuse-ld=lld"]

# On macOS (use the default or mold):
# [target.x86_64-apple-darwin]
# rustflags = ["-C", "link-arg=-fuse-ld=/opt/homebrew/bin/ld64.mold"]

# Optimize deps but not your own code (faster iteration)
[profile.dev.package."*"]
opt-level = 2
```

```bash
# Use sccache for caching across builds
cargo install sccache
export RUSTC_WRAPPER=sccache

# Use cargo-nextest for faster test execution
cargo install cargo-nextest
cargo nextest run
```

### Understanding Compiler Errors

Knowing the pipeline helps you understand error messages:

- **Parse errors** ("expected `;`", "unexpected token") — Stage 1
- **Name resolution errors** ("cannot find value `x`") — Stage 2 (HIR)
- **Type errors** ("expected `u32`, found `&str`") — Stage 3
- **Borrow checker errors** ("cannot borrow as mutable") — Stage 3 (on MIR)
- **Lifetime errors** ("does not live long enough") — Stage 3
- **Linker errors** ("undefined symbol") — Stage 6

When you get a confusing error, knowing which stage it comes from helps you understand what the compiler is actually complaining about.

## The Pipeline as Design Philosophy

Understanding the compilation pipeline reveals why Rust is the way it is.

**Why is there no function overloading?** Because name resolution happens before type checking. When the compiler sees `foo(x)`, it needs to know which `foo` you mean before it knows the type of `x`. Trait methods provide a form of overloading that works within this constraint.

**Why are trait impls separate from struct definitions?** Because the compiler resolves them in a separate pass. This enables the orphan rules and coherence checking that prevent conflicting implementations.

**Why do generics require explicit trait bounds?** Because type checking happens before monomorphization. The compiler needs to verify that your generic code is valid for *any* type that satisfies the bounds, not just the types you happen to use. This is different from C++ templates, which are checked only after instantiation.

**Why does the borrow checker sometimes feel too strict?** Because it makes decisions based on the MIR control flow graph, which doesn't always capture the programmer's intent. The compiler is conservative — if it can't prove safety, it rejects the code. Better a false positive than a memory bug.

## Series Wrap-Up

Over these ten lessons, we've gone from individual bytes in memory to the full compilation pipeline. Here's what we covered:

1. **Memory Layout** — Size, alignment, padding, niche optimization
2. **Stack vs Heap** — Where data lives and what it costs
3. **Box** — Heap allocation with ownership semantics
4. **vtables** — How dynamic dispatch works at the machine level
5. **Fat Pointers** — The metadata behind `&[T]`, `&str`, and `&dyn Trait`
6. **repr Attributes** — Taking control of memory layout for FFI and performance
7. **Drop Order** — When and how destructors run
8. **Custom Allocators** — Replacing `malloc` with arenas, pools, and more
9. **Miri** — Verifying unsafe code catches UB
10. **The Compiler Pipeline** — How source becomes binary

These aren't just academic details. They're the tools you need to write fast, correct, and interoperable Rust. Every time you choose between `Box` and a stack allocation, between generics and `dyn Trait`, between `repr(Rust)` and `repr(C)` — you're making decisions that touch these internals. Now you can make them with confidence.
