---
title: 'Lesson 5: Saga Pattern — Distributed transactions without two-phase commit'
author: Atharva Pandey
series: "Go Microservices Patterns"
lesson: 5
keywords:
  - Go
  - golang
  - microservices
  - saga pattern
  - distributed transactions
  - eventual consistency
  - compensating transactions
tags:
  - Go tutorial
  - golang
  - microservices
date: '2025-04-18T00:00:00.000Z'
---

Two-phase commit is theoretically elegant and operationally painful. I've seen it cause system-wide deadlocks during network partitions, coordinator failures that required manual database recovery, and deployment coupling so tight that every service had to be upgraded in lockstep. The saga pattern is the alternative — it trades atomicity for availability, compensates for failures with explicit rollback actions, and keeps each service's transactions entirely local. It's the pattern I reach for whenever I need "all of this must succeed together" across more than one database.

## The Problem

When a business operation spans multiple services, there's no shared transaction to wrap it all in.

```go
// WRONG — trying to use a single transaction across service boundaries
func (s *CheckoutService) Process(ctx context.Context, req CheckoutRequest) error {
    tx, err := s.db.BeginTx(ctx, nil) // Only covers THIS service's database
    if err != nil {
        return err
    }
    defer tx.Rollback()

    // These are remote calls — they have their own transactions, not this one
    if err := s.inventory.Reserve(ctx, req.Items); err != nil { // DB A
        return err // inventory was changed, but nothing else was
    }
    if err := s.payment.Charge(ctx, req.UserID, req.Total); err != nil { // DB B
        // inventory is now reserved, payment failed
        // tx.Rollback() does NOTHING for inventory or payment
        return err
    }
    if err := s.orders.Create(ctx, req); err != nil { // DB C
        // inventory reserved, payment charged, order not created
        return err
    }

    return tx.Commit() // This only commits THIS service's data
}
```

The transaction only covers the local database. Every remote call is its own transaction. If payment succeeds and order creation fails, you've charged the customer but created no order.

## The Idiomatic Way

The choreography-based saga lets each service react to events and emit compensating events if something goes wrong. The orchestration-based saga has a central coordinator that tracks state and issues compensating commands explicitly.

**Orchestration-based saga — clearer failure handling:**

```go
// checkout-service/internal/saga/checkout.go
package saga

type CheckoutSaga struct {
    inventory InventoryService
    payment   PaymentService
    orders    OrderService
    store     SagaStore // persists saga state for recovery
}

type CheckoutState struct {
    ID            string
    Request       CheckoutRequest
    ReservationID string
    ChargeID      string
    OrderID       string
    Status        SagaStatus // Pending, Compensating, Completed, Failed
    Step          SagaStep
}

func (s *CheckoutSaga) Execute(ctx context.Context, req CheckoutRequest) (*Order, error) {
    state := &CheckoutState{
        ID:      uuid.New().String(),
        Request: req,
        Status:  SagaPending,
    }
    if err := s.store.Save(ctx, state); err != nil {
        return nil, fmt.Errorf("persist saga: %w", err)
    }

    // Step 1: Reserve inventory
    reservation, err := s.inventory.Reserve(ctx, req.Items)
    if err != nil {
        state.Status = SagaFailed
        s.store.Save(ctx, state)
        return nil, fmt.Errorf("reserve inventory: %w", err)
    }
    state.ReservationID = reservation.ID
    state.Step = StepInventoryReserved
    s.store.Save(ctx, state)

    // Step 2: Charge payment
    charge, err := s.payment.Charge(ctx, req.UserID, req.Total)
    if err != nil {
        // Compensate: release the inventory reservation
        s.compensate(ctx, state)
        return nil, fmt.Errorf("charge payment: %w", err)
    }
    state.ChargeID = charge.ID
    state.Step = StepPaymentCharged
    s.store.Save(ctx, state)

    // Step 3: Create order
    order, err := s.orders.Create(ctx, req, charge.ID)
    if err != nil {
        s.compensate(ctx, state)
        return nil, fmt.Errorf("create order: %w", err)
    }
    state.OrderID = order.ID
    state.Status = SagaCompleted
    s.store.Save(ctx, state)

    return order, nil
}

// compensate executes rollback steps in reverse order
func (s *CheckoutSaga) compensate(ctx context.Context, state *CheckoutState) {
    state.Status = SagaCompensating

    if state.ChargeID != "" {
        if err := s.payment.Refund(ctx, state.ChargeID); err != nil {
            log.Printf("compensate: refund %s failed: %v", state.ChargeID, err)
            // Record for manual reconciliation — don't panic
        }
    }

    if state.ReservationID != "" {
        if err := s.inventory.Release(ctx, state.ReservationID); err != nil {
            log.Printf("compensate: release %s failed: %v", state.ReservationID, err)
        }
    }

    state.Status = SagaFailed
    s.store.Save(ctx, state)
}
```

