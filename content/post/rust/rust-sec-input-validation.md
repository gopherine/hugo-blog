---
title: "Lesson 2: Input Validation and Sanitization — Trust nothing"
author: Atharva Pandey
series: "Rust Security in Production"
lesson: 2
keywords: [Rust, rust, security, input validation, sanitization, injection, parsing]
tags: [Rust tutorial, rust, security]
date: '2025-05-03T16:12:00.000Z'
---

A few months ago, I was reviewing a PR where someone had written a REST API handler that took a user-supplied filename, appended it to a base path, and opened the file. The code compiled perfectly. Clippy was happy. Tests passed. And it was a textbook path traversal vulnerability — `../../etc/passwd` would work just fine.

Rust's type system protects you from memory corruption. It doesn't protect you from trusting user input. That's still on you. And honestly? It's where most production vulnerabilities in Rust code are going to come from.

## The Parse, Don't Validate Philosophy

There's a famous blog post by Alexis King called "Parse, Don't Validate." The core idea is: instead of checking whether data is valid and then passing around the raw data (hoping nobody forgets the check), parse the raw data into a type that *can only represent valid states*.

This is where Rust's type system becomes a security tool — not because of memory safety, but because of expressiveness.

### The Wrong Way

```rust
fn create_user(username: &str, email: &str, age: i32) -> Result<(), String> {
    // Validate inline — easy to forget, easy to skip
    if username.is_empty() || username.len() > 64 {
        return Err("invalid username".into());
    }
    if !email.contains('@') {
        return Err("invalid email".into());
    }
    if age < 0 || age > 150 {
        return Err("invalid age".into());
    }

    // Now we're working with raw strings and an i32
    // Any function we pass these to has NO guarantee they've been validated
    save_to_database(username, email, age)
}
```

### The Right Way

```rust
use std::fmt;

#[derive(Debug, Clone)]
pub struct Username(String);

impl Username {
    pub fn parse(input: &str) -> Result<Self, ValidationError> {
        let trimmed = input.trim();
        if trimmed.is_empty() {
            return Err(ValidationError::Empty("username"));
        }
        if trimmed.len() > 64 {
            return Err(ValidationError::TooLong("username", 64));
        }
        // Only allow alphanumeric, underscores, hyphens
        if !trimmed.chars().all(|c| c.is_alphanumeric() || c == '_' || c == '-') {
            return Err(ValidationError::InvalidChars("username"));
        }
        Ok(Username(trimmed.to_string()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Debug, Clone)]
pub struct Email(String);

impl Email {
    pub fn parse(input: &str) -> Result<Self, ValidationError> {
        let trimmed = input.trim().to_lowercase();
        // This is simplified — real email validation is complex.
        // Consider using the `validator` crate for production.
        let parts: Vec<&str> = trimmed.splitn(2, '@').collect();
        if parts.len() != 2 || parts[0].is_empty() || parts[1].is_empty() {
            return Err(ValidationError::InvalidFormat("email"));
        }
        if !parts[1].contains('.') {
            return Err(ValidationError::InvalidFormat("email"));
        }
        Ok(Email(trimmed))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Debug, Clone, Copy)]
pub struct Age(u8);

impl Age {
    pub fn parse(value: i32) -> Result<Self, ValidationError> {
        if value < 0 || value > 150 {
            return Err(ValidationError::OutOfRange("age", 0, 150));
        }
        Ok(Age(value as u8))
    }

    pub fn value(&self) -> u8 {
        self.0
    }
}

#[derive(Debug)]
pub enum ValidationError {
    Empty(&'static str),
    TooLong(&'static str, usize),
    InvalidChars(&'static str),
    InvalidFormat(&'static str),
    OutOfRange(&'static str, i32, i32),
}

impl fmt::Display for ValidationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Empty(field) => write!(f, "{} cannot be empty", field),
            Self::TooLong(field, max) => {
                write!(f, "{} cannot exceed {} characters", field, max)
            }
            Self::InvalidChars(field) => {
                write!(f, "{} contains invalid characters", field)
            }
            Self::InvalidFormat(field) => write!(f, "{} has invalid format", field),
            Self::OutOfRange(field, min, max) => {
                write!(f, "{} must be between {} and {}", field, min, max)
            }
        }
    }
}

// Now the function signature tells you everything:
fn create_user(username: Username, email: Email, age: Age) -> Result<(), String> {
    // These values are GUARANTEED to be valid.
    // You can't accidentally pass an unvalidated string.
    save_to_database(username.as_str(), email.as_str(), age.value())
}

fn save_to_database(username: &str, email: &str, age: u8) -> Result<(), String> {
    println!("Saving: {} / {} / {}", username, email, age);
    Ok(())
}
```

