---
title: "Lesson 6: Control Flow — if, loop, while, for — and why there's no ternary"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 6
keywords: [Rust, rust, control flow, if else, loop, while, for, match, pattern matching]
tags: [Rust tutorial, rust, beginner]
date: '2024-03-14T19:10:00.000Z'
---

I once reviewed a pull request that had seven levels of nested if-else. In Go. The author said "the language doesn't give me better tools." In Rust, you've got `match`, labeled loops, `if let`, and blocks-as-expressions — enough to keep your code flat and readable even when the logic is complex.

## if / else

```rust
fn main() {
    let temperature = 35;

    if temperature > 30 {
        println!("It's hot");
    } else if temperature > 20 {
        println!("It's nice");
    } else {
        println!("It's cold");
    }
}
```

Standard stuff. No parentheses around the condition — that's a syntax error in Rust. The braces are mandatory, even for single-line bodies. No arguing about whether to use braces. They're required. Discussion over.

The condition must be a `bool`. Rust doesn't do truthy/falsy:

```rust
fn main() {
    let x = 1;
    // if x { ... }     // ERROR: expected bool, found integer
    if x != 0 {         // OK — explicit comparison
        println!("x is not zero");
    }
}
```

I know some people find this annoying. I think it's correct. Implicit truthiness is a source of bugs in Python, JavaScript, and C. Being explicit costs you a few characters and saves you a class of mistakes.

### if as an Expression (Revisited)

We covered this in the last lesson, but it's worth repeating because it's *that* useful:

```rust
fn main() {
    let x = 10;

    let description = if x > 0 {
        "positive"
    } else if x < 0 {
        "negative"
    } else {
        "zero"
    };

    println!("{x} is {description}");
}
```

This replaces the ternary operator (`? :`) from C-family languages. Rust doesn't have a ternary because `if` already works as an expression. One less syntax to remember.

## loop — Infinite Loops

```rust
fn main() {
    let mut count = 0;

    loop {
        count += 1;
        if count > 5 {
            break;
        }
        println!("count: {count}");
    }

    println!("Done! Final count: {count}");
}
```

`loop` creates an infinite loop. You exit with `break`. Why have a dedicated `loop` keyword when `while true` works? Two reasons:

1. **The compiler knows it's infinite.** It can reason about control flow better. A `while true` loop might not run at all (the compiler can't always prove that `true` is always true after optimizations). `loop` is guaranteed to run at least once.

2. **`loop` can return a value.** This is genuinely useful:

```rust
fn main() {
    let mut counter = 0;

    let result = loop {
        counter += 1;
        if counter == 10 {
            break counter * 2;  // loop "returns" 20
        }
    };

    println!("result: {result}");  // 20
}
```

You pass a value to `break`, and the loop expression evaluates to that value. Try doing *that* with `while true` in other languages.

## while

```rust
fn main() {
    let mut n = 1;

    while n <= 64 {
        println!("{n}");
        n *= 2;
    }
}
```

Standard while loop. Condition is checked before each iteration. Nothing surprising here.

One thing I want to flag: in Rust, you'll rarely use `while` for iterating over collections. `for` is almost always better. `while` is for genuinely conditional loops — "keep going until this condition changes."

## for — The Workhorse

```rust
fn main() {
    // Range (exclusive end)
    for i in 0..5 {
        println!("{i}");  // 0, 1, 2, 3, 4
    }

    // Range (inclusive end)
    for i in 0..=5 {
        println!("{i}");  // 0, 1, 2, 3, 4, 5
    }
}
```

`for` in Rust iterates over anything that implements the `Iterator` trait. Ranges (`0..5`, `0..=5`) are the simplest case, but you can iterate over arrays, vectors, strings, hash maps, file lines — anything.

```rust
fn main() {
    let names = ["Alice", "Bob", "Charlie"];

    for name in names {
        println!("Hello, {name}!");
    }
}
```

### Iterating with Index

Need the index? Use `.iter().enumerate()`:

```rust
fn main() {
    let colors = ["red", "green", "blue"];

    for (index, color) in colors.iter().enumerate() {
        println!("{index}: {color}");
    }
}
```

### Iterating in Reverse

```rust
fn main() {
    for i in (1..=5).rev() {
        println!("{i}");  // 5, 4, 3, 2, 1
    }
}
```

### Iterating with Step

There's no built-in step syntax like Python's `range(0, 10, 2)`, but you can use `.step_by()`:

```rust
fn main() {
    for i in (0..20).step_by(3) {
        println!("{i}");  // 0, 3, 6, 9, 12, 15, 18
    }
}
```

