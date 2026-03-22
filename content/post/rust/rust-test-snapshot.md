---
title: "Lesson 8: Snapshot Testing with insta — Catch regressions instantly"
author: Atharva Pandey
series: "Rust Testing Masterclass"
lesson: 8
keywords: [Rust, rust, testing, snapshot testing, insta, regression testing, golden files]
tags: [Rust tutorial, rust, testing]
date: '2024-08-27T10:30:00.000Z'
---

I refactored a code formatter once — changed how it handles indentation for nested blocks. I had assertions checking specific outputs for five test cases, and they all passed. But the formatter produced subtly different output for a sixth pattern I hadn't tested, and a user filed a bug three days later. If I'd had snapshot tests, I would've seen every output change in a diff during the refactor. Five minutes of review instead of three days of embarrassment.

## The Problem

Some functions produce complex, structured output — error messages, formatted text, serialized data, API responses, AST dumps. Writing assertions for these is painful:

```rust
// Nobody wants to maintain this
assert_eq!(
    format_error(&err),
    "Error at line 14, column 3:\n  unexpected token '}'\n  expected: identifier or keyword\n  note: did you mean to close the block on line 12?"
);
```

That string literal is fragile, hard to read, and annoying to update. If you change the error format even slightly, you're editing dozens of these by hand.

Snapshot testing flips this around. You capture the output once, store it in a file, and the test framework automatically detects when the output changes. If the change is intentional, you approve it. If it's not, you've caught a regression.

## Setting Up insta

`insta` is the standard snapshot testing crate for Rust. It's maintained by Armin Ronacher (the creator of Flask), and it's excellent.

```toml
[dev-dependencies]
insta = { version = "1.39", features = ["yaml"] }
```

Install the companion CLI tool — you'll need it for reviewing snapshots:

```bash
cargo install cargo-insta
```

## Your First Snapshot Test

```rust
use insta::assert_snapshot;

fn format_greeting(name: &str, title: Option<&str>) -> String {
    match title {
        Some(t) => format!("Dear {} {},\n\nThank you for your inquiry.\n\nBest regards,\nSupport Team", t, name),
        None => format!("Hi {},\n\nThanks for reaching out!\n\nCheers,\nSupport Team", name),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_greeting_with_title() {
        assert_snapshot!(format_greeting("Smith", Some("Dr.")));
    }

    #[test]
    fn test_greeting_without_title() {
        assert_snapshot!(format_greeting("Alice", None));
    }
}
```

The first time you run `cargo test`, these tests *fail* — there's no snapshot to compare against. Run `cargo insta review` to see the pending snapshots and approve them:

```bash
cargo insta review
```

This opens an interactive UI showing you the new snapshot content. Press `a` to accept. insta creates snapshot files:

```
src/snapshots/
├── my_crate__tests__test_greeting_with_title.snap
└── my_crate__tests__test_greeting_without_title.snap
```

Each `.snap` file contains the expected output:

```
---
source: src/lib.rs
expression: "format_greeting(\"Smith\", Some(\"Dr.\"))"
---
Dear Dr. Smith,

Thank you for your inquiry.

Best regards,
Support Team
```

Now the test passes. If someone changes the output, the test fails and shows a diff. Run `cargo insta review` to see what changed and decide whether to accept or reject it.

## Inline Snapshots

For shorter outputs, you can embed the snapshot directly in the test file:

```rust
#[cfg(test)]
mod tests {
    use insta::assert_snapshot;

    fn pluralize(word: &str, count: usize) -> String {
        match count {
            0 => format!("no {}s", word),
            1 => format!("1 {}", word),
            n => format!("{} {}s", n, word),
        }
    }

    #[test]
    fn test_pluralize_zero() {
        assert_snapshot!(pluralize("item", 0), @"no items");
    }

    #[test]
    fn test_pluralize_one() {
        assert_snapshot!(pluralize("item", 1), @"1 item");
    }

    #[test]
    fn test_pluralize_many() {
        assert_snapshot!(pluralize("item", 5), @"5 items");
    }
}
```

The `@"..."` syntax is the inline snapshot. When you first write the test, leave it as `@""` and run `cargo insta review` — insta fills in the value for you. The snapshot lives right next to the assertion, which is great for small values.

## YAML Snapshots for Structured Data

For structs and complex types, YAML snapshots are much more readable than debug output:

```rust
use serde::Serialize;

#[derive(Debug, Serialize)]
struct UserProfile {
    id: u64,
    username: String,
    email: String,
    roles: Vec<String>,
    active: bool,
}

#[derive(Debug, Serialize)]
struct ApiResponse {
    status: u16,
    users: Vec<UserProfile>,
    total_count: usize,
    page: usize,
}

fn build_response(users: Vec<UserProfile>, page: usize) -> ApiResponse {
    let total = users.len();
    ApiResponse {
        status: 200,
        users,
        total_count: total,
        page,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use insta::assert_yaml_snapshot;

    #[test]
    fn test_api_response() {
        let users = vec![
            UserProfile {
                id: 1,
                username: "alice".to_string(),
                email: "alice@test.com".to_string(),
                roles: vec!["admin".to_string(), "user".to_string()],
                active: true,
            },
            UserProfile {
                id: 2,
                username: "bob".to_string(),
                email: "bob@test.com".to_string(),
                roles: vec!["user".to_string()],
                active: false,
            },
        ];

        let response = build_response(users, 1);
        assert_yaml_snapshot!(response);
    }

    #[test]
    fn test_empty_response() {
        let response = build_response(vec![], 1);
        assert_yaml_snapshot!(response);
    }
}
```

