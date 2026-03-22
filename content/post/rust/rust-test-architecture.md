---
title: "Lesson 12: Test Architecture — When to unit, integration, or e2e"
author: Atharva Pandey
series: "Rust Testing Masterclass"
lesson: 12
keywords: [Rust, rust, testing, test architecture, test pyramid, test strategy, unit test, integration test, e2e]
tags: [Rust tutorial, rust, testing]
date: '2024-09-08T11:10:00.000Z'
---

I inherited a Rust project with 800 tests. Running them took 45 minutes. I dug in and found: 600 unit tests that mocked every dependency (most tested nothing meaningful), 180 integration tests that duplicated what the unit tests already covered, and 20 end-to-end tests that were flaky because they hit a staging server. The project had *more tests* than any codebase I'd seen — and also *more bugs*. The volume was high, but the strategy was garbage.

Testing is architecture. Where you draw the test boundaries matters more than how many tests you write.

## The Problem

Every testing tool has a sweet spot. Unit tests are fast and precise but can't catch integration bugs. Integration tests verify real interactions but are slower and harder to debug. End-to-end tests prove the system works but are slow and brittle.

The question isn't "should I write unit tests?" — it's "for *this* piece of code, what kind of test gives me the most confidence per dollar of maintenance cost?" That calculation is different for a pure function, a database query, and a CLI tool.

## The Testing Pyramid (And Why It's Incomplete)

You've probably seen the testing pyramid: lots of unit tests at the bottom, fewer integration tests in the middle, even fewer e2e tests at the top. The idea is that fast, cheap tests form the foundation, and expensive tests are used sparingly.

This is a reasonable starting point but it breaks down in practice. Here's my version:

**Unit tests** — Test pure logic. Functions that take inputs and produce outputs without side effects. Validators, parsers, algorithms, data transformations. These should be the bulk of your tests because they're fast, deterministic, and cheap to maintain.

**Integration tests** — Test boundaries. Database queries, HTTP handlers, file I/O, message queue consumers. Anything that crosses a system boundary. Fewer of these, but each one covers more ground.

**End-to-end tests** — Test workflows. The full path from user input to final output. A CLI command that reads a file, processes it, and writes the result. An API endpoint that authenticates, queries, and responds. Very few of these, focused on critical paths.

**Property tests** — Test invariants. Sprinkle these wherever you have properties that should hold for any input. They live alongside unit tests but catch a different class of bug.

The key insight: the right test type depends on the code, not a formula.

## Structuring a Real Project

Here's how I organize tests in a serious Rust project:

```
my_project/
├── src/
│   ├── lib.rs
│   ├── domain/         # Pure business logic
│   │   ├── mod.rs
│   │   ├── order.rs    # Unit tests inline
│   │   └── pricing.rs  # Unit tests inline
│   ├── db/             # Database layer
│   │   ├── mod.rs
│   │   └── queries.rs  # Minimal unit tests, mostly integration tested
│   ├── api/            # HTTP handlers
│   │   ├── mod.rs
│   │   └── handlers.rs # Thin layer, integration tested
│   └── service/        # Orchestration
│       ├── mod.rs
│       └── order_service.rs  # Unit tests with mocks for boundaries
├── tests/
│   ├── common/
│   │   └── mod.rs      # Shared test fixtures
│   ├── api_tests.rs    # HTTP integration tests
│   └── db_tests.rs     # Database integration tests
└── benches/
    └── pricing_bench.rs
```

### Layer 1: Domain (Heavy Unit Testing)

Pure business logic gets the most unit tests. No dependencies, no mocking needed.

