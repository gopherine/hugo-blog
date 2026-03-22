---
title: "Lesson 6: State Machines with Enums — Compile-time guarantees"
author: Atharva Pandey
series: "Rust Pattern Matching & Enums Deep Dive"
lesson: 6
keywords: [Rust, rust, pattern matching, enums, state machines, typestate pattern, finite automata]
tags: [Rust tutorial, rust, pattern-matching]
date: '2024-08-02T07:20:00.000Z'
---

Three years ago, I debugged a payment processing system where an order could go from "refunded" back to "shipped." Nobody intended for that to happen. The state machine was implemented with a status string and a bunch of if/else checks scattered across eight files. One developer added a shortcut. Review missed it. Customers got refund emails followed by shipping confirmations.

If the state machine had been in the type system, that shortcut wouldn't have compiled.

## The Problem: State Machines in Strings and Booleans

Most state machines in production code aren't called state machines. They're a `status` column in a database, a `state` field on a struct, or a set of boolean flags. The transitions between states live in business logic functions, and nothing enforces that the transitions are valid.

```rust
// The "just use strings" approach
struct Order {
    id: u64,
    status: String, // "pending", "paid", "shipped", "delivered", "refunded"
    // What stops status from being "banana"? Nothing.
    // What stops going from "refunded" to "shipped"? Discipline.
}
```

This is a bug factory. Every function that touches `status` must check the current state, validate the transition, and handle invalid cases — all manually, all at runtime.

## The Idiomatic Way: Enums as State Machines

Rust enums model state machines naturally. Each variant is a state, each variant carries the data relevant to that state, and `match` forces you to handle every state explicitly:

```rust
#[derive(Debug)]
enum OrderState {
    Pending {
        created_at: u64,
        items: Vec<String>,
    },
    Paid {
        paid_at: u64,
        items: Vec<String>,
        amount_cents: u64,
        transaction_id: String,
    },
    Shipped {
        shipped_at: u64,
        items: Vec<String>,
        tracking_number: String,
    },
    Delivered {
        delivered_at: u64,
        items: Vec<String>,
    },
    Refunded {
        refunded_at: u64,
        reason: String,
        original_amount_cents: u64,
    },
    Cancelled {
        cancelled_at: u64,
        reason: String,
    },
}

#[derive(Debug)]
enum OrderError {
    InvalidTransition { from: String, to: String },
}

impl OrderState {
    fn pay(self, timestamp: u64, amount: u64, txn_id: String) -> Result<OrderState, OrderError> {
        match self {
            OrderState::Pending { items, .. } => Ok(OrderState::Paid {
                paid_at: timestamp,
                items,
                amount_cents: amount,
                transaction_id: txn_id,
            }),
            other => Err(OrderError::InvalidTransition {
                from: format!("{:?}", other),
                to: "Paid".to_string(),
            }),
        }
    }

    fn ship(self, timestamp: u64, tracking: String) -> Result<OrderState, OrderError> {
        match self {
            OrderState::Paid { items, .. } => Ok(OrderState::Shipped {
                shipped_at: timestamp,
                items,
                tracking_number: tracking,
            }),
            other => Err(OrderError::InvalidTransition {
                from: format!("{:?}", other),
                to: "Shipped".to_string(),
            }),
        }
    }

    fn deliver(self, timestamp: u64) -> Result<OrderState, OrderError> {
        match self {
            OrderState::Shipped { items, .. } => Ok(OrderState::Delivered {
                delivered_at: timestamp,
                items,
            }),
            other => Err(OrderError::InvalidTransition {
                from: format!("{:?}", other),
                to: "Delivered".to_string(),
            }),
        }
    }

    fn refund(self, timestamp: u64, reason: String) -> Result<OrderState, OrderError> {
        match self {
            OrderState::Paid { amount_cents, .. } => Ok(OrderState::Refunded {
                refunded_at: timestamp,
                reason,
                original_amount_cents: amount_cents,
            }),
            OrderState::Delivered { .. } => Ok(OrderState::Refunded {
                refunded_at: timestamp,
                reason,
                original_amount_cents: 0, // Would look up original amount
            }),
            other => Err(OrderError::InvalidTransition {
                from: format!("{:?}", other),
                to: "Refunded".to_string(),
            }),
        }
    }

    fn cancel(self, timestamp: u64, reason: String) -> Result<OrderState, OrderError> {
        match self {
            OrderState::Pending { .. } => Ok(OrderState::Cancelled {
                cancelled_at: timestamp,
                reason,
            }),
            other => Err(OrderError::InvalidTransition {
                from: format!("{:?}", other),
                to: "Cancelled".to_string(),
            }),
        }
    }
}

fn main() {
    let order = OrderState::Pending {
        created_at: 1000,
        items: vec!["Widget".to_string(), "Gadget".to_string()],
    };

    // Happy path
    let order = order.pay(1001, 2999, "txn_abc123".to_string()).unwrap();
    let order = order.ship(1002, "TRACK123".to_string()).unwrap();
    let order = order.deliver(1003).unwrap();
    println!("Final: {:?}", order);

    // Invalid transition — try to ship a pending order
    let order2 = OrderState::Pending {
        created_at: 2000,
        items: vec!["Doohickey".to_string()],
    };

    match order2.ship(2001, "TRACK456".to_string()) {
        Ok(_) => println!("Shipped!"),
        Err(e) => println!("Error: {:?}", e),
    }
}
```

