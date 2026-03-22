---
title: "Lesson 9: Macro Abuse — When a function would do"
author: Atharva Pandey
series: "Rust Anti-Patterns & Code Smells"
lesson: 9
keywords: [Rust, rust, anti-patterns, code quality, macros, macro_rules, proc macros, metaprogramming]
tags: [Rust tutorial, rust, anti-patterns, code-quality]
date: '2025-04-21T17:08:00.000Z'
---

I spent an entire afternoon debugging a test failure that turned out to be caused by a macro expanding variable names in a way I didn't expect. The macro was called `make_handler!` and it generated HTTP handler functions from a declarative DSL that someone on the team had invented. The "DSL" saved maybe ten lines of boilerplate per handler. The macro definition was 200 lines of nested `macro_rules!` with five recursion levels, three `tt` munchers, and hygiene workarounds that I'm still not convinced were correct. When a new developer asked how to add a query parameter to a handler, nobody could explain it without first teaching them how the macro worked.

Macros are powerful. They're also the sharpest tool in Rust's toolbox, and people love cutting themselves with them.

## The Smell

Macro abuse comes in several forms:

### The "I don't want to type this" macro

```rust
macro_rules! map {
    ($($key:expr => $val:expr),* $(,)?) => {
        {
            let mut m = std::collections::HashMap::new();
            $(m.insert($key, $val);)*
            m
        }
    };
}

// Used like:
let config = map! {
    "host" => "localhost",
    "port" => "8080",
    "debug" => "true",
};
```

This saves maybe two lines compared to:

```rust
let config = HashMap::from([
    ("host", "localhost"),
    ("port", "8080"),
    ("debug", "true"),
]);
```

The standard library already has `HashMap::from()` with array syntax. The macro adds a custom syntax that new developers won't recognize, that IDEs can't autocomplete, and that produces unhelpful error messages when you get it wrong.

### The "code generation" macro that should be a function

```rust
macro_rules! validate_field {
    ($field:expr, $name:literal, min_len = $min:expr) => {
        if $field.len() < $min {
            return Err(ValidationError::TooShort {
                field: $name,
                min_length: $min,
                actual: $field.len(),
            });
        }
    };
    ($field:expr, $name:literal, max_len = $max:expr) => {
        if $field.len() > $max {
            return Err(ValidationError::TooLong {
                field: $name,
                max_length: $max,
                actual: $field.len(),
            });
        }
    };
    ($field:expr, $name:literal, pattern = $pat:expr) => {
        if !$pat.is_match(&$field) {
            return Err(ValidationError::InvalidFormat {
                field: $name,
                pattern: stringify!($pat),
            });
        }
    };
}

fn validate_user(user: &UserInput) -> Result<(), ValidationError> {
    validate_field!(user.name, "name", min_len = 1);
    validate_field!(user.name, "name", max_len = 100);
    validate_field!(user.email, "email", pattern = EMAIL_RE);
    validate_field!(user.password, "password", min_len = 8);
    Ok(())
}
```

This looks clever, but it's a macro doing what a function can do perfectly well. The `return Err(...)` inside the macro is the biggest red flag — it uses the caller's control flow, which makes the macro's behavior invisible at the call site. Someone reading `validate_field!` doesn't expect it to return early from the enclosing function.

### The "DSL" macro that reinvents the language

```rust
macro_rules! route {
    (GET $path:literal => $handler:ident) => {
        router.get($path, $handler)
    };
    (POST $path:literal => $handler:ident) => {
        router.post($path, $handler)
    };
    (GET $path:literal => $handler:ident, middleware: [$($mw:ident),*]) => {
        router.get($path, $handler)$(.middleware($mw))*
    };
}

// Usage
route!(GET "/users" => list_users);
route!(POST "/users" => create_user);
route!(GET "/admin" => admin_panel, middleware: [auth, rate_limit]);
```

This DSL saves you from writing `router.get("/users", list_users)` — a perfectly readable line of Rust. The macro version introduces a custom syntax that your IDE doesn't understand, that doesn't show up in symbol search, and that breaks in mysterious ways when you try to extend it.

## Why It's Actually Bad

**Error messages are terrible.** When you get a type error inside a macro expansion, the compiler points at the macro invocation, not the actual problematic code. You get messages like:

```
error[E0308]: mismatched types
  --> src/main.rs:47:5
   |
47 |     validate_field!(user.age, "age", min_len = 0);
   |     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ expected `usize`, found `u32`
   |
   = note: this error originates in the macro `validate_field` (in Nightly builds, run with -Z macro-backtrace for more info)
```

Good luck figuring out which part of the macro expansion caused the type mismatch. With a function, the error points directly at the problematic argument.

**IDEs can't help you.** Go-to-definition on a macro invocation takes you to the macro definition, not the generated code. Autocomplete doesn't work inside macro arguments. Refactoring tools can't rename things generated by macros. You lose most of the tooling that makes Rust productive.

**They're hard to read and maintain.** `macro_rules!` syntax is its own sub-language with its own rules about repetitions, fragment specifiers, and hygiene. Even experienced Rust developers struggle with complex `macro_rules!` macros. Proc macros are even worse — they're literally Rust programs that generate Rust code, with all the complexity that implies.

