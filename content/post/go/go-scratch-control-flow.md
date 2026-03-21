---
title: "Lesson 3: Control Flow — if, for, and switch are all you need"
author: Atharva Pandey
series: "Go from Scratch"
lesson: 3
keywords: [Go, golang, beginner, control flow, if else, for loop, switch, break, continue]
tags: [Go tutorial, golang, beginner]
date: '2024-01-12T00:00:00.000Z'
---

Every program you'll ever write needs to make decisions and repeat actions. "If this condition is true, do that. Otherwise, do something else." "Keep doing this until we run out of items." These are the fundamental building blocks of logic, and Go handles them with just three constructs: `if`, `for`, and `switch`.

That's it. No `while`. No `do-while`. No `foreach` as a separate keyword. Go's designers made a deliberate choice to keep the language small — fewer constructs means fewer ways to do the same thing, which means code is more consistent and easier to read across different projects and teams.

If you're coming from JavaScript or Java, this might feel like something was left out. Stick with it. You'll find that `for` alone covers every looping case you'll ever encounter.

---

## The Basics

### `if` / `else`

The basic structure should look familiar:

```go
package main

import "fmt"

func main() {
    age := 20

    if age >= 18 {
        fmt.Println("You can vote.")
    } else {
        fmt.Println("You cannot vote yet.")
    }
}
```

A couple of things to note right away:

1. No parentheses around the condition. You write `if age >= 18`, not `if (age >= 18)`. Parentheses are allowed but considered bad style.
2. The opening curly brace `{` must be on the same line as the `if`. Go's formatter enforces this.

You can chain conditions with `else if`:

```go
package main

import "fmt"

func main() {
    score := 75

    if score >= 90 {
        fmt.Println("Grade: A")
    } else if score >= 70 {
        fmt.Println("Grade: B")
    } else if score >= 50 {
        fmt.Println("Grade: C")
    } else {
        fmt.Println("Grade: F")
    }
}
```

### The `if` init statement — a Go-specific pattern

Go lets you run a short statement right before the condition check, and any variable declared there is scoped to the `if` block:

```go
package main

import (
    "fmt"
    "strconv"
)

func main() {
    input := "42"

    if num, err := strconv.Atoi(input); err == nil {
        fmt.Println("Parsed number:", num)
    } else {
        fmt.Println("Error:", err)
    }
    // num and err are not accessible here
}
```

`strconv.Atoi` converts a string to an integer and returns two values: the result and an error (if something went wrong). This pattern — short statement + condition — is very common in Go error handling. Don't worry about `err` too much right now; we'll cover errors in detail later.

### `for` — the only loop

Go has exactly one looping keyword: `for`. But it's flexible enough to replace everything you might reach for in other languages.

**Basic loop with a counter** (like a `for` loop in C/Java):

```go
package main

import "fmt"

func main() {
    for i := 0; i < 5; i++ {
        fmt.Println("i is", i)
    }
}
```

Three parts separated by semicolons: `init; condition; post`. The `init` runs once before the loop. The `condition` is checked before each iteration — if false, the loop stops. The `post` runs after each iteration.

**Loop until a condition** (like a `while` loop):

```go
package main

import "fmt"

func main() {
    count := 0

    for count < 5 {
        fmt.Println("count is", count)
        count++
    }
}
```

Drop the init and post parts, keep just the condition — and you have a while loop. Same keyword, different form.

**Infinite loop** (runs until you break out of it):

```go
package main

import "fmt"

func main() {
    attempts := 0

    for {
        attempts++
        fmt.Println("attempt", attempts)

        if attempts >= 3 {
            fmt.Println("Done.")
            break
        }
    }
}
```

`for` with no condition runs forever. `break` exits the loop immediately. `continue` skips the rest of the current iteration and moves to the next.

### `switch`

`switch` in Go is cleaner than in C or Java. The biggest difference: cases don't fall through by default. You don't need `break` at the end of every case.

```go
package main

import "fmt"

func main() {
    day := "Tuesday"

    switch day {
    case "Monday", "Tuesday", "Wednesday", "Thursday", "Friday":
        fmt.Println("Weekday — back to work.")
    case "Saturday", "Sunday":
        fmt.Println("Weekend — rest up.")
    default:
        fmt.Println("Not a real day.")
    }
}
```

Notice you can put multiple values in one case by separating them with commas. The `default` case runs when nothing else matches — it's optional.

You can also write a `switch` without an expression, which acts like a chain of `if-else` conditions:

```go
package main

import "fmt"

func main() {
    score := 82

    switch {
    case score >= 90:
        fmt.Println("Excellent")
    case score >= 70:
        fmt.Println("Good")
    case score >= 50:
        fmt.Println("Passing")
    default:
        fmt.Println("Needs improvement")
    }
}
```

This is cleaner than a long `if-else if-else if` chain when you have more than three or four conditions.

---

## Try It Yourself

Write a FizzBuzz program — a classic programming exercise. For each number from 1 to 20:
- If the number is divisible by both 3 and 5, print "FizzBuzz"
- If divisible by 3, print "Fizz"
- If divisible by 5, print "Buzz"
- Otherwise, print the number

The `%` operator gives you the remainder of a division: `10 % 3` is `1`, `9 % 3` is `0`.

```go
package main

import "fmt"

func main() {
    for i := 1; i <= 20; i++ {
        if i%15 == 0 {
            fmt.Println("FizzBuzz")
        } else if i%3 == 0 {
            fmt.Println("Fizz")
        } else if i%5 == 0 {
            fmt.Println("Buzz")
        } else {
            fmt.Println(i)
        }
    }
}
```

Once you have it working, try rewriting the inner logic using `switch` instead of `if-else`.

---

## Common Mistakes

**Putting a curly brace on its own line**

This is a syntax error in Go:

```go
if x > 0
{  // Error: unexpected newline, expecting { after if clause
    fmt.Println("positive")
}
```

The opening `{` must always be on the same line as the `if`, `for`, or `switch`. Go's parser requires it, and `gofmt` (the standard formatter) enforces it.

**Expecting fallthrough in `switch`**

Coming from C or Java, you might expect `switch` cases to fall through automatically if you don't `break`. In Go, each case stops automatically. If you explicitly *want* fallthrough, you can write the `fallthrough` keyword — but it's rare and usually a sign to restructure your code.

**Forgetting `break` vs `continue`**

`break` exits the loop entirely. `continue` skips to the next iteration. These are easy to mix up when you're tired. If your loop isn't behaving as expected, check which one you used.

**Using `==` vs `=` in conditions**

`=` is assignment. `==` is comparison. Writing `if x = 5` instead of `if x == 5` is a compile error in Go (unlike C, where it silently works and causes bugs). The compiler has your back here.

---

## Key Takeaway

Go's control flow is intentionally minimal: `if` for decisions, `for` for all looping scenarios, and `switch` for multi-branch comparisons. There is no `while`, no `do-while`, no `foreach` — `for` adapts to cover all of them. Switch cases don't fall through by default, which eliminates an entire class of bugs common in other languages. Once you internalize these three constructs, you have everything you need to express any logical flow in Go.

---

*[← Previous: Lesson 2 — Variables and Types](/post/go/go-scratch-variables-types) | [Course Index: Go from Scratch](/series/go-from-scratch) | [Next: Lesson 4 — Functions →](/post/go/go-scratch-functions)*
