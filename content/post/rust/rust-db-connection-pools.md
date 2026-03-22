---
title: "Lesson 3: Connection Pooling with deadpool and bb8 — Managing database connections"
author: Atharva Pandey
series: "Rust Database Patterns"
lesson: 3
keywords: [Rust, rust, database, SQL, connection pool, deadpool, bb8, postgres, async]
tags: [Rust tutorial, rust, database, postgres]
date: '2024-10-24T14:35:00.000Z'
---

I once watched a Rust service fall over under modest load — maybe 200 concurrent requests — because every request opened a new Postgres connection, used it for one query, and dropped it. The database was spending more time on TLS handshakes and connection setup than on actual queries. CPU was fine, memory was fine, but `pg_stat_activity` showed 200+ connections churning constantly. The fix took ten lines of code: add a connection pool.

## Why Connections Are Expensive

A database connection isn't a simple network socket. When you connect to Postgres, here's what happens:

1. TCP handshake (1 round trip)
2. TLS negotiation (2-3 round trips if using SSL)
3. Authentication (1-2 round trips)
4. Backend process fork (Postgres forks a process per connection)
5. Session setup (timezone, encoding, search_path)

That's 5-7 network round trips before you execute a single query. On a cloud deployment where your app and database are in different availability zones, each round trip might be 1-2ms. So you're burning 10-15ms just to connect — and if your actual query takes 5ms, you're spending three times more on connection overhead than on work.

A connection pool solves this by maintaining a set of pre-established connections. When your code needs a database connection, it borrows one from the pool, uses it, and returns it. No setup overhead. The connection is already authenticated, already has TLS established, already has a Postgres backend process assigned.

## The Two Main Options: deadpool and bb8

Rust has two mature async connection pool crates. They solve the same problem with slightly different philosophies.

**deadpool** — Simpler API, less configuration, good defaults. My default choice.

**bb8** — More configurable, closer to Java's HikariCP in design, handles edge cases that deadpool doesn't.

Both work. I'll show you both, and you can pick whichever fits your project.

## deadpool with SQLx

```toml
[dependencies]
sqlx = { version = "0.8", features = ["runtime-tokio", "postgres", "macros"] }
tokio = { version = "1", features = ["full"] }
```

SQLx actually bundles its own connection pool, so for SQLx projects you don't even need a separate pooling crate:

```rust
use sqlx::postgres::PgPoolOptions;
use std::time::Duration;

#[tokio::main]
async fn main() -> Result<(), sqlx::Error> {
    let pool = PgPoolOptions::new()
        .max_connections(20)
        .min_connections(5)
        .acquire_timeout(Duration::from_secs(3))
        .idle_timeout(Duration::from_secs(600))
        .max_lifetime(Duration::from_secs(1800))
        .connect("postgres://localhost/myapp")
        .await?;

    // Use the pool directly — connections are borrowed and returned automatically
    let row = sqlx::query!("SELECT COUNT(*) as count FROM users")
        .fetch_one(&pool)
        .await?;

    println!("User count: {}", row.count.unwrap_or(0));

    Ok(())
}
```

Those configuration values matter. Here's what each one does and how to think about sizing them:

- **`max_connections(20)`** — Upper bound. Set this based on your Postgres `max_connections` divided by the number of application instances. If Postgres allows 100 connections and you run 5 instances, that's 20 per instance.
- **`min_connections(5)`** — Pre-warmed connections. These are created at startup so the first requests don't pay the connection cost.
- **`acquire_timeout(3s)`** — How long to wait for a connection before giving up. If all connections are busy, new requests queue here. Too long and your request latency spikes. Too short and you get errors under load.
- **`idle_timeout(600s)`** — Close connections that haven't been used in 10 minutes. Prevents stale connections from accumulating.
- **`max_lifetime(1800s)`** — Force-close connections after 30 minutes regardless of activity. This prevents issues with long-lived connections that accumulate server-side state or hit Postgres memory leaks.

## deadpool with Diesel

If you're using Diesel with async support, deadpool has a dedicated integration:

```toml
[dependencies]
diesel = { version = "2.2", features = ["postgres"] }
diesel-async = { version = "0.5", features = ["deadpool", "postgres"] }
tokio = { version = "1", features = ["full"] }
```

```rust
use diesel_async::pooled_connection::deadpool::Pool;
use diesel_async::pooled_connection::AsyncDieselConnectionManager;
use diesel_async::{AsyncPgConnection, RunQueryDsl};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let config = AsyncDieselConnectionManager::<AsyncPgConnection>::new(
        "postgres://localhost/myapp"
    );

    let pool = Pool::builder(config)
        .max_size(20)
        .build()?;

    // Borrow a connection from the pool
    let mut conn = pool.get().await?;

    // Use it like a normal Diesel connection
    let results = users::table
        .limit(10)
        .load::<User>(&mut conn)
        .await?;

    // Connection is returned to the pool when `conn` is dropped
    Ok(())
}
```

The key thing to notice: `conn` is returned to the pool automatically when it goes out of scope. Rust's ownership system handles this perfectly — no `try/finally` blocks, no `defer`, no forgetting to release the connection.

## bb8 — The Configurable Option

bb8 gives you more knobs to turn:

```toml
[dependencies]
bb8 = "0.8"
bb8-postgres = "0.8"
tokio-postgres = "0.7"
tokio = { version = "1", features = ["full"] }
```

