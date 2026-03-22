---
title: "Lesson 5: Mocking — mockall and faking dependencies"
author: Atharva Pandey
series: "Rust Testing Masterclass"
lesson: 5
keywords: [Rust, rust, testing, mocking, mockall, dependency injection, traits, test doubles]
tags: [Rust tutorial, rust, testing]
date: '2024-08-19T08:15:00.000Z'
---

I spent an embarrassing amount of time early in my Rust journey trying to mock a database connection by hand. I built a fake struct, implemented the trait, tracked method calls with `RefCell<Vec<...>>` wrappers, wrote expectation-checking logic... and ended up with 200 lines of mock code to test 15 lines of business logic. Then I found `mockall` and felt like an idiot.

## The Problem

Real code has dependencies. Your payment processor calls Stripe. Your user service queries a database. Your notification system sends emails. You can't — and shouldn't — hit these real services in unit tests. They're slow, flaky, expensive, and non-deterministic.

The solution is test doubles: objects that stand in for real dependencies. But Rust's type system makes this trickier than in dynamic languages. You can't just monkey-patch a function or pass a dictionary where an object is expected. You need traits, and you need something to generate implementations of those traits.

## Design for Testability First

Before we touch any mocking library, here's the most important lesson: **if your code is hard to mock, the problem is your code, not the mocking tool.**

The key principle is dependency injection through traits. Instead of hardcoding a concrete type, accept a trait.

```rust
// BAD: hardcoded dependency, impossible to test without a real database
fn get_user_email(user_id: u64) -> String {
    let db = Database::connect("postgres://prod/main"); // yikes
    db.query("SELECT email FROM users WHERE id = $1", &[&user_id])
}

// GOOD: dependency injected through a trait
trait UserRepository {
    fn find_email(&self, user_id: u64) -> Result<String, RepositoryError>;
}

fn get_user_email(repo: &dyn UserRepository, user_id: u64) -> Result<String, RepositoryError> {
    repo.find_email(user_id)
}
```

Now you can pass a real database in production and a fake in tests. The function doesn't know or care which one it gets.

## Manual Mocks

You can always build mocks by hand. For simple traits, this is perfectly reasonable.

```rust
use std::fmt;

#[derive(Debug)]
struct ServiceError(String);

impl fmt::Display for ServiceError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl std::error::Error for ServiceError {}

trait EmailSender {
    fn send(&self, to: &str, subject: &str, body: &str) -> Result<(), ServiceError>;
}

struct NotificationService<E: EmailSender> {
    sender: E,
}

impl<E: EmailSender> NotificationService<E> {
    fn new(sender: E) -> Self {
        NotificationService { sender }
    }

    fn notify_user(&self, email: &str, message: &str) -> Result<(), ServiceError> {
        self.sender.send(email, "Notification", message)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    struct FakeEmailSender {
        sent: RefCell<Vec<(String, String, String)>>,
        should_fail: bool,
    }

    impl FakeEmailSender {
        fn new() -> Self {
            FakeEmailSender {
                sent: RefCell::new(Vec::new()),
                should_fail: false,
            }
        }

        fn failing() -> Self {
            FakeEmailSender {
                sent: RefCell::new(Vec::new()),
                should_fail: true,
            }
        }

        fn sent_count(&self) -> usize {
            self.sent.borrow().len()
        }

        fn last_recipient(&self) -> Option<String> {
            self.sent.borrow().last().map(|(to, _, _)| to.clone())
        }
    }

    impl EmailSender for FakeEmailSender {
        fn send(&self, to: &str, subject: &str, body: &str) -> Result<(), ServiceError> {
            if self.should_fail {
                return Err(ServiceError("send failed".to_string()));
            }
            self.sent.borrow_mut().push((
                to.to_string(),
                subject.to_string(),
                body.to_string(),
            ));
            Ok(())
        }
    }

    #[test]
    fn test_notify_sends_email() {
        let sender = FakeEmailSender::new();
        let service = NotificationService::new(sender);

        service.notify_user("user@test.com", "Hello!").unwrap();

        assert_eq!(service.sender.sent_count(), 1);
        assert_eq!(service.sender.last_recipient(), Some("user@test.com".to_string()));
    }

    #[test]
    fn test_notify_propagates_errors() {
        let sender = FakeEmailSender::failing();
        let service = NotificationService::new(sender);

        let result = service.notify_user("user@test.com", "Hello!");
        assert!(result.is_err());
    }
}
```

This works fine. But notice how much boilerplate the `FakeEmailSender` requires — the struct definition, the `RefCell` wrapper for tracking calls, the constructor variants, the trait implementation. For a trait with five methods, this gets ugly fast.

## mockall: Automated Mock Generation

