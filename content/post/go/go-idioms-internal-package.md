---
title: 'Lesson 20: internal Package Is Underrated — Compiler-enforced privacy for free'
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
series: "Idiomatic Go"
lesson: 20
---

Go has two visibility levels: exported (starts with a capital letter) and unexported (doesn't). Most engineers use only these two. But there's a third option that the language gives you for free, and it's more useful than most people realize. The `internal` directory enforces that certain packages can only be imported by code within your own module — and the compiler, not documentation or convention, does the enforcing.

Exported symbols are API commitments. Once something is exported and external code is depending on it, changing it is a breaking change. The `internal` package is the escape hatch: share code across multiple packages in your own codebase without accidentally publishing an API surface you'll have to maintain forever.

## The Problem

Without `internal`, you have two options when you need to share code across packages in your module:

1. Export it. Now it's public API. External packages can import and depend on it. Changing field names, function signatures, or the package path becomes a breaking change.
2. Duplicate it. Maintenance hell.

Here's the first option playing out:

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

Once this is out there, external packages import it. If you change the field names or restructure the package, you break their code. You've accidentally published an API by doing nothing other than using the only tool you thought you had.

The same problem appears in SDKs and libraries:

```go
// WRONG — this is now public API even though it's an implementation detail
// github.com/yourorg/sdk/httputil/request.go
package httputil

// RetryState tracks retry attempts — not intended for external use
type RetryState struct {
    Attempts  int
    LastError error
    Backoff   time.Duration
}
```

External packages can now import and use `RetryState`. In v2, you need to either version your module or accept a breaking change.

## The Idiomatic Way

Move implementation-only types and functions into an `internal` directory. The rule is simple: a package path containing `internal` can only be imported by code rooted at the parent of that `internal` directory.

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
    // JWT parsing logic
}
```

`Payload` and `Parse` are freely usable anywhere in `github.com/yourorg/yourpkg`, but invisible to external importers. Refactor, rename, restructure — no downstream breakage possible.

If another module tries to import it:

```
imports mymodule/internal/auth: use of internal package not allowed
```

Compile-time. No flag to override it. No workaround other than restructuring.

A mature project layout using `internal`:

```
myapp/
├── cmd/
│   ├── server/
│   │   └── main.go
│   └── worker/
│       └── main.go
├── internal/
│   ├── auth/
│   │   ├── auth.go
│   │   └── auth_test.go
│   ├── database/
│   │   └── db.go
│   ├── middleware/
│   │   └── middleware.go
│   └── config/
│       └── config.go
├── pkg/
│   └── apiclient/   # deliberately exposed to external importers
│       └── client.go
└── go.mod
```

The `cmd/` packages are entry points. `internal/` is everything that's implementation detail. `pkg/` is what you deliberately expose. Everything in `internal/` is freely shareable within `myapp` but completely opaque to external code.

## In The Wild

The `internal` directory can appear anywhere in a package path, not just at the top level. This gives you nested privacy — package-private visibility within a specific subtree.

Suppose your `auth` package has grown large and you want to split it without changing the public API:

```
internal/
└── auth/
    ├── auth.go           # public-facing functions and types
    ├── internal/
    │   ├── token/
    │   │   └── token.go  # JWT generation/parsing
    │   ├── session/
    │   │   └── session.go
    │   └── lookup/
    │       └── lookup.go
    └── auth_test.go
```

`auth/internal/token` is only accessible from within `auth/`. Even other packages in your top-level `internal/` can't reach it. This is implementation detail of `auth` that even your own other packages shouldn't depend on.

The public `auth.go` becomes thin — just the interface that delegates to the internal packages:

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

The public API of `auth` didn't change. The implementation is now split into focused, private sub-packages. No external breakage, clean internals.

## The Gotchas

**Putting too little in `internal`.** The default should be `internal`. If you're writing an application (not a library), almost everything except the `cmd/` entry points belongs in `internal/`. Don't wait until you have a problem to start using it — use it from day one and export deliberately rather than accidentally.

**Forgetting that `internal` can be nested.** A `cmd/server/internal/config` package is only accessible from `cmd/server/`. If you mean to share that config across the whole module, it needs to be at `internal/config` at the module root. The placement determines the scope of access, so be intentional about where you put the `internal` directory.

**Confusing `internal` with unexported identifiers.** Unexported identifiers are invisible outside their package. `internal` packages are invisible outside their parent subtree but can export identifiers freely. They solve different problems: unexported is for package-level privacy, `internal` is for module-level privacy of entire packages.

## Key Takeaway

The `internal` package is enforced by the compiler, documented by the directory structure, and costs nothing to use. It gives you a vocabulary of three visibility levels — package-private (unexported), module-private (`internal`), and public (exported outside `internal`) — instead of just two. I've seen codebases where every helper type ended up accidentally exported because engineers didn't know they had a better option. Start every project with `internal/` as the default home for implementation code. Export only what you deliberately intend to be part of your public interface. The boundary between "things I'll maintain as stable API" and "implementation detail" should be visible in the directory structure, not buried in comments.

---

← [Previous: Table-Driven Tests](/post/go/go-idioms-table-driven-tests) | [Course Index](/post/go/) | [Next: Composition Over Inheritance](/post/go/go-idioms-composition) →