## break and continue

Same semantics as C-family languages:

```rust
fn main() {
    for i in 0..10 {
        if i % 2 == 0 {
            continue;  // skip even numbers
        }
        if i > 7 {
            break;     // stop at 7
        }
        println!("{i}");  // 1, 3, 5, 7
    }
}
```

## Labeled Loops — An Underrated Feature

When you have nested loops, you can label them and break/continue from specific ones:

```rust
fn main() {
    'outer: for x in 0..5 {
        for y in 0..5 {
            if x + y > 4 {
                println!("Breaking outer at x={x}, y={y}");
                continue 'outer;
            }
            print!("({x},{y}) ");
        }
        println!();
    }
}
```

The label syntax is `'name:` before the loop. Then `break 'name` or `continue 'name` targets that specific loop. This eliminates the need for boolean flags to break out of nested loops — a pattern I used to write all the time in Java and hated every time.

You can even return values from labeled loops:

```rust
fn main() {
    let result = 'search: {
        for x in 0..100 {
            for y in 0..100 {
                if x * y == 4896 {
                    break 'search (x, y);
                }
            }
        }
        (0, 0) // not found
    };

    println!("Found: {:?}", result);
}
```

## if let — Pattern Matching Light

When you want to match one specific pattern and ignore everything else:

```rust
fn main() {
    let some_value: Option<i32> = Some(42);

    if let Some(x) = some_value {
        println!("Got a value: {x}");
    } else {
        println!("No value");
    }
}
```

Don't worry about `Option` and `Some` yet — we'll cover them in Lesson 12. The point here is the syntax: `if let` combines pattern matching with conditional execution. It's cleaner than a full `match` when you only care about one variant.

## while let

Same idea as `if let`, but looping:

```rust
fn main() {
    let mut stack = vec![1, 2, 3, 4, 5];

    while let Some(top) = stack.pop() {
        println!("Popped: {top}");
    }

    println!("Stack is empty now");
}
```

This pops elements until `stack.pop()` returns `None`, at which point the loop ends. Clean, readable, no explicit `break` needed.

## A Practical Example: FizzBuzz

Can't have a programming tutorial without FizzBuzz:

```rust
fn fizzbuzz(n: u32) -> String {
    match (n % 3, n % 5) {
        (0, 0) => String::from("FizzBuzz"),
        (0, _) => String::from("Fizz"),
        (_, 0) => String::from("Buzz"),
        _ => n.to_string(),
    }
}

fn main() {
    for i in 1..=30 {
        println!("{}: {}", i, fizzbuzz(i));
    }
}
```

This uses `match`, which we'll cover properly in Lesson 13. But notice how clean this is — pattern matching on a tuple of remainders. No nested ifs. No messy boolean logic.

## Another Example: Finding Primes

```rust
fn is_prime(n: u32) -> bool {
    if n < 2 {
        return false;
    }
    if n == 2 {
        return true;
    }
    if n % 2 == 0 {
        return false;
    }

    let mut i = 3;
    while i * i <= n {
        if n % i == 0 {
            return false;
        }
        i += 2;
    }
    true
}

fn main() {
    let primes: Vec<u32> = (2..100).filter(|&n| is_prime(n)).collect();
    println!("Primes under 100: {:?}", primes);
    println!("Count: {}", primes.len());
}
```

The main function uses iterator methods we haven't covered yet (Lesson 22). Don't worry about the syntax — focus on how `is_prime` uses early returns with `return` and an implicit return at the end. Both styles work together.

## What Rust Doesn't Have

A few control flow constructs you might expect from other languages that Rust intentionally omits:

- **No ternary operator** (`? :`) — `if` is already an expression
- **No do-while** — use `loop` with a `break` condition at the end
- **No switch/case with fallthrough** — `match` is strictly better (Lesson 13)
- **No goto** — labeled breaks cover the legitimate use cases

Each omission is deliberate. Rust's control flow is small, consistent, and expressive. You don't need twelve different ways to branch.

## When to Use What

My guidelines:

- **`if/else`** — simple branching on a boolean condition
- **`match`** — branching on multiple patterns (Lesson 13)
- **`for`** — iterating over collections or ranges (90% of your loops)
- **`while`** — looping until a condition changes
- **`loop`** — infinite loops, or when the loop itself computes a value
- **`if let`** — matching one specific pattern
- **`while let`** — looping while a pattern matches

That covers control flow. Next lesson is the big one — ownership. It's the concept that makes Rust different from every other language you've used, and it's the concept that most people struggle with initially. But once it clicks, everything else in the language makes sense.
