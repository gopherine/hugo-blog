---
title: "Lesson 11: Interfaces — Contracts without the paperwork"
author: "Atharva Pandey"
keywords:
  - "Go"
  - "golang"
  - "interfaces"
  - "io.Reader"
  - "io.Writer"
  - "empty interface"
  - "type assertion"
  - "Go from Scratch"
tags:
  - "Go tutorial"
  - "golang"
  - "beginner"
series: "Go from Scratch"
lesson: 11
date: "2024-02-24"
---

When I first learned about interfaces in Go, I expected something complicated — annotations, `implements` keywords, class hierarchies. Instead, Go just asked: "does your type have the right methods?" That's it. No paperwork. No explicit declaration. If your type does what the interface requires, it satisfies the interface automatically.

This sounds small, but it changes how you design programs entirely.

## The Basics

An interface in Go is a named set of method signatures. Any type that implements those methods automatically satisfies the interface. You don't declare that you're satisfying it. Go figures it out at compile time.

Here's the simplest possible example:

```go
type Greeter interface {
    Greet() string
}

type English struct{}
type Spanish struct{}

func (e English) Greet() string  { return "Hello!" }
func (s Spanish) Greet() string  { return "¡Hola!" }

func sayHello(g Greeter) {
    fmt.Println(g.Greet())
}

func main() {
    sayHello(English{})  // Hello!
    sayHello(Spanish{})  // ¡Hola!
}
```

`English` and `Spanish` never say "I implement Greeter." They just have a `Greet()` method that returns a string. That's enough. This is called **implicit interface satisfaction**, and it's one of my favourite things about Go.

**io.Reader and io.Writer**

The most important interfaces in the Go standard library are `io.Reader` and `io.Writer`. They look deceptively simple:

```go
type Reader interface {
    Read(p []byte) (n int, err error)
}

type Writer interface {
    Write(p []byte) (n int, err error)
}
```

That's one method each. Yet because of this, you can write a function that works with a file, a network connection, an in-memory buffer, or a gzip stream — all with the exact same code, because they all satisfy `io.Reader`.

```go
func printContents(r io.Reader) {
    data, err := io.ReadAll(r)
    if err != nil {
        fmt.Println("error:", err)
        return
    }
    fmt.Println(string(data))
}

func main() {
    // Works with a file
    f, _ := os.Open("hello.txt")
    printContents(f)

    // Works with a string in memory
    printContents(strings.NewReader("hello from memory"))
}
```

**The empty interface**

There's a special interface with no methods at all: `interface{}`. Since it requires nothing, every type satisfies it. In modern Go (1.18+), you can write `any` instead, which is just an alias.

```go
func printAnything(v any) {
    fmt.Println(v)
}

printAnything(42)
printAnything("hello")
printAnything([]int{1, 2, 3})
```

Use `any` sparingly. When you accept `any`, you lose type safety — the compiler can't help you anymore. But it's genuinely useful in places like JSON unmarshalling or logging utilities.

**Type assertions**

When you have a value stored as an interface, you sometimes need to get back to the concrete type. That's what a type assertion does:

```go
var v any = "hello"

s, ok := v.(string)
if ok {
    fmt.Println("it's a string:", s)
} else {
    fmt.Println("not a string")
}
```

Always use the two-value form `v.(T)` with `ok`. The single-value form `s := v.(string)` will panic at runtime if `v` isn't actually a string.

You can also use a **type switch** when you have multiple possible types:

```go
func describe(v any) {
    switch val := v.(type) {
    case int:
        fmt.Printf("integer: %d\n", val)
    case string:
        fmt.Printf("string: %q\n", val)
    default:
        fmt.Printf("unknown type: %T\n", val)
    }
}
```

## Try It Yourself

Build a small shape calculator using interfaces:

```go
package main

import (
    "fmt"
    "math"
)

type Shape interface {
    Area() float64
    Perimeter() float64
}

type Circle struct {
    Radius float64
}

type Rectangle struct {
    Width, Height float64
}

func (c Circle) Area() float64      { return math.Pi * c.Radius * c.Radius }
func (c Circle) Perimeter() float64 { return 2 * math.Pi * c.Radius }

func (r Rectangle) Area() float64      { return r.Width * r.Height }
func (r Rectangle) Perimeter() float64 { return 2 * (r.Width + r.Height) }

func printShape(s Shape) {
    fmt.Printf("Area: %.2f, Perimeter: %.2f\n", s.Area(), s.Perimeter())
}

func main() {
    printShape(Circle{Radius: 5})
    printShape(Rectangle{Width: 4, Height: 6})
}
```

Run this. Add a `Triangle` type. Watch how `printShape` keeps working without any changes — that's the power of interfaces.

## Common Mistakes

**Defining interfaces too early.** A lot of beginners define interfaces before they have even two implementations. Don't. Write the concrete types first. Extract an interface only when you actually need to swap implementations or write a test.

**Making interfaces too large.** The standard library's most-used interfaces have one or two methods. If your interface has eight methods, it'll be hard to satisfy and hard to mock. Break it up.

**Using `any` everywhere.** It feels flexible, but it defeats the purpose of a typed language. If you find yourself casting `any` to specific types in lots of places, the design needs rethinking.

**Forgetting the two-value type assertion.** This will panic at runtime with no useful message:

```go
// DANGEROUS — panics if v is not a string
s := v.(string)

// SAFE — check first
s, ok := v.(string)
```

## Key Takeaway

Interfaces in Go are about behaviour, not identity. Your type doesn't need to announce what it implements — it just needs to have the right methods. This keeps code loosely coupled and easy to test. The standard library's `io.Reader` and `io.Writer` are perfect examples: they're small, focused contracts that unlock enormous composability. Start with concrete types, extract interfaces when you need them, and keep them small.

---

**Series: Go from Scratch**

- Previous: [Lesson 10: Pointers](/post/go/go-scratch-pointers)
- Next: [Lesson 12: Error Handling](/post/go/go-scratch-errors)
