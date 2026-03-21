---
title: "Lesson 10: Over-Engineering CRUD — Not every API needs a hexagonal architecture"
author: Atharva Pandey
series: "Go Anti-Patterns & Code Smells"
lesson: 10
keywords:
  - Go
  - golang
  - anti-patterns
  - code quality
tags:
  - Go tutorial
  - golang
  - code quality
date: '2025-07-02T00:00:00.000Z'
---

There is a category of service that I have built many times and will build many more times: an API that creates, reads, updates, and deletes records in a relational database, with some validation and authentication. There is nothing glamorous about it, and there is nothing architecturally complex about it either. The correct implementation is straightforward, fast to build, easy to test, and easy to read. The over-engineered implementation has event sourcing, CQRS, a message bus, six layers of abstraction, and takes three months to build something that the straightforward implementation would have shipped in two weeks.

I have been the developer who over-engineered a CRUD service. I have also been the developer who inherited one and spent weeks unwinding the architecture before I could add a single feature. This lesson is about the judgment call: when simple is right, when complexity earns its keep, and how to resist the gravitational pull of patterns that are impressive-looking but inappropriate.

## The Problem

Applying domain-driven design, CQRS, and event sourcing to a feature that is genuinely just CRUD:

```go
// WRONG — DDD aggregate, command bus, and event store for a blog post API
type CreatePostCommand struct {
    AuthorID string
    Title    string
    Body     string
}

type PostCreatedEvent struct {
    PostID    string
    AuthorID  string
    Title     string
    CreatedAt time.Time
}

type PostAggregate struct {
    id         string
    title      string
    body       string
    events     []PostCreatedEvent
    version    int
}

func (p *PostAggregate) Handle(cmd CreatePostCommand) error {
    event := PostCreatedEvent{
        PostID:    uuid.New().String(),
        AuthorID:  cmd.AuthorID,
        Title:     cmd.Title,
        CreatedAt: time.Now(),
    }
    p.Apply(event)
    return nil
}

func (p *PostAggregate) Apply(event PostCreatedEvent) {
    p.id = event.PostID
    p.title = event.Title
    p.events = append(p.events, event)
    p.version++
}
// ... event store, projection, read model, command bus...
// For a blog post. That users write and read. In a database.
```

For a blog post API, this is months of engineering work and ongoing maintenance overhead to solve a problem that `INSERT INTO posts VALUES (...)` solves in one query.

The second failure: generic repository patterns that introduce abstraction for its own sake:

```go
// WRONG — generic repository that obscures what queries are actually run
type Repository[T any] interface {
    FindByID(id string) (T, error)
    FindAll(filters map[string]any) ([]T, error) // untyped filter map
    Save(entity T) error
    Delete(id string) error
}

// The "FindAll(filters map[string]any)" signature is a red flag:
// - What keys are valid? Undocumented.
// - What happens with unknown keys? Undefined.
// - How do you sort? Unclear.
// - How do you paginate? Missing.
// - The implementation requires SQL building from a map, which is complex and fragile.
```

## The Idiomatic Way

For CRUD, write direct, typed functions that map closely to the queries they execute. Explicitness beats abstraction here:

```go
// RIGHT — direct, typed CRUD functions with clear purpose
type PostStore struct {
    db *sql.DB
}

func (s *PostStore) Create(ctx context.Context, authorID, title, body string) (*Post, error) {
    var p Post
    err := s.db.QueryRowContext(ctx,
        `INSERT INTO posts (id, author_id, title, body, created_at)
         VALUES ($1, $2, $3, $4, NOW())
         RETURNING id, author_id, title, body, created_at`,
        uuid.New().String(), authorID, title, body,
    ).Scan(&p.ID, &p.AuthorID, &p.Title, &p.Body, &p.CreatedAt)
    if err != nil {
        return nil, fmt.Errorf("create post: %w", err)
    }
    return &p, nil
}

func (s *PostStore) GetByID(ctx context.Context, id string) (*Post, error) {
    var p Post
    err := s.db.QueryRowContext(ctx,
        `SELECT id, author_id, title, body, created_at FROM posts WHERE id = $1`, id,
    ).Scan(&p.ID, &p.AuthorID, &p.Title, &p.Body, &p.CreatedAt)
    if errors.Is(err, sql.ErrNoRows) {
        return nil, ErrNotFound
    }
    if err != nil {
        return nil, fmt.Errorf("get post: %w", err)
    }
    return &p, nil
}

type ListPostsFilter struct {
    AuthorID  string // empty means no filter
    Limit     int    // 0 means use default
    Offset    int
    SortBy    string // "created_at" or "title"
    SortOrder string // "asc" or "desc"
}

func (s *PostStore) List(ctx context.Context, f ListPostsFilter) ([]*Post, error) {
    // Explicit, type-safe filter construction — not a map[string]any
    query := `SELECT id, author_id, title, body, created_at FROM posts`
    args := []any{}
    conditions := []string{}

    if f.AuthorID != "" {
        args = append(args, f.AuthorID)
        conditions = append(conditions, fmt.Sprintf("author_id = $%d", len(args)))
    }

    if len(conditions) > 0 {
        query += " WHERE " + strings.Join(conditions, " AND ")
    }

    sortBy := "created_at"
    if f.SortBy == "title" {
        sortBy = "title"
    }
    sortOrder := "DESC"
    if f.SortOrder == "asc" {
        sortOrder = "ASC"
    }
    query += fmt.Sprintf(" ORDER BY %s %s", sortBy, sortOrder)

    limit := 20
    if f.Limit > 0 && f.Limit <= 100 {
        limit = f.Limit
    }
    query += fmt.Sprintf(" LIMIT %d OFFSET %d", limit, f.Offset)

    rows, err := s.db.QueryContext(ctx, query, args...)
    if err != nil {
        return nil, fmt.Errorf("list posts: %w", err)
    }
    defer rows.Close()

    var posts []*Post
    for rows.Next() {
        var p Post
        if err := rows.Scan(&p.ID, &p.AuthorID, &p.Title, &p.Body, &p.CreatedAt); err != nil {
            return nil, fmt.Errorf("scan post: %w", err)
        }
        posts = append(posts, &p)
    }
    return posts, rows.Err()
}
```

