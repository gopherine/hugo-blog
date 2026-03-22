---
title: "Lesson 7: The Visitor Pattern via Enums — When trait objects won't cut it"
author: Atharva Pandey
series: "Rust Pattern Matching & Enums Deep Dive"
lesson: 7
keywords: [Rust, rust, pattern matching, enums, visitor pattern, enum dispatch, trait objects, dynamic dispatch]
tags: [Rust tutorial, rust, pattern-matching]
date: '2024-08-05T20:15:00.000Z'
---

I spent a week once trying to make trait objects work for an AST walker. Every new operation meant a new trait, a new impl block for every node type, and a growing pile of boilerplate. When I rewrote the whole thing with an enum and match, the code shrank by 60% and got faster. Not every problem needs dynamic dispatch.

## The Problem: Open vs. Closed Hierarchies

In object-oriented languages, the visitor pattern exists because class hierarchies are "open" — anyone can add new subclasses, so you can't write a switch over all possible types. You need double dispatch through interfaces.

Rust enums are "closed" — all variants are defined in one place. This changes the equation completely. When you know all the variants at compile time, you don't need the visitor pattern's indirection. A `match` does the job with zero overhead.

But there's a deeper question: **when should you use enums with match, and when should you use trait objects?** Getting this wrong leads to either unmaintainable `match` arms or unnecessarily complex trait hierarchies.

## Trait Objects: The OOP Approach

Here's the trait object approach to a simple expression evaluator:

```rust
trait Expr {
    fn evaluate(&self) -> f64;
    fn to_string(&self) -> String;
}

struct Literal {
    value: f64,
}

struct Add {
    left: Box<dyn Expr>,
    right: Box<dyn Expr>,
}

struct Multiply {
    left: Box<dyn Expr>,
    right: Box<dyn Expr>,
}

impl Expr for Literal {
    fn evaluate(&self) -> f64 {
        self.value
    }
    fn to_string(&self) -> String {
        format!("{}", self.value)
    }
}

impl Expr for Add {
    fn evaluate(&self) -> f64 {
        self.left.evaluate() + self.right.evaluate()
    }
    fn to_string(&self) -> String {
        format!("({} + {})", self.left.to_string(), self.right.to_string())
    }
}

impl Expr for Multiply {
    fn evaluate(&self) -> f64 {
        self.left.evaluate() * self.right.evaluate()
    }
    fn to_string(&self) -> String {
        format!("({} * {})", self.left.to_string(), self.right.to_string())
    }
}

fn main() {
    // (3 + 4) * 2
    let expr: Box<dyn Expr> = Box::new(Multiply {
        left: Box::new(Add {
            left: Box::new(Literal { value: 3.0 }),
            right: Box::new(Literal { value: 4.0 }),
        }),
        right: Box::new(Literal { value: 2.0 }),
    });

    println!("{} = {}", expr.to_string(), expr.evaluate());
}
```

This works. But now try adding a new operation — say, `count_nodes()`. You need to add a method to the `Expr` trait, then implement it for every struct. With three node types, that's manageable. With twenty node types in a real compiler, it's a nightmare.

And there's a subtle problem: the trait approach makes it easy to add new *types* but hard to add new *operations*. This is the expression problem, and it matters a lot for domain-specific applications.

## The Idiomatic Way: Enum Dispatch

With enums, operations are just functions with a `match`:

```rust
#[derive(Debug, Clone)]
enum Expr {
    Literal(f64),
    Add(Box<Expr>, Box<Expr>),
    Multiply(Box<Expr>, Box<Expr>),
    Negate(Box<Expr>),
}

fn evaluate(expr: &Expr) -> f64 {
    match expr {
        Expr::Literal(n) => *n,
        Expr::Add(left, right) => evaluate(left) + evaluate(right),
        Expr::Multiply(left, right) => evaluate(left) * evaluate(right),
        Expr::Negate(inner) => -evaluate(inner),
    }
}

fn to_string(expr: &Expr) -> String {
    match expr {
        Expr::Literal(n) => format!("{}", n),
        Expr::Add(left, right) => {
            format!("({} + {})", to_string(left), to_string(right))
        }
        Expr::Multiply(left, right) => {
            format!("({} * {})", to_string(left), to_string(right))
        }
        Expr::Negate(inner) => format!("(-{})", to_string(inner)),
    }
}

// Adding a new operation is just adding a new function
fn count_nodes(expr: &Expr) -> usize {
    match expr {
        Expr::Literal(_) => 1,
        Expr::Add(left, right) | Expr::Multiply(left, right) => {
            1 + count_nodes(left) + count_nodes(right)
        }
        Expr::Negate(inner) => 1 + count_nodes(inner),
    }
}

fn depth(expr: &Expr) -> usize {
    match expr {
        Expr::Literal(_) => 1,
        Expr::Add(left, right) | Expr::Multiply(left, right) => {
            1 + depth(left).max(depth(right))
        }
        Expr::Negate(inner) => 1 + depth(inner),
    }
}

fn main() {
    // (3 + 4) * (-2)
    let expr = Expr::Multiply(
        Box::new(Expr::Add(
            Box::new(Expr::Literal(3.0)),
            Box::new(Expr::Literal(4.0)),
        )),
        Box::new(Expr::Negate(Box::new(Expr::Literal(2.0)))),
    );

    println!("{} = {}", to_string(&expr), evaluate(&expr));
    println!("Nodes: {}, Depth: {}", count_nodes(&expr), depth(&expr));
}
```

Adding `count_nodes()` and `depth()` required zero changes to existing code. No traits to modify, no impl blocks to update. Just a new function with a `match`.

## Transformations: Returning Modified Trees

One of the biggest advantages of enum dispatch over trait objects is tree transformations. With trait objects, you'd need downcasting or a complex visitor interface. With enums, you just build a new tree:

```rust
#[derive(Debug, Clone)]
enum Expr {
    Literal(f64),
    Add(Box<Expr>, Box<Expr>),
    Multiply(Box<Expr>, Box<Expr>),
    Negate(Box<Expr>),
}

fn simplify(expr: Expr) -> Expr {
    match expr {
        // Constant folding: evaluate operations on two literals
        Expr::Add(left, right) => {
            let left = simplify(*left);
            let right = simplify(*right);
            match (&left, &right) {
                (Expr::Literal(a), Expr::Literal(b)) => Expr::Literal(a + b),
                _ => Expr::Add(Box::new(left), Box::new(right)),
            }
        }
        Expr::Multiply(left, right) => {
            let left = simplify(*left);
            let right = simplify(*right);
            match (&left, &right) {
                (Expr::Literal(a), Expr::Literal(b)) => Expr::Literal(a * b),
                // Multiply by zero
                (Expr::Literal(n), _) | (_, Expr::Literal(n)) if *n == 0.0 => {
                    Expr::Literal(0.0)
                }
                // Multiply by one
                (Expr::Literal(n), other) | (other, Expr::Literal(n)) if *n == 1.0 => {
                    other.clone()
                }
                _ => Expr::Multiply(Box::new(left), Box::new(right)),
            }
        }
        // Double negation
        Expr::Negate(inner) => match simplify(*inner) {
            Expr::Negate(double_inner) => *double_inner,
            Expr::Literal(n) => Expr::Literal(-n),
            simplified => Expr::Negate(Box::new(simplified)),
        },
        other => other,
    }
}

fn format_expr(expr: &Expr) -> String {
    match expr {
        Expr::Literal(n) => format!("{}", n),
        Expr::Add(l, r) => format!("({} + {})", format_expr(l), format_expr(r)),
        Expr::Multiply(l, r) => format!("({} * {})", format_expr(l), format_expr(r)),
        Expr::Negate(inner) => format!("(-{})", format_expr(inner)),
    }
}

fn main() {
    // (3 + 4) * 1 => should simplify to 7
    let expr = Expr::Multiply(
        Box::new(Expr::Add(
            Box::new(Expr::Literal(3.0)),
            Box::new(Expr::Literal(4.0)),
        )),
        Box::new(Expr::Literal(1.0)),
    );
    println!("Before: {}", format_expr(&expr));
    let simplified = simplify(expr);
    println!("After:  {}", format_expr(&simplified));

    // -(-x) => x
    let expr2 = Expr::Negate(Box::new(Expr::Negate(Box::new(Expr::Literal(5.0)))));
    println!("\nBefore: {}", format_expr(&expr2));
    let simplified2 = simplify(expr2);
    println!("After:  {}", format_expr(&simplified2));
}
```

