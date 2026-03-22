---
title: "Lesson 8: Testing with Real Databases — No more mocking SQL"
author: Atharva Pandey
series: "Rust Database Patterns"
lesson: 8
keywords: [Rust, rust, database, SQL, testing, integration tests, postgres, sqlx, test fixtures]
tags: [Rust tutorial, rust, database, postgres]
date: '2024-11-03T13:15:00.000Z'
---

I spent two days debugging a production issue where a query returned duplicate rows. The unit tests all passed — every mock returned exactly the expected data. The problem was a missing `DISTINCT` in a JOIN query that only manifested with real data containing multiple matching rows. The mocks were too perfect. They never produced the messy data that real databases contain.

That was when I stopped mocking SQL.

## The Problem with Mocking Database Calls

Mocking databases is popular because it's convenient. You don't need Docker, you don't need a test database, your tests run in milliseconds. But you're testing the wrong thing.

When you mock a database call, you're testing that your code does the right thing *assuming the query returns what you expect*. You're not testing the query itself. You're not testing:

- Whether the query is syntactically valid
- Whether it returns the correct columns
- Whether JOINs produce the right cardinality
- Whether NULL handling works as expected
- Whether your indexes are actually used
- Whether your transaction isolation is correct

You're writing tests that prove your Rust code compiles. That's not nothing, but it's not enough.

## sqlx::test — The Right Way

SQLx has built-in test infrastructure that creates a fresh database for each test, runs your migrations, and tears it down afterward:

```toml
[dev-dependencies]
sqlx = { version = "0.8", features = ["runtime-tokio", "postgres", "macros"] }
tokio = { version = "1", features = ["full"] }
```

```rust
#[cfg(test)]
mod tests {
    use sqlx::PgPool;
    use uuid::Uuid;

    #[sqlx::test(migrations = "./migrations")]
    async fn create_user_works(pool: PgPool) {
        let user = sqlx::query!(
            "INSERT INTO users (username, email) VALUES ($1, $2)
             RETURNING id, username, email, created_at",
            "testuser",
            "test@example.com"
        )
        .fetch_one(&pool)
        .await
        .unwrap();

        assert_eq!(user.username, "testuser");
        assert_eq!(user.email, "test@example.com");
        assert!(user.id != Uuid::nil());
    }

    #[sqlx::test(migrations = "./migrations")]
    async fn duplicate_username_fails(pool: PgPool) {
        // Create first user
        sqlx::query!(
            "INSERT INTO users (username, email) VALUES ($1, $2)",
            "duplicate",
            "first@example.com"
        )
        .fetch_one(&pool)
        .await
        .unwrap();

        // Try to create another with the same username
        let result = sqlx::query!(
            "INSERT INTO users (username, email) VALUES ($1, $2)",
            "duplicate",
            "second@example.com"
        )
        .fetch_one(&pool)
        .await;

        assert!(result.is_err());
    }

    #[sqlx::test(migrations = "./migrations")]
    async fn find_by_username_returns_none_for_missing(pool: PgPool) {
        let result = sqlx::query!(
            "SELECT id, username, email FROM users WHERE username = $1",
            "nonexistent"
        )
        .fetch_optional(&pool)
        .await
        .unwrap();

        assert!(result.is_none());
    }
}
```

Each test gets its own database. Test A can't pollute test B's data. Tests run in parallel safely. After the test completes, the database is dropped.

Set the `DATABASE_URL` environment variable to point at your Postgres server (the test databases are created alongside your existing databases):

```bash
DATABASE_URL=postgres://localhost/myapp cargo test
```

## Test Fixtures

Real tests need realistic data. Writing INSERT statements in every test is tedious. Build test fixtures:

```rust
#[cfg(test)]
mod fixtures {
    use sqlx::PgPool;
    use uuid::Uuid;
    use chrono::NaiveDateTime;

    pub struct TestUser {
        pub id: Uuid,
        pub username: String,
        pub email: String,
    }

    pub async fn create_test_user(pool: &PgPool, username: &str) -> TestUser {
        let email = format!("{}@test.com", username);
        let record = sqlx::query!(
            "INSERT INTO users (username, email) VALUES ($1, $2)
             RETURNING id, username, email",
            username,
            email
        )
        .fetch_one(pool)
        .await
        .unwrap();

        TestUser {
            id: record.id,
            username: record.username,
            email: record.email,
        }
    }

    pub async fn create_test_order(
        pool: &PgPool,
        user: &TestUser,
        total: i64,
        status: &str,
    ) -> Uuid {
        let record = sqlx::query!(
            "INSERT INTO orders (customer_id, total, status) VALUES ($1, $2, $3) RETURNING id",
            user.id,
            total,
            status
        )
        .fetch_one(pool)
        .await
        .unwrap();

        record.id
    }

    pub async fn seed_standard_dataset(pool: &PgPool) -> (TestUser, TestUser) {
        let alice = create_test_user(pool, "alice").await;
        let bob = create_test_user(pool, "bob").await;

        // Alice has 3 orders
        create_test_order(pool, &alice, 5000, "paid").await;
        create_test_order(pool, &alice, 3200, "shipped").await;
        create_test_order(pool, &alice, 1500, "pending").await;

        // Bob has 1 order
        create_test_order(pool, &bob, 9900, "paid").await;

        (alice, bob)
    }
}
```

