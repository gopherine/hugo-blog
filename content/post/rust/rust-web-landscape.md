---
title: "Lesson 1: The Rust Web Landscape — Axum, Actix, Rocket and why I pick Axum"
author: Atharva Pandey
series: "Rust Web & API Development"
lesson: 1
keywords: [Rust, rust, web, API, axum, actix-web, rocket, web framework comparison, tower]
tags: [Rust tutorial, rust, web, api]
date: '2024-10-01T10:22:00.000Z'
---

I spent three weeks building a service in Actix-web before ripping it out and switching to Axum. Not because Actix was bad — it's genuinely fast and battle-tested. I switched because every time I needed custom middleware, I was fighting the framework instead of writing my application. That experience taught me something: in Rust web development, the framework you pick determines how much you fight the type system versus how much you work *with* it.

## The Current State of Rust on the Web

Rust's web ecosystem has matured significantly. We're past the "can Rust even do web?" phase. Multiple production services handle millions of requests daily in Rust — Discord, Cloudflare, AWS. The question isn't whether Rust works for web, it's which tool fits your needs.

There are three serious contenders. Let me be honest about all of them.

## Actix-web

Actix-web is the oldest and most benchmarked framework. It consistently tops the TechEmpower benchmarks and has been production-ready for years.

```rust
use actix_web::{web, App, HttpServer, HttpResponse};

async fn hello() -> HttpResponse {
    HttpResponse::Ok().body("Hello from Actix")
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    HttpServer::new(|| {
        App::new()
            .route("/", web::get().to(hello))
    })
    .bind("127.0.0.1:3000")?
    .run()
    .await
}
```

**What's good:** Raw performance. Mature ecosystem. Lots of middleware crates. If you need to squeeze every last microsecond, Actix is hard to beat.

**What's not:** Actix has its own abstractions for everything. Its middleware system, its extractors, its error types — they're all Actix-specific. When you write Actix middleware, you're learning Actix patterns, not general Rust patterns. The `Transform` and `Service` traits for middleware are notoriously hard to implement correctly.

There's also the historical drama. The original creator burned out in 2020 after a controversy about unsafe code usage. The project continued under community maintenance and is healthy today, but it's worth knowing the history.

## Rocket

Rocket was the first Rust web framework that felt *nice* to use. It pioneered the attribute-macro approach to routing.

```rust
#[macro_use] extern crate rocket;

#[get("/hello/<name>")]
fn hello(name: &str) -> String {
    format!("Hello, {}!", name)
}

#[launch]
fn rocket() -> _ {
    rocket::build().mount("/", routes![hello])
}
```

**What's good:** Beautiful API. If you're coming from Flask or Express, Rocket feels immediately familiar. The request guards system is elegant. Documentation is excellent.

**What's not:** Rocket took years to get async support (v0.5). That delay caused a massive exodus of users. The ecosystem is smaller now. Rocket also uses its own runtime and its own abstractions — similar to Actix in that regard. And the macro-heavy approach makes compile errors harder to debug. When something goes wrong inside a `#[get]` macro, you're reading error messages about generated code you never wrote.

## Axum

Axum came out of the Tokio project — the same team that maintains the async runtime that powers most of the Rust async ecosystem. This is not a coincidence. It's the key to understanding why Axum works the way it does.

```rust
use axum::{routing::get, Router};

async fn hello() -> &'static str {
    "Hello from Axum"
}

#[tokio::main]
async fn main() {
    let app = Router::new().route("/hello", get(hello));

    let listener = tokio::net::TcpListener::bind("0.0.0.0:3000")
        .await
        .unwrap();
    axum::serve(listener, app).await.unwrap();
}
```

**What's good:** Axum doesn't invent its own middleware system. It uses Tower — the same middleware layer that Tonic (gRPC), Hyper (HTTP), and half the Rust networking ecosystem uses. Learn Tower once, use it everywhere. Handlers are just async functions. Extractors are just trait implementations. There's no macro magic — the type system does the work.

**What's not:** Axum's compile errors can be rough. When you mess up an extractor type, the compiler dumps a wall of trait bound errors that require experience to decipher. The framework is also younger than Actix and Rocket, so the third-party ecosystem is still catching up in some areas.

## Why I Pick Axum

Here's my reasoning, and I want to be specific about it.

**Tower compatibility.** This is the killer feature. Tower is to Rust networking what Express middleware is to Node — except it's framework-agnostic. A Tower middleware works with Axum, Tonic, Hyper, and any other Tower-based service. When you invest time writing middleware for Axum, you're building reusable infrastructure, not framework-locked code.

