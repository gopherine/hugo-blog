---
title: "Lesson 5: Enums Carrying Data — Modeling real domains"
author: Atharva Pandey
series: "Rust Pattern Matching & Enums Deep Dive"
lesson: 5
keywords: [Rust, rust, pattern matching, enums, algebraic data types, sum types, domain modeling]
tags: [Rust tutorial, rust, pattern-matching]
date: '2024-07-30T09:55:00.000Z'
---

The worst bug I ever shipped came from a `status` field that was a string. It could be "active", "Active", "ACTIVE", "active ", or — my personal favorite — "acitve". Six months of data with a typo nobody caught because strings don't have a compiler checking them.

Rust enums would have made that bug impossible.

## The Problem With Primitive Obsession

In most languages, developers reach for strings, integers, and booleans to represent domain concepts. A user's role is a string. An order status is an integer. A payment method is... also a string. This is called "primitive obsession" and it's responsible for an entire category of bugs:

- Invalid values that look valid (`"pending"` vs `"Pending"`)
- Missing context (what does `status = 3` mean without documentation?)
- Impossible to enforce invariants (how do you prevent `payment_method = "banana"`?)

Enums fix all of this by making the domain model *part of the type system*.

## The Idiomatic Way: Enums With Data

Rust enums aren't like C enums. They're algebraic data types — each variant can carry different amounts and types of data:

```rust
#[derive(Debug)]
enum PaymentMethod {
    CreditCard {
        number_last_four: String,
        expiry_month: u8,
        expiry_year: u16,
    },
    BankTransfer {
        routing_number: String,
        account_last_four: String,
    },
    Crypto {
        wallet_address: String,
        network: CryptoNetwork,
    },
    Cash,
}

#[derive(Debug)]
enum CryptoNetwork {
    Bitcoin,
    Ethereum,
    Solana,
}

fn describe_payment(method: &PaymentMethod) -> String {
    match method {
        PaymentMethod::CreditCard { number_last_four, .. } => {
            format!("Credit card ending in {}", number_last_four)
        }
        PaymentMethod::BankTransfer { account_last_four, .. } => {
            format!("Bank account ending in {}", account_last_four)
        }
        PaymentMethod::Crypto { network, .. } => {
            format!("{:?} wallet", network)
        }
        PaymentMethod::Cash => "Cash".to_string(),
    }
}

fn main() {
    let methods = vec![
        PaymentMethod::CreditCard {
            number_last_four: "4242".to_string(),
            expiry_month: 12,
            expiry_year: 2025,
        },
        PaymentMethod::BankTransfer {
            routing_number: "021000021".to_string(),
            account_last_four: "6789".to_string(),
        },
        PaymentMethod::Crypto {
            wallet_address: "0xabc123".to_string(),
            network: CryptoNetwork::Ethereum,
        },
        PaymentMethod::Cash,
    ];

    for m in &methods {
        println!("{}", describe_payment(m));
    }
}
```

Notice what this buys you. A `CreditCard` *always* has a last four digits, expiry month, and expiry year. A `BankTransfer` *always* has a routing number. `Cash` carries no data at all. You can't accidentally create a `CreditCard` without an expiry, and you can't accidentally check the `wallet_address` of a `BankTransfer`. The compiler prevents it.

## Making Illegal States Unrepresentable

This is the phrase that changed how I design software. If your types make it impossible to represent invalid states, you never need to write validation code for those states. You never need to write tests for them. They simply cannot exist.

Here's the classic example — a form that can be in different states:

```rust
// Bad: all fields optional, unclear which combinations are valid
struct FormBad {
    submitted: bool,
    validated: bool,
    submitted_at: Option<u64>,
    errors: Option<Vec<String>>,
    result: Option<String>,
}

// Good: each state is a distinct variant with exactly the right data
#[derive(Debug)]
enum Form {
    Draft {
        fields: Vec<(String, String)>,
    },
    Submitted {
        fields: Vec<(String, String)>,
        submitted_at: u64,
    },
    Validated {
        fields: Vec<(String, String)>,
        submitted_at: u64,
    },
    Rejected {
        fields: Vec<(String, String)>,
        errors: Vec<String>,
    },
    Accepted {
        result_id: String,
        accepted_at: u64,
    },
}

impl Form {
    fn submit(self, timestamp: u64) -> Form {
        match self {
            Form::Draft { fields } => Form::Submitted {
                fields,
                submitted_at: timestamp,
            },
            other => {
                println!("Cannot submit form in state: {:?}", other);
                other
            }
        }
    }

    fn validate(self) -> Form {
        match self {
            Form::Submitted { fields, submitted_at } => {
                let errors: Vec<String> = fields
                    .iter()
                    .filter(|(_, v)| v.is_empty())
                    .map(|(k, _)| format!("{} is required", k))
                    .collect();

                if errors.is_empty() {
                    Form::Validated {
                        fields,
                        submitted_at,
                    }
                } else {
                    Form::Rejected { fields, errors }
                }
            }
            other => {
                println!("Cannot validate form in state: {:?}", other);
                other
            }
        }
    }
}

fn main() {
    let form = Form::Draft {
        fields: vec![
            ("name".to_string(), "Alice".to_string()),
            ("email".to_string(), "alice@example.com".to_string()),
        ],
    };

    let form = form.submit(1000);
    let form = form.validate();
    println!("Final state: {:?}", form);

    // Form with missing fields
    let form2 = Form::Draft {
        fields: vec![
            ("name".to_string(), "Bob".to_string()),
            ("email".to_string(), String::new()), // empty!
        ],
    };

    let form2 = form2.submit(1001);
    let form2 = form2.validate();
    println!("Final state: {:?}", form2);
}
```

