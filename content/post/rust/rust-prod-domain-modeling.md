---
title: "Lesson 2: Domain Modeling with Rust's Type System — Making impossible states impossible"
author: Atharva Pandey
series: "Rust in Production — Real-World Architecture"
lesson: 2
keywords: [Rust, rust, architecture, production, domain modeling, type system, newtype, state machines]
tags: [Rust tutorial, rust, architecture, production]
date: '2025-10-17T14:38:00.000Z'
---

We shipped a bug to production that cost us about three hours of incident response and a very uncomfortable Slack thread. The root cause? Someone passed a `user_id` where an `order_id` was expected. Both were `String`. Both were UUIDs. The compiler had no way to tell them apart. The function signature said `fn cancel_order(order_id: String, user_id: String)`, and someone called it with the arguments flipped.

This is the kind of bug that makes you rethink everything. Not because it's complex — because it's *stupid*. And stupid bugs that slip through a strong type system mean the type system wasn't being used right.

Rust gives you the most expressive type system of any mainstream systems language. If you're using `String` for everything and `bool` for state flags, you're leaving half the compiler's power on the table.

## The Newtype Pattern — Your First Line of Defense

The fix for the `user_id`/`order_id` mixup is almost embarrassingly simple:

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct UserId(uuid::Uuid);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct OrderId(uuid::Uuid);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Email(String);

#[derive(Debug, Clone, PartialEq)]
pub struct Money {
    cents: i64,
    currency: Currency,
}
```

Now `fn cancel_order(order_id: OrderId, user_id: UserId)` won't even compile if you swap the arguments. Zero runtime cost. Infinite compile-time safety.

But here's where most tutorials stop — they show you the newtype and move on. Let's talk about what makes this *actually* useful in production.

### Validated Newtypes

A newtype that wraps any `String` without validation is better than a raw `String`, but not by much. The real win comes from making construction fallible:

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Email(String);

impl Email {
    pub fn parse(raw: &str) -> Result<Self, DomainError> {
        let trimmed = raw.trim().to_lowercase();

        if trimmed.is_empty() {
            return Err(DomainError::Validation("email cannot be empty".into()));
        }

        let parts: Vec<&str> = trimmed.splitn(2, '@').collect();
        if parts.len() != 2 || parts[0].is_empty() || parts[1].is_empty() {
            return Err(DomainError::Validation(
                format!("'{}' is not a valid email", raw)
            ));
        }

        if !parts[1].contains('.') {
            return Err(DomainError::Validation(
                format!("'{}' has no valid domain", raw)
            ));
        }

        Ok(Self(trimmed))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }

    pub fn domain(&self) -> &str {
        self.0.split('@').nth(1).unwrap() // safe: validated at construction
    }
}
```

The `unwrap()` in `domain()` looks scary, but it's *proven safe* by the constructor. If you have an `Email`, it has an `@` sign — period. That invariant is established once and trusted everywhere.

This is what I mean by "making impossible states impossible." There's no `Email` in your system that doesn't contain a valid email address. You can't forget to validate. You can't skip the check in one code path and not another.

### The Money Problem

