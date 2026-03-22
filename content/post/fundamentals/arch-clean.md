---
title: "Lesson 2: Clean Architecture — Not the textbook version"
author: Atharva Pandey
keywords:
  - clean architecture
  - hexagonal architecture
  - ports and adapters
  - software architecture
  - dependency inversion
  - backend engineering
tags:
  - fundamentals
  - architecture
series: "Software Architecture That Survives"
lesson: 2
date: '2024-05-19T00:00:00.000Z'
---

I've read the Clean Architecture book. I've also seen teams implement it so literally that they had six layers of indirection for a CRUD endpoint: a controller called a use case, which called a domain service, which called a port, which went through an adapter, which called a repository, which hit the database. Each hop had its own error mapping. Changing a database column required touching eight files. That's not clean. That's engineering theater.

Here's what I actually do in practice — the pragmatic version that gives you the benefits without the ceremony.

## How It Works

The core insight of clean architecture (and its siblings: hexagonal architecture, ports and adapters, onion architecture) is the **dependency rule**: source code dependencies must point inward. Outer layers depend on inner layers. Inner layers know nothing about outer layers.

```
                    ┌─────────────────────────────┐
                    │   Frameworks & Drivers       │  ← HTTP, database drivers,
                    │  (outermost layer)           │     external APIs
                    │   ┌───────────────────────┐  │
                    │   │   Interface Adapters  │  │  ← HTTP handlers, repo impls,
                    │   │                       │  │     serialization
                    │   │   ┌───────────────┐   │  │
                    │   │   │  Application  │   │  │  ← Use cases, service layer
                    │   │   │  Business     │   │  │
                    │   │   │  ┌─────────┐  │   │  │
                    │   │   │  │ Domain  │  │   │  │  ← Entities, business rules
                    │   │   │  └─────────┘  │   │  │
                    │   │   └───────────────┘   │  │
                    │   └───────────────────────┘  │
                    └─────────────────────────────┘
                         Dependencies point inward →
```

In practice, for a Go HTTP service, this translates to three concrete things:

1. **Domain types don't import anything from your infrastructure** (no `database/sql`, no `net/http`, no `encoding/json`).
2. **Business logic doesn't care whether you're using Postgres, MySQL, or an in-memory store** — it depends on interfaces.
3. **HTTP handlers don't contain business logic** — they translate HTTP to/from domain types and call the service layer.

The test for whether your architecture is working: can you test your business logic without starting an HTTP server or a database? If yes, you're probably on the right track.

## Why It Matters

The clean dependency direction makes two things possible:

**Testability**: Business logic depends on interfaces. Tests inject fake implementations. You can unit-test complex business rules without database setup or HTTP mocking. Tests are fast, deterministic, and tell you exactly what broke.

**Replaceability**: When you need to switch from PostgreSQL to CockroachDB, or from `net/http` to Fiber, or from REST to gRPC — you change the outermost layer only. The business logic is untouched because it never knew what database you were using.

In practice, you rarely replace databases wholesale. But you definitely swap transports: you add gRPC support to an existing REST service, you add a CLI interface, you add a message queue consumer for async processing. With clean architecture, each new transport is a new adapter. The business logic handles the request regardless of how it arrived.

## Production Example

A concrete Go example — an order service. This is the structure I actually use:

```
internal/
  domain/
    order.go        ← types, business rules, no imports from infra
    errors.go       ← domain-specific errors
  service/
    orders.go       ← use cases, depends on domain + repository interface
  repository/
    orders_pg.go    ← PostgreSQL implementation of repository interface
    orders_mock.go  ← test double (generated or hand-written)
  handler/
    orders_http.go  ← HTTP handlers, JSON serialization
    orders_grpc.go  ← gRPC handlers (later addition, no service change)
```

**domain/order.go** — pure business types and rules:

```go
package domain

import (
    "errors"
    "time"
)

type Order struct {
    ID          string
    CustomerID  string
    Items       []LineItem
    Total       Money
    Status      OrderStatus
    CreatedAt   time.Time
}

type OrderStatus int
const (
    StatusDraft     OrderStatus = iota
    StatusConfirmed
    StatusShipped
    StatusCancelled
)

// Business rule: can only cancel pending orders
func (o *Order) Cancel() error {
    if o.Status != StatusDraft && o.Status != StatusConfirmed {
        return errors.New("cannot cancel order in status " + o.Status.String())
    }
    o.Status = StatusCancelled
    return nil
}

// No database/sql, no net/http, no encoding/json here. Pure logic.
```

**service/orders.go** — use cases, depends on interface:

```go
package service

import (
    "context"
    "fmt"

    "github.com/example/app/internal/domain"
)

// OrderRepository is the port — an interface defined in the service layer
type OrderRepository interface {
    Get(ctx context.Context, id string) (*domain.Order, error)
    Insert(ctx context.Context, order *domain.Order) error
    Update(ctx context.Context, order *domain.Order) error
}

type PaymentService interface {
    Authorize(ctx context.Context, method domain.PaymentMethod, amount domain.Money) (string, error)
    Void(ctx context.Context, authToken string) error
}

type Orders struct {
    repo     OrderRepository
    payments PaymentService
}

func NewOrders(repo OrderRepository, payments PaymentService) *Orders {
    return &Orders{repo: repo, payments: payments}
}

func (s *Orders) CancelOrder(ctx context.Context, orderID, userID string) error {
    order, err := s.repo.Get(ctx, orderID)
    if err != nil {
        return fmt.Errorf("get order: %w", err)
    }
    if order.CustomerID != userID {
        return domain.ErrForbidden
    }
    if err := order.Cancel(); err != nil {
        return err // domain error — order can't be cancelled
    }
    if order.PaymentAuthToken != "" {
        if err := s.payments.Void(ctx, order.PaymentAuthToken); err != nil {
            // Log but don't fail — void is best-effort
            // In production, add this to a retry queue
        }
    }
    return s.repo.Update(ctx, order)
}
```

**repository/orders_pg.go** — the adapter:

```go
package repository

import (
    "context"
    "database/sql"
    "fmt"

    "github.com/example/app/internal/domain"
)

type PostgresOrderRepo struct {
    db *sql.DB
}

func (r *PostgresOrderRepo) Get(ctx context.Context, id string) (*domain.Order, error) {
    var o domain.Order
    err := r.db.QueryRowContext(ctx,
        `SELECT id, customer_id, status, total_cents FROM orders WHERE id = $1`, id,
    ).Scan(&o.ID, &o.CustomerID, &o.Status, &o.Total)
    if err == sql.ErrNoRows {
        return nil, domain.ErrNotFound
    }
    if err != nil {
        return nil, fmt.Errorf("query: %w", err)
    }
    return &o, nil
}
```

**Testing the service without the database:**

```go
func TestCancelOrder(t *testing.T) {
    repo := &mockOrderRepo{
        orders: map[string]*domain.Order{
            "order-1": {
                ID:         "order-1",
                CustomerID: "user-abc",
                Status:     domain.StatusConfirmed,
            },
        },
    }
    payments := &mockPayments{}
    svc := service.NewOrders(repo, payments)

    err := svc.CancelOrder(context.Background(), "order-1", "user-abc")
    assert.NoError(t, err)
    assert.Equal(t, domain.StatusCancelled, repo.orders["order-1"].Status)
}
```

No HTTP server, no database. Tests run in milliseconds.

## The Tradeoffs

**Boilerplate**: Clean architecture requires more types. You'll have domain types, request/response types for HTTP, and database row types — often all representing the same order. The mapping between them is tedious. The payoff is that each layer changes for its own reasons, not for others.

**When to skip it**: For simple CRUD endpoints with no real business logic, the layers are overhead with no benefit. A `GetUser` handler that reads a row and returns it doesn't need a domain model and a use case. Apply the pattern where business logic exists. Don't apply it uniformly to everything.

**Anemic domain model problem**: If your domain types are just data bags with no methods (getters and setters), you haven't actually captured business rules in the domain. Business logic ends up in the service layer as procedural code. This is fine if your domain is simple. It's a problem if you have complex rules — you lose the ability to enforce invariants at the type level.

**Over-abstraction**: I've seen code where the repository interface had 30 methods and was implemented by exactly one type. The interface existed to satisfy the pattern, not because there were multiple implementations. An interface with one implementation isn't really an interface — it's just indirection for its own sake. Create interfaces at the point where they earn their keep: testability, or genuine replaceability.

**The "onion" vs "ports and adapters" confusion**: These are variations of the same idea. Don't get religious about the exact number of layers or the exact naming convention. The principle is: business logic in the center, external concerns on the outside, dependencies pointing inward.

## Key Takeaway

Clean architecture is about one rule: business logic doesn't know what database, HTTP framework, or message queue you use. Dependencies point inward — the domain core is isolated from infrastructure concerns. In Go, this means domain types have no infrastructure imports, service methods depend on interfaces, and adapters (HTTP handlers, database repositories) live at the edges. The reward is fast unit tests that run without starting a database, and the ability to add new transports or swap infrastructure without touching business logic. Apply it where you have real business complexity. Skip the ceremony for simple CRUD.

---

**Previous:** [Lesson 1: Monolith First](/post/fundamentals/arch-monolith-first/)
**Next:** [Lesson 3: Event-Driven Architecture — Events vs commands](/post/fundamentals/arch-event-driven/)