```rust
use tower::ServiceBuilder;
use tower_http::cors::CorsLayer;
use tower_http::trace::TraceLayer;

let app = Router::new()
    .route("/api/users", get(list_users).post(create_user))
    .layer(
        ServiceBuilder::new()
            .layer(TraceLayer::new_for_http())
            .layer(CorsLayer::permissive())
    );
```

**No macros for routing.** Rocket's `#[get("/path")]` is cute until you need dynamic route registration, conditional routes, or programmatic router construction. Axum routes are plain Rust — you can build them in loops, conditionally add them, compose them from different modules. It's just code.

**Extractors are types.** In Axum, the arguments to your handler function *are* the extractors. Want a JSON body? Add `Json<T>` as a parameter. Want a path parameter? Add `Path<String>`. Want the database pool? Add `State<Pool>`. The compiler verifies everything fits together at compile time.

```rust
use axum::{extract::{Path, State, Json}, http::StatusCode};
use sqlx::PgPool;

async fn get_user(
    State(pool): State<PgPool>,
    Path(user_id): Path<i64>,
) -> Result<Json<User>, StatusCode> {
    let user = sqlx::query_as!(User, "SELECT * FROM users WHERE id = $1", user_id)
        .fetch_one(&pool)
        .await
        .map_err(|_| StatusCode::NOT_FOUND)?;

    Ok(Json(user))
}
```

**The team behind it.** Axum is maintained by the Tokio team. These are the people who maintain Tokio, Hyper, Tower, Tonic, Mio, and Bytes. They're not going anywhere. The bus factor is low, the funding is real (they have corporate sponsors), and the design decisions are made by people who deeply understand async Rust.

## When I Wouldn't Pick Axum

I'm not a zealot. There are cases where the other frameworks make more sense.

- **Maximum raw throughput with minimal abstraction:** Actix-web still edges out Axum in some benchmarks. If you're building a proxy or a gateway where every microsecond matters and you don't need much middleware, Actix is a reasonable choice.

- **Rapid prototyping by a Rust beginner:** Rocket's API is genuinely more approachable. If you're new to Rust and just want to get something running, Rocket's documentation and error messages (outside of macro issues) are friendlier.

- **Existing codebase:** If your team already has production Actix code, switching to Axum for ideological reasons is foolish. Ship features, don't rewrite frameworks.

## The Dependency Tree

One thing people overlook is how these frameworks relate to each other underneath.

```
Actix-web → actix-rt (own runtime) → tokio (partial)
Rocket    → rocket (own runtime) → tokio
Axum      → tokio + hyper + tower (directly)
```

Axum is thin. It's a routing and extraction layer on top of Hyper and Tower. This means you get the full power of the Tokio ecosystem without adapter layers. When Tokio ships a performance improvement, Axum gets it automatically.

## Setting Up for This Course

Throughout this course, we'll use Axum. Here's the base `Cargo.toml` we'll build on:

```toml
[package]
name = "rust-web-course"
version = "0.1.0"
edition = "2021"

[dependencies]
axum = "0.7"
tokio = { version = "1", features = ["full"] }
tower = "0.4"
tower-http = { version = "0.5", features = ["cors", "trace"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter"] }
```

And the skeleton application:

```rust
use axum::{routing::get, Router};
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

#[tokio::main]
async fn main() {
    tracing_subscriber::registry()
        .with(tracing_subscriber::EnvFilter::new(
            std::env::var("RUST_LOG").unwrap_or_else(|_| "info".into()),
        ))
        .with(tracing_subscriber::fmt::layer())
        .init();

    let app = Router::new()
        .route("/health", get(|| async { "ok" }));

    let listener = tokio::net::TcpListener::bind("0.0.0.0:3000")
        .await
        .unwrap();

    tracing::info!("listening on {}", listener.local_addr().unwrap());
    axum::serve(listener, app).await.unwrap();
}
```

Run it:

```bash
RUST_LOG=info cargo run
```

Hit `http://localhost:3000/health` — you should see `ok`. That's your foundation. Everything we build in this course starts here.

## What's Coming

Next up, we'll go deep on Axum's routing, handlers, and extractors. Not the surface-level stuff — the patterns you need when your API has fifty routes, nested resources, and shared state across handlers. The stuff you figure out at 2am when the docs run out.