If your system handles money (and most do), this pattern is essential:

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Currency {
    Usd,
    Eur,
    Gbp,
    Jpy,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Money {
    /// Amount in smallest currency unit (cents, pence, yen)
    cents: i64,
    currency: Currency,
}

impl Money {
    pub fn new(cents: i64, currency: Currency) -> Self {
        Self { cents, currency }
    }

    pub fn usd(cents: i64) -> Self {
        Self::new(cents, Currency::Usd)
    }

    pub fn add(&self, other: &Money) -> Result<Money, DomainError> {
        if self.currency != other.currency {
            return Err(DomainError::CurrencyMismatch {
                expected: self.currency,
                got: other.currency,
            });
        }
        Ok(Money::new(
            self.cents.checked_add(other.cents)
                .ok_or(DomainError::Overflow)?,
            self.currency,
        ))
    }

    pub fn multiply(&self, quantity: i64) -> Result<Money, DomainError> {
        Ok(Money::new(
            self.cents.checked_mul(quantity)
                .ok_or(DomainError::Overflow)?,
            self.currency,
        ))
    }

    pub fn is_positive(&self) -> bool {
        self.cents > 0
    }
}
```

You literally cannot add USD to EUR. The compiler won't let you. Compare this to the Go/Java approach where money is an `int64` and currency is a separate field that you *hope* someone checks.

## Enums as State Machines

This is where Rust's type system really shines. Most languages model state with a string field or an enum — but they still let you access fields that don't make sense in a given state. Rust doesn't.

Consider an order lifecycle:

```rust
pub enum Order {
    Draft(DraftOrder),
    Placed(PlacedOrder),
    Paid(PaidOrder),
    Shipped(ShippedOrder),
    Delivered(DeliveredOrder),
    Cancelled(CancelledOrder),
}

pub struct DraftOrder {
    pub id: OrderId,
    pub customer_id: UserId,
    pub items: Vec<LineItem>,
    pub created_at: DateTime<Utc>,
}

pub struct PlacedOrder {
    pub id: OrderId,
    pub customer_id: UserId,
    pub items: Vec<LineItem>,
    pub total: Money,
    pub placed_at: DateTime<Utc>,
}

pub struct PaidOrder {
    pub id: OrderId,
    pub customer_id: UserId,
    pub items: Vec<LineItem>,
    pub total: Money,
    pub placed_at: DateTime<Utc>,
    pub payment_id: PaymentId,
    pub paid_at: DateTime<Utc>,
}

pub struct ShippedOrder {
    pub id: OrderId,
    pub customer_id: UserId,
    pub items: Vec<LineItem>,
    pub total: Money,
    pub tracking_number: TrackingNumber,
    pub shipped_at: DateTime<Utc>,
}

pub struct CancelledOrder {
    pub id: OrderId,
    pub customer_id: UserId,
    pub reason: CancellationReason,
    pub cancelled_at: DateTime<Utc>,
}

pub struct DeliveredOrder {
    pub id: OrderId,
    pub customer_id: UserId,
    pub items: Vec<LineItem>,
    pub total: Money,
    pub tracking_number: TrackingNumber,
    pub delivered_at: DateTime<Utc>,
}
```

Now here's the magic — the state transitions are methods that *consume* the old state and *produce* the new one:

```rust
impl DraftOrder {
    pub fn place(self) -> Result<PlacedOrder, DomainError> {
        if self.items.is_empty() {
            return Err(DomainError::EmptyOrder);
        }

        let total = self.items.iter()
            .try_fold(Money::usd(0), |acc, item| {
                let line_total = item.price.multiply(item.quantity as i64)?;
                acc.add(&line_total)
            })?;

        Ok(PlacedOrder {
            id: self.id,
            customer_id: self.customer_id,
            items: self.items,
            total,
            placed_at: Utc::now(),
        })
    }
}

impl PlacedOrder {
    pub fn pay(self, payment_id: PaymentId) -> PaidOrder {
        PaidOrder {
            id: self.id,
            customer_id: self.customer_id,
            items: self.items,
            total: self.total,
            placed_at: self.placed_at,
            payment_id,
            paid_at: Utc::now(),
        }
    }

    pub fn cancel(self, reason: CancellationReason) -> CancelledOrder {
        CancelledOrder {
            id: self.id,
            customer_id: self.customer_id,
            reason,
            cancelled_at: Utc::now(),
        }
    }
}

impl PaidOrder {
    pub fn ship(self, tracking_number: TrackingNumber) -> ShippedOrder {
        ShippedOrder {
            id: self.id,
            customer_id: self.customer_id,
            items: self.items,
            total: self.total,
            tracking_number,
            shipped_at: Utc::now(),
        }
    }
}
```

Think about what this buys you:

- You **cannot** ship an order that hasn't been paid. There's no `ShippedOrder::from(DraftOrder)` — it doesn't exist.
- You **cannot** access `tracking_number` on a `DraftOrder`. The field literally doesn't exist on that struct.
- You **cannot** cancel a delivered order. There's no `cancel` method on `DeliveredOrder`.
- If you add a new state, the compiler tells you everywhere you forgot to handle it via exhaustive `match`.

Compare this to the typical approach in other languages — an `order.status` string field and an `order.tracking_number` that's `Option<String>` and might or might not be set depending on runtime state.

## The Builder Pattern for Complex Construction

When an entity has many fields and some are optional, the builder pattern shines:

```rust
pub struct OrderBuilder {
    customer_id: UserId,
    items: Vec<LineItem>,
    notes: Option<String>,
    shipping_preference: ShippingPreference,
}

impl OrderBuilder {
    pub fn new(customer_id: UserId) -> Self {
        Self {
            customer_id,
            items: Vec::new(),
            notes: None,
            shipping_preference: ShippingPreference::Standard,
        }
    }

    pub fn add_item(mut self, item: LineItem) -> Self {
        self.items.push(item);
        self
    }

    pub fn notes(mut self, notes: impl Into<String>) -> Self {
        self.notes = Some(notes.into());
        self
    }

    pub fn express_shipping(mut self) -> Self {
        self.shipping_preference = ShippingPreference::Express;
        self
    }

    pub fn build(self) -> Result<DraftOrder, DomainError> {
        if self.items.is_empty() {
            return Err(DomainError::Validation(
                "order must have at least one item".into()
            ));
        }

        Ok(DraftOrder {
            id: OrderId::new(),
            customer_id: self.customer_id,
            items: self.items,
            created_at: Utc::now(),
        })
    }
}
```

Validation happens at `build()` time. Once you have a `DraftOrder`, it's valid.

## Making It Work with Databases

"This is great in theory, but how do I store a `ShippedOrder` in Postgres?"

Fair question. You need a mapping layer. I typically use a flat database row and convert:

```rust
#[derive(sqlx::FromRow)]
struct OrderRow {
    id: uuid::Uuid,
    customer_id: uuid::Uuid,
    status: String,
    items_json: serde_json::Value,
    total_cents: Option<i64>,
    currency: Option<String>,
    payment_id: Option<uuid::Uuid>,
    tracking_number: Option<String>,
    placed_at: Option<DateTime<Utc>>,
    paid_at: Option<DateTime<Utc>>,
    shipped_at: Option<DateTime<Utc>>,
    delivered_at: Option<DateTime<Utc>>,
    cancelled_at: Option<DateTime<Utc>>,
    cancellation_reason: Option<String>,
    created_at: DateTime<Utc>,
}

impl TryFrom<OrderRow> for Order {
    type Error = DomainError;

    fn try_from(row: OrderRow) -> Result<Self, Self::Error> {
        let id = OrderId::from_uuid(row.id);
        let customer_id = UserId::from_uuid(row.customer_id);
        let items: Vec<LineItem> = serde_json::from_value(row.items_json)
            .map_err(|e| DomainError::Deserialization(e.to_string()))?;

        match row.status.as_str() {
            "draft" => Ok(Order::Draft(DraftOrder {
                id,
                customer_id,
                items,
                created_at: row.created_at,
            })),
            "placed" => Ok(Order::Placed(PlacedOrder {
                id,
                customer_id,
                items,
                total: Money::new(
                    row.total_cents.ok_or(DomainError::MissingField("total_cents"))?,
                    parse_currency(&row.currency.ok_or(DomainError::MissingField("currency"))?)?,
                ),
                placed_at: row.placed_at.ok_or(DomainError::MissingField("placed_at"))?,
            })),
            "shipped" => Ok(Order::Shipped(ShippedOrder {
                id,
                customer_id,
                items,
                total: Money::new(
                    row.total_cents.ok_or(DomainError::MissingField("total_cents"))?,
                    parse_currency(&row.currency.ok_or(DomainError::MissingField("currency"))?)?,
                ),
                tracking_number: TrackingNumber::parse(
                    &row.tracking_number.ok_or(DomainError::MissingField("tracking_number"))?
                )?,
                shipped_at: row.shipped_at.ok_or(DomainError::MissingField("shipped_at"))?,
            })),
            // ... other states
            unknown => Err(DomainError::UnknownStatus(unknown.to_string())),
        }
    }
}
```

Yes, this is more code than a single struct with `Option` fields everywhere. But every `Option` in the flat approach is a potential `unwrap()` panic waiting to happen. The enum approach surfaces those invariants at the type level.

## When This Is Overkill

I want to be honest here — this level of domain modeling isn't always worth it. If you're building a CRUD app with five endpoints and no complex business logic, a flat struct with `serde` derives is fine. Don't build a cathedral to serve sandwiches.

But the moment you have:
- State transitions that need to be enforced
- Domain invariants that go beyond "this field is required"
- Multiple teams working on the same domain
- Business rules that change every quarter

...then encoding those rules in the type system pays for itself in the first month. Bugs you never have to debug. Invalid states you never have to handle. Edge cases that literally cannot exist.

The compiler becomes your QA team, and unlike humans, it never misses a case.

Next up: we'll take these domain modeling concepts and layer them into a hexagonal architecture — ports, adapters, and clean boundaries that let you swap infrastructure without touching business logic.
