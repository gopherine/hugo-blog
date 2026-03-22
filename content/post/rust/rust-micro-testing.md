---
title: "Lesson 7: Testing Microservices — Contract tests and integration"
author: Atharva Pandey
series: "Rust Microservices Patterns"
lesson: 7
keywords: [Rust, rust, microservices, testing, contract testing, integration testing, mocking, testcontainers]
tags: [Rust tutorial, rust, microservices, architecture]
date: '2025-06-14T07:33:00.000Z'
---

I deployed a change to the order service that renamed a field from `total_amount` to `total_cents`. Made perfect sense — cents avoid floating-point nonsense. All unit tests passed. Integration tests passed. Staging looked fine.

Production broke instantly. The payment service was still expecting `total_amount`. It deserialized the response, got `None` for the amount, and started processing $0 charges. We caught it in four minutes, but four minutes of free orders adds up.

The problem wasn't that we didn't test. We tested plenty — within each service. What we didn't test was the *contract* between services. That's a fundamentally different thing, and getting it wrong is the most common way microservices fail.

## The Testing Pyramid for Microservices

The classic testing pyramid — lots of unit tests, fewer integration tests, even fewer E2E tests — needs modification for microservices. Here's what actually works:

1. **Unit tests** — Test business logic in isolation. Mock all I/O. Fast, run on every commit.
2. **Contract tests** — Verify that service interfaces match what consumers expect. Run on every PR.
3. **Integration tests** — Test a single service with real dependencies (database, message queue). Use testcontainers.
4. **Component tests** — Test a single service end-to-end via its API. Real HTTP, real database, mocked peer services.
5. **E2E tests** — Test the full system. Run in staging. Slow, flaky, but necessary.

Most teams skip contract tests. That's why most teams get bitten by the exact scenario I described above.

## Contract Testing with Trait Boundaries

Remember the service trait pattern from Lesson 1? It's the foundation of testable microservices. When you define `trait OrderService`, you can verify that both the real implementation *and* the consumers' expectations match the same contract.

```rust
// crates/contracts/src/order_contract.rs

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

/// The contract between order service producers and consumers.
/// Both sides implement against this trait — if either deviates,
/// the compiler catches it.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct OrderDto {
    pub id: Uuid,
    pub customer_id: Uuid,
    pub status: String,
    pub total_cents: i64,
    pub items: Vec<OrderItemDto>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct OrderItemDto {
    pub product_id: Uuid,
    pub quantity: u32,
    pub unit_price_cents: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CreateOrderDto {
    pub customer_id: Uuid,
    pub items: Vec<OrderItemDto>,
    pub idempotency_key: String,
}

#[async_trait]
pub trait OrderServiceContract: Send + Sync {
    async fn create_order(&self, req: CreateOrderDto) -> Result<OrderDto, ContractError>;
    async fn get_order(&self, id: Uuid) -> Result<Option<OrderDto>, ContractError>;
    async fn cancel_order(&self, id: Uuid, reason: &str) -> Result<OrderDto, ContractError>;
}

#[derive(Debug, thiserror::Error)]
pub enum ContractError {
    #[error("not found: {0}")]
    NotFound(String),
    #[error("invalid input: {0}")]
    InvalidInput(String),
    #[error("conflict: {0}")]
    Conflict(String),
    #[error("internal: {0}")]
    Internal(String),
}
```

Now here's the trick — write a test suite that runs against `dyn OrderServiceContract`. Both the real service and the mock must pass the same tests.