The `simplify` function takes an `Expr` by value, matches on it, recursively simplifies children, and builds a new tree. This is natural with enums. With trait objects, you'd need `Box<dyn Expr>` returns and downcasting — painful and error-prone.

## Real-World Example: Command Processing

Here's a pattern I use in production for handling heterogeneous commands:

```rust
use std::collections::HashMap;

#[derive(Debug)]
enum Command {
    Get { key: String },
    Set { key: String, value: String, ttl: Option<u64> },
    Delete { key: String },
    Increment { key: String, amount: i64 },
    BatchGet { keys: Vec<String> },
    Ping,
}

#[derive(Debug)]
enum Response {
    Value(Option<String>),
    Ok,
    Error(String),
    Integer(i64),
    Bulk(Vec<Option<String>>),
    Pong,
}

struct Store {
    data: HashMap<String, String>,
}

impl Store {
    fn new() -> Self {
        Store {
            data: HashMap::new(),
        }
    }

    fn execute(&mut self, cmd: Command) -> Response {
        match cmd {
            Command::Get { key } => {
                Response::Value(self.data.get(&key).cloned())
            }
            Command::Set { key, value, ttl: _ } => {
                self.data.insert(key, value);
                Response::Ok
            }
            Command::Delete { key } => {
                match self.data.remove(&key) {
                    Some(_) => Response::Integer(1),
                    None => Response::Integer(0),
                }
            }
            Command::Increment { key, amount } => {
                let entry = self.data.entry(key).or_insert_with(|| "0".to_string());
                match entry.parse::<i64>() {
                    Ok(current) => {
                        let new_val = current + amount;
                        *entry = new_val.to_string();
                        Response::Integer(new_val)
                    }
                    Err(_) => Response::Error("value is not an integer".to_string()),
                }
            }
            Command::BatchGet { keys } => {
                let values: Vec<Option<String>> = keys
                    .iter()
                    .map(|k| self.data.get(k).cloned())
                    .collect();
                Response::Bulk(values)
            }
            Command::Ping => Response::Pong,
        }
    }
}

fn format_response(resp: &Response) -> String {
    match resp {
        Response::Value(Some(v)) => format!("\"{}\"", v),
        Response::Value(None) => "(nil)".to_string(),
        Response::Ok => "OK".to_string(),
        Response::Error(msg) => format!("ERR {}", msg),
        Response::Integer(n) => format!("(integer) {}", n),
        Response::Bulk(values) => {
            let lines: Vec<String> = values
                .iter()
                .enumerate()
                .map(|(i, v)| match v {
                    Some(s) => format!("{}) \"{}\"", i + 1, s),
                    None => format!("{}) (nil)", i + 1),
                })
                .collect();
            lines.join("\n")
        }
        Response::Pong => "PONG".to_string(),
    }
}

fn main() {
    let mut store = Store::new();

    let commands = vec![
        Command::Ping,
        Command::Set {
            key: "name".to_string(),
            value: "Alice".to_string(),
            ttl: None,
        },
        Command::Set {
            key: "counter".to_string(),
            value: "10".to_string(),
            ttl: None,
        },
        Command::Get { key: "name".to_string() },
        Command::Increment { key: "counter".to_string(), amount: 5 },
        Command::Get { key: "counter".to_string() },
        Command::BatchGet {
            keys: vec!["name".to_string(), "counter".to_string(), "missing".to_string()],
        },
        Command::Delete { key: "name".to_string() },
        Command::Get { key: "name".to_string() },
    ];

    for cmd in commands {
        println!("> {:?}", cmd);
        let resp = store.execute(cmd);
        println!("{}\n", format_response(&resp));
    }
}
```

