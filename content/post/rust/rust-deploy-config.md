---
title: "Lesson 7: Configuration — Environment, files, feature flags"
author: Atharva Pandey
series: "Rust Deployment & Operations"
lesson: 7
keywords: [Rust, rust, deployment, configuration, environment variables, config files, feature flags, twelve-factor]
tags: [Rust tutorial, rust, deployment, devops]
date: '2025-05-05T10:15:00.000Z'
---

I once shipped a service to production with the staging database URL hardcoded. Not in an environment variable — literally in the source code, in a `const`. It ran for two hours writing production data to the staging database before anyone noticed. The fix was easy. The data migration to clean up the mess took three days.

Configuration is one of those things that seems trivial until it bites you. And in Rust, we have the type system to make configuration bulletproof — but only if we structure things right. Let me walk you through the approach I've converged on after making every possible configuration mistake.

## The Twelve-Factor Approach (And Where It Falls Short)

The twelve-factor app methodology says: store config in environment variables. Period. No config files, no command-line flags, just `std::env::var("DATABASE_URL")` everywhere.

This is a good *starting point*. Environment variables work everywhere — Docker, Kubernetes, systemd, CI, your local shell. But as your service grows, pure environment variables hit limits:

- No type safety. Everything is a `String`. Is `MAX_CONNECTIONS` supposed to be `"10"` or `10`?
- No defaults. Forget to set one variable and your service crashes on startup.
- No validation. Set `PORT=banana` and find out at request time, not boot time.
- No structure. Complex configs (lists, nested objects) become ugly: `ALLOWED_ORIGINS=http://a.com,http://b.com`

The solution: use environment variables as the *source*, but deserialize them into a typed Rust struct at startup.

## The Config Struct Pattern

Here's what I use for every service:

```toml
# Cargo.toml
[dependencies]
config = "0.14"
serde = { version = "1", features = ["derive"] }
secrecy = { version = "0.10", features = ["serde"] }
```

```rust
use config::{Config, Environment, File};
use secrecy::SecretString;
use serde::Deserialize;
use std::time::Duration;

#[derive(Debug, Deserialize, Clone)]
pub struct AppConfig {
    pub server: ServerConfig,
    pub database: DatabaseConfig,
    pub auth: AuthConfig,
    pub features: FeatureFlags,
}

#[derive(Debug, Deserialize, Clone)]
pub struct ServerConfig {
    #[serde(default = "default_host")]
    pub host: String,
    #[serde(default = "default_port")]
    pub port: u16,
    #[serde(default = "default_request_timeout")]
    #[serde(with = "humantime_serde")]
    pub request_timeout: Duration,
}

#[derive(Debug, Deserialize, Clone)]
pub struct DatabaseConfig {
    pub url: SecretString,
    #[serde(default = "default_max_connections")]
    pub max_connections: u32,
    #[serde(default = "default_min_connections")]
    pub min_connections: u32,
    #[serde(default = "default_connect_timeout")]
    #[serde(with = "humantime_serde")]
    pub connect_timeout: Duration,
}

#[derive(Debug, Deserialize, Clone)]
pub struct AuthConfig {
    pub jwt_secret: SecretString,
    #[serde(default = "default_token_expiry")]
    #[serde(with = "humantime_serde")]
    pub token_expiry: Duration,
}

#[derive(Debug, Deserialize, Clone)]
pub struct FeatureFlags {
    #[serde(default)]
    pub enable_new_search: bool,
    #[serde(default)]
    pub enable_rate_limiting: bool,
    #[serde(default = "default_rate_limit")]
    pub rate_limit_per_minute: u32,
}

fn default_host() -> String { "0.0.0.0".to_string() }
fn default_port() -> u16 { 3000 }
fn default_request_timeout() -> Duration { Duration::from_secs(30) }
fn default_max_connections() -> u32 { 10 }
fn default_min_connections() -> u32 { 2 }
fn default_connect_timeout() -> Duration { Duration::from_secs(5) }
fn default_token_expiry() -> Duration { Duration::from_secs(3600) }
fn default_rate_limit() -> u32 { 100 }
```

A few design decisions here:

**`SecretString` for sensitive values.** The `secrecy` crate wraps strings so they don't accidentally get logged or printed in debug output. `Debug` for `SecretString` prints `[REDACTED]` instead of the actual value. This has saved me from leaking secrets in panic messages more than once.