```rust
// src/domain/pricing.rs

#[derive(Debug, Clone, PartialEq)]
pub struct Money {
    cents: i64,
}

impl Money {
    pub fn new(cents: i64) -> Self {
        Money { cents }
    }

    pub fn from_dollars(dollars: f64) -> Self {
        Money {
            cents: (dollars * 100.0).round() as i64,
        }
    }

    pub fn cents(&self) -> i64 {
        self.cents
    }

    pub fn add(&self, other: &Money) -> Money {
        Money {
            cents: self.cents + other.cents,
        }
    }
}

#[derive(Debug, Clone)]
pub struct Discount {
    pub kind: DiscountKind,
    pub value: f64,
}

#[derive(Debug, Clone)]
pub enum DiscountKind {
    Percentage,
    FixedAmount,
}

pub fn apply_discount(price: &Money, discount: &Discount) -> Money {
    match discount.kind {
        DiscountKind::Percentage => {
            let reduction = (price.cents() as f64 * discount.value / 100.0).round() as i64;
            Money::new(price.cents() - reduction)
        }
        DiscountKind::FixedAmount => {
            let reduction = (discount.value * 100.0).round() as i64;
            let result = price.cents() - reduction;
            Money::new(if result < 0 { 0 } else { result })
        }
    }
}

pub fn calculate_tax(price: &Money, rate: f64) -> Money {
    let tax = (price.cents() as f64 * rate / 100.0).round() as i64;
    Money::new(tax)
}

pub fn calculate_total(
    items: &[(Money, u32)],
    discount: Option<&Discount>,
    tax_rate: f64,
) -> Money {
    let subtotal = items
        .iter()
        .fold(Money::new(0), |acc, (price, qty)| {
            let item_total = Money::new(price.cents() * *qty as i64);
            acc.add(&item_total)
        });

    let discounted = match discount {
        Some(d) => apply_discount(&subtotal, d),
        None => subtotal,
    };

    let tax = calculate_tax(&discounted, tax_rate);
    discounted.add(&tax)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_money_from_dollars() {
        assert_eq!(Money::from_dollars(10.50).cents(), 1050);
        assert_eq!(Money::from_dollars(0.01).cents(), 1);
        assert_eq!(Money::from_dollars(0.0).cents(), 0);
    }

    #[test]
    fn test_percentage_discount() {
        let price = Money::new(10000); // $100
        let discount = Discount {
            kind: DiscountKind::Percentage,
            value: 20.0,
        };
        assert_eq!(apply_discount(&price, &discount).cents(), 8000);
    }

    #[test]
    fn test_fixed_discount() {
        let price = Money::new(10000);
        let discount = Discount {
            kind: DiscountKind::FixedAmount,
            value: 15.0,
        };
        assert_eq!(apply_discount(&price, &discount).cents(), 8500);
    }

    #[test]
    fn test_fixed_discount_doesnt_go_negative() {
        let price = Money::new(500); // $5
        let discount = Discount {
            kind: DiscountKind::FixedAmount,
            value: 10.0, // $10 off
        };
        assert_eq!(apply_discount(&price, &discount).cents(), 0);
    }

    #[test]
    fn test_tax_calculation() {
        let price = Money::new(10000);
        assert_eq!(calculate_tax(&price, 8.5).cents(), 850);
    }

    #[test]
    fn test_total_with_discount_and_tax() {
        let items = vec![
            (Money::new(2000), 2), // $20 x 2
            (Money::new(1500), 1), // $15 x 1
        ];
        let discount = Discount {
            kind: DiscountKind::Percentage,
            value: 10.0,
        };
        // Subtotal: $55, After 10% discount: $49.50, Tax at 8%: $3.96
        // Total: $53.46
        let total = calculate_total(&items, Some(&discount), 8.0);
        assert_eq!(total.cents(), 5346);
    }

    #[test]
    fn test_total_no_discount() {
        let items = vec![(Money::new(1000), 3)];
        let total = calculate_total(&items, None, 10.0);
        // $30 + $3 tax = $33
        assert_eq!(total.cents(), 3300);
    }

    #[test]
    fn test_empty_cart() {
        let total = calculate_total(&[], None, 10.0);
        assert_eq!(total.cents(), 0);
    }
}
```

No mocks. No setup infrastructure. Just inputs and outputs. These tests run in microseconds and tell you exactly what broke.

### Layer 2: Service (Unit Tests with Mocking at Boundaries)

The service layer orchestrates domain logic and external systems. Mock the external systems, test the orchestration.

