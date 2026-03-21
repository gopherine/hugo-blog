---
title: 'Lesson 2: Inter-Service Communication — HTTP, gRPC, or events? It depends on the coupling.'
author: Atharva Pandey
series: "Go Microservices Patterns"
lesson: 2
keywords:
  - Go
  - golang
  - microservices
  - gRPC
  - HTTP
  - events
  - messaging
  - inter-service communication
tags:
  - Go tutorial
  - golang
  - microservices
date: '2024-09-10T00:00:00.000Z'
---

Every time I've seen a team choose their inter-service communication protocol by default — "we'll use REST for everything" or "we're going gRPC-native" — they've ended up with a transport mechanism that fights against some of their use cases. The choice between HTTP, gRPC, and event-driven messaging is a question about coupling: how tightly do these services need to be synchronized? The answer to that question selects your transport, not the other way around.

## The Problem

Using the same transport for every communication pattern creates mismatches that show up as availability problems and operational complexity.

```go
// WRONG — using synchronous HTTP for a fire-and-forget operation
func (s *OrderService) Submit(ctx context.Context, order Order) (Order, error) {
    // Save order
    saved, err := s.store.Insert(ctx, order)
    if err != nil {
        return Order{}, err
    }

    // Synchronous call to email service — blocks order creation
    resp, err := s.emailClient.Post(ctx, "/send",
        EmailRequest{To: order.Email, Template: "order_confirmed", OrderID: saved.ID},
    )
    if err != nil || resp.StatusCode != 200 {
        // Order was saved but email failed — what do we return?
        // Do we rollback? Do we return a partial success?
        return Order{}, fmt.Errorf("email failed: %w", err)
    }

    return saved, nil
}
```

The email service being down now causes order submission to fail. These two operations have completely different availability requirements — order submission must be highly available; email delivery can tolerate delays.

## The Idiomatic Way

Match the transport to the coupling requirement:

- **Synchronous HTTP (REST)**: The caller needs a response to proceed. Human-facing CRUD, query APIs, anything where the caller blocks waiting for a result.
- **gRPC**: Internal service-to-service calls where schema enforcement, streaming, or performance matter. Strong typing via Protocol Buffers eliminates entire classes of API contract bugs.
- **Events/messaging**: Operations where the caller doesn't need an immediate response — notifications, analytics, downstream side effects. The producer is decoupled from the consumer's availability.

**gRPC for internal service calls:**

```go
// order-service/internal/inventory/client.go
package inventory

import (
    "context"
    inventorypb "github.com/yourorg/proto/inventory/v1"
    "google.golang.org/grpc"
)

type Client struct {
    conn inventorypb.InventoryServiceClient
}

func NewClient(target string, opts ...grpc.DialOption) (*Client, error) {
    cc, err := grpc.NewClient(target, opts...)
    if err != nil {
        return nil, fmt.Errorf("dial inventory: %w", err)
    }
    return &Client{conn: inventorypb.NewInventoryServiceClient(cc)}, nil
}

func (c *Client) Reserve(ctx context.Context, items []ReservationItem) (*Reservation, error) {
    req := &inventorypb.ReserveRequest{Items: toProtoItems(items)}
    resp, err := c.conn.Reserve(ctx, req)
    if err != nil {
        return nil, fmt.Errorf("reserve inventory: %w", err)
    }
    return fromProtoReservation(resp), nil
}
```

**Events for fire-and-forget side effects:**

```go
// order-service/internal/order/service.go
package order

func (s *Service) Submit(ctx context.Context, o Order) (Order, error) {
    // Reserve inventory — synchronous, caller needs the result
    reservation, err := s.inventory.Reserve(ctx, o.Items)
    if err != nil {
        return Order{}, fmt.Errorf("reserve inventory: %w", err)
    }

    saved, err := s.store.Insert(ctx, o, reservation.ID)
    if err != nil {
        // Best-effort release — or handle via saga
        s.inventory.Release(context.Background(), reservation.ID)
        return Order{}, fmt.Errorf("persist order: %w", err)
    }

    // Fire-and-forget events — these services don't block order creation
    s.events.Publish(ctx, OrderSubmitted{
        OrderID:       saved.ID,
        UserID:        saved.UserID,
        Items:         saved.Items,
        ReservationID: reservation.ID,
    })

    return saved, nil
}
```

The `notification-service` and `analytics-service` subscribe to `OrderSubmitted` — their availability doesn't affect order submission at all.

## In The Wild

A logistics platform I worked on had 11 services all talking to each other via REST. The `shipment-service` had 6 upstream dependencies it called synchronously. If any one of them had elevated latency — even the `notification-service`, which just sent emails — the shipment endpoint would time out.

We ran a coupling analysis: for each inter-service call, we asked "does the caller need the result to proceed, or is this a side effect?" Of the 34 unique inter-service call paths, 21 were side effects that didn't need a synchronous response. We moved those to Kafka events. The shipment endpoint went from 6 synchronous dependencies to 2.

p99 latency on the shipment endpoint dropped from 800ms to 140ms. More importantly, the system survived a 20-minute outage of the notification service without any impact on core shipment operations — a first.

## The Gotchas

**gRPC requires a build step.** Protocol Buffers need to be compiled (`protoc`) and the generated code needs to be versioned and distributed. This is manageable overhead but it is overhead. For teams without protobuf tooling in their CI already, REST is less friction for simple internal APIs.

**At-least-once delivery means idempotency is required.** Event-driven systems deliver messages at least once — sometimes more. Every event consumer must be idempotent: processing the same event twice must produce the same result as processing it once. Use a message ID to deduplicate in the consumer.

**Don't use events to avoid thinking about transactions.** "We'll just publish an event and the other service will handle it eventually" is not a data consistency strategy. For operations that require consistency across services, you need a saga pattern (covered in a later lesson) or careful use of outbox patterns.

**Timeouts and retries are different for each transport.** HTTP retries on idempotent GET and DELETE. gRPC has built-in retry policies via service configs. Event systems handle retries via dead-letter queues. Configure each appropriately.

## Key Takeaway

Let coupling requirements select your transport protocol. If the caller needs the result to proceed, use synchronous communication — REST for simplicity, gRPC for internal services where schema enforcement and performance matter. If the caller doesn't need the result immediately, use events — your services become independent of each other's availability. The discipline is making this choice deliberately for each interaction rather than defaulting to one protocol for everything.

---

[← Lesson 1: Service Boundaries](/post/go/go-micro-boundaries) | [Course Index](/series/go-microservices-patterns) | [Next → Lesson 3: Service Discovery](/post/go/go-micro-discovery)
