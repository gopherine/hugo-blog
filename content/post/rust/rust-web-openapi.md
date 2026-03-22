---
title: "Lesson 10: OpenAPI / Swagger Generation — Documentation from code"
author: Atharva Pandey
series: "Rust Web & API Development"
lesson: 10
keywords: [Rust, rust, web, API, axum, OpenAPI, Swagger, utoipa, documentation, API docs]
tags: [Rust tutorial, rust, web, api]
date: '2024-10-21T10:05:00.000Z'
---

I've never seen a team maintain a separate OpenAPI spec in sync with their actual API for more than three months. Someone adds a field, forgets to update the docs, and suddenly the spec says one thing and the API does another. The only API documentation that stays accurate is documentation generated from the code itself. If the code changes, the docs change. No human discipline required.

## The Approach: utoipa

`utoipa` is the go-to crate for generating OpenAPI specs from Rust code. It uses derive macros and attribute annotations to produce an OpenAPI 3.1 JSON spec at compile time. You add annotations to your types and handlers, and utoipa generates a spec that's always in sync with your code.

```toml
[dependencies]
utoipa = { version = "4", features = ["axum_extras"] }
utoipa-swagger-ui = { version = "7", features = ["axum"] }
```

## Documenting Types

Start by annotating your request and response types with `ToSchema`:

```rust
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;

/// A user in the system
#[derive(Serialize, Deserialize, ToSchema)]
pub struct User {
    /// Unique identifier
    #[schema(example = 1)]
    pub id: i64,

    /// Email address
    #[schema(example = "alice@example.com")]
    pub email: String,

    /// Display name
    #[schema(example = "Alice Smith")]
    pub name: String,

    /// User role
    #[schema(example = "user", default = "user")]
    pub role: String,

    /// Account creation timestamp
    pub created_at: chrono::DateTime<chrono::Utc>,
}

/// Input for creating a new user
#[derive(Deserialize, ToSchema)]
pub struct CreateUserInput {
    /// Must be a valid email address
    #[schema(example = "bob@example.com")]
    pub email: String,

    /// Must be between 1 and 100 characters
    #[schema(example = "Bob Jones", min_length = 1, max_length = 100)]
    pub name: String,

    /// Must be at least 8 characters
    #[schema(example = "securepass123", min_length = 8)]
    pub password: String,
}

/// Input for updating user fields (all optional)
#[derive(Deserialize, ToSchema)]
pub struct UpdateUserInput {
    #[schema(example = "newemail@example.com")]
    pub email: Option<String>,

    #[schema(example = "New Name")]
    pub name: Option<String>,
}
```

The `#[schema]` attributes add examples, constraints, and descriptions that appear in the generated documentation. These don't affect runtime behavior — they're purely for the spec.

## Documenting Endpoints

Annotate your handlers with `#[utoipa::path]`:

```rust
use axum::{
    extract::{Path, Query, State, Json},
    http::StatusCode,
};

/// List all users with pagination
#[utoipa::path(
    get,
    path = "/api/users",
    params(
        ("page" = Option<i64>, Query, description = "Page number (default: 1)"),
        ("per_page" = Option<i64>, Query, description = "Items per page (default: 20, max: 100)"),
        ("sort_by" = Option<String>, Query, description = "Sort field: name, email, created_at"),
    ),
    responses(
        (status = 200, description = "List of users", body = PaginatedResponse<User>),
        (status = 500, description = "Internal server error", body = AppError),
    ),
    tag = "Users"
)]
async fn list_users(
    State(state): State<AppState>,
    Query(params): Query<PaginationParams>,
) -> Result<Json<PaginatedResponse<User>>, AppError> {
    // ... implementation
    todo!()
}

/// Get a single user by ID
#[utoipa::path(
    get,
    path = "/api/users/{id}",
    params(
        ("id" = i64, Path, description = "User ID"),
    ),
    responses(
        (status = 200, description = "User found", body = User),
        (status = 404, description = "User not found", body = AppError),
    ),
    tag = "Users"
)]
async fn get_user(
    State(state): State<AppState>,
    Path(id): Path<i64>,
) -> Result<Json<User>, AppError> {
    // ... implementation
    todo!()
}

/// Create a new user
#[utoipa::path(
    post,
    path = "/api/users",
    request_body = CreateUserInput,
    responses(
        (status = 201, description = "User created", body = User),
        (status = 409, description = "Email already exists", body = AppError),
        (status = 422, description = "Validation error", body = AppError),
    ),
    tag = "Users"
)]
async fn create_user(
    State(state): State<AppState>,
    Json(input): Json<CreateUserInput>,
) -> Result<(StatusCode, Json<User>), AppError> {
    // ... implementation
    todo!()
}

/// Update an existing user
#[utoipa::path(
    put,
    path = "/api/users/{id}",
    params(
        ("id" = i64, Path, description = "User ID"),
    ),
    request_body = UpdateUserInput,
    responses(
        (status = 200, description = "User updated", body = User),
        (status = 404, description = "User not found", body = AppError),
        (status = 422, description = "Validation error", body = AppError),
    ),
    security(("bearer_auth" = [])),
    tag = "Users"
)]
async fn update_user(
    State(state): State<AppState>,
    Path(id): Path<i64>,
    Json(input): Json<UpdateUserInput>,
) -> Result<Json<User>, AppError> {
    // ... implementation
    todo!()
}

/// Delete a user
#[utoipa::path(
    delete,
    path = "/api/users/{id}",
    params(
        ("id" = i64, Path, description = "User ID"),
    ),
    responses(
        (status = 204, description = "User deleted"),
        (status = 404, description = "User not found", body = AppError),
    ),
    security(("bearer_auth" = [])),
    tag = "Users"
)]
async fn delete_user(
    State(state): State<AppState>,
    Path(id): Path<i64>,
) -> Result<StatusCode, AppError> {
    // ... implementation
    todo!()
}
```

