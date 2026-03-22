---
title: "Lesson 3: Hexagonal Architecture in Rust — Ports, adapters, and boundaries"
author: Atharva Pandey
series: "Rust in Production — Real-World Architecture"
lesson: 3
keywords: [Rust, rust, architecture, production, hexagonal, ports, adapters, clean architecture, DDD]
tags: [Rust tutorial, rust, architecture, production]
date: '2025-10-19T11:05:00.000Z'
---

About a year ago, I had to swap out our payment provider. In Go, that would've been a two-week project — chasing down every place we called Stripe's SDK, updating structs, fixing test mocks. In our Rust service, it took a day and a half. The reason wasn't Rust itself. It was how we'd structured the code.

Hexagonal architecture (sometimes called "ports and adapters") is one of those patterns that sounds academic until you actually need to replace a database, swap a message broker, or test your business logic without spinning up Docker containers. In Rust, traits make it feel natural rather than ceremonial.

## The Core Idea

Draw a hexagon. Your business logic lives inside. Everything outside — databases, HTTP, message queues, third-party APIs — connects through well-defined ports. The inside doesn't know what's outside. The outside conforms to what the inside expects.

In Rust terms: **ports are traits. Adapters are trait implementations.**

```
                    ┌─────────────────────┐
    HTTP Adapter ──▶│                     │◀── CLI Adapter
                    │   ┌─────────────┐   │
  Postgres Adapter ▶│   │   Domain    │   │◀ Redis Adapter
                    │   │   (traits)  │   │
   Stripe Adapter ──▶│   └─────────────┘   │◀── SQS Adapter
                    │                     │
                    └─────────────────────┘
```

The domain defines "I need something that can store users" (a port). The infrastructure provides "Here's a Postgres implementation" (an adapter). The domain never mentions Postgres. The adapter never changes business rules.

## Defining Ports

Ports are just traits. But how you design them matters enormously. Here's a payment port:

```rust
// src/domain/ports/payment.rs

use crate::domain::model::{Money, OrderId, CustomerId};
use async_trait::async_trait;

#[derive(Debug, Clone)]
pub struct PaymentIntent {
    pub order_id: OrderId,
    pub customer_id: CustomerId,
    pub amount: Money,
    pub description: String,
}

#[derive(Debug, Clone)]
pub struct PaymentConfirmation {
    pub payment_id: String,
    pub status: PaymentStatus,
    pub charged_amount: Money,
    pub provider_reference: String,
}

#[derive(Debug, Clone, PartialEq)]
pub enum PaymentStatus {
    Succeeded,
    RequiresAction,
    Failed(String),
}

#[derive(Debug, thiserror::Error)]
pub enum PaymentError {
    #[error("payment declined: {reason}")]
    Declined { reason: String },

    #[error("provider unavailable: {0}")]
    ProviderUnavailable(String),

    #[error("invalid payment data: {0}")]
    InvalidData(String),

    #[error("payment not found: {0}")]
    NotFound(String),
}

#[async_trait]
pub trait PaymentGateway: Send + Sync {
    async fn create_payment(
        &self,
        intent: PaymentIntent,
    ) -> Result<PaymentConfirmation, PaymentError>;

    async fn refund(
        &self,
        payment_id: &str,
        amount: Money,
    ) -> Result<PaymentConfirmation, PaymentError>;

    async fn get_status(
        &self,
        payment_id: &str,
    ) -> Result<PaymentStatus, PaymentError>;
}
```

Notice: `PaymentGateway` says nothing about Stripe, PayPal, or any specific provider. It speaks the language of the domain — `Money`, `OrderId`, `PaymentIntent`. The error type is domain-specific too: `Declined`, `ProviderUnavailable`. Not `reqwest::Error` or `serde_json::Error`.

Here's a notification port:

```rust
// src/domain/ports/notifications.rs

use crate::domain::model::{Email, OrderId, UserId};
use async_trait::async_trait;

#[derive(Debug)]
pub enum Notification {
    OrderConfirmation {
        recipient: Email,
        order_id: OrderId,
        total_display: String,
    },
    ShippingUpdate {
        recipient: Email,
        order_id: OrderId,
        tracking_url: String,
    },
    PasswordReset {
        recipient: Email,
        reset_token: String,
        expires_in_minutes: u32,
    },
}

#[derive(Debug, thiserror::Error)]
pub enum NotificationError {
    #[error("failed to send to {recipient}: {reason}")]
    SendFailed { recipient: String, reason: String },

    #[error("template rendering failed: {0}")]
    TemplateError(String),
}

#[async_trait]
pub trait NotificationService: Send + Sync {
    async fn send(&self, notification: Notification) -> Result<(), NotificationError>;
}
```

