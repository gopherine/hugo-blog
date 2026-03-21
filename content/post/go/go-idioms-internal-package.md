---
title: 'Go Idioms: internal Package Is Underrated'
author: Atharva Pandey
keywords:
  - Go
  - golang
  - internal package
  - package visibility
  - project structure
  - API design
  - module boundaries
tags:
  - Go tutorial
  - golang
date: '2025-09-08T00:00:00.000Z'
---
s exported; if it doesn't, it's only accessible within the same package. What many developers don't reach for often enough is the `internal` directory, which gives you a third option: accessible within your module (or a specific subtree), but not to outside importers. The compiler enforces it, not documentation or convention.

This matters more than it sounds. Exported symbols are API commitments. The `internal` package lets you share code across multiple packages in your own codebase without accidentally publishing an API surface you'll have to maintain forever.

## How internal Works

The rule is simple: a package path containing an `internal` directory can only be imported by code rooted at the parent of the `internal` directory.

Given this layout:

```
mymodule/
├── internal/
│   └── auth/
│       └── token.go
├── server/
│   └── handler.go
└── client/
    └── client.go
```

`server/handler.go` and `client/client.go` can both import `mymodule/internal/auth`. But if you have a separate module — say `myothertool` — and it tries to import `mymodule/internal/auth`, the Go compiler refuses:

```
imports mymodule/internal/auth: use of internal package not allowed
```

This enforcement is at compile time, not runtime. No `go build` flag to override it. No workaround other than restructuring.

The `internal` directory can appear anywhere in a package path, not just at the top level:

```
mymodule/
└── cmd/
    └── server/
        ├── internal/
        │   └── config/
        │       └── config.go
        └── main.go
```

Here, `mymodule/cmd/server/internal/config` can only be imported by code under `mymodule/cmd/server/`. Even other packages in `mymodule` can't import it.

## Why You Should Use It More

Here's the problem internal solves. You're building a package, say `github.com/yourorg/yourpkg`. You have helper code — shared types, utility functions — that you need in multiple internal packages. Without `internal`, you have two bad options:

1. Export those helpers. Now they're public API. Users will import and depend on them. Changing them becomes a breaking change.
2. Duplicate the code across packages. Now you have maintenance hell.

`internal` gives you a third way:

```go
// WRONG — exporting utility types that should be internal
// github.com/yourorg/yourpkg/helper/types.go
package helper

// TokenPayload is exported, but you never intended it to be public API
type TokenPayload struct {
    UserID    string
    ExpiresAt time.Time
    Scopes    []string
}
```

Once this is exported, external packages can import it. If you change the field names or the package path, you break their code. You've accidentally published an API.

```go
// RIGHT — internal keeps it private to your module
// github.com/yourorg/yourpkg/internal/token/token.go
package token

type Payload struct {
    UserID    string
    ExpiresAt time.Time
    Scopes    []string
}

func Parse(raw string) (Payload, error) {
    // ... JWT parsing logic
}
```

Now `Payload` and `Parse` can be used freely by any package in `github.com/yourorg/yourpkg`, but they're invisible to external importers. You can refactor, rename, or restructure them without worrying about breaking downstream users.

## Structuring a Real Project with internal

A mature Go project typically looks something like this:

```
myapp/
├── cmd/
│   ├── server/
│   │   └── main.go          # binary entry point
│   └── worker/
│       └── main.go          # another binary
├── internal/
│   ├── auth/
│   │   ├── auth.go
│   │   └── auth_test.go
│   ├── database/
│   │   ├── db.go
│   │   └── migrations/
│   ├── middleware/
│   │   └── middleware.go
│   └── config/
│       └── config.go
├── pkg/
│   └── apiclient/           # intended for external use
│       └── client.go
└── go.mod
```

The layout convention: `cmd/` holds binary entry points, `internal/` holds everything that's implementation detail, `pkg/` holds what you deliberately expose to external importers.

The `cmd/server/main.go` wires it all together:

```go
package main

import (
    "myapp/internal/auth"
    "myapp/internal/config"
    "myapp/internal/database"
    "myapp/internal/middleware"
)

func main() {
    cfg := config.Load()
    db := database.Connect(cfg.DatabaseURL)
    authSvc := auth.NewService(db, cfg.JWTSecret)

    mux := http.NewServeMux()
    mux.Handle("/api/", middleware.Auth(authSvc, apiHandler(db)))

    http.ListenAndServe(cfg.Addr, mux)
}
```

