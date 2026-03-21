---
title: "Lesson 2: Variables and Types — Go is typed, and that's a good thing"
author: Atharva Pandey
series: "Go from Scratch"
lesson: 2
keywords: [Go, golang, beginner, variables, types, var, type inference, constants, zero values]
tags: [Go tutorial, golang, beginner]
date: '2024-01-08T00:00:00.000Z'
---

If you're coming from Python or JavaScript, Go's type system might feel like extra work at first. Why should you have to tell the compiler that a variable holds a number? Can't it just figure it out?

Here's the thing — it usually can. Go has type inference, so you rarely have to write out types explicitly. But when types *are* written out, every person reading your code knows exactly what kind of data they're dealing with. There's no guessing, no surprises at runtime because a string snuck in where you expected a number. Go catches those mistakes at compile time, before your code ever runs.

This lesson covers how to declare variables, what types are available, and a few Go-specific ideas — like zero values — that will save you from bugs you didn't even know you were about to write.

---

## The Basics

### Declaring a variable with `var`

The explicit way to declare a variable in Go is with the `var` keyword:

```go
package main

import "fmt"

func main() {
    var name string = "Atharva"
    var age  int    = 28
    var score float64 = 98.5
    var active bool = true

    fmt.Println(name, age, score, active)
}
```

The format is: `var variableName type = value`.

Go's four basic types you'll use constantly:
- `string` — text, always in double quotes
- `int` — whole numbers (1, -42, 1000000)
- `float64` — decimal numbers (3.14, -0.5)
- `bool` — true or false, nothing else

### The short declaration `:=`

Writing `var name string = "Atharva"` is verbose. Go gives you a shorthand that infers the type from the value:

```go
package main

import "fmt"

func main() {
    name   := "Atharva"   // Go infers: string
    age    := 28           // Go infers: int
    score  := 98.5         // Go infers: float64
    active := true         // Go infers: bool

    fmt.Println(name, age, score, active)
}
```

`:=` means "declare and assign". Go looks at the value on the right side, figures out the type, and creates the variable. This is the form you'll use most often inside functions.

One important rule: `:=` only works *inside* a function. At the package level (outside any function), you must use `var`.

### Zero values — Go's hidden safety net

In some languages, an uninitialized variable contains garbage — whatever happened to be in that memory location. Go doesn't do that. If you declare a variable without giving it a value, Go automatically sets it to its *zero value*:

```go
package main

import "fmt"

func main() {
    var name   string   // zero value: ""  (empty string)
    var count  int      // zero value: 0
    var ratio  float64  // zero value: 0.0
    var active bool     // zero value: false

    fmt.Printf("name=%q, count=%d, ratio=%f, active=%t\n",
        name, count, ratio, active)
}
```

Output: `name="", count=0, ratio=0.000000, active=false`

This is a deliberate design choice. Zero values make Go programs more predictable — you always know what you're starting with. No undefined behavior, no accidental nil pointer dereferences from forgetting to initialize.

### Type inference doesn't mean no types

Even though Go infers types, the types are still there and they're fixed. Once a variable has a type, it keeps that type forever. You can't do this:

```go
count := 5
count = "hello"  // compile error: cannot use "hello" (type string) as type int
```

Compare this to Python or JavaScript where a variable can hold anything at any time. Go's approach means the compiler can catch entire categories of bugs before your program runs.

### Multiple assignment

You can declare and assign multiple variables at once:

```go
package main

import "fmt"

func main() {
    x, y := 10, 20
    fmt.Println(x, y)

    // Swap two variables — a neat Go trick
    x, y = y, x
    fmt.Println(x, y) // 20 10
}
```

This is especially useful when functions return multiple values, which we'll cover in Lesson 4.

### Constants — values that never change

Use `const` when you have a value that should never be reassigned. The classic examples are configuration values, mathematical constants, or status codes:

```go
package main

import "fmt"

const Pi = 3.14159
const AppName = "MyApp"
const MaxRetries = 3

func main() {
    fmt.Println("App:", AppName)
    fmt.Println("Pi:", Pi)
    fmt.Println("Max retries:", MaxRetries)
}
```

Constants in Go are evaluated at compile time. You cannot assign a variable to a constant — the value must be a literal or an expression made up of other constants. Trying to reassign a constant is a compile error.

---

## Try It Yourself

Write a small program that calculates the area of a rectangle:

```go
package main

import "fmt"

func main() {
    width  := 10.0
    height := 5.5

    area := width * height

    fmt.Println("Width:", width)
    fmt.Println("Height:", height)
    fmt.Println("Area:", area)
}
```

Run it. Then try changing `width` to an `int` by declaring it as `var width int = 10`. What happens when you multiply an `int` by a `float64`? (Spoiler: Go won't allow it. You'd need to convert one of them — `float64(width) * height` — but we'll dig into type conversions soon.)

---

## Common Mistakes

**Using `:=` outside a function**

If you write `name := "Atharva"` at the top level of a file (outside any function), Go will error: "non-declaration statement outside function body". Use `var name = "Atharva"` there instead.

**Declaring a variable and never using it**

Like unused imports, unused variables are a compile error in Go. If you write `x := 5` and never read `x`, Go refuses to build. This forces you to keep code clean. If you genuinely need a throwaway variable (common with multiple return values), use the blank identifier `_`:

```go
result, _ := someFunction() // discard the second return value
```

**Confusing `=` and `:=`**

`:=` declares a new variable. `=` assigns to an existing one. Using `:=` on an already-declared variable will give you "no new variables on left side of :=" if there's nothing new on the left. Using `=` on an undeclared variable will give you "undefined". When in doubt: first time you use a variable, use `:=`. After that, use `=`.

**Assuming float when Go infers int**

When you write `x := 5`, Go infers `int`, not `float64`. If you later need decimal precision, you have to be explicit: `x := 5.0` or `var x float64 = 5`.

---

## Key Takeaway

Go gives you two ways to declare variables: `var` (explicit) and `:=` (short, inferred). Either way, the type is set at declaration and never changes. Zero values mean every variable starts in a known, safe state — no garbage, no surprises. Constants are for values that should never change. These rules together make Go code predictable: when you read a variable, you always know what type it is and that it was initialized deliberately.

---

*[← Previous: Lesson 1 — Your First Go Program](/post/go/go-scratch-first-program) | [Course Index: Go from Scratch](/series/go-from-scratch) | [Next: Lesson 3 — Control Flow →](/post/go/go-scratch-control-flow)*