**`humantime_serde` for durations.** Instead of configuring timeouts in raw milliseconds (`CONNECT_TIMEOUT=5000`), you can write `CONNECT_TIMEOUT=5s` or `CONNECT_TIMEOUT=2m30s`. Much harder to misconfigure.

**Explicit defaults.** Every field has a sensible default. The service should be able to start with only the required fields (database URL, JWT secret) and use defaults for everything else.

## Loading Configuration

The `config` crate supports layered configuration — load from multiple sources with later sources overriding earlier ones:

```rust
impl AppConfig {
    pub fn load() -> Result<Self, config::ConfigError> {
        let run_mode = std::env::var("APP_ENV").unwrap_or_else(|_| "development".into());

        let config = Config::builder()
            // Start with default values
            .set_default("server.host", "0.0.0.0")?
            .set_default("server.port", 3000)?

            // Load base config file
            .add_source(File::with_name("config/default").required(false))

            // Load environment-specific config
            .add_source(
                File::with_name(&format!("config/{}", run_mode)).required(false),
            )

            // Load local overrides (not committed to git)
            .add_source(File::with_name("config/local").required(false))

            // Override with environment variables
            // APP_SERVER__PORT=8080 maps to server.port
            .add_source(
                Environment::with_prefix("APP")
                    .separator("__")
                    .try_parsing(true),
            )

            .build()?;

        config.try_deserialize()
    }
}
```

The loading order is deliberate:

1. **Built-in defaults** — the absolute baseline
2. **`config/default.toml`** — shared defaults committed to the repo
3. **`config/{environment}.toml`** — environment-specific overrides (production.toml, staging.toml)
4. **`config/local.toml`** — developer-specific overrides, gitignored
5. **Environment variables** — highest priority, used in deployment

Each layer overrides the previous one. A developer can put their local database URL in `config/local.toml`, CI uses environment variables, and production uses Kubernetes secrets mounted as environment variables. Same code, different config sources.

### Config Files

```toml
# config/default.toml
[server]
host = "0.0.0.0"
port = 3000
request_timeout = "30s"

[database]
max_connections = 10
min_connections = 2
connect_timeout = "5s"

[features]
enable_new_search = false
enable_rate_limiting = false
rate_limit_per_minute = 100
```

```toml
# config/production.toml
[server]
request_timeout = "15s"

[database]
max_connections = 50
min_connections = 10
connect_timeout = "3s"

[features]
enable_rate_limiting = true
rate_limit_per_minute = 60
```

```toml
# config/local.toml (gitignored)
[database]
url = "postgres://localhost:5432/myapp_dev"

[auth]
jwt_secret = "dev-secret-not-for-production"
```

### Environment Variable Mapping

The `APP` prefix and `__` separator mean:

```bash
export APP_SERVER__PORT=8080        # → server.port = 8080
export APP_DATABASE__URL=postgres://...  # → database.url = "postgres://..."
export APP_FEATURES__ENABLE_NEW_SEARCH=true  # → features.enable_new_search = true
```

Double underscore (`__`) as a separator because single underscore is common in variable names. `APP_DATABASE_URL` is ambiguous — is it `database.url` or `database_url`? `APP_DATABASE__URL` is unambiguous.

## Validation at Startup

Loading config is one thing. Validating it is another. Catch problems at startup, not at request time:

```rust
impl AppConfig {
    pub fn validate(&self) -> Result<(), Vec<String>> {
        let mut errors = Vec::new();

        if self.server.port == 0 {
            errors.push("server.port must be non-zero".to_string());
        }

        if self.database.max_connections < self.database.min_connections {
            errors.push(format!(
                "database.max_connections ({}) must be >= min_connections ({})",
                self.database.max_connections, self.database.min_connections
            ));
        }

        if self.database.connect_timeout > self.server.request_timeout {
            errors.push(
                "database.connect_timeout should be less than server.request_timeout".to_string()
            );
        }

        if self.features.enable_rate_limiting && self.features.rate_limit_per_minute == 0 {
            errors.push(
                "rate_limit_per_minute must be > 0 when rate limiting is enabled".to_string()
            );
        }

        if errors.is_empty() {
            Ok(())
        } else {
            Err(errors)
        }
    }
}
```

Use it in `main`:

```rust
#[tokio::main]
async fn main() {
    let config = AppConfig::load().unwrap_or_else(|e| {
        eprintln!("Failed to load configuration: {}", e);
        std::process::exit(1);
    });

    if let Err(errors) = config.validate() {
        eprintln!("Configuration validation failed:");
        for error in &errors {
            eprintln!("  - {}", error);
        }
        std::process::exit(1);
    }

    // Now we know config is valid — proceed with initialization
    init_tracing();
    tracing::info!("configuration loaded and validated");

    // ...
}
```

