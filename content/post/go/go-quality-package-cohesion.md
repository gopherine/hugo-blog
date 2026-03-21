---
title: 'Lesson 6: Package Cohesion — Everything in a package should belong together'
author: Atharva Pandey
series: "Go Code Quality & Maintainability"
lesson: 6
keywords:
  - Go
  - golang
  - packages
  - code quality
  - cohesion
  - architecture
tags:
  - Go tutorial
  - golang
  - code quality
date: '2025-02-06T00:00:00.000Z'
---

Go packages are the primary unit of code organization. They determine what's visible to whom, what gets compiled together, and — most importantly — they communicate intent to every engineer who reads the codebase. A package with high cohesion says something true and useful: "everything here is about orders" or "everything here handles HTTP middleware." A package with low cohesion says nothing — it's a filing cabinet where things went when nobody knew where else to put them.

## The Problem

Low-cohesion packages appear in two main forms: the dumping-ground package and the artificially-split package.

```
// WRONG — dumping-ground structure
internal/
  helpers/
    db.go          // database utilities
    strings.go     // string manipulation
    crypto.go      // password hashing
    http.go        // HTTP client wrappers
    time.go        // time formatting
    email.go       // email sending
    validation.go  // input validation
```

Nothing in `helpers/` belongs together except by the criterion "I didn't know where else to put this." When a developer needs password hashing, they have to know to look in `helpers`. When they need email sending, same package. The package name communicates nothing — it's organizational noise.

The artificially-split version is the opposite problem:

```
// WRONG — unnecessary splitting that fragments cohesion
order/
  types.go         // Order, OrderItem, OrderStatus types — nothing else
  interfaces.go    // OrderRepository, OrderService interfaces — nothing else
  errors.go        // order-specific errors — nothing else
  validation.go    // order validation — nothing else
```

These files are all about orders, but the split creates four places to look for anything order-related. A new developer has to hunt across four files to understand the complete picture of what an `Order` is and how it behaves.

## The Idiomatic Way

A package has high cohesion when you can describe its purpose in one sentence without using "and." The test: could the package name serve as that description? `order` passes. `helpers` fails.

```
// RIGHT — package structure reflects domain concepts
internal/
  order/
    order.go       // Order, OrderItem, OrderStatus types + business methods
    store.go       // database operations for orders
    service.go     // business logic that orchestrates store + events
    handler.go     // HTTP handlers for the orders API
  user/
    user.go        // User type + business methods
    store.go       // database operations for users
    auth.go        // authentication and authorization logic
    handler.go     // HTTP handlers for the users API
  payment/
    payment.go     // Payment type + business methods
    stripe.go      // Stripe integration
    handler.go     // HTTP handlers for payments API
```

Every package has a clear answer to "what is this?" Types, storage, business logic, and HTTP handling all live together inside their domain boundary. Finding the validation logic for an order means looking in `order/`, not hunting for a `validation.go` file in a utility package.

Package-level coupling is explicit and intentional:

```go
// order/service.go
package order

// Service depends on a storage interface and a payment interface.
// These dependencies are explicit and injected.
type Service struct {
    store   Store
    payment PaymentGateway
    events  EventPublisher
}

func (s *Service) Submit(ctx context.Context, o Order) (ID, error) {
    if err := o.Validate(); err != nil {
        return 0, fmt.Errorf("validate: %w", err)
    }

    charge, err := s.payment.Charge(ctx, o.UserID, o.Total())
    if err != nil {
        return 0, fmt.Errorf("charge: %w", err)
    }

    id, err := s.store.Insert(ctx, o, charge.ID)
    if err != nil {
        return 0, fmt.Errorf("persist: %w", err)
    }

    s.events.Publish(OrderSubmitted{OrderID: id, UserID: o.UserID})
    return id, nil
}
```

`order.Service` depends on interfaces (`Store`, `PaymentGateway`, `EventPublisher`) that are defined in the `order` package and implemented by other packages. The `order` package doesn't import from `payment` or `user` directly — it defines the contracts it needs.

## In The Wild

A monolith I worked on started with domain packages (order, user, product) and grew over two years into something like 60% domain packages and 40% utility packages. Every feature added something to one utility package or another. By the time I joined, `pkg/util` had 2,400 lines across 18 files.

We ran a two-month exercise: for every function in `util`, find its caller(s) and move it to the package that was calling it. If multiple callers existed, ask whether they should share a domain package or whether the function was truly generic enough to warrant its own package. By the end, `util` was gone. About 70% of the functions moved into domain packages. About 20% moved into genuinely shared packages like `pkg/httputil` and `pkg/dbutil`. About 10% turned out to be dead code that hadn't been called in years.

## The Gotchas

**Circular imports will stop you cold.** Go's compiler forbids circular imports. If `order` imports `user` and `user` imports `order`, you get a compile error. This is a sign that the domain boundary is wrong — something in `user` belongs in `order`, or there's a shared concept that needs its own package (e.g., `identity`).

**Shared types don't require a shared package.** If `order` and `payment` both need a `Money` type, the temptation is to create a `shared/types` package. Resist this. Either define `Money` in the package that conceptually owns it, or use a separate `money` package if the concept is truly orthogonal to both domains.

**Internal packages enforce package boundaries at the compiler level.** Putting code under `internal/` makes it importable only by the parent module. Use this aggressively to prevent other packages from depending on implementation details.

**`_test` packages let you test the public interface without a cyclic dependency.** A test in `order_test` (external test package) tests only the exported API. A test in `order` (internal test package) can test unexported helpers. Both approaches are valid — choose based on what you're testing.

## Key Takeaway

Package cohesion is the discipline of putting things together because they belong together, not because they were convenient to co-locate. One sentence, no "and" — that's your package's mandate. Structure packages around domain concepts, not technical layers. Keep types, storage, business logic, and HTTP handling inside their domain boundary. Use interfaces to express dependencies explicitly and invertibly. Delete utility packages by moving their contents to the packages that own the concepts.

---

[← Lesson 5: Small Functions Win](/post/go/go-quality-small-functions) | [Course Index](/series/go-code-quality-maintainability) | [Next → Lesson 7: Kill the Utils Package](/post/go/go-quality-kill-utils)
