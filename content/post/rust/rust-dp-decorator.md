---
title: "Lesson 5: Decorator Pattern — Wrapping with trait composition"
author: Atharva Pandey
series: "Rust Design Patterns"
lesson: 5
keywords: [Rust, rust, design patterns, decorator pattern, trait composition, newtype, wrapper types]
tags: [Rust tutorial, rust, design-patterns, architecture]
date: '2025-10-09T16:30:00.000Z'
---

I remember the moment Decorator clicked for me. I was reading the source for Java's I/O library — `BufferedInputStream` wrapping `FileInputStream` wrapping `InputStream`. Three layers deep, each adding behavior without subclassing. Elegant. Then I tried to write the same thing in Rust and learned that "wrapping a thing while preserving its interface" is a fundamentally different exercise when you don't have inheritance.

The good news? Rust's version is often *better* than the OOP original, because composition is the default and the compiler enforces the contracts.

## Decorator in OOP vs. Rust

In Java or Python, Decorator works through inheritance. You extend a base class (or implement an interface), hold an instance of the same type, and delegate all methods — overriding the ones you want to modify. The problem is that delegation is manual and error-prone. Miss one method and you've silently broken the contract.

Rust doesn't have inheritance. Instead, you implement a trait for your wrapper type, hold the inner type as a field, and delegate explicitly. It sounds like more work, but because traits are explicit and exhaustive, you *can't* miss a method. The compiler tells you.

## The Basic Pattern

Let's build a logging decorator for a simple service trait:

```rust
pub trait UserRepository {
    fn find_by_id(&self, id: u64) -> Option<User>;
    fn save(&mut self, user: &User) -> Result<(), String>;
    fn delete(&mut self, id: u64) -> Result<(), String>;
}

#[derive(Debug, Clone)]
pub struct User {
    pub id: u64,
    pub name: String,
    pub email: String,
}

// Concrete implementation
pub struct PostgresUserRepo {
    // In reality, this would hold a connection pool
    users: std::collections::HashMap<u64, User>,
}

impl PostgresUserRepo {
    pub fn new() -> Self {
        Self {
            users: std::collections::HashMap::new(),
        }
    }
}

impl UserRepository for PostgresUserRepo {
    fn find_by_id(&self, id: u64) -> Option<User> {
        self.users.get(&id).cloned()
    }

    fn save(&mut self, user: &User) -> Result<(), String> {
        self.users.insert(user.id, user.clone());
        Ok(())
    }

    fn delete(&mut self, id: u64) -> Result<(), String> {
        self.users
            .remove(&id)
            .map(|_| ())
            .ok_or_else(|| format!("User {} not found", id))
    }
}
```

Now the decorator — a logging wrapper:

```rust
pub struct LoggingUserRepo<R: UserRepository> {
    inner: R,
    prefix: String,
}

impl<R: UserRepository> LoggingUserRepo<R> {
    pub fn new(inner: R, prefix: impl Into<String>) -> Self {
        Self {
            inner,
            prefix: prefix.into(),
        }
    }
}

impl<R: UserRepository> UserRepository for LoggingUserRepo<R> {
    fn find_by_id(&self, id: u64) -> Option<User> {
        println!("[{}] find_by_id({})", self.prefix, id);
        let result = self.inner.find_by_id(id);
        println!(
            "[{}] find_by_id({}) -> {}",
            self.prefix,
            id,
            if result.is_some() { "found" } else { "not found" }
        );
        result
    }

    fn save(&mut self, user: &User) -> Result<(), String> {
        println!("[{}] save(user_id={})", self.prefix, user.id);
        let result = self.inner.save(user);
        println!("[{}] save -> {:?}", self.prefix, result);
        result
    }

    fn delete(&mut self, id: u64) -> Result<(), String> {
        println!("[{}] delete({})", self.prefix, id);
        let result = self.inner.delete(id);
        println!("[{}] delete -> {:?}", self.prefix, result);
        result
    }
}
```

