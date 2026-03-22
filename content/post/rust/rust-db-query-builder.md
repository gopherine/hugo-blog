---
title: "Lesson 7: Building Type-Safe Query Builders — Queries that can't be wrong"
author: Atharva Pandey
series: "Rust Database Patterns"
lesson: 7
keywords: [Rust, rust, database, SQL, query builder, type-safe, postgres, generics, builder pattern]
tags: [Rust tutorial, rust, database, postgres]
date: '2024-11-01T09:28:00.000Z'
---

I was reviewing a PR that had a search endpoint with 12 optional filters. The handler was a 200-line function full of `if let Some(...)` blocks, each appending a different SQL fragment to a `String`. It worked — until someone forgot a space between `AND` and a column name, and the query silently returned zero results instead of erroring. No compile error. No test failure. Just a missing space in a string.

You can do better than string concatenation. You can build a query builder where invalid queries are structurally impossible.

## The Problem with Dynamic Queries

Static queries — the ones you write in `sqlx::query!` macros — are great when you know exactly what you're asking at compile time. But real applications have search endpoints, filter panels, admin dashboards. The query shape changes based on user input:

- "Show me orders from the last 30 days" → needs a date filter
- "Show me orders from the last 30 days by customer X" → needs date filter AND customer filter
- "Show me orders sorted by total descending, page 3" → needs sorting AND pagination

The naive approach is string concatenation:

```rust
// Don't do this
fn build_search_query(filters: &SearchFilters) -> String {
    let mut sql = "SELECT * FROM orders WHERE 1=1".to_string();
    if let Some(ref customer) = filters.customer_id {
        sql.push_str(&format!(" AND customer_id = '{}'", customer)); // SQL INJECTION!
    }
    if let Some(ref status) = filters.status {
        sql.push_str(&format!(" AND status = '{}'", status)); // SQL INJECTION!
    }
    sql
}
```

This is both unsafe (SQL injection) and fragile (missing spaces, wrong quoting, no type checking). We need something better.

## A Type-Safe Query Builder

Here's the approach: encode query constraints in the type system. Each filter method returns a new builder type, and the final `build()` method only exists on types that represent valid queries.

Let's build one step by step:

```rust
use sqlx::{PgPool, QueryBuilder, Postgres};
use chrono::NaiveDateTime;
use uuid::Uuid;

#[derive(Debug, Clone)]
pub struct OrderSearch {
    filters: Vec<Filter>,
    sort: Option<SortClause>,
    pagination: Pagination,
}

#[derive(Debug, Clone)]
enum Filter {
    CustomerId(Uuid),
    Status(String),
    CreatedAfter(NaiveDateTime),
    CreatedBefore(NaiveDateTime),
    MinTotal(i64),
    MaxTotal(i64),
    ProductId(Uuid),
}

#[derive(Debug, Clone)]
struct SortClause {
    column: SortColumn,
    direction: SortDirection,
}

#[derive(Debug, Clone)]
enum SortColumn {
    CreatedAt,
    Total,
    Status,
}

#[derive(Debug, Clone)]
enum SortDirection {
    Asc,
    Desc,
}

#[derive(Debug, Clone)]
struct Pagination {
    limit: i64,
    offset: i64,
}

impl OrderSearch {
    pub fn new() -> Self {
        Self {
            filters: Vec::new(),
            sort: None,
            pagination: Pagination {
                limit: 20,
                offset: 0,
            },
        }
    }

    pub fn customer(mut self, id: Uuid) -> Self {
        self.filters.push(Filter::CustomerId(id));
        self
    }

    pub fn status(mut self, status: impl Into<String>) -> Self {
        self.filters.push(Filter::Status(status.into()));
        self
    }

    pub fn created_after(mut self, dt: NaiveDateTime) -> Self {
        self.filters.push(Filter::CreatedAfter(dt));
        self
    }

    pub fn created_before(mut self, dt: NaiveDateTime) -> Self {
        self.filters.push(Filter::CreatedBefore(dt));
        self
    }

    pub fn min_total(mut self, amount: i64) -> Self {
        self.filters.push(Filter::MinTotal(amount));
        self
    }

    pub fn max_total(mut self, amount: i64) -> Self {
        self.filters.push(Filter::MaxTotal(amount));
        self
    }

    pub fn has_product(mut self, product_id: Uuid) -> Self {
        self.filters.push(Filter::ProductId(product_id));
        self
    }

    pub fn sort_by(mut self, column: SortColumn, direction: SortDirection) -> Self {
        self.sort = Some(SortClause { column, direction });
        self
    }

    pub fn page(mut self, page: i64, per_page: i64) -> Self {
        let per_page = per_page.min(100).max(1);
        let page = page.max(1);
        self.pagination = Pagination {
            limit: per_page,
            offset: (page - 1) * per_page,
        };
        self
    }

    pub fn build(&self) -> QueryBuilder<'_, Postgres> {
        let mut builder = QueryBuilder::new(
            "SELECT o.id, o.customer_id, o.status, o.total, o.created_at FROM orders o"
        );

        // Check if we need a JOIN for product filtering
        let needs_join = self.filters.iter().any(|f| matches!(f, Filter::ProductId(_)));
        if needs_join {
            builder.push(" JOIN order_items oi ON oi.order_id = o.id");
        }

        builder.push(" WHERE 1=1");

        for filter in &self.filters {
            match filter {
                Filter::CustomerId(id) => {
                    builder.push(" AND o.customer_id = ");
                    builder.push_bind(*id);
                }
                Filter::Status(s) => {
                    builder.push(" AND o.status = ");
                    builder.push_bind(s.clone());
                }
                Filter::CreatedAfter(dt) => {
                    builder.push(" AND o.created_at >= ");
                    builder.push_bind(*dt);
                }
                Filter::CreatedBefore(dt) => {
                    builder.push(" AND o.created_at <= ");
                    builder.push_bind(*dt);
                }
                Filter::MinTotal(amount) => {
                    builder.push(" AND o.total >= ");
                    builder.push_bind(*amount);
                }
                Filter::MaxTotal(amount) => {
                    builder.push(" AND o.total <= ");
                    builder.push_bind(*amount);
                }
                Filter::ProductId(id) => {
                    builder.push(" AND oi.product_id = ");
                    builder.push_bind(*id);
                }
            }
        }

        if let Some(ref sort) = self.sort {
            let col = match sort.column {
                SortColumn::CreatedAt => "o.created_at",
                SortColumn::Total => "o.total",
                SortColumn::Status => "o.status",
            };
            let dir = match sort.direction {
                SortDirection::Asc => "ASC",
                SortDirection::Desc => "DESC",
            };
            builder.push(format!(" ORDER BY {} {}", col, dir));
        } else {
            builder.push(" ORDER BY o.created_at DESC");
        }

        builder.push(" LIMIT ");
        builder.push_bind(self.pagination.limit);
        builder.push(" OFFSET ");
        builder.push_bind(self.pagination.offset);

        builder
    }
}
```

Usage looks clean and readable:

```rust
#[derive(Debug, sqlx::FromRow)]
struct Order {
    id: Uuid,
    customer_id: Uuid,
    status: String,
    total: i64,
    created_at: NaiveDateTime,
}

async fn search_orders(pool: &PgPool) -> Result<Vec<Order>, sqlx::Error> {
    let search = OrderSearch::new()
        .customer(some_customer_id)
        .status("shipped")
        .created_after(thirty_days_ago)
        .sort_by(SortColumn::Total, SortDirection::Desc)
        .page(2, 25);

    let mut query = search.build();
    query.build_query_as::<Order>()
        .fetch_all(pool)
        .await
}
```

Every filter uses `push_bind` — parameterized queries, no SQL injection possible. The `SortColumn` enum restricts sorting to known columns — you can't sort by `; DROP TABLE orders`. The pagination clamps values to reasonable ranges.

## Type-State Pattern for Enforcing Query Construction Rules

Want to go further? You can use the typestate pattern to enforce rules at compile time. For example: you must specify at least one filter before building the query (to prevent unbounded full-table scans).

