---
title: 'Lesson 1: Service Boundaries — Split by business capability, not by technical layer'
author: Atharva Pandey
series: "Go Microservices Patterns"
lesson: 1
keywords:
  - Go
  - golang
  - microservices
  - service boundaries
  - domain-driven design
  - architecture
tags:
  - Go tutorial
  - golang
  - microservices
date: '2024-07-06T00:00:00.000Z'
---

The most expensive architectural mistake I've seen teams make with microservices is splitting by technical layer instead of by business capability. I've seen systems with a "UserDataService," a "UserLogicService," and a "UserNotificationService" — three services that all have to be deployed together for any user-related feature to work, that share a database, and that call each other synchronously for every request. They're not microservices; they're a distributed monolith with extra network latency.

## The Problem

Splitting by technical layer produces artificial service boundaries that maximize coupling and minimize autonomy.

```
// WRONG — technical layer split
services/
  data-service/        // all reads and writes for all domains
  business-service/    // all business logic for all domains
  notification-service/ // all notifications for all domains
  api-service/         // all HTTP endpoints for all domains
```

A user registration request now touches four services in sequence. The `api-service` calls `business-service`, which calls `data-service` to write the user, then calls `notification-service` to send a welcome email. Any one of these failing cascades back to the caller. Deploying a change to user registration logic requires coordinating four separate deployments.

Here's what the inter-service communication looks like in this topology:

```go
// WRONG — api-service calling three other services for one operation
func (h *Handler) RegisterUser(w http.ResponseWriter, r *http.Request) {
    var req RegisterRequest
    json.NewDecoder(r.Body).Decode(&req)

    // synchronous call to business service
    user, err := h.businessClient.CreateUser(r.Context(), req)
    if err != nil {
        http.Error(w, "create failed", 500)
        return
    }

    // business service already called data service internally
    // now notify — but this is a SEPARATE network hop
    if err := h.notificationClient.SendWelcome(r.Context(), user.ID); err != nil {
        // user exists but welcome email failed — partial state
        http.Error(w, "notification failed", 500)
        return
    }

    json.NewEncoder(w).Encode(user)
}
```

Partial failure is now a real problem: the user record was written but the welcome email failed. You have inconsistent state and no clear owner responsible for reconciliation.

## The Idiomatic Way

Split by business capability — the things your business actually does. Each service owns a complete vertical slice: its API, its business logic, its data.

```
// RIGHT — business capability split
services/
  user-service/         // everything about users: registration, auth, profiles
  order-service/        // everything about orders: creation, fulfillment, history
  inventory-service/    // everything about inventory: stock levels, reservations
  notification-service/ // notification delivery across all channels
```

`notification-service` is still a separate service here — but now it's a capability (delivering notifications), not a technical layer (sending emails for one domain). It's an autonomous service that other services fire events to, not a synchronous dependency that must succeed for other operations to complete.

A business-capability-aligned service in Go:

```go
// user-service/internal/registration/handler.go
package registration

// Everything the user-service needs for registration lives here.
// No cross-service synchronous calls during the happy path.
func (h *Handler) Register(w http.ResponseWriter, r *http.Request) {
    var req RegisterRequest
    if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
        http.Error(w, "invalid request", http.StatusBadRequest)
        return
    }

    user, err := h.svc.Register(r.Context(), req)
    if err != nil {
        if errors.Is(err, ErrEmailTaken) {
            http.Error(w, "email already registered", http.StatusConflict)
            return
        }
        http.Error(w, "registration failed", http.StatusInternalServerError)
        return
    }

    // Publish an event — notification-service handles the welcome email
    // asynchronously. User registration is complete regardless.
    h.events.Publish(UserRegistered{
        UserID:    user.ID,
        Email:     user.Email,
        CreatedAt: user.CreatedAt,
    })

    w.WriteHeader(http.StatusCreated)
    json.NewEncoder(w).Encode(user)
}
```

User registration completes atomically within the `user-service`. The welcome email is a side effect handled by `notification-service` via an event — if it fails, the user is still registered, and the notification service can retry independently.

## In The Wild

A platform I helped migrate had started as a single monolith and been split into microservices by a consultancy that split it by layer: data services, logic services, API services. Two years later it had 24 services but the blast radius for any single feature change was still most of the system because the layers were all deeply coupled.

We ran a bounded context mapping exercise — Domain-Driven Design's tool for identifying where natural service boundaries lie. We interviewed product owners, read the business requirements, and drew capability boundaries around what the business actually did: Catalog, Inventory, Orders, Payments, Fulfillment, Identity.

The migration from 24 layer-services to 6 capability-services took six months but the independence gains were immediate. After the migration, the Catalog team shipped features without coordinating with the Orders team for the first time in two years.

## The Gotchas

**"It depends on too many services" is a design smell, not a feature request.** If a new feature requires touching five services, the service boundaries may be wrong, or the feature is genuinely cross-cutting (in which case it belongs in a new capability service).

**Data ownership must be exclusive.** Two services sharing a database table is still a monolith. Each service should own its data — if `order-service` needs user information, it either calls `user-service` via API or maintains a read replica of the user data it needs (the "local cache" pattern).

**Start with a modular monolith, not microservices.** If you're building from scratch, the right first step is a well-structured monolith with clear internal package boundaries. Splitting into services is much easier when you already know where the boundaries are — and you only learn where the boundaries are by running the system.

**Conway's Law is real.** Your service boundaries will mirror your team structure whether you intend it or not. Design your teams and your services together, not separately.

## Key Takeaway

Service boundaries that align with business capabilities produce services that can be developed, deployed, and scaled independently. Technical-layer splits produce distributed monoliths with all the complexity of microservices and none of the autonomy. Each service should own a complete vertical slice: its API, its logic, its data. Use events for cross-service side effects rather than synchronous calls. Start with a modular monolith and split only when you have clear evidence of where the boundaries should be.

---

[Course Index](/series/go-microservices-patterns) | [Next → Lesson 2: Inter-Service Communication](/post/go/go-micro-communication)