Both `Command` and `Response` are enums. The `execute` method is a visitor over `Command` variants. `format_response` is a visitor over `Response` variants. Adding a new command means adding a variant, a match arm in `execute`, and nothing else.

## When Trait Objects Win

I don't want to oversell enum dispatch. Trait objects are the right choice when:

1. **The set of types is open.** Plugin systems, middleware chains, generic handlers — when users of your library define new types, you need traits.

2. **You need heterogeneous collections from external code.** If you can't modify the enum definition, trait objects let you extend behavior.

3. **New types are more common than new operations.** If you're constantly adding new node types but rarely adding new operations, trait objects scale better.

The decision framework I use:

```rust
// Use enums when:
// - You own all the variants
// - New operations are more common than new types
// - You need exhaustive matching
// - You want zero-cost dispatch
// - You need transformations (building new trees)

// Use trait objects when:
// - External code defines types
// - New types are more common than new operations
// - You need dynamic dispatch (plugins, callbacks)
// - You don't need exhaustive matching

// Quick example of when traits are better:
trait Middleware {
    fn process(&self, request: &str) -> String;
}

struct Logger;
struct RateLimiter;
struct Auth;

impl Middleware for Logger {
    fn process(&self, request: &str) -> String {
        println!("LOG: {}", request);
        request.to_string()
    }
}

impl Middleware for RateLimiter {
    fn process(&self, request: &str) -> String {
        // In reality, you'd check rate limits here
        request.to_string()
    }
}

impl Middleware for Auth {
    fn process(&self, request: &str) -> String {
        format!("[authenticated] {}", request)
    }
}

fn run_pipeline(middlewares: &[Box<dyn Middleware>], request: &str) -> String {
    let mut result = request.to_string();
    for mw in middlewares {
        result = mw.process(&result);
    }
    result
}

fn main() {
    let pipeline: Vec<Box<dyn Middleware>> = vec![
        Box::new(Logger),
        Box::new(RateLimiter),
        Box::new(Auth),
    ];

    let result = run_pipeline(&pipeline, "GET /api/users");
    println!("Final: {}", result);
}
```

In the middleware case, users might want to add their own middleware types. An enum wouldn't work because you'd have to modify the source every time someone wanted a new middleware. Traits are the right tool here.

## Performance: Enum Dispatch vs. Trait Objects

Enum dispatch is a static `match` — the compiler often optimizes it into a jump table or a series of comparisons. No vtable lookup, no indirection, no cache misses from chasing pointers.

Trait objects use dynamic dispatch — a vtable pointer, an indirect function call, and the associated cache pressure. For most applications, this doesn't matter. For tight loops processing millions of nodes (like a compiler's optimization passes), it matters a lot.

I've measured 2-5x speedups from converting trait-object-based AST walkers to enum-based ones in hot paths. Your mileage will vary, but if you're doing performance-sensitive tree processing, benchmark both.

Enum dispatch through pattern matching is one of Rust's strongest features. It gives you exhaustive, zero-cost, transformation-friendly dispatch over closed type hierarchies. Combined with everything else in this series — destructuring, guards, or patterns — it handles surprisingly complex problems with clean, maintainable code.

Next and final: refutable vs. irrefutable patterns. The distinction that determines where you can use each type of pattern in Rust.
