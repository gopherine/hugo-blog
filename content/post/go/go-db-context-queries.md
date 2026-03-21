---
title: "Lesson 6: Context with Database Queries — Every query needs a timeout"
author: Atharva Pandey
series: "Go Database Patterns"
lesson: 6
keywords:
  - Go
  - golang
  - database
  - postgres
  - context
  - QueryContext
  - ExecContext
  - statement_timeout
  - query timeout
tags:
  - Go tutorial
  - golang
  - database
date: '2025-12-03T00:00:00.000Z'
---

We once had a query that ran for 47 minutes. I'm not joking. A reporting query that worked fine on the test dataset decided to do a full sequential scan on a 200M-row table in production because someone dropped an index by accident the night before. The query just ran. And ran. And ran. The connection was held the whole time, blocking the pool. New requests started queuing. Within 10 minutes, the service was effectively down — not because of an error, but because every database connection was held by queries waiting for that one slow one to finish.

The fix was to add a context timeout to every database call. That query, with a proper timeout, would have failed after 5 seconds with a clear error instead of silently killing the service for 47 minutes.

## The Problem

The problem is using the non-context versions of database methods, or passing a background context with no timeout:

```go
// WRONG — no context, no timeout, query runs until Postgres decides it's done
func GetUserReport(db *sql.DB, userID int) ([]ReportRow, error) {
    // db.Query has no context — it will run forever if Postgres is slow
    rows, err := db.Query(`
        SELECT u.id, u.name, COUNT(o.id) as order_count, SUM(o.total) as revenue
        FROM users u
        LEFT JOIN orders o ON o.user_id = u.id
        WHERE u.id = $1
        GROUP BY u.id, u.name
    `, userID)
    if err != nil {
        return nil, err
    }
    defer rows.Close()
    // ...
}

// WRONG — context.Background() with no deadline is the same as no context
func GetUser(db *sql.DB, userID int) (*User, error) {
    var u User
    err := db.QueryRowContext(
        context.Background(), // no deadline — might as well not have a context
        "SELECT id, name, email FROM users WHERE id = $1",
        userID,
    ).Scan(&u.ID, &u.Name, &u.Email)
    return &u, err
}
```

The second example is sneakier. It uses `QueryRowContext` — looks correct — but `context.Background()` has no deadline. A stuck query will still run forever. The context only helps if it carries a deadline or can be cancelled by something upstream.

```go
// WRONG — using request context but not propagating it to database calls
func (h *UserHandler) GetUser(w http.ResponseWriter, r *http.Request) {
    id, _ := strconv.Atoi(chi.URLParam(r, "id"))

    // The request context has a deadline — but we're not using it!
    user, err := h.store.GetByID(id) // no ctx argument
    if err != nil {
        http.Error(w, "error", 500)
        return
    }
    json.NewEncoder(w).Encode(user)
}

// The store method signature is missing the context parameter
func (s *UserStore) GetByID(id int) (*User, error) {
    var u User
    err := s.db.QueryRowContext(context.Background(), // wrong — should be the passed ctx
        "SELECT id, name, email FROM users WHERE id = $1", id,
    ).Scan(&u.ID, &u.Name, &u.Email)
    return &u, err
}
```

When the HTTP client disconnects (timeout, user navigates away, load balancer closes the connection), the request context is cancelled. But the database query keeps running because it's using `context.Background()` instead of the request context. You're doing work that nobody will ever read, and holding a connection while doing it.

## The Idiomatic Way

Every function that touches the database should accept a context, and that context should flow from the request (or the job, or the cron) all the way down to every database call:

```go
// RIGHT — context flows from request to DB call
func (h *UserHandler) GetUser(w http.ResponseWriter, r *http.Request) {
    id, err := strconv.Atoi(chi.URLParam(r, "id"))
    if err != nil {
        http.Error(w, "invalid id", http.StatusBadRequest)
        return
    }

    // r.Context() carries the request deadline and cancellation
    user, err := h.store.GetByID(r.Context(), id)
    if errors.Is(err, ErrUserNotFound) {
        http.Error(w, "not found", http.StatusNotFound)
        return
    }
    if err != nil {
        http.Error(w, "internal error", http.StatusInternalServerError)
        return
    }
    json.NewEncoder(w).Encode(user)
}

// The store always accepts context as the first argument
func (s *UserStore) GetByID(ctx context.Context, id int) (*User, error) {
    var u User
    err := s.db.QueryRowContext(ctx, // ← the passed context, not Background()
        "SELECT id, name, email, created_at FROM users WHERE id = $1", id,
    ).Scan(&u.ID, &u.Name, &u.Email, &u.CreatedAt)
    if errors.Is(err, sql.ErrNoRows) {
        return nil, ErrUserNotFound
    }
    if err != nil {
        return nil, fmt.Errorf("get user %d: %w", id, err)
    }
    return &u, nil
}
```

For background jobs and operations that don't have a request context, add an explicit timeout:

```go
// RIGHT — explicit timeout for background operations
func (s *ReportService) GenerateDailyReport(ctx context.Context) error {
    // Even if ctx is Background(), add a specific timeout for this operation
    queryCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
    defer cancel()

    rows, err := s.db.QueryContext(queryCtx, `
        SELECT
            DATE(created_at) as day,
            COUNT(*) as order_count,
            SUM(total) as revenue
        FROM orders
        WHERE created_at >= NOW() - INTERVAL '1 day'
        GROUP BY DATE(created_at)
    `)
    if err != nil {
        if ctx.Err() != nil {
            return fmt.Errorf("report query cancelled: %w", ctx.Err())
        }
        return fmt.Errorf("report query: %w", err)
    }
    defer rows.Close()
    // ...
}
```

