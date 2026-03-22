---
title: "Lesson 5: DDD Essentials — Bounded contexts and aggregates"
author: Atharva Pandey
keywords:
  - DDD
  - domain-driven design
  - bounded context
  - aggregates
  - ubiquitous language
  - software architecture
tags:
  - fundamentals
  - architecture
series: "Software Architecture That Survives"
lesson: 5
date: '2024-07-07T00:00:00.000Z'
---

The concept of "customer" meant something different in every team I talked to at one company. To the billing team, a customer was a billing account. To the support team, a customer was a person who filed tickets. To the identity team, a customer was an authenticated principal. Every team had their own `Customer` struct, and syncing them was a full-time job. That's the problem DDD's bounded context concept solves — and it's one of those ideas that sounds academic until you've suffered without it.

I'm not going to cover all of DDD here. The full Eric Evans book is 500 pages and a lot of it is taxonomy. I'll cover the two concepts that change how I design systems: bounded contexts and aggregates.

## How It Works

**Bounded Contexts**

A bounded context is a boundary within which a specific domain model applies. "Customer" inside the billing context has billing-specific fields and behavior. "Customer" inside the support context has support-specific fields. They're not the same `Customer` struct — they're two different representations of the same real-world entity, optimized for their own context.

```
┌─────────────────────────┐     ┌──────────────────────────┐
│   Billing Context       │     │   Support Context         │
│                         │     │                           │
│  Customer {             │     │  Customer {               │
│    id: UUID             │     │    id: UUID               │
│    payment_method       │     │    full_name              │
│    billing_address      │     │    timezone               │
│    invoice_currency     │     │    preferred_language     │
│    subscription_tier    │     │    support_tier           │
│  }                      │     │    open_ticket_count      │
│                         │     │  }                        │
└─────────────────────────┘     └──────────────────────────┘
         ↑                                  ↑
         └──── shared customer ID ──────────┘
               (the only thing in common)
```

