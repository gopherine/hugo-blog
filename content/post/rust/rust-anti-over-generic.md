---
title: "Lesson 5: Over-Genericizing — Not everything needs <T>"
author: Atharva Pandey
series: "Rust Anti-Patterns & Code Smells"
lesson: 5
keywords: [Rust, rust, anti-patterns, code quality, generics, type parameters, monomorphization, complexity]
tags: [Rust tutorial, rust, anti-patterns, code-quality]
date: '2025-04-13T11:28:00.000Z'
---

I reviewed a library last year where the author had made literally everything generic. The HTTP client was generic over the transport, the serializer, the deserializer, the error type, the retry policy, the timeout strategy, and the logger. Using it required spelling out a type signature that looked like this:

```rust
let client: HttpClient<
    TcpTransport<TlsConfig>,
    JsonSerializer<PrettyPrint>,
    JsonDeserializer<StrictMode>,
    AppError,
    ExponentialBackoff<SystemClock>,
    FixedTimeout,
    SlogLogger<JsonFormat>,
> = HttpClient::new(/* ... */);
```

The kicker? The library was an internal tool used by exactly one team. There was one transport, one serializer, one error type. Every type parameter had exactly one implementation. The author had written an extensible framework for a problem that didn't need extending.

Generics are one of Rust's best features. They enable zero-cost abstractions, code reuse, and powerful type-level programming. They're also one of the most over-applied features I see in Rust codebases. Not everything needs to be generic. Sometimes a concrete type is the right answer.

## The Smell

Over-genericizing shows up in several patterns:

### Pattern 1: Generic for no reason

```rust
// This function is generic, but there's only one caller, and it always passes &str
fn process_input<T: AsRef<str>>(input: T) -> Result<Output> {
    let s = input.as_ref();
    // ... do stuff with s
}

// Every call site:
process_input("hello");
process_input(some_string.as_str());
// Nobody ever passes a custom AsRef<str> implementation
```

### Pattern 2: Type parameter soup

```rust
struct Pipeline<R, T, S, E>
where
    R: Reader<Item = T>,
    T: Transform<Output = S>,
    S: Sink<Error = E>,
    E: std::error::Error + Send + 'static,
{
    reader: R,
    transformer: PhantomData<T>,
    sink: PhantomData<S>,
    _error: PhantomData<E>,
}

impl<R, T, S, E> Pipeline<R, T, S, E>
where
    R: Reader<Item = T>,
    T: Transform<Output = S>,
    S: Sink<Error = E>,
    E: std::error::Error + Send + 'static,
{
    fn run(&self) -> Result<(), E> {
        // ... 10 lines of actual logic buried under 15 lines of where clauses
    }
}
```

The where clauses are longer than the implementation. That's a red flag.

### Pattern 3: "Future-proofing" that never pays off

```rust
// "We might want to support different databases someday"
trait DatabaseBackend {
    type Connection;
    type Error;
    fn connect(&self) -> Result<Self::Connection, Self::Error>;
    fn query(&self, conn: &Self::Connection, sql: &str) -> Result<Vec<Row>, Self::Error>;
}

struct UserService<DB: DatabaseBackend> {
    db: DB,
}

// Two years later: still only one implementation, PostgresBackend
```

## Why It's Actually Bad

**Compile times.** Every generic function is monomorphized — the compiler generates a separate copy for each concrete type it's called with. Even when there's only one concrete type, the compiler still has to do the generic resolution, trait bound checking, and monomorphization work. A heavily generic codebase compiles significantly slower than an equivalent concrete one.

**Error messages from hell.** Try getting a type mismatch error in a function with four type parameters and eight trait bounds. The compiler error will be a wall of text with nested type names that wrap around your terminal three times. I've seen developers spend an hour decoding a compiler error that boils down to "you passed the wrong type."

Here's a real-world-ish example of what you'll see:

```
error[E0277]: the trait bound `JsonParser<StrictMode>: Deserializer<for<'de>
    serde::de::DeserializeSeed<'de, Value = Response<Vec<UserDto<FullProfile>>>>>`
    is not satisfied
  --> src/client.rs:47:22
```

Good luck debugging that on a Friday afternoon.

**Cognitive overhead.** When you read `fn process(input: &str)`, you know exactly what it takes. When you read `fn process<T: AsRef<str> + Debug + Send + 'static>(input: T)`, you have to mentally evaluate the trait bounds to understand what's actually accepted. For a library with hundreds of users, that flexibility might be worth the cognitive cost. For internal code with three callers? Absolutely not.

**It makes refactoring harder, not easier.** The irony of over-genericizing for "flexibility" is that generic code is harder to change. Want to add a new capability to your pipeline? You need a new trait bound, which cascades through every impl block, every function signature, every caller. Concrete code is often easier to restructure because you're not fighting a web of trait constraints.

## The Fix

