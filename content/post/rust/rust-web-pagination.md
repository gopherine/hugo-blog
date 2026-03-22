---
title: "Lesson 7: Pagination, Filtering, and Sorting — API patterns that scale"
author: Atharva Pandey
series: "Rust Web & API Development"
lesson: 7
keywords: [Rust, rust, web, API, axum, pagination, cursor, filtering, sorting, keyset]
tags: [Rust tutorial, rust, web, api]
date: '2024-10-14T16:30:00.000Z'
---

We shipped a "list all orders" endpoint that returned everything. No pagination. Worked great in development with 50 test records. In production, one customer had 340,000 orders. The endpoint took 12 seconds, the response was 45MB, and the frontend crashed trying to render it. We added pagination that afternoon. You should add it before that afternoon.

## Offset-Based Pagination

The most common approach. Simple to implement, easy to understand, and good enough for most internal tools and admin panels.

```rust
use axum::{extract::{Query, State}, Json};
use serde::{Deserialize, Serialize};

#[derive(Deserialize)]
pub struct PaginationParams {
    pub page: Option<i64>,
    pub per_page: Option<i64>,
    pub sort_by: Option<String>,
    pub sort_order: Option<String>,
}

impl PaginationParams {
    pub fn page(&self) -> i64 {
        self.page.unwrap_or(1).max(1)
    }

    pub fn per_page(&self) -> i64 {
        self.per_page.unwrap_or(20).clamp(1, 100)
    }

    pub fn offset(&self) -> i64 {
        (self.page() - 1) * self.per_page()
    }
}

#[derive(Serialize)]
pub struct PaginatedResponse<T: Serialize> {
    pub data: Vec<T>,
    pub pagination: PaginationMeta,
}

#[derive(Serialize)]
pub struct PaginationMeta {
    pub total: i64,
    pub page: i64,
    pub per_page: i64,
    pub total_pages: i64,
    pub has_next: bool,
    pub has_prev: bool,
}

impl PaginationMeta {
    pub fn new(total: i64, page: i64, per_page: i64) -> Self {
        let total_pages = (total as f64 / per_page as f64).ceil() as i64;
        Self {
            total,
            page,
            per_page,
            total_pages,
            has_next: page < total_pages,
            has_prev: page > 1,
        }
    }
}
```

Usage in a handler:

```rust
async fn list_users(
    State(state): State<AppState>,
    Query(params): Query<PaginationParams>,
) -> Result<Json<PaginatedResponse<User>>, AppError> {
    let page = params.page();
    let per_page = params.per_page();
    let offset = params.offset();

    let users = sqlx::query_as!(
        User,
        r#"SELECT id, email, name, role, created_at, updated_at
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
        pagination: PaginationMeta::new(total, page, per_page),
    }))
}
```

The response:

```json
{
  "data": [
    { "id": 1, "email": "alice@example.com", "name": "Alice" },
    { "id": 2, "email": "bob@example.com", "name": "Bob" }
  ],
  "pagination": {
    "total": 150,
    "page": 1,
    "per_page": 20,
    "total_pages": 8,
    "has_next": true,
    "has_prev": false
  }
}
```

### The Offset Problem

Offset pagination has a well-known scaling issue. `OFFSET 100000` tells PostgreSQL to fetch 100,000 rows, throw them away, and return the next batch. On large tables, deep pages get progressively slower.

For most applications, this doesn't matter — users rarely go past page 10. But if you're building an API that clients paginate through exhaustively (data exports, sync jobs, infinite scrolls), you need cursor-based pagination.

## Cursor-Based Pagination

Instead of "give me page 47," cursor pagination says "give me 20 items after this specific item." The cursor is typically an encoded representation of the last item's sort key.

```rust
use base64::{engine::general_purpose::STANDARD as BASE64, Engine};

#[derive(Deserialize)]
pub struct CursorParams {
    pub cursor: Option<String>,
    pub limit: Option<i64>,
    pub direction: Option<String>, // "next" or "prev"
}

#[derive(Serialize, Deserialize)]
struct Cursor {
    id: i64,
    created_at: String,
}

impl Cursor {
    fn encode(&self) -> String {
        let json = serde_json::to_string(self).unwrap();
        BASE64.encode(json.as_bytes())
    }

    fn decode(encoded: &str) -> Result<Self, AppError> {
        let bytes = BASE64.decode(encoded)
            .map_err(|_| AppError::bad_request("Invalid cursor"))?;
        let json = String::from_utf8(bytes)
            .map_err(|_| AppError::bad_request("Invalid cursor"))?;
        serde_json::from_str(&json)
            .map_err(|_| AppError::bad_request("Invalid cursor"))
    }
}

#[derive(Serialize)]
pub struct CursorResponse<T: Serialize> {
    pub data: Vec<T>,
    pub next_cursor: Option<String>,
    pub prev_cursor: Option<String>,
    pub has_more: bool,
}
```

The handler:

```rust
async fn list_users_cursor(
    State(state): State<AppState>,
    Query(params): Query<CursorParams>,
) -> Result<Json<CursorResponse<User>>, AppError> {
    let limit = params.limit.unwrap_or(20).clamp(1, 100);

    let users = if let Some(ref cursor_str) = params.cursor {
        let cursor = Cursor::decode(cursor_str)?;
        let created_at: chrono::DateTime<chrono::Utc> = cursor.created_at.parse()
            .map_err(|_| AppError::bad_request("Invalid cursor timestamp"))?;

        sqlx::query_as!(
            User,
            r#"SELECT id, email, name, role, created_at, updated_at
               FROM users
               WHERE (created_at, id) < ($1, $2)
               ORDER BY created_at DESC, id DESC
               LIMIT $3"#,
            created_at,
            cursor.id,
            limit + 1, // fetch one extra to determine has_more
        )
        .fetch_all(&state.db)
        .await?
    } else {
        sqlx::query_as!(
            User,
            r#"SELECT id, email, name, role, created_at, updated_at
               FROM users
               ORDER BY created_at DESC, id DESC
               LIMIT $1"#,
            limit + 1,
        )
        .fetch_all(&state.db)
        .await?
    };

    let has_more = users.len() as i64 > limit;
    let users: Vec<User> = users.into_iter().take(limit as usize).collect();

    let next_cursor = if has_more {
        users.last().map(|u| Cursor {
            id: u.id,
            created_at: u.created_at.to_rfc3339(),
        }.encode())
    } else {
        None
    };

    Ok(Json(CursorResponse {
        data: users,
        next_cursor,
        prev_cursor: params.cursor, // the current cursor becomes the prev cursor
        has_more,
    }))
}
```

The `(created_at, id) < ($1, $2)` is a *keyset* comparison — it uses a composite index efficiently. This query has consistent performance regardless of how deep you paginate, because there's no `OFFSET`. Make sure you have a compound index:

```sql
CREATE INDEX idx_users_created_at_id ON users(created_at DESC, id DESC);
```

### Which Pagination to Use

| Feature | Offset | Cursor |
|---------|--------|--------|
| Jump to specific page | Yes | No |
| Consistent deep pagination | No (slow) | Yes |
| Handles concurrent inserts | No (items shift) | Yes |
| Implementation complexity | Low | Medium |
| URL shareable | Yes (page=5) | Awkward (long cursor string) |

My rule: offset for admin panels and dashboards where users click page numbers. Cursor for public APIs, mobile apps, and anything with infinite scroll.

## Filtering

Most list endpoints need filtering. The challenge is building it safely — no SQL injection, no arbitrary column access.

```rust
#[derive(Deserialize)]
pub struct UserFilters {
    pub role: Option<String>,
    pub search: Option<String>,
    pub created_after: Option<chrono::DateTime<chrono::Utc>>,
    pub created_before: Option<chrono::DateTime<chrono::Utc>>,
}

async fn list_users_filtered(
    State(state): State<AppState>,
    Query(pagination): Query<PaginationParams>,
    Query(filters): Query<UserFilters>,
) -> Result<Json<PaginatedResponse<User>>, AppError> {
    let page = pagination.page();
    let per_page = pagination.per_page();
    let offset = pagination.offset();

    // Using COALESCE/NULL trick for optional filters
    let users = sqlx::query_as!(
        User,
        r#"SELECT id, email, name, role, created_at, updated_at
           FROM users
           WHERE ($1::text IS NULL OR role = $1)
             AND ($2::text IS NULL OR (name ILIKE '%' || $2 || '%' OR email ILIKE '%' || $2 || '%'))
             AND ($3::timestamptz IS NULL OR created_at >= $3)
             AND ($4::timestamptz IS NULL OR created_at <= $4)
           ORDER BY created_at DESC
           LIMIT $5 OFFSET $6"#,
        filters.role,
        filters.search,
        filters.created_after,
        filters.created_before,
        per_page,
        offset,
    )
    .fetch_all(&state.db)
    .await?;

    let total = sqlx::query_scalar!(
        r#"SELECT COUNT(*) FROM users
           WHERE ($1::text IS NULL OR role = $1)
             AND ($2::text IS NULL OR (name ILIKE '%' || $2 || '%' OR email ILIKE '%' || $2 || '%'))
             AND ($3::timestamptz IS NULL OR created_at >= $3)
             AND ($4::timestamptz IS NULL OR created_at <= $4)"#,
        filters.role,
        filters.search,
        filters.created_after,
        filters.created_before,
    )
    .fetch_one(&state.db)
    .await?
    .unwrap_or(0);

    Ok(Json(PaginatedResponse {
        data: users,
        pagination: PaginationMeta::new(total, page, per_page),
    }))
}
```

The `$1::text IS NULL OR role = $1` pattern is a clean way to make filters optional in a single static query. When the parameter is `None`, PostgreSQL short-circuits the `IS NULL` check and skips the condition. No dynamic SQL needed.

For the search filter — `ILIKE '%' || $2 || '%'` — the concatenation happens inside PostgreSQL, keeping the search term parameterized. Never build `LIKE` patterns in Rust with `format!()` and pass them as raw strings. That's how you get injection.

## Sorting

