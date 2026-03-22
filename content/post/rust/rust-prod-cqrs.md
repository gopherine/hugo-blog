---
title: "Lesson 4: CQRS and Event Sourcing — Separating reads from writes"
author: Atharva Pandey
series: "Rust in Production — Real-World Architecture"
lesson: 4
keywords: [Rust, rust, architecture, production, CQRS, event sourcing, event-driven, domain events]
tags: [Rust tutorial, rust, architecture, production]
date: '2025-10-21T08:27:00.000Z'
---

We had this inventory service that was doing fine until it wasn't. Reads were simple — "how many units of product X are available?" Writes were complex — reservations, adjustments, transfers between warehouses, reconciliation with physical counts. Both read and write operations hit the same database table, the same data model, and the same set of queries that were getting increasingly gnarly.

Then we hit Black Friday. Read traffic spiked 40x. The complex write queries were locking rows that the read queries needed. We couldn't scale reads without scaling writes. We couldn't optimize the read path without breaking the write path's invariants.

That's when we split things apart.

## What CQRS Actually Is

CQRS — Command Query Responsibility Segregation — is a fancy name for a simple idea: **use different models for reading and writing data.**

Your write model enforces business rules and records what happened. Your read model is optimized for queries. They don't have to look the same. They don't have to live in the same database. They often shouldn't.

Event sourcing takes this further: instead of storing current state, you store the sequence of events that led to that state. Your "database" is an append-only log of domain events. Current state is derived by replaying those events.

Let me show you how this looks in Rust.

## Domain Events

Everything starts with events. An event is a fact — something that happened in the past tense:

```rust
// src/domain/events.rs

use chrono::{DateTime, Utc};
use serde::{Serialize, Deserialize};
use uuid::Uuid;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EventEnvelope {
    pub event_id: Uuid,
    pub aggregate_id: String,
    pub aggregate_type: String,
    pub sequence_number: u64,
    pub occurred_at: DateTime<Utc>,
    pub payload: DomainEvent,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum DomainEvent {
    // Inventory events
    ProductRegistered {
        product_id: String,
        name: String,
        sku: String,
        initial_quantity: u32,
    },
    StockReceived {
        product_id: String,
        warehouse_id: String,
        quantity: u32,
        reference: String,
    },
    StockReserved {
        product_id: String,
        order_id: String,
        quantity: u32,
    },
    ReservationCancelled {
        product_id: String,
        order_id: String,
        quantity: u32,
    },
    StockShipped {
        product_id: String,
        order_id: String,
        quantity: u32,
        warehouse_id: String,
    },
    StockAdjusted {
        product_id: String,
        warehouse_id: String,
        old_quantity: u32,
        new_quantity: u32,
        reason: String,
    },
}

impl EventEnvelope {
    pub fn new(
        aggregate_id: impl Into<String>,
        aggregate_type: impl Into<String>,
        sequence_number: u64,
        payload: DomainEvent,
    ) -> Self {
        Self {
            event_id: Uuid::new_v4(),
            aggregate_id: aggregate_id.into(),
            aggregate_type: aggregate_type.into(),
            sequence_number,
            occurred_at: Utc::now(),
            payload,
        }
    }
}
```

Events use `serde` because they *are* data. They're serialized to the event store, transmitted across services, replayed for projections. This is the one place where serialization is part of the domain contract.

## The Aggregate — Write Side

An aggregate is the write model. It processes commands, enforces invariants, and emits events:

