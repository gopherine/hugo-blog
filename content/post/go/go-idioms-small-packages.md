---
title: 'Lesson 22: Small Packages Win — One package, one job'
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
series: "Idiomatic Go"
lesson: 22
date: '2026-01-26T00:00:00.000Z'
---

There is a particular kind of Go codebase that's immediately recognizable as written by someone still thinking in another language. It has a `utils` package. Maybe a `common` package. Possibly a `helpers` folder with a file called `misc.go`. Every function that doesn't obviously belong somewhere ends up there, and over time these packages become the junk drawers of the codebase — bloated, unfocused, and imported by everything.

Go has a better way. Small packages with narrow responsibilities. One package, one idea.

## The Problem

The reason `utils` packages form is understandable: you need a helper function and you're not sure where it belongs, so you put it somewhere neutral. That's reasonable the first time. The problem is it never stops there.

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

The same problem shows up in naming. If your package is called `userManager`, callers write `userManager.UserManager{}`. The package name and the type name repeat each other — Go calls this stuttering, and it's a sign the names haven't been thought through:

```go
// WRONG — verbose, redundant, stuttering names
package userManager    // two words, camelCase

import "myapp/userManager"
u := userManager.UserManager{} // painful to read
```

## The Idiomatic Way

Break your junk drawer into packages where each one has a name you can explain in a single sentence. Move functions to where they semantically belong:

```
// RIGHT — focused packages with clear purpose
myapp/
  currency/
    format.go    // formatAmount, parseCurrency
  timeutil/
    parse.go     // parseDate, formatDate
  validate/
    email.go     // validateEmail
  retry/
    retry.go     // retry logic with backoff
  handlers/
    api.go
```

When someone needs date formatting, they look in `timeutil`. The name tells them where to look without consulting anyone.

On naming: short, lowercase, single words. And apply the no-stutter rule — if your package is named `user`, don't name your types `UserProfile` or `UserService`. Just `Profile` and `Service`. The package name is the namespace.

```go
// RIGHT — short, clear, no stutter
package user

import "myapp/user"
u := user.Profile{}   // reads naturally
```

The standard library does this consistently. `http.Handler`, not `http.HTTPHandler`. `json.Encoder`, not `json.JSONEncoder`. `fmt.Println`, not `fmt.FmtPrintln`.

For circular imports, the compiler will refuse to compile them — and that's a feature. A circular dependency is almost always a design problem:

```go
// WRONG — circular dependency that won't compile
// package user
import "myapp/order"
type User struct { Orders []order.Order }

// package order
import "myapp/user"
type Order struct { Buyer user.User }
```

The fix is usually one of three things: extract the shared concept into a third package, use an interface, or merge the packages. In this case, using shared IDs instead of embedding full types is often the cleanest path:

```go
// RIGHT — shared types in a separate package
// package domain
type UserID string
type OrderID string

// package user
import "myapp/domain"
type User struct {
    ID   domain.UserID
    Name string
}

// package order
import "myapp/domain"
type Order struct {
    ID      domain.OrderID
    BuyerID domain.UserID  // reference by ID, not by embedding the full User
}
```

Now `user` and `order` both depend on `domain`, but not on each other. The dependency graph is a tree, not a cycle.

## In The Wild

The Go standard library is the best package design reference in any language's standard library. Spend an afternoon reading its structure:

- `net/http` — HTTP client and server. One idea, well-executed.
- `encoding/json` — JSON encoding and decoding. Nothing else.
- `database/sql` — A database interface. Not a specific driver, just the interface.
- `sync` — Synchronization primitives. Mutex, WaitGroup, Once.
- `io` — Basic I/O interfaces. Reader, Writer, Closer.
- `io/fs` — Filesystem abstractions. Separated from `io` because it's a distinct concept.

When the standard library needed to split a concept, it put the sub-concept in a sub-package. `io/fs` is not in `io` itself because it's a separate enough idea to deserve its own namespace. But it lives under `io` because it's related.

Also note what the standard library *didn't* do: it didn't split `net/http` into `net/http/request`, `net/http/response`, and `net/http/handler` just for organizational purity. Those three things are always used together. Splitting them would force every caller to import three packages. A good test: if every package that imports A also always imports B, and they're never used independently, they should probably be one package.

## The Gotchas

**Splitting too eagerly creates import ceremony.** A package with two functions used only in one place doesn't need to exist. Over-splitting adds import statements, adds namespace decisions for callers, and can make code harder to follow when the context jumps between packages constantly.

**`internal` doesn't replace good naming.** Putting everything in `internal/utils` is the same junk drawer problem, just hidden behind the `internal` enforcement. The structure of your packages should reflect the structure of your problem domain, not serve as a filing cabinet.

**Package-level `init()` is a global dependency.** When packages register themselves in `init()` — a common pattern in database drivers and plugin systems — package splits can create subtle ordering dependencies. Be careful when splitting packages that use `init()`.

## Key Takeaway

The mental model that cuts through all the edge cases is simple: a package should have one idea at its core. Not one file. Not one function. One *idea*. When you can state it in a short sentence, you have a good package. When you find yourself saying "it contains various utilities for..." — stop, split, and name each piece properly. Good package design makes a codebase navigable: new developers know where to look, dependencies are explicit and acyclic, and names are honest enough that documentation practically writes itself.

---

← [Lesson 21: Composition Over Inheritance](/post/go/go-idioms-composition) | [Course Index](/post/go/) | [Lesson 23: Error Values, Not Exceptions](/post/go/go-idioms-error-values) →
