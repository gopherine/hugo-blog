---
title: "Lesson 10: Dependent Type Tricks — Encoding constraints in types"
author: Atharva Pandey
series: "Rust Type System Wizardry"
lesson: 10
keywords: [Rust, rust, type system, dependent types, type-level constraints, refinement types, const generics]
tags: [Rust tutorial, rust, type-system, advanced]
date: '2025-09-22T15:50:00.000Z'
---

There's a running joke in the Rust community: "Rust has a dependent type system, it just doesn't know it." And like most good jokes, there's truth in it. Rust doesn't have *real* dependent types like Idris or Agda, where types can depend on arbitrary runtime values. But with const generics, sealed constructors, and some creative type engineering, you can get surprisingly close.

This final lesson is about pushing Rust's type system to its absolute limits — encoding constraints that most people assume you need a dependently-typed language for.

## What Are Dependent Types?

In a dependently-typed language, types can depend on values. The canonical example is a vector with its length encoded in the type:

```text
-- In Idris:
data Vect : Nat -> Type -> Type where
    Nil  : Vect 0 a
    (::) : a -> Vect n a -> Vect (n + 1) a
```

Here, `Vect 3 Int` is a different type from `Vect 5 Int`. The *value* 3 appears in the *type*. You can write a function `append : Vect n a -> Vect m a -> Vect (n + m) a` and the compiler will verify that the lengths add up.

Rust can't do this in full generality. But it can approximate it in specific, practically useful ways.

## Const Generics as Lightweight Dependent Types

Const generics are the closest thing Rust has to dependent types. The length is a type-level value:

```rust
struct Array<T, const N: usize> {
    data: [T; N],
}

impl<T: Default + Copy, const N: usize> Array<T, N> {
    fn new() -> Self {
        Array {
            data: [T::default(); N],
        }
    }
}

impl<T, const N: usize> Array<T, N> {
    fn len(&self) -> usize { N }

    fn get(&self, index: usize) -> Option<&T> {
        if index < N {
            Some(&self.data[index])
        } else {
            None
        }
    }
}

// Type-safe head: only works on non-empty arrays
impl<T, const N: usize> Array<T, N> {
    fn head(&self) -> &T
    where
        Assert<{ N > 0 }>: IsTrue,
    {
        &self.data[0]
    }
}

// These trait bounds encode the constraint "N > 0"
struct Assert<const COND: bool>;
trait IsTrue {}
impl IsTrue for Assert<true> {}

fn main() {
    let arr = Array::<i32, 5>::new();
    let first = arr.head(); // Works — 5 > 0
    println!("Head: {}", first);

    // let empty = Array::<i32, 0>::new();
    // empty.head(); // COMPILE ERROR: Assert<false> doesn't implement IsTrue
}
```

The `where Assert<{ N > 0 }>: IsTrue` bound is a compile-time assertion. If `N` is 0, `Assert<false>` doesn't implement `IsTrue`, and the method is unavailable. The constraint is checked *by the type system*.

This is a form of dependent typing: the available methods on a type depend on the *value* of a const generic parameter.

## Bounded Integers

One of the most practical applications: integers that can only hold values in a specific range:

```rust
pub struct Bounded<const MIN: i64, const MAX: i64> {
    value: i64,
}

impl<const MIN: i64, const MAX: i64> Bounded<MIN, MAX> {
    pub fn new(value: i64) -> Option<Self> {
        if value >= MIN && value <= MAX {
            Some(Bounded { value })
        } else {
            None
        }
    }

    pub fn get(&self) -> i64 {
        self.value
    }

    // Saturating add that stays in bounds
    pub fn saturating_add(self, other: i64) -> Self {
        let result = (self.value + other).clamp(MIN, MAX);
        Bounded { value: result }
    }
}

// Type aliases for common ranges
type Percentage = Bounded<0, 100>;
type Port = Bounded<1, 65535>;
type DayOfMonth = Bounded<1, 31>;
type Byte = Bounded<0, 255>;

fn set_opacity(opacity: Percentage) {
    println!("Opacity: {}%", opacity.get());
    // We KNOW it's between 0 and 100. No validation needed.
}

fn listen(port: Port) {
    println!("Listening on port {}", port.get());
    // We KNOW it's between 1 and 65535.
}

fn main() {
    let opacity = Percentage::new(75).unwrap();
    set_opacity(opacity);

    // Can't pass an invalid percentage
    let invalid = Percentage::new(150); // Returns None
    assert!(invalid.is_none());

    // Can't pass a raw i64 where Percentage is expected
    // set_opacity(75); // TYPE ERROR
}
```

