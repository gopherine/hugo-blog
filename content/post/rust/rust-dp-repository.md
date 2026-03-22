---
title: "Lesson 7: Repository Pattern — Abstracting storage"
author: Atharva Pandey
series: "Rust Design Patterns"
lesson: 7
keywords: [Rust, rust, design patterns, repository pattern, storage abstraction, database, testing, trait bounds]
tags: [Rust tutorial, rust, design-patterns, architecture]
date: '2025-10-13T13:45:00.000Z'
---

Every backend developer eventually writes the same code: a function that takes a database connection, runs a query, maps the rows to a struct, and returns it. Then you write another one. And another. Pretty soon your business logic is tangled up with SQL strings and connection pool handles, and testing anything requires a running database.

The Repository pattern fixes this. It's old — Martin Fowler wrote about it in 2002 — but the way Rust implements it is genuinely different from what you'd do in Java or C#. Rust's trait system, combined with generics and lifetimes, gives you a repository abstraction that's zero-cost in production and trivially mockable in tests.

## The Core Idea

A Repository is an abstraction over data storage. Your business logic talks to the repository trait. The concrete implementation talks to Postgres, SQLite, Redis, or an in-memory HashMap. The business logic doesn't know and doesn't care.

Let's build one. First, the domain types:

```rust
use std::time::SystemTime;

#[derive(Debug, Clone, PartialEq)]
pub struct Article {
    pub id: ArticleId,
    pub title: String,
    pub body: String,
    pub author_id: UserId,
    pub published: bool,
    pub created_at: SystemTime,
    pub updated_at: SystemTime,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct ArticleId(pub u64);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct UserId(pub u64);
```

Newtype IDs — `ArticleId` and `UserId` — prevent you from accidentally passing a user ID where an article ID is expected. Cheap trick, massive payoff.

## The Repository Trait

```rust
#[derive(Debug, Clone)]
pub struct ArticleFilter {
    pub author_id: Option<UserId>,
    pub published_only: bool,
    pub search_term: Option<String>,
}

impl Default for ArticleFilter {
    fn default() -> Self {
        Self {
            author_id: None,
            published_only: false,
            search_term: None,
        }
    }
}

pub trait ArticleRepository {
    type Error: std::fmt::Debug;

    fn find_by_id(&self, id: ArticleId) -> Result<Option<Article>, Self::Error>;
    fn find_all(&self, filter: &ArticleFilter) -> Result<Vec<Article>, Self::Error>;
    fn save(&mut self, article: &Article) -> Result<(), Self::Error>;
    fn delete(&mut self, id: ArticleId) -> Result<bool, Self::Error>;
    fn next_id(&self) -> Result<ArticleId, Self::Error>;
}
```

A few design choices worth noting:

**Associated type for errors** — `type Error`. Each backend has different error types. Postgres gives you `sqlx::Error`, an in-memory store gives you `Infallible`. The associated type lets each implementation use its natural error without forcing everything through a common error enum.

**`find_by_id` returns `Option`** — the absence of an entity isn't an error, it's a valid result. This is idiomatic Rust. Compare to Java repositories that throw `EntityNotFoundException` — that's control flow via exceptions, and it's terrible.

**`&mut self` for writes, `&self` for reads** — the borrow checker enforces read/write separation at the type level. You can't accidentally call `save` if you only have a shared reference.

## In-Memory Implementation (for Tests)