And stacking decorators:

```rust
pub struct CachingUserRepo<R: UserRepository> {
    inner: R,
    cache: std::collections::HashMap<u64, User>,
}

impl<R: UserRepository> CachingUserRepo<R> {
    pub fn new(inner: R) -> Self {
        Self {
            inner,
            cache: std::collections::HashMap::new(),
        }
    }
}

impl<R: UserRepository> UserRepository for CachingUserRepo<R> {
    fn find_by_id(&self, id: u64) -> Option<User> {
        if let Some(user) = self.cache.get(&id) {
            return Some(user.clone());
        }
        self.inner.find_by_id(id)
    }

    fn save(&mut self, user: &User) -> Result<(), String> {
        self.cache.insert(user.id, user.clone());
        self.inner.save(user)
    }

    fn delete(&mut self, id: u64) -> Result<(), String> {
        self.cache.remove(&id);
        self.inner.delete(id)
    }
}

fn main() {
    let repo = PostgresUserRepo::new();
    let repo = CachingUserRepo::new(repo);
    let mut repo = LoggingUserRepo::new(repo, "UserService");

    let user = User {
        id: 1,
        name: "Alice".into(),
        email: "alice@example.com".into(),
    };

    repo.save(&user).unwrap();
    repo.find_by_id(1); // Hits cache
    repo.find_by_id(999); // Cache miss, hits inner
}
```

The type of `repo` is `LoggingUserRepo<CachingUserRepo<PostgresUserRepo>>`. That's verbose, but the compiler knows the exact chain at compile time. No vtable, no dynamic dispatch, every call can be inlined. In a hot path, this matters.

## The Newtype Pattern: Decorating Without Traits

Sometimes you want to add behavior to an existing type without defining a trait. The newtype pattern — wrapping a type in a single-field tuple struct — is Rust's lightweight Decorator:

```rust
pub struct SortedVec<T: Ord>(Vec<T>);

impl<T: Ord> SortedVec<T> {
    pub fn new() -> Self {
        Self(Vec::new())
    }

    pub fn insert(&mut self, item: T) {
        let pos = self.0.binary_search(&item).unwrap_or_else(|e| e);
        self.0.insert(pos, item);
    }

    pub fn contains(&self, item: &T) -> bool {
        self.0.binary_search(item).is_ok()
    }

    pub fn as_slice(&self) -> &[T] {
        &self.0
    }

    pub fn into_inner(self) -> Vec<T> {
        self.0
    }
}

impl<T: Ord + std::fmt::Debug> std::fmt::Debug for SortedVec<T> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "SortedVec({:?})", self.0)
    }
}
```

`SortedVec` decorates `Vec` with a sorted invariant. You can't accidentally unsort it because you only interact through the controlled API. The inner `Vec` is private. This is a compile-time guarantee that `Vec` alone can't provide.

In OOP languages, you'd subclass `ArrayList` and override `add()`. In Rust, you wrap and expose only the operations that preserve your invariant. It's more explicit and — in my opinion — more correct.

## Dynamic Decorator with Trait Objects

When you need runtime composition — maybe you're building decorators from a config file — you swap generics for trait objects:

```rust
pub struct DynLoggingRepo {
    inner: Box<dyn UserRepository>,
}

impl DynLoggingRepo {
    pub fn new(inner: Box<dyn UserRepository>) -> Self {
        Self { inner }
    }
}

impl UserRepository for DynLoggingRepo {
    fn find_by_id(&self, id: u64) -> Option<User> {
        println!("[LOG] find_by_id({})", id);
        self.inner.find_by_id(id)
    }

    fn save(&mut self, user: &User) -> Result<(), String> {
        println!("[LOG] save({})", user.id);
        self.inner.save(user)
    }

    fn delete(&mut self, id: u64) -> Result<(), String> {
        println!("[LOG] delete({})", id);
        self.inner.delete(id)
    }
}

// Build decorator chain at runtime
fn build_repo(config: &Config) -> Box<dyn UserRepository> {
    let mut repo: Box<dyn UserRepository> = Box::new(PostgresUserRepo::new());

    if config.enable_caching {
        // Need to take ownership and re-box
        repo = Box::new(CachingUserRepoBoxed::new(repo));
    }
    if config.enable_logging {
        repo = Box::new(DynLoggingRepo::new(repo));
    }

    repo
}
```

