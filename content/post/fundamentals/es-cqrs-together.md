---
title: "Lesson 3: CQRS + Event Sourcing Together — When the combination makes sense"
author: Atharva Pandey
keywords:
  - CQRS
  - event sourcing
  - command query responsibility segregation
  - domain events
  - aggregate
  - software architecture
tags:
  - fundamentals
  - architecture
  - event sourcing
series: "Event Sourcing Deep Dive"
lesson: 3
date: '2024-11-24T00:00:00.000Z'
---

CQRS and event sourcing are often discussed together, presented as a single package, and that conflation is responsible for a lot of unnecessary complexity in codebases that should have stayed simple. I've seen teams adopt both because they read a blog post, without being clear on why they needed either. I've also seen teams who should have used both and instead built elaborate workarounds that were essentially CQRS and event sourcing with worse ergonomics. The question isn't "should I use CQRS and event sourcing?" — it's "do I have the specific problems these patterns solve?"

## How It Works

CQRS separates write operations (commands) from read operations (queries). You have a command side — where all state mutations happen — and a query side — where all reads happen. These sides can use different models, different databases, different scaling strategies.

Event sourcing says: store state changes as an immutable sequence of events, not as updated rows. Current state is derived from events by replaying them.

The two patterns are independently valuable and independently deployable. But they compose particularly well:

- Event sourcing provides the command side's write model: events are appended to streams when commands succeed
- Projections (which we covered last lesson) consume those event streams to build the query side's read models
- CQRS provides the architectural frame that keeps the two sides clean and separated

```
┌─────────────────────────────────────────────────────────────────────┐
│                    CQRS + Event Sourcing                            │
│                                                                     │
│  Command Side                        Query Side                     │
│  ─────────────                       ──────────                     │
│  Command Handler                     Query Handler                  │
│       │                                   │                        │
│       ▼                                   ▼                        │
│  Domain Aggregate  ─── events ──▶  Projection/Read Model           │
│       │                                   │                        │
│       ▼                                   ▼                        │
│  Event Store                         Read Database                  │
│  (append-only)                       (PostgreSQL, Redis,            │
│                                       Elasticsearch, etc.)          │
└─────────────────────────────────────────────────────────────────────┘
```

## The Algorithm

The command flow in CQRS + event sourcing follows these steps:

1. A command arrives (e.g., `TransferMoney { from: 42, to: 57, amount: 200 }`)
2. The command handler loads the aggregate by reading its event stream from the event store
3. The aggregate validates the command against its current state (rebuilt by replaying events)
4. If valid, the aggregate produces new domain events
5. The command handler appends those events to the event store with optimistic concurrency
6. The command handler returns success (not the new state — just confirmation the command was accepted)

The query flow is entirely independent:

1. A query arrives (e.g., `GetAccountBalance { id: 42 }`)
2. The query handler reads from the denormalized read model (a SQL table, Redis key, etc.)
3. Returns the result directly — no domain logic, no event replaying

Here's a concrete implementation:

```go
// ---- COMMAND SIDE ----

type TransferMoneyCommand struct {
    FromAccountID string
    ToAccountID   string
    Amount        int
    Reference     string
}

type TransferMoneyHandler struct {
    store EventStore
}

func (h *TransferMoneyHandler) Handle(ctx context.Context, cmd TransferMoneyCommand) error {
    // Load source account aggregate
    fromEvents, err := h.store.ReadStream(ctx, "account-"+cmd.FromAccountID, 0)
    if err != nil {
        return err
    }
    from := rebuildAccount(fromEvents)

    // Load destination account aggregate
    toEvents, err := h.store.ReadStream(ctx, "account-"+cmd.ToAccountID, 0)
    if err != nil {
        return err
    }
    to := rebuildAccount(toEvents)

    // Business logic validation
    if from.Balance < cmd.Amount {
        return ErrInsufficientFunds
    }
    if from.Status != "active" || to.Status != "active" {
        return ErrAccountNotActive
    }

    // Produce events
    withdrawnEvent := Event{
        Type: "MoneyWithdrawn",
        Data: mustMarshal(MoneyWithdrawnData{Amount: cmd.Amount, Reference: cmd.Reference, TransferTo: cmd.ToAccountID}),
    }
    depositedEvent := Event{
        Type: "MoneyDeposited",
        Data: mustMarshal(MoneyDepositedData{Amount: cmd.Amount, Reference: cmd.Reference, TransferFrom: cmd.FromAccountID}),
    }

    // Append to both streams (two separate appends — see tradeoffs for atomicity discussion)
    fromVersion := int64(len(fromEvents) - 1)
    if _, err := h.store.AppendToStream(ctx, "account-"+cmd.FromAccountID, fromVersion, []Event{withdrawnEvent}); err != nil {
        return err
    }

    toVersion := int64(len(toEvents) - 1)
    if _, err := h.store.AppendToStream(ctx, "account-"+cmd.ToAccountID, toVersion, []Event{depositedEvent}); err != nil {
        // Compensating event to reverse the withdrawal
        h.store.AppendToStream(ctx, "account-"+cmd.FromAccountID, fromVersion+1, []Event{
            {Type: "MoneyDepositedAsReversal", Data: mustMarshal(MoneyDepositedData{Amount: cmd.Amount, Reference: "REVERSAL-" + cmd.Reference})},
        })
        return err
    }

    return nil
}

// ---- QUERY SIDE ----

type GetAccountBalanceQuery struct {
    AccountID string
}

type AccountBalanceResult struct {
    AccountID   string
    Balance     int
    Status      string
    LastUpdated time.Time
}

type GetAccountBalanceHandler struct {
    db *sql.DB
}

func (h *GetAccountBalanceHandler) Handle(ctx context.Context, q GetAccountBalanceQuery) (AccountBalanceResult, error) {
    var result AccountBalanceResult
    err := h.db.QueryRowContext(ctx, `
        SELECT id, balance, status, last_activity
        FROM accounts_summary
        WHERE id = $1
    `, q.AccountID).Scan(&result.AccountID, &result.Balance, &result.Status, &result.LastUpdated)
    return result, err
}
```

