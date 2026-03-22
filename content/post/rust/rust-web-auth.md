---
title: "Lesson 5: Authentication — JWT, sessions, OAuth"
author: Atharva Pandey
series: "Rust Web & API Development"
lesson: 5
keywords: [Rust, rust, web, API, axum, authentication, JWT, sessions, OAuth, bcrypt, argon2]
tags: [Rust tutorial, rust, web, api]
date: '2024-10-09T11:00:00.000Z'
---

I've reviewed auth implementations at four different companies. Three of them stored passwords in SHA-256 without salting. One stored them in *plain text* in a column called `password_encrypted` — because naming it "encrypted" apparently counted as security. Auth is the part of your application that bad actors actively try to break. Getting it wrong isn't a bug, it's a liability.

## Password Hashing: Do This Right or Don't Do It At All

Before we talk about tokens or sessions, let's nail password storage. The rules are simple and non-negotiable:

1. **Never** store plaintext passwords
2. **Never** use fast hashes (MD5, SHA-256) for passwords
3. **Always** use a purpose-built password hashing algorithm
4. **Always** use unique salts (the library handles this)

The two acceptable choices in 2024 are **Argon2** and **bcrypt**. Argon2 is newer and has tunable memory hardness (making GPU attacks harder). Bcrypt is battle-tested and well-understood. I'll use Argon2 here.

```toml
[dependencies]
argon2 = "0.5"
rand = "0.8"
```

```rust
use argon2::{
    password_hash::{rand_core::OsRng, PasswordHash, PasswordHasher, PasswordVerifier, SaltString},
    Argon2,
};

pub fn hash_password(password: &str) -> Result<String, argon2::password_hash::Error> {
    let salt = SaltString::generate(&mut OsRng);
    let argon2 = Argon2::default();
    let hash = argon2.hash_password(password.as_bytes(), &salt)?;
    Ok(hash.to_string())
}

pub fn verify_password(password: &str, hash: &str) -> Result<bool, argon2::password_hash::Error> {
    let parsed_hash = PasswordHash::new(hash)?;
    Ok(Argon2::default()
        .verify_password(password.as_bytes(), &parsed_hash)
        .is_ok())
}
```

The resulting hash string includes the algorithm, parameters, salt, and hash — everything needed to verify later. It looks like: `$argon2id$v=19$m=19456,t=2,p=1$salt$hash`. You store the whole string in your database.

## JWT Authentication

JWTs are stateless tokens — the server doesn't need to look up a session store to validate them. The token itself contains the claims (user ID, roles, expiration), signed with a secret key. If the signature is valid and the token isn't expired, the user is authenticated.

```toml
[dependencies]
jsonwebtoken = "9"
chrono = { version = "0.4", features = ["serde"] }
```

### Token Creation

```rust
use jsonwebtoken::{encode, decode, Header, Validation, EncodingKey, DecodingKey};
use serde::{Deserialize, Serialize};
use chrono::{Utc, Duration};

#[derive(Debug, Serialize, Deserialize)]
pub struct Claims {
    pub sub: String,        // subject (user ID)
    pub email: String,
    pub role: String,
    pub exp: usize,         // expiration (UNIX timestamp)
    pub iat: usize,         // issued at
}

pub struct JwtConfig {
    pub secret: String,
    pub expiration_hours: i64,
}

impl JwtConfig {
    pub fn create_token(&self, user_id: &str, email: &str, role: &str) -> Result<String, jsonwebtoken::errors::Error> {
        let now = Utc::now();
        let expires_at = now + Duration::hours(self.expiration_hours);

        let claims = Claims {
            sub: user_id.to_string(),
            email: email.to_string(),
            role: role.to_string(),
            exp: expires_at.timestamp() as usize,
            iat: now.timestamp() as usize,
        };

        encode(
            &Header::default(),
            &claims,
            &EncodingKey::from_secret(self.secret.as_bytes()),
        )
    }

    pub fn validate_token(&self, token: &str) -> Result<Claims, jsonwebtoken::errors::Error> {
        let token_data = decode::<Claims>(
            token,
            &DecodingKey::from_secret(self.secret.as_bytes()),
            &Validation::default(),
        )?;
        Ok(token_data.claims)
    }
}
```

