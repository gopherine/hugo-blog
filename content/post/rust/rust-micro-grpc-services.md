---
title: "Lesson 2: gRPC Microservices with tonic — Production-grade RPC"
author: Atharva Pandey
series: "Rust Microservices Patterns"
lesson: 2
keywords: [Rust, rust, microservices, gRPC, tonic, protobuf, RPC, prost, tower]
tags: [Rust tutorial, rust, microservices, architecture]
date: '2025-06-03T14:37:00.000Z'
---

The first time I used gRPC in production was on a Go project. The experience was fine — `protoc` generated stubs, you implemented an interface, done. Then I tried `tonic` in Rust, and I realized what gRPC was *supposed* to feel like. Type-safe request/response types generated at compile time, streaming that works with Rust's async model, and interceptors built on the same Tower middleware stack as Axum. It's gRPC done right.

Let me walk you through building a production-grade gRPC service from scratch.

## Why gRPC Over REST

Quick sidebar on when gRPC makes sense. For service-to-service communication inside your infrastructure, gRPC wins on almost every dimension:

- **Schema enforcement** — Protobuf definitions are the contract. Both sides agree on the types at compile time.
- **Performance** — Binary serialization is significantly faster than JSON. For high-throughput internal calls, this matters.
- **Streaming** — Server-streaming, client-streaming, and bidirectional streaming are first-class concepts.
- **Code generation** — You write a `.proto` file and get type-safe client and server code.

For public-facing APIs? Stick with REST/JSON. Browsers, mobile apps, and third-party developers don't want to deal with protobuf.

## Setting Up the Project

First, the dependencies. Tonic uses `prost` for protobuf code generation and `tonic-build` as a build-time tool.

```toml
# Cargo.toml
[package]
name = "order-grpc"
version = "0.1.0"
edition = "2021"

[dependencies]
tonic = "0.12"
prost = "0.13"
prost-types = "0.13"
tokio = { version = "1", features = ["full"] }
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter"] }
uuid = { version = "1", features = ["v4"] }
chrono = "0.4"
anyhow = "1"
tower = "0.5"

[build-dependencies]
tonic-build = "0.12"
```

## The Proto Definition

This is where you spend the most time — and you should. A well-designed proto file is worth ten hours of implementation work.

```protobuf
// proto/order.proto
syntax = "proto3";

package order.v1;

service OrderService {
  // Unary RPCs
  rpc CreateOrder(CreateOrderRequest) returns (CreateOrderResponse);
  rpc GetOrder(GetOrderRequest) returns (GetOrderResponse);
  rpc CancelOrder(CancelOrderRequest) returns (CancelOrderResponse);

  // Server-streaming: get real-time order status updates
  rpc WatchOrder(WatchOrderRequest) returns (stream OrderStatusUpdate);

  // Client-streaming: bulk import orders
  rpc BulkCreateOrders(stream CreateOrderRequest) returns (BulkCreateResponse);
}

message CreateOrderRequest {
  string customer_id = 1;
  string idempotency_key = 2;
  repeated OrderItem items = 3;
}

message OrderItem {
  string product_id = 1;
  uint32 quantity = 2;
  int64 price_cents = 3;
}

message CreateOrderResponse {
  Order order = 1;
}

message GetOrderRequest {
  string order_id = 1;
}

message GetOrderResponse {
  Order order = 1;
}

message CancelOrderRequest {
  string order_id = 1;
  string reason = 2;
}

message CancelOrderResponse {
  Order order = 1;
}

message WatchOrderRequest {
  string order_id = 1;
}

message OrderStatusUpdate {
  string order_id = 1;
  OrderStatus status = 2;
  string message = 3;
  int64 timestamp_ms = 4;
}

message BulkCreateResponse {
  uint32 total_created = 1;
  uint32 total_failed = 2;
  repeated string failed_ids = 3;
}

message Order {
  string id = 1;
  string customer_id = 2;
  OrderStatus status = 3;
  repeated OrderItem items = 4;
  int64 total_cents = 5;
  int64 created_at_ms = 6;
}

enum OrderStatus {
  ORDER_STATUS_UNSPECIFIED = 0;
  ORDER_STATUS_PENDING = 1;
  ORDER_STATUS_CONFIRMED = 2;
  ORDER_STATUS_SHIPPED = 3;
  ORDER_STATUS_DELIVERED = 4;
  ORDER_STATUS_CANCELLED = 5;
}
```

