---
title: "Lesson 3: Global Mutable State — The variable that breaks every test"
author: Atharva Pandey
series: "Go Anti-Patterns & Code Smells"
lesson: 3
keywords:
  - Go
  - golang
  - anti-patterns
  - code quality
tags:
  - Go tutorial
  - golang
  - code quality
date: '2024-09-05T00:00:00.000Z'
---

There was a test suite I worked on where tests passed individually but failed when run together. The failure pattern was non-deterministic — sometimes test A broke test B, sometimes test C broke test A, and it changed with the `-count` flag. After two hours of bisecting, we found it: a package-level `var config Config` that every test modified by calling `loadConfig("testdata/some-fixture.json")`. The tests were sharing state without knowing it, and the one that ran last set the config for all the others.

Global mutable state in Go is seductive because it is convenient — you can access it from anywhere without passing arguments through every function call. It is destructive because it creates invisible dependencies between code paths that you thought were independent. It makes tests flaky, makes concurrent code unsafe, and makes large codebases increasingly difficult to reason about.

## The Problem

Package-level variables that are mutated at runtime are the most common form of global mutable state:

```go
// WRONG — package-level mutable variables
package app

var (
    db     *sql.DB        // initialized in main, used everywhere
    logger *log.Logger    // same
    cfg    Config         // loaded at startup, possibly reloaded
    cache  map[string]any // mutable cache, race condition waiting to happen
)

func init() {
    cfg = loadConfig()
    db = connectDB(cfg)
    logger = log.New(os.Stdout, "", log.LstdFlags)
    cache = make(map[string]any)
}

func GetUser(id string) (*User, error) {
    // Accesses global db — can't be tested without a real database
    return queryUser(db, id)
}

func CacheSet(key string, val any) {
    cache[key] = val // DATA RACE if called concurrently
}
```

Every function that touches `db`, `logger`, `cfg`, or `cache` has a hidden dependency on global state. Tests for `GetUser` cannot be isolated — they require the global `db` to be initialized. The `cache` map has a data race under any concurrent access.

A subtler form is using the `init()` function for side effects that should be explicit:

```go
// WRONG — init() registering global state
func init() {
    http.HandleFunc("/api/users", handleUsers) // registers into global DefaultServeMux
    http.HandleFunc("/api/orders", handleOrders)
}
```

Functions registered in `init()` are invisible to the caller. Any test that imports this package gets the side effects, even if it does not want them. You cannot prevent it.

## The Idiomatic Way

Move shared state into a struct that is created explicitly and injected through constructors:

```go
// RIGHT — dependencies held in a struct, explicitly constructed
package app

type App struct {
    db     *sql.DB
    logger *slog.Logger
    cache  *Cache
    cfg    Config
}

func NewApp(cfg Config) (*App, error) {
    db, err := sql.Open("postgres", cfg.DatabaseURL)
    if err != nil {
        return nil, fmt.Errorf("open database: %w", err)
    }

    return &App{
        db:     db,
        logger: slog.New(slog.NewJSONHandler(os.Stdout, nil)),
        cache:  NewCache(),
        cfg:    cfg,
    }, nil
}

func (a *App) GetUser(ctx context.Context, id string) (*User, error) {
    return queryUser(ctx, a.db, id)
}
```

Tests construct an `App` with test-specific dependencies:

```go
// RIGHT — test constructs its own App with test doubles
func TestGetUser(t *testing.T) {
    db := openTestDB(t)         // temporary test database
    logger := slog.New(slog.NewTextHandler(io.Discard, nil)) // silent in tests

    app := &App{
        db:     db,
        logger: logger,
        cache:  NewCache(),
    }

    user, err := app.GetUser(context.Background(), "test-user-id")
    // ...
}
```

For state that genuinely needs to be shared across goroutines, protect it explicitly with a mutex rather than leaving it as an unprotected global:

```go
// RIGHT — thread-safe cache with explicit locking
type Cache struct {
    mu    sync.RWMutex
    items map[string]any
}

func NewCache() *Cache {
    return &Cache{items: make(map[string]any)}
}

func (c *Cache) Set(key string, val any) {
    c.mu.Lock()
    defer c.mu.Unlock()
    c.items[key] = val
}

func (c *Cache) Get(key string) (any, bool) {
    c.mu.RLock()
    defer c.mu.RUnlock()
    val, ok := c.items[key]
    return val, ok
}
```

## In The Wild

**`slog.SetDefault`.** The `log/slog` package (Go 1.21+) has a package-level default logger. Calling `slog.SetDefault` in your library code mutates global state that affects every other library in the binary. In application code, it is fine. In library code, accept a `*slog.Logger` as a parameter instead.

**Feature flags.** A common pattern is storing feature flags in a package-level variable and updating them at runtime. This works but introduces a data race if the variable is written by one goroutine and read by another. Use `sync/atomic.Value` for the atomic pointer swap, or a struct with a mutex:

```go
// RIGHT — atomic feature flag reload
var featureFlags atomic.Value // holds FeatureFlags struct

func ReloadFeatureFlags(flags FeatureFlags) {
    featureFlags.Store(flags)
}

func IsFeatureEnabled(name string) bool {
    flags, _ := featureFlags.Load().(FeatureFlags)
    return flags.IsEnabled(name)
}
```

**Test isolation with `t.Cleanup`.** If you must use global state in tests (legacy code, third-party packages that use globals), restore it in a cleanup function:

```go
func TestWithGlobal(t *testing.T) {
    original := globalVar
    t.Cleanup(func() { globalVar = original }) // always restored, even on failure
    globalVar = "test-value"
    // ... test
}
```

## The Gotchas

**`sync.Once` and global initialization.** Using `sync.Once` to lazily initialize a global is still global mutable state — it just defers the mutation. The component that reads the global still has a hidden dependency. `sync.Once` is appropriate for truly immutable initialization (registering a package's codecs once), but not for application configuration or connections.

**Package-level `var _ = ...` assignments.** Some packages use blank identifier assignments in `var` blocks to trigger side effects. These are global state mutations that are very hard to see in code review. Be suspicious of any `var _ = someFunction()` at the package level.

**Test parallelism.** When you run tests with `t.Parallel()`, tests in the same package run concurrently. Any global state accessed by those tests will have data races. The `-race` flag will catch this — run `go test -race ./...` regularly to find these before they cause flaky CI.

**`http.DefaultServeMux`** is the canonical example of global mutable state in the standard library. It is convenient for small programs and absolutely wrong for libraries. Always construct an explicit `http.ServeMux` and pass it where needed, never register into the default.

## Key Takeaway

Global mutable state creates invisible dependencies, makes tests non-deterministic, and introduces data races under concurrency. The solution is a struct that holds its own state, constructed explicitly in `main` and injected wherever it is needed. This is not about religious adherence to dependency injection frameworks — it is about making dependencies visible at the function signature level, where they can be reasoned about and replaced in tests.

---

**Go Anti-Patterns & Code Smells**

Previous: [Lesson 2: Giant God Structs — If your struct has 30 fields, it has 30 problems](/post/go/go-anti-god-structs/)
Next: [Lesson 4: Channel Misuse — You used a channel where a mutex would do](/post/go/go-anti-channel-misuse/)