Everything in `internal/` is freely shareable within `myapp`, but completely opaque to external code. If someone vendors your module or imports your `pkg/apiclient`, they can't accidentally (or deliberately) reach into your internals.

## Preventing Accidental API Surface Leaks

The `internal` package is especially useful when your codebase is a library or SDK that other teams or external users import. Without it, any exported type becomes part of your public API surface, even if you only exported it to share it across two internal packages.

```go
// WRONG — this is now public API even though it's an implementation detail
// github.com/yourorg/sdk/httputil/request.go
package httputil

// RetryState tracks retry attempts — not intended for public use
type RetryState struct {
    Attempts  int
    LastError error
    Backoff   time.Duration
}
```

External packages can now import and use `RetryState`. If you want to change its fields in v2, you need to either version your module or accept a breaking change.

```go
// RIGHT — move it to internal
// github.com/yourorg/sdk/internal/retry/state.go
package retry

type State struct {
    Attempts  int
    LastError error
    Backoff   time.Duration
}
```

Now `State` is freely usable within `github.com/yourorg/sdk`, but external packages can't import it. You can change it however you want without it being a breaking API change.

## Refactoring a Package: A Concrete Example

Suppose you have an `auth` package that's grown too large. It handles token generation, user lookup, and session management all in one file. You want to split it but keep the public API stable.

Before refactoring:

```
internal/
└── auth/
    └── auth.go  # 600 lines doing everything
```

After:

```
internal/
└── auth/
    ├── auth.go          # public-facing functions and types
    ├── internal/
    │   ├── token/
    │   │   └── token.go   # JWT generation/parsing
    │   ├── session/
    │   │   └── session.go # session store operations
    │   └── lookup/
    │       └── lookup.go  # user lookup and caching
    └── auth_test.go
```

Yes, `internal` directories can be nested. `auth/internal/token` is only accessible from within `auth/` — not even other packages in your top-level `internal/` can reach it. This gives you package-private visibility: implementation details of `auth` that even your own other packages shouldn't depend on.

The `auth.go` file becomes thin — just the public interface that delegates to the internal packages:

```go
package auth

import (
    "myapp/internal/auth/internal/lookup"
    "myapp/internal/auth/internal/session"
    "myapp/internal/auth/internal/token"
)

type Service struct {
    tokens   *token.Generator
    sessions *session.Store
    users    *lookup.Cache
}

func NewService(db *sql.DB, secret string) *Service {
    return &Service{
        tokens:   token.NewGenerator(secret),
        sessions: session.NewStore(db),
        users:    lookup.NewCache(db),
    }
}

func (s *Service) Login(username, password string) (string, error) {
    user, err := s.users.Authenticate(username, password)
    if err != nil {
        return "", fmt.Errorf("auth.Login: %w", err)
    }
    tok, err := s.tokens.Generate(user.ID)
    if err != nil {
        return "", fmt.Errorf("auth.Login: generating token: %w", err)
    }
    if err := s.sessions.Create(user.ID, tok); err != nil {
        return "", fmt.Errorf("auth.Login: creating session: %w", err)
    }
    return tok, nil
}
```

The public API of `auth` didn't change — `Service`, `NewService`, and `Login` are still there. But the implementation is now cleanly split into focused packages, none of which are accessible to callers of `auth`.

## When to Use internal

Not everything needs to be in `internal`. Here's the decision:

- Code shared only within your module, not intended as public API → `internal`
- Code you want to make available to external importers → exported package outside `internal`
- Code used only within a single package → unexported identifiers in that package

If you're writing an application (not a library), put almost everything in `internal/`. Your `cmd/` packages are entry points; your `internal/` packages are the program. Anything you don't explicitly intend to expose should be in `internal/` by default.

If you're writing a library, `internal/` is where your implementation lives. Your `pkg/` or top-level packages are your public API. The internal/external boundary is the same as the "things I'll maintain as stable API" boundary.

The `internal` package is enforced by the compiler, documented by the directory structure, and costs nothing. Use it. Your future self — and any external users of your code — will thank you for having clear boundaries from day one.
