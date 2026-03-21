---
title: "Lesson 9: Fake Clean Architecture — Layers without purpose are just folders"
author: Atharva Pandey
series: "Go Anti-Patterns & Code Smells"
lesson: 9
keywords:
  - Go
  - golang
  - anti-patterns
  - code quality
tags:
  - Go tutorial
  - golang
  - code quality
date: '2025-05-20T00:00:00.000Z'
---

I cloned a Go repository that was described to me as a "clean architecture" implementation. It had six layers: `handler`, `usecase`, `service`, `repository`, `model`, and `dto`. Every user-related operation required touching at least four files across four packages. When I added a new field to the user profile, I updated the database schema, the `model.User`, the `dto.UserDTO`, the `repository.UserRepository`, the `service.UserService`, the `usecase.UserUseCase`, and the `handler.UserHandler`. Eight files for one field. The indirection was total, the business logic was nowhere — it was scattered across the layers in thin delegating functions that called the layer below and returned the result.

This is fake clean architecture. Real clean architecture has a purpose: separating concerns so that business rules are independent of frameworks, databases, and delivery mechanisms. Fake clean architecture has the folder structure without the purpose — layers that do not enforce any useful boundary, where every change ripples through all of them simultaneously.

## The Problem

Layers that delegate without adding value:

```go
// WRONG — five layers of delegation, zero business logic isolation

// handler/user_handler.go
func (h *UserHandler) GetProfile(w http.ResponseWriter, r *http.Request) {
    id := chi.URLParam(r, "id")
    user, err := h.usecase.GetUserProfile(r.Context(), id) // delegates to usecase
    // ...
}

// usecase/user_usecase.go
func (u *UserUseCase) GetUserProfile(ctx context.Context, id string) (*dto.UserDTO, error) {
    return u.service.GetUserProfile(ctx, id) // delegates to service
}

// service/user_service.go
func (s *UserService) GetUserProfile(ctx context.Context, id string) (*dto.UserDTO, error) {
    user, err := s.repo.FindByID(ctx, id) // delegates to repository
    if err != nil {
        return nil, err
    }
    return dto.FromUser(user), nil // converts model to DTO
}

// repository/user_repository.go
func (r *UserRepository) FindByID(ctx context.Context, id string) (*model.User, error) {
    return r.db.QueryUser(ctx, id) // delegates to database
}
```

Trace through this for `GET /users/{id}`: handler → usecase → service → repository → database. The usecase and service layers add no logic — they are pure pass-throughs. The DTO conversion in the service layer is the only non-trivial operation, and it could live in the handler.

The second failure: packages that depend on each other in the wrong direction, defeating the purpose of the layering:

```go
// WRONG — layers with circular dependencies
// model imports from repository (for pagination types)
// repository imports from model (for User type)
// service imports from repository AND handler (for request types)
// the "dependency rule" of clean architecture is violated in all directions
```

## The Idiomatic Way

The dependency rule of clean architecture is: outer layers depend on inner layers, never the reverse. Business logic does not import HTTP handlers or database drivers. But you do not need six layers to enforce this — in most Go services, three is enough, and sometimes two is correct:

```go
// RIGHT — three meaningful layers: HTTP, domain, storage
// Each layer has a clear purpose:
// - http: decode requests, call domain, encode responses
// - domain: business logic, knows nothing about HTTP or SQL
// - store: persistence, knows nothing about HTTP or domain rules

// domain/user.go — business rules live here
type User struct {
    ID        string
    Name      string
    Email     string
    Plan      string
    CreatedAt time.Time
}

func (u *User) CanUpgrade(targetPlan string) error {
    if u.Plan == targetPlan {
        return errors.New("already on this plan")
    }
    return nil
}

// store/user_store.go — persistence, depends only on domain types
type UserStore struct{ db *sql.DB }

func (s *UserStore) FindByID(ctx context.Context, id string) (*domain.User, error) {
    var u domain.User
    err := s.db.QueryRowContext(ctx,
        "SELECT id, name, email, plan, created_at FROM users WHERE id = $1", id,
    ).Scan(&u.ID, &u.Name, &u.Email, &u.Plan, &u.CreatedAt)
    if err != nil {
        return nil, fmt.Errorf("find user: %w", err)
    }
    return &u, nil
}

// http/user_handler.go — delivery, depends on store, knows nothing about SQL
type UserHandler struct {
    store interface {
        FindByID(ctx context.Context, id string) (*domain.User, error)
    }
}

func (h *UserHandler) GetProfile(w http.ResponseWriter, r *http.Request) {
    id := chi.URLParam(r, "id")
    user, err := h.store.FindByID(r.Context(), id)
    if err != nil {
        http.Error(w, "not found", http.StatusNotFound)
        return
    }
    json.NewEncoder(w).Encode(userResponse(user))
}
```