`mockall` generates all that boilerplate for you. Add it to your dev dependencies:

```toml
[dev-dependencies]
mockall = "0.13"
```

### Basic Usage

```rust
use mockall::automock;
use std::fmt;

#[derive(Debug)]
struct RepoError(String);

impl fmt::Display for RepoError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl std::error::Error for RepoError {}

#[automock]
trait UserRepository {
    fn find_by_id(&self, id: u64) -> Result<String, RepoError>;
    fn save(&self, name: &str) -> Result<u64, RepoError>;
    fn delete(&self, id: u64) -> Result<(), RepoError>;
}

struct UserService<R: UserRepository> {
    repo: R,
}

impl<R: UserRepository> UserService<R> {
    fn new(repo: R) -> Self {
        UserService { repo }
    }

    fn get_username(&self, id: u64) -> Result<String, RepoError> {
        self.repo.find_by_id(id)
    }

    fn create_user(&self, name: &str) -> Result<u64, RepoError> {
        if name.is_empty() {
            return Err(RepoError("name cannot be empty".to_string()));
        }
        self.repo.save(name)
    }

    fn remove_user(&self, id: u64) -> Result<(), RepoError> {
        // Check user exists first
        self.repo.find_by_id(id)?;
        self.repo.delete(id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_get_username() {
        let mut mock = MockUserRepository::new();
        mock.expect_find_by_id()
            .with(mockall::predicate::eq(42))
            .times(1)
            .returning(|_| Ok("Alice".to_string()));

        let service = UserService::new(mock);
        let name = service.get_username(42).unwrap();
        assert_eq!(name, "Alice");
    }

    #[test]
    fn test_create_user_validates_name() {
        let mock = MockUserRepository::new();
        // save() should never be called
        let service = UserService::new(mock);
        let result = service.create_user("");
        assert!(result.is_err());
    }

    #[test]
    fn test_create_user_calls_save() {
        let mut mock = MockUserRepository::new();
        mock.expect_save()
            .with(mockall::predicate::eq("Bob"))
            .times(1)
            .returning(|_| Ok(123));

        let service = UserService::new(mock);
        let id = service.create_user("Bob").unwrap();
        assert_eq!(id, 123);
    }

    #[test]
    fn test_remove_user_checks_existence() {
        let mut mock = MockUserRepository::new();
        mock.expect_find_by_id()
            .with(mockall::predicate::eq(99))
            .times(1)
            .returning(|_| Err(RepoError("not found".to_string())));
        // delete should never be called since find_by_id fails

        let service = UserService::new(mock);
        let result = service.remove_user(99);
        assert!(result.is_err());
    }

    #[test]
    fn test_remove_user_success() {
        let mut mock = MockUserRepository::new();
        mock.expect_find_by_id()
            .returning(|_| Ok("Alice".to_string()));
        mock.expect_delete()
            .with(mockall::predicate::eq(42))
            .times(1)
            .returning(|_| Ok(()));

        let service = UserService::new(mock);
        service.remove_user(42).unwrap();
    }
}
```

The `#[automock]` attribute generates a `MockUserRepository` struct with `expect_*` methods for each trait method. You set expectations — what arguments to expect, how many times, what to return — and the mock verifies them.

### Expectations in Detail

**Argument matching:**

```rust
use mockall::predicate::*;

// Exact match
mock.expect_find_by_id()
    .with(eq(42))
    .returning(|_| Ok("Alice".to_string()));

// Any argument
mock.expect_find_by_id()
    .withf(|id| *id > 0)
    .returning(|_| Ok("Someone".to_string()));

// Custom predicate
mock.expect_save()
    .withf(|name: &str| name.starts_with("user_"))
    .returning(|_| Ok(1));
```

**Call counts:**

```rust
// Exactly once
mock.expect_delete().times(1).returning(|_| Ok(()));

// At least 2 times
mock.expect_find_by_id().times(2..).returning(|_| Ok("x".into()));

// Between 1 and 5
mock.expect_save().times(1..=5).returning(|_| Ok(1));

// Never (will panic if called)
mock.expect_delete().never();
```

**Sequential returns:**

```rust
let mut mock = MockUserRepository::new();
let mut seq = mockall::Sequence::new();

mock.expect_find_by_id()
    .times(1)
    .in_sequence(&mut seq)
    .returning(|_| Err(RepoError("not found".into())));

mock.expect_find_by_id()
    .times(1)
    .in_sequence(&mut seq)
    .returning(|_| Ok("Alice".into()));

// First call returns error, second returns Ok
```

## When NOT to Mock

I have a strong opinion here: **most code doesn't need mocking.**

Mocking is for external boundaries — network calls, databases, file systems, third-party APIs. If you're mocking internal structs that you control, you're probably testing at the wrong level.

