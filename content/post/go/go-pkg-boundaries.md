---
title: "Lesson 1: Package Boundaries — One package, one responsibility, zero excuses"
author: Atharva Pandey
series: "Go Package & Module Architecture"
lesson: 1
keywords: [Go, golang, packages, modules, architecture]
tags: [Go tutorial, golang, architecture]
date: '2024-06-03T00:00:00.000Z'
---

I have refactored a lot of Go codebases — my own included. And the single most common structural mistake I see is the `utils` package. Or `helpers`. Or `common`. Or sometimes the worst offender of all: a package named after the entire application domain with fifty unrelated files sitting inside it. When I first started writing Go seriously, I made all of these mistakes. This lesson is about why package boundaries matter and how to get them right.

## The Problem

When you start a new service, everything feels manageable. You have a `main.go`, maybe a `handlers` package, and then a catch-all bucket for everything else. The `utils` package starts with one function. Then two. Then someone adds a database helper. Then a string formatting function. Then JWT parsing. Before long, you have a package that does everything and means nothing.

The trouble with this approach is that it makes reasoning about your code expensive. You cannot look at a package name and understand what it does. You cannot change one piece without worrying about what else might break. Import cycles start to emerge because everything depends on everything. Worse still, testing becomes a nightmare — to unit-test one function you end up pulling in half the application.

Go's package system is designed to enforce cohesion. A package is the unit of compilation, the unit of encapsulation, and the unit of dependency. When you get the boundaries wrong, every downstream benefit of Go's module system gets eroded.

Here is what the bad version looks like in practice.

## The Idiomatic Way

**Wrong: the kitchen-sink utils package**

```go
// package utils — this file has no business being a single package
package utils

import (
    "database/sql"
    "fmt"
    "net/http"
    "strings"
    "time"

    "github.com/golang-jwt/jwt/v5"
)

func FormatName(first, last string) string {
    return strings.TrimSpace(fmt.Sprintf("%s %s", first, last))
}

func ParseJWT(tokenString, secret string) (*jwt.MapClaims, error) {
    // ... jwt parsing logic
    return nil, nil
}

func RespondJSON(w http.ResponseWriter, status int, body any) {
    w.Header().Set("Content-Type", "application/json")
    w.WriteHeader(status)
    // ... encode body
}

func GetUserByID(db *sql.DB, id int64) (string, error) {
    // ... database logic
    return "", nil
}

func FormatTimestamp(t time.Time) string {
    return t.Format(time.RFC3339)
}
```

This package imports `database/sql`, `net/http`, and `github.com/golang-jwt/jwt/v5` simultaneously. Every package that imports `utils` for `FormatName` now also transitively depends on JWT and HTTP libraries. Compilation is slower, the dependency graph is muddied, and the package reveals nothing about intent.

**Right: purpose-driven packages with narrow scope**

```go
// package nameutil — only formatting concerns
package nameutil

import (
    "fmt"
    "strings"
)

func Format(first, last string) string {
    return strings.TrimSpace(fmt.Sprintf("%s %s", first, last))
}
```

```go
// package httputil — only HTTP response concerns
package httputil

import (
    "encoding/json"
    "net/http"
)

func RespondJSON(w http.ResponseWriter, status int, body any) {
    w.Header().Set("Content-Type", "application/json")
    w.WriteHeader(status)
    _ = json.NewEncoder(w).Encode(body)
}
```

```go
// package auth — JWT logic grouped with its domain
package auth

import (
    "errors"

    "github.com/golang-jwt/jwt/v5"
)

type Claims struct {
    jwt.RegisteredClaims
    UserID int64 `json:"user_id"`
}

func ParseToken(tokenString, secret string) (*Claims, error) {
    claims := &Claims{}
    token, err := jwt.ParseWithClaims(tokenString, claims, func(t *jwt.Token) (any, error) {
        if _, ok := t.Method.(*jwt.SigningMethodHMAC); !ok {
            return nil, errors.New("unexpected signing method")
        }
        return []byte(secret), nil
    })
    if err != nil || !token.Valid {
        return nil, errors.New("invalid token")
    }
    return claims, nil
}
```

