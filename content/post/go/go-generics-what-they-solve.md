---
title: "Lesson 1: What Generics Solve in Go — Remove duplication without hiding intent"
author: Atharva Pandey
series: "Go Generics Masterclass"
lesson: 1
keywords:
  - Go
  - golang
  - generics
  - type parameters
  - interface{}
  - any
  - code generation
  - generics introduction
tags:
  - Go tutorial
  - golang
  - generics
date: '2024-06-07T00:00:00.000Z'
---

I spent two years writing Go before generics shipped in 1.18, and I'll be honest — I didn't miss them at first. Go's simplicity felt like a feature. Then I found myself maintaining a codebase with six nearly-identical sort functions, a `MinInt`, `MinFloat64`, `MinInt64`, and a comment that said "do not touch, generated." That's when I started caring.

Generics in Go aren't a revolution. They're a targeted fix for a specific, real pain point: you had to either use `interface{}` and lose type safety, or repeat yourself across every concrete type you cared about. Neither option aged well.

## The Problem

Before Go 1.18, if you wanted a `Max` function that worked on both integers and floats, you had two choices — and both were bad.

**Option A: Write it for every type.**

```go
// WRONG — or at least, wrong at scale
func MaxInt(a, b int) int {
    if a > b {
        return a
    }
    return b
}

func MaxFloat64(a, b float64) float64 {
    if a > b {
        return a
    }
    return b
}

func MaxInt64(a, b int64) int64 {
    if a > b {
        return a
    }
    return b
}
```

This is mechanical duplication. The logic is identical. The only thing changing is the type signature. If a bug sneaks into one version, you have to find every copy. That's not engineering — that's maintenance debt accumulating by the hour.

**Option B: Use `interface{}` (now `any`) and cast at runtime.**

```go
// WRONG — loses type safety entirely
func Max(a, b interface{}) interface{} {
    // how do you compare these? You can't without a type switch.
    switch v := a.(type) {
    case int:
        if v > b.(int) {
            return a
        }
        return b
    case float64:
        if v > b.(float64) {
            return a
        }
        return b
    }
    panic("unsupported type")
}
```

Now you've got runtime panics lurking in production. The compiler can't help you. You lose auto-complete. Every caller has to cast the result back. And if someone passes a `string`, you find out at 2am when alerts fire.

Some teams reached for code generation via `go generate` and tools like `stringer` or hand-rolled templates. It works, but you're now maintaining templates, generated files, and the infrastructure around them. That's a tax on every refactor.

## The Idiomatic Way

With generics, you write the function once and let the compiler enforce types at call sites:

```go
// RIGHT — generic Max using a type constraint
import "golang.org/x/exp/constraints"

func Max[T constraints.Ordered](a, b T) T {
    if a > b {
        return a
    }
    return b
}

// Works for all ordered types — no casting, no panics, no copies
result1 := Max(3, 7)           // int
result2 := Max(1.5, 2.3)      // float64
result3 := Max("apple", "banana") // string
```

The `[T constraints.Ordered]` part is the type parameter. `T` is a placeholder — at compile time, the compiler generates a concrete version for each type you actually use. You get the full benefits of static typing: the compiler catches mismatches, your editor auto-completes, and the output is as fast as if you'd written the function by hand for each type.

This is the core value proposition of Go generics: **remove mechanical duplication without sacrificing type safety or readability**.

Notice what didn't change — the function body is still just an `if` statement. It looks boring. That's the point. Generics shouldn't make code feel clever. They should make it feel obvious.

## In The Wild

Let me show you what this looks like in a real codebase. Say you're building a service that deals with both `UserID` (which is an `int64`) and `OrderID` (also `int64`), and you've got a bunch of filter utilities:

```go
// Before generics — repeated for every ID type
func FilterUserIDs(ids []UserID, keep func(UserID) bool) []UserID {
    var result []UserID
    for _, id := range ids {
        if keep(id) {
            result = append(result, id)
        }
    }
    return result
}

func FilterOrderIDs(ids []OrderID, keep func(OrderID) bool) []OrderID {
    var result []OrderID
    for _, id := range ids {
        if keep(id) {
            result = append(result, id)
        }
    }
    return result
}
```

With generics, you collapse both into one:

```go
// RIGHT — one function, works for any slice type
func Filter[T any](slice []T, keep func(T) bool) []T {
    var result []T
    for _, v := range slice {
        if keep(v) {
            result = append(result, v)
        }
    }
    return result
}

activeUsers := Filter(userIDs, func(id UserID) bool {
    return activeSet[id]
})

recentOrders := Filter(orderIDs, func(id OrderID) bool {
    return id > cutoffOrderID
})
```

Same function. Different types. Zero runtime cost from generics — the compiler does the specialization at build time. This is the use case generics were designed for.

## The Gotchas

Generics solve mechanical duplication. They don't solve everything, and they introduce a few traps worth knowing upfront.

**Trap 1: Reaching for generics when interfaces are the right tool.**

If what varies is *behavior* — not just the data type — use an interface. Generics shine when the algorithm is fixed and the data type varies. If different types need to do different things, that's polymorphism, and Go already handles that with interfaces. I'll cover this distinction in depth in Lesson 4.

**Trap 2: Assuming generics make code more readable.**

They don't, by default. A function with four type parameters and nested constraints is harder to read than four concrete functions. The bar for introducing generics should be: "does this make the code obviously simpler?" If you have to think about it, the answer's probably no.

**Trap 3: Thinking generics are free to maintain.**

A generic function has more surface area than a concrete one. Its type constraint is part of its API contract. If you tighten a constraint later, callers break. If you loosen it, you might enable misuse. Design your constraints carefully — they're permanent the moment someone depends on them.

**Trap 4: Using generics to avoid thinking about design.**

I've seen people slap `[T any]` on everything because it feels like forward-compatibility. It's not. It's premature abstraction. Start concrete. If you find yourself writing the same function body three times for three different types, that's when you extract the generic version. Not before.

## Key Takeaway

Generics in Go are a tool for eliminating one specific kind of pain: mechanical duplication across types where the logic is identical. Before generics, you either copied code, lost type safety with `interface{}`, or reached for code generation. Now you have a fourth option that's cleaner than all three.

Use them when the algorithm is the same and only the type varies. Don't use them as a first resort — use them when you've felt the duplication pain and confirmed it's the type that's varying, not the behavior. The rest of this series will show you exactly where that line is.

---

*← Previous | [Course Index](/series/go-generics-masterclass/) | [Next → Lesson 2: Type Parameters and Constraints](/post/go/go-generics-type-parameters/)*
