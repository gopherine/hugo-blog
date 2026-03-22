---
title: "Lesson 1: Event Store Design — Append-only logs that are your source of truth"
author: Atharva Pandey
keywords:
  - event sourcing
  - event store
  - append-only log
  - source of truth
  - event-driven architecture
  - EventStoreDB
tags:
  - fundamentals
  - architecture
  - event sourcing
series: "Event Sourcing Deep Dive"
lesson: 1
date: '2024-06-29T00:00:00.000Z'
---

My first encounter with event sourcing was a financial system that needed a complete audit trail. The business requirement was clear: every change to an account balance had to be traceable — who changed it, when, why. The first approach was an audit log table alongside the main accounts table. We'd write to both in a transaction. Within six months, the audit table was out of sync with the accounts table. Bugs in the dual-write logic, a migration that updated account records without touching the audit log, a direct database fix that bypassed the application layer. The audit log was nearly useless. The core insight I eventually arrived at: the audit log shouldn't be a secondary record — it should be the primary record. That's event sourcing.

## How It Works

In traditional state-based storage, you store the current state of an entity and update it in place. An account has a balance column; a payment overwrites that column with the new balance. History is gone unless you separately build an audit mechanism.

In event sourcing, you store the events that led to the current state, not the current state itself. An account has a stream of events: `AccountOpened`, `MoneyDeposited`, `MoneyWithdrawn`, `AccountFrozen`. The current balance is not stored — it's derived by replaying the events in order. The event stream is the source of truth. Current state is a cache.

```
Traditional:
  accounts table:
    id=42, balance=850, status=active, last_updated=2024-06-29

Event-Sourced:
  account-42 event stream:
    [1] AccountOpened      { initial_balance: 1000, opened_at: 2024-01-15 }
    [2] MoneyDeposited     { amount: 200, reference: "SALARY-JAN",   at: 2024-01-31 }
    [3] MoneyWithdrawn     { amount: 150, reference: "RENT-FEB",     at: 2024-02-01 }
    [4] MoneyDeposited     { amount: 200, reference: "SALARY-FEB",   at: 2024-02-28 }
    [5] MoneyWithdrawn     { amount: 400, reference: "HOLIDAY",      at: 2024-03-10 }

  Current balance = 1000 + 200 - 150 + 200 - 400 = 850 ✓
```