Each package now has a clear name that communicates exactly what it does. The imports inside each package are minimal and relevant. A change to JWT parsing does not risk touching HTTP response formatting.

**Right: cohesion test — ask yourself "what is this package about?"**

```go
// A well-bounded package passes the "elevator pitch" test.
// You should be able to describe a package in one sentence:
//
// package order  — manages order lifecycle: creation, fulfillment, cancellation
// package billing — handles payment processing and invoice generation
// package notify  — dispatches email and SMS notifications
//
// If your answer requires "and" more than once, the package probably
// needs to be split.

// Contrast with packages that fail the test:
//
// package utils   — does... stuff?
// package common  — common to what?
// package shared  — shared with whom?
//
// These names describe nothing. They are magnets for unrelated code.
```

The naming discipline is not pedantic — it is the first layer of documentation your codebase has. A new engineer reading the import list should understand the dependency topology without reading a single line of implementation.

## In The Wild

In the standard library itself, look at `net/http`. It does not live in a package called `utils/networking`. It is precisely scoped: HTTP. Within it, the designers made further distinctions — `net/http/httptest` for testing utilities, `net/http/httputil` for proxies and response dumpers. Each sub-package is navigable on its own.

The Kubernetes codebase, before it became the behemoth it is today, established conventions around package naming that large Go projects still follow. Packages like `pkg/api`, `pkg/scheduler`, and `pkg/kubelet` each own a clear domain slice. The infamous `vendor` and early `pkg/util` directories eventually became the cautionary tale that helped shape better conventions.

In my own services I tend to follow a layout where packages correspond to business capabilities: `order`, `customer`, `inventory`, `billing`. When something is purely infrastructure — database clients, HTTP clients, observability setup — I put it under `infra/` as a sub-package of the infrastructure concern, not as a global dumping ground.

## The Gotchas

**Gotcha 1: splitting too early.** Packages should emerge from real cohesion, not from imagined future needs. Do not create a `formatter` package on day one with one function. Start with the code in the same package as its consumer. Extract it when you actually have a second distinct consumer.

**Gotcha 2: naming packages after layers.** Packages named `repository`, `service`, or `handler` describe a layer, not a capability. They invite everything of that "type" to pile in regardless of domain. Prefer `orderrepo`, `orderservice`, or just let the directory structure carry the layer concept while the package focuses on the domain.

**Gotcha 3: confusing package name with directory name.** In Go the `package` declaration in a file and the directory name are independent (though they should match by convention). A directory `internal/auth` should have `package auth` at the top of its files, not `package internal_auth` or `package main`. Go tooling and human readers both benefit from consistency here.

**Gotcha 4: large packages hiding behind a single file.** I have seen 4,000-line single-file packages. Go has no rule against multiple files in a package. Split a large package across logical files (`order_create.go`, `order_fulfill.go`, `order_cancel.go`) while keeping the package name the same. Files are free; unreadable monoliths are not.

## Key Takeaway

A package is a contract, not a container. Its name should tell you what it does, not where it lives in the codebase. One responsibility per package means you can reason about it, test it, and depend on it in isolation. The moment you reach for `utils`, stop and ask: what is the actual concept I am modeling here? Name that, and build from there.

Good package boundaries are the foundation everything else in this series builds on. Cyclic dependency problems, interface placement decisions, and workspace ergonomics all become easier when your packages are well-bounded to begin with.

---

**Series: Go Package & Module Architecture**

- Lesson 1: Package Boundaries — *you are here*
- [Lesson 2: Avoiding Cyclic Dependencies](/post/go/go-pkg-cyclic-deps/)
- [Lesson 3: internal/ Usage Patterns](/post/go/go-pkg-internal/)
- [Lesson 4: Interface Placement](/post/go/go-pkg-interface-placement/)
- [Lesson 5: Monolith vs Multi-Module](/post/go/go-pkg-mono-vs-multi/)
- [Lesson 6: Dependency Direction](/post/go/go-pkg-dependency-direction/)
- [Lesson 7: go.work and Workspace Mode](/post/go/go-pkg-workspaces/)
