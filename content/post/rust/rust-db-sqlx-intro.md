---
title: "Lesson 1: SQLx — Compile-time checked queries"
author: Atharva Pandey
series: "Rust Database Patterns"
lesson: 1
keywords: [Rust, rust, database, SQL, SQLx, compile-time, postgres, async]
tags: [Rust tutorial, rust, database, postgres]
date: '2024-10-20T11:23:00.000Z'
---

I shipped a typo in a SQL column name to production last year. The column was `user_nme` instead of `user_name`. The Go service compiled fine, the tests passed (they used mocks), and the bug sat in production for three hours before a customer reported it. Three hours of silent failures because the query returned zero rows instead of erroring out.

That was the day I started using SQLx in Rust. I haven't shipped a SQL typo since.

## The Problem with String-Based SQL

Most database libraries across every language work the same way: you write SQL as a string, pass it to a function, and hope for the best. The compiler has no idea what's inside that string. It could be valid SQL. It could be your grocery list. The compiler doesn't care — it's just a `&str`.

This means an entire category of bugs can only be caught at runtime:

- Misspelled column names
- Wrong number of bind parameters
- Type mismatches between your Rust types and database columns
- References to tables that don't exist
- Queries that are syntactically invalid

Every one of these is a production incident waiting to happen. And if your test suite uses mocks instead of a real database (which we'll address in lesson 8), you won't catch them there either.

## Enter SQLx

SQLx is an async Rust SQL toolkit that does something genuinely clever: it checks your queries against a real database at compile time. Not at runtime. Not in tests. At `cargo build` time.

Here's what that looks like in practice.

Add SQLx to your `Cargo.toml`:

```toml
[dependencies]
sqlx = { version = "0.8", features = ["runtime-tokio", "postgres", "macros", "chrono", "uuid"] }
tokio = { version = "1", features = ["full"] }
chrono = { version = "0.4", features = ["serde"] }
uuid = { version = "1", features = ["v4", "serde"] }
serde = { version = "1", features = ["derive"] }
```

Now let's define a table and query it:

```rust
use sqlx::postgres::PgPoolOptions;
use sqlx::FromRow;
use chrono::NaiveDateTime;
use uuid::Uuid;

#[derive(Debug, FromRow)]
struct User {
    id: Uuid,
    username: String,
    email: String,
    created_at: NaiveDateTime,
}

#[tokio::main]
async fn main() -> Result<(), sqlx::Error> {
    let pool = PgPoolOptions::new()
        .max_connections(5)
        .connect("postgres://localhost/myapp")
        .await?;

    // This query is checked at compile time
    let users = sqlx::query_as!(
        User,
        "SELECT id, username, email, created_at FROM users WHERE username = $1",
        "atharva"
    )
    .fetch_all(&pool)
    .await?;

    for user in users {
        println!("{}: {}", user.username, user.email);
    }

    Ok(())
}
```

The `query_as!` macro is where the magic happens. During compilation, SQLx connects to your database (using the `DATABASE_URL` environment variable), sends the query to Postgres for parsing, and verifies:

1. The table `users` exists
2. The columns `id`, `username`, `email`, `created_at` exist
3. The types match your `User` struct
4. The bind parameter `$1` expects a type compatible with `&str`

If any of these checks fail, **your code won't compile**. That's not a linter warning. That's a hard compiler error.

## The Two Modes: Online and Offline

There's an obvious question here — what about CI? What about building on machines that don't have your database running?

SQLx handles this with offline mode. You run `cargo sqlx prepare` locally (where your database is running), and it generates a `.sqlx/` directory with cached query metadata. Check that directory into git, and CI can verify queries without a live database.

```bash
# Generate offline query data
cargo sqlx prepare

# Now CI can build without DATABASE_URL
SQLX_OFFLINE=true cargo build
```

This is one of those design decisions that shows the SQLx team actually ships production software. They understood that "just connect to the database during builds" doesn't work in real CI pipelines.

## query! vs query_as! vs query_scalar!

SQLx gives you several macro variants, and picking the right one matters.

**`query!`** returns an anonymous struct — great for one-off queries:

```rust
let record = sqlx::query!(
    "SELECT username, email FROM users WHERE id = $1",
    user_id
)
.fetch_one(&pool)
.await?;

// Access fields directly
println!("{}", record.username);
println!("{}", record.email);
```

**`query_as!`** maps to a named struct — what you'll use most of the time:

```rust
#[derive(Debug, FromRow)]
struct UserSummary {
    username: String,
    email: String,
}

let user = sqlx::query_as!(
    UserSummary,
    "SELECT username, email FROM users WHERE id = $1",
    user_id
)
.fetch_one(&pool)
.await?;
```

**`query_scalar!`** returns a single value — perfect for counts and existence checks:

```rust
let count = sqlx::query_scalar!(
    "SELECT COUNT(*) FROM users WHERE created_at > $1",
    cutoff_date
)
.fetch_one(&pool)
.await?;

println!("Users created after cutoff: {}", count.unwrap_or(0));
```

## Handling Nullable Columns

One place where SQLx shines over string-based approaches is null handling. If a column is nullable in your schema, SQLx forces you to use `Option<T>` in your struct. If you try to use `String` for a nullable `VARCHAR`, it won't compile.

```rust
#[derive(Debug, FromRow)]
struct UserProfile {
    id: Uuid,
    username: String,        // NOT NULL column
    bio: Option<String>,     // nullable column
    avatar_url: Option<String>, // nullable column
}

let profile = sqlx::query_as!(
    UserProfile,
    "SELECT id, username, bio, avatar_url FROM user_profiles WHERE id = $1",
    user_id
)
.fetch_one(&pool)
.await?;

// The compiler knows bio might be None
match profile.bio {
    Some(bio) => println!("Bio: {}", bio),
    None => println!("No bio set"),
}
```

This is genuinely better than what most ORMs give you. In many languages, nullable columns are silently mapped to empty strings or zero values, and you find out about the null at runtime when something breaks downstream.

## Fetch Strategies

SQLx provides several ways to retrieve results, and the difference matters for performance:

```rust
// Fetch exactly one row — errors if 0 or 2+ rows
let user = sqlx::query_as!(User, "SELECT * FROM users WHERE id = $1", id)
    .fetch_one(&pool)
    .await?;

// Fetch zero or one row — returns Option
let maybe_user = sqlx::query_as!(User, "SELECT * FROM users WHERE id = $1", id)
    .fetch_optional(&pool)
    .await?;

// Fetch all rows into a Vec — careful with large result sets
let all_users = sqlx::query_as!(User, "SELECT * FROM users LIMIT 100")
    .fetch_all(&pool)
    .await?;

// Stream rows one at a time — for large result sets
use futures::TryStreamExt;

let mut stream = sqlx::query_as!(User, "SELECT * FROM users")
    .fetch(&pool);

while let Some(user) = stream.try_next().await? {
    process_user(user);
}
```

The streaming approach with `fetch()` is critical for large datasets. If you `fetch_all` a million rows, you're loading them all into memory at once. With streaming, you process one at a time.

## Dynamic Queries with QueryBuilder

The compile-time macros are great until you need dynamic queries — WHERE clauses that change based on user input, optional filters, that sort of thing. For those, SQLx provides `QueryBuilder`:

```rust
use sqlx::QueryBuilder;
use sqlx::Postgres;

fn build_user_search(
    username: Option<&str>,
    email: Option<&str>,
    min_age: Option<i32>,
) -> QueryBuilder<'_, Postgres> {
    let mut builder = QueryBuilder::new("SELECT * FROM users WHERE 1=1");

    if let Some(name) = username {
        builder.push(" AND username ILIKE ");
        builder.push_bind(format!("%{}%", name));
    }

    if let Some(email_val) = email {
        builder.push(" AND email = ");
        builder.push_bind(email_val);
    }

    if let Some(age) = min_age {
        builder.push(" AND age >= ");
        builder.push_bind(age);
    }

    builder
}

#[tokio::main]
async fn main() -> Result<(), sqlx::Error> {
    let pool = PgPoolOptions::new()
        .max_connections(5)
        .connect("postgres://localhost/myapp")
        .await?;

    let mut query = build_user_search(Some("ath"), None, Some(21));
    let users = query
        .build_query_as::<User>()
        .fetch_all(&pool)
        .await?;

    for user in users {
        println!("{:?}", user);
    }

    Ok(())
}
```

Notice that `push_bind` handles parameterization — you're never interpolating user input into the SQL string. SQL injection is structurally impossible when you use the builder correctly.

## Error Handling Done Right

SQLx errors are actually useful, unlike the opaque errors you get from some database libraries:

```rust
use sqlx::Error;

async fn get_user(pool: &sqlx::PgPool, id: Uuid) -> Result<User, String> {
    sqlx::query_as!(User, "SELECT id, username, email, created_at FROM users WHERE id = $1", id)
        .fetch_one(pool)
        .await
        .map_err(|e| match e {
            Error::RowNotFound => format!("No user found with id {}", id),
            Error::Database(db_err) => {
                // You can inspect the Postgres error code
                if let Some(code) = db_err.code() {
                    format!("Database error (code {}): {}", code, db_err)
                } else {
                    format!("Database error: {}", db_err)
                }
            }
            Error::PoolTimedOut => "Connection pool exhausted".to_string(),
            _ => format!("Unexpected error: {}", e),
        })
}
```

The `Error::Database` variant gives you access to the underlying Postgres error code, which is invaluable for handling constraint violations, unique conflicts, and other domain-specific errors.

## Why Not Just Use an ORM?

I'll cover Diesel (Rust's main ORM) in the next lesson, but here's my take: SQLx is the right default for most Rust projects. You write real SQL, which means you can use every Postgres feature without fighting an abstraction layer. CTEs, window functions, lateral joins, JSON operators — it all just works.

ORMs add value when you have lots of simple CRUD and want to avoid writing boilerplate. But in my experience, the moment your queries get interesting — and they always do — you end up fighting the ORM's query builder instead of just writing the SQL you already know.

SQLx gives you the safety benefits of compile-time checking without taking away your ability to write the exact SQL you need.

## What's Next

Next up, we'll look at Diesel — the ORM approach to database access in Rust. It solves a different set of problems than SQLx, and understanding both will help you pick the right tool for your project.

The key takeaway here: if your database queries are strings that only get validated at runtime, you're leaving bugs on the table. SQLx moves that validation to compile time, and the difference in shipped-bug rate is dramatic.