```rust
// crates/contracts/src/order_contract_tests.rs

#[cfg(test)]
use super::*;

/// Contract test suite.
/// Any implementation of OrderServiceContract must pass ALL of these.
/// Run this against the real service AND the mock.
pub async fn contract_test_suite(service: &dyn OrderServiceContract) {
    test_create_order(service).await;
    test_get_nonexistent_order(service).await;
    test_cancel_order(service).await;
    test_cancel_already_cancelled(service).await;
    test_idempotent_create(service).await;
}

async fn test_create_order(service: &dyn OrderServiceContract) {
    let req = CreateOrderDto {
        customer_id: Uuid::new_v4(),
        items: vec![OrderItemDto {
            product_id: Uuid::new_v4(),
            quantity: 2,
            unit_price_cents: 1500,
        }],
        idempotency_key: Uuid::new_v4().to_string(),
    };

    let result = service.create_order(req.clone()).await;
    assert!(result.is_ok(), "create_order should succeed");

    let order = result.unwrap();
    assert_eq!(order.customer_id, req.customer_id);
    assert_eq!(order.status, "pending");
    assert_eq!(order.items.len(), 1);
    assert_eq!(order.items[0].quantity, 2);
    assert_eq!(order.items[0].unit_price_cents, 1500);
}

async fn test_get_nonexistent_order(service: &dyn OrderServiceContract) {
    let result = service.get_order(Uuid::new_v4()).await;
    assert!(result.is_ok());
    assert!(result.unwrap().is_none(), "nonexistent order should return None");
}

async fn test_cancel_order(service: &dyn OrderServiceContract) {
    // Create first
    let req = CreateOrderDto {
        customer_id: Uuid::new_v4(),
        items: vec![OrderItemDto {
            product_id: Uuid::new_v4(),
            quantity: 1,
            unit_price_cents: 999,
        }],
        idempotency_key: Uuid::new_v4().to_string(),
    };
    let order = service.create_order(req).await.unwrap();

    // Cancel
    let cancelled = service
        .cancel_order(order.id, "changed my mind")
        .await;
    assert!(cancelled.is_ok());
    assert_eq!(cancelled.unwrap().status, "cancelled");
}

async fn test_cancel_already_cancelled(service: &dyn OrderServiceContract) {
    let req = CreateOrderDto {
        customer_id: Uuid::new_v4(),
        items: vec![OrderItemDto {
            product_id: Uuid::new_v4(),
            quantity: 1,
            unit_price_cents: 500,
        }],
        idempotency_key: Uuid::new_v4().to_string(),
    };
    let order = service.create_order(req).await.unwrap();
    service.cancel_order(order.id, "first cancel").await.unwrap();

    // Second cancel should fail
    let result = service.cancel_order(order.id, "second cancel").await;
    assert!(result.is_err(), "cancelling an already cancelled order should fail");
}

async fn test_idempotent_create(service: &dyn OrderServiceContract) {
    let key = Uuid::new_v4().to_string();
    let req = CreateOrderDto {
        customer_id: Uuid::new_v4(),
        items: vec![OrderItemDto {
            product_id: Uuid::new_v4(),
            quantity: 1,
            unit_price_cents: 2000,
        }],
        idempotency_key: key.clone(),
    };

    let order1 = service.create_order(req.clone()).await.unwrap();
    let order2 = service.create_order(req).await.unwrap();

    assert_eq!(order1.id, order2.id, "idempotent creates should return the same order");
}
```

## Integration Testing with Testcontainers

Unit tests with mocks prove your logic works. Integration tests with real databases prove your SQL works. Testcontainers lets you spin up real Postgres, Redis, and NATS instances in Docker for testing.

