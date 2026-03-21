---
title: 'Lesson 4: Returning Concrete Types — Give callers the real thing'
author: Atharva Pandey
series: "Go Interfaces & Abstraction Design"
lesson: 4
keywords:
  - Go
  - golang
  - interfaces
  - concrete types
  - return types
  - Go design patterns
tags:
  - Go tutorial
  - golang
  - interfaces
date: '2024-10-01T00:00:00.000Z'
---

There is a piece of Go advice that sounds almost too obvious to state, and yet I see it violated in production codebases every week: return concrete types from your functions. Not interfaces. Not `interface{}`. The actual, named, exported struct that your function produces.

The reason this feels controversial is that it conflicts with object-oriented instincts. In Java and C#, returning an interface from a factory is considered good practice — it hides the implementation detail and "programs to an interface." In Go, that instinct leads to information loss, surprise type assertions, and constructors that promise less than they deliver.

## The Problem

Here is the pattern I see most often: a constructor that returns an interface because the developer wants to "keep options open."

```go
// WRONG — returning an interface from a constructor
package cache

type Cache interface {
    Get(key string) ([]byte, bool)
    Set(key string, val []byte, ttl time.Duration)
    Delete(key string)
}

type memoryCache struct {
    mu      sync.RWMutex
    entries map[string]entry
    stats   Stats
}

func New() Cache {
    return &memoryCache{entries: make(map[string]entry)}
}

// memoryCache also has these, but callers can't reach them:
func (c *memoryCache) Stats() Stats       { return c.stats }
func (c *memoryCache) Flush()             { /* clear all entries */ }
func (c *memoryCache) Len() int           { return len(c.entries) }
```

The `New` function returns `Cache`. But `memoryCache` has `Stats()`, `Flush()`, and `Len()` — useful methods for monitoring, testing, and lifecycle management. By returning the interface, the constructor threw them away. Any caller who needs `Flush()` has to write this:

```go
c := cache.New()
// Need to flush? Type assert your way back to the concrete type.
if mc, ok := c.(*cache.memoryCache); ok {
    mc.Flush()
}
```

Except `memoryCache` is unexported, so this does not even compile. The caller is stuck. They have to either add `Flush` to the `Cache` interface (making it wider than it should be) or restructure their code entirely. You hid the thing they needed.

The second version of this mistake is using `interface{}` or `any` as a return type when a concrete type is known:

```go
// WRONG — loses all type information
func NewHandler(kind string) interface{} {
    switch kind {
    case "http":
        return &HTTPHandler{}
    case "grpc":
        return &GRPCHandler{}
    }
    return nil
}
```

Every caller must type-assert the result before they can use it. This is the worst of both worlds: you have abandoned static typing without gaining any real flexibility.

## The Idiomatic Way

Return the concrete type. Always. The caller decides what interface to hold it through.

```go
// RIGHT — return the concrete type, all methods are accessible
package cache

type MemoryCache struct {
    mu      sync.RWMutex
    entries map[string]entry
    stats   Stats
}

func New() *MemoryCache {
    return &MemoryCache{entries: make(map[string]entry)}
}

func (c *MemoryCache) Get(key string) ([]byte, bool)                  { /* ... */ }
func (c *MemoryCache) Set(key string, val []byte, ttl time.Duration)  { /* ... */ }
func (c *MemoryCache) Delete(key string)                              { /* ... */ }
func (c *MemoryCache) Stats() Stats                                   { return c.stats }
func (c *MemoryCache) Flush()                                         { /* ... */ }
func (c *MemoryCache) Len() int                                       { return len(c.entries) }
```

Now a caller who only needs the cache interface behavior can hold it as one:

```go
// Caller chooses the narrowest interface it needs
type cacher interface {
    Get(key string) ([]byte, bool)
    Set(key string, val []byte, ttl time.Duration)
}

func NewService(c cacher) *Service { return &Service{cache: c} }

// In main.go or test setup:
mc := cache.New()
defer mc.Flush() // accessible because we hold the concrete type here
svc := NewService(mc) // compiles: *cache.MemoryCache satisfies cacher
```

