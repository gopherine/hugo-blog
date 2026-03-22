---
title: "Lesson 7: Feature Flags at the Type Level — Compile-time feature control"
author: Atharva Pandey
series: "Rust in Production — Real-World Architecture"
lesson: 7
keywords: [Rust, rust, architecture, production, feature flags, conditional compilation, cfg, cargo features]
tags: [Rust tutorial, rust, architecture, production]
date: '2025-10-28T13:19:00.000Z'
---

We had a feature that was ready for staging but absolutely not ready for production. In my previous Go gig, we'd have used a runtime feature flag service — LaunchDarkly or similar. Evaluate a boolean at request time, show the new code path to internal testers, hide it from everyone else.

In Rust, we had another option. We could decide *at compile time* whether the feature existed in the binary at all. Not a runtime check. Not a boolean. The code literally wasn't in the production binary. You couldn't accidentally enable it. You couldn't exploit it. It didn't exist.

That's the power of Cargo features and conditional compilation. And when you combine them with Rust's type system, you get something genuinely unique — feature flags that the compiler enforces.

## Cargo Features — The Basics

Cargo features are declared in `Cargo.toml` and control conditional compilation:

```toml
# Cargo.toml
[features]
default = ["postgres"]

# Infrastructure backends
postgres = ["sqlx/postgres"]
sqlite = ["sqlx/sqlite"]

# Optional capabilities
redis-cache = ["redis"]
s3-storage = ["aws-sdk-s3"]
metrics = ["prometheus"]

# Experimental features
experimental-graphql = ["async-graphql"]
admin-panel = []
```

In your code, you use `#[cfg(feature = "...")]` to conditionally compile:

```rust
// src/infra/mod.rs

#[cfg(feature = "postgres")]
pub mod postgres;

#[cfg(feature = "sqlite")]
pub mod sqlite;

#[cfg(feature = "redis-cache")]
pub mod redis;

#[cfg(feature = "s3-storage")]
pub mod s3;
```

Build with specific features:

```bash
# Development build with SQLite and no external services
cargo build --features sqlite

# Production build with everything
cargo build --features "postgres,redis-cache,s3-storage,metrics"

# Build with default features disabled
cargo build --no-default-features --features sqlite
```

The unused code isn't just hidden — it's not compiled. Not in the binary. Not in memory. Not a potential attack surface.

## Feature-Gated Modules

Here's how I structure a service that can run with different storage backends:

```rust
// src/infra/storage.rs

use crate::domain::ports::storage::{StorageBackend, StorageError};
use async_trait::async_trait;

#[cfg(feature = "s3-storage")]
mod s3_backend {
    use super::*;
    use aws_sdk_s3::Client;

    pub struct S3Storage {
        client: Client,
        bucket: String,
    }

    impl S3Storage {
        pub async fn new(bucket: String) -> Result<Self, StorageError> {
            let config = aws_config::load_defaults(aws_config::BehaviorVersion::latest()).await;
            let client = Client::new(&config);
            Ok(Self { client, bucket })
        }
    }

    #[async_trait]
    impl StorageBackend for S3Storage {
        async fn put(&self, key: &str, data: &[u8]) -> Result<(), StorageError> {
            self.client
                .put_object()
                .bucket(&self.bucket)
                .key(key)
                .body(data.to_vec().into())
                .send()
                .await
                .map_err(|e| StorageError::Write(e.to_string()))?;
            Ok(())
        }

        async fn get(&self, key: &str) -> Result<Vec<u8>, StorageError> {
            let response = self.client
                .get_object()
                .bucket(&self.bucket)
                .key(key)
                .send()
                .await
                .map_err(|e| StorageError::Read(e.to_string()))?;

            let bytes = response.body.collect().await
                .map_err(|e| StorageError::Read(e.to_string()))?;
            Ok(bytes.into_bytes().to_vec())
        }

        async fn delete(&self, key: &str) -> Result<(), StorageError> {
            self.client
                .delete_object()
                .bucket(&self.bucket)
                .key(key)
                .send()
                .await
                .map_err(|e| StorageError::Delete(e.to_string()))?;
            Ok(())
        }
    }
}

#[cfg(feature = "s3-storage")]
pub use s3_backend::S3Storage;

// Local filesystem fallback — always available
pub struct LocalStorage {
    base_path: std::path::PathBuf,
}

impl LocalStorage {
    pub fn new(base_path: impl Into<std::path::PathBuf>) -> Self {
        Self { base_path: base_path.into() }
    }
}

#[async_trait]
impl StorageBackend for LocalStorage {
    async fn put(&self, key: &str, data: &[u8]) -> Result<(), StorageError> {
        let path = self.base_path.join(key);
        if let Some(parent) = path.parent() {
            tokio::fs::create_dir_all(parent).await
                .map_err(|e| StorageError::Write(e.to_string()))?;
        }
        tokio::fs::write(&path, data).await
            .map_err(|e| StorageError::Write(e.to_string()))?;
        Ok(())
    }

    async fn get(&self, key: &str) -> Result<Vec<u8>, StorageError> {
        let path = self.base_path.join(key);
        tokio::fs::read(&path).await
            .map_err(|e| StorageError::Read(e.to_string()))
    }

    async fn delete(&self, key: &str) -> Result<(), StorageError> {
        let path = self.base_path.join(key);
        tokio::fs::remove_file(&path).await
            .map_err(|e| StorageError::Delete(e.to_string()))
    }
}
```

