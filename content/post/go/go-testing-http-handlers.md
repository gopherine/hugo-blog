---
title: "Lesson 4: HTTP Handler Testing — httptest is your best friend"
author: Atharva Pandey
series: "Go Testing Masterclass"
lesson: 4
keywords:
  - Go
  - golang
  - testing
  - httptest
  - HTTP handler testing
  - net/http/httptest
  - ResponseRecorder
  - test server
tags:
  - Go tutorial
  - golang
  - testing
date: '2024-09-01T00:00:00.000Z'
---

Every Go HTTP handler is just a function that takes a `ResponseWriter` and a `*Request`. That's the entire contract. The fact that the standard library ships with a testing package that lets you call that function with a fake writer and a real request — without starting a server, without binding a port — is a gift that too many people overlook. I spent months spinning up actual servers in tests before I discovered `net/http/httptest`. I will not let you make the same mistake.

## The Problem

The naive approach to HTTP handler testing is to start a real server:

```go
// WRONG — starting a real server in tests is slow and fragile
func TestGetUser(t *testing.T) {
    // Start the server in a goroutine
    go http.ListenAndServe(":8080", setupRouter())

    time.Sleep(100 * time.Millisecond) // hope the server is ready

    resp, err := http.Get("http://localhost:8080/users/1")
    if err != nil {
        t.Fatalf("request failed: %v", err)
    }
    defer resp.Body.Close()

    if resp.StatusCode != http.StatusOK {
        t.Errorf("status: got %d, want %d", resp.StatusCode, http.StatusOK)
    }
}
```

This is broken in multiple ways. The `time.Sleep` is a race — on a slow machine, the server isn't ready yet. Port 8080 might already be in use. You can't run multiple tests in parallel because they'd conflict on the port. And you're testing the network stack, which you don't own, instead of your handler logic, which you do.

A subtler problem is testing handlers with too much wiring — constructing the full application just to test one endpoint:

```go
// WRONG — testing one handler by booting the entire application
func TestCreateOrderHandler(t *testing.T) {
    app, err := NewApplication(Config{
        DatabaseURL: "postgres://...",
        RedisURL:    "redis://...",
        // ...
    })
    if err != nil {
        t.Fatal(err)
    }
    defer app.Close()

    // Now make a request to /orders
    // This boots a database connection, Redis, and a full HTTP router
    // just to test JSON parsing and status codes.
}
```

You're testing infrastructure you don't need to test here. Each unit of handler logic should be testable without its dependencies being real.

## The Idiomatic Way

`httptest.NewRecorder()` gives you a `ResponseWriter` that records what your handler writes. `httptest.NewRequest()` builds a `*http.Request` without needing a real connection. Together, they let you call your handler as a plain function.

```go
// RIGHT — testing a handler with httptest.NewRecorder
func TestGetUserHandler(t *testing.T) {
    tests := []struct {
        name       string
        userID     string
        wantStatus int
        wantBody   string
    }{
        {
            name:       "existing user",
            userID:     "1",
            wantStatus: http.StatusOK,
            wantBody:   `"id":1`,
        },
        {
            name:       "non-existent user",
            userID:     "999",
            wantStatus: http.StatusNotFound,
            wantBody:   `"error"`,
        },
        {
            name:       "invalid id",
            userID:     "abc",
            wantStatus: http.StatusBadRequest,
        },
    }

    store := NewInMemoryUserStore() // fast, no external deps
    store.Add(&User{ID: 1, Name: "Alice"})
    handler := NewUserHandler(store)

    for _, tt := range tests {
        tt := tt
        t.Run(tt.name, func(t *testing.T) {
            t.Parallel()
            req := httptest.NewRequest(http.MethodGet, "/users/"+tt.userID, nil)
            rec := httptest.NewRecorder()

            handler.GetUser(rec, req)

            res := rec.Result()
            defer res.Body.Close()

            if res.StatusCode != tt.wantStatus {
                t.Errorf("status: got %d, want %d", res.StatusCode, tt.wantStatus)
            }

            if tt.wantBody != "" {
                body, _ := io.ReadAll(res.Body)
                if !strings.Contains(string(body), tt.wantBody) {
                    t.Errorf("body %q does not contain %q", string(body), tt.wantBody)
                }
            }
        })
    }
}
```

For testing middleware, or when you need to test the full routing chain without a real server, `httptest.NewServer` spins up a local server on an OS-assigned port and tears it down when you call `Close`:

```go
// RIGHT — httptest.NewServer for middleware and routing integration tests
func TestAuthMiddleware(t *testing.T) {
    handler := AuthMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        userID := r.Context().Value(contextKeyUserID)
        fmt.Fprintf(w, "user=%v", userID)
    }))

    srv := httptest.NewServer(handler)
    defer srv.Close()

    t.Run("valid token", func(t *testing.T) {
        req, _ := http.NewRequest(http.MethodGet, srv.URL+"/", nil)
        req.Header.Set("Authorization", "Bearer valid-token-123")

        resp, err := srv.Client().Do(req)
        if err != nil {
            t.Fatal(err)
        }
        defer resp.Body.Close()

        if resp.StatusCode != http.StatusOK {
            t.Errorf("status: got %d, want 200", resp.StatusCode)
        }
        body, _ := io.ReadAll(resp.Body)
        if !strings.Contains(string(body), "user=") {
            t.Errorf("expected user context in body, got %q", string(body))
        }
    })

    t.Run("missing token", func(t *testing.T) {
        resp, err := http.Get(srv.URL + "/")
        if err != nil {
            t.Fatal(err)
        }
        defer resp.Body.Close()
        if resp.StatusCode != http.StatusUnauthorized {
            t.Errorf("status: got %d, want 401", resp.StatusCode)
        }
    })
}
```

The key insight: `srv.Client()` returns an HTTP client pre-configured to talk to this test server. Use it instead of `http.DefaultClient` so your test doesn't accidentally hit production URLs.

## In The Wild

The pattern I reach for on every project is a handler test helper that wires up all the routing middleware and returns a test server:

```go
func newTestServer(t *testing.T, store Store) *httptest.Server {
    t.Helper()
    router := chi.NewRouter()
    router.Use(LoggingMiddleware)
    router.Use(RequestIDMiddleware)
    h := NewHandlers(store)
    router.Get("/users/{id}", h.GetUser)
    router.Post("/orders", h.CreateOrder)
    srv := httptest.NewServer(router)
    t.Cleanup(srv.Close)
    return srv
}

func TestOrderFlow(t *testing.T) {
    store := NewInMemoryStore()
    srv := newTestServer(t, store)

    // POST /orders
    body := `{"user_id":1,"item_id":"sku-001","quantity":2}`
    resp, err := srv.Client().Post(srv.URL+"/orders", "application/json",
        strings.NewReader(body))
    if err != nil {
        t.Fatal(err)
    }
    defer resp.Body.Close()

    if resp.StatusCode != http.StatusCreated {
        t.Fatalf("expected 201, got %d", resp.StatusCode)
    }

    var order Order
    if err := json.NewDecoder(resp.Body).Decode(&order); err != nil {
        t.Fatalf("decode response: %v", err)
    }
    if order.ID == 0 {
        t.Error("expected non-zero order ID")
    }
}
```

Using `t.Cleanup(srv.Close)` instead of `defer srv.Close()` means the server shuts down after all subtests complete, not after the function returns — critical when you have parallel subtests that outlive the parent function scope.

## The Gotchas

**`rec.Result()` vs `rec.Body` directly.** Use `rec.Result()` — it returns a proper `*http.Response` with headers correctly parsed. Reading `rec.Body` directly as a `bytes.Buffer` bypasses the response structure and makes header assertions harder.

**Route parameters aren't set automatically.** When you use `httptest.NewRequest` and call the handler directly (not through a router), the URL path variables (like `{id}` in chi or `{id}` in the standard library's new mux) are not populated. Either test the handler through the router, or pass path params via the request context manually for the router you're using.

**`httptest.NewServer` uses a real port.** It's not on loopback — it's a real TCP server. In environments with strict firewall rules or port sandboxing, this can fail. `httptest.NewRecorder` has no such constraint.

**Checking response body after `rec.Result()`.** `rec.Result().Body` is a snapshot. You can't call `rec.Result()` twice and expect to read the body twice — it's an `io.ReadCloser` that drains. Read it once and keep the bytes.

## Key Takeaway

The `net/http/httptest` package is designed exactly for this. It's not a workaround — it's the standard approach. `httptest.NewRecorder` for testing individual handlers, `httptest.NewServer` for middleware and routing. No ports to manage, no timing issues, no real network calls. Your HTTP handler tests should be as fast as unit tests and as reliable as your logic — because that's exactly what they are.

---

[Course Index](/series/go-testing-masterclass) | [← Lesson 3](/post/go/go-testing-integration) | [Next → Lesson 5: Database Testing with Testcontainers](/post/go/go-testing-database)
