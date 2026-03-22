---
title: "Lesson 1: Service Boundaries and API Contracts — Where to draw the lines"
author: Atharva Pandey
series: "Rust Microservices Patterns"
lesson: 1
keywords: [Rust, rust, microservices, service boundaries, API contracts, domain-driven design, protobuf]
tags: [Rust tutorial, rust, microservices, architecture]
date: '2025-06-01T09:14:00.000Z'
---

I once joined a team that had 47 microservices for what was essentially a CRUD app with a payment flow. Forty-seven. Each one had its own database, its own deployment pipeline, its own on-call rotation. When a customer placed an order, the request bounced through eleven services before a confirmation email went out. Latency was terrible, debugging was a nightmare, and nobody could explain why the "UserPreferences" service existed separately from the "UserProfile" service.

That experience permanently changed how I think about service boundaries. The hard part of microservices isn't building them — Rust makes that relatively pleasant. The hard part is deciding *where to cut*.

## The Boundary Problem

Most teams draw service boundaries wrong because they optimize for the wrong thing. They look at their codebase, see logical groupings of code, and split along those lines. "Here's the user code, here's the order code, here's the notification code — three services!"

That's backwards. Service boundaries should follow *business domain boundaries*, not code organization boundaries. A service should encapsulate a business capability — something that makes sense to a product person, not just an engineer.

Here's my rule of thumb: if splitting two pieces of functionality into separate services means they need to talk to each other on every single request, you've drawn the line in the wrong place.

## Defining Contracts in Rust

Before you write a single handler, define your contracts. In Rust, we have a fantastic tool for this — the type system. I like to start with a shared types crate that both sides of a service boundary depend on.

```rust
// crates/contracts/src/lib.rs
// Shared types that define the contract between services

use serde::{Deserialize, Serialize};
use uuid::Uuid;
use chrono::{DateTime, Utc};

/// Every API response wraps its payload in this envelope.
/// Consistency across services matters more than you think.
#[derive(Debug, Serialize, Deserialize)]
pub struct ApiResponse<T: Serialize> {
    pub data: Option<T>,
    pub error: Option<ApiError>,
    pub request_id: String,
    pub timestamp: DateTime<Utc>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ApiError {
    pub code: ErrorCode,
    pub message: String,
    pub details: Option<serde_json::Value>,
}

#[derive(Debug, Serialize, Deserialize, Clone, Copy, PartialEq, Eq)]
pub enum ErrorCode {
    NotFound,
    InvalidInput,
    Conflict,
    Internal,
    Unauthorized,
    RateLimited,
    ServiceUnavailable,
}

impl<T: Serialize> ApiResponse<T> {
    pub fn success(data: T, request_id: String) -> Self {
        Self {
            data: Some(data),
            error: None,
            request_id,
            timestamp: Utc::now(),
        }
    }

    pub fn error(code: ErrorCode, message: impl Into<String>, request_id: String) -> Self {
        Self {
            data: None,
            error: Some(ApiError {
                code,
                message: message.into(),
                details: None,
            }),
            request_id,
            timestamp: Utc::now(),
        }
    }
}
```

Notice I'm not using generic `String` error messages — I'm using an enum. This forces every service to speak the same error language. When your frontend team asks "what errors can this endpoint return?", you point them at the enum. No ambiguity.

## Domain Events as Contracts

Service boundaries don't just define request/response contracts. They define *event* contracts — what one service tells the world when something interesting happens.

