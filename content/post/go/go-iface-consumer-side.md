---
title: 'Lesson 2: Consumer-Side Interfaces — Define where you use, not where you build'
author: Atharva Pandey
series: "Go Interfaces & Abstraction Design"
lesson: 2
keywords:
  - Go
  - golang
  - interfaces
  - consumer-side interfaces
  - dependency injection
  - Go design patterns
tags:
  - Go tutorial
  - golang
  - interfaces
date: '2024-07-15T00:00:00.000Z'
---

There is a convention I spent a long time following without questioning it: put the interface in the same package as the type that implements it. If I had a `payment` package with a `Stripe` struct, I would also define `PaymentProcessor` right there. Callers would import `payment.PaymentProcessor`. It felt natural — the interface lives next to the thing it describes.

Go's designers had a different idea in mind, and it took me several refactoring sessions on a real codebase before I understood why it matters: interfaces should be defined by the package that uses them, not the package that implements them. The Go standard library does this consistently and for good reason.

## The Problem

Here is how the producer-side interface pattern usually looks in a codebase:

```go
// payment/payment.go — WRONG: interface lives with the implementation
package payment

type Processor interface {
    Charge(ctx context.Context, amount int, currency string) (string, error)
    Refund(ctx context.Context, chargeID string) error
    GetCharge(ctx context.Context, chargeID string) (*Charge, error)
}

type StripeProcessor struct {
    client *stripe.Client
    log    *slog.Logger
}

func (s *StripeProcessor) Charge(ctx context.Context, amount int, currency string) (string, error) {
    // Stripe API call
}

func (s *StripeProcessor) Refund(ctx context.Context, chargeID string) error {
    // Stripe API call
}

func (s *StripeProcessor) GetCharge(ctx context.Context, chargeID string) (*Charge, error) {
    // Stripe API call
}
```

Now your `order` package imports `payment` to get the interface:

```go
// order/handler.go — importing from producer
package order

import "myapp/payment"

type CheckoutHandler struct {
    processor payment.Processor
}
```

This creates a hard dependency from `order` to `payment`. Whenever `payment.Processor` grows — a new method is added to handle webhooks, or a `BatchCharge` method gets appended — `order` is affected even if it does not use the new method. Every other package that imports `payment.Processor` needs to update their mocks. The interface, which should be about what the consumer needs, has become about what the producer has decided to expose.

There is also a circular dependency risk. If `payment` ever needs to call something in `order` (for fraud checking, order validation, etc.), you have a cycle. You can break it with a third package, but that third package ends up importing both — and you've built a dependency graph that fights you every time it grows.

## The Idiomatic Way

The idiomatic solution is to define the interface in the `order` package, as small as it needs to be for order processing:

```go
// order/handler.go — RIGHT: interface defined where it's consumed
package order

import "context"

// charger is defined right here, for this package's needs only.
// It doesn't care about Refund or GetCharge.
type charger interface {
    Charge(ctx context.Context, amount int, currency string) (string, error)
}

type CheckoutHandler struct {
    processor charger
}

func (h *CheckoutHandler) Checkout(ctx context.Context, cart Cart) error {
    chargeID, err := h.processor.Charge(ctx, cart.Total(), cart.Currency())
    if err != nil {
        return fmt.Errorf("charging cart: %w", err)
    }
    return h.saveOrder(ctx, cart, chargeID)
}
```

The `payment` package does not need to know this interface exists. `StripeProcessor` satisfies it automatically because Go uses structural typing. If you add `BatchCharge` to `StripeProcessor` tomorrow, the `charger` interface in `order` is unaffected. If you add a refund handler in a `refund` package, it defines its own minimal interface with just `Refund`. Each consumer is protected from growth in the implementation it doesn't care about.

The narrowness pays off immediately in tests:

```go
// order/handler_test.go — fake is two lines because the interface is one method
package order

type fakeCharger struct {
    chargeID string
    err      error
}

func (f *fakeCharger) Charge(_ context.Context, _ int, _ string) (string, error) {
    return f.chargeID, f.err
}

func TestCheckout_ChargeError(t *testing.T) {
    h := &CheckoutHandler{
        processor: &fakeCharger{err: errors.New("card declined")},
    }
    err := h.Checkout(context.Background(), testCart())
    if err == nil {
        t.Fatal("expected error, got nil")
    }
}
```

No generated mocks. No mock framework. No assertion on whether `Refund` was called. The test is about behavior, not about which methods the payment processor has.

## In The Wild

The most instructive example in the Go standard library is `io.Reader` and `io.Writer`. They are defined in the `io` package, but they are used everywhere — `bufio`, `net/http`, `compress/gzip`, `encoding/json`. Each of those packages imports `io` for the interface, not for any concrete implementation. The concrete implementations — `os.File`, `bytes.Buffer`, `net.Conn` — live in completely separate packages and satisfy `io.Reader` without any explicit declaration.

In a large payment platform I worked on, we had a billing service, a subscription service, and a notifications service — all of which needed to know whether a payment had succeeded. We initially shared a `PaymentResult` type and a `PaymentEventListener` interface from a central `payments` package. That central package became a bottleneck: every team touched it, every deploy needed coordination, and it imported from packages that imported from it.

The fix was to move the interface definition to each consumer:

```go
// billing/processor.go
package billing

type paymentEventSource interface {
    SubscribeToPaymentEvents(ctx context.Context, handler func(PaymentEvent)) error
}

// subscription/renewal.go
package subscription

type paymentChecker interface {
    GetPaymentStatus(ctx context.Context, subID string) (PaymentStatus, error)
}
```

The `payments` package published a concrete event bus and status checker. Each downstream package defined exactly the slice of that it needed. The central package dependency vanished. Teams could deploy independently.

```go
// payments/events.go — concrete, no interfaces defined here
package payments

type EventBus struct { /* ... */ }

func (b *EventBus) SubscribeToPaymentEvents(ctx context.Context, handler func(PaymentEvent)) error {
    // implementation
}

func (b *EventBus) GetPaymentStatus(ctx context.Context, subID string) (PaymentStatus, error) {
    // implementation
}
// billing.paymentEventSource and subscription.paymentChecker are both satisfied.
// EventBus doesn't know about either of them.
```

## The Gotchas

**Unexported interface names are fine and often preferable.** The `charger` and `paymentEventSource` interfaces in the examples above are unexported. That is intentional. If only one package needs the interface, there is no reason to export it. Keeping it unexported signals to readers that this is an internal seam, not a public contract.

**Don't duplicate types across packages.** The interface should reference behavior, not types. If you are defining `charger` in the `order` package but it needs to return `payment.Charge`, you still import `payment` — just for the data type, not for the interface. That is a perfectly healthy dependency. The distinction is that `order` is not locked to `payment`'s interface definition.

**Beware of convenience packages that re-export everything.** Some codebases create an `interfaces` or `contracts` package that holds every interface in the application. This re-centralizes the very coupling you were trying to avoid. Interfaces belong to their consumers, not to a shared registry.

## Key Takeaway

When you define an interface in the package that implements the type, you are designing for the producer's perspective. When you define it in the package that uses it, you are designing for the consumer's perspective — and in Go, the consumer wins. Consumer-side interfaces stay narrow because they only cover what is actually needed. They stay stable because they are not affected by growth in the implementation. They make mocking trivial because there is nothing excess to implement. The convention feels backwards at first if you are coming from languages where interfaces are declared. But once you internalize it, you will find that your packages stop fighting each other and start cooperating.

---

← [Lesson 1: When NOT to Use Interfaces](/post/go/go-iface-when-not) | [Course Index](/series/go-interfaces-abstraction-design) | [Next → Lesson 3: Interface Pollution](/post/go/go-iface-pollution)