Two things I always do: prefix enum values with the enum name (protobuf best practice — avoids namespace collisions), and always include an `UNSPECIFIED` zero value.

## Build Script

```rust
// build.rs
fn main() -> Result<(), Box<dyn std::error::Error>> {
    tonic_build::configure()
        .build_server(true)
        .build_client(true)
        .compile_protos(&["proto/order.proto"], &["proto/"])?;
    Ok(())
}
```

## Implementing the Server

Here's where tonic really shines. The generated code gives you a trait to implement, and Rust's type system makes sure you handle every RPC.

```rust
// src/server.rs
use tonic::{Request, Response, Status};
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;
use uuid::Uuid;

// Include the generated code
pub mod order_proto {
    tonic::include_proto!("order.v1");
}

use order_proto::order_service_server::OrderService;
use order_proto::*;

/// In-memory store for demonstration.
/// In production, this would be backed by a database.
pub struct OrderServiceImpl {
    orders: Arc<RwLock<HashMap<String, Order>>>,
    // Track idempotency keys to prevent duplicate order creation
    idempotency_keys: Arc<RwLock<HashMap<String, String>>>,
}

impl OrderServiceImpl {
    pub fn new() -> Self {
        Self {
            orders: Arc::new(RwLock::new(HashMap::new())),
            idempotency_keys: Arc::new(RwLock::new(HashMap::new())),
        }
    }
}

#[tonic::async_trait]
impl OrderService for OrderServiceImpl {
    async fn create_order(
        &self,
        request: Request<CreateOrderRequest>,
    ) -> Result<Response<CreateOrderResponse>, Status> {
        let req = request.into_inner();

        // Idempotency check — return existing order if key was seen
        {
            let keys = self.idempotency_keys.read().await;
            if let Some(existing_id) = keys.get(&req.idempotency_key) {
                let orders = self.orders.read().await;
                if let Some(order) = orders.get(existing_id) {
                    return Ok(Response::new(CreateOrderResponse {
                        order: Some(order.clone()),
                    }));
                }
            }
        }

        // Validate input
        if req.items.is_empty() {
            return Err(Status::invalid_argument("order must have at least one item"));
        }

        if req.customer_id.is_empty() {
            return Err(Status::invalid_argument("customer_id is required"));
        }

        let total_cents: i64 = req.items.iter()
            .map(|item| item.price_cents * item.quantity as i64)
            .sum();

        let order = Order {
            id: Uuid::new_v4().to_string(),
            customer_id: req.customer_id,
            status: OrderStatus::Pending as i32,
            items: req.items,
            total_cents,
            created_at_ms: chrono::Utc::now().timestamp_millis(),
        };

        // Store the order and idempotency key
        {
            let mut orders = self.orders.write().await;
            let mut keys = self.idempotency_keys.write().await;
            keys.insert(req.idempotency_key, order.id.clone());
            orders.insert(order.id.clone(), order.clone());
        }

        tracing::info!(order_id = %order.id, "order created");

        Ok(Response::new(CreateOrderResponse {
            order: Some(order),
        }))
    }

    async fn get_order(
        &self,
        request: Request<GetOrderRequest>,
    ) -> Result<Response<GetOrderResponse>, Status> {
        let order_id = request.into_inner().order_id;

        let orders = self.orders.read().await;
        match orders.get(&order_id) {
            Some(order) => Ok(Response::new(GetOrderResponse {
                order: Some(order.clone()),
            })),
            None => Err(Status::not_found(format!("order {} not found", order_id))),
        }
    }

    async fn cancel_order(
        &self,
        request: Request<CancelOrderRequest>,
    ) -> Result<Response<CancelOrderResponse>, Status> {
        let req = request.into_inner();

        let mut orders = self.orders.write().await;
        match orders.get_mut(&req.order_id) {
            Some(order) => {
                let current_status = OrderStatus::try_from(order.status)
                    .unwrap_or(OrderStatus::Unspecified);

                // Only pending or confirmed orders can be cancelled
                match current_status {
                    OrderStatus::Pending | OrderStatus::Confirmed => {
                        order.status = OrderStatus::Cancelled as i32;
                        tracing::info!(
                            order_id = %req.order_id,
                            reason = %req.reason,
                            "order cancelled"
                        );
                        Ok(Response::new(CancelOrderResponse {
                            order: Some(order.clone()),
                        }))
                    }
                    _ => Err(Status::failed_precondition(format!(
                        "cannot cancel order in {:?} state",
                        current_status
                    ))),
                }
            }
            None => Err(Status::not_found(format!(
                "order {} not found",
                req.order_id
            ))),
        }
    }

    // Server-streaming RPC
    type WatchOrderStream = ReceiverStream<Result<OrderStatusUpdate, Status>>;

    async fn watch_order(
        &self,
        request: Request<WatchOrderRequest>,
    ) -> Result<Response<Self::WatchOrderStream>, Status> {
        let order_id = request.into_inner().order_id;

        // Verify order exists
        {
            let orders = self.orders.read().await;
            if !orders.contains_key(&order_id) {
                return Err(Status::not_found(format!(
                    "order {} not found",
                    order_id
                )));
            }
        }

        let (tx, rx) = mpsc::channel(32);
        let orders = self.orders.clone();

        // Spawn a task that watches for status changes
        tokio::spawn(async move {
            let mut last_status = -1i32;
            loop {
                let current_status = {
                    let orders = orders.read().await;
                    orders.get(&order_id).map(|o| o.status)
                };

                match current_status {
                    Some(status) if status != last_status => {
                        last_status = status;
                        let update = OrderStatusUpdate {
                            order_id: order_id.clone(),
                            status,
                            message: format!("Status changed to {:?}",
                                OrderStatus::try_from(status)
                                    .unwrap_or(OrderStatus::Unspecified)),
                            timestamp_ms: chrono::Utc::now().timestamp_millis(),
                        };
                        if tx.send(Ok(update)).await.is_err() {
                            break; // Client disconnected
                        }
                    }
                    None => {
                        let _ = tx.send(Err(Status::not_found("order deleted"))).await;
                        break;
                    }
                    _ => {}
                }

                tokio::time::sleep(tokio::time::Duration::from_millis(500)).await;
            }
        });

        Ok(Response::new(ReceiverStream::new(rx)))
    }

    // Client-streaming RPC
    async fn bulk_create_orders(
        &self,
        request: Request<tonic::Streaming<CreateOrderRequest>>,
    ) -> Result<Response<BulkCreateResponse>, Status> {
        let mut stream = request.into_inner();
        let mut total_created = 0u32;
        let mut total_failed = 0u32;
        let mut failed_ids = Vec::new();

        while let Some(req) = stream.message().await? {
            if req.items.is_empty() {
                total_failed += 1;
                failed_ids.push(req.idempotency_key);
                continue;
            }

            let total_cents: i64 = req.items.iter()
                .map(|item| item.price_cents * item.quantity as i64)
                .sum();

            let order = Order {
                id: Uuid::new_v4().to_string(),
                customer_id: req.customer_id,
                status: OrderStatus::Pending as i32,
                items: req.items,
                total_cents,
                created_at_ms: chrono::Utc::now().timestamp_millis(),
            };

            let mut orders = self.orders.write().await;
            orders.insert(order.id.clone(), order);
            total_created += 1;
        }

        Ok(Response::new(BulkCreateResponse {
            total_created,
            total_failed,
            failed_ids,
        }))
    }
}
```

