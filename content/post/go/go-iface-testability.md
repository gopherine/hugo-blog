---
title: 'Lesson 6: Testability Without Over-Mocking — Fakes beat mocks every time'
author: Atharva Pandey
series: "Go Interfaces & Abstraction Design"
lesson: 6
keywords:
  - Go
  - golang
  - interfaces
  - testing
  - fakes
  - mocks
  - testability
tags:
  - Go tutorial
  - golang
  - interfaces
date: '2025-01-06T00:00:00.000Z'
---

Mock frameworks are a seductive solution to a real problem. You have a dependency — a database, an email service, an HTTP client — and you need your tests to run without it. A mock framework promises to solve this with generated code and assertion APIs. In my experience, what actually happens is that the tests become tightly coupled to implementation rather than behavior, they break whenever you refactor internals, and maintaining the mock library becomes a part-time job.

Fakes are different. A fake is a real implementation of an interface that is designed for testing — lightweight, in-memory, correct enough for the tests that use it. The distinction matters: a mock records calls and asserts on them, a fake actually does the work using simple in-memory data structures. Fakes produce tests that verify behavior. Mocks produce tests that verify procedure.

## The Problem

Here is what happens when you reach for a mock framework:

```go
// WRONG — over-specified mock that breaks on any refactor
func TestCreateOrder(t *testing.T) {
    ctrl := gomock.NewController(t)
    defer ctrl.Finish()

    mockStore := mocks.NewMockOrderStore(ctrl)
    mockNotifier := mocks.NewMockNotifier(ctrl)

    // Test is brittle: asserts on exact call order and arguments
    mockStore.EXPECT().
        BeginTx(gomock.Any()).
        Return(mockTx, nil).
        Times(1)
    mockStore.EXPECT().
        CreateOrder(gomock.Any(), gomock.Any()).
        Return(&Order{ID: "ord-1"}, nil).
        Times(1)
    mockStore.EXPECT().
        CommitTx(gomock.Any()).
        Return(nil).
        Times(1)
    mockNotifier.EXPECT().
        SendOrderConfirmation(gomock.Any(), "ord-1", gomock.Any()).
        Return(nil).
        Times(1)

    svc := NewOrderService(mockStore, mockNotifier)
    _, err := svc.CreateOrder(context.Background(), testOrderRequest())
    if err != nil {
        t.Fatal(err)
    }
}
```

This test asserts on every method call, including `BeginTx` and `CommitTx` — internal implementation details of `CreateOrder`. If you refactor `CreateOrder` to use a connection pool instead of explicit transactions, this test breaks even if the behavior is identical. The test is testing the implementation, not the contract.

And the mock definitions themselves are generated code — a file you cannot easily read, cannot modify manually, and must regenerate every time the interface changes.

## The Idiomatic Way

Write a fake. A fake implements the interface using maps, slices, and channels. It is honest about what it stores. It is simple enough to read without a debugger.

```go
// RIGHT — a fake that implements the interface for real, in memory
type fakeOrderStore struct {
    mu     sync.Mutex
    orders map[string]*Order
    nextID int
}

func newFakeOrderStore() *fakeOrderStore {
    return &fakeOrderStore{orders: make(map[string]*Order)}
}

func (f *fakeOrderStore) CreateOrder(_ context.Context, req CreateOrderRequest) (*Order, error) {
    f.mu.Lock()
    defer f.mu.Unlock()
    f.nextID++
    o := &Order{ID: fmt.Sprintf("ord-%d", f.nextID), Items: req.Items}
    f.orders[o.ID] = o
    return o, nil
}

func (f *fakeOrderStore) GetOrder(_ context.Context, id string) (*Order, error) {
    f.mu.Lock()
    defer f.mu.Unlock()
    o, ok := f.orders[id]
    if !ok {
        return nil, ErrOrderNotFound
    }
    return o, nil
}
```

The test now looks like this:

```go
func TestCreateOrder(t *testing.T) {
    store := newFakeOrderStore()
    notifier := &fakeNotifier{}

    svc := NewOrderService(store, notifier)
    order, err := svc.CreateOrder(context.Background(), testOrderRequest())
    if err != nil {
        t.Fatalf("unexpected error: %v", err)
    }

    // Assert on outcomes, not on how they were achieved
    if order.ID == "" {
        t.Error("expected order to have an ID")
    }

    // Verify the order was actually stored
    stored, err := store.GetOrder(context.Background(), order.ID)
    if err != nil {
        t.Fatalf("order not found in store: %v", err)
    }
    if stored.ID != order.ID {
        t.Errorf("got %s, want %s", stored.ID, order.ID)
    }
}
```

