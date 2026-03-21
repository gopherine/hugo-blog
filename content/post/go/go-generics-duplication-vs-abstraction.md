---
title: "Lesson 8: When Duplication Is Better Than Abstraction — Bad abstraction costs more than repeated code"
author: Atharva Pandey
series: "Go Generics Masterclass"
lesson: 8
keywords:
  - Go
  - golang
  - generics
  - duplication vs abstraction
  - premature abstraction
  - decision framework
  - golden rule
  - when to use generics
  - code clarity
tags:
  - Go tutorial
  - golang
  - generics
date: '2024-11-17T00:00:00.000Z'
---

I want to end this course with the idea I wish I'd understood at the start — not just intellectually, but in my gut. It's this: **duplication is a problem you can see. Bad abstraction is a problem you can't see until it's too late.**

Duplicated code is visible. You grep for it, you find it, you fix it. It's annoying, but it's honest. Bad abstraction hides behind clean-looking code. It's the function that requires fifteen minutes of context to understand, the type parameter that nobody knows how to satisfy, the constraint that's technically correct but practically useless. It costs you in onboarding, in debugging, in every change that touches it for the rest of the codebase's life.

Generics make abstraction cheaper to write. That's dangerous. When something is cheap, you use it more than you should.

## The Problem

Here's the scenario. You've got two functions:

```go
// Version A — duplicated but clear
func minInt(a, b int) int {
    if a < b {
        return a
    }
    return b
}

func minFloat64(a, b float64) float64 {
    if a < b {
        return a
    }
    return b
}
```

And a colleague suggests replacing them with:

```go
// Version B — generic
import "cmp"

func min[T cmp.Ordered](a, b T) T {
    if a < b {
        return a
    }
    return b
}
```

Version B is objectively better here. Two functions become one. The type constraint is clear. Any reader who knows basic generics can parse it in three seconds. This is the right call.

Now here's a harder scenario. You've got two functions that look similar:

```go
// These look duplicated — but are they?
func truncateUserName(name string, maxLen int) string {
    if len(name) <= maxLen {
        return name
    }
    return name[:maxLen-3] + "..."
}

func truncateProductDescription(desc string, maxLen int) string {
    if len(desc) <= maxLen {
        return desc
    }
    return desc[:maxLen-3] + "..."
}
```

Same logic. Same underlying type. A strong case for generics, right?

Actually, no. And here's why: the types are already the same (`string`). There's no type duplication. What's duplicated is the function body — and a generic function can't fix that. What you'd actually write is:

```go
// RIGHT — a regular function, no generics needed
func truncate(s string, maxLen int) string {
    if len(s) <= maxLen {
        return s
    }
    return s[:maxLen-3] + "..."
}
```

Plain function. No type parameters. This is the lesson: generics solve type duplication, not code duplication in general. If the types are already the same, you don't need generics — you need a regular refactor.

## The Idiomatic Way

The five-question decision framework I use before introducing any generic:

**Question 1: Is the logic actually identical across types?**

Not "similar" — identical. If there's any type-specific branching, conditional behavior, or difference in edge case handling, the logic isn't identical. Write separate functions.

**Question 2: Does the type actually vary at call sites?**

If all current and foreseeable callers use the same type, you don't need generics. You need a concrete function. Generics are justified by actual type variation, not hypothetical future variation.

**Question 3: Is the constraint expressible without a novel interface?**

If expressing your constraint requires defining a new interface just to make the generic work, stop. The new interface is now part of your API, and if it's contrived, it's a smell. Interfaces should emerge from behavior, not be created to enable generics.

**Question 4: Does the generic version read as clearly as the concrete version?**

Read the generic function signature and body. Does it take longer to understand than the concrete version? If a new team member would spend more time understanding the generic than the duplication would have cost, the generic is wrong.

**Question 5: Would removing one call site justify removing the generic entirely?**

If your "generic" function is only called with one type in practice, it's a concrete function wearing a generic costume. The `[T any]` is dead weight. Remove it.

Here's what this looks like in practice:

```go
// WRONG — a generic function that fails questions 2 and 5
func wrapError[T any](msg string, err error, context T) error {
    return fmt.Errorf("%s (%v): %w", msg, context, err)
}

// In practice, T is always string at every call site
wrapError("failed to fetch user", err, "user_id: 42")
wrapError("payment declined", err, "order_id: 99")
```

