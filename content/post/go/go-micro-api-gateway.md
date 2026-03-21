---
title: 'Lesson 6: API Gateway Patterns — One entry point, many backends'
author: Atharva Pandey
series: "Go Microservices Patterns"
lesson: 6
keywords:
  - Go
  - golang
  - microservices
  - API gateway
  - reverse proxy
  - rate limiting
  - authentication
tags:
  - Go tutorial
  - golang
  - microservices
date: '2025-06-18T00:00:00.000Z'
---

The first time I connected a mobile frontend directly to 11 microservices, I created 11 places for the mobile team to integrate, 11 different auth schemes to understand, 11 different error formats to handle, and a situation where a single product screen required 6 parallel API calls because the data was spread across 6 services. An API gateway solves all of this: one URL, one auth scheme, one error format, and the ability to aggregate multiple service responses into a single response the client actually needs.

## The Problem

Without an API gateway, clients are tightly coupled to the internal service topology.

```go
// Mobile client making 4 calls to render one screen — WRONG architectural approach
// This is what the mobile developer has to do without a gateway:

// Call 1: Get user profile
GET https://user-service.internal:8081/users/123

// Call 2: Get recent orders
GET https://order-service.internal:8082/users/123/orders?limit=5

// Call 3: Get account balance
GET https://payment-service.internal:8083/accounts/123/balance

// Call 4: Get notifications
GET https://notification-service.internal:8084/users/123/notifications?unread=true

// The mobile client now knows about 4 internal services, 4 ports,
// 4 authentication tokens, and must handle 4 independent failure modes.
```

Refactoring any internal service breaks the mobile client directly. The mobile app needs to implement retry logic for four services independently. Moving a service to a different host requires a mobile app release.

## The Idiomatic Way

An API gateway in Go is a reverse proxy with cross-cutting concerns applied at one layer.

**Building a lightweight gateway in Go:**

```go
// gateway/main.go
package main

import (
    "net/http"
    "net/http/httputil"
    "net/url"
)

type Gateway struct {
    routes []Route
    mw     []Middleware
}

type Route struct {
    Prefix  string
    Backend *url.URL
    Rewrite func(r *http.Request) // optional path rewriting
}

func (g *Gateway) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    for _, route := range g.routes {
        if strings.HasPrefix(r.URL.Path, route.Prefix) {
            if route.Rewrite != nil {
                route.Rewrite(r)
            }
            proxy := httputil.NewSingleHostReverseProxy(route.Backend)
            proxy.ServeHTTP(w, r)
            return
        }
    }
    http.NotFound(w, r)
}

func main() {
    gw := &Gateway{
        routes: []Route{
            {
                Prefix:  "/api/users",
                Backend: mustParseURL("http://user-service:8080"),
            },
            {
                Prefix:  "/api/orders",
                Backend: mustParseURL("http://order-service:8080"),
            },
            {
                Prefix:  "/api/payments",
                Backend: mustParseURL("http://payment-service:8080"),
            },
        },
    }

    // Apply middleware: auth → rate limit → logging → proxy
    handler := authMiddleware(rateLimitMiddleware(loggingMiddleware(gw)))
    http.ListenAndServe(":8080", handler)
}
```

**Authentication middleware — verify once, pass identity downstream:**

```go
// gateway/middleware/auth.go
func authMiddleware(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        token := r.Header.Get("Authorization")
        if token == "" {
            http.Error(w, "unauthorized", http.StatusUnauthorized)
            return
        }

        claims, err := verifyJWT(token)
        if err != nil {
            http.Error(w, "invalid token", http.StatusUnauthorized)
            return
        }

        // Downstream services receive identity as a trusted header
        // They don't need to verify the JWT — the gateway already did
        r.Header.Set("X-User-ID", strconv.FormatInt(claims.UserID, 10))
        r.Header.Set("X-User-Role", claims.Role)
        r.Header.Del("Authorization") // Don't forward the raw token

        next.ServeHTTP(w, r)
    })
}
```

**Response aggregation for dashboard endpoints:**

```go
// gateway/handlers/dashboard.go
// A single endpoint that aggregates data from multiple services
func (h *DashboardHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    userID := r.Header.Get("X-User-ID")
    ctx := r.Context()

    // Fan out to all three services concurrently
    type result[T any] struct {
        val T
        err error
    }

    profileCh := make(chan result[*UserProfile], 1)
    ordersCh := make(chan result[[]Order], 1)
    balanceCh := make(chan result[*Balance], 1)

    go func() {
        p, err := h.users.GetProfile(ctx, userID)
        profileCh <- result[*UserProfile]{p, err}
    }()
    go func() {
        o, err := h.orders.GetRecent(ctx, userID, 5)
        ordersCh <- result[[]Order]{o, err}
    }()
    go func() {
        b, err := h.payments.GetBalance(ctx, userID)
        balanceCh <- result[*Balance]{b, err}
    }()

    profile := <-profileCh
    orders := <-ordersCh
    balance := <-balanceCh

    if profile.err != nil {
        http.Error(w, "failed to load profile", http.StatusInternalServerError)
        return
    }

    // Non-critical data — return partial response if these fail
    resp := DashboardResponse{
        Profile: profile.val,
        Orders:  orders.val,  // nil if orders.err != nil
        Balance: balance.val, // nil if balance.err != nil
    }

    json.NewEncoder(w).Encode(resp)
}
```

The mobile client makes one call to `/api/dashboard` instead of four — the gateway handles the fan-out and aggregation.

## In The Wild

After introducing a gateway on a platform with 14 microservices, the mobile team went from managing 14 auth tokens and 14 base URLs to managing one. We moved rate limiting, request logging, and JWT verification from 14 individual services into the gateway. That's 14 places where auth code could have bugs, reduced to one. We caught and fixed two subtle JWT expiry bugs during the migration that had been living in individual services undetected.

The latency improvement surprised us. Dashboard-style endpoints went from requiring 4 serial or ad-hoc parallel client-side calls to one gateway call with proper server-side parallelism. The mobile app's time-to-interactive on the home screen dropped by 40%.

## The Gotchas

**Don't put business logic in the gateway.** The gateway's job is cross-cutting concerns: routing, auth, rate limiting, response aggregation. Business logic belongs in the services. A gateway that knows about business rules becomes a bottleneck and a deployment coupling point — exactly what you're trying to avoid.

**Circuit breaking belongs at the gateway.** If `order-service` is down, the gateway should return a degraded response (e.g., dashboard without orders) rather than returning 500 for the entire dashboard. Use a circuit breaker library like `sony/gobreaker` at the proxy level.

**Rate limiting must be distributed.** A per-gateway-instance rate limiter is bypassed when the gateway scales out. Use a shared Redis-backed rate limiter so limits are enforced globally, not per-instance.

**Gateway latency adds up.** Every request passes through the gateway. Make it fast. Avoid synchronous calls to any external system (no auth service roundtrips — verify JWTs locally). Profile and optimize the hot path; 1ms added to every request becomes significant at scale.

## Key Takeaway

🎓 **Course Complete!** You've finished "Go Microservices Patterns."

An API gateway is the single entry point that decouples clients from your internal service topology. It centralizes cross-cutting concerns — authentication, rate limiting, logging — that would otherwise be duplicated across every service. It enables response aggregation that reduces the number of round trips clients must make. Build it with Go's `net/http/httputil.ReverseProxy` as the foundation, apply cross-cutting middleware as a chain, and resist the temptation to add business logic. Keep it fast, keep it simple, and let the services do the domain work.

---

[← Lesson 5: Saga Pattern](/post/go/go-micro-sagas) | [Course Index](/series/go-microservices-patterns)