## Interceptors (Middleware)

Tonic builds on Tower, which means interceptors compose beautifully. Here's how to add authentication and request logging:

```rust
// src/interceptors.rs
use tonic::{Request, Status};
use tracing::info;

/// Authentication interceptor.
/// Checks for a valid bearer token in the metadata.
pub fn auth_interceptor(req: Request<()>) -> Result<Request<()>, Status> {
    let token = req.metadata()
        .get("authorization")
        .and_then(|v| v.to_str().ok());

    match token {
        Some(t) if t.starts_with("Bearer ") => {
            let token_value = &t[7..];
            // In production, verify JWT here
            if validate_token(token_value) {
                Ok(req)
            } else {
                Err(Status::unauthenticated("invalid token"))
            }
        }
        _ => Err(Status::unauthenticated("missing authorization header")),
    }
}

fn validate_token(token: &str) -> bool {
    // Real implementation would verify JWT signature,
    // check expiration, validate claims, etc.
    !token.is_empty()
}

/// Tower layer for request logging and metrics.
/// This is more powerful than a simple interceptor because
/// it can inspect both requests and responses.
use tower::{Layer, Service};
use std::task::{Context, Poll};
use std::pin::Pin;
use std::future::Future;

#[derive(Clone)]
pub struct LoggingLayer;

impl<S> Layer<S> for LoggingLayer {
    type Service = LoggingService<S>;

    fn layer(&self, service: S) -> Self::Service {
        LoggingService { inner: service }
    }
}

#[derive(Clone)]
pub struct LoggingService<S> {
    inner: S,
}

impl<S, ReqBody, ResBody> Service<http::Request<ReqBody>> for LoggingService<S>
where
    S: Service<http::Request<ReqBody>, Response = http::Response<ResBody>> + Clone + Send + 'static,
    S::Future: Send + 'static,
    ReqBody: Send + 'static,
{
    type Response = S::Response;
    type Error = S::Error;
    type Future = Pin<Box<dyn Future<Output = Result<Self::Response, Self::Error>> + Send>>;

    fn poll_ready(&mut self, cx: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
        self.inner.poll_ready(cx)
    }

    fn call(&mut self, req: http::Request<ReqBody>) -> Self::Future {
        let mut inner = self.inner.clone();
        let method = req.uri().path().to_string();
        let start = std::time::Instant::now();

        Box::pin(async move {
            let response = inner.call(req).await;
            let elapsed = start.elapsed();

            info!(
                method = %method,
                duration_ms = elapsed.as_millis() as u64,
                "gRPC request completed"
            );

            response
        })
    }
}
```