```rust
use std::collections::HashMap;

pub struct InMemoryArticleRepo {
    articles: HashMap<ArticleId, Article>,
    next_id: u64,
}

impl InMemoryArticleRepo {
    pub fn new() -> Self {
        Self {
            articles: HashMap::new(),
            next_id: 1,
        }
    }

    pub fn with_seed(articles: Vec<Article>) -> Self {
        let max_id = articles.iter().map(|a| a.id.0).max().unwrap_or(0);
        let map: HashMap<_, _> = articles.into_iter().map(|a| (a.id, a)).collect();
        Self {
            articles: map,
            next_id: max_id + 1,
        }
    }
}

impl ArticleRepository for InMemoryArticleRepo {
    type Error = std::convert::Infallible;

    fn find_by_id(&self, id: ArticleId) -> Result<Option<Article>, Self::Error> {
        Ok(self.articles.get(&id).cloned())
    }

    fn find_all(&self, filter: &ArticleFilter) -> Result<Vec<Article>, Self::Error> {
        let results: Vec<Article> = self
            .articles
            .values()
            .filter(|a| {
                if let Some(author_id) = filter.author_id {
                    if a.author_id != author_id {
                        return false;
                    }
                }
                if filter.published_only && !a.published {
                    return false;
                }
                if let Some(ref term) = filter.search_term {
                    let term_lower = term.to_lowercase();
                    if !a.title.to_lowercase().contains(&term_lower)
                        && !a.body.to_lowercase().contains(&term_lower)
                    {
                        return false;
                    }
                }
                true
            })
            .cloned()
            .collect();

        Ok(results)
    }

    fn save(&mut self, article: &Article) -> Result<(), Self::Error> {
        self.articles.insert(article.id, article.clone());
        Ok(())
    }

    fn delete(&mut self, id: ArticleId) -> Result<bool, Self::Error> {
        Ok(self.articles.remove(&id).is_some())
    }

    fn next_id(&self) -> Result<ArticleId, Self::Error> {
        Ok(ArticleId(self.next_id))
    }
}
```

This in-memory implementation is your testing workhorse. No database required, deterministic, fast. Seed it with test data via `with_seed`, run your business logic, assert the results.

## SQL Implementation (Production)

Here's what the real implementation looks like with `sqlx`:

```rust
use sqlx::PgPool;

pub struct PostgresArticleRepo {
    pool: PgPool,
}

impl PostgresArticleRepo {
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }
}

// For async repos, you'd use an async trait.
// Using async-trait crate or native async traits (Rust 1.75+)
impl ArticleRepository for PostgresArticleRepo {
    type Error = sqlx::Error;

    fn find_by_id(&self, id: ArticleId) -> Result<Option<Article>, Self::Error> {
        // In real code, this would be async.
        // Showing synchronous for pattern clarity.
        todo!("SELECT * FROM articles WHERE id = $1")
    }

    fn find_all(&self, filter: &ArticleFilter) -> Result<Vec<Article>, Self::Error> {
        todo!("Dynamic query building based on filter")
    }

    fn save(&mut self, article: &Article) -> Result<(), Self::Error> {
        todo!("INSERT ... ON CONFLICT DO UPDATE")
    }

    fn delete(&mut self, id: ArticleId) -> Result<bool, Self::Error> {
        todo!("DELETE FROM articles WHERE id = $1")
    }

    fn next_id(&self) -> Result<ArticleId, Self::Error> {
        todo!("SELECT nextval('articles_id_seq')")
    }
}
```

I'm using `todo!()` here because the SQL details aren't the point — the *shape* is the point. The Postgres implementation and the in-memory implementation have the same interface. Your service code works with either one.

## The Service Layer

Now your business logic is clean:

```rust
pub struct ArticleService<R: ArticleRepository> {
    repo: R,
}

impl<R: ArticleRepository> ArticleService<R> {
    pub fn new(repo: R) -> Self {
        Self { repo }
    }

    pub fn publish_article(
        &mut self,
        title: String,
        body: String,
        author_id: UserId,
    ) -> Result<Article, ServiceError<R::Error>> {
        if title.trim().is_empty() {
            return Err(ServiceError::Validation("title cannot be empty".into()));
        }
        if body.len() < 100 {
            return Err(ServiceError::Validation(
                "article body must be at least 100 characters".into(),
            ));
        }

        let id = self.repo.next_id().map_err(ServiceError::Storage)?;
        let now = SystemTime::now();

        let article = Article {
            id,
            title,
            body,
            author_id,
            published: true,
            created_at: now,
            updated_at: now,
        };

        self.repo.save(&article).map_err(ServiceError::Storage)?;
        Ok(article)
    }

    pub fn get_published_by_author(
        &self,
        author_id: UserId,
    ) -> Result<Vec<Article>, ServiceError<R::Error>> {
        let filter = ArticleFilter {
            author_id: Some(author_id),
            published_only: true,
            ..Default::default()
        };
        self.repo.find_all(&filter).map_err(ServiceError::Storage)
    }
}

#[derive(Debug)]
pub enum ServiceError<E: std::fmt::Debug> {
    Validation(String),
    Storage(E),
    NotFound,
}
```

