---
title: "Lesson 6: API Versioning — URL, header, or content negotiation"
author: Atharva Pandey
keywords:
  - API versioning
  - REST API
  - backward compatibility
  - software architecture
  - backend engineering
tags:
  - fundamentals
  - architecture
series: "Software Architecture That Survives"
lesson: 6
date: '2024-07-24T00:00:00.000Z'
---

The first time I shipped a breaking change to a production API without a version, I got an incident at 2am because a partner integration stopped working. They'd been calling `/api/orders` for six months. I changed the response shape. Their code broke. That was when "API versioning" moved from "thing to do eventually" to "thing to do before you ship anything external."

There's no universally right answer to how you version APIs. There are tradeoffs, and the choice you make will shape your codebase for years. Here's how I think about it.

## How It Works

There are three main approaches: URL path versioning, request header versioning, and content negotiation. A fourth — query parameter versioning — exists but is widely considered bad practice and I won't recommend it.

**URL Path Versioning**

Version is part of the URL:

```
GET /v1/orders/123
GET /v2/orders/123
```

This is the most common approach and the easiest to understand. The version is visible, debuggable in browser history and server logs, and routes cleanly in any HTTP infrastructure.

```go
// Go router setup for URL versioning
mux := http.NewServeMux()
mux.Handle("/v1/", v1.Handler())
mux.Handle("/v2/", v2.Handler())
```

Drawbacks: the version is part of the resource identifier, which is semantically awkward — the resource hasn't changed, just its representation. You also pollute your URL namespace.

**Header Versioning**

Version is in a custom request header:

```
GET /orders/123
Api-Version: 2024-07-24
```

Or using the Accept header:

```
GET /orders/123
Accept: application/vnd.example.v2+json
```

Header versioning keeps URLs clean. But it's invisible in browser address bars and harder to test with curl unless you remember to include the header. It's also harder to route at the load balancer level if you want version-specific backends.

**Content Negotiation (Accept Header)**

The standard HTTP mechanism for negotiating response format:

```
GET /orders/123
Accept: application/vnd.example.orders.v2+json
```

Server responds with:
```
Content-Type: application/vnd.example.orders.v2+json
```

This is RESTful-purist-approved but practically unusual. The overhead is in maintaining multiple media types and the complexity it adds to routing and middleware.

**Stripe's Date-Based Versioning**

Stripe uses a clever variant: version by date, not by number. Each API version is a date string (`2024-07-01`). The account has a default version (set when created). Requests can override with the `Stripe-Version` header. Behavior changes are recorded by date.

This works well for API providers with long-term clients — you can tell exactly which behavior a client gets based on their version date, and you can clearly communicate "we changed X behavior on this date."

**What a Breaking Change Actually Is**

Understanding what to version requires understanding what's breaking. Breaking changes are:

- Removing a field from a response
- Renaming a field
- Changing a field's type (string to integer, etc.)
- Removing an endpoint
- Changing authentication requirements
- Making a previously optional field required

Non-breaking changes are additive:

- Adding new optional fields to a response
- Adding new optional request parameters
- Adding new endpoints
- Adding new enum values (mostly — depends on client behavior)

Non-breaking changes should never require a version bump. The ability to add fields without versioning is why you should design your response schemas to allow unknown fields from the start.

## Why It Matters

The versioning strategy you choose determines how you manage backward compatibility as your API evolves. Get it wrong and you have one of two failure modes:

1. **Never break clients** → You accumulate technical debt in your API because you can't clean up bad design decisions. Every mistake is permanent.
2. **Break clients without warning** → You damage trust with integrators and partners. They stop relying on you.

The right approach allows you to evolve your API while giving clients enough time to migrate.

## Production Example

URL path versioning is what I use for public APIs. Here's a full Go implementation with versioned handlers that share business logic:

```go
package main

import (
    "encoding/json"
    "net/http"

    "github.com/example/orders/service"
)

// V1 response — the original shape
type OrderResponseV1 struct {
    ID       string `json:"id"`
    Status   string `json:"status"`
    Total    int64  `json:"total"` // in cents, poorly named
    Customer string `json:"customer"` // just the name, no ID
}

// V2 response — fixed field naming, added structured customer
type OrderResponseV2 struct {
    ID         string          `json:"id"`
    Status     string          `json:"status"`
    TotalCents int64           `json:"total_cents"` // clearer naming
    Customer   CustomerV2      `json:"customer"`
    CreatedAt  string          `json:"created_at"`
}

type CustomerV2 struct {
    ID   string `json:"id"`
    Name string `json:"name"`
}

// Both handlers call the same service layer
type OrderHandlerV1 struct{ svc *service.Orders }
type OrderHandlerV2 struct{ svc *service.Orders }

func (h *OrderHandlerV1) GetOrder(w http.ResponseWriter, r *http.Request) {
    id := r.PathValue("id")
    order, err := h.svc.GetOrder(r.Context(), id)
    if err != nil {
        writeError(w, err)
        return
    }
    // Map domain model to V1 response
    resp := OrderResponseV1{
        ID:       order.ID,
        Status:   order.Status.String(),
        Total:    order.Total.Cents,
        Customer: order.CustomerName, // old flattened field
    }
    json.NewEncoder(w).Encode(resp)
}

func (h *OrderHandlerV2) GetOrder(w http.ResponseWriter, r *http.Request) {
    id := r.PathValue("id")
    order, err := h.svc.GetOrder(r.Context(), id)
    if err != nil {
        writeError(w, err)
        return
    }
    // Map domain model to V2 response — richer structure
    resp := OrderResponseV2{
        ID:         order.ID,
        Status:     order.Status.String(),
        TotalCents: order.Total.Cents,
        Customer: CustomerV2{
            ID:   order.CustomerID,
            Name: order.CustomerName,
        },
        CreatedAt: order.CreatedAt.Format(time.RFC3339),
    }
    json.NewEncoder(w).Encode(resp)
}
```

**Sunset headers** — telling clients when a version is going away:

```go
func (h *OrderHandlerV1) GetOrder(w http.ResponseWriter, r *http.Request) {
    // Inform clients this version is deprecated
    w.Header().Set("Deprecation", "true")
    w.Header().Set("Sunset", "Sat, 01 Mar 2025 00:00:00 GMT")
    w.Header().Set("Link", `</v2/orders>; rel="successor-version"`)
    // ... rest of handler
}
```

**Version-based routing in nginx:**

```nginx
# Route v1 and v2 to different backend pools if needed
upstream v1_backend {
    server orders-v1:8080;
}
upstream v2_backend {
    server orders-v2:8080;
}

location ~ ^/v1/ {
    proxy_pass http://v1_backend;
}
location ~ ^/v2/ {
    proxy_pass http://v2_backend;
}
```

**My deprecation process:**

1. Ship v2.
2. Add `Deprecation` and `Sunset` headers to v1 responses.
3. Monitor v1 traffic — track unique callers via API key or user agent.
4. Email/contact active v1 callers with migration guide.
5. At sunset date, return 410 Gone with a helpful body: `{"error": "v1 retired, see https://docs.example.com/migration/v2"}`.
6. Remove v1 code 3 months after the 410 (to handle anyone who missed the deadline).

## The Tradeoffs

**URL versioning vs header versioning**: URL versioning wins on simplicity and debuggability. Header versioning wins on semantic purity. For most teams, simplicity wins. Use URL versioning unless you have a specific reason not to.

**Version granularity**: Versioning the entire API (`/v2/`) is easier to reason about but means a major bump for any change. Per-endpoint versioning (`/orders/v2/`) is more surgical but creates an explosion of version combinations. I prefer whole-API versioning for public APIs, per-endpoint versioning for internal ones where teams control both sides.

**How many versions to maintain**: Supporting many parallel API versions indefinitely is a maintenance nightmare. Two concurrent versions maximum — the current stable version and one version behind. Anything older than that gets a sunset deadline.

**Internal vs external APIs**: Internal APIs (service-to-service) can evolve faster because you control both sides. External APIs (partner integrations, mobile apps) need longer deprecation windows. The difference matters when choosing version cadence and sunset timelines.

**Versioning response shape vs behavior**: Sometimes what changes is not the JSON shape but the behavior (a status transition rule changes, a validation rule tightens). Version this too — but document it explicitly in your changelog, because behavior changes without shape changes are invisible to clients who aren't reading your docs.

## Key Takeaway

API versioning is not optional for any API with external consumers. URL path versioning is the pragmatic choice: visible, debuggable, and routes cleanly. Design your schemas to allow additive changes without versioning — use optional fields, allow unknown fields in your parsers, and never rename fields in place. When you must break compatibility, version, communicate with sunset headers, give clients a migration window measured in months, and then retire cleanly. Two active versions maximum. Old versions are technical debt that accumulates interest.

---

**Previous:** [Lesson 5: DDD Essentials](/post/fundamentals/arch-ddd/)
**Next:** [Lesson 7: Migration Strategies — Strangler fig and feature flags](/post/fundamentals/arch-migrations/)