With the `FormBad` struct, you could have `submitted = true` and `validated = true` but also have `errors = Some(vec!["bad email"])`. What does that mean? Nobody knows. With the enum version, you're either `Validated` or `Rejected` — never both.

## Option and Result: The Most Important Enums

The two enums you'll use most in Rust are `Option<T>` and `Result<T, E>`. They're just regular enums:

```rust
// This is literally how they're defined in std
// enum Option<T> {
//     Some(T),
//     None,
// }
//
// enum Result<T, E> {
//     Ok(T),
//     Err(E),
// }

#[derive(Debug)]
enum AppError {
    NotFound(String),
    PermissionDenied { user: String, resource: String },
    DatabaseError(String),
    Validation(Vec<String>),
}

fn find_user(id: u64) -> Result<String, AppError> {
    match id {
        1 => Ok("Alice".to_string()),
        2 => Ok("Bob".to_string()),
        _ => Err(AppError::NotFound(format!("User {}", id))),
    }
}

fn get_user_email(id: u64) -> Result<String, AppError> {
    let name = find_user(id)?; // The ? operator destructures Result

    // Simulate email lookup
    match name.as_str() {
        "Alice" => Ok("alice@example.com".to_string()),
        _ => Err(AppError::NotFound(format!("Email for {}", name))),
    }
}

fn main() {
    for id in [1, 2, 3] {
        match get_user_email(id) {
            Ok(email) => println!("User {}: {}", id, email),
            Err(AppError::NotFound(what)) => println!("Not found: {}", what),
            Err(AppError::PermissionDenied { user, resource }) => {
                println!("{} cannot access {}", user, resource);
            }
            Err(e) => println!("Error: {:?}", e),
        }
    }
}
```

The `?` operator is pattern matching in disguise. When you write `find_user(id)?`, Rust desugars it to roughly:

```rust
// match find_user(id) {
//     Ok(val) => val,
//     Err(e) => return Err(e.into()),
// }
```

Every time you use `?`, you're destructuring a `Result`.

## Designing Good Enums

After building a lot of Rust services, I've developed some strong opinions about enum design.

### Each Variant Should Carry Exactly What It Needs

Don't put shared data at the enum level just because most variants use it. If a variant doesn't need a field, it shouldn't have it:

```rust
// Bad: response_time doesn't make sense for Timeout
// struct Request {
//     status: RequestStatus,
//     response_time: Option<Duration>,  // None for Timeout, Some for everything else
// }

// Good: each variant carries its own relevant data
#[derive(Debug)]
enum RequestResult {
    Success {
        status_code: u16,
        body: String,
        response_time_ms: u64,
    },
    ClientError {
        status_code: u16,
        message: String,
        response_time_ms: u64,
    },
    ServerError {
        status_code: u16,
        retries: u32,
        response_time_ms: u64,
    },
    Timeout {
        after_ms: u64,
    },
    ConnectionRefused {
        host: String,
        port: u16,
    },
}

fn summarize(result: &RequestResult) -> String {
    match result {
        RequestResult::Success { status_code, response_time_ms, .. } => {
            format!("{} in {}ms", status_code, response_time_ms)
        }
        RequestResult::ClientError { status_code, message, .. } => {
            format!("{}: {}", status_code, message)
        }
        RequestResult::ServerError { status_code, retries, .. } => {
            format!("{} after {} retries", status_code, retries)
        }
        RequestResult::Timeout { after_ms } => {
            format!("Timeout after {}ms", after_ms)
        }
        RequestResult::ConnectionRefused { host, port } => {
            format!("Connection refused to {}:{}", host, port)
        }
    }
}

fn main() {
    let results = vec![
        RequestResult::Success {
            status_code: 200,
            body: "ok".to_string(),
            response_time_ms: 45,
        },
        RequestResult::Timeout { after_ms: 30000 },
        RequestResult::ConnectionRefused {
            host: "db.internal".to_string(),
            port: 5432,
        },
    ];

    for r in &results {
        println!("{}", summarize(r));
    }
}
```