The difference is subtle but critical. In the first version, every function that receives a `&str` has to either re-validate or *trust* that validation happened upstream. In the second version, the type system enforces it. If you have a `Username`, it's valid. Full stop.

## Path Traversal — The One That Bites Everyone

This is probably the most common vulnerability I see in Rust web services. Someone builds a file path from user input without sanitizing it.

```rust
use std::path::{Path, PathBuf};
use std::io;

// VULNERABLE — do not use
fn serve_file_bad(base: &Path, user_path: &str) -> io::Result<Vec<u8>> {
    let full_path = base.join(user_path);
    std::fs::read(&full_path)
}

// An attacker sends: "../../../etc/passwd"
// And you just served them your password file. Nice.
```

Here's the fix:

```rust
use std::path::{Path, PathBuf};
use std::io;

#[derive(Debug)]
pub enum PathError {
    Traversal,
    NotFound,
    Io(io::Error),
}

fn serve_file_safe(base: &Path, user_path: &str) -> Result<Vec<u8>, PathError> {
    // Step 1: Reject obviously suspicious input
    if user_path.contains("..") || user_path.starts_with('/') || user_path.starts_with('\\') {
        return Err(PathError::Traversal);
    }

    // Step 2: Build the path and canonicalize both
    let full_path = base.join(user_path);

    let canonical_base = base.canonicalize().map_err(PathError::Io)?;
    let canonical_full = full_path.canonicalize().map_err(|_| PathError::NotFound)?;

    // Step 3: Verify the resolved path is still under the base
    if !canonical_full.starts_with(&canonical_base) {
        return Err(PathError::Traversal);
    }

    std::fs::read(&canonical_full).map_err(PathError::Io)
}
```

The `canonicalize()` call resolves symlinks and `..` components, so even if someone gets creative with symlinks, you catch it. Always canonicalize and check the prefix. Always.

## SQL Injection — Yes, It Can Happen in Rust

If you're using something like `sqlx` with parameterized queries, you're fine. But string formatting SQL queries is just as dangerous in Rust as anywhere else.

```rust
// DON'T DO THIS — SQL injection waiting to happen
fn find_user_bad(pool: &Pool, username: &str) -> Result<User, Error> {
    let query = format!("SELECT * FROM users WHERE username = '{}'", username);
    // An attacker sends: ' OR '1'='1
    sqlx::query_as::<_, User>(&query)
        .fetch_one(pool)
        .await
}

// DO THIS — parameterized queries
async fn find_user_good(
    pool: &sqlx::PgPool,
    username: &Username, // Note: using our validated type!
) -> Result<User, sqlx::Error> {
    sqlx::query_as::<_, User>("SELECT id, username, email FROM users WHERE username = $1")
        .bind(username.as_str())
        .fetch_one(pool)
        .await
}
```

The `sqlx::query!` macro goes even further — it validates your SQL against the actual database schema at compile time:

```rust
async fn find_user_checked(
    pool: &sqlx::PgPool,
    username: &str,
) -> Result<User, sqlx::Error> {
    // This won't compile if the SQL is invalid or the types don't match
    let row = sqlx::query!(
        "SELECT id, username, email FROM users WHERE username = $1",
        username
    )
    .fetch_one(pool)
    .await?;

    Ok(User {
        id: row.id,
        username: row.username,
        email: row.email,
    })
}

struct User {
    id: i64,
    username: String,
    email: String,
}
```

## Integer Overflow — The Silent Killer

In release mode, Rust doesn't panic on integer overflow — it wraps. This is a known footgun and it can absolutely be a security issue.

```rust
fn allocate_buffer(item_count: u32, item_size: u32) -> Vec<u8> {
    // In release mode, this wraps silently if the multiplication overflows
    let total_size = item_count * item_size;
    vec![0u8; total_size as usize]
}

// An attacker sends item_count = 0x10000, item_size = 0x10001
// 0x10000 * 0x10001 = 0x1_0001_0000 which wraps to 0x0001_0000 in u32
// You allocate a small buffer but think it's bigger — classic overflow vuln
```

The fix:

```rust
fn allocate_buffer_safe(
    item_count: u32,
    item_size: u32,
) -> Result<Vec<u8>, &'static str> {
    let total_size = item_count
        .checked_mul(item_size)
        .ok_or("integer overflow in size calculation")?;

    // Also set a reasonable upper bound
    if total_size > 10_000_000 {
        return Err("allocation too large");
    }

    Ok(vec![0u8; total_size as usize])
}
```

Use `checked_add`, `checked_mul`, `checked_sub` for any arithmetic involving untrusted input. Or enable overflow checks in release mode:

```toml
[profile.release]
overflow-checks = true
```

This adds a small performance cost, but for most services it's negligible and the safety benefit is enormous.

## Deserialization — Where Types Meet Reality

Serde is fantastic, but it'll happily deserialize anything that structurally matches your type. You still need to validate the *values*.

```rust
use serde::Deserialize;

// This will deserialize any JSON that has the right shape,
// even if the values are nonsensical
#[derive(Deserialize)]
struct RawRequest {
    username: String,     // could be empty or 10MB of garbage
    count: i64,           // could be negative or i64::MAX
    redirect_url: String, // could be "javascript:alert(1)"
}

// Better: deserialize into raw types, then parse into validated types
#[derive(Deserialize)]
struct RawRequest2 {
    username: String,
    count: i64,
    redirect_url: String,
}

struct ValidatedRequest {
    username: Username,
    count: u32,
    redirect_url: SafeUrl,
}

impl RawRequest2 {
    fn validate(self) -> Result<ValidatedRequest, ValidationError> {
        Ok(ValidatedRequest {
            username: Username::parse(&self.username)?,
            count: {
                if self.count < 0 || self.count > 1000 {
                    return Err(ValidationError::OutOfRange("count", 0, 1000));
                }
                self.count as u32
            },
            redirect_url: SafeUrl::parse(&self.redirect_url)?,
        })
    }
}

struct SafeUrl(String);

impl SafeUrl {
    fn parse(input: &str) -> Result<Self, ValidationError> {
        let trimmed = input.trim();
        // Only allow https URLs to our own domain
        if !trimmed.starts_with("https://") {
            return Err(ValidationError::InvalidFormat("redirect_url"));
        }
        // Reject anything with javascript:, data:, or other schemes embedded
        let lower = trimmed.to_lowercase();
        if lower.contains("javascript:") || lower.contains("data:") {
            return Err(ValidationError::InvalidFormat("redirect_url"));
        }
        Ok(SafeUrl(trimmed.to_string()))
    }
}
```

## Using the `validator` Crate

For common validation patterns, the `validator` crate integrates nicely with serde and gives you derive macros:

```toml
[dependencies]
validator = { version = "0.18", features = ["derive"] }
serde = { version = "1", features = ["derive"] }
```

