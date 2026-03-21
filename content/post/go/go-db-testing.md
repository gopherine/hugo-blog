---
title: "Lesson 9: Testing with Real Databases — Mocking sql.DB is lying to yourself"
author: Atharva Pandey
series: "Go Database Patterns"
lesson: 9
keywords:
  - Go
  - golang
  - database
  - postgres
  - testcontainers
  - testing
  - sqlmock
  - integration testing
  - per-test transactions
tags:
  - Go tutorial
  - golang
  - database
date: '2026-02-18T00:00:00.000Z'
---

For years, the standard advice for testing Go database code was to use `sqlmock` — a library that intercepts database calls and lets you assert which queries were run. I used it for a while. Then I started finding production bugs that my `sqlmock` tests were actively hiding: constraint violations that only happen with real Postgres, query plan differences, JSON operator behavior, NULL handling edge cases, transaction isolation behavior. `sqlmock` tests were passing while the same code was failing in production. That's worse than no tests at all.

The right way to test database code is with a real database. The friction used to be real — standing up a Postgres instance in CI was annoying. `testcontainers-go` removed that friction almost entirely. Now I'd rather run tests against a real Postgres container than maintain a mocking layer that lies to me.

## The Problem

The sqlmock approach looks like this. It's seductive because it's fast and requires no external dependencies:

```go
// WRONG — sqlmock tests don't catch real database behavior
import "github.com/DATA-DOG/go-sqlmock"

func TestCreateUser_sqlmock(t *testing.T) {
    db, mock, err := sqlmock.New()
    if err != nil {
        t.Fatal(err)
    }
    defer db.Close()

    // We're telling the mock exactly what query to expect and what to return
    // This test passes even if the SQL is completely wrong
    mock.ExpectQuery("INSERT INTO users").
        WithArgs("Alice", "alice@example.com").
        WillReturnRows(sqlmock.NewRows([]string{"id"}).AddRow(1))

    store := NewUserStore(db)
    user, err := store.Create(context.Background(), "Alice", "alice@example.com")
    if err != nil {
        t.Fatal(err)
    }
    if user.ID != 1 {
        t.Errorf("expected id 1, got %d", user.ID)
    }

    // What this test doesn't catch:
    // - The RETURNING clause being wrong
    // - A unique constraint violation on email
    // - The query not matching the actual table schema
    // - Transaction behavior
    // - Any Postgres-specific behavior whatsoever
}
```

Another common antipattern: using a SQLite in-memory database as a "drop-in replacement" for Postgres in tests. SQLite and Postgres have meaningfully different behavior — NULL handling, JSON operators, `RETURNING`, window functions, array types, advisory locks. A SQLite test suite gives you false confidence about Postgres-specific code.

```go
// WRONG — SQLite as a substitute for Postgres in tests
func TestWithSQLite(t *testing.T) {
    db, err := sql.Open("sqlite3", ":memory:")
    if err != nil {
        t.Fatal(err)
    }

    // Your schema has Postgres-specific types: JSONB, UUID, SERIAL, arrays
    // SQLite doesn't support them — you write a different schema for tests
    // Now you're testing a different database than you're running in production
    _, err = db.Exec(`CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, data TEXT)`)
    // This isn't your production schema. This test doesn't mean anything.
}
```

## The Idiomatic Way

Testcontainers-go starts a real Postgres Docker container for your tests. It handles startup, health-checking, and teardown automatically:

```go
// RIGHT — testcontainers-go for a real Postgres instance in tests
import (
    "github.com/testcontainers/testcontainers-go"
    "github.com/testcontainers/testcontainers-go/modules/postgres"
    "github.com/testcontainers/testcontainers-go/wait"
)

// TestMain sets up a shared Postgres container for the whole test package
var testDB *sql.DB

func TestMain(m *testing.M) {
    ctx := context.Background()

    pgContainer, err := postgres.RunContainer(ctx,
        testcontainers.WithImage("postgres:16-alpine"),
        postgres.WithDatabase("testdb"),
        postgres.WithUsername("testuser"),
        postgres.WithPassword("testpass"),
        testcontainers.WithWaitStrategy(
            wait.ForLog("database system is ready to accept connections").
                WithOccurrence(2).
                WithStartupTimeout(30*time.Second),
        ),
    )
    if err != nil {
        log.Fatalf("start postgres container: %v", err)
    }
    defer pgContainer.Terminate(ctx)

    connStr, err := pgContainer.ConnectionString(ctx, "sslmode=disable")
    if err != nil {
        log.Fatalf("connection string: %v", err)
    }

    testDB, err = sql.Open("postgres", connStr)
    if err != nil {
        log.Fatalf("open db: %v", err)
    }
    defer testDB.Close()

    // Run migrations against the real Postgres container
    if err := RunMigrations(connStr, "../../migrations"); err != nil {
        log.Fatalf("migrations: %v", err)
    }

    os.Exit(m.Run())
}
```