The trade-off is clear: dynamic dispatch adds a vtable lookup per method call, and you lose the compiler's ability to inline across layers. For I/O-bound operations like database queries, that's irrelevant. For CPU-bound inner loops, stick with generics.

## The `Deref` Antipattern

I see this mistake a lot: people implement `Deref` on their wrapper type to "inherit" all methods from the inner type:

```rust
// Don't do this for decorator purposes
impl<R: UserRepository> std::ops::Deref for LoggingUserRepo<R> {
    type Target = R;
    fn deref(&self) -> &R {
        &self.inner
    }
}
```

This lets you call methods on the inner type directly, bypassing the wrapper. That defeats the entire purpose of the decorator. `Deref` is for smart pointers (`Box`, `Arc`, `Rc`), not for decoration. The Rust API guidelines are explicit about this — `Deref` should only be used for transparent pointer-like types.

## Practical Example: Retrying HTTP Client

Here's a decorator stack I've used in production — a retry layer around an HTTP client:

```rust
pub trait HttpClient: Send + Sync {
    fn get(&self, url: &str) -> Result<Response, HttpError>;
    fn post(&self, url: &str, body: &[u8]) -> Result<Response, HttpError>;
}

#[derive(Debug)]
pub struct Response {
    pub status: u16,
    pub body: Vec<u8>,
}

#[derive(Debug)]
pub struct HttpError {
    pub message: String,
    pub retryable: bool,
}

pub struct RetryingClient<C: HttpClient> {
    inner: C,
    max_retries: u32,
    base_delay_ms: u64,
}

impl<C: HttpClient> RetryingClient<C> {
    pub fn new(inner: C, max_retries: u32) -> Self {
        Self {
            inner,
            max_retries,
            base_delay_ms: 100,
        }
    }

    fn retry<F>(&self, operation: F) -> Result<Response, HttpError>
    where
        F: Fn() -> Result<Response, HttpError>,
    {
        let mut last_error = None;
        for attempt in 0..=self.max_retries {
            match operation() {
                Ok(resp) => return Ok(resp),
                Err(e) if e.retryable && attempt < self.max_retries => {
                    let delay = self.base_delay_ms * 2u64.pow(attempt);
                    std::thread::sleep(std::time::Duration::from_millis(delay));
                    last_error = Some(e);
                }
                Err(e) => return Err(e),
            }
        }
        Err(last_error.unwrap())
    }
}

impl<C: HttpClient> HttpClient for RetryingClient<C> {
    fn get(&self, url: &str) -> Result<Response, HttpError> {
        self.retry(|| self.inner.get(url))
    }

    fn post(&self, url: &str, body: &[u8]) -> Result<Response, HttpError> {
        self.retry(|| self.inner.post(url, body))
    }
}
```

Each layer does one thing. The base client handles HTTP. The retry layer handles transient failures. You could add a `MetricsClient` that records latencies, a `CircuitBreakerClient` that opens after N failures, and stack them all together. Each decorator is independently testable — give it a mock inner client and verify its behavior in isolation.

## Key Takeaways

Decorator in Rust is composition, not inheritance. You wrap a type, implement the same trait, and delegate. Generics give you zero-cost stacking. Trait objects give you runtime flexibility. The newtype pattern gives you invariant enforcement without trait overhead.

The one thing to remember: don't abuse `Deref` for this. It's tempting, and it'll work until it doesn't — at which point you'll have subtle bugs where some calls go through the decorator and others bypass it. Keep your layers explicit. The compiler will thank you.