The second layer of defense is Postgres's own `statement_timeout`. This is a server-side limit that kills queries regardless of whether the client sent a cancellation:

```go
// RIGHT — set statement_timeout at the session level for safety
func NewDB(dsn string) (*sql.DB, error) {
    db, err := sql.Open("postgres", dsn)
    if err != nil {
        return nil, err
    }

    // Set statement_timeout on every new connection
    // This is the last line of defense — if the Go context doesn't cancel the query,
    // Postgres will kill it after 10 seconds
    db.SetConnMaxLifetime(5 * time.Minute)

    // Use a connection hook to set statement_timeout
    // (with pgx driver, this is done in the config)
    // With lib/pq, execute it after opening:
    _, err = db.Exec("SET statement_timeout = '10s'")
    if err != nil {
        return nil, fmt.Errorf("set statement_timeout: %w", err)
    }

    return db, nil
}

// Better: use pgx and set it in the config
func NewDBWithPgx(dsn string) (*sql.DB, error) {
    config, err := pgxpool.ParseConfig(dsn)
    if err != nil {
        return nil, err
    }

    // AfterConnect runs on every new connection
    config.AfterConnect = func(ctx context.Context, conn *pgx.Conn) error {
        _, err := conn.Exec(ctx, "SET statement_timeout = '10s'")
        return err
    }

    pool, err := pgxpool.NewWithConfig(context.Background(), config)
    if err != nil {
        return nil, err
    }

    // Wrap pgxpool to use with database/sql interface
    return stdlib.OpenDBFromPool(pool), nil
}
```

## In The Wild

The real-world pattern for an HTTP service is a middleware that adds a per-request database timeout:

```go
// RIGHT — request timeout middleware that limits total request time including DB
func RequestTimeoutMiddleware(timeout time.Duration) func(http.Handler) http.Handler {
    return func(next http.Handler) http.Handler {
        return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
            ctx, cancel := context.WithTimeout(r.Context(), timeout)
            defer cancel()
            next.ServeHTTP(w, r.WithContext(ctx))
        })
    }
}

// Wire it up in main
func main() {
    mux := chi.NewRouter()
    mux.Use(RequestTimeoutMiddleware(5 * time.Second)) // all requests time out after 5s

    mux.Get("/users/{id}", userHandler.GetUser)
    mux.Get("/reports/daily", reportHandler.GetDailyReport) // this one might need longer
    // ...
}

// For the report endpoint, override the timeout
func (h *ReportHandler) GetDailyReport(w http.ResponseWriter, r *http.Request) {
    // Override the middleware timeout for long-running reports
    ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
    defer cancel()

    report, err := h.reports.GetDaily(ctx)
    // ...
}
```

## The Gotchas

**Context cancellation errors look different than query errors.** When a context deadline is exceeded or the context is cancelled, `QueryContext` returns `context.DeadlineExceeded` or `context.Canceled`. These should be handled differently from database errors:

```go
// RIGHT — distinguish context errors from database errors
func (s *UserStore) GetByID(ctx context.Context, id int) (*User, error) {
    var u User
    err := s.db.QueryRowContext(ctx,
        "SELECT id, name, email FROM users WHERE id = $1", id,
    ).Scan(&u.ID, &u.Name, &u.Email)

    if err == nil {
        return &u, nil
    }
    if errors.Is(err, sql.ErrNoRows) {
        return nil, ErrUserNotFound
    }
    if errors.Is(err, context.DeadlineExceeded) {
        return nil, fmt.Errorf("query timed out after %v: %w", ctx, err)
    }
    if errors.Is(err, context.Canceled) {
        return nil, fmt.Errorf("query cancelled: %w", err)
    }
    return nil, fmt.Errorf("query: %w", err)
}
```

**Cancelling the context doesn't immediately stop the query on the Postgres side.** When the Go context is cancelled, the database driver sends a cancellation signal to Postgres. Postgres will try to stop the query, but there's a small window where the query is still running. `statement_timeout` is the authoritative kill switch on the Postgres side.

**Deadline propagation in transactions.** If you call `db.BeginTx(ctx, nil)` with a context that has a deadline, the entire transaction (including commit) must complete within that deadline. If the transaction takes longer, you'll get a context timeout error — but the transaction may or may not have committed by the time the error is returned. Handle this carefully. For long transactions, use a context with a longer deadline than for individual queries.

**Logging cancelled queries.** When a user disconnects, their request context gets cancelled, and you'll see context cancellation errors in your logs. These are expected and normal — they shouldn't fire alerts. Filter them in your error logging:

```go
if err != nil && !errors.Is(err, context.Canceled) {
    log.Printf("db error: %v", err)
}
```

## Key Takeaway

Every database call should use a context with a meaningful deadline. Thread the request context from the HTTP handler down to every database call — this gives you automatic cancellation when clients disconnect. For background jobs, use `context.WithTimeout`. Set Postgres's `statement_timeout` as a server-side safety net. Distinguish context cancellation errors from real database errors in your error handling — cancelled queries are often expected behavior, not bugs. The 47-minute query that took my service down? Three lines of context would have killed it in 10 seconds and logged a clear error.

---

← [Lesson 5: sqlc vs ORM vs Raw SQL](/post/go/go-db-sqlc-vs-orm) | [Lesson 7: The N+1 Problem →](/post/go/go-db-n-plus-one)
