---
title: "Lesson 3: Event-Driven Architecture in Rust — Decoupled systems"
author: Atharva Pandey
series: "Rust Microservices Patterns"
lesson: 3
keywords: [Rust, rust, microservices, event-driven, NATS, message queue, pub/sub, CQRS]
tags: [Rust tutorial, rust, microservices, architecture]
date: '2025-06-05T11:02:00.000Z'
---

The worst production incident I ever dealt with was a cascading failure triggered by a single slow database query. Service A called Service B synchronously, which called Service C, which ran a query that usually took 2ms but on that particular Tuesday took 45 seconds because of a missing index on a new column. Service A's thread pool exhausted, its health check failed, Kubernetes restarted it, and for twenty minutes the entire checkout flow was down — because of a read query in a recommendation engine.

That's the day I truly understood why event-driven architecture exists. It's not about being trendy. It's about not having your payment system die because your recommendation engine had a bad day.

## Synchronous Coupling Is the Real Problem

Let's be clear about what we're solving. The issue isn't REST vs gRPC vs GraphQL. The issue is *temporal coupling* — Service A can only succeed if Service B responds successfully, right now, in this request cycle.

In an event-driven system, Service A publishes an event and moves on. Service B picks it up when it's ready. If B is down, the event sits in a queue. If B is slow, A doesn't notice. If B throws an error, A is already done.

This isn't free — you trade consistency for availability. You trade simplicity for resilience. Those are real tradeoffs, and for many workflows they're the right ones.

## The Event Bus Abstraction

First, let's build an abstraction over our event bus. I like NATS for Rust projects — it's fast, operationally simple, and the Rust client is excellent. But the point of the abstraction is that you should be able to swap it.

```rust
// src/events/bus.rs

use async_trait::async_trait;
use serde::{de::DeserializeOwned, Serialize};
use std::fmt::Debug;

/// An event that can be published to the bus.
/// We require Clone so events can be retried.
pub trait Event: Serialize + DeserializeOwned + Clone + Debug + Send + Sync + 'static {
    /// The subject/topic this event publishes to.
    /// Convention: "domain.entity.action" (e.g., "orders.order.created")
    fn subject(&self) -> &str;

    /// Unique event ID for deduplication
    fn event_id(&self) -> &str;
}

/// Abstraction over the message bus.
/// Implementations handle serialization, delivery guarantees,
/// and connection management.
#[async_trait]
pub trait EventBus: Send + Sync + 'static {
    async fn publish<E: Event>(&self, event: &E) -> Result<(), EventBusError>;

    async fn subscribe(
        &self,
        subject: &str,
        handler: Box<dyn EventHandler>,
    ) -> Result<SubscriptionHandle, EventBusError>;
}

#[async_trait]
pub trait EventHandler: Send + Sync + 'static {
    async fn handle(&self, payload: &[u8]) -> Result<(), HandlerError>;
}

#[derive(Debug)]
pub struct SubscriptionHandle {
    pub id: String,
}

#[derive(Debug, thiserror::Error)]
pub enum EventBusError {
    #[error("connection failed: {0}")]
    Connection(String),
    #[error("publish failed: {0}")]
    Publish(String),
    #[error("subscribe failed: {0}")]
    Subscribe(String),
    #[error("serialization error: {0}")]
    Serialization(String),
}

#[derive(Debug, thiserror::Error)]
pub enum HandlerError {
    #[error("transient error (will retry): {0}")]
    Transient(String),
    #[error("permanent error (dead letter): {0}")]
    Permanent(String),
}
```

The key decision here: `HandlerError` distinguishes between transient and permanent failures. A transient error means "try again later" — network blip, database connection lost, temporary rate limit. A permanent error means "this message is broken, stop trying" — invalid payload, business rule violation, missing required data. This distinction drives the retry and dead-letter logic.

## NATS Implementation