Each transition method takes `self` by value — it *consumes* the old state. After calling `.pay()`, the `Pending` state no longer exists. You can't accidentally use the old state because the compiler moved it. That's ownership doing double duty as a state machine enforcement mechanism.

## Transition Tables

For more complex state machines, I like to separate the transition logic from the implementation:

```rust
#[derive(Debug, Clone, PartialEq)]
enum ConnectionState {
    Disconnected,
    Connecting { attempt: u32 },
    Connected { session_id: String },
    Reconnecting { attempt: u32, last_session: String },
    Failed { reason: String },
}

#[derive(Debug)]
enum ConnectionEvent {
    Connect,
    ConnectionEstablished(String), // session_id
    ConnectionLost,
    ConnectionFailed(String),      // reason
    Reset,
    MaxRetriesReached,
}

const MAX_RETRIES: u32 = 3;

fn transition(state: ConnectionState, event: ConnectionEvent) -> ConnectionState {
    match (state, event) {
        // From Disconnected
        (ConnectionState::Disconnected, ConnectionEvent::Connect) => {
            ConnectionState::Connecting { attempt: 1 }
        }

        // From Connecting
        (ConnectionState::Connecting { .. }, ConnectionEvent::ConnectionEstablished(sid)) => {
            ConnectionState::Connected { session_id: sid }
        }
        (ConnectionState::Connecting { attempt }, ConnectionEvent::ConnectionFailed(reason)) => {
            if attempt >= MAX_RETRIES {
                ConnectionState::Failed { reason }
            } else {
                ConnectionState::Connecting { attempt: attempt + 1 }
            }
        }

        // From Connected
        (ConnectionState::Connected { session_id }, ConnectionEvent::ConnectionLost) => {
            ConnectionState::Reconnecting {
                attempt: 1,
                last_session: session_id,
            }
        }

        // From Reconnecting
        (ConnectionState::Reconnecting { last_session, .. },
         ConnectionEvent::ConnectionEstablished(sid)) => {
            println!("Reconnected (was: {})", last_session);
            ConnectionState::Connected { session_id: sid }
        }
        (ConnectionState::Reconnecting { attempt, .. },
         ConnectionEvent::MaxRetriesReached) if attempt >= MAX_RETRIES => {
            ConnectionState::Failed {
                reason: "max retries exceeded".to_string(),
            }
        }
        (ConnectionState::Reconnecting { attempt, last_session },
         ConnectionEvent::ConnectionFailed(_)) => {
            ConnectionState::Reconnecting {
                attempt: attempt + 1,
                last_session,
            }
        }

        // From Failed
        (ConnectionState::Failed { .. }, ConnectionEvent::Reset) => {
            ConnectionState::Disconnected
        }

        // Invalid transitions — stay in current state
        (state, event) => {
            println!("Ignoring {:?} in state {:?}", event, state);
            state
        }
    }
}

fn main() {
    let mut state = ConnectionState::Disconnected;
    println!("State: {:?}", state);

    let events = vec![
        ConnectionEvent::Connect,
        ConnectionEvent::ConnectionFailed("timeout".to_string()),
        ConnectionEvent::ConnectionEstablished("sess_001".to_string()),
        ConnectionEvent::ConnectionLost,
        ConnectionEvent::ConnectionEstablished("sess_002".to_string()),
    ];

    for event in events {
        println!("Event: {:?}", event);
        state = transition(state, event);
        println!("State: {:?}\n", state);
    }
}
```

The tuple match `(state, event)` is the transition table. Every valid `(state, event)` pair has an arm. Invalid pairs fall through to the catch-all. If you add a new state or event, the compiler warns you about unhandled combinations (unless the catch-all swallows them — which is a tradeoff you make intentionally).

## The Typestate Pattern

The examples above catch invalid transitions at *runtime*. If you want compile-time enforcement — making invalid transitions impossible to even write — you use the typestate pattern. Instead of one enum, you use separate types for each state:

```rust
struct Draft {
    content: String,
}

struct InReview {
    content: String,
    reviewer: String,
}

struct Published {
    content: String,
    published_at: u64,
    url: String,
}

struct Archived {
    content: String,
    archived_at: u64,
}

// Each state has methods that return only valid next states

impl Draft {
    fn new(content: String) -> Self {
        Draft { content }
    }

    fn submit_for_review(self, reviewer: String) -> InReview {
        InReview {
            content: self.content,
            reviewer,
        }
    }
}

impl InReview {
    fn approve(self, timestamp: u64) -> Published {
        Published {
            content: self.content,
            published_at: timestamp,
            url: format!("/posts/{}", timestamp),
        }
    }

    fn reject(self) -> Draft {
        Draft {
            content: self.content,
        }
    }
}

impl Published {
    fn archive(self, timestamp: u64) -> Archived {
        Archived {
            content: self.content,
            archived_at: timestamp,
        }
    }

    fn url(&self) -> &str {
        &self.url
    }
}

fn main() {
    let post = Draft::new("My blog post".to_string());

    // Valid flow: draft -> review -> published -> archived
    let post = post.submit_for_review("editor@blog.com".to_string());
    let post = post.approve(1000);
    println!("Published at: {}", post.url());
    let _archived = post.archive(2000);

    // This won't compile — you can't publish a draft directly:
    // let post2 = Draft::new("Another post".to_string());
    // let published = post2.approve(1000); // ERROR: no method `approve` on Draft

    // This won't compile — you can't archive something in review:
    // let post3 = Draft::new("Third post".to_string());
    // let in_review = post3.submit_for_review("editor".to_string());
    // let archived = in_review.archive(1000); // ERROR: no method `archive` on InReview
}
```

