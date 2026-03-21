---
title: "Lesson 4: The Repository Pattern — Sometimes a function is enough"
author: Atharva Pandey
series: "Go Database Patterns"
lesson: 4
keywords:
  - Go
  - golang
  - database
  - postgres
  - repository pattern
  - interface
  - testability
  - over-engineering
tags:
  - Go tutorial
  - golang
  - database
date: '2025-10-02T00:00:00.000Z'
---

The repository pattern is one of those ideas that sounds great in an architecture talk and causes real pain when applied indiscriminately to a Go codebase. I've seen teams add a `UserRepository` interface with five methods, a concrete `postgresUserRepository` implementation, and a `mockUserRepository` for tests — and then wonder why everything takes three times as long to write. I've also seen teams skip the pattern entirely and end up with `*sql.DB` threaded through 40 different functions, impossible to test without a real database.

The truth is somewhere in between, and the right choice depends on what you're building.

## The Problem

The most over-engineered version of the repository pattern looks like this:

```go
// WRONG — interface-first design adds abstraction with no benefit yet
type UserRepository interface {
    GetByID(ctx context.Context, id int) (*User, error)
    GetByEmail(ctx context.Context, email string) (*User, error)
    Create(ctx context.Context, u *User) error
    Update(ctx context.Context, u *User) error
    Delete(ctx context.Context, id int) error
    ListActive(ctx context.Context, limit, offset int) ([]*User, error)
}

type postgresUserRepository struct {
    db *sql.DB
}

// ... 200 lines of implementation ...

type mockUserRepository struct {
    users map[int]*User
    // ... mock implementations for every method ...
}
```

The problem isn't that the interface is wrong. It's that it was created before anyone had a reason for it. Now every time you add a new query — say, `GetByVerificationToken` — you have to update the interface, the Postgres implementation, and the mock. That's three files for one new query. The interface has become a tax.

The opposite extreme is just as bad:

```go
// WRONG — *sql.DB passed everywhere, no structure, untestable
func HandleGetUser(db *sql.DB, w http.ResponseWriter, r *http.Request) {
    id := chi.URLParam(r, "id")
    // Direct DB access in the handler — can't test without a real DB
    var user User
    err := db.QueryRowContext(r.Context(),
        "SELECT id, name, email FROM users WHERE id = $1", id,
    ).Scan(&user.ID, &user.Name, &user.Email)
    // ...
}

func HandleCreateOrder(db *sql.DB, w http.ResponseWriter, r *http.Request) {
    // 50 lines of SQL mixed with HTTP handling
    // Transactions, multi-table inserts, everything here
}
```

Database logic directly in handlers is hard to test, hard to reuse, and hard to reason about. The SQL is tangled with HTTP concerns. You can't unit-test the business logic without spinning up a database.

## The Idiomatic Way

The pragmatic middle ground is a concrete struct with methods, and an interface only when you need one for testing or swapping implementations:

```go
// RIGHT — concrete type with methods, no premature interface
type UserStore struct {
    db *sql.DB
}

func NewUserStore(db *sql.DB) *UserStore {
    return &UserStore{db: db}
}

func (s *UserStore) GetByID(ctx context.Context, id int) (*User, error) {
    var u User
    err := s.db.QueryRowContext(ctx,
        "SELECT id, name, email, created_at FROM users WHERE id = $1", id,
    ).Scan(&u.ID, &u.Name, &u.Email, &u.CreatedAt)
    if errors.Is(err, sql.ErrNoRows) {
        return nil, ErrUserNotFound
    }
    if err != nil {
        return nil, fmt.Errorf("get user: %w", err)
    }
    return &u, nil
}

func (s *UserStore) Create(ctx context.Context, name, email string) (*User, error) {
    var u User
    err := s.db.QueryRowContext(ctx,
        "INSERT INTO users (name, email) VALUES ($1, $2) RETURNING id, name, email, created_at",
        name, email,
    ).Scan(&u.ID, &u.Name, &u.Email, &u.CreatedAt)
    if err != nil {
        return nil, fmt.Errorf("create user: %w", err)
    }
    return &u, nil
}
```

Now the HTTP handler takes `*UserStore` directly. No interface. No mock. You add new queries by adding new methods to `UserStore`. No ceremony.

When do you add the interface? When you have a specific reason: you want to test business logic in isolation without a database, you have multiple implementations (Postgres and an in-memory store for tests), or you're publishing a library where callers need to provide their own implementation:

```go
// RIGHT — add the interface when you have a reason for it
type UserGetter interface {
    GetByID(ctx context.Context, id int) (*User, error)
}

// Your handler only depends on the subset of UserStore it needs
type UserHandler struct {
    users UserGetter
}

func NewUserHandler(users UserGetter) *UserHandler {
    return &UserHandler{users: users}
}

func (h *UserHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    idStr := chi.URLParam(r, "id")
    id, err := strconv.Atoi(idStr)
    if err != nil {
        http.Error(w, "invalid id", http.StatusBadRequest)
        return
    }

    user, err := h.users.GetByID(r.Context(), id)
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

// In tests — simple mock, only the methods you need
type mockUserGetter struct {
    user *User
    err  error
}

func (m *mockUserGetter) GetByID(_ context.Context, _ int) (*User, error) {
    return m.user, m.err
}

func TestUserHandler_NotFound(t *testing.T) {
    h := NewUserHandler(&mockUserGetter{err: ErrUserNotFound})
    req := httptest.NewRequest("GET", "/users/1", nil)
    rec := httptest.NewRecorder()
    h.ServeHTTP(rec, req)
    if rec.Code != http.StatusNotFound {
        t.Errorf("expected 404, got %d", rec.Code)
    }
}
```

The interface is small — just `GetByID`. The mock is five lines. The handler is testable without a database.

## In The Wild

For complex write operations that span multiple tables, the store method takes a transaction parameter:

```go
// RIGHT — accepting *sql.Tx for operations that participate in a transaction
type OrderStore struct {
    db *sql.DB
}

// querier is satisfied by both *sql.DB and *sql.Tx
type querier interface {
    QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row
    ExecContext(ctx context.Context, query string, args ...any) (sql.Result, error)
}

func (s *OrderStore) createOrderInTx(ctx context.Context, q querier, userID int, total int) (int, error) {
    var orderID int
    err := q.QueryRowContext(ctx,
        "INSERT INTO orders (user_id, total) VALUES ($1, $2) RETURNING id",
        userID, total,
    ).Scan(&orderID)
    return orderID, err
}

func (s *OrderStore) decrementInventoryInTx(ctx context.Context, q querier, productID, qty int) error {
    result, err := q.ExecContext(ctx,
        "UPDATE inventory SET qty = qty - $1 WHERE product_id = $2 AND qty >= $1",
        qty, productID,
    )
    if err != nil {
        return err
    }
    affected, _ := result.RowsAffected()
    if affected == 0 {
        return ErrInsufficientInventory
    }
    return nil
}

func (s *OrderStore) PlaceOrder(ctx context.Context, userID, productID, qty, total int) (int, error) {
    tx, err := s.db.BeginTx(ctx, nil)
    if err != nil {
        return 0, err
    }
    defer tx.Rollback()

    orderID, err := s.createOrderInTx(ctx, tx, userID, total)
    if err != nil {
        return 0, fmt.Errorf("create order: %w", err)
    }

    if err := s.decrementInventoryInTx(ctx, tx, productID, qty); err != nil {
        return 0, fmt.Errorf("decrement inventory: %w", err)
    }

    return orderID, tx.Commit()
}
```

The `querier` interface lets the internal methods work with either a `*sql.DB` or a `*sql.Tx`, so you can call them standalone or inside a transaction.

## The Gotchas

**Don't model your repository after your database tables.** A `UserRepository` that exactly mirrors the `users` table is often the wrong abstraction. Your queries are about business operations, not table CRUD. A `UserStore.GetActiveWithRecentOrders(...)` is more useful than `UserRepository.ListWhere(...)`. Model the operations your application actually needs.

**Leaking SQL into your domain.** When a repository method returns an error like `pq: duplicate key value violates unique constraint "users_email_key"`, you've leaked a Postgres-specific error into your application layer. Translate these at the boundary:

```go
func (s *UserStore) Create(ctx context.Context, name, email string) (*User, error) {
    u, err := s.create(ctx, name, email)
    if err != nil {
        var pgErr *pq.Error
        if errors.As(err, &pgErr) && pgErr.Code == "23505" { // unique_violation
            return nil, ErrEmailTaken
        }
        return nil, fmt.Errorf("create user: %w", err)
    }
    return u, nil
}
```

Now your handler deals with `ErrEmailTaken`, not Postgres error codes.

**The full-repo interface is a maintenance trap.** An interface with 15 methods means a 15-method mock. Every new method added to the store requires updating the mock. Prefer small, focused interfaces. If a handler only calls `GetByID`, it only needs an interface with `GetByID`.

## Key Takeaway

The repository pattern is a tool, not a religion. Start with a concrete `*Store` struct and add methods as you need them. Skip the interface until you have a specific reason — testing business logic in isolation, multiple implementations, or clean dependency inversion. When you do add an interface, keep it small and focused on what the consumer needs, not what the store provides. And always translate database errors into domain errors at the store boundary.

---

← [Lesson 3: Transactions That Don't Bite](/post/go/go-db-transactions) | [Lesson 5: sqlc vs ORM vs Raw SQL →](/post/go/go-db-sqlc-vs-orm)
