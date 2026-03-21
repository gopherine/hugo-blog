---
title: "Lesson 4: Interfaces vs Generics — Behavior or data shape? That's your answer"
author: Atharva Pandey
series: "Go Generics Masterclass"
lesson: 4
keywords:
  - Go
  - golang
  - generics
  - interfaces
  - polymorphism
  - io.Reader
  - behavior abstraction
  - data shape
  - type parameters
  - decision framework
tags:
  - Go tutorial
  - golang
  - generics
date: '2024-08-14T00:00:00.000Z'
---

One of the most common mistakes I see in Go codebases post-1.18 is people reaching for generics when interfaces were already the right tool — and vice versa. The two features look superficially similar. Both let you write code that works with multiple types. But they're solving different problems, and using the wrong one makes code harder to read, test, and extend.

Here's the question I ask myself every time: **Is what varies the behavior, or the data shape?** That single question gets me to the right answer ninety percent of the time.

## The Problem

The confusion is understandable. Both interfaces and generics let you write a function that accepts "something," without specifying the exact type. But the moment you look at what you're actually doing with that "something," the right choice becomes clear.

Here's an example of each mistake:

```go
// WRONG — using generics when behavior varies (use an interface instead)
type Processor[T any] interface {
    Process(input T) error
}

// The problem: different T types will have DIFFERENT Process implementations.
// That's polymorphism — that's what interfaces are for.
// Generics here adds complexity with zero benefit.
```

```go
// WRONG — using interface{} when only the data type varies (use generics instead)
func copySlice(src interface{}) interface{} {
    // You're forced to use reflection or a massive type switch
    // just to copy a slice. The algorithm is identical for all types.
    // This is exactly what generics are designed for.
}
```

The first mistake treats generics as a way to write "typed interfaces." The second mistake reaches for `interface{}` out of habit, ignoring that the algorithm is actually type-independent.

## The Idiomatic Way

**Interfaces win when different types have different behavior.**

`io.Reader` is the canonical example. The `Read(p []byte) (n int, err error)` method is implemented differently by a file, a network connection, a bytes buffer, and a gzip decompressor. Each type has its own logic. What unifies them is the *contract* — the method signature — not the underlying data.

```go
// RIGHT — interface for behavioral polymorphism
type io.Reader interface {
    Read(p []byte) (n int, err error)
}

// Works with any Reader — file, network, buffer, gzip stream
func process(r io.Reader) error {
    buf := make([]byte, 4096)
    for {
        n, err := r.Read(buf)
        if n > 0 {
            // process buf[:n]
        }
        if err == io.EOF {
            return nil
        }
        if err != nil {
            return err
        }
    }
}
```

The key test: would you implement `Read` the same way for a file as for a network connection? No. The behavior is different. That's an interface.

**Generics win when the algorithm is the same and only the type varies.**

Say you're writing a `Chunk` function that splits a slice into smaller slices of at most size `n`. The logic is identical whether you're chunking `[]User`, `[]Order`, or `[]int`. There's no behavior that varies — just the type label.

```go
// RIGHT — generics for type-independent algorithms
func Chunk[T any](slice []T, size int) [][]T {
    if size <= 0 {
        return nil
    }
    var chunks [][]T
    for size < len(slice) {
        slice, chunks = slice[size:], append(chunks, slice[:size])
    }
    return append(chunks, slice)
}

// Same function, different types — no code duplication
userChunks := Chunk(users, 100)
orderChunks := Chunk(orders, 50)
```

Would you implement `Chunk` differently for `[]User` vs `[]Order`? No — the chunking logic is identical. That's generics.

**The io.Reader test** is a quick mental check I run: "If I swapped this type for a completely different type, would the implementation change?" If yes — interface. If no — generics (or just a concrete type if there's only one).

Here's a side-by-side that makes the distinction concrete. We have a logging system that supports multiple backends:

```go
// RIGHT — use an interface for the logger because each backend logs differently
type Logger interface {
    Info(msg string, fields ...Field)
    Error(msg string, err error, fields ...Field)
}

// Implementations are all different
type JSONLogger struct{ ... }
func (l *JSONLogger) Info(msg string, fields ...Field) { /* write JSON */ }

type TextLogger struct{ ... }
func (l *TextLogger) Info(msg string, fields ...Field) { /* write plain text */ }

// Now a generic log formatter — converts any structured data to Fields
// The ALGORITHM is the same (iterate fields, format values), TYPE varies
func StructToFields[T any](v T) []Field {
    // uses reflection or a marker interface — same logic for all T
}
```

See the split? The logger is an interface (behavior varies). The formatter is generic (algorithm is fixed).

## In The Wild

I refactored a payment processing service that had this pattern:

```go
// WRONG — generic where an interface belongs
type PaymentProcessor[T any] struct {
    processor T
}

func (p PaymentProcessor[T]) Charge(amount float64) error {
    // can't call any methods on p.processor — T is unconstrained
    // so this does nothing useful
    return nil
}
```

The team thought they were being "future-proof." What they actually built was a struct that couldn't do anything with its own field. The fix was to define the behavior as an interface:

```go
// RIGHT — interface captures the contract, generics aren't needed here
type PaymentProcessor interface {
    Charge(amount float64) error
    Refund(txID string, amount float64) error
}

type StripeProcessor struct { client *stripe.Client }
func (s *StripeProcessor) Charge(amount float64) error { ... }

type BraintreeProcessor struct { gateway *braintree.Gateway }
func (b *BraintreeProcessor) Charge(amount float64) error { ... }
```

Now the code is testable (you can mock `PaymentProcessor`), extensible (add a new backend without changing callers), and obvious (the method set documents the contract clearly).

The generic version — `PaymentProcessor[T]` — was actually *worse* than the interface version on every dimension.

## The Gotchas

**Gotcha 1: Generics make mocking harder.**

With an interface, you mock it. With a generic type, you have to instantiate it with a concrete type for testing. If your generic code wraps behavior (like an HTTP client), you've made it harder to test. Interfaces are still the right tool for anything you want to swap out in tests.

**Gotcha 2: Interfaces allow runtime polymorphism; generics don't.**

With an interface, you can have a `[]Logger` containing different logger implementations. With generics, `[]Stack[int]` and `[]Stack[string]` are different types — you can't mix them in the same slice. If you need to work with collections of different concrete types that share behavior, you need an interface, not generics.

**Gotcha 3: Interface methods can't use type parameters.**

If you find yourself wanting to write `Process[T any](input T) error` as an interface method, you can't — Go doesn't allow it. This is often a signal that you should use generics at the function level, not the interface level.

**Gotcha 4: Don't replace `io.Reader`, `io.Writer`, and `fmt.Stringer` with generic alternatives.**

These interfaces are woven into the standard library and every third-party package. Replacing them with generic versions breaks interoperability. The ecosystem uses interfaces for behavioral contracts. Respect that.

## Key Takeaway

Use interfaces when different types implement the same behavior differently — the method bodies vary. Use generics when the algorithm is identical and only the type label changes — the method body is the same for all types. When you're unsure, ask: "Would I implement this differently for a file than for a network connection?" That's the io.Reader test. If yes, it's an interface. If no, it's generics. Getting this right is the single most impactful generic design decision you'll make.

---

*[← Lesson 3: Good Patterns](/post/go/go-generics-good-patterns/) | [Course Index](/series/go-generics-masterclass/) | [Next → Lesson 5: Anti-Patterns](/post/go/go-generics-anti-patterns/)*
