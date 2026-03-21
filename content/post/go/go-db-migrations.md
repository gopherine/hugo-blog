---
title: "Lesson 8: Migrations Without Downtime — ALTER TABLE can lock your entire database"
author: Atharva Pandey
series: "Go Database Patterns"
lesson: 8
keywords:
  - Go
  - golang
  - database
  - postgres
  - migrations
  - golang-migrate
  - goose
  - zero-downtime
  - ALTER TABLE
  - NOT NULL
tags:
  - Go tutorial
  - golang
  - database
date: '2026-01-27T00:00:00.000Z'
---

The first time I caused a production outage with a database migration, I was adding a `NOT NULL` column to a table. The migration looked innocent. It ran fine on staging with 1,000 rows. In production with 40 million rows, it locked the entire table for 11 minutes while Postgres rewrote every row. Every write to that table failed with a lock timeout. We rolled back the app but couldn't roll back the migration. It was a terrible morning.

Database migrations are the most dangerous routine operation in a deployed Go service. A bad migration can take down a production system in ways that no amount of application code review will catch. The rules aren't complicated, but they're counterintuitive until you've been burned.

## The Problem

The naive migration approach is running schema changes directly without thinking about what Postgres actually does under the hood:

```sql
-- WRONG — this locks the entire table for the duration of the rewrite
ALTER TABLE orders ADD COLUMN fulfilled_at TIMESTAMP NOT NULL DEFAULT NOW();
```

This migration looks reasonable. But on a large table, `ADD COLUMN ... NOT NULL DEFAULT` causes Postgres to rewrite the entire table. That's a full table lock — no reads, no writes — for as long as the rewrite takes. At 40 million rows, that's potentially 10+ minutes. Your service is down for those 10 minutes.

The code equivalent — running migrations at startup inside your Go binary — is another footgun:

```go
// WRONG — running migrations at startup blocks the service from starting
// and prevents horizontal scaling
func main() {
    db, err := NewDB(os.Getenv("DATABASE_URL"))
    if err != nil {
        log.Fatal(err)
    }

    // This blocks startup until migrations complete
    // If you have 2 instances starting simultaneously, both try to run migrations
    // One will succeed; the other will try to run already-applied migrations
    if err := runMigrations(db); err != nil {
        log.Fatal("migrations failed:", err)
    }

    startServer(db)
}
```

Running migrations at binary startup causes two problems: startup is blocked until migrations complete (so rolling deployments stall), and multiple instances starting simultaneously can race on the migration state.

```sql
-- WRONG — NOT NULL without a default fails on non-empty tables
-- And NOT NULL with DEFAULT is a table rewrite in older Postgres versions
ALTER TABLE users ADD COLUMN phone VARCHAR(20) NOT NULL;
-- ERROR: column "phone" of relation "users" contains null values
-- (fails immediately on a non-empty table)
```

## The Idiomatic Way

Use a proper migration tool — either `golang-migrate` or `goose` — with versioned SQL files. Run migrations as a separate step from your service startup:

**Setting up golang-migrate:**

```go
// RIGHT — golang-migrate as a separate binary or init container step
import (
    "github.com/golang-migrate/migrate/v4"
    _ "github.com/golang-migrate/migrate/v4/database/postgres"
    _ "github.com/golang-migrate/migrate/v4/source/file"
)

func RunMigrations(databaseURL, migrationsPath string) error {
    m, err := migrate.New(
        "file://"+migrationsPath,
        databaseURL,
    )
    if err != nil {
        return fmt.Errorf("migrate.New: %w", err)
    }
    defer m.Close()

    if err := m.Up(); err != nil && !errors.Is(err, migrate.ErrNoChange) {
        return fmt.Errorf("migrate.Up: %w", err)
    }

    version, dirty, err := m.Version()
    if err != nil {
        return fmt.Errorf("version: %w", err)
    }
    if dirty {
        return fmt.Errorf("migration %d is dirty — manual intervention required", version)
    }

    log.Printf("database at migration version %d", version)
    return nil
}
```

Migration files — numbered, paired up/down:

```
migrations/
  000001_create_users.up.sql
  000001_create_users.down.sql
  000002_add_orders.up.sql
  000002_add_orders.down.sql
  000003_add_fulfilled_at_to_orders.up.sql
  000003_add_fulfilled_at_to_orders.down.sql
```

Run migrations from a separate entrypoint or an init container — never from your main service:

```go
// cmd/migrate/main.go — separate binary for migrations
func main() {
    if err := RunMigrations(
        os.Getenv("DATABASE_URL"),
        "./migrations",
    ); err != nil {
        log.Fatalf("migration failed: %v", err)
    }
    log.Println("migrations complete")
}
```

In Kubernetes, this is an init container that runs before the service pod starts. In Fly.io or Railway, it's a `release_command`. In a CI/CD pipeline, it's a step before deploy. The key is: migrations run exactly once, before new code is live, not inside the service that will be horizontally scaled.

## In The Wild

Zero-downtime migration for adding a NOT NULL column is a three-step process — you deploy three times, not once:

```sql
-- Step 1: Add the column nullable, no default required, no table rewrite
-- Deploy this first, with the old code still running
-- 000010_add_phone_step1.up.sql
ALTER TABLE users ADD COLUMN phone VARCHAR(20);
-- This is instant — Postgres 11+ adds nullable columns without a table rewrite
```

```go
// Step 2 (application code): backfill the column
// Run this as a background job or a one-time script
// RIGHT — batch backfill to avoid locking
func BackfillPhoneColumn(ctx context.Context, db *sql.DB) error {
    const batchSize = 1000
    var lastID int

    for {
        result, err := db.ExecContext(ctx, `
            UPDATE users
            SET phone = ''
            WHERE id > $1
              AND phone IS NULL
            ORDER BY id
            LIMIT $2
        `, lastID, batchSize)
        if err != nil {
            return fmt.Errorf("backfill: %w", err)
        }

        affected, _ := result.RowsAffected()
        if affected == 0 {
            break // all rows backfilled
        }

        // Get the max ID we just updated for the next batch
        err = db.QueryRowContext(ctx,
            "SELECT MAX(id) FROM users WHERE id > $1 AND phone = '' LIMIT $2",
            lastID, batchSize,
        ).Scan(&lastID)
        if err != nil {
            return err
        }

        log.Printf("backfilled up to user %d", lastID)
        time.Sleep(10 * time.Millisecond) // small pause to avoid overwhelming the DB
    }
    return nil
}
```

```sql
-- Step 3: Add the NOT NULL constraint after backfill is complete
-- Postgres 12+ can add NOT NULL constraints without a full table scan
-- if there's a CHECK constraint already, but the safe way is:
-- 000011_add_phone_not_null.up.sql
ALTER TABLE users ALTER COLUMN phone SET NOT NULL;
-- Postgres validates this by checking for NULLs — fast if no NULLs exist
-- If you're on Postgres 12+, use a NOT VALID constraint first, then VALIDATE:

-- Even safer for large tables:
ALTER TABLE users ADD CONSTRAINT users_phone_not_null CHECK (phone IS NOT NULL) NOT VALID;
-- (runs instantly — NOT VALID skips existing rows)
-- Then in a separate migration after backfill:
ALTER TABLE users VALIDATE CONSTRAINT users_phone_not_null;
-- (this takes a SHARE UPDATE EXCLUSIVE lock, not a full ACCESS EXCLUSIVE lock)
```

**Adding an index without locking:**

```sql
-- WRONG — CREATE INDEX locks the table for reads AND writes
CREATE INDEX idx_orders_user_id ON orders(user_id);

-- RIGHT — CREATE INDEX CONCURRENTLY takes longer but doesn't lock
CREATE INDEX CONCURRENTLY idx_orders_user_id ON orders(user_id);
-- Important: CONCURRENTLY can't run inside a transaction block
-- golang-migrate runs each file in a transaction by default
-- You need to disable that for CONCURRENTLY migrations
```

With golang-migrate, disable transactions for specific files:

```sql
-- 000012_add_index_concurrently.up.sql
-- migrate:disable-transactions

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_orders_user_id ON orders(user_id);
```

## The Gotchas

**Dirty migrations are a manual process.** If a migration fails halfway through (connection dropped, OOM, killed process), golang-migrate marks it as "dirty." You can't run more migrations until you manually resolve it. Connect to the database and either complete the partial migration or revert it, then run `migrate force <version>` to clear the dirty state. This is scary the first time. It's why you test migrations in staging on a production-sized dataset.

**Down migrations are a lie for most scenarios.** The `down.sql` file sounds reassuring — if something goes wrong, just roll back. In practice, rolling back a migration that deleted data or added columns with existing data is impossible. Down migrations are useful during development. In production, forward-only migrations (write a new migration that undoes the change) are safer.

**Migration ordering in teams.** When two developers add migrations simultaneously with sequential numbers, you get conflicts. Use timestamps instead of sequential numbers: `20260115_143022_add_phone_to_users.up.sql`. Goose supports timestamp-based naming natively.

```go
// goose supports timestamp naming out of the box
// goose create add_phone_to_users sql
// Creates: 20260127143022_add_phone_to_users.sql
```

**The `SET DEFAULT` trap.** `ALTER TABLE orders ALTER COLUMN status SET DEFAULT 'pending'` is fast. But `ALTER TABLE orders ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'pending'` in Postgres < 11 is a table rewrite. Postgres 11+ made nullable column additions instant, but NOT NULL with default is still a rewrite in some versions. Test on a production-sized copy.

## Key Takeaway

Migrations are infrastructure operations, not application code. Run them with a dedicated tool (golang-migrate or goose), in a separate step from your service startup, on the correct migration version. Zero-downtime schema changes follow a pattern: add nullable first, backfill in batches, then add the constraint. Use `CREATE INDEX CONCURRENTLY` for new indexes. Test migrations against a production-sized dataset before deploying — what takes 50ms on staging's 10,000-row table might take 20 minutes on production's 50-million-row table. Find out on staging, not on a Friday afternoon.

---

← [Lesson 7: The N+1 Problem](/post/go/go-db-n-plus-one) | [Lesson 9: Testing with Real Databases →](/post/go/go-db-testing)