## Building Adapters

Now let's implement a Stripe adapter for the payment port:

```rust
// src/infra/stripe/payment.rs

use crate::domain::ports::payment::{
    PaymentGateway, PaymentIntent, PaymentConfirmation,
    PaymentStatus, PaymentError,
};
use crate::domain::model::Money;
use async_trait::async_trait;

pub struct StripePaymentGateway {
    client: reqwest::Client,
    api_key: String,
    base_url: String,
}

impl StripePaymentGateway {
    pub fn new(api_key: String) -> Self {
        Self {
            client: reqwest::Client::new(),
            api_key,
            base_url: "https://api.stripe.com/v1".to_string(),
        }
    }

    fn map_stripe_status(status: &str) -> PaymentStatus {
        match status {
            "succeeded" => PaymentStatus::Succeeded,
            "requires_action" | "requires_confirmation" => PaymentStatus::RequiresAction,
            other => PaymentStatus::Failed(format!("unexpected status: {}", other)),
        }
    }
}

#[async_trait]
impl PaymentGateway for StripePaymentGateway {
    async fn create_payment(
        &self,
        intent: PaymentIntent,
    ) -> Result<PaymentConfirmation, PaymentError> {
        let response = self.client
            .post(&format!("{}/payment_intents", self.base_url))
            .bearer_auth(&self.api_key)
            .form(&[
                ("amount", intent.amount.cents().to_string()),
                ("currency", intent.amount.currency_code().to_string()),
                ("description", intent.description.clone()),
                ("metadata[order_id]", intent.order_id.to_string()),
                ("metadata[customer_id]", intent.customer_id.to_string()),
                ("confirm", "true".to_string()),
            ])
            .send()
            .await
            .map_err(|e| PaymentError::ProviderUnavailable(e.to_string()))?;

        if !response.status().is_success() {
            let error_body = response.text().await.unwrap_or_default();
            return Err(PaymentError::Declined {
                reason: error_body,
            });
        }

        let stripe_response: StripePaymentIntentResponse = response
            .json()
            .await
            .map_err(|e| PaymentError::InvalidData(e.to_string()))?;

        Ok(PaymentConfirmation {
            payment_id: stripe_response.id,
            status: Self::map_stripe_status(&stripe_response.status),
            charged_amount: intent.amount,
            provider_reference: stripe_response.client_secret,
        })
    }

    async fn refund(
        &self,
        payment_id: &str,
        amount: Money,
    ) -> Result<PaymentConfirmation, PaymentError> {
        let response = self.client
            .post(&format!("{}/refunds", self.base_url))
            .bearer_auth(&self.api_key)
            .form(&[
                ("payment_intent", payment_id.to_string()),
                ("amount", amount.cents().to_string()),
            ])
            .send()
            .await
            .map_err(|e| PaymentError::ProviderUnavailable(e.to_string()))?;

        if !response.status().is_success() {
            return Err(PaymentError::Declined {
                reason: "refund failed".to_string(),
            });
        }

        Ok(PaymentConfirmation {
            payment_id: payment_id.to_string(),
            status: PaymentStatus::Succeeded,
            charged_amount: amount,
            provider_reference: String::new(),
        })
    }

    async fn get_status(
        &self,
        payment_id: &str,
    ) -> Result<PaymentStatus, PaymentError> {
        let response = self.client
            .get(&format!("{}/payment_intents/{}", self.base_url, payment_id))
            .bearer_auth(&self.api_key)
            .send()
            .await
            .map_err(|e| PaymentError::ProviderUnavailable(e.to_string()))?;

        let pi: StripePaymentIntentResponse = response
            .json()
            .await
            .map_err(|e| PaymentError::InvalidData(e.to_string()))?;

        Ok(Self::map_stripe_status(&pi.status))
    }
}

#[derive(serde::Deserialize)]
struct StripePaymentIntentResponse {
    id: String,
    status: String,
    client_secret: String,
}
```

The Stripe-specific types (`StripePaymentIntentResponse`) live *inside* the adapter. They never leak into the domain. When we switched from Stripe to a different provider, we wrote a new adapter, changed one line in the startup wiring, and the domain code didn't change at all.

## The Service Layer — Where Ports Meet Logic

Domain services consume ports through generics or trait objects:

```rust
// src/domain/services/checkout.rs

use crate::domain::model::*;
use crate::domain::ports::payment::{PaymentGateway, PaymentIntent, PaymentError};
use crate::domain::ports::notifications::{NotificationService, Notification};
use crate::domain::ports::orders::OrderRepository;

pub struct CheckoutService {
    orders: Box<dyn OrderRepository>,
    payments: Box<dyn PaymentGateway>,
    notifications: Box<dyn NotificationService>,
}

impl CheckoutService {
    pub fn new(
        orders: Box<dyn OrderRepository>,
        payments: Box<dyn PaymentGateway>,
        notifications: Box<dyn NotificationService>,
    ) -> Self {
        Self { orders, payments, notifications }
    }

    pub async fn checkout(&self, order_id: OrderId) -> Result<PaidOrder, CheckoutError> {
        // Fetch the order
        let order = self.orders.find_by_id(order_id).await?
            .ok_or(CheckoutError::OrderNotFound(order_id))?;

        // Only placed orders can be checked out
        let placed = match order {
            Order::Placed(p) => p,
            Order::Draft(_) => return Err(CheckoutError::OrderNotPlaced),
            Order::Paid(_) => return Err(CheckoutError::AlreadyPaid),
            _ => return Err(CheckoutError::InvalidState),
        };

        // Create payment
        let intent = PaymentIntent {
            order_id: placed.id,
            customer_id: placed.customer_id,
            amount: placed.total,
            description: format!("Order {}", placed.id),
        };

        let confirmation = self.payments
            .create_payment(intent)
            .await
            .map_err(CheckoutError::PaymentFailed)?;

        // Transition order state
        let paid = placed.pay(PaymentId::from_str(&confirmation.payment_id));

        // Persist
        self.orders.save(&Order::Paid(paid.clone())).await?;

        // Send confirmation (don't fail checkout if notification fails)
        let customer = self.orders.get_customer_email(paid.customer_id).await?;
        if let Some(email) = customer {
            let _ = self.notifications.send(Notification::OrderConfirmation {
                recipient: email,
                order_id: paid.id,
                total_display: paid.total.display(),
            }).await;
        }

        Ok(paid)
    }
}

#[derive(Debug, thiserror::Error)]
pub enum CheckoutError {
    #[error("order {0} not found")]
    OrderNotFound(OrderId),
    #[error("order not in placed state")]
    OrderNotPlaced,
    #[error("order already paid")]
    AlreadyPaid,
    #[error("invalid order state for checkout")]
    InvalidState,
    #[error("payment failed: {0}")]
    PaymentFailed(PaymentError),
    #[error("storage error: {0}")]
    Storage(#[from] StorageError),
}
```

Look at what `CheckoutService` knows: orders, payments, notifications. It doesn't know about Postgres, Stripe, or SendGrid. Those are adapters wired in from outside.

## Testing Without Infrastructure

