---
title: 'Go Idioms: Small Packages Win'
author: Atharva Pandey
keywords:
  - Go
  - golang
  - package design
  - package naming
  - circular imports
  - standard library
  - software architecture
tags:
  - Go tutorial
  - golang
date: '2026-01-26T00:00:00.000Z'
---
s a particular kind of Go codebase that's immediately recognizable as written by someone still thinking in another language. It has a `utils` package. Maybe a `common` package. Possibly a `helpers` folder with a file called `misc.go`. Every new function that doesn't obviously belong somewhere ends up there, and over time these packages become the junk drawers of the codebase — bloated, unfocused, and imported by everything.

Go has a better way. Small packages with narrow responsibilities. One package, one idea.

## The Problem with Catch-All Packages

The reason `utils` packages form is understandable: you're writing something and you need a helper function. You're not sure where it belongs, so you put it somewhere neutral. That's reasonable the first time. The problem is that it never stops there.

```
// WRONG — the utils anti-pattern
myapp/
  utils/
    utils.go         // parseDate, formatCurrency, validateEmail, retryHTTP, base64Encode...
    http_utils.go    // more helpers
    string_utils.go  // yet more
  models/
    user.go
  handlers/
    api.go
```

Now `utils` is imported by `models`, `handlers`, and everything else. It imports nothing specific itself, but it contains 40 functions that have nothing to do with each other. Adding one function to `utils` could subtly affect behavior elsewhere. Testing it requires loading everything. And when a new developer asks "where does the date formatting happen?", the answer is "utils, probably" — which isn't an answer at all.

```
// RIGHT — focused packages with clear purpose
myapp/
  currency/
    format.go    // formatAmount, parseCurrency
  timeutil/
    parse.go     // parseDate, formatDate — named better than "utils"
  validate/
    email.go     // validateEmail
  retry/
    retry.go     // retry logic with backoff
  handlers/
    api.go
```

Each package has a reason to exist that you can explain in one sentence. When someone needs date formatting, they look in `timeutil`. The name tells you where to look.

## Package Naming: Short, Lowercase, No Stuttering

Go package names are short, lowercase, single words when possible. The name should describe what the package *provides*, not what it *is*.

```go
// WRONG — verbose, redundant, stuttering names
package userManager    // two words, camelCase
package httpHelpers    // unclear purpose
package stringUtils    // the dreaded Utils

import "myapp/userManager"
u := userManager.UserManager{} // "userManager.UserManager" is painful to read
```

```go
// RIGHT — short, clear, no stutter
package user

import "myapp/user"
u := user.Profile{}   // "user.Profile" reads naturally
```

The "no stutter" rule is worth memorizing. If your package is named `user`, don't name your types `UserProfile` or `UserService`. Just `Profile` and `Service`. The package name provides the namespace — you don't need to repeat it inside the package.

The standard library demonstrates this consistently. The `http` package has `http.Handler`, not `http.HTTPHandler`. The `json` package has `json.Encoder`, not `json.JSONEncoder`. The `fmt` package has `fmt.Println`, not `fmt.FmtPrintln`.

## The Standard Library as a Model

Spend an afternoon reading the Go standard library's package structure. It's one of the best-designed package hierarchies in any language's standard library, and it's worth studying.

`net/http` — HTTP client and server. One idea, well-executed.
`encoding/json` — JSON encoding and decoding. Nothing else.
`database/sql` — A database interface. Not a specific database driver, just the interface.
`sync` — Synchronization primitives. Mutex, WaitGroup, Once.
`io` — Basic I/O interfaces. Reader, Writer, Closer.
`io/fs` — Filesystem abstractions. Separated from `io` because it's a distinct concept.

Notice that when the standard library needs to split a concept, it puts the sub-concept in a sub-package. `io/fs` is not in `io` itself because it's a separate enough idea to deserve its own namespace. But it lives under `io` because it's related.

## Avoiding Circular Imports

Go refuses to compile circular imports. If package A imports package B, and package B imports package A, the compiler stops you with an error. This is not a limitation — it's a feature that forces you to think about your dependency graph.

```go
// WRONG — circular dependency
// package user
import "myapp/order"
type User struct { Orders []order.Order }

// package order
import "myapp/user"
type Order struct { Buyer user.User }
// This won't compile.
```

The circular import here reveals a design problem: both packages know about each other, which means neither is truly focused. The fix is usually one of three things: extract the shared concept into a third package, use an interface instead of a concrete type, or merge the packages.

```go
// RIGHT — shared types in a separate package, or use interfaces
// package domain (shared types)
type UserID string
type OrderID string

// package user
import "myapp/domain"
type User struct {
    ID     domain.UserID
    Name   string
}

// package order
import "myapp/domain"
type Order struct {
    ID     domain.OrderID
    BuyerID domain.UserID  // reference by ID, not by embedding the full User
}
```

Now `user` and `order` both depend on `domain`, but not on each other. The dependency graph is a tree, not a cycle.

## When to Split a Package

Split a package when:

- It has grown large enough that the package documentation becomes a table of contents rather than a summary
- It contains distinct concepts that are independently useful (someone might want one without the other)
- Different parts have different testing needs or stability guarantees
- Two distinct audiences use different parts of it

```go
// A crypto package that handles both symmetric and asymmetric encryption
// might be better split:
crypto/aes/   — symmetric, used for data at rest
crypto/rsa/   — asymmetric, used for key exchange
```

The standard library did exactly this. Not everything lives in one `crypto` package.

## When NOT to Split a Package

Splitting packages has a cost. Each split adds an import. It adds a namespace. It adds a decision for every caller: which package do I need? If the concepts are tightly coupled and always used together, splitting them makes the API worse.

```go
// DON'T split these into separate packages just for organizational purity:
// http/request/   — Request type
// http/response/  — Response type
// http/handler/   — Handler interface
// They're all used together. Splitting makes every caller import three things.
```

A good test: if every package that imports Package A also always imports Package B, and they're never used independently, they should probably be one package.

## One Package, One Idea

The mental model that cuts through all the edge cases is simple: a package should have one idea at its core. Not one file. Not one function. One *idea*.

`http` is about the HTTP protocol. `json` is about JSON. `retry` is about retrying operations with backoff. `validate` is about validating input.

When you can state the idea in a short sentence, you have a good package. When you find yourself saying "it contains various utilities for..." — that's the warning sign. Stop, split, and name each piece properly.

Good package design isn't about following rules for their own sake. It's about making the codebase navigable. When every package has a clear purpose, new developers know where to look. When dependencies are explicit and acyclic, refactoring is safe. When names are honest, documentation writes itself.

Small packages win not because of some abstract architectural virtue, but because they make the day-to-day work of maintaining a Go codebase significantly more pleasant.