```rust
// tests/integration/order_service_test.rs

use testcontainers::{clients::Cli, images::postgres::Postgres, Container};
use sqlx::PgPool;
use uuid::Uuid;

struct TestContext {
    pool: PgPool,
    _container: Container<'static, Postgres>,
}

async fn setup() -> TestContext {
    let docker = Cli::default();
    let container = docker.run(
        Postgres::default()
            .with_env_var("POSTGRES_DB", "test_orders")
            .with_env_var("POSTGRES_USER", "test")
            .with_env_var("POSTGRES_PASSWORD", "test"),
    );

    let port = container.get_host_port_ipv4(5432);
    let url = format!("postgres://test:test@localhost:{}/test_orders", port);

    let pool = PgPool::connect(&url).await.expect("failed to connect");

    // Run migrations
    sqlx::migrate!("./migrations")
        .run(&pool)
        .await
        .expect("migrations failed");

    TestContext {
        pool,
        _container: container,
    }
}

#[tokio::test]
async fn test_order_creation_persists() {
    let ctx = setup().await;
    let service = OrderServiceImpl::new(ctx.pool.clone());

    let req = CreateOrderDto {
        customer_id: Uuid::new_v4(),
        items: vec![OrderItemDto {
            product_id: Uuid::new_v4(),
            quantity: 3,
            unit_price_cents: 1200,
        }],
        idempotency_key: Uuid::new_v4().to_string(),
    };

    let created = service.create_order(req).await.unwrap();

    // Verify it's actually in the database
    let row = sqlx::query_as::<_, (Uuid, String, i64)>(
        "SELECT id, status, total_cents FROM orders WHERE id = $1",
    )
    .bind(created.id)
    .fetch_one(&ctx.pool)
    .await
    .unwrap();

    assert_eq!(row.0, created.id);
    assert_eq!(row.1, "pending");
    assert_eq!(row.2, 3600); // 3 * 1200
}

#[tokio::test]
async fn test_concurrent_idempotent_creates() {
    let ctx = setup().await;
    let service = std::sync::Arc::new(OrderServiceImpl::new(ctx.pool.clone()));

    let key = Uuid::new_v4().to_string();
    let customer_id = Uuid::new_v4();

    // Fire 10 concurrent creates with the same idempotency key
    let handles: Vec<_> = (0..10)
        .map(|_| {
            let svc = service.clone();
            let key = key.clone();
            tokio::spawn(async move {
                svc.create_order(CreateOrderDto {
                    customer_id,
                    items: vec![OrderItemDto {
                        product_id: Uuid::new_v4(),
                        quantity: 1,
                        unit_price_cents: 100,
                    }],
                    idempotency_key: key,
                })
                .await
            })
        })
        .collect();

    let results: Vec<_> = futures::future::join_all(handles)
        .await
        .into_iter()
        .map(|r| r.unwrap())
        .collect();

    // All should succeed
    let successes: Vec<_> = results.iter().filter(|r| r.is_ok()).collect();
    assert!(!successes.is_empty());

    // All successful results should return the same order ID
    let ids: std::collections::HashSet<_> = successes
        .iter()
        .map(|r| r.as_ref().unwrap().id)
        .collect();
    assert_eq!(ids.len(), 1, "all idempotent creates should return the same order");
}
```

## Component Testing — Testing the HTTP Layer

Unit and integration tests cover logic and data. Component tests cover the full request lifecycle — HTTP parsing, middleware, serialization, error responses.

```rust
// tests/component/api_test.rs

use axum::body::Body;
use axum::http::{Request, StatusCode};
use tower::ServiceExt; // for oneshot
use serde_json::json;

/// Test the actual HTTP API without starting a real server.
/// axum's Router implements tower::Service, so we can call it directly.
#[tokio::test]
async fn test_create_order_api() {
    let app = build_test_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/orders")
                .header("content-type", "application/json")
                .header("authorization", "Bearer test-token")
                .body(Body::from(
                    json!({
                        "customer_id": "550e8400-e29b-41d4-a716-446655440000",
                        "items": [{
                            "product_id": "660e8400-e29b-41d4-a716-446655440000",
                            "quantity": 2,
                            "unit_price_cents": 1500
                        }],
                        "idempotency_key": "test-key-123"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);

    let body = axum::body::to_bytes(response.into_body(), usize::MAX)
        .await
        .unwrap();
    let order: serde_json::Value = serde_json::from_slice(&body).unwrap();

    assert!(order["id"].is_string());
    assert_eq!(order["status"], "pending");
    assert_eq!(order["items"].as_array().unwrap().len(), 1);
}

#[tokio::test]
async fn test_create_order_without_auth() {
    let app = build_test_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/orders")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({
                        "customer_id": "550e8400-e29b-41d4-a716-446655440000",
                        "items": []
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn test_create_order_empty_items() {
    let app = build_test_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/orders")
                .header("content-type", "application/json")
                .header("authorization", "Bearer test-token")
                .body(Body::from(
                    json!({
                        "customer_id": "550e8400-e29b-41d4-a716-446655440000",
                        "items": [],
                        "idempotency_key": "key-1"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn test_get_nonexistent_order() {
    let app = build_test_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/api/orders/550e8400-e29b-41d4-a716-446655440000")
                .header("authorization", "Bearer test-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
}
```

