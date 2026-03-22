---
title: "Lesson 4: Schema Migrations in Rust Projects — Evolving your database"
author: Atharva Pandey
series: "Rust Database Patterns"
lesson: 4
keywords: [Rust, rust, database, SQL, migrations, schema, postgres, refund]
tags: [Rust tutorial, rust, database, postgres]
date: '2024-10-26T19:12:00.000Z'
---

A teammate once ran `ALTER TABLE orders DROP COLUMN status` on the production database because he'd tested it locally and "it worked fine." What he didn't realize was that three other services depended on that column, and they all started throwing errors simultaneously. We spent the evening restoring from a backup.

Schema migrations exist to prevent exactly this — they're version control for your database.

## The Problem

Your database schema isn't static. Features get added, requirements change, data models evolve. You need a way to:

1. Track what schema changes have been applied
2. Apply changes in the correct order
3. Roll back changes when they break things
4. Ensure every environment (dev, staging, prod) has the same schema
5. Coordinate schema changes across a team

Doing this manually — running SQL scripts by hand, keeping a wiki page of "what to run" — works until it doesn't. And when it doesn't, you get the dropped-column incident I described above.

## Migration Tools in the Rust Ecosystem

You have three main options:

- **Diesel CLI** — Built into Diesel, generates `schema.rs` automatically
- **SQLx CLI** — Works with SQLx, lightweight and unopinionated
- **refinery** — Standalone migration runner, works with any database library

I'll cover all three because which one you pick depends on your database library choice.

## SQLx Migrations

If you're using SQLx, its migration system is straightforward:

```bash
cargo install sqlx-cli
sqlx migrate add create_users
```

This creates `migrations/YYYYMMDDHHMMSS_create_users.sql`. Write your migration:

```sql
-- migrations/20241026_create_users.sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(255) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    bio TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_username ON users(username);
```

Run it:

```bash
sqlx migrate run
```

SQLx tracks applied migrations in a `_sqlx_migrations` table. Each migration runs exactly once, in filename order.

You can also run migrations from your application at startup:

```rust
use sqlx::postgres::PgPoolOptions;
use sqlx::migrate::Migrator;
use std::path::Path;

#[tokio::main]
async fn main() -> Result<(), sqlx::Error> {
    let pool = PgPoolOptions::new()
        .max_connections(5)
        .connect("postgres://localhost/myapp")
        .await?;

    // Run migrations at startup
    sqlx::migrate!("./migrations")
        .run(&pool)
        .await?;

    println!("Migrations applied successfully");
    Ok(())
}
```

That `sqlx::migrate!` macro embeds the migration files into your binary at compile time. Your deployed binary carries its own migrations — no need to ship SQL files alongside it. This is genuinely clever.

## Diesel Migrations

Diesel's migration system uses paired up/down files:

```bash
diesel migration generate create_users
```

This creates a directory with two files:

```sql
-- migrations/2024-10-26-191200_create_users/up.sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(255) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    bio TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

```sql
-- migrations/2024-10-26-191200_create_users/down.sql
DROP TABLE users;
```

```bash
diesel migration run      # Apply pending migrations
diesel migration revert   # Roll back the last migration
diesel migration redo     # Revert and re-apply (useful during development)
```

The big difference from SQLx: after running migrations, Diesel regenerates `src/schema.rs`. This means your Rust types and your database schema are always in sync. If you add a column to a migration, the corresponding Rust table definition updates automatically.

You can embed Diesel migrations in your binary too:

```rust
use diesel_migrations::{embed_migrations, EmbeddedMigrations, MigrationHarness};

pub const MIGRATIONS: EmbeddedMigrations = embed_migrations!("./migrations");

fn run_migrations(conn: &mut PgConnection) {
    conn.run_pending_migrations(MIGRATIONS)
        .expect("Failed to run migrations");
}
```

## refinery — The Standalone Option

If you want a migration tool that's decoupled from your database library, refinery is excellent:

```toml
[dependencies]
refinery = { version = "0.8", features = ["tokio-postgres"] }
tokio-postgres = "0.7"
tokio = { version = "1", features = ["full"] }
```

refinery uses a directory of numbered SQL files:

```
migrations/
  V1__create_users.sql
  V2__create_posts.sql
  V3__add_user_roles.sql
```

```rust
use refinery::embed_migrations;

