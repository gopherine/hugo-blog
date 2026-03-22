---
title: "Lesson 6: The Repository Pattern in Rust — Abstracting persistence"
author: Atharva Pandey
series: "Rust Database Patterns"
lesson: 6
keywords: [Rust, rust, database, SQL, repository pattern, abstraction, postgres, trait, async]
tags: [Rust tutorial, rust, database, postgres]
date: '2024-10-30T16:40:00.000Z'
---

I once inherited a codebase where every HTTP handler had raw SQL queries inline — `sqlx::query!` calls scattered through 80+ route handlers. Changing a table name meant grep-and-replace across the entire project. Adding a cache layer meant touching every handler. Testing a handler meant spinning up a real database. It worked, technically, but nobody wanted to touch it.

The repository pattern fixes this. It puts a wall between your business logic and your database, and that wall pays for itself fast.

## What Is the Repository Pattern?

The idea is simple: define a trait that describes *what* you can do with your data, then implement it with *how* it actually talks to the database. Your business logic depends on the trait, not the implementation.

```
Business Logic → Repository Trait → PostgresRepository
                                  → InMemoryRepository (for tests)
                                  → CachedRepository (for production)
```

Your handlers don't know or care whether they're talking to Postgres, SQLite, or a HashMap in memory. They just call methods on the repository.

## The Basic Pattern

Start with your domain types — these should have zero database dependencies:

```rust
use chrono::NaiveDateTime;
use uuid::Uuid;
use serde::{Serialize, Deserialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct User {
    pub id: Uuid,
    pub username: String,
    pub email: String,
    pub bio: Option<String>,
    pub created_at: NaiveDateTime,
}

#[derive(Debug, Clone)]
pub struct CreateUser {
    pub username: String,
    pub email: String,
    pub bio: Option<String>,
}

#[derive(Debug, Clone)]
pub struct UpdateUser {
    pub username: Option<String>,
    pub email: Option<String>,
    pub bio: Option<Option<String>>,
}
```

Now define the repository trait:

```rust
use async_trait::async_trait;

#[derive(Debug, thiserror::Error)]
pub enum RepoError {
    #[error("not found")]
    NotFound,
    #[error("conflict: {0}")]
    Conflict(String),
    #[error("internal: {0}")]
    Internal(String),
}

#[async_trait]
pub trait UserRepository: Send + Sync {
    async fn find_by_id(&self, id: Uuid) -> Result<User, RepoError>;
    async fn find_by_username(&self, username: &str) -> Result<User, RepoError>;
    async fn find_all(&self, limit: i64, offset: i64) -> Result<Vec<User>, RepoError>;
    async fn create(&self, input: CreateUser) -> Result<User, RepoError>;
    async fn update(&self, id: Uuid, input: UpdateUser) -> Result<User, RepoError>;
    async fn delete(&self, id: Uuid) -> Result<(), RepoError>;
}
```

A few design decisions worth noting:

1. **Custom error type.** The trait returns `RepoError`, not `sqlx::Error` or `diesel::result::Error`. Your business logic shouldn't know what database library you're using.

2. **`Send + Sync` bound.** Required for using the trait across async tasks and in web frameworks like Axum that need `Send` futures.

3. **No database types leak through.** The `User` struct uses `Uuid` and `NaiveDateTime` from standard crates, not database-specific types.

## The Postgres Implementation