```rust
use std::marker::PhantomData;

// Type states
struct Unfiltered;
struct Filtered;

struct SafeOrderSearch<State> {
    inner: OrderSearch,
    _state: PhantomData<State>,
}

impl SafeOrderSearch<Unfiltered> {
    pub fn new() -> Self {
        Self {
            inner: OrderSearch::new(),
            _state: PhantomData,
        }
    }

    // These methods transition from Unfiltered to Filtered
    pub fn customer(self, id: Uuid) -> SafeOrderSearch<Filtered> {
        SafeOrderSearch {
            inner: self.inner.customer(id),
            _state: PhantomData,
        }
    }

    pub fn status(self, status: impl Into<String>) -> SafeOrderSearch<Filtered> {
        SafeOrderSearch {
            inner: self.inner.status(status),
            _state: PhantomData,
        }
    }

    pub fn created_after(self, dt: NaiveDateTime) -> SafeOrderSearch<Filtered> {
        SafeOrderSearch {
            inner: self.inner.created_after(dt),
            _state: PhantomData,
        }
    }
}

impl SafeOrderSearch<Filtered> {
    // Additional filters after the first one stay in Filtered state
    pub fn customer(self, id: Uuid) -> Self {
        Self {
            inner: self.inner.customer(id),
            _state: PhantomData,
        }
    }

    pub fn status(self, status: impl Into<String>) -> Self {
        Self {
            inner: self.inner.status(status),
            _state: PhantomData,
        }
    }

    pub fn min_total(self, amount: i64) -> Self {
        Self {
            inner: self.inner.min_total(amount),
            _state: PhantomData,
        }
    }

    pub fn page(self, page: i64, per_page: i64) -> Self {
        Self {
            inner: self.inner.page(page, per_page),
            _state: PhantomData,
        }
    }

    pub fn sort_by(self, column: SortColumn, direction: SortDirection) -> Self {
        Self {
            inner: self.inner.sort_by(column, direction),
            _state: PhantomData,
        }
    }

    // build() only exists on Filtered — you can't build an unfiltered query
    pub fn build(&self) -> QueryBuilder<'_, Postgres> {
        self.inner.build()
    }
}
```

Now this compiles:

```rust
let search = SafeOrderSearch::new()
    .customer(id)      // transitions to Filtered
    .min_total(1000)   // stays Filtered
    .page(1, 20);

let query = search.build(); // OK: we're in Filtered state
```

And this doesn't:

```rust
let search = SafeOrderSearch::new()
    .page(1, 20);

// search.build(); // ERROR: build() doesn't exist on SafeOrderSearch<Unfiltered>
```

The compiler prevents unfiltered queries at build time. No runtime check, no forgotten validation, no full-table scan sneaking into production.

## Integrating with Web Handlers

Here's how the query builder connects to an Axum handler:

```rust
use axum::extract::Query;
use serde::Deserialize;

#[derive(Debug, Deserialize)]
struct OrderSearchParams {
    customer_id: Option<Uuid>,
    status: Option<String>,
    created_after: Option<NaiveDateTime>,
    created_before: Option<NaiveDateTime>,
    min_total: Option<i64>,
    max_total: Option<i64>,
    sort_by: Option<String>,
    sort_dir: Option<String>,
    page: Option<i64>,
    per_page: Option<i64>,
}

async fn search_orders_handler(
    State(state): State<AppState>,
    Query(params): Query<OrderSearchParams>,
) -> Result<Json<Vec<Order>>, AppError> {
    let mut search = OrderSearch::new();

    if let Some(id) = params.customer_id {
        search = search.customer(id);
    }
    if let Some(status) = params.status {
        search = search.status(status);
    }
    if let Some(dt) = params.created_after {
        search = search.created_after(dt);
    }
    if let Some(dt) = params.created_before {
        search = search.created_before(dt);
    }
    if let Some(min) = params.min_total {
        search = search.min_total(min);
    }
    if let Some(max) = params.max_total {
        search = search.max_total(max);
    }

    // Parse sort parameters safely
    if let Some(ref col) = params.sort_by {
        let column = match col.as_str() {
            "created_at" => SortColumn::CreatedAt,
            "total" => SortColumn::Total,
            "status" => SortColumn::Status,
            _ => SortColumn::CreatedAt, // safe default
        };
        let direction = match params.sort_dir.as_deref() {
            Some("asc") => SortDirection::Asc,
            _ => SortDirection::Desc,
        };
        search = search.sort_by(column, direction);
    }

    search = search.page(
        params.page.unwrap_or(1),
        params.per_page.unwrap_or(20),
    );

    let mut query = search.build();
    let orders = query
        .build_query_as::<Order>()
        .fetch_all(&state.db)
        .await
        .map_err(|e| AppError::Internal(e.to_string()))?;

    Ok(Json(orders))
}
```

