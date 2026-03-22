---
title: "Lesson 3: Session Types — Protocol safety at compile time"
author: Atharva Pandey
series: "Rust Type System Wizardry"
lesson: 3
keywords: [Rust, rust, type system, session types, protocol safety, compile time, type-level protocols]
tags: [Rust tutorial, rust, type-system, advanced]
date: '2025-09-05T10:45:00.000Z'
---

I once spent three days debugging a distributed system where two services were sending messages in the wrong order. Service A expected a handshake acknowledgment before data, but Service B had been refactored to send data first. Both services compiled, both passed their unit tests, and both exploded in production. The protocol contract existed only in a Google Doc that nobody had updated.

Session types fix this. They encode the *entire communication protocol* in the type system — who sends what, in what order, and when. If you violate the protocol, your code doesn't compile. Period.

## What Are Session Types?

Session types come from process calculus theory (specifically, the pi-calculus). The core idea is: a communication channel has a *type* that describes the sequence of operations that must happen on it. After each operation, the channel's type changes to reflect what must happen next.

Think of it like a typestate pattern, but specifically designed for two-party communication.

In theory, a session type might look like:

```text
Send<Request, Recv<Response, End>>
```

This reads as: "Send a Request, then receive a Response, then the session ends." The dual (the other side's view) would be:

```text
Recv<Request, Send<Response, End>>
```

Whatever one side sends, the other receives. The types must be dual to each other, or the program won't type-check.

## Building Session Types in Rust

Rust doesn't have native session types, but we can simulate them. Let's build a minimal session type library:

```rust
use std::marker::PhantomData;

// The end of a session
struct End;

// Send a value of type T, then continue with protocol S
struct Send<T, S> {
    _phantom: PhantomData<(T, S)>,
}

// Receive a value of type T, then continue with protocol S
struct Recv<T, S> {
    _phantom: PhantomData<(T, S)>,
}

// A channel with a session type
struct Chan<Protocol> {
    // In a real implementation, this would wrap an actual channel
    _protocol: PhantomData<Protocol>,
}
```

All of these are zero-sized. The protocol exists purely in the type system.

## Implementing Channel Operations

Each operation on the channel consumes it and returns a new channel with the "rest" of the protocol:

```rust
use std::marker::PhantomData;
use std::sync::mpsc;

struct End;
struct Send<T, S>(PhantomData<(T, S)>);
struct Recv<T, S>(PhantomData<(T, S)>);

struct Chan<P> {
    tx: mpsc::Sender<Box<dyn std::any::Any + std::marker::Send>>,
    rx: mpsc::Receiver<Box<dyn std::any::Any + std::marker::Send>>,
    _protocol: PhantomData<P>,
}

impl<P> Chan<P> {
    fn transition<NewP>(self) -> Chan<NewP> {
        Chan {
            tx: self.tx,
            rx: self.rx,
            _protocol: PhantomData,
        }
    }
}

impl<T: std::any::Any + std::marker::Send, S> Chan<Send<T, S>> {
    fn send(self, value: T) -> Chan<S> {
        self.tx.send(Box::new(value)).unwrap();
        self.transition()
    }
}

impl<T: std::any::Any + std::marker::Send, S> Chan<Recv<T, S>> {
    fn recv(self) -> (T, Chan<S>) {
        let value = self.rx.recv().unwrap();
        let value = *value.downcast::<T>().unwrap();
        (value, self.transition())
    }
}

impl Chan<End> {
    fn close(self) {
        // Session complete — channel is consumed
        drop(self);
    }
}
```

The key insight: `send()` is only available when the protocol type starts with `Send<T, S>`. After sending, you get back a `Chan<S>` — the rest of the protocol. Same for `recv()`. And `close()` is only available when the protocol is `End`.

## Defining a Protocol

Let's define a simple request-response protocol:

```rust
use std::marker::PhantomData;

struct End;
struct Send<T, S>(PhantomData<(T, S)>);
struct Recv<T, S>(PhantomData<(T, S)>);

// Client's view of the protocol
// 1. Send a query string
// 2. Receive a response string
// 3. End
type ClientProtocol = Send<String, Recv<String, End>>;

// Server's view — the dual
// 1. Receive a query string
// 2. Send a response string
// 3. End
type ServerProtocol = Recv<String, Send<String, End>>;
```

The client and server protocols are duals of each other. We can compute the dual automatically:

```rust
use std::marker::PhantomData;

struct End;
struct Send<T, S>(PhantomData<(T, S)>);
struct Recv<T, S>(PhantomData<(T, S)>);

trait HasDual {
    type Dual;
}

impl HasDual for End {
    type Dual = End;
}

impl<T, S: HasDual> HasDual for Send<T, S> {
    type Dual = Recv<T, S::Dual>;
}

impl<T, S: HasDual> HasDual for Recv<T, S> {
    type Dual = Send<T, S::Dual>;
}
```

Now `<Send<String, Recv<String, End>> as HasDual>::Dual` automatically gives us `Recv<String, Send<String, End>>`. The compiler computes the server-side protocol from the client-side protocol.

## A Complete Working Example

Let me put it all together with a real-ish example — a typed ping-pong protocol:

```rust
use std::any::Any;
use std::marker::PhantomData;
use std::sync::mpsc;
use std::thread;

// Protocol primitives
struct End;
struct Send<T, S>(PhantomData<(T, S)>);
struct Recv<T, S>(PhantomData<(T, S)>);

// Dual computation
trait HasDual {
    type Dual;
}
impl HasDual for End {
    type Dual = End;
}
impl<T, S: HasDual> HasDual for Send<T, S> {
    type Dual = Recv<T, S::Dual>;
}
impl<T, S: HasDual> HasDual for Recv<T, S> {
    type Dual = Send<T, S::Dual>;
}

// Channel
struct Chan<P> {
    tx: mpsc::Sender<Box<dyn Any + std::marker::Send>>,
    rx: mpsc::Receiver<Box<dyn Any + std::marker::Send>>,
    _p: PhantomData<P>,
}

impl<P> Chan<P> {
    fn cast<Q>(self) -> Chan<Q> {
        Chan { tx: self.tx, rx: self.rx, _p: PhantomData }
    }
}

impl<T: Any + std::marker::Send, S> Chan<Send<T, S>> {
    fn send(self, val: T) -> Chan<S> {
        self.tx.send(Box::new(val)).unwrap();
        self.cast()
    }
}

impl<T: Any + std::marker::Send, S> Chan<Recv<T, S>> {
    fn recv(self) -> (T, Chan<S>) {
        let val = self.rx.recv().unwrap();
        (*val.downcast::<T>().unwrap(), self.cast())
    }
}

impl Chan<End> {
    fn close(self) { /* consumed */ }
}

// Create a session pair
fn session_pair<P: HasDual>() -> (Chan<P>, Chan<P::Dual>) {
    let (tx1, rx1) = mpsc::channel();
    let (tx2, rx2) = mpsc::channel();
    (
        Chan { tx: tx1, rx: rx2, _p: PhantomData },
        Chan { tx: tx2, rx: rx1, _p: PhantomData },
    )
}

// Protocol: Client sends i32, server responds with String, done.
type Protocol = Send<i32, Recv<String, End>>;

fn client(chan: Chan<Protocol>) {
    let chan = chan.send(42);
    let (response, chan) = chan.recv();
    println!("Client got: {}", response);
    chan.close();
}

fn server(chan: Chan<<Protocol as HasDual>::Dual>) {
    // Server's protocol is Recv<i32, Send<String, End>>
    let (number, chan) = chan.recv();
    println!("Server got: {}", number);
    let chan = chan.send(format!("You sent {}", number));
    chan.close();
}

fn main() {
    let (client_chan, server_chan) = session_pair::<Protocol>();

    let t1 = thread::spawn(move || client(client_chan));
    let t2 = thread::spawn(move || server(server_chan));

    t1.join().unwrap();
    t2.join().unwrap();
}
```

Run this and you get:

```text
Server got: 42
Client got: You sent 42
```

Now try swapping the `send` and `recv` calls in the client:

```rust
fn client_wrong(chan: Chan<Protocol>) {
    // Protocol says Send first, but we try to Recv
    let (response, chan) = chan.recv(); // COMPILE ERROR!
}
```

The compiler catches it. `Chan<Send<i32, ...>>` doesn't have a `recv()` method. Protocol violation detected at compile time.

## Adding Choice to Protocols

Real protocols have branching — "if the login succeeds, proceed with data; if it fails, disconnect." We can encode this:

```rust
use std::marker::PhantomData;

struct End;
struct Send<T, S>(PhantomData<(T, S)>);
struct Recv<T, S>(PhantomData<(T, S)>);

// Offer a choice: the peer selects between protocol A or protocol B
struct Offer<A, B>(PhantomData<(A, B)>);

// Select one of two protocols
struct Select<A, B>(PhantomData<(A, B)>);

// Channel with a selection method
struct Chan<P> {
    _p: PhantomData<P>,
}

impl<P> Chan<P> {
    fn cast<Q>(self) -> Chan<Q> {
        Chan { _p: PhantomData }
    }
}

enum Branch<A, B> {
    Left(A),
    Right(B),
}

impl<A, B> Chan<Offer<A, B>> {
    fn offer(self) -> Branch<Chan<A>, Chan<B>> {
        // In a real impl, read a tag from the channel
        // to determine which branch the peer selected
        Branch::Left(self.cast())
    }
}

impl<A, B> Chan<Select<A, B>> {
    fn select_left(self) -> Chan<A> {
        // Send a tag indicating left branch
        self.cast()
    }

    fn select_right(self) -> Chan<B> {
        // Send a tag indicating right branch
        self.cast()
    }
}
```

Now you can define protocols with branches:

```rust
use std::marker::PhantomData;

struct End;
struct Send<T, S>(PhantomData<(T, S)>);
struct Recv<T, S>(PhantomData<(T, S)>);
struct Select<A, B>(PhantomData<(A, B)>);

// Login protocol from client's perspective:
// 1. Send username
// 2. Send password
// 3. Select: either proceed to data exchange, or quit
type LoginProtocol = Send<String, Send<String,
    Select<
        // Success path: receive data, end
        Recv<Vec<u8>, End>,
        // Failure path: receive error message, end
        Recv<String, End>,
    >
>>;
```

The client *must* handle both branches. The server *must* handle both branches. The compiler enforces the complete protocol on both sides.

## Recursive Protocols

Some protocols repeat — think of a REPL or a message loop. This is trickier because Rust doesn't directly support recursive type aliases. But you can use a trait to define the recursion:

```rust
use std::marker::PhantomData;

struct End;
struct Send<T, S>(PhantomData<(T, S)>);
struct Recv<T, S>(PhantomData<(T, S)>);
struct Select<A, B>(PhantomData<(A, B)>);

// Use a wrapper struct for recursion
struct Rec<F>(PhantomData<F>);

// A message loop: send a message, receive a response, then either
// loop again or end.
// We can approximate this with a fixed depth or use a different encoding.

// Practical approach: define a "step" and manually chain it
type Step = Send<String, Recv<String, End>>;

// For three rounds of request-response:
type ThreeRounds = Send<String, Recv<String,
    Send<String, Recv<String,
        Send<String, Recv<String, End>>
    >>
>>;
```

Full recursive session types require more machinery than Rust's type system easily provides. In practice, I've found that either unrolling a few iterations or using a different encoding (like a trait-based approach) works well enough.

## When to Use Session Types

I'll be blunt — you probably won't implement a session type library from scratch for a production system. The existing crates like `session-types` and `sesstype` provide this, and the theory gets deep fast.

But understanding session types changes how you think about protocol design. Even if you don't use the full type-level machinery, the *idea* — that protocols should be statically verifiable — is incredibly valuable.

I've used lightweight versions of this pattern for:

- **Database connection protocols** (connect → auth → query → close)
- **Payment processing** (validate → authorize → capture/void)
- **File format parsers** (header → metadata → data → checksum)
- **State machine APIs** where users must call methods in a specific order

The full type-level encoding is for when correctness is non-negotiable — cryptographic protocols, financial transactions, safety-critical systems. For everything else, the typestate pattern from Lesson 2 usually gives you 80% of the benefit at 20% of the complexity.

## The Bigger Picture

Session types are part of a broader idea in type theory: using types to express *behavioral* properties, not just *structural* ones. A `String` tells you what data you have. A session type tells you what you *must do next*. That's a fundamentally different kind of type — and Rust's ownership system (which already tracks "who can use this value" and "when does it get destroyed") is uniquely suited to express it.

The fact that `send()` consumes the channel and returns a new one with a different type — that's ownership semantics making session types *natural* in Rust. In most other languages, you'd need a linear type system bolted on as an afterthought. In Rust, you already have it.

Next up: simulating higher-kinded types in Rust — the one thing Haskell programmers love to lord over us.
