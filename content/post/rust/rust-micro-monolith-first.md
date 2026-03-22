---
title: "Lesson 8: Monolith-First — Modular monoliths in Rust"
author: Atharva Pandey
series: "Rust Microservices Patterns"
lesson: 8
keywords: [Rust, rust, microservices, modular monolith, monolith-first, architecture, service extraction, Cargo workspaces]
tags: [Rust tutorial, rust, microservices, architecture]
date: '2025-06-17T13:19:00.000Z'
---

I'm going to tell you something that might sound weird after seven lessons about microservices patterns: don't start with microservices. Start with a monolith. A well-structured, modular monolith that's *designed* to be split later.

This isn't contrarianism for its own sake. I've seen three teams build microservices from day one. All three regretted it. One team spent more time debugging distributed system issues than building features. Another had seven services that each handled about 50 requests per day — the infrastructure cost was absurd. The third discovered six months in that they'd drawn their service boundaries wrong and had to do a painful re-architecture.

Here's the thing: you don't know where the boundaries should be on day one. You learn them through building features, watching traffic patterns, and feeling the pain of modules that change together too often. A monolith gives you that learning time without the operational overhead of distributed systems.

## The Modular Monolith Pattern

A modular monolith isn't a big ball of mud. It's a monolith with internal boundaries that mirror the service boundaries you might create later. Each module:

- Has its own types and business logic
- Exposes a public API through a trait
- Owns its own database tables (no cross-module table access)
- Communicates with other modules through explicit interfaces

Rust's module system and Cargo workspaces make this natural. Here's the project structure:

```
my-platform/
├── Cargo.toml          # Workspace root
├── crates/
│   ├── app/            # The binary — wires everything together
│   │   └── src/
│   │       └── main.rs
│   ├── shared/         # Cross-cutting concerns
│   │   └── src/
│   │       ├── lib.rs
│   │       ├── auth.rs
│   │       └── telemetry.rs
│   ├── orders/         # Order domain module
│   │   └── src/
│   │       ├── lib.rs
│   │       ├── domain.rs
│   │       ├── repository.rs
│   │       ├── handlers.rs
│   │       └── events.rs
│   ├── payments/       # Payment domain module
│   │   └── src/
│   │       ├── lib.rs
│   │       ├── domain.rs
│   │       ├── repository.rs
│   │       ├── handlers.rs
│   │       └── stripe.rs
│   ├── inventory/      # Inventory domain module
│   │   └── src/
│   │       ├── lib.rs
│   │       ├── domain.rs
│   │       ├── repository.rs
│   │       └── handlers.rs
│   └── notifications/  # Notification module
│       └── src/
│           ├── lib.rs
│           ├── email.rs
│           └── push.rs
```

```toml
# Cargo.toml (workspace root)
[workspace]
members = [
    "crates/app",
    "crates/shared",
    "crates/orders",
    "crates/payments",
    "crates/inventory",
    "crates/notifications",
]

[workspace.dependencies]
tokio = { version = "1", features = ["full"] }
axum = "0.7"
sqlx = { version = "0.8", features = ["postgres", "runtime-tokio", "uuid", "chrono"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
uuid = { version = "1", features = ["v4", "serde"] }
chrono = { version = "0.4", features = ["serde"] }
tracing = "0.1"
async-trait = "0.1"
thiserror = "2"
anyhow = "1"
```

## Module Public APIs

Each module exposes a trait. This is the boundary. No other module reaches into your internals.

