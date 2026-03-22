---
title: "Lesson 2: Diesel — The ORM approach"
author: Atharva Pandey
series: "Rust Database Patterns"
lesson: 2
keywords: [Rust, rust, database, SQL, Diesel, ORM, postgres, schema]
tags: [Rust tutorial, rust, database, postgres]
date: '2024-10-22T08:47:00.000Z'
---

I was two weeks into a project where I had to write about 40 CRUD endpoints for an admin panel. Each one needed the same pattern: validate input, build a query, map results to a struct, handle errors. By endpoint number six, I was copy-pasting SQLx queries and changing column names. That's when a colleague asked, "Why aren't you using Diesel?"

He was right. Sometimes you don't want to write SQL. Sometimes you want the boilerplate to disappear.

## The Problem with Raw SQL at Scale

SQLx is excellent — I covered it in lesson 1 and I still reach for it in most projects. But there's a class of applications where writing raw SQL becomes tedious: CRUD-heavy services, admin panels, data pipelines with lots of simple inserts and selects.

When you're writing your fifteenth `INSERT INTO users (username, email, created_at) VALUES ($1, $2, $3) RETURNING id, username, email, created_at`, the compile-time checking doesn't make you feel clever anymore. It makes you feel like a human code generator.

ORMs exist to solve this. And Diesel is Rust's most mature ORM.

## Setting Up Diesel

Diesel requires a CLI tool for migrations and schema generation. Install it first:

```bash
cargo install diesel_cli --no-default-features --features postgres
```

Then set up your project:

```bash
echo DATABASE_URL=postgres://localhost/myapp > .env
diesel setup
```

This creates a `migrations/` directory and a `diesel.toml` config file.

Your `Cargo.toml`:

```toml
[dependencies]
diesel = { version = "2.2", features = ["postgres", "chrono", "uuid"] }
dotenvy = "0.15"
chrono = { version = "0.4", features = ["serde"] }
uuid = { version = "1", features = ["v4", "serde"] }
serde = { version = "1", features = ["derive"] }
```

## Migrations and Schema Generation

Diesel manages your schema through migrations. Create one:

```bash
diesel migration generate create_users
```

This generates two files: `up.sql` and `down.sql`. Fill them in:

```sql
-- migrations/XXXX_create_users/up.sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR NOT NULL UNIQUE,
    email VARCHAR NOT NULL UNIQUE,
    bio TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

```sql
-- migrations/XXXX_create_users/down.sql
DROP TABLE users;
```

Run the migration:

```bash
diesel migration run
```

Here's where Diesel diverges from SQLx in a fundamental way. After running the migration, Diesel generates a `src/schema.rs` file that describes your tables in Rust:

```rust
// src/schema.rs (auto-generated — don't edit by hand)
diesel::table! {
    users (id) {
        id -> Uuid,
        username -> Varchar,
        email -> Varchar,
        bio -> Nullable<Text>,
        created_at -> Timestamp,
    }
}
```

This `table!` macro is the foundation of Diesel's type safety. Every query you write goes through this schema definition, and the compiler ensures your Rust types match your database types.

## Models: The Diesel Way

Diesel uses separate structs for reading and writing, which feels weird at first but makes a lot of sense:

```rust
use diesel::prelude::*;
use chrono::NaiveDateTime;
use uuid::Uuid;

// For reading from the database
#[derive(Debug, Queryable, Selectable)]
#[diesel(table_name = crate::schema::users)]
pub struct User {
    pub id: Uuid,
    pub username: String,
    pub email: String,
    pub bio: Option<String>,
    pub created_at: NaiveDateTime,
}

// For inserting new records
#[derive(Debug, Insertable)]
#[diesel(table_name = crate::schema::users)]
pub struct NewUser<'a> {
    pub username: &'a str,
    pub email: &'a str,
    pub bio: Option<&'a str>,
}

// For updating existing records
#[derive(Debug, AsChangeset)]
#[diesel(table_name = crate::schema::users)]
pub struct UpdateUser<'a> {
    pub username: Option<&'a str>,
    pub email: Option<&'a str>,
    pub bio: Option<Option<&'a str>>, // Option<Option<T>> to distinguish "don't update" from "set to null"
}
```

The `Option<Option<&str>>` for `bio` in `UpdateUser` is one of those things that looks nuts until you realize the problem it solves. `None` means "don't change this field." `Some(None)` means "set this field to NULL." `Some(Some("hello"))` means "set this to 'hello'." Three distinct states, all type-safe.

## CRUD Operations

Here's where Diesel earns its keep — the CRUD code is significantly less boilerplate than raw SQL:

```rust
use diesel::prelude::*;
use crate::schema::users;
use crate::schema::users::dsl::*;

pub fn establish_connection() -> PgConnection {
    let database_url = std::env::var("DATABASE_URL")
        .expect("DATABASE_URL must be set");
    PgConnection::establish(&database_url)
        .expect("Error connecting to database")
}

// CREATE
pub fn create_user(conn: &mut PgConnection, new_user: &NewUser) -> QueryResult<User> {
    diesel::insert_into(users::table)
        .values(new_user)
        .returning(User::as_returning())
        .get_result(conn)
}

// READ
pub fn find_user_by_id(conn: &mut PgConnection, user_id: Uuid) -> QueryResult<User> {
    users.find(user_id).first(conn)
}

pub fn find_users_by_username(
    conn: &mut PgConnection,
    name_pattern: &str,
) -> QueryResult<Vec<User>> {
    users
        .filter(username.ilike(format!("%{}%", name_pattern)))
        .order(created_at.desc())
        .limit(50)
        .load(conn)
}

