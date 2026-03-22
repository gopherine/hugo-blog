---
title: "Lesson 3: Match Guards and Bindings — Fine-grained control"
author: Atharva Pandey
series: "Rust Pattern Matching & Enums Deep Dive"
lesson: 3
keywords: [Rust, rust, pattern matching, match guards, if guard, variable binding, match arm]
tags: [Rust tutorial, rust, pattern-matching]
date: '2024-07-25T08:30:00.000Z'
---

A few months back I was writing a rate limiter. The logic was simple: if the request is from an internal IP *and* the rate is under the limit, allow it. If it's external *and* under a different limit, allow it. Otherwise, reject. I started with nested `if` statements inside match arms and ended up with something that looked like a plate of spaghetti. Then I rewrote it with match guards and the whole function collapsed to six clean lines.

Match guards are the tool you reach for when the *shape* of the data isn't enough and you need to inspect the *values* too.

## The Problem

Patterns in Rust are structural — they match on types, variants, and shapes. But sometimes you need conditional logic that goes beyond structure:

```rust
enum Temperature {
    Celsius(f64),
    Fahrenheit(f64),
}

// You want to check if a temperature is "comfortable"
// Patterns alone can't express "Celsius between 20 and 26"
fn is_comfortable(temp: &Temperature) -> bool {
    match temp {
        Temperature::Celsius(c) => *c >= 20.0 && *c <= 26.0,
        Temperature::Fahrenheit(f) => *f >= 68.0 && *f <= 79.0,
    }
}
```

This works, but the match arm body is doing two things: the condition check *and* the return value. When you have more arms with more conditions, this pattern breaks down fast.

## The Idiomatic Way: Match Guards

A match guard is an `if` condition attached to a match arm. The arm only matches if both the pattern *and* the guard are true:

```rust
enum Temperature {
    Celsius(f64),
    Fahrenheit(f64),
}

fn comfort_level(temp: &Temperature) -> &'static str {
    match temp {
        Temperature::Celsius(c) if *c < 0.0 => "freezing",
        Temperature::Celsius(c) if *c < 15.0 => "cold",
        Temperature::Celsius(c) if *c <= 26.0 => "comfortable",
        Temperature::Celsius(c) if *c <= 35.0 => "hot",
        Temperature::Celsius(_) => "extreme",

        Temperature::Fahrenheit(f) if *f < 32.0 => "freezing",
        Temperature::Fahrenheit(f) if *f < 59.0 => "cold",
        Temperature::Fahrenheit(f) if *f <= 79.0 => "comfortable",
        Temperature::Fahrenheit(f) if *f <= 95.0 => "hot",
        Temperature::Fahrenheit(_) => "extreme",
    }
}

fn main() {
    let temps = vec![
        Temperature::Celsius(-5.0),
        Temperature::Celsius(22.0),
        Temperature::Celsius(40.0),
        Temperature::Fahrenheit(72.0),
        Temperature::Fahrenheit(100.0),
    ];

    for t in &temps {
        println!("{}", comfort_level(t));
    }
}
```

The guard comes after the pattern but before the `=>`. The variable `c` is bound by the pattern and available in the guard. Clean, readable, and each arm has a single responsibility.

## Guards Don't Affect Exhaustiveness

This is the critical thing to understand: **match guards don't count toward exhaustiveness checking.** The compiler treats a guarded arm as potentially non-matching, so you still need a catch-all:

```rust
fn describe_number(n: i32) -> &'static str {
    match n {
        x if x < 0 => "negative",
        0 => "zero",
        x if x > 0 => "positive",
        // Without this, the compiler complains — even though
        // mathematically, these three arms cover all i32 values.
        // The compiler can't prove guards are exhaustive.
        _ => unreachable!(),
    }
}

fn main() {
    println!("{}", describe_number(-5));
    println!("{}", describe_number(0));
    println!("{}", describe_number(42));
}
```

The `unreachable!()` macro signals to the compiler (and to readers) that this branch should never execute. If it does, the program panics with a clear message. I prefer this over `_` with some dummy return value — it's honest about the programmer's intent.

## Combining Guards With Destructuring

Guards really shine when combined with the destructuring from the previous lesson:

```rust
#[derive(Debug)]
struct HttpResponse {
    status: u16,
    body: String,
    content_length: usize,
}

fn log_response(resp: &HttpResponse) {
    match resp {
        HttpResponse { status: 200, body, .. } if body.is_empty() => {
            println!("200 OK (empty body)");
        }
        HttpResponse { status: 200, content_length, .. } if *content_length > 1_000_000 => {
            println!("200 OK (large response: {} bytes)", content_length);
        }
        HttpResponse { status: 200, .. } => {
            println!("200 OK");
        }
        HttpResponse { status, body, .. } if *status >= 400 && *status < 500 => {
            println!("Client error {}: {}", status, body);
        }
        HttpResponse { status, body, .. } if *status >= 500 => {
            println!("Server error {}: {}", status, body);
        }
        HttpResponse { status, .. } => {
            println!("Status: {}", status);
        }
    }
}

fn main() {
    let responses = vec![
        HttpResponse { status: 200, body: String::new(), content_length: 0 },
        HttpResponse { status: 200, body: "ok".to_string(), content_length: 2_000_000 },
        HttpResponse { status: 200, body: "ok".to_string(), content_length: 100 },
        HttpResponse { status: 404, body: "not found".to_string(), content_length: 9 },
        HttpResponse { status: 503, body: "overloaded".to_string(), content_length: 10 },
    ];

    for r in &responses {
        log_response(r);
    }
}
```

