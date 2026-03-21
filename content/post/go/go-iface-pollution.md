---
title: 'Lesson 3: Interface Pollution — More interfaces means more indirection'
author: Atharva Pandey
series: "Go Interfaces & Abstraction Design"
lesson: 3
keywords:
  - Go
  - golang
  - interfaces
  - interface pollution
  - over-abstraction
  - Go design patterns
tags:
  - Go tutorial
  - golang
  - interfaces
date: '2024-08-22T00:00:00.000Z'
---

Interface pollution is not a hypothetical risk. I have walked into codebases where more than half the types in the package are interfaces, where every function parameter is an interface even when the concrete type is never substituted, and where adding a new feature means navigating six files just to trace what a single method call actually does. The code is technically "flexible" in the sense that every seam is abstract. In practice it is a maze with no map.

The insidious thing about interface pollution is that it accumulates incrementally. No one writes thirty interfaces on day one. They add them one at a time, each addition feeling reasonable in isolation, until the cumulative weight becomes a serious maintenance problem.

## The Problem

The most common source of pollution is what I call the mirror interface: an interface that is defined to match a concrete type one-to-one, usually in the same package, usually returned from the constructor.

```go
// WRONG — mirror interface with seven methods for a type that has one real consumer
package repository

type UserRepository interface {
    Create(ctx context.Context, user User) error
    Get(ctx context.Context, id string) (*User, error)
    Update(ctx context.Context, id string, patch UserPatch) error
    Delete(ctx context.Context, id string) error
    List(ctx context.Context, filter UserFilter) ([]*User, error)
    Count(ctx context.Context, filter UserFilter) (int, error)
    Exists(ctx context.Context, id string) (bool, error)
}

type postgresUserRepository struct {
    db *sql.DB
}

func New(db *sql.DB) UserRepository {
    return &postgresUserRepository{db: db}
}
```

Every test that uses this repository needs to implement all seven methods, even if the function under test only calls `Get`. Every time a new method is added to the postgres implementation, the interface must be updated, which cascades to every mock. The flexibility this interface promises is theoretical — no one is going to swap in a DynamoDB implementation.

A second form of pollution comes from interfaces that exist purely to satisfy a framework convention rather than a genuine need for polymorphism:

```go
// WRONG — interface for a type that will never be substituted
type Logger interface {
    Info(msg string, args ...any)
    Error(msg string, args ...any)
    Debug(msg string, args ...any)
    Warn(msg string, args ...any)
    With(args ...any) Logger
}
```

If your application uses `*slog.Logger` everywhere, this interface buys nothing. You are already using a concrete type that has these methods. The interface just adds a wrapper around something you could reference directly.

## The Idiomatic Way

The prescription is not "never use interfaces" — it is "use interfaces at seams where substitution actually happens." For a repository layer, that usually means the interface belongs in the package that calls the repository, not the package that implements it, and it covers only the methods that caller needs.

```go
// RIGHT — each caller defines its own minimal interface
package user

// Only this package's handlers need to find and create users.
type userStore interface {
    Get(ctx context.Context, id string) (*User, error)
    Create(ctx context.Context, user User) error
}

type Handler struct {
    store userStore
}
```

The postgres implementation in the `repository` package satisfies `userStore` without knowing the interface exists. If a different handler in an `admin` package needs `List` and `Count`, it defines its own interface with just those two methods. Each piece of code documents its own minimal contract.

For the logger problem, use the concrete type from the standard library and pass it directly. If you genuinely need to swap the logger in tests, a function value or a simpler two-method interface is all you need:

```go
// RIGHT — if you truly need to swap the logger, define only what you use
package worker

type logger interface {
    Info(msg string, args ...any)
    Error(msg string, args ...any)
}

type Worker struct {
    log logger
}
```

This is two methods, not five, and `*slog.Logger` satisfies it. In tests, a simple struct that records messages is sufficient — no need to implement `With`, `Debug`, `Warn`, or anything else.