```rust
use bb8::Pool;
use bb8_postgres::PostgresConnectionManager;
use tokio_postgres::NoTls;
use std::time::Duration;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let manager = PostgresConnectionManager::new_from_stringlike(
        "host=localhost dbname=myapp",
        NoTls,
    )?;

    let pool = Pool::builder()
        .max_size(20)
        .min_idle(Some(5))
        .max_lifetime(Some(Duration::from_secs(1800)))
        .idle_timeout(Some(Duration::from_secs(600)))
        .connection_timeout(Duration::from_secs(3))
        .build(manager)
        .await?;

    let conn = pool.get().await?;

    let rows = conn
        .query("SELECT id, username FROM users LIMIT 10", &[])
        .await?;

    for row in rows {
        let id: uuid::Uuid = row.get(0);
        let username: String = row.get(1);
        println!("{}: {}", id, username);
    }

    Ok(())
}
```

bb8's distinguishing feature is `min_idle` — it maintains a minimum number of idle connections. If connections get checked out and the idle count drops below this threshold, bb8 proactively creates new ones in the background. This prevents latency spikes when you get a burst of traffic.

## Health Checks and Connection Validation

Connections go stale. Postgres might restart, a network blip might kill a TCP connection, or a firewall might silently close an idle connection. A good pool validates connections before handing them out.

With SQLx's built-in pool, this happens automatically — SQLx pings the connection before returning it from the pool.

With bb8, you can customize the health check:

```rust
use bb8::ManageConnection;
use async_trait::async_trait;

struct CustomManager {
    inner: PostgresConnectionManager<NoTls>,
}

#[async_trait]
impl ManageConnection for CustomManager {
    type Connection = tokio_postgres::Client;
    type Error = tokio_postgres::Error;

    async fn connect(&self) -> Result<Self::Connection, Self::Error> {
        self.inner.connect().await
    }

    async fn is_valid(&self, conn: &mut Self::Connection) -> Result<(), Self::Error> {
        conn.simple_query("SELECT 1").await.map(|_| ())
    }

    fn has_broken(&self, conn: &mut Self::Connection) -> bool {
        conn.is_closed()
    }
}
```

## Connection Pool in a Web Server

Here's a realistic example — integrating a pool with an Axum web server:

```rust
use axum::{
    extract::State,
    routing::get,
    Router,
    Json,
};
use sqlx::postgres::PgPoolOptions;
use sqlx::PgPool;
use std::time::Duration;

#[derive(Clone)]
struct AppState {
    db: PgPool,
}

async fn list_users(State(state): State<AppState>) -> Json<Vec<User>> {
    let users = sqlx::query_as!(
        User,
        "SELECT id, username, email, created_at FROM users ORDER BY created_at DESC LIMIT 50"
    )
    .fetch_all(&state.db)
    .await
    .unwrap_or_default();

    Json(users)
}

async fn get_user(
    State(state): State<AppState>,
    axum::extract::Path(user_id): axum::extract::Path<uuid::Uuid>,
) -> Result<Json<User>, axum::http::StatusCode> {
    sqlx::query_as!(
        User,
        "SELECT id, username, email, created_at FROM users WHERE id = $1",
        user_id
    )
    .fetch_optional(&state.db)
    .await
    .map_err(|_| axum::http::StatusCode::INTERNAL_SERVER_ERROR)?
    .map(Json)
    .ok_or(axum::http::StatusCode::NOT_FOUND)
}

#[tokio::main]
async fn main() {
    let pool = PgPoolOptions::new()
        .max_connections(20)
        .min_connections(5)
        .acquire_timeout(Duration::from_secs(3))
        .connect("postgres://localhost/myapp")
        .await
        .expect("Failed to create pool");

    let state = AppState { db: pool };

    let app = Router::new()
        .route("/users", get(list_users))
        .route("/users/:id", get(get_user))
        .with_state(state);

    let listener = tokio::net::TcpListener::bind("0.0.0.0:3000")
        .await
        .unwrap();

    axum::serve(listener, app).await.unwrap();
}
```

The pool lives in `AppState`, which is cloned into every request handler via Axum's `State` extractor. Cloning a pool is cheap — it's just an `Arc` internally.

## Monitoring Your Pool

You can't fix what you can't measure. SQLx's pool exposes metrics:

```rust
async fn pool_health(State(state): State<AppState>) -> String {
    let pool = &state.db;
    format!(
        "Pool stats:\n  Size: {}\n  Idle: {}\n  Active: {}",
        pool.size(),
        pool.num_idle(),
        pool.size() - pool.num_idle() as u32,
    )
}
```

In production, wire these into your metrics system (Prometheus, Datadog, whatever). The metric you care about most is **acquire wait time** — how long requests spend waiting for a free connection. If that number starts climbing, your pool is too small or your queries are too slow.

## Sizing Rules of Thumb

Pool sizing is one of those things people overthink. Here are my rules:

1. **Start with `max_connections = 2 * CPU cores + 1`** on the application side. A 4-core server gets a pool of 9. This accounts for connections that are waiting on I/O (not using CPU) while others are processing results.

2. **Never exceed Postgres `max_connections / number_of_app_instances`.** If Postgres allows 100 connections and you have 5 app servers, cap each at 20.

3. **Set `min_connections` to about 25% of `max_connections`.** This keeps the pool warm without wasting resources during low-traffic periods.

4. **Set `acquire_timeout` to your SLA minus average query time.** If your API must respond within 5 seconds and your average query is 50ms, a 3-second acquire timeout gives you margin.

5. **Watch for connection exhaustion.** If you see acquire timeouts in your logs, either increase the pool size, optimize slow queries, or add a circuit breaker.

## What's Next

We've covered how to connect to databases (lessons 1-2) and how to manage those connections efficiently (this lesson). Next up: schema migrations — how to evolve your database schema in a Rust project without losing data or your sanity.
