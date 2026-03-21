---
title: "Lesson 3: Integration Tests — Unit tests lie, integration tests prove"
author: Atharva Pandey
series: "Go Testing Masterclass"
lesson: 3
keywords:
  - Go
  - golang
  - testing
  - integration tests
  - TestMain
  - test setup
  - test teardown
  - build tags
tags:
  - Go tutorial
  - golang
  - testing
date: '2024-08-01T00:00:00.000Z'
---

Unit tests feel productive. You write a function, you write a test, everything goes green, and you merge. But unit tests exist in a bubble — a bubble where every dependency returns exactly what you told it to return. The real world doesn't do that. Your database serializes a `time.Time` differently than you expect. Your HTTP client follows a redirect your mock never mentioned. Your message queue drops a message under backpressure that your fake queue cheerfully delivered. I've had unit test suites where every test passed and the feature didn't work in staging. Integration tests are what close that gap.

## The Problem

The most dangerous pattern I've seen is a codebase with 90% unit test coverage and no integration tests. The coverage number looks great. The confidence it represents is false.

```go
// WRONG — testing with a mock that lies about reality
type MockUserStore struct{}

func (m *MockUserStore) GetUser(id int) (*User, error) {
    return &User{ID: id, Name: "test"}, nil
}

func TestGetUserProfile(t *testing.T) {
    store := &MockUserStore{}
    svc := NewUserService(store)
    profile, err := svc.GetUserProfile(1)
    if err != nil {
        t.Fatal(err)
    }
    if profile.Name != "test" {
        t.Errorf("unexpected name: %s", profile.Name)
    }
}
```

This test passes 100% of the time, no matter what. The `MockUserStore` is a fiction you wrote. It tells `GetUserProfile` that user 1 always exists, always has the name "test", and never returns an error. What happens when you run against a real database and user 1 doesn't exist? What if `GetUser` does something unexpected with NULL columns? You don't know, because your mock told you everything was fine.

A related problem is integration tests that are too coupled to test setup and can't be selectively run:

```go
// WRONG — integration test with no way to skip in unit-test-only mode
func TestCreateOrder_Integration(t *testing.T) {
    // Assumes a running database at localhost:5432
    db, err := sql.Open("postgres", "postgres://localhost/testdb")
    if err != nil {
        t.Fatal(err) // Fails in CI if no DB is available
    }
    // ... test body
}
```

This either fails loudly when there's no database, or — worse — someone adds a `t.Skip()` that gets committed and the test never runs again.

## The Idiomatic Way

There are two idiomatic approaches. The first is build tags to separate unit and integration tests. The second is environment-variable gating. I prefer environment variables because they work without build tag bookkeeping.

```go
// RIGHT — gating integration tests behind an environment variable
func TestCreateOrder_Integration(t *testing.T) {
    if testing.Short() {
        t.Skip("skipping integration test in short mode")
    }
    dsn := os.Getenv("TEST_DATABASE_URL")
    if dsn == "" {
        t.Skip("TEST_DATABASE_URL not set, skipping integration test")
    }

    db, err := sql.Open("postgres", dsn)
    if err != nil {
        t.Fatalf("failed to open db: %v", err)
    }
    defer db.Close()

    store := NewPostgresStore(db)
    svc := NewOrderService(store)

    order, err := svc.CreateOrder(context.Background(), OrderRequest{
        UserID:   1,
        ItemID:   "sku-001",
        Quantity: 2,
    })
    if err != nil {
        t.Fatalf("CreateOrder failed: %v", err)
    }
    if order.ID == 0 {
        t.Error("expected a non-zero order ID")
    }
    if order.Status != StatusPending {
        t.Errorf("status: got %s, want %s", order.Status, StatusPending)
    }
}
```

Run unit tests with `go test ./...` or `go test -short ./...`. Run integration tests with `TEST_DATABASE_URL=postgres://localhost/testdb go test ./...`.

For shared setup across many integration tests, `TestMain` is the right tool:

```go
// RIGHT — TestMain for integration test suite setup/teardown
var testDB *sql.DB

func TestMain(m *testing.M) {
    dsn := os.Getenv("TEST_DATABASE_URL")
    if dsn == "" {
        // No database configured — run only unit tests
        os.Exit(m.Run())
    }

    var err error
    testDB, err = sql.Open("postgres", dsn)
    if err != nil {
        fmt.Fprintf(os.Stderr, "failed to connect to test database: %v\n", err)
        os.Exit(1)
    }
    defer testDB.Close()

    // Run migrations on the test database
    if err := runMigrations(testDB); err != nil {
        fmt.Fprintf(os.Stderr, "failed to run migrations: %v\n", err)
        os.Exit(1)
    }

    // Run all tests in this package
    code := m.Run()

    // Cleanup: drop all tables or truncate
    cleanupTestDB(testDB)

    os.Exit(code)
}

func TestCreateOrder(t *testing.T) {
    if testDB == nil {
        t.Skip("no test database available")
    }
    // Uses the package-level testDB set up in TestMain
    store := NewPostgresStore(testDB)
    // ... test body
}
```

`TestMain` runs once per package. It sets up expensive resources (database connections, seeded data, network services) once, then all tests in the package share them. Without it, every test that needs a DB connection opens and closes one, which is slow and brittle.

## In The Wild

One of the most valuable integration test patterns I've used is the transaction-rollback pattern for database tests:

```go
func withTestTx(t *testing.T, fn func(tx *sql.Tx)) {
    t.Helper()
    tx, err := testDB.Begin()
    if err != nil {
        t.Fatalf("begin transaction: %v", err)
    }
    // Always roll back — no state leaks between tests
    t.Cleanup(func() {
        tx.Rollback()
    })
    fn(tx)
}

func TestCreateAndFetchOrder(t *testing.T) {
    if testDB == nil {
        t.Skip("no test database available")
    }

    withTestTx(t, func(tx *sql.Tx) {
        store := NewPostgresStoreWithTx(tx)
        order, err := store.Create(context.Background(), OrderRequest{
            UserID: 1, ItemID: "sku-001", Quantity: 1,
        })
        if err != nil {
            t.Fatalf("Create: %v", err)
        }

        fetched, err := store.Get(context.Background(), order.ID)
        if err != nil {
            t.Fatalf("Get: %v", err)
        }
        if fetched.ItemID != order.ItemID {
            t.Errorf("ItemID: got %s, want %s", fetched.ItemID, order.ItemID)
        }
    })
    // Transaction rolled back — database is clean for next test
}
```

The rollback approach means integration tests never leave residue in the database. Each test runs against a clean state without you needing to write cleanup code. The rollback handles it.

## The Gotchas

**`t.Cleanup` vs `defer` inside a subtest.** Inside a `t.Run` body, `defer` runs when the function returns, but `t.Cleanup` is registered with the test and runs when the subtest finishes (which is after `t.Parallel()` subtests complete). For setup that must outlive the function body, use `t.Cleanup`.

**Shared state between tests.** If two integration tests modify the same row, they'll interfere with each other. Use unique IDs for test data — a combination of test name and a random suffix. Or use the transaction-rollback pattern above.

**Migration order matters.** When `TestMain` runs migrations on a fresh test database, make sure your migration tool is idempotent. I've been burned by migration runners that fail when a table already exists, crashing the test suite before a single test runs.

**Don't name integration tests with `_Integration` suffix as a convention.** That naming doesn't compose with the standard tooling. Use the `testing.Short()` check or environment variable guards — those integrate naturally with `go test` flags.

## Key Takeaway

Unit tests verify logic. Integration tests verify that the pieces fit together. You need both, and the boundary between them should be explicit. An integration test that runs against a real database, real file system, and real HTTP endpoints will catch a whole class of bugs that no mock ever will. The cost is that they're slower and need infrastructure. Manage that cost with `TestMain` and environment variable gating, not by skipping integration tests altogether.

---

[Course Index](/series/go-testing-masterclass) | [← Lesson 2](/post/go/go-testing-fuzzing) | [Next → Lesson 4: HTTP Handler Testing](/post/go/go-testing-http-handlers)
