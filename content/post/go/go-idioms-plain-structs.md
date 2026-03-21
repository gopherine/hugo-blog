---
title: 'Lesson 24: Prefer Plain Structs — Boring code is correct code'
author: Atharva Pandey
keywords:
  - Go
  - golang
  - structs
  - functional options
  - factory functions
  - design patterns
  - configuration
  - refactoring
tags:
  - Go tutorial
  - golang
series: "Idiomatic Go"
lesson: 24
date: '2025-11-17T00:00:00.000Z'
---

There is a particular brand of cleverness that feels deeply satisfying to write and deeply painful to maintain. The `AbstractServiceProviderFactory`. The builder that returns a builder that configures a builder. The generic interface so abstract it could model anything and therefore models nothing well. Go's culture pushes back hard against this tendency, and for good reason: I've seen more bugs traced to abstraction layers than to simple structs.

## The Problem

The most common form of over-engineering in Go is attempting to import Java-style construction patterns. Go doesn't have constructors, so engineers invent them — and the result is code that's harder to configure, not easier:

```go
// WRONG — trying to enforce Java-style object construction
type Server struct {
    host    string
    port    int
    timeout time.Duration
}

// Zero value is broken — you're forced to use this constructor
func NewServer() *Server {
    return &Server{
        host:    "localhost",
        port:    8080,
        timeout: 30 * time.Second,
    }
}

// But what if I want a different host? The caller has to set fields manually:
s := NewServer()
s.host = "0.0.0.0"  // accessing unexported field — this won't even compile
// So you add getters and setters... and now you have Java in Go
```

The deeper failure mode is enterprise pattern fever. When this thinking scales, you get structures that exist solely to manufacture other structures:

```go
// WRONG — enterprise fever dream
type UserRepositoryInterface interface {
    FindByID(ctx context.Context, id string) (*User, error)
}

type UserRepositoryFactory interface {
    CreateUserRepository() UserRepositoryInterface
}

type AbstractUserRepositoryFactory struct{}

func (f *AbstractUserRepositoryFactory) CreateUserRepository() UserRepositoryInterface {
    return &ConcreteUserRepository{}
}

type UserServiceProvider struct {
    factory UserRepositoryFactory
}
```

A new developer reads this for five minutes and still doesn't know what `UserServiceProvider` does. The abstraction is solving a problem that Go doesn't have.

## The Idiomatic Way

Accept a config struct. Apply defaults for zero values. Keep everything visible and explicit:

```go
// RIGHT — accept a config struct, provide useful defaults
type ServerConfig struct {
    Host    string
    Port    int
    Timeout time.Duration
}

type Server struct {
    config ServerConfig
}

func NewServer(cfg ServerConfig) *Server {
    if cfg.Host == "" {
        cfg.Host = "localhost"
    }
    if cfg.Port == 0 {
        cfg.Port = 8080
    }
    if cfg.Timeout == 0 {
        cfg.Timeout = 30 * time.Second
    }
    return &Server{config: cfg}
}

// Caller is explicit about what they're configuring:
s := NewServer(ServerConfig{
    Host:    "0.0.0.0",
    Port:    9090,
    Timeout: 60 * time.Second,
})
```

Readable. Self-documenting. No documentation needed to understand what each field does.

For the factory problem, strip the abstractions and use the concrete type directly:

```go
// RIGHT — just use the concrete type
type UserRepository struct {
    db *sql.DB
}

func NewUserRepository(db *sql.DB) *UserRepository {
    return &UserRepository{db: db}
}

func (r *UserRepository) FindByID(ctx context.Context, id string) (*User, error) {
    // actual implementation
}
```

If you need to mock `UserRepository` for testing, define a narrow interface at the point of use — not in the repository's own package:

```go
// In the package that needs it, not in the repository package
type userFinder interface {
    FindByID(ctx context.Context, id string) (*User, error)
}
```

This is the Go idiom: accept interfaces, return concrete types. Define the interface where it's consumed. Keep it narrow. Don't create it until you actually need the polymorphism.