This is the real payoff. You can test the entire checkout flow without a database, without a payment provider, without anything:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::sync::Mutex;

    struct InMemoryOrderRepo {
        orders: Mutex<HashMap<OrderId, Order>>,
    }

    impl InMemoryOrderRepo {
        fn new() -> Self {
            Self { orders: Mutex::new(HashMap::new()) }
        }

        fn with_order(self, order: Order) -> Self {
            let id = match &order {
                Order::Placed(o) => o.id,
                Order::Draft(o) => o.id,
                _ => panic!("unexpected state for test setup"),
            };
            self.orders.lock().unwrap().insert(id, order);
            self
        }
    }

    #[async_trait]
    impl OrderRepository for InMemoryOrderRepo {
        async fn find_by_id(&self, id: OrderId) -> Result<Option<Order>, StorageError> {
            Ok(self.orders.lock().unwrap().get(&id).cloned())
        }

        async fn save(&self, order: &Order) -> Result<(), StorageError> {
            let id = order.id();
            self.orders.lock().unwrap().insert(id, order.clone());
            Ok(())
        }

        async fn get_customer_email(&self, _id: UserId) -> Result<Option<Email>, StorageError> {
            Ok(Some(Email::parse("test@example.com").unwrap()))
        }
    }

    struct MockPaymentGateway {
        should_succeed: bool,
    }

    #[async_trait]
    impl PaymentGateway for MockPaymentGateway {
        async fn create_payment(
            &self,
            _intent: PaymentIntent,
        ) -> Result<PaymentConfirmation, PaymentError> {
            if self.should_succeed {
                Ok(PaymentConfirmation {
                    payment_id: "pay_test_123".to_string(),
                    status: PaymentStatus::Succeeded,
                    charged_amount: Money::usd(1000),
                    provider_reference: "ref_123".to_string(),
                })
            } else {
                Err(PaymentError::Declined {
                    reason: "insufficient funds".to_string(),
                })
            }
        }

        async fn refund(&self, _id: &str, _amt: Money) -> Result<PaymentConfirmation, PaymentError> {
            unimplemented!("not needed for this test")
        }

        async fn get_status(&self, _id: &str) -> Result<PaymentStatus, PaymentError> {
            unimplemented!("not needed for this test")
        }
    }

    struct NoopNotifications;

    #[async_trait]
    impl NotificationService for NoopNotifications {
        async fn send(&self, _n: Notification) -> Result<(), NotificationError> {
            Ok(())
        }
    }

    #[tokio::test]
    async fn checkout_succeeds_for_placed_order() {
        let order = Order::Placed(PlacedOrder {
            id: OrderId::new(),
            customer_id: UserId::new(),
            items: vec![LineItem::test_item()],
            total: Money::usd(2500),
            placed_at: Utc::now(),
        });
        let order_id = order.id();

        let service = CheckoutService::new(
            Box::new(InMemoryOrderRepo::new().with_order(order)),
            Box::new(MockPaymentGateway { should_succeed: true }),
            Box::new(NoopNotifications),
        );

        let result = service.checkout(order_id).await;
        assert!(result.is_ok());

        let paid = result.unwrap();
        assert_eq!(paid.id, order_id);
    }

    #[tokio::test]
    async fn checkout_fails_when_payment_declined() {
        let order = Order::Placed(PlacedOrder {
            id: OrderId::new(),
            customer_id: UserId::new(),
            items: vec![LineItem::test_item()],
            total: Money::usd(2500),
            placed_at: Utc::now(),
        });
        let order_id = order.id();

        let service = CheckoutService::new(
            Box::new(InMemoryOrderRepo::new().with_order(order)),
            Box::new(MockPaymentGateway { should_succeed: false }),
            Box::new(NoopNotifications),
        );

        let result = service.checkout(order_id).await;
        assert!(matches!(result, Err(CheckoutError::PaymentFailed(_))));
    }
}
```

These tests run in milliseconds. No Docker. No test database. No API keys. And they test real business logic — state transitions, error handling, the works.

## Static Dispatch vs Dynamic Dispatch

You might have noticed I used `Box<dyn PaymentGateway>` above. You could also use generics:

```rust
pub struct CheckoutService<O, P, N>
where
    O: OrderRepository,
    P: PaymentGateway,
    N: NotificationService,
{
    orders: O,
    payments: P,
    notifications: N,
}
```

Static dispatch is faster — no vtable lookups. But it makes your type signatures grow exponentially as you add ports. I use dynamic dispatch (`Box<dyn Trait>`) in application services and static dispatch in hot paths like data pipelines. The performance difference in a web service is negligible.

## The Wiring Layer

The only place that knows about both domain ports and infrastructure adapters is the composition root — typically your `startup.rs`:

```rust
// src/startup.rs

pub fn build_checkout_service(config: &AppConfig, pool: PgPool) -> CheckoutService {
    let orders = Box::new(PgOrderRepository::new(pool.clone()));
    let payments: Box<dyn PaymentGateway> = match config.payment_provider.as_str() {
        "stripe" => Box::new(StripePaymentGateway::new(
            config.stripe_api_key.clone(),
        )),
        "mock" => Box::new(MockPaymentGateway::always_succeeds()),
        other => panic!("unknown payment provider: {}", other),
    };
    let notifications = Box::new(SendGridNotifications::new(
        config.sendgrid_api_key.clone(),
    ));

    CheckoutService::new(orders, payments, notifications)
}
```

This is the *one file* that ties everything together. When you want to add a new payment provider, you add a new adapter and update this match arm. Nothing else changes.

## When Hexagonal Is Too Much

I'll be straight with you: if your service has two endpoints and talks to one database, hexagonal architecture is overkill. You'll spend more time on abstractions than on the actual problem.

But the moment you need to:
- Test business logic without infrastructure
- Support multiple implementations of the same capability (e.g., local storage in dev, S3 in prod)
- Replace a third-party provider without rewriting business logic
- Onboard engineers who need clear boundaries to understand the codebase

...then ports and adapters pays for itself many times over. And Rust's trait system makes it feel like a natural extension of the language rather than a design pattern bolted on top.

Next lesson: we'll build on these boundaries to implement CQRS and event sourcing — separating your read and write paths for systems that need to scale independently.