The query handler reads from `accounts_summary` — the denormalized read model maintained by the projection from the previous lesson. It has no knowledge of the event store.

## Production Example

The clearest signal that a domain benefits from CQRS + event sourcing is the combination of these characteristics: complex write-side logic with many validations, diverse read-side requirements that don't fit a single normalized model, audit/compliance requirements, and the need to answer historical questions about state.

An order management system for an e-commerce platform is the canonical example. On the write side: an `Order` aggregate that goes through states (created → payment-pending → payment-confirmed → fulfillment-in-progress → shipped → delivered), with complex validation rules at each transition. Multiple parties interact with the order (payment service, warehouse, shipping partner), each generating events. On the read side, you have fundamentally different query shapes:

- Customer portal: order status, tracking info, summary
- Warehouse dashboard: orders pending fulfillment, grouped by warehouse
- Finance dashboard: orders by revenue, refunds, payment method
- Customer support: full order history with timeline, every state change
- Analytics: cohort analysis, conversion rates, average delivery time

With a single normalized orders table, you're constantly fighting the impedance mismatch between the write model (complex state machine) and the read models (many denormalized shapes). With CQRS + event sourcing:

- Write model: `Order` aggregate with event-sourced state
- Read models: five separate projections, each optimized for its consumer
- Complete audit trail: every state transition is an event, customer support can show the full history

The compliance read model — which needs to be immutable for regulatory reasons — is just another projection that appends to a separate append-only audit table and never updates or deletes rows.

## The Tradeoffs

**Atomic transactions across aggregates**: Event stores typically don't support multi-stream atomic transactions. In the transfer example above, if the deposit to the destination account fails after the withdrawal succeeds, you need a compensating event. This is the **saga pattern**: a sequence of local transactions where each step has a compensating transaction if a later step fails. For money transfers, compensating events are natural (reverse the withdrawal). For other domains, designing compensations requires careful thought.

**When NOT to use this combination**: CQRS + event sourcing adds real complexity. You need to understand eventual consistency, manage projection lag, handle compensating transactions, think about event schema evolution, and operate an event store. For a simple CRUD application — a blog, a settings panel, a user profile page — this complexity brings zero benefit. Use it when: (a) the domain has genuinely complex business rules that benefit from the aggregate/command model, (b) audit requirements make event storage valuable, (c) you have multiple divergent read models that fight the write model.

**Testing**: The event-sourced command side is actually easier to test than traditional systems. Given a sequence of prior events (initial state) and a command, assert the resulting new events. No database mocking required — just pure function-style tests:

```go
func TestTransfer_InsufficientFunds(t *testing.T) {
    // Given: an account with balance 100
    givenEvents := []Event{
        {Type: "AccountOpened", Data: mustMarshal(AccountOpenedData{InitialBalance: 100})},
    }
    account := rebuildAccount(givenEvents)

    // When: we try to transfer 200
    cmd := TransferMoneyCommand{Amount: 200}

    // Then: command is rejected
    err := account.ValidateTransfer(cmd)
    assert.ErrorIs(t, err, ErrInsufficientFunds)
}
```

**The read model is disposable**: This is a feature, not a limitation. When a product requirement changes and you need a new column in your read model, you add it to the projection logic and rebuild. The event store is permanent; read models are ephemeral cached views of it.

## Key Takeaway

CQRS and event sourcing are separately valuable patterns that compose naturally. CQRS provides the separation of write concerns from read concerns. Event sourcing provides an immutable, auditable write model and the ability to project that history into any read model shape you need. Together they handle the hardest problems in complex domain systems: divergent read/write models, audit requirements, temporal queries, and multiple consumers needing different data shapes. They are not a default choice — apply them when you have complex write-side business logic, multiple heterogeneous read models, and audit/compliance requirements. The complexity cost is real: eventual consistency, compensating transactions, projection management, and event schema evolution are non-trivial. The domains where they shine are complex enough that the alternative — fighting a single overloaded relational model — costs more in the long run.

---

**Previous:** [Lesson 2: Projections and Read Models — Build any view from your event stream](/post/fundamentals/es-projections/)

---

🎓 **Course Complete!** You've finished "Event Sourcing Deep Dive." From designing an event store with append-only semantics and optimistic concurrency, through building flexible projections and read models, to combining CQRS with event sourcing for complex domains — you now have a complete mental model of the pattern, when to reach for it, and how to build it in production.
