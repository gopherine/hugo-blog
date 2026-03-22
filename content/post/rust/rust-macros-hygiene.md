---
title: "Lesson 4: Macro Hygiene — Scoping and naming pitfalls"
author: Atharva Pandey
series: "Rust Macros & Metaprogramming"
lesson: 4
keywords: [Rust, rust, macros, hygiene, scoping, macro_rules, variable shadowing]
tags: [Rust tutorial, rust, macros, metaprogramming]
date: '2025-02-12T16:10:00.000Z'
---

I once spent an embarrassing amount of time debugging a macro that worked perfectly in one file and broke in another. Same macro, same input, different behavior. Turned out the expansion was referencing a variable called `result` — which happened to shadow a `result` variable at the call site. The macro was hygienically correct in isolation but collided with the caller's namespace. That's when I actually understood what "macro hygiene" means and why Rust only gets it partially right.

## What Is Macro Hygiene?

Hygiene is about name isolation. When a macro introduces a variable, that variable shouldn't interfere with variables at the call site. And when the call site passes a name into a macro, the macro shouldn't accidentally capture it.

In an unhygienic macro system (like C's preprocessor), this is a constant problem:

```c
// C macro — NOT Rust
#define DOUBLE(x) ({ int tmp = (x); tmp + tmp; })

int tmp = 5;
int result = DOUBLE(tmp); // Bug: the macro's 'tmp' shadows the caller's 'tmp'
```

Rust's `macro_rules!` system is *partially* hygienic. Variables introduced by the macro get their own scope and won't collide with the caller's variables. But there are important exceptions and edge cases.

## Rust's Hygiene Model

### Local Variables Are Hygienic

Variables created inside a macro expansion live in their own "syntax context." They don't interfere with identically-named variables at the call site:

```rust
macro_rules! make_x {
    ($val:expr) => {
        let x = $val;
        println!("macro's x = {}", x);
    };
}

fn main() {
    let x = 100;
    make_x!(42);
    println!("caller's x = {}", x); // still 100, not 42
}
```

The macro creates its own `x` that's invisible to the caller. The caller's `x` survives unchanged. This is hygiene doing its job.

### But Items Are NOT Hygienic

Functions, structs, enums, traits — items defined in a macro expansion — are *not* hygienic. They're visible at the call site:

```rust
macro_rules! define_helper {
    () => {
        fn helper() -> &'static str {
            "I was defined by a macro"
        }
    };
}

fn main() {
    define_helper!();
    println!("{}", helper()); // works — helper is visible
}
```

This is by design. You *want* macros to be able to define structs, functions, and trait implementations that the caller can use. But it means name collisions are possible for items:

```rust
macro_rules! define_helper {
    () => {
        fn helper() -> &'static str {
            "macro version"
        }
    };
}

fn helper() -> &'static str {
    "original version"
}

fn main() {
    // define_helper!(); // ERROR: duplicate definition of `helper`
    println!("{}", helper());
}
```

If you uncomment that macro call, you get a compile error. Two functions with the same name in the same scope.

## The Classic Hygiene Pitfall

Here's the bug pattern that bites most people. Your macro creates a temporary variable, and the caller happens to pass an expression that references a variable with the same name:

```rust
macro_rules! calculate {
    ($expr:expr) => {
        {
            let value = $expr;
            let result = value * 2;
            result
        }
    };
}

fn main() {
    let value = 10;
    let output = calculate!(value + 5); // This works fine!
    println!("{}", output); // 30
}
```

Wait — this actually works? Yes! Because of hygiene, the macro's `value` and the caller's `value` are in different syntax contexts. The `$expr` captured `value + 5` from the caller's context, and when it's substituted into `let value = $expr`, the right-hand side still refers to the caller's `value`.

But here's where it gets tricky:

```rust
macro_rules! with_binding {
    ($name:ident, $val:expr, $body:expr) => {
        {
            let $name = $val;
            $body
        }
    };
}

fn main() {
    // This works — the macro introduces 'x' and the body uses it
    let result = with_binding!(x, 42, x + 1);
    println!("{}", result); // 43
}
```

Here `$name` is an identifier *from the caller's context*. When the macro expands `let $name = $val`, the `$name` retains the caller's syntax context. So `$body` (also from the caller's context) can see it. This works because both the binding and the usage share the same syntax context — the caller's.

If the macro tried to introduce its own hardcoded name and expect the caller's expression to use it, that would fail:

```rust
macro_rules! broken_binding {
    ($body:expr) => {
        {
            let magic_value = 42;
            $body  // Can $body see magic_value? NO.
        }
    };
}

fn main() {
    // This will NOT compile:
    // let result = broken_binding!(magic_value + 1);
    // error: cannot find value `magic_value` in this scope
}
```

The macro's `magic_value` is in the macro's syntax context. The caller's `magic_value` (if they wrote one) is in the caller's syntax context. They don't match.

## Working Around Hygiene

Sometimes you *need* the macro to expose a binding to the caller. The idiomatic way: accept the name as a parameter.

```rust
macro_rules! timed {
    ($elapsed:ident, $body:block) => {
        let start = std::time::Instant::now();
        $body
        let $elapsed = start.elapsed();
    };
}

fn main() {
    timed!(duration, {
        let mut sum = 0u64;
        for i in 0..1_000_000 {
            sum += i;
        }
        println!("sum = {sum}");
    });
    println!("took {:?}", duration);
}
```

The caller picks the name `duration`. The macro binds it. Both sides agree on the name because it came from the caller.

## Path Resolution

Another hygiene concern: what happens when a macro references external types or functions? If the caller hasn't imported them, the expansion fails.

```rust
macro_rules! make_hashmap {
    ($($k:expr => $v:expr),* $(,)?) => {
        {
            // This references HashMap — is it in scope?
            let mut map = std::collections::HashMap::new();
            $(map.insert($k, $v);)*
            map
        }
    };
}

fn main() {
    // Works because we used the full path std::collections::HashMap
    let m = make_hashmap!("a" => 1, "b" => 2);
    println!("{:?}", m);
}
```

Always use fully qualified paths in macro expansions. Don't assume the caller has imported anything. This is why you'll see `::std::vec::Vec` or `::core::option::Option` in well-written macros — the leading `::` ensures it resolves from the crate root, not from whatever module the caller happens to be in.

```rust
macro_rules! robust_vec {
    ($($x:expr),* $(,)?) => {
        {
            let mut v = ::std::vec::Vec::new();
            $(v.push($x);)*
            v
        }
    };
}
```

For library crates, there's an extra consideration. If your macro is used by external crates, `std` paths work, but paths to your own crate's items need special handling. The `$crate` metavariable resolves to the name of the crate where the macro is defined:

```rust
// In your library crate 'mylib'
pub fn internal_helper() -> i32 { 42 }

#[macro_export]
macro_rules! use_helper {
    () => {
        $crate::internal_helper()
    };
}
```

`$crate` is essential for exported macros. Without it, the user would need to know your crate's internal module structure, and any refactoring on your side would break their code.

## Variable Shadowing in Macros

Shadowing inside macros works the same as regular Rust, but the interaction with hygiene creates subtle behavior:

```rust
macro_rules! shadow_demo {
    ($x:expr) => {
        let val = $x;
        let val = val + 1;  // shadows the previous val
        println!("macro's val = {}", val);
    };
}

fn main() {
    let val = 100;
    shadow_demo!(val);
    println!("caller's val = {}", val); // still 100
}
```

The macro shadows its own `val` — that's fine, it's all within the macro's syntax context. The caller's `val` is untouched.

Where it gets confusing is when macros are called multiple times:

```rust
macro_rules! counter {
    ($label:expr) => {
        let count = 0;
        println!("{}: {}", $label, count);
    };
}

fn main() {
    counter!("first");
    counter!("second");
    // Each expansion creates its own 'count' — no collision
}
```

Each macro invocation creates a fresh syntax context. The two `count` variables don't interact.

## The Mut Problem

A common frustration: you want the macro to mutate a caller's variable, but hygiene gets in the way.

```rust
macro_rules! push_all {
    ($vec:expr, $($item:expr),*) => {
        $($vec.push($item);)*
    };
}

fn main() {
    let mut items = vec![1, 2, 3];
    push_all!(items, 4, 5, 6);
    println!("{:?}", items); // [1, 2, 3, 4, 5, 6]
}
```

This works because `$vec` captures `items` from the caller's context, and the expansion calls `.push()` on it. The macro doesn't introduce any new bindings — it just operates on the caller's expression. Hygiene isn't an issue here because no new names are created.

But if your macro tried to create a mutable binding internally and return it:

```rust
macro_rules! make_vec {
    ($($item:expr),*) => {
        {
            let mut v = Vec::new();
            $(v.push($item);)*
            v  // returned — caller gets an immutable binding unless they use let mut
        }
    };
}

fn main() {
    let data = make_vec!(1, 2, 3);
    // data is immutable here — the mut was internal to the macro
    println!("{:?}", data);
}
```

The `mut` on `v` inside the macro is for the macro's internal use. When `v` is returned as the block's value, the caller binds it however they want. This is actually clean behavior.

## Testing Hygiene

Here's a practical test you can run to verify your macro's hygiene:

```rust
macro_rules! hygienic_test {
    ($x:expr) => {
        {
            // Use names that might collide
            let result = $x;
            let temp = result;
            let output = temp;
            output
        }
    };
}

fn main() {
    // Try to trip up the macro with same-named variables
    let result = 1;
    let temp = 2;
    let output = 3;

    let a = hygienic_test!(result);   // should be 1
    let b = hygienic_test!(temp);     // should be 2
    let c = hygienic_test!(output);   // should be 3

    assert_eq!(a, 1);
    assert_eq!(b, 2);
    assert_eq!(c, 3);
    println!("all hygienic: {a} {b} {c}");
}
```

If all assertions pass, your macro is hygienic with respect to those names. Do this with every internal variable name your macro uses.

## Rules of Thumb

After writing and debugging enough macros, here's what I stick to:

1. **Always use fully qualified paths.** `::std::collections::HashMap`, not `HashMap`. Use `$crate` for your own crate's items.

2. **Prefix internal variables.** If hygiene isn't enough (and sometimes it isn't, especially with items), prefix internal names: `__macro_internal_result` instead of `result`. Ugly? Yes. Safe? Also yes.

3. **Accept names as parameters.** If the caller needs to use a binding your macro creates, make them pass the name in. Don't rely on magic variable names.

4. **Test with adversarial inputs.** Call your macro with expressions that use the same variable names as your macro's internals. If it breaks, you have a hygiene bug.

5. **Wrap expansions in blocks.** `{ let x = ...; x }` limits the scope of internal variables. This is the simplest way to prevent leakage.

6. **Don't generate items with common names.** If your macro defines a function called `new` or `default`, it will eventually collide with something. Use descriptive names or accept the name as a parameter.

Hygiene is one of those things that's invisible when it works and infuriating when it doesn't. Understanding the model — local variables are isolated, items are not, paths need qualification — saves you from the subtle bugs that only surface when your macro is used in an unexpected context.

Next up: debugging macros with `cargo-expand` and `trace_macros`. Because when your macro does expand but produces wrong code, you need to see exactly what it generated.
