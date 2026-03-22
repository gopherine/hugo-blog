---
title: "Lesson 2: HTTP Clients — reqwest and hyper"
author: Atharva Pandey
series: "Rust Networking & Distributed Systems"
lesson: 2
keywords: [Rust, rust, networking, distributed, HTTP, reqwest, hyper, client, REST, API]
tags: [Rust tutorial, rust, networking, distributed-systems]
date: '2025-05-14T16:45:00.000Z'
---

I once spent three hours debugging a production issue that turned out to be an HTTP client with no timeout configured. Three hours. The client was happily waiting forever for a response from a service that had crashed, holding a database connection open the entire time. That experience permanently changed how I think about HTTP clients — they're not just "make a request, get a response." They're complex state machines with connection pools, redirect policies, timeout hierarchies, and a dozen other knobs that matter when things go wrong.

## The Two-Layer Stack

Rust's HTTP client story is built on two crates that serve very different purposes:

- **hyper** — a low-level, correct, fast HTTP implementation. It gives you raw control over every aspect of HTTP/1.1 and HTTP/2 connections. Think of it as the engine.
- **reqwest** — a high-level, ergonomic client built on top of hyper. Connection pooling, JSON serialization, cookie jars, proxy support — all batteries included.

For 90% of use cases, reqwest is what you want. You drop down to hyper when you need custom connection handling, exotic protocols, or when you're building a proxy/load balancer yourself.

## Getting Started with reqwest

```toml
[dependencies]
reqwest = { version = "0.12", features = ["json"] }
tokio = { version = "1", features = ["full"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

The simplest possible request:

```rust
#[tokio::main]
async fn main() -> Result<(), reqwest::Error> {
    let body = reqwest::get("https://httpbin.org/get")
        .await?
        .text()
        .await?;

    println!("{body}");
    Ok(())
}
```

Two `.await` points — one for sending the request and receiving headers, one for reading the body. This distinction matters. You can inspect status codes and headers before committing to reading potentially gigabytes of response body.

## Building a Proper Client

That one-shot `reqwest::get()` creates a new client for every request. In production, you want a shared `Client` instance — it maintains a connection pool, reuses TCP connections across requests, and shares TLS session state.

```rust
use reqwest::Client;
use std::time::Duration;

fn build_client() -> Client {
    Client::builder()
        .timeout(Duration::from_secs(30))
        .connect_timeout(Duration::from_secs(5))
        .pool_max_idle_per_host(10)
        .pool_idle_timeout(Duration::from_secs(90))
        .user_agent("my-service/1.0")
        .build()
        .expect("Failed to build HTTP client")
}
```

Let me break down those settings because every one of them matters:

- `timeout` — total time for the entire request/response cycle. If the server is slow to send the body, this catches it.
- `connect_timeout` — how long to wait for the TCP connection. Set this lower than your overall timeout so you fail fast on unreachable hosts.
- `pool_max_idle_per_host` — how many idle connections to keep per host. Too few and you're constantly doing TCP+TLS handshakes. Too many and you're wasting file descriptors.
- `pool_idle_timeout` — how long to keep idle connections around. Set this shorter than the server's keep-alive timeout or you'll hit reset connections.

## Working with JSON APIs

This is where reqwest really earns its keep. Define your types, derive Serialize/Deserialize, and the client handles the rest.

```rust
use reqwest::Client;
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize)]
struct CreatePost {
    title: String,
    body: String,
    user_id: u32,
}

#[derive(Debug, Deserialize)]
struct Post {
    id: u32,
    title: String,
    body: String,
    #[serde(rename = "userId")]
    user_id: u32,
}

async fn create_post(client: &Client) -> Result<Post, reqwest::Error> {
    let new_post = CreatePost {
        title: "Rust Networking".into(),
        body: "Building HTTP clients is fun".into(),
        user_id: 1,
    };

    let post: Post = client
        .post("https://jsonplaceholder.typicode.com/posts")
        .json(&new_post)
        .send()
        .await?
        .error_for_status()?  // Turn 4xx/5xx into errors
        .json()
        .await?;

    Ok(post)
}