```rust
// crates/contracts/src/events.rs

use serde::{Deserialize, Serialize};
use uuid::Uuid;
use chrono::{DateTime, Utc};

/// Envelope for all domain events.
/// Every event carries enough context to be processed independently.
#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct DomainEvent<T: Serialize> {
    pub event_id: Uuid,
    pub aggregate_id: String,
    pub event_type: String,
    pub version: u32,
    pub occurred_at: DateTime<Utc>,
    pub payload: T,
    pub metadata: EventMetadata,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct EventMetadata {
    pub correlation_id: String,
    pub causation_id: String,
    pub source_service: String,
}

// Order domain events
#[derive(Debug, Serialize, Deserialize, Clone)]
pub enum OrderEvent {
    Created {
        customer_id: Uuid,
        items: Vec<OrderItem>,
        total_cents: i64,
    },
    Confirmed {
        confirmed_at: DateTime<Utc>,
        estimated_delivery: DateTime<Utc>,
    },
    Shipped {
        tracking_number: String,
        carrier: String,
    },
    Cancelled {
        reason: String,
        refund_amount_cents: i64,
    },
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct OrderItem {
    pub product_id: Uuid,
    pub quantity: u32,
    pub price_cents: i64,
}

// Payment domain events
#[derive(Debug, Serialize, Deserialize, Clone)]
pub enum PaymentEvent {
    Initiated {
        order_id: Uuid,
        amount_cents: i64,
        currency: String,
    },
    Authorized {
        provider_ref: String,
        authorized_at: DateTime<Utc>,
    },
    Captured {
        captured_amount_cents: i64,
    },
    Failed {
        reason: String,
        is_retryable: bool,
    },
}
```

This is where Rust shines compared to Go or Java. Those `OrderEvent` and `PaymentEvent` enums are *exhaustive*. When you add a new variant, the compiler finds every `match` statement in every service that handles these events and forces you to handle the new case. Try getting that guarantee in a language with string-based event types.

## The Service Trait Pattern

I like defining each service's public interface as a trait. This gives you a single place to see everything a service does, and it makes testing trivial.

```rust
// crates/order-service/src/lib.rs

use async_trait::async_trait;
use contracts::{ApiError, OrderEvent};
use uuid::Uuid;

#[derive(Debug, Clone)]
pub struct CreateOrderRequest {
    pub customer_id: Uuid,
    pub items: Vec<OrderItemRequest>,
    pub idempotency_key: String,
}

#[derive(Debug, Clone)]
pub struct OrderItemRequest {
    pub product_id: Uuid,
    pub quantity: u32,
}

#[derive(Debug, Clone)]
pub struct Order {
    pub id: Uuid,
    pub customer_id: Uuid,
    pub status: OrderStatus,
    pub items: Vec<OrderLineItem>,
    pub total_cents: i64,
}

#[derive(Debug, Clone, PartialEq)]
pub enum OrderStatus {
    Pending,
    Confirmed,
    Shipped,
    Delivered,
    Cancelled,
}

#[derive(Debug, Clone)]
pub struct OrderLineItem {
    pub product_id: Uuid,
    pub quantity: u32,
    pub unit_price_cents: i64,
    pub line_total_cents: i64,
}

/// The order service's public contract.
/// Everything a consumer needs to interact with orders.
#[async_trait]
pub trait OrderService: Send + Sync {
    async fn create_order(&self, req: CreateOrderRequest) -> Result<Order, ApiError>;
    async fn get_order(&self, id: Uuid) -> Result<Option<Order>, ApiError>;
    async fn confirm_order(&self, id: Uuid) -> Result<Order, ApiError>;
    async fn cancel_order(&self, id: Uuid, reason: String) -> Result<Order, ApiError>;
    async fn list_orders_for_customer(
        &self,
        customer_id: Uuid,
        page: u32,
        page_size: u32,
    ) -> Result<Vec<Order>, ApiError>;
}
```

Now any service that talks to the order service depends on `dyn OrderService`, not on HTTP calls or gRPC stubs directly. You can swap the implementation from an in-process call (monolith mode) to an HTTP client (microservice mode) without changing any calling code. This is crucial — it's how you go monolith-first and split later.

## Workspace Structure for Multi-Service Projects

Here's the Cargo workspace layout I've settled on after several projects:

```toml
# Cargo.toml (workspace root)
[workspace]
members = [
    "crates/contracts",
    "crates/shared",
    "crates/order-service",
    "crates/payment-service",
    "crates/notification-service",
    "crates/gateway",
]

[workspace.dependencies]
tokio = { version = "1", features = ["full"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
uuid = { version = "1", features = ["v4", "serde"] }
chrono = { version = "0.4", features = ["serde"] }
tracing = "0.1"
anyhow = "1"
async-trait = "0.1"
axum = "0.7"
```

