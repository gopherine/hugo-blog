---
title: "Lesson 6: Dependency Direction — Always depend inward, never outward"
author: Atharva Pandey
series: "Go Package & Module Architecture"
lesson: 6
keywords: [Go, golang, packages, modules, architecture]
tags: [Go tutorial, golang, architecture]
date: '2025-01-02T00:00:00.000Z'
---

We fixed cycles in lesson 2. But removing cycles is a necessary condition for good architecture, not a sufficient one. You can have a perfectly cycle-free dependency graph where every arrow points in the wrong direction, and the result is still a system that is painful to test, extend, and reason about.

Dependency direction is about more than just "does A import B." It is about which layer owns the core business rules and which layers are allowed to know about which other layers. Get this wrong and your domain logic ends up tangled with your database driver. Get it right and you can replace your entire HTTP layer, or swap Postgres for a different store, without changing a single line of business logic.

## The Problem

The classic mistake is an architecture where business logic reaches out to infrastructure. An order service that directly instantiates a Postgres connection. A billing domain that formats HTTP responses. A customer record that knows about the specific JSON structure of a third-party payment gateway's API.

This is not just aesthetically displeasing. It is structurally fragile. Every time the infrastructure changes — a new database driver version, a new HTTP framework, a different message broker — you have to touch your business logic. Your domain objects become untestable without spinning up real infrastructure. The cost of change accumulates in the most sensitive part of the codebase.

The principle of dependency direction says: the core domain should depend on nothing. Infrastructure should depend on the domain. The flow of source-code dependencies runs inward, toward the most stable layer — the domain — and outward only for composition.

## The Idiomatic Way

**Wrong: domain logic reaching outward into infrastructure**

```go
// package order — domain package that knows too much
package order

import (
    "context"
    "database/sql"
    "encoding/json"
    "net/http"

    "github.com/lib/pq" // Postgres driver — domain knows about the DB driver!
)

type Order struct {
    ID     int64
    Total  float64
    Status string
}

// Domain logic directly coupled to Postgres
func Place(ctx context.Context, db *sql.DB, total float64) (*Order, error) {
    o := &Order{Total: total, Status: "pending"}
    err := db.QueryRowContext(ctx,
        "INSERT INTO orders (total, status) VALUES ($1, $2) RETURNING id",
        o.Total, o.Status,
    ).Scan(&o.ID)
    if pq.ErrorCode(err) == "23505" { // Postgres-specific error code in domain!
        return nil, ErrDuplicate
    }
    return o, err
}

// Domain logic directly coupled to HTTP
func HandlePlace(w http.ResponseWriter, r *http.Request) {
    var req struct{ Total float64 }
    _ = json.NewDecoder(r.Body).Decode(&req)
    o, err := Place(r.Context(), globalDB, req.Total)
    if err != nil {
        http.Error(w, err.Error(), http.StatusInternalServerError)
        return
    }
    _ = json.NewEncoder(w).Encode(o)
}

var globalDB *sql.DB
var ErrDuplicate = fmt.Errorf("order already exists")
```

This `order` package imports `database/sql`, a Postgres driver, `net/http`, and `encoding/json`. To unit-test the business logic of placing an order, you need all of that infrastructure available. The domain is polluted with infrastructure knowledge at every level.

**Right: domain at the center, infrastructure at the edge**

The domain package defines what it needs through interfaces. It does not know or care how those interfaces are satisfied.

