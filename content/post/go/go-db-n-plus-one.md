---
title: "Lesson 7: The N+1 Problem — One query per row is a performance bug"
author: Atharva Pandey
series: "Go Database Patterns"
lesson: 7
keywords:
  - Go
  - golang
  - database
  - postgres
  - N+1 problem
  - query optimization
  - JOIN
  - sqlx
  - batch queries
tags:
  - Go tutorial
  - golang
  - database
date: '2025-12-25T00:00:00.000Z'
---

The N+1 problem is the most common database performance issue I find when reviewing Go code, and it's especially sneaky because it looks totally fine in development. You have a list of 10 users, you fetch their orders, 11 queries, no problem. You deploy to production, the table has 50,000 users, your endpoint suddenly takes 40 seconds, and you get a 3am page. The queries were always there — you just didn't notice them until the data grew.

The name "N+1" comes from the pattern: 1 query to get a list of N items, then N additional queries to fetch related data for each item. It's not exotic. It's just a loop that calls the database on every iteration.

## The Problem

Here's what N+1 looks like in Go. It's obvious in isolation but easy to miss when reading a larger codebase:

```go
// WRONG — N+1: one query for users, then one per user for orders
func GetUsersWithOrders(ctx context.Context, db *sql.DB) ([]UserWithOrders, error) {
    // Query 1: get all users
    rows, err := db.QueryContext(ctx, "SELECT id, name FROM users WHERE active = true")
    if err != nil {
        return nil, err
    }
    defer rows.Close()

    var users []UserWithOrders
    for rows.Next() {
        var u UserWithOrders
        if err := rows.Scan(&u.ID, &u.Name); err != nil {
            return nil, err
        }
        users = append(users, u)
    }

    // Queries 2 through N+1: one per user
    for i := range users {
        orderRows, err := db.QueryContext(ctx,
            "SELECT id, total, created_at FROM orders WHERE user_id = $1",
            users[i].ID, // separate round trip for every user
        )
        if err != nil {
            return nil, err
        }
        defer orderRows.Close()

        for orderRows.Next() {
            var o Order
            if err := orderRows.Scan(&o.ID, &o.Total, &o.CreatedAt); err != nil {
                return nil, err
            }
            users[i].Orders = append(users[i].Orders, o)
        }
    }

    return users, nil
}
// 1000 active users = 1001 queries
// Each query: ~1ms round trip to Postgres
// Total: ~1 second minimum just in network overhead
// Under load: 1000 active connections held simultaneously
```

GORM's `Preload` hides this beautifully. I mean that sarcastically:

```go
// WRONG — GORM Preload looks clean but generates N+1 queries
type User struct {
    gorm.Model
    Name   string
    Active bool
    Orders []Order
}

func GetUsersWithOrdersGORM(db *gorm.DB) ([]User, error) {
    var users []User
    // This runs:
    //   SELECT * FROM users WHERE active = true
    //   SELECT * FROM orders WHERE user_id IN (1) -- for first user
    //   SELECT * FROM orders WHERE user_id IN (2) -- for second user
    //   ... one per user
    result := db.Where("active = ?", true).Preload("Orders").Find(&users)
    return users, result.Error
}
// Looks like one call. Is actually N+1 calls. The abstraction hid the problem.
```

## The Idiomatic Way

There are two correct approaches: JOIN and batch IN query. Each has its place.

**JOIN approach — best when the result set fits in memory and you need matched rows:**

```go
// RIGHT — single query with JOIN, all data in one round trip
func GetUsersWithOrdersJOIN(ctx context.Context, db *sql.DB) ([]UserWithOrders, error) {
    const query = `
        SELECT
            u.id        AS user_id,
            u.name      AS user_name,
            o.id        AS order_id,
            o.total     AS order_total,
            o.created_at AS order_created_at
        FROM users u
        LEFT JOIN orders o ON o.user_id = u.id
        WHERE u.active = true
        ORDER BY u.id, o.created_at DESC
    `
    rows, err := db.QueryContext(ctx, query)
    if err != nil {
        return nil, fmt.Errorf("query: %w", err)
    }
    defer rows.Close()

    // Build the map in one pass — users deduplicated, orders accumulated
    userMap := make(map[int]*UserWithOrders)
    var userOrder []int // preserve ordering

    for rows.Next() {
        var (
            userID    int
            userName  string
            orderID   sql.NullInt64
            orderTotal sql.NullInt64
            orderCreated sql.NullTime
        )
        if err := rows.Scan(&userID, &userName, &orderID, &orderTotal, &orderCreated); err != nil {
            return nil, fmt.Errorf("scan: %w", err)
        }

        u, exists := userMap[userID]
        if !exists {
            u = &UserWithOrders{ID: userID, Name: userName}
            userMap[userID] = u
            userOrder = append(userOrder, userID)
        }

        if orderID.Valid {
            u.Orders = append(u.Orders, Order{
                ID:        int(orderID.Int64),
                Total:     int(orderTotal.Int64),
                CreatedAt: orderCreated.Time,
            })
        }
    }
    if err := rows.Err(); err != nil {
        return nil, err
    }

    result := make([]UserWithOrders, 0, len(userOrder))
    for _, id := range userOrder {
        result = append(result, *userMap[id])
    }
    return result, nil
}
// 1 query, 1 round trip, regardless of user count
```

