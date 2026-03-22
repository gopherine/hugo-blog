---
title: "Lesson 11: Integration Testing HTTP Services — Testing without mocks"
author: Atharva Pandey
series: "Rust Web & API Development"
lesson: 11
keywords: [Rust, rust, web, API, axum, testing, integration tests, HTTP testing, test helpers]
tags: [Rust tutorial, rust, web, api]
date: '2024-10-24T15:40:00.000Z'
---

I worked on a codebase that had 600 unit tests with mocked HTTP clients, mocked databases, mocked everything. All 600 passed. The application didn't work. The mocks were wrong — they returned data in a format the real database never produced. Those 600 tests gave the team confidence to ship broken code. Integration tests that hit real infrastructure are harder to write but they tell you whether your application actually works.

## The Axum Testing Model

Axum has a superpower for testing: you don't need to start a TCP server. You can send requests directly to your `Router` using `tower::ServiceExt`. No port binding, no HTTP client, no network roundtrip. Tests run fast and don't flake due to port conflicts.

```rust
use axum::{
    body::Body,
    http::{Request, StatusCode},
    Router,
};
use tower::ServiceExt; // for `oneshot`
use http_body_util::BodyExt; // for `collect`

#[tokio::test]
async fn test_health_check() {
    let app = create_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .uri("/health")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
}
```

The `.oneshot()` method sends a single request through the router and returns the response. It consumes the router, so each test gets a fresh instance. No shared state, no test interference.

## A Test Helper Module

Writing raw `Request::builder()` calls in every test gets old fast. Build a helper:

```rust
// tests/common/mod.rs

use axum::{body::Body, http::{Request, Method, StatusCode}, Router};
use http_body_util::BodyExt;
use serde::de::DeserializeOwned;
use serde_json::Value;
use tower::ServiceExt;

pub struct TestClient {
    app: Router,
}

impl TestClient {
    pub fn new(app: Router) -> Self {
        Self { app }
    }

    pub async fn get(&self, uri: &str) -> TestResponse {
        self.request(Method::GET, uri, Body::empty(), None).await
    }

    pub async fn get_with_token(&self, uri: &str, token: &str) -> TestResponse {
        self.request(Method::GET, uri, Body::empty(), Some(token)).await
    }

    pub async fn post_json(&self, uri: &str, body: &Value) -> TestResponse {
        let body = Body::from(serde_json::to_string(body).unwrap());
        self.request(Method::POST, uri, body, None).await
    }

    pub async fn post_json_with_token(&self, uri: &str, body: &Value, token: &str) -> TestResponse {
        let body = Body::from(serde_json::to_string(body).unwrap());
        self.request(Method::POST, uri, body, Some(token)).await
    }

    pub async fn put_json_with_token(&self, uri: &str, body: &Value, token: &str) -> TestResponse {
        let body = Body::from(serde_json::to_string(body).unwrap());
        self.request(Method::PUT, uri, body, Some(token)).await
    }

    pub async fn delete_with_token(&self, uri: &str, token: &str) -> TestResponse {
        self.request(Method::DELETE, uri, Body::empty(), Some(token)).await
    }

    async fn request(
        &self,
        method: Method,
        uri: &str,
        body: Body,
        token: Option<&str>,
    ) -> TestResponse {
        let mut builder = Request::builder()
            .method(method)
            .uri(uri)
            .header("Content-Type", "application/json");

        if let Some(token) = token {
            builder = builder.header("Authorization", format!("Bearer {}", token));
        }

        let request = builder.body(body).unwrap();

        let response = self.app.clone().oneshot(request).await.unwrap();
        let status = response.status();
        let body = response.into_body().collect().await.unwrap().to_bytes();
        let body_bytes = body.to_vec();

        TestResponse {
            status,
            body: body_bytes,
        }
    }
}

pub struct TestResponse {
    pub status: StatusCode,
    body: Vec<u8>,
}

impl TestResponse {
    pub fn json<T: DeserializeOwned>(&self) -> T {
        serde_json::from_slice(&self.body).unwrap()
    }

    pub fn text(&self) -> String {
        String::from_utf8(self.body.clone()).unwrap()
    }

    pub fn assert_status(&self, expected: StatusCode) -> &Self {
        assert_eq!(self.status, expected, "Response body: {}", self.text());
        self
    }
}
```