The handler for this is equally direct — no command bus, no aggregate, no events:

```go
// RIGHT — simple HTTP handler for simple CRUD
type PostHandler struct {
    store *PostStore
}

func (h *PostHandler) Create(w http.ResponseWriter, r *http.Request) {
    var req struct {
        Title string `json:"title" validate:"required,max=200"`
        Body  string `json:"body"  validate:"required"`
    }
    if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
        http.Error(w, "invalid JSON", http.StatusBadRequest)
        return
    }

    user := UserFromContext(r.Context())
    post, err := h.store.Create(r.Context(), user.ID, req.Title, req.Body)
    if err != nil {
        http.Error(w, "internal error", http.StatusInternalServerError)
        return
    }

    w.WriteHeader(http.StatusCreated)
    json.NewEncoder(w).Encode(post)
}
```

## In The Wild

**When does complexity earn its keep?** CQRS and event sourcing are the right answer when you have genuinely different read and write requirements — high-throughput write paths with complex read models, or when you need a full audit history that can replay state. A blog post, a user profile, a product listing — these are not those things. A financial ledger, a distributed inventory system, a collaborative document editor — those might be.

**sqlx and pgx.** If you want slightly more ergonomic SQL without the overhead of a full ORM, `jmoiron/sqlx` adds named queries and struct scanning to `database/sql`. `jackc/pgx` is a PostgreSQL driver with connection pooling and batch support. Either is a reasonable choice. Neither requires abandoning direct SQL.

**Generated CRUD.** For services where you truly want thin CRUD over a database schema, `sqlc` generates type-safe Go code from SQL queries. You write SQL, `sqlc` generates the Go functions, and you get the explicitness of raw SQL with type safety. This is the right amount of abstraction for pure CRUD — automated, but transparent.

## The Gotchas

**"We might need CQRS later."** CQRS is not an easy retrofit to a CRUD codebase. But starting with CQRS on a CRUD problem is adding complexity now for a migration you may never need to do. If you do need to add a separate read model later, the migration from direct SQL to CQRS is a well-understood pattern — you can do it when you actually need it.

**ORM magic.** Full ORMs like GORM add convenience — less SQL, automatic migrations, hook callbacks — and introduce complexity — implicit queries, N+1 problems, magical field mapping. For simple CRUD they work fine. For anything involving complex joins, transactions, or performance-sensitive queries, knowing exactly what SQL is being executed matters. `sqlc` or `sqlx` with explicit queries is usually the better tradeoff.

**Testing CRUD.** Integration tests against a real database (using testcontainers or a local Postgres in CI) are more valuable than unit tests with SQL mocks for data-layer code. The query is the logic; mocking it out and testing the Go code that calls it tests nothing meaningful. Write a test that actually runs the query against a test database.

## Key Takeaway

The right architecture for your service is the simplest one that correctly solves the problem you actually have. For CRUD: direct SQL queries, typed store functions, thin HTTP handlers, and integration tests against a real database. Complexity — CQRS, event sourcing, hexagonal architecture — is appropriate when the problem demands it, not as a default. Build the simple version first. Add architecture when the system's actual requirements justify it.

---

**Go Anti-Patterns & Code Smells**

Previous: [Lesson 9: Fake Clean Architecture — Layers without purpose are just folders](/post/go/go-anti-fake-clean-arch/)

---

🎓 **Course Complete!** You have finished Go Anti-Patterns & Code Smells. These ten lessons cover the most common design and structural mistakes in Go codebases — from over-abstracting interfaces to over-engineering CRUD. The thread through all of them is the same: add complexity when it solves a real problem, not in anticipation of problems you might not have.
