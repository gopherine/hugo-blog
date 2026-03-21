---
title: "Lesson 5: sqlc vs ORM vs Raw SQL — Pick your tradeoff, not your religion"
author: Atharva Pandey
series: "Go Database Patterns"
lesson: 5
keywords:
  - Go
  - golang
  - database
  - postgres
  - sqlc
  - GORM
  - ORM
  - raw SQL
  - Ent
tags:
  - Go tutorial
  - golang
  - database
date: '2025-10-29T00:00:00.000Z'
---

Every time someone asks "should I use GORM or raw SQL?" a flame war breaks out. I've been on both sides of that argument, and I've shipped production systems using all four approaches — raw SQL, GORM, sqlc, and Ent. My opinion now is boring: each one is the right choice in a specific context, and none of them is universally correct. The question isn't which one is best, it's which tradeoffs you're signing up for.

Let me show you the same query in each approach so you can see concretely what you're choosing between.

The query: fetch all active users with their most recent order total. It involves a JOIN and a subquery.

## The Problem

Raw SQL with no type safety is the most common footgun for teams new to Go database programming:

```go
// WRONG — raw SQL with stringly-typed Scan, no compile-time safety
func GetActiveUsersWithOrderTotals(db *sql.DB, ctx context.Context) ([]UserWithOrder, error) {
    rows, err := db.QueryContext(ctx, `
        SELECT u.id, u.name, u.email, o.total
        FROM users u
        LEFT JOIN orders o ON o.id = (
            SELECT id FROM orders
            WHERE user_id = u.id
            ORDER BY created_at DESC
            LIMIT 1
        )
        WHERE u.active = true
    `)
    if err != nil {
        return nil, err
    }
    defer rows.Close()

    var results []UserWithOrder
    for rows.Next() {
        var r UserWithOrder
        // If you add a column to the SELECT, you must remember to add it here
        // If you reorder the columns, this silently scans into wrong fields
        // If o.total is NULL, this panics
        if err := rows.Scan(&r.ID, &r.Name, &r.Email, &r.Total); err != nil {
            return nil, err
        }
        results = append(results, r)
    }
    return results, rows.Err()
}
```

The problem with this isn't SQL — SQL is fine. The problem is that the connection between the query and the Go struct is invisible. Add a column, forget to update Scan, and you get a runtime panic or wrong data. There's no compiler helping you.

The GORM footgun is the opposite problem — too much magic hiding what's actually happening:

```go
// WRONG — GORM with lazy loading, N+1 hiding behind convenience
type User struct {
    gorm.Model
    Name   string
    Email  string
    Active bool
    Orders []Order // GORM "hasMany" relationship
}

type Order struct {
    gorm.Model
    UserID int
    Total  int
}

func GetActiveUsersGORM(db *gorm.DB) ([]User, error) {
    var users []User
    // This runs SELECT * FROM users WHERE active = true
    // Then for EACH user, runs SELECT * FROM orders WHERE user_id = ?
    // That's the N+1 problem, disguised as convenience
    result := db.Where("active = ?", true).Preload("Orders").Find(&users)
    return users, result.Error
}
```

## The Idiomatic Way

Here's the same query in all four approaches so you can compare directly:

**Raw SQL — explicit, full control, but manual scanning:**

```go
// Raw SQL: full control, type-safe scanning is manual discipline
func GetActiveUsersRaw(db *sql.DB, ctx context.Context) ([]UserWithOrder, error) {
    const query = `
        SELECT
            u.id,
            u.name,
            u.email,
            COALESCE(
                (SELECT total FROM orders WHERE user_id = u.id ORDER BY created_at DESC LIMIT 1),
                0
            ) AS last_order_total
        FROM users u
        WHERE u.active = true
        ORDER BY u.name
    `
    rows, err := db.QueryContext(ctx, query)
    if err != nil {
        return nil, fmt.Errorf("query: %w", err)
    }
    defer rows.Close()

    var results []UserWithOrder
    for rows.Next() {
        var r UserWithOrder
        if err := rows.Scan(&r.ID, &r.Name, &r.Email, &r.LastOrderTotal); err != nil {
            return nil, fmt.Errorf("scan: %w", err)
        }
        results = append(results, r)
    }
    return results, rows.Err()
}
// Pro: you see exactly what SQL runs. Con: refactoring column order breaks Scan silently.
```

**sqlc — type safety generated from SQL, my personal preference:**

```sql
-- queries/users.sql
-- name: GetActiveUsersWithLastOrder :many
SELECT
    u.id,
    u.name,
    u.email,
    COALESCE(
        (SELECT total FROM orders WHERE user_id = u.id ORDER BY created_at DESC LIMIT 1),
        0
    ) AS last_order_total
FROM users u
WHERE u.active = true
ORDER BY u.name;
```