The contexts share an identity (the customer's UUID) but maintain separate models. Integration between contexts happens through APIs or events — not by importing each other's internal models.

This is how you prevent the dreaded "God Customer object" with 80 fields that serves all contexts, where adding a billing field potentially breaks the support module, and no one owns the model's integrity.

**Aggregates**

An aggregate is a cluster of domain objects that's treated as a single unit for consistency. There's a root entity (the aggregate root) that controls all access to the objects inside. Invariants — business rules about consistency — are enforced at the aggregate boundary.

The key rules:
1. External code only holds references to the aggregate root, not to internal objects.
2. All changes go through the root, which enforces invariants.
3. Each aggregate is loaded and saved as a single transaction.

Example: an `Order` aggregate root containing `LineItem`s:

```
Order (aggregate root)
  ├── id
  ├── status
  ├── total
  └── []LineItem
        ├── product_id
        ├── quantity
        └── price
```

You don't modify a `LineItem` directly from outside the aggregate. You call `order.UpdateItemQuantity(productID, newQuantity)`. The order enforces the invariant: "total must always equal the sum of line item prices." The order can also enforce "you can't add items to a confirmed order."

Why does this matter? Because without an aggregate root, you have five different code paths that can all modify `LineItem` objects, and none of them has the full picture to enforce the invariant. Bugs.

**Ubiquitous Language**

The language in your code should match the language your domain experts use. If your product team calls it a "campaign," don't call it a "promotion" in the code. If they say "expire a session," use `session.Expire()` not `session.Deactivate()` or `session.Delete()`. This sounds minor — it's not. Code that uses the same words as the business makes conversations easier and bugs more visible.

## Why It Matters

Bounded contexts are how you draw service boundaries correctly. Microservice advocates often say "services should be small." Small how? DDD gives a concrete answer: one service per bounded context. The billing service owns the billing context. The support service owns the support context. They don't share a database or models.

Aggregates tell you what to put in a single transaction. If two things must always be consistent with each other, they belong in the same aggregate (and the same transaction). If they can be eventually consistent, they belong in separate aggregates (and communicate via events).

## Production Example

An `Order` aggregate in Go that enforces invariants:

```go
package domain

import (
    "errors"
    "time"
)

// The aggregate root. External code only deals with *Order.
type Order struct {
    id         string
    customerID string
    items      []LineItem
    total      Money
    status     OrderStatus
    createdAt  time.Time
}

// LineItem is internal to the order aggregate.
// No package outside domain/ should hold a *LineItem directly.
type LineItem struct {
    productID string
    quantity  int
    unitPrice Money
}

// All mutations go through the aggregate root — invariants enforced here
func (o *Order) AddItem(productID string, quantity int, unitPrice Money) error {
    if o.status != StatusDraft {
        return errors.New("cannot add items to a non-draft order")
    }
    if quantity <= 0 {
        return errors.New("quantity must be positive")
    }

    // Check for duplicate — update quantity instead of adding
    for i, item := range o.items {
        if item.productID == productID {
            o.items[i].quantity += quantity
            o.recalculateTotal()
            return nil
        }
    }

    o.items = append(o.items, LineItem{
        productID: productID,
        quantity:  quantity,
        unitPrice: unitPrice,
    })
    o.recalculateTotal()
    return nil
}

func (o *Order) Confirm() error {
    if o.status != StatusDraft {
        return errors.New("can only confirm draft orders")
    }
    if len(o.items) == 0 {
        return errors.New("cannot confirm empty order")
    }
    if o.total.Amount <= 0 {
        return errors.New("cannot confirm order with zero total")
    }
    o.status = StatusConfirmed
    return nil
}

// Invariant: total always matches line items
func (o *Order) recalculateTotal() {
    total := Money{Currency: "USD"}
    for _, item := range o.items {
        total.Amount += item.unitPrice.Amount * int64(item.quantity)
    }
    o.total = total
}

// Only expose what's needed — getters, not field access
func (o *Order) ID() string          { return o.id }
func (o *Order) Status() OrderStatus { return o.status }
func (o *Order) Total() Money        { return o.total }
func (o *Order) Items() []LineItem   {
    // Return a copy — prevent external mutation of the slice
    result := make([]LineItem, len(o.items))
    copy(result, o.items)
    return result
}
```

Bounded contexts in practice — two services with separate `Customer` representations:

```go
// billing/domain/customer.go — billing context's customer model
package domain

type Customer struct {
    ID              string
    BillingEmail    string
    PaymentMethodID string
    SubscriptionID  string
    InvoiceCurrency string
}

// support/domain/customer.go — support context's customer model
package domain

type Customer struct {
    ID               string
    DisplayName      string
    Timezone         string
    SupportTier      SupportTier
    OpenTicketCount  int
}
```

They share only the customer `ID`. When the support service needs billing information, it calls the billing service API — it doesn't share a database or model.

**Context mapping** — when two contexts need to integrate:

```go
// The "Anti-Corruption Layer" pattern:
// When the billing service publishes a CustomerUpgraded event,
// the support service translates it into its own model rather than
// using the billing context's types directly.

type BillingEventTranslator struct {
    customerRepo support.CustomerRepository
}

func (t *BillingEventTranslator) OnCustomerUpgraded(ctx context.Context, event billing.CustomerUpgradedEvent) error {
    customer, err := t.customerRepo.Get(ctx, event.CustomerID)
    if err != nil {
        return err
    }
    // Map billing tier to support tier — translation, not import
    customer.SupportTier = mapBillingTierToSupportTier(event.NewTier)
    return t.customerRepo.Update(ctx, customer)
}
```

## The Tradeoffs

**DDD overhead**: Full DDD adds significant design ceremony. Strategic patterns (bounded contexts, context maps, event storming) are useful for complex domains with large teams. Tactical patterns (aggregates, value objects, domain services) add value when business logic is complex. Don't apply them to a simple user registration flow.

**Aggregate size**: Small aggregates are better for concurrency. A large aggregate that includes every related entity creates lock contention — everything that changes anything in the order tree has to lock the root. Design aggregates to be as small as possible while still enforcing their invariants.

**Cross-aggregate consistency**: Business operations that span aggregates (and therefore span transactions) require eventual consistency. "Place order" might update the order aggregate AND the inventory aggregate. If inventory is a separate aggregate, you accept that they might be briefly inconsistent, or you use sagas/process managers to coordinate.

**Identifying aggregates**: The question "what invariants must always hold?" is the guide. If two entities must be consistent with each other transactionally, they belong in the same aggregate. If they can be eventually consistent, separate aggregates.

**Shared kernel**: Sometimes two bounded contexts genuinely share a small subset of the domain model (like a common `Money` type or `Address` type). This shared code is the "shared kernel." Keep it small and treat changes to it as a major version change — everyone using it is affected.

## Key Takeaway

DDD's bounded context concept gives you a principled basis for where to draw service boundaries: one service per context, each context owning its own model of shared domain entities. Aggregates enforce consistency invariants by making the root entity the single path through which state changes. The practical payoff: "Customer" is no longer a contested battlefield object that means different things to different teams. Each context has the customer it needs. And your transactions are clean — one aggregate per transaction, sagas for cross-aggregate coordination. Apply these ideas where domain complexity warrants them; skip the ceremony for CRUD-heavy, logic-light services.

---

**Previous:** [Lesson 4: CQRS](/post/fundamentals/arch-cqrs/)
**Next:** [Lesson 6: API Versioning — URL, header, or content negotiation](/post/fundamentals/arch-api-versioning/)