Type-safe sorting prevents clients from ordering by arbitrary columns (including ones they shouldn't see).

```rust
#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UserSortField {
    Name,
    Email,
    CreatedAt,
    Role,
}

impl UserSortField {
    fn as_sql(&self) -> &'static str {
        match self {
            Self::Name => "name",
            Self::Email => "email",
            Self::CreatedAt => "created_at",
            Self::Role => "role",
        }
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SortOrder {
    Asc,
    Desc,
}

impl SortOrder {
    fn as_sql(&self) -> &'static str {
        match self {
            Self::Asc => "ASC",
            Self::Desc => "DESC",
        }
    }
}
```

Since `query_as!` needs a literal SQL string, dynamic sorting requires `QueryBuilder`:

```rust
use sqlx::QueryBuilder;

async fn list_users_sorted(
    State(state): State<AppState>,
    Query(pagination): Query<PaginationParams>,
    Query(filters): Query<UserFilters>,
) -> Result<Json<PaginatedResponse<User>>, AppError> {
    let sort_field = pagination.sort_by
        .as_deref()
        .and_then(|s| serde_json::from_value::<UserSortField>(serde_json::Value::String(s.to_string())).ok())
        .unwrap_or(UserSortField::CreatedAt);

    let sort_order = pagination.sort_order
        .as_deref()
        .and_then(|s| serde_json::from_value::<SortOrder>(serde_json::Value::String(s.to_string())).ok())
        .unwrap_or(SortOrder::Desc);

    let mut query = QueryBuilder::new(
        "SELECT id, email, name, role, created_at, updated_at FROM users WHERE 1=1"
    );

    if let Some(ref role) = filters.role {
        query.push(" AND role = ");
        query.push_bind(role);
    }

    if let Some(ref search) = filters.search {
        query.push(" AND (name ILIKE ");
        query.push_bind(format!("%{}%", search));
        query.push(" OR email ILIKE ");
        query.push_bind(format!("%{}%", search));
        query.push(")");
    }

    // Sort field comes from our enum — safe to interpolate
    query.push(format!(" ORDER BY {} {}", sort_field.as_sql(), sort_order.as_sql()));

    query.push(" LIMIT ");
    query.push_bind(pagination.per_page());
    query.push(" OFFSET ");
    query.push_bind(pagination.offset());

    let users = query
        .build_query_as::<User>()
        .fetch_all(&state.db)
        .await?;

    // Count query (same filters, no sorting/pagination)
    let mut count_query = QueryBuilder::new("SELECT COUNT(*) FROM users WHERE 1=1");

    if let Some(ref role) = filters.role {
        count_query.push(" AND role = ");
        count_query.push_bind(role);
    }

    if let Some(ref search) = filters.search {
        count_query.push(" AND (name ILIKE ");
        count_query.push_bind(format!("%{}%", search));
        count_query.push(" OR email ILIKE ");
        count_query.push_bind(format!("%{}%", search));
        count_query.push(")");
    }

    let total: (i64,) = count_query
        .build_query_as()
        .fetch_one(&state.db)
        .await?;

    Ok(Json(PaginatedResponse {
        data: users,
        pagination: PaginationMeta::new(total.0, pagination.page(), pagination.per_page()),
    }))
}
```

The sort field is safe to interpolate directly because it comes from our `UserSortField` enum — the only possible values are column names we've explicitly allowed. There's no path from user input to arbitrary SQL here. This is Rust's type system protecting you.

## Making It Reusable

You'll implement pagination on many endpoints. Abstract the common parts:

```rust
pub trait Paginate {
    fn apply_pagination<'a>(
        builder: &mut QueryBuilder<'a, sqlx::Postgres>,
        params: &PaginationParams,
    ) {
        builder.push(" LIMIT ");
        builder.push_bind(params.per_page());
        builder.push(" OFFSET ");
        builder.push_bind(params.offset());
    }
}

pub async fn count_with_filters(
    pool: &sqlx::PgPool,
    table: &str,
    where_clause: &str,
) -> Result<i64, sqlx::Error> {
    let query = format!("SELECT COUNT(*) as count FROM {} WHERE {}", table, where_clause);
    let row: (i64,) = sqlx::query_as(&query)
        .fetch_one(pool)
        .await?;
    Ok(row.0)
}
```

But honestly? Don't over-abstract pagination. Every endpoint has slightly different filtering and sorting needs. A bit of repetition in your handlers is better than a generic pagination framework that's hard to customize. Copy-paste the pattern, adjust the queries, move on.

## Performance Tips

- **Always have indexes on sort columns.** Without an index, PostgreSQL does a full table scan and sorts in memory. With millions of rows, that's seconds of latency.
- **COUNT(*) is expensive.** On very large tables, consider caching the total count or using approximate counts: `SELECT reltuples::bigint FROM pg_class WHERE relname = 'users'`.
- **Use EXPLAIN ANALYZE.** Before shipping a list endpoint, run the query through `EXPLAIN ANALYZE` to verify it uses indexes.
- **Limit `per_page` max.** I cap it at 100. A client requesting 10,000 items per page is either confused or trying to dump your database.
- **Consider materialized views** for complex filtered aggregations that don't need real-time accuracy.

Next up: WebSockets. Real-time communication in Rust.
