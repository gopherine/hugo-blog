---
title: "Lesson 16: Enums Over Booleans — Make illegal states unrepresentable"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 16
keywords: [Rust, rust, idiomatic, enums, booleans, type safety, state modeling]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-05-05T08:17:00.000Z'
---

I once spent an entire afternoon debugging a function with this signature: `process(data, true, false, true)`. Three booleans. What did they mean? I had to read the function definition every single time I encountered a call site. Is `true` for "verbose"? For "dry-run"? For "force"?

Booleans are the most overused type in programming. They encode exactly one bit of information — yes or no — and they tell you nothing about *what question they're answering*. Enums fix this.

---

## The Problem: Boolean Blindness

```rust
fn send_email(to: &str, body: &str, html: bool, urgent: bool, track: bool) {
    // What does send_email("bob@x.com", "hi", true, false, true) mean?
    // You have to look at the parameter names every time.
    if html {
        // render as HTML
    }
    if urgent {
        // set priority header
    }
    if track {
        // add tracking pixel
    }
    println!("Sending to {}", to);
}
```

At the call site:

```rust
fn main() {
    send_email("bob@example.com", "Hello!", true, false, true);
    // What is true, false, true? Nobody knows without checking the definition.
}
```

This is boolean blindness — the booleans carry no semantic meaning at the call site. And it gets worse when someone swaps two of them accidentally. The code compiles fine. The bug is silent.

---

## The Idiomatic Way: Use Enums

```rust
#[derive(Debug)]
enum EmailFormat {
    PlainText,
    Html,
}

#[derive(Debug)]
enum Priority {
    Normal,
    Urgent,
}

#[derive(Debug)]
enum Tracking {
    Enabled,
    Disabled,
}

fn send_email(
    to: &str,
    body: &str,
    format: EmailFormat,
    priority: Priority,
    tracking: Tracking,
) {
    match format {
        EmailFormat::Html => { /* render HTML */ }
        EmailFormat::PlainText => { /* render plain text */ }
    }
    match priority {
        Priority::Urgent => { /* set priority header */ }
        Priority::Normal => {}
    }
    match tracking {
        Tracking::Enabled => { /* add tracking */ }
        Tracking::Disabled => {}
    }
    println!("Sending to {}", to);
}

fn main() {
    send_email(
        "bob@example.com",
        "Hello!",
        EmailFormat::Html,
        Priority::Normal,
        Tracking::Enabled,
    );
    // Crystal clear. No ambiguity. Can't accidentally swap priority and tracking.
}
```

The call site is self-documenting. You can read it without looking at the function signature. And you *can't* pass `Priority::Urgent` where `Tracking` is expected — the type system prevents it.

---

## Beyond Two States

Booleans are limited to two states. Requirements rarely are.

```rust
// Started as a boolean, then requirements grew:
// "Can users also be in a 'pending approval' state?"
// "What about 'banned with a reason'?"

// BAD: boolean flags that grew out of control
struct UserBad {
    name: String,
    is_active: bool,
    is_banned: bool,
    is_pending: bool,
    ban_reason: Option<String>,
    // Can a user be both active and banned? is_active=true, is_banned=true?
    // That's an illegal state that the type system allows.
}

// GOOD: enum makes illegal states impossible
#[derive(Debug)]
enum UserStatus {
    PendingApproval,
    Active,
    Suspended { reason: String, until: Option<String> },
    Banned { reason: String },
    Deleted,
}

#[derive(Debug)]
struct User {
    name: String,
    status: UserStatus,
}

fn can_post(user: &User) -> bool {
    matches!(user.status, UserStatus::Active)
}

fn main() {
    let user = User {
        name: "Atharva".into(),
        status: UserStatus::Suspended {
            reason: "Spam".into(),
            until: Some("2024-06-01".into()),
        },
    };
    println!("{:?} can post: {}", user.name, can_post(&user));
}
```

With the boolean approach, you could have `is_active=true, is_banned=true` — a state that makes no sense but is perfectly representable. With the enum, a user has exactly one status. Always.

---

## Making Illegal States Unrepresentable

This is the core principle. If a combination of values shouldn't exist, don't make it possible to construct it.

```rust
// BAD: allows impossible states
struct Connection {
    is_connected: bool,
    is_authenticated: bool,
    username: Option<String>,
    // Can be authenticated without being connected? That's nonsense.
    // Can have a username while not authenticated? Also nonsense.
}

// GOOD: each state only contains relevant data
#[derive(Debug)]
enum Connection {
    Disconnected,
    Connected {
        address: String,
    },
    Authenticated {
        address: String,
        username: String,
        session_token: String,
    },
}

impl Connection {
    fn send_message(&self, msg: &str) -> Result<(), String> {
        match self {
            Connection::Authenticated { username, .. } => {
                println!("[{}] Sending: {}", username, msg);
                Ok(())
            }
            Connection::Connected { .. } => {
                Err("Must authenticate before sending messages".into())
            }
            Connection::Disconnected => {
                Err("Not connected".into())
            }
        }
    }
}

fn main() {
    let conn = Connection::Authenticated {
        address: "server.example.com".into(),
        username: "atharva".into(),
        session_token: "abc123".into(),
    };
    let _ = conn.send_message("Hello!");
}
```