Now tests are concise:

```rust
#[sqlx::test(migrations = "./migrations")]
async fn search_by_customer_returns_correct_orders(pool: PgPool) {
    let (alice, bob) = fixtures::seed_standard_dataset(&pool).await;

    let alice_orders = sqlx::query!(
        "SELECT id, total FROM orders WHERE customer_id = $1 ORDER BY total DESC",
        alice.id
    )
    .fetch_all(&pool)
    .await
    .unwrap();

    assert_eq!(alice_orders.len(), 3);
    assert_eq!(alice_orders[0].total, 5000); // Highest first
}

#[sqlx::test(migrations = "./migrations")]
async fn order_total_aggregation(pool: PgPool) {
    let (alice, _bob) = fixtures::seed_standard_dataset(&pool).await;

    let total = sqlx::query_scalar!(
        "SELECT COALESCE(SUM(total), 0) FROM orders WHERE customer_id = $1",
        alice.id
    )
    .fetch_one(&pool)
    .await
    .unwrap();

    assert_eq!(total, Some(9700)); // 5000 + 3200 + 1500
}
```

## Testing Transactions

Transaction behavior is one of the hardest things to test with mocks and one of the easiest to test with real databases:

```rust
#[sqlx::test(migrations = "./migrations")]
async fn transfer_funds_is_atomic(pool: PgPool) {
    // Setup: two accounts with known balances
    sqlx::query!(
        "INSERT INTO accounts (id, owner, balance) VALUES ($1, 'alice', 10000), ($2, 'bob', 5000)",
        Uuid::new_v4(),
        Uuid::new_v4()
    )
    .execute(&pool)
    .await
    .unwrap();

    let alice = sqlx::query!("SELECT id FROM accounts WHERE owner = 'alice'")
        .fetch_one(&pool).await.unwrap();
    let bob = sqlx::query!("SELECT id FROM accounts WHERE owner = 'bob'")
        .fetch_one(&pool).await.unwrap();

    // Transfer 3000 from Alice to Bob
    transfer_funds(&pool, alice.id, bob.id, 3000).await.unwrap();

    // Verify balances
    let alice_balance = sqlx::query_scalar!("SELECT balance FROM accounts WHERE id = $1", alice.id)
        .fetch_one(&pool).await.unwrap();
    let bob_balance = sqlx::query_scalar!("SELECT balance FROM accounts WHERE id = $1", bob.id)
        .fetch_one(&pool).await.unwrap();

    assert_eq!(alice_balance, 7000);
    assert_eq!(bob_balance, 8000);
}

#[sqlx::test(migrations = "./migrations")]
async fn transfer_insufficient_funds_rolls_back(pool: PgPool) {
    sqlx::query!(
        "INSERT INTO accounts (id, owner, balance) VALUES ($1, 'alice', 1000), ($2, 'bob', 5000)",
        Uuid::new_v4(),
        Uuid::new_v4()
    )
    .execute(&pool)
    .await
    .unwrap();

    let alice = sqlx::query!("SELECT id FROM accounts WHERE owner = 'alice'")
        .fetch_one(&pool).await.unwrap();
    let bob = sqlx::query!("SELECT id FROM accounts WHERE owner = 'bob'")
        .fetch_one(&pool).await.unwrap();

    // Try to transfer more than Alice has
    let result = transfer_funds(&pool, alice.id, bob.id, 5000).await;
    assert!(result.is_err());

    // Verify NO balances changed (rollback worked)
    let alice_balance = sqlx::query_scalar!("SELECT balance FROM accounts WHERE id = $1", alice.id)
        .fetch_one(&pool).await.unwrap();
    let bob_balance = sqlx::query_scalar!("SELECT balance FROM accounts WHERE id = $1", bob.id)
        .fetch_one(&pool).await.unwrap();

    assert_eq!(alice_balance, 1000); // Unchanged
    assert_eq!(bob_balance, 5000);   // Unchanged
}
```

Try writing these tests with mocks. You'd have to simulate transaction semantics — tracking what's been "written" and rolling it back on error. It's a reimplementation of a database inside your test suite. Absurd.

## Testing the Repository Pattern

If you built repositories (lesson 6), you can test both the interface and the implementation:

```rust
// Test the Postgres implementation against real database
#[sqlx::test(migrations = "./migrations")]
async fn pg_repo_create_and_find(pool: PgPool) {
    let repo = PgUserRepository::new(pool);

    let created = repo.create(CreateUser {
        username: "testuser".to_string(),
        email: "test@example.com".to_string(),
        bio: Some("Hello".to_string()),
    }).await.unwrap();

    let found = repo.find_by_id(created.id).await.unwrap();

    assert_eq!(found.username, "testuser");
    assert_eq!(found.email, "test@example.com");
    assert_eq!(found.bio, Some("Hello".to_string()));
}

#[sqlx::test(migrations = "./migrations")]
async fn pg_repo_duplicate_username_returns_conflict(pool: PgPool) {
    let repo = PgUserRepository::new(pool);

    repo.create(CreateUser {
        username: "taken".to_string(),
        email: "first@example.com".to_string(),
        bio: None,
    }).await.unwrap();

    let result = repo.create(CreateUser {
        username: "taken".to_string(),
        email: "second@example.com".to_string(),
        bio: None,
    }).await;

    assert!(matches!(result, Err(RepoError::Conflict(_))));
}

// Test the in-memory implementation for logic verification
#[tokio::test]
async fn inmemory_repo_create_and_find() {
    let repo = InMemoryUserRepository::new();

    let created = repo.create(CreateUser {
        username: "testuser".to_string(),
        email: "test@example.com".to_string(),
        bio: None,
    }).await.unwrap();

    let found = repo.find_by_id(created.id).await.unwrap();
    assert_eq!(found.username, "testuser");
}
```

This gives you two levels of confidence: the in-memory tests verify your business logic fast (no database round trips), and the Postgres tests verify your SQL actually works.

## Testing with Docker in CI

Your CI pipeline needs a Postgres instance. Here's a GitHub Actions setup:

```yaml
# .github/workflows/test.yml
name: Tests
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
          POSTGRES_DB: test
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable

      - name: Run tests
        env:
          DATABASE_URL: postgres://test:test@localhost/test
        run: cargo test
```

The `services` block spins up Postgres before your tests run. The health check ensures the database is ready before tests start. Each `#[sqlx::test]` creates and drops its own database within this Postgres instance.

## Property-Based Testing with Databases

For extra confidence, combine property-based testing with real databases:

```rust
#[cfg(test)]
mod property_tests {
    use proptest::prelude::*;
    use sqlx::PgPool;
    use tokio::runtime::Runtime;

    fn valid_username() -> impl Strategy<Value = String> {
        "[a-z][a-z0-9_]{2,19}".prop_map(|s| s)
    }

    fn valid_email() -> impl Strategy<Value = String> {
        "[a-z]{3,10}@[a-z]{3,10}\\.(com|org|net)".prop_map(|s| s)
    }

    // Note: proptest doesn't natively support async, so we use a runtime
    proptest! {
        #[test]
        fn created_user_can_be_retrieved(
            username in valid_username(),
            email in valid_email()
        ) {
            let rt = Runtime::new().unwrap();
            rt.block_on(async {
                let pool = PgPool::connect("postgres://localhost/test_props")
                    .await
                    .unwrap();

                sqlx::migrate!("./migrations").run(&pool).await.unwrap();

                // Clean up from previous run
                let _ = sqlx::query!("DELETE FROM users WHERE username = $1", &username)
                    .execute(&pool).await;

                let created = sqlx::query!(
                    "INSERT INTO users (username, email) VALUES ($1, $2) RETURNING id",
                    &username, &email
                )
                .fetch_one(&pool)
                .await
                .unwrap();

                let found = sqlx::query!(
                    "SELECT username, email FROM users WHERE id = $1",
                    created.id
                )
                .fetch_one(&pool)
                .await
                .unwrap();

                prop_assert_eq!(&found.username, &username);
                prop_assert_eq!(&found.email, &email);
            });
        }
    }
}
```

Property tests generate hundreds of random inputs and verify invariants hold. Combined with a real database, they catch edge cases that hand-written tests miss — weird Unicode in usernames, very long strings, empty strings hitting NOT NULL constraints.

## Performance: Keeping Database Tests Fast

Database tests are slower than unit tests. That's the tradeoff. But you can keep them reasonable:

1. **Run in parallel.** `#[sqlx::test]` creates isolated databases per test, so parallelism is safe. Cargo runs tests in parallel by default.

2. **Minimize fixture data.** Don't seed a million rows when ten will test the same logic.

3. **Separate fast and slow tests.** Use a feature flag or test module structure:

```rust
// Fast tests: always run
#[cfg(test)]
mod unit_tests {
    // In-memory repository tests, pure logic tests
}

// Slow tests: run with `cargo test -- --include-ignored` or in CI
#[cfg(test)]
mod integration_tests {
    #[sqlx::test(migrations = "./migrations")]
    #[ignore]  // Only run when explicitly requested
    async fn large_dataset_pagination(pool: PgPool) {
        // Insert 1000 rows, test pagination behavior
    }
}
```

4. **Use `UNLOGGED` tables in test schemas.** If you control the test migration, `CREATE UNLOGGED TABLE` skips WAL writes and is significantly faster for insert-heavy test fixtures. Don't do this in production migrations, obviously.

## What's Next

You've got tests that hit a real database and verify real queries. But how do you know those queries are fast? Lesson 9 covers N+1 queries, indexes, and EXPLAIN — the performance side of database work in Rust.
