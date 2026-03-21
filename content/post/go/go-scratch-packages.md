---
title: "Lesson 16: Packages and Imports — Organizing code like a professional"
author: Atharva Pandey
series: "Go from Scratch"
lesson: 16
keywords: [Go, golang, beginner, packages, imports, exported, unexported, init function, package declaration]
tags: [Go tutorial, golang, beginner]
date: '2024-03-27T00:00:00.000Z'
---

Every Go program I've written has taught me the same lesson over and over: good code isn't just about what you write inside functions — it's about how you organize those functions in the first place. When I started with Go, I crammed everything into one file. It worked, until it didn't. By the time I had a few hundred lines, I couldn't find anything. That's when packages stopped being an abstract concept and became something I genuinely needed.

Packages are Go's answer to the question "how do we keep code manageable?" They let you split your program into logical units, reuse code across projects, and share functionality with the world. In this lesson, we'll go from the very basics — what a package declaration actually means — all the way to creating your own packages and avoiding a couple of traps that trip up most beginners.

---

## The Basics

### Package declarations

Every single Go file must start with a `package` declaration. You've been writing `package main` since lesson one. `main` is a special package name that tells Go "this is the entry point of an executable." Any other name creates a *library package* — code that other packages can import and use, but can't be run directly.

```go
// In file greet/greet.go
package greet
```

The package name should be short, lowercase, and descriptive. Conventionally, it matches the last segment of the directory path containing the file. If your file lives at `myapp/greet/greet.go`, the package is called `greet`.

### Import paths

When you import a package, you write its *import path* — which is the directory path relative to your module root (we'll cover modules in the next lesson), or an absolute path for the standard library.

```go
import (
    "fmt"           // standard library — short name
    "math/rand"     // standard library — nested
    "os"
)
```

The name you use to call functions from a package is the *package name* declared at the top of that package's files, not the last segment of the path (though they're almost always the same). `math/rand` gives you `rand.Intn(...)`, not `math/rand.Intn(...)`.

You can give an import an alias if there's a conflict or if the default name is awkward:

```go
import (
    "fmt"
    mrand "math/rand"   // alias: now use mrand.Intn(...)
    crand "crypto/rand" // both packages are called "rand" — aliases fix the clash
)
```

### Exported vs unexported: the capital letter rule

This is one of Go's most elegant rules, and it took me embarrassingly long to really internalize it.

**If a name starts with a capital letter, it's exported — visible outside the package. If it starts with a lowercase letter, it's unexported — private to the package.**

That's it. No `public`, `private`, or `protected` keywords. One rule, applied consistently to functions, types, variables, and struct fields.

```go
package greet

// Exported — other packages can call this
func Hello(name string) string {
    return buildMessage(name) // calls an unexported helper
}

// Unexported — only code inside the greet package can call this
func buildMessage(name string) string {
    return "Hello, " + name + "!"
}
```

If you try to call `greet.buildMessage("Alice")` from outside the package, the compiler will refuse with an error. This isn't a runtime check — it's caught at compile time, which means zero surprises in production.

### Creating your own package

Let me walk you through creating a small `mathutil` package from scratch.

```
myapp/
├── go.mod
├── main.go
└── mathutil/
    └── mathutil.go
```

```go
// mathutil/mathutil.go
package mathutil

// Abs returns the absolute value of n.
func Abs(n int) int {
    if n < 0 {
        return -n
    }
    return n
}

// clamp is unexported — internal helper only
func clamp(n, min, max int) int {
    if n < min {
        return min
    }
    if n > max {
        return max
    }
    return n
}

// Clamp returns n clamped to [min, max]. Exported wrapper around the helper.
func Clamp(n, min, max int) int {
    return clamp(n, min, max)
}
```

```go
// main.go
package main

import (
    "fmt"
    "myapp/mathutil"
)

func main() {
    fmt.Println(mathutil.Abs(-7))      // 7
    fmt.Println(mathutil.Clamp(15, 0, 10)) // 10
}
```

The import path `"myapp/mathutil"` assumes your module is named `myapp`. Again, we'll cover that in the next lesson — for now, just notice that the import path matches the directory structure.

### The `init()` function

Each package (and each file within a package) can declare an `init()` function. Go calls it automatically before `main()` runs, after all variable declarations in the package have been evaluated. You can't call `init()` yourself.

```go
package greet

var defaultGreeting string

func init() {
    defaultGreeting = "Hello"
}

func Greet(name string) string {
    return defaultGreeting + ", " + name + "!"
}
```

`init()` is useful for one-time setup: registering drivers, validating configuration, seeding state. But don't overuse it. If you find yourself writing complex logic in `init()`, it's usually a sign that you should be doing that setup explicitly in `main()` where it's visible and testable. Hidden initialization causes hidden bugs.

### Dot imports — and why you shouldn't use them

Go lets you do this:

```go
import . "fmt"

// Now you can call Println directly without the fmt. prefix
Println("hello")
```

This is called a *dot import*. It dumps all exported names from `fmt` directly into your current file's namespace. I'm telling you about it only so you recognise it if you see it — you should almost never write it yourself. It makes code harder to read because you can't tell where a function comes from without checking the imports. The only place dot imports are acceptable is in test files for DSL-style test helpers, and even then, sparingly.

---

## Try It Yourself

Create a folder called `converter` inside your project and add a file `converter.go` with `package converter`. Write two exported functions: `CelsiusToFahrenheit(c float64) float64` and `FahrenheitToCelsius(f float64) float64`. Then import and use both functions from `main.go`.

As a bonus, add an unexported helper `roundToTwo(f float64) float64` that both functions use internally. Verify that calling `converter.roundToTwo(...)` from `main.go` gives you a compile error.

---

## Common Mistakes

**Package name doesn't match directory name**

Your directory is called `mathutil` but the file says `package utils`. This compiles, but it's deeply confusing. Stick to the convention: package name matches directory name.

**Importing a package but not using it**

Go will refuse to compile if you import something you don't use. The fix is simply to delete the import. If you need a package's `init()` side effects without using its exported names, use a blank import: `import _ "some/package"`.

**Treating unexported as "less important"**

Unexported doesn't mean "messy internal stuff." Your unexported functions should be just as clean, well-named, and documented as exported ones. The only difference is visibility.

**Circular imports**

If package A imports package B and package B imports package A, Go will refuse to compile. The fix is to restructure: extract the shared types into a third package that both A and B import, or merge A and B if they're too tightly coupled to be separate.

---

## Key Takeaway

Packages are the primary unit of code organization in Go. Every file declares its package, every name is either exported (capital letter) or unexported (lowercase), and imports are explicit. Creating your own packages is as simple as creating a directory with `.go` files. Keep `init()` simple, avoid dot imports, and match your package name to your directory name. This discipline pays off enormously as your project grows.

---

*[Course Index: Go from Scratch](/series/go-from-scratch) | [← Lesson 15: Select](/post/go/go-scratch-select) | [Lesson 17: Go Modules →](/post/go/go-scratch-modules)*
