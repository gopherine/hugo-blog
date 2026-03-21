---
title: 'Lesson 7: Kill the Utils Package — util.go is where code goes to hide'
author: Atharva Pandey
series: "Go Code Quality & Maintainability"
lesson: 7
keywords:
  - Go
  - golang
  - utils
  - helpers
  - code quality
  - package design
tags:
  - Go tutorial
  - golang
  - code quality
date: '2025-04-01T00:00:00.000Z'
---

Every Go codebase I've worked on for more than a year has a `util` package. Some have a `helpers` package. Some have both. I've seen `common`, `shared`, `misc`, and once, memorably, `stuff`. These packages are where code goes when a developer doesn't know where it belongs — which means they're the first place reviewers stop reading carefully, the last place new engineers look when they can't find something, and the primary location of dead code in any codebase over 18 months old.

## The Problem

The `util` package accumulates because naming is hard and pressure is real. It's always faster to add a function to an existing package than to think carefully about where it belongs. But fast now means slow forever.

```go
// util/util.go — the graveyard
package util

import (...)

// added 2 years ago — only one caller
func FormatCurrency(amount float64, currency string) string { ... }

// added 18 months ago — 3 callers, all in the payment package
func RoundToTwoDecimalPlaces(f float64) float64 { ... }

// added 14 months ago — 0 callers (dead code)
func ParseDateRange(s string) (time.Time, time.Time, error) { ... }

// added 10 months ago — only used in tests
func MustParseUUID(s string) uuid.UUID { ... }

// added 8 months ago — only the order service calls this
func CalculateDiscountedPrice(original, discount float64) float64 { ... }

// added 3 months ago — used everywhere
func PtrTo[T any](v T) *T { return &v }
```

Six functions, four different homes they should have. One is dead. The package name communicates nothing about what any of them do. Nobody knows what's in here without reading it.

## The Idiomatic Way

The migration process is straightforward: audit every function in the util package, find its callers, and move it to where it belongs.

**Step 1: Find every function and its callers.**

```bash
# List exported functions in util package
grep -n "^func " util/util.go

# Find callers of a specific function
grep -r "util\.FormatCurrency" --include="*.go" .
```

**Step 2: Classify each function.** Does it belong to a specific domain? Move it there. Is it genuinely general-purpose with multiple callers across domains? It might warrant its own focused package. Is it only used in tests? Move it to `testutil` or the test file itself. Is it unused? Delete it.

```go
// BEFORE — in util/util.go
func CalculateDiscountedPrice(original, discount float64) float64 {
    if discount < 0 || discount > 1 {
        return original
    }
    return original * (1 - discount)
}

// AFTER — in order/pricing.go, where it actually belongs
package order

func discountedPrice(original, discount float64) float64 {
    if discount < 0 || discount > 1 {
        return original
    }
    return original * (1 - discount)
}
```

It's now unexported because its only callers are in the `order` package. The function moved, its scope narrowed, and the `order` package got richer.

**Step 3: For genuinely shared helpers, name the package after what it does, not that it's a helper.**

```go
// WRONG
package util

func PtrTo[T any](v T) *T { return &v }

// RIGHT — package name describes the domain
package ptr

func To[T any](v T) *T { return &v }
// callers: ptr.To(someValue) — readable, self-documenting
```

`ptr.To` is immediately understandable. `util.PtrTo` is a noise prefix attached to a function in a noise package.

## In The Wild

A microservices platform I worked on had a shared `util` module that was imported by 14 services. It had 47 exported functions spanning string manipulation, JSON helpers, database retry logic, HTTP client wrappers, and some business-logic helpers that had been put there "because other services might need them someday."

The shared `util` module was a deployment hazard: changing anything in it required coordinating updates across 14 services. Engineers were afraid to touch it. It hadn't been refactored in 18 months because the blast radius was too large.

We migrated it over three months. Each function went to one of: the standard library (several had been reimplementing `strings.Builder` logic), a focused shared package (`httpretry`, `pgutil`), or the specific service that was the only caller. The shared `util` module went from 47 functions to 0 and was deleted. The 14 services each got slightly larger but completely self-contained dependency sets.

## The Gotchas

**Moving code breaks callers.** Use `sed` or your IDE's refactor tooling to update imports in one shot. Run `go build ./...` after each move. If the codebase is large, do the migration in small batches — one function per PR — so reviewers can verify correctness without drowning in diff.

**Don't create a new `util`-shaped package under a different name.** `common`, `shared`, `base`, `core` — these are all the same antipattern with different labels. The name isn't the problem; the lack of cohesion is. If you find yourself creating a new package and struggling to describe what it's for, that's the warning sign.

**Exported functions become part of your API.** When you move a function from `util` to a domain package and make it unexported, you're reducing the API surface of the domain package. That's almost always the right call. Export only what external packages genuinely need.

**Test helpers are a legitimate exception.** A `testutil` package with helpers for setting up test databases, generating fixture data, or building test HTTP requests is cohesive — everything in it serves the same purpose (making tests easier to write). The difference is that `testutil` has a clear mandate; `util` does not.

## Key Takeaway

The `util` package is a symptom of deferred decisions about ownership. Kill it by moving each function to the package that owns its concept, naming focused shared packages after what they do rather than that they're utilities, and deleting dead code ruthlessly. If a function only has one caller, it probably belongs in that caller's package. If a function has many callers but no coherent name for a package, that's a clue the function is doing something generic enough to warrant careful naming — not a license to dump it in `util`. Make the placement decision once and make it clearly.

---

[← Lesson 6: Package Cohesion](/post/go/go-quality-package-cohesion) | [Course Index](/series/go-code-quality-maintainability) | [Next → Lesson 8: Linting with golangci-lint](/post/go/go-quality-linting)