```rust
// crates/orders/src/lib.rs

pub mod domain;
pub mod events;
mod handlers;
mod repository;

use async_trait::async_trait;
use domain::{Order, CreateOrderRequest, OrderError};
use uuid::Uuid;

/// Everything another module needs to interact with orders.
/// This trait IS the module boundary.
#[async_trait]
pub trait OrderModule: Send + Sync {
    async fn create_order(&self, req: CreateOrderRequest) -> Result<Order, OrderError>;
    async fn get_order(&self, id: Uuid) -> Result<Option<Order>, OrderError>;
    async fn cancel_order(&self, id: Uuid, reason: &str) -> Result<Order, OrderError>;
    async fn list_orders_for_customer(
        &self,
        customer_id: Uuid,
        page: u32,
    ) -> Result<Vec<Order>, OrderError>;
}

/// The concrete implementation. Other modules never see this directly —
/// they interact through `dyn OrderModule`.
pub struct OrderModuleImpl {
    repo: repository::OrderRepository,
    event_publisher: Box<dyn EventPublisher>,
}

impl OrderModuleImpl {
    pub fn new(pool: sqlx::PgPool, event_publisher: Box<dyn EventPublisher>) -> Self {
        Self {
            repo: repository::OrderRepository::new(pool),
            event_publisher,
        }
    }

    /// Return Axum routes for this module.
    /// The app binary merges all module routes together.
    pub fn routes(self: std::sync::Arc<Self>) -> axum::Router {
        handlers::routes(self)
    }
}

#[async_trait]
impl OrderModule for OrderModuleImpl {
    async fn create_order(&self, req: CreateOrderRequest) -> Result<Order, OrderError> {
        // Validate
        if req.items.is_empty() {
            return Err(OrderError::InvalidInput("order must have at least one item".into()));
        }

        // Create
        let order = self.repo.insert_order(&req).await?;

        // Publish event
        self.event_publisher.publish(events::OrderCreated {
            order_id: order.id,
            customer_id: order.customer_id,
            total_cents: order.total_cents,
        }).await?;

        Ok(order)
    }

    async fn get_order(&self, id: Uuid) -> Result<Option<Order>, OrderError> {
        self.repo.find_by_id(id).await
    }

    async fn cancel_order(&self, id: Uuid, reason: &str) -> Result<Order, OrderError> {
        let order = self.repo.find_by_id(id).await?
            .ok_or(OrderError::NotFound(id))?;

        if order.status != domain::OrderStatus::Pending {
            return Err(OrderError::InvalidStateTransition {
                from: order.status.clone(),
                to: domain::OrderStatus::Cancelled,
            });
        }

        let cancelled = self.repo.update_status(id, domain::OrderStatus::Cancelled).await?;

        self.event_publisher.publish(events::OrderCancelled {
            order_id: id,
            reason: reason.to_string(),
        }).await?;

        Ok(cancelled)
    }

    async fn list_orders_for_customer(
        &self,
        customer_id: Uuid,
        page: u32,
    ) -> Result<Vec<Order>, OrderError> {
        self.repo.find_by_customer(customer_id, page, 20).await
    }
}

/// Abstraction for publishing events.
/// In monolith mode: direct function calls.
/// After extraction: NATS/Kafka.
#[async_trait]
pub trait EventPublisher: Send + Sync {
    async fn publish(&self, event: impl serde::Serialize + Send + 'static) -> Result<(), OrderError>;
}
```

```rust
// crates/orders/src/domain.rs

use serde::{Deserialize, Serialize};
use uuid::Uuid;
use chrono::{DateTime, Utc};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Order {
    pub id: Uuid,
    pub customer_id: Uuid,
    pub status: OrderStatus,
    pub items: Vec<OrderItem>,
    pub total_cents: i64,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum OrderStatus {
    Pending,
    Confirmed,
    Shipped,
    Delivered,
    Cancelled,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrderItem {
    pub product_id: Uuid,
    pub quantity: u32,
    pub unit_price_cents: i64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct CreateOrderRequest {
    pub customer_id: Uuid,
    pub items: Vec<OrderItem>,
    pub idempotency_key: String,
}

#[derive(Debug, thiserror::Error)]
pub enum OrderError {
    #[error("order not found: {0}")]
    NotFound(Uuid),
    #[error("invalid input: {0}")]
    InvalidInput(String),
    #[error("invalid state transition from {from:?} to {to:?}")]
    InvalidStateTransition {
        from: OrderStatus,
        to: OrderStatus,
    },
    #[error("database error: {0}")]
    Database(#[from] sqlx::Error),
    #[error("internal error: {0}")]
    Internal(String),
}
```

## Inter-Module Communication — In-Process

Here's the key: modules talk to each other through traits, not HTTP calls. In monolith mode, the calls are just function calls — zero serialization, zero network overhead.

