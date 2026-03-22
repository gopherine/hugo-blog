---
title: "Lesson 3: Event-Driven Architecture — Events vs commands"
author: Atharva Pandey
keywords:
  - event-driven architecture
  - events
  - commands
  - Kafka
  - message queue
  - pub-sub
  - software architecture
tags:
  - fundamentals
  - architecture
series: "Software Architecture That Survives"
lesson: 3
date: '2024-06-04T00:00:00.000Z'
---

I used to use "event" and "command" interchangeably. A message is a message, right? Then I started debugging an event-driven system where the payment service was publishing `ProcessPayment` events, the order service was publishing `CreateShipment` events, and two teams were arguing about who owned the workflow. The problem was naming: they were publishing commands disguised as events. The distinction isn't pedantic — it determines who owns the workflow and how the system evolves.

## How It Works

**The Fundamental Difference**

A **command** is an instruction to do something. It has one intended recipient. It implies intent from the sender. The sender cares whether it succeeded.

```
Command: CreateShipment { order_id: "123", address: {...} }
→ Sent to: ShippingService
→ Implies: "I want you to create a shipment. Please."
→ Has a result: success or failure
```

An **event** is a statement of fact about something that already happened. It has no intended recipient. The publisher doesn't know or care who receives it. It has no "result."

```
Event: OrderConfirmed { order_id: "123", customer_id: "456", items: [...], total: 99.00 }
→ Published to: everyone who cares (or no one)
→ Implies: "This happened."
→ Has no result from the publisher's perspective
```

This distinction changes the coupling model entirely:

```
Commands (tight coupling)
OrderService → [CreateShipment] → ShippingService
           ↑ sender knows receiver, receiver must exist

Events (loose coupling)
OrderService → [OrderConfirmed] → Kafka topic
                                      ↓
                             ShippingService (subscribes)
                             InvoiceService (subscribes)
                             AnalyticsService (subscribes)
                             ↑ publisher knows none of these
```

When you publish an event, you can add a new consumer without touching the publisher. The order service doesn't need to be changed when you add an analytics pipeline. This is the defining benefit of event-driven architecture.

**The Event Structure**

A well-designed domain event has:

```json
{
  "event_id": "evt_01HX2B3F4Y...",
  "event_type": "order.confirmed",
  "event_version": "1.0",
  "occurred_at": "2024-06-04T14:23:11Z",
  "aggregate_id": "order_123",
  "aggregate_type": "order",
  "data": {
    "order_id": "order_123",
    "customer_id": "cust_456",
    "items": [...],
    "total_cents": 9900,
    "currency": "USD"
  },
  "metadata": {
    "correlation_id": "req_abc123",
    "causation_id": "cmd_xyz789",
    "published_by": "order-service"
  }
}
```

The `aggregate_id` is the key for partitioning — events for the same order go to the same Kafka partition, ensuring ordering for that order.

**Event Sourcing vs Event-Driven**

These are different patterns often confused:

- **Event-Driven Architecture**: Services communicate via events. The current state is stored in a normal database (Postgres, MySQL). Events are the communication mechanism.
- **Event Sourcing**: The current state is derived from the history of events. Instead of storing `order.status = "confirmed"`, you store the sequence of events that led to that state. Powerful but complex.

Most systems benefit from event-driven communication without needing full event sourcing.

**Message Brokers**

Events live in message brokers:

- **Kafka**: High-throughput, durable, ordered within partitions, log-based (consumers can replay). The default choice for production event-driven systems.
- **RabbitMQ**: Traditional message queue, supports complex routing, not naturally replayable. Good for task queues and work distribution.
- **AWS SQS/SNS**: Managed, simple, good for AWS-native architectures. SQS is queues, SNS is fan-out pub-sub.
- **NATS JetStream**: Lightweight, fast, good for latency-sensitive use cases.

## Why It Matters

Event-driven architecture shines in three scenarios:

1. **Cross-domain workflows**: An order confirmation triggers shipping, invoicing, analytics, notifications, and loyalty points. With direct calls, the order service must know about all these downstream systems. With events, the order service publishes one event and is done.

2. **Temporal decoupling**: Consumers can be down. The event waits in the broker. When the consumer recovers, it processes events in order. With synchronous calls, a downstream service being unavailable means the upstream service fails too.

3. **Audit and replay**: Events are facts about what happened. With Kafka's log retention, you can replay events to rebuild state, backfill a new service with historical data, or diagnose production issues.

## Production Example

Publishing events in Go, ensuring at-least-once delivery:

```go
package events

import (
    "context"
    "encoding/json"
    "fmt"
    "time"

    "github.com/segmentio/kafka-go"
)

type Publisher struct {
    writer *kafka.Writer
}

func NewPublisher(brokers []string) *Publisher {
    return &Publisher{
        writer: &kafka.Writer{
            Addr:         kafka.TCP(brokers...),
            Balancer:     &kafka.Hash{}, // Consistent hashing by key
            RequiredAcks: kafka.RequireAll, // All replicas must acknowledge
            Async:        false, // Synchronous publish — we know when it's committed
        },
    }
}

type OrderConfirmedEvent struct {
    EventID     string    `json:"event_id"`
    EventType   string    `json:"event_type"`
    OccurredAt  time.Time `json:"occurred_at"`
    OrderID     string    `json:"order_id"`
    CustomerID  string    `json:"customer_id"`
    TotalCents  int64     `json:"total_cents"`
}

func (p *Publisher) PublishOrderConfirmed(ctx context.Context, orderID, customerID string, totalCents int64) error {
    event := OrderConfirmedEvent{
        EventID:    newEventID(),
        EventType:  "order.confirmed",
        OccurredAt: time.Now().UTC(),
        OrderID:    orderID,
        CustomerID: customerID,
        TotalCents: totalCents,
    }
    payload, err := json.Marshal(event)
    if err != nil {
        return fmt.Errorf("marshal event: %w", err)
    }

    return p.writer.WriteMessages(ctx, kafka.Message{
        Topic: "orders.events",
        Key:   []byte(orderID), // Partition by order ID — same order, same partition
        Value: payload,
    })
}
```

Consuming events with idempotent handling:

```go
type ShippingEventHandler struct {
    processor ShipmentCreator
    processed ProcessedEventStore // tracks which event IDs we've handled
}

func (h *ShippingEventHandler) Handle(ctx context.Context, msg kafka.Message) error {
    var event OrderConfirmedEvent
    if err := json.Unmarshal(msg.Value, &event); err != nil {
        // Poison pill — can't parse. Send to DLQ, don't retry.
        return h.sendToDeadLetterQueue(ctx, msg)
    }

    // Idempotency check — at-least-once delivery means duplicates happen
    if already, _ := h.processed.Has(ctx, event.EventID); already {
        return nil // Already processed, skip
    }

    if err := h.processor.CreateShipment(ctx, event.OrderID, event.CustomerID); err != nil {
        // Return error to trigger retry (Kafka consumer will not commit the offset)
        return fmt.Errorf("create shipment: %w", err)
    }

    // Mark as processed only after successful handling
    return h.processed.Mark(ctx, event.EventID)
}
```

The dead letter queue (DLQ) is critical: when a message can't be processed after N retries, move it out of the main topic. This prevents a bad message from blocking all subsequent messages on that partition.

**Transactional outbox pattern** — ensuring events are published if-and-only-if the database transaction commits:

```go
// Within a database transaction:
// 1. Update the database state
// 2. Insert event into an outbox table (same transaction)
// A separate outbox relay process polls the outbox table and publishes to Kafka

tx, _ := db.BeginTx(ctx, nil)
// Update order status
tx.ExecContext(ctx, `UPDATE orders SET status = 'confirmed' WHERE id = $1`, orderID)
// Insert event into outbox
tx.ExecContext(ctx, `INSERT INTO outbox (event_type, payload) VALUES ($1, $2)`,
    "order.confirmed", payload)
tx.Commit() // Event is committed or rolled back with the order update
```

Without the transactional outbox, you can update the database but fail to publish the event (or vice versa), leaving the system in an inconsistent state.

## The Tradeoffs

**Eventual consistency**: Event-driven systems are eventually consistent. After an order is confirmed, the shipping service might not have created the shipment yet. If a user checks the order status 50ms later, the shipment might not show "preparing." You need to design your UX and business flows around this. Some operations genuinely require strong consistency — don't force them into async events.

**Debugging complexity**: A synchronous call stack in a monolith is easy to trace. An event-driven flow across five services, each with its own retry logic and DLQ, requires correlation IDs and distributed tracing. Budget for this tooling before committing to the architecture.

**Event schema evolution**: Events are a public API. Once other services depend on your event schema, you can't remove fields or change types without breaking consumers. Add fields, never remove them. Version your schemas. Use a schema registry (Confluent Schema Registry with Avro, or Protobuf) to enforce compatibility.

**Commands still have a place**: Not everything should be an event. When you need a synchronous response (user registration returns a user ID, a payment returns a success/failure), use a command/RPC. Events model facts about what happened, not requests for action.

**Message ordering**: Kafka guarantees ordering within a partition, not across partitions. If event A must be processed before event B for a given entity, ensure they go to the same partition (use the entity ID as the partition key). If your consumer scales by adding more instances, each instance owns a subset of partitions.

## Key Takeaway

Events state facts about what happened. Commands are instructions to do something. The distinction matters because events decouple producer from consumer — you add new consumers without touching publishers. Event-driven architecture excels at cross-domain workflows, temporal decoupling, and audit/replay scenarios. The hidden costs are eventual consistency, debugging complexity, and event schema governance. Build your systems around the transactional outbox pattern to ensure events are published reliably, and always implement idempotent consumers — at-least-once delivery guarantees duplicates will arrive.

---

**Previous:** [Lesson 2: Clean Architecture](/post/fundamentals/arch-clean/)
**Next:** [Lesson 4: CQRS — When reads and writes need different models](/post/fundamentals/arch-cqrs/)