The key pattern for per-test isolation is wrapping each test in a transaction and rolling it back at the end. This is dramatically faster than dropping and recreating the schema per test:

```go
// RIGHT — per-test transaction rollback for fast, isolated tests
func newTestTx(t *testing.T) *sql.Tx {
    t.Helper()
    tx, err := testDB.BeginTx(context.Background(), nil)
    if err != nil {
        t.Fatalf("begin tx: %v", err)
    }
    t.Cleanup(func() {
        // Always rollback — even if the test panicked
        if err := tx.Rollback(); err != nil && !errors.Is(err, sql.ErrTxDone) {
            t.Logf("rollback: %v", err)
        }
    })
    return tx
}

// Tests use the transaction instead of the real DB
// Everything is rolled back after the test — database is pristine for the next test
func TestCreateUser(t *testing.T) {
    tx := newTestTx(t)
    store := NewUserStoreWithQuerier(tx) // store accepts the querier interface

    user, err := store.Create(context.Background(), "Alice", "alice@example.com")
    if err != nil {
        t.Fatalf("create user: %v", err)
    }
    if user.ID == 0 {
        t.Error("expected non-zero ID from RETURNING clause")
    }
    if user.Name != "Alice" {
        t.Errorf("expected Alice, got %s", user.Name)
    }

    // Verify the row actually exists in the database within this transaction
    var count int
    err = tx.QueryRowContext(context.Background(),
        "SELECT COUNT(*) FROM users WHERE email = $1", "alice@example.com",
    ).Scan(&count)
    if err != nil {
        t.Fatal(err)
    }
    if count != 1 {
        t.Errorf("expected 1 user, got %d", count)
    }
    // t.Cleanup rollback fires here — alice never actually committed
}

func TestCreateUser_DuplicateEmail(t *testing.T) {
    tx := newTestTx(t)
    store := NewUserStoreWithQuerier(tx)

    // First create succeeds
    _, err := store.Create(context.Background(), "Alice", "alice@example.com")
    if err != nil {
        t.Fatalf("first create: %v", err)
    }

    // Second create with same email hits the unique constraint
    _, err = store.Create(context.Background(), "Alice2", "alice@example.com")
    if !errors.Is(err, ErrEmailTaken) {
        t.Errorf("expected ErrEmailTaken, got %v", err)
    }
    // This tests real Postgres constraint behavior — sqlmock can't do this
}
```

For the per-test transaction pattern to work, your store needs to accept either `*sql.DB` or `*sql.Tx`. Use the `querier` interface pattern from Lesson 4:

```go
// RIGHT — store accepts a querier so tests can pass a transaction
type querier interface {
    QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row
    QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error)
    ExecContext(ctx context.Context, query string, args ...any) (sql.Result, error)
}

type UserStore struct {
    q querier
}

func NewUserStore(db *sql.DB) *UserStore {
    return &UserStore{q: db}
}

func NewUserStoreWithQuerier(q querier) *UserStore {
    return &UserStore{q: q}
}

// Production: NewUserStore(db)
// Tests: NewUserStoreWithQuerier(tx) — all changes roll back after test
```

## In The Wild

Loading test fixtures is cleaner with a dedicated helper that uses the transaction:

```go
// RIGHT — fixture loader that works within a test transaction
type Fixtures struct {
    tx *sql.Tx
}

func (f *Fixtures) CreateUser(t *testing.T, name, email string) *User {
    t.Helper()
    var u User
    err := f.tx.QueryRowContext(context.Background(),
        "INSERT INTO users (name, email) VALUES ($1, $2) RETURNING id, name, email, created_at",
        name, email,
    ).Scan(&u.ID, &u.Name, &u.Email, &u.CreatedAt)
    if err != nil {
        t.Fatalf("fixture CreateUser: %v", err)
    }
    return &u
}

func (f *Fixtures) CreateOrder(t *testing.T, userID, total int) *Order {
    t.Helper()
    var o Order
    err := f.tx.QueryRowContext(context.Background(),
        "INSERT INTO orders (user_id, total) VALUES ($1, $2) RETURNING id, user_id, total, created_at",
        userID, total,
    ).Scan(&o.ID, &o.UserID, &o.Total, &o.CreatedAt)
    if err != nil {
        t.Fatalf("fixture CreateOrder: %v", err)
    }
    return &o
}

// Test using fixtures
func TestGetUsersWithOrders(t *testing.T) {
    tx := newTestTx(t)
    fix := &Fixtures{tx: tx}

    alice := fix.CreateUser(t, "Alice", "alice@example.com")
    fix.CreateOrder(t, alice.ID, 100)
    fix.CreateOrder(t, alice.ID, 250)

    bob := fix.CreateUser(t, "Bob", "bob@example.com")
    // Bob has no orders

    store := NewUserStoreWithQuerier(tx)
    results, err := store.GetActiveUsersWithOrders(context.Background())
    if err != nil {
        t.Fatal(err)
    }

    // Find Alice in results
    var aliceResult *UserWithOrders
    for i := range results {
        if results[i].ID == alice.ID {
            aliceResult = &results[i]
        }
    }
    if aliceResult == nil {
        t.Fatal("alice not found in results")
    }
    if len(aliceResult.Orders) != 2 {
        t.Errorf("expected 2 orders for alice, got %d", len(aliceResult.Orders))
    }

    // Verify Bob is there with no orders
    var bobResult *UserWithOrders
    for i := range results {
        if results[i].ID == bob.ID {
            bobResult = &results[i]
        }
    }
    if bobResult == nil {
        t.Fatal("bob not found in results")
    }
    if len(bobResult.Orders) != 0 {
        t.Errorf("expected 0 orders for bob, got %d", len(bobResult.Orders))
    }
}
```

## The Gotchas

**Testcontainers requires Docker.** If your CI environment doesn't have Docker (some hosted CI services don't), testcontainers won't work. Most modern CI systems (GitHub Actions, GitLab CI, CircleCI) support Docker. If yours doesn't, an alternative is using a `DATABASE_URL` environment variable to point tests at a pre-provisioned Postgres service container.

**Container startup time.** Starting a Postgres container takes 3-10 seconds. Using `TestMain` to start one container shared across the entire test package means you pay this cost once, not once per test. Don't start a new container per test function.

**The transaction isolation gotcha.** The per-test transaction pattern means your test is always running inside an open transaction. Some database behaviors differ inside a transaction — for example, sequences return the next value and don't roll back (so IDs won't be contiguous after rollback). And if your code explicitly begins a transaction, you now have nested transactions (which Postgres doesn't support directly — it would be a savepoint). For tests that need to test your transaction code, you'll need to run those against the real DB without the wrapping transaction.

**Parallel tests with transactions.** `t.Parallel()` with per-test transactions works fine — each test has its own transaction, and they don't interfere. Just make sure you're not holding references to the parent `testDB` from within transactions.

**Why not sqlmock?** I want to be fair: `sqlmock` has legitimate uses for testing error conditions that are hard to trigger with a real database — dropped connections mid-query, specific driver errors, etc. But it should be a last resort for those edge cases, not the primary database testing strategy.

## Key Takeaway

Test your database code with a real database. `testcontainers-go` makes it easy to spin up Postgres in CI — one container per test package, started in `TestMain`. Use per-test transactions with `t.Cleanup(tx.Rollback)` for fast, isolated tests that don't interfere with each other. Use a `querier` interface to allow stores to accept either `*sql.DB` or `*sql.Tx`. Build a fixture helper to set up test data cleanly. The tests run slower than `sqlmock` — typically 2-5x — but they catch real bugs that mocks hide. That tradeoff is not even close.

---

← [Lesson 8: Migrations Without Downtime](/post/go/go-db-migrations)

🎓 **Course Complete!** You've finished the Go Database Patterns series. Head back to the [series overview](/tags/go-database-patterns) to review any lesson.