Three layers, each with a clear purpose. No pass-through delegation. Business logic in the domain package. The handler is responsible for HTTP concerns (decoding, encoding, status codes) and nothing else.

When you do need a service layer — for coordinating multiple stores, applying domain logic that crosses store boundaries, or enforcing business rules that the domain object cannot enforce alone — make it explicit about what logic it actually provides:

```go
// RIGHT — service layer with actual business logic
type OrderService struct {
    orders    OrderStore
    inventory InventoryStore
    payments  PaymentGateway
    mailer    Mailer
}

func (s *OrderService) PlaceOrder(ctx context.Context, req PlaceOrderRequest) (*domain.Order, error) {
    // Actual business logic: reserve inventory, charge payment, create order
    // This cannot live in a handler or a single store — it spans multiple concerns
    if err := s.inventory.Reserve(ctx, req.Items); err != nil {
        return nil, fmt.Errorf("reserve inventory: %w", err)
    }

    charge, err := s.payments.Charge(ctx, req.UserID, req.TotalAmount)
    if err != nil {
        _ = s.inventory.Release(ctx, req.Items) // compensate
        return nil, fmt.Errorf("payment: %w", err)
    }

    order, err := s.orders.Create(ctx, req, charge.ID)
    if err != nil {
        return nil, fmt.Errorf("create order: %w", err)
    }

    _ = s.mailer.SendOrderConfirmation(ctx, order)
    return order, nil
}
```

This service layer earns its existence: it coordinates multiple stores and a gateway, handles compensation logic, and encapsulates a business process that spans several infrastructure components.

## In The Wild

**Package by feature, not by layer.** Instead of `model/`, `repository/`, `service/`, `handler/` at the top level — which forces every change to touch all directories — consider packaging by feature: `user/`, `order/`, `payment/`. Within each feature package, you can have the storage, domain, and HTTP layers organized however makes sense for that feature. This makes features cohesive and changes more local.

**Flat structure for simple services.** A service with four or five entities does not need nested packages. A single `main.go`, an `app/` package, and a `store/` package is often enough. Add structure when you find yourself needing it, not in anticipation of growth.

**Hexagonal architecture in Go.** If you are building something that genuinely needs hexagonal architecture — pluggable databases, multiple delivery mechanisms, independence from all infrastructure — Go can do it, but the "ports and adapters" concept maps naturally to Go interfaces defined at the domain boundary. The adapters are the implementations; the ports are the interfaces. Keep the domain package import-free from anything outside the standard library.

## The Gotchas

**Adding a layer does not add quality.** Code quality comes from clear logic, explicit errors, good naming, and tested behavior — not from the number of indirection layers. A service that is 200 lines of clear, well-tested logic in one file is higher quality than the same logic spread across 200 files in six layers.

**DTOs everywhere.** In fake clean architecture, DTOs (Data Transfer Objects) proliferate — `UserModel`, `UserDTO`, `UserRequest`, `UserResponse`, `UserEntity`. Every layer has its own type and every boundary requires a conversion function. Most of the time, one or two types (domain type + API response) are enough. Add types when they carry different validation, serialization, or behavioral semantics — not to have a type per layer.

**Testing the wrong layer.** With deep layering, teams write tests that test the delegation — that `usecase.GetUser` calls `service.GetUser` — rather than the behavior. These tests have no value and break every time you refactor the layer structure. Test behavior: given these inputs, what outputs and side effects are produced?

## Key Takeaway

Architecture layers should enforce meaningful boundaries, not create ceremony. If adding a new field requires modifying six files in six packages and none of them contain the logic that makes the change meaningful, the architecture is overhead, not structure. Start flat, identify the actual boundaries your design needs (HTTP concerns, business logic, persistence), and add layers only when they enforce a real constraint.

---

**Go Anti-Patterns & Code Smells**

Previous: [Lesson 8: Premature Abstraction — Wrong abstraction costs more than duplication](/post/go/go-anti-premature-abstraction/)
Next: [Lesson 10: Over-Engineering CRUD — Not every API needs a hexagonal architecture](/post/go/go-anti-over-engineering/)
