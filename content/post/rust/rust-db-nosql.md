---
title: "Lesson 10: Redis, MongoDB, and Non-Relational Stores — Beyond SQL"
author: Atharva Pandey
series: "Rust Database Patterns"
lesson: 10
keywords: [Rust, rust, database, Redis, MongoDB, NoSQL, caching, postgres, key-value]
tags: [Rust tutorial, rust, database, postgres]
date: '2024-11-10T15:37:00.000Z'
---

I spent a week optimizing a Postgres query that powered a leaderboard. It involved a complex window function over millions of rows, and no amount of indexing got it under 200ms. Then a senior engineer walked over, looked at the query, and said "Why isn't this in Redis?" He was right. I replaced 30 lines of SQL with a sorted set and the response time dropped to 2ms.

Not every data problem is a SQL problem. Sometimes you need the right tool, not a better query plan.

## When to Reach Beyond Postgres

Postgres is phenomenal. I've used it as a queue, a cache, a document store, and a pub/sub system. But there are situations where a specialized store is the right call:

- **Caching** — You need sub-millisecond reads for data that doesn't change often. Redis.
- **Session storage** — You need fast key-value access with automatic expiration. Redis.
- **Rate limiting** — You need atomic counters with TTLs. Redis.
- **Leaderboards** — You need sorted sets with rank lookups. Redis.
- **Flexible schemas** — Your document structure varies per record and you don't want ALTER TABLE for every new field. MongoDB.
- **Time series** — You're ingesting millions of data points per second. A dedicated time-series database.
- **Full-text search** — You need relevance scoring, faceted search, fuzzy matching. Elasticsearch or Meilisearch.

The pattern I recommend: Postgres as your source of truth, with specialized stores for specific access patterns. Not instead of SQL — alongside it.

## Redis in Rust

The `redis` crate is mature and well-maintained. Here's how to set it up:

```toml
[dependencies]
redis = { version = "0.27", features = ["tokio-comp", "connection-manager"] }
tokio = { version = "1", features = ["full"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

### Basic Connection and Operations

```rust
use redis::AsyncCommands;

#[tokio::main]
async fn main() -> redis::RedisResult<()> {
    let client = redis::Client::open("redis://127.0.0.1/")?;
    let mut conn = client.get_multiplexed_async_connection().await?;

    // String operations
    conn.set("user:1:name", "atharva").await?;
    let name: String = conn.get("user:1:name").await?;
    println!("Name: {}", name);

    // Set with expiration (TTL in seconds)
    conn.set_ex("session:abc123", "user_data_here", 3600).await?;

    // Increment (atomic)
    conn.incr("page_views:homepage", 1i64).await?;
    let views: i64 = conn.get("page_views:homepage").await?;
    println!("Views: {}", views);

    Ok(())
}
```

### Connection Pooling with Redis

For production, use a connection manager that handles reconnection:

```rust
use redis::aio::ConnectionManager;

#[derive(Clone)]
struct AppState {
    db: sqlx::PgPool,
    redis: ConnectionManager,
}

async fn create_app_state() -> AppState {
    let db = sqlx::PgPool::connect("postgres://localhost/myapp")
        .await
        .expect("Failed to connect to Postgres");

    let redis_client = redis::Client::open("redis://127.0.0.1/")
        .expect("Invalid Redis URL");
    let redis = ConnectionManager::new(redis_client)
        .await
        .expect("Failed to connect to Redis");

    AppState { db, redis }
}
```

`ConnectionManager` automatically reconnects if the connection drops. It's also `Clone`, so you can share it across handlers without wrapping it in `Arc`.

### Caching Pattern: Read-Through Cache

The most common Redis pattern — check cache first, fall back to database:

```rust
use redis::AsyncCommands;
use std::time::Duration;

