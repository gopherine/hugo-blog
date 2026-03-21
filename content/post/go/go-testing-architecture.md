---
title: "Lesson 10: Test Architecture — Tests that survive refactoring"
author: Atharva Pandey
series: "Go Testing Masterclass"
lesson: 10
keywords:
  - Go
  - golang
  - testing
  - test architecture
  - test design
  - refactoring
  - test pyramid
  - maintainable tests
  - test organization
tags:
  - Go tutorial
  - golang
  - testing
date: '2025-03-25T00:00:00.000Z'
---

Every test suite starts clean. Then the codebase grows, the team grows, deadlines hit, and gradually the tests become the thing you dread touching. You add a feature and a hundred tests break — not because the feature is wrong, but because the tests were coupled to implementation details. You rename a method and spend more time updating tests than writing the feature. Sound familiar? The problem isn't the tests themselves. It's the architecture — the structure, the layering, and the principles that determine whether your test suite stays an asset or becomes a liability.

This is the lesson I wish I'd had at the start. Not a specific technique, but the underlying principles that make every other technique pay off.

## The Problem

The most common architectural failure is tests that know too much about implementation:

```go
// WRONG — tests tightly coupled to internal implementation
func TestOrderService_CreateOrder(t *testing.T) {
    mockDB := new(MockDatabase)
    mockQueue := new(MockMessageQueue)
    mockInventory := new(MockInventoryClient)
    mockMetrics := new(MockMetricsRecorder)

    // Test knows the exact sequence of internal calls
    mockInventory.On("Reserve", "sku-001", 2).Return("reservation-xyz", nil)
    mockDB.On("Begin").Return(mockTx, nil)
    mockDB.On("InsertOrder", mockTx, mock.Anything).Return("ord-123", nil)
    mockQueue.On("Publish", "order.created", mock.Anything).Return(nil)
    mockMetrics.On("Increment", "order.created").Return()
    mockTx.On("Commit").Return(nil)

    svc := NewOrderService(mockDB, mockQueue, mockInventory, mockMetrics)
    order, err := svc.CreateOrder(ctx, OrderRequest{
        UserID: 1, ItemID: "sku-001", Quantity: 2,
    })

    if err != nil {
        t.Fatal(err)
    }
    mockDB.AssertExpectations(t)
    mockQueue.AssertExpectations(t)
    mockInventory.AssertExpectations(t)
    mockMetrics.AssertExpectations(t)
}
```

This test will break every time you refactor `CreateOrder` internally. Rename the metrics call. Reorder the database operations. Add a new internal step. Every change — even one that preserves the public contract — will break this test. You've written a test that documents your implementation, not your behaviour.

The opposite failure is tests that are too coarse — testing too much in one test:

```go
// WRONG — one giant test that covers everything
func TestEntireOrderFlow(t *testing.T) {
    // Sets up the entire world
    db := setupTestDB(t)
    queue := setupTestQueue(t)
    inventory := setupInventory(t)
    http := setupHTTPServer(t)

    // Then chains twenty assertions into one test
    // When it fails, you have no idea which step broke
    resp := http.Post("/register", ...)
    resp = http.Post("/login", ...)
    resp = http.Post("/cart/add", ...)
    resp = http.Post("/orders", ...)
    resp = http.Get("/orders/"+orderID, ...)
    // ... fifteen more steps
}
```

When this test fails, you get a single red test. Which of the twenty steps failed? You have to read through the entire test body and match the failure message to a step. The test is telling you "something is wrong" without telling you what.

## The Idiomatic Way

Good test architecture follows three principles: test behaviour not implementation, one assertion per concept, and layer your tests by scope.

**Test behaviour, not implementation.** A test should describe what the function promises, not how it keeps that promise:

```go
// RIGHT — test describes the contract, not the implementation
func TestOrderService_CreateOrder(t *testing.T) {
    t.Run("creates order with pending status", func(t *testing.T) {
        store := newFakeOrderStore()
        inventory := newFakeInventory()
        inventory.addStock("sku-001", 10)
        svc := NewOrderService(store, inventory)

        order, err := svc.CreateOrder(ctx, OrderRequest{
            UserID: 1, ItemID: "sku-001", Quantity: 2,
        })

        if err != nil {
            t.Fatalf("unexpected error: %v", err)
        }
        if order.Status != StatusPending {
            t.Errorf("status: got %s, want pending", order.Status)
        }
        if order.ID == "" {
            t.Error("expected non-empty order ID")
        }
    })

    t.Run("deducts inventory on create", func(t *testing.T) {
        store := newFakeOrderStore()
        inventory := newFakeInventory()
        inventory.addStock("sku-001", 10)
        svc := NewOrderService(store, inventory)

        _, err := svc.CreateOrder(ctx, OrderRequest{
            UserID: 1, ItemID: "sku-001", Quantity: 3,
        })
        if err != nil {
            t.Fatalf("unexpected error: %v", err)
        }

        remaining := inventory.getStock("sku-001")
        if remaining != 7 {
            t.Errorf("stock after create: got %d, want 7", remaining)
        }
    })

    t.Run("rejects order when stock insufficient", func(t *testing.T) {
        store := newFakeOrderStore()
        inventory := newFakeInventory()
        inventory.addStock("sku-001", 1) // only 1 in stock
        svc := NewOrderService(store, inventory)

        _, err := svc.CreateOrder(ctx, OrderRequest{
            UserID: 1, ItemID: "sku-001", Quantity: 2,
        })
        if err == nil {
            t.Fatal("expected error for insufficient stock, got nil")
        }
        if !errors.Is(err, ErrInsufficientStock) {
            t.Errorf("expected ErrInsufficientStock, got %T: %v", err, err)
        }
    })
}
```

