---
title: 'Go Idioms: Prefer Plain Structs Over Clever Abstractions'
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
date: '2025-11-17T00:00:00.000Z'
---
s a particular brand of cleverness that feels deeply satisfying to write and deeply painful to maintain. You've probably encountered it: the `AbstractServiceProviderFactory`, the builder that returns a builder that configures a builder, the generic interface so abstract it could model anything and therefore models nothing well. Go's culture pushes back hard against this tendency.

The Go community has a phrase: "clear is better than clever." It's not just a slogan — it's a design principle that shapes what idiomatic Go actually looks like in production.

## Factory Functions, Not Constructors

Go doesn't have constructors. You create values, not objects. When a type needs initialization logic, you write a plain function.

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

// But what if I want a different host? The caller has to go set fields manually:
s := NewServer()
s.host = "0.0.0.0"  // accessing unexported field — this won't even compile
// So you add getters and setters... and now you have Java in Go
```

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

The config struct approach is readable, doesn't require documentation to understand, and lets callers provide exactly what they need while getting sensible defaults for the rest.

## The Functional Options Pattern — and When It's Actually Worth It

Functional options are a popular Go pattern. The idea is to pass option functions that configure a value:

```go
type Option func(*Server)

func WithHost(host string) Option {
    return func(s *Server) { s.config.Host = host }
}

func WithPort(port int) Option {
    return func(s *Server) { s.config.Port = port }
}

func NewServer(opts ...Option) *Server {
    s := &Server{config: ServerConfig{
        Host:    "localhost",
        Port:    8080,
        Timeout: 30 * time.Second,
    }}
    for _, opt := range opts {
        opt(s)
    }
    return s
}

// Usage:
s := NewServer(WithHost("0.0.0.0"), WithPort(9090))
```

This pattern is legitimate and well-suited to specific situations. It shines when:

- The type is part of a library and you can't predict all configuration needs at the time you write it
- You need to add configuration options without breaking existing callers
- Some options are computed or conditional in ways that don't fit neatly into a struct literal

But be honest about when you actually need it. For an internal server type used in two places, a plain config struct is clearer. Functional options add indirection. Each option is a closure. The caller can't see all available options without reading the docs. For internal code, that cost rarely pays off.

```go
// WRONG — functional options for a simple internal type
// Nothing here justifies the added complexity
type dbPool struct{ maxConns int; timeout time.Duration }

type DBOption func(*dbPool)
func WithMaxConns(n int) DBOption { return func(p *dbPool) { p.maxConns = n } }
func WithTimeout(d time.Duration) DBOption { return func(p *dbPool) { p.timeout = d } }

func NewDBPool(opts ...DBOption) *dbPool { ... }

// RIGHT — just use a struct
type DBPoolConfig struct {
    MaxConns int
    Timeout  time.Duration
}
func NewDBPool(cfg DBPoolConfig) *dbPool { ... }
```

## Avoiding Enterprise Patterns

Go is not Java. The patterns that manage complexity in large Java codebases — dependency injection frameworks, abstract factory hierarchies, service locators — solve problems Go doesn't have in the same way.

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

func NewUserServiceProvider(factory UserRepositoryFactory) *UserServiceProvider {
    return &UserServiceProvider{factory: factory}
}
```

```go
// RIGHT — just use the concrete type, define an interface only when you need polymorphism
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

If you later need to mock `UserRepository` for testing, define a narrow interface at the point of use:

```go
// In the package that needs it, not in the repository package
type userFinder interface {
    FindByID(ctx context.Context, id string) (*User, error)
}
```

This is the Go idiom: accept interfaces, return concrete types. Define the interface where it's consumed, keep it narrow, and don't create it until you need it.

## A Refactoring: From Over-Engineered to Simple

Here's a realistic before-and-after. Suppose you inherited this:

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

Now the refactored version:

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

Forty lines became fifteen. The behavior is identical. The intent is clearer. There's no factory factory, no Register method to call, no ordering dependency in setup code. A new developer can read this in thirty seconds.

## The Boring Technology Advantage

The "boring technology" argument — made famous in infrastructure circles but equally applicable to code design — is that boring, predictable, well-understood solutions carry less risk than clever ones. They're easier to debug at 2am. They're easier to hand off. They survive team turnover.

Go's plain struct pattern is boring technology. It's `struct{ fields }` and `func New(cfg Config) *Thing`. It has no magic. That's exactly the point.

The instinct to reach for abstraction often comes from good intentions — you want the code to be flexible, extensible, elegant. But flexibility you don't need is complexity you have to maintain. Add abstraction when the concrete problem in front of you demands it, not in anticipation of problems you might someday have.

Write the plain struct first. Refactor toward the pattern when the code tells you it needs it — not before.