The event store has three fundamental operations: **append** (add events to a stream), **read** (read events from a stream from position N), and **subscribe** (receive new events as they're appended). That's it. No update, no delete.

## The Algorithm

An event store is an append-only log, partitioned by stream. Each stream is identified by a stream ID (e.g., `account-42`), and events in a stream are ordered by a sequence number (position). The critical invariant: once written, events are never modified or deleted.

The most important operation besides appending is **optimistic concurrency control**. When you append events to a stream, you specify the expected version — the position of the last event you read. If the stream has advanced since you read it (someone else appended an event), your append fails with a conflict. This prevents two concurrent processes from both successfully processing the same version of an aggregate.

```go
type EventStore interface {
    // Append events to a stream, with optimistic concurrency.
    // expectedVersion: the version the caller believes the stream is at.
    // Returns the new version after appending, or ErrVersionConflict.
    AppendToStream(ctx context.Context, streamID string, expectedVersion int64, events []Event) (int64, error)

    // Read events from a stream starting at fromVersion.
    ReadStream(ctx context.Context, streamID string, fromVersion int64) ([]RecordedEvent, error)

    // Subscribe to all events in a stream (or all streams) from a position.
    SubscribeToStream(ctx context.Context, streamID string, fromPosition int64) (<-chan RecordedEvent, error)
}

type Event struct {
    Type     string          // e.g., "MoneyDeposited"
    Data     json.RawMessage // event payload
    Metadata json.RawMessage // correlationId, causationId, userId, etc.
}

type RecordedEvent struct {
    Event
    StreamID  string
    Version   int64     // position within the stream
    GlobalPos int64     // global ordering across all streams
    Timestamp time.Time
}
```

A concrete append with optimistic concurrency:

```go
func (a *Account) Deposit(ctx context.Context, store EventStore, amount int, reference string) error {
    // Load current state by reading the event stream
    events, err := store.ReadStream(ctx, "account-"+a.ID, 0)
    if err != nil {
        return err
    }
    currentVersion := int64(len(events)) - 1

    // Apply business logic against current state
    if a.Status != "active" {
        return ErrAccountFrozen
    }

    // Build the new event
    event := Event{
        Type: "MoneyDeposited",
        Data: json.Marshal(MoneyDepositedData{
            Amount:    amount,
            Reference: reference,
        }),
    }

    // Append with the expected version — this fails if another process appended first
    _, err = store.AppendToStream(ctx, "account-"+a.ID, currentVersion, []Event{event})
    if errors.Is(err, ErrVersionConflict) {
        // Retry: reload state and try again
        return a.Deposit(ctx, store, amount, reference)
    }
    return err
}
```

The rebuild-from-events flow is called loading an aggregate. For a long-lived stream, replaying thousands of events on every operation is too slow. This is solved with **snapshots**: periodically store the current state alongside the event stream, and when loading, start from the most recent snapshot and replay only the events after it.

## Production Example

EventStoreDB is the purpose-built database for event sourcing. It uses an immutable, append-only log as its storage model, with streams as the primary abstraction. Each stream has persistent subscriptions that allow consumers to receive events in order and track their position (checkpoint) independently.

In a payment processing system I worked on, we used EventStoreDB with the following stream naming convention:

- `account-{accountId}` — events for a specific account aggregate
- `transaction-{txId}` — events for a specific transaction
- `$all` — the global event log (every event ever, across all streams)
- `$ce-account` — category stream: all events from streams starting with `account-`

The `$ce-account` stream is a projection that EventStoreDB builds automatically. It's how you read all account events without knowing individual account IDs — useful for building read models that aggregate across all accounts.

A persistent subscription on `$ce-account` drives a projection that maintains a denormalized accounts summary table for the admin dashboard:

```
EventStoreDB                    Projection Consumer
  $ce-account stream   ───────▶  reads events in order
  [AccountOpened]                checkpoints every 100 events
  [MoneyDeposited]               upserts to: accounts_summary table
  [MoneyWithdrawn]               (PostgreSQL read model)
  [AccountFrozen]
  ...
```

If the read model gets corrupted or the schema needs to change, we drop the table, reset the subscription checkpoint to position 0, and let the consumer rebuild the entire read model from the event history. Complete auditability, and the ability to replay events into any new read model shape we need — this is the core value proposition.

## The Tradeoffs

**Rebuild time vs snapshot frequency**: Without snapshots, loading a high-traffic aggregate (thousands of events) is slow. With snapshots, you add storage and snapshot management complexity. For most aggregates, snapshots at every 50-100 events is a reasonable starting point. For low-traffic aggregates, snapshots may not be necessary at all.

**Event schema evolution**: Events are immutable, but your event schema needs to change as the business evolves. The safe strategies: (a) add new fields as optional, never remove fields (schema evolution with backward compatibility); (b) create a new event type that supersedes the old one and write "upcasters" — functions that transform old events into new shapes when reading. The worst strategy: rename or change the semantics of an existing event type.

**Unique constraint enforcement**: In state-based systems, a `UNIQUE` constraint in the database enforces "no two accounts with the same email." In event sourcing, you can't enforce global uniqueness constraints inside a single aggregate stream. You need either a separate reservation table (create a record to "claim" the email before appending the event), or an eventually-consistent approach where you detect duplicates after the fact and compensate.

**Not everything should be event-sourced**: Event sourcing is valuable for domains with complex business rules, full audit requirements, or temporal queries ("what was the balance on March 1st?"). It's overkill for simple CRUD with no history requirements. A blog post's view count doesn't need to be event-sourced. A financial transaction does.

## Key Takeaway

An event store is an append-only log of domain events, partitioned by stream, where current state is derived by replaying events rather than stored directly. The three critical design decisions are: optimistic concurrency control (prevent lost writes), snapshot strategy (prevent slow rebuilds), and event schema evolution (handle change without breaking history). EventStoreDB is the purpose-built solution; PostgreSQL with an append-only events table is a viable simpler alternative if you're already using Postgres. The audit log problem that sent me down this path turns out to be solved by design: when the event log is your source of truth, the audit trail is not a secondary concern — it's the primary data structure.

---

**Next:** [Lesson 2: Projections and Read Models — Build any view from your event stream](/post/fundamentals/es-projections/)