embed_migrations!("./migrations");

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let (mut client, connection) = tokio_postgres::connect(
        "host=localhost dbname=myapp",
        tokio_postgres::NoTls,
    ).await?;

    tokio::spawn(async move {
        if let Err(e) = connection.await {
            eprintln!("Connection error: {}", e);
        }
    });

    // Run migrations
    migrations::runner()
        .run_async(&mut client)
        .await?;

    println!("All migrations applied");
    Ok(())
}
```

refinery's naming convention (`V1__`, `V2__`) is simple and conflict-resistant. No timestamps to collide, no directories to create. It tracks applied migrations in a `refinery_schema_history` table.

## Writing Good Migrations

The tool matters less than the practices. Here's what I've learned from years of schema changes:

### Always be additive first

Adding a column is safe. Dropping a column is dangerous. When you need to rename a column, do it in three steps across three deployments:

```sql
-- Migration 1: Add the new column
ALTER TABLE users ADD COLUMN display_name VARCHAR(255);
UPDATE users SET display_name = username;

-- Migration 2: Update application code to use display_name
-- (no SQL change, just a code deployment)

-- Migration 3: Drop the old column after confirming nothing uses it
ALTER TABLE users DROP COLUMN username;
```

Yes, this is three deployments for a column rename. It's also zero downtime and zero broken services.

### Make migrations idempotent when possible

```sql
-- Bad: fails if run twice
CREATE TABLE users (...);

-- Good: safe to run multiple times
CREATE TABLE IF NOT EXISTS users (...);

-- Bad: fails if index already exists
CREATE INDEX idx_users_email ON users(email);

-- Good: won't fail on duplicate
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
```

### Never modify data and schema in the same migration

```sql
-- Bad: mixing DDL and DML
ALTER TABLE users ADD COLUMN role VARCHAR(50) DEFAULT 'user';
UPDATE users SET role = 'admin' WHERE email IN ('admin@company.com');

-- Good: separate migrations
-- Migration N: add the column
ALTER TABLE users ADD COLUMN role VARCHAR(50) DEFAULT 'user';

-- Migration N+1: set initial data
UPDATE users SET role = 'admin' WHERE email IN ('admin@company.com');
```

Why? Because in Postgres, DDL statements are transactional. If you mix DDL and DML and the DML fails, the entire migration rolls back including the schema change. Keeping them separate gives you cleaner rollback boundaries.

### Include timing expectations in comments

```sql
-- Migration: add full-text search index
-- Expected time: ~5 minutes on 10M rows
-- WARNING: Takes an ACCESS EXCLUSIVE lock, plan for downtime
CREATE INDEX idx_posts_content_trgm ON posts USING gin(content gin_trgm_ops);
```

Future you (or your teammate) will appreciate knowing that this migration takes 5 minutes, not 5 seconds.

### Use CONCURRENTLY for indexes on large tables

```sql
-- This locks the table for the entire index build. Bad for large tables.
CREATE INDEX idx_orders_created ON orders(created_at);

-- This builds the index without blocking writes. Takes longer but no downtime.
CREATE INDEX CONCURRENTLY idx_orders_created ON orders(created_at);
```

One gotcha: `CREATE INDEX CONCURRENTLY` can't run inside a transaction. If your migration tool wraps each migration in a transaction (Diesel does, SQLx does by default), you'll need to handle this specially.

With SQLx, you can disable the transaction wrapper per migration by adding a comment:

```sql
-- migrate:no-transaction
CREATE INDEX CONCURRENTLY idx_orders_created ON orders(created_at);
```

## Testing Migrations

Here's a pattern I use to verify migrations work correctly:

```rust
#[cfg(test)]
mod tests {
    use sqlx::PgPool;

    #[sqlx::test]
    async fn migrations_apply_cleanly(pool: PgPool) {
        // sqlx::test automatically runs all migrations
        // If we get here, they all applied successfully

        // Verify the schema looks right
        let result = sqlx::query!(
            "SELECT column_name, data_type
             FROM information_schema.columns
             WHERE table_name = 'users'
             ORDER BY ordinal_position"
        )
        .fetch_all(&pool)
        .await
        .unwrap();

        assert!(result.iter().any(|r| r.column_name == "id"));
        assert!(result.iter().any(|r| r.column_name == "email"));
    }
}
```

The `#[sqlx::test]` attribute creates a fresh database for each test and runs all migrations automatically. After the test, it drops the database. Perfect isolation, no test pollution.

## CI/CD Pipeline Integration

In your CI pipeline, migrations should be a gate:

```yaml
# .github/workflows/ci.yml (relevant section)
- name: Run migrations
  run: sqlx migrate run
  env:
    DATABASE_URL: postgres://postgres:password@localhost/test_db

- name: Verify sqlx offline data is current
  run: |
    cargo sqlx prepare --check
```

That `--check` flag verifies that your `.sqlx/` directory (the offline query cache) is up to date with your current migrations. If someone adds a migration but forgets to run `cargo sqlx prepare`, CI catches it.

## What's Next

Now that you can evolve your schema safely, we need to talk about what happens when things go wrong mid-query. Lesson 5 covers transactions and error rollback — how to make multiple database operations atomic so you never end up with half-applied changes.