`Percentage` and `Port` are different types. You can't mix them up. And neither can hold an out-of-range value. This is refinement typing — types refined by predicates on their values.

## Type-Safe State with Encoded Capabilities

Here's a pattern I've used in production: encoding a set of capabilities as type-level booleans:

```rust
use std::marker::PhantomData;

struct Yes;
struct No;

struct File<CanRead, CanWrite, CanSeek> {
    path: String,
    _read: PhantomData<CanRead>,
    _write: PhantomData<CanWrite>,
    _seek: PhantomData<CanSeek>,
}

impl File<No, No, No> {
    fn open(path: &str) -> Self {
        File {
            path: path.to_string(),
            _read: PhantomData,
            _write: PhantomData,
            _seek: PhantomData,
        }
    }
}

// Grant capabilities
impl<W, S> File<No, W, S> {
    fn with_read(self) -> File<Yes, W, S> {
        File {
            path: self.path,
            _read: PhantomData,
            _write: PhantomData,
            _seek: PhantomData,
        }
    }
}

impl<R, S> File<R, No, S> {
    fn with_write(self) -> File<R, Yes, S> {
        File {
            path: self.path,
            _read: PhantomData,
            _write: PhantomData,
            _seek: PhantomData,
        }
    }
}

impl<R, W> File<R, W, No> {
    fn with_seek(self) -> File<R, W, Yes> {
        File {
            path: self.path,
            _read: PhantomData,
            _write: PhantomData,
            _seek: PhantomData,
        }
    }
}

// Operations require specific capabilities
impl<W, S> File<Yes, W, S> {
    fn read_all(&self) -> Vec<u8> {
        println!("Reading from {}", self.path);
        vec![]
    }
}

impl<R, S> File<R, Yes, S> {
    fn write_bytes(&self, data: &[u8]) {
        println!("Writing {} bytes to {}", data.len(), self.path);
    }
}

impl<R, W> File<R, W, Yes> {
    fn seek_to(&self, pos: u64) {
        println!("Seeking to position {} in {}", pos, self.path);
    }
}

fn main() {
    let f = File::open("data.txt")
        .with_read()
        .with_write();

    f.read_all();       // Works — has read capability
    f.write_bytes(b"hello"); // Works — has write capability
    // f.seek_to(0);    // COMPILE ERROR — no seek capability

    // Read-only file
    let readonly = File::open("config.txt").with_read();
    readonly.read_all();
    // readonly.write_bytes(b"oops"); // COMPILE ERROR — no write capability
}
```

Each capability is a type parameter. Methods are only available when the corresponding capability is `Yes`. The compiler tracks which capabilities a file handle has, and prevents operations that weren't granted. Zero runtime cost — all three `PhantomData` fields are zero-sized.

## Non-Empty Collections

Let's build a proper non-empty vector — one that the type system guarantees always has at least one element:

```rust
pub struct NonEmptyVec<T> {
    head: T,
    tail: Vec<T>,
}

impl<T> NonEmptyVec<T> {
    pub fn new(head: T) -> Self {
        NonEmptyVec {
            head,
            tail: Vec::new(),
        }
    }

    pub fn from_vec(mut vec: Vec<T>) -> Option<Self> {
        if vec.is_empty() {
            None
        } else {
            let head = vec.remove(0);
            Some(NonEmptyVec { head, tail: vec })
        }
    }

    pub fn first(&self) -> &T {
        &self.head // Always safe — always exists
    }

    pub fn last(&self) -> &T {
        self.tail.last().unwrap_or(&self.head)
    }

    pub fn push(&mut self, value: T) {
        self.tail.push(value);
    }

    pub fn len(&self) -> usize {
        1 + self.tail.len() // Always >= 1
    }

    pub fn iter(&self) -> impl Iterator<Item = &T> {
        std::iter::once(&self.head).chain(self.tail.iter())
    }

    // fold doesn't need an initial value — the head IS the initial value
    pub fn reduce<F: Fn(T, T) -> T>(self, f: F) -> T
    where
        T: Clone,
    {
        self.tail.into_iter().fold(self.head, f)
    }
}

fn average(values: &NonEmptyVec<f64>) -> f64 {
    let sum: f64 = values.iter().sum();
    sum / values.len() as f64
    // No division by zero possible — len() >= 1
}

fn main() {
    let scores = NonEmptyVec::new(85.0);

    println!("Average: {}", average(&scores));

    let mut more_scores = NonEmptyVec::new(90.0);
    more_scores.push(85.0);
    more_scores.push(92.0);

    println!("Average: {}", average(&more_scores));

    // Can't create from empty:
    let empty: Option<NonEmptyVec<i32>> = NonEmptyVec::from_vec(vec![]);
    assert!(empty.is_none());
}
```

`NonEmptyVec` makes the non-emptiness invariant *structural*. There's no way to construct an empty one. `first()` and `last()` don't return `Option` — they always succeed. `reduce()` doesn't need an initial value because the head serves as one. Division by `len()` is always safe.

## Type-Level Permission System

This is one of my favorite patterns — encoding authorization levels in the type system:

```rust
use std::marker::PhantomData;

// Permission levels — ordered by privilege
trait Permission {}
struct Guest;
struct User;
struct Admin;
struct SuperAdmin;

impl Permission for Guest {}
impl Permission for User {}
impl Permission for Admin {}
impl Permission for SuperAdmin {}

// "At least" relationships
trait AtLeast<P: Permission> {}
impl AtLeast<Guest> for Guest {}
impl AtLeast<Guest> for User {}
impl AtLeast<Guest> for Admin {}
impl AtLeast<Guest> for SuperAdmin {}
impl AtLeast<User> for User {}
impl AtLeast<User> for Admin {}
impl AtLeast<User> for SuperAdmin {}
impl AtLeast<Admin> for Admin {}
impl AtLeast<Admin> for SuperAdmin {}
impl AtLeast<SuperAdmin> for SuperAdmin {}

struct Session<P: Permission> {
    username: String,
    _level: PhantomData<P>,
}

impl Session<Guest> {
    fn anonymous() -> Self {
        Session {
            username: "anonymous".to_string(),
            _level: PhantomData,
        }
    }
}

impl<P: Permission> Session<P> {
    fn upgrade<Q: Permission>(self) -> Session<Q> {
        Session {
            username: self.username,
            _level: PhantomData,
        }
    }
}

// Public pages — anyone can view
fn view_homepage<P: Permission + AtLeast<Guest>>(_session: &Session<P>) {
    println!("Welcome to the homepage");
}

// Profile — requires at least User level
fn view_profile<P: Permission + AtLeast<User>>(session: &Session<P>) {
    println!("Profile for: {}", session.username);
}

// Admin panel — requires at least Admin level
fn admin_panel<P: Permission + AtLeast<Admin>>(session: &Session<P>) {
    println!("Admin panel for: {}", session.username);
}

// System config — requires SuperAdmin
fn system_config<P: Permission + AtLeast<SuperAdmin>>(session: &Session<P>) {
    println!("System config accessed by: {}", session.username);
}

fn main() {
    let guest = Session::<Guest>::anonymous();
    view_homepage(&guest); // OK

    // guest can't view profile:
    // view_profile(&guest); // COMPILE ERROR: Guest doesn't implement AtLeast<User>

    let admin: Session<Admin> = guest.upgrade();
    view_homepage(&admin);  // OK — Admin >= Guest
    view_profile(&admin);   // OK — Admin >= User
    admin_panel(&admin);    // OK — Admin >= Admin
    // system_config(&admin); // COMPILE ERROR: Admin doesn't implement AtLeast<SuperAdmin>
}
```