async fn get_posts(client: &Client) -> Result<Vec<Post>, reqwest::Error> {
    let posts: Vec<Post> = client
        .get("https://jsonplaceholder.typicode.com/posts")
        .query(&[("userId", "1"), ("_limit", "5")])
        .send()
        .await?
        .error_for_status()?
        .json()
        .await?;

    Ok(posts)
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let client = Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .build()?;

    let created = create_post(&client).await?;
    println!("Created: {created:?}");

    let posts = get_posts(&client).await?;
    println!("Fetched {} posts", posts.len());
    for post in &posts {
        println!("  [{id}] {title}", id = post.id, title = post.title);
    }

    Ok(())
}
```

That `.error_for_status()` call is easy to forget but important. By default, reqwest treats a 404 or 500 as a successful response — it returns `Ok` with the error body. Calling `.error_for_status()` converts non-2xx responses into `Err` values so they propagate naturally through your error handling.

## Streaming Large Responses

When you're downloading files or consuming server-sent events, you don't want to buffer the entire response in memory. reqwest lets you stream the body chunk by chunk.

```rust
use reqwest::Client;
use tokio::io::AsyncWriteExt;
use tokio::fs::File;
use futures::StreamExt;

async fn download_file(
    client: &Client,
    url: &str,
    path: &str,
) -> Result<u64, Box<dyn std::error::Error>> {
    let response = client.get(url).send().await?.error_for_status()?;

    let total_size = response
        .content_length()
        .unwrap_or(0);

    println!("Downloading {total_size} bytes to {path}");

    let mut file = File::create(path).await?;
    let mut downloaded: u64 = 0;
    let mut stream = response.bytes_stream();

    while let Some(chunk) = stream.next().await {
        let chunk = chunk?;
        file.write_all(&chunk).await?;
        downloaded += chunk.len() as u64;

        if total_size > 0 {
            let pct = (downloaded as f64 / total_size as f64) * 100.0;
            print!("\r{downloaded}/{total_size} ({pct:.1}%)");
        }
    }

    println!("\nDone.");
    Ok(downloaded)
}
```

This uses constant memory regardless of file size. The chunks arrive as they come off the wire, and you write them straight to disk.

## Going Lower with hyper

Sometimes reqwest's abstractions get in your way. Maybe you need HTTP/2 server push handling, or you're building a reverse proxy and need zero-copy forwarding, or you need to inspect raw headers before they get normalized. That's when you reach for hyper directly.

```toml
[dependencies]
hyper = { version = "1", features = ["client", "http1"] }
hyper-util = { version = "0.1", features = ["client-legacy", "http1", "tokio"] }
http-body-util = "0.1"
tokio = { version = "1", features = ["full"] }
bytes = "1"
```

```rust
use http_body_util::{BodyExt, Empty};
use hyper::body::Bytes;
use hyper::Request;
use hyper_util::client::legacy::Client;
use hyper_util::rt::TokioExecutor;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let client = Client::builder(TokioExecutor::new())
        .build_http();

    let req = Request::builder()
        .method("GET")
        .uri("http://httpbin.org/get")
        .header("User-Agent", "hyper-raw/1.0")
        .body(Empty::<Bytes>::new())?;

    let response = client.request(req).await?;

    println!("Status: {}", response.status());
    println!("Headers:");
    for (name, value) in response.headers() {
        println!("  {name}: {}", value.to_str().unwrap_or("<binary>"));
    }

    let body = response.into_body().collect().await?.to_bytes();
    let text = String::from_utf8_lossy(&body);
    println!("\nBody:\n{text}");

    Ok(())
}
```

Hyper 1.0 changed the API significantly from 0.14. The body types are more explicit now — you have to specify what kind of body you're sending (empty, full, streaming). It's more verbose but also more honest about what's happening.

## Building a Typed API Client

In any real project, you'll want to wrap your HTTP calls in a typed client struct. This centralizes configuration, handles auth, and gives you a single place to add retry logic, metrics, or caching later.

```rust
use reqwest::{Client, StatusCode};
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use std::time::Duration;

#[derive(Debug, thiserror::Error)]
pub enum ApiError {
    #[error("HTTP error: {0}")]
    Http(#[from] reqwest::Error),
    #[error("Not found: {0}")]
    NotFound(String),
    #[error("Rate limited, retry after {retry_after_secs}s")]
    RateLimited { retry_after_secs: u64 },
    #[error("Server error: {status} — {body}")]
    Server { status: u16, body: String },
}

pub struct ApiClient {
    client: Client,
    base_url: String,
    api_key: String,
}

impl ApiClient {
    pub fn new(base_url: &str, api_key: &str) -> Self {
        let client = Client::builder()
            .timeout(Duration::from_secs(30))
            .connect_timeout(Duration::from_secs(5))
            .pool_max_idle_per_host(5)
            .build()
            .expect("Failed to build client");

        Self {
            client,
            base_url: base_url.trim_end_matches('/').to_string(),
            api_key: api_key.to_string(),
        }
    }