This test does not care whether `CreateOrder` uses a transaction internally. It does not care whether it calls `BeginTx` once or twice. It cares whether an order was created with an ID and whether it can be retrieved. If you refactor the internal transaction logic, this test stays green as long as the behavior remains correct.

Here is the fake notifier — notice how it records what was sent so you can still verify notifications happened, without asserting on call counts:

```go
type fakeNotifier struct {
    mu            sync.Mutex
    sentOrderIDs  []string
}

func (f *fakeNotifier) SendOrderConfirmation(_ context.Context, orderID string, _ NotificationData) error {
    f.mu.Lock()
    defer f.mu.Unlock()
    f.sentOrderIDs = append(f.sentOrderIDs, orderID)
    return nil
}

func (f *fakeNotifier) WasSent(orderID string) bool {
    f.mu.Lock()
    defer f.mu.Unlock()
    for _, id := range f.sentOrderIDs {
        if id == orderID {
            return true
        }
    }
    return false
}
```

You can write: `if !notifier.WasSent(order.ID) { t.Error("notification not sent") }`. This asserts on the outcome — a notification exists for the order — not on the mechanism.

## In The Wild

The project where fakes changed my testing approach most was a SaaS billing system. We had mocks for Stripe, for the database, and for an internal audit logger. The mock files were nearly twice as long as the implementation files. Adding a new feature required updating the interface, regenerating the mocks, and fixing every test that now had a compilation error because a new method appeared.

I rewrote the test infrastructure over two weekends. The core was a `FakePaymentGateway` that stored charges in a map and returned predictable IDs:

```go
type FakePaymentGateway struct {
    mu      sync.Mutex
    charges map[string]*Charge
    nextSeq int
    failOn  string // set to make a specific charge ID fail
}

func (f *FakePaymentGateway) Charge(ctx context.Context, req ChargeRequest) (*Charge, error) {
    f.mu.Lock()
    defer f.mu.Unlock()
    f.nextSeq++
    id := fmt.Sprintf("ch_fake_%d", f.nextSeq)
    if id == f.failOn {
        return nil, &GatewayError{Code: "card_declined"}
    }
    c := &Charge{ID: id, Amount: req.Amount, Currency: req.Currency}
    f.charges[id] = c
    return c, nil
}

func (f *FakePaymentGateway) GetCharge(ctx context.Context, id string) (*Charge, error) {
    f.mu.Lock()
    defer f.mu.Unlock()
    c, ok := f.charges[id]
    if !ok {
        return nil, ErrChargeNotFound
    }
    return c, nil
}
```

Tests became twenty lines instead of sixty. They broke when behavior changed, not when implementation changed. The fake was shared across all tests in the billing package as a `testdata` helper — one struct, used everywhere.

## The Gotchas

**Fakes can drift from real behavior.** A mock that validates argument types enforces the contract mechanically. A fake that stores things in a map can diverge from what the real implementation does under edge cases — concurrent writes, error conditions, constraint violations. Mitigate this with a shared test suite: a set of tests that run against both the real implementation and the fake to verify they behave identically at the contract level.

**Not everything needs a fake.** Simple leaf dependencies — a logger, a clock, a random ID generator — are often better handled with a function value or a tiny struct, not a full fake. Save full fakes for dependencies with multiple methods and stateful behavior.

**Fakes belong in test files or `testdata` packages.** They are not production code. Either define them in `_test.go` files (so they are not compiled into the binary) or put them in an `internal/testutil` package that is only imported by tests.

## Key Takeaway

The difference between a mock and a fake is the difference between verifying that your code made the right calls versus verifying that your code produced the right outcomes. Fakes win on readability, maintainability, and the ability to survive refactors. They require a bit more upfront investment — you have to write the in-memory implementation — but that investment pays off every time you change internal behavior without breaking the test suite. Write the interface, write the fake, write the test once. Refactor freely.

---

← [Lesson 5: Composition with Embedding](/post/go/go-iface-composition) | [Course Index](/series/go-interfaces-abstraction-design) | [Next → Lesson 7: The io.Reader/Writer Ecosystem](/post/go/go-iface-io-ecosystem)
