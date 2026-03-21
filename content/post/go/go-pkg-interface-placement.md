---
title: "Lesson 4: Interface Placement — Define interfaces where they're used, not where they're implemented"
author: Atharva Pandey
series: "Go Package & Module Architecture"
lesson: 4
keywords: [Go, golang, packages, modules, architecture]
tags: [Go tutorial, golang, architecture]
date: '2024-09-28T00:00:00.000Z'
---

This is the Go idiom that surprises people coming from Java or C# the most. In those languages, you define an interface in the same place as (or even before) the implementation, and consumers import the interface. Go's approach is the exact opposite, and it takes a while to internalize why. Once it clicks, it changes how you think about dependencies fundamentally.

The rule is: define an interface in the package that needs it, not in the package that satisfies it. It sounds backwards. It is not. It is one of the most powerful design decisions the language enables.

## The Problem

The Java habit of "define the interface alongside the implementation" looks like this in Go:

```
myapp/
  storage/
    storage.go     ← defines UserStorage interface AND PostgresStorage implementation
  service/
    user.go        ← imports storage, uses storage.UserStorage
```

The `storage` package exports both an interface and a concrete type. The `service` package imports `storage` to use the interface. This seems fine. But it creates an invisible coupling: `service` now depends on `storage` not just for the implementation, but also for the interface definition. The two packages are tied together at the dependency level even when you swap implementations.

It also creates a problem of "for whom is this interface?" An interface defined next to its implementation tends to be shaped by the implementation, not by the consumer's needs. It grows to match every method the implementation has, rather than the minimal surface the consumer requires. This is where you get interfaces with fifteen methods that are impossible to mock without implementing all of them.

Go's implicit interface satisfaction frees you from this entirely. Any type that has the right methods satisfies the interface — no declaration of intent needed. This means you can define a narrow interface right where you use it, shaped exactly by what you need, and any type that happens to fit will satisfy it.

## The Idiomatic Way

**Wrong: interface defined in the implementation package, consumer imports it**

```go
// package storage — defines both the interface and the Postgres implementation
package storage

import "context"

// UserStore is defined here, in the package that implements it.
// This is the Java/C# pattern. It works but it's not idiomatic Go.
type UserStore interface {
    GetUser(ctx context.Context, id int64) (*User, error)
    CreateUser(ctx context.Context, u *User) error
    UpdateUser(ctx context.Context, u *User) error
    DeleteUser(ctx context.Context, id int64) error
    ListUsers(ctx context.Context, filter UserFilter) ([]*User, error)
}

type PostgresUserStore struct {
    // ... db pool
}

func (p *PostgresUserStore) GetUser(ctx context.Context, id int64) (*User, error) {
    return nil, nil
}
// ... and so on for every method
```

```go
// package service — imports storage just to get the interface type
package service

import (
    "context"

    "myapp/storage" // tightly coupled to storage package
)

type UserService struct {
    store storage.UserStore // consumer depends on producer's package for the type
}

func (s *UserService) GetUser(ctx context.Context, id int64) (*storage.User, error) {
    return s.store.GetUser(ctx, id)
}
```

To test `UserService`, you now have to import `storage` in your test file too, implement all five methods of `storage.UserStore` in your mock, and deal with the `storage.User` type throughout. The consumer package cannot exist in isolation.

**Right: interface defined in the consuming package, shaped by what the consumer needs**

```go
// package service — defines only the interface IT needs
package service

import "context"

// userReader is a narrow interface defined here in the service package.
// It only asks for what this service actually uses.
// Note: unexported because it's an internal implementation detail.
type userReader interface {
    GetUser(ctx context.Context, id int64) (*User, error)
}

type User struct {
    ID    int64
    Email string
    Name  string
}

type UserService struct {
    store userReader
}

func NewUserService(store userReader) *UserService {
    return &UserService{store: store}
}

func (s *UserService) Profile(ctx context.Context, id int64) (*User, error) {
    return s.store.GetUser(ctx, id)
}
```

```go
// package storage — has NO interface definition, just the concrete type
package storage

import (
    "context"

    "myapp/service" // storage can import service's User type if needed,
                    // OR storage defines its own type and a converter exists
)

type PostgresUserStore struct {
    // ... db pool
}

// GetUser satisfies service.userReader implicitly.
// storage doesn't know or care about service's interface.
func (p *PostgresUserStore) GetUser(ctx context.Context, id int64) (*service.User, error) {
    // ... query postgres
    return &service.User{ID: id, Email: "user@example.com"}, nil
}
```