```go
// package order — pure domain, zero infrastructure imports
package order

import (
    "context"
    "errors"
    "fmt"
    "time"
)

var (
    ErrInvalidTotal   = errors.New("order total must be positive")
    ErrAlreadyFulfilled = errors.New("order is already fulfilled")
)

type Status string

const (
    StatusPending   Status = "pending"
    StatusFulfilled Status = "fulfilled"
    StatusCancelled Status = "cancelled"
)

type Order struct {
    ID        int64
    Total     float64
    Status    Status
    CreatedAt time.Time
}

// Repository is defined in the domain — it describes what the domain
// needs from persistence, in domain terms. Not in SQL terms.
type Repository interface {
    Save(ctx context.Context, o *Order) error
    FindByID(ctx context.Context, id int64) (*Order, error)
}

type Service struct {
    repo Repository
}

func NewService(repo Repository) *Service {
    return &Service{repo: repo}
}

// Place contains pure business logic. No SQL. No HTTP. No JSON.
func (s *Service) Place(ctx context.Context, total float64) (*Order, error) {
    if total <= 0 {
        return nil, fmt.Errorf("placing order: %w", ErrInvalidTotal)
    }
    o := &Order{
        Total:     total,
        Status:    StatusPending,
        CreatedAt: time.Now(),
    }
    if err := s.repo.Save(ctx, o); err != nil {
        return nil, fmt.Errorf("placing order: %w", err)
    }
    return o, nil
}

// Fulfill contains business rules for fulfillment.
func (s *Service) Fulfill(ctx context.Context, id int64) (*Order, error) {
    o, err := s.repo.FindByID(ctx, id)
    if err != nil {
        return nil, fmt.Errorf("fulfilling order %d: %w", id, err)
    }
    if o.Status != StatusPending {
        return nil, fmt.Errorf("fulfilling order %d: %w", id, ErrAlreadyFulfilled)
    }
    o.Status = StatusFulfilled
    if err := s.repo.Save(ctx, o); err != nil {
        return nil, fmt.Errorf("fulfilling order %d: %w", id, err)
    }
    return o, nil
}
```

Now the infrastructure layers depend on the domain, not the other way around:

```go
// package postgres — infrastructure layer, depends on domain
package postgres

import (
    "context"
    "database/sql"
    "fmt"

    "myapp/order" // infrastructure imports domain ✓
)

// OrderRepository implements order.Repository using Postgres.
// The domain never imported this package.
type OrderRepository struct {
    db *sql.DB
}

func NewOrderRepository(db *sql.DB) *OrderRepository {
    return &OrderRepository{db: db}
}

func (r *OrderRepository) Save(ctx context.Context, o *order.Order) error {
    if o.ID == 0 {
        return r.db.QueryRowContext(ctx,
            "INSERT INTO orders (total, status, created_at) VALUES ($1, $2, $3) RETURNING id",
            o.Total, string(o.Status), o.CreatedAt,
        ).Scan(&o.ID)
    }
    _, err := r.db.ExecContext(ctx,
        "UPDATE orders SET status = $1 WHERE id = $2",
        string(o.Status), o.ID,
    )
    return err
}

func (r *OrderRepository) FindByID(ctx context.Context, id int64) (*order.Order, error) {
    o := &order.Order{}
    var status string
    err := r.db.QueryRowContext(ctx,
        "SELECT id, total, status, created_at FROM orders WHERE id = $1", id,
    ).Scan(&o.ID, &o.Total, &status, &o.CreatedAt)
    if err != nil {
        return nil, fmt.Errorf("querying order %d: %w", id, err)
    }
    o.Status = order.Status(status)
    return o, nil
}
```

```go
// package httphandler — HTTP layer depends on domain, not on postgres
package httphandler

import (
    "encoding/json"
    "net/http"

    "myapp/order" // HTTP layer imports domain ✓
)

type OrderHandler struct {
    svc *order.Service
}

func NewOrderHandler(svc *order.Service) *OrderHandler {
    return &OrderHandler{svc: svc}
}

func (h *OrderHandler) Place(w http.ResponseWriter, r *http.Request) {
    var req struct {
        Total float64 `json:"total"`
    }
    if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
        http.Error(w, "bad request", http.StatusBadRequest)
        return
    }
    o, err := h.svc.Place(r.Context(), req.Total)
    if err != nil {
        http.Error(w, err.Error(), http.StatusInternalServerError)
        return
    }
    w.Header().Set("Content-Type", "application/json")
    _ = json.NewEncoder(w).Encode(o)
}
```