This is the typestate pattern in action. `Draft` doesn't have an `approve()` method. `InReview` doesn't have an `archive()` method. Invalid transitions don't fail at runtime — they don't exist as callable code. The compiler removes the entire class of bugs.

The tradeoff? You can't easily store different states in the same collection. A `Vec<Draft>` and a `Vec<Published>` are different types. When you need heterogeneous collections, wrap them back in an enum:

```rust
enum BlogPost {
    Draft(Draft),
    InReview(InReview),
    Published(Published),
    Archived(Archived),
}
```

Now you get both: the enum for storage and the typestate methods for transitions.

## When to Use Which Approach

I pick between these based on the problem:

**Enum state machine** (runtime checks): Good when states are stored in databases, serialized over the wire, or managed by a framework. Most web application state machines fall here. You get exhaustive matching and clear data modeling, with invalid transitions caught at runtime.

**Typestate pattern** (compile-time checks): Good for protocol implementations, builder patterns, and any flow where you control the entire lifecycle in code. TCP connection setup, configuration builders, transaction lifecycles — places where an invalid transition is always a programmer error, never a user error.

**String/integer status** (no checks): Never. I'm not kidding. Even a simple two-state toggle should be a `bool` or a two-variant enum, not a string.

## A Practical Builder Pattern

The typestate pattern is most commonly seen in builders:

```rust
struct DbConfig {
    host: String,
    port: u16,
    database: String,
    pool_size: u32,
}

// Typestate markers
struct NeedsHost;
struct NeedsPort;
struct NeedsDatabase;
struct Ready;

struct DbConfigBuilder<State> {
    host: Option<String>,
    port: Option<u16>,
    database: Option<String>,
    pool_size: u32,
    _state: std::marker::PhantomData<State>,
}

impl DbConfigBuilder<NeedsHost> {
    fn new() -> Self {
        DbConfigBuilder {
            host: None,
            port: None,
            database: None,
            pool_size: 10,
            _state: std::marker::PhantomData,
        }
    }

    fn host(self, host: &str) -> DbConfigBuilder<NeedsPort> {
        DbConfigBuilder {
            host: Some(host.to_string()),
            port: self.port,
            database: self.database,
            pool_size: self.pool_size,
            _state: std::marker::PhantomData,
        }
    }
}

impl DbConfigBuilder<NeedsPort> {
    fn port(self, port: u16) -> DbConfigBuilder<NeedsDatabase> {
        DbConfigBuilder {
            host: self.host,
            port: Some(port),
            database: self.database,
            pool_size: self.pool_size,
            _state: std::marker::PhantomData,
        }
    }
}

impl DbConfigBuilder<NeedsDatabase> {
    fn database(self, db: &str) -> DbConfigBuilder<Ready> {
        DbConfigBuilder {
            host: self.host,
            port: self.port,
            database: Some(db.to_string()),
            pool_size: self.pool_size,
            _state: std::marker::PhantomData,
        }
    }
}

impl DbConfigBuilder<Ready> {
    fn pool_size(mut self, size: u32) -> Self {
        self.pool_size = size;
        self
    }

    fn build(self) -> DbConfig {
        DbConfig {
            host: self.host.unwrap(),
            port: self.port.unwrap(),
            database: self.database.unwrap(),
            pool_size: self.pool_size,
        }
    }
}

fn main() {
    let config = DbConfigBuilder::new()
        .host("localhost")
        .port(5432)
        .database("myapp")
        .pool_size(20)
        .build();

    println!("{}:{}/{} (pool: {})",
        config.host, config.port, config.database, config.pool_size);

    // This won't compile — can't build without setting required fields:
    // let bad = DbConfigBuilder::new().host("localhost").build();
    // ERROR: no method `build` on DbConfigBuilder<NeedsPort>
}
```

You can't call `.build()` until you've set host, port, and database — in that order. `pool_size` is optional with a default. The state machine is enforced entirely at compile time. Zero runtime cost, zero runtime checks.

State machines with enums (and the typestate pattern) are one of Rust's most powerful design tools. They turn runtime invariants into compile-time guarantees. The investment in type design pays off every time someone modifies the code — because the compiler catches the mistakes humans miss.

Next up: the visitor pattern via enums. When trait objects feel like the wrong tool, enum dispatch gives you exhaustive, zero-cost alternatives.
