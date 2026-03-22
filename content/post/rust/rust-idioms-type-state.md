---
title: "Lesson 6: The Typestate Pattern — Compile-time state machines"
author: Atharva Pandey
series: "Idiomatic Rust"
lesson: 6
keywords: [Rust, rust, idiomatic, typestate, state machine, compile time, type system]
tags: [Rust tutorial, rust, idiomatic-rust]
date: '2024-04-19T10:28:00.000Z'
---

I once spent two days debugging a production issue where someone called `.send()` on an HTTP request builder *before* setting the URL. The code compiled fine — it was a runtime error that only surfaced under specific conditions. In Python. In Java. In Go. This kind of bug is everywhere.

In Rust, you can make it literally impossible to compile. The typestate pattern encodes state transitions into the type system. If the state machine says you can't send before setting a URL, the compiler enforces it. Not with runtime checks. Not with assertions. With *types*.

---

## The Problem: Runtime State Validation

Here's the typical approach — a builder with runtime checks:

```rust
struct EmailBuilder {
    from: Option<String>,
    to: Option<String>,
    subject: Option<String>,
    body: Option<String>,
}

impl EmailBuilder {
    fn new() -> Self {
        EmailBuilder {
            from: None,
            to: None,
            subject: None,
            body: None,
        }
    }

    fn from(mut self, addr: &str) -> Self {
        self.from = Some(addr.to_string());
        self
    }

    fn to(mut self, addr: &str) -> Self {
        self.to = Some(addr.to_string());
        self
    }

    fn subject(mut self, subj: &str) -> Self {
        self.subject = Some(subj.to_string());
        self
    }

    fn body(mut self, content: &str) -> Self {
        self.body = Some(content.to_string());
        self
    }

    fn send(self) -> Result<(), String> {
        let from = self.from.ok_or("Missing 'from' field")?;
        let to = self.to.ok_or("Missing 'to' field")?;
        let subject = self.subject.ok_or("Missing 'subject' field")?;
        let body = self.body.unwrap_or_default();

        println!("Sending from {} to {}: {} — {}", from, to, subject, body);
        Ok(())
    }
}
```

This works. But nothing stops you from calling `send()` with missing fields — you discover the error at runtime. In a strongly typed language with a sophisticated type system, we can do better.

---

## The Typestate Pattern

The idea: use *different types* to represent different states. Methods are only available on the type that represents the correct state.

```rust
use std::marker::PhantomData;

// State markers — zero-sized types
struct NeedsFrom;
struct NeedsTo;
struct NeedsSubject;
struct Ready;

struct Email<State> {
    from: String,
    to: String,
    subject: String,
    body: String,
    _state: PhantomData<State>,
}

impl Email<NeedsFrom> {
    fn new() -> Email<NeedsFrom> {
        Email {
            from: String::new(),
            to: String::new(),
            subject: String::new(),
            body: String::new(),
            _state: PhantomData,
        }
    }

    fn from(self, addr: &str) -> Email<NeedsTo> {
        Email {
            from: addr.to_string(),
            to: self.to,
            subject: self.subject,
            body: self.body,
            _state: PhantomData,
        }
    }
}

impl Email<NeedsTo> {
    fn to(self, addr: &str) -> Email<NeedsSubject> {
        Email {
            from: self.from,
            to: addr.to_string(),
            subject: self.subject,
            body: self.body,
            _state: PhantomData,
        }
    }
}

impl Email<NeedsSubject> {
    fn subject(self, subj: &str) -> Email<Ready> {
        Email {
            from: self.from,
            to: self.to,
            subject: subj.to_string(),
            body: self.body,
            _state: PhantomData,
        }
    }
}

impl Email<Ready> {
    fn body(mut self, content: &str) -> Self {
        self.body = content.to_string();
        self
    }

    fn send(self) {
        println!(
            "Sending from {} to {}: {} — {}",
            self.from, self.to, self.subject, self.body
        );
    }
}

fn main() {
    // This compiles — correct order
    Email::new()
        .from("me@example.com")
        .to("you@example.com")
        .subject("Hello")
        .body("Hi there!")
        .send();

    // This won't compile — can't call .send() on NeedsSubject state
    // Email::new()
    //     .from("me@example.com")
    //     .to("you@example.com")
    //     .send(); // ERROR: no method named `send` found for Email<NeedsSubject>
}
```

The magic: `.send()` only exists on `Email<Ready>`. You physically cannot call it until you've gone through `from → to → subject`. The compiler doesn't just check this — it *makes it impossible to express the wrong sequence*.

---

## A Simpler Example: File Operations

Typestate works beautifully for resources with distinct operational phases:

```rust
use std::marker::PhantomData;

struct Closed;
struct Open;

struct FileHandle<State> {
    path: String,
    content: String,
    _state: PhantomData<State>,
}

impl FileHandle<Closed> {
    fn new(path: &str) -> FileHandle<Closed> {
        FileHandle {
            path: path.to_string(),
            content: String::new(),
            _state: PhantomData,
        }
    }

    fn open(self) -> FileHandle<Open> {
        println!("Opening {}", self.path);
        FileHandle {
            path: self.path,
            content: String::from("file contents here"),
            _state: PhantomData,
        }
    }
}

impl FileHandle<Open> {
    fn read(&self) -> &str {
        &self.content
    }

    fn write(&mut self, data: &str) {
        self.content.push_str(data);
    }

    fn close(self) -> FileHandle<Closed> {
        println!("Closing {}", self.path);
        FileHandle {
            path: self.path,
            content: String::new(),
            _state: PhantomData,
        }
    }
}

fn main() {
    let file = FileHandle::new("data.txt");
    // file.read(); // ERROR: no method `read` on FileHandle<Closed>

    let mut file = file.open();
    println!("{}", file.read());
    file.write(" appended data");
    println!("{}", file.read());

    let _closed = file.close();
    // _closed.read(); // ERROR: no method `read` on FileHandle<Closed>
}
```

Read and write are only available when the file is open. You literally cannot read from a closed file — the type system won't let you.

---

## Connection Pool Example

Here's a more realistic example — a database connection with distinct lifecycle states:

```rust
use std::marker::PhantomData;

struct Disconnected;
struct Connected;
struct InTransaction;

struct DbConn<State> {
    url: String,
    _state: PhantomData<State>,
}

impl DbConn<Disconnected> {
    fn new(url: &str) -> Self {
        DbConn {
            url: url.to_string(),
            _state: PhantomData,
        }
    }

    fn connect(self) -> Result<DbConn<Connected>, String> {
        println!("Connecting to {}", self.url);
        Ok(DbConn {
            url: self.url,
            _state: PhantomData,
        })
    }
}

impl DbConn<Connected> {
    fn query(&self, sql: &str) -> Vec<String> {
        println!("Executing: {}", sql);
        vec![String::from("row1"), String::from("row2")]
    }

    fn begin_transaction(self) -> DbConn<InTransaction> {
        println!("BEGIN TRANSACTION");
        DbConn {
            url: self.url,
            _state: PhantomData,
        }
    }

    fn disconnect(self) -> DbConn<Disconnected> {
        println!("Disconnecting");
        DbConn {
            url: self.url,
            _state: PhantomData,
        }
    }
}

impl DbConn<InTransaction> {
    fn query(&self, sql: &str) -> Vec<String> {
        println!("Executing (in txn): {}", sql);
        vec![String::from("row1")]
    }

    fn commit(self) -> DbConn<Connected> {
        println!("COMMIT");
        DbConn {
            url: self.url,
            _state: PhantomData,
        }
    }

    fn rollback(self) -> DbConn<Connected> {
        println!("ROLLBACK");
        DbConn {
            url: self.url,
            _state: PhantomData,
        }
    }
}

fn main() -> Result<(), String> {
    let conn = DbConn::new("postgres://localhost/mydb");
    let conn = conn.connect()?;

    let results = conn.query("SELECT * FROM users");
    println!("Got {} rows", results.len());

    let txn = conn.begin_transaction();
    txn.query("INSERT INTO users VALUES ('atharva')");
    let conn = txn.commit();

    // Can't commit twice — txn was consumed by commit()
    // txn.commit(); // ERROR: use of moved value

    // Can't query a disconnected connection
    let _disconnected = conn.disconnect();
    // _disconnected.query("SELECT 1"); // ERROR: no method `query`

    Ok(())
}
```

Notice: you can't commit a transaction twice (it's consumed). You can't query a disconnected connection. You can't disconnect mid-transaction. All enforced at compile time. Zero runtime cost.

---

## When to Use Typestate

Typestate is powerful but not free — it adds complexity to your API. Use it when:

1. **State transitions are critical to correctness.** Protocols, resource lifecycles, multi-step processes.
2. **Invalid states cause real damage.** Sending unfinished requests, querying closed connections, operating on uninitialized resources.
3. **The state machine is relatively simple.** 3-5 states is the sweet spot. Beyond that, the type explosion gets unwieldy.

Don't use typestate for:

- Simple optional fields (just use the builder pattern with `Option`).
- States that change dynamically at runtime (use enums instead).
- Internal implementation details that users don't interact with.

---

## The Trade-off: Ergonomics vs Safety

The honest truth: typestate makes APIs safer but sometimes less convenient. Users can't reorder method calls freely. You can't store the builder in different states in the same collection without a trait object or enum wrapper. And the type signatures can get verbose.

My take? For libraries and safety-critical code, typestate is worth the ergonomic cost. For internal application code, a runtime check with a good error message is usually fine.

The beauty of Rust is that *you get to choose*. Most languages don't even give you the option.

---

## Key Takeaways

- Typestate uses distinct types to represent states, making invalid transitions a compile error.
- `PhantomData` lets you tag a struct with a state type without any runtime cost.
- Methods only exist on the appropriate state type — calling them in the wrong state doesn't compile.
- Ownership ensures you can't use a value after a state transition (the old state is consumed).
- Use typestate for protocols, resource lifecycles, and multi-step processes. Skip it for simple internal code.