The permission hierarchy is entirely in the type system. An `Admin` session can access anything up to admin level, but not super-admin functions. A `Guest` session can only view public pages. The compiler enforces the access control policy.

## Encoding Natural Number Constraints

Using const generics with nightly features, you can encode arithmetic constraints:

```rust
#![feature(generic_const_exprs)]

// A matrix where we encode that rows * cols matches the data length
struct Matrix<const ROWS: usize, const COLS: usize> {
    data: [f64; ROWS * COLS],
}

// Safe transpose: (R x C) -> (C x R)
impl<const R: usize, const C: usize> Matrix<R, C>
where
    [(); R * C]:,
    [(); C * R]:,
{
    fn transpose(&self) -> Matrix<C, R> {
        let mut result = [0.0f64; C * R];
        for i in 0..R {
            for j in 0..C {
                result[j * R + i] = self.data[i * C + j];
            }
        }
        Matrix { data: result }
    }
}

// Matrix multiplication with dimension checking
impl<const M: usize, const N: usize> Matrix<M, N>
where
    [(); M * N]:,
{
    fn multiply<const P: usize>(&self, other: &Matrix<N, P>) -> Matrix<M, P>
    where
        [(); N * P]:,
        [(); M * P]:,
    {
        let mut result = [0.0f64; M * P];
        for i in 0..M {
            for j in 0..P {
                let mut sum = 0.0;
                for k in 0..N {
                    sum += self.data[i * N + k] * other.data[k * P + j];
                }
                result[i * P + j] = sum;
            }
        }
        Matrix { data: result }
    }
}
```

On nightly, `ROWS * COLS` in the array type is computed at compile time. The transpose function changes the type from `Matrix<R, C>` to `Matrix<C, R>`. Multiplication from `Matrix<M, N>` and `Matrix<N, P>` produces `Matrix<M, P>`. The shared `N` ensures dimensions match.

## The Builder Pattern with Required Fields

Here's a practical pattern for ensuring that all required fields are set before building:

```rust
use std::marker::PhantomData;

struct Required;
struct Optional;
struct Set;

struct ConfigBuilder<Host, Port, DbName> {
    host: Option<String>,
    port: Option<u16>,
    db_name: Option<String>,
    max_connections: u32,  // Optional with default
    _host: PhantomData<Host>,
    _port: PhantomData<Port>,
    _db_name: PhantomData<DbName>,
}

impl ConfigBuilder<Required, Required, Required> {
    fn new() -> Self {
        ConfigBuilder {
            host: None,
            port: None,
            db_name: None,
            max_connections: 10,
            _host: PhantomData,
            _port: PhantomData,
            _db_name: PhantomData,
        }
    }
}

impl<P, D> ConfigBuilder<Required, P, D> {
    fn host(self, host: &str) -> ConfigBuilder<Set, P, D> {
        ConfigBuilder {
            host: Some(host.to_string()),
            port: self.port,
            db_name: self.db_name,
            max_connections: self.max_connections,
            _host: PhantomData,
            _port: PhantomData,
            _db_name: PhantomData,
        }
    }
}

impl<H, D> ConfigBuilder<H, Required, D> {
    fn port(self, port: u16) -> ConfigBuilder<H, Set, D> {
        ConfigBuilder {
            host: self.host,
            port: Some(port),
            db_name: self.db_name,
            max_connections: self.max_connections,
            _host: PhantomData,
            _port: PhantomData,
            _db_name: PhantomData,
        }
    }
}

impl<H, P> ConfigBuilder<H, P, Required> {
    fn db_name(self, name: &str) -> ConfigBuilder<H, P, Set> {
        ConfigBuilder {
            host: self.host,
            port: self.port,
            db_name: Some(name.to_string()),
            max_connections: self.max_connections,
            _host: PhantomData,
            _port: PhantomData,
            _db_name: PhantomData,
        }
    }
}

impl<H, P, D> ConfigBuilder<H, P, D> {
    fn max_connections(mut self, n: u32) -> Self {
        self.max_connections = n;
        self
    }
}

struct DbConfig {
    host: String,
    port: u16,
    db_name: String,
    max_connections: u32,
}

// build() is ONLY available when all required fields are set
impl ConfigBuilder<Set, Set, Set> {
    fn build(self) -> DbConfig {
        DbConfig {
            host: self.host.unwrap(),
            port: self.port.unwrap(),
            db_name: self.db_name.unwrap(),
            max_connections: self.max_connections,
        }
    }
}

fn main() {
    // All required fields — builds successfully
    let config = ConfigBuilder::new()
        .host("localhost")
        .port(5432)
        .db_name("myapp")
        .max_connections(20)
        .build();

    println!("Connected to {}:{}/{}", config.host, config.port, config.db_name);

    // Missing db_name — won't compile:
    // let bad = ConfigBuilder::new()
    //     .host("localhost")
    //     .port(5432)
    //     .build(); // ERROR: no method `build` on ConfigBuilder<Set, Set, Required>

    // Fields can be set in any order:
    let config2 = ConfigBuilder::new()
        .db_name("other")
        .port(3306)
        .host("remote-server")
        .build();

    println!("Connected to {}:{}/{}", config2.host, config2.port, config2.db_name);
}
```

Each required field starts as `Required` and becomes `Set` when you call the corresponding method. `build()` is only available when all three are `Set`. The compiler catches missing fields — not at runtime, not through documentation, but through the type system.

## Where Rust Stops and True Dependent Types Begin

Let's be honest about the boundaries. Rust can encode:
- Fixed numeric constraints via const generics
- Capability tracking via phantom type parameters
- Ordered state transitions via typestate
- Validated data via sealed constructors

Rust *cannot* encode:
- Constraints that depend on runtime values ("this vector has the same length as that vector")
- Arbitrary logical propositions as types
- Proofs about program behavior (termination, correctness)
- Type-level functions over runtime data

For those, you genuinely need a language like Idris, Agda, Coq, or Lean. But here's my honest take after years of working in Rust: the subset of dependent typing that Rust supports covers the vast majority of real-world needs. I've shipped production systems with const-generic dimensions, typestate protocols, and proof witnesses, and I've never once thought "I really wish I had full dependent types right now."

The pragmatic subset is enough. Rust gives you *just enough* type-level power to make the important invariants compile-time checked, without drowning you in proof obligations for every integer addition. That's the Rust philosophy: practical safety, not theoretical purity.

## Wrapping Up the Series

Over these ten lessons, we've gone from zero-sized types (the building blocks) through typestate, session types, higher-kinded type simulation, existential types, variance, type-level computation, sealed traits, proof witnesses, and dependent type approximations.

The common thread: Rust's type system is far more expressive than most people realize. It's not Haskell. It's not Idris. But it's a type system where you can encode real-world invariants — protocol correctness, dimensional analysis, capability tracking, state machine validity — and have the compiler enforce them for free.

The key is knowing *when* to use these techniques. Not every function needs proof witnesses. Not every struct needs phantom type parameters. But when correctness matters — when a bug means corrupted data, a security breach, or a system crash — these tools are invaluable. They move the bug from "discovered in production at 3 AM" to "caught by the compiler before you push."

That's what type system wizardry is really about. Not showing off clever types. Making programs that cannot be wrong.