```rust
// crates/payments/src/lib.rs

use async_trait::async_trait;
use orders::OrderModule;
use std::sync::Arc;

pub struct PaymentModuleImpl {
    repo: PaymentRepository,
    // Depends on the ORDER MODULE'S TRAIT, not its implementation
    orders: Arc<dyn OrderModule>,
}

impl PaymentModuleImpl {
    pub fn new(
        pool: sqlx::PgPool,
        orders: Arc<dyn OrderModule>,
    ) -> Self {
        Self {
            repo: PaymentRepository::new(pool),
            orders,
        }
    }

    pub async fn process_payment(
        &self,
        order_id: uuid::Uuid,
    ) -> Result<Payment, PaymentError> {
        // Get order details — in monolith mode, this is a direct function call.
        // After extraction, this becomes an HTTP/gRPC call.
        // The calling code doesn't change.
        let order = self.orders.get_order(order_id)
            .await
            .map_err(|e| PaymentError::Internal(e.to_string()))?
            .ok_or(PaymentError::OrderNotFound(order_id))?;

        // Charge the card
        let payment = self.charge_card(&order).await?;

        // Save payment record
        self.repo.save(&payment).await?;

        Ok(payment)
    }

    async fn charge_card(&self, order: &orders::domain::Order) -> Result<Payment, PaymentError> {
        // Call Stripe/Adyen/etc.
        Ok(Payment {
            id: uuid::Uuid::new_v4(),
            order_id: order.id,
            amount_cents: order.total_cents,
            status: PaymentStatus::Captured,
        })
    }
}

#[derive(Debug)]
pub struct Payment {
    pub id: uuid::Uuid,
    pub order_id: uuid::Uuid,
    pub amount_cents: i64,
    pub status: PaymentStatus,
}

#[derive(Debug)]
pub enum PaymentStatus {
    Pending,
    Captured,
    Refunded,
    Failed,
}

#[derive(Debug, thiserror::Error)]
pub enum PaymentError {
    #[error("order not found: {0}")]
    OrderNotFound(uuid::Uuid),
    #[error("payment declined: {0}")]
    Declined(String),
    #[error("internal: {0}")]
    Internal(String),
}

struct PaymentRepository {
    pool: sqlx::PgPool,
}

impl PaymentRepository {
    fn new(pool: sqlx::PgPool) -> Self { Self { pool } }
    async fn save(&self, _payment: &Payment) -> Result<(), PaymentError> { Ok(()) }
}
```

## In-Process Event Bus

For events within the monolith, use a simple channel-based bus. No need for NATS when everything runs in the same process.

```rust
// crates/shared/src/events.rs

use async_trait::async_trait;
use std::sync::Arc;
use tokio::sync::broadcast;
use tracing::info;

/// In-process event bus using tokio broadcast channels.
/// Swap this for NATS/Kafka when you extract services.
pub struct InProcessEventBus {
    sender: broadcast::Sender<EventEnvelope>,
}

#[derive(Debug, Clone)]
pub struct EventEnvelope {
    pub event_type: String,
    pub payload: Vec<u8>,
}

impl InProcessEventBus {
    pub fn new(capacity: usize) -> Self {
        let (sender, _) = broadcast::channel(capacity);
        Self { sender }
    }

    pub fn publish(&self, event_type: &str, payload: Vec<u8>) {
        let envelope = EventEnvelope {
            event_type: event_type.to_string(),
            payload,
        };
        // Ignore error — it means no subscribers, which is fine
        let _ = self.sender.send(envelope);
    }

    pub fn subscribe(&self) -> broadcast::Receiver<EventEnvelope> {
        self.sender.subscribe()
    }
}

/// Start a background task that routes events to handlers.
pub async fn start_event_router(
    bus: Arc<InProcessEventBus>,
    handlers: Vec<(String, Box<dyn Fn(Vec<u8>) -> futures::future::BoxFuture<'static, ()> + Send + Sync>)>,
) {
    let mut rx = bus.subscribe();

    tokio::spawn(async move {
        while let Ok(envelope) = rx.recv().await {
            for (event_type, handler) in &handlers {
                if *event_type == envelope.event_type {
                    let payload = envelope.payload.clone();
                    handler(payload).await;
                }
            }
        }
    });
}
```

## The App Binary — Wiring It All Together

The `app` crate is where you compose everything into a running application. This is the only crate that knows about all modules.