## Mocking Downstream Services

When testing the order service, you don't want to call the real inventory or payment services. But you don't want to mock at the HTTP level either — that's too fragile. Mock at the trait level.

```rust
// tests/mocks/inventory_mock.rs

use async_trait::async_trait;
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

/// A configurable mock for the inventory service.
/// You can set up expected responses per product ID
/// and verify which calls were made.
pub struct MockInventoryService {
    stock: Arc<Mutex<HashMap<String, u32>>>,
    call_log: Arc<Mutex<Vec<String>>>,
}

impl MockInventoryService {
    pub fn new() -> Self {
        Self {
            stock: Arc::new(Mutex::new(HashMap::new())),
            call_log: Arc::new(Mutex::new(Vec::new())),
        }
    }

    pub fn with_stock(self, product_id: &str, quantity: u32) -> Self {
        self.stock
            .lock()
            .unwrap()
            .insert(product_id.to_string(), quantity);
        self
    }

    /// Verify that specific products were checked
    pub fn assert_checked(&self, product_id: &str) {
        let log = self.call_log.lock().unwrap();
        assert!(
            log.contains(&product_id.to_string()),
            "expected inventory check for {}, but it wasn't called. Calls: {:?}",
            product_id,
            *log
        );
    }

    pub fn call_count(&self) -> usize {
        self.call_log.lock().unwrap().len()
    }
}

#[async_trait]
impl InventoryServiceContract for MockInventoryService {
    async fn check_availability(
        &self,
        product_ids: &[String],
    ) -> Result<HashMap<String, u32>, ContractError> {
        let stock = self.stock.lock().unwrap();
        let mut log = self.call_log.lock().unwrap();

        let mut result = HashMap::new();
        for id in product_ids {
            log.push(id.clone());
            if let Some(&qty) = stock.get(id) {
                result.insert(id.clone(), qty);
            } else {
                result.insert(id.clone(), 0);
            }
        }

        Ok(result)
    }
}

/// A mock that simulates failures.
pub struct FailingInventoryService {
    fail_after: usize,
    call_count: Arc<Mutex<usize>>,
}

impl FailingInventoryService {
    pub fn new(fail_after: usize) -> Self {
        Self {
            fail_after,
            call_count: Arc::new(Mutex::new(0)),
        }
    }
}

#[async_trait]
impl InventoryServiceContract for FailingInventoryService {
    async fn check_availability(
        &self,
        _product_ids: &[String],
    ) -> Result<HashMap<String, u32>, ContractError> {
        let mut count = self.call_count.lock().unwrap();
        *count += 1;
        if *count > self.fail_after {
            Err(ContractError::Internal("inventory service unavailable".into()))
        } else {
            Ok(HashMap::new())
        }
    }
}
```

## Testing Event Handlers

Event-driven systems need dedicated testing patterns. You need to verify that events trigger the right handlers and that handlers are idempotent.

