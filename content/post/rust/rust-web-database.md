---
title: "Lesson 6: Database Integration — SQLx and connection management"
author: Atharva Pandey
series: "Rust Web & API Development"
lesson: 6
keywords: [Rust, rust, web, API, axum, SQLx, database, PostgreSQL, connection pool, migrations]
tags: [Rust tutorial, rust, web, api]
date: '2024-10-12T07:45:00.000Z'
---

My first Rust web service leaked database connections. I opened a new connection per request and forgot that Rust's ownership system doesn't magically manage TCP sockets. After about 200 concurrent users, PostgreSQL refused new connections and the whole service went down. Connection pooling isn't optional — it's the first thing you set up.

## Why SQLx

There are three main approaches to database access in Rust:

- **Diesel** — A full ORM with a query builder. Generates SQL at compile time. Requires a build step that connects to your database. Strong opinions about schema management.
- **SeaORM** — An async ORM inspired by ActiveRecord. Higher level, more magic. Good if you like ORMs.
- **SQLx** — Not an ORM. You write SQL. SQLx compiles your SQL queries against a real database at compile time, verifying that your SQL is valid and your result types match the columns returned.

I use SQLx because I like writing SQL and I don't trust ORMs in production. ORMs generate queries you can't see, and when they generate bad queries (and they will), debugging is miserable. With SQLx, the SQL is right there in your code, and the compiler verifies it's correct.

```toml
[dependencies]
sqlx = { version = "0.8", features = ["runtime-tokio", "tls-rustls", "postgres", "chrono", "uuid"] }
```

## Connection Pooling

SQLx's `PgPool` manages a pool of database connections internally. You create it once at startup and share it across all handlers via Axum's state.

```rust
use sqlx::postgres::PgPoolOptions;

#[tokio::main]
async fn main() {
    let database_url = std::env::var("DATABASE_URL")
        .expect("DATABASE_URL must be set");

    let pool = PgPoolOptions::new()
        .max_connections(20)
        .min_connections(5)
        .acquire_timeout(std::time::Duration::from_secs(5))
        .idle_timeout(std::time::Duration::from_secs(600))
        .max_lifetime(std::time::Duration::from_secs(1800))
        .connect(&database_url)
        .await
        .expect("Failed to create pool");

    let app = Router::new()
        .route("/api/users", get(list_users).post(create_user))
        .with_state(AppState { db: pool });

    // ...
}
```

Some numbers to think about:

- **max_connections**: Your PostgreSQL default is usually 100. If you run 5 replicas of your service, that's 20 connections each. Leave headroom for admin tools and migrations.
- **min_connections**: Keep some connections warm to avoid the latency of establishing new ones under sudden load.
- **acquire_timeout**: How long a handler waits for a free connection before giving up. Five seconds is generous — if your pool is exhausted for five seconds, something is very wrong.
- **idle_timeout**: Close idle connections after this duration. Prevents stale connections from accumulating.
- **max_lifetime**: Force-recycle connections after this duration, even if they're still working. This protects against server-side connection state corruption and helps with load balancer rotations.

## Migrations

SQLx has a built-in migration system. Create a `migrations` directory in your project root:

```bash
sqlx migrate add create_users
```

This creates a file like `migrations/20241012074500_create_users.sql`:

```sql
-- migrations/20241012074500_create_users.sql
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    password_hash TEXT NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'user',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_users_email ON users(email);
```

Run migrations at application startup:

```rust
sqlx::migrate!("./migrations")
    .run(&pool)
    .await
    .expect("Failed to run migrations");
```

Or from the command line:

```bash
sqlx migrate run --database-url postgres://user:pass@localhost/mydb
```

The `sqlx migrate!` macro embeds migration files into your binary. Your deployment artifact contains everything needed to set up the database — no separate migration scripts to track.

## Compile-Time Checked Queries

This is SQLx's killer feature. The `query!` and `query_as!` macros connect to your database *at compile time* and verify that:

1. Your SQL syntax is valid
2. Referenced tables and columns exist
3. Parameter types match
4. The returned columns match your Rust struct

