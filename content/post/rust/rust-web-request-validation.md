---
title: "Lesson 4: Request Validation and Error Responses — Clean input handling"
author: Atharva Pandey
series: "Rust Web & API Development"
lesson: 4
keywords: [Rust, rust, web, API, axum, validation, error handling, error responses, validator]
tags: [Rust tutorial, rust, web, api]
date: '2024-10-07T19:15:00.000Z'
---

A junior engineer on my team once deployed an endpoint that accepted any string as an email address. Someone submitted "lol" as their email, the downstream email service threw a cryptic error, and our error tracking lit up with 500s for an hour. Input validation isn't glamorous, but skipping it is how you get paged at dinner.

## The Problem with Default Error Responses

Out of the box, Axum's error responses are... not great. Send malformed JSON to a `Json<T>` handler and you get back:

```
HTTP/1.1 422 Unprocessable Entity

Failed to deserialize the JSON body into the target type: missing field `name` at line 1 column 12
```

Plain text. No structured format. Internal type names leaked to clients. This is fine for development but unacceptable for production. We need:

1. Consistent JSON error responses
2. Meaningful error messages that don't leak internals
3. Field-level validation errors
4. A clean pattern for creating and returning errors from handlers

## Building a Custom Error Type

The foundation of good error handling in Axum is a custom error type that implements `IntoResponse`. This type replaces all the ad-hoc `StatusCode` returns we've been using.

```rust
use axum::{
    http::StatusCode,
    response::{IntoResponse, Response},
    Json,
};
use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct AppError {
    #[serde(skip)]
    pub status: StatusCode,
    pub error: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub details: Option<Vec<ValidationError>>,
}

#[derive(Debug, Serialize)]
pub struct ValidationError {
    pub field: String,
    pub message: String,
}

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        let body = Json(serde_json::json!({
            "error": self.error,
            "message": self.message,
            "details": self.details,
        }));
        (self.status, body).into_response()
    }
}
```

Now every error response from your API looks like:

```json
{
  "error": "validation_error",
  "message": "Invalid input",
  "details": [
    { "field": "email", "message": "must be a valid email address" },
    { "field": "age", "message": "must be between 1 and 150" }
  ]
}
```

### Convenience Constructors

Typing out the full struct every time is tedious. Add helper methods:

```rust
impl AppError {
    pub fn bad_request(message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::BAD_REQUEST,
            error: "bad_request".to_string(),
            message: message.into(),
            details: None,
        }
    }

    pub fn not_found(message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::NOT_FOUND,
            error: "not_found".to_string(),
            message: message.into(),
            details: None,
        }
    }

    pub fn unauthorized(message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::UNAUTHORIZED,
            error: "unauthorized".to_string(),
            message: message.into(),
            details: None,
        }
    }

    pub fn internal(message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::INTERNAL_SERVER_ERROR,
            error: "internal_error".to_string(),
            message: message.into(),
            details: None,
        }
    }

    pub fn validation(errors: Vec<ValidationError>) -> Self {
        Self {
            status: StatusCode::UNPROCESSABLE_ENTITY,
            error: "validation_error".to_string(),
            message: "Validation failed".to_string(),
            details: Some(errors),
        }
    }
}
```

Handlers become clean:

```rust
async fn get_user(
    State(state): State<AppState>,
    Path(id): Path<i64>,
) -> Result<Json<User>, AppError> {
    let user = state.db.find_user(id)
        .await
        .map_err(|_| AppError::internal("Failed to query user"))?
        .ok_or_else(|| AppError::not_found(format!("User {} not found", id)))?;

    Ok(Json(user))
}
```

## Catching Extractor Failures

Remember that ugly default error when JSON deserialization fails? We can intercept that by creating a custom JSON extractor that produces our `AppError` format.

```rust
use axum::{
    async_trait,
    extract::{FromRequest, Request, rejection::JsonRejection},
    Json,
};
use serde::de::DeserializeOwned;

pub struct ValidJson<T>(pub T);

#[async_trait]
impl<S, T> FromRequest<S> for ValidJson<T>
where
    T: DeserializeOwned,
    S: Send + Sync,
{
    type Rejection = AppError;

    async fn from_request(req: Request, state: &S) -> Result<Self, Self::Rejection> {
        match Json::<T>::from_request(req, state).await {
            Ok(Json(value)) => Ok(ValidJson(value)),
            Err(rejection) => {
                let message = match rejection {
                    JsonRejection::JsonDataError(err) => {
                        format!("Invalid JSON data: {}", err.body_text())
                    }
                    JsonRejection::JsonSyntaxError(_) => {
                        "Malformed JSON".to_string()
                    }
                    JsonRejection::MissingJsonContentType(_) => {
                        "Content-Type must be application/json".to_string()
                    }
                    _ => "Unknown JSON error".to_string(),
                };
                Err(AppError::bad_request(message))
            }
        }
    }
}
```

Now use `ValidJson<T>` instead of `Json<T>` in your handlers:

