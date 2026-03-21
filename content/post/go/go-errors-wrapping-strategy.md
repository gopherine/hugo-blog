---
title: "Lesson 3: Wrapping Strategy — Every wrap should add context, never noise"
author: Atharva Pandey
series: "Go Error Design at Scale"
lesson: 3
keywords:
  - Go
  - golang
  - error handling
  - error wrapping
  - fmt.Errorf
  - %w
  - error context
tags:
  - Go tutorial
  - golang
date: '2025-02-04T00:00:00.000Z'
---

There's a certain kind of error message I've come to dread in production logs: `auth: db: sql: connection refused`. Technically, it contains the full path from the handler down to the socket. Practically, it tells me nothing I couldn't figure out from the stack trace — and it takes ten seconds to parse. That error message is the output of wrapping done wrong: every layer added its name reflexively, without thinking about what the reader actually needs.

Wrapping errors with `fmt.Errorf` and `%w` is the right tool. But like any tool, it can be used well or badly. This lesson is about developing the judgment to tell the difference.

## The Problem

The first mistake is **under-wrapping** — returning errors as-is from every layer, losing all context about where in the call chain the failure happened.

```go
// WRONG — no context, impossible to trace in production
func (r *UserRepo) FindByID(ctx context.Context, id string) (*User, error) {
    var u User
    err := r.db.QueryRowContext(ctx, `SELECT id FROM users WHERE id=$1`, id).Scan(&u.ID)
    if err != nil {
        return nil, err // returns "sql: no rows in result set" — but from where?
    }
    return &u, nil
}

func (s *UserService) Get(ctx context.Context, id string) (*User, error) {
    u, err := s.repo.FindByID(ctx, id)
    if err != nil {
        return nil, err // strips all context again
    }
    return u, nil
}
```

When this hits your log, you see `sql: no rows in result set`. You know it came from somewhere in the user flow — but which path? Which caller? Which ID was being looked up? You're now opening files and grepping, hoping the stack trace (if you have one) is enough.

The second mistake is **over-wrapping** — adding a layer's name at every single step regardless of whether it adds signal.

```go
// WRONG — mechanical wrapping adds noise without signal
func (r *UserRepo) FindByID(ctx context.Context, id string) (*User, error) {
    var u User
    err := r.db.QueryRowContext(ctx, `SELECT id FROM users WHERE id=$1`, id).Scan(&u.ID)
    if err != nil {
        return nil, fmt.Errorf("user repo: %w", err) // just the layer name, no data
    }
    return &u, nil
}

func (s *UserService) Get(ctx context.Context, id string) (*User, error) {
    u, err := s.repo.FindByID(ctx, id)
    if err != nil {
        return nil, fmt.Errorf("user service: %w", err) // still no data
    }
    return u, nil
}

func (h *Handler) GetUser(w http.ResponseWriter, r *http.Request) {
    id := r.URL.Query().Get("id")
    _, err := h.svc.Get(r.Context(), id)
    if err != nil {
        // log shows: "handler: user service: user repo: sql: no rows in result set"
        // layer names tell me nothing the code structure doesn't already tell me
        log.Println(fmt.Errorf("handler: %w", err))
    }
}
```

The log message `handler: user service: user repo: sql: no rows in result set` is longer but not more useful. I still don't know which user ID failed.

## The Idiomatic Way

The rule I follow: **wrap with the operation and its key inputs**. Not the layer name — the operation name and the data that makes this invocation unique.

```go
// RIGHT — wrap with operation + identifying data
func (r *UserRepo) FindByID(ctx context.Context, id string) (*User, error) {
    var u User
    err := r.db.QueryRowContext(ctx, `SELECT id, email FROM users WHERE id=$1`, id).
        Scan(&u.ID, &u.Email)
    if errors.Is(err, sql.ErrNoRows) {
        return nil, ErrNotFound // translate, don't wrap the driver error
    }
    if err != nil {
        return nil, fmt.Errorf("find user %s: %w", id, err) // operation + id
    }
    return &u, nil
}

func (s *UserService) GetProfile(ctx context.Context, userID string) (*Profile, error) {
    user, err := s.repo.FindByID(ctx, userID)
    if err != nil {
        // don't add another layer — repo already has the context
        // only wrap if THIS layer is doing something meaningful with it
        return nil, err
    }
    // ... build profile
    return toProfile(user), nil
}
```

Now when the error hits the log, it says `find user abc-123: sql: connection refused`. I know the operation, I know the ID, I know the underlying cause. That's everything I need to investigate.

Here's the test I apply before adding a wrap: **"Does this wrap tell the reader something they couldn't infer from the error message alone?"** If the answer is no, don't wrap — just return.