Here is a more complete before-and-after that shows the reduction in mock surface area:

```go
// BEFORE — test must implement 7 methods even though only Get is called
type mockUserRepo struct{}
func (m *mockUserRepo) Create(ctx context.Context, user User) error          { return nil }
func (m *mockUserRepo) Get(ctx context.Context, id string) (*User, error)    { return nil, nil }
func (m *mockUserRepo) Update(ctx context.Context, id string, p UserPatch) error { return nil }
func (m *mockUserRepo) Delete(ctx context.Context, id string) error          { return nil }
func (m *mockUserRepo) List(ctx context.Context, f UserFilter) ([]*User, error) { return nil, nil }
func (m *mockUserRepo) Count(ctx context.Context, f UserFilter) (int, error) { return 0, nil }
func (m *mockUserRepo) Exists(ctx context.Context, id string) (bool, error)  { return false, nil }

// AFTER — narrow interface, test is one meaningful method
type stubGetter struct {
    user *User
    err  error
}
func (s *stubGetter) Get(_ context.Context, _ string) (*User, error) {
    return s.user, s.err
}
```

The test struct is five lines. It expresses only what the test cares about. There are no method stubs returning zero values for operations the test never exercises.

## In The Wild

One pattern that generates interface pollution fast is trying to make every external dependency mockable "from the start." Cache clients, feature flag SDKs, email senders — the developer wraps each one in a bespoke interface defined next to the implementation, reasoning that this makes the system testable.

What actually happens is that each of these interfaces mirrors its source SDK. The SDK updates, the interface needs updating. New methods are added to be safe, making the mock surface larger. Eventually, tests that need to add a single happy path require updating a mock that has thirteen methods.

The production fix I have applied twice is a dependency audit: enumerate every interface in the codebase and identify which ones have more than one implementation (including fakes/mocks in tests). Any interface with exactly one real implementation and one test double is a candidate for replacement with a function value or a narrower definition.

```go
// Replace a single-method interface with a function value — simpler, clearer
// BEFORE
type EmailSender interface {
    Send(to, subject, body string) error
}

// AFTER — a function is already an interface with one method
type SendEmailFunc func(to, subject, body string) error

type NotificationService struct {
    sendEmail SendEmailFunc
}

// In tests:
svc := &NotificationService{
    sendEmail: func(to, subject, body string) error { return nil },
}

// In production:
svc := &NotificationService{
    sendEmail: emailClient.Send,
}
```

Fewer types. Fewer files. The same testability.

## The Gotchas

**Interfaces accumulate silently.** A linter like `iface` or a periodic manual count of interface definitions in each package can surface pollution before it becomes load-bearing. Left unchecked, interfaces proliferate because each addition feels costless.

**Wide interfaces make embedding hard.** If you have a fifteen-method `Repository` interface and you want to embed it into a struct that adds caching behavior, you now have to forward fifteen methods. A five-method interface with two focused subsets is much easier to wrap with a decorator.

**Interface saturation breaks the "what does this actually do" question.** When every dependency is an interface and every interface has five or more methods, tracing what happens when a function is called becomes a discipline in itself. The concrete implementations are the documentation. Hiding them behind layers of abstraction makes the system harder to reason about, not easier.

## Key Takeaway

Interface pollution is a form of premature generalization. It optimizes for flexibility that never arrives while paying a real ongoing cost in indirection, mock maintenance, and cognitive load. The antidote is not to avoid interfaces — it is to be deliberate about each one. Ask whether there is a real polymorphism need right now, not a hypothetical one. Ask whether the interface is as narrow as it can be. Ask whether a function value would serve the same purpose with less ceremony. Most of the time, removing an interface makes the code more readable, not less safe.

---

← [Lesson 2: Consumer-Side Interfaces](/post/go/go-iface-consumer-side) | [Course Index](/series/go-interfaces-abstraction-design) | [Next → Lesson 4: Returning Concrete Types](/post/go/go-iface-return-concrete)
