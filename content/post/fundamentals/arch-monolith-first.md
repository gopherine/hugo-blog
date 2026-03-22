---
title: "Lesson 1: Monolith First — Starting with microservices is usually wrong"
author: Atharva Pandey
keywords:
  - monolith
  - microservices
  - software architecture
  - system design
  - backend engineering
tags:
  - fundamentals
  - architecture
series: "Software Architecture That Survives"
lesson: 1
date: '2024-05-03T00:00:00.000Z'
---

I've seen two teams start new products with microservices. One spent four months before they had anything deployed — Kubernetes setup, service discovery, distributed tracing, a CI/CD pipeline for twelve repos, and debates about how to split domains they hadn't fully understood yet. They ran out of runway. The other team I worked on started with a monolith, shipped their first real user feature in three weeks, and only extracted services when the actual pain of growth made the benefit obvious. We're still running that system, and it handles tens of millions of requests a day.

The appeal of microservices is real. But premature architectural complexity is as dangerous as premature optimization — and in my experience, more likely to kill a product.

## How It Works

A monolith is a single deployable unit. All the code runs in one process. This is not the same as "big ball of mud" or "untestable legacy nightmare." A well-structured monolith has clear internal module boundaries, separate packages for different domains, and clean interfaces between components — it just happens to deploy as one artifact.

```
Monolith
┌───────────────────────────────────────────────────┐
│                                                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │
│  │  Orders  │  │ Payments │  │  Inventory   │   │
│  │  module  │  │  module  │  │   module     │   │
│  └────┬─────┘  └────┬─────┘  └──────┬───────┘   │
│       │             │               │             │
│  ┌────▼─────────────▼───────────────▼───────┐    │
│  │           Shared Database                │    │
│  └───────────────────────────────────────────┘   │
└───────────────────────────────────────────────────┘
     ▲ Single deployable binary, one process
```

```
Microservices
┌──────────┐  HTTP/gRPC   ┌──────────┐  HTTP/gRPC  ┌──────────────┐
│  Orders  │ ─────────── ▶│ Payments │ ──────────── ▶│  Inventory   │
│ service  │              │ service  │               │  service     │
└──────┬───┘              └──────┬───┘               └──────┬───────┘
       │                         │                           │
  ┌────▼────┐               ┌────▼────┐               ┌─────▼──────┐
  │ Orders  │               │Payments │               │ Inventory  │
  │   DB    │               │   DB    │               │    DB      │
  └─────────┘               └─────────┘               └────────────┘
```

Microservices give you independent deployability, independent scalability, and strong isolation. These are real benefits. But they come with costs that are often invisible until you're paying them:

- **Distributed systems complexity**: Network calls fail. You need retries, timeouts, circuit breakers, and distributed tracing. None of this is needed in a monolith.
- **Data consistency**: Transactions that span a single database are simple. Transactions that span services require saga patterns, event-driven workflows, or compensating transactions.
- **Local reasoning**: In a monolith, you can trace an entire code path with a debugger. In microservices, a single user request touches five services across five repos.
- **Operational overhead**: Each service needs its own deployment, monitoring, scaling policy, and on-call runbook.

The question is never "monolith or microservices" in the abstract. It's "what does this specific system need right now."

## Why It Matters

Martin Fowler's original "Monolith First" essay made this point well: you should usually start with a monolith, for the simple reason that you don't know your domain well enough to draw service boundaries correctly. Service boundaries are hard to change after the fact. An incorrect boundary in a monolith is just a package rename. An incorrect boundary in a microservices architecture means migrating data, rewriting network interfaces, and coordinating deployments.

The modular monolith is the pattern I recommend: build your monolith as if it were microservices internally. Clear module interfaces. No package importing from another module's internals. Domain logic isolated from infrastructure. This gives you most of the organizational benefits of microservices while deferring the operational complexity until you've actually learned where the seams are.

## Production Example

A Go monolith with clear module boundaries — ready to extract into services when needed:

```go
// Internal package structure:
// orders/
//   handler.go     — HTTP handlers
//   service.go     — business logic
//   repository.go  — data access
// payments/
//   handler.go
//   service.go
//   repository.go
// inventory/
//   handler.go
//   service.go
//   repository.go

// orders/service.go — business logic, no HTTP, no DB drivers
package orders

import (
    "context"
    "fmt"

    // Depend on interfaces, not implementations
    "github.com/example/app/payments"
    "github.com/example/app/inventory"
)

type Service struct {
    repo      Repository
    payments  payments.Authorizer  // interface, not concrete type
    inventory inventory.Checker    // interface, not concrete type
}

func (s *Service) CreateOrder(ctx context.Context, req CreateOrderRequest) (*Order, error) {
    // Check inventory — interface call, not HTTP call
    if err := s.inventory.Reserve(ctx, req.Items); err != nil {
        return nil, fmt.Errorf("inventory reserve: %w", err)
    }

    // Authorize payment — interface call, not HTTP call
    auth, err := s.payments.Authorize(ctx, req.PaymentMethod, req.Total)
    if err != nil {
        // Compensate — release inventory
        s.inventory.Release(ctx, req.Items)
        return nil, fmt.Errorf("payment auth: %w", err)
    }

    order := &Order{
        Items:        req.Items,
        PaymentAuth:  auth.Token,
        Status:       StatusPending,
    }
    if err := s.repo.Insert(ctx, order); err != nil {
        s.payments.Void(ctx, auth.Token)
        s.inventory.Release(ctx, req.Items)
        return nil, fmt.Errorf("insert order: %w", err)
    }

    return order, nil
}

// When you later extract payments into its own service:
// Change payments.Authorizer to call HTTP instead of in-process.
// The orders/service.go doesn't change at all.
```

The key discipline: modules communicate only through interfaces. `orders` never imports `payments` internals. This makes the eventual extraction mechanical rather than architectural.

When should you actually extract a service? I use a checklist:

1. **Independent deployment rate**: Does this module deploy more often than others, and deploys block unrelated work?
2. **Independent scaling needs**: Does this component need 10x the resources of everything else?
3. **Team ownership**: Is there a separate team whose blast radius should be isolated?
4. **Technology mismatch**: Does this component need a different language, framework, or database?

If you can't answer yes to at least one of these, you don't need a microservice. You need a module.

## The Tradeoffs

**Monolith scaling**: A monolith can't scale individual components independently. If your image processing is CPU-bound and your API layer is I/O-bound, they share the same resource pool. This matters when components have dramatically different profiles. Vertical scaling (bigger machines) and running multiple instances of the entire monolith solve most cases.

**Long build and test times**: As a monolith grows, CI times increase. A build cache, parallel tests, and clear test isolation strategies address this. It's a solvable problem — and still easier than coordinating deployments across twenty services.

**Deployments**: Deploying the monolith deploys everything. A bug in a minor module requires a rollback of the entire binary. Feature flags and progressive rollouts mitigate this. But it's a real concern as the team grows.

**Database as coupling point**: A shared database in a monolith creates coupling between modules. Schema changes affect everyone. The fix is module-level schema ownership: the inventory module owns its tables, and other modules access inventory data only through the inventory service interface. Enforce this with separate schemas and restricted database users in production.

**The big ball of mud risk**: Monolith does not mean "no architecture." Without enforced module boundaries, it becomes spaghetti code. Tools like `go-arch-lint` or dependency rules in your CI enforce the boundaries you draw.

## Key Takeaway

Starting with a microservices architecture when you don't yet fully understand your domain is premature complexity. A well-structured modular monolith ships faster, is easier to reason about, and lets you learn where the real seams are before committing to service boundaries that are expensive to change. Build your monolith with clean module interfaces — as if each module could become a service. Extract into services when you have a concrete operational reason. The goal is software that ships and survives, not software that demonstrates familiarity with distributed systems patterns.

---

**Next:** [Lesson 2: Clean Architecture — Not the textbook version](/post/fundamentals/arch-clean/)