## Startup Wiring with Feature Gates

The composition root handles feature-based wiring:

```rust
// src/startup.rs

use crate::domain::ports::storage::StorageBackend;

pub async fn build_storage(config: &StorageConfig) -> Box<dyn StorageBackend> {
    match config.backend.as_str() {
        #[cfg(feature = "s3-storage")]
        "s3" => {
            let storage = crate::infra::storage::S3Storage::new(
                config.s3_bucket.clone().expect("S3_BUCKET required")
            ).await.expect("failed to initialize S3");
            Box::new(storage)
        }

        "local" | _ => {
            let path = config.local_path.as_deref().unwrap_or("/tmp/storage");
            Box::new(crate::infra::storage::LocalStorage::new(path))
        }
    }
}
```

If you compile without `s3-storage`, the `"s3"` arm doesn't exist. If someone sets `STORAGE_BACKEND=s3` in an environment where the feature isn't compiled, they'll hit the `_` fallback or — better — you add a compile-time check:

```rust
#[cfg(not(feature = "s3-storage"))]
"s3" => {
    panic!("S3 storage requested but the 's3-storage' feature is not enabled. \
            Rebuild with: cargo build --features s3-storage");
}
```

Clear, immediate, unambiguous.

## Compile-Time Feature Flags for Business Logic

This is where things get interesting. You can use features to gate entire business capabilities:

```rust
// src/api/routes.rs

pub fn create_router(state: AppState) -> Router {
    let mut router = Router::new()
        .route("/health", get(health_check))
        .nest("/api/v1", v1_routes(state.clone()));

    #[cfg(feature = "admin-panel")]
    {
        router = router.nest("/admin", admin_routes(state.clone()));
    }

    #[cfg(feature = "experimental-graphql")]
    {
        router = router.route("/graphql", post(graphql_handler))
            .route("/graphql/playground", get(graphql_playground));
    }

    #[cfg(feature = "metrics")]
    {
        router = router.route("/metrics", get(prometheus_metrics));
    }

    router.with_state(state)
}
```

Your production binary: `cargo build --release --features "postgres,redis-cache,metrics"`. No admin panel. No experimental GraphQL. No attack surface for features that aren't ready.

Your staging binary: `cargo build --features "postgres,redis-cache,metrics,admin-panel,experimental-graphql"`. Everything enabled for testing.

Your local dev binary: `cargo build --features sqlite`. Minimal dependencies, fast compile, runs without Docker.

## Type-Level Feature Flags

Here's a pattern I really like — using phantom types to make feature access compile-time safe:

```rust
use std::marker::PhantomData;

// Feature markers — zero-sized types
pub struct PremiumEnabled;
pub struct BasicOnly;

pub struct ApiClient<F> {
    base_url: String,
    api_key: String,
    _feature: PhantomData<F>,
}

impl<F> ApiClient<F> {
    // Methods available to all tiers
    pub async fn get_profile(&self, user_id: &str) -> Result<Profile, ApiError> {
        // ... basic API call
        todo!()
    }

    pub async fn list_items(&self, limit: u32) -> Result<Vec<Item>, ApiError> {
        // ... basic API call
        todo!()
    }
}

impl ApiClient<PremiumEnabled> {
    // Methods ONLY available to premium tier
    pub async fn bulk_export(&self, query: ExportQuery) -> Result<ExportResult, ApiError> {
        // ... expensive operation
        todo!()
    }

    pub async fn analytics(&self, range: DateRange) -> Result<AnalyticsReport, ApiError> {
        // ... premium-only feature
        todo!()
    }

    pub async fn custom_webhooks(&self, config: WebhookConfig) -> Result<(), ApiError> {
        // ... premium-only feature
        todo!()
    }
}

// Construction controls which features are available
pub fn create_basic_client(base_url: String, api_key: String) -> ApiClient<BasicOnly> {
    ApiClient {
        base_url,
        api_key,
        _feature: PhantomData,
    }
}

pub fn create_premium_client(base_url: String, api_key: String) -> ApiClient<PremiumEnabled> {
    ApiClient {
        base_url,
        api_key,
        _feature: PhantomData,
    }
}
```

Now if someone tries to call `.bulk_export()` on a basic client, it's a compile error — not a runtime error, not a 403, not a "please upgrade" message. The method doesn't exist on that type.

