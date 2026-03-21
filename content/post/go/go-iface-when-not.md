---
title: 'Lesson 1: When NOT to Use Interfaces — Abstractions cost clarity'
author: Atharva Pandey
series: "Go Interfaces & Abstraction Design"
lesson: 1
keywords:
  - Go
  - golang
  - interfaces
  - abstraction
  - over-engineering
  - Go design patterns
tags:
  - Go tutorial
  - golang
  - interfaces
date: '2024-06-08T00:00:00.000Z'
---

The first time I saw Go interfaces click in a codebase, I made the same mistake every developer makes: I started reaching for them everywhere. If I had a struct, I made an interface for it. If I had two types that shared even one method name, I defined an interface over both. It felt disciplined. It felt like proper software engineering. It was making my code worse.

The uncomfortable truth about Go interfaces is that they are most powerful when you use them least. Unlike Java, where every class implementing an interface is an explicit declaration, Go interfaces are satisfied implicitly. That means there is almost zero syntax cost to adding one. And that near-zero cost hides the real cost: cognitive overhead, indirection, and the loss of explicit signal about what a piece of code actually does.

## The Problem

Here is a pattern I see constantly in Go codebases, especially from developers coming from object-oriented languages:

```go
// WRONG — interface defined at the implementation site for a type that has one user
type UserServiceInterface interface {
    CreateUser(ctx context.Context, req CreateUserRequest) (*User, error)
    GetUser(ctx context.Context, id string) (*User, error)
    DeleteUser(ctx context.Context, id string) error
    ListUsers(ctx context.Context, filter UserFilter) ([]*User, error)
    UpdateUser(ctx context.Context, id string, req UpdateUserRequest) (*User, error)
}

type userService struct {
    db   *sql.DB
    log  *slog.Logger
    cache *redis.Client
}

func NewUserService(db *sql.DB, log *slog.Logger, cache *redis.Client) UserServiceInterface {
    return &userService{db: db, log: log, cache: cache}
}
```

The interface is defined in the same package as the struct. It mirrors the struct's methods one-to-one. The constructor returns the interface, not the concrete type. And there is only one implementation — the one right below the interface definition.

This pattern looks safe. It looks testable. But if you try to mock this interface in a test, you are writing a twelve-method mock for a function that probably only calls `GetUser`. And if tomorrow you want to add `BulkCreateUsers` to `userService`, you have to update the interface, every mock, and every consumer. You created bureaucracy without buying anything.

The second version of this mistake is the "just in case" interface:

```go
// WRONG — abstraction for a dependency that will never change
type ConfigLoaderInterface interface {
    Load() (Config, error)
}

type envConfigLoader struct{}

func (e *envConfigLoader) Load() (Config, error) {
    // reads from os.Getenv
}
```

There will never be another `ConfigLoader`. You are not going to hot-swap config loading strategies at runtime. The interface exists "just in case" — and it makes every reader of this code ask: what else can a `ConfigLoader` be? There is no answer. The question is noise.

## The Idiomatic Way

The idiomatic Go answer is simple: don't add an interface until you need polymorphism. Specifically, the right time to introduce an interface is when:

1. You have two or more concrete types that need to be used interchangeably.
2. You need to decouple a dependency for testing and you cannot use the concrete type directly.
3. You are writing a library and you want callers to provide their own implementations.

Until one of those is true, use the concrete type directly.

```go
// RIGHT — return the concrete type, let callers decide what interface they need
type UserService struct {
    db    *sql.DB
    log   *slog.Logger
    cache *redis.Client
}

func NewUserService(db *sql.DB, log *slog.Logger, cache *redis.Client) *UserService {
    return &UserService{db: db, log: log, cache: cache}
}

func (s *UserService) CreateUser(ctx context.Context, req CreateUserRequest) (*User, error) {
    // implementation
}

func (s *UserService) GetUser(ctx context.Context, id string) (*User, error) {
    // implementation
}
```

Now when your HTTP handler only needs `GetUser`, it defines the minimal interface it requires:

```go
// RIGHT — interface defined at the consumer, only as wide as needed
type userGetter interface {
    GetUser(ctx context.Context, id string) (*User, error)
}

type UserHandler struct {
    users userGetter
}
```

The interface lives in the package that needs it. It is narrow. If tomorrow `UserService` gains twenty more methods, this interface does not change. And in tests, you implement a two-line struct that satisfies `userGetter` — not a twenty-method mock.

Here is the key insight: interfaces in Go are implicit, which means the relationship between a concrete type and an interface is always something you can add after the fact. You do not need to declare it upfront. When you actually need polymorphism, you write the interface then — and the concrete type already satisfies it without changes.

```go
// Concrete type written first, no interface planned
type DiskCache struct {
    dir string
}

func (c *DiskCache) Get(key string) ([]byte, error) { /* ... */ }
func (c *DiskCache) Set(key string, val []byte) error { /* ... */ }

// Later, when you add a MemoryCache and need to use them interchangeably:
type Cache interface {
    Get(key string) ([]byte, error)
    Set(key string, val []byte) error
}

// DiskCache already satisfies Cache. No changes needed.
```

## In The Wild

I once inherited a service with forty-three interfaces in a single `interfaces.go` file. Every struct had a corresponding interface. Every constructor returned an interface. There were interfaces for things like `TimeProvider` (one method: `Now() time.Time`) that existed because someone heard "you should mock time" and ran with it.

The practical effect was that reading any piece of code required jumping through three layers of indirection to understand what was actually happening. Adding a method to a struct meant touching five files. Running the test suite required an IDE that could generate mocks automatically because maintaining them by hand was impossible.

The cleanup was gradual. We identified interfaces with exactly one implementation and removed them, replacing interface returns with concrete types. We moved the remaining interfaces — the real ones, for things like external HTTP clients and database layers — to the packages that consumed them. The codebase dropped by roughly 800 lines. Test compilation time went down. New engineers stopped asking "what is this interface for?"

```go
// Before: 3 files, 2 indirections, 1 real implementation
// interfaces/time.go
type TimeProvider interface {
    Now() time.Time
}

// providers/time.go
type realTimeProvider struct{}
func (r *realTimeProvider) Now() time.Time { return time.Now() }

// app.go
func New(tp interfaces.TimeProvider) *App { ... }

// After: just use time.Now() directly, or pass it as a function value if you need to mock it
type App struct {
    now func() time.Time
}

func New() *App {
    return &App{now: time.Now}
}

// In tests:
app := &App{now: func() time.Time { return time.Date(2024, 1, 1, 0, 0, 0, 0, time.UTC) }}
```

A function value is simpler than an interface when you only need one method. The standard library uses this pattern extensively — `http.HandlerFunc`, `sort.Slice`, and many others accept function values rather than single-method interfaces.

## The Gotchas

**Every interface imposes a cost on mocking.** When an interface is defined, someone can mock it. When someone mocks it, test suites grow brittle because they assert on every method call rather than behavior. Less interface surface area means fewer opportunities for over-specified tests.

**Interfaces do not document intent.** A concrete struct with exported fields and doc comments tells you exactly what you are working with. An interface tells you only the contract. In most cases, the extra indirection is not worth the information loss.

**Implicit satisfaction cuts both ways.** A type can accidentally satisfy an interface you never intended it to satisfy. This is almost never a problem in practice, but it is a reason not to define wide interfaces with common method names — they can create false compatibility where none was intended.

## Key Takeaway

The Go designers gave you interfaces without forcing you to declare them. That restraint is the hint. Interfaces are a tool for managing polymorphism when you actually have it — not a default architectural layer you add to every type on principle. The next time you find yourself writing an interface for a type with one implementation, ask whether the caller actually needs the abstraction or whether you are just mirroring structure for its own sake. Usually the answer is the latter, and the idiomatic move is to delete the interface and return the concrete type instead.

---

[Course Index](/series/go-interfaces-abstraction-design) | [Next → Lesson 2: Consumer-Side Interfaces](/post/go/go-iface-consumer-side)