```rust
// src/service/order_service.rs

use crate::domain::pricing::{self, Discount, Money};

pub trait OrderRepository {
    fn save_order(&self, items: &[(String, Money, u32)], total: &Money) -> Result<u64, String>;
    fn find_order(&self, id: u64) -> Result<Option<OrderRecord>, String>;
}

pub trait NotificationService {
    fn send_confirmation(&self, order_id: u64, email: &str) -> Result<(), String>;
}

#[derive(Debug, Clone)]
pub struct OrderRecord {
    pub id: u64,
    pub total_cents: i64,
    pub status: String,
}

pub struct OrderService<R: OrderRepository, N: NotificationService> {
    repo: R,
    notifications: N,
    tax_rate: f64,
}

impl<R: OrderRepository, N: NotificationService> OrderService<R, N> {
    pub fn new(repo: R, notifications: N, tax_rate: f64) -> Self {
        OrderService {
            repo,
            notifications,
            tax_rate,
        }
    }

    pub fn place_order(
        &self,
        items: &[(String, Money, u32)],
        discount: Option<&Discount>,
        customer_email: &str,
    ) -> Result<u64, String> {
        if items.is_empty() {
            return Err("cannot place empty order".to_string());
        }

        let price_items: Vec<(Money, u32)> = items
            .iter()
            .map(|(_, price, qty)| (price.clone(), *qty))
            .collect();

        let total = pricing::calculate_total(&price_items, discount, self.tax_rate);

        let order_id = self.repo.save_order(items, &total)?;

        // Notification failure shouldn't fail the order
        if let Err(e) = self.notifications.send_confirmation(order_id, customer_email) {
            eprintln!("Warning: failed to send confirmation: {}", e);
        }

        Ok(order_id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct FakeRepo {
        should_fail: bool,
    }

    impl OrderRepository for FakeRepo {
        fn save_order(&self, _items: &[(String, Money, u32)], _total: &Money) -> Result<u64, String> {
            if self.should_fail {
                Err("db error".to_string())
            } else {
                Ok(42)
            }
        }

        fn find_order(&self, _id: u64) -> Result<Option<OrderRecord>, String> {
            Ok(None)
        }
    }

    struct FakeNotifier {
        should_fail: bool,
    }

    impl NotificationService for FakeNotifier {
        fn send_confirmation(&self, _order_id: u64, _email: &str) -> Result<(), String> {
            if self.should_fail {
                Err("email failed".to_string())
            } else {
                Ok(())
            }
        }
    }

    #[test]
    fn test_place_order_success() {
        let service = OrderService::new(
            FakeRepo { should_fail: false },
            FakeNotifier { should_fail: false },
            8.0,
        );

        let items = vec![("Widget".to_string(), Money::new(1000), 2)];
        let order_id = service.place_order(&items, None, "test@test.com").unwrap();
        assert_eq!(order_id, 42);
    }

    #[test]
    fn test_empty_order_rejected() {
        let service = OrderService::new(
            FakeRepo { should_fail: false },
            FakeNotifier { should_fail: false },
            8.0,
        );

        let result = service.place_order(&[], None, "test@test.com");
        assert!(result.is_err());
    }

    #[test]
    fn test_db_failure_propagated() {
        let service = OrderService::new(
            FakeRepo { should_fail: true },
            FakeNotifier { should_fail: false },
            8.0,
        );

        let items = vec![("Widget".to_string(), Money::new(1000), 1)];
        let result = service.place_order(&items, None, "test@test.com");
        assert!(result.is_err());
    }

    #[test]
    fn test_notification_failure_doesnt_fail_order() {
        let service = OrderService::new(
            FakeRepo { should_fail: false },
            FakeNotifier { should_fail: true },
            8.0,
        );

        let items = vec![("Widget".to_string(), Money::new(1000), 1)];
        let result = service.place_order(&items, None, "test@test.com");
        assert!(result.is_ok()); // order succeeds even if notification fails
    }
}
```

Notice what's being tested here: the *orchestration logic* — empty order validation, error propagation from the repo, graceful handling of notification failures. The pricing math is tested in the domain layer. The database is tested in integration tests. Each layer tests what it owns.

