---
title: "Lesson 4: Functions — Small functions are Go's building blocks"
author: Atharva Pandey
series: "Go from Scratch"
lesson: 4
keywords: [Go, golang, beginner, functions, multiple return values, variadic, func, named returns]
tags: [Go tutorial, golang, beginner]
date: '2024-01-17T00:00:00.000Z'
---

If there's one thing experienced Go programmers agree on, it's this: write small functions. Not tiny, cryptic one-liners, but focused functions that do one thing and are easy to test. Go's function syntax encourages this style — it's clean, explicit, and has one feature that's genuinely different from most languages you've used before: multiple return values.

In Python you can return a tuple. In JavaScript you can return an array. But in Go, returning two or three values is a first-class language feature with its own syntax, and it fundamentally changes how error handling works. By the end of this lesson you'll understand Go functions well enough to start writing real, useful programs.

---

## The Basics

### Declaring a function

The syntax is `func name(parameters) returnType { body }`:

```go
package main

import "fmt"

func greet(name string) string {
    return "Hello, " + name + "!"
}

func main() {
    message := greet("Atharva")
    fmt.Println(message)
}
```

Let's unpack this:

- `func` is the keyword that starts a function declaration
- `greet` is the function name
- `(name string)` is the parameter list — parameter name comes first, then the type. This is the opposite of Java or C.
- `string` after the parentheses is the return type
- `return` sends a value back to the caller

If your function takes multiple parameters of the same type, you can list them together:

```go
func add(a, b int) int {
    return a + b
}
```

`a, b int` means both `a` and `b` are `int`. Much more concise than `(int a, int b)`.

### Functions with no return value

If a function doesn't return anything, just omit the return type entirely. No `void` keyword needed:

```go
package main

import "fmt"

func printSeparator() {
    fmt.Println("-------------------")
}

func main() {
    fmt.Println("Section 1")
    printSeparator()
    fmt.Println("Section 2")
    printSeparator()
}
```

### Multiple return values — Go's standout feature

This is where Go diverges from most languages. A function can return multiple values directly:

```go
package main

import (
    "errors"
    "fmt"
)

func divide(a, b float64) (float64, error) {
    if b == 0 {
        return 0, errors.New("cannot divide by zero")
    }
    return a / b, nil
}

func main() {
    result, err := divide(10, 3)
    if err != nil {
        fmt.Println("Error:", err)
        return
    }
    fmt.Printf("10 / 3 = %.2f\n", result)

    _, err = divide(5, 0)
    if err != nil {
        fmt.Println("Error:", err)
    }
}
```

The return type `(float64, error)` means this function returns two things: a number and an error. The caller gets both and must decide what to do. Notice `nil` — in Go, `nil` means "no value, no error". We return `nil` for the error when everything succeeds.

The pattern `result, err := someFunction()` is everywhere in Go. Check `err` immediately after calling any function that can fail. This is Go's approach to error handling — explicit, visible, impossible to accidentally ignore.

The `_` (blank identifier) on the second `divide` call discards the first return value when we don't need it.

### Named return values

You can give your return values names. They then act like pre-declared variables inside the function:

```go
package main

import "fmt"

func minMax(numbers []int) (min, max int) {
    min = numbers[0]
    max = numbers[0]

    for _, n := range numbers {
        if n < min {
            min = n
        }
        if n > max {
            max = n
        }
    }

    return // "naked return" — returns min and max as currently set
}

func main() {
    lo, hi := minMax([]int{3, 1, 9, 2, 7})
    fmt.Println("min:", lo, "max:", hi)
}
```

The `return` at the end with no values is called a *naked return* — it returns whatever the named return variables currently hold. Named returns are most useful when a function is short and the names make the signature clearer. For longer functions, explicit returns are usually better.

### Variadic functions — accepting any number of arguments

A *variadic* function accepts zero or more arguments of the same type. You mark the parameter with `...` before its type:

```go
package main

import "fmt"

func sum(numbers ...int) int {
    total := 0
    for _, n := range numbers {
        total += n
    }
    return total
}

func main() {
    fmt.Println(sum(1, 2, 3))          // 6
    fmt.Println(sum(10, 20, 30, 40))   // 100
    fmt.Println(sum())                  // 0
}
```

Inside the function, `numbers` is treated as a slice (we'll cover slices in the next lesson). If you already have a slice and want to pass it to a variadic function, use `...` when calling:

```go
nums := []int{1, 2, 3, 4, 5}
fmt.Println(sum(nums...))  // spreads the slice into individual arguments
```

### Functions as values

In Go, functions are *first-class values*. You can assign them to variables, pass them as arguments, and return them from other functions:

```go
package main

import "fmt"

func applyTwice(f func(int) int, x int) int {
    return f(f(x))
}

func double(n int) int {
    return n * 2
}

func main() {
    result := applyTwice(double, 3)
    fmt.Println(result) // double(double(3)) = double(6) = 12

    // You can also define a function inline (an "anonymous function")
    square := func(n int) int {
        return n * n
    }
    fmt.Println(applyTwice(square, 3)) // square(square(3)) = square(9) = 81
}
```

The type of a function is written as `func(paramTypes) returnTypes`. So `func(int) int` describes a function that takes one `int` and returns one `int`. This becomes powerful when you start working with higher-order patterns like callbacks and middleware.

---

## Try It Yourself

Write a function called `celsius` that converts a Fahrenheit temperature to Celsius. The formula is `(F - 32) * 5 / 9`. Then write another function called `tempDescription` that returns both the Celsius value and a string description ("freezing", "cold", "warm", "hot") based on the result:

```go
package main

import "fmt"

func celsius(f float64) float64 {
    return (f - 32) * 5 / 9
}

func tempDescription(f float64) (float64, string) {
    c := celsius(f)
    switch {
    case c <= 0:
        return c, "freezing"
    case c <= 10:
        return c, "cold"
    case c <= 25:
        return c, "warm"
    default:
        return c, "hot"
    }
}

func main() {
    c, desc := tempDescription(98.6)
    fmt.Printf("%.1f°C — %s\n", c, desc)
}
```

---

## Common Mistakes

**Ignoring return values**

Unlike some languages, if a function returns values, you generally can't just call it and discard everything silently. If a function returns an error, ignoring it means your program might continue in a broken state. Use `_` explicitly when you truly don't need a value — it signals intent.

**Putting the type before the parameter name**

If you're used to Java or C, you might write `func greet(string name)` by instinct. Go is the opposite: `func greet(name string)`. The name comes first.

**Trying to use naked returns in long functions**

Naked returns (returning named variables without listing them) are legal but make long functions hard to follow. A reader has to remember what the named return variables are and trace their values back through the code. Use explicit returns in functions longer than a few lines.

**Forgetting that variadic params are a slice**

Inside a variadic function, the parameter is a slice, so you iterate it with `range` or access elements by index. You can't use it directly as a single value.

---

## Key Takeaway

Go functions are declared with `func`, take named parameters, and return typed values. The standout feature is multiple return values — used everywhere for returning a result alongside an error. Variadic functions (`...type`) accept any number of arguments of one type. Functions are first-class values you can assign and pass around. Together these features give you powerful, readable building blocks: small functions that do one thing, are easy to test, and compose cleanly into larger programs.

---

*[← Previous: Lesson 3 — Control Flow](/post/go/go-scratch-control-flow) | [Course Index: Go from Scratch](/series/go-from-scratch) | [Next: Lesson 5 — Arrays and Slices →](/post/go/go-scratch-arrays-slices)*
