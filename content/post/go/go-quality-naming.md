---
title: 'Lesson 3: Idiomatic Naming — Names are your documentation'
author: Atharva Pandey
series: "Go Code Quality & Maintainability"
lesson: 3
keywords:
  - Go
  - golang
  - naming conventions
  - code quality
  - readability
  - idiomatic Go
tags:
  - Go tutorial
  - golang
  - code quality
date: '2024-09-04T00:00:00.000Z'
---

When I review Go code, naming problems tell me more about the author's understanding of the codebase than almost anything else. A function called `HandleRequest` that parses JSON, queries a database, formats a response, and writes to a log tells me its author didn't know what it does either. A variable called `data` in a function that handles three different kinds of data tells me the author stopped thinking halfway through. Names are the first layer of documentation — they're read far more often than comments, and unlike comments, they can't drift out of sync with the code.

## The Problem

Bad Go naming clusters into a few predictable patterns.

```go
// WRONG — context-free names that force the reader to trace execution
func Process(d []byte, t string, f bool) ([]byte, error) {
    var res []byte
    // ... 80 lines of logic
    return res, nil
}

// WRONG — redundant package prefix
package user

type UserService struct{} // user.UserService — "user" appears twice

func (s *UserService) GetUserByID(id int64) (*User, error) {} // GetUserByID in package user

// WRONG — Hungarian notation / type encoding in names
var strName string
var intAge int
var bIsActive bool
var arrItems []Item
```

The first example forces you to figure out what `d`, `t`, and `f` represent from context. The second is a packaging antipattern — `user.UserService` reads oddly because the package name already provides the `user` context. The third is Hungarian notation, which Go explicitly discourages because the type system already carries that information.

## The Idiomatic Way

Go's official naming conventions are spelled out in Effective Go and the Go Code Review Comments document. The core rules:

**Packages are lowercase, single-word, no underscores.** `httputil`, not `http_util` or `HttpUtil`.

**Local variables use short names when scope is small.** A loop index is `i`, not `loopIndex`. A context is `ctx`. An error is `err`. These are Go-wide conventions and violating them makes your code read as non-Go.

```go
// RIGHT — short names for tight scopes, descriptive names for wide scopes
func (s *OrderService) Submit(ctx context.Context, order Order) (OrderID, error) {
    if err := s.validate(ctx, order); err != nil {
        return 0, fmt.Errorf("validate order: %w", err)
    }

    id, err := s.store.Insert(ctx, order)
    if err != nil {
        return 0, fmt.Errorf("insert order: %w", err)
    }

    return id, nil
}
```

**Exported names don't repeat the package name.** In package `order`, the struct is `Service` not `OrderService`. Callers write `order.Service`, which reads naturally.

```go
// RIGHT — package context does the work
package order

type Service struct {
    store Store
    mailer Mailer
}

func (s *Service) Submit(ctx context.Context, o Order) (ID, error) {}
func (s *Service) Cancel(ctx context.Context, id ID) error {}
```

**Boolean functions and variables ask a yes/no question.** `IsValid`, `HasPermission`, `enabled` — not `CheckValid`, `PermissionExists`, `flag`.

```go
// RIGHT — boolean naming reads like a question
func (u *User) IsActive() bool {
    return u.Status == StatusActive && !u.DeletedAt.Valid
}

if u.IsActive() {
    // reads naturally
}
```

**Acronyms are all-caps.** `userID` not `userId`, `parseURL` not `parseUrl`, `HTTPClient` not `HttpClient`. This is a Go convention that surprises people coming from other languages.

## In The Wild

I inherited a service where every handler function was named after the HTTP method and path: `PostV1UsersHandler`, `GetV1UsersIDHandler`, `PatchV1UsersIDHandler`. There were 34 of these. Finding the handler for a business operation required knowing the HTTP route, not the domain concept.

We renamed them to reflect what they do: `CreateUserHandler`, `GetUserHandler`, `UpdateUserHandler`. Then we went further and put them on the handler struct so they just became methods: `(h *Handler) CreateUser`, `(h *Handler) GetUser`. The route-to-handler mapping became explicit in the router setup instead of encoded in function names.

What surprised me: a naming change that touched no logic, no tests, no behavior — just renaming — reduced the average time new engineers spent navigating the codebase by about a third. We measured this by asking them to find "where does password reset happen" before and after.

## The Gotchas

**Short names in wide scopes are actively harmful.** `i` is fine for a loop counter. `i` as a function parameter in a function that does authorization, parsing, and persistence is not — the reader loses context. The rule is: the longer the variable's scope, the more descriptive its name needs to be.

**Verb-first names for functions, noun-first for types.** Functions do things: `ParseRequest`, `ValidateToken`, `SendEmail`. Types are things: `RequestParser`, `TokenValidator`, `EmailSender`. When a function name starts with a noun, it's usually either misnamed or doing too much.

**`Get` prefix is usually redundant.** `user.GetName()` can just be `user.Name()`. The `Get` prefix is inherited from Java getters and adds no information. The exception is functions that actually perform I/O or computation: `GetUserFromDB` correctly signals that it does a round trip.

**Don't use `Err` as a general error variable when you have multiple errors in scope.** If you shadow `err` across multiple variable declarations in a function without `:=`, you'll have stale error references. Use distinct names when multiple errors coexist: `parseErr`, `insertErr`.

## Key Takeaway

Go naming conventions exist to make code read like prose, not like a type system exercise. Package names provide context — don't repeat them in exported identifiers. Short names are correct for short-lived variables; descriptive names carry their weight as scope widens. Acronyms go all-caps. Booleans ask questions. Function names are verbs. Type names are nouns. Get these right and your code documents itself — get them wrong and every reader pays the cognitive tax every time they read the file.

---

[← Lesson 2: Spotting Over-Abstraction](/post/go/go-quality-over-abstraction) | [Course Index](/series/go-code-quality-maintainability) | [Next → Lesson 4: Code Review Heuristics](/post/go/go-quality-code-review)