Now tests read like specs:

```rust
#[tokio::test]
async fn test_create_and_get_user() {
    let app = create_test_app().await;
    let client = TestClient::new(app);

    // Create a user
    let create_response = client
        .post_json("/api/users", &serde_json::json!({
            "email": "alice@example.com",
            "name": "Alice",
            "password": "securepassword123"
        }))
        .await;

    create_response.assert_status(StatusCode::CREATED);
    let user: Value = create_response.json();
    assert_eq!(user["email"], "alice@example.com");
    assert_eq!(user["name"], "Alice");

    let user_id = user["id"].as_i64().unwrap();

    // Get the user
    let get_response = client.get(&format!("/api/users/{}", user_id)).await;
    get_response.assert_status(StatusCode::OK);
    let fetched: Value = get_response.json();
    assert_eq!(fetched["email"], "alice@example.com");
}
```

## Testing with a Real Database

For integration tests that hit the database, you need a test database. My pattern: create a unique database for each test, run the test, drop the database.

```rust
use sqlx::PgPool;
use uuid::Uuid;

pub struct TestDb {
    pub pool: PgPool,
    db_name: String,
    base_url: String,
}

impl TestDb {
    pub async fn new() -> Self {
        let base_url = std::env::var("TEST_DATABASE_URL")
            .unwrap_or_else(|_| "postgres://postgres:postgres@localhost:5432".to_string());

        let db_name = format!("test_{}", Uuid::new_v4().to_string().replace('-', ""));

        // Connect to the default database to create our test database
        let admin_pool = PgPool::connect(&format!("{}/postgres", base_url))
            .await
            .expect("Failed to connect to admin database");

        sqlx::query(&format!("CREATE DATABASE {}", db_name))
            .execute(&admin_pool)
            .await
            .expect("Failed to create test database");

        let pool = PgPool::connect(&format!("{}/{}", base_url, db_name))
            .await
            .expect("Failed to connect to test database");

        // Run migrations
        sqlx::migrate!("./migrations")
            .run(&pool)
            .await
            .expect("Failed to run migrations");

        Self {
            pool,
            db_name,
            base_url,
        }
    }
}

impl Drop for TestDb {
    fn drop(&mut self) {
        // Spawn a blocking task to drop the database
        // We need a new runtime because Drop is sync
        let base_url = self.base_url.clone();
        let db_name = self.db_name.clone();

        // Use std::thread to avoid runtime nesting issues
        std::thread::spawn(move || {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async {
                let admin_pool = PgPool::connect(&format!("{}/postgres", base_url))
                    .await
                    .unwrap();

                // Terminate connections to the test database
                let _ = sqlx::query(&format!(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{}'",
                    db_name
                ))
                .execute(&admin_pool)
                .await;

                let _ = sqlx::query(&format!("DROP DATABASE IF EXISTS {}", db_name))
                    .execute(&admin_pool)
                    .await;
            });
        })
        .join()
        .ok();
    }
}
```

Use it in tests:

```rust
async fn create_test_app() -> Router {
    let test_db = TestDb::new().await;

    let state = AppState {
        db: test_db.pool.clone(),
        jwt: JwtConfig {
            secret: "test-secret-key-for-testing-only".to_string(),
            expiration_hours: 1,
        },
    };

    // You'd normally store test_db somewhere so it doesn't get dropped
    // Use a static or leak it for tests
    let pool = test_db.pool.clone();
    std::mem::forget(test_db); // Keep the database alive

    create_router(state)
}
```

