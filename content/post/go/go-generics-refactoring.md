---
title: "Lesson 7: Refactoring Concrete to Generic — Start concrete, extract when proven"
author: Atharva Pandey
series: "Go Generics Masterclass"
lesson: 7
keywords:
  - Go
  - golang
  - generics
  - refactoring
  - concrete to generic
  - extract generic function
  - refactoring walkthrough
  - code deduplication
  - when to refactor
tags:
  - Go tutorial
  - golang
  - generics
date: '2024-10-25T00:00:00.000Z'
---

The advice "start concrete, extract when proven" is easy to say and surprisingly hard to follow when you're in the middle of writing the third version of the same function. The temptation to generalize early is real. I've felt it. Most engineers who care about clean code have felt it.

But the cost of premature abstraction is higher than the cost of temporary duplication. A duplicated function can be removed with a search-and-replace. A bad abstraction gets built around, extended in wrong directions, and defended by sunk-cost thinking. This lesson is about how to do the refactoring correctly — waiting until the pattern is proven, then extracting it cleanly.

## The Problem

The typical starting point: you've written a utility function for one type. Then you need the same logic for a second type. Then a third. The duplication is obvious, but you're not sure if it's stable enough to abstract.

Here's what that looks like in practice. We're building a notification service. We start with email:

```go
// WRONG — we have three copies of the same logic
func deduplicateEmails(emails []string) []string {
    seen := make(map[string]bool)
    var result []string
    for _, e := range emails {
        if !seen[e] {
            seen[e] = true
            result = append(result, e)
        }
    }
    return result
}

func deduplicateUserIDs(ids []int64) []int64 {
    seen := make(map[int64]bool)
    var result []int64
    for _, id := range ids {
        if !seen[id] {
            seen[id] = true
            result = append(result, id)
        }
    }
    return result
}

func deduplicateTagNames(tags []string) []string {
    seen := make(map[string]bool)
    var result []string
    for _, t := range tags {
        if !seen[t] {
            seen[t] = true
            result = append(result, t)
        }
    }
    return result
}
```

Three functions. Identical logic. Two of them even have the same underlying type (`string`). The only variation is the type label. This is exactly the mechanical duplication that generics are designed to fix.

## The Idiomatic Way

The refactoring process has four steps:

**Step 1: Identify the pattern.**

Look at the three function bodies. What's identical? The structure: create a `seen` map, iterate, check if seen, add to result. What varies? The type of the slice elements. Is the behavior different for different types? No — deduplication logic is the same regardless of whether you're deduplicating strings, ints, or custom types. This passes the generics test.

**Step 2: Write the generic version.**

Start with the simplest type parameter that makes the function body compile:

```go
// RIGHT — extract the generic version
func Deduplicate[T comparable](items []T) []T {
    seen := make(map[T]bool)
    result := make([]T, 0, len(items))
    for _, item := range items {
        if !seen[item] {
            seen[item] = true
            result = append(result, item)
        }
    }
    return result
}
```

The constraint is `comparable` because we're using `T` as a map key. We pre-allocate `result` with `make([]T, 0, len(items))` because we know the worst case (all items unique) ahead of time. These are the same optimizations you'd make in a concrete function.

**Step 3: Replace the concrete versions one at a time.**

Don't delete all three and hope for the best. Replace one, run the tests, commit. Then the next.

```go
// Before
emails = deduplicateEmails(emails)

// After — the compiler will catch any type mismatch immediately
emails = Deduplicate(emails)
```

```go
// Before
ids = deduplicateUserIDs(ids)

// After
ids = Deduplicate(ids)
```

```go
// Before
tags = deduplicateTagNames(tags)

// After
tags = Deduplicate(tags)
```

**Step 4: Verify readability improved.**

This is the step most refactoring guides skip. After replacing the concrete versions, read the call sites again. Is `Deduplicate(emails)` clearer than `deduplicateEmails(emails)`? Yes — it's shorter and the type is obvious from context. Did the function signature get harder to understand? No — `[T comparable]` is easy to parse once you know generics at all. The refactoring improved things. You can proceed.