```rust
// tests/event_handler_test.rs

use tokio::sync::mpsc;

/// In-memory event bus for testing.
/// Captures all published events for assertions.
pub struct TestEventBus {
    published: Arc<Mutex<Vec<(String, Vec<u8>)>>>,
}

impl TestEventBus {
    pub fn new() -> Self {
        Self {
            published: Arc::new(Mutex::new(Vec::new())),
        }
    }

    pub fn published_events(&self) -> Vec<(String, Vec<u8>)> {
        self.published.lock().unwrap().clone()
    }

    pub fn published_count(&self) -> usize {
        self.published.lock().unwrap().len()
    }

    pub fn events_for_subject(&self, subject: &str) -> Vec<Vec<u8>> {
        self.published
            .lock()
            .unwrap()
            .iter()
            .filter(|(s, _)| s == subject)
            .map(|(_, payload)| payload.clone())
            .collect()
    }
}

#[tokio::test]
async fn test_order_created_triggers_payment() {
    let bus = TestEventBus::new();
    let handler = PaymentEventHandler::new(/* deps */);

    let event = OrderCreated {
        event_id: uuid::Uuid::new_v4().to_string(),
        order_id: uuid::Uuid::new_v4(),
        customer_id: uuid::Uuid::new_v4(),
        items: vec![],
        total_cents: 5000,
        created_at: chrono::Utc::now(),
    };

    let payload = serde_json::to_vec(&event).unwrap();
    let result = handler.handle(&payload).await;
    assert!(result.is_ok());

    // Verify payment was initiated
    // (check mock payment provider, database state, etc.)
}

#[tokio::test]
async fn test_handler_idempotency() {
    let handler = PaymentEventHandler::new(/* deps */);

    let event = OrderCreated {
        event_id: "fixed-event-id".to_string(),
        order_id: uuid::Uuid::new_v4(),
        customer_id: uuid::Uuid::new_v4(),
        items: vec![],
        total_cents: 5000,
        created_at: chrono::Utc::now(),
    };

    let payload = serde_json::to_vec(&event).unwrap();

    // Process the same event twice
    handler.handle(&payload).await.unwrap();
    handler.handle(&payload).await.unwrap();

    // Verify payment was initiated only ONCE
    // assert_eq!(mock_payment_provider.charge_count(), 1);
}
```

## Saga Testing

Sagas need special attention. You need to test both the happy path and every failure point.

```rust
#[tokio::test]
async fn test_saga_compensates_on_shipping_failure() {
    let inventory = MockInventoryStep::new(true);  // succeeds
    let payment = MockPaymentStep::new(true);      // succeeds
    let shipping = MockShippingStep::new(false);    // fails

    let saga = SagaOrchestrator::new()
        .step(inventory.clone())
        .step(payment.clone())
        .step(shipping);

    let mut context = SagaContext::new();
    context.set("order_id", &"test-order-123");
    context.set("total_cents", &5000i64);
    context.set("customer_id", &"cust-456");

    let result = saga.execute(context).await;
    assert!(result.is_err());

    // Verify compensations ran in reverse order
    assert!(payment.was_refunded(), "payment should be refunded");
    assert!(inventory.was_released(), "inventory should be released");
}

#[tokio::test]
async fn test_saga_happy_path() {
    let inventory = MockInventoryStep::new(true);
    let payment = MockPaymentStep::new(true);
    let shipping = MockShippingStep::new(true);
    let notification = MockNotificationStep::new(true);

    let saga = SagaOrchestrator::new()
        .step(inventory)
        .step(payment)
        .step(shipping)
        .step(notification);

    let mut context = SagaContext::new();
    context.set("order_id", &"test-order-789");
    context.set("total_cents", &10000i64);
    context.set("customer_id", &"cust-012");

    let result = saga.execute(context).await;
    assert!(result.is_ok());

    // Verify all steps executed
    let ctx = result.unwrap();
    assert!(ctx.get::<String>("reservation_id").is_some());
    assert!(ctx.get::<String>("payment_id").is_some());
    assert!(ctx.get::<String>("shipment_id").is_some());
}
```

## CI Pipeline for Microservice Tests

Here's the test strategy I recommend for CI:

```yaml
# .github/workflows/test.yml
name: Tests
on: [pull_request]

jobs:
  unit-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: cargo test --lib --workspace

  contract-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: cargo test --test contract_tests --workspace

  integration-tests:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_PASSWORD: test
        ports:
          - 5432:5432
      nats:
        image: nats:2
        ports:
          - 4222:4222
    steps:
      - uses: actions/checkout@v4
      - run: cargo test --test integration --workspace
```

Unit tests run in seconds. Contract tests in under a minute. Integration tests with real databases — a few minutes. This layering means you get fast feedback on most changes and thorough validation before merge.

The total_amount-to-total_cents rename? With contract tests, the PR would have failed CI before I even opened it. Five minutes of contract test setup would have saved twenty minutes of production incident and a very awkward postmortem.

Final lesson next — starting with a monolith and splitting later. It's the approach I'd recommend to anyone starting a new project.
