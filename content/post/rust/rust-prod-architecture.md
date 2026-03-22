---
title: "Lesson 1: Structuring a Large Rust Application — Beyond hello world"
author: Atharva Pandey
series: "Rust in Production — Real-World Architecture"
lesson: 1
keywords: [Rust, rust, architecture, production, project structure, modules, organization]
tags: [Rust tutorial, rust, architecture, production]
date: '2025-10-15T09:14:00.000Z'
---

The moment I knew our Rust project structure was broken was when a junior engineer asked me where to put a new endpoint. I opened the repo, stared at the `src/` directory, and realized I couldn't confidently answer. We had 40,000 lines of Rust spread across files with names like `utils.rs`, `helpers.rs`, `types.rs`, and the ever-popular `misc.rs`. Everything compiled. Nothing made sense.

Most Rust tutorials stop at "put your code in `main.rs` and maybe `lib.rs`." That works for a CLI tool or a weekend project. It completely falls apart when you've got a team of eight engineers building a platform with multiple services, shared domain logic, and infrastructure that's evolving every sprint.

Let me show you what I've settled on after restructuring three production Rust codebases.

## The Flat File Trap

Here's what most Rust projects look like after six months of organic growth:

```
src/
├── main.rs
├── config.rs
├── db.rs
├── handlers.rs      # 2000 lines, still growing
├── models.rs        # every struct you've ever written
├── errors.rs
├── utils.rs         # the junk drawer
├── middleware.rs
├── auth.rs
└── types.rs         # what's the difference between this and models.rs?
```

This is the "everything is a sibling" layout. It's seductive because it's simple — every file is one `use crate::` away from every other file. But simplicity here is a trap. When `handlers.rs` imports from `db.rs` which imports from `models.rs` which imports from `utils.rs` which imports from `handlers.rs`... you don't have architecture. You have spaghetti with a Rust-flavored sauce.

## What Actually Works: Domain-Driven Modules

The structure I keep coming back to organizes code by what it *does*, not what it *is*:

```
src/
├── main.rs
├── lib.rs
├── config/
│   ├── mod.rs
│   ├── app.rs
│   └── database.rs
├── domain/
│   ├── mod.rs
│   ├── user/
│   │   ├── mod.rs
│   │   ├── entity.rs
│   │   ├── repository.rs   # trait definition
│   │   ├── service.rs
│   │   └── error.rs
│   └── order/
│       ├── mod.rs
│       ├── entity.rs
│       ├── repository.rs
│       ├── service.rs
│       └── error.rs
├── infra/
│   ├── mod.rs
│   ├── postgres/
│   │   ├── mod.rs
│   │   ├── user_repo.rs    # implements domain::user::repository
│   │   └── order_repo.rs
│   └── redis/
│       ├── mod.rs
│       └── cache.rs
├── api/
│   ├── mod.rs
│   ├── routes.rs
│   ├── middleware/
│   │   ├── mod.rs
│   │   ├── auth.rs
│   │   └── logging.rs
│   ├── handlers/
│   │   ├── mod.rs
│   │   ├── user.rs
│   │   └── order.rs
│   └── dto/
│       ├── mod.rs
│       ├── user.rs
│       └── order.rs
└── startup.rs
```

The key insight: **your domain module should have zero dependencies on your infrastructure.** The `domain::user::repository` module defines a *trait*. The `infra::postgres::user_repo` module *implements* that trait. The dependency arrow points inward.

## Building the Domain Layer

Let's make this concrete. Here's how I set up a domain entity that actually holds up in production:

```rust
// src/domain/user/entity.rs

use chrono::{DateTime, Utc};
use uuid::Uuid;

#[derive(Debug, Clone)]
pub struct User {
    id: UserId,
    email: Email,
    display_name: String,
    role: Role,
    created_at: DateTime<Utc>,
    updated_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct UserId(Uuid);

impl UserId {
    pub fn new() -> Self {
        Self(Uuid::new_v4())
    }

    pub fn from_uuid(id: Uuid) -> Self {
        Self(id)
    }

    pub fn as_uuid(&self) -> &Uuid {
        &self.0
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Email(String);

impl Email {
    pub fn parse(raw: &str) -> Result<Self, DomainError> {
        let trimmed = raw.trim().to_lowercase();
        if trimmed.contains('@') && trimmed.len() > 3 {
            Ok(Self(trimmed))
        } else {
            Err(DomainError::InvalidEmail(raw.to_string()))
        }
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Role {
    User,
    Admin,
    ServiceAccount,
}

use super::error::DomainError;

impl User {
    pub fn new(email: Email, display_name: String) -> Self {
        let now = Utc::now();
        Self {
            id: UserId::new(),
            email,
            display_name,
            role: Role::User,
            created_at: now,
            updated_at: now,
        }
    }

    pub fn promote_to_admin(&mut self) -> Result<(), DomainError> {
        if self.role == Role::ServiceAccount {
            return Err(DomainError::CannotPromoteServiceAccount);
        }
        self.role = Role::Admin;
        self.updated_at = Utc::now();
        Ok(())
    }

    pub fn id(&self) -> UserId { self.id }
    pub fn email(&self) -> &Email { &self.email }
    pub fn role(&self) -> Role { self.role }
}
```