### Layer 3: Integration Tests (Real Boundaries)

```rust
// tests/api_tests.rs

use my_project::api;

#[test]
fn test_health_endpoint() {
    let app = api::build_app();
    let response = app.get("/health").send();
    assert_eq!(response.status(), 200);
}

#[test]
fn test_create_order_endpoint() {
    let app = api::build_test_app(); // uses in-memory database
    let response = app
        .post("/orders")
        .json(&serde_json::json!({
            "items": [
                {"name": "Widget", "price": 10.00, "quantity": 2}
            ],
            "email": "test@test.com"
        }))
        .send();

    assert_eq!(response.status(), 201);
    let body: serde_json::Value = response.json();
    assert!(body["order_id"].is_number());
}

#[test]
fn test_create_order_empty_items_rejected() {
    let app = api::build_test_app();
    let response = app
        .post("/orders")
        .json(&serde_json::json!({
            "items": [],
            "email": "test@test.com"
        }))
        .send();

    assert_eq!(response.status(), 400);
}
```

These tests hit the real HTTP layer with a real (but in-memory) database. They're slower than unit tests, but they verify that serialization, routing, middleware, and database queries all work together.

## Decision Framework

Here's my actual decision tree for "what type of test should I write?"

**Is it a pure function?** → Unit test. No mocking, no fixtures, just input/output.

**Does it coordinate multiple components?** → Unit test with mocks at the boundaries. Test the orchestration logic, not the components themselves.

**Does it cross a system boundary (database, network, filesystem)?** → Integration test with a real (or realistic) dependency. Testcontainers for databases, httptest for HTTP clients, tempdir for files.

**Is it a critical user-facing workflow?** → End-to-end test. Minimize these — they're expensive. Cover the happy path and the most important error path.

**Does it have mathematical properties?** → Property test alongside the unit tests.

**Is it a parser or deserializer?** → Fuzz test in addition to everything else.

## Anti-Patterns I've Seen

**The mock everything approach.** Every dependency is mocked, every interaction is verified. Tests pass when the implementation changes in ways that break the actual system. You're testing that code calls methods in a specific order, not that it produces correct results.

**The ice cream cone.** More e2e tests than unit tests. Everything is slow, everything is flaky, and when something fails you have no idea which component is broken.

**Testing framework code.** Tests that verify the web framework routes correctly, the ORM generates valid SQL, or the serialization library handles types properly. These libraries have their own tests. Test *your* code.

**Copy-paste test factories.** Fifty tests that each construct the same complex object graph with minor variations. Use fixtures, builders, or parametrized tests.

**Asserting on internal state.** Tests that reach into private fields to verify implementation details. Refactors break these tests even when behavior is unchanged. Test the public interface.

## How Many Tests Is Enough?

There's no formula. But here's my heuristic:

For a module with N public functions, I want:
- At least one test for the happy path of each function
- At least one test for each error path
- At least one edge case test per function (empty input, zero, boundary values)
- Property tests for functions with mathematical invariants
- Integration tests for functions that interact with external systems

If I'm confident in the test suite, I can refactor fearlessly. If I'm nervous about changing something, I'm missing tests. That feeling of nervousness is the signal.

## Wrapping Up the Course

Over twelve lessons we've covered the complete Rust testing toolkit:

- **Unit tests** for individual functions
- **Integration tests** for the public API
- **Doc tests** for living documentation
- **Fixtures** for reusable test infrastructure
- **Mocking** for isolating dependencies
- **Property testing** for finding edge cases automatically
- **Fuzz testing** for security and robustness
- **Snapshot testing** for complex output verification
- **Code coverage** for finding blind spots
- **Benchmarking** for performance verification
- **CI** for automated, reproducible testing
- **Architecture** for knowing which tool to reach for

The tools matter, but the strategy matters more. A well-designed test suite with the right tests at the right level gives you confidence to ship fast. A poorly designed one — no matter how many tests it has — just gives you a slow CI pipeline and false confidence.

Write tests that catch bugs. Delete tests that don't. Refactor fearlessly.
