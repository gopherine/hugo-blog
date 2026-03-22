---
title: "Lesson 12: Macro Anti-Patterns — When not to macro"
author: Atharva Pandey
series: "Rust Macros & Metaprogramming"
lesson: 12
keywords: [Rust, rust, macros, anti-patterns, best practices, metaprogramming, code quality]
tags: [Rust tutorial, rust, macros, metaprogramming]
date: '2025-03-04T12:50:00.000Z'
---

I once worked on a codebase where someone had written a macro for everything. Creating structs? Macro. Implementing a two-line function? Macro. Logging? Custom logging macro that wrapped `println!` and added a timestamp. The macro definitions file was 800 lines long. The macros had macros inside them. Nobody on the team could modify them without breaking something, and the original author had left six months earlier. That project taught me more about when *not* to use macros than any tutorial ever could.

## Anti-Pattern #1: Macro When a Function Would Do

This is the most common mistake. Someone discovers macros and starts using them for everything, including cases where a regular function works fine.

```rust
// Don't do this
macro_rules! add_one {
    ($x:expr) => {
        $x + 1
    };
}

// Do this
fn add_one(x: i32) -> i32 {
    x + 1
}
```

The macro version has no advantages: no variadic arguments, no type-level code generation, no compile-time validation, no syntax manipulation. It's just worse in every way — harder to debug, no type checking on the input, invisible to IDE tooling, and impossible to use as a function pointer.

**The rule:** if you can write it as a function or a generic function, write it as a function. Reach for macros only when functions physically cannot express what you need.

Examples of things that genuinely need macros:
- `vec![1, 2, 3]` — variadic, type-generic, heap allocation
- `println!("{}", x)` — format string parsing, variadic
- `#[derive(Debug)]` — code generation from struct definition

Examples that don't need macros:
- `max!(a, b)` — use `std::cmp::max` or a generic function
- `square!(x)` — just write `fn square(x: f64) -> f64 { x * x }`
- `log!(level, msg)` — unless you need `file!()` and `line!()` information, a function is fine

## Anti-Pattern #2: The God Macro

A single macro that does everything — generates structs, implements traits, sets up validation, creates database queries, registers routes, and makes coffee.

```rust
// This is a nightmare
macro_rules! define_entity {
    ($name:ident {
        $($field:ident : $ty:ty $([ $($attr:tt)* ])?),* $(,)?
    }) => {
        // 200 lines of expansion covering:
        // - struct definition
        // - builder pattern
        // - Display impl
        // - Serialize/Deserialize
        // - database query generation
        // - API route registration
        // - validation
        // - test scaffold
    };
}

define_entity!(User {
    id: i64 [primary_key, auto_increment],
    name: String [validate(min_len = 1, max_len = 100)],
    email: String [validate(email), unique],
    created_at: DateTime [default(now)],
});
```

This looks appealing — one call generates everything! But it's terrible in practice:

- **Debugging is impossible.** When something breaks, you're debugging 200 lines of generated code where any piece could be the culprit.
- **Compilation errors are cryptic.** An error in the serialization logic points at the macro call site, not at the specific field or attribute causing the issue.
- **Modification is terrifying.** Changing one arm of the expansion can break all the others. The macro's internal state is invisible.
- **Reuse is zero.** The macro is so specific to your domain that it can't be used anywhere else, and it can't be composed with other macros.

**The fix:** break it into small, composable pieces. Use multiple derive macros, each doing one thing:

```rust
#[derive(Debug, Serialize, Deserialize)]
#[derive(Validate)]   // handles validation
#[derive(Builder)]     // handles builder pattern
#[table("users")]      // handles DB mapping (attribute macro)
struct User {
    #[validate(min_len = 1, max_len = 100)]
    name: String,

    #[validate(email)]
    email: String,
}
```

Each macro is independently testable, debuggable, and replaceable. The composition happens at the use site, not inside a monolithic macro definition.

## Anti-Pattern #3: Macro for Abstraction

Macros are for code generation, not abstraction. When you want to hide complexity, use traits and generics. When you want to *generate* code that would be repetitive to write by hand, use macros.

