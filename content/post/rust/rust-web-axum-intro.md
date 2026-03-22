---
title: "Lesson 2: Axum from Zero — Routing, handlers, extractors"
author: Atharva Pandey
series: "Rust Web & API Development"
lesson: 2
keywords: [Rust, rust, web, API, axum, routing, handlers, extractors, state management]
tags: [Rust tutorial, rust, web, api]
date: '2024-10-03T14:45:00.000Z'
---

The first time I tried Axum, I wrote a handler that took five extractor arguments and spent twenty minutes staring at a compiler error that said my function "didn't implement Handler." Turns out the order of extractors matters, and there's a limit on how many you can have. Nobody tells you that upfront. So I'm telling you now.

## Routing Fundamentals

Axum's router is just a struct that maps HTTP methods and paths to handler functions. No macros, no attributes — you build routes with method calls.

```rust
use axum::{
    routing::{get, post, put, delete},
    Router,
};

let app = Router::new()
    .route("/users", get(list_users).post(create_user))
    .route("/users/:id", get(get_user).put(update_user).delete(delete_user));
```

Notice the `:id` syntax for path parameters. Axum uses colon-prefixed segments, similar to Express. You can also use wildcards:

```rust
// Matches /files/anything/here/deeply/nested
.route("/files/*path", get(serve_file))
```

### Nesting Routers

This is where Axum gets interesting for real applications. You don't shove 80 routes into one file — you compose routers from modules.

```rust
// src/routes/users.rs
pub fn router() -> Router<AppState> {
    Router::new()
        .route("/", get(list_users).post(create_user))
        .route("/:id", get(get_user).put(update_user).delete(delete_user))
        .route("/:id/posts", get(get_user_posts))
}

// src/routes/posts.rs
pub fn router() -> Router<AppState> {
    Router::new()
        .route("/", get(list_posts).post(create_post))
        .route("/:id", get(get_post).put(update_post).delete(delete_post))
}

// src/main.rs
let app = Router::new()
    .nest("/api/users", users::router())
    .nest("/api/posts", posts::router())
    .with_state(state);
```

The `.nest()` method prepends the given path to all routes in the nested router. So `users::router()`'s `/` becomes `/api/users/`, and `/:id` becomes `/api/users/:id`. Clean separation.

### Fallback Routes

What happens when someone hits a path that doesn't exist? By default, Axum returns a plain 404. You probably want something more useful.

```rust
use axum::http::StatusCode;
use axum::Json;
use serde_json::json;

async fn fallback() -> (StatusCode, Json<serde_json::Value>) {
    (
        StatusCode::NOT_FOUND,
        Json(json!({ "error": "route not found" })),
    )
}

let app = Router::new()
    .route("/api/users", get(list_users))
    .fallback(fallback);
```

## Handlers — They're Just Functions

An Axum handler is any async function that takes zero or more extractors and returns something that implements `IntoResponse`. That's it. No trait to implement on a struct, no macro to slap on. Just a function.

```rust
// Simplest possible handler
async fn hello() -> &'static str {
    "hello"
}

// Returning a status code with a body
async fn created() -> (StatusCode, String) {
    (StatusCode::CREATED, "resource created".to_string())
}

// Returning JSON
async fn json_handler() -> Json<serde_json::Value> {
    Json(json!({ "status": "ok", "version": "1.0" }))
}

// Returning headers + status + body
use axum::http::header;

async fn with_headers() -> (StatusCode, [(header::HeaderName, &'static str); 1], String) {
    (
        StatusCode::OK,
        [(header::CONTENT_TYPE, "text/plain")],
        "custom headers".to_string(),
    )
}
```

The `IntoResponse` trait is implemented for a surprising number of types. Strings, byte vectors, status codes, tuples of status codes and bodies, `Json<T>`, `Html<T>`, and more. You can also implement it for your own types — we'll do that when we build error responses.

### The Handler Trait

Here's the thing nobody explains clearly: Axum's `Handler` trait is implemented for async functions with up to 16 parameters. Each parameter must implement `FromRequestParts` or the *last* parameter can implement `FromRequest`. This distinction matters.

`FromRequestParts` extractors can read headers, query strings, path parameters — anything that doesn't consume the request body. `FromRequest` extractors consume the body. Since you can only read the body once, only the *last* extractor can be a `FromRequest` type.

```rust
// This works — Json (FromRequest) is last
async fn create_user(
    State(pool): State<PgPool>,        // FromRequestParts
    Path(org_id): Path<i64>,           // FromRequestParts
    Json(body): Json<CreateUserInput>, // FromRequest — must be last
) -> Result<Json<User>, AppError> {
    // ...
}

// This WON'T compile — Json is not last
async fn broken(
    Json(body): Json<CreateUserInput>, // FromRequest — NOT last!
    State(pool): State<PgPool>,        // FromRequestParts
) -> Result<Json<User>, AppError> {
    // ...
}
```

I've seen people spend hours on this. The error message says something about `Handler` not being implemented, which is technically correct but misleading. The fix is always: put body-consuming extractors last.

## Extractors — The Real Power

