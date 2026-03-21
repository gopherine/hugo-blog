---
title: "Lesson 5: Anti-Patterns — Just because you can doesn't mean you should"
author: Atharva Pandey
series: "Go Generics Masterclass"
lesson: 5
keywords:
  - Go
  - golang
  - generics
  - anti-patterns
  - generic repository
  - over-abstraction
  - FP abuse
  - Map FlatMap
  - nested constraints
  - premature abstraction
tags:
  - Go tutorial
  - golang
  - generics
date: '2024-09-08T00:00:00.000Z'
---

Every powerful tool has a failure mode that looks like success. With generics, the failure mode is this: you write something that compiles, works correctly, and is impressively abstract — but your teammates can't read it, can't debug it, and quietly work around it. I've written code like that. It felt clever in the moment. It was a problem in practice.

This lesson is about the patterns that sound good and turn out badly. I'm calling them out explicitly because they're seductive — especially if you've spent time in Haskell or Scala and you know what generic abstractions *can* look like.

## The Problem

The root cause of most generic anti-patterns is the same: solving a problem you don't have yet. You see a type with two concrete implementations, and instead of writing two straightforward functions, you build a generic abstraction "just in case there's a third." But there isn't. And now you've paid the complexity tax upfront for a benefit that never arrives.

```go
// WRONG — generic repository that adds complexity without adding value
type Repository[T any] interface {
    FindByID(id int) (T, error)
    FindAll() ([]T, error)
    Save(entity T) error
    Delete(id int) error
}

type genericRepo[T any] struct {
    db *sql.DB
    tableName string
    scanFunc  func(*sql.Row) (T, error)
}

func (r *genericRepo[T]) FindByID(id int) (T, error) {
    row := r.db.QueryRow(
        fmt.Sprintf("SELECT * FROM %s WHERE id = $1", r.tableName), id,
    )
    return r.scanFunc(row)
}
```

This looks reasonable until you realize that every real query is different. `Users` need JOINs with roles. `Orders` need filters on status and date ranges. `Products` need full-text search. The moment you need a query that doesn't fit the generic mold, you're adding escape hatches — `RawQuery`, `FindWithFilter`, `FindByCustomCondition` — and the generic abstraction becomes load-bearing scaffolding around the real code.

You haven't removed duplication. You've just moved it, and added a framework to navigate.

## The Idiomatic Way

For repositories specifically, the Go community has largely settled on a different answer: concrete repository types with explicit methods.

```go
// RIGHT — concrete, explicit, no generic overhead
type UserRepository struct {
    db *sql.DB
}

func (r *UserRepository) FindByID(ctx context.Context, id int) (User, error) {
    var u User
    err := r.db.QueryRowContext(ctx,
        `SELECT id, name, email, role FROM users WHERE id = $1`, id,
    ).Scan(&u.ID, &u.Name, &u.Email, &u.Role)
    return u, err
}

func (r *UserRepository) FindActiveByRole(ctx context.Context, role string) ([]User, error) {
    rows, err := r.db.QueryContext(ctx,
        `SELECT id, name, email, role FROM users WHERE active = true AND role = $1`, role,
    )
    // ... scan rows
}
```

This is more code. It's also far easier to read, test, and modify. The SQL is visible. The return types are explicit. There's no mental overhead of "which generic instantiation am I looking at?"

**The FP-style pipeline anti-pattern** is the one that hurts the most when it lands in a codebase. Someone with a functional programming background discovers that Go generics can express `Map`, `Filter`, `FlatMap`, and `Fold`, and decides to use them everywhere:

```go
// WRONG — FP-style chaining that's harder to read than a for loop
result := Fold(
    FlatMap(
        Map(users, func(u User) []Order { return u.Orders }),
        func(orders []Order) []Order {
            return Filter(orders, func(o Order) bool { return o.Status == "completed" })
        },
    ),
    0.0,
    func(total float64, o Order) float64 { return total + o.Amount },
)
```

Compare that to:

```go
// RIGHT — explicit loops are clearer for most Go readers
var total float64
for _, user := range users {
    for _, order := range user.Orders {
        if order.Status == "completed" {
            total += order.Amount
        }
    }
}
```

The loop version is longer. It's also immediately obvious to any Go developer what it does, how it handles edge cases, and where to add error handling. The FP version requires understanding four higher-order functions and their composition before you can reason about what the code does.

Go is not Haskell. The idiom for data transformation in Go is a for loop, not a pipeline of functions. Generics don't change that.

**Over-generalized helpers** are the third pattern. A sign of this is a type constraint that looks like this:

```go
// WRONG — deeply nested constraint that nobody can parse
type Processable[T any] interface {
    comparable
    ~int | ~int64 | ~string
    fmt.Stringer
    json.Marshaler
}

func Process[T Processable[T]](items []T) []string {
    // ...
}
```

This constraint is so restrictive it probably only admits types you wrote yourself, and so complex that readers can't figure out what types qualify without sitting down with the spec. If you find yourself writing a constraint this involved, step back. You're probably not solving a general problem — you're solving a specific problem that would be cleaner with a concrete type.

## In The Wild

I saw this play out on a backend team building a multi-tenant SaaS. An engineer — brilliant, genuinely — had written a generic `TenantScoped[T]` middleware wrapper that automatically injected tenant context into any type:

```go
// WRONG — solving a coordination problem with generics
type TenantScoped[T any] struct {
    inner    T
    tenantID string
}

func NewTenantScoped[T any](inner T, tenantID string) *TenantScoped[T] {
    return &TenantScoped[T]{inner: inner, tenantID: tenantID}
}

func (ts *TenantScoped[T]) Do(ctx context.Context) error {
    ctx = WithTenantID(ctx, ts.tenantID)
    // but now what? Can't call any method on ts.inner without a constraint
}
```

The issue: tenant scoping is a *behavioral* concern. It belongs in a context, middleware chain, or method on each type — not in a generic wrapper. The wrapper couldn't actually call any methods on `T` unless you added a `TenantAware` interface constraint, at which point you might as well just use the interface directly and skip the generic altogether.

The fix was to use a context-based approach and a `TenantAware` interface — three concrete types implementing it, each handling tenant context in their own way. Thirty lines of clear code replaced the generic wrapper and its growing list of escape hatches.

## The Gotchas

**Gotcha 1: Generic code is harder to debug.**

When your generic function panics, the stack trace shows the instantiated type name, which can be verbose and confusing. When a concrete function panics, the error is usually self-explanatory. Don't underestimate the debugging tax of abstraction.

**Gotcha 2: Adding type parameters to a public API is a breaking change to remove.**

If you publish a generic type and a user depends on it, removing the type parameter is a breaking change. Be conservative about making public APIs generic. It's much easier to add generics later than to remove them.

**Gotcha 3: Generic functions can't be passed as values without specifying type arguments.**

You can't do `f := Map` and then call `f(users, ...)`. You'd need `f := Map[User, string]`. This limits composability and is a real ergonomics drawback compared to languages with full type inference.

**Gotcha 4: Over-abstraction compounds.**

One generic repository is manageable. A generic repository that feeds into a generic service layer that feeds into a generic handler layer — by the time you've built three levels, a simple database query spans six files and two abstraction layers. The complexity compounds. Start concrete, and only extract generic abstractions when the duplication is proven and the pattern is stable.

## Key Takeaway

Generics are not a design pattern. They're a mechanism for eliminating type-specific duplication in stable, well-understood algorithms. The most seductive anti-patterns — generic repositories, FP-style pipelines, deeply nested constraints — all share the same failure mode: they use generics to solve problems that aren't actually about type duplication. When you feel the urge to make something generic, ask yourself: "Am I eliminating duplication, or am I imposing structure?" If it's the latter, reach for a concrete type and a clear interface instead.

---

*[← Lesson 4: Interfaces vs Generics](/post/go/go-generics-interfaces-vs-generics/) | [Course Index](/series/go-generics-masterclass/) | [Next → Lesson 6: Real-World Examples](/post/go/go-generics-real-world/)*