```rust
// src/events/nats_bus.rs

use super::bus::*;
use async_nats::Client;
use serde::{de::DeserializeOwned, Serialize};
use std::sync::Arc;
use tracing::{error, info, warn};

pub struct NatsBus {
    client: Client,
    service_name: String,
}

impl NatsBus {
    pub async fn connect(
        url: &str,
        service_name: &str,
    ) -> Result<Self, EventBusError> {
        let client = async_nats::connect(url)
            .await
            .map_err(|e| EventBusError::Connection(e.to_string()))?;

        info!(url = %url, service = %service_name, "connected to NATS");

        Ok(Self {
            client,
            service_name: service_name.to_string(),
        })
    }
}

#[async_trait::async_trait]
impl EventBus for NatsBus {
    async fn publish<E: Event>(&self, event: &E) -> Result<(), EventBusError> {
        let payload = serde_json::to_vec(event)
            .map_err(|e| EventBusError::Serialization(e.to_string()))?;

        self.client
            .publish(event.subject().to_string(), payload.into())
            .await
            .map_err(|e| EventBusError::Publish(e.to_string()))?;

        info!(
            subject = %event.subject(),
            event_id = %event.event_id(),
            "event published"
        );

        Ok(())
    }

    async fn subscribe(
        &self,
        subject: &str,
        handler: Box<dyn EventHandler>,
    ) -> Result<SubscriptionHandle, EventBusError> {
        let mut subscriber = self.client
            .subscribe(subject.to_string())
            .await
            .map_err(|e| EventBusError::Subscribe(e.to_string()))?;

        let handler = Arc::new(handler);
        let subject = subject.to_string();

        tokio::spawn(async move {
            while let Some(msg) = subscriber.next().await {
                let handler = handler.clone();
                let subject = subject.clone();

                tokio::spawn(async move {
                    match handler.handle(&msg.payload).await {
                        Ok(()) => {
                            info!(subject = %subject, "event handled successfully");
                        }
                        Err(HandlerError::Transient(e)) => {
                            warn!(
                                subject = %subject,
                                error = %e,
                                "transient handler error — message will be retried"
                            );
                            // In JetStream, you'd NAK the message here
                        }
                        Err(HandlerError::Permanent(e)) => {
                            error!(
                                subject = %subject,
                                error = %e,
                                "permanent handler error — sending to dead letter"
                            );
                            // Publish to dead letter subject
                        }
                    }
                });
            }
        });

        Ok(SubscriptionHandle {
            id: uuid::Uuid::new_v4().to_string(),
        })
    }
}

// Helper: subscribe with automatic deserialization
use tokio::sync::mpsc;
use std::marker::PhantomData;

pub struct TypedHandler<E, F>
where
    E: Event,
    F: Fn(E) -> futures::future::BoxFuture<'static, Result<(), HandlerError>> + Send + Sync + 'static,
{
    handler_fn: F,
    _phantom: PhantomData<E>,
}

impl<E, F> TypedHandler<E, F>
where
    E: Event,
    F: Fn(E) -> futures::future::BoxFuture<'static, Result<(), HandlerError>> + Send + Sync + 'static,
{
    pub fn new(handler_fn: F) -> Self {
        Self {
            handler_fn,
            _phantom: PhantomData,
        }
    }
}

#[async_trait::async_trait]
impl<E, F> EventHandler for TypedHandler<E, F>
where
    E: Event,
    F: Fn(E) -> futures::future::BoxFuture<'static, Result<(), HandlerError>> + Send + Sync + 'static,
{
    async fn handle(&self, payload: &[u8]) -> Result<(), HandlerError> {
        let event: E = serde_json::from_slice(payload)
            .map_err(|e| HandlerError::Permanent(format!("deserialization failed: {}", e)))?;
        (self.handler_fn)(event).await
    }
}
```

## Domain Events in Practice

Let's define real domain events and show how services react to them:

```rust
// src/events/domain.rs

use serde::{Deserialize, Serialize};
use uuid::Uuid;
use chrono::{DateTime, Utc};
use super::bus::Event;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrderCreated {
    pub event_id: String,
    pub order_id: Uuid,
    pub customer_id: Uuid,
    pub items: Vec<OrderItemData>,
    pub total_cents: i64,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrderItemData {
    pub product_id: Uuid,
    pub quantity: u32,
    pub price_cents: i64,
}

impl Event for OrderCreated {
    fn subject(&self) -> &str { "orders.order.created" }
    fn event_id(&self) -> &str { &self.event_id }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PaymentCompleted {
    pub event_id: String,
    pub order_id: Uuid,
    pub amount_cents: i64,
    pub provider: String,
    pub provider_ref: String,
    pub completed_at: DateTime<Utc>,
}

impl Event for PaymentCompleted {
    fn subject(&self) -> &str { "payments.payment.completed" }
    fn event_id(&self) -> &str { &self.event_id }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PaymentFailed {
    pub event_id: String,
    pub order_id: Uuid,
    pub reason: String,
    pub is_retryable: bool,
    pub failed_at: DateTime<Utc>,
}

impl Event for PaymentFailed {
    fn subject(&self) -> &str { "payments.payment.failed" }
    fn event_id(&self) -> &str { &self.event_id }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InventoryReserved {
    pub event_id: String,
    pub order_id: Uuid,
    pub reservations: Vec<ReservationData>,
    pub reserved_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReservationData {
    pub product_id: Uuid,
    pub quantity: u32,
    pub warehouse_id: String,
}

impl Event for InventoryReserved {
    fn subject(&self) -> &str { "inventory.reservation.created" }
    fn event_id(&self) -> &str { &self.event_id }
}
```

## The Order Flow — Event-Driven Style

Here's how an order actually flows through the system without a single synchronous inter-service call:

```rust
// src/order_service.rs — the publisher side

use crate::events::{bus::EventBus, domain::*};
use uuid::Uuid;

pub struct OrderService<B: EventBus> {
    bus: B,
    // db, etc.
}

impl<B: EventBus> OrderService<B> {
    pub async fn create_order(
        &self,
        customer_id: Uuid,
        items: Vec<OrderItemData>,
    ) -> Result<Uuid, anyhow::Error> {
        let order_id = Uuid::new_v4();
        let total_cents: i64 = items.iter()
            .map(|i| i.price_cents * i.quantity as i64)
            .sum();

        // 1. Save order to local database (status: PENDING)
        // self.db.insert_order(...).await?;

        // 2. Publish event — other services react asynchronously
        let event = OrderCreated {
            event_id: Uuid::new_v4().to_string(),
            order_id,
            customer_id,
            items,
            total_cents,
            created_at: chrono::Utc::now(),
        };

        self.bus.publish(&event).await?;

        // 3. Return immediately — we don't wait for payment,
        //    inventory, or notification services
        Ok(order_id)
    }
}
```