Each arm destructures the struct *and* applies conditions. The order matters — Rust evaluates arms top to bottom and picks the first match. That 200-with-empty-body arm has to come before the generic 200 arm, or it'd never trigger.

## Variable Binding in Guards

Variables bound in the pattern are available in both the guard and the arm body. But there's a subtlety — guards borrow the matched value, they don't move it:

```rust
fn process_names(names: Vec<String>) {
    for name in names {
        match name {
            ref n if n.starts_with('A') => {
                println!("Starts with A: {}", n);
            }
            ref n if n.len() > 10 => {
                println!("Long name: {}", n);
            }
            n => {
                println!("Other: {}", n);
            }
        }
    }
}

fn main() {
    let names = vec![
        "Alice".to_string(),
        "Bob".to_string(),
        "Alexandrina".to_string(),
        "Christopher".to_string(),
    ];
    process_names(names);
}
```

The `ref` keyword creates a reference instead of moving the value. In modern Rust, match ergonomics handle most of these cases automatically when you're matching on references, but it's worth knowing the explicit form.

## Guards With Enums: A Practical Example

Here's a permission system I've actually used in production (simplified):

```rust
#[derive(Debug, Clone)]
enum Role {
    Admin,
    Editor { department: String },
    Viewer,
}

#[derive(Debug)]
enum Action {
    Read(String),          // resource name
    Write(String),         // resource name
    Delete(String),        // resource name
    ManageUsers,
}

fn is_allowed(role: &Role, action: &Action) -> bool {
    match (role, action) {
        // Admins can do anything
        (Role::Admin, _) => true,

        // Editors can read anything
        (Role::Editor { .. }, Action::Read(_)) => true,

        // Editors can write to their own department's resources
        (Role::Editor { department }, Action::Write(resource))
            if resource.starts_with(department) => true,

        // Viewers can only read
        (Role::Viewer, Action::Read(_)) => true,

        // Everything else is denied
        _ => false,
    }
}

fn main() {
    let editor = Role::Editor { department: "engineering".to_string() };

    let cases = vec![
        (editor.clone(), Action::Read("sales/report".to_string())),
        (editor.clone(), Action::Write("engineering/config".to_string())),
        (editor.clone(), Action::Write("sales/report".to_string())),
        (editor.clone(), Action::Delete("engineering/old".to_string())),
        (Role::Admin, Action::Delete("anything".to_string())),
        (Role::Viewer, Action::Read("public/page".to_string())),
        (Role::Viewer, Action::Write("public/page".to_string())),
    ];

    for (role, action) in &cases {
        println!("{:?} -> {:?}: {}", role, action, is_allowed(role, action));
    }
}
```

That tuple match `(role, action)` is a trick worth remembering — you can match on multiple values simultaneously by packing them into a tuple. The guard on the editor-write arm checks if the resource belongs to the editor's department. Pattern + guard + tuple matching. Powerful stuff.

## Performance Considerations

Guards are evaluated at runtime, unlike patterns which are compiled into efficient jump tables. For hot code paths, keep guards cheap. Don't call expensive functions in guard positions — if you need complex logic, bind the variable in the pattern and do the computation in the arm body:

```rust
struct Request {
    path: String,
    headers: Vec<(String, String)>,
}

fn has_header(headers: &[(String, String)], key: &str) -> bool {
    headers.iter().any(|(k, _)| k == key)
}

fn route(req: &Request) {
    match req {
        // Fine — string prefix check is O(1)
        Request { path, .. } if path.starts_with("/api/") => {
            println!("API route: {}", path);
        }
        // Fine for most cases, but note this scans the headers vec
        Request { headers, path, .. } if has_header(headers, "Authorization") => {
            println!("Authenticated request to {}", path);
        }
        Request { path, .. } => {
            println!("Public route: {}", path);
        }
    }
}

fn main() {
    let req = Request {
        path: "/api/users".to_string(),
        headers: vec![("Content-Type".to_string(), "application/json".to_string())],
    };
    route(&req);

    let req2 = Request {
        path: "/dashboard".to_string(),
        headers: vec![("Authorization".to_string(), "Bearer xyz".to_string())],
    };
    route(&req2);
}
```

## My Rules for Guards

After writing a lot of Rust, I've settled on a few guidelines:

1. **Keep guards simple.** If a guard is more than one condition with `&&`, consider splitting the arm.
2. **Order matters.** Put more specific guarded arms before less specific ones. Rust picks the first match.
3. **Don't duplicate logic.** If the same guard appears on multiple arms, there's probably a better way to structure the match.
4. **Use `unreachable!()` honestly.** If your guards are logically exhaustive but the compiler can't prove it, use `unreachable!()` — not a silent `_ => default`.

Match guards bridge the gap between structural matching and arbitrary conditions. They keep your control flow in a single `match` instead of scattering it across nested `if`/`else` blocks. Combined with destructuring, they handle surprisingly complex logic in a flat, readable way.

Next up: or patterns, `@` bindings, and rest patterns — the full pattern syntax that most Rust developers only discover when they need it.
