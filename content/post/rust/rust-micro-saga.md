---
title: "Lesson 4: Saga Pattern — Distributed transactions without 2PC"
author: Atharva Pandey
series: "Rust Microservices Patterns"
lesson: 4
keywords: [Rust, rust, microservices, saga pattern, distributed transactions, compensating actions, choreography, orchestration]
tags: [Rust tutorial, rust, microservices, architecture]
date: '2025-06-07T08:48:00.000Z'
---

Here's a scenario that'll ruin your week. A customer places an order. Your order service saves it. Your payment service charges their card. Your inventory service reserves the items. Your shipping service schedules a pickup. Then the shipping service discovers the item is oversized and can't be shipped to that address.

Now what? The card's been charged. The inventory's been reserved. The order exists. You need to undo three things across three services, each with their own database, each with their own failure modes. Welcome to distributed transactions.

Traditional databases solve this with two-phase commit (2PC). The coordinator says "prepare to commit" to every participant, waits for all of them to say "ready," then says "commit." If any participant says "no," everyone rolls back. It's elegant in theory and terrible in practice — a single slow participant blocks everyone, and if the coordinator crashes between prepare and commit, you're stuck.

The saga pattern is the alternative: instead of one big atomic transaction, you run a sequence of local transactions with *compensating actions* for rollback. Each step either succeeds and triggers the next, or fails and triggers compensation for everything that already happened.

## Two Flavors: Choreography vs. Orchestration

There are two ways to coordinate a saga.

**Choreography**: Each service publishes events, and other services react. There's no central coordinator. The order of operations emerges from the event flow. It's simple for 2-3 step sagas and becomes a nightmare to debug with more.

**Orchestration**: A central saga coordinator tells each service what to do and handles the compensation flow. It's more code up front but dramatically easier to reason about and debug.

I use orchestration almost exclusively now. Here's why: when a saga fails at step 4 of 6, I want one place to look — the saga log — to understand what happened and what compensations ran. With choreography, that information is scattered across six services' logs.

## The Saga Engine

Let's build an orchestration-based saga engine in Rust. The type system is going to do serious work here.

```rust
// src/saga/mod.rs

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use std::fmt::Debug;
use uuid::Uuid;

/// A single step in a saga.
/// Each step has an action and a compensating action.
#[async_trait]
pub trait SagaStep: Send + Sync {
    /// A human-readable name for logging and debugging.
    fn name(&self) -> &str;

    /// Execute the forward action.
    /// Returns step-specific output that may be needed by later steps.
    async fn execute(&self, context: &mut SagaContext) -> Result<(), SagaStepError>;

    /// Undo the forward action.
    /// Called when a later step fails and we need to roll back.
    /// Compensations MUST be idempotent — they may be called multiple times.
    async fn compensate(&self, context: &mut SagaContext) -> Result<(), SagaStepError>;
}

/// Shared context that flows through all saga steps.
/// Each step can read data set by previous steps and write data
/// for subsequent steps.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SagaContext {
    pub saga_id: String,
    pub data: serde_json::Map<String, serde_json::Value>,
}

impl SagaContext {
    pub fn new() -> Self {
        Self {
            saga_id: Uuid::new_v4().to_string(),
            data: serde_json::Map::new(),
        }
    }

    pub fn set<V: Serialize>(&mut self, key: &str, value: &V) {
        self.data.insert(
            key.to_string(),
            serde_json::to_value(value).expect("serialization failed"),
        );
    }

    pub fn get<V: for<'de> Deserialize<'de>>(&self, key: &str) -> Option<V> {
        self.data
            .get(key)
            .and_then(|v| serde_json::from_value(v.clone()).ok())
    }
}

#[derive(Debug, thiserror::Error)]
pub enum SagaStepError {
    #[error("step failed (retriable): {0}")]
    Transient(String),
    #[error("step failed (permanent): {0}")]
    Permanent(String),
    #[error("compensation failed: {0}")]
    CompensationFailed(String),
}

/// The state of a saga at any point in time.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum SagaState {
    Running { completed_steps: usize },
    Completed,
    Compensating { from_step: usize, current_step: usize },
    CompensationFailed { step: usize, error: String },
    Failed { step: usize, error: String },
}
```

## The Saga Orchestrator

This is the brain. It runs steps forward, and if anything fails, it runs compensations backward.