Extractors are how you pull data out of incoming requests. They're Axum's core abstraction, and understanding them deeply saves you enormous amounts of time.

### Path Parameters

```rust
use axum::extract::Path;

// Single parameter
async fn get_user(Path(id): Path<i64>) -> String {
    format!("User {}", id)
}

// Multiple parameters — use a tuple
async fn get_user_post(
    Path((user_id, post_id)): Path<(i64, i64)>,
) -> String {
    format!("User {} Post {}", user_id, post_id)
}

// Named parameters — use a struct
#[derive(Deserialize)]
struct UserPostPath {
    user_id: i64,
    post_id: i64,
}

async fn get_user_post_named(
    Path(params): Path<UserPostPath>,
) -> String {
    format!("User {} Post {}", params.user_id, params.post_id)
}
```

I strongly prefer the struct approach for anything with more than one parameter. Tuple ordering is error-prone and the compiler won't save you if you swap two `i64` values.

### Query Parameters

```rust
use axum::extract::Query;

#[derive(Deserialize)]
struct Pagination {
    page: Option<u32>,
    per_page: Option<u32>,
    sort_by: Option<String>,
}

async fn list_users(Query(params): Query<Pagination>) -> Json<Vec<User>> {
    let page = params.page.unwrap_or(1);
    let per_page = params.per_page.unwrap_or(20);
    // ...
}
```

Hit `/users?page=2&per_page=50&sort_by=name` and the `Pagination` struct fills in automatically. Missing fields become `None` because they're `Option<T>`.

### JSON Body

```rust
use axum::Json;
use serde::Deserialize;

#[derive(Deserialize)]
struct CreateUser {
    email: String,
    name: String,
    role: Option<String>,
}

async fn create_user(Json(input): Json<CreateUser>) -> (StatusCode, Json<User>) {
    // input.email, input.name, input.role are available
    let user = User {
        id: 1,
        email: input.email,
        name: input.name,
        role: input.role.unwrap_or_else(|| "user".to_string()),
    };
    (StatusCode::CREATED, Json(user))
}
```

If the JSON is malformed or missing required fields, Axum returns a 422 by default. We'll customize this in Lesson 4.

### Headers

```rust
use axum::http::HeaderMap;
use axum::http::header;

// All headers
async fn with_headers(headers: HeaderMap) -> String {
    let ua = headers
        .get(header::USER_AGENT)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("unknown");
    format!("User-Agent: {}", ua)
}

// Typed header extraction
use axum_extra::TypedHeader;
use axum_extra::headers::Authorization;
use axum_extra::headers::authorization::Bearer;

async fn protected(
    TypedHeader(auth): TypedHeader<Authorization<Bearer>>,
) -> String {
    format!("Token: {}", auth.token())
}
```

### State

State is how you share things across handlers — database pools, configuration, caches.

```rust
use axum::extract::State;
use std::sync::Arc;

#[derive(Clone)]
struct AppState {
    db: PgPool,
    redis: RedisPool,
    config: Arc<Config>,
}

async fn list_users(State(state): State<AppState>) -> Json<Vec<User>> {
    let users = sqlx::query_as!(User, "SELECT * FROM users")
        .fetch_all(&state.db)
        .await
        .unwrap();
    Json(users)
}

// In main:
let state = AppState {
    db: PgPool::connect("postgres://...").await.unwrap(),
    redis: RedisPool::new("redis://..."),
    config: Arc::new(Config::from_env()),
};

let app = Router::new()
    .route("/users", get(list_users))
    .with_state(state);
```

