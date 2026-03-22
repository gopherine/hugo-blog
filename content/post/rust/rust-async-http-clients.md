---
title: "Lesson 13: Building HTTP Clients with reqwest — Async HTTP done right"
author: Atharva Pandey
series: "Async Rust Masterclass"
lesson: 13
keywords: [Rust, rust, async, tokio, reqwest, http, client, API, json, retry]
tags: [Rust tutorial, rust, async, tokio]
date: '2025-01-30T16:42:09.000Z'
---

Almost every backend service I've built makes HTTP calls to *something* — a third-party API, another microservice, a webhook endpoint. And almost every production HTTP bug I've dealt with comes from one of three things: missing timeouts, not reusing connections, or ignoring response bodies.

reqwest is the HTTP client for async Rust. It's built on hyper and Tokio, handles connection pooling, TLS, cookies, compression, and all the stuff you don't want to think about. But you still need to use it correctly.

## Setup

```toml
[dependencies]
reqwest = { version = "0.12", features = ["json"] }
tokio = { version = "1", features = ["full"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

## Basic Requests

```rust
use reqwest;

#[tokio::main]
async fn main() -> Result<(), reqwest::Error> {
    // Simple GET
    let body = reqwest::get("https://httpbin.org/get")
        .await?
        .text()
        .await?;
    println!("Response: {}", &body[..100]);

    // POST with body
    let client = reqwest::Client::new();
    let resp = client
        .post("https://httpbin.org/post")
        .body("raw body content")
        .send()
        .await?;
    println!("Status: {}", resp.status());

    Ok(())
}
```

## The Client — Reuse It

This is the single most important thing about reqwest: **create one `Client` and reuse it**.

```rust
use reqwest::Client;
use std::time::Duration;

// BAD: Creates a new connection pool for every request
async fn bad_approach() {
    for _ in 0..100 {
        let resp = reqwest::get("https://httpbin.org/get").await.unwrap();
        // Each call creates a new Client, new connection pool, new TLS handshake
        drop(resp);
    }
}

// GOOD: Reuse the client
async fn good_approach() {
    let client = Client::builder()
        .timeout(Duration::from_secs(10))
        .pool_max_idle_per_host(10)
        .build()
        .unwrap();

    for _ in 0..100 {
        let resp = client.get("https://httpbin.org/get").send().await.unwrap();
        // Reuses connections from the pool
        drop(resp);
    }
}

#[tokio::main]
async fn main() {
    good_approach().await;
}
```

The `Client` internally maintains a connection pool. Creating a new one per request means you pay for TCP handshakes and TLS negotiation every time. With a shared client, subsequent requests to the same host reuse existing connections.

## Configuring the Client

```rust
use reqwest::{Client, header};
use std::time::Duration;

fn build_api_client() -> Client {
    let mut headers = header::HeaderMap::new();
    headers.insert(
        header::ACCEPT,
        header::HeaderValue::from_static("application/json"),
    );
    headers.insert(
        header::USER_AGENT,
        header::HeaderValue::from_static("my-app/1.0"),
    );

    Client::builder()
        .timeout(Duration::from_secs(30))        // Overall request timeout
        .connect_timeout(Duration::from_secs(5))  // TCP connect timeout
        .pool_max_idle_per_host(20)               // Connection pool size
        .pool_idle_timeout(Duration::from_secs(90)) // How long idle connections live
        .default_headers(headers)                  // Headers on every request
        .gzip(true)                                // Accept gzip responses
        .redirect(reqwest::redirect::Policy::limited(5)) // Max redirects
        .build()
        .unwrap()
}

#[tokio::main]
async fn main() {
    let client = build_api_client();
    let resp = client.get("https://httpbin.org/get").send().await.unwrap();
    println!("Status: {}", resp.status());
}
```

## JSON APIs

```rust
use reqwest::Client;
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize)]
struct CreateUser {
    name: String,
    email: String,
}

#[derive(Debug, Deserialize)]
struct ApiResponse {
    json: serde_json::Value,
    url: String,
}

#[tokio::main]
async fn main() -> Result<(), reqwest::Error> {
    let client = Client::new();

    // POST JSON
    let user = CreateUser {
        name: "Atharva".to_string(),
        email: "atharva@example.com".to_string(),
    };

    let resp: ApiResponse = client
        .post("https://httpbin.org/post")
        .json(&user)  // Serializes to JSON, sets Content-Type
        .send()
        .await?
        .json()  // Deserializes response body
        .await?;

    println!("Response: {:#?}", resp);

    // GET with query parameters
    let resp = client
        .get("https://httpbin.org/get")
        .query(&[("page", "1"), ("limit", "10")])
        .send()
        .await?
        .json::<serde_json::Value>()
        .await?;

    println!("Args: {}", resp["args"]);

    Ok(())
}
```

## Error Handling

reqwest errors are not just network errors — you need to check HTTP status codes too:

```rust
use reqwest::{Client, StatusCode};