Notice what's missing? No `#[derive(Serialize, Deserialize)]`. No `#[sqlx::FromRow]`. No `#[derive(ToSchema)]`. The domain entity knows nothing about JSON, databases, or OpenAPI. That's deliberate. The moment you slap `serde` on your domain types, you've coupled your business logic to your serialization format. When you need to change how a field is serialized for the API without changing the domain — and you *will* — you're stuck.

## The Repository Trait Pattern

The domain defines what it needs. Infrastructure provides it:

```rust
// src/domain/user/repository.rs

use super::entity::{User, UserId, Email};
use super::error::DomainError;
use async_trait::async_trait;

#[async_trait]
pub trait UserRepository: Send + Sync {
    async fn find_by_id(&self, id: UserId) -> Result<Option<User>, DomainError>;
    async fn find_by_email(&self, email: &Email) -> Result<Option<User>, DomainError>;
    async fn save(&self, user: &User) -> Result<(), DomainError>;
    async fn delete(&self, id: UserId) -> Result<(), DomainError>;
    async fn list(&self, offset: i64, limit: i64) -> Result<Vec<User>, DomainError>;
}
```

And the Postgres implementation lives in `infra/`:

```rust
// src/infra/postgres/user_repo.rs

use crate::domain::user::entity::{User, UserId, Email, Role};
use crate::domain::user::repository::UserRepository;
use crate::domain::user::error::DomainError;
use async_trait::async_trait;
use sqlx::PgPool;

pub struct PgUserRepository {
    pool: PgPool,
}

impl PgUserRepository {
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }
}

#[derive(sqlx::FromRow)]
struct UserRow {
    id: uuid::Uuid,
    email: String,
    display_name: String,
    role: String,
    created_at: chrono::DateTime<chrono::Utc>,
    updated_at: chrono::DateTime<chrono::Utc>,
}

impl TryFrom<UserRow> for User {
    type Error = DomainError;

    fn try_from(row: UserRow) -> Result<Self, Self::Error> {
        let email = Email::parse(&row.email)?;
        let role = match row.role.as_str() {
            "user" => Role::User,
            "admin" => Role::Admin,
            "service_account" => Role::ServiceAccount,
            other => return Err(DomainError::InvalidRole(other.to_string())),
        };

        Ok(User::reconstitute(
            UserId::from_uuid(row.id),
            email,
            row.display_name,
            role,
            row.created_at,
            row.updated_at,
        ))
    }
}

#[async_trait]
impl UserRepository for PgUserRepository {
    async fn find_by_id(&self, id: UserId) -> Result<Option<User>, DomainError> {
        let row = sqlx::query_as::<_, UserRow>(
            "SELECT id, email, display_name, role, created_at, updated_at
             FROM users WHERE id = $1"
        )
        .bind(id.as_uuid())
        .fetch_optional(&self.pool)
        .await
        .map_err(|e| DomainError::Infrastructure(e.to_string()))?;

        row.map(User::try_from).transpose()
    }

    async fn save(&self, user: &User) -> Result<(), DomainError> {
        let role_str = match user.role() {
            Role::User => "user",
            Role::Admin => "admin",
            Role::ServiceAccount => "service_account",
        };

        sqlx::query(
            "INSERT INTO users (id, email, display_name, role, created_at, updated_at)
             VALUES ($1, $2, $3, $4, $5, $6)
             ON CONFLICT (id) DO UPDATE SET
                email = $2, display_name = $3, role = $4, updated_at = $6"
        )
        .bind(user.id().as_uuid())
        .bind(user.email().as_str())
        .bind(&user.display_name())
        .bind(role_str)
        .bind(user.created_at())
        .bind(user.updated_at())
        .execute(&self.pool)
        .await
        .map_err(|e| DomainError::Infrastructure(e.to_string()))?;

        Ok(())
    }

    // ... other methods follow the same pattern
    async fn find_by_email(&self, email: &Email) -> Result<Option<User>, DomainError> {
        let row = sqlx::query_as::<_, UserRow>(
            "SELECT id, email, display_name, role, created_at, updated_at
             FROM users WHERE email = $1"
        )
        .bind(email.as_str())
        .fetch_optional(&self.pool)
        .await
        .map_err(|e| DomainError::Infrastructure(e.to_string()))?;

        row.map(User::try_from).transpose()
    }

    async fn delete(&self, id: UserId) -> Result<(), DomainError> {
        sqlx::query("DELETE FROM users WHERE id = $1")
            .bind(id.as_uuid())
            .execute(&self.pool)
            .await
            .map_err(|e| DomainError::Infrastructure(e.to_string()))?;
        Ok(())
    }

    async fn list(&self, offset: i64, limit: i64) -> Result<Vec<User>, DomainError> {
        let rows = sqlx::query_as::<_, UserRow>(
            "SELECT id, email, display_name, role, created_at, updated_at
             FROM users ORDER BY created_at DESC LIMIT $1 OFFSET $2"
        )
        .bind(limit)
        .bind(offset)
        .fetch_all(&self.pool)
        .await
        .map_err(|e| DomainError::Infrastructure(e.to_string()))?;

        rows.into_iter().map(User::try_from).collect()
    }
}
```