```rust
async fn create_user(
    State(state): State<AppState>,
    ValidJson(input): ValidJson<CreateUserInput>,
) -> Result<(StatusCode, Json<User>), AppError> {
    // If we get here, JSON parsing succeeded
    // ...
}
```

Malformed JSON now returns:

```json
{
  "error": "bad_request",
  "message": "Invalid JSON data: missing field `name` at line 1 column 12",
  "details": null
}
```

Much better.

## Field-Level Validation with the validator Crate

Deserialization catches structural problems — missing fields, wrong types. But it doesn't catch *business rule* violations. An email field that contains "not-an-email" deserializes just fine as a `String`. You need validation.

The `validator` crate is the standard solution:

```toml
[dependencies]
validator = { version = "0.18", features = ["derive"] }
```

```rust
use validator::Validate;
use serde::Deserialize;

#[derive(Deserialize, Validate)]
pub struct CreateUserInput {
    #[validate(email(message = "must be a valid email address"))]
    pub email: String,

    #[validate(length(min = 1, max = 100, message = "must be between 1 and 100 characters"))]
    pub name: String,

    #[validate(range(min = 0, max = 150, message = "must be between 0 and 150"))]
    pub age: Option<u32>,

    #[validate(length(min = 8, message = "must be at least 8 characters"))]
    pub password: String,

    #[validate(url(message = "must be a valid URL"))]
    pub website: Option<String>,
}
```

Available validators include: `email`, `url`, `length`, `range`, `must_match` (for password confirmation), `contains`, `does_not_contain`, `regex`, and `custom` for anything else.

### Integrating Validation into the Extractor

Let's extend our custom JSON extractor to run validation automatically:

```rust
use validator::Validate;

pub struct ValidatedJson<T>(pub T);

#[async_trait]
impl<S, T> FromRequest<S> for ValidatedJson<T>
where
    T: DeserializeOwned + Validate,
    S: Send + Sync,
{
    type Rejection = AppError;

    async fn from_request(req: Request, state: &S) -> Result<Self, Self::Rejection> {
        // First, parse the JSON
        let Json(value) = Json::<T>::from_request(req, state)
            .await
            .map_err(|rejection| {
                let message = match rejection {
                    JsonRejection::JsonDataError(err) => {
                        format!("Invalid JSON: {}", err.body_text())
                    }
                    JsonRejection::JsonSyntaxError(_) => "Malformed JSON".to_string(),
                    JsonRejection::MissingJsonContentType(_) => {
                        "Content-Type must be application/json".to_string()
                    }
                    _ => "JSON parsing error".to_string(),
                };
                AppError::bad_request(message)
            })?;

        // Then, validate
        value.validate().map_err(|e| {
            let errors: Vec<ValidationError> = e
                .field_errors()
                .into_iter()
                .flat_map(|(field, errors)| {
                    errors.iter().map(move |error| ValidationError {
                        field: field.to_string(),
                        message: error
                            .message
                            .as_ref()
                            .map(|m| m.to_string())
                            .unwrap_or_else(|| format!("invalid value for {}", field)),
                    })
                })
                .collect();
            AppError::validation(errors)
        })?;

        Ok(ValidatedJson(value))
    }
}
```

Usage in handlers is seamless:

```rust
async fn create_user(
    State(state): State<AppState>,
    ValidatedJson(input): ValidatedJson<CreateUserInput>,
) -> Result<(StatusCode, Json<User>), AppError> {
    // If we get here:
    // 1. JSON was valid
    // 2. All fields passed validation
    // We can trust the data

    let user = state.db.create_user(input).await
        .map_err(|e| AppError::internal("Failed to create user"))?;

    Ok((StatusCode::CREATED, Json(user)))
}
```

Submit bad data:

```bash
curl -X POST http://localhost:3000/api/users \
  -H "Content-Type: application/json" \
  -d '{"email": "not-an-email", "name": "", "password": "short", "age": 200}'
```

Response:

```json
{
  "error": "validation_error",
  "message": "Validation failed",
  "details": [
    { "field": "email", "message": "must be a valid email address" },
    { "field": "name", "message": "must be between 1 and 100 characters" },
    { "field": "password", "message": "must be at least 8 characters" },
    { "field": "age", "message": "must be between 0 and 150" }
  ]
}
```

All errors returned at once — not one at a time. This is important for UX. Nobody wants to fix one field, submit, get another error, fix that, submit again.

## Custom Validators

The built-in validators cover common cases, but you'll need custom logic. Maybe usernames can't contain spaces, or dates must be in the future.