The caller at the composition root holds the concrete type and can access all methods. The service that needs only the cache behavior holds the narrow interface. No type assertions. No hidden methods. No information loss.

This same principle applies everywhere a function creates something:

```go
// RIGHT — constructors return concrete types
func NewHTTPClient(timeout time.Duration) *http.Client {
    return &http.Client{
        Timeout: timeout,
        Transport: &http.Transport{
            MaxIdleConns:    100,
            IdleConnTimeout: 90 * time.Second,
        },
    }
}

func NewSlogLogger(level slog.Level) *slog.Logger {
    return slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
        Level: level,
    }))
}
```

Both return concrete types. Both expose everything. The caller who wants to hold `*http.Client` as some narrower interface can do so at the call site.

## In The Wild

The Go standard library is consistent about this. `bytes.NewBuffer` returns `*bytes.Buffer`, not `io.ReadWriter`. `bufio.NewReader` returns `*bufio.Reader`, not `io.Reader`. `os.Open` returns `*os.File`, not `io.Reader` — even though `*os.File` satisfies `io.Reader`, `io.Writer`, `io.Seeker`, `io.Closer`, and several more.

This pattern means you can always pass an `*os.File` to a function that accepts `io.Reader`, but you can also call `file.Seek(0, io.SeekStart)` when you need to reset position, `file.Stat()` when you need file metadata, and `file.Close()` when you are done — none of which are available on `io.Reader`.

I once worked on a codebase that had a `NewDB` constructor returning a `DB` interface. The interface had twelve methods. The production implementation was a postgres wrapper. The test implementation was a sqlite wrapper. The sqlite wrapper was subtly incompatible in about four behaviors — things like how it handled concurrent writes, whether it supported `RETURNING` clauses, whether certain error codes matched. The interface gave the illusion of substitutability where the semantics were actually different.

The fix was to stop hiding the concrete postgres type:

```go
// Concrete type, exported, full access
func NewPostgresDB(connStr string) (*PostgresDB, error) {
    db, err := sql.Open("postgres", connStr)
    if err != nil {
        return nil, err
    }
    return &PostgresDB{db: db}, nil
}
```

Tests that needed a real database got one. Tests that needed a fake got an in-process fake — a lightweight struct that recorded calls, not a different SQL implementation pretending to be postgres. The semantic mismatch disappeared because we stopped pretending the implementations were interchangeable.

## The Gotchas

**The one legitimate exception is `error`.** `error` is an interface return type. But `error` is special: callers who want to inspect the concrete error type use `errors.As`, which is designed for exactly that purpose, and most callers don't need the concrete type at all. This is not a template for your own types — it is a narrow exception for a value type whose concrete form is deliberately opaque.

**Package-level `var _ Interface = (*Concrete)(nil)` checks.** If you want to verify at compile time that your concrete type satisfies an interface, use this pattern in the package that defines the type. It is a compile-time assertion, not a commitment to return the interface.

```go
// Compile-time check: MemoryCache must satisfy the Cache interface
var _ Cache = (*MemoryCache)(nil)
```

**Returning concrete types does not mean exporting every field.** Keep fields unexported. The concrete type exposes its capabilities through methods, not through field access. Returning `*MemoryCache` does not mean callers can reach into `mc.entries` — it means they can call `mc.Flush()`.

## Key Takeaway

Returning concrete types is not a shortcut or a failure to plan for extensibility. It is the correct default in Go because it preserves the maximum capability for callers. Callers can always narrow a concrete type to an interface at their call site — they cannot widen an interface to a concrete type without a type assertion. By returning the real thing, you give callers control. By returning an interface, you take it away. The standard library made this choice everywhere it matters, and the result is a set of packages that compose with almost anything. Follow the same principle in your own constructors and you will build types that are just as easy to use.

---

← [Lesson 3: Interface Pollution](/post/go/go-iface-pollution) | [Course Index](/series/go-interfaces-abstraction-design) | [Next → Lesson 5: Composition with Embedding](/post/go/go-iface-composition)