## Wiring It All Together

```rust
// src/main.rs
use tonic::transport::Server;
use tracing_subscriber::EnvFilter;

mod server;
mod interceptors;

use server::order_proto::order_service_server::OrderServiceServer;
use server::OrderServiceImpl;
use interceptors::{auth_interceptor, LoggingLayer};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env()
            .add_directive("order_grpc=debug".parse()?))
        .init();

    let addr = "0.0.0.0:50051".parse()?;
    let order_service = OrderServiceImpl::new();

    tracing::info!(%addr, "starting gRPC server");

    Server::builder()
        .layer(LoggingLayer)
        .add_service(
            OrderServiceServer::with_interceptor(
                order_service,
                auth_interceptor,
            )
        )
        .serve(addr)
        .await?;

    Ok(())
}
```

## Building the Client

Tonic generates client code too. Here's a client with retry logic and connection pooling:

```rust
// src/client.rs
use tonic::transport::{Channel, Endpoint};
use tonic::metadata::MetadataValue;
use tonic::{Request, Status};
use std::time::Duration;

pub mod order_proto {
    tonic::include_proto!("order.v1");
}

use order_proto::order_service_client::OrderServiceClient;
use order_proto::*;

pub struct OrderClient {
    client: OrderServiceClient<Channel>,
    auth_token: String,
}

impl OrderClient {
    pub async fn connect(addr: &str, auth_token: String) -> anyhow::Result<Self> {
        let endpoint = Endpoint::from_shared(addr.to_string())?
            .timeout(Duration::from_secs(5))
            .connect_timeout(Duration::from_secs(5))
            .tcp_keepalive(Some(Duration::from_secs(30)));

        let channel = endpoint.connect().await?;

        Ok(Self {
            client: OrderServiceClient::new(channel),
            auth_token,
        })
    }

    /// Create an order with automatic retry on transient failures.
    pub async fn create_order(
        &mut self,
        customer_id: &str,
        items: Vec<OrderItem>,
        idempotency_key: &str,
    ) -> Result<Order, Status> {
        let request = CreateOrderRequest {
            customer_id: customer_id.to_string(),
            idempotency_key: idempotency_key.to_string(),
            items,
        };

        let mut retries = 3;
        loop {
            let mut req = Request::new(request.clone());
            let token: MetadataValue<_> = format!("Bearer {}", self.auth_token)
                .parse()
                .map_err(|_| Status::internal("invalid token format"))?;
            req.metadata_mut().insert("authorization", token);

            match self.client.create_order(req).await {
                Ok(response) => {
                    return response.into_inner().order
                        .ok_or_else(|| Status::internal("empty response"));
                }
                Err(status) if is_retryable(&status) && retries > 0 => {
                    retries -= 1;
                    tracing::warn!(
                        retries_remaining = retries,
                        code = ?status.code(),
                        "retrying failed request"
                    );
                    tokio::time::sleep(Duration::from_millis(100 * (4 - retries as u64))).await;
                }
                Err(status) => return Err(status),
            }
        }
    }
}

fn is_retryable(status: &Status) -> bool {
    matches!(
        status.code(),
        tonic::Code::Unavailable
            | tonic::Code::DeadlineExceeded
            | tonic::Code::ResourceExhausted
    )
}
```