```go
// sqlc generates this — you never write it by hand
type GetActiveUsersWithLastOrderRow struct {
    ID             int32
    Name           string
    Email          string
    LastOrderTotal int32
}

func (q *Queries) GetActiveUsersWithLastOrder(ctx context.Context) ([]GetActiveUsersWithLastOrderRow, error) {
    rows, err := q.db.QueryContext(ctx, getActiveUsersWithLastOrder)
    // ... generated scanning code, 100% correct ...
}

// Your code:
func GetActiveUsers(ctx context.Context, q *db.Queries) ([]db.GetActiveUsersWithLastOrderRow, error) {
    return q.GetActiveUsersWithLastOrder(ctx)
    // That's it. The generated code handles scanning.
    // Change the SQL? Regenerate. Wrong SQL? Compile error.
}
// Pro: type-safe, no magic, compile-time SQL validation. Con: code-gen step, slightly more setup.
```

**GORM — done correctly with explicit joins:**

```go
// GORM: used correctly with explicit Select and Joins instead of Preload
type UserWithOrder struct {
    ID             uint
    Name           string
    Email          string
    LastOrderTotal int
}

func GetActiveUsersGORMRight(db *gorm.DB) ([]UserWithOrder, error) {
    var results []UserWithOrder
    err := db.Model(&User{}).
        Select(`users.id, users.name, users.email,
            COALESCE((
                SELECT total FROM orders
                WHERE user_id = users.id
                ORDER BY created_at DESC LIMIT 1
            ), 0) AS last_order_total`).
        Where("users.active = ?", true).
        Order("users.name").
        Scan(&results).Error
    return results, err
    // Pro: familiar to teams coming from Rails/Django. Con: raw SQL in a string anyway,
    // plus GORM's abstraction overhead, plus magic that's hard to debug.
}
```

## In The Wild

Here's the honest comparison table:

| | Raw SQL | GORM | sqlc | Ent |
|---|---|---|---|---|
| **Type safety** | Manual | Partial | Full | Full |
| **SQL control** | Full | Limited | Full | Limited |
| **Compile errors** | No | No | Yes | Yes |
| **Learning curve** | Low | Medium | Medium | High |
| **Migration support** | None | AutoMigrate | None (use goose/migrate) | Built-in |
| **Complex queries** | Natural | Painful | Natural | Painful |
| **Schema changes** | Risky | Risky | Caught at gen time | Caught at gen time |

My personal take: **sqlc for most new services**. You write SQL (which you should know anyway), run `sqlc generate`, and get type-safe Go code. You see exactly what queries run. Complex JOINs and CTEs are no problem — you're writing SQL, not fighting an ORM query builder. The only overhead is running `sqlc generate` after schema changes, which you should be doing with a Makefile target anyway.

**Raw SQL** makes sense for very small services, scripts, or one-off tools where the setup overhead of sqlc isn't worth it.

**GORM** makes sense if you're coming from a framework-heavy background and the team is more comfortable with ORM conventions, or if you need AutoMigrate for rapid prototyping. Just be disciplined about avoiding `Preload` on anything that could return more than a handful of rows.

**Ent** makes sense for large teams that want a code-first schema definition and are willing to invest in learning the framework. It's powerful but it's a big dependency with its own paradigm.

## The Gotchas

**GORM's `AutoMigrate` is not for production.** It's great for rapid development. It should never run automatically in a production deployment. It drops column constraints, can't handle column renames, and has no rollback. Use a proper migration tool (we cover this in Lesson 8).

**sqlc requires discipline on the SQL side.** If your SQL has a bug, sqlc won't catch logical errors — it catches type mismatches and missing columns. Write tests for your queries (Lesson 9 covers this with testcontainers).

**GORM's soft deletes are a footgun.** If your model embeds `gorm.Model`, GORM adds `deleted_at` and every query gets a `WHERE deleted_at IS NULL` appended. This is invisible until you need to query deleted records and can't figure out why your query returns nothing.

**sqlc with Postgres-specific features is worth it.** sqlc understands Postgres types — `JSONB`, arrays, enums, `RETURNING`. You can use the full power of Postgres without losing type safety. With GORM, you often fall back to raw SQL for anything complex anyway.

**The performance gap is usually not where you think.** GORM's overhead is single-digit microseconds per query. The real performance problems are N+1 queries (Lesson 7), missing indexes, and holding connections too long. Don't pick raw SQL for "performance" reasons when GORM's `Preload` behavior is the actual risk.

## Key Takeaway

Pick based on your team and use case, not tribalism. sqlc is my default recommendation for new services in Go — it gives you full SQL control with compile-time type safety and zero magic. Raw SQL is fine for simple services if you're disciplined about scanning. GORM is fine if your team knows its footguns. Ent is worth it for large teams doing code-first schema design. Whatever you pick, understand the SQL it generates and have a plan for complex queries before you're under production pressure.

---

← [Lesson 4: The Repository Pattern](/post/go/go-db-repository-pattern) | [Lesson 6: Context with Database Queries →](/post/go/go-db-context-queries)