**They can't be unit tested.** You can't call a `macro_rules!` macro in a test and check that it generated the right code. You can only test the code it generates, which means bugs in the macro are surfaced indirectly.

**Hidden control flow.** Macros can use `return`, `break`, `continue`, and `?` — all of which affect the calling function's control flow. A function call can't do this. When you see `some_function()`, you know it can't return from your function. When you see `some_macro!()`, all bets are off.

## The Fix

### Fix 1: Use functions for validation

Replace the validation macro with a function:

```rust
fn validate_min_length(value: &str, field: &str, min: usize) -> Result<(), ValidationError> {
    if value.len() < min {
        return Err(ValidationError::TooShort {
            field: field.to_string(),
            min_length: min,
            actual: value.len(),
        });
    }
    Ok(())
}

fn validate_max_length(value: &str, field: &str, max: usize) -> Result<(), ValidationError> {
    if value.len() > max {
        return Err(ValidationError::TooLong {
            field: field.to_string(),
            max_length: max,
            actual: value.len(),
        });
    }
    Ok(())
}

fn validate_pattern(value: &str, field: &str, pattern: &Regex) -> Result<(), ValidationError> {
    if !pattern.is_match(value) {
        return Err(ValidationError::InvalidFormat {
            field: field.to_string(),
            pattern: pattern.to_string(),
        });
    }
    Ok(())
}

fn validate_user(user: &UserInput) -> Result<(), ValidationError> {
    validate_min_length(&user.name, "name", 1)?;
    validate_max_length(&user.name, "name", 100)?;
    validate_pattern(&user.email, "email", &EMAIL_RE)?;
    validate_min_length(&user.password, "password", 8)?;
    Ok(())
}
```

More lines? Slightly. But every function is individually testable, has proper type signatures, produces clear error messages, and doesn't hide control flow. The `?` at each call site makes the early return explicit.

### Fix 2: Use builder patterns instead of DSL macros

Replace the route DSL with a builder:

```rust
fn configure_routes(router: &mut Router) {
    router.get("/users", list_users);
    router.post("/users", create_user);
    router.get("/admin", admin_panel)
        .middleware(auth)
        .middleware(rate_limit);
}
```

This is just Rust. Your IDE understands it. The compiler gives good errors. New developers can read it without learning a custom DSL. You can add new routing features by adding methods to the builder, not by extending a macro grammar.

### Fix 3: Use generics and traits instead of code generation

If you find yourself writing macros to generate similar impls for different types, consider whether a trait with generics would work:

```rust
// Bad: macro generating repetitive impls
macro_rules! impl_from_row {
    ($type:ident { $($field:ident : $col:literal),* }) => {
        impl FromRow for $type {
            fn from_row(row: &Row) -> Result<Self> {
                Ok($type {
                    $($field: row.get($col)?),*
                })
            }
        }
    };
}

impl_from_row!(User { id: "id", name: "name", email: "email" });
impl_from_row!(Order { id: "id", total: "total", status: "status" });

// Better: derive macro (if you must) or manual implementation
// Sometimes the "boilerplate" is actually clearer:
impl FromRow for User {
    fn from_row(row: &Row) -> Result<Self> {
        Ok(User {
            id: row.get("id")?,
            name: row.get("name")?,
            email: row.get("email")?,
        })
    }
}
```

Yes, the manual impl is more lines. It's also debuggable, searchable, and produces good error messages. If you have fifty types that need `FromRow`, then a derive proc macro might be justified — but consider using an existing one like `sqlx::FromRow` before writing your own.

### Fix 4: Use const and inline functions for compile-time computation

If you're using macros for compile-time string manipulation or constant computation:

```rust
// Bad
macro_rules! env_or_default {
    ($key:literal, $default:literal) => {
        match option_env!($key) {
            Some(v) => v,
            None => $default,
        }
    };
}

// Good — const fn where possible
const fn default_port() -> u16 {
    8080
}

// For env vars, this is one of the few legitimate macro use cases
// since option_env! is itself a macro. But keep it simple.
```

## When Macros Are Justified

I'm not saying never use macros. Here's when they earn their keep:

**Derive macros for repetitive trait impls.** `#[derive(Debug, Clone, Serialize)]` is one of Rust's best features. If you have a genuinely repetitive pattern across many types, a derive macro is the right tool.

**The standard library macros: `vec!`, `format!`, `println!`, `assert!`.** These are so well-known and well-designed that they effectively *are* the language.

**When you need syntax that functions can't express.** Variadic arguments, compile-time format strings, DSLs that genuinely save significant complexity (like `sqlx::query!` which validates SQL at compile time against your database schema).

**Reducing genuine boilerplate across 10+ instances.** If you're implementing the same 20-line pattern for 15 different types, and a trait can't capture it, a macro is justified. But the threshold should be high.

The rule: if a function can do it, use a function. Functions are the default. Macros are the exception, reserved for problems that functions genuinely can't solve. When you do write a macro, keep it small, keep it obvious, and document what it expands to.

Every macro you write is a small language inside your language. Make sure it's a language worth learning.