### Use Tuple Variants for Simple Wrappers

When a variant just wraps a single value, use a tuple variant instead of a struct variant:

```rust
#[derive(Debug)]
enum Token {
    // Tuple variants — clean for single values
    Integer(i64),
    Float(f64),
    StringLiteral(String),
    Identifier(String),

    // Unit variants — no data needed
    Plus,
    Minus,
    Star,
    Slash,
    LeftParen,
    RightParen,

    // Struct variant — multiple related fields
    Error { line: usize, column: usize, message: String },
}

fn token_debug(token: &Token) -> String {
    match token {
        Token::Integer(n) => format!("INT({})", n),
        Token::Float(f) => format!("FLOAT({})", f),
        Token::StringLiteral(s) => format!("STR({:?})", s),
        Token::Identifier(name) => format!("ID({})", name),
        Token::Plus => "+".to_string(),
        Token::Minus => "-".to_string(),
        Token::Star => "*".to_string(),
        Token::Slash => "/".to_string(),
        Token::LeftParen => "(".to_string(),
        Token::RightParen => ")".to_string(),
        Token::Error { line, column, message } => {
            format!("ERROR at {}:{}: {}", line, column, message)
        }
    }
}

fn main() {
    let tokens = vec![
        Token::Identifier("x".to_string()),
        Token::Plus,
        Token::Integer(42),
        Token::Star,
        Token::LeftParen,
        Token::Float(3.14),
        Token::RightParen,
    ];

    let debug: Vec<String> = tokens.iter().map(token_debug).collect();
    println!("{}", debug.join(" "));
}
```

### Don't Fear Large Enums

I've seen developers avoid enums because "they'd have too many variants." But that's exactly when enums shine — a 20-variant enum with exhaustive matching is *safer* than a string that could be any of 20 values. The compiler tracks every variant. A string tracks nothing.

## Recursive Enums

Enums can reference themselves, which is how you build tree structures:

```rust
#[derive(Debug)]
enum Json {
    Null,
    Boolean(bool),
    Number(f64),
    Str(String),
    Array(Vec<Json>),
    Object(Vec<(String, Json)>),
}

fn format_json(value: &Json, indent: usize) -> String {
    let pad = " ".repeat(indent);
    match value {
        Json::Null => "null".to_string(),
        Json::Boolean(b) => b.to_string(),
        Json::Number(n) => n.to_string(),
        Json::Str(s) => format!("\"{}\"", s),
        Json::Array(items) if items.is_empty() => "[]".to_string(),
        Json::Array(items) => {
            let inner: Vec<String> = items
                .iter()
                .map(|item| format!("{}  {}", pad, format_json(item, indent + 2)))
                .collect();
            format!("[\n{}\n{}]", inner.join(",\n"), pad)
        }
        Json::Object(pairs) if pairs.is_empty() => "{}".to_string(),
        Json::Object(pairs) => {
            let inner: Vec<String> = pairs
                .iter()
                .map(|(k, v)| {
                    format!("{}  \"{}\": {}", pad, k, format_json(v, indent + 2))
                })
                .collect();
            format!("{{\n{}\n{}}}", inner.join(",\n"), pad)
        }
    }
}

fn main() {
    let data = Json::Object(vec![
        ("name".to_string(), Json::Str("Alice".to_string())),
        ("age".to_string(), Json::Number(30.0)),
        ("active".to_string(), Json::Boolean(true)),
        ("scores".to_string(), Json::Array(vec![
            Json::Number(95.0),
            Json::Number(87.0),
            Json::Number(92.0),
        ])),
        ("address".to_string(), Json::Null),
    ]);

    println!("{}", format_json(&data, 0));
}
```

The `Json` enum is a sum type — any JSON value is *exactly one* of these variants. Combined with pattern matching, you get exhaustive handling of every possible JSON structure. No `instanceof` checks, no type casting, no runtime surprises.

Enums with data are the heart of Rust's type system. They replace inheritance hierarchies, union types, optional fields, and stringly-typed values with something the compiler can actually verify. Once you start designing with "make illegal states unrepresentable" in mind, you'll wonder how you ever built anything without it.

Next up: state machines with enums. We'll take these ideas further and build systems where invalid state transitions are compile errors.