```rust
// crates/app/src/main.rs

use std::sync::Arc;
use axum::Router;
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env())
        .json()
        .init();

    let database_url = std::env::var("DATABASE_URL")
        .unwrap_or_else(|_| "postgres://localhost/myplatform".into());

    let pool = sqlx::PgPool::connect(&database_url).await?;
    sqlx::migrate!("../../migrations").run(&pool).await?;

    // Create the in-process event bus
    let event_bus = Arc::new(shared::events::InProcessEventBus::new(1024));

    // Create the event publisher for the order module
    let order_event_pub = Box::new(InProcessPublisher {
        bus: event_bus.clone(),
    });

    // Initialize modules with their dependencies
    let order_module = Arc::new(
        orders::OrderModuleImpl::new(pool.clone(), order_event_pub)
    );

    let payment_module = Arc::new(
        payments::PaymentModuleImpl::new(pool.clone(), order_module.clone())
    );

    let inventory_module = Arc::new(
        inventory::InventoryModuleImpl::new(pool.clone())
    );

    // Compose routes from all modules
    let app = Router::new()
        .merge(order_module.clone().routes())
        .merge(payment_module.routes())
        .merge(inventory_module.routes())
        .layer(shared::auth::auth_layer())
        .layer(shared::telemetry::trace_layer());

    let listener = tokio::net::TcpListener::bind("0.0.0.0:8080").await?;
    tracing::info!("monolith listening on 0.0.0.0:8080");

    axum::serve(listener, app)
        .with_graceful_shutdown(shared::shutdown::signal())
        .await?;

    Ok(())
}

/// Adapter: InProcessEventBus → orders::EventPublisher
struct InProcessPublisher {
    bus: Arc<shared::events::InProcessEventBus>,
}

#[async_trait::async_trait]
impl orders::EventPublisher for InProcessPublisher {
    async fn publish(
        &self,
        event: impl serde::Serialize + Send + 'static,
    ) -> Result<(), orders::domain::OrderError> {
        let payload = serde_json::to_vec(&event)
            .map_err(|e| orders::domain::OrderError::Internal(e.to_string()))?;
        self.bus.publish("order_event", payload);
        Ok(())
    }
}
```

One binary. One deployment. One database connection pool. One set of logs. Simple to run, simple to debug, simple to deploy.

## Database Isolation Without Separate Databases

Each module owns its tables, but they share a database. Enforce the boundary with schemas:

```sql
-- migrations/001_initial.sql

-- Each module gets its own schema
CREATE SCHEMA orders;
CREATE SCHEMA payments;
CREATE SCHEMA inventory;

-- Order module tables
CREATE TABLE orders.orders (
    id UUID PRIMARY KEY,
    customer_id UUID NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    total_cents BIGINT NOT NULL,
    idempotency_key TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE orders.order_items (
    id UUID PRIMARY KEY,
    order_id UUID NOT NULL REFERENCES orders.orders(id),
    product_id UUID NOT NULL,
    quantity INT NOT NULL,
    unit_price_cents BIGINT NOT NULL
);

-- Payment module tables
CREATE TABLE payments.payments (
    id UUID PRIMARY KEY,
    order_id UUID NOT NULL, -- NO foreign key to orders schema!
    amount_cents BIGINT NOT NULL,
    status TEXT NOT NULL,
    provider_ref TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Inventory module tables
CREATE TABLE inventory.products (
    id UUID PRIMARY KEY,
    sku TEXT UNIQUE NOT NULL,
    stock_quantity INT NOT NULL DEFAULT 0
);

CREATE TABLE inventory.reservations (
    id UUID PRIMARY KEY,
    order_id UUID NOT NULL,
    product_id UUID NOT NULL REFERENCES inventory.products(id),
    quantity INT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Notice: no foreign keys between schemas. The `payments.payments` table has an `order_id` column but no `REFERENCES orders.orders(id)`. That's intentional. Cross-schema foreign keys create tight coupling — if you later extract payments to its own database, those constraints have to go anyway.

## When to Extract: The Decision Framework

You've been running your modular monolith for six months. Everything works. When do you actually split something out?

```rust
// This isn't code to run — it's a mental model.
// Score each criterion 0-2 for each module.

struct ExtractionScore {
    // Does this module need to scale independently?
    // 0: same scale as everything else
    // 1: somewhat different (2-3x)
    // 2: dramatically different (10x+)
    independent_scaling: u8,

    // Does a separate team own this module?
    // 0: same team works on everything
    // 1: different team touches it sometimes
    // 2: dedicated team with independent roadmap
    team_ownership: u8,

    // Does this module deploy on a different schedule?
    // 0: deploys with everything else
    // 1: occasionally needs independent deploys
    // 2: deploys multiple times daily while others are stable
    deployment_frequency: u8,

    // Are there compliance/security isolation requirements?
    // 0: no special requirements
    // 1: soft requirements (audit logging)
    // 2: hard requirements (PCI, HIPAA, separate VPC)
    compliance_isolation: u8,

    // How much inter-module communication is there?
    // 0: heavy communication (bad candidate for extraction)
    // 1: moderate (some calls, mostly independent)
    // 2: minimal (fire-and-forget events, rare queries)
    communication_independence: u8,
}

