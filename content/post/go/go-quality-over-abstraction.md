---
title: 'Lesson 2: Spotting Over-Abstraction — When your abstraction is the problem'
author: Atharva Pandey
series: "Go Code Quality & Maintainability"
lesson: 2
keywords:
  - Go
  - golang
  - abstraction
  - code quality
  - interfaces
  - over-engineering
tags:
  - Go tutorial
  - golang
  - code quality
date: '2024-07-24T00:00:00.000Z'
---

There's a version of Go code I see in almost every codebase that's been touched by developers who came from Java or C# — it's covered in interfaces, repositories, factories, and service layers that all wrap exactly one concrete implementation. No tests mock these interfaces. No second implementation exists. The abstraction is doing nothing except adding indirection. I've written code like this myself, and the tell is always the same: when something breaks, I have to jump through five files to understand what happens when I call `CreateUser`.

## The Problem

Over-abstraction in Go usually arrives in one of two flavors: the single-implementation interface and the pass-through wrapper.

```go
// OVER-ABSTRACTED — UserRepository interface with exactly one implementation
type UserRepository interface {
    FindByID(ctx context.Context, id int64) (*User, error)
    FindByEmail(ctx context.Context, email string) (*User, error)
    Save(ctx context.Context, user *User) error
    Delete(ctx context.Context, id int64) error
}

type PostgresUserRepository struct {
    db *sql.DB
}

func (r *PostgresUserRepository) FindByID(ctx context.Context, id int64) (*User, error) {
    // actual SQL
}

// UserService wraps the repository — but adds nothing
type UserService struct {
    repo UserRepository
}

func (s *UserService) GetUser(ctx context.Context, id int64) (*User, error) {
    return s.repo.FindByID(ctx, id)
}
```

`UserService.GetUser` is a pass-through. It accepts a `UserRepository` interface but there's only ever a `PostgresUserRepository`. The interface is purely ceremonial — it costs maintenance overhead every time you add a method, and it provides no benefit in return.

The second flavor is the configuration object factory pattern imported wholesale from enterprise Java:

```go
// OVER-ABSTRACTED — factory for something that should just be a struct literal
type EmailSenderConfig struct {
    Host     string
    Port     int
    From     string
    UsesTLS  bool
}

type EmailSenderFactory struct{}

func (f *EmailSenderFactory) Build(cfg EmailSenderConfig) EmailSender {
    return &SMTPEmailSender{
        host:    cfg.Host,
        port:    cfg.Port,
        from:    cfg.From,
        usesTLS: cfg.UsesTLS,
    }
}
```

The factory is four extra lines for zero gain. You could just write `&SMTPEmailSender{host: cfg.Host, ...}` at the call site.

## The Idiomatic Way

The Go proverb that cuts through this is Rob Pike's: "A little copying is better than a little dependency." The corollary for abstractions is: **create an interface when you have two implementations, not in anticipation of a second one.**

```go
// IDIOMATIC — no interface until there's a second implementation
type UserStore struct {
    db *sql.DB
}

func (s *UserStore) FindByID(ctx context.Context, id int64) (*User, error) {
    var u User
    err := s.db.QueryRowContext(ctx,
        "SELECT id, email, name FROM users WHERE id = $1", id,
    ).Scan(&u.ID, &u.Email, &u.Name)
    if errors.Is(err, sql.ErrNoRows) {
        return nil, ErrUserNotFound
    }
    return &u, err
}
```

When tests need a fake, that's the moment to extract an interface — and Go's implicit interface satisfaction means you extract exactly the methods you need, not a full repository interface.

```go
// Interface extracted for testing — minimal surface area
type userFinder interface {
    FindByID(ctx context.Context, id int64) (*User, error)
}

// Now your handler depends on the minimal interface, not the full store
func NewUserHandler(finder userFinder) *UserHandler {
    return &UserHandler{finder: finder}
}
```

This interface lives next to the code that consumes it, not next to the code that implements it. That's the Go convention — interfaces belong to the consumer, not the producer.

## In The Wild

A team I consulted for had a codebase with 23 interfaces in an `interfaces/` package. Every interface had one implementation. Tests existed for maybe 40% of them. The real cost showed up during onboarding: new engineers spent their first week tracing through layers before they could make a change with confidence.

We spent two weeks deleting abstractions — specifically, any interface with a single implementation and no test mocks. We went from 23 interfaces to 6. The codebase shrank by about 800 lines. Build time dropped slightly. New engineers could now read the data flow for a feature in a single file instead of five.

The heuristic we settled on: if you can delete the interface and replace it with a concrete type and the code still compiles and all tests still pass, delete the interface.

## The Gotchas

**Mocking isn't a reason to add an interface to everything.** The fact that you want to mock something in a test is a signal that it has a side effect — a database, a network call, the clock. It's not a reason to wrap everything in an interface by default. Go's `httptest` package lets you test HTTP handlers without abstracting the HTTP layer at all.

**Deep call chains hide over-abstraction.** If you need to trace through four layers (handler → service → repository → adapter) to understand what happens when you call an endpoint, the layers aren't adding clarity. Each layer should add a distinct transformation — if it doesn't, it's a tax on the reader.

**"We might need this later" is how abstractions accumulate.** YAGNI applies to interfaces as much as to features. Extract when you need it, not when you imagine you might.

**Interfaces with more than 3–4 methods are usually modelling the wrong thing.** Large interfaces are hard to implement, hard to mock, and tend to grow. If an interface has eight methods, split it into focused sub-interfaces or question whether it should be an interface at all.

## Key Takeaway

In Go, abstractions have a cost — indirection, maintenance, cognitive load — and they only pay for themselves when they enable testing, extensibility, or polymorphism you're actually using. Create interfaces at the point of consumption, for the methods you actually need, when you have a second implementation or a test that needs a fake. Delete any interface that has one implementation and no mocks. Concrete, readable, direct code is a feature, not a liability.

---

[← Lesson 1: Refactoring Go Code Safely](/post/go/go-quality-refactoring) | [Course Index](/series/go-code-quality-maintainability) | [Next → Lesson 3: Idiomatic Naming](/post/go/go-quality-naming)
