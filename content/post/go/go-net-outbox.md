---
title: 'Lesson 7: Outbox Pattern — Reliable events without distributed transactions'
author: Atharva Pandey
series: "Go Networking & Distributed Systems"
lesson: 7
keywords:
  - Go
  - golang
  - outbox pattern
  - distributed transactions
  - events
  - message queue
  - at-least-once
  - reliability
tags:
  - Go tutorial
  - golang
  - networking
date: '2025-03-20T00:00:00.000Z'
---

Here's a bug that's bitten almost every distributed system I've worked on: a service saves a record to the database and then publishes an event to Kafka. The record is saved. The process crashes before the publish. Now the database says the order exists, but the fulfillment service never heard about it. The order sits in limbo forever.

The naive fix — wrapping both operations in a transaction — doesn't work because Kafka isn't a participant in your database transaction. You can't two-phase commit across Postgres and Kafka without a distributed transaction coordinator, and nobody wants to operate one of those in production.

The outbox pattern solves this elegantly: write the event to a table in the same database transaction as the business record, then relay events from that table to Kafka in a separate process. Atomic database writes; no distributed transaction needed.

## The Problem

The dual-write problem is any situation where you need two separate systems to both receive an update:

```go
// WRONG — two writes with no atomicity guarantee
func createOrder(ctx context.Context, order Order) error {
    // Step 1: save to database
    if err := db.InsertOrder(ctx, order); err != nil {
        return err
    }

    // *** CRASH HERE ***
    // The order is in the DB but the event was never published.
    // Fulfillment service never processes it.

    // Step 2: publish event to Kafka
    event := OrderCreatedEvent{OrderID: order.ID, Items: order.Items}
    data, _ := json.Marshal(event)
    if err := kafkaWriter.WriteMessages(ctx, kafka.Message{Value: data}); err != nil {
        // The database write already happened — we can't roll it back.
        // We could retry the publish, but what if Kafka is down for an hour?
        return err
    }
    return nil
}
```

The process can crash at any point between the two writes. Kafka can be temporarily unavailable. The database transaction can succeed while the Kafka write fails. There's no way to make these two writes atomic without the outbox pattern.

## The Idiomatic Way

The outbox pattern has three parts: an outbox table, transactional writes to the outbox alongside business records, and a relay process that moves outbox rows to the message broker.

First, the schema (in SQL):

```sql
CREATE TABLE outbox (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic       TEXT NOT NULL,
    key         TEXT,
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at TIMESTAMPTZ
);

-- Index for the relay process — unpublished events, oldest first.
CREATE INDEX outbox_unpublished ON outbox (created_at)
    WHERE published_at IS NULL;
```

The write path: save the business record and the outbox event in the same transaction:

```go
func createOrder(ctx context.Context, tx *sql.Tx, order Order) error {
    // Insert the business record.
    if _, err := tx.ExecContext(ctx,
        `INSERT INTO orders (id, user_id, total) VALUES ($1, $2, $3)`,
        order.ID, order.UserID, order.Total,
    ); err != nil {
        return fmt.Errorf("insert order: %w", err)
    }

    // Insert the outbox event in the SAME transaction.
    // If the transaction rolls back, the event is also rolled back.
    payload, err := json.Marshal(OrderCreatedEvent{
        OrderID: order.ID,
        UserID:  order.UserID,
        Items:   order.Items,
        Total:   order.Total,
    })
    if err != nil {
        return fmt.Errorf("marshal event: %w", err)
    }

    if _, err := tx.ExecContext(ctx,
        `INSERT INTO outbox (topic, key, payload) VALUES ($1, $2, $3)`,
        "orders.created", order.ID, payload,
    ); err != nil {
        return fmt.Errorf("insert outbox: %w", err)
    }

    return nil // caller commits the transaction
}
```

Now the relay process — it polls the outbox and publishes events to Kafka:

```go
func runRelay(ctx context.Context, db *sql.DB, writer *kafka.Writer) {
    ticker := time.NewTicker(500 * time.Millisecond)
    defer ticker.Stop()

    for {
        select {
        case <-ctx.Done():
            return
        case <-ticker.C:
            if err := relayBatch(ctx, db, writer); err != nil {
                log.Printf("relay batch: %v", err)
            }
        }
    }
}

func relayBatch(ctx context.Context, db *sql.DB, writer *kafka.Writer) error {
    // SELECT FOR UPDATE SKIP LOCKED — allows multiple relay instances
    // without duplicate publishing. Each row is locked by exactly one reader.
    rows, err := db.QueryContext(ctx, `
        SELECT id, topic, key, payload
        FROM outbox
        WHERE published_at IS NULL
        ORDER BY created_at
        LIMIT 100
        FOR UPDATE SKIP LOCKED
    `)
    if err != nil {
        return fmt.Errorf("query: %w", err)
    }
    defer rows.Close()

    var msgs []kafka.Message
    var ids []string

    for rows.Next() {
        var id, topic, key string
        var payload []byte
        if err := rows.Scan(&id, &topic, &key, &payload); err != nil {
            return err
        }
        msgs = append(msgs, kafka.Message{
            Topic: topic,
            Key:   []byte(key),
            Value: payload,
        })
        ids = append(ids, id)
    }

    if len(msgs) == 0 {
        return nil
    }

    // Publish to Kafka. This is at-least-once — if we crash after publishing
    // but before marking as published, the events will be re-published.
    if err := writer.WriteMessages(ctx, msgs...); err != nil {
        return fmt.Errorf("write messages: %w", err)
    }

    // Mark as published. Use a placeholder for the IN clause.
    placeholders := make([]string, len(ids))
    args := make([]any, len(ids))
    for i, id := range ids {
        placeholders[i] = fmt.Sprintf("$%d", i+1)
        args[i] = id
    }
    _, err = db.ExecContext(ctx,
        `UPDATE outbox SET published_at = NOW() WHERE id IN (`+strings.Join(placeholders, ",")+`)`,
        args...,
    )
    return err
}
```

The `FOR UPDATE SKIP LOCKED` is the key to safe concurrent relay instances. Multiple relay processes can run simultaneously — each one locks different rows, skipping rows locked by other instances. This provides both parallelism and exactly-once relay semantics within the database layer.

## In The Wild

The relay process gives you at-least-once delivery — if the process crashes after publishing but before marking the rows as published, those events will be published again on the next relay run. Your consumers need to be idempotent. The standard approach is to include the outbox row's UUID as a Kafka message key or header, and deduplicate on the consumer side:

```go
// Consumer-side deduplication using Redis SET NX
func processWithDedup(ctx context.Context, msg kafka.Message, rdb *redis.Client) error {
    eventID := string(msg.Headers[0].Value) // outbox UUID in header
    dedupKey := "processed:" + eventID

    // NX = only set if not exists. Returns 0 if key already exists.
    set, err := rdb.SetNX(ctx, dedupKey, "1", 24*time.Hour).Result()
    if err != nil {
        return err
    }
    if !set {
        return nil // already processed — skip
    }

    return handleEvent(ctx, msg.Value)
}
```

For high-volume services, the outbox table can grow large. Add a cleanup job that deletes rows where `published_at` is older than 7 days. Keep a day or two of history for debugging — being able to query "what events did we publish for order X?" is invaluable.

## The Gotchas

**Polling latency.** The relay process polls on a timer, which means events have up to [poll interval] latency before they're published. 500ms is usually fine; for lower latency, use `LISTEN/NOTIFY` in PostgreSQL to wake up the relay when new rows are inserted, rather than polling on a fixed interval.

**Ordering guarantees.** The outbox preserves insertion order within a single transaction, but the relay processes rows in `created_at` order with a 100-row batch. For strict ordering per entity (all events for order X in order), use the entity ID as the Kafka partition key and ensure the relay batches events per partition key.

**Schema migrations.** The outbox table evolves alongside your event schemas. Use a `schema_version` column in your payload envelope. Consumers need to handle multiple versions.

## Key Takeaway

The outbox pattern decouples database consistency from message broker availability. The database write is atomic — either both the business record and the outbox event are committed, or neither is. The message broker gets the event eventually, from the relay process. The result is reliable event publishing without distributed transactions or two-phase commit.

---

**Previous:** [Lesson 6: Eventual Consistency](/post/go/go-net-eventual-consistency/)
**Next:** [Lesson 8: gRPC Basics and Streaming — Protobuf on the wire, types in your code](/post/go/go-net-grpc/)
