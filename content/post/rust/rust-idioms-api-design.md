---
title: "Lesson 20: API Design Guidelines — The Rust way"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 20
keywords: [Rust, rust, idiomatic, API design, library design, ergonomics, conventions]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-05-12T16:40:00.000Z'
---

I've written APIs in a dozen languages, and Rust is the only one where the community has near-universal agreement on how APIs should look. There's an unofficial (but incredibly thorough) Rust API Guidelines document, and most popular crates follow it closely. When you learn these conventions, every new crate feels familiar.

Here's what I've distilled from reading hundreds of crate APIs and writing a few of my own.

---

## Naming Conventions

Rust has strong naming conventions, and deviating from them makes your crate feel alien.

### Methods that return references: no `get_` prefix

```rust
struct User {
    name: String,
    email: String,
}

impl User {
    // GOOD: just the field name
    fn name(&self) -> &str {
        &self.name
    }

    fn email(&self) -> &str {
        &self.email
    }

    // BAD: unnecessary get_ prefix
    // fn get_name(&self) -> &str { &self.name }
}
```

The Rust stdlib doesn't use `get_` prefixes. `vec.len()`, not `vec.get_len()`. `str.is_empty()`, not `str.get_is_empty()`.

Exception: `get()` on collections returns `Option<&T>`, and that's the convention — `HashMap::get`, `Vec::get`. These make sense because "get" implies "might not exist."

### Conversion method names

| Pattern | Meaning | Example |
|---------|---------|---------|
| `as_` | Cheap, borrowed view | `as_str()`, `as_slice()`, `as_bytes()` |
| `to_` | Expensive conversion | `to_string()`, `to_vec()`, `to_uppercase()` |
| `into_` | Ownership conversion | `into_inner()`, `into_bytes()`, `into_iter()` |

```rust
struct Wrapper {
    inner: Vec<u8>,
}

impl Wrapper {
    // Cheap reference — no allocation
    fn as_slice(&self) -> &[u8] {
        &self.inner
    }

    // Expensive — allocates a new String
    fn to_hex_string(&self) -> String {
        self.inner.iter().map(|b| format!("{:02x}", b)).collect()
    }

    // Ownership transfer — consumes self
    fn into_inner(self) -> Vec<u8> {
        self.inner
    }
}
```

### Boolean methods: `is_`, `has_`, `can_`

```rust
impl User {
    fn is_active(&self) -> bool { true }
    fn has_permissions(&self) -> bool { true }
    fn can_edit(&self) -> bool { true }
}
```

### Constructor: `new()` or specific names

```rust
struct Config {
    path: String,
}

impl Config {
    // Primary constructor
    fn new(path: &str) -> Self {
        Config { path: path.to_string() }
    }

    // Named constructors for alternative creation paths
    fn from_env() -> Self {
        let path = std::env::var("CONFIG_PATH").unwrap_or_else(|_| "config.toml".into());
        Config { path }
    }

    fn default_config() -> Self {
        Config { path: "default.toml".into() }
    }
}
```

---

## Parameter Types: Be Generous in What You Accept

### Accept `&str`, not `&String`

```rust
// GOOD: accepts &str, &String, String (via deref)
fn greet(name: &str) {
    println!("Hello, {}", name);
}

// BAD: unnecessarily restrictive
// fn greet(name: &String) { ... }
```

### Accept `&[T]`, not `&Vec<T>`

```rust
// GOOD: works with Vec, array, slice
fn average(numbers: &[f64]) -> f64 {
    numbers.iter().sum::<f64>() / numbers.len() as f64
}
```

### Accept `impl Into<T>` for owned parameters

```rust
struct Message {
    text: String,
}

impl Message {
    // Accepts String, &str, Cow<str>, etc.
    fn new(text: impl Into<String>) -> Self {
        Message { text: text.into() }
    }
}

fn main() {
    let m1 = Message::new("hello");           // &str
    let m2 = Message::new(String::from("hi")); // String — no extra allocation
    println!("{}, {}", m1.text, m2.text);
}
```

### Accept `impl AsRef<Path>` for file paths

```rust
use std::path::Path;

fn read_config(path: impl AsRef<Path>) -> std::io::Result<String> {
    std::fs::read_to_string(path)
}

fn main() {
    // All of these work:
    let _ = read_config("config.toml");
    let _ = read_config(String::from("config.toml"));
    let _ = read_config(Path::new("config.toml"));
}
```

---

## Return Types: Be Precise in What You Return

### Return owned types from constructors

```rust
// GOOD: caller owns the result
fn create_greeting(name: &str) -> String {
    format!("Hello, {}!", name)
}
```

### Return `&self` from builder methods

```rust
struct Query {
    table: String,
    conditions: Vec<String>,
}

impl Query {
    fn new(table: &str) -> Self {
        Query { table: table.into(), conditions: Vec::new() }
    }

    fn where_clause(mut self, condition: &str) -> Self {
        self.conditions.push(condition.into());
        self // returns self for chaining
    }

    fn build(&self) -> String {
        if self.conditions.is_empty() {
            format!("SELECT * FROM {}", self.table)
        } else {
            format!("SELECT * FROM {} WHERE {}", self.table, self.conditions.join(" AND "))
        }
    }
}

fn main() {
    let q = Query::new("users")
        .where_clause("age > 18")
        .where_clause("active = true")
        .build();
    println!("{}", q);
}
```