```rust
// src/domain/inventory/aggregate.rs

use crate::domain::events::DomainEvent;
use std::collections::HashMap;

#[derive(Debug)]
pub struct InventoryAggregate {
    product_id: String,
    name: String,
    sku: String,
    total_stock: u32,
    reserved: u32,
    warehouse_stock: HashMap<String, u32>,
    reservations: HashMap<String, u32>, // order_id -> quantity
    version: u64,
    pending_events: Vec<DomainEvent>,
}

#[derive(Debug, thiserror::Error)]
pub enum InventoryError {
    #[error("insufficient stock: available {available}, requested {requested}")]
    InsufficientStock { available: u32, requested: u32 },
    #[error("reservation not found for order {0}")]
    ReservationNotFound(String),
    #[error("product already registered")]
    AlreadyRegistered,
    #[error("invalid quantity: {0}")]
    InvalidQuantity(String),
}

impl InventoryAggregate {
    /// Create a new aggregate from scratch
    pub fn register(
        product_id: String,
        name: String,
        sku: String,
        initial_quantity: u32,
    ) -> Self {
        let mut agg = Self {
            product_id: product_id.clone(),
            name: name.clone(),
            sku: sku.clone(),
            total_stock: initial_quantity,
            reserved: 0,
            warehouse_stock: HashMap::new(),
            reservations: HashMap::new(),
            version: 0,
            pending_events: Vec::new(),
        };

        agg.record(DomainEvent::ProductRegistered {
            product_id,
            name,
            sku,
            initial_quantity,
        });

        agg
    }

    /// Reconstruct aggregate from event history
    pub fn from_events(events: &[DomainEvent]) -> Option<Self> {
        let mut agg: Option<Self> = None;

        for event in events {
            match event {
                DomainEvent::ProductRegistered {
                    product_id, name, sku, initial_quantity,
                } => {
                    agg = Some(Self {
                        product_id: product_id.clone(),
                        name: name.clone(),
                        sku: sku.clone(),
                        total_stock: *initial_quantity,
                        reserved: 0,
                        warehouse_stock: HashMap::new(),
                        reservations: HashMap::new(),
                        version: 1,
                        pending_events: Vec::new(),
                    });
                }
                _ => {
                    if let Some(ref mut a) = agg {
                        a.apply(event);
                        a.version += 1;
                    }
                }
            }
        }

        agg
    }

    fn available_stock(&self) -> u32 {
        self.total_stock.saturating_sub(self.reserved)
    }

    /// Reserve stock for an order
    pub fn reserve(
        &mut self,
        order_id: String,
        quantity: u32,
    ) -> Result<(), InventoryError> {
        let available = self.available_stock();
        if quantity > available {
            return Err(InventoryError::InsufficientStock {
                available,
                requested: quantity,
            });
        }

        self.reserved += quantity;
        self.reservations.insert(order_id.clone(), quantity);

        self.record(DomainEvent::StockReserved {
            product_id: self.product_id.clone(),
            order_id,
            quantity,
        });

        Ok(())
    }

    /// Cancel a reservation
    pub fn cancel_reservation(
        &mut self,
        order_id: &str,
    ) -> Result<(), InventoryError> {
        let quantity = self.reservations.remove(order_id)
            .ok_or_else(|| InventoryError::ReservationNotFound(order_id.to_string()))?;

        self.reserved -= quantity;

        self.record(DomainEvent::ReservationCancelled {
            product_id: self.product_id.clone(),
            order_id: order_id.to_string(),
            quantity,
        });

        Ok(())
    }

    /// Receive new stock
    pub fn receive_stock(
        &mut self,
        warehouse_id: String,
        quantity: u32,
        reference: String,
    ) -> Result<(), InventoryError> {
        if quantity == 0 {
            return Err(InventoryError::InvalidQuantity(
                "cannot receive zero quantity".into()
            ));
        }

        self.total_stock += quantity;
        *self.warehouse_stock.entry(warehouse_id.clone()).or_insert(0) += quantity;

        self.record(DomainEvent::StockReceived {
            product_id: self.product_id.clone(),
            warehouse_id,
            quantity,
            reference,
        });

        Ok(())
    }

    fn apply(&mut self, event: &DomainEvent) {
        match event {
            DomainEvent::StockReceived { warehouse_id, quantity, .. } => {
                self.total_stock += quantity;
                *self.warehouse_stock.entry(warehouse_id.clone()).or_insert(0) += quantity;
            }
            DomainEvent::StockReserved { order_id, quantity, .. } => {
                self.reserved += quantity;
                self.reservations.insert(order_id.clone(), *quantity);
            }
            DomainEvent::ReservationCancelled { order_id, quantity, .. } => {
                self.reserved -= quantity;
                self.reservations.remove(order_id);
            }
            DomainEvent::StockShipped { quantity, warehouse_id, order_id, .. } => {
                self.total_stock -= quantity;
                self.reserved -= self.reservations.remove(order_id).unwrap_or(0);
                if let Some(ws) = self.warehouse_stock.get_mut(warehouse_id) {
                    *ws = ws.saturating_sub(*quantity);
                }
            }
            DomainEvent::StockAdjusted { warehouse_id, new_quantity, old_quantity, .. } => {
                let diff = *new_quantity as i64 - *old_quantity as i64;
                self.total_stock = (self.total_stock as i64 + diff) as u32;
                self.warehouse_stock.insert(warehouse_id.clone(), *new_quantity);
            }
            _ => {} // ProductRegistered handled in from_events
        }
    }

    fn record(&mut self, event: DomainEvent) {
        self.apply(&event);
        self.pending_events.push(event);
    }

    pub fn pending_events(&self) -> &[DomainEvent] {
        &self.pending_events
    }

    pub fn clear_pending_events(&mut self) {
        self.pending_events.clear();
    }

    pub fn version(&self) -> u64 {
        self.version
    }
}
```