async fn get_user_cached(
    state: &AppState,
    user_id: uuid::Uuid,
) -> Result<User, Box<dyn std::error::Error>> {
    let cache_key = format!("user:{}", user_id);
    let mut redis = state.redis.clone();

    // Try cache first
    let cached: Option<String> = redis.get(&cache_key).await?;
    if let Some(json) = cached {
        let user: User = serde_json::from_str(&json)?;
        return Ok(user);
    }

    // Cache miss — fetch from Postgres
    let user = sqlx::query_as!(
        User,
        "SELECT id, username, email, bio, created_at FROM users WHERE id = $1",
        user_id
    )
    .fetch_one(&state.db)
    .await?;

    // Store in cache with 5-minute TTL
    let json = serde_json::to_string(&user)?;
    redis.set_ex(&cache_key, &json, 300).await?;

    Ok(user)
}

// Invalidate cache when data changes
async fn update_user(
    state: &AppState,
    user_id: uuid::Uuid,
    input: UpdateUser,
) -> Result<User, Box<dyn std::error::Error>> {
    let user = sqlx::query_as!(
        User,
        "UPDATE users SET username = COALESCE($1, username), email = COALESCE($2, email)
         WHERE id = $3 RETURNING *",
        input.username,
        input.email,
        user_id
    )
    .fetch_one(&state.db)
    .await?;

    // Invalidate the cache
    let mut redis = state.redis.clone();
    let cache_key = format!("user:{}", user_id);
    redis.del(&cache_key).await?;

    Ok(user)
}
```

The critical detail: invalidate on write, not update. Don't try to keep the cache in sync by writing to both Postgres and Redis. Delete the cache entry and let the next read repopulate it. This avoids consistency issues where the cache has stale data.

### Rate Limiting with Redis

Redis's atomic increment with TTL makes rate limiting trivial:

```rust
use redis::AsyncCommands;

async fn check_rate_limit(
    redis: &mut ConnectionManager,
    client_ip: &str,
    limit: i64,
    window_secs: u64,
) -> Result<bool, redis::RedisError> {
    let key = format!("rate:{}:{}", client_ip, window_secs);

    let count: i64 = redis.incr(&key, 1i64).await?;

    if count == 1 {
        // First request in this window — set the expiration
        redis.expire(&key, window_secs as i64).await?;
    }

    Ok(count <= limit)
}

// Usage in middleware
async fn rate_limit_middleware(
    State(state): State<AppState>,
    request: axum::extract::Request,
    next: axum::middleware::Next,
) -> axum::response::Response {
    let ip = request
        .headers()
        .get("x-forwarded-for")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("unknown");

    let mut redis = state.redis.clone();
    let allowed = check_rate_limit(&mut redis, ip, 100, 60)
        .await
        .unwrap_or(true); // Fail open if Redis is down

    if !allowed {
        return (
            axum::http::StatusCode::TOO_MANY_REQUESTS,
            "Rate limit exceeded",
        ).into_response();
    }

    next.run(request).await
}
```

Notice the `unwrap_or(true)` — if Redis is down, we fail open and allow the request. This is a deliberate choice. Your rate limiter shouldn't bring down your entire service. If Redis goes offline, you lose rate limiting temporarily, which is better than returning 500 errors to everyone.

### Leaderboards with Sorted Sets

This is where Redis genuinely excels over any SQL solution:

```rust
use redis::AsyncCommands;

async fn update_score(
    redis: &mut ConnectionManager,
    player_id: &str,
    score: f64,
) -> redis::RedisResult<()> {
    redis.zadd("leaderboard:global", player_id, score).await
}

async fn get_top_players(
    redis: &mut ConnectionManager,
    count: isize,
) -> redis::RedisResult<Vec<(String, f64)>> {
    redis.zrevrange_withscores("leaderboard:global", 0, count - 1).await
}

async fn get_player_rank(
    redis: &mut ConnectionManager,
    player_id: &str,
) -> redis::RedisResult<Option<i64>> {
    // Returns 0-based rank, highest score first
    redis.zrevrank("leaderboard:global", player_id).await
}