### Login Endpoint

```rust
use axum::{extract::State, Json};
use serde::Deserialize;

#[derive(Deserialize)]
struct LoginInput {
    email: String,
    password: String,
}

#[derive(Serialize)]
struct LoginResponse {
    token: String,
    token_type: String,
    expires_in: i64,
}

async fn login(
    State(state): State<AppState>,
    Json(input): Json<LoginInput>,
) -> Result<Json<LoginResponse>, AppError> {
    // Find user by email
    let user = sqlx::query_as!(
        User,
        "SELECT id, email, name, password_hash, role FROM users WHERE email = $1",
        input.email
    )
    .fetch_optional(&state.db)
    .await
    .map_err(|_| AppError::internal("Database error"))?
    .ok_or_else(|| AppError::unauthorized("Invalid email or password"))?;

    // Verify password
    let is_valid = verify_password(&input.password, &user.password_hash)
        .map_err(|_| AppError::internal("Password verification failed"))?;

    if !is_valid {
        return Err(AppError::unauthorized("Invalid email or password"));
    }

    // Create JWT
    let token = state.jwt.create_token(
        &user.id.to_string(),
        &user.email,
        &user.role,
    )
    .map_err(|_| AppError::internal("Token creation failed"))?;

    Ok(Json(LoginResponse {
        token,
        token_type: "Bearer".to_string(),
        expires_in: state.jwt.expiration_hours * 3600,
    }))
}
```

Notice the error message is always "Invalid email or password" — never "User not found" or "Wrong password." Telling attackers *which* part failed helps them enumerate valid emails.

### Auth Middleware

```rust
use axum::{
    extract::State,
    http::Request,
    middleware::Next,
    response::Response,
};

#[derive(Clone, Debug)]
pub struct AuthUser {
    pub id: String,
    pub email: String,
    pub role: String,
}

pub async fn require_auth(
    State(state): State<AppState>,
    mut request: Request<axum::body::Body>,
    next: Next,
) -> Result<Response, AppError> {
    let token = request
        .headers()
        .get("Authorization")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .ok_or_else(|| AppError::unauthorized("Missing authorization header"))?;

    let claims = state
        .jwt
        .validate_token(token)
        .map_err(|e| match e.kind() {
            jsonwebtoken::errors::ErrorKind::ExpiredSignature => {
                AppError::unauthorized("Token expired")
            }
            _ => AppError::unauthorized("Invalid token"),
        })?;

    let auth_user = AuthUser {
        id: claims.sub,
        email: claims.email,
        role: claims.role,
    };

    request.extensions_mut().insert(auth_user);
    Ok(next.run(request).await)
}

// Role-based authorization middleware
pub async fn require_role(
    role: &str,
    request: Request<axum::body::Body>,
    next: Next,
) -> Result<Response, AppError> {
    let auth_user = request
        .extensions()
        .get::<AuthUser>()
        .ok_or_else(|| AppError::unauthorized("Not authenticated"))?;

    if auth_user.role != role {
        return Err(AppError::forbidden("Insufficient permissions"));
    }

    Ok(next.run(request).await)
}
```

Wire it up:

```rust
use axum::middleware;

let public = Router::new()
    .route("/api/login", post(login))
    .route("/api/register", post(register));

let protected = Router::new()
    .route("/api/me", get(get_profile))
    .route("/api/users", get(list_users))
    .layer(middleware::from_fn_with_state(state.clone(), require_auth));

let admin = Router::new()
    .route("/admin/users", get(admin_list_users))
    .route("/admin/users/:id/ban", post(ban_user))
    .layer(middleware::from_fn_with_state(state.clone(), require_auth))
    .layer(middleware::from_fn(|req, next| require_role("admin", req, next)));

let app = Router::new()
    .merge(public)
    .merge(protected)
    .merge(admin)
    .with_state(state);
```

### Extracting the Current User in Handlers

Once the auth middleware runs, handlers can extract the user:

```rust
use axum::Extension;

async fn get_profile(
    Extension(user): Extension<AuthUser>,
) -> Json<serde_json::Value> {
    Json(json!({
        "id": user.id,
        "email": user.email,
        "role": user.role,
    }))
}

async fn update_profile(
    Extension(user): Extension<AuthUser>,
    State(state): State<AppState>,
    Json(input): Json<UpdateProfileInput>,
) -> Result<Json<User>, AppError> {
    let updated = sqlx::query_as!(
        User,
        "UPDATE users SET name = $1 WHERE id = $2 RETURNING *",
        input.name,
        user.id.parse::<i64>().unwrap()
    )
    .fetch_one(&state.db)
    .await?;

    Ok(Json(updated))
}
```

## Refresh Tokens

Access tokens should be short-lived (15 minutes to 1 hour). Refresh tokens are long-lived (days to weeks) and are used to get new access tokens without re-entering credentials.

```rust
use uuid::Uuid;

#[derive(Serialize)]
struct TokenPair {
    access_token: String,
    refresh_token: String,
    token_type: String,
    expires_in: i64,
}

async fn login(
    State(state): State<AppState>,
    Json(input): Json<LoginInput>,
) -> Result<Json<TokenPair>, AppError> {
    // ... validate credentials ...

    let access_token = state.jwt.create_token(&user.id.to_string(), &user.email, &user.role)
        .map_err(|_| AppError::internal("Token creation failed"))?;

    // Refresh token is an opaque random string, stored in the database
    let refresh_token = Uuid::new_v4().to_string();

    sqlx::query!(
        "INSERT INTO refresh_tokens (token, user_id, expires_at) VALUES ($1, $2, $3)",
        refresh_token,
        user.id,
        Utc::now() + Duration::days(30),
    )
    .execute(&state.db)
    .await
    .map_err(|_| AppError::internal("Failed to store refresh token"))?;

    Ok(Json(TokenPair {
        access_token,
        refresh_token,
        token_type: "Bearer".to_string(),
        expires_in: 3600, // 1 hour
    }))
}

async fn refresh(
    State(state): State<AppState>,
    Json(input): Json<RefreshInput>,
) -> Result<Json<TokenPair>, AppError> {
    // Look up the refresh token
    let token_record = sqlx::query!(
        "SELECT user_id, expires_at FROM refresh_tokens WHERE token = $1",
        input.refresh_token
    )
    .fetch_optional(&state.db)
    .await
    .map_err(|_| AppError::internal("Database error"))?
    .ok_or_else(|| AppError::unauthorized("Invalid refresh token"))?;

    // Check expiration
    if token_record.expires_at < Utc::now() {
        return Err(AppError::unauthorized("Refresh token expired"));
    }

    // Delete the used refresh token (rotation)
    sqlx::query!("DELETE FROM refresh_tokens WHERE token = $1", input.refresh_token)
        .execute(&state.db)
        .await
        .map_err(|_| AppError::internal("Database error"))?;

    // Get user and issue new token pair
    let user = sqlx::query_as!(User, "SELECT * FROM users WHERE id = $1", token_record.user_id)
        .fetch_one(&state.db)
        .await?;

    // Issue new access + refresh tokens
    let access_token = state.jwt.create_token(&user.id.to_string(), &user.email, &user.role)
        .map_err(|_| AppError::internal("Token creation failed"))?;

    let new_refresh_token = Uuid::new_v4().to_string();

    sqlx::query!(
        "INSERT INTO refresh_tokens (token, user_id, expires_at) VALUES ($1, $2, $3)",
        new_refresh_token,
        user.id,
        Utc::now() + Duration::days(30),
    )
    .execute(&state.db)
    .await
    .map_err(|_| AppError::internal("Failed to store refresh token"))?;

    Ok(Json(TokenPair {
        access_token,
        refresh_token: new_refresh_token,
        token_type: "Bearer".to_string(),
        expires_in: 3600,
    }))
}
```

The key detail: refresh token **rotation**. When a refresh token is used, it's deleted and a new one is issued. If an attacker steals a refresh token and uses it, the legitimate user's next refresh attempt will fail (token already deleted), alerting them that something is wrong. If the legitimate user uses it first, the attacker's stolen token becomes invalid.