The pattern: commands validate, produce events, and apply them. The `apply` method is pure state mutation — no validation, no side effects. This means replaying events always works, even if business rules have changed since the event was recorded.

## The Event Store

The event store is append-only. This is a critical property — you never update or delete events:

```rust
// src/infra/event_store.rs

use crate::domain::events::{DomainEvent, EventEnvelope};
use async_trait::async_trait;
use sqlx::PgPool;

#[async_trait]
pub trait EventStore: Send + Sync {
    async fn append(
        &self,
        aggregate_id: &str,
        aggregate_type: &str,
        expected_version: u64,
        events: Vec<DomainEvent>,
    ) -> Result<(), EventStoreError>;

    async fn load_events(
        &self,
        aggregate_id: &str,
    ) -> Result<Vec<DomainEvent>, EventStoreError>;

    async fn load_events_since(
        &self,
        aggregate_id: &str,
        since_version: u64,
    ) -> Result<Vec<DomainEvent>, EventStoreError>;
}

#[derive(Debug, thiserror::Error)]
pub enum EventStoreError {
    #[error("concurrency conflict: expected version {expected}, found {actual}")]
    ConcurrencyConflict { expected: u64, actual: u64 },
    #[error("storage error: {0}")]
    Storage(String),
}

pub struct PgEventStore {
    pool: PgPool,
}

impl PgEventStore {
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }
}

#[async_trait]
impl EventStore for PgEventStore {
    async fn append(
        &self,
        aggregate_id: &str,
        aggregate_type: &str,
        expected_version: u64,
        events: Vec<DomainEvent>,
    ) -> Result<(), EventStoreError> {
        let mut tx = self.pool.begin().await
            .map_err(|e| EventStoreError::Storage(e.to_string()))?;

        // Optimistic concurrency check
        let current_version: Option<(i64,)> = sqlx::query_as(
            "SELECT MAX(sequence_number) FROM events WHERE aggregate_id = $1 FOR UPDATE"
        )
        .bind(aggregate_id)
        .fetch_optional(&mut *tx)
        .await
        .map_err(|e| EventStoreError::Storage(e.to_string()))?;

        let actual_version = current_version
            .and_then(|r| r.0.map(|v| v as u64))
            .unwrap_or(0);

        if actual_version != expected_version {
            return Err(EventStoreError::ConcurrencyConflict {
                expected: expected_version,
                actual: actual_version,
            });
        }

        for (i, event) in events.iter().enumerate() {
            let envelope = EventEnvelope::new(
                aggregate_id,
                aggregate_type,
                expected_version + i as u64 + 1,
                event.clone(),
            );

            let payload = serde_json::to_value(&envelope.payload)
                .map_err(|e| EventStoreError::Storage(e.to_string()))?;

            sqlx::query(
                "INSERT INTO events (event_id, aggregate_id, aggregate_type,
                 sequence_number, occurred_at, payload)
                 VALUES ($1, $2, $3, $4, $5, $6)"
            )
            .bind(envelope.event_id)
            .bind(&envelope.aggregate_id)
            .bind(&envelope.aggregate_type)
            .bind(envelope.sequence_number as i64)
            .bind(envelope.occurred_at)
            .bind(payload)
            .execute(&mut *tx)
            .await
            .map_err(|e| EventStoreError::Storage(e.to_string()))?;
        }

        tx.commit().await
            .map_err(|e| EventStoreError::Storage(e.to_string()))?;

        Ok(())
    }

    async fn load_events(
        &self,
        aggregate_id: &str,
    ) -> Result<Vec<DomainEvent>, EventStoreError> {
        let rows: Vec<(serde_json::Value,)> = sqlx::query_as(
            "SELECT payload FROM events WHERE aggregate_id = $1
             ORDER BY sequence_number ASC"
        )
        .bind(aggregate_id)
        .fetch_all(&self.pool)
        .await
        .map_err(|e| EventStoreError::Storage(e.to_string()))?;

        rows.into_iter()
            .map(|(payload,)| {
                serde_json::from_value(payload)
                    .map_err(|e| EventStoreError::Storage(e.to_string()))
            })
            .collect()
    }

    async fn load_events_since(
        &self,
        aggregate_id: &str,
        since_version: u64,
    ) -> Result<Vec<DomainEvent>, EventStoreError> {
        let rows: Vec<(serde_json::Value,)> = sqlx::query_as(
            "SELECT payload FROM events WHERE aggregate_id = $1
             AND sequence_number > $2
             ORDER BY sequence_number ASC"
        )
        .bind(aggregate_id)
        .bind(since_version as i64)
        .fetch_all(&self.pool)
        .await
        .map_err(|e| EventStoreError::Storage(e.to_string()))?;

        rows.into_iter()
            .map(|(payload,)| {
                serde_json::from_value(payload)
                    .map_err(|e| EventStoreError::Storage(e.to_string()))
            })
            .collect()
    }
}
```