A cleaner approach — use a fixture function that returns both the app and the database handle:

```rust
struct TestFixture {
    client: TestClient,
    _db: TestDb, // underscore prefix: we hold it to keep the DB alive
}

async fn setup() -> TestFixture {
    let db = TestDb::new().await;

    let state = AppState {
        db: db.pool.clone(),
        jwt: JwtConfig {
            secret: "test-secret".to_string(),
            expiration_hours: 1,
        },
    };

    let app = create_router(state);
    let client = TestClient::new(app);

    TestFixture { client, _db: db }
}

#[tokio::test]
async fn test_user_crud() {
    let fixture = setup().await;

    // Create
    let resp = fixture.client.post_json("/api/users", &serde_json::json!({
        "email": "test@example.com",
        "name": "Test User",
        "password": "password123"
    })).await;
    resp.assert_status(StatusCode::CREATED);

    // ... more operations
}
```

## Testing Auth Flows

```rust
async fn login_as(client: &TestClient, email: &str, password: &str) -> String {
    let resp = client.post_json("/api/login", &serde_json::json!({
        "email": email,
        "password": password,
    })).await;
    resp.assert_status(StatusCode::OK);
    let body: Value = resp.json();
    body["token"].as_str().unwrap().to_string()
}

#[tokio::test]
async fn test_protected_endpoint_requires_auth() {
    let fixture = setup().await;

    // Without token — should fail
    let resp = fixture.client.get("/api/me").await;
    resp.assert_status(StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn test_protected_endpoint_with_auth() {
    let fixture = setup().await;

    // Register a user
    fixture.client.post_json("/api/register", &serde_json::json!({
        "email": "auth@example.com",
        "name": "Auth Test",
        "password": "password123"
    })).await.assert_status(StatusCode::CREATED);

    // Login
    let token = login_as(&fixture.client, "auth@example.com", "password123").await;

    // Access protected endpoint
    let resp = fixture.client.get_with_token("/api/me", &token).await;
    resp.assert_status(StatusCode::OK);
    let user: Value = resp.json();
    assert_eq!(user["email"], "auth@example.com");
}

#[tokio::test]
async fn test_expired_token_rejected() {
    let fixture = setup().await;

    // Create a token that's already expired
    let expired_token = create_expired_token("1", "test@example.com", "user");

    let resp = fixture.client.get_with_token("/api/me", &expired_token).await;
    resp.assert_status(StatusCode::UNAUTHORIZED);
    let body: Value = resp.json();
    assert_eq!(body["message"], "Token expired");
}
```

## Testing Validation

```rust
#[tokio::test]
async fn test_create_user_validation() {
    let fixture = setup().await;

    // Missing required fields
    let resp = fixture.client.post_json("/api/users", &serde_json::json!({
        "email": "not-an-email"
    })).await;
    resp.assert_status(StatusCode::BAD_REQUEST);

    // Invalid email
    let resp = fixture.client.post_json("/api/users", &serde_json::json!({
        "email": "not-an-email",
        "name": "Test",
        "password": "password123"
    })).await;
    resp.assert_status(StatusCode::UNPROCESSABLE_ENTITY);
    let body: Value = resp.json();
    assert!(body["details"].as_array().unwrap().iter().any(|e| e["field"] == "email"));

    // Password too short
    let resp = fixture.client.post_json("/api/users", &serde_json::json!({
        "email": "valid@example.com",
        "name": "Test",
        "password": "short"
    })).await;
    resp.assert_status(StatusCode::UNPROCESSABLE_ENTITY);
    let body: Value = resp.json();
    assert!(body["details"].as_array().unwrap().iter().any(|e| e["field"] == "password"));
}

#[tokio::test]
async fn test_duplicate_email_rejected() {
    let fixture = setup().await;

    let user_data = serde_json::json!({
        "email": "dupe@example.com",
        "name": "First User",
        "password": "password123"
    });

    // First creation succeeds
    fixture.client.post_json("/api/users", &user_data).await
        .assert_status(StatusCode::CREATED);

    // Second creation with same email fails
    let resp = fixture.client.post_json("/api/users", &user_data).await;
    resp.assert_status(StatusCode::CONFLICT);
}
```

