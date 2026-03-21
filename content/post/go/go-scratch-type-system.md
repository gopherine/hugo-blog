---
title: "Lesson 23: The Type System — Types, aliases, conversions, and embedding"
author: "Atharva Pandey"
series: "Go from Scratch"
lesson: 23
keywords: ["Go", "golang", "type system", "type definition", "type alias", "type conversion", "embedding", "type switch", "beginner"]
tags: ["Go tutorial", "golang", "beginner"]
date: "2024-05-02T00:00:00.000Z"
---

When I first came to Go from Python, the type system felt like a lot of ceremony. Why can't I just use an `int` where a `float64` is expected? Why do I need to define a whole new type just to give an integer more meaning? The answers to those questions turned out to be one of the things I now appreciate most about Go. Types aren't bureaucracy — they're a way of making your code explain itself.

This lesson covers the features of Go's type system that come up constantly in real code: defining your own types, the difference between a type definition and an alias, explicit conversions, embedding structs, and type switches.

---

## The Basics

### Type definitions vs type aliases

A *type definition* creates a brand new type based on an existing one:

```go
package main

import "fmt"

type Celsius float64
type Fahrenheit float64

func main() {
    boiling := Celsius(100)
    freezing := Fahrenheit(32)

    fmt.Println(boiling)   // 100
    fmt.Println(freezing)  // 32

    // This would NOT compile:
    // var x Celsius = freezing
}
```

`Celsius` and `Fahrenheit` are both backed by `float64`, but they are different types. You cannot accidentally assign one to the other — Go's compiler will catch it. This is brilliant for preventing bugs where you mix up units, IDs, or other quantities that happen to share the same underlying type.

A *type alias*, on the other hand, is just another name for the exact same type:

```go
type MyFloat = float64  // alias, not a new type
```

With an alias, `MyFloat` and `float64` are completely interchangeable — the compiler treats them as identical. Aliases exist mainly for large-scale refactoring (renaming a type across a codebase gradually) or for API compatibility. In everyday code, you'll almost always want a type definition, not an alias.

### Explicit type conversions

Go never converts between types automatically. If you have a `Celsius` and need a `float64`, you must say so explicitly:

```go
package main

import "fmt"

type Celsius float64

func toFahrenheit(c Celsius) float64 {
    return float64(c)*9/5 + 32
}

func main() {
    temp := Celsius(37)
    fmt.Printf("%.1f°C is %.1f°F\n", float64(temp), toFahrenheit(temp))
    // Output: 37.0°C is 98.6°F
}
```

The conversion syntax is just the target type used like a function: `float64(c)`. This works between numeric types, and between a named type and its underlying type. It does not work between completely unrelated types — you can't convert a string to an int with this syntax (you'd use `strconv` for that).

This explicitness is deliberate. It forces you to acknowledge every time a type boundary is crossed, which prevents silent precision loss and accidental mixing of incompatible values.

### Embedding structs

Go doesn't have class inheritance, but it has something arguably better: *embedding*. You can embed one struct inside another, and the outer struct automatically gains all the fields and methods of the inner one.

```go
package main

import "fmt"

type Animal struct {
    Name string
}

func (a Animal) Speak() string {
    return a.Name + " makes a sound."
}

type Dog struct {
    Animal        // embedded — not a field name, just the type
    Breed string
}

func main() {
    d := Dog{
        Animal: Animal{Name: "Rex"},
        Breed:  "Labrador",
    }

    fmt.Println(d.Name)    // promoted from Animal
    fmt.Println(d.Speak()) // promoted method from Animal
    fmt.Println(d.Breed)
}
```

The fields and methods of `Animal` are *promoted* to `Dog`. You can access `d.Name` directly even though `Name` is defined on `Animal`. This is called promotion. `Dog` doesn't inherit from `Animal` — it contains an `Animal` and re-exposes its surface. The difference matters when `Dog` defines its own `Speak` method: it shadows the embedded one, it doesn't override it in an inheritance sense.

Embedding is the Go way of sharing behaviour without the complexity of class hierarchies.

### Type switches

When you have an interface value and need to handle different concrete types differently, a type switch is the clearest way to do it:

```go
package main

import "fmt"

func describe(i interface{}) {
    switch v := i.(type) {
    case int:
        fmt.Printf("Integer: %d\n", v)
    case string:
        fmt.Printf("String: %q (length %d)\n", v, len(v))
    case bool:
        fmt.Printf("Boolean: %t\n", v)
    default:
        fmt.Printf("Unknown type: %T\n", v)
    }
}

func main() {
    describe(42)
    describe("hello")
    describe(true)
    describe(3.14)
}
```

The syntax `v := i.(type)` is special — it's only valid inside a `switch`. In each `case`, `v` is automatically the correct concrete type, so inside `case int` you can use `v` as an `int` without any extra assertion. The `default` branch catches anything you didn't explicitly handle. `%T` prints the type name, which is handy for debugging.

---

## Try It Yourself

**Exercise 1:** Define a type `Meters` based on `float64` and a type `Feet` based on `float64`. Write a function `metersToFeet(m Meters) Feet` that does the conversion (1 metre = 3.28084 feet). Make sure the function cannot accept a raw `float64` — the type system should enforce that.

**Exercise 2:** Create a `Shape` interface with a single method `Area() float64`. Define `Circle` and `Rectangle` structs that implement it. Write a function that accepts an `interface{}` and uses a type switch to call `Area()` if it's a shape, or prints "not a shape" otherwise.

**Exercise 3:** Embed a `Timestamp` struct (with a single `CreatedAt time.Time` field) into a `BlogPost` struct. Construct a `BlogPost` and print its `CreatedAt` field directly (using promotion) without going through `.Timestamp.CreatedAt`.

---

## Common Mistakes

**Thinking a type alias and a type definition are the same**

`type MyInt int` creates a new type. `type MyInt = int` creates an alias. The former is a distinct type; the latter is just a second name for `int`. Trying to use a new type where the underlying type is expected (or vice versa) without explicit conversion will fail to compile. With an alias it won't — they're identical.

**Expecting automatic numeric conversions**

Coming from Python or JavaScript, you might expect `int + float64` to just work. It doesn't in Go. You must write `float64(myInt) + myFloat`. This is intentional and catches a class of bugs that are surprisingly common in dynamically typed languages.

**Accessing an embedded field when there's a name collision**

If both the outer and inner struct have a field called `Name`, the outer one wins for `d.Name`. To get the inner one you must be explicit: `d.Animal.Name`. Go doesn't error on this; it just silently picks the outermost one. Being aware of this prevents confusing bugs when embedding structs that have fields in common.

**Using interface{} everywhere instead of defined types**

A type switch on `interface{}` is useful in specific contexts (generic utilities, logging, deserialization). But if you find yourself writing type switches all over your business logic, it's a sign you should define proper types or interfaces instead. The type system is there to help you, not to be worked around.

---

## Key Takeaway

Go's type system is strict by design. Type definitions create genuinely new types that the compiler treats as distinct from their underlying type — you must convert explicitly to cross that boundary. Embedding lets structs share behaviour through composition without inheritance. Type switches let you safely handle interface values of different concrete types. The common thread through all of this is that Go wants every type boundary to be visible in the source code, so that a future reader — including future you — knows exactly what is happening without having to trace through implicit conversions.

---

*[← Previous: Lesson 22 — File I/O](/post/go/go-scratch-files) | [Course Index: Go from Scratch](/series/go-from-scratch) | [Next: Lesson 24 — The Go Toolchain →](/post/go/go-scratch-toolchain)*
