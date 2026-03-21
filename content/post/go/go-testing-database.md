---
title: "Lesson 5: Database Testing with Testcontainers — Mock the DB and you mock the truth"
author: Atharva Pandey
series: "Go Testing Masterclass"
lesson: 5
keywords:
  - Go
  - golang
  - testing
  - testcontainers
  - database testing
  - PostgreSQL
  - Docker
  - integration testing
tags:
  - Go tutorial
  - golang
  - testing
date: '2024-10-10T00:00:00.000Z'
---

I used to mock databases. I had a clean `Store` interface, a `MockStore` implementation for tests, and 100% coverage on my service layer. I felt good about it. Then we migrated from MySQL to PostgreSQL and discovered that a dozen subtle behaviours we'd been mocking around were wrong — `UPSERT` semantics, NULL handling in `GROUP BY`, timestamp precision, transaction isolation differences. The mock had been lying to us for months. Testcontainers fixed that.

The idea is simple: instead of mocking your database, you run a real one in a Docker container, scoped to your test run. It starts, you run tests against it, it stops. The container is ephemeral, isolated, and identical to production.

## The Problem

The canonical "database mock" approach that breaks down in practice:

```go
// WRONG — mock store that lies about real database behavior
type MockUserStore struct {
    users map[int]*User
}

func (m *MockUserStore) CreateUser(ctx context.Context, u *User) error {
    m.users[u.ID] = u
    return nil
}

func (m *MockUserStore) GetUser(ctx context.Context, id int) (*User, error) {
    u, ok := m.users[id]
    if !ok {
        return nil, ErrNotFound
    }
    return u, nil
}

func TestCreateAndGetUser(t *testing.T) {
    store := &MockUserStore{users: make(map[int]*User)}
    svc := NewUserService(store)

    err := svc.CreateUser(ctx, &User{ID: 1, Email: "alice@example.com"})
    if err != nil {
        t.Fatal(err)
    }

    user, err := svc.GetUser(ctx, 1)
    if err != nil {
        t.Fatal(err)
    }
    // This passes, but would the real DB? What about email uniqueness constraints?
    // What about the trigger that sets created_at? What about row-level locking?
    _ = user
}
```

The mock will never tell you that your `CreateUser` SQL has a syntax error. It won't tell you that a constraint violation returns a specific error type you should be wrapping. It won't tell you that your query is doing a full table scan because you forgot to add an index. All of those things are database facts that a mock erases.

A slightly better but still problematic approach is using SQLite for tests when production runs PostgreSQL:

```go
// WRONG — SQLite in tests, PostgreSQL in production
func setupTestDB(t *testing.T) *sql.DB {
    db, err := sql.Open("sqlite3", ":memory:")
    if err != nil {
        t.Fatal(err)
    }
    db.Exec(`CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)`)
    return db
}
```

SQLite's type system, NULL semantics, and transaction behaviour differ enough from PostgreSQL that tests that pass against SQLite will fail against Postgres — and vice versa. You're not testing your database code. You're testing your code against a different database.

## The Idiomatic Way

`testcontainers-go` gives you a programmatic API to start Docker containers from your tests. The container lifecycle is tied to your test lifecycle.

First, install the dependency:

```bash
go get github.com/testcontainers/testcontainers-go
go get github.com/testcontainers/testcontainers-go/modules/postgres
```

Then write your test setup:

```go
// RIGHT — real PostgreSQL in a Docker container for tests
import (
    "context"
    "database/sql"
    "fmt"
    "testing"

    _ "github.com/lib/pq"
    "github.com/testcontainers/testcontainers-go"
    "github.com/testcontainers/testcontainers-go/modules/postgres"
    "github.com/testcontainers/testcontainers-go/wait"
)

func setupPostgres(t *testing.T) *sql.DB {
    t.Helper()
    ctx := context.Background()

    container, err := postgres.RunContainer(ctx,
        testcontainers.WithImage("postgres:16-alpine"),
        postgres.WithDatabase("testdb"),
        postgres.WithUsername("test"),
        postgres.WithPassword("test"),
        testcontainers.WithWaitStrategy(
            wait.ForLog("database system is ready to accept connections").
                WithOccurrence(2),
        ),
    )
    if err != nil {
        t.Fatalf("start postgres container: %v", err)
    }
    t.Cleanup(func() {
        if err := container.Terminate(ctx); err != nil {
            t.Logf("terminate container: %v", err)
        }
    })

    dsn, err := container.ConnectionString(ctx, "sslmode=disable")
    if err != nil {
        t.Fatalf("get connection string: %v", err)
    }

    db, err := sql.Open("postgres", dsn)
    if err != nil {
        t.Fatalf("open db: %v", err)
    }
    t.Cleanup(func() { db.Close() })

    // Run your real migrations
    if err := runMigrations(db); err != nil {
        t.Fatalf("migrations: %v", err)
    }

    return db
}

func TestCreateUser_RealDB(t *testing.T) {
    if testing.Short() {
        t.Skip("skipping database integration test")
    }

    db := setupPostgres(t)
    store := NewPostgresUserStore(db)
    ctx := context.Background()

    t.Run("creates user with generated ID", func(t *testing.T) {
        user := &User{Email: "alice@example.com", Name: "Alice"}
        err := store.CreateUser(ctx, user)
        if err != nil {
            t.Fatalf("CreateUser: %v", err)
        }
        if user.ID == 0 {
            t.Error("expected ID to be set after create")
        }
    })

    t.Run("rejects duplicate email", func(t *testing.T) {
        user1 := &User{Email: "bob@example.com", Name: "Bob"}
        user2 := &User{Email: "bob@example.com", Name: "Robert"}

        if err := store.CreateUser(ctx, user1); err != nil {
            t.Fatalf("first CreateUser: %v", err)
        }
        err := store.CreateUser(ctx, user2)
        if err == nil {
            t.Fatal("expected error for duplicate email, got nil")
        }
        if !errors.Is(err, ErrDuplicateEmail) {
            t.Errorf("expected ErrDuplicateEmail, got %v", err)
        }
    })
}
```

The `t.Cleanup` callbacks ensure the container is terminated and the database connection is closed when the test ends — whether it passes, fails, or panics.

## In The Wild

For suites with many database tests, starting one container per test is expensive. `TestMain` lets you share a container across the entire package:

```go
var sharedDB *sql.DB

func TestMain(m *testing.M) {
    if os.Getenv("CI") == "" && os.Getenv("TEST_DATABASE_URL") == "" {
        // No docker/CI environment: skip database tests
        os.Exit(m.Run())
    }

    ctx := context.Background()
    container, err := postgres.RunContainer(ctx,
        testcontainers.WithImage("postgres:16-alpine"),
        postgres.WithDatabase("testdb"),
        postgres.WithUsername("test"),
        postgres.WithPassword("test"),
        testcontainers.WithWaitStrategy(
            wait.ForLog("database system is ready to accept connections").
                WithOccurrence(2),
        ),
    )
    if err != nil {
        fmt.Fprintf(os.Stderr, "start postgres: %v\n", err)
        os.Exit(1)
    }
    defer container.Terminate(ctx)

    dsn, _ := container.ConnectionString(ctx, "sslmode=disable")
    sharedDB, err = sql.Open("postgres", dsn)
    if err != nil {
        fmt.Fprintf(os.Stderr, "open db: %v\n", err)
        os.Exit(1)
    }
    defer sharedDB.Close()

    runMigrations(sharedDB)

    os.Exit(m.Run())
}
```

Each individual test then uses a transaction and rolls it back (as shown in Lesson 3) to ensure isolation without starting a new container.

## The Gotchas

**Startup time.** Starting a PostgreSQL container takes 2-5 seconds on a modern machine. That's acceptable for an integration test suite but not for unit tests. Always gate these tests with `testing.Short()` or an environment variable.

**Docker not available.** In some CI environments, Docker isn't available (sandboxed runners, restrictive security policies). Testcontainers supports Podman and other OCI runtimes, but you'll need to configure the `TESTCONTAINERS_RYUK_DISABLED` and `DOCKER_HOST` environment variables appropriately. Have a fallback strategy — either skip the tests or point to a pre-provisioned test database.

**Image versions drift.** If your test uses `postgres:latest` and your production uses `postgres:15.3`, you might be testing against a different version than you deploy. Pin the version in tests to match production.

**Connection pool exhaustion.** If you create a new `*sql.DB` for every test without closing it (or without `t.Cleanup`), you'll exhaust the container's connection limit quickly. Always register cleanup.

## Key Takeaway

A mock database is a story you tell yourself about how your database behaves. A real database is how it actually behaves. Testcontainers bridges that gap with minimal friction — you get a real Postgres container in a few lines of Go, tied to your test lifecycle, torn down automatically. The startup cost is real but worth it. The bugs it catches — constraint violations, type coercion surprises, query plan regressions — are exactly the kind you'd otherwise find in production at the worst possible moment.

---

[Course Index](/series/go-testing-masterclass) | [← Lesson 4](/post/go/go-testing-http-handlers) | [Next → Lesson 6: Mocking Alternatives](/post/go/go-testing-mocking)