The `tag` groups endpoints in the Swagger UI. The `security` attribute marks endpoints that require authentication.

## Documenting Error Responses

Make your error type documentable:

```rust
use utoipa::ToSchema;

#[derive(Serialize, ToSchema)]
pub struct AppError {
    /// Error type identifier
    #[schema(example = "not_found")]
    pub error: String,

    /// Human-readable error message
    #[schema(example = "User not found")]
    pub message: String,

    /// Field-level validation errors (when applicable)
    pub details: Option<Vec<FieldError>>,
}

#[derive(Serialize, ToSchema)]
pub struct FieldError {
    /// The field that failed validation
    #[schema(example = "email")]
    pub field: String,

    /// Description of the validation failure
    #[schema(example = "must be a valid email address")]
    pub message: String,
}
```

## Documenting Pagination

```rust
#[derive(Serialize, ToSchema)]
pub struct PaginatedResponse<T: ToSchema> {
    /// The page of results
    pub data: Vec<T>,
    /// Pagination metadata
    pub pagination: PaginationMeta,
}

#[derive(Serialize, ToSchema)]
pub struct PaginationMeta {
    /// Total number of items across all pages
    #[schema(example = 150)]
    pub total: i64,

    /// Current page number
    #[schema(example = 1)]
    pub page: i64,

    /// Items per page
    #[schema(example = 20)]
    pub per_page: i64,

    /// Total number of pages
    #[schema(example = 8)]
    pub total_pages: i64,

    /// Whether there is a next page
    pub has_next: bool,

    /// Whether there is a previous page
    pub has_prev: bool,
}
```

## Assembling the API Doc

Create the OpenAPI spec by listing all your endpoints and types:

```rust
use utoipa::OpenApi;

#[derive(OpenApi)]
#[openapi(
    info(
        title = "My API",
        version = "1.0.0",
        description = "REST API for user management",
        contact(
            name = "API Support",
            email = "api@example.com",
        ),
    ),
    paths(
        list_users,
        get_user,
        create_user,
        update_user,
        delete_user,
        login,
    ),
    components(
        schemas(
            User,
            CreateUserInput,
            UpdateUserInput,
            AppError,
            FieldError,
            PaginatedResponse<User>,
            PaginationMeta,
            LoginInput,
            LoginResponse,
        ),
    ),
    modifiers(&SecurityAddon),
    tags(
        (name = "Users", description = "User management endpoints"),
        (name = "Auth", description = "Authentication endpoints"),
    ),
)]
struct ApiDoc;

// Add JWT bearer security scheme
struct SecurityAddon;

impl utoipa::Modify for SecurityAddon {
    fn modify(&self, openapi: &mut utoipa::openapi::OpenApi) {
        if let Some(components) = openapi.components.as_mut() {
            components.add_security_scheme(
                "bearer_auth",
                utoipa::openapi::security::SecurityScheme::Http(
                    utoipa::openapi::security::Http::new(
                        utoipa::openapi::security::HttpAuthScheme::Bearer,
                    ),
                ),
            );
        }
    }
}
```

## Serving Swagger UI

Now serve the interactive docs:

```rust
use utoipa_swagger_ui::SwaggerUi;

let app = Router::new()
    .route("/api/users", get(list_users).post(create_user))
    .route("/api/users/:id", get(get_user).put(update_user).delete(delete_user))
    .route("/api/login", post(login))
    .merge(
        SwaggerUi::new("/docs")
            .url("/api-docs/openapi.json", ApiDoc::openapi()),
    )
    .with_state(state);
```