#[derive(Debug)]
enum ApiError {
    Network(reqwest::Error),
    NotFound,
    RateLimited { retry_after: Option<u64> },
    ServerError(StatusCode),
    Unexpected(StatusCode, String),
}

impl From<reqwest::Error> for ApiError {
    fn from(e: reqwest::Error) -> Self {
        ApiError::Network(e)
    }
}

async fn fetch_resource(client: &Client, id: u32) -> Result<String, ApiError> {
    let resp = client
        .get(format!("https://httpbin.org/status/{}", 200 + (id % 5) * 100))
        .send()
        .await?;

    match resp.status() {
        StatusCode::OK => {
            let body = resp.text().await?;
            Ok(body)
        }
        StatusCode::NOT_FOUND => Err(ApiError::NotFound),
        StatusCode::TOO_MANY_REQUESTS => {
            let retry_after = resp
                .headers()
                .get("retry-after")
                .and_then(|v| v.to_str().ok())
                .and_then(|v| v.parse().ok());
            Err(ApiError::RateLimited { retry_after })
        }
        status if status.is_server_error() => Err(ApiError::ServerError(status)),
        status => {
            let body = resp.text().await.unwrap_or_default();
            Err(ApiError::Unexpected(status, body))
        }
    }
}

#[tokio::main]
async fn main() {
    let client = Client::new();

    for id in 0..5 {
        match fetch_resource(&client, id).await {
            Ok(body) => println!("[{id}] Success: {} bytes", body.len()),
            Err(e) => println!("[{id}] Error: {e:?}"),
        }
    }
}
```

**Important:** `resp.error_for_status()` is a shortcut that converts 4xx/5xx status codes into errors:

```rust
use reqwest::Client;

#[tokio::main]
async fn main() {
    let client = Client::new();

    let result = client
        .get("https://httpbin.org/status/404")
        .send()
        .await
        .unwrap()
        .error_for_status();

    match result {
        Ok(resp) => println!("OK: {}", resp.status()),
        Err(e) => println!("HTTP error: {e}"),
    }
}
```

## Concurrent Requests

```rust
use reqwest::Client;
use std::time::Duration;

async fn fetch_url(client: &Client, url: &str) -> Result<(String, usize), String> {
    let resp = client
        .get(url)
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .map_err(|e| format!("{url}: {e}"))?;

    let status = resp.status().to_string();
    let body = resp.text().await.map_err(|e| format!("{url}: {e}"))?;

    Ok((status, body.len()))
}

#[tokio::main]
async fn main() {
    let client = Client::builder()
        .timeout(Duration::from_secs(10))
        .build()
        .unwrap();

    let urls = vec![
        "https://httpbin.org/get",
        "https://httpbin.org/ip",
        "https://httpbin.org/user-agent",
        "https://httpbin.org/headers",
    ];

    // Concurrent requests with join_all
    let futures: Vec<_> = urls.iter()
        .map(|url| fetch_url(&client, url))
        .collect();

    let results = futures::future::join_all(futures).await;

    for (url, result) in urls.iter().zip(results) {
        match result {
            Ok((status, size)) => println!("{url}: {status} ({size} bytes)"),
            Err(e) => println!("Error: {e}"),
        }
    }
}
```

## Streaming Responses

For large responses, don't buffer the entire body in memory:

```rust
use reqwest::Client;
use tokio::io::AsyncWriteExt;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let client = Client::new();

    let resp = client
        .get("https://httpbin.org/stream/10")
        .send()
        .await?;

    println!("Status: {}", resp.status());
    println!("Content-Length: {:?}", resp.content_length());

    // Stream the response body chunk by chunk
    let mut stream = resp.bytes_stream();

    use futures::StreamExt;
    let mut total = 0usize;
    while let Some(chunk) = stream.next().await {
        let chunk = chunk?;
        total += chunk.len();
        println!("Received chunk: {} bytes", chunk.len());
    }

    println!("Total downloaded: {total} bytes");

    Ok(())
}
```

## A Production-Ready API Client

Here's the pattern I use for wrapping third-party APIs:

```rust
use reqwest::{Client, StatusCode};
use serde::{Deserialize, Serialize};
use std::time::Duration;

#[derive(Debug)]
pub enum ApiError {
    Network(reqwest::Error),
    NotFound,
    RateLimited(Duration),
    Auth(String),
    Server(String),
    Parse(String),
}