```rust
use serde::Deserialize;
use validator::Validate;

#[derive(Debug, Deserialize, Validate)]
struct CreateUserRequest {
    #[validate(length(min = 1, max = 64))]
    #[validate(regex(path = "RE_USERNAME"))]
    username: String,

    #[validate(email)]
    email: String,

    #[validate(range(min = 0, max = 150))]
    age: u32,

    #[validate(url)]
    website: Option<String>,
}

use once_cell::sync::Lazy;
use regex::Regex;

static RE_USERNAME: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^[a-zA-Z0-9_-]+$").unwrap()
});

async fn handle_create_user(body: CreateUserRequest) -> Result<(), String> {
    body.validate().map_err(|e| format!("Validation failed: {}", e))?;

    // Now you know the constraints are satisfied
    println!("Creating user: {}", body.username);
    Ok(())
}
```

It's convenient, but I still prefer the newtype pattern for critical security boundaries. `validator` checks the data; newtypes make the check impossible to forget.

## A Complete Validation Layer

Here's how I structure validation in a real Axum handler:

```rust
use axum::{extract::Json, http::StatusCode, response::IntoResponse};
use serde::Deserialize;

#[derive(Deserialize)]
struct RawTransferRequest {
    from_account: String,
    to_account: String,
    amount_cents: i64,
    memo: Option<String>,
}

struct ValidTransfer {
    from_account: AccountId,
    to_account: AccountId,
    amount_cents: u64,
    memo: Option<SanitizedText>,
}

#[derive(Debug, Clone)]
struct AccountId(String);

impl AccountId {
    fn parse(input: &str) -> Result<Self, String> {
        let trimmed = input.trim();
        if trimmed.len() != 12 {
            return Err("account ID must be 12 characters".into());
        }
        if !trimmed.chars().all(|c| c.is_ascii_alphanumeric()) {
            return Err("account ID must be alphanumeric".into());
        }
        Ok(AccountId(trimmed.to_string()))
    }
}

#[derive(Debug, Clone)]
struct SanitizedText(String);

impl SanitizedText {
    fn parse(input: &str, max_len: usize) -> Result<Self, String> {
        let trimmed = input.trim();
        if trimmed.len() > max_len {
            return Err(format!("text exceeds {} characters", max_len));
        }
        // Strip control characters, HTML tags, etc.
        let cleaned: String = trimmed
            .chars()
            .filter(|c| !c.is_control())
            .collect();
        // Strip anything that looks like HTML
        let cleaned = cleaned
            .replace('<', "&lt;")
            .replace('>', "&gt;");
        Ok(SanitizedText(cleaned))
    }
}

impl RawTransferRequest {
    fn validate(self) -> Result<ValidTransfer, String> {
        let from = AccountId::parse(&self.from_account)?;
        let to = AccountId::parse(&self.to_account)?;

        if self.amount_cents <= 0 {
            return Err("amount must be positive".into());
        }
        if self.amount_cents > 1_000_000_00 {
            return Err("amount exceeds maximum transfer limit".into());
        }

        let memo = match self.memo {
            Some(m) => Some(SanitizedText::parse(&m, 500)?),
            None => None,
        };

        Ok(ValidTransfer {
            from_account: from,
            to_account: to,
            amount_cents: self.amount_cents as u64,
            memo,
        })
    }
}

async fn handle_transfer(
    Json(raw): Json<RawTransferRequest>,
) -> impl IntoResponse {
    let transfer = match raw.validate() {
        Ok(t) => t,
        Err(e) => return (StatusCode::BAD_REQUEST, e).into_response(),
    };

    // From here on, everything is validated and sanitized.
    // The types guarantee it.
    (StatusCode::OK, "transfer initiated").into_response()
}
```

## What I Tell My Team

Every time you take a `String` or `i64` in a function that handles user data, ask yourself: can this function be called with unvalidated data? If yes, you have a potential vulnerability. Wrap it in a newtype. Parse at the boundary. Carry validated types through your entire codebase.

Rust gives you memory safety for free. Input validation is the next layer — and unlike most languages, Rust gives you the type system to make it airtight. Use it.