async fn get_rank_range(
    redis: &mut ConnectionManager,
    start: isize,
    stop: isize,
) -> redis::RedisResult<Vec<(String, f64)>> {
    redis.zrevrange_withscores("leaderboard:global", start, stop).await
}
```

All of these operations are O(log N) regardless of how many players exist. Getting the rank of a specific player in a 10-million-entry leaderboard takes microseconds. Try doing that with a SQL `ORDER BY` and `ROW_NUMBER()`.

## MongoDB in Rust

The official `mongodb` crate provides async access to MongoDB:

```toml
[dependencies]
mongodb = "3"
tokio = { version = "1", features = ["full"] }
serde = { version = "1", features = ["derive"] }
```

### Basic CRUD

```rust
use mongodb::{Client, Collection, bson::doc};
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
struct Event {
    #[serde(rename = "_id", skip_serializing_if = "Option::is_none")]
    id: Option<mongodb::bson::oid::ObjectId>,
    event_type: String,
    user_id: String,
    payload: mongodb::bson::Document,
    timestamp: chrono::DateTime<chrono::Utc>,
}

#[tokio::main]
async fn main() -> Result<(), mongodb::error::Error> {
    let client = Client::with_uri_str("mongodb://localhost:27017").await?;
    let db = client.database("myapp");
    let events: Collection<Event> = db.collection("events");

    // Insert
    let event = Event {
        id: None,
        event_type: "page_view".to_string(),
        user_id: "user_123".to_string(),
        payload: doc! {
            "page": "/dashboard",
            "referrer": "https://google.com",
            "browser": "Firefox"
        },
        timestamp: chrono::Utc::now(),
    };

    events.insert_one(event).await?;

    // Query
    let filter = doc! {
        "event_type": "page_view",
        "timestamp": {
            "$gte": chrono::Utc::now() - chrono::Duration::hours(24)
        }
    };

    let mut cursor = events.find(filter).await?;

    use futures::TryStreamExt;
    while let Some(event) = cursor.try_next().await? {
        println!("{}: {} - {:?}", event.event_type, event.user_id, event.payload);
    }

    Ok(())
}
```

### When MongoDB Actually Makes Sense

I want to be honest: MongoDB gets recommended in situations where Postgres JSONB would work fine. But there are legitimate use cases:

**Event logging with variable schemas.** If every event type has different payload fields and you don't want to normalize them into relational tables, MongoDB's flexible documents are a good fit.

```rust
// Each event type has completely different payload shapes
// In Postgres, you'd use JSONB. In MongoDB, it's native.

let click_event = doc! {
    "type": "click",
    "element_id": "btn-submit",
    "coordinates": { "x": 450, "y": 230 },
    "timestamp": chrono::Utc::now()
};

let purchase_event = doc! {
    "type": "purchase",
    "order_id": "ord-12345",
    "items": [
        { "sku": "WIDGET-A", "qty": 2, "price": 1999 },
        { "sku": "GADGET-B", "qty": 1, "price": 4999 }
    ],
    "total": 8997,
    "timestamp": chrono::Utc::now()
};
```

**High-volume writes where you can tolerate eventual consistency.** MongoDB's write performance for insert-heavy workloads (analytics, IoT sensor data) can outperform Postgres because it doesn't enforce the same ACID guarantees by default.

**Document-oriented data that doesn't need JOINs.** If your data model is a tree of nested objects that you always read and write as a unit, MongoDB eliminates the object-relational impedance mismatch.

## Multi-Store Architecture

Real applications often use multiple data stores. Here's a practical architecture:

```rust
use sqlx::PgPool;
use redis::aio::ConnectionManager;
use mongodb::Collection;

#[derive(Clone)]
struct DataLayer {
    postgres: PgPool,
    redis: ConnectionManager,
    events: Collection<Event>,
}

impl DataLayer {
    async fn new() -> Result<Self, Box<dyn std::error::Error>> {
        let postgres = PgPool::connect("postgres://localhost/myapp").await?;

        let redis_client = redis::Client::open("redis://127.0.0.1/")?;
        let redis = ConnectionManager::new(redis_client).await?;

        let mongo = mongodb::Client::with_uri_str("mongodb://localhost:27017").await?;
        let events = mongo.database("myapp").collection("events");

        Ok(Self { postgres, redis, events })
    }
}