```rust
// Bad: using a macro as an abstraction layer
macro_rules! database_query {
    (find $table:ident where $col:ident = $val:expr) => {
        {
            let conn = get_connection();
            let query = format!(
                "SELECT * FROM {} WHERE {} = $1",
                stringify!($table),
                stringify!($col),
            );
            conn.query(&query, &[$val])
        }
    };
}

let users = database_query!(find users where id = 42);
```

This macro hides a SQL injection vulnerability behind cute syntax. It makes the code look safe while being anything but. The string interpolation of table and column names is unparameterized — a `stringify!` macro doesn't escape anything.

More importantly, this "abstraction" is leaky. What happens when you need a JOIN? An ORDER BY? A subquery? You either extend the macro with more patterns (hello, god macro) or you drop back to raw SQL. The abstraction never covers enough cases.

**Better approach:** use a query builder library, an ORM, or sqlx's compile-time-checked queries. These provide actual type safety, not just syntactic sugar.

## Anti-Pattern #4: Hiding Control Flow

Macros that hide `return`, `break`, `continue`, or `?` are a maintenance hazard:

```rust
// Terrible idea
macro_rules! try_or_return {
    ($expr:expr) => {
        match $expr {
            Ok(val) => val,
            Err(_) => return,
        }
    };
}

fn process() {
    let data = try_or_return!(fetch_data()); // silently returns on error!
    // Is this line reachable? You have to check the macro to know.
    do_something(data);
}
```

Someone reading `process()` can't tell that the first line might return early without looking up the macro definition. The `return` is invisible at the call site. This violates a fundamental principle: control flow should be visible.

The `?` operator gets a pass because it's a language-level feature that everyone knows. A custom macro that hides returns is just confusing.

```rust
// Clear, readable, no hidden control flow
fn process() -> Result<(), Error> {
    let data = fetch_data()?;  // ? is universally understood
    do_something(data);
    Ok(())
}
```

## Anti-Pattern #5: Macro-Heavy Tests

I've seen test suites where every test is generated by a macro:

```rust
macro_rules! test_case {
    ($name:ident, $input:expr, $expected:expr) => {
        #[test]
        fn $name() {
            assert_eq!(process($input), $expected);
        }
    };
}

test_case!(test_empty, "", "");
test_case!(test_hello, "hello", "HELLO");
test_case!(test_numbers, "abc123", "ABC123");
// ... 200 more
```

This seems clean. But when `test_numbers` fails, the error message points at the `assert_eq!` inside the macro, not at the specific `test_case!` invocation. The stack trace says "line 4 in macro definition." Good luck figuring out which of the 200 test cases broke.

**Better approach:** use data-driven tests with a loop, or use a testing framework that supports parameterized tests:

```rust
#[test]
fn test_process() {
    let cases = vec![
        ("", ""),
        ("hello", "HELLO"),
        ("abc123", "ABC123"),
    ];

    for (input, expected) in cases {
        assert_eq!(
            process(input), expected,
            "failed for input: {:?}", input,
        );
    }
}
```

When this fails, you get the exact input that caused the failure. No macro debugging needed.

If you genuinely need separate test functions (for parallel execution, for example), use a crate like `test-case`:

```rust
#[test_case("" => "" ; "empty string")]
#[test_case("hello" => "HELLO" ; "simple word")]
#[test_case("abc123" => "ABC123" ; "alphanumeric")]
fn test_process(input: &str) -> String {
    process(input)
}
```

## Anti-Pattern #6: Not Using Built-in Alternatives

Before writing a macro, check if the standard library or a well-maintained crate already does what you need.

Don't write `assert_approx!` — use `assert!((a - b).abs() < epsilon)` or the `approx` crate.

Don't write `hashmap!` — since Rust 1.56, you can use:
```rust
let map = HashMap::from([
    ("key1", "value1"),
    ("key2", "value2"),
]);
```

Don't write `lazy_static!` — use `std::sync::LazyLock` (stable since Rust 1.80):
```rust
use std::sync::LazyLock;

static CONFIG: LazyLock<Config> = LazyLock::new(|| {
    Config::load().expect("failed to load config")
});
```

Don't write a custom derive for `Display` on enums — use `strum` or `thiserror`.

The ecosystem is mature. Someone has almost certainly published and maintained the macro you're about to write.

## Anti-Pattern #7: Unsafe Code in Macro Output

Macros that generate `unsafe` blocks are dangerous because the user might not realize their safe-looking code is actually unsafe:

```rust
// DANGEROUS
macro_rules! fast_access {
    ($slice:expr, $idx:expr) => {
        unsafe { *$slice.get_unchecked($idx) }
    };
}

let data = vec![1, 2, 3];
let val = fast_access!(data, 10); // UB! But the call site looks safe
```

The caller writes `fast_access!(data, 10)` which looks like a normal, safe macro call. But it generates an `unsafe` block with unchecked access. Out-of-bounds access is undefined behavior, and the macro hides that danger.

If your macro genuinely needs to generate `unsafe` code, at minimum:
- Name the macro with an `unsafe` prefix: `unsafe_fast_access!`
- Document the safety requirements prominently
- Consider requiring the caller to write `unsafe` themselves

## Anti-Pattern #8: Over-Abstracted Macro Libraries

I've seen internal macro libraries where the macros define their own "framework" with custom lifecycle hooks, dependency injection, and plugin systems — all in macros.

```rust
// Internal framework powered entirely by macros
app! {
    modules: [auth, users, billing],
    middleware: [logging, cors, rate_limit],
    database: postgres("DATABASE_URL"),
    cache: redis("REDIS_URL"),

    routes! {
        GET "/users" => users::list,
        POST "/users" => users::create,
        GET "/users/:id" => users::get,
    }
}
```

Looks elegant in the README. In practice:

- Error messages are meaningless — they point at the macro, never at your code
- IDE support is nonexistent — no autocomplete, no go-to-definition, no refactoring
- Testing requires understanding the macro's expansion, not just the API
- Upgrading the macro library is terrifying because the expansion might change

**The alternative:** use regular Rust. A builder pattern with method calls gives you the same ergonomics with full IDE support and clear error messages:

```rust
App::builder()
    .module(auth::module())
    .module(users::module())
    .middleware(logging())
    .middleware(cors())
    .database(postgres_from_env("DATABASE_URL"))
    .route(get("/users", users::list))
    .route(post("/users", users::create))
    .build()
    .run()
    .await
```

More verbose? Slightly. Debuggable, testable, refactorable, IDE-friendly? Absolutely.

## When Macros ARE the Right Call

After all these anti-patterns, here's when macros genuinely pay for themselves:

1. **Trait implementations across many types.** The standard library uses `macro_rules!` to implement traits for all numeric types. This is exactly the right use case — mechanical repetition with no variation in logic.

2. **Derive macros for common traits.** If every struct in your codebase needs the same boilerplate implementation, a derive macro eliminates a class of bugs and saves real time.

3. **Compile-time validation.** sqlx's `query!` macro catches SQL errors before runtime. That's not syntactic sugar — it's a fundamentally different development experience.

4. **Variadic APIs.** When you genuinely need variable-length arguments in Rust, macros are the only option.

5. **Format strings.** Any API that uses format-string-like syntax (`println!`, `format!`, `log::info!`) inherently needs macros to parse the format at compile time.

## A Decision Checklist

Before writing a macro, ask yourself:

- **Can a function do this?** If yes, write a function.
- **Can generics and traits do this?** If yes, use generics.
- **Does an existing crate do this?** If yes, depend on it.
- **Will I be the only person who understands this?** If yes, reconsider.
- **Is the boilerplate I'm eliminating genuinely repetitive?** "I typed similar code twice" is not enough justification. "I typed identical code for 15 types" is.
- **Will the error messages be acceptable?** If users will struggle to understand compilation failures, the macro costs more than it saves.

Macros are one of Rust's most powerful features. Like all powerful features, they demand restraint. The best macro is one that's invisible to its users — it just works, the errors are clear, and nobody needs to read the implementation to use it effectively. If your macro doesn't meet that bar, a regular function is probably the better choice.

---

That wraps up this course on Rust macros and metaprogramming. We went from "why macros exist" through `macro_rules!` patterns, hygiene, debugging, all three kinds of procedural macros, the `syn`/`quote` ecosystem, real-world patterns from major crates, and finally the anti-patterns to avoid.

The progression I'd recommend from here: start with `macro_rules!` for your first few macros. Get comfortable with fragment specifiers and repetition. When you hit the limits — identifier manipulation, complex parsing, external validation — move to proc macros. And always, always ask whether a function would be simpler.
