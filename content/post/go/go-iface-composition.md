---
title: 'Lesson 5: Composition with Embedding — Small interfaces compose into powerful contracts'
author: Atharva Pandey
series: "Go Interfaces & Abstraction Design"
lesson: 5
keywords:
  - Go
  - golang
  - interfaces
  - embedding
  - composition
  - Go design patterns
tags:
  - Go tutorial
  - golang
  - interfaces
date: '2024-11-12T00:00:00.000Z'
---

One of the things that surprised me most about Go when I came from Python was that Go has no inheritance. No base classes, no method overriding, no type hierarchies. What it has instead is embedding — the ability to include one type inside another so that the outer type promotes the inner type's methods. Combined with interface composition, this gives you something more flexible than inheritance: you can build complex behaviors from simple, testable pieces without the fragility that comes from deep class trees.

The key insight is that you should design your interfaces to be small and compose them when you need something bigger. This is the design philosophy behind `io.ReadWriter`, `io.ReadWriteCloser`, and the rest of the `io` package's interface family. Go does not ask you to implement one giant interface — it asks you to implement small ones that combine cleanly.

## The Problem

The instinct coming from OOP is to define one rich interface that captures everything a type might need to do:

```go
// WRONG — one large interface that forces implementors to carry everything
type Storage interface {
    Get(ctx context.Context, key string) ([]byte, error)
    Set(ctx context.Context, key string, val []byte) error
    Delete(ctx context.Context, key string) error
    List(ctx context.Context, prefix string) ([]string, error)
    Watch(ctx context.Context, key string) (<-chan WatchEvent, error)
    Lock(ctx context.Context, key string, ttl time.Duration) (Lock, error)
    Unlock(ctx context.Context, lockID string) error
    Compact(ctx context.Context) error
    Backup(ctx context.Context, w io.Writer) error
}
```

This interface has nine methods. Any consumer that needs only `Get` and `Set` must still hold a type that implements all nine — which means test doubles must implement all nine. Any consumer that only uses `Watch` must declare a dependency on the same interface as consumers that only use `Compact`. The interface is a bag of loosely related functionality bundled together by accident of origin.

When you try to add a caching layer around this interface, you need to forward all nine methods even if you only intercept `Get` and `Set`. When you try to add a read-only version of `Storage`, you cannot express it in terms of the existing interface — you have to define a whole new one or embed it and mark methods as panic-on-call.

## The Idiomatic Way

Break the large interface into small, focused interfaces and compose them at the points where you need the combination:

```go
// RIGHT — small focused interfaces
type Getter interface {
    Get(ctx context.Context, key string) ([]byte, error)
}

type Setter interface {
    Set(ctx context.Context, key string, val []byte) error
}

type Deleter interface {
    Delete(ctx context.Context, key string) error
}

type Lister interface {
    List(ctx context.Context, prefix string) ([]string, error)
}

type Watcher interface {
    Watch(ctx context.Context, key string) (<-chan WatchEvent, error)
}

// Compose when you need the combination
type ReadWriter interface {
    Getter
    Setter
}

type ReadWriteDeleter interface {
    Getter
    Setter
    Deleter
}
```

Now each consumer declares exactly what it needs:

```go
// A read-heavy handler only needs a Getter
type QueryHandler struct {
    store Getter
}

// A replication service needs to read, write, and list
type Replicator struct {
    source Getter
    dest   ReadWriter
    lister Lister
}
```

The test doubles are proportionally small. A test for `QueryHandler` implements exactly one method. A test for `Replicator` implements the three methods those operations require — nothing more.

Embedding applies to structs as well, and it is the Go equivalent of a mixin. You can embed an interface inside a struct to satisfy the interface and delegate methods:

```go
// ReadOnlyStore wraps a full store but only exposes read operations
type ReadOnlyStore struct {
    Getter // embedded: ReadOnlyStore satisfies Getter automatically
    Lister // embedded: ReadOnlyStore satisfies Lister automatically
}

func NewReadOnlyStore(g Getter, l Lister) *ReadOnlyStore {
    return &ReadOnlyStore{Getter: g, Lister: l}
}

// Usage: pass a ReadOnlyStore wherever a Getter or Lister is accepted
// without exposing Set, Delete, Watch, Lock, etc.
```