The YAML snapshot is human-readable and diffs beautifully:

```yaml
---
source: src/lib.rs
expression: response
---
status: 200
users:
  - id: 1
    username: alice
    email: alice@test.com
    roles:
      - admin
      - user
    active: true
  - id: 2
    username: bob
    email: bob@test.com
    roles:
      - user
    active: false
total_count: 2
page: 1
```

When a field changes, the diff shows exactly which field, in context.

## Redacting Dynamic Values

Some values change every run — timestamps, UUIDs, random IDs. insta lets you redact them:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use insta::{assert_yaml_snapshot, with_settings};

    #[derive(Debug, serde::Serialize)]
    struct AuditEntry {
        id: String,
        timestamp: String,
        user: String,
        action: String,
    }

    fn create_audit_entry(user: &str, action: &str) -> AuditEntry {
        AuditEntry {
            id: uuid::Uuid::new_v4().to_string(),
            timestamp: chrono::Utc::now().to_rfc3339(),
            user: user.to_string(),
            action: action.to_string(),
        }
    }

    #[test]
    fn test_audit_entry() {
        let entry = create_audit_entry("alice", "login");

        assert_yaml_snapshot!(entry, {
            ".id" => "[uuid]",
            ".timestamp" => "[timestamp]",
        });
    }
}
```

The snapshot replaces the dynamic values with placeholders:

```yaml
---
id: "[uuid]"
timestamp: "[timestamp]"
user: alice
action: login
```

Now the test is stable across runs while still verifying the structure and static values.

## Snapshot Testing for Error Messages

This is where snapshots really earn their keep. Error messages are long, detailed, and change frequently during development. Writing manual assertions for them is a nightmare.

```rust
#[derive(Debug)]
struct ValidationError {
    field: String,
    message: String,
    code: String,
}

fn validate_user_input(
    username: &str,
    email: &str,
    age: Option<i32>,
) -> Result<(), Vec<ValidationError>> {
    let mut errors = Vec::new();

    if username.is_empty() {
        errors.push(ValidationError {
            field: "username".to_string(),
            message: "Username is required".to_string(),
            code: "REQUIRED".to_string(),
        });
    } else if username.len() < 3 {
        errors.push(ValidationError {
            field: "username".to_string(),
            message: format!("Username must be at least 3 characters, got {}", username.len()),
            code: "TOO_SHORT".to_string(),
        });
    }

    if !email.contains('@') {
        errors.push(ValidationError {
            field: "email".to_string(),
            message: format!("'{}' is not a valid email address", email),
            code: "INVALID_FORMAT".to_string(),
        });
    }

    if let Some(age) = age {
        if age < 0 || age > 150 {
            errors.push(ValidationError {
                field: "age".to_string(),
                message: format!("Age must be between 0 and 150, got {}", age),
                code: "OUT_OF_RANGE".to_string(),
            });
        }
    }

    if errors.is_empty() {
        Ok(())
    } else {
        Err(errors)
    }
}

fn format_errors(errors: &[ValidationError]) -> String {
    errors
        .iter()
        .map(|e| format!("[{}] {}: {}", e.code, e.field, e.message))
        .collect::<Vec<_>>()
        .join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;
    use insta::assert_snapshot;

    #[test]
    fn test_all_fields_invalid() {
        let errors = validate_user_input("", "not-email", Some(200)).unwrap_err();
        assert_snapshot!(format_errors(&errors));
    }

    #[test]
    fn test_short_username() {
        let errors = validate_user_input("ab", "ok@test.com", Some(25)).unwrap_err();
        assert_snapshot!(format_errors(&errors));
    }

    #[test]
    fn test_valid_input() {
        let result = validate_user_input("alice", "alice@test.com", Some(30));
        assert!(result.is_ok());
    }
}
```

If you tweak the error message format — say you want to add line numbers or change the bracket style — `cargo insta review` shows you every affected message in one pass. You review the diff, accept it, and move on. Way faster than updating fifty `assert_eq!` strings.

## The Workflow

Here's how I use insta day-to-day:

1. **Write the test** with `assert_snapshot!` and run `cargo test`. It fails.
2. **Run `cargo insta review`** to see the output and accept it.
3. **Commit the `.snap` files** alongside your code.
4. **When code changes**, `cargo test` fails if any output differs.
5. **Run `cargo insta review`** to see what changed. Accept intentional changes, investigate unexpected ones.

In CI:

```bash
cargo insta test --review=fail
```

This runs the tests and fails if any snapshots are pending review. No one can accidentally merge unreviewed snapshot changes.

## When to Use Snapshots

**Great for:**
- Error messages and diagnostic output
- Serialized data (JSON, YAML, TOML)
- Pretty-printed ASTs or IR
- CLI output formatting
- API response bodies
- Code generation output

**Not great for:**
- Simple scalar values (just use `assert_eq!`)
- Non-deterministic output (use redactions or don't snapshot)
- Binary data (snapshots are text-based)

The sweet spot is structured text output where writing manual assertions would be tedious and fragile. If you can check the value with a single `assert_eq!`, a snapshot is overkill.

## What's Next

We've been writing tests — but how do we know we've written *enough*? Code coverage tools answer that question by showing which lines, branches, and functions your tests actually exercise. We'll set up `tarpaulin` and `llvm-cov` next.