The key insight: `contracts` and `shared` are library crates that contain zero business logic. `contracts` defines the *what* — types, events, API shapes. `shared` defines the *how* — common middleware, telemetry setup, health check endpoints.

Each service crate has its own `main.rs` and can be built and deployed independently. But during development, everything compiles together, and Rust's type checker validates cross-service contracts at compile time.

## API Versioning Strategy

I've been bitten by breaking API changes in microservices too many times. Here's the approach that's saved me:

```rust
// crates/contracts/src/versioned.rs

use serde::{Deserialize, Serialize};

/// API version negotiation.
/// Consumers declare the version they expect,
/// producers handle backward compatibility.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum ApiVersion {
    V1,
    V2,
}

/// V1 of the order response — never change this once shipped.
#[derive(Debug, Serialize, Deserialize)]
pub struct OrderResponseV1 {
    pub id: String,
    pub status: String,
    pub total: f64, // Regret: should have been cents from day one
}

/// V2 fixes the mistakes of V1.
#[derive(Debug, Serialize, Deserialize)]
pub struct OrderResponseV2 {
    pub id: String,
    pub status: String,
    pub total_cents: i64,
    pub currency: String,
    pub line_items: Vec<LineItemResponseV2>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct LineItemResponseV2 {
    pub product_id: String,
    pub quantity: u32,
    pub unit_price_cents: i64,
}

/// Conversion from the internal domain model to versioned responses.
/// The domain model is the source of truth — responses are projections.
impl From<&Order> for OrderResponseV1 {
    fn from(order: &Order) -> Self {
        Self {
            id: order.id.to_string(),
            status: format!("{:?}", order.status),
            total: order.total_cents as f64 / 100.0,
        }
    }
}

impl From<&Order> for OrderResponseV2 {
    fn from(order: &Order) -> Self {
        Self {
            id: order.id.to_string(),
            status: format!("{:?}", order.status),
            total_cents: order.total_cents,
            currency: "USD".to_string(),
            line_items: order.items.iter().map(|i| LineItemResponseV2 {
                product_id: i.product_id.to_string(),
                quantity: i.quantity,
                unit_price_cents: i.unit_price_cents,
            }).collect(),
        }
    }
}

// Usage: type-safe reference to the right Order type
pub use order_service::Order;
```

The trick is that your internal domain model never changes based on API version. You convert at the boundary. V1 consumers get V1 responses, V2 consumers get V2 responses, and your business logic doesn't care.

## Where Teams Go Wrong

After consulting on half a dozen microservice architectures, I see the same mistakes:

**Too many services too early.** Start with one. Split when you have a concrete reason — different scaling requirements, different team ownership, different deployment cadences. "It might need to scale independently someday" is not a reason.

**Shared databases.** If two services read and write the same tables, they're not separate services — they're a distributed monolith with network calls. Each service owns its data completely.

**Synchronous chains.** If Service A calls Service B which calls Service C which calls Service D to handle a single request, you've built a distributed stack trace, not a microservice architecture. Prefer events and async communication.

**No contract testing.** If you change a service's API and only find out it breaks consumers in production, your architecture is a house of cards. We'll cover this properly in Lesson 7.

## When to Split

Here's my checklist. You need at least two "yes" answers before splitting a service:

1. **Different scaling profile?** — The image processing module needs 10x the compute of the order module.
2. **Different team ownership?** — The payments team ships independently from the catalog team.
3. **Different deployment frequency?** — The recommendation engine changes daily, the auth service changes monthly.
4. **Different availability requirements?** — The search service can tolerate 30s of downtime, the payment service cannot.
5. **Data isolation requirement?** — PCI compliance means payment data must be in a separate, audited service.

If you only answer "yes" to one or none, keep it as a module inside a monolith. You can always split later if your boundaries are clean — and Rust's trait system makes those boundaries very clean.

## Coming Up

In the next lesson, we'll get concrete with gRPC services using `tonic`. We'll build a real service with proper error handling, streaming, and interceptors. The patterns from this lesson — the contract crate, the service trait, the workspace layout — will be the foundation for everything that follows.

The theme for this entire course: design decisions matter more than implementation details. Rust gives you incredible tools to enforce good design at compile time. But it can't tell you where to draw the lines. That part's on you.