The `UserRow` struct has `#[derive(sqlx::FromRow)]` — that's fine. It lives in the infrastructure layer. The domain stays clean.

## Wiring It Up: The Startup Module

The part most tutorials skip is how everything connects. I use a dedicated startup module:

```rust
// src/startup.rs

use crate::config::AppConfig;
use crate::domain::user::repository::UserRepository;
use crate::domain::user::service::UserService;
use crate::infra::postgres::user_repo::PgUserRepository;
use sqlx::postgres::PgPoolOptions;
use std::sync::Arc;

pub struct AppState {
    pub user_service: UserService<PgUserRepository>,
    // add more services as you grow
}

pub async fn build_app(config: &AppConfig) -> Result<AppState, anyhow::Error> {
    let pool = PgPoolOptions::new()
        .max_connections(config.database.max_connections)
        .connect(&config.database.url)
        .await?;

    sqlx::migrate!("./migrations").run(&pool).await?;

    let user_repo = PgUserRepository::new(pool.clone());
    let user_service = UserService::new(user_repo);

    Ok(AppState { user_service })
}
```

And the service is generic over the repository trait — which means testing is trivial:

```rust
// src/domain/user/service.rs

use super::entity::{User, UserId, Email};
use super::error::DomainError;
use super::repository::UserRepository;

pub struct UserService<R: UserRepository> {
    repo: R,
}

impl<R: UserRepository> UserService<R> {
    pub fn new(repo: R) -> Self {
        Self { repo }
    }

    pub async fn register_user(
        &self,
        email_raw: &str,
        display_name: String,
    ) -> Result<User, DomainError> {
        let email = Email::parse(email_raw)?;

        if self.repo.find_by_email(&email).await?.is_some() {
            return Err(DomainError::EmailAlreadyExists(email_raw.to_string()));
        }

        let user = User::new(email, display_name);
        self.repo.save(&user).await?;
        Ok(user)
    }

    pub async fn promote_user(&self, id: UserId) -> Result<User, DomainError> {
        let mut user = self.repo.find_by_id(id).await?
            .ok_or(DomainError::UserNotFound(id))?;
        user.promote_to_admin()?;
        self.repo.save(&user).await?;
        Ok(user)
    }
}
```

## The Module Visibility Strategy

Here's a thing I wish someone had told me earlier: Rust's module system is your architecture enforcement tool. Use it.

```rust
// src/domain/mod.rs
pub mod user;
pub mod order;

// src/domain/user/mod.rs
pub mod entity;
pub mod error;
pub mod service;
pub mod repository;

// Entity fields are private — you can only construct via methods
// Repository is a trait — you can't accidentally use Postgres directly
// Service is the public API for the domain
```

If a handler tries to directly instantiate a `User` by filling in struct fields, the compiler stops them. If someone tries to import `PgUserRepository` in the domain layer, they have to explicitly reach into `crate::infra::` — which is a code review red flag you can grep for.

I've seen teams add a `#[deny(clippy::wildcard_imports)]` lint and a CI check that greps for `use crate::infra` inside the `domain/` directory. Five minutes of setup, prevents an entire category of architectural drift.

## What About `main.rs`?

Keep it tiny. Seriously. My `main.rs` files are usually under 30 lines:

```rust
// src/main.rs

use myapp::config::AppConfig;
use myapp::startup::build_app;
use myapp::api::routes::create_router;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::init();

    let config = AppConfig::load()?;
    let state = build_app(&config).await?;
    let router = create_router(state);

    let listener = tokio::net::TcpListener::bind(&config.server.address).await?;
    tracing::info!("listening on {}", config.server.address);
    axum::serve(listener, router).await?;

    Ok(())
}
```

Everything else lives in `lib.rs` and its children. This matters because `lib.rs` is independently testable — you can write integration tests in `tests/` that import your library crate without going through `main`.

## The Rules I Follow

After three restructurings, here's what I've landed on:

1. **No file over 400 lines.** If a file is growing past this, it's doing too much. Split it.
2. **Domain knows nothing about infrastructure.** Ever. If you need a database concept in the domain, abstract it behind a trait.
3. **DTOs are separate from entities.** Your API response shape is not your domain shape. They evolve independently.
4. **One `mod.rs` per directory.** It re-exports what's public. Everything else is an implementation detail.
5. **Errors are domain-specific.** Each domain module has its own error type. A global `AppError` exists only in the API layer for HTTP response mapping.

None of this is revolutionary. It's just discipline. The Rust compiler will keep your code correct — but it won't keep your code *organized*. That's on you.

In the next lesson, we'll dig into domain modeling with Rust's type system — making illegal states unrepresentable so that entire categories of bugs simply can't exist.