The `expected_version` parameter gives you optimistic concurrency control. If two concurrent writes try to modify the same aggregate, one will fail with a `ConcurrencyConflict` — and the application can retry.

## Projections — The Read Side

This is where CQRS shines. Your read model can be a completely different shape from your write model:

```rust
// src/infra/projections/inventory_view.rs

use crate::domain::events::DomainEvent;
use sqlx::PgPool;

pub struct InventoryProjection {
    pool: PgPool,
}

impl InventoryProjection {
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }

    pub async fn handle_event(&self, event: &DomainEvent) -> Result<(), ProjectionError> {
        match event {
            DomainEvent::ProductRegistered {
                product_id, name, sku, initial_quantity,
            } => {
                sqlx::query(
                    "INSERT INTO inventory_view
                     (product_id, name, sku, total_stock, available_stock, reserved_stock)
                     VALUES ($1, $2, $3, $4, $4, 0)"
                )
                .bind(product_id)
                .bind(name)
                .bind(sku)
                .bind(*initial_quantity as i64)
                .execute(&self.pool)
                .await?;
            }

            DomainEvent::StockReceived { product_id, quantity, .. } => {
                sqlx::query(
                    "UPDATE inventory_view
                     SET total_stock = total_stock + $2,
                         available_stock = available_stock + $2,
                         updated_at = NOW()
                     WHERE product_id = $1"
                )
                .bind(product_id)
                .bind(*quantity as i64)
                .execute(&self.pool)
                .await?;
            }

            DomainEvent::StockReserved { product_id, quantity, .. } => {
                sqlx::query(
                    "UPDATE inventory_view
                     SET reserved_stock = reserved_stock + $2,
                         available_stock = available_stock - $2,
                         updated_at = NOW()
                     WHERE product_id = $1"
                )
                .bind(product_id)
                .bind(*quantity as i64)
                .execute(&self.pool)
                .await?;
            }

            DomainEvent::ReservationCancelled { product_id, quantity, .. } => {
                sqlx::query(
                    "UPDATE inventory_view
                     SET reserved_stock = reserved_stock - $2,
                         available_stock = available_stock + $2,
                         updated_at = NOW()
                     WHERE product_id = $1"
                )
                .bind(product_id)
                .bind(*quantity as i64)
                .execute(&self.pool)
                .await?;
            }

            _ => {} // other events don't affect this projection
        }

        Ok(())
    }
}
```

The `inventory_view` table is denormalized, pre-computed, and fast to query. It's eventually consistent with the event store — but for most read use cases, that's perfectly fine. And because it's derived from events, you can rebuild it any time by replaying the event log.