```rust
use sqlx::PgPool;

pub struct PgUserRepository {
    pool: PgPool,
}

impl PgUserRepository {
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }
}

#[async_trait]
impl UserRepository for PgUserRepository {
    async fn find_by_id(&self, id: Uuid) -> Result<User, RepoError> {
        sqlx::query_as!(
            User,
            "SELECT id, username, email, bio, created_at FROM users WHERE id = $1",
            id
        )
        .fetch_optional(&self.pool)
        .await
        .map_err(|e| RepoError::Internal(e.to_string()))?
        .ok_or(RepoError::NotFound)
    }

    async fn find_by_username(&self, username: &str) -> Result<User, RepoError> {
        sqlx::query_as!(
            User,
            "SELECT id, username, email, bio, created_at FROM users WHERE username = $1",
            username
        )
        .fetch_optional(&self.pool)
        .await
        .map_err(|e| RepoError::Internal(e.to_string()))?
        .ok_or(RepoError::NotFound)
    }

    async fn find_all(&self, limit: i64, offset: i64) -> Result<Vec<User>, RepoError> {
        sqlx::query_as!(
            User,
            "SELECT id, username, email, bio, created_at FROM users
             ORDER BY created_at DESC LIMIT $1 OFFSET $2",
            limit,
            offset
        )
        .fetch_all(&self.pool)
        .await
        .map_err(|e| RepoError::Internal(e.to_string()))
    }

    async fn create(&self, input: CreateUser) -> Result<User, RepoError> {
        sqlx::query_as!(
            User,
            "INSERT INTO users (username, email, bio) VALUES ($1, $2, $3)
             RETURNING id, username, email, bio, created_at",
            input.username,
            input.email,
            input.bio
        )
        .fetch_one(&self.pool)
        .await
        .map_err(|e| match e {
            sqlx::Error::Database(ref db_err) if db_err.code().as_deref() == Some("23505") => {
                RepoError::Conflict("username or email already exists".to_string())
            }
            other => RepoError::Internal(other.to_string()),
        })
    }

    async fn update(&self, id: Uuid, input: UpdateUser) -> Result<User, RepoError> {
        // Build the update dynamically based on which fields are Some
        let current = self.find_by_id(id).await?;

        let new_username = input.username.as_deref().unwrap_or(&current.username);
        let new_email = input.email.as_deref().unwrap_or(&current.email);
        let new_bio = match input.bio {
            Some(bio) => bio,
            None => current.bio.clone(),
        };

        sqlx::query_as!(
            User,
            "UPDATE users SET username = $1, email = $2, bio = $3
             WHERE id = $4
             RETURNING id, username, email, bio, created_at",
            new_username,
            new_email,
            new_bio.as_deref(),
            id
        )
        .fetch_one(&self.pool)
        .await
        .map_err(|e| RepoError::Internal(e.to_string()))
    }

    async fn delete(&self, id: Uuid) -> Result<(), RepoError> {
        let rows = sqlx::query!("DELETE FROM users WHERE id = $1", id)
            .execute(&self.pool)
            .await
            .map_err(|e| RepoError::Internal(e.to_string()))?
            .rows_affected();

        if rows == 0 {
            Err(RepoError::NotFound)
        } else {
            Ok(())
        }
    }
}
```

Notice how the `create` method translates Postgres error code `23505` (unique violation) into a domain-level `Conflict` error. The business logic never sees raw database error codes.

## The In-Memory Implementation (for Tests)

This is where the pattern really pays off:

```rust
use std::sync::Mutex;
use std::collections::HashMap;

pub struct InMemoryUserRepository {
    store: Mutex<HashMap<Uuid, User>>,
}

impl InMemoryUserRepository {
    pub fn new() -> Self {
        Self {
            store: Mutex::new(HashMap::new()),
        }
    }
}

#[async_trait]
impl UserRepository for InMemoryUserRepository {
    async fn find_by_id(&self, id: Uuid) -> Result<User, RepoError> {
        let store = self.store.lock().unwrap();
        store.get(&id).cloned().ok_or(RepoError::NotFound)
    }

    async fn find_by_username(&self, username: &str) -> Result<User, RepoError> {
        let store = self.store.lock().unwrap();
        store
            .values()
            .find(|u| u.username == username)
            .cloned()
            .ok_or(RepoError::NotFound)
    }

    async fn find_all(&self, limit: i64, offset: i64) -> Result<Vec<User>, RepoError> {
        let store = self.store.lock().unwrap();
        let mut users: Vec<_> = store.values().cloned().collect();
        users.sort_by(|a, b| b.created_at.cmp(&a.created_at));
        Ok(users
            .into_iter()
            .skip(offset as usize)
            .take(limit as usize)
            .collect())
    }

    async fn create(&self, input: CreateUser) -> Result<User, RepoError> {
        let mut store = self.store.lock().unwrap();

        // Check for conflicts
        if store.values().any(|u| u.username == input.username || u.email == input.email) {
            return Err(RepoError::Conflict("username or email already exists".to_string()));
        }

        let user = User {
            id: Uuid::new_v4(),
            username: input.username,
            email: input.email,
            bio: input.bio,
            created_at: chrono::Utc::now().naive_utc(),
        };

        store.insert(user.id, user.clone());
        Ok(user)
    }

    async fn update(&self, id: Uuid, input: UpdateUser) -> Result<User, RepoError> {
        let mut store = self.store.lock().unwrap();
        let user = store.get_mut(&id).ok_or(RepoError::NotFound)?;

        if let Some(username) = input.username {
            user.username = username;
        }
        if let Some(email) = input.email {
            user.email = email;
        }
        if let Some(bio) = input.bio {
            user.bio = bio;
        }

        Ok(user.clone())
    }

    async fn delete(&self, id: Uuid) -> Result<(), RepoError> {
        let mut store = self.store.lock().unwrap();
        store.remove(&id).ok_or(RepoError::NotFound)?;
        Ok(())
    }
}
```

Now your unit tests can run without a database, without Docker, without any external dependencies. They're fast, deterministic, and isolated.

## Wiring It Into a Web Server

Here's how you use the repository with Axum:

```rust
use axum::{
    extract::{Path, State, Query},
    routing::{get, post, put, delete},
    Router, Json,
};
use std::sync::Arc;
use serde::Deserialize;

type DynUserRepo = Arc<dyn UserRepository>;

#[derive(Clone)]
struct AppState {
    users: DynUserRepo,
}

async fn list_users(
    State(state): State<AppState>,
    Query(params): Query<PaginationParams>,
) -> Result<Json<Vec<User>>, AppError> {
    let users = state.users.find_all(params.limit(), params.offset()).await?;
    Ok(Json(users))
}

async fn get_user(
    State(state): State<AppState>,
    Path(id): Path<Uuid>,
) -> Result<Json<User>, AppError> {
    let user = state.users.find_by_id(id).await?;
    Ok(Json(user))
}

async fn create_user(
    State(state): State<AppState>,
    Json(input): Json<CreateUser>,
) -> Result<Json<User>, AppError> {
    let user = state.users.create(input).await?;
    Ok(Json(user))
}

#[derive(Deserialize)]
struct PaginationParams {
    page: Option<i64>,
    per_page: Option<i64>,
}

impl PaginationParams {
    fn limit(&self) -> i64 { self.per_page.unwrap_or(20).min(100) }
    fn offset(&self) -> i64 { (self.page.unwrap_or(1) - 1) * self.limit() }
}

// Map RepoError to HTTP responses
struct AppError(RepoError);

impl From<RepoError> for AppError {
    fn from(e: RepoError) -> Self { AppError(e) }
}

impl axum::response::IntoResponse for AppError {
    fn into_response(self) -> axum::response::Response {
        let (status, msg) = match self.0 {
            RepoError::NotFound => (axum::http::StatusCode::NOT_FOUND, "Not found"),
            RepoError::Conflict(_) => (axum::http::StatusCode::CONFLICT, "Conflict"),
            RepoError::Internal(_) => (
                axum::http::StatusCode::INTERNAL_SERVER_ERROR,
                "Internal error",
            ),
        };
        (status, msg).into_response()
    }
}

// Production setup
async fn production_app(pool: PgPool) -> Router {
    let state = AppState {
        users: Arc::new(PgUserRepository::new(pool)),
    };

    Router::new()
        .route("/users", get(list_users).post(create_user))
        .route("/users/:id", get(get_user))
        .with_state(state)
}

// Test setup — no database needed
#[cfg(test)]
fn test_app() -> Router {
    let state = AppState {
        users: Arc::new(InMemoryUserRepository::new()),
    };

    Router::new()
        .route("/users", get(list_users).post(create_user))
        .route("/users/:id", get(get_user))
        .with_state(state)
}
```

