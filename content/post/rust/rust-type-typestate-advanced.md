---
title: "Lesson 2: Advanced Typestate — Multi-state machines at compile time"
author: Atharva Pandey
series: "Rust Type System Wizardry"
lesson: 2
keywords: [Rust, rust, type system, typestate, state machine, compile time, type-level state]
tags: [Rust tutorial, rust, type-system, advanced]
date: '2025-09-03T14:17:00.000Z'
---

The first time I implemented a connection pool that *couldn't* be misused — not through discipline or documentation, but because the compiler physically rejected invalid state transitions — I felt like I'd discovered a cheat code. Not a runtime check. Not an assertion. A straight-up compiler error if you tried to read from a connection you hadn't authenticated yet.

This is the typestate pattern taken to its logical extreme. In Lesson 1 we saw the basics with `PhantomData` and builder states. Now we're going to encode full multi-state machines where every transition is checked at compile time. No runtime overhead. No state field to match on. Just the type system doing its job.

## The Problem with Runtime State Machines

Most state machines look like this:

```rust
enum ConnectionState {
    Disconnected,
    Connected,
    Authenticated,
    InTransaction,
}

struct Connection {
    state: ConnectionState,
    // ... other fields
}

impl Connection {
    fn query(&self, sql: &str) -> Result<Vec<Row>, Error> {
        match self.state {
            ConnectionState::Authenticated | ConnectionState::InTransaction => {
                // do the query
                Ok(vec![])
            }
            _ => Err(Error::NotAuthenticated),
        }
    }
}
```

This works, but every method needs to check the current state. You find out about state violations at runtime — maybe in production, maybe at 3 AM. And nothing stops a future contributor from forgetting a state check in a new method.

## Encoding States as Types

The typestate pattern moves the state from a runtime value to a compile-time type parameter:

```rust
use std::marker::PhantomData;

// States — all zero-sized
struct Disconnected;
struct Connected;
struct Authenticated;
struct InTransaction;

struct Connection<State> {
    host: String,
    port: u16,
    _state: PhantomData<State>,
}
```

Each state is a ZST. `Connection<Disconnected>` and `Connection<Authenticated>` are *different types*. You can't pass one where the other is expected.

## Defining Transitions

State transitions become methods that consume `self` and return a new type:

```rust
use std::marker::PhantomData;

struct Disconnected;
struct Connected;
struct Authenticated;
struct InTransaction;

struct Connection<State> {
    host: String,
    port: u16,
    _state: PhantomData<State>,
}

struct Row;
struct Error;

impl Connection<Disconnected> {
    fn new(host: &str, port: u16) -> Self {
        Connection {
            host: host.to_string(),
            port,
            _state: PhantomData,
        }
    }

    fn connect(self) -> Result<Connection<Connected>, Error> {
        // ... TCP handshake ...
        Ok(Connection {
            host: self.host,
            port: self.port,
            _state: PhantomData,
        })
    }
}

impl Connection<Connected> {
    fn authenticate(self, user: &str, pass: &str) -> Result<Connection<Authenticated>, Error> {
        // ... auth handshake ...
        Ok(Connection {
            host: self.host,
            port: self.port,
            _state: PhantomData,
        })
    }

    fn disconnect(self) -> Connection<Disconnected> {
        Connection {
            host: self.host,
            port: self.port,
            _state: PhantomData,
        }
    }
}

impl Connection<Authenticated> {
    fn query(&self, sql: &str) -> Result<Vec<Row>, Error> {
        // ... execute query ...
        Ok(vec![])
    }

    fn begin_transaction(self) -> Connection<InTransaction> {
        Connection {
            host: self.host,
            port: self.port,
            _state: PhantomData,
        }
    }

    fn disconnect(self) -> Connection<Disconnected> {
        Connection {
            host: self.host,
            port: self.port,
            _state: PhantomData,
        }
    }
}

impl Connection<InTransaction> {
    fn query(&self, sql: &str) -> Result<Vec<Row>, Error> {
        Ok(vec![])
    }

    fn commit(self) -> Connection<Authenticated> {
        Connection {
            host: self.host,
            port: self.port,
            _state: PhantomData,
        }
    }

    fn rollback(self) -> Connection<Authenticated> {
        Connection {
            host: self.host,
            port: self.port,
            _state: PhantomData,
        }
    }
}
```

Now look at what happens:

```rust
fn main() -> Result<(), Error> {
    let conn = Connection::new("localhost", 5432);

    // This won't compile — can't query a disconnected connection
    // conn.query("SELECT 1");

    let conn = conn.connect()?;

    // This won't compile — can't query before auth
    // conn.query("SELECT 1");

    let conn = conn.authenticate("admin", "password")?;

    // NOW you can query
    conn.query("SELECT 1")?;

    let conn = conn.begin_transaction();
    conn.query("INSERT INTO users VALUES (1, 'alice')")?;

    // Can't begin a nested transaction — no such method on InTransaction
    // conn.begin_transaction();

    let conn = conn.commit();
    // Back to Authenticated state — can query again
    conn.query("SELECT * FROM users")?;

    Ok(())
}
```

Every invalid operation is a compile error. Not a runtime panic, not an error variant — a *compile error*. The compiler literally won't let you build a program that queries before authenticating.

## The Transition Boilerplate Problem

You probably noticed all those repetitive struct constructions during transitions. Let's fix that:

```rust
use std::marker::PhantomData;

struct Connection<State> {
    host: String,
    port: u16,
    _state: PhantomData<State>,
}

impl<S> Connection<S> {
    fn transition<NewState>(self) -> Connection<NewState> {
        Connection {
            host: self.host,
            port: self.port,
            _state: PhantomData,
        }
    }
}
```

Now transitions are clean:

```rust
impl Connection<Connected> {
    fn authenticate(self, _user: &str, _pass: &str) -> Connection<Authenticated> {
        // ... auth logic ...
        self.transition()
    }
}
```

One line. The `transition` method is a private implementation detail — it consumes the old state and produces the new one. Because `PhantomData` is zero-sized, this compiles down to literally nothing at runtime. The struct layout is identical across all states.

## Multi-Dimensional State

Real systems often have multiple independent state dimensions. A network connection might be both "authenticated" and "encrypted" — or just one, or neither. You can model this with multiple type parameters:

```rust
use std::marker::PhantomData;

struct Unencrypted;
struct Encrypted;
struct Unauthenticated;
struct Authenticated;

struct Connection<Encryption, Auth> {
    host: String,
    _enc: PhantomData<Encryption>,
    _auth: PhantomData<Auth>,
}

impl<Auth> Connection<Unencrypted, Auth> {
    fn upgrade_tls(self) -> Connection<Encrypted, Auth> {
        Connection {
            host: self.host,
            _enc: PhantomData,
            _auth: PhantomData,
        }
    }
}

impl<Enc> Connection<Enc, Unauthenticated> {
    fn authenticate(self, _creds: &str) -> Connection<Enc, Authenticated> {
        Connection {
            host: self.host,
            _enc: PhantomData,
            _auth: PhantomData,
        }
    }
}

// Only allow queries on encrypted + authenticated connections
impl Connection<Encrypted, Authenticated> {
    fn query(&self, _sql: &str) -> Vec<String> {
        vec![]
    }
}

fn main() {
    let conn: Connection<Unencrypted, Unauthenticated> = Connection {
        host: "db.example.com".to_string(),
        _enc: PhantomData,
        _auth: PhantomData,
    };

    // Can authenticate first, then encrypt
    let conn = conn.authenticate("admin");
    let conn = conn.upgrade_tls();
    conn.query("SELECT 1"); // Works — encrypted and authenticated

    // Or encrypt first, then authenticate
    let conn2: Connection<Unencrypted, Unauthenticated> = Connection {
        host: "db.example.com".to_string(),
        _enc: PhantomData,
        _auth: PhantomData,
    };
    let conn2 = conn2.upgrade_tls();
    let conn2 = conn2.authenticate("admin");
    conn2.query("SELECT 1"); // Also works
}
```

Two independent type parameters, four possible states, and the compiler tracks them independently. You can upgrade TLS regardless of auth state, and authenticate regardless of encryption state. But you can only query when *both* are in the right state.

## Conditional Methods with Trait Bounds

Sometimes you want a method available across multiple states but not all of them. Traits help:

```rust
use std::marker::PhantomData;

struct Idle;
struct Processing;
struct Completed;
struct Failed;

trait CanRetry {}
impl CanRetry for Failed {}
impl CanRetry for Idle {}

trait HasResult {}
impl HasResult for Completed {}
impl HasResult for Failed {}

struct Job<State> {
    id: u64,
    payload: String,
    _state: PhantomData<State>,
}

impl<S> Job<S> {
    fn transition<NewState>(self) -> Job<NewState> {
        Job {
            id: self.id,
            payload: self.payload,
            _state: PhantomData,
        }
    }
}

impl Job<Idle> {
    fn new(id: u64, payload: String) -> Self {
        Job {
            id,
            payload,
            _state: PhantomData,
        }
    }

    fn start(self) -> Job<Processing> {
        println!("Starting job {}", self.id);
        self.transition()
    }
}

impl Job<Processing> {
    fn complete(self) -> Job<Completed> {
        self.transition()
    }

    fn fail(self, _reason: &str) -> Job<Failed> {
        self.transition()
    }
}

// retry() is available on any state that implements CanRetry
impl<S: CanRetry> Job<S> {
    fn retry(self) -> Job<Processing> {
        println!("Retrying job {}", self.id);
        self.transition()
    }
}

// get_result() is available on any state that has a result
impl<S: HasResult> Job<S> {
    fn status_message(&self) -> &str {
        "finished (success or failure)"
    }
}

fn main() {
    let job = Job::new(1, "process_data".to_string());

    // Can retry from Idle
    let job = job.retry();

    // Simulate failure
    let job = job.fail("timeout");

    // Can check status on Failed (implements HasResult)
    println!("Status: {}", job.status_message());

    // Can retry from Failed
    let job = job.retry();
    let job = job.complete();

    // Can check status on Completed too
    println!("Status: {}", job.status_message());

    // Can't retry a completed job — Completed doesn't implement CanRetry
    // job.retry(); // Compile error!
}
```

This is where the pattern gets really expressive. You're not just encoding a linear state machine — you're encoding arbitrary rules about which operations are valid in which states, using trait bounds as the enforcement mechanism.

## Handling State-Specific Data

Sometimes different states carry different data. You can use an enum internally while still using typestate externally:

```rust
use std::marker::PhantomData;

struct Draft;
struct Published;
struct Archived;

struct Article<State> {
    title: String,
    body: String,
    metadata: StateData<State>,
    _state: PhantomData<State>,
}

// Different states carry different metadata
struct StateData<S>(PhantomData<S>);

struct DraftMeta {
    last_edit: String,
}

struct PublishedMeta {
    published_at: String,
    url: String,
}

struct ArchivedMeta {
    archived_at: String,
    reason: String,
}

impl Article<Draft> {
    fn new(title: String, body: String) -> Self {
        Article {
            title,
            body,
            metadata: StateData(PhantomData),
            _state: PhantomData,
        }
    }
}
```

But honestly — at this point you might be overcomplicating things. The beauty of typestate is its simplicity. If you need state-specific data, sometimes a regular enum with methods is cleaner. Typestate shines when the *operations* change between states, not the *data*.

## When Typestate Doesn't Work

I want to be real about the limitations, because I've seen people try to force typestate where it doesn't fit.

**Dynamic state transitions**: If the next state depends on runtime data (user input, API response), you can't use typestate directly. You'd need something like:

```rust
enum ConnectionResult {
    Authenticated(Connection<Authenticated>),
    Failed(Connection<Connected>),
}
```

This works, but it gets unwieldy fast.

**Collections of mixed states**: You can't put `Connection<Connected>` and `Connection<Authenticated>` in the same `Vec`, because they're different types. If you need a collection of connections in various states, you need a trait object or enum wrapper.

**Too many states**: If your state machine has 20 states and 50 transitions, typestate will drown you in impl blocks. Use it for critical protocols with a small number of states where correctness matters enormously.

## My Rules of Thumb

After using this pattern in production for a few years, here's when I reach for it:

1. **Safety-critical protocols**: Connection setup, payment processing, anything where a wrong state transition means data corruption.
2. **3-7 states max**: Beyond that, the ergonomic cost outweighs the safety benefit.
3. **Linear or near-linear flow**: Branching is fine, but if every state can transition to every other state, you're going to hate yourself.
4. **API boundaries**: When you're designing a library and want to make misuse impossible, not just unlikely.

The typestate pattern isn't about replacing every `match` statement with a type parameter. It's about identifying the *critical* state transitions in your system and making the compiler enforce them. Used judiciously, it's one of the most powerful tools in the Rust programmer's toolkit.

Next lesson, we'll take this further into session types — encoding entire communication protocols in the type system.