The saga state is persisted at every step. If the process crashes mid-saga, a background recovery worker can find in-progress sagas and either resume or compensate:

```go
// Recovery worker — runs on startup and periodically
func (s *SagaRecovery) RecoverPending(ctx context.Context) error {
    pending, err := s.store.FindByStatus(ctx, SagaPending, SagaCompensating)
    if err != nil {
        return err
    }

    for _, state := range pending {
        age := time.Since(state.UpdatedAt)
        if age < 30*time.Second {
            continue // Give the original executor time to finish
        }
        // Resume or compensate based on last completed step
        go s.saga.Resume(ctx, state)
    }
    return nil
}
```

## In The Wild

A travel booking service I worked on had to coordinate hotel reservation, flight booking, and payment in sequence. The original implementation was a nested try-catch in PHP that had race conditions and left "ghost bookings" — hotel rooms reserved but never confirmed or released — at a rate of about 0.3% of transactions. Over a high-traffic weekend that was dozens of stranded rooms.

We replaced it with an orchestration-based saga. Each step was explicitly persisted before and after. The compensation logic for each step (cancel hotel reservation, cancel flight, refund payment) was tested independently. Ghost bookings dropped to zero within the first week because compensating transactions ran reliably on step failure, and the recovery worker cleaned up any sagas that were abandoned due to process crashes.

## The Gotchas

**Compensating transactions can also fail.** Your compensation code must be idempotent and should handle its own errors gracefully — log them for manual reconciliation rather than panicking. Some failures genuinely require human intervention; the goal is to minimize them, not eliminate the possibility.

**Saga state must be durable.** If you keep saga state in memory and the process crashes, in-progress sagas are lost. Use a database (or the outbox pattern with a message broker) to persist state at every step transition.

**Choreography sagas are simpler but harder to debug.** In choreography, each service reacts to events and publishes compensating events. There's no central coordinator, so the saga's state is distributed across event logs. Tracing a failing saga requires correlating events across multiple services — possible, but harder than inspecting a single saga state record.

**Idempotency is required at every step.** The saga might retry a step (due to timeout, network failure, or crash recovery). Every operation in a saga must be idempotent — if `inventory.Reserve` is called twice with the same saga ID, it should return the same reservation ID, not create a second reservation.

## Key Takeaway

The saga pattern is the correct answer to "how do I maintain data consistency across multiple services without two-phase commit?" Each step is a local transaction. Failures are handled by explicit compensating transactions in reverse order. Persist saga state at every transition so recovery is possible after crashes. Make every step idempotent so retries are safe. The result is a system that's available during partial failures and self-healing — not one that deadlocks waiting for a distributed coordinator.

---

[← Lesson 4: Distributed Tracing](/post/go/go-micro-tracing) | [Course Index](/series/go-microservices-patterns) | [Next → Lesson 6: API Gateway Patterns](/post/go/go-micro-api-gateway)