## Combining Cfg Attributes

You can compose feature conditions:

```rust
// Only compile if BOTH features are enabled
#[cfg(all(feature = "postgres", feature = "redis-cache"))]
pub mod cached_pg_repo {
    // Postgres-backed repository with Redis caching
}

// Compile if EITHER feature is enabled
#[cfg(any(feature = "postgres", feature = "sqlite"))]
pub fn run_migrations() {
    // ...
}

// Compile if feature is NOT enabled
#[cfg(not(feature = "metrics"))]
pub fn record_metric(_name: &str, _value: f64) {
    // no-op when metrics aren't enabled
}

#[cfg(feature = "metrics")]
pub fn record_metric(name: &str, value: f64) {
    prometheus::histogram!(name, value);
}
```

This is powerful but be careful — complex `cfg` conditions become hard to reason about. I try to keep them to single features or simple `all`/`any` combinations.

## Feature-Specific Error Types

When features add capabilities, they often add error variants. Handle this cleanly:

```rust
#[derive(Debug, thiserror::Error)]
pub enum AppError {
    #[error("domain error: {0}")]
    Domain(#[from] DomainError),

    #[error("database error: {0}")]
    Database(String),

    #[cfg(feature = "redis-cache")]
    #[error("cache error: {0}")]
    Cache(String),

    #[cfg(feature = "s3-storage")]
    #[error("storage error: {0}")]
    Storage(String),
}

impl axum::response::IntoResponse for AppError {
    fn into_response(self) -> axum::response::Response {
        let (status, message) = match &self {
            AppError::Domain(e) => (StatusCode::BAD_REQUEST, e.to_string()),
            AppError::Database(e) => (StatusCode::INTERNAL_SERVER_ERROR, e.clone()),

            #[cfg(feature = "redis-cache")]
            AppError::Cache(e) => {
                tracing::warn!("cache error (non-fatal): {}", e);
                (StatusCode::INTERNAL_SERVER_ERROR, "internal error".to_string())
            }

            #[cfg(feature = "s3-storage")]
            AppError::Storage(e) => {
                (StatusCode::INTERNAL_SERVER_ERROR, e.clone())
            }
        };

        (status, Json(serde_json::json!({"error": message}))).into_response()
    }
}
```

## Testing Feature Combinations

This is the part people forget. You need CI that tests different feature combinations:

```yaml
# .github/workflows/ci.yml
jobs:
  test:
    strategy:
      matrix:
        features:
          - "postgres"
          - "sqlite"
          - "postgres,redis-cache"
          - "postgres,redis-cache,s3-storage,metrics"
          - "postgres,admin-panel,experimental-graphql"
    steps:
      - uses: actions/checkout@v4
      - run: cargo test --features "${{ matrix.features }}"
      - run: cargo clippy --features "${{ matrix.features }}" -- -D warnings
```

Every feature combination that you ship must be tested. Otherwise you'll discover at deploy time that feature X doesn't compile without feature Y — a dependency you didn't realize existed.

Also, use `cargo hack` for exhaustive checking:

```bash
# Test every feature individually
cargo hack test --each-feature

# Test all feature combinations (expensive but thorough)
cargo hack test --feature-powerset

# Check that every feature compiles independently
cargo hack check --each-feature --no-dev-deps
```

## Runtime vs Compile-Time: When to Use Which

Compile-time feature flags (Cargo features) are great for:
- **Infrastructure swapping** — different databases, caches, cloud providers
- **Build profiles** — dev vs staging vs production capabilities
- **Optional dependencies** — don't pull in AWS SDK if you don't need it
- **Security hardening** — remove admin endpoints from production binaries

Runtime feature flags (LaunchDarkly, environment variables) are better for:
- **Gradual rollouts** — 5% of users see the new checkout flow
- **Kill switches** — disable a feature without redeploying
- **A/B testing** — compare two implementations with real traffic
- **User-specific features** — premium vs free tier at request time

Use both. Compile-time flags decide what *can* exist in the binary. Runtime flags decide what *does* execute for a given request. They're complementary, not competing.

```rust
pub async fn handle_request(
    State(state): State<AppState>,
    request: Request,
) -> Response {
    // Compile-time: this code only exists if the feature is compiled in
    #[cfg(feature = "new-pricing-engine")]
    {
        // Runtime: check if this user should see the new pricing
        if state.feature_flags.is_enabled("new-pricing", &user_id) {
            return new_pricing::handle(request).await;
        }
    }

    // Fallback to existing behavior
    legacy_pricing::handle(request).await
}
```

The layering is clean. The new pricing engine isn't even in the binary unless you compile it in. And even when it is, it only activates for users the runtime flag system selects.

Next: migrating services from Go, Python, and Java to Rust — when it makes sense, when it doesn't, and how to do it without losing your mind.
