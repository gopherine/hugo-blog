---
title: "Lesson 9: Message Queues — NATS, Kafka, RabbitMQ from Rust"
author: Atharva Pandey
series: "Rust Networking & Distributed Systems"
lesson: 9
keywords: [Rust, rust, networking, distributed, message queue, NATS, Kafka, RabbitMQ, async, pub-sub]
tags: [Rust tutorial, rust, networking, distributed-systems]
date: '2025-06-01T15:25:00.000Z'
---

The moment I stopped thinking of services as calling each other and started thinking of them as reacting to events, my architecture got dramatically simpler. Instead of service A calling service B calling service C in a synchronous chain that's as fragile as it sounds, service A publishes an event. Services B and C subscribe and react independently. A doesn't even know they exist. B can be down for maintenance without affecting A. C can be added next month without changing A's code. Message queues are the backbone of this pattern.

## Why Message Queues?

Synchronous HTTP/gRPC calls create tight coupling. The caller has to know the callee's address, the callee has to be running, and the caller blocks until the callee responds. Message queues decouple all three:

- **Temporal decoupling** — the producer and consumer don't need to be running at the same time. Messages persist in the queue.
- **Spatial decoupling** — the producer doesn't need to know where (or how many) consumers exist.
- **Load leveling** — if producers generate work faster than consumers can handle, the queue absorbs the burst.

The three big players in this space are NATS, Apache Kafka, and RabbitMQ. They serve different needs, and the Rust ecosystem has solid clients for all three.

## NATS — Simple, Fast, Cloud-Native

NATS is my default choice for most projects. It's a single binary, trivial to deploy, and its Rust client is excellent. It does pub/sub, request/reply, and with JetStream, persistent messaging.

```toml
[dependencies]
async-nats = "0.37"
tokio = { version = "1", features = ["full"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

### Basic Pub/Sub

```rust
use async_nats;
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
struct OrderEvent {
    order_id: String,
    user_id: String,
    amount_cents: u64,
    event_type: String,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let client = async_nats::connect("localhost:4222").await?;

    // Subscribe to order events
    let mut subscriber = client.subscribe("orders.>").await?;

    // Publish some events
    let event = OrderEvent {
        order_id: "ord-001".into(),
        user_id: "usr-42".into(),
        amount_cents: 4999,
        event_type: "created".into(),
    };

    let payload = serde_json::to_vec(&event)?;
    client.publish("orders.created", payload.into()).await?;

    let event2 = OrderEvent {
        order_id: "ord-001".into(),
        user_id: "usr-42".into(),
        amount_cents: 4999,
        event_type: "paid".into(),
    };

    let payload2 = serde_json::to_vec(&event2)?;
    client.publish("orders.paid", payload2.into()).await?;

    // Consume messages
    // (In production, this would be in a separate service)
    while let Some(msg) = subscriber.next().await {
        let event: OrderEvent = serde_json::from_slice(&msg.payload)?;
        println!(
            "Received on {}: {:?}",
            msg.subject, event
        );
    }

    Ok(())
}
```

The `orders.>` subscription uses NATS wildcards — `>` matches one or more tokens. So it catches `orders.created`, `orders.paid`, `orders.shipped`, etc. This is incredibly useful for building services that need to observe all events in a domain.

### Request/Reply

NATS also supports request/reply — basically RPC over a message queue. The client sends a message and waits for a response.

```rust
use async_nats;
use tokio::time::Duration;

async fn run_responder(
    client: async_nats::Client,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut sub = client.subscribe("pricing.calculate").await?;

    while let Some(msg) = sub.next().await {
        let item = String::from_utf8_lossy(&msg.payload);
        println!("Calculating price for: {item}");

        // Simulate price calculation
        let price = match item.as_ref() {
            "widget" => 999,
            "gadget" => 2499,
            _ => 100,
        };

        let response = format!("{{\"price_cents\": {price}}}");

        if let Some(reply) = msg.reply {
            client
                .publish(reply, response.into())
                .await?;
        }
    }

    Ok(())
}