Here's a second, slightly more complex example: a `FirstOrDefault` function. We had three concrete versions in a data access layer:

```go
// WRONG — repeated firstOrDefault for different types
func firstUserOrDefault(users []User, defaultUser User) User {
    if len(users) == 0 {
        return defaultUser
    }
    return users[0]
}

func firstOrderOrDefault(orders []Order, defaultOrder Order) Order {
    if len(orders) == 0 {
        return defaultOrder
    }
    return orders[0]
}

func firstStringOrDefault(strs []string, defaultStr string) string {
    if len(strs) == 0 {
        return defaultStr
    }
    return strs[0]
}
```

The generic extraction is straightforward:

```go
// RIGHT — works for any type
func FirstOrDefault[T any](items []T, defaultValue T) T {
    if len(items) == 0 {
        return defaultValue
    }
    return items[0]
}
```

No constraint needed at all here — we're not comparing or operating on `T`, just returning it. `any` is exactly right.

## In The Wild

The most common mistake I see during this kind of refactoring is over-extracting. You identify a pattern that looks duplicated, but the "duplication" is coincidental — the functions happen to look the same now, but they'll diverge as requirements change.

The test: are these functions duplicated because of a shared algorithm, or because of a shared current implementation? If the former, extract. If the latter, wait.

For example — say you have:

```go
func validateUser(u User) error {
    if u.Name == "" {
        return errors.New("name required")
    }
    if u.Email == "" {
        return errors.New("email required")
    }
    return nil
}

func validateProduct(p Product) error {
    if p.Name == "" {
        return errors.New("name required")
    }
    if p.SKU == "" {
        return errors.New("sku required")
    }
    return nil
}
```

These look similar, but the validation rules are type-specific. `User` requires email; `Product` requires SKU. If you extract a `Validate[T any]` function, you'll immediately hit the problem: you can't call type-specific validation logic on `T` without a `Validatable` interface — and at that point, you should just use the interface directly, not generics.

This is coincidental duplication. Don't extract it.

**When to stop** is as important as knowing when to start. Stop when:

- The generic version has more constraints than the bodies it'd replace
- The call sites require explicit type parameters (inference fails)
- The abstraction requires a marker interface that didn't exist before
- A new reader would find the concrete versions more understandable than the generic one

## The Gotchas

**Gotcha 1: Tests are mandatory before refactoring.**

If your concrete functions don't have tests, write them first. You need a safety net for the refactoring. The generic version might have a subtle behavior difference (zero-value handling, nil slice behavior) that only surfaces later without tests.

**Gotcha 2: The generic function's package placement matters.**

Where you put `Deduplicate` determines who can import it. If it's in an internal package, only your own modules can use it. If it's in a public utility package, you're making an API commitment. Be intentional about this — don't default to `util` or `helpers`.

**Gotcha 3: Don't refactor across packages in one commit.**

If `deduplicateEmails` is used in three different packages, replace it in one package at a time. Large cross-package refactors are hard to review and hard to roll back.

**Gotcha 4: Some duplication is intentional isolation.**

Sometimes two packages have the same utility function because they're intentionally isolated — they shouldn't share a dependency. A payment package and a notification package both having a `contains` helper isn't a problem to fix. If extracting the generic version requires a new shared package, ask whether that coupling is appropriate.

## Key Takeaway

The right time to refactor concrete to generic is after you've written the function twice, the third is clearly needed, and you can confirm the algorithm doesn't vary by type. Do it incrementally — one call site at a time, tests passing at each step. Then verify that the result is actually clearer. If the generic version is harder to read than the concrete versions it replaced, you've gone one step too far.

---

*[← Lesson 6: Real-World Examples](/post/go/go-generics-real-world/) | [Course Index](/series/go-generics-masterclass/) | [Next → Lesson 8: When Duplication Is Better Than Abstraction](/post/go/go-generics-duplication-vs-abstraction/)*