Now if you refactor the internal implementation — change how inventory is reserved, add a caching layer, change the database schema — these tests don't break. They test what `CreateOrder` promises, not how it works.

**Layer your tests by scope.** A healthy test pyramid has many unit tests, fewer integration tests, and few end-to-end tests. The rule of thumb:

```go
// Unit tests: fast, no external dependencies, test one thing
// Lives in the same package, uses fakes
func TestParseAmount(t *testing.T) { ... }

// Integration tests: real external deps, test multiple layers together
// Gated behind environment variable or build tag
func TestCreateOrder_Integration(t *testing.T) { ... }

// End-to-end tests: the full stack, real HTTP, real database
// Separate test binary or package, run in dedicated CI stage
func TestOrderFlow_E2E(t *testing.T) { ... }
```

**Organize tests to match the code structure.** Tests live next to the code they test. `order_service.go` → `order_service_test.go`. Integration tests that span multiple packages get their own `_test` directory or file with a build tag.

## In The Wild

The test helper pattern is the foundation of maintainable test architecture. Every project I've worked on for more than six months develops a set of test helpers that:

1. Create clean fakes for all the major interfaces
2. Wire up the service or handler under test
3. Provide assertion helpers for the domain's common patterns

```go
// testhelpers_test.go (in the service package)
func newOrderServiceForTest(t *testing.T, opts ...testOpt) (*OrderService, *testDeps) {
    t.Helper()
    deps := &testDeps{
        store:     newFakeOrderStore(),
        inventory: newFakeInventory(),
        events:    newFakeEventPublisher(),
    }
    for _, opt := range opts {
        opt(deps)
    }
    svc := NewOrderService(deps.store, deps.inventory, deps.events)
    return svc, deps
}

type testOpt func(*testDeps)

func withInitialStock(itemID string, qty int) testOpt {
    return func(d *testDeps) {
        d.inventory.addStock(itemID, qty)
    }
}

// Now tests are readable and concise
func TestCreateOrder_SufficientStock(t *testing.T) {
    svc, deps := newOrderServiceForTest(t, withInitialStock("sku-001", 10))

    order, err := svc.CreateOrder(ctx, OrderRequest{UserID: 1, ItemID: "sku-001", Quantity: 2})
    if err != nil {
        t.Fatal(err)
    }

    if deps.events.countOf("order.created") != 1 {
        t.Error("expected one order.created event")
    }
    if deps.inventory.getStock("sku-001") != 8 {
        t.Errorf("stock: got %d, want 8", deps.inventory.getStock("sku-001"))
    }
    _ = order
}
```

The test reads like documentation. The setup is one line. The assertions are focused on the contract, not the implementation.

## The Gotchas

**Helper functions that hide too much.** A test helper that does setup, exercise, and assertion in one call makes failures impossible to diagnose. Keep helpers focused on setup. Leave exercise and assertion in the test itself.

**Test packages that grow to be larger than the code they test.** When your test package has ten helper files and a thousand lines of setup, the tests themselves are a codebase that needs maintaining. Factor shared helpers into a testhelpers package that can be reused across packages — but don't let helpers become a second application.

**Not running the full test suite before merging.** Test architecture only pays off if the tests actually run. Unit tests run fast — there's no excuse not to run them locally. Integration tests can be slower, but they must run in CI on every merge. A test that never runs might as well not exist.

**Changing the interface and not fixing the tests.** When you change a public interface, some tests will break. That's expected — they're testing that interface. But if your refactor doesn't change the public contract, and tests still break, that's a signal your tests were testing the wrong thing. Use it as feedback.

## Key Takeaway

Tests that survive refactoring share one quality: they test what a function *promises*, not *how* it keeps that promise. Build your test architecture around behaviours and contracts — subtests for each behaviour, fakes instead of mocks, helpers for setup, assertions for outcomes. Invest in that architecture early. The return compounds. A test suite where every test is clearly named, tests one specific behaviour, and breaks only when the contract changes is one where every failure is actionable, every refactor is confident, and the test suite remains an asset years after the initial code was written. That's the goal.

---

[Course Index](/series/go-testing-masterclass) | [← Lesson 9](/post/go/go-testing-flaky)

---

🎓 **Course Complete!** You've finished the Go Testing Masterclass. From subtests and fuzzing through to test architecture, you now have the full toolkit to write a test suite that's fast, reliable, maintainable, and actually catches bugs. Happy shipping.
