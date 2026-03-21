---
title: "Lesson 1: database/sql Done Right — The stdlib is better than you think"
author: Atharva Pandey
series: "Go Database Patterns"
lesson: 1
keywords:
  - Go
  - golang
  - database
  - postgres
  - database/sql
  - connection pool
  - sql.Open
  - QueryRow
tags:
  - Go tutorial
  - golang
  - database
date: '2025-07-08T00:00:00.000Z'
---

I spent a long time reaching for third-party database libraries in Go before I actually read the `database/sql` docs. When I finally did, I was embarrassed — the stdlib had everything I needed and I'd been adding dependencies for no reason. The problem isn't that `database/sql` is limited. The problem is that it has a handful of non-obvious behaviors that, if you don't know about them, will burn you in production. Once you internalize those, you'll write better database code than most people using ORMs.

Let me walk you through the parts that trip everyone up.

## The Problem

The single most common mistake I see is treating `sql.Open` like it's actually opening a connection:

```go
// WRONG — assuming sql.Open establishes a connection
func main() {
    db, err := sql.Open("postgres", "postgres://user:pass@localhost/mydb")
    if err != nil {
        log.Fatal("failed to connect:", err)
    }
    defer db.Close()

    // Now we query — but we've never verified the connection works
    rows, err := db.Query("SELECT id, name FROM users")
    // This is where you first find out the DSN was wrong
}
```

`sql.Open` validates the driver arguments and creates a `*sql.DB` handle — it does not open a connection. The actual connections are lazy. If your DSN is wrong, your credentials are bad, or Postgres isn't running, you won't find out until the first query. In a service that starts and registers routes before touching the database, this means you'll serve traffic before discovering that your database connection is broken.

The second common mistake is ignoring `rows.Close()`:

```go
// WRONG — forgetting to close rows keeps connections held open
func getUserNames(db *sql.DB) ([]string, error) {
    rows, err := db.Query("SELECT name FROM users")
    if err != nil {
        return nil, err
    }
    // Missing: defer rows.Close()

    var names []string
    for rows.Next() {
        var name string
        if err := rows.Scan(&name); err != nil {
            return nil, err // connection leaked here — rows never closed
        }
        names = append(names, name)
    }
    return names, nil
}
```

When you return early from an error in `rows.Scan` without calling `rows.Close()`, the underlying connection stays checked out from the pool. Do this enough times and you've exhausted your pool. Under load, every request that hits this code path leaks a connection until the pool is full and new requests start timing out.

## The Idiomatic Way

Fix `sql.Open` by immediately pinging the database to verify connectivity:

```go
// RIGHT — ping at startup to fail fast on bad config
func NewDB(dsn string) (*sql.DB, error) {
    db, err := sql.Open("postgres", dsn)
    if err != nil {
        return nil, fmt.Errorf("sql.Open: %w", err)
    }

    // Verify the connection actually works before returning
    ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
    defer cancel()

    if err := db.PingContext(ctx); err != nil {
        db.Close()
        return nil, fmt.Errorf("db.Ping: %w", err)
    }

    // Set pool parameters (we'll cover these in depth in Lesson 2)
    db.SetMaxOpenConns(25)
    db.SetMaxIdleConns(25)
    db.SetConnMaxLifetime(5 * time.Minute)

    return db, nil
}
```

Now if your DSN is wrong, your service fails at startup rather than silently degrading under load. That's the behavior you want — fail fast, fail loud.

For `rows`, the pattern is always `defer rows.Close()` immediately after checking the error:

```go
// RIGHT — defer rows.Close immediately, check rows.Err() at the end
func getUserNames(db *sql.DB, ctx context.Context) ([]string, error) {
    rows, err := db.QueryContext(ctx, "SELECT name FROM users ORDER BY name")
    if err != nil {
        return nil, fmt.Errorf("query: %w", err)
    }
    defer rows.Close() // always, unconditionally, right here

    var names []string
    for rows.Next() {
        var name string
        if err := rows.Scan(&name); err != nil {
            return nil, fmt.Errorf("scan: %w", err)
        }
        names = append(names, name)
    }

    // Check for errors that happened during iteration
    if err := rows.Err(); err != nil {
        return nil, fmt.Errorf("rows: %w", err)
    }

    return names, nil
}
```

The `rows.Err()` check at the end catches errors that happen mid-iteration — network drops, context cancellations mid-result-set. Without it, you'll silently return a partial result and think everything's fine.

## In The Wild

Knowing when to use `Query`, `QueryRow`, and `Exec` matters more than most people realize:

```go
// RIGHT — choosing the right method for the right job
func dbPatterns(db *sql.DB, ctx context.Context) {
    // Exec: for INSERT/UPDATE/DELETE — you want LastInsertId or RowsAffected
    result, err := db.ExecContext(ctx,
        "UPDATE accounts SET balance = balance - $1 WHERE id = $2",
        100, 42,
    )
    if err != nil {
        log.Fatal(err)
    }
    affected, _ := result.RowsAffected()
    if affected == 0 {
        log.Println("no rows updated — account might not exist")
    }

    // QueryRow: for SELECT that returns exactly one row
    var balance int
    err = db.QueryRowContext(ctx,
        "SELECT balance FROM accounts WHERE id = $1", 42,
    ).Scan(&balance)
    if errors.Is(err, sql.ErrNoRows) {
        log.Println("account not found")
        return
    }
    if err != nil {
        log.Fatal(err)
    }

    // Query: for SELECT that returns multiple rows
    rows, err := db.QueryContext(ctx,
        "SELECT id, name FROM users WHERE active = true",
    )
    if err != nil {
        log.Fatal(err)
    }
    defer rows.Close()
    for rows.Next() {
        var id int
        var name string
        if err := rows.Scan(&id, &name); err != nil {
            log.Fatal(err)
        }
        fmt.Printf("user %d: %s\n", id, name)
    }
    if err := rows.Err(); err != nil {
        log.Fatal(err)
    }
}
```

One thing people consistently mess up: `QueryRow` never returns an error directly. The error is deferred to `Scan`. That means `db.QueryRow(...)` will always return a non-nil `*Row` — you can't check for errors until you call `.Scan()`. Don't do `row, err := db.QueryRow(...)` — it won't compile the way you expect, and `QueryRow` only has one return value.

Another real-world pattern worth internalizing is prepared statements for hot paths:

```go
// RIGHT — prepared statements for queries executed many times per second
type UserStore struct {
    db       *sql.DB
    getByID  *sql.Stmt
    listActive *sql.Stmt
}

func NewUserStore(db *sql.DB) (*UserStore, error) {
    getByID, err := db.Prepare("SELECT id, name, email FROM users WHERE id = $1")
    if err != nil {
        return nil, fmt.Errorf("prepare getByID: %w", err)
    }

    listActive, err := db.Prepare("SELECT id, name FROM users WHERE active = true ORDER BY name")
    if err != nil {
        getByID.Close()
        return nil, fmt.Errorf("prepare listActive: %w", err)
    }

    return &UserStore{db: db, getByID: getByID, listActive: listActive}, nil
}

func (s *UserStore) Close() {
    s.getByID.Close()
    s.listActive.Close()
}
```

Prepared statements avoid re-parsing the query on every call. For a query that runs hundreds of times per second, this matters. For a query that runs once at startup, it doesn't — don't over-optimize.

## The Gotchas

**`*sql.DB` is a pool, not a connection.** It's safe to use from multiple goroutines. You should create one `*sql.DB` per database and share it throughout your application. Creating a new `*sql.DB` per request is a serious antipattern — you'll exhaust file descriptors and overwhelm Postgres with connection churn.

**`sql.ErrNoRows` is not actually an error.** When you're looking up something by ID and it doesn't exist, `QueryRow().Scan()` returns `sql.ErrNoRows`. That's expected behavior — a not-found, not a failure. Handle it explicitly with `errors.Is(err, sql.ErrNoRows)` and return a domain-level "not found" to your callers rather than leaking the database error up the stack.

**Scan into pointers for nullable columns.** If a column can be NULL and you scan it into a `string`, you'll panic. Use `sql.NullString`, `sql.NullInt64`, `sql.NullTime`, etc., or use pointer types (`*string`). NULL columns that cause panics in production are a special kind of humiliation.

**`db.Query` in a loop is almost always wrong.** If you're calling `db.Query` inside a `for` loop, you're about to discover the N+1 problem (we'll cover that in Lesson 7). Each call checks out a connection from the pool, runs a query, and returns it. A loop of 1000 items means 1000 round trips to the database. This is slow and shows up immediately in production load.

**Always use `ExecContext`, `QueryContext`, `QueryRowContext`.** The variants without context have no way to cancel or time out. In a production service, every database call should have a context — either from the request context or a dedicated `context.WithTimeout`. We'll go deep on this in Lesson 6.

## Key Takeaway

`database/sql` gives you everything you need to talk to a relational database correctly. Ping at startup to fail fast. Defer `rows.Close()` immediately after checking the query error. Check `rows.Err()` after the loop. Use `QueryRow` for single-row lookups, `Query` for multi-row results, and `Exec` when you care about rows affected. One `*sql.DB` per database, shared across your whole service. Get these right and you won't need an ORM for 80% of what you're building.

---

[Lesson 2: Connection Pool Tuning →](/post/go/go-db-connection-pools)
