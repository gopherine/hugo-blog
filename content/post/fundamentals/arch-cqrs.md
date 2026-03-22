---
title: "Lesson 4: CQRS — When reads and writes need different models"
author: Atharva Pandey
keywords:
  - CQRS
  - command query responsibility segregation
  - read model
  - write model
  - software architecture
  - backend engineering
tags:
  - fundamentals
  - architecture
series: "Software Architecture That Survives"
lesson: 4
date: '2024-06-21T00:00:00.000Z'
---

I maintained an e-commerce admin dashboard that had a query so complex it took 12 seconds to run. It joined seven tables across the orders, inventory, and customers domains to build a summary view of "all orders pending fulfillment, with customer tier, item details, and warehouse stock levels." Every time the admin loaded the page, twelve seconds. We tried indexes, caching, materialized views — all helped at the margins. The fundamental problem was that we were asking our write-optimized relational model to answer a read-optimized reporting question. Separating the read model was the only real fix.

## How It Works

CQRS stands for Command Query Responsibility Segregation. The core idea is simple: the model you use to change state (commands) doesn't have to be the same model you use to query state (queries). They can be different data stores, different data shapes, different even technologies.

**The Traditional Approach (no CQRS)**

```
                   ┌──────────────────┐
Request ──────────▶│   Single Model   │
                   │   (Postgres)     │
Response ◀─────────│                  │
                   └──────────────────┘
```

One database, one schema, serving both writes (INSERT/UPDATE) and reads (SELECT). Your write model is normalized for consistency. Your read model needs denormalized views for performance. These two needs constantly fight each other.

**CQRS Split**

```
Write path:
Command ──────▶ Write Model (normalized DB) ──────▶ Domain Events
                (Postgres, optimized for                    │
                 consistency, transactions)                 │
                                                            ▼
Read path:                                        ┌────────────────────┐
Query ────────▶ Read Model (denormalized) ◀───────│ Projection Builder │
                (Elasticsearch, Redis,             │ (consumes events)  │
                 read-optimized Postgres view)     └────────────────────┘
```

The write side handles commands: validate, enforce business rules, update the authoritative store, emit events. The read side handles queries: serve from a projection that's been pre-shaped for the exact query your UI needs.

**Projections**

A projection is a read model built by consuming domain events. When an `OrderConfirmed` event fires, the projection builder updates the `orders_summary` read model. When an `ItemShipped` event fires, it updates again. The read model is eventually consistent — it lags the write model by however long it takes to process the event.

The power of projections: you can have multiple read models optimized for different queries, all derived from the same event stream. Add a new reporting requirement → add a new projection. Don't touch the write model.

**Levels of CQRS**

CQRS exists on a spectrum. You don't have to go full separate databases:

1. **Method-level** (lightest): `OrderService.CreateOrder()` for writes, `OrderQueryService.GetOrderSummary()` for reads. Same database, different code paths. Often just separating the two responsibilities in code is enough.

2. **Schema-level**: Write tables normalized (orders, order_items, customers). Read views denormalized (materialized view joining all three, refreshed on demand). One database, two schemas.

3. **Database-level** (full CQRS): Separate databases. Write to Postgres, read from Elasticsearch or a Redis read model. Projection built asynchronously from events.

Start with level 1. Escalate only when you can measure the need.

## Why It Matters

Three concrete situations where CQRS pays off:

1. **Complex reporting queries**: Dashboard queries that aggregate across multiple domains are naturally denormalized. If you pre-build the projection, the query becomes a simple key lookup.

2. **Different scaling needs for reads and writes**: Write load and read load scale differently. With a single model, you scale both together. With CQRS, scale your read replicas independently from your write primary.

3. **Temporal read requirements**: "Show me the state of this order as it was last Tuesday." With event sourcing + CQRS, this is a projection built from events up to that timestamp. With a mutable database, you need an audit log or temporal tables.

## Production Example

A practical CQRS implementation in Go — method-level separation with a read projection for the admin dashboard:

```go
// Write side — domain model, strict business rules
package orders

type OrderCommandService struct {
    repo    OrderRepository // writes to postgres orders table
    events  EventPublisher
}

func (s *OrderCommandService) ConfirmOrder(ctx context.Context, orderID string) error {
    order, err := s.repo.GetForUpdate(ctx, orderID) // SELECT FOR UPDATE
    if err != nil {
        return err
    }
    if err := order.Confirm(); err != nil {
        return err // domain validation
    }
    if err := s.repo.Update(ctx, order); err != nil {
        return err
    }
    return s.events.Publish(ctx, OrderConfirmedEvent{
        OrderID:    order.ID,
        CustomerID: order.CustomerID,
        Items:      order.Items,
    })
}

// Read side — optimized read model, no business logic
type OrderQueryService struct {
    readDB *sql.DB // could be a read replica, or separate DB
}

// This query shape is exactly what the admin dashboard needs.
// No joins at query time — the projection pre-joined everything.
type OrderSummary struct {
    OrderID         string
    CustomerName    string
    CustomerTier    string
    Items           []ItemSummary
    TotalCents      int64
    FulfillmentStatus string
    WarehouseRegion   string
}

func (s *OrderQueryService) GetPendingFulfillment(ctx context.Context) ([]OrderSummary, error) {
    rows, err := s.readDB.QueryContext(ctx, `
        SELECT order_id, customer_name, customer_tier,
               items_json, total_cents, fulfillment_status, warehouse_region
        FROM orders_fulfillment_view
        WHERE fulfillment_status = 'pending'
        ORDER BY created_at ASC
        LIMIT 500
    `)
    // ... scan and return
}

// Projection builder — listens to events and maintains the read model
type FulfillmentProjection struct {
    db *sql.DB
}

func (p *FulfillmentProjection) OnOrderConfirmed(ctx context.Context, event OrderConfirmedEvent) error {
    itemsJSON, _ := json.Marshal(event.Items)

    _, err := p.db.ExecContext(ctx, `
        INSERT INTO orders_fulfillment_view
            (order_id, customer_name, customer_tier, items_json, total_cents,
             fulfillment_status, warehouse_region)
        SELECT
            $1,
            c.full_name,
            c.tier,
            $2,
            $3,
            'pending',
            w.region
        FROM customers c
        JOIN warehouses w ON w.can_fulfill_country = c.country
        WHERE c.id = $4
        ORDER BY w.distance_km ASC
        LIMIT 1
        ON CONFLICT (order_id) DO UPDATE
            SET fulfillment_status = 'pending'
    `, event.OrderID, itemsJSON, event.TotalCents, event.CustomerID)
    return err
}

func (p *FulfillmentProjection) OnOrderShipped(ctx context.Context, event OrderShippedEvent) error {
    _, err := p.db.ExecContext(ctx, `
        UPDATE orders_fulfillment_view
        SET fulfillment_status = 'shipped',
            shipped_at = $2
        WHERE order_id = $1
    `, event.OrderID, event.ShippedAt)
    return err
}
```

The admin dashboard query that used to take 12 seconds now returns in under 50ms — it's reading from a pre-built projection, not joining seven tables at query time.

**Rebuilding a projection:**

```go
// When you add a new projection or fix a bug in an existing one,
// replay all events from Kafka from the beginning:
func (p *FulfillmentProjection) Rebuild(ctx context.Context, consumer *kafka.Reader) error {
    consumer.SetOffset(kafka.FirstOffset)
    for {
        msg, err := consumer.FetchMessage(ctx)
        if err != nil {
            return err
        }
        var event Event
        json.Unmarshal(msg.Value, &event)
        if err := p.handle(ctx, event); err != nil {
            return err
        }
        consumer.CommitMessages(ctx, msg)
    }
}
```

## The Tradeoffs

**Eventual consistency**: The read model lags the write model. If a user confirms an order and immediately refreshes the admin dashboard, they might not see it yet. Design your UX to account for this — show a loading state, use optimistic updates, or read directly from the write model for the "just submitted" confirmation page.

**Complexity cost**: CQRS at the database-level adds a second database, an event bus, a projection builder process, and the operational overhead of keeping them all running. This is real cost. Only pay it when the read/write mismatch is causing actual problems you've measured.

**Projection bugs**: If your projection builder has a bug, your read model is wrong. You need to be able to rebuild projections from scratch. This means retaining event history long enough to rebuild. Kafka's log retention configuration matters.

**Query vs command ambiguity**: Some operations are both — a "read with side effects" (marking a notification as read, recording a search hit). These are commands, not queries. Don't let the CQRS naming confuse you into making them purely read-side.

**Start simple**: Don't start with full CQRS. Start with a clean separation in code — `OrderCommandService` and `OrderQueryService` in the same codebase, hitting the same database. Extract to separate stores when you've measured that the shared model is causing real pain.

## Key Takeaway

CQRS separates the model you write to from the model you read from. At its simplest, it's just two service classes in the same codebase. At its most sophisticated, it's separate databases maintained in sync via event-driven projections. The motivation is always the same: write models are normalized for consistency, read models need denormalized shapes for performance. When these two needs cause real friction — slow queries, complex joins, scaling mismatches — separating them pays off. Start with method-level separation, escalate to projection-based read models only when you've measured the need.

---

**Previous:** [Lesson 3: Event-Driven Architecture](/post/fundamentals/arch-event-driven/)
**Next:** [Lesson 5: DDD Essentials — Bounded contexts and aggregates](/post/fundamentals/arch-ddd/)
