---
title: "Lesson 6: API Versioning and Backwards Compatibility — Don't break your users"
author: Atharva Pandey
series: "Rust in Production — Real-World Architecture"
lesson: 6
keywords: [Rust, rust, architecture, production, API versioning, backwards compatibility, semver, REST]
tags: [Rust tutorial, rust, architecture, production]
date: '2025-10-26T10:55:00.000Z'
---

I once shipped a "minor" API change on a Friday. Renamed a JSON field from `user_name` to `username`. Seemed harmless — we were cleaning up inconsistencies. By Monday morning, we had 14 support tickets from integration partners whose parsers broke. One partner had hardcoded the field name into a system that processed payroll. People didn't get paid because I renamed a JSON field.

That was the last time I treated backwards compatibility as optional.

In Rust, the type system gives you superpowers for evolving APIs without breaking consumers. But you need to be deliberate about it. Let me walk you through the strategies I've used in production.

## URL-Based API Versioning

The simplest approach — and the one I recommend starting with — is URL path versioning:

```rust
// src/api/routes.rs

use axum::{Router, routing::get, routing::post};

pub fn create_router(state: AppState) -> Router {
    Router::new()
        .nest("/api/v1", v1_routes(state.clone()))
        .nest("/api/v2", v2_routes(state))
}

fn v1_routes(state: AppState) -> Router {
    Router::new()
        .route("/users", get(v1::handlers::list_users))
        .route("/users/:id", get(v1::handlers::get_user))
        .route("/users", post(v1::handlers::create_user))
        .route("/orders", get(v1::handlers::list_orders))
        .with_state(state)
}

fn v2_routes(state: AppState) -> Router {
    Router::new()
        .route("/users", get(v2::handlers::list_users))
        .route("/users/:id", get(v2::handlers::get_user))
        .route("/users", post(v2::handlers::create_user))
        .route("/orders", get(v2::handlers::list_orders))
        // v2 adds new endpoints
        .route("/users/:id/preferences", get(v2::handlers::get_preferences))
        .route("/users/:id/preferences", post(v2::handlers::update_preferences))
        .with_state(state)
}
```

The key insight: **v1 and v2 share the same domain layer.** The difference is in DTOs and handlers, not in business logic.

## Versioned DTOs

This is where the real work happens. Your v1 response stays frozen. Your v2 response can evolve:

```rust
// src/api/v1/dto.rs

use serde::Serialize;

#[derive(Serialize)]
pub struct UserResponse {
    pub id: String,
    pub user_name: String,      // the legacy field name — frozen forever
    pub email: String,
    pub role: String,
    pub created_at: String,
}

impl From<&domain::User> for UserResponse {
    fn from(user: &domain::User) -> Self {
        Self {
            id: user.id().to_string(),
            user_name: user.display_name().to_string(),  // map to legacy name
            email: user.email().as_str().to_string(),
            role: format!("{:?}", user.role()),
            created_at: user.created_at().to_rfc3339(),
        }
    }
}
```

```rust
// src/api/v2/dto.rs

use serde::{Serialize, Deserialize};

#[derive(Serialize)]
pub struct UserResponse {
    pub id: String,
    pub username: String,        // the corrected field name
    pub email: String,
    pub role: UserRole,          // structured enum instead of string
    pub preferences: Option<UserPreferences>,  // new in v2
    pub created_at: String,
    pub updated_at: String,      // new in v2
}

#[derive(Serialize)]
#[serde(rename_all = "snake_case")]
pub enum UserRole {
    User,
    Admin,
    ServiceAccount,
}

#[derive(Serialize, Deserialize)]
pub struct UserPreferences {
    pub timezone: String,
    pub locale: String,
    pub notifications_enabled: bool,
}

impl From<&domain::User> for UserResponse {
    fn from(user: &domain::User) -> Self {
        Self {
            id: user.id().to_string(),
            username: user.display_name().to_string(),
            email: user.email().as_str().to_string(),
            role: match user.role() {
                domain::Role::User => UserRole::User,
                domain::Role::Admin => UserRole::Admin,
                domain::Role::ServiceAccount => UserRole::ServiceAccount,
            },
            preferences: user.preferences().map(|p| UserPreferences {
                timezone: p.timezone.clone(),
                locale: p.locale.clone(),
                notifications_enabled: p.notifications_enabled,
            }),
            created_at: user.created_at().to_rfc3339(),
            updated_at: user.updated_at().to_rfc3339(),
        }
    }
}
```

Both DTOs implement `From<&domain::User>`. The domain doesn't know or care about API versions. The conversion logic handles the mapping — including that legacy `user_name` field that we can never change in v1.

