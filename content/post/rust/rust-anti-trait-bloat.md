---
title: "Lesson 6: Trait Bloat — Interface segregation in Rust"
author: Atharva Pandey
series: "Rust Anti-Patterns & Code Smells"
lesson: 6
keywords: [Rust, rust, anti-patterns, code quality, traits, interface segregation, trait design, composition]
tags: [Rust tutorial, rust, anti-patterns, code-quality]
date: '2025-04-15T09:55:00.000Z'
---

I worked on a project that had a `Storage` trait with twenty-three methods. Twenty-three. It handled reading, writing, deleting, listing, searching, watching for changes, managing permissions, computing checksums, and streaming large files. Every backend — S3, local filesystem, in-memory for tests — had to implement all twenty-three methods. The in-memory test backend had fourteen methods that just returned `unimplemented!()`. The local filesystem backend panicked on the permissions methods because POSIX permissions don't map to the trait's model. And every time someone added a new method to the trait, every backend had to be updated — even the ones where the new method made no sense.

This is trait bloat. It's the Rust version of the Interface Segregation Principle violation from SOLID, and it's everywhere.

## The Smell

A bloated trait looks like this:

```rust
trait Repository {
    // Basic CRUD
    fn create(&self, entity: &Entity) -> Result<Id>;
    fn read(&self, id: &Id) -> Result<Option<Entity>>;
    fn update(&self, entity: &Entity) -> Result<()>;
    fn delete(&self, id: &Id) -> Result<()>;

    // Querying
    fn find_by_name(&self, name: &str) -> Result<Vec<Entity>>;
    fn find_by_date_range(&self, start: DateTime, end: DateTime) -> Result<Vec<Entity>>;
    fn search(&self, query: &SearchQuery) -> Result<SearchResults>;
    fn count(&self) -> Result<usize>;

    // Bulk operations
    fn bulk_create(&self, entities: &[Entity]) -> Result<Vec<Id>>;
    fn bulk_delete(&self, ids: &[Id]) -> Result<usize>;

    // Transactions
    fn begin_transaction(&mut self) -> Result<Transaction>;
    fn commit(&mut self, tx: Transaction) -> Result<()>;
    fn rollback(&mut self, tx: Transaction) -> Result<()>;

    // Migrations
    fn migrate(&self, version: u32) -> Result<()>;
    fn current_version(&self) -> Result<u32>;

    // Monitoring
    fn health_check(&self) -> Result<bool>;
    fn connection_count(&self) -> usize;
}
```

Seventeen methods. And here's the problem: most consumers of this trait only need two or three of them. The function that creates a user only needs `create`. The health check endpoint only needs `health_check`. The migration tool only needs `migrate` and `current_version`. But because they all depend on `Repository`, they all require an implementation that provides everything.

## Why It's Actually Bad

**Every implementor must implement everything.** If you have three backends (Postgres, SQLite, in-memory) and add one method to a 17-method trait, you're touching three files. If the new method doesn't make sense for all backends (in-memory migration? what?), you're writing `unimplemented!()` or returning a dummy value — which defeats the purpose of having a trait.

**Testing becomes painful.** Want to test a function that takes `&dyn Repository`? Your mock has to implement all seventeen methods, even if the function only calls one. Yes, you can use mockall or similar tools, but you're generating code for sixteen methods you don't care about.

**It couples unrelated concerns.** Why does the same trait handle CRUD operations and database migrations? These are different responsibilities with different consumers, different lifecycles, and different testing requirements. Bundling them together creates artificial dependencies.

**Trait objects become impossible.** Some of those methods might have generic parameters or return `Self`, which makes the trait not object-safe. Now you can't use `dyn Repository` at all, forcing static dispatch everywhere, which feeds into the over-genericizing problem from the previous lesson.

**It violates the principle of least privilege.** A function that only needs to read data shouldn't receive a capability that lets it delete data. With a giant trait, every consumer gets full power whether they need it or not.

## The Fix

### Step 1: Split by consumer need

Look at who calls each method. Group methods that are always used together:

```rust
trait ReadRepository {
    fn read(&self, id: &Id) -> Result<Option<Entity>>;
    fn find_by_name(&self, name: &str) -> Result<Vec<Entity>>;
    fn count(&self) -> Result<usize>;
}

trait WriteRepository {
    fn create(&self, entity: &Entity) -> Result<Id>;
    fn update(&self, entity: &Entity) -> Result<()>;
    fn delete(&self, id: &Id) -> Result<()>;
}

trait BulkRepository {
    fn bulk_create(&self, entities: &[Entity]) -> Result<Vec<Id>>;
    fn bulk_delete(&self, ids: &[Id]) -> Result<usize>;
}

trait Searchable {
    fn search(&self, query: &SearchQuery) -> Result<SearchResults>;
    fn find_by_date_range(&self, start: DateTime, end: DateTime) -> Result<Vec<Entity>>;
}

trait Transactional {
    fn begin_transaction(&mut self) -> Result<Transaction>;
    fn commit(&mut self, tx: Transaction) -> Result<()>;
    fn rollback(&mut self, tx: Transaction) -> Result<()>;
}

trait Migratable {
    fn migrate(&self, version: u32) -> Result<()>;
    fn current_version(&self) -> Result<u32>;
}

trait HealthCheckable {
    fn health_check(&self) -> Result<bool>;
    fn connection_count(&self) -> usize;
}
```