    async fn request<T: DeserializeOwned>(
        &self,
        method: reqwest::Method,
        path: &str,
        body: Option<&impl Serialize>,
    ) -> Result<T, ApiError> {
        let url = format!("{}{path}", self.base_url);

        let mut req = self
            .client
            .request(method, &url)
            .header("Authorization", format!("Bearer {}", self.api_key))
            .header("Accept", "application/json");

        if let Some(body) = body {
            req = req.json(body);
        }

        let response = req.send().await?;
        let status = response.status();

        match status {
            s if s.is_success() => Ok(response.json().await?),
            StatusCode::NOT_FOUND => Err(ApiError::NotFound(url)),
            StatusCode::TOO_MANY_REQUESTS => {
                let retry_after = response
                    .headers()
                    .get("retry-after")
                    .and_then(|v| v.to_str().ok())
                    .and_then(|v| v.parse().ok())
                    .unwrap_or(60);
                Err(ApiError::RateLimited {
                    retry_after_secs: retry_after,
                })
            }
            _ => {
                let body = response.text().await.unwrap_or_default();
                Err(ApiError::Server {
                    status: status.as_u16(),
                    body,
                })
            }
        }
    }

    pub async fn get<T: DeserializeOwned>(&self, path: &str) -> Result<T, ApiError> {
        self.request::<T>(reqwest::Method::GET, path, None::<&()>)
            .await
    }

    pub async fn post<T: DeserializeOwned>(
        &self,
        path: &str,
        body: &impl Serialize,
    ) -> Result<T, ApiError> {
        self.request(reqwest::Method::POST, path, Some(body)).await
    }
}
```

This pattern — typed client, centralized auth, structured errors — is the foundation of every good API integration I've written. Notice how the `RateLimited` variant carries the retry-after value. That's not just for nice error messages — it feeds directly into the retry logic we'll build in lesson 7.

## Connection Pool Tuning

One thing that trips people up: reqwest's default connection pool settings are fine for a single-service client, but if you're hitting many different hosts (like a web crawler or API aggregator), you need to think about file descriptor limits.

Each idle connection holds an open socket. If you've got `pool_max_idle_per_host = 10` and you're talking to 1,000 different hosts, that's potentially 10,000 open file descriptors just for idle connections. The default `ulimit -n` on most Linux systems is 1024.

```rust
let client = Client::builder()
    // For services hitting many hosts
    .pool_max_idle_per_host(2)     // Fewer idle connections per host
    .pool_idle_timeout(Duration::from_secs(30))  // Drop them faster
    // For services hitting one or two hosts heavily
    // .pool_max_idle_per_host(20)
    // .pool_idle_timeout(Duration::from_secs(120))
    .build()?;
```

There's no universal right answer — it depends on your traffic pattern.

## Middleware-Style Request Modification

reqwest doesn't have a formal middleware system, but you can use its `RequestBuilder` to build one. I like wrapping the client in a middleware chain:

```rust
use reqwest::{Client, Request, Response};
use std::time::Instant;

type Middleware = Box<dyn Fn(Request) -> Request + Send + Sync>;

struct InstrumentedClient {
    client: Client,
    middleware: Vec<Middleware>,
}

impl InstrumentedClient {
    fn new(client: Client) -> Self {
        Self {
            client,
            middleware: Vec::new(),
        }
    }

    fn with_middleware(mut self, m: Middleware) -> Self {
        self.middleware.push(m);
        self
    }

    async fn execute(&self, mut req: Request) -> Result<Response, reqwest::Error> {
        for m in &self.middleware {
            req = m(req);
        }

        let method = req.method().clone();
        let url = req.url().clone();
        let start = Instant::now();

        let response = self.client.execute(req).await?;

        let elapsed = start.elapsed();
        println!(
            "{method} {url} -> {} in {elapsed:?}",
            response.status()
        );

        Ok(response)
    }
}
```

This gives you a clean way to add request IDs, trace headers, or auth tokens without cluttering every call site.

## What's Next

HTTP is the lingua franca of the web, but it's not the only game in town. When you need strong typing, bidirectional streaming, and code generation across languages, gRPC is the answer. Next up, we'll build gRPC services with tonic — Rust's premier gRPC framework.
