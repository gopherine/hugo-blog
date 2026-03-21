---
title: "Lesson 1: Interface Everywhere Syndrome — Not every dependency needs an interface"
author: Atharva Pandey
series: "Go Anti-Patterns & Code Smells"
lesson: 1
keywords:
  - Go
  - golang
  - anti-patterns
  - code quality
tags:
  - Go tutorial
  - golang
  - code quality
date: '2024-06-18T00:00:00.000Z'
---

When I came to Go from a Java background, I brought some habits with me that made my code worse, not better. The most persistent one was defining an interface for every dependency, regardless of whether there was any realistic chance of substituting an alternative implementation. I wrote `UserRepositoryInterface`, `EmailSenderInterface`, `LoggerInterface` — an entire shadow type system that mirrored every concrete type in the codebase. Every file had a corresponding interface file. The codebase was twice as large as it needed to be and no easier to test.

Go's interface system is genuinely different from Java's. In Java, interfaces are declarations of intent — you define them upfront and implement them explicitly. In Go, interfaces are satisfied implicitly, which means you define them where they are consumed, not where they are implemented. This difference changes everything about when and where you should create interfaces.

## The Problem

The classic anti-pattern: define an interface upfront, before you have any concrete implementation, for everything:

```go
// WRONG — interface defined by the producer, not the consumer
// file: repository/user_repository_interface.go

type UserRepositoryInterface interface {
    FindByID(id string) (*User, error)
    FindByEmail(email string) (*User, error)
    Save(user *User) error
    Delete(id string) error
    List(offset, limit int) ([]*User, error)
    Count() (int, error)
    FindByRole(role string) ([]*User, error)
    // ... 15 more methods
}

// file: repository/postgres_user_repository.go
type PostgresUserRepository struct { db *sql.DB }
func (r *PostgresUserRepository) FindByID(id string) (*User, error) { ... }
// ... implements all 22 methods

// file: service/user_service.go
// Only uses FindByID, FindByEmail, and Save — but must import the 22-method interface
type UserService struct {
    repo UserRepositoryInterface
}
```

Three things are wrong here. First, the interface has 22 methods — which means any test double must implement all 22, most of which are irrelevant to the behavior being tested. Second, the interface is owned by the package that produces the implementation, not the package that consumes it. Third, there is only one implementation and there will only ever be one, making the interface purely ceremonial.

A second manifestation of this pattern is wrapping standard library types in interfaces for no reason:

```go
// WRONG — wrapping http.Client in an interface nobody needs
type HTTPClientInterface interface {
    Do(req *http.Request) (*http.Response, error)
    Get(url string) (*http.Response, error)
    Post(url, contentType string, body io.Reader) (*http.Response, error)
}

type MyService struct {
    httpClient HTTPClientInterface
}
```

The `*http.Client` is already designed to be replaceable through its `Transport` field. Wrapping it in an interface adds indirection without adding flexibility.

## The Idiomatic Way

Define interfaces at the point of consumption, make them as narrow as possible, and only create them when you have a concrete need — testing a boundary, or genuinely needing to substitute implementations:

```go
// RIGHT — narrow interface defined by the consumer, only what it needs
// file: service/user_service.go

// UserFinder is the only interface this service needs.
// Defined here, by the consumer, not the repository package.
type UserFinder interface {
    FindByID(ctx context.Context, id string) (*User, error)
    FindByEmail(ctx context.Context, email string) (*User, error)
}

type UserSaver interface {
    Save(ctx context.Context, user *User) error
}

type UserService struct {
    finder UserFinder
    saver  UserSaver
}
```

This is Go's "accept interfaces, return structs" idiom in practice. The concrete `PostgresUserRepository` satisfies both interfaces implicitly — you do not need to declare that it does. The service only depends on the two behaviors it actually uses.

For dependencies that do not need testing or substitution, use the concrete type directly:

```go
// RIGHT — use the concrete type when there's no reason to abstract
import "github.com/redis/go-redis/v9"

type CacheService struct {
    client *redis.Client  // not wrapped in an interface — no need
}

func NewCacheService(client *redis.Client) *CacheService {
    return &CacheService{client: client}
}
```

If you later need to test `CacheService` in isolation, you have two options: define a narrow interface at that point, or use the real Redis in your tests with a testcontainers setup. The second option is often better than you expect.

For the standard library's `http.Client`, replace the transport in tests rather than wrapping the client in an interface:

```go
// RIGHT — use http.Transport for test substitution, no interface needed
type MyService struct {
    httpClient *http.Client
}

func NewMyService() *MyService {
    return &MyService{httpClient: &http.Client{Timeout: 10 * time.Second}}
}

// In tests, replace the transport:
func TestMyService(t *testing.T) {
    transport := &mockTransport{...} // implements http.RoundTripper
    svc := &MyService{
        httpClient: &http.Client{Transport: transport},
    }
    // ...
}
```

## In The Wild

The guideline from the Go FAQ is worth internalizing: "Don't design with interfaces, discover them." This means you start with concrete types, and only when you find yourself wanting to write the same logic with different types, or when you need to test across a boundary, do you extract an interface.

The Go standard library models this well. `io.Reader` and `io.Writer` are two of the most used interfaces in the ecosystem. Both are single-method. They emerged from the observation that many types share the same read and write behavior — the interface was not designed in advance, it was discovered.

When you do need a large interface — say, you're wrapping a complex external SDK — use embedding to compose narrow interfaces:

```go
// RIGHT — compose narrow interfaces rather than one large one
type Reader interface {
    Read(ctx context.Context, id string) (*Record, error)
}

type Writer interface {
    Write(ctx context.Context, r *Record) error
}

type ReadWriter interface {
    Reader
    Writer
}
```

Components that only read declare a `Reader` dependency. Components that only write declare a `Writer`. Only components that need both declare `ReadWriter`. Tests can implement just what they need.

## The Gotchas

**The "testability" argument.** Teams often justify large upfront interfaces by saying "we need it for mocking." But if your unit test requires a 22-method mock, the component under test has too many dependencies. The solution is not a better mock — it is a smaller component. Use the interface proliferation as a signal that the design needs rethinking.

**Circular imports.** Producer-owned interfaces often lead to circular imports: the service imports the repository interface from the repository package, and the repository package imports domain types from the service package. Consumer-owned interfaces break this cycle because each package only imports what it directly uses.

**Interface pollution in public APIs.** If you are writing a library (not an application), the calculus is different. Exporting interfaces that callers can implement to extend behavior is a common library pattern. But keep them small — the Go ecosystem has shown that single-method interfaces like `http.Handler`, `io.Reader`, and `sort.Interface` are the most composable.

**`interface{}` as a shortcut.** Replacing a concrete type with `interface{}` (or `any`) to avoid thinking about the right abstraction is the worst form of this anti-pattern. You lose all type safety and push the problem to runtime. If you find yourself reaching for `any`, stop and think about whether a type parameter (generics) or a properly scoped interface is the right answer.

## Key Takeaway

In Go, interfaces are discovered at the consumer, not declared at the producer. Define interfaces as narrow as they need to be for the consuming code. Start with concrete types and extract an interface only when you have a specific reason — a test boundary, a genuine need for substitution. A codebase with forty single-method interfaces is far healthier than one with four interfaces of ten methods each.

---

**Go Anti-Patterns & Code Smells**

Next: [Lesson 2: Giant God Structs — If your struct has 30 fields, it has 30 problems](/post/go/go-anti-god-structs/)