```go
// RIGHT — just use fmt.Errorf directly, or a concrete helper
func wrapError(msg string, err error, context string) error {
    return fmt.Errorf("%s (%s): %w", msg, context, err)
}
```

The generic version added nothing. `T` is `string` everywhere. You've made the function signature harder to read and gained zero type safety or reuse.

## In The Wild

The clearest case I've seen for "duplication is better" came from a team building a rules engine. They had three rule evaluation functions:

```go
func evaluatePricingRule(r PricingRule, ctx OrderContext) bool { ... }
func evaluateShippingRule(r ShippingRule, ctx ShipmentContext) bool { ... }
func evaluateInventoryRule(r InventoryRule, ctx InventoryContext) bool { ... }
```

An engineer — well-intentioned — wanted to generalize this into:

```go
// WRONG — forced generics where interfaces are needed, and behavior varies
func evaluateRule[R any, C any](rule R, ctx C) bool {
    // Can't do anything here — R and C are unconstrained
    // Would need constraints that are essentially the interface anyway
}
```

The problem: the evaluation logic is completely different for each rule type. Pricing rules check price thresholds. Shipping rules check geolocation. Inventory rules check stock levels. These are not the same algorithm. Wrapping them in a generic function requires a constraint like:

```go
type Rule[C any] interface {
    Evaluate(ctx C) bool
}
```

Which is just an interface. And if it's an interface, you don't need the generic wrapper at all — you use the interface directly and let each type implement `Evaluate` in its own way.

The three concrete functions were the right answer. They were clear, independently testable, and accurately reflected that the rules engine had three different domains with different logic. The duplication was superficial (same signature shape) but the implementation was genuinely different. Abstracting it would have been dishonest code.

## The Gotchas

**Gotcha 1: The three-strikes rule is a guideline, not a law.**

The popular advice is "don't abstract until you've seen it three times." That's a useful heuristic, but it's not infallible. Three instances of coincidentally similar code is still not a reason to abstract. The question is whether the similarity is structural (same algorithm, different type) or accidental (happen to look alike today).

**Gotcha 2: Code review pressure skews toward abstraction.**

In code review, reviewers notice duplication and suggest abstracting it. They don't feel the pain of the resulting abstraction — that lands on the next person who has to modify it. If you're in review and someone suggests a generic abstraction for two instances, it's completely reasonable to say "I'll extract this when there's a third" and push back.

**Gotcha 3: Go's standard library is conservative for a reason.**

Look at the Go standard library. Even post-1.18, it's not riddled with generics. The team added generic functions in `slices`, `maps`, and `cmp` packages — small, targeted utilities where the value is undeniable. The rest of the library stayed concrete. That restraint is intentional. Follow it.

**Gotcha 4: Abstraction debt is harder to pay than duplication debt.**

If you duplicate a function and later decide to deduplicate, you find both copies, write the generic version, replace the call sites, done. If you abstract prematurely and later realize the abstraction is wrong, you have to understand all callers, migrate them, delete the abstraction, and deal with the confusion you introduced in the meantime. The refactoring cost is asymmetric. Err on the side of duplication.

## Key Takeaway

Generics exist to eliminate mechanical type duplication — places where you'd write the same function body with a different type signature. They're not a tool for eliminating all repetition, expressing FP abstractions, or future-proofing against requirements that don't exist yet. The golden rule: if a function would be identical for three different types except for the type name, and you actually need all three, extract the generic version. Otherwise, leave it concrete.

Bad abstraction costs more than repeated code — in reading time, debugging time, onboarding time, and the cognitive overhead of every change that touches it. That cost is invisible until it's enormous. Generics are powerful enough that the temptation to abstract prematurely is real. The best Go code I've read resists that temptation.

This is the end of the Go Generics Masterclass. The full series covers what generics solve, how type parameters and constraints work, the good patterns, the interface vs. generics decision, the anti-patterns to avoid, real production implementations, the refactoring workflow, and finally — the most important lesson — when to walk away from abstraction altogether. Use generics deliberately. Your future teammates will thank you.

---

*[← Lesson 7: Refactoring Concrete to Generic](/post/go/go-generics-refactoring/) | [Course Index](/series/go-generics-masterclass/) | Next → (Graduate Complete!)*