Notice the idempotency key. This is critical for retries — if a request succeeds on the server but the response is lost due to a network blip, the retry will return the same order instead of creating a duplicate.

## Health Checks

Every gRPC service needs to implement the standard health checking protocol. Kubernetes, load balancers, and service meshes all rely on it.

```rust
use tonic_health::server::HealthReporter;

async fn run_server() -> anyhow::Result<()> {
    let (mut health_reporter, health_service) = tonic_health::server::health_reporter();

    // Mark the service as serving
    health_reporter
        .set_serving::<OrderServiceServer<OrderServiceImpl>>()
        .await;

    let addr = "0.0.0.0:50051".parse()?;
    let order_service = OrderServiceImpl::new();

    Server::builder()
        .add_service(health_service)
        .add_service(OrderServiceServer::new(order_service))
        .serve(addr)
        .await?;

    Ok(())
}
```

## gRPC Reflection

In development, you want to be able to poke at your service without writing a client. gRPC reflection lets tools like `grpcurl` and Postman discover your service's API.

```toml
# Add to Cargo.toml
[dependencies]
tonic-reflection = "0.12"

[build-dependencies]
tonic-build = "0.12"
```

```rust
// build.rs — updated
fn main() -> Result<(), Box<dyn std::error::Error>> {
    tonic_build::configure()
        .build_server(true)
        .build_client(true)
        .file_descriptor_set_path("src/generated/order_descriptor.bin")
        .compile_protos(&["proto/order.proto"], &["proto/"])?;
    Ok(())
}
```

Then add the reflection service alongside your main service. Now anyone can run `grpcurl -plaintext localhost:50051 list` and see every RPC you expose.

## Lessons from Production

A few things I've learned shipping tonic services:

**Set deadlines on everything.** If you don't set a timeout, a hung downstream service will hold connections forever. Tonic's `Endpoint::timeout()` is your friend.

**Use streaming carefully.** Server-streaming is great for real-time updates. Client-streaming is great for bulk operations. Bidirectional streaming is great for chat protocols and rarely needed elsewhere. Don't use streaming just because you can — it adds complexity to error handling and resource management.

**Proto evolution matters.** Never change field numbers. Never reuse deleted field numbers. Always add new fields as optional. These rules sound obvious, but I've seen each one violated in production.

**Interceptors beat per-handler logic.** Auth, logging, tracing, rate limiting — anything cross-cutting goes in an interceptor. If you're copy-pasting the same three lines into every handler, you're doing it wrong.

In the next lesson, we'll look at event-driven architecture — what happens when you don't want services talking to each other directly at all.