Now visit `http://localhost:3000/docs` — you get a fully interactive Swagger UI where you can:

- Browse all endpoints grouped by tag
- See request/response schemas with examples
- Try out API calls directly from the browser
- See authentication requirements
- Download the raw OpenAPI JSON at `/api-docs/openapi.json`

## Serving the Raw JSON Spec

If you want the spec available without the Swagger UI (for code generators, other tools):

```rust
async fn openapi_json() -> Json<utoipa::openapi::OpenApi> {
    Json(ApiDoc::openapi())
}

let app = Router::new()
    .route("/openapi.json", get(openapi_json))
    // ... other routes
```

## Documenting Auth Endpoints

```rust
/// Login with email and password
#[utoipa::path(
    post,
    path = "/api/login",
    request_body = LoginInput,
    responses(
        (status = 200, description = "Login successful", body = LoginResponse),
        (status = 401, description = "Invalid credentials", body = AppError),
        (status = 429, description = "Too many login attempts", body = AppError),
    ),
    tag = "Auth"
)]
async fn login(
    State(state): State<AppState>,
    Json(input): Json<LoginInput>,
) -> Result<Json<LoginResponse>, AppError> {
    // ... implementation
    todo!()
}

#[derive(Deserialize, ToSchema)]
struct LoginInput {
    /// User's email address
    #[schema(example = "alice@example.com")]
    email: String,

    /// User's password
    #[schema(example = "password123")]
    password: String,
}

#[derive(Serialize, ToSchema)]
struct LoginResponse {
    /// JWT access token
    #[schema(example = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...")]
    token: String,

    /// Token type (always "Bearer")
    #[schema(example = "Bearer")]
    token_type: String,

    /// Token lifetime in seconds
    #[schema(example = 3600)]
    expires_in: i64,
}
```

## Enum Documentation

Enums render nicely in OpenAPI:

```rust
#[derive(Serialize, Deserialize, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum UserRole {
    /// Regular user with basic permissions
    User,
    /// Moderator with content management permissions
    Moderator,
    /// Administrator with full access
    Admin,
}

#[derive(Serialize, Deserialize, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum SortOrder {
    Asc,
    Desc,
}
```

In the generated spec, these show up as string enums with allowed values — Swagger UI renders them as dropdown selectors in the "Try it out" forms.

## Conditional Documentation

You probably don't want Swagger UI in production. Use a feature flag:

```toml
[features]
swagger = ["utoipa-swagger-ui"]

[dependencies]
utoipa-swagger-ui = { version = "7", features = ["axum"], optional = true }
```

```rust
fn build_router(state: AppState) -> Router {
    let router = Router::new()
        .route("/api/users", get(list_users).post(create_user))
        .route("/api/users/:id", get(get_user).put(update_user).delete(delete_user));

    #[cfg(feature = "swagger")]
    let router = router.merge(
        SwaggerUi::new("/docs")
            .url("/api-docs/openapi.json", ApiDoc::openapi()),
    );

    router.with_state(state)
}
```

Build with docs for development:

```bash
cargo run --features swagger
```

Build without for production:

```bash
cargo build --release
```

## Generating Client SDKs

The OpenAPI JSON spec isn't just for documentation — it's a machine-readable contract. You can generate client libraries from it:

```bash
# Generate a TypeScript client
npx openapi-typescript-codegen \
  --input http://localhost:3000/api-docs/openapi.json \
  --output ./frontend/src/api \
  --client axios

# Generate a Python client
openapi-generator-cli generate \
  -i http://localhost:3000/api-docs/openapi.json \
  -g python \
  -o ./python-client
```

This is the real payoff. Your frontend team gets type-safe API clients generated from the same source of truth as the backend. No mismatches, no stale documentation, no manual type definitions.

## Practical Advice

**Annotate everything from the start.** Retrofitting utoipa annotations to an existing API is tedious. Adding them as you write each handler takes seconds.

**Use descriptive examples.** A schema that says `email: string` is less useful than one that says `email: string, example: "alice@example.com"`. Good examples make the Swagger UI "Try it out" feature actually usable.

**Document error responses consistently.** Every endpoint should list its possible error codes. Frontend developers need to know what to catch.

**Version your API in the spec.** When you make breaking changes, update the version number. Clients that parse the spec programmatically can detect incompatible changes.

**Don't document internal endpoints.** Not every route needs to be in the public spec. Health checks, metrics endpoints, admin-only routes — keep them out of the public docs unless external consumers need them.

The overhead is minimal — a few derive macros and some attribute annotations. What you get back is documentation that's always correct, client SDKs that generate themselves, and a team that can actually use your API without reading your source code.

Next: integration testing. How to test HTTP services properly without mocking everything away.
