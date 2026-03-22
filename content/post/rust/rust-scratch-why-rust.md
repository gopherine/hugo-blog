---
title: "Lesson 1: Why Rust Exists — And why you should care"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 1
keywords: [Rust, rust, why rust, systems programming, memory safety, rust vs c++]
tags: [Rust tutorial, rust, beginner]
date: '2024-03-05T09:15:00.000Z'
---

I mass-deleted a production database once because a C program I'd written had a use-after-free bug that corrupted a pointer used for query routing. Took us fourteen hours to recover. That was the week I started learning Rust.

## The Problem Rust Solves

Every few years, someone publishes a study on CVEs in major software projects. The numbers are always the same: roughly 70% of critical security vulnerabilities in C and C++ codebases are memory safety issues. Buffer overflows, use-after-free, double-free, null pointer dereferences. The same bugs, decade after decade.

This isn't because C and C++ programmers are bad. It's because the languages make it *easy* to write these bugs and *hard* to catch them. You're juggling raw pointers, manual memory management, and undefined behavior — all while trying to ship features. Something will slip through.

Rust was created at Mozilla specifically to address this. Graydon Hoare started it as a personal project around 2006, Mozilla adopted it in 2009, and version 1.0 shipped in 2015. The core idea: what if the compiler could catch memory bugs *before* your code ever runs?

## What Makes Rust Different

Three things set Rust apart from everything else I've used.

**Ownership and borrowing.** Every value in Rust has exactly one owner. When the owner goes out of scope, the value is dropped — automatically, deterministically, no garbage collector needed. You can lend references to values, but the compiler enforces strict rules about who can read and who can write, and for how long. This eliminates data races at compile time. Not "reduces." *Eliminates.*

**Zero-cost abstractions.** Rust gives you high-level features — generics, pattern matching, iterators, closures — without runtime overhead. When you write a chain of iterator adapters, the compiler optimizes it down to the same machine code you'd write by hand. You don't have to choose between abstraction and performance.

**No garbage collector.** This is a big deal for systems programming. Languages like Go and Java pause your program periodically to clean up memory. That's fine for web servers, but it's a deal-breaker for game engines, embedded systems, and real-time audio. Rust manages memory through ownership rules checked at compile time. Your program runs without pauses.

## Who's Using It

This isn't an academic experiment. Real companies ship real Rust in production.

- **Linux kernel** — Rust is now an officially supported language for kernel modules
- **Android** — Google uses Rust for security-critical components in the Android OS
- **AWS** — Firecracker (the VM manager behind Lambda) is written in Rust
- **Discord** — Rewrote their Read States service from Go to Rust for better latency
- **Cloudflare** — Their Pingora proxy handles a significant chunk of internet traffic, written in Rust
- **Microsoft** — Exploring Rust for Windows kernel components

When companies with billions at stake choose a language for their most critical infrastructure, pay attention.

## The Tradeoffs — Because There Are Always Tradeoffs

I'm not going to pretend Rust is perfect. That would be dishonest.

**The learning curve is real.** Ownership and borrowing will fight you for the first few weeks. You'll write code that seems obviously correct and the compiler will reject it. This is frustrating. It gets better, but it takes time.

**Compile times are slow.** A medium-sized Rust project takes noticeably longer to compile than the equivalent Go or C project. Incremental compilation helps, but clean builds can be painful. The Rust team is actively working on this, and it's improved a lot, but it's still a weak spot.

**The ecosystem is younger.** You won't find a Rust library for everything. Some domains — like GUI development — are still rough around the edges. The ecosystem grows fast, but it's not at Python or JavaScript levels of "there's a package for that."

**Not every project needs Rust.** If you're building a CRUD API, Go or Python will get you there faster. Rust shines when you need performance, reliability, or both — but it's not the right tool for every job.

## Why I Think You Should Learn It Anyway

Even if you never write production Rust, learning it will make you a better programmer. The ownership model forces you to think about where data lives, who can access it, and when it gets cleaned up. These are concepts that matter in *every* language — Rust just makes them explicit.

I write better Go code because of Rust. I write better C code because of Rust. I even write better Python code because Rust trained me to think about data ownership and lifetimes, even when the language doesn't enforce them.

And if you do end up writing production Rust? You'll ship code with a level of confidence that's hard to achieve in other systems languages. When the compiler says your code is safe, it *means* it.

## What This Series Covers

We're going to build up Rust from absolute zero. No assumptions about prior Rust experience. I do assume you've programmed before — you know what a variable is, what a function does, what a loop looks like. But we'll start with installing the toolchain and writing Hello World, and build from there.

Here's the rough arc:
- **Lessons 1-6**: Basics — toolchain, syntax, types, control flow
- **Lessons 7-10**: Ownership, borrowing, slices, strings — the core concepts
- **Lessons 11-18**: Structs, enums, collections, error handling, modules, crates
- **Lessons 19-22**: Traits, generics, closures, iterators — the power tools
- **Lessons 23-25**: Testing, file I/O, and where to go next

Each lesson builds on the previous ones. There's working code you can compile and run. I'll tell you what I think is important, what I think is overrated, and where the real footguns are hiding.

## One More Thing

Rust has a reputation for being hard. I think that reputation is partially earned but mostly overblown. The borrow checker is strict, but it's not arbitrary — every rule exists because it prevents a real bug. Once you understand *why* the rules exist, they start feeling less like restrictions and more like guardrails.

The compiler errors are good. Really good. When Rust tells you something is wrong, it usually tells you *how to fix it*. Pay attention to the error messages. Read them carefully. They're written by people who genuinely want to help you succeed.

Let's get started. Next lesson — we install the toolchain.
