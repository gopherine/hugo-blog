---
title: "Lesson 2: unwrap() in Production — Time bombs waiting to explode"
author: Atharva Pandey
series: "Rust Anti-Patterns & Code Smells"
lesson: 2
keywords: [Rust, rust, anti-patterns, code quality, unwrap, error handling, Option, Result, panic]
tags: [Rust tutorial, rust, anti-patterns, code-quality]
date: '2025-04-07T14:35:00.000Z'
---

A service I was responsible for went down at 2 AM on a Saturday because of a single `.unwrap()` call on line 847 of a file nobody had touched in months. The function parsed a config value that was supposed to always be present. Somebody changed the config format in a different repo, the value became optional, and that `.unwrap()` detonated like a landmine — `thread 'main' panicked at 'called Option::unwrap() on a None value'`. Down. Dead. Pager screaming.

Rust gives you `Option` and `Result` specifically so you don't have null pointer exceptions and unhandled errors. Then people go and `.unwrap()` everything, throwing that safety straight in the garbage.

## The Smell

Here's what unwrap abuse looks like in the wild:

```rust
fn get_user_profile(db: &Database, user_id: &str) -> UserProfile {
    let row = db.query("SELECT * FROM users WHERE id = $1", &[user_id])
        .unwrap()  // panics on DB error
        .first()
        .unwrap(); // panics if no rows

    let name = row.get::<String>("name").unwrap();       // panics if column missing
    let email = row.get::<String>("email").unwrap();      // panics if column missing
    let age = row.get::<String>("age").unwrap()           // panics if column missing
        .parse::<u32>().unwrap();                         // panics if not a number
    let joined = row.get::<String>("joined_at").unwrap()  // panics if column missing
        .parse::<DateTime<Utc>>().unwrap();               // panics if bad date format

    UserProfile { name, email, age, joined }
}
```

Six unwraps. Six panic points. And here's the really insidious thing — this code will work perfectly in development, where the database schema is correct, all columns exist, all data is well-formed. It'll work in staging. It'll pass every test you write against a clean test database. Then it'll explode in production the first time it encounters a row with a null email, or a malformed date from a legacy migration, or a transient database connection hiccup.

## Why It's Actually Bad

Let's be precise about what `.unwrap()` does. On an `Option::None` or `Result::Err`, it panics. A panic in Rust unwinds the stack (or aborts, depending on your config) and kills the current thread. In a web server, that might kill one request handler. In a single-threaded CLI tool, that kills the whole process.

**Panics are for bugs, not for expected errors.** The Rust philosophy is clear about this: use `Result` for errors that can reasonably happen (network failures, invalid input, missing files), and `panic!` for situations that represent programmer mistakes (indexing out of bounds, violated invariants). When you `.unwrap()` a database query result, you're saying "a database error is a programmer bug, not a runtime possibility." That's delusional.

**You lose all context.** When unwrap panics, you get a message like `called Result::unwrap() on an Err value: IoError("connection refused")`. No context about what you were trying to do, what the input was, or how to fix it. Compare that to a properly propagated error: `"failed to load user profile for user_id=abc123: database connection refused"`. Which one do you want to see at 2 AM?

**It defeats Rust's error handling system.** Rust doesn't have exceptions. It has `Result`. The entire ecosystem is built around explicit, composable error handling. When you unwrap, you're opting out of the language's greatest strength.

## The Fix

### Step 1: Propagate with `?`

The simplest fix is usually the `?` operator. Change your function to return `Result` and let errors bubble up:

```rust
use anyhow::{Context, Result};

fn get_user_profile(db: &Database, user_id: &str) -> Result<UserProfile> {
    let rows = db.query("SELECT * FROM users WHERE id = $1", &[user_id])
        .context("failed to query user table")?;

    let row = rows.first()
        .ok_or_else(|| anyhow::anyhow!("user not found: {}", user_id))?;

    let name: String = row.get("name")
        .context("missing 'name' column")?;
    let email: String = row.get("email")
        .context("missing 'email' column")?;
    let age: u32 = row.get::<String>("age")
        .context("missing 'age' column")?
        .parse()
        .context("invalid age format")?;
    let joined: DateTime<Utc> = row.get::<String>("joined_at")
        .context("missing 'joined_at' column")?
        .parse()
        .context("invalid joined_at date format")?;

    Ok(UserProfile { name, email, age, joined })
}
```