Look at `ServiceError<R::Error>` — the storage error type flows through the generics. If `R` is `InMemoryArticleRepo`, the storage error is `Infallible` (it can never happen). If `R` is `PostgresArticleRepo`, it's `sqlx::Error`. The type system tracks this automatically.

## Testing Without a Database

This is the real payoff:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    fn test_service() -> ArticleService<InMemoryArticleRepo> {
        ArticleService::new(InMemoryArticleRepo::new())
    }

    #[test]
    fn publish_article_validates_title() {
        let mut service = test_service();
        let result = service.publish_article(
            "".into(),
            "a".repeat(200),
            UserId(1),
        );
        assert!(matches!(result, Err(ServiceError::Validation(_))));
    }

    #[test]
    fn publish_article_validates_body_length() {
        let mut service = test_service();
        let result = service.publish_article(
            "Good Title".into(),
            "too short".into(),
            UserId(1),
        );
        assert!(matches!(result, Err(ServiceError::Validation(_))));
    }

    #[test]
    fn publish_and_retrieve_articles() {
        let mut service = test_service();

        let article = service
            .publish_article(
                "Test Article".into(),
                "a".repeat(200),
                UserId(1),
            )
            .unwrap();

        assert!(article.published);

        let articles = service.get_published_by_author(UserId(1)).unwrap();
        assert_eq!(articles.len(), 1);
        assert_eq!(articles[0].title, "Test Article");

        // Different author — should return empty
        let articles = service.get_published_by_author(UserId(2)).unwrap();
        assert!(articles.is_empty());
    }
}
```

These tests run in milliseconds. No Docker. No database migrations. No test data cleanup. The in-memory repo is created fresh for each test. You're testing your *business logic*, not your database driver.

## Dynamic Dispatch Alternative

When you need to swap implementations at runtime — like reading a config flag — use trait objects:

```rust
pub struct DynamicArticleService {
    repo: Box<dyn ArticleRepository<Error = Box<dyn std::error::Error>>>,
}
```

But honestly? In most applications, you know at startup time which database you're using. Generics give you zero overhead and better type errors. Reserve `dyn` for plugin architectures or truly polymorphic use cases.

## The Async Question

Real-world repositories are almost always async. With Rust 1.75+ async traits, this is clean:

```rust
pub trait AsyncArticleRepository {
    type Error: std::fmt::Debug + Send;

    async fn find_by_id(&self, id: ArticleId) -> Result<Option<Article>, Self::Error>;
    async fn save(&self, article: &Article) -> Result<(), Self::Error>;
    // Note: &self, not &mut self — async contexts typically use
    // interior mutability (connection pool handles this)
}
```

Notice I switched `save` from `&mut self` to `&self`. In async code, you typically use a connection pool, which handles concurrent access internally. Requiring `&mut self` would mean you can't share the repository across tasks.

## Common Mistakes

**Don't leak storage details into the trait.** If your trait returns `sqlx::Row` or takes `&PgPool`, it's not an abstraction — it's a wrapper. The trait should speak in domain types only.

**Don't over-abstract.** If you have one database and no plans to change, a repository trait might be YAGNI. Use it when you need testability, when you support multiple backends, or when your domain logic is complex enough to justify the separation.

**Don't forget about transactions.** The basic repository trait doesn't handle transactions. For multi-entity operations, you need a Unit of Work pattern or transaction-scoped repositories. That's a whole separate design discussion, but the simplest approach is passing a transaction handle to methods that need it.

The Repository pattern in Rust isn't revolutionary — it's the same abstraction as in any other language. But Rust's generics make the abstraction truly zero-cost, and the type system catches interface violations at compile time instead of with integration test failures at 2 AM. That's worth the trait definition.