```rust
use sqlx::FromRow;
use chrono::{DateTime, Utc};

#[derive(FromRow, Serialize)]
pub struct User {
    pub id: i64,
    pub email: String,
    pub name: String,
    #[serde(skip)]
    pub password_hash: String,
    pub role: String,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

async fn get_user(
    State(state): State<AppState>,
    Path(id): Path<i64>,
) -> Result<Json<User>, AppError> {
    let user = sqlx::query_as!(
        User,
        r#"SELECT id, email, name, password_hash, role,
           created_at, updated_at
           FROM users WHERE id = $1"#,
        id
    )
    .fetch_optional(&state.db)
    .await?
    .ok_or_else(|| AppError::not_found("User not found"))?;

    Ok(Json(user))
}
```

If you rename a column in a migration but forget to update the query, `cargo build` fails. If you pass an `i32` where the column expects `i64`, it fails. This catches an enormous class of bugs that other languages only find at runtime.

### Offline Mode

Compile-time checking requires a database connection during `cargo build`. That's fine for development but breaks CI builds. SQLx supports offline mode:

```bash
# Generate query metadata
cargo sqlx prepare

# This creates a .sqlx/ directory with JSON files describing each query
# Commit this directory to git
```

Then set the environment variable:

```bash
SQLX_OFFLINE=true cargo build
```

Now CI can build without a running database.

## CRUD Operations

### Create

```rust
async fn create_user(
    State(state): State<AppState>,
    ValidatedJson(input): ValidatedJson<CreateUserInput>,
) -> Result<(StatusCode, Json<User>), AppError> {
    let password_hash = hash_password(&input.password)
        .map_err(|_| AppError::internal("Password hashing failed"))?;

    let user = sqlx::query_as!(
        User,
        r#"INSERT INTO users (email, name, password_hash, role)
           VALUES ($1, $2, $3, 'user')
           RETURNING id, email, name, password_hash, role, created_at, updated_at"#,
        input.email,
        input.name,
        password_hash,
    )
    .fetch_one(&state.db)
    .await
    .map_err(|e| match e {
        sqlx::Error::Database(ref db_err) if db_err.is_unique_violation() => {
            AppError::conflict("A user with this email already exists")
        }
        _ => AppError::internal("Failed to create user"),
    })?;

    Ok((StatusCode::CREATED, Json(user)))
}
```

### Read (List with Pagination)

```rust
#[derive(Deserialize)]
struct ListParams {
    page: Option<i64>,
    per_page: Option<i64>,
}

#[derive(Serialize)]
struct PaginatedResponse<T: Serialize> {
    data: Vec<T>,
    total: i64,
    page: i64,
    per_page: i64,
}

async fn list_users(
    State(state): State<AppState>,
    Query(params): Query<ListParams>,
) -> Result<Json<PaginatedResponse<User>>, AppError> {
    let page = params.page.unwrap_or(1).max(1);
    let per_page = params.per_page.unwrap_or(20).clamp(1, 100);
    let offset = (page - 1) * per_page;

    let users = sqlx::query_as!(
        User,
        r#"SELECT id, email, name, password_hash, role, created_at, updated_at
           FROM users
           ORDER BY created_at DESC
           LIMIT $1 OFFSET $2"#,
        per_page,
        offset,
    )
    .fetch_all(&state.db)
    .await?;

    let total = sqlx::query_scalar!("SELECT COUNT(*) FROM users")
        .fetch_one(&state.db)
        .await?
        .unwrap_or(0);

    Ok(Json(PaginatedResponse {
        data: users,
        total,
        page,
        per_page,
    }))
}
```

### Update

```rust
#[derive(Deserialize)]
struct UpdateUserInput {
    name: Option<String>,
    email: Option<String>,
}

async fn update_user(
    State(state): State<AppState>,
    Path(id): Path<i64>,
    ValidatedJson(input): ValidatedJson<UpdateUserInput>,
) -> Result<Json<User>, AppError> {
    // Build update dynamically based on provided fields
    let user = sqlx::query_as!(
        User,
        r#"UPDATE users
           SET name = COALESCE($1, name),
               email = COALESCE($2, email),
               updated_at = NOW()
           WHERE id = $3
           RETURNING id, email, name, password_hash, role, created_at, updated_at"#,
        input.name,
        input.email,
        id,
    )
    .fetch_optional(&state.db)
    .await?
    .ok_or_else(|| AppError::not_found("User not found"))?;

    Ok(Json(user))
}
```