Now each function declares exactly what it needs:

```rust
fn get_user(repo: &impl ReadRepository, id: &UserId) -> Result<User> {
    repo.read(id)?.ok_or_else(|| anyhow::anyhow!("user not found"))
}

fn create_user(repo: &impl WriteRepository, user: &User) -> Result<UserId> {
    repo.create(user)
}

fn migrate_database(db: &impl Migratable) -> Result<()> {
    let current = db.current_version()?;
    db.migrate(current + 1)
}
```

The `get_user` function can't accidentally delete data. The `migrate_database` function doesn't need a mock that implements CRUD. Each function gets the minimum capability it requires.

### Step 2: Compose traits with supertraits when needed

If some code genuinely needs multiple capabilities, use trait bounds:

```rust
fn transfer_entity(
    source: &impl ReadRepository,
    dest: &(impl WriteRepository + BulkRepository),
    id: &Id,
) -> Result<()> {
    let entity = source.read(id)?
        .ok_or_else(|| anyhow::anyhow!("entity not found"))?;
    dest.create(&entity)?;
    Ok(())
}

// Or define a composite trait if this combination is common
trait FullRepository: ReadRepository + WriteRepository + Searchable {}
impl<T: ReadRepository + WriteRepository + Searchable> FullRepository for T {}
```

The blanket implementation means any type that implements all three sub-traits automatically implements `FullRepository`. No extra code needed.

### Step 3: Use default methods for optional capabilities

Some methods have sensible defaults. Use them:

```rust
trait BulkRepository: WriteRepository {
    fn bulk_create(&self, entities: &[Entity]) -> Result<Vec<Id>> {
        // Default: just loop. Backends can override with optimized batch inserts.
        entities.iter().map(|e| self.create(e)).collect()
    }

    fn bulk_delete(&self, ids: &[Id]) -> Result<usize> {
        let mut count = 0;
        for id in ids {
            self.delete(id)?;
            count += 1;
        }
        Ok(count)
    }
}
```

Now implementing `BulkRepository` is free for any type that already implements `WriteRepository`. Backends that support efficient bulk operations can override the defaults, and everyone else gets a working implementation for free.

### Step 4: Use extension traits for convenience methods

Keep the core trait small and put convenience methods in an extension trait:

```rust
// Core trait: minimal, must implement
trait ReadRepository {
    fn read(&self, id: &Id) -> Result<Option<Entity>>;
}

// Extension trait: automatically implemented, never overridden
trait ReadRepositoryExt: ReadRepository {
    fn read_or_error(&self, id: &Id) -> Result<Entity> {
        self.read(id)?.ok_or_else(|| anyhow::anyhow!("not found: {id}"))
    }

    fn exists(&self, id: &Id) -> Result<bool> {
        Ok(self.read(id)?.is_some())
    }

    fn read_many(&self, ids: &[Id]) -> Result<Vec<Entity>> {
        ids.iter()
            .map(|id| self.read_or_error(id))
            .collect()
    }
}

// Blanket implementation
impl<T: ReadRepository> ReadRepositoryExt for T {}
```

This is the pattern used by `Iterator` in the standard library. The core trait has one required method (`next`), and the extension provides dozens of convenience methods (`map`, `filter`, `fold`, etc.) that are automatically available.

## How Small Should a Trait Be?

There's no magic number, but here are guidelines I use:

**1-3 methods:** Ideal. Focused, easy to implement, easy to mock. Think `Read`, `Write`, `Iterator`.

**4-6 methods:** Fine if they're truly cohesive — they all operate on the same abstraction and are typically used together.

**7+ methods:** Suspicious. Ask whether every implementor genuinely needs every method, or if you're bundling unrelated things.

**1 method:** Often the best. Single-method traits are incredibly composable. `Fn`, `Display`, `Drop`, `Clone` — some of Rust's most useful traits have exactly one method.

## The Standard Library Gets This Right

Look at how the standard library designs traits:

- `Read` has 2 required methods (`read`, plus `read_to_end` and others with defaults)
- `Write` has 2 required methods
- `Iterator` has 1 required method
- `Display` has 1 required method
- `FromStr` has 1 required method

These are tiny, focused, and composable. The standard library never bundles "read" and "write" into a single trait — they're separate, because some types are read-only, some are write-only, and composing `Read + Write` is trivial.

Follow their lead. Small traits compose into exactly the capability you need. Big traits force consumers to accept — and implementors to provide — more than they need. In Rust, where traits are the primary abstraction mechanism, getting their granularity right is one of the most impactful design decisions you'll make.
