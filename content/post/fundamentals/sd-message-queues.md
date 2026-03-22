---
title: "Lesson 5: Message Queues — Decoupling Services Without Losing Messages"
author: "Atharva Pandey"
series: "System Design from First Principles"
lesson: 5
tags:
  - fundamentals
  - system design
keywords:
  - message queues
  - Kafka
  - RabbitMQ
  - pub/sub
  - at-least-once delivery
  - exactly-once delivery
  - system design
date: "2024-06-19T00:00:00.000Z"
---

Synchronous calls are elegant until they're not. Service A calls Service B. B is slow. A waits. A's request pool fills up. A becomes slow. The caller of A waits. The whole request chain stalls. Add a few more services in the chain and you have a distributed deadlock in slow motion. Message queues exist to break this dependency — to let a producer say "here's some work" and move on, without caring whether the consumer is fast, slow, or temporarily down.

## The Core Concept

A message queue is a durable buffer that sits between producers and consumers. The producer writes a message to the queue and gets an acknowledgment that the queue received it. The consumer reads messages from the queue at its own pace, processes them, and acknowledges each one. The queue holds messages until they're consumed.

This achieves three things:
1. **Temporal decoupling**: the producer and consumer don't need to be running at the same time (within the queue's retention window).
2. **Rate decoupling**: the producer can spike; the consumer processes at a steady rate. The queue absorbs the burst.
3. **Fault isolation**: if the consumer crashes, messages stay in the queue. When it recovers, it picks up where it left off.

**Delivery Semantics**

This is where message queues get subtle. There are three delivery guarantees:

- **At-most-once**: messages may be lost, but never delivered twice. Achieved by not storing the message durably and not retrying. Good for metrics or logs where losing some data is acceptable.
- **At-least-once**: every message is delivered, but may be delivered more than once if the consumer processes it but crashes before acknowledging. The queue doesn't know the difference between "crashed after processing" and "crashed before processing," so it redelivers on timeout. **This is the default for most queues.** Consumers must be idempotent.
- **Exactly-once**: every message is delivered and processed exactly once. Extremely hard to guarantee in distributed systems. Kafka achieves this within the broker but getting true end-to-end exactly-once requires the consumer's action itself to be idempotent or transactional.

**Idempotency under at-least-once**

Because at-least-once is the practical baseline, your consumers should be idempotent: processing the same message twice should have the same effect as processing it once.

```go
func ProcessPayment(ctx context.Context, msg PaymentMessage) error {
    // Check if already processed using idempotency key
    exists, err := db.ExistsPayment(ctx, msg.IdempotencyKey)
    if err != nil {
        return err
    }
    if exists {
        // Already processed — safe to acknowledge and skip
        return nil
    }

    // Process and record atomically
    return db.WithTransaction(ctx, func(tx *sql.Tx) error {
        if err := processPaymentTx(tx, msg); err != nil {
            return err
        }
        return recordIdempotencyKey(tx, msg.IdempotencyKey)
    })
}
```

## How to Design It

**Point-to-point vs. Publish-Subscribe**

In a point-to-point queue (like SQS), each message is consumed by one consumer. Multiple consumers can read from the same queue for load balancing, but each message goes to exactly one of them.

In a pub/sub model (like Kafka topics, SNS), messages are published to a topic. Multiple subscriber groups each get their own copy of every message. Adding a new consumer group doesn't affect existing ones — you can "replay" messages.

Kafka's log-based model is particularly powerful: it stores all messages durably for a configurable retention period. Consumers track their position (offset) independently. A new service can read the entire history. A failing service can replay from where it left off. This makes Kafka excellent for event streaming and event sourcing.

**Push vs. Pull**

Some queues push messages to consumers (RabbitMQ's default). Others let consumers pull at their own rate (SQS, Kafka). Pull-based systems give consumers better control over their own load — they process as fast as they can without getting overwhelmed. Push-based is simpler but requires careful backpressure design.

**Dead Letter Queues**

What happens to a message that can't be processed? Maybe the format is invalid, or the downstream service it needs is permanently down. Without a DLQ, you either drop it (at-most-once semantics accidentally) or retry forever (blocking the queue for other messages).

A dead letter queue captures messages that have exceeded a retry limit. An operator can inspect them, fix the bug, and replay them. Every production queue setup should have a DLQ.

```
Normal Queue → Consumer fails after 3 retries → Dead Letter Queue
                                                       ↓
                                              Ops investigates
                                              Replays after fix
```

**Queue depth as a signal**

The depth of a queue (number of unprocessed messages) is one of the most useful metrics in a distributed system. A growing queue means your consumers can't keep up. Autoscaling policies often use queue depth as the trigger — scale out consumers when queue depth exceeds a threshold.

## Real-World Example

Consider an e-commerce checkout flow. Without a queue:

```
User → Checkout Service → [calls] → Inventory Service
                        → [calls] → Payment Service
                        → [calls] → Email Service
                        → [calls] → Analytics Service
```

Every service failure degrades checkout. Email Service going down means checkout fails. That's wrong.

With a queue:

```
User → Checkout Service → Confirms order → publishes OrderPlaced event
                                                    ↓
                              ┌─────────────────────┤
                              ↓                     ↓                    ↓
                     Inventory Consumer   Email Consumer   Analytics Consumer
```

Checkout is fast and decoupled. Each downstream service processes at its own rate. If Email Service goes down for an hour, messages queue up and are processed when it recovers. No orders are lost.

Uber uses Kafka for their event streaming backbone — hundreds of topics, billions of messages per day. Every significant action (trip started, payment processed, driver location update) is a Kafka event that multiple downstream services consume independently.

## Interview Tips

When you propose a message queue, expect: "Why not just call the service directly?" The answer: reliability and rate decoupling. If the downstream service is slow or temporarily unavailable, a synchronous call means your upstream service degrades too. A queue lets the upstream service succeed immediately and lets the downstream service process at its own pace.

"How do you ensure no messages are lost?" — The queue persists messages to disk before acknowledging the producer. Consumers only acknowledge after processing. Replication across queue brokers adds further durability.

"What if a message is processed twice?" — Consumer idempotency. Use a unique message ID or business-level idempotency key. Check before acting, or make the action inherently idempotent.

"Kafka vs SQS vs RabbitMQ?" — Kafka: high throughput, durable log, replay, event streaming. SQS: managed, simple, point-to-point or fanout (with SNS), no replay. RabbitMQ: flexible routing, push-based, lower throughput than Kafka but easier for task queues. Choose based on whether you need replay, your throughput requirements, and how much you want to manage.

"How do you handle ordering?" — SQS standard queues have no ordering guarantees. SQS FIFO queues preserve order within a message group. Kafka preserves ordering within a partition. If order matters, partition by the entity ID (e.g., all events for the same order ID go to the same partition).

## Key Takeaway

Message queues decouple producers from consumers in time, rate, and fault domain. At-least-once delivery is the practical default — design consumers to be idempotent. Use dead letter queues to catch unprocessable messages rather than letting them block the queue forever. Queue depth is a leading indicator of consumer health and a reliable autoscaling signal. Kafka's log model enables replay and independent consumer groups, making it ideal for event streaming; simpler queues like SQS suit task distribution. The right choice depends on throughput needs, ordering requirements, and operational complexity tolerance.

---

**Previous:** [Lesson 4: Database Scaling](/post/fundamentals/sd-database-scaling/)
**Next:** [Lesson 6: CDNs — Put Your Bytes Close to Your Users](/post/fundamentals/sd-cdns/)