### Rule 1: Start concrete, generalize when you have two use cases

Don't write generic code until you actually need it for different types. Write the concrete version first:

```rust
// Start here
fn process_input(input: &str) -> Result<Output> {
    // ...
}

// Only if you genuinely need to accept String, &str, Cow<str>, etc.
// AND the callers actually benefit from it:
fn process_input(input: &str) -> Result<Output> {
    // Still concrete! The caller converts: process_input(&my_string)
}
```

Most of the time, just taking `&str` and letting the caller do `.as_ref()` or `.as_str()` is fine. The conversion happens once at the call site. You don't need to make the function generic to save the caller six characters.

### Rule 2: Use concrete types behind a trait when you need abstraction

If you need to swap implementations (real vs mock, for testing), use trait objects or a trait with one or two implementations — but keep the struct concrete:

```rust
// Define the trait
trait UserRepository {
    fn find_by_id(&self, id: &str) -> Result<Option<User>>;
    fn save(&self, user: &User) -> Result<()>;
}

// Option A: trait object (dynamic dispatch, simpler types)
struct UserService {
    repo: Box<dyn UserRepository>,
}

impl UserService {
    fn new(repo: impl UserRepository + 'static) -> Self {
        UserService { repo: Box::new(repo) }
    }

    fn get_user(&self, id: &str) -> Result<User> {
        self.repo.find_by_id(id)?
            .ok_or_else(|| anyhow::anyhow!("user not found: {id}"))
    }
}

// Option B: one generic parameter, if you really need static dispatch
struct UserService<R: UserRepository> {
    repo: R,
}
```

Option A gives you simpler type signatures everywhere `UserService` is used. Option B gives you static dispatch and no heap allocation. For most applications — especially ones doing I/O — the dynamic dispatch overhead is negligible and the simplicity wins.

### Rule 3: Use `impl Trait` in argument position for simple cases

When a function needs trait flexibility but you don't need to name the type parameter:

```rust
// Instead of this
fn log_all<I: IntoIterator<Item = T>, T: Display>(items: I) {
    for item in items {
        println!("{item}");
    }
}

// Do this
fn log_all(items: impl IntoIterator<Item = impl Display>) {
    for item in items {
        println!("{item}");
    }
}
```

Same monomorphization, cleaner syntax, and you don't pollute the function signature with type parameters you'll never reference again.

### Rule 4: The "three callers" test

Before making something generic, count the concrete types it'll actually be used with:

- **One type:** Don't make it generic. Period.
- **Two types:** Consider it, but only if the types are genuinely different (not just `String` and `&str`).
- **Three or more types:** Probably worth generalizing.

This isn't a hard rule, but it's a useful heuristic. The Rust standard library gets to be generic because it serves millions of use cases. Your internal order processing service probably doesn't.

### Rule 5: Watch the where clause length

If your where clauses are longer than your function body, something has gone wrong. Here's a refactoring technique: if you find yourself with this:

```rust
fn process<R, T, S, E>(reader: R, sink: S) -> Result<(), E>
where
    R: Reader<Item = T> + Send + Sync + 'static,
    T: Serialize + DeserializeOwned + Debug + Clone + Send + 'static,
    S: Sink<Item = T, Error = E> + Send + 'static,
    E: std::error::Error + From<io::Error> + From<serde_json::Error> + Send + 'static,
{
    let item = reader.next()?;
    sink.write(item)?;
    Ok(())
}
```

Ask yourself: what am I actually gaining from all this? Can I define a supertrait that bundles the common bounds?

```rust
trait PipelineItem: Serialize + DeserializeOwned + Debug + Clone + Send + 'static {}
impl<T> PipelineItem for T where T: Serialize + DeserializeOwned + Debug + Clone + Send + 'static {}

// Now the bounds are cleaner
fn process<T: PipelineItem>(reader: impl Reader<Item = T>, sink: impl Sink<Item = T>) -> Result<()> {
    let item = reader.next()?;
    sink.write(item)?;
    Ok(())
}
```

Or better yet, ask if you can just use concrete types.

## The Underlying Problem

Over-genericizing is usually driven by one of two impulses: "what if we need to change this later?" or "I want this to be reusable." Both are reasonable instincts taken too far.

The "what if" impulse leads to premature abstraction. You're solving a problem you don't have yet, and you might never have. If you do need to generalize later, Rust makes it relatively straightforward to extract a trait and add a type parameter — it's not the kind of refactoring that requires a ground-up rewrite.

The "reusability" impulse leads to over-engineered internal code. Internal libraries serve a known, finite set of use cases. You don't need the same level of generality as `serde` or `tokio`. Write concrete code that solves your specific problem clearly, and generalize only when a second genuine use case appears.

The best Rust code I've read is surprisingly concrete. It uses generics where they earn their keep and concrete types everywhere else. That's the balance to aim for.