## Serde's Secret Weapons

Serde has features specifically designed for backwards-compatible evolution. Use them:

```rust
#[derive(Deserialize)]
pub struct CreateUserRequest {
    pub email: String,
    pub username: String,

    // Added in v2.1 — existing clients don't send this
    #[serde(default)]
    pub timezone: Option<String>,

    // Added in v2.2 — defaults to true for existing clients
    #[serde(default = "default_notifications")]
    pub notifications_enabled: bool,

    // Renamed field — accept both old and new name
    #[serde(alias = "display_name")]
    pub username_display: String,
}

fn default_notifications() -> bool {
    true
}
```

- `#[serde(default)]` — if the field is missing, use `Default::default()` (or `None` for `Option`).
- `#[serde(default = "...")]` — if missing, call a custom function.
- `#[serde(alias = "...")]` — accept the old field name as an alternative.
- `#[serde(skip_serializing_if = "Option::is_none")]` — don't include `null` fields in output.

These are non-breaking changes. Existing clients keep working. New clients can use new fields.

## The Additive-Only Rule

Here's the principle that saves you: **API changes should be additive.** You can:

- Add new fields (with defaults)
- Add new endpoints
- Add new enum variants (if the client can ignore unknowns)
- Add new optional query parameters

You cannot:

- Remove fields
- Rename fields
- Change field types
- Change the meaning of existing values
- Make optional fields required

If you need to do any of the "cannot" things, that's a new API version.

```rust
/// A response that follows additive-only evolution
#[derive(Serialize)]
pub struct OrderResponse {
    // v1 fields — never change these
    pub id: String,
    pub status: String,
    pub total_cents: i64,
    pub currency: String,
    pub created_at: String,

    // v1.1 — added, optional for backwards compat
    #[serde(skip_serializing_if = "Option::is_none")]
    pub estimated_delivery: Option<String>,

    // v1.2 — added
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tracking_url: Option<String>,

    // v1.3 — added, structured data
    #[serde(skip_serializing_if = "Option::is_none")]
    pub line_items: Option<Vec<LineItemResponse>>,
}
```

Each addition is a backward-compatible evolution of the same API version. No need for v2 unless you're making breaking changes.

## Header-Based Versioning

For more granular control, you can use custom headers:

```rust
use axum::{
    extract::Request,
    http::StatusCode,
    middleware::Next,
    response::Response,
};

pub async fn version_middleware(
    request: Request,
    next: Next,
) -> Result<Response, StatusCode> {
    let version = request
        .headers()
        .get("X-API-Version")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("2024-01-01");  // default to earliest version

    // Store version in request extensions for handlers to read
    let mut request = request;
    request.extensions_mut().insert(ApiVersion::parse(version)?);

    Ok(next.run(request).await)
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct ApiVersion {
    pub year: u16,
    pub month: u8,
    pub day: u8,
}

impl ApiVersion {
    pub fn parse(s: &str) -> Result<Self, StatusCode> {
        let parts: Vec<&str> = s.split('-').collect();
        if parts.len() != 3 {
            return Err(StatusCode::BAD_REQUEST);
        }
        Ok(Self {
            year: parts[0].parse().map_err(|_| StatusCode::BAD_REQUEST)?,
            month: parts[1].parse().map_err(|_| StatusCode::BAD_REQUEST)?,
            day: parts[2].parse().map_err(|_| StatusCode::BAD_REQUEST)?,
        })
    }

    pub fn supports(&self, feature_date: &str) -> bool {
        let feature = Self::parse(feature_date).unwrap();
        *self >= feature
    }
}
```

Then in handlers:

```rust
use axum::Extension;

pub async fn get_user(
    Extension(version): Extension<ApiVersion>,
    Path(user_id): Path<String>,
    State(state): State<AppState>,
) -> Result<Json<serde_json::Value>, AppError> {
    let user = state.user_service.find_by_id(&user_id).await?;

    let response = if version.supports("2024-06-01") {
        // New response format with preferences
        serde_json::to_value(v2::UserResponse::from(&user))?
    } else {
        // Legacy response format
        serde_json::to_value(v1::UserResponse::from(&user))?
    };

    Ok(Json(response))
}
```

Date-based versioning (like Stripe uses) is elegant because there's no ambiguity about ordering. `2024-06-01` is obviously newer than `2024-01-01`.

## Request Compatibility with Enums

Deserializing requests is trickier than serializing responses. You need to handle values you don't recognize:

```rust
#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OrderStatus {
    Draft,
    Placed,
    Paid,
    Shipped,
    Delivered,
    Cancelled,
    // Catch-all for forward compatibility
    #[serde(other)]
    Unknown,
}

impl OrderStatus {
    pub fn is_known(&self) -> bool {
        !matches!(self, OrderStatus::Unknown)
    }
}
```

`#[serde(other)]` is a lifesaver. If a client sends a status value that your current version doesn't know about (maybe they're talking to a newer version of a sibling service), you get `Unknown` instead of a deserialization error.

## Deprecation Strategy

Don't just remove old versions. Deprecate them with clear timelines:

```rust
use axum::{
    response::Response,
    middleware::Next,
    extract::Request,
};

pub async fn deprecation_middleware(
    request: Request,
    next: Next,
) -> Response {
    let path = request.uri().path().to_string();
    let mut response = next.run(request).await;

    if path.starts_with("/api/v1") {
        response.headers_mut().insert(
            "Deprecation",
            "true".parse().unwrap(),
        );
        response.headers_mut().insert(
            "Sunset",
            "Sat, 01 Mar 2025 00:00:00 GMT".parse().unwrap(),
        );
        response.headers_mut().insert(
            "Link",
            "</api/v2>; rel=\"successor-version\"".parse().unwrap(),
        );
    }

    response
}
```

The `Deprecation`, `Sunset`, and `Link` headers are standard HTTP headers (RFC 8594 and RFC 8288). Good API clients will surface these warnings. Give your consumers at least 6 months between deprecation and removal.

And log it:

```rust
pub async fn v1_usage_tracking(
    request: Request,
    next: Next,
) -> Response {
    let client_id = request
        .headers()
        .get("X-Client-Id")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("unknown")
        .to_string();

    let path = request.uri().path().to_string();

    tracing::warn!(
        client_id = %client_id,
        path = %path,
        "deprecated v1 API access"
    );

    // Emit metric for dashboarding
    metrics::counter!("api.deprecated.v1.requests", "client" => client_id).increment(1);

    next.run(request).await
}
```

Before removing v1, check the metrics. If `client_x` is still making 10,000 requests per day to v1, you need to reach out to them — not just flip a switch.

## Schema Validation with Tests

The nuclear option for preventing accidental breaking changes: snapshot testing your API schema.

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn v1_user_response_schema_unchanged() {
        let user = domain::User::test_fixture();
        let response = v1::UserResponse::from(&user);
        let json = serde_json::to_value(&response).unwrap();

        // These fields MUST exist with these exact names
        assert!(json.get("id").is_some(), "missing 'id' field");
        assert!(json.get("user_name").is_some(), "missing 'user_name' field");
        assert!(json.get("email").is_some(), "missing 'email' field");
        assert!(json.get("role").is_some(), "missing 'role' field");
        assert!(json.get("created_at").is_some(), "missing 'created_at' field");

        // These fields MUST NOT change type
        assert!(json["id"].is_string(), "'id' must be string");
        assert!(json["user_name"].is_string(), "'user_name' must be string");
        assert!(json["role"].is_string(), "'role' must be string");
    }

    #[test]
    fn v1_create_user_accepts_minimal_payload() {
        // Ensure old clients with minimal payloads still work
        let minimal = r#"{"email": "test@example.com", "user_name": "Test"}"#;
        let result: Result<v1::CreateUserRequest, _> = serde_json::from_str(minimal);
        assert!(result.is_ok(), "minimal v1 payload should still parse");
    }

    #[test]
    fn v2_create_user_accepts_v1_payload() {
        // v2 should be able to parse what v1 clients send
        let v1_payload = r#"{"email": "test@example.com", "username": "Test"}"#;
        let result: Result<v2::CreateUserRequest, _> = serde_json::from_str(v1_payload);
        assert!(result.is_ok(), "v2 should accept v1-style payloads");
    }
}
```

These tests are cheap to write and catch regressions immediately. If someone renames `user_name` to `username` in the v1 DTO, the test fails before it ever reaches code review.

## The Bigger Picture

API versioning isn't just about technical correctness — it's about trust. Your consumers built systems that depend on your API behaving a certain way. When you break that contract, you break their trust. And trust, once broken, is very hard to rebuild.

The strategies are:

1. **Start with additive-only changes.** You'd be surprised how far this takes you.
2. **Use serde's defaults, aliases, and skip attributes** to maintain parsing compatibility.
3. **When you must break, create a new version** with a clear deprecation timeline.
4. **Test your schema** to prevent accidental breaks.
5. **Monitor deprecated API usage** so removal is data-driven, not hope-driven.

Next lesson: we'll look at compile-time feature flags in Rust — controlling what code is included in your binary at build time, not runtime.
