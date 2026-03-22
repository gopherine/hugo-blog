---
title: "Lesson 2: Projections and Read Models — Build any view from your event stream"
author: Atharva Pandey
keywords:
  - event sourcing
  - projections
  - read models
  - CQRS
  - event-driven
  - eventually consistent
tags:
  - fundamentals
  - architecture
  - event sourcing
series: "Event Sourcing Deep Dive"
lesson: 2
date: '2024-09-13T00:00:00.000Z'
---

After I shipped the event-sourced account system, the first question from the product team was: "Can we have a page that shows all accounts that have been dormant for more than 90 days?" In a traditional system, this is a query: `SELECT * FROM accounts WHERE last_activity < NOW() - INTERVAL '90 days'`. In event sourcing, there's no `last_activity` column — there's an event stream. My first instinct was to query the event store directly, find the latest event per stream, and filter. It worked. Then they asked for the top 100 accounts by balance. Then active accounts by geography. Then a real-time dashboard with all of the above simultaneously. Querying the event store for each of these is either very slow or very complex. The answer is projections — pre-computed read models built from your event streams.

## How It Works

A projection is a process that reads events from a stream and builds a queryable representation — a read model — optimized for a specific query pattern. The projection subscribes to the event stream, processes events in order, and maintains its own data store (usually a relational table, a document store, or an in-memory structure) that can be queried efficiently.

The key properties of projections:

**Idempotent**: Processing the same event twice should produce the same result. This enables at-least-once delivery semantics without corruption.

**Checkpointed**: The projection tracks the last event position it processed. If it restarts, it resumes from the checkpoint rather than replaying everything from the beginning.

**Rebuildable**: If the read model schema changes, or if data is corrupted, you reset the checkpoint to zero and let the projection rebuild from the full event history.

```
Event Store                  Projection                  Read Model
  account-42    ─────────▶  reads MoneyDeposited   ─▶  accounts_summary
  account-57    ─────────▶  reads MoneyWithdrawn   ─▶    (PostgreSQL)
  account-91    ─────────▶  reads AccountFrozen    ─▶
  ...                       checkpoints position
                            at every N events
```

## The Algorithm

Projections come in three flavors depending on their scope:

**Stream projection**: Subscribes to a single stream. Typically used to rebuild the current state of one aggregate — this is how you load an aggregate from the event store without replaying from scratch (the projection is effectively a snapshotting mechanism).

**Category projection**: Subscribes to all streams in a category. `$ce-account` in EventStoreDB gives you all events from all `account-*` streams. This is how you build read models that aggregate across all instances of an entity type.

**Global projection**: Subscribes to the global event log (`$all`). Used for cross-aggregate read models, analytics, audit logs, and integration events.

Here's a concrete category projection implementation:

```go
type AccountSummaryProjection struct {
    db         *sql.DB
    checkpoint int64
}

func (p *AccountSummaryProjection) Run(ctx context.Context, store EventStore) error {
    // Load the last checkpoint
    p.checkpoint = p.loadCheckpoint()

    // Subscribe from the last checkpoint
    ch, err := store.SubscribeToStream(ctx, "$ce-account", p.checkpoint)
    if err != nil {
        return err
    }

    batch := 0
    for event := range ch {
        if err := p.handleEvent(ctx, event); err != nil {
            return err
        }

        batch++
        if batch >= 100 {
            p.saveCheckpoint(event.GlobalPos)
            batch = 0
        }
    }
    return nil
}

func (p *AccountSummaryProjection) handleEvent(ctx context.Context, event RecordedEvent) error {
    switch event.Type {
    case "AccountOpened":
        var data AccountOpenedData
        json.Unmarshal(event.Data, &data)
        _, err := p.db.ExecContext(ctx, `
            INSERT INTO accounts_summary (id, owner_name, balance, status, opened_at, last_activity)
            VALUES ($1, $2, $3, 'active', $4, $4)
            ON CONFLICT (id) DO NOTHING
        `, extractAccountID(event.StreamID), data.OwnerName, data.InitialBalance, event.Timestamp)
        return err

    case "MoneyDeposited":
        var data MoneyDepositedData
        json.Unmarshal(event.Data, &data)
        _, err := p.db.ExecContext(ctx, `
            UPDATE accounts_summary
            SET balance = balance + $2, last_activity = $3
            WHERE id = $1
        `, extractAccountID(event.StreamID), data.Amount, event.Timestamp)
        return err

    case "MoneyWithdrawn":
        var data MoneyWithdrawnData
        json.Unmarshal(event.Data, &data)
        _, err := p.db.ExecContext(ctx, `
            UPDATE accounts_summary
            SET balance = balance - $2, last_activity = $3
            WHERE id = $1
        `, extractAccountID(event.StreamID), data.Amount, event.Timestamp)
        return err

    case "AccountFrozen":
        _, err := p.db.ExecContext(ctx, `
            UPDATE accounts_summary SET status = 'frozen' WHERE id = $1
        `, extractAccountID(event.StreamID))
        return err
    }
    return nil // ignore unknown event types
}
```