Each variant carries exactly the data that's relevant for that state. `Disconnected` has no address. `Connected` has no username. `Authenticated` has everything. There's no way to have a session token without being authenticated.

---

## Replacing Boolean Returns

Functions that return `bool` are another code smell. What does `true` mean? What does `false` mean?

```rust
// BAD: what does the bool mean?
fn validate_password(password: &str) -> bool {
    password.len() >= 8
}

// GOOD: the return type tells you what happened
#[derive(Debug)]
enum PasswordStrength {
    TooShort { min_length: usize },
    Weak,
    Medium,
    Strong,
}

fn check_password(password: &str) -> PasswordStrength {
    if password.len() < 8 {
        return PasswordStrength::TooShort { min_length: 8 };
    }

    let has_upper = password.chars().any(|c| c.is_uppercase());
    let has_lower = password.chars().any(|c| c.is_lowercase());
    let has_digit = password.chars().any(|c| c.is_ascii_digit());
    let has_special = password.chars().any(|c| !c.is_alphanumeric());

    let score = [has_upper, has_lower, has_digit, has_special]
        .iter()
        .filter(|&&b| b)
        .count();

    match score {
        0..=1 => PasswordStrength::Weak,
        2..=3 => PasswordStrength::Medium,
        _ => PasswordStrength::Strong,
    }
}

fn main() {
    let result = check_password("MyP@ssw0rd!");
    match result {
        PasswordStrength::TooShort { min_length } => {
            println!("Too short! Need at least {} characters", min_length);
        }
        PasswordStrength::Weak => println!("Weak password"),
        PasswordStrength::Medium => println!("Decent password"),
        PasswordStrength::Strong => println!("Strong password!"),
    }
}
```

The enum return type is infinitely more useful than a boolean. The caller knows exactly what happened and can react accordingly.

---

## The `Option` Alternative to Boolean + Value

A common pattern: a boolean flag paired with a value that only matters when the flag is true.

```rust
// BAD
struct SearchOptions {
    use_regex: bool,
    regex_pattern: String, // Meaningless when use_regex is false
    case_sensitive: bool,
}

// GOOD
#[derive(Debug)]
enum SearchPattern {
    Literal(String),
    Regex(String),
}

#[derive(Debug)]
enum CaseSensitivity {
    Sensitive,
    Insensitive,
}

#[derive(Debug)]
struct SearchOptions {
    pattern: SearchPattern,
    case: CaseSensitivity,
}

fn search(text: &str, options: &SearchOptions) -> Vec<String> {
    let _ = text; // simplified
    match &options.pattern {
        SearchPattern::Literal(s) => {
            println!("Literal search for '{}'", s);
        }
        SearchPattern::Regex(pattern) => {
            println!("Regex search with '{}'", pattern);
        }
    }
    vec![]
}

fn main() {
    let opts = SearchOptions {
        pattern: SearchPattern::Regex(r"\d{3}-\d{4}".into()),
        case: CaseSensitivity::Insensitive,
    };
    search("some text", &opts);
}
```

---

## Enums With Methods

Enums in Rust aren't just data containers — they can have methods, making them even more powerful:

```rust
#[derive(Debug, Clone, Copy, PartialEq)]
enum Direction {
    North,
    South,
    East,
    West,
}

impl Direction {
    fn opposite(&self) -> Direction {
        match self {
            Direction::North => Direction::South,
            Direction::South => Direction::North,
            Direction::East => Direction::West,
            Direction::West => Direction::East,
        }
    }

    fn is_horizontal(&self) -> bool {
        matches!(self, Direction::East | Direction::West)
    }

    fn delta(&self) -> (i32, i32) {
        match self {
            Direction::North => (0, 1),
            Direction::South => (0, -1),
            Direction::East => (1, 0),
            Direction::West => (-1, 0),
        }
    }
}

fn main() {
    let dir = Direction::North;
    println!("{:?} opposite is {:?}", dir, dir.opposite());
    println!("Horizontal: {}", dir.is_horizontal());

    let (dx, dy) = dir.delta();
    println!("Move by ({}, {})", dx, dy);
}
```

---

## When Booleans Are Actually Fine

I'm not saying never use booleans. They're perfect when:

- The meaning is obvious from context: `is_empty()`, `contains()`, `starts_with()`
- There are genuinely only two states with no chance of growing
- It's a local variable with a clear name: `let found = items.contains(&target);`

```rust
fn main() {
    let name = "Atharva";
    let is_long = name.len() > 10; // Fine — obvious meaning
    let starts_upper = name.chars().next().map_or(false, |c| c.is_uppercase()); // Fine

    println!("{}: long={}, starts_upper={}", name, is_long, starts_upper);
}
```

The rule isn't "never use booleans." It's "if a boolean parameter makes the call site confusing, replace it with an enum."

---

## Key Takeaways

- Boolean parameters create ambiguous call sites — `f(true, false, true)` is unreadable.
- Enums are self-documenting: `Priority::Urgent` is clearer than `true`.
- Use enums to make illegal states unrepresentable — if a combination shouldn't exist, make it impossible to construct.
- Replace `bool` return values with descriptive enums when the boolean's meaning isn't obvious.
- Pair related booleans and optional values into a single enum with per-variant data.
- Booleans are fine for obvious predicates (`is_empty`, `contains`) — use judgment.