## Testing Pagination

```rust
#[tokio::test]
async fn test_pagination() {
    let fixture = setup().await;

    // Create 25 users
    for i in 0..25 {
        fixture.client.post_json("/api/users", &serde_json::json!({
            "email": format!("user{}@example.com", i),
            "name": format!("User {}", i),
            "password": "password123"
        })).await.assert_status(StatusCode::CREATED);
    }

    // First page
    let resp = fixture.client.get("/api/users?page=1&per_page=10").await;
    resp.assert_status(StatusCode::OK);
    let body: Value = resp.json();
    assert_eq!(body["data"].as_array().unwrap().len(), 10);
    assert_eq!(body["pagination"]["total"], 25);
    assert_eq!(body["pagination"]["total_pages"], 3);
    assert_eq!(body["pagination"]["has_next"], true);
    assert_eq!(body["pagination"]["has_prev"], false);

    // Last page
    let resp = fixture.client.get("/api/users?page=3&per_page=10").await;
    resp.assert_status(StatusCode::OK);
    let body: Value = resp.json();
    assert_eq!(body["data"].as_array().unwrap().len(), 5);
    assert_eq!(body["pagination"]["has_next"], false);
    assert_eq!(body["pagination"]["has_prev"], true);
}
```

## Testing Error Responses

```rust
#[tokio::test]
async fn test_not_found_returns_json() {
    let fixture = setup().await;

    let resp = fixture.client.get("/api/users/99999").await;
    resp.assert_status(StatusCode::NOT_FOUND);

    let body: Value = resp.json();
    assert_eq!(body["error"], "not_found");
    assert!(body["message"].as_str().unwrap().contains("not found"));
}

#[tokio::test]
async fn test_malformed_json_returns_400() {
    let fixture = setup().await;

    let resp = fixture.client
        .request(
            Method::POST,
            "/api/users",
            Body::from("this is not json"),
            None,
        )
        .await;
    resp.assert_status(StatusCode::BAD_REQUEST);
}
```

## Running Tests in Parallel

By default, Rust runs tests in parallel. Each test gets its own database (thanks to our `TestDb` helper), so they don't interfere. But be aware:

- Database creation adds overhead. If you have 100 tests, that's 100 databases created and destroyed.
- PostgreSQL has a default max connections limit. Lots of parallel tests can exhaust it.

Limit parallelism if needed:

```bash
cargo test -- --test-threads=4
```

Or use a connection-limited pool in your test helper.

## Test Organization

```
tests/
├── common/
│   └── mod.rs          # TestClient, TestDb, setup()
├── api/
│   ├── mod.rs
│   ├── users_test.rs   # User CRUD tests
│   ├── auth_test.rs    # Auth flow tests
│   └── posts_test.rs   # Post CRUD tests
└── integration.rs      # Imports all test modules
```

Each test file focuses on one resource or feature. The common module provides shared helpers. Everything runs against real infrastructure.

## The Anti-Mock Philosophy

I'm not saying never mock. I mock external services — Stripe, SendGrid, S3. You don't want your test suite to fail because Stripe is having a bad day. But I don't mock the database, I don't mock the HTTP layer, and I don't mock internal services. Those are the parts where bugs actually live.

A test that proves your SQL query returns the right data from a real PostgreSQL instance is worth ten tests that prove your mock returns the right data from a fake database. The mock can't tell you about missing indexes, transaction isolation issues, or type coercion surprises. The real database can.

Final lesson: production deployment — Docker, graceful shutdown, and observability.