You built a capability restriction layer in four lines. No forwarding methods, no boilerplate. The concrete type behind `Getter` still does the work — `ReadOnlyStore` just controls what the outside world can see.

## In The Wild

The `io` package is the canonical example. `io.ReadWriter` is defined as:

```go
type ReadWriter interface {
    Reader
    Writer
}
```

Nothing more. That is the entire definition. Any type that implements both `Read` and `Write` automatically satisfies `ReadWriter`. You can pass it to anything that accepts a `Reader`, anything that accepts a `Writer`, or anything that accepts a `ReadWriter`. The composition is free and complete.

When I built a distributed configuration store, I started with a single `ConfigStore` interface with eight methods and ran into every problem described above. I refactored to small composable interfaces, and the results were immediate:

```go
// Small interfaces make the permission model explicit
type ConfigReader interface {
    Get(ctx context.Context, key string) (string, error)
    GetAll(ctx context.Context, namespace string) (map[string]string, error)
}

type ConfigWriter interface {
    Set(ctx context.Context, key, value string) error
    Delete(ctx context.Context, key string) error
}

type ConfigStore interface {
    ConfigReader
    ConfigWriter
}

// Read-only service gets only what it needs
type AuditService struct {
    store ConfigReader
}

// Admin service gets the full contract
type AdminService struct {
    store ConfigStore
}
```

The `AuditService` can never accidentally write to the config store — not through a bug, not through a refactor. The type system enforces the permission boundary at compile time.

Adding a caching decorator became straightforward:

```go
// CachedReader wraps a ConfigReader with an in-memory cache
type CachedReader struct {
    inner ConfigReader
    cache *sync.Map
}

func (c *CachedReader) Get(ctx context.Context, key string) (string, error) {
    if v, ok := c.cache.Load(key); ok {
        return v.(string), nil
    }
    val, err := c.inner.Get(ctx, key)
    if err == nil {
        c.cache.Store(key, val)
    }
    return val, err
}

func (c *CachedReader) GetAll(ctx context.Context, ns string) (map[string]string, error) {
    return c.inner.GetAll(ctx, ns)
}
```

`CachedReader` satisfies `ConfigReader`. It does not need to know anything about `ConfigWriter` or `ConfigStore`. If I later need to invalidate the cache on writes, I compose a `ConfigStore`-aware invalidator — without changing anything already written.

## The Gotchas

**Embedding an interface in a struct is not the same as embedding a struct.** When you embed an interface in a struct, the embedded field is `nil` by default. If you forget to initialize it, you get a nil pointer panic at runtime when a method is called. Always initialize embedded interfaces in your constructor.

```go
// Dangerous if inner is nil
type ReadOnlyStore struct {
    Getter
}

// Safe: always initialize
func NewReadOnlyStore(g Getter) *ReadOnlyStore {
    return &ReadOnlyStore{Getter: g} // explicit initialization
}
```

**Name collisions need resolution.** If two embedded interfaces have a method with the same name but different signatures, the outer type does not compile. Design your small interfaces so their method names are distinct enough to compose without collision.

**Do not go so small that composition becomes verbose.** A `Namer` interface with one method `Name() string` might be too granular. Use judgment — the goal is "focused," not "one method maximum." Group methods that are always used together.

## Key Takeaway

Small interfaces compose into powerful contracts exactly the way small functions compose into complex behavior. The embedding mechanism in Go makes this composition essentially free: if you design your interfaces to be narrow and coherent, you can combine them at whatever granularity a consumer requires without any extra code. The result is permission boundaries enforced by the compiler, test doubles that are proportional to what is being tested, and decorator layers that can target exactly the methods they need to intercept. The `io` package proved this design at scale twenty years ago. It is worth copying.

---

← [Lesson 4: Returning Concrete Types](/post/go/go-iface-return-concrete) | [Course Index](/series/go-interfaces-abstraction-design) | [Next → Lesson 6: Testability Without Over-Mocking](/post/go/go-iface-testability)