impl ExtractionScore {
    fn total(&self) -> u8 {
        self.independent_scaling
            + self.team_ownership
            + self.deployment_frequency
            + self.compliance_isolation
            + self.communication_independence
    }

    fn should_extract(&self) -> bool {
        // Extract when the score is 6+ out of 10
        self.total() >= 6
    }
}
```

## The Extraction Process

When it's time to split, here's the process:

1. **Create the HTTP/gRPC client implementation.** You already have the trait. Write a new implementation that makes network calls instead of direct function calls.

2. **Run both implementations in parallel.** The monolith still calls the module directly, but also sends the same request to the extracted service. Compare results. This is "dark launching."

3. **Switch traffic gradually.** Feature flag controls whether requests go to the in-process module or the extracted service. Start at 1%, watch metrics, increase.

4. **Remove the in-process module.** Once the extracted service handles 100% of traffic and you're confident, delete the module from the monolith.

```rust
// Step 1: HTTP client that implements the same trait
pub struct OrderServiceHttpClient {
    client: reqwest::Client,
    base_url: String,
}

#[async_trait]
impl OrderModule for OrderServiceHttpClient {
    async fn create_order(&self, req: CreateOrderRequest) -> Result<Order, OrderError> {
        let response = self.client
            .post(format!("{}/api/orders", self.base_url))
            .json(&req)
            .send()
            .await
            .map_err(|e| OrderError::Internal(e.to_string()))?;

        if response.status().is_success() {
            response.json().await
                .map_err(|e| OrderError::Internal(e.to_string()))
        } else {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            Err(OrderError::Internal(format!("{}: {}", status, body)))
        }
    }

    async fn get_order(&self, id: Uuid) -> Result<Option<Order>, OrderError> {
        let response = self.client
            .get(format!("{}/api/orders/{}", self.base_url, id))
            .send()
            .await
            .map_err(|e| OrderError::Internal(e.to_string()))?;

        match response.status().as_u16() {
            200 => Ok(Some(response.json().await
                .map_err(|e| OrderError::Internal(e.to_string()))?)),
            404 => Ok(None),
            status => Err(OrderError::Internal(format!("unexpected status: {}", status))),
        }
    }

    async fn cancel_order(&self, id: Uuid, reason: &str) -> Result<Order, OrderError> {
        let response = self.client
            .post(format!("{}/api/orders/{}/cancel", self.base_url, id))
            .json(&serde_json::json!({ "reason": reason }))
            .send()
            .await
            .map_err(|e| OrderError::Internal(e.to_string()))?;

        response.json().await
            .map_err(|e| OrderError::Internal(e.to_string()))
    }

    async fn list_orders_for_customer(
        &self,
        customer_id: Uuid,
        page: u32,
    ) -> Result<Vec<Order>, OrderError> {
        let response = self.client
            .get(format!(
                "{}/api/customers/{}/orders?page={}",
                self.base_url, customer_id, page
            ))
            .send()
            .await
            .map_err(|e| OrderError::Internal(e.to_string()))?;

        response.json().await
            .map_err(|e| OrderError::Internal(e.to_string()))
    }
}
```

The calling code in the payment module doesn't change at all. It still takes `Arc<dyn OrderModule>`. The wiring in `main.rs` switches from `OrderModuleImpl` to `OrderServiceHttpClient`. That's it.

## The Real Win

This is why I'm so insistent on the trait-based boundary pattern. Extraction isn't a rewrite. It's swapping an implementation. The trait guarantees both implementations have the same interface. The contract tests (Lesson 7) guarantee both implementations have the same behavior.

I've seen teams do this extraction in production with zero downtime. Module to microservice in a week, with confidence. Compare that to teams who built microservices from day one, realized their boundaries were wrong, and spent months untangling distributed spaghetti.

Start with the monolith. Get the boundaries right. Then split when — and only when — you have a real reason.

## Wrapping Up the Course

Over eight lessons, we've covered the full spectrum: from where to draw boundaries, through gRPC and event-driven communication, distributed transactions with sagas, service mesh integration, distributed tracing, testing strategies, and finally back to the beginning — start simple, split later.

The consistent theme: Rust's type system is your secret weapon for microservices. Traits define clean boundaries. Enums make event contracts exhaustive. The compiler catches contract violations at build time. Ownership semantics prevent data races in concurrent event handlers.

Microservices are a tool, not a goal. Use them when the operational complexity is justified by real needs — independent scaling, team autonomy, compliance isolation. Until then, a well-structured Rust monolith will take you further than most teams imagine.