async fn run_requester(
    client: async_nats::Client,
) -> Result<(), Box<dyn std::error::Error>> {
    // Request with timeout
    let response = client
        .request("pricing.calculate", "widget".into())
        .await?;

    println!("Price response: {}", String::from_utf8_lossy(&response.payload));

    Ok(())
}
```

### JetStream for Persistent Messaging

Basic NATS pub/sub is fire-and-forget — if no subscriber is listening when a message is published, it's gone. JetStream adds persistence, replay, and consumer groups.

```rust
use async_nats::jetstream;
use async_nats::jetstream::consumer::PullConsumer;
use futures::StreamExt;

async fn jetstream_example() -> Result<(), Box<dyn std::error::Error>> {
    let client = async_nats::connect("localhost:4222").await?;
    let js = jetstream::new(client);

    // Create or get a stream
    let stream = js
        .get_or_create_stream(jetstream::stream::Config {
            name: "ORDERS".to_string(),
            subjects: vec!["orders.>".to_string()],
            retention: jetstream::stream::RetentionPolicy::Limits,
            max_messages: 100_000,
            ..Default::default()
        })
        .await?;

    // Publish with acknowledgment
    let ack = js
        .publish("orders.created", r#"{"order_id":"ord-002"}"#.into())
        .await?
        .await?;
    println!("Published, sequence: {}", ack.sequence);

    // Create a durable consumer
    let consumer: PullConsumer = stream
        .get_or_create_consumer(
            "order-processor",
            jetstream::consumer::pull::Config {
                durable_name: Some("order-processor".to_string()),
                ack_policy: jetstream::consumer::AckPolicy::Explicit,
                ..Default::default()
            },
        )
        .await?;

    // Fetch and process messages
    let mut messages = consumer.fetch().max_messages(10).messages().await?;

    while let Some(msg) = messages.next().await {
        let msg = msg?;
        println!(
            "Processing: {}",
            String::from_utf8_lossy(&msg.payload)
        );
        msg.ack().await?;
    }

    Ok(())
}
```

The explicit `ack()` call is critical. If your service crashes between receiving a message and acknowledging it, NATS will redeliver the message to another consumer. This gives you at-least-once delivery semantics — the message is guaranteed to be processed, though it might be processed more than once. Your consumers need to be idempotent.

## Apache Kafka — The Log

Kafka is fundamentally different from NATS and RabbitMQ. It's not a message queue — it's a distributed commit log. Messages are written to partitioned topics and retained for a configurable period (days, weeks, forever). Consumers track their position in the log and can replay from any point.

This makes Kafka ideal for event sourcing, stream processing, and any use case where you need to replay history.

```toml
[dependencies]
rdkafka = { version = "0.36", features = ["cmake-build"] }
tokio = { version = "1", features = ["full"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

### Producer

```rust
use rdkafka::config::ClientConfig;
use rdkafka::producer::{FutureProducer, FutureRecord};
use serde::Serialize;
use std::time::Duration;

#[derive(Serialize)]
struct UserEvent {
    user_id: String,
    action: String,
    timestamp: i64,
}

async fn produce_events() -> Result<(), Box<dyn std::error::Error>> {
    let producer: FutureProducer = ClientConfig::new()
        .set("bootstrap.servers", "localhost:9092")
        .set("message.timeout.ms", "5000")
        .set("acks", "all") // Wait for all replicas to acknowledge
        .create()?;

    let events = vec![
        UserEvent {
            user_id: "usr-1".into(),
            action: "login".into(),
            timestamp: 1700000000,
        },
        UserEvent {
            user_id: "usr-2".into(),
            action: "purchase".into(),
            timestamp: 1700000001,
        },
        UserEvent {
            user_id: "usr-1".into(),
            action: "logout".into(),
            timestamp: 1700000005,
        },
    ];

    for event in &events {
        let payload = serde_json::to_string(event)?;

        // Key determines partition — same user always goes to same partition
        let delivery = producer
            .send(
                FutureRecord::to("user-events")
                    .key(&event.user_id)
                    .payload(&payload),
                Duration::from_secs(5),
            )
            .await;

        match delivery {
            Ok((partition, offset)) => {
                println!(
                    "Delivered to partition {partition}, offset {offset}: {}",
                    event.action
                );
            }
            Err((err, _)) => {
                eprintln!("Delivery failed: {err}");
            }
        }
    }

    Ok(())
}
```

The message **key** is important in Kafka. Messages with the same key always go to the same partition, which guarantees ordering per key. For user events, keying by `user_id` means all events for a given user are processed in order — even across multiple consumer instances.

### Consumer

```rust
use rdkafka::config::ClientConfig;
use rdkafka::consumer::{CommitMode, Consumer, StreamConsumer};
use rdkafka::Message;
use futures::StreamExt;

async fn consume_events() -> Result<(), Box<dyn std::error::Error>> {
    let consumer: StreamConsumer = ClientConfig::new()
        .set("bootstrap.servers", "localhost:9092")
        .set("group.id", "analytics-service")
        .set("auto.offset.reset", "earliest")
        .set("enable.auto.commit", "false") // Manual commits
        .create()?;

    consumer.subscribe(&["user-events"])?;

    let mut stream = consumer.stream();

    while let Some(result) = stream.next().await {
        match result {
            Ok(msg) => {
                let payload = msg.payload_view::<str>().unwrap_or(Ok(""))?;
                let key = msg
                    .key_view::<str>()
                    .unwrap_or(Ok(""))
                    .unwrap_or("");

                println!(
                    "Topic: {}, Partition: {}, Offset: {}, Key: {key}",
                    msg.topic(),
                    msg.partition(),
                    msg.offset(),
                );
                println!("  Payload: {payload}");

                // Process the message...

                // Commit the offset after successful processing
                consumer.commit_message(&msg, CommitMode::Async)?;
            }
            Err(e) => {
                eprintln!("Kafka error: {e}");
            }
        }
    }

    Ok(())
}
```

Manual offset commits give you control over exactly-once processing semantics. Don't commit until you've fully processed the message. If your service crashes, it'll restart from the last committed offset and reprocess any uncommitted messages.

## RabbitMQ — The Classic

RabbitMQ is the traditional message broker with the richest routing capabilities. Exchanges, bindings, routing keys, dead-letter queues — it's got all the primitives for complex routing topologies.

```toml
[dependencies]
lapin = "2"
tokio = { version = "1", features = ["full"] }
tokio-executor-trait = "2"
tokio-reactor-trait = "1"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

```rust
use lapin::{
    options::*, types::FieldTable, BasicProperties, Channel,
    Connection, ConnectionProperties,
};
use futures::StreamExt;

async fn setup_rabbit() -> Result<Channel, Box<dyn std::error::Error>> {
    let conn = Connection::connect(
        "amqp://guest:guest@localhost:5672",
        ConnectionProperties::default()
            .with_executor(tokio_executor_trait::Tokio::current())
            .with_reactor(tokio_reactor_trait::Tokio),
    )
    .await?;

    let channel = conn.create_channel().await?;

    // Declare an exchange
    channel
        .exchange_declare(
            "events",
            lapin::ExchangeKind::Topic,
            ExchangeDeclareOptions::default(),
            FieldTable::default(),
        )
        .await?;

    // Declare a queue
    channel
        .queue_declare(
            "order-notifications",
            QueueDeclareOptions {
                durable: true,
                ..Default::default()
            },
            FieldTable::default(),
        )
        .await?;

    // Bind queue to exchange with routing pattern
    channel
        .queue_bind(
            "order-notifications",
            "events",
            "order.#", // Match order.created, order.shipped, etc.
            QueueBindOptions::default(),
            FieldTable::default(),
        )
        .await?;

    Ok(channel)
}

async fn publish_event(
    channel: &Channel,
    routing_key: &str,
    payload: &[u8],
) -> Result<(), Box<dyn std::error::Error>> {
    channel
        .basic_publish(
            "events",
            routing_key,
            BasicPublishOptions::default(),
            payload,
            BasicProperties::default()
                .with_content_type("application/json".into())
                .with_delivery_mode(2), // Persistent
        )
        .await?
        .await?; // Wait for confirm

    Ok(())
}

async fn consume_events(channel: &Channel) -> Result<(), Box<dyn std::error::Error>> {
    let mut consumer = channel
        .basic_consume(
            "order-notifications",
            "notification-service",
            BasicConsumeOptions::default(),
            FieldTable::default(),
        )
        .await?;

    println!("Waiting for messages...");

    while let Some(delivery) = consumer.next().await {
        match delivery {
            Ok(delivery) => {
                let payload = String::from_utf8_lossy(&delivery.data);
                println!(
                    "Received [{}]: {payload}",
                    delivery.routing_key
                );

                // Process the message...

                // Acknowledge
                delivery
                    .ack(BasicAckOptions::default())
                    .await?;
            }
            Err(e) => {
                eprintln!("Consumer error: {e}");
            }
        }
    }

    Ok(())
}
```

RabbitMQ's topic exchange routing is powerful. The pattern `order.#` matches `order.created`, `order.shipped`, and `order.item.added`. The `*` wildcard matches exactly one word, while `#` matches zero or more words. This lets you build fine-grained subscriptions without changing the publisher.

## Choosing the Right Queue

After using all three in production, here's my decision framework:

**NATS** — choose when you want simplicity, low latency, and cloud-native tooling. Great for microservice communication, IoT, and edge computing. JetStream adds persistence when you need it. The Rust client is the best of the three.

**Kafka** — choose when you need an event log that retains history, when you're doing stream processing, or when ordering guarantees per partition matter. Heavier to operate but unmatched for high-throughput, event-sourcing architectures. The `rdkafka` crate wraps librdkafka (C library), so it's fast but adds a C dependency.

**RabbitMQ** — choose when you need complex routing topologies, when you want mature tooling (the management UI is excellent), or when your organization already runs it. The `lapin` crate is solid and pure Rust.

| Feature | NATS | Kafka | RabbitMQ |
|---------|------|-------|----------|
| Latency | Very low | Low-Medium | Medium |
| Throughput | High | Very high | Medium |
| Message retention | Configurable (JetStream) | Configurable (default) | Until consumed |
| Ordering | Per subject | Per partition | Per queue |
| Routing | Subjects + wildcards | Topics + partitions | Exchanges + bindings |
| Complexity | Low | High | Medium |
| Rust client | Excellent | Good (C wrapper) | Good |

## Error Handling Patterns

Regardless of which queue you use, you need a strategy for messages that fail processing. The standard approach is a dead-letter queue (DLQ):

```rust
async fn process_with_dlq(
    msg: &[u8],
    max_retries: u32,
) -> Result<(), ProcessError> {
    // Parse retry count from message headers
    let retry_count = extract_retry_count(msg);

    match process_message(msg).await {
        Ok(()) => Ok(()),
        Err(e) if retry_count < max_retries => {
            // Requeue with incremented retry count
            let mut headers = extract_headers(msg);
            headers.insert("x-retry-count", (retry_count + 1).to_string());
            requeue_with_delay(msg, &headers, backoff_delay(retry_count)).await?;
            Ok(())
        }
        Err(e) => {
            // Max retries exceeded — send to dead letter queue
            send_to_dlq(msg, &e.to_string()).await?;
            eprintln!("Message sent to DLQ after {max_retries} retries: {e}");
            Ok(())
        }
    }
}
```

The DLQ captures messages that can't be processed — malformed data, business logic errors, bugs in your consumer. Someone (or something) can then inspect the DLQ, fix the issue, and replay the messages.

## What's Next

We've covered the building blocks — TCP, HTTP, gRPC, WebSockets, DNS, TLS, retries, circuit breakers, and message queues. In the final lesson, we'll zoom out and look at distributed system patterns: consensus algorithms, CRDTs, consistency models, and how to reason about systems that span multiple machines.