Here's my decision framework:

- **Pure functions** — No mocking needed. Pass input, check output.
- **Functions with injected dependencies** — Mock the dependency trait.
- **Functions that call other functions you own** — Don't mock. Test the outer function directly.
- **Database/API calls** — Mock the trait. Or better, use integration tests with a real test database.

Over-mocking creates brittle tests that break every time you refactor internals, even when the behavior is unchanged. I've seen teams where mocks took more effort to maintain than the production code they were testing.

## The trait-based approach vs. generics

One pattern decision you'll face: `dyn Trait` vs generics.

```rust
// Generic approach — zero-cost, but monomorphized
struct Service<R: Repository> {
    repo: R,
}

// Trait object approach — tiny runtime cost, more flexible
struct Service {
    repo: Box<dyn Repository>,
}
```

For production code, I usually prefer generics. For testing, both work fine with mockall. The generic approach avoids the allocation overhead of `Box`, but in tests that overhead is irrelevant.

## A Complete Example

Here's a more realistic scenario — a payment processing service:

```rust
use mockall::automock;
use std::fmt;

#[derive(Debug, Clone)]
pub struct PaymentResult {
    pub transaction_id: String,
    pub status: String,
}

#[derive(Debug)]
pub struct PaymentError(pub String);

impl fmt::Display for PaymentError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "payment error: {}", self.0)
    }
}

impl std::error::Error for PaymentError {}

#[automock]
pub trait PaymentGateway {
    fn charge(&self, amount_cents: u64, card_token: &str) -> Result<PaymentResult, PaymentError>;
    fn refund(&self, transaction_id: &str) -> Result<(), PaymentError>;
}

#[automock]
pub trait AuditLog {
    fn record(&self, event: &str, details: &str);
}

pub struct PaymentService<G: PaymentGateway, A: AuditLog> {
    gateway: G,
    audit: A,
}

impl<G: PaymentGateway, A: AuditLog> PaymentService<G, A> {
    pub fn new(gateway: G, audit: A) -> Self {
        PaymentService { gateway, audit }
    }

    pub fn process_payment(
        &self,
        amount_cents: u64,
        card_token: &str,
    ) -> Result<PaymentResult, PaymentError> {
        if amount_cents == 0 {
            return Err(PaymentError("amount must be positive".to_string()));
        }

        let result = self.gateway.charge(amount_cents, card_token)?;
        self.audit.record("payment", &format!("charged {} cents, txn: {}", amount_cents, result.transaction_id));
        Ok(result)
    }

    pub fn refund_payment(&self, transaction_id: &str) -> Result<(), PaymentError> {
        self.gateway.refund(transaction_id)?;
        self.audit.record("refund", &format!("refunded txn: {}", transaction_id));
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use mockall::predicate::*;

    #[test]
    fn test_successful_payment() {
        let mut gateway = MockPaymentGateway::new();
        gateway.expect_charge()
            .with(eq(1000), eq("tok_test"))
            .times(1)
            .returning(|_, _| Ok(PaymentResult {
                transaction_id: "txn_123".to_string(),
                status: "success".to_string(),
            }));

        let mut audit = MockAuditLog::new();
        audit.expect_record()
            .times(1)
            .returning(|_, _| ());

        let service = PaymentService::new(gateway, audit);
        let result = service.process_payment(1000, "tok_test").unwrap();
        assert_eq!(result.transaction_id, "txn_123");
    }

    #[test]
    fn test_zero_amount_rejected_without_gateway_call() {
        let gateway = MockPaymentGateway::new();
        // No expectations set — if charge() is called, mock panics
        let audit = MockAuditLog::new();

        let service = PaymentService::new(gateway, audit);
        let result = service.process_payment(0, "tok_test");
        assert!(result.is_err());
    }

    #[test]
    fn test_gateway_failure_propagated() {
        let mut gateway = MockPaymentGateway::new();
        gateway.expect_charge()
            .returning(|_, _| Err(PaymentError("card declined".to_string())));

        let audit = MockAuditLog::new();
        // audit.record should NOT be called since payment failed

        let service = PaymentService::new(gateway, audit);
        let err = service.process_payment(500, "tok_bad").unwrap_err();
        assert!(err.0.contains("declined"));
    }
}
```

Clean, readable, and each test verifies exactly one behavior. The mocks handle the infrastructure; the assertions verify the business logic.

## What's Next

Mocking gives you control over dependencies. But what about finding bugs you didn't think to test for? Property-based testing generates thousands of random inputs and checks that your code's invariants hold. That's a fundamentally different approach, and it catches bugs that hand-written tests miss. We'll cover it next with `proptest`.
