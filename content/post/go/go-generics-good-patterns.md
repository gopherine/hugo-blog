---
title: "Lesson 3: Good Patterns — Generic code should be boring"
author: Atharva Pandey
series: "Go Generics Masterclass"
lesson: 3
keywords:
  - Go
  - golang
  - generics
  - Map
  - Filter
  - Reduce
  - Stack
  - Set
  - Result type
  - generic data structures
  - slice utilities
tags:
  - Go tutorial
  - golang
  - generics
date: '2024-07-22T00:00:00.000Z'
---

The best generic code I've ever written looks like the worst. No clever type wizardry, no five-level constraint hierarchies — just a function that clearly does one thing, works for any type that makes sense, and disappears into the background of a codebase. When someone reads it two years later and immediately understands it, that's a win.

This lesson is about the patterns where generics genuinely shine. These are the ones that survived code review, that made the team's lives measurably better, and that I'd reach for again without hesitation.

## The Problem

Before generics, Go developers either lived with repeated utility code or pulled in third-party packages like `github.com/samber/lo` just to get a `Filter` function. The standard library had nothing. Teams copy-pasted the same `containsString`, `containsInt`, `filterUsers` helpers across packages.

```go
// WRONG — repeated across every package that needs it
func containsString(slice []string, target string) bool {
    for _, s := range slice {
        if s == target {
            return true
        }
    }
    return false
}

func containsInt(slice []int, target int) bool {
    for _, n := range slice {
        if n == target {
            return true
        }
    }
    return false
}

// And on and on for every type...
```

The duplication isn't just annoying — it's a maintenance risk. When you find a bug (what if `slice` is nil?), you have to find every copy.

## The Idiomatic Way

The sweet spot for generics is small, single-purpose utility functions over slices and maps. Here's the core set I keep in an internal `sliceutil` package:

```go
// RIGHT — the core slice utilities
package sliceutil

import "cmp"

// Contains reports whether target is in slice.
func Contains[T comparable](slice []T, target T) bool {
    for _, v := range slice {
        if v == target {
            return true
        }
    }
    return false
}

// Filter returns a new slice with only the elements that satisfy keep.
func Filter[T any](slice []T, keep func(T) bool) []T {
    result := make([]T, 0, len(slice))
    for _, v := range slice {
        if keep(v) {
            result = append(result, v)
        }
    }
    return result
}

// Map transforms each element of slice using f.
func Map[T, U any](slice []T, f func(T) U) []U {
    result := make([]U, len(slice))
    for i, v := range slice {
        result[i] = f(v)
    }
    return result
}

// Reduce folds slice into a single value.
func Reduce[T, U any](slice []T, initial U, f func(U, T) U) U {
    acc := initial
    for _, v := range slice {
        acc = f(acc, v)
    }
    return acc
}

// Keys returns the keys of a map as a slice.
func Keys[K comparable, V any](m map[K]V) []K {
    keys := make([]K, 0, len(m))
    for k := range m {
        keys = append(keys, k)
    }
    return keys
}
```

These are boring. They're supposed to be boring. Usage reads naturally:

```go
active := sliceutil.Filter(users, func(u User) bool { return u.Active })
names := sliceutil.Map(users, func(u User) string { return u.Name })
total := sliceutil.Reduce(orders, 0.0, func(sum float64, o Order) float64 {
    return sum + o.Amount
})
```

**Generic data structures** are the other place generics pay off clearly. A typed stack:

```go
// RIGHT — a type-safe stack
type Stack[T any] struct {
    items []T
}

func (s *Stack[T]) Push(v T) {
    s.items = append(s.items, v)
}

func (s *Stack[T]) Pop() (T, bool) {
    if len(s.items) == 0 {
        var zero T
        return zero, false
    }
    last := len(s.items) - 1
    item := s.items[last]
    s.items = s.items[:last]
    return item, true
}

func (s *Stack[T]) Peek() (T, bool) {
    if len(s.items) == 0 {
        var zero T
        return zero, false
    }
    return s.items[len(s.items)-1], true
}

func (s *Stack[T]) Len() int {
    return len(s.items)
}
```

No type assertions. No panics. The compiler ensures you put `int`s in and get `int`s out.

**A `Result[T]` type** is another pattern I've found genuinely useful. It makes error-handling in pipelines cleaner:

```go
// RIGHT — a Result type that wraps either a value or an error
type Result[T any] struct {
    value T
    err   error
}

func OK[T any](v T) Result[T] {
    return Result[T]{value: v}
}

func Err[T any](err error) Result[T] {
    return Result[T]{err: err}
}

func (r Result[T]) Unwrap() (T, error) {
    return r.value, r.err
}

func (r Result[T]) IsOK() bool {
    return r.err == nil
}

func (r Result[T]) Value() T {
    if r.err != nil {
        panic("called Value on an error Result")
    }
    return r.value
}
```

Usage in a processing pipeline:

```go
func fetchUser(id int) Result[User] {
    u, err := db.GetUser(id)
    if err != nil {
        return Err[User](err)
    }
    return OK(u)
}

result := fetchUser(42)
if !result.IsOK() {
    // handle error
}
user := result.Value()
```

## In The Wild

Here's how these patterns combine in a real feature. Say we're building a notification service that needs to batch-send messages only to active, verified users:

```go
func (s *NotificationService) SendBatch(userIDs []UserID, msg string) error {
    // Fetch all users
    users, err := s.repo.GetUsers(userIDs)
    if err != nil {
        return fmt.Errorf("fetching users: %w", err)
    }

    // Filter to active + verified
    eligible := sliceutil.Filter(users, func(u User) bool {
        return u.Active && u.Verified
    })

    if len(eligible) == 0 {
        return nil
    }

    // Extract email addresses
    emails := sliceutil.Map(eligible, func(u User) string {
        return u.Email
    })

    // Send
    return s.mailer.SendBulk(emails, msg)
}
```

That reads like English. You can follow what it does without knowing anything about generics. The generic functions are invisible — they're just plumbing that disappears into the background.

Compare that to what this would have looked like before generics: two custom loops, two intermediate slices, and type-specific function names that might differ across packages. The generic version isn't just shorter — it's more trustworthy because `sliceutil.Filter` is tested once and used everywhere.

## The Gotchas

**Gotcha 1: Pre-allocating with `make` matters.**

In the `Filter` example above, I used `make([]T, 0, len(slice))` instead of `var result []T`. This pre-allocates capacity for the worst case (all elements pass the filter) and avoids repeated reallocation. For large slices, this is the difference between one allocation and many. Always pre-allocate when you know an upper bound.

**Gotcha 2: Return the zero value, not `nil`, from generic functions.**

When a generic function needs to return "nothing" (like `Pop` when the stack is empty), use `var zero T` to get the zero value for whatever type `T` is. Don't return `nil` — that only works for pointer types and will cause a compile error for value types like `int` or `struct{}`.

```go
// WRONG — nil doesn't work for value types
func (s *Stack[T]) Pop() (T, bool) {
    if len(s.items) == 0 {
        return nil, false // compile error if T is int
    }
    ...
}

// RIGHT — var zero T always works
func (s *Stack[T]) Pop() (T, bool) {
    if len(s.items) == 0 {
        var zero T
        return zero, false
    }
    ...
}
```

**Gotcha 3: Two-type-parameter functions need both types to be inferrable.**

`Map[T, U]` works because both `T` and `U` can be inferred from the input slice and return type of `f`. But if you write a function where `U` can't be inferred, callers have to specify both parameters. Keep your generic function signatures simple enough that inference works — it dramatically improves ergonomics.

**Gotcha 4: Generic types can't be compared with `==` unless `T` is `comparable`.**

If you build a generic `Set[T]` and want a `Difference` function, you need `T comparable`. But you also need to be careful about nil maps and other edge cases. Test your generic utilities thoroughly — the bugs tend to be in the edge cases that aren't specific to any one type.

## Key Takeaway

The good generic patterns are the boring ones: `Filter`, `Map`, `Contains`, typed stacks and sets, a `Result[T]` wrapper. They work because the algorithm is fixed and only the type varies. Each one replaces a family of type-specific functions that would otherwise spread across your codebase and diverge over time. Write them once, test them once, and let the compiler verify their use everywhere.

---

*[← Lesson 2: Type Parameters and Constraints](/post/go/go-generics-type-parameters/) | [Course Index](/series/go-generics-masterclass/) | [Next → Lesson 4: Interfaces vs Generics](/post/go/go-generics-interfaces-vs-generics/)*
