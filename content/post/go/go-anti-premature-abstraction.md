---
title: "Lesson 8: Premature Abstraction — Wrong abstraction costs more than duplication"
author: Atharva Pandey
series: "Go Anti-Patterns & Code Smells"
lesson: 8
keywords:
  - Go
  - golang
  - anti-patterns
  - code quality
tags:
  - Go tutorial
  - golang
  - code quality
date: '2025-04-02T00:00:00.000Z'
---

There is a principle in software development — "Don't Repeat Yourself" — that is so widely known it gets abbreviated to DRY and invoked to justify almost any abstraction. The problem is that DRY is a principle about knowledge, not syntax. Two pieces of code that look the same but represent different concepts should stay separate. Two pieces of code that represent the same concept should indeed be unified. Most premature abstractions happen when developers see syntactic similarity and immediately reach for abstraction, before they understand whether the similarity is incidental or fundamental.

I have maintained Go codebases where an abstraction was introduced at week two of a project to "avoid duplication" — and then spent the next eight months contorting every feature to fit the abstraction. Removing the abstraction and accepting the duplication took a day. Maintaining it had cost weeks. The wrong abstraction is not neutral — it is actively harmful.

## The Problem

The classic premature abstraction: a generic helper that is built around the current shape of two similar functions before their requirements are understood:

```go
// WRONG — premature abstraction built from incidental similarity
// At week 2, CreateUser and CreateProduct look similar, so we abstract:

type Creator[T any] struct {
    validate func(T) error
    save     func(T) error
    notify   func(T)
}

func (c *Creator[T]) Create(item T) error {
    if err := c.validate(item); err != nil {
        return fmt.Errorf("validation: %w", err)
    }
    if err := c.save(item); err != nil {
        return fmt.Errorf("save: %w", err)
    }
    c.notify(item)
    return nil
}

// Then requirements diverge:
// Users need email verification before saving
// Products need inventory reservation after saving but before notification
// Orders need a two-phase commit across two databases
// Now every feature is fighting the abstraction to add its specific behavior
```

Three months later, `Creator` has a `preHook`, `postHook`, `conditionalNotify` flag, and a `skipValidationInTest` option — and it is harder to understand than just writing three separate functions.

A second form: extracting a utility function the moment any duplication appears, before you have seen enough uses to know what the right signature is:

```go
// WRONG — utility extracted too early with the wrong signature
// We have two places that paginate database results, so we extract:

func Paginate(db *sql.DB, table string, offset, limit int) ([]map[string]any, error) {
    query := fmt.Sprintf("SELECT * FROM %s LIMIT $1 OFFSET $2", table) // SQL injection
    rows, err := db.Query(query, limit, offset)
    // ...
}

// Third use case needs:
// - WHERE clause for filtering
// - ORDER BY for sorting
// - Specific columns, not SELECT *
// - A typed result, not map[string]any
// The abstraction does not fit. We either extend it into a query builder
// (reinventing sqlx) or work around it with hacks.
```

## The Idiomatic Way

Wait until you have three concrete uses before abstracting. By the third use, you have enough information to know what varies and what is stable:

```go
// RIGHT — three concrete implementations, then identify the pattern
func createUser(ctx context.Context, db *sql.DB, u CreateUserRequest) (*User, error) {
    if err := validateUser(u); err != nil {
        return nil, err
    }
    hash, err := hashPassword(u.Password)
    if err != nil {
        return nil, err
    }
    return insertUser(ctx, db, u.Name, u.Email, hash)
}

func createProduct(ctx context.Context, db *sql.DB, p CreateProductRequest) (*Product, error) {
    if err := validateProduct(p); err != nil {
        return nil, err
    }
    if err := reserveInventory(ctx, p.SKU, p.InitialStock); err != nil {
        return nil, err
    }
    return insertProduct(ctx, db, p)
}

func createOrder(ctx context.Context, db *sql.DB, o CreateOrderRequest) (*Order, error) {
    if err := validateOrder(o); err != nil {
        return nil, err
    }
    // two-phase commit, different from the other two
    return createOrderWithTransaction(ctx, db, o)
}

// After the third use: these three are NOT the same. Each has unique steps.
// The right response is to accept the duplication, not force an abstraction.
```

When abstraction IS the right answer — when the similarity is deep, not incidental — make it as narrow as the shared behavior actually is:

```go
// RIGHT — abstraction over what is genuinely shared
// All three need audit logging after a successful create. That is shared.

type AuditLogger interface {
    Log(ctx context.Context, action, entityType, entityID string) error
}

func createUserWithAudit(ctx context.Context, db *sql.DB, audit AuditLogger, u CreateUserRequest) (*User, error) {
    user, err := createUser(ctx, db, u)
    if err != nil {
        return nil, err
    }
    _ = audit.Log(ctx, "create", "user", user.ID) // log, don't fail on audit error
    return user, nil
}
```

The abstraction is over exactly one shared behavior — audit logging — not over the entire create lifecycle.

## In The Wild

**The Rule of Three.** Martin Fowler's heuristic: the first time you write something, just write it. The second time you do something similar, note the duplication but proceed. The third time, refactor. By the third occurrence, you have enough context to know whether the similarity is deep or incidental.

**Abstraction direction matters.** There are two directions to abstract: pulling up common code into a shared function (bottom-up), or designing a framework first and writing implementations against it (top-down). In application code, bottom-up is almost always safer. In library code where you are designing for extensibility, top-down may be appropriate — but only when you have multiple consumers already in mind.

**Copy-paste duplication is fine temporarily.** "We should not have two functions that do almost the same thing" is a reasonable code review comment. The response is not to immediately abstract — it is to investigate whether the similarity is real, and if so, what the right abstraction is. A `// TODO: may want to unify with createProduct` comment is a legitimate intermediate state.

## The Gotchas

**Abstraction as compression.** Some developers abstract to reduce lines of code — fewer lines feels cleaner. But abstraction that saves ten lines while adding an indirection that requires understanding the abstraction before understanding the code is a net loss. Evaluate abstractions by whether they reduce the total cognitive load on a reader, not by line count.

**Generics and premature abstraction.** Go 1.18 added generics, which makes certain abstractions very easy to write. `Map[T, U]([]T, func(T) U) []U` is useful and general. But it is also easy to write a generic function for something that only has one concrete instantiation in your codebase. A generic function with one use case is premature abstraction in a different syntax.

**"We might need it later."** The most dangerous phrase in software design. Abstracting for hypothetical future requirements adds complexity today for benefits that may never materialize. Build for what you know today. When the new requirement arrives, you will have more information about what the right abstraction is than you have right now.

**Refactoring is not failure.** When you discover you need an abstraction — the third use case has arrived, the pattern is clear — refactoring to introduce it is correct and healthy. The premature abstraction anti-pattern is not about avoiding all abstraction; it is about not reaching for it before you understand the domain well enough to abstract correctly.

## Key Takeaway

Duplication is a problem you can fix later, with more information. A wrong abstraction is a constraint that shapes every piece of code you write in its vicinity. Accept duplication until the third use, look for the stable axis across the uses, and abstract only that — not the whole thing. The right abstraction makes code clearer and simpler. The wrong one does the opposite.

---

**Go Anti-Patterns & Code Smells**

Previous: [Lesson 7: Panic as Error Handling — Panic is for bugs, not business logic](/post/go/go-anti-panic-misuse/)
Next: [Lesson 9: Fake Clean Architecture — Layers without purpose are just folders](/post/go/go-anti-fake-clean-arch/)