```go
// RIGHT — knowing when NOT to re-wrap
func (s *UserService) CreateOrder(ctx context.Context, userID string, req OrderRequest) error {
    user, err := s.users.FindByID(ctx, userID)
    if err != nil {
        return err // "find user abc-123: ..." already tells the whole story
    }

    if err := s.inventory.Reserve(ctx, req.ItemID, req.Quantity); err != nil {
        // THIS wrap adds value: associates the user ID with the reservation failure
        return fmt.Errorf("create order for user %s: reserve item %s: %w",
            userID, req.ItemID, err)
    }

    return nil
}
```

## In The Wild

A real pattern I use in services with database access: translate driver errors at the repo boundary, wrap with operation+key-data, then pass up cleanly. The service layer only wraps when it's doing something that adds new identifying context.

```go
// Repository — translate + wrap with data
func (r *OrderRepo) Insert(ctx context.Context, o *Order) error {
    _, err := r.db.ExecContext(ctx,
        `INSERT INTO orders (id, user_id, amount) VALUES ($1, $2, $3)`,
        o.ID, o.UserID, o.Amount,
    )
    if err != nil {
        // check for specific postgres constraint violations
        var pgErr *pgconn.PgError
        if errors.As(err, &pgErr) && pgErr.Code == "23505" {
            return fmt.Errorf("insert order %s: %w", o.ID, ErrDuplicate)
        }
        return fmt.Errorf("insert order %s: %w", o.ID, err)
    }
    return nil
}

// Service — only wraps when adding context that doesn't exist yet
func (s *OrderService) PlaceOrder(ctx context.Context, userID string, req PlaceOrderRequest) (*Order, error) {
    order := &Order{
        ID:     newID(),
        UserID: userID,
        Amount: req.Amount,
    }

    if err := s.repo.Insert(ctx, order); err != nil {
        if errors.Is(err, ErrDuplicate) {
            return nil, err // just return — the duplicate is already identified
        }
        // wrap because now we associate the user with the failed order
        return nil, fmt.Errorf("place order for user %s: %w", userID, err)
    }

    return order, nil
}

// Handler — no wrapping, just logging and response mapping
func (h *OrderHandler) PlaceOrder(w http.ResponseWriter, r *http.Request) {
    userID := r.Context().Value(userIDKey).(string)
    var req PlaceOrderRequest
    if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
        http.Error(w, "bad request", http.StatusBadRequest)
        return
    }

    order, err := h.svc.PlaceOrder(r.Context(), userID, req)
    if err != nil {
        // at the boundary: log the full chain, return a clean message
        log.Printf("place order handler: %v", err)
        if errors.Is(err, ErrDuplicate) {
            http.Error(w, "order already exists", http.StatusConflict)
            return
        }
        http.Error(w, "internal error", http.StatusInternalServerError)
        return
    }
    json.NewEncoder(w).Encode(order)
}
```

## The Gotchas

**Don't wrap `context.Canceled` or `context.DeadlineExceeded`.** These aren't your error — they're the caller's intent. Wrapping them buries the sentinel, and callers up the chain check for them specifically. Let them pass through unwrapped.

```go
// WRONG — wrapping context errors
func fetchData(ctx context.Context) error {
    if err := callDownstream(ctx); err != nil {
        return fmt.Errorf("fetch data: %w", err) // wrapping context.Canceled is fine
        // but it means callers need errors.Is(err, context.Canceled) — which still works
    }
    // Actually fine via %w — errors.Is still works. The real antipattern is
    // wrapping with %v and losing the sentinel:
    return fmt.Errorf("fetch data: %v", err) // WRONG — breaks errors.Is check
}
```

**Avoid wrapping the same error at multiple sibling call sites.** If you have three branches that all call the same repo method and wrap with the same context, extract the wrapping to the caller:

```go
// WRONG — repetitive wrap at every call site
if userID == "" {
    u, err = r.FindByAnon(ctx)
    if err != nil {
        return fmt.Errorf("get user: %w", err)
    }
} else {
    u, err = r.FindByID(ctx, userID)
    if err != nil {
        return fmt.Errorf("get user: %w", err)
    }
}

// RIGHT — single wrap point
u, err = r.findUser(ctx, userID) // internal helper resolves the branch
if err != nil {
    return fmt.Errorf("get user: %w", err) // wrapped once
}
```

**Don't use `errors.New` inside a wrap when you mean to attach a sentinel.** `fmt.Errorf("not found: %w", errors.New("user"))` is just confusing — if you want to return `ErrNotFound`, return it directly or wrap it.

## Key Takeaway

Wrap with **operation name and identifying data** — not layer names, not generic descriptions. Ask yourself: "If I'm staring at this error in a 3am incident, does this context help me find the bug faster?" If yes, wrap it. If no, return it as-is. Over-wrapping creates visual noise that slows down debugging just as much as under-wrapping. And never wrap with `%v` when you mean `%w` — you'll silently break the unwrap chain and wonder why `errors.Is` stopped working.

---

Previous: [Lesson 2: errors.Is and errors.As](go-errors-is-as.md) | Next: [Lesson 4: Operational vs Domain Errors — Not all errors deserve the same treatment](go-errors-operational-vs-domain.md)