```go
// package main — the only place that knows about all three layers
package main

import (
    "database/sql"
    "log"
    "net/http"

    _ "github.com/lib/pq"
    "myapp/httphandler"
    "myapp/order"
    "myapp/postgres"
)

func main() {
    db, err := sql.Open("postgres", "postgres://localhost/myapp?sslmode=disable")
    if err != nil {
        log.Fatal(err)
    }

    repo := postgres.NewOrderRepository(db)
    svc := order.NewService(repo)
    handler := httphandler.NewOrderHandler(svc)

    http.HandleFunc("/orders", handler.Place)
    log.Fatal(http.ListenAndServe(":8080", nil))
}
```

The dependency arrows: `main` → `httphandler` → `order` ← `postgres` ← `main`. The domain (`order`) is at the center. Infrastructure layers (`postgres`, `httphandler`) point inward toward it. `main` is the composition root.

## In The Wild

Clean Architecture, Hexagonal Architecture (Ports and Adapters), and Onion Architecture are all names for variations of this same idea. The specific terminology varies, but the dependency direction rule is consistent across all of them: the domain is at the center and has no outward dependencies.

In the `go-chi/chi` ecosystem, the HTTP routing layer has zero awareness of any database driver or ORM. It depends on `net/http` from the standard library and nothing else. Handlers are wired in by the application. This is the pattern working at the framework level.

The `ent` ORM generates typed, domain-aware code that infrastructure packages use. The generated code lives outside your domain logic. Your domain defines what entities it works with; `ent` provides the persistence mechanism that satisfies your `Repository` interfaces.

## The Gotchas

**Gotcha 1: the "anemic domain model" trap.** When people first apply this pattern, they sometimes end up with a domain package that is just a bag of structs with no behavior. All the logic ends up in "service" classes that do not belong to the domain either. Push actual business rules — validations, state transitions, invariants — into the domain types themselves.

**Gotcha 2: leaking infrastructure types into the domain.** If your domain `Repository` interface returns `*sql.Rows`, you have leaked infrastructure into the domain. The interface should speak domain language: `*Order`, `[]Order`, `error`. The mapping to and from database types happens in the infrastructure layer.

**Gotcha 3: putting the composition root in the wrong place.** The wiring of all layers together should happen in one place: `main`, or a dedicated `app` package. If any domain package or infrastructure package is doing the wiring, the architecture is starting to break down.

**Gotcha 4: over-abstracting early.** Not every application needs four strict layers. A small CRUD service may be perfectly fine with a thin layer between HTTP and database. Apply the dependency direction principle judiciously — the benefit is proportional to the complexity and longevity of the application.

## Key Takeaway

Dependency direction is not just a style preference. It is what determines whether your business logic can be tested in isolation, whether your infrastructure can be swapped, and whether your domain stays comprehensible as the system grows. Always ask: does this dependency arrow point inward toward the domain, or outward toward infrastructure? If it points outward, there is an interface to define and an inversion to apply.

When the domain depends on nothing, it belongs to nobody — which means it is free to evolve on its own terms, tested without mocks of infrastructure, and readable without knowing anything about how data is stored or transported.

---

**Series: Go Package & Module Architecture**

- [Lesson 1: Package Boundaries](/post/go/go-pkg-boundaries/)
- [Lesson 2: Avoiding Cyclic Dependencies](/post/go/go-pkg-cyclic-deps/)
- [Lesson 3: internal/ Usage Patterns](/post/go/go-pkg-internal/)
- [Lesson 4: Interface Placement](/post/go/go-pkg-interface-placement/)
- [Lesson 5: Monolith vs Multi-Module](/post/go/go-pkg-mono-vs-multi/)
- Lesson 6: Dependency Direction — *you are here*
- [Lesson 7: go.work and Workspace Mode](/post/go/go-pkg-workspaces/)