**Batch IN approach — best when you need to avoid a cartesian product, or you already have a list of IDs:**

```go
// RIGHT — batch query with IN clause, two round trips instead of N+1
func GetOrdersForUsers(ctx context.Context, db *sql.DB, userIDs []int) (map[int][]Order, error) {
    if len(userIDs) == 0 {
        return nil, nil
    }

    // Build the placeholder list: ($1, $2, $3, ...)
    placeholders := make([]string, len(userIDs))
    args := make([]any, len(userIDs))
    for i, id := range userIDs {
        placeholders[i] = fmt.Sprintf("$%d", i+1)
        args[i] = id
    }

    query := fmt.Sprintf(
        "SELECT user_id, id, total, created_at FROM orders WHERE user_id IN (%s)",
        strings.Join(placeholders, ", "),
    )

    rows, err := db.QueryContext(ctx, query, args...)
    if err != nil {
        return nil, fmt.Errorf("batch orders query: %w", err)
    }
    defer rows.Close()

    result := make(map[int][]Order)
    for rows.Next() {
        var (
            userID    int
            o         Order
        )
        if err := rows.Scan(&userID, &o.ID, &o.Total, &o.CreatedAt); err != nil {
            return nil, err
        }
        result[userID] = append(result[userID], o)
    }
    return result, rows.Err()
}

// Usage — two queries total, not N+1
func GetUsersWithOrdersBatch(ctx context.Context, db *sql.DB) ([]UserWithOrders, error) {
    // Query 1: get users
    users, err := getActiveUsers(ctx, db)
    if err != nil {
        return nil, err
    }

    // Extract IDs
    userIDs := make([]int, len(users))
    for i, u := range users {
        userIDs[i] = u.ID
    }

    // Query 2: get all orders for those users in one batch
    ordersByUser, err := GetOrdersForUsers(ctx, db, userIDs)
    if err != nil {
        return nil, err
    }

    // Assemble — no more queries
    for i := range users {
        users[i].Orders = ordersByUser[users[i].ID]
    }
    return users, nil
}
```

If you're using `sqlx`, the `sqlx.In` helper builds the IN clause for you:

```go
// RIGHT — sqlx.In for clean batch queries
import "github.com/jmoiron/sqlx"

func GetOrdersForUsersSQLX(ctx context.Context, db *sqlx.DB, userIDs []int) ([]Order, error) {
    query, args, err := sqlx.In(
        "SELECT user_id, id, total, created_at FROM orders WHERE user_id IN (?)",
        userIDs,
    )
    if err != nil {
        return nil, err
    }

    // sqlx.In uses ? placeholders — rebind for Postgres
    query = db.Rebind(query)

    var orders []Order
    if err := db.SelectContext(ctx, &orders, query, args...); err != nil {
        return nil, err
    }
    return orders, nil
}
```

## In The Wild

Here's a real benchmark comparing the approaches for 100 users with an average of 5 orders each:

```
BenchmarkNPlusOne-8         10     112433052 ns/op   (112ms — 101 queries)
BenchmarkJOIN-8           1000       1205441 ns/op   (1.2ms — 1 query)
BenchmarkBatchIN-8         800       1489823 ns/op   (1.5ms — 2 queries)
```

That's a 93x difference between N+1 and JOIN. At 1000 users it would be 1000x. The JOIN and batch IN approaches are close — JOIN wins by a small margin because it's one fewer round trip, but batch IN is sometimes more readable for complex cases.

## The Gotchas

**JOIN can create a cartesian product.** If a user has 100 orders and 50 tags, a JOIN on both produces 5000 rows for that user. Use separate batch queries for multiple associations:

```go
// RIGHT — separate batch queries for multiple associations, not a JOIN on both
orders, err := GetOrdersForUsers(ctx, db, userIDs)
tags, err := GetTagsForUsers(ctx, db, userIDs)
// Assemble separately — no cartesian product
```

**IN with thousands of IDs slows down.** Postgres handles `IN (...)` well up to a few thousand values. Beyond that, the query planner struggles. If you're batching 10,000+ IDs, use a temporary table or a `COPY` approach instead.

**Detecting N+1 in code review.** The pattern to look for: a `db.Query` or `db.QueryRow` call inside a `for` loop. That's almost always N+1. A useful lint rule you can add to code review: flag any database call inside a loop and require justification.

**Logging slow queries in Postgres.** Add `log_min_duration_statement = 100` to your `postgresql.conf` to log any query slower than 100ms. This won't catch N+1 (each individual query is fast), but it will catch missing indexes and bad query plans. For N+1 specifically, instrument your `*sql.DB` to count queries per request.

## Key Takeaway

The N+1 problem is always the same pattern: a query inside a loop. The fix is always one of two things: a JOIN to get everything in one query, or a batch IN query to get related data in one round trip per association. The performance difference isn't marginal — it's often 100x or more in production. Audit your database-heavy endpoints by counting how many queries they execute, not just how long each individual query takes. One fast endpoint making 500 queries is a much bigger problem than one query taking 50ms.

---

← [Lesson 6: Context with Database Queries](/post/go/go-db-context-queries) | [Lesson 8: Migrations Without Downtime →](/post/go/go-db-migrations)