## OAuth2 — The Brief Version

For OAuth2 flows (Google, GitHub login), the `oauth2` crate handles the protocol. I won't implement a full OAuth flow here — it's mostly HTTP redirects and token exchanges that are better shown in a dedicated article. But here's the skeleton:

```toml
[dependencies]
oauth2 = "4"
reqwest = { version = "0.12", features = ["json"] }
```

```rust
use oauth2::{
    AuthorizationCode, AuthUrl, ClientId, ClientSecret,
    CsrfToken, RedirectUrl, Scope, TokenResponse, TokenUrl,
    basic::BasicClient,
};

fn build_oauth_client() -> BasicClient {
    BasicClient::new(
        ClientId::new(std::env::var("GITHUB_CLIENT_ID").unwrap()),
        Some(ClientSecret::new(std::env::var("GITHUB_CLIENT_SECRET").unwrap())),
        AuthUrl::new("https://github.com/login/oauth/authorize".to_string()).unwrap(),
        Some(TokenUrl::new("https://github.com/login/oauth/access_token".to_string()).unwrap()),
    )
    .set_redirect_uri(
        RedirectUrl::new("http://localhost:3000/auth/github/callback".to_string()).unwrap(),
    )
}

// Step 1: Redirect user to GitHub
async fn github_login(State(state): State<AppState>) -> axum::response::Redirect {
    let (auth_url, csrf_token) = state
        .oauth_client
        .authorize_url(CsrfToken::new_random)
        .add_scope(Scope::new("user:email".to_string()))
        .url();

    // Store csrf_token in session for verification in callback
    axum::response::Redirect::temporary(auth_url.as_str())
}

// Step 2: Handle callback from GitHub
#[derive(Deserialize)]
struct OAuthCallback {
    code: String,
    state: String,
}

async fn github_callback(
    State(state): State<AppState>,
    Query(params): Query<OAuthCallback>,
) -> Result<Json<TokenPair>, AppError> {
    // Verify CSRF state token
    // Exchange code for access token
    let token = state
        .oauth_client
        .exchange_code(AuthorizationCode::new(params.code))
        .request_async(oauth2::reqwest::async_http_client)
        .await
        .map_err(|_| AppError::unauthorized("OAuth token exchange failed"))?;

    // Use the access token to get user info from GitHub API
    let client = reqwest::Client::new();
    let github_user: serde_json::Value = client
        .get("https://api.github.com/user")
        .header("Authorization", format!("Bearer {}", token.access_token().secret()))
        .header("User-Agent", "rust-web-app")
        .send()
        .await
        .map_err(|_| AppError::internal("Failed to fetch user info"))?
        .json()
        .await
        .map_err(|_| AppError::internal("Failed to parse user info"))?;

    // Find or create user in our database
    // Issue our own JWT
    // Return token pair
    todo!("Create or find user, issue JWT")
}
```

The pattern is: redirect to provider → user authenticates there → provider redirects back with a code → exchange code for access token → use access token to get user info → create/find local user → issue your own JWT.

## Security Checklist

Before shipping auth to production:

- [ ] Passwords hashed with Argon2 or bcrypt (never SHA/MD5)
- [ ] JWT secret is at least 256 bits, loaded from environment
- [ ] Access tokens expire in 15 minutes to 1 hour
- [ ] Refresh tokens are rotated on use
- [ ] Login errors don't distinguish between "user not found" and "wrong password"
- [ ] Rate limiting on login endpoint (we'll cover this in Lesson 9)
- [ ] HTTPS in production (never send tokens over plain HTTP)
- [ ] No tokens in URL query parameters (use headers)
- [ ] Refresh tokens stored hashed in the database (same as passwords)
- [ ] CSRF protection for cookie-based auth

Auth is never "done." You'll revisit it, tighten it, audit it. But this foundation — Argon2 hashing, short-lived JWTs, refresh token rotation, proper error messages — handles most web applications well.

Next: database integration with SQLx. Time to actually persist things.