```rust
// src/saga/orchestrator.rs

use super::*;
use tracing::{error, info, warn};

pub struct SagaOrchestrator {
    steps: Vec<Box<dyn SagaStep>>,
    max_retries: usize,
}

impl SagaOrchestrator {
    pub fn new() -> Self {
        Self {
            steps: Vec::new(),
            max_retries: 3,
        }
    }

    pub fn with_max_retries(mut self, retries: usize) -> Self {
        self.max_retries = retries;
        self
    }

    pub fn step(mut self, step: impl SagaStep + 'static) -> Self {
        self.steps.push(Box::new(step));
        self
    }

    /// Execute the saga. Returns the final context on success,
    /// or the error and context state on failure.
    pub async fn execute(
        &self,
        mut context: SagaContext,
    ) -> Result<SagaContext, SagaExecutionError> {
        info!(saga_id = %context.saga_id, steps = self.steps.len(), "starting saga");

        let mut completed_steps: usize = 0;

        // Forward phase: execute each step
        for (i, step) in self.steps.iter().enumerate() {
            info!(
                saga_id = %context.saga_id,
                step = step.name(),
                index = i,
                "executing saga step"
            );

            match self.execute_with_retry(step.as_ref(), &mut context).await {
                Ok(()) => {
                    completed_steps = i + 1;
                    info!(
                        saga_id = %context.saga_id,
                        step = step.name(),
                        "step completed successfully"
                    );
                }
                Err(e) => {
                    error!(
                        saga_id = %context.saga_id,
                        step = step.name(),
                        error = %e,
                        "step failed, starting compensation"
                    );

                    // Compensate all previously completed steps in reverse
                    self.compensate(&mut context, completed_steps).await?;

                    return Err(SagaExecutionError::StepFailed {
                        step_name: step.name().to_string(),
                        step_index: i,
                        error: e.to_string(),
                    });
                }
            }
        }

        info!(saga_id = %context.saga_id, "saga completed successfully");
        Ok(context)
    }

    async fn execute_with_retry(
        &self,
        step: &dyn SagaStep,
        context: &mut SagaContext,
    ) -> Result<(), SagaStepError> {
        let mut last_error = None;

        for attempt in 0..=self.max_retries {
            match step.execute(context).await {
                Ok(()) => return Ok(()),
                Err(SagaStepError::Permanent(e)) => {
                    // Don't retry permanent failures
                    return Err(SagaStepError::Permanent(e));
                }
                Err(e) => {
                    if attempt < self.max_retries {
                        warn!(
                            step = step.name(),
                            attempt = attempt + 1,
                            max_retries = self.max_retries,
                            error = %e,
                            "step failed, retrying"
                        );
                        let backoff = tokio::time::Duration::from_millis(
                            100 * 2u64.pow(attempt as u32),
                        );
                        tokio::time::sleep(backoff).await;
                    }
                    last_error = Some(e);
                }
            }
        }

        Err(last_error.unwrap())
    }

    /// Run compensating actions in reverse order.
    async fn compensate(
        &self,
        context: &mut SagaContext,
        completed_steps: usize,
    ) -> Result<(), SagaExecutionError> {
        info!(
            saga_id = %context.saga_id,
            steps_to_compensate = completed_steps,
            "starting compensation"
        );

        for i in (0..completed_steps).rev() {
            let step = &self.steps[i];
            info!(
                saga_id = %context.saga_id,
                step = step.name(),
                "compensating step"
            );

            // Compensations get multiple retries — they MUST succeed
            let mut attempts = 0;
            loop {
                match step.compensate(context).await {
                    Ok(()) => {
                        info!(
                            saga_id = %context.saga_id,
                            step = step.name(),
                            "compensation successful"
                        );
                        break;
                    }
                    Err(e) => {
                        attempts += 1;
                        if attempts >= self.max_retries * 2 {
                            error!(
                                saga_id = %context.saga_id,
                                step = step.name(),
                                error = %e,
                                "compensation failed after max retries — MANUAL INTERVENTION REQUIRED"
                            );
                            return Err(SagaExecutionError::CompensationFailed {
                                step_name: step.name().to_string(),
                                step_index: i,
                                error: e.to_string(),
                            });
                        }
                        warn!(
                            saga_id = %context.saga_id,
                            step = step.name(),
                            attempt = attempts,
                            error = %e,
                            "compensation failed, retrying"
                        );
                        tokio::time::sleep(tokio::time::Duration::from_millis(
                            200 * attempts as u64,
                        ))
                        .await;
                    }
                }
            }
        }

        info!(saga_id = %context.saga_id, "all compensations complete");
        Ok(())
    }
}

#[derive(Debug, thiserror::Error)]
pub enum SagaExecutionError {
    #[error("step '{step_name}' (index {step_index}) failed: {error}")]
    StepFailed {
        step_name: String,
        step_index: usize,
        error: String,
    },
    #[error("compensation for step '{step_name}' (index {step_index}) failed: {error}")]
    CompensationFailed {
        step_name: String,
        step_index: usize,
        error: String,
    },
}
```

## The Order Saga — Full Implementation

Now let's use this to build a real order processing saga:

```rust
// src/saga/order_saga.rs

use super::*;
use uuid::Uuid;

// --- Step 1: Reserve Inventory ---

pub struct ReserveInventoryStep {
    // inventory service client
}

#[async_trait::async_trait]
impl SagaStep for ReserveInventoryStep {
    fn name(&self) -> &str {
        "reserve_inventory"
    }

    async fn execute(&self, context: &mut SagaContext) -> Result<(), SagaStepError> {
        let order_id: String = context
            .get("order_id")
            .ok_or_else(|| SagaStepError::Permanent("missing order_id".into()))?;

        let items: Vec<serde_json::Value> = context
            .get("items")
            .ok_or_else(|| SagaStepError::Permanent("missing items".into()))?;

        // Call inventory service to reserve items
        // let reservation_id = inventory_client.reserve(&order_id, &items).await?;
        let reservation_id = Uuid::new_v4().to_string(); // placeholder

        tracing::info!(
            order_id = %order_id,
            reservation_id = %reservation_id,
            "inventory reserved"
        );

        // Store reservation ID so compensation can release it
        context.set("reservation_id", &reservation_id);

        Ok(())
    }

    async fn compensate(&self, context: &mut SagaContext) -> Result<(), SagaStepError> {
        let reservation_id: String = match context.get("reservation_id") {
            Some(id) => id,
            None => {
                // No reservation was created — nothing to compensate
                return Ok(());
            }
        };

        // Call inventory service to release reservation
        // inventory_client.release_reservation(&reservation_id).await?;

        tracing::info!(reservation_id = %reservation_id, "inventory reservation released");
        Ok(())
    }
}

// --- Step 2: Process Payment ---

pub struct ProcessPaymentStep {
    // payment service client
}

#[async_trait::async_trait]
impl SagaStep for ProcessPaymentStep {
    fn name(&self) -> &str {
        "process_payment"
    }

    async fn execute(&self, context: &mut SagaContext) -> Result<(), SagaStepError> {
        let order_id: String = context.get("order_id").unwrap();
        let amount_cents: i64 = context.get("total_cents").unwrap();
        let customer_id: String = context.get("customer_id").unwrap();

        // Call payment service
        // let payment_id = payment_client.charge(&customer_id, amount_cents).await?;
        let payment_id = Uuid::new_v4().to_string(); // placeholder

        tracing::info!(
            order_id = %order_id,
            payment_id = %payment_id,
            amount = amount_cents,
            "payment processed"
        );

        context.set("payment_id", &payment_id);
        Ok(())
    }

    async fn compensate(&self, context: &mut SagaContext) -> Result<(), SagaStepError> {
        let payment_id: String = match context.get("payment_id") {
            Some(id) => id,
            None => return Ok(()),
        };

        // Call payment service to refund
        // payment_client.refund(&payment_id).await?;

        tracing::info!(payment_id = %payment_id, "payment refunded");
        Ok(())
    }
}

// --- Step 3: Schedule Shipping ---

pub struct ScheduleShippingStep {
    // shipping service client
}

#[async_trait::async_trait]
impl SagaStep for ScheduleShippingStep {
    fn name(&self) -> &str {
        "schedule_shipping"
    }

    async fn execute(&self, context: &mut SagaContext) -> Result<(), SagaStepError> {
        let order_id: String = context.get("order_id").unwrap();

        // Call shipping service
        // This might fail if the address is invalid for certain items
        // let shipment_id = shipping_client.schedule(&order_id).await?;
        let shipment_id = Uuid::new_v4().to_string(); // placeholder

        tracing::info!(
            order_id = %order_id,
            shipment_id = %shipment_id,
            "shipping scheduled"
        );

        context.set("shipment_id", &shipment_id);
        Ok(())
    }

    async fn compensate(&self, context: &mut SagaContext) -> Result<(), SagaStepError> {
        let shipment_id: String = match context.get("shipment_id") {
            Some(id) => id,
            None => return Ok(()),
        };

        // Cancel the shipment
        // shipping_client.cancel(&shipment_id).await?;

        tracing::info!(shipment_id = %shipment_id, "shipment cancelled");
        Ok(())
    }
}

// --- Step 4: Send Confirmation ---

pub struct SendConfirmationStep {
    // notification service client
}

#[async_trait::async_trait]
impl SagaStep for SendConfirmationStep {
    fn name(&self) -> &str {
        "send_confirmation"
    }

    async fn execute(&self, context: &mut SagaContext) -> Result<(), SagaStepError> {
        let order_id: String = context.get("order_id").unwrap();
        let customer_id: String = context.get("customer_id").unwrap();

        // Send email/push notification
        // notification_client.send_order_confirmation(&customer_id, &order_id).await?;

        tracing::info!(
            order_id = %order_id,
            customer_id = %customer_id,
            "confirmation sent"
        );

        Ok(())
    }

    async fn compensate(&self, context: &mut SagaContext) -> Result<(), SagaStepError> {
        // Notifications are fire-and-forget — no compensation needed.
        // You might send a "sorry, your order was cancelled" email here,
        // but that's a new action, not a compensation.
        Ok(())
    }
}
```