```go
// service/user_test.go — test without touching the storage package at all
package service_test

import (
    "context"
    "testing"

    "myapp/service"
)

// mockStore only needs to implement GetUser — the one method the interface requires.
type mockStore struct {
    user *service.User
    err  error
}

func (m *mockStore) GetUser(_ context.Context, _ int64) (*service.User, error) {
    return m.user, m.err
}

func TestUserService_Profile(t *testing.T) {
    mock := &mockStore{user: &service.User{ID: 1, Name: "Atharva"}}
    svc := service.NewUserService(mock)

    got, err := svc.Profile(context.Background(), 1)
    if err != nil {
        t.Fatalf("unexpected error: %v", err)
    }
    if got.Name != "Atharva" {
        t.Errorf("expected Atharva, got %s", got.Name)
    }
}
```

The test file imports only `service`. No `storage` package. No five-method mock. The interface was shaped entirely by what the consumer needed.

**Right: multiple narrow interfaces from a single concrete type**

This is the full power of consumer-side interfaces. One concrete type in `storage` can satisfy many different narrow interfaces defined across many consuming packages:

```go
// package orderservice — defines its own narrow interface
package orderservice

import "context"

// Only needs to look up users by ID for order association.
type userLookup interface {
    GetUser(ctx context.Context, id int64) (*User, error)
}
```

```go
// package auditlog — defines its own narrow interface
package auditlog

import "context"

// Only needs to list users for audit reporting.
type userLister interface {
    ListUsers(ctx context.Context) ([]*User, error)
}
```

```go
// package main — wires the single Postgres implementation to all consumers
package main

import (
    "myapp/auditlog"
    "myapp/orderservice"
    "myapp/storage"
)

func main() {
    store := storage.NewPostgresUserStore(/* db */)

    // store implicitly satisfies both interfaces.
    // No casting, no adapter, no interface declaration in storage needed.
    orderSvc := orderservice.NewService(store)
    audit := auditlog.NewLogger(store)

    _, _ = orderSvc, audit
}
```

## In The Wild

The `io` package is the canonical example. `io.Reader` is one method: `Read(p []byte) (n int, err error)`. It is defined in `io`, which is the consumer abstraction, not in `os` or `bytes` or `net`. Any concrete type that has the right method signature satisfies it, regardless of its package of origin.

The `database/sql` driver package uses the same pattern. `database/sql/driver` defines the interfaces (`driver.Driver`, `driver.Conn`, `driver.Stmt`) that the `database/sql` package needs from drivers. Drivers implement these interfaces without needing to import `database/sql` itself.

In my own codebases I treat this as a firm rule: if a concrete type needs to be mocked in tests for package X, the interface lives in package X. I have never needed to break this rule.

## The Gotchas

**Gotcha 1: interfaces as return types in the producing package.** If `storage.NewPostgresUserStore` returns an interface type, the storage package is making a decision for all consumers about what the type looks like. Return the concrete type from constructors. Let consumers define the interface they need.

**Gotcha 2: large "god interfaces" checked into a shared package.** Teams sometimes create a `contracts` or `interfaces` package with every interface in the system. This is the implementation-side pattern dressed up differently. The interfaces are still shaped by the implementations, not by the consumers.

**Gotcha 3: exporting interfaces you do not have to.** A narrow interface used by a single function can often be unexported (`type userReader interface`). Only export it if you expect external consumers to use it directly in their own types or function signatures.

**Gotcha 4: circular imports through shared types.** If `service` defines an interface that takes `storage.User` as a parameter, you have introduced a dependency on `storage` anyway, defeating part of the benefit. Keep your domain types in neutral locations (lesson 2's `domain` package) so interfaces can stay clean.

## Key Takeaway

Go's implicit interface satisfaction is not just a syntactic convenience — it is an architectural feature. By defining interfaces where they are consumed rather than where they are implemented, you get the narrowest possible interface, the weakest possible coupling, and tests that do not require importing half the world.

The next time you reach to define an interface, ask: which package needs this behavior? Put the interface there. The concrete type will satisfy it without knowing it exists. That is Go's type system working for you, not against you.

---

**Series: Go Package & Module Architecture**

- [Lesson 1: Package Boundaries](/post/go/go-pkg-boundaries/)
- [Lesson 2: Avoiding Cyclic Dependencies](/post/go/go-pkg-cyclic-deps/)
- [Lesson 3: internal/ Usage Patterns](/post/go/go-pkg-internal/)
- Lesson 4: Interface Placement — *you are here*
- [Lesson 5: Monolith vs Multi-Module](/post/go/go-pkg-mono-vs-multi/)
- [Lesson 6: Dependency Direction](/post/go/go-pkg-dependency-direction/)
- [Lesson 7: go.work and Workspace Mode](/post/go/go-pkg-workspaces/)