The handler reads query parameters, maps them through the builder, and executes the result. No string concatenation. No SQL injection surface. Every filter value is parameterized. Every sort column is validated against the enum.

## Bulk Insert Builder

Query builders aren't just for SELECT. Here's a common pattern for batch inserts:

```rust
pub struct BulkInsert<'a> {
    items: Vec<NewOrder<'a>>,
}

struct NewOrder<'a> {
    customer_id: Uuid,
    status: &'a str,
    total: i64,
}

impl<'a> BulkInsert<'a> {
    pub fn new() -> Self {
        Self { items: Vec::new() }
    }

    pub fn add(mut self, order: NewOrder<'a>) -> Self {
        self.items.push(order);
        self
    }

    pub async fn execute(self, pool: &PgPool) -> Result<u64, sqlx::Error> {
        if self.items.is_empty() {
            return Ok(0);
        }

        let mut builder = QueryBuilder::new(
            "INSERT INTO orders (customer_id, status, total) "
        );

        builder.push_values(self.items.iter(), |mut b, order| {
            b.push_bind(order.customer_id)
                .push_bind(order.status)
                .push_bind(order.total);
        });

        let result = builder.build().execute(pool).await?;
        Ok(result.rows_affected())
    }
}

// Usage
async fn import_orders(pool: &PgPool) -> Result<(), sqlx::Error> {
    let inserted = BulkInsert::new()
        .add(NewOrder { customer_id: id1, status: "pending", total: 5000 })
        .add(NewOrder { customer_id: id2, status: "pending", total: 3200 })
        .add(NewOrder { customer_id: id3, status: "pending", total: 8900 })
        .execute(pool)
        .await?;

    println!("Inserted {} orders", inserted);
    Ok(())
}
```

SQLx's `push_values` method handles the `VALUES ($1, $2, $3), ($4, $5, $6), ...` syntax correctly, including parameter numbering. You'd be amazed how many hand-rolled implementations get the parameter numbering wrong.

## Performance Consideration: Prepared Statement Caching

One thing to be aware of: dynamic queries generated by `QueryBuilder` are not cached as prepared statements in the same way `query!` macros are. Each unique query string gets a new prepared statement on the Postgres side.

If your query builder produces a small number of distinct query shapes (e.g., "filter by customer only" vs "filter by customer and status"), Postgres will cache and reuse the plans. If every query is unique (different combinations of dozens of filters), you might see slightly higher planning overhead.

In practice, this is rarely a bottleneck — Postgres query planning takes microseconds for simple queries. But if you profile and see planning time dominating execution time, consider normalizing your query shapes by always including all filter columns and using a sentinel value for "no filter":

```rust
// Instead of conditionally adding filters, always include them
// with a "match everything" fallback
builder.push(" AND (o.customer_id = ");
builder.push_bind(customer_id_filter);
builder.push(" OR ");
builder.push_bind(customer_id_filter.is_none());
builder.push(")");
```

But honestly? Don't do this unless you've measured a problem. Clarity beats micro-optimization.

## What's Next

We've built queries that can't be wrong. But how do you know they're fast? And how do you test them against real data? Lesson 8 covers testing with real databases — why mocking SQL is usually wrong, and how to spin up test databases that give you confidence your queries actually work.