The state must implement `Clone`. For cheap-to-clone things like connection pools (they're just `Arc` wrappers internally), this is free. For expensive structs, wrap them in `Arc`.

## Building a Complete CRUD Example

Here's a full working example that ties everything together. This is the skeleton pattern I use for every new Axum service.

```rust
use axum::{
    extract::{Path, Query, State, Json},
    http::StatusCode,
    routing::{get, post, put, delete},
    Router,
};
use serde::{Deserialize, Serialize};
use std::sync::{Arc, Mutex};
use std::collections::HashMap;

// --- Models ---

#[derive(Clone, Serialize, Deserialize)]
struct User {
    id: u64,
    email: String,
    name: String,
}

#[derive(Deserialize)]
struct CreateUserInput {
    email: String,
    name: String,
}

#[derive(Deserialize)]
struct UpdateUserInput {
    email: Option<String>,
    name: Option<String>,
}

#[derive(Deserialize)]
struct ListParams {
    page: Option<u32>,
    per_page: Option<u32>,
}

// --- State ---

#[derive(Clone)]
struct AppState {
    users: Arc<Mutex<HashMap<u64, User>>>,
    next_id: Arc<Mutex<u64>>,
}

impl AppState {
    fn new() -> Self {
        Self {
            users: Arc::new(Mutex::new(HashMap::new())),
            next_id: Arc::new(Mutex::new(1)),
        }
    }
}

// --- Handlers ---

async fn list_users(
    State(state): State<AppState>,
    Query(params): Query<ListParams>,
) -> Json<Vec<User>> {
    let users = state.users.lock().unwrap();
    let page = params.page.unwrap_or(1) as usize;
    let per_page = params.per_page.unwrap_or(20) as usize;
    let skip = (page - 1) * per_page;

    let result: Vec<User> = users
        .values()
        .skip(skip)
        .take(per_page)
        .cloned()
        .collect();

    Json(result)
}

async fn get_user(
    State(state): State<AppState>,
    Path(id): Path<u64>,
) -> Result<Json<User>, StatusCode> {
    let users = state.users.lock().unwrap();
    users
        .get(&id)
        .cloned()
        .map(Json)
        .ok_or(StatusCode::NOT_FOUND)
}

async fn create_user(
    State(state): State<AppState>,
    Json(input): Json<CreateUserInput>,
) -> (StatusCode, Json<User>) {
    let mut next_id = state.next_id.lock().unwrap();
    let id = *next_id;
    *next_id += 1;

    let user = User {
        id,
        email: input.email,
        name: input.name,
    };

    state.users.lock().unwrap().insert(id, user.clone());
    (StatusCode::CREATED, Json(user))
}

async fn update_user(
    State(state): State<AppState>,
    Path(id): Path<u64>,
    Json(input): Json<UpdateUserInput>,
) -> Result<Json<User>, StatusCode> {
    let mut users = state.users.lock().unwrap();
    let user = users.get_mut(&id).ok_or(StatusCode::NOT_FOUND)?;

    if let Some(email) = input.email {
        user.email = email;
    }
    if let Some(name) = input.name {
        user.name = name;
    }

    Ok(Json(user.clone()))
}

async fn delete_user(
    State(state): State<AppState>,
    Path(id): Path<u64>,
) -> StatusCode {
    let mut users = state.users.lock().unwrap();
    if users.remove(&id).is_some() {
        StatusCode::NO_CONTENT
    } else {
        StatusCode::NOT_FOUND
    }
}

// --- Main ---

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt::init();

    let state = AppState::new();

    let app = Router::new()
        .route("/api/users", get(list_users).post(create_user))
        .route("/api/users/:id", get(get_user).put(update_user).delete(delete_user))
        .with_state(state);

    let listener = tokio::net::TcpListener::bind("0.0.0.0:3000")
        .await
        .unwrap();

    tracing::info!("listening on {}", listener.local_addr().unwrap());
    axum::serve(listener, app).await.unwrap();
}
```

This uses in-memory storage with `Mutex<HashMap>` — obviously not for production, but it lets you test every CRUD operation without setting up a database. We'll swap in SQLx in Lesson 6.

## Writing Custom Extractors

Sometimes the built-in extractors aren't enough. Maybe you want to extract a user from a JWT token in every request. You can write your own extractor.

```rust
use axum::{
    async_trait,
    extract::FromRequestParts,
    http::{request::Parts, StatusCode},
};

struct CurrentUser {
    id: u64,
    email: String,
}

#[async_trait]
impl<S> FromRequestParts<S> for CurrentUser
where
    S: Send + Sync,
{
    type Rejection = StatusCode;

    async fn from_request_parts(parts: &mut Parts, _state: &S) -> Result<Self, Self::Rejection> {
        let auth_header = parts
            .headers
            .get("Authorization")
            .and_then(|v| v.to_str().ok())
            .ok_or(StatusCode::UNAUTHORIZED)?;

        let token = auth_header
            .strip_prefix("Bearer ")
            .ok_or(StatusCode::UNAUTHORIZED)?;

        // In reality, you'd validate a JWT here
        // For now, just parse a fake user ID from the token
        let user_id: u64 = token.parse().map_err(|_| StatusCode::UNAUTHORIZED)?;

        Ok(CurrentUser {
            id: user_id,
            email: format!("user{}@example.com", user_id),
        })
    }
}

// Now use it like any other extractor
async fn me(user: CurrentUser) -> Json<serde_json::Value> {
    Json(json!({
        "id": user.id,
        "email": user.email,
    }))
}
```

The pattern: implement `FromRequestParts` for read-only extractors, `FromRequest` for body-consuming extractors. Return a `Rejection` type when extraction fails. That rejection automatically becomes the HTTP response.

## Common Gotchas

**Extractor ordering matters.** Body-consuming extractors go last. I said it before, I'll say it again, because you'll forget and waste an hour on it.

**State type must match.** If your router uses `.with_state(AppState)`, every handler on that router must accept `State<AppState>` (or no state at all). Mixing state types across nested routers requires careful use of `.with_state()` at each nesting level.

**Don't block the async runtime.** If you use `Mutex::lock()` in an async handler, make sure the lock is held for a very short time. For anything longer, use `tokio::sync::Mutex` or move the work to a blocking thread with `tokio::task::spawn_blocking`. We used `std::sync::Mutex` in the example above because the operations are trivial — don't do this with database calls or file I/O.

**The 16-extractor limit.** `Handler` is implemented for functions with up to 16 arguments. If you hit this limit, your handler is doing too much. Refactor — combine related extractors into a single struct.

Next up: middleware with Tower layers. The real reason I picked Axum over everything else.