```rust
// src/payment_listener.rs — the subscriber side

use crate::events::{bus::*, domain::*};
use tracing::{info, error};

pub struct PaymentListener {
    // payment provider client, database, etc.
}

impl PaymentListener {
    /// Subscribe to order events and initiate payment processing.
    pub async fn start(self, bus: &impl EventBus) -> Result<(), EventBusError> {
        let handler = TypedHandler::new(move |event: OrderCreated| {
            Box::pin(async move {
                info!(
                    order_id = %event.order_id,
                    amount = event.total_cents,
                    "initiating payment for order"
                );

                // Call payment provider
                match process_payment(event.order_id, event.total_cents).await {
                    Ok(provider_ref) => {
                        info!(
                            order_id = %event.order_id,
                            provider_ref = %provider_ref,
                            "payment succeeded"
                        );
                        // Publish PaymentCompleted event
                        // (In reality, you'd inject the bus here)
                        Ok(())
                    }
                    Err(e) if e.is_retryable() => {
                        Err(HandlerError::Transient(format!(
                            "payment provider error: {}",
                            e
                        )))
                    }
                    Err(e) => {
                        Err(HandlerError::Permanent(format!(
                            "payment permanently failed: {}",
                            e
                        )))
                    }
                }
            })
        });

        bus.subscribe("orders.order.created", Box::new(handler)).await?;
        Ok(())
    }
}

// Placeholder for actual payment processing
async fn process_payment(
    _order_id: uuid::Uuid,
    _amount_cents: i64,
) -> Result<String, PaymentError> {
    // Call Stripe, Adyen, etc.
    Ok("pi_abc123".to_string())
}

#[derive(Debug)]
struct PaymentError {
    message: String,
    retryable: bool,
}

impl PaymentError {
    fn is_retryable(&self) -> bool {
        self.retryable
    }
}

impl std::fmt::Display for PaymentError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.message)
    }
}
```

## Idempotent Handlers

This is the single most important pattern in event-driven systems. Events can be delivered more than once — at-least-once delivery is the norm, not the exception. Every handler must be idempotent.

```rust
// src/events/idempotent.rs

use std::collections::HashSet;
use tokio::sync::RwLock;

/// Simple in-memory deduplication.
/// In production, use Redis or a database table.
pub struct IdempotencyGuard {
    processed_ids: RwLock<HashSet<String>>,
}

impl IdempotencyGuard {
    pub fn new() -> Self {
        Self {
            processed_ids: RwLock::new(HashSet::new()),
        }
    }

    /// Returns true if this event has already been processed.
    /// Marks it as processed if not.
    pub async fn check_and_mark(&self, event_id: &str) -> bool {
        // First, check with a read lock (fast path)
        {
            let ids = self.processed_ids.read().await;
            if ids.contains(event_id) {
                return true; // Already processed
            }
        }

        // Not seen — acquire write lock and insert
        let mut ids = self.processed_ids.write().await;
        // Double-check after acquiring write lock
        if ids.contains(event_id) {
            return true;
        }
        ids.insert(event_id.to_string());
        false
    }
}

/// Wrapper that adds idempotency to any handler.
pub struct IdempotentHandler<H: EventHandler> {
    inner: H,
    guard: IdempotencyGuard,
}

impl<H: EventHandler> IdempotentHandler<H> {
    pub fn new(inner: H) -> Self {
        Self {
            inner,
            guard: IdempotencyGuard::new(),
        }
    }
}

#[async_trait::async_trait]
impl<H: EventHandler> EventHandler for IdempotentHandler<H> {
    async fn handle(&self, payload: &[u8]) -> Result<(), HandlerError> {
        // Extract event_id from payload
        let envelope: serde_json::Value = serde_json::from_slice(payload)
            .map_err(|e| HandlerError::Permanent(e.to_string()))?;

        let event_id = envelope["event_id"]
            .as_str()
            .ok_or_else(|| HandlerError::Permanent("missing event_id".to_string()))?;

        if self.guard.check_and_mark(event_id).await {
            tracing::debug!(event_id = %event_id, "duplicate event, skipping");
            return Ok(());
        }

        self.inner.handle(payload).await
    }
}
```

## The Outbox Pattern

There's a subtle but devastating bug in the order service code above. See it? We save the order to the database, *then* publish the event. If the publish fails after the DB commit, we have an order with no event — downstream services never learn about it.

The fix is the transactional outbox pattern:

```rust
// src/events/outbox.rs

use sqlx::PgPool;
use uuid::Uuid;
use chrono::Utc;

/// Instead of publishing events directly, write them to an outbox
/// table in the same transaction as your business data.
/// A background process reads the outbox and publishes to the bus.
pub struct OutboxWriter {
    pool: PgPool,
}

impl OutboxWriter {
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }

    /// Write an event to the outbox within an existing transaction.
    pub async fn write_event(
        tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
        subject: &str,
        event_id: &str,
        payload: &serde_json::Value,
    ) -> Result<(), sqlx::Error> {
        sqlx::query(
            r#"
            INSERT INTO event_outbox (id, subject, payload, created_at, published)
            VALUES ($1, $2, $3, $4, false)
            "#,
        )
        .bind(event_id)
        .bind(subject)
        .bind(payload)
        .bind(Utc::now())
        .execute(&mut **tx)
        .await?;

        Ok(())
    }
}

/// Background publisher that reads from the outbox
/// and publishes to the event bus.
pub struct OutboxPublisher<B: super::bus::EventBus> {
    pool: PgPool,
    bus: B,
}

impl<B: super::bus::EventBus> OutboxPublisher<B> {
    pub fn new(pool: PgPool, bus: B) -> Self {
        Self { pool, bus }
    }

    /// Poll the outbox table and publish unpublished events.
    /// Run this in a background task.
    pub async fn run(&self) -> Result<(), anyhow::Error> {
        loop {
            let events: Vec<OutboxRow> = sqlx::query_as(
                r#"
                SELECT id, subject, payload, created_at
                FROM event_outbox
                WHERE published = false
                ORDER BY created_at ASC
                LIMIT 100
                FOR UPDATE SKIP LOCKED
                "#,
            )
            .fetch_all(&self.pool)
            .await?;

            if events.is_empty() {
                tokio::time::sleep(tokio::time::Duration::from_millis(100)).await;
                continue;
            }

            for event in &events {
                // Publish to NATS/Kafka/whatever
                self.bus.publish_raw(&event.subject, &event.payload).await?;

                // Mark as published
                sqlx::query("UPDATE event_outbox SET published = true WHERE id = $1")
                    .bind(&event.id)
                    .execute(&self.pool)
                    .await?;
            }

            tracing::info!(count = events.len(), "published outbox events");
        }
    }
}

#[derive(sqlx::FromRow)]
struct OutboxRow {
    id: String,
    subject: String,
    payload: serde_json::Value,
    created_at: chrono::DateTime<Utc>,
}
```

Now your order creation looks like this:

```rust
pub async fn create_order_safe(
    &self,
    customer_id: Uuid,
    items: Vec<OrderItemData>,
) -> Result<Uuid, anyhow::Error> {
    let order_id = Uuid::new_v4();
    let event_id = Uuid::new_v4().to_string();

    let mut tx = self.pool.begin().await?;

    // Insert order
    sqlx::query("INSERT INTO orders (id, customer_id, status) VALUES ($1, $2, 'PENDING')")
        .bind(order_id)
        .bind(customer_id)
        .execute(&mut *tx)
        .await?;

    // Write event to outbox in the SAME transaction
    let event_payload = serde_json::json!({
        "event_id": event_id,
        "order_id": order_id,
        "customer_id": customer_id,
        "items": items,
    });

    OutboxWriter::write_event(
        &mut tx,
        "orders.order.created",
        &event_id,
        &event_payload,
    ).await?;

    // Both succeed or both fail — no split-brain
    tx.commit().await?;

    Ok(order_id)
}
```

This guarantees that if the order is saved, the event will eventually be published. No split-brain, no lost events.

## When Not to Go Event-Driven

I want to be honest about when this approach is overkill:

- **Request/response workflows** where the caller genuinely needs an answer right now — "is this coupon valid?" doesn't need an event.
- **Simple CRUD** with few consumers — if only one service cares about an entity, just call it directly.
- **Strong consistency requirements** — if you need a real-time accurate inventory count before allowing a purchase, eventual consistency might not cut it.

Event-driven architecture is powerful, but it's also harder to debug, harder to test, and harder to reason about. Use it where you need the decoupling, not everywhere by default.

Next up — what happens when a multi-step process fails halfway through? That's the saga pattern, and it's where things get really interesting.