Every error now carries context. If parsing the age fails, you don't get a generic parse error — you get `"invalid age format"` with the underlying error chained. And the caller decides how to handle it: retry, return a 404, log and skip, whatever makes sense.

### Step 2: Use `map`, `and_then`, `unwrap_or` for Options

When you have an `Option` and a reasonable default exists, don't unwrap — provide the default:

```rust
// Bad
let port = config.get("port").unwrap();

// Good: default value
let port = config.get("port").unwrap_or(&"8080".to_string());

// Good: compute default lazily
let port = config.get("port").unwrap_or_else(|| {
    eprintln!("No port configured, using default 8080");
    &default_port
});

// Good: transform the Option
let port: u16 = config.get("port")
    .and_then(|p| p.parse().ok())
    .unwrap_or(8080);
```

### Step 3: Pattern match when you need different behavior

Sometimes `?` isn't enough because different errors require different responses. Pattern match on the result:

```rust
fn load_config(path: &str) -> Config {
    match std::fs::read_to_string(path) {
        Ok(contents) => parse_config(&contents),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            eprintln!("Config file not found, using defaults");
            Config::default()
        }
        Err(e) => {
            eprintln!("Failed to read config: {e}. Using defaults.");
            Config::default()
        }
    }
}
```

This is intentional. You've thought about each failure mode and decided what to do. That's the opposite of `.unwrap()`, which says "I refuse to think about failure."

### Step 4: Use `expect()` when panic is genuinely appropriate

Sometimes panicking *is* the right call — when the error represents a bug in your program, not a runtime condition. In those cases, use `.expect()` with a message explaining why this should never fail:

```rust
// This is fine — if the regex doesn't compile, that's a bug in our code
let email_re = Regex::new(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
    .expect("email regex should be valid — this is a compile-time constant");

// This is fine — we just checked the condition
let items = vec![1, 2, 3];
if !items.is_empty() {
    let first = items.first().expect("just checked that items is non-empty");
}

// This is fine — channel receiver in a controlled thread
let msg = rx.recv().expect("sender should not be dropped while receiver is alive");
```

The key difference: `.expect("reason")` documents *why* you believe this can't fail. `.unwrap()` just says "YOLO."

## The Gray Areas

I should be honest — there are places where `.unwrap()` is tolerable:

**Tests.** In test code, unwrap is fine. If something panics, the test fails — that's exactly what you want. Using `?` in tests adds noise.

```rust
#[test]
fn test_parse_config() {
    let config = parse_config("key=value\n").unwrap();
    assert_eq!(config.get("key").unwrap(), "value");
}
```

**Scripts and prototypes.** If you're writing a one-off script that you'll throw away, unwrap is fine. Ship fast, move on.

**After a check that guarantees success.** If you've already validated that the value exists, unwrapping the validated result is okay — though I'd still prefer `expect()` with a note.

**Lazy statics and one-time initialization.** Regexes, compiled templates, parsed constants — these are known at compile time and panicking on a bug is appropriate.

## A Clippy Rule You Should Enable

Clippy has a lint for this: `clippy::unwrap_used`. Add it to your crate:

```rust
// In main.rs or lib.rs
#![deny(clippy::unwrap_used)]
```

This makes every `.unwrap()` a compile error. You'll have to explicitly opt in to unwrap with `#[allow(clippy::unwrap_used)]` when you've made a conscious decision that panicking is correct. That friction is the point — it forces you to think about each case.

For tests, you can relax it:

```rust
// In your test modules
#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    // ...
}
```

## The Real Lesson

Every `.unwrap()` is a decision to crash instead of handling an error. Sometimes that's the right decision. But it should always be a *conscious* decision, never a default. The difference between a robust Rust service and a fragile one isn't the type system or the borrow checker — it's whether the developers treated error handling as a first-class concern or just unwrapped their way to "it compiles."

Think of it this way: Rust's type system already eliminated null pointer dereferences, use-after-free, and data races. The one class of runtime crashes it *can't* prevent is the ones you opt into with `.unwrap()`. Don't throw away the safety you got for free.