// UPDATE
pub fn update_user(
    conn: &mut PgConnection,
    user_id: Uuid,
    changes: &UpdateUser,
) -> QueryResult<User> {
    diesel::update(users.find(user_id))
        .set(changes)
        .returning(User::as_returning())
        .get_result(conn)
}

// DELETE
pub fn delete_user(conn: &mut PgConnection, user_id: Uuid) -> QueryResult<usize> {
    diesel::delete(users.find(user_id)).execute(conn)
}
```

Compare this to writing the equivalent five queries by hand in SQLx. There's no SQL string to mess up, no bind parameters to count, no column ordering to keep track of. The schema module handles all of that.

## Complex Queries

Diesel's query builder handles joins, aggregations, and subqueries — though some operations are more ergonomic than others:

```rust
use crate::schema::{users, posts, comments};

// Inner join
pub fn users_with_posts(conn: &mut PgConnection) -> QueryResult<Vec<(User, Post)>> {
    users::table
        .inner_join(posts::table)
        .select((User::as_select(), Post::as_select()))
        .load(conn)
}

// Left join with optional result
pub fn users_with_optional_posts(
    conn: &mut PgConnection,
) -> QueryResult<Vec<(User, Option<Post>)>> {
    users::table
        .left_join(posts::table)
        .select((User::as_select(), Post::as_select().nullable()))
        .load(conn)
}

// Aggregation
pub fn post_count_by_user(conn: &mut PgConnection) -> QueryResult<Vec<(Uuid, i64)>> {
    use diesel::dsl::count;

    posts::table
        .group_by(posts::user_id)
        .select((posts::user_id, count(posts::id)))
        .load(conn)
}

// Filtering with multiple conditions
pub fn active_users_with_recent_posts(
    conn: &mut PgConnection,
    since: NaiveDateTime,
) -> QueryResult<Vec<User>> {
    users::table
        .filter(
            users::id.eq_any(
                posts::table
                    .filter(posts::published_at.gt(since))
                    .select(posts::user_id)
            )
        )
        .load(conn)
}
```

## Where Diesel Gets Annoying

I won't pretend Diesel is all sunshine. There are real pain points.

**Compile times.** Diesel's derive macros and schema types generate a lot of code. On large projects with dozens of tables, compile times can balloon. This is the number one complaint I hear from Diesel users.

**Complex queries.** The moment you need a CTE, a window function, or Postgres-specific JSON operators, you're reaching for `diesel::sql_query` — which is basically raw SQL mode. Diesel's query builder doesn't cover every SQL feature.

```rust
// When the query builder can't express what you need
let results = diesel::sql_query(
    "WITH recent AS (
        SELECT user_id, COUNT(*) as post_count
        FROM posts
        WHERE published_at > NOW() - INTERVAL '30 days'
        GROUP BY user_id
    )
    SELECT u.*, r.post_count
    FROM users u
    JOIN recent r ON u.id = r.user_id
    ORDER BY r.post_count DESC"
)
.load::<UserWithPostCount>(conn)?;
```

At that point, you've lost the type safety that was the whole reason to use Diesel, and you might as well be using SQLx.

**Async support.** Diesel is synchronous by default. There's `diesel-async` as a separate crate, but it's not as mature as Diesel itself. If your application is heavily async (most web servers are), this adds friction.

```rust
// Using diesel-async
use diesel_async::{RunQueryDsl, AsyncPgConnection};
use diesel_async::pooled_connection::deadpool::Pool;

pub async fn find_user_async(
    pool: &Pool<AsyncPgConnection>,
    user_id: Uuid,
) -> QueryResult<User> {
    let mut conn = pool.get().await.map_err(|e| {
        diesel::result::Error::DatabaseError(
            diesel::result::DatabaseErrorKind::Unknown,
            Box::new(e.to_string()),
        )
    })?;
    users::table.find(user_id).first(&mut conn).await
}
```

## Diesel vs SQLx: My Take

Here's my honest assessment after using both in production:

**Use Diesel when:**
- You have lots of CRUD operations (admin panels, internal tools, data-heavy APIs)
- Your team is more comfortable with ORM patterns than raw SQL
- You want the schema-as-code approach with generated migrations
- Most of your queries are standard selects, inserts, updates, deletes

**Use SQLx when:**
- Your queries are complex (analytics, reporting, anything with CTEs or window functions)
- You want full access to Postgres features without an abstraction layer
- You're already comfortable writing SQL
- Your application is async-first

**Use both when:**
- You have a mixed workload — Diesel for CRUD, SQLx for reporting queries

There's no law that says you can only use one. I've worked on projects that use Diesel for the admin API and SQLx for the analytics dashboard, and it works fine.

## Practical Tip: The schema.rs Workflow

One thing that trips people up with Diesel is the `schema.rs` workflow. When you or a teammate adds a migration, you need to regenerate the schema file:

```bash
diesel migration run  # applies the migration
diesel print-schema > src/schema.rs  # regenerates schema.rs
```

Put this in a Makefile or just-file. Otherwise, you'll waste time on mysterious compile errors because your schema.rs is out of date.

```makefile
.PHONY: db-migrate
db-migrate:
	diesel migration run
	diesel print-schema > src/schema.rs
	cargo fmt
```

## What's Next

Both SQLx and Diesel need database connections, and managing those connections efficiently is critical for production applications. In lesson 3, we'll look at connection pooling with deadpool and bb8 — because opening a new TCP connection for every query is a great way to kill your database server.