// Postgres: source of truth for structured data
async fn get_user(data: &DataLayer, id: uuid::Uuid) -> Result<User, sqlx::Error> {
    // Check Redis cache first
    let mut redis = data.redis.clone();
    let cache_key = format!("user:{}", id);

    if let Ok(Some(cached)) = redis.get::<_, Option<String>>(&cache_key).await {
        if let Ok(user) = serde_json::from_str::<User>(&cached) {
            return Ok(user);
        }
    }

    // Fetch from Postgres
    let user = sqlx::query_as!(User,
        "SELECT id, username, email, bio, created_at FROM users WHERE id = $1", id
    )
    .fetch_one(&data.postgres)
    .await?;

    // Populate cache
    if let Ok(json) = serde_json::to_string(&user) {
        let _: Result<(), _> = redis.set_ex(&cache_key, &json, 300).await;
    }

    Ok(user)
}

// MongoDB: high-volume event logging
async fn log_event(data: &DataLayer, event: Event) -> Result<(), mongodb::error::Error> {
    data.events.insert_one(event).await?;
    Ok(())
}

// Redis: real-time counters
async fn increment_page_views(data: &DataLayer, page: &str) -> Result<i64, redis::RedisError> {
    let mut redis = data.redis.clone();
    redis.incr(format!("views:{}", page), 1i64).await
}
```

Each store handles what it's best at:
- Postgres stores users, orders, products — relational data with ACID guarantees
- Redis handles caching, counters, rate limits, sessions — fast ephemeral data
- MongoDB stores events — high-volume, variable-schema, write-heavy data

## Error Handling Across Stores

When you have multiple data stores, error handling gets interesting. A write might succeed in Postgres but fail in Redis. You need to decide: is that an error?

```rust
#[derive(Debug, thiserror::Error)]
enum DataError {
    #[error("database error: {0}")]
    Database(#[from] sqlx::Error),

    #[error("cache error: {0}")]
    Cache(String),

    #[error("event store error: {0}")]
    EventStore(String),
}

async fn create_user_full(
    data: &DataLayer,
    input: CreateUser,
) -> Result<User, DataError> {
    // 1. Write to Postgres (must succeed)
    let user = sqlx::query_as!(User,
        "INSERT INTO users (username, email) VALUES ($1, $2) RETURNING *",
        input.username, input.email
    )
    .fetch_one(&data.postgres)
    .await?;

    // 2. Populate cache (best effort — log failure but don't fail the request)
    let mut redis = data.redis.clone();
    if let Ok(json) = serde_json::to_string(&user) {
        if let Err(e) = redis.set_ex::<_, _, ()>(
            &format!("user:{}", user.id), &json, 300
        ).await {
            eprintln!("Failed to cache new user: {}", e);
            // Don't return error — user was created successfully
        }
    }

    // 3. Log event (best effort)
    let event = Event {
        id: None,
        event_type: "user_created".to_string(),
        user_id: user.id.to_string(),
        payload: doc! { "username": &user.username },
        timestamp: chrono::Utc::now(),
    };
    if let Err(e) = data.events.insert_one(event).await {
        eprintln!("Failed to log user_created event: {}", e);
        // Don't return error — user was created successfully
    }

    Ok(user)
}
```

The pattern: the primary write (Postgres) must succeed. Secondary writes (cache, event log) are best-effort. Log failures but don't fail the request. If your cache is down, users should still be able to sign up — they'll just experience slightly slower reads until the cache comes back.

## Wrapping Up the Series

We've covered the full stack of database patterns in Rust:

1. **SQLx** — compile-time checked raw SQL
2. **Diesel** — the ORM approach
3. **Connection pools** — managing database connections efficiently
4. **Migrations** — evolving your schema safely
5. **Transactions** — atomic operations and rollback
6. **Repository pattern** — abstracting persistence
7. **Query builders** — type-safe dynamic queries
8. **Testing** — testing against real databases
9. **Performance** — N+1 queries, indexes, EXPLAIN
10. **Non-relational stores** — Redis, MongoDB, and when to use them

The throughline across all ten lessons: Rust's type system is your best friend for database work. Compile-time query checking, ownership-based transaction management, trait-based repository abstraction — these aren't academic exercises. They're patterns that prevent real production bugs.

Pick the right tool for each data problem, test against real databases, and let the compiler catch what it can. Your on-call rotation will thank you.