Now the dormant account query is trivial:

```sql
SELECT id, owner_name, balance, last_activity
FROM accounts_summary
WHERE last_activity < NOW() - INTERVAL '90 days'
  AND status = 'active'
ORDER BY last_activity ASC;
```

And you can build arbitrarily many additional read models from the same event stream: a `dormant_accounts` table, a `balance_by_geography` materialized view, a real-time WebSocket feed for the admin dashboard. Each is a separate projection consuming the same events.

## Production Example

One of the most powerful uses of projections is **temporal queries** — the ability to ask "what was the state of the system at time T?" This is impossible with traditional overwrite-in-place storage without extensive audit machinery. With event sourcing, it's a projection parameterized by time.

In the accounting system, we got a regulatory requirement: given any account ID, show the complete state of that account as it would have appeared on December 31st, 2023 for year-end reporting. The implementation:

```go
func buildAccountStateAtTime(events []RecordedEvent, asOf time.Time) AccountState {
    state := AccountState{}
    for _, event := range events {
        if event.Timestamp.After(asOf) {
            break  // stop replaying once we've passed the target time
        }
        applyEvent(&state, event)
    }
    return state
}
```

No special query, no separate audit table to maintain, no reconciliation. Just replay the event stream up to the target timestamp. The event store is the machine that can answer any question about past state, as long as it was captured as events.

Another powerful pattern is **multiple read models for the same data**. For our payment system:

- `accounts_summary` — flat table for the admin portal (PostgreSQL)
- `account_search` — Elasticsearch index for full-text search across account metadata
- `dormancy_alerts` — Redis sorted set for real-time dormancy monitoring
- `compliance_ledger` — append-only table in a separate compliance database with stricter access control

All four are built from the same event stream. If requirements change, we add a new projection. If a read model gets corrupted, we rebuild from events. The event store is write-once, and read models are disposable.

## The Tradeoffs

**Eventual consistency**: Read models are always slightly behind the event stream. When you write an event and immediately query the read model, the read model may not yet reflect the event. This is eventual consistency, and it's inherent to the pattern. Strategies: (a) return the expected new state to the client directly from the command handler without querying the read model, (b) poll the read model until it reaches the expected version, (c) accept eventual consistency and design the UI accordingly.

**Projection fan-out**: If you have many projections all subscribing to the same high-volume event stream, each projection consumes resources. A stream processing 10,000 events per second with 20 projections means 200,000 event processing operations per second. Design projections to be cheap (simple SQL upserts), and consider filtering at the subscription level (subscribe to specific event types only).

**Schema migration of read models**: Changing a read model's schema is easy — drop and rebuild. But during the rebuild, the read model is stale (or unavailable). For zero-downtime migrations, build the new read model in parallel under a different name, run both until the new one is caught up, then switch over. This is the blue-green deployment pattern applied to projections.

**Poison events**: If a single event is malformed and causes the projection to crash repeatedly, it becomes a "poison pill" that blocks the entire projection. Build in dead-letter handling: if an event fails to process N times, log it, skip it, and continue. Alert on skipped events — they represent data quality issues that need investigation.

## Key Takeaway

Projections are the read side of event sourcing. They consume your immutable event stream and build queryable read models optimized for specific access patterns. The three essential properties — idempotent, checkpointed, and rebuildable — make projections resilient and flexible. The ability to build any read model from the same event history means you can answer new business questions (dormancy queries, temporal state, compliance reports) without migrating data or changing your write model. The tradeoff is eventual consistency: read models lag behind the event stream. Design your application and UI with that lag in mind, and you'll find event sourcing's flexibility far outweighs the consistency complexity.

---

**Previous:** [Lesson 1: Event Store Design — Append-only logs that are your source of truth](/post/fundamentals/es-event-store/)
**Next:** [Lesson 3: CQRS + Event Sourcing Together — When the combination makes sense](/post/fundamentals/es-cqrs-together/)