Want a different view? Write a different projection. Want real-time analytics? Project into a time-series database. Want search? Project into Elasticsearch. Each projection is independent.

## The Command Handler

Putting it together — commands flow through a handler that loads the aggregate, applies the command, and saves the events:

```rust
// src/application/commands.rs

use crate::domain::inventory::aggregate::{InventoryAggregate, InventoryError};
use crate::infra::event_store::{EventStore, EventStoreError};
use crate::infra::projections::inventory_view::InventoryProjection;

pub struct InventoryCommandHandler<S: EventStore> {
    store: S,
    projection: InventoryProjection,
}

impl<S: EventStore> InventoryCommandHandler<S> {
    pub fn new(store: S, projection: InventoryProjection) -> Self {
        Self { store, projection }
    }

    pub async fn reserve_stock(
        &self,
        product_id: &str,
        order_id: String,
        quantity: u32,
    ) -> Result<(), CommandError> {
        // Load aggregate from events
        let events = self.store.load_events(product_id).await?;
        let mut aggregate = InventoryAggregate::from_events(&events)
            .ok_or(CommandError::AggregateNotFound(product_id.to_string()))?;

        // Execute command
        aggregate.reserve(order_id, quantity)?;

        // Save new events
        let new_events = aggregate.pending_events().to_vec();
        self.store.append(
            product_id,
            "Inventory",
            aggregate.version(),
            new_events.clone(),
        ).await?;

        // Update read model
        for event in &new_events {
            self.projection.handle_event(event).await
                .map_err(|e| {
                    tracing::error!("projection update failed: {}", e);
                    // Don't fail the command — projection can catch up later
                })
                .ok();
        }

        Ok(())
    }
}

#[derive(Debug, thiserror::Error)]
pub enum CommandError {
    #[error("aggregate not found: {0}")]
    AggregateNotFound(String),
    #[error(transparent)]
    Domain(#[from] InventoryError),
    #[error(transparent)]
    Store(#[from] EventStoreError),
}
```

Notice: if the projection update fails, we log it but don't fail the command. The event is already persisted — the projection can catch up later. This is a deliberate trade-off: write-side consistency is strict, read-side consistency is eventual.

## Snapshots for Performance

As your event stream grows, replaying all events to rebuild an aggregate gets slow. Snapshots solve this:

```rust
pub struct Snapshot {
    pub aggregate_id: String,
    pub version: u64,
    pub state: serde_json::Value,
}

// Loading with snapshot:
async fn load_aggregate(
    store: &impl EventStore,
    snapshot_store: &impl SnapshotStore,
    aggregate_id: &str,
) -> Option<InventoryAggregate> {
    // Try loading from snapshot first
    if let Some(snapshot) = snapshot_store.load(aggregate_id).await.ok().flatten() {
        let mut aggregate: InventoryAggregate =
            serde_json::from_value(snapshot.state).ok()?;

        // Load only events since snapshot
        let new_events = store
            .load_events_since(aggregate_id, snapshot.version)
            .await
            .ok()?;

        for event in &new_events {
            aggregate.apply(event);
        }

        Some(aggregate)
    } else {
        // No snapshot — replay from beginning
        let events = store.load_events(aggregate_id).await.ok()?;
        InventoryAggregate::from_events(&events)
    }
}
```

Take a snapshot every N events (I usually do every 100). The cost is storing one extra row; the benefit is loading an aggregate in one read instead of replaying thousands of events.

## When CQRS Makes Sense

CQRS adds complexity. Don't use it everywhere. Use it when:

- **Read and write models have different shapes.** Your write model enforces invariants; your read model is denormalized for queries.
- **Read and write traffic scale differently.** Scaling reads independently is a massive operational win.
- **You need an audit trail.** Event sourcing gives you a complete, immutable history for free.
- **Multiple projections serve different needs.** Dashboard view, search index, analytics — all from the same events.

Don't use it for simple CRUD. Don't use it because it sounds cool in a conference talk. Use it when the problem demands it.

Next: how to organize all of this across multiple crates in a Rust workspace — because a single `Cargo.toml` stops scaling at around 50,000 lines of code.