Notice that validation runs *before* tracing initialization. If config is invalid, we print to stderr and exit. No point initializing a logging system if the config that tells you where to log is broken.

## Feature Flags

Feature flags in the config struct are the simplest approach and work well for small teams:

```rust
async fn search_handler(
    State(state): State<AppState>,
    Query(params): Query<SearchParams>,
) -> impl IntoResponse {
    if state.config.features.enable_new_search {
        new_search_engine::search(&state.db, &params).await
    } else {
        legacy_search::search(&state.db, &params).await
    }
}
```

For more sophisticated needs — percentage rollouts, user-based targeting, runtime toggling — you'll want a dedicated feature flag service like LaunchDarkly or Unleash. But for "enable this feature in production after we've verified it in staging," config-based flags are perfect.

### Runtime Config Reload

Sometimes you want to change configuration without restarting the service. Using `Arc<RwLock<>>`:

```rust
use std::sync::Arc;
use tokio::sync::RwLock;

#[derive(Clone)]
struct AppState {
    config: Arc<RwLock<AppConfig>>,
    db: PgPool,
}

async fn reload_config(State(state): State<AppState>) -> impl IntoResponse {
    match AppConfig::load() {
        Ok(new_config) => {
            if let Err(errors) = new_config.validate() {
                return (StatusCode::BAD_REQUEST,
                    format!("Invalid config: {:?}", errors));
            }
            *state.config.write().await = new_config;
            (StatusCode::OK, "configuration reloaded".to_string())
        }
        Err(e) => {
            (StatusCode::INTERNAL_SERVER_ERROR,
                format!("Failed to load config: {}", e))
        }
    }
}

// Reading config in handlers:
async fn some_handler(State(state): State<AppState>) -> impl IntoResponse {
    let config = state.config.read().await;
    if config.features.enable_rate_limiting {
        // ...
    }
    // ...
}
```

Be careful with runtime reloading — not everything *can* be changed at runtime. Database pool size, server port, TLS certificates — these require restarts. Feature flags and rate limits are safe to reload. Make this distinction explicit in your config struct, maybe by separating `StaticConfig` (set at startup) from `DynamicConfig` (reloadable).

## Secrets Management

Environment variables are fine for most config, but secrets need extra care:

**Never commit secrets to git.** Not even in `config/production.toml`. Use environment variables, Kubernetes secrets, or a secrets manager like HashiCorp Vault.

**Use `.env` files only for local development:**

```toml
# .env (gitignored)
APP_DATABASE__URL=postgres://localhost/myapp
APP_AUTH__JWT_SECRET=dev-only-secret
```

Load it with `dotenvy`:

```rust
fn main() {
    // Only load .env in development
    dotenvy::dotenv().ok(); // .ok() to ignore missing .env in production

    let config = AppConfig::load().unwrap();
    // ...
}
```

**In Kubernetes, use Secrets:**

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: myservice-secrets
type: Opaque
stringData:
  APP_DATABASE__URL: "postgres://user:pass@db:5432/prod"
  APP_AUTH__JWT_SECRET: "real-production-secret"
---
apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      containers:
        - name: myservice
          envFrom:
            - secretRef:
                name: myservice-secrets
            - configMapRef:
                name: myservice-config
```

## The Complete Config Module

Here's how I organize this in a real project:

```
src/
  config/
    mod.rs          # AppConfig struct, load(), validate()
  main.rs
config/
  default.toml      # Defaults (committed)
  production.toml   # Production overrides (committed)
  staging.toml      # Staging overrides (committed)
  local.toml        # Developer overrides (gitignored)
.env                # Local secrets (gitignored)
```

The `.gitignore`:

```
config/local.toml
.env
.env.*
```

This structure is dead simple and covers every deployment scenario I've encountered. Local development uses `.env` and `config/local.toml`. CI uses environment variables. Staging and production use Kubernetes secrets plus `config/{env}.toml`. The config struct ensures type safety everywhere, and validation at startup catches misconfigurations before they cause runtime errors.

## What's Next

We've been building up the operational side of Rust deployment. One thing we haven't tuned yet: the binary itself. In the final lesson, we'll dig into Rust's release profiles — LTO, codegen units, panic strategies, PGO, and all the knobs you can turn to produce the fastest, smallest binary possible. It's the fun optimization stuff.