### Return `Result` for anything that can fail

```rust
use std::io;

// GOOD: caller decides how to handle failure
fn load_data(path: &str) -> io::Result<Vec<u8>> {
    std::fs::read(path)
}

// BAD: panics on failure — caller has no choice
// fn load_data(path: &str) -> Vec<u8> {
//     std::fs::read(path).unwrap()
// }
```

---

## Error Handling in APIs

### Libraries should return errors, not print them

```rust
// BAD: library function that prints errors
fn parse_config_bad(input: &str) -> Option<u16> {
    match input.parse::<u16>() {
        Ok(port) => Some(port),
        Err(e) => {
            eprintln!("Parse error: {}", e); // Don't do this in a library!
            None
        }
    }
}

// GOOD: library function that returns errors
fn parse_config(input: &str) -> Result<u16, std::num::ParseIntError> {
    input.parse::<u16>()
}
```

The caller controls error handling — logging, displaying, converting, whatever. The library just reports what went wrong.

### Don't panic in libraries

```rust
// BAD: panics if index is out of bounds
// fn get_item(items: &[String], index: usize) -> &String {
//     &items[index] // panics!
// }

// GOOD: returns Option
fn get_item(items: &[String], index: usize) -> Option<&String> {
    items.get(index)
}
```

Reserve panics for truly unrecoverable situations — invariant violations that indicate a programming error, not runtime conditions.

---

## Make Common Things Easy, Advanced Things Possible

```rust
struct HttpClient {
    timeout_ms: u64,
    max_retries: u32,
    user_agent: String,
}

impl HttpClient {
    // Simple: sensible defaults
    fn new() -> Self {
        HttpClient {
            timeout_ms: 30_000,
            max_retries: 3,
            user_agent: String::from("rust-http/1.0"),
        }
    }

    // Advanced: full customization
    fn with_timeout(mut self, ms: u64) -> Self {
        self.timeout_ms = ms;
        self
    }

    fn with_retries(mut self, n: u32) -> Self {
        self.max_retries = n;
        self
    }

    fn with_user_agent(mut self, ua: impl Into<String>) -> Self {
        self.user_agent = ua.into();
        self
    }

    fn get(&self, url: &str) -> String {
        println!("GET {} (timeout: {}ms, retries: {}, ua: {})",
            url, self.timeout_ms, self.max_retries, self.user_agent);
        String::from("response")
    }
}

fn main() {
    // Simple use: just works
    let client = HttpClient::new();
    client.get("https://api.example.com/data");

    // Advanced use: customized
    let client = HttpClient::new()
        .with_timeout(5000)
        .with_retries(1)
        .with_user_agent("my-app/2.0");
    client.get("https://api.example.com/data");
}
```

---

## Implement Standard Traits

Every public type should implement the standard traits that make sense:

```rust
use std::fmt;

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct ApiKey {
    key: String,
    prefix: String,
}

impl fmt::Display for ApiKey {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // Show prefix but redact the key
        write!(f, "{}...", &self.prefix)
    }
}

impl Default for ApiKey {
    fn default() -> Self {
        ApiKey {
            key: String::new(),
            prefix: String::from("ak"),
        }
    }
}
```

At minimum: `Debug`. For value types: add `Clone`, `PartialEq`. For collection keys: add `Eq`, `Hash`. For user-facing types: add `Display`. For configuration: add `Default`.

---

## Document the Contract

```rust
/// Parses a port number from a string.
///
/// # Errors
///
/// Returns an error if the string is not a valid integer
/// or if the port is outside the range 1-65535.
///
/// # Examples
///
/// ```
/// let port = parse_port("8080").unwrap();
/// assert_eq!(port, 8080);
/// ```
pub fn parse_port(input: &str) -> Result<u16, String> {
    let port: u16 = input.parse().map_err(|e| format!("invalid port: {}", e))?;
    if port == 0 {
        return Err("port must be non-zero".into());
    }
    Ok(port)
}
```

Document:
- What the function does (one line)
- **Errors** — when does it return `Err`?
- **Panics** — when does it panic? (Ideally never for public APIs)
- **Examples** — runnable code that shows usage

---

## Key Takeaways

- Follow naming conventions: no `get_` prefix, use `as_`/`to_`/`into_` for conversions, `is_`/`has_` for booleans.
- Accept the most general parameter type: `&str` over `&String`, `&[T]` over `&Vec<T>`, `impl Into<T>` for owned values.
- Return precise types: `Result` for fallible operations, `Option` for nullable values.
- Libraries should never print errors or panic — return errors and let the caller decide.
- Make simple things easy with good defaults. Make advanced things possible with builder methods.
- Implement `Debug`, `Clone`, `PartialEq`, and `Display` where appropriate — they're part of the API contract.