The `COALESCE($1, name)` pattern is a clean way to handle partial updates. If the client sends `null` for a field (or omits it, making it `None` in Rust), the existing value is preserved. No need to build dynamic SQL strings.

### Delete

```rust
async fn delete_user(
    State(state): State<AppState>,
    Path(id): Path<i64>,
) -> Result<StatusCode, AppError> {
    let result = sqlx::query!("DELETE FROM users WHERE id = $1", id)
        .execute(&state.db)
        .await?;

    if result.rows_affected() == 0 {
        return Err(AppError::not_found("User not found"));
    }

    Ok(StatusCode::NO_CONTENT)
}
```

## Transactions

When you need multiple operations to succeed or fail atomically:

```rust
async fn transfer_funds(
    State(state): State<AppState>,
    ValidatedJson(input): ValidatedJson<TransferInput>,
) -> Result<Json<serde_json::Value>, AppError> {
    let mut tx = state.db.begin().await?;

    // Debit sender
    let sender = sqlx::query!(
        "UPDATE accounts SET balance = balance - $1 WHERE id = $2 AND balance >= $1 RETURNING balance",
        input.amount,
        input.from_account_id,
    )
    .fetch_optional(&mut *tx)
    .await?
    .ok_or_else(|| AppError::bad_request("Insufficient funds or account not found"))?;

    // Credit receiver
    sqlx::query!(
        "UPDATE accounts SET balance = balance + $1 WHERE id = $2",
        input.amount,
        input.to_account_id,
    )
    .execute(&mut *tx)
    .await?;

    // Record the transfer
    sqlx::query!(
        "INSERT INTO transfers (from_account, to_account, amount) VALUES ($1, $2, $3)",
        input.from_account_id,
        input.to_account_id,
        input.amount,
    )
    .execute(&mut *tx)
    .await?;

    // Commit — if any step above failed, we already returned an error
    // and the transaction is rolled back when `tx` is dropped
    tx.commit().await?;

    Ok(Json(json!({ "status": "completed", "new_balance": sender.balance })))
}
```

If your function returns early (via `?` or an explicit error), the transaction is dropped without committing, which triggers an automatic rollback. Rust's ownership system makes this safe — no possibility of forgetting to rollback.

## Dynamic Queries with query_builder

Sometimes you need truly dynamic SQL — optional filters, variable sort orders. `query_as!` doesn't support that because the SQL must be a string literal. Use `QueryBuilder` instead:

```rust
use sqlx::QueryBuilder;

async fn search_users(
    State(state): State<AppState>,
    Query(params): Query<SearchParams>,
) -> Result<Json<Vec<User>>, AppError> {
    let mut builder = QueryBuilder::new(
        "SELECT id, email, name, password_hash, role, created_at, updated_at FROM users WHERE 1=1"
    );

    if let Some(ref name) = params.name {
        builder.push(" AND name ILIKE ");
        builder.push_bind(format!("%{}%", name));
    }

    if let Some(ref role) = params.role {
        builder.push(" AND role = ");
        builder.push_bind(role);
    }

    builder.push(" ORDER BY created_at DESC");
    builder.push(" LIMIT ");
    builder.push_bind(params.per_page.unwrap_or(20));
    builder.push(" OFFSET ");
    builder.push_bind((params.page.unwrap_or(1) - 1) * params.per_page.unwrap_or(20));

    let users = builder
        .build_query_as::<User>()
        .fetch_all(&state.db)
        .await?;

    Ok(Json(users))
}
```

`push_bind` uses parameterized queries — no SQL injection risk. Never use `format!()` to insert user input into SQL strings. Ever.

## Connection Health Checks

Your health endpoint should verify the database connection:

```rust
async fn health(State(state): State<AppState>) -> Result<Json<serde_json::Value>, AppError> {
    sqlx::query("SELECT 1")
        .execute(&state.db)
        .await
        .map_err(|_| AppError::internal("Database health check failed"))?;

    Ok(Json(json!({
        "status": "healthy",
        "database": "connected",
    })))
}
```

Load balancers and orchestrators use this to determine if your instance can serve traffic. If the database is unreachable, the health check fails, and traffic routes elsewhere.

That's the database layer. SQLx gives you raw SQL power with compile-time safety — the best of both worlds. Next lesson: pagination, filtering, and sorting patterns that actually scale.