Same handlers, same routes, different backing store. The handlers are completely agnostic about where the data comes from.

## Adding a Cache Layer

The repository pattern makes it trivial to add caching without touching business logic:

```rust
use std::sync::Arc;
use tokio::sync::RwLock;
use std::collections::HashMap;
use std::time::{Duration, Instant};

struct CachedUserRepository {
    inner: Arc<dyn UserRepository>,
    cache: RwLock<HashMap<Uuid, (User, Instant)>>,
    ttl: Duration,
}

impl CachedUserRepository {
    pub fn new(inner: Arc<dyn UserRepository>, ttl: Duration) -> Self {
        Self {
            inner,
            cache: RwLock::new(HashMap::new()),
            ttl,
        }
    }
}

#[async_trait]
impl UserRepository for CachedUserRepository {
    async fn find_by_id(&self, id: Uuid) -> Result<User, RepoError> {
        // Check cache first
        {
            let cache = self.cache.read().await;
            if let Some((user, inserted_at)) = cache.get(&id) {
                if inserted_at.elapsed() < self.ttl {
                    return Ok(user.clone());
                }
            }
        }

        // Cache miss — fetch from database
        let user = self.inner.find_by_id(id).await?;

        // Update cache
        {
            let mut cache = self.cache.write().await;
            cache.insert(id, (user.clone(), Instant::now()));
        }

        Ok(user)
    }

    async fn create(&self, input: CreateUser) -> Result<User, RepoError> {
        let user = self.inner.create(input).await?;
        // Insert into cache immediately
        let mut cache = self.cache.write().await;
        cache.insert(user.id, (user.clone(), Instant::now()));
        Ok(user)
    }

    async fn delete(&self, id: Uuid) -> Result<(), RepoError> {
        self.inner.delete(id).await?;
        // Invalidate cache
        let mut cache = self.cache.write().await;
        cache.remove(&id);
        Ok(())
    }

    // ... delegate other methods to self.inner
    async fn find_by_username(&self, username: &str) -> Result<User, RepoError> {
        self.inner.find_by_username(username).await
    }

    async fn find_all(&self, limit: i64, offset: i64) -> Result<Vec<User>, RepoError> {
        self.inner.find_all(limit, offset).await
    }

    async fn update(&self, id: Uuid, input: UpdateUser) -> Result<User, RepoError> {
        let user = self.inner.update(id, input).await?;
        let mut cache = self.cache.write().await;
        cache.insert(id, (user.clone(), Instant::now()));
        Ok(user)
    }
}
```

Now you can stack it:

```rust
let pg_repo = Arc::new(PgUserRepository::new(pool));
let cached_repo = Arc::new(CachedUserRepository::new(pg_repo, Duration::from_secs(60)));

let state = AppState { users: cached_repo };
```

Zero changes to any handler. The cache sits transparently between your business logic and the database.

## When Not to Use This Pattern

I want to be honest — the repository pattern adds indirection, and indirection has costs:

- **Simple CRUD apps** with 3-5 endpoints don't need this. Just call SQLx directly from your handlers.
- **If you'll never swap databases**, the "abstract over the backend" benefit is theoretical.
- **If you'll never write unit tests** (no judgment, some projects don't), the in-memory implementation has no consumer.

The pattern earns its keep when your project is large enough that database concerns would otherwise leak everywhere, or when you need testability without spinning up a real database for every test run.

My threshold: if you have more than 10 handlers touching the same data, use a repository.

## What's Next

The repository pattern gives you clean interfaces, but sometimes you need to build queries dynamically — user-driven search, multi-column filtering, optional sort orders. Lesson 7 covers building type-safe query builders in Rust that make invalid queries unrepresentable.