impl std::fmt::Display for ApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ApiError::Network(e) => write!(f, "network error: {e}"),
            ApiError::NotFound => write!(f, "not found"),
            ApiError::RateLimited(d) => write!(f, "rate limited, retry after {d:?}"),
            ApiError::Auth(msg) => write!(f, "auth error: {msg}"),
            ApiError::Server(msg) => write!(f, "server error: {msg}"),
            ApiError::Parse(msg) => write!(f, "parse error: {msg}"),
        }
    }
}

impl From<reqwest::Error> for ApiError {
    fn from(e: reqwest::Error) -> Self {
        ApiError::Network(e)
    }
}

pub struct GitHubClient {
    client: Client,
    base_url: String,
}

#[derive(Debug, Deserialize)]
pub struct Repo {
    pub name: String,
    pub full_name: String,
    pub stargazers_count: u64,
    pub language: Option<String>,
}

impl GitHubClient {
    pub fn new(token: &str) -> Self {
        use reqwest::header;
        let mut headers = header::HeaderMap::new();
        headers.insert(
            header::AUTHORIZATION,
            header::HeaderValue::from_str(&format!("Bearer {token}")).unwrap(),
        );
        headers.insert(
            header::ACCEPT,
            header::HeaderValue::from_static("application/vnd.github.v3+json"),
        );
        headers.insert(
            header::USER_AGENT,
            header::HeaderValue::from_static("rust-app/1.0"),
        );

        let client = Client::builder()
            .default_headers(headers)
            .timeout(Duration::from_secs(15))
            .connect_timeout(Duration::from_secs(5))
            .pool_max_idle_per_host(5)
            .build()
            .unwrap();

        GitHubClient {
            client,
            base_url: "https://api.github.com".to_string(),
        }
    }

    pub async fn get_repo(&self, owner: &str, repo: &str) -> Result<Repo, ApiError> {
        let url = format!("{}/repos/{owner}/{repo}", self.base_url);
        let resp = self.client.get(&url).send().await?;

        match resp.status() {
            StatusCode::OK => {
                resp.json().await.map_err(|e| ApiError::Parse(e.to_string()))
            }
            StatusCode::NOT_FOUND => Err(ApiError::NotFound),
            StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN => {
                let body = resp.text().await.unwrap_or_default();
                Err(ApiError::Auth(body))
            }
            StatusCode::TOO_MANY_REQUESTS => {
                let retry_after = resp
                    .headers()
                    .get("retry-after")
                    .and_then(|v| v.to_str().ok())
                    .and_then(|v| v.parse::<u64>().ok())
                    .unwrap_or(60);
                Err(ApiError::RateLimited(Duration::from_secs(retry_after)))
            }
            status => {
                let body = resp.text().await.unwrap_or_default();
                Err(ApiError::Server(format!("{status}: {body}")))
            }
        }
    }

    pub async fn list_repos(&self, user: &str) -> Result<Vec<Repo>, ApiError> {
        let url = format!("{}/users/{user}/repos", self.base_url);
        let resp = self.client
            .get(&url)
            .query(&[("sort", "stars"), ("per_page", "10")])
            .send()
            .await?;

        if resp.status().is_success() {
            resp.json().await.map_err(|e| ApiError::Parse(e.to_string()))
        } else {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            Err(ApiError::Server(format!("{status}: {body}")))
        }
    }
}

#[tokio::main]
async fn main() {
    // In real code, read from environment
    let client = GitHubClient::new("fake-token");

    match client.list_repos("rust-lang").await {
        Ok(repos) => {
            for repo in repos {
                println!("{}: {} stars ({:?})",
                    repo.full_name,
                    repo.stargazers_count,
                    repo.language,
                );
            }
        }
        Err(e) => println!("Error: {e}"),
    }
}
```

## Common Mistakes

**1. Not consuming the response body:**
```rust
// This leaks connections! The body must be consumed or dropped.
// let resp = client.get(url).send().await?;
// if resp.status() != 200 { return Err(...); }
// The body is still attached to the connection

// Always consume or explicitly drop:
// let _body = resp.text().await?; // Consumes the body
```

**2. No timeout:**
```rust
// BAD: Can hang forever
// client.get(url).send().await?;

// GOOD: Always set timeouts
// client.get(url).timeout(Duration::from_secs(5)).send().await?;
// Or set it on the Client builder
```

**3. Cloning the client is free:**
```rust
// Client uses Arc internally — cloning is just bumping a reference count
// let client = Client::new();
// let client2 = client.clone(); // Cheap! Same connection pool.
```

reqwest makes HTTP easy, but the patterns around it — connection reuse, proper error handling, timeouts, streaming — are what make the difference between "it works on my machine" and "it works in production under load."

Next: Tower and the service middleware pattern — the abstraction layer that ties all of this together.