## Running the Saga

```rust
// src/main.rs

use saga::orchestrator::SagaOrchestrator;
use saga::order_saga::*;
use saga::SagaContext;

async fn process_order(
    customer_id: &str,
    items: Vec<serde_json::Value>,
    total_cents: i64,
) -> Result<String, anyhow::Error> {
    let saga = SagaOrchestrator::new()
        .with_max_retries(3)
        .step(ReserveInventoryStep {})
        .step(ProcessPaymentStep {})
        .step(ScheduleShippingStep {})
        .step(SendConfirmationStep {});

    let mut context = SagaContext::new();
    context.set("order_id", &uuid::Uuid::new_v4().to_string());
    context.set("customer_id", &customer_id);
    context.set("items", &items);
    context.set("total_cents", &total_cents);

    let result = saga.execute(context).await?;

    let order_id: String = result.get("order_id").unwrap();
    Ok(order_id)
}
```

Here's what happens when shipping fails:

1. Reserve inventory — succeeds, saves `reservation_id`
2. Process payment — succeeds, saves `payment_id`
3. Schedule shipping — **fails** (oversized item, invalid address, whatever)
4. Compensation kicks in:
   - Compensate step 2: refund payment using `payment_id`
   - Compensate step 1: release reservation using `reservation_id`

The caller gets a clean error. No orphaned charges, no phantom reservations.

## Persisting Saga State

For production, you need to persist saga state. If your saga coordinator crashes between steps, you need to know where to resume.

```rust
// src/saga/persistence.rs

use sqlx::PgPool;
use super::{SagaContext, SagaState};

pub struct SagaStore {
    pool: PgPool,
}

impl SagaStore {
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }

    pub async fn save_saga(
        &self,
        saga_id: &str,
        saga_type: &str,
        state: &SagaState,
        context: &SagaContext,
    ) -> Result<(), sqlx::Error> {
        let state_json = serde_json::to_value(state).unwrap();
        let context_json = serde_json::to_value(context).unwrap();

        sqlx::query(
            r#"
            INSERT INTO sagas (id, saga_type, state, context, updated_at)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (id) DO UPDATE SET
                state = $3,
                context = $4,
                updated_at = NOW()
            "#,
        )
        .bind(saga_id)
        .bind(saga_type)
        .bind(state_json)
        .bind(context_json)
        .execute(&self.pool)
        .await?;

        Ok(())
    }

    pub async fn load_incomplete_sagas(
        &self,
    ) -> Result<Vec<(String, String, SagaState, SagaContext)>, sqlx::Error> {
        let rows: Vec<(String, String, serde_json::Value, serde_json::Value)> = sqlx::query_as(
            r#"
            SELECT id, saga_type, state, context
            FROM sagas
            WHERE state NOT IN ('Completed')
            ORDER BY updated_at ASC
            "#,
        )
        .fetch_all(&self.pool)
        .await?;

        Ok(rows
            .into_iter()
            .map(|(id, saga_type, state, context)| {
                (
                    id,
                    saga_type,
                    serde_json::from_value(state).unwrap(),
                    serde_json::from_value(context).unwrap(),
                )
            })
            .collect())
    }
}
```

On startup, your saga coordinator loads all incomplete sagas and resumes them. That's why compensations must be idempotent — a saga might compensate a step that already compensated before the crash.

## Common Mistakes

**Non-idempotent compensations.** If "refund payment" charges the customer again instead of refunding because you got the API call wrong, your saga makes things worse. Test compensations as thoroughly as forward actions.

**Missing the "nothing to compensate" case.** If step 2 fails *before* doing anything (validation error), step 1's compensation still runs. Make sure compensations handle the case where the forward action partially completed or didn't complete at all.

**Ignoring the time gap.** Between reserving inventory (step 1) and charging the card (step 2), time passes. What if the price changed? What if inventory was released by a timeout? Your saga steps need to verify preconditions, not just act blindly.

**No saga timeout.** A saga that runs forever because a step is stuck in retry loops will hold resources (reserved inventory, pending payments) indefinitely. Set a global timeout and trigger compensation if it's exceeded.

The saga pattern isn't simple. It's genuinely hard to get right. But it's the correct answer to "how do I coordinate multiple services that each own their own data?" The alternative — distributed locks and two-phase commit — is worse.

Next lesson: what happens at the infrastructure layer? We're talking service meshes.