The functional options pattern is legitimate, but it has a real cost. Use it for library code that can't predict all configuration needs upfront, or when you need to add options without breaking existing callers. Don't reach for it by default:

```go
// WRONG — functional options for a simple internal type
type DBOption func(*dbPool)
func WithMaxConns(n int) DBOption { return func(p *dbPool) { p.maxConns = n } }
func WithTimeout(d time.Duration) DBOption { return func(p *dbPool) { p.timeout = d } }

// RIGHT — just use a struct
type DBPoolConfig struct {
    MaxConns int
    Timeout  time.Duration
}
func NewDBPool(cfg DBPoolConfig) *dbPool { ... }
```

Functional options add indirection. Each option is a closure. Callers can't see all available options without reading docs or searching for `With*` functions. For internal code used in a handful of places, that cost almost never pays off.

## In The Wild

Here's a realistic before-and-after. You inherit this notification service:

```go
// BEFORE — too clever
type NotificationStrategy interface {
    Notify(ctx context.Context, msg Message) error
}

type NotificationStrategyFactory struct {
    strategies map[string]NotificationStrategy
}

func (f *NotificationStrategyFactory) Register(name string, s NotificationStrategy) {
    f.strategies[name] = s
}

func (f *NotificationStrategyFactory) Get(name string) (NotificationStrategy, error) {
    s, ok := f.strategies[name]
    if !ok {
        return nil, fmt.Errorf("unknown strategy: %s", name)
    }
    return s, nil
}

type NotificationService struct {
    factory *NotificationStrategyFactory
}

func (s *NotificationService) Send(ctx context.Context, channel string, msg Message) error {
    strategy, err := s.factory.Get(channel)
    if err != nil {
        return err
    }
    return strategy.Notify(ctx, msg)
}
```

Refactored:

```go
// AFTER — direct and explicit
type Notifier interface {
    Notify(ctx context.Context, msg Message) error
}

type NotificationService struct {
    channels map[string]Notifier
}

func NewNotificationService(channels map[string]Notifier) *NotificationService {
    return &NotificationService{channels: channels}
}

func (s *NotificationService) Send(ctx context.Context, channel string, msg Message) error {
    n, ok := s.channels[channel]
    if !ok {
        return fmt.Errorf("unknown notification channel: %s", channel)
    }
    return n.Notify(ctx, msg)
}

// Setup is explicit:
svc := NewNotificationService(map[string]Notifier{
    "email": emailNotifier,
    "slack": slackNotifier,
    "sms":   smsNotifier,
})
```

Forty lines became fifteen. Identical behavior. No factory factory. No `Register` method to call in the right order. A new developer reads it in thirty seconds.

## The Gotchas

**Hiding state behind unexported fields you can't initialize.** If your struct's zero value is broken and your `New*` function is the only way to create a valid instance, make sure that function is discoverable. Document the invariant. Unexported fields with no public constructor leave callers writing invalid zero values and wondering why things panic.

**Prematurely extracting interfaces.** Defining `UserRepositoryInterface` before you have a second implementation is speculative abstraction. You're guessing about a future requirement. Write the concrete type. Extract the interface if and when you actually need the substitution.

**Config structs that grow unbounded.** A `ServerConfig` with 20 fields is a code smell. It usually means the type is doing too many things. Split the type before splitting the config struct.

## Key Takeaway

The "boring technology" argument — that boring, predictable, well-understood solutions carry less risk than clever ones — applies just as much to code design as to infrastructure choices. Plain structs are boring. `struct{ fields }` and `func New(cfg Config) *Thing` have no magic. That's exactly the point. Flexibility you don't need is complexity you have to maintain. Write the plain struct first. Add the functional options or the interface when the code tells you it needs them — not in anticipation of problems you might someday have. The instinct to add abstraction comes from good intentions, but in Go, the clearest code is usually the most correct code.

---

← [Lesson 23: Error Values, Not Exceptions](/post/go/go-idioms-error-values) | [Course Index](/post/go/) | [Lesson 25: Simplicity Is a Language Feature](/post/go/go-idioms-simplicity) →
