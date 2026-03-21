---
title: 'Lesson 5: Message Queues in Go — NATS, RabbitMQ, Kafka — pick your tradeoff'
author: Atharva Pandey
series: "Go Networking & Distributed Systems"
lesson: 5
keywords:
  - Go
  - golang
  - message queue
  - NATS
  - RabbitMQ
  - Kafka
  - pub/sub
  - async
  - distributed systems
tags:
  - Go tutorial
  - golang
  - networking
date: '2024-11-20T00:00:00.000Z'
---

I've integrated all three of these systems in production Go services, and the question I get most often is: "Which one should I use?" The honest answer is that it depends on what guarantee you actually need — and most engineers pick based on familiarity or hype rather than requirements. NATS, RabbitMQ, and Kafka solve meaningfully different problems, and choosing the wrong one creates operational pain that no amount of clever application code can fix.

Let me walk through what each system is actually good at, with concrete Go code for each, and then talk about how to make the choice.

## The Problem

The naive approach to async work is a goroutine and a channel:

```go
// WORKS for in-process work, FAILS when the process crashes
jobs := make(chan Order, 1000)

go func() {
    for order := range jobs {
        if err := processOrder(order); err != nil {
            log.Println("process error:", err)
            // order is gone — no retry, no dead letter queue
        }
    }
}()

func handleCheckout(w http.ResponseWriter, r *http.Request) {
    var order Order
    json.NewDecoder(r.Body).Decode(&order)
    jobs <- order // if process crashes here, order is lost
    w.WriteHeader(http.StatusAccepted)
}
```

In-process channels work fine for tasks that don't need durability — if the process crashes and the task is lost, you don't care. For anything that matters — orders, payments, notifications, audit events — you need a durable queue.

## The Idiomatic Way

**NATS** is the right choice when you need speed and don't need durability. It's a pub/sub system that's extremely fast (sub-millisecond in LAN environments) and trivially simple to operate. Messages that arrive with no subscribers are dropped. NATS JetStream adds persistence, but plain NATS is at-most-once delivery.

```go
import "github.com/nats-io/nats.go"

// Publisher
nc, _ := nats.Connect(nats.DefaultURL)
defer nc.Drain()

data, _ := json.Marshal(event)
if err := nc.Publish("orders.created", data); err != nil {
    return fmt.Errorf("publish: %w", err)
}

// Subscriber — receives messages as they arrive, no persistence
sub, err := nc.Subscribe("orders.created", func(msg *nats.Msg) {
    var event OrderCreated
    if err := json.Unmarshal(msg.Data, &event); err != nil {
        log.Println("bad message:", err)
        return
    }
    handleOrderCreated(event)
})
if err != nil {
    return fmt.Errorf("subscribe: %w", err)
}
defer sub.Unsubscribe()
```

Use NATS for internal service communication where speed matters and you can tolerate the occasional dropped message — metrics fan-out, cache invalidation signals, non-critical notifications.

**RabbitMQ** is the right choice when you need reliable delivery with complex routing and you want at-most-once or at-least-once semantics with manual acknowledgement. It's the traditional enterprise message broker, and its routing model — exchanges, bindings, queues — is very expressive.

```go
import amqp "github.com/rabbitmq/amqp091-go"

func publish(ch *amqp.Channel, body []byte) error {
    return ch.PublishWithContext(ctx,
        "orders",      // exchange
        "order.placed", // routing key
        true,          // mandatory — fail if no queue is bound
        false,         // immediate — deprecated, don't use
        amqp.Publishing{
            DeliveryMode: amqp.Persistent, // survive broker restart
            ContentType:  "application/json",
            Body:         body,
        },
    )
}

// Consumer with manual acknowledgement.
msgs, err := ch.Consume(
    "order-processing", // queue name
    "",                 // consumer tag (auto-generated)
    false,              // autoAck — we'll ack manually
    false, false, false,
    nil,
)
for msg := range msgs {
    if err := processOrder(msg.Body); err != nil {
        // Nack with requeue=true sends it back to the queue.
        msg.Nack(false, true)
        continue
    }
    msg.Ack(false) // ack only after successful processing
}
```

