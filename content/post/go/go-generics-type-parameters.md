---
title: "Lesson 2: Type Parameters and Constraints — Teaching the compiler what you mean"
author: Atharva Pandey
series: "Go Generics Masterclass"
lesson: 2
keywords:
  - Go
  - golang
  - generics
  - type parameters
  - constraints
  - constraints.Ordered
  - comparable
  - any
  - interface union
  - underlying type
  - tilde operator
tags:
  - Go tutorial
  - golang
  - generics
date: '2024-07-01T00:00:00.000Z'
---

The hardest part of learning Go generics isn't the concept — it's the syntax. The first time I read a function signature like `func Keys[K comparable, V any](m map[K]V) []K`, I did a double-take. It looks like someone snuck type annotations from another language into Go. But once it clicks, it's actually pretty elegant — every piece is there for a reason.

This lesson is about understanding type parameters and constraints deeply enough that you can write them yourself without copying from examples. That's the threshold that separates "I've used generics" from "I understand generics."

## The Problem

The confusion usually starts with the question: what operations can I actually perform on a generic type? With a concrete type like `int`, you know you can add, compare, and format it. With a `string`, you can concatenate and index it. But with a plain `T`, you can't do much at all — and the compiler will yell at you if you try.

```go
// WRONG — you can't compare T without a constraint
func Contains[T any](slice []T, target T) bool {
    for _, v := range slice {
        if v == target { // compile error: cannot compare T with ==
            return true
        }
    }
    return false
}
```

The error makes sense when you think about it. `T` could be anything — including a `[]byte` or a `map[string]int`, which aren't comparable in Go. The compiler can't generate the `==` operation unless it knows `T` supports it. That's exactly what constraints are for: you're telling the compiler "T can be any type, *as long as it satisfies this constraint*."

```go
// WRONG — you can't do arithmetic on T without a numeric constraint
func Sum[T any](nums []T) T {
    var total T
    for _, n := range nums {
        total += n // compile error: operator + not defined for T
    }
    return total
}
```

Same story. The compiler needs to know `T` supports `+` before it can compile that line.

## The Idiomatic Way

**The `comparable` constraint** is the one you reach for when you need `==` or `!=`. It covers all types that Go can compare: numbers, strings, booleans, pointers, channels, arrays (not slices), and structs whose fields are all comparable.

```go
// RIGHT — comparable tells the compiler T supports ==
func Contains[T comparable](slice []T, target T) bool {
    for _, v := range slice {
        if v == target {
            return true
        }
    }
    return false
}

// Works for any comparable type
Contains([]int{1, 2, 3}, 2)           // true
Contains([]string{"a", "b"}, "c")     // false
Contains([]UserID{10, 20, 30}, 20)    // true — custom type works too
```

**The `any` constraint** is just an alias for `interface{}`. It means "no constraint at all." Use it when you genuinely don't need to do anything with `T` except store and return it — like in a generic cache or container.

**The `constraints.Ordered` constraint** covers types that support `<`, `>`, `<=`, `>=` — numbers and strings. It's defined in `golang.org/x/exp/constraints`, though as of Go 1.21, `cmp.Ordered` from the standard library covers the same ground.

```go
// RIGHT — Ordered for any type that supports comparison operators
import "cmp"

func Min[T cmp.Ordered](a, b T) T {
    if a < b {
        return a
    }
    return b
}
```

**Custom constraints with interface unions** are where things get interesting. You can define your own constraint by listing the exact set of types you want to allow:

```go
// RIGHT — custom constraint using a union of types
type Integer interface {
    int | int8 | int16 | int32 | int64
}

func Sum[T Integer](nums []T) T {
    var total T
    for _, n := range nums {
        total += n
    }
    return total
}
```

This is an *interface type set*. The constraint `Integer` is satisfied by any type whose underlying type is one of the listed types — as long as you use the tilde `~` operator.

**The tilde `~` operator** is the subtle but critical piece. Without `~`, your constraint only accepts the exact named types you list. With `~`, it also accepts any user-defined type whose *underlying* type matches:

```go
// WRONG — doesn't accept custom types based on int
type Celsius float64
type Fahrenheit float64

type Degrees interface {
    float64 // no tilde — only accepts literal float64, not Celsius or Fahrenheit
}

func Average[T Degrees](temps []T) T { ... }

// This fails: Celsius does not satisfy Degrees
avg := Average([]Celsius{98.6, 99.1, 97.8})
```

```go
// RIGHT — tilde accepts any type whose underlying type is float64
type Degrees interface {
    ~float64 // accepts float64, Celsius, Fahrenheit, and any other ~float64 type
}

func Average[T Degrees](temps []T) T {
    var sum T
    for _, t := range temps {
        sum += t
    }
    return sum / T(len(temps))
}

// Now this works
avg := Average([]Celsius{98.6, 99.1, 97.8})
```

That tilde makes a huge difference in practice. Almost every custom numeric or string constraint should use `~`, or else callers with domain-specific types (like `UserID int64` or `Celsius float64`) can't use your generic function.

## In The Wild

Here's a real pattern I use for building type-safe set operations. The key insight is combining `comparable` with an interface to get both equality checks and string representation:

```go
// A generic set that works for any comparable type
type Set[T comparable] struct {
    items map[T]struct{}
}

func NewSet[T comparable](items ...T) *Set[T] {
    s := &Set[T]{items: make(map[T]struct{})}
    for _, item := range items {
        s.items[item] = struct{}{}
    }
    return s
}

func (s *Set[T]) Contains(item T) bool {
    _, ok := s.items[item]
    return ok
}

func (s *Set[T]) Add(item T) {
    s.items[item] = struct{}{}
}

func (s *Set[T]) Len() int {
    return len(s.items)
}
```

Usage looks exactly like you'd want it to:

```go
ids := NewSet(1, 2, 3, 4, 5)
ids.Contains(3)  // true
ids.Contains(9)  // false

tags := NewSet("go", "generics", "backend")
tags.Add("performance")
```

The type parameter `T comparable` is the only thing making this work. Without it, you'd need a separate `IntSet`, `StringSet`, `UserIDSet`, and so on.

## The Gotchas

**Gotcha 1: Methods can't have their own type parameters.**

In Go, you can't add a type parameter to a method that isn't on the receiver type. This trips people up when they try to write something like a fluent builder:

```go
// WRONG — methods can't declare new type parameters
type Builder struct{}

func (b *Builder) Transform[T any](input T) T { // compile error
    return input
}
```

The workaround is to either make `T` a type parameter on the struct itself, or use a top-level generic function instead of a method.

**Gotcha 2: Type inference doesn't always work.**

Go can usually infer type parameters from the arguments you pass, but not always. When it can't, you have to specify them explicitly:

```go
// Sometimes inference fails — you have to be explicit
result := Map[string, int](words, len) // explicit type params
```

Don't fight this. Explicit type parameters are fine — they make the code clearer anyway.

**Gotcha 3: Interface constraints aren't the same as interface types.**

A constraint like `Integer` above can only be used as a type constraint — you can't use it as a variable type. `var x Integer` is a compile error. This is intentional; it prevents the confusing case where someone uses a constraint where a regular interface is needed.

**Gotcha 4: Combining method and type constraints.**

You can combine them in one interface:

```go
type Stringer interface {
    ~string | ~[]byte
    String() string
}
```

But this means "the underlying type is `string` or `[]byte`, AND the type has a `String()` method." Be careful — it's easy to write a constraint nobody can satisfy.

## Key Takeaway

Type parameters are the variable. Constraints are the type of that variable. The tilde `~` prefix means "any type whose underlying type is this." Use `comparable` for equality, `cmp.Ordered` for ordering, and custom interface unions when you need arithmetic or specific method sets. When in doubt, start with the loosest constraint that lets your function body compile — that's the tightest constraint you actually need.

---

*[← Lesson 1: What Generics Solve](/post/go/go-generics-what-they-solve/) | [Course Index](/series/go-generics-masterclass/) | [Next → Lesson 3: Good Patterns](/post/go/go-generics-good-patterns/)*