```rust
use validator::ValidationError as ValidatorError;

fn validate_username(username: &str) -> Result<(), ValidatorError> {
    if username.contains(' ') {
        return Err(ValidatorError::new("no_spaces")
            .with_message("username cannot contain spaces".into()));
    }
    if username.starts_with('_') {
        return Err(ValidatorError::new("no_leading_underscore")
            .with_message("username cannot start with an underscore".into()));
    }
    // Check for reserved names
    let reserved = ["admin", "root", "system", "api"];
    if reserved.contains(&username.to_lowercase().as_str()) {
        return Err(ValidatorError::new("reserved")
            .with_message("this username is reserved".into()));
    }
    Ok(())
}

#[derive(Deserialize, Validate)]
pub struct CreateUserInput {
    #[validate(custom(function = "validate_username"))]
    pub username: String,

    #[validate(email)]
    pub email: String,
}
```

For validation that requires database lookups (checking if an email is already taken), don't put it in the validator. That belongs in the handler or service layer — validators should be pure functions.

## Converting External Errors

Your handlers will call libraries that return their own error types — database errors, HTTP client errors, serialization errors. You need to convert these to `AppError`.

```rust
// Implement From<T> for common error types
impl From<sqlx::Error> for AppError {
    fn from(err: sqlx::Error) -> Self {
        match err {
            sqlx::Error::RowNotFound => {
                AppError::not_found("Resource not found")
            }
            sqlx::Error::Database(db_err) => {
                // Check for unique constraint violations
                if db_err.is_unique_violation() {
                    AppError::bad_request("A record with this value already exists")
                } else {
                    tracing::error!("Database error: {:?}", db_err);
                    AppError::internal("Database error")
                }
            }
            _ => {
                tracing::error!("Unexpected database error: {:?}", err);
                AppError::internal("Database error")
            }
        }
    }
}
```

Now `?` works directly with SQLx errors:

```rust
async fn get_user(
    State(state): State<AppState>,
    Path(id): Path<i64>,
) -> Result<Json<User>, AppError> {
    let user = sqlx::query_as!(User, "SELECT * FROM users WHERE id = $1", id)
        .fetch_one(&state.db)
        .await?; // Automatically converts sqlx::Error to AppError

    Ok(Json(user))
}
```

No manual `.map_err()` needed. The `From` implementation handles the conversion, and you only log internal details — clients get sanitized messages.

## Query Parameter Validation

Don't forget query parameters. The same pattern works:

```rust
#[derive(Deserialize, Validate)]
pub struct ListParams {
    #[validate(range(min = 1, max = 1000))]
    pub page: Option<u32>,

    #[validate(range(min = 1, max = 100))]
    pub per_page: Option<u32>,

    pub sort_by: Option<SortField>,
    pub sort_order: Option<SortOrder>,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SortField {
    Name,
    Email,
    CreatedAt,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SortOrder {
    Asc,
    Desc,
}
```

Using an enum for `sort_by` means invalid values get caught at deserialization time. Someone sends `?sort_by=hackme` and serde rejects it before your code ever runs. No SQL injection through sort parameters — Rust's type system prevents it structurally.

## Putting It All Together

Here's a complete module structure for error handling and validation:

```rust
// src/error.rs
use axum::{
    http::StatusCode,
    response::{IntoResponse, Response},
    Json,
};
use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct AppError {
    #[serde(skip)]
    pub status: StatusCode,
    pub error: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub details: Option<Vec<FieldError>>,
}

#[derive(Debug, Serialize)]
pub struct FieldError {
    pub field: String,
    pub message: String,
}

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        let status = self.status;
        let body = Json(self);
        (status, body).into_response()
    }
}

impl AppError {
    pub fn bad_request(msg: impl Into<String>) -> Self {
        Self { status: StatusCode::BAD_REQUEST, error: "bad_request".into(), message: msg.into(), details: None }
    }

    pub fn not_found(msg: impl Into<String>) -> Self {
        Self { status: StatusCode::NOT_FOUND, error: "not_found".into(), message: msg.into(), details: None }
    }

    pub fn unauthorized(msg: impl Into<String>) -> Self {
        Self { status: StatusCode::UNAUTHORIZED, error: "unauthorized".into(), message: msg.into(), details: None }
    }

    pub fn forbidden(msg: impl Into<String>) -> Self {
        Self { status: StatusCode::FORBIDDEN, error: "forbidden".into(), message: msg.into(), details: None }
    }

    pub fn conflict(msg: impl Into<String>) -> Self {
        Self { status: StatusCode::CONFLICT, error: "conflict".into(), message: msg.into(), details: None }
    }

    pub fn internal(msg: impl Into<String>) -> Self {
        Self { status: StatusCode::INTERNAL_SERVER_ERROR, error: "internal_error".into(), message: msg.into(), details: None }
    }

    pub fn validation(errors: Vec<FieldError>) -> Self {
        Self { status: StatusCode::UNPROCESSABLE_ENTITY, error: "validation_error".into(), message: "Validation failed".into(), details: Some(errors) }
    }
}
```

Every error from your API is now structured, consistent, and safe. No internal type names, no stack traces, no surprise plain-text responses. Your frontend team will thank you.

Next up: authentication — JWT tokens, sessions, and OAuth. The part where you get to be paranoid about security.