The manual ack pattern is what gives you at-least-once delivery. The message stays in the queue until your consumer explicitly acknowledges it. If your process crashes mid-processing, RabbitMQ re-delivers the message to another consumer. This means your processing logic needs to be idempotent — processing the same message twice should produce the same result.

**Kafka** is the right choice when you need durable, ordered, replayable event streams at scale. Kafka is a distributed log, not a traditional queue. Messages are retained for a configurable period regardless of whether they've been consumed. Multiple consumer groups can read the same stream independently, at their own pace. It's operationally heavier than NATS or RabbitMQ but provides guarantees they can't.

```go
import "github.com/segmentio/kafka-go"

// Writer (producer)
w := &kafka.Writer{
    Addr:         kafka.TCP("localhost:9092"),
    Topic:        "orders",
    Balancer:     &kafka.LeastBytes{},
    RequiredAcks: kafka.RequireAll, // wait for all replicas
}

err := w.WriteMessages(ctx, kafka.Message{
    Key:   []byte(order.ID), // same key = same partition = ordered delivery
    Value: data,
})

// Reader (consumer) — offset is stored in Kafka, not in consumer memory
r := kafka.NewReader(kafka.ReaderConfig{
    Brokers:  []string{"localhost:9092"},
    GroupID:  "order-service",
    Topic:    "orders",
    MinBytes: 10e3,
    MaxBytes: 10e6,
})

for {
    msg, err := r.ReadMessage(ctx)
    if err != nil {
        break
    }
    processOrder(msg.Value)
    // Commit offset only after successful processing
    r.CommitMessages(ctx, msg)
}
```

The key insight with Kafka is the consumer group offset. Each consumer group tracks its own position in the log. If you deploy a new service that needs to process historical events, it starts reading from the beginning. This replay capability is invaluable for rebuilding derived state, debugging, and auditing.

## In The Wild

My decision tree looks like this:

- **Do you need < 1ms internal fan-out with acceptable message loss?** → NATS
- **Do you need reliable task queues with flexible routing and dead-letter queues?** → RabbitMQ
- **Do you need ordered event streams, replay capability, or multiple independent consumers?** → Kafka

One pattern I use across all three systems: a wrapper that adds structured context to messages — correlation IDs, trace headers, schema version. This makes debugging distributed flows dramatically easier:

```go
type Envelope struct {
    SchemaVersion int             `json:"schema_version"`
    TraceID       string          `json:"trace_id"`
    PublishedAt   time.Time       `json:"published_at"`
    Payload       json.RawMessage `json:"payload"`
}

func wrap(ctx context.Context, payload any) ([]byte, error) {
    data, err := json.Marshal(payload)
    if err != nil {
        return nil, err
    }
    env := Envelope{
        SchemaVersion: 1,
        TraceID:       trace.SpanFromContext(ctx).SpanContext().TraceID().String(),
        PublishedAt:   time.Now().UTC(),
        Payload:       data,
    }
    return json.Marshal(env)
}
```

## The Gotchas

**Connection management.** All three systems need connection pooling and reconnection logic. The clients have reconnection support but you need to configure it — don't assume the library handles transient network failures transparently. NATS's `nats.Connect` accepts reconnect options; RabbitMQ AMQP channels need to be re-created after connection drops; Kafka readers reconnect automatically.

**Back pressure.** If consumers are slower than producers, messages pile up. Kafka handles this naturally via log retention. RabbitMQ queues fill up and can OOM the broker. NATS drops messages with no subscribers. Monitor queue depth and consumer lag for every system — it's the primary operational signal.

**Schema evolution.** Messages outlive the code that produced them. Kafka's replay means a consumer written today must handle messages produced two years ago. Use a schema registry (Confluent Schema Registry for Kafka, or DIY with the version field in your envelope) and always add new fields rather than changing existing ones.

## Key Takeaway

NATS is fast and simple — choose it for speed. RabbitMQ is reliable and flexible — choose it for task queues. Kafka is durable and replayable — choose it for event streams. The mistake is using one system for everything because you're already familiar with it. Know what guarantee you actually need before you pick.

---

**Previous:** [Lesson 4: Connection Pooling](/post/go/go-net-conn-pooling/)
**Next:** [Lesson 6: Eventual Consistency — Your data will be wrong, temporarily](/post/go/go-net-eventual-consistency/)
