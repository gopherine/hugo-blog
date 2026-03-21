---
title: "Lesson 12: Error Handling — Errors are values, not exceptions"
author: "Atharva Pandey"
keywords:
  - "Go"
  - "golang"
  - "error handling"
  - "error interface"
  - "fmt.Errorf"
  - "custom errors"
  - "Go from Scratch"
tags:
  - "Go tutorial"
  - "golang"
  - "beginner"
series: "Go from Scratch"
lesson: 12
date: "2024-03-06"
---

The first time I wrote Go after years of Python and JavaScript, I kept waiting for the `try/catch` block. It never came. Instead, almost every function returned two values: the result, and an error. I had to check the error every single time. It felt tedious at first. After a few weeks, I realised it was one of the best design decisions in the language.

In Go, errors are just values. They're not special. They don't teleport up the call stack. They sit right there, in your return value, waiting for you to deal with them.

## The Basics

The `error` type in Go is actually an interface. It lives in the standard library and looks like this:

```go
type error interface {
    Error() string
}
```

Any type that has an `Error() string` method is an error. That's all it takes. When a function can fail, it returns an `error` as its last return value. When everything went fine, it returns `nil`.

```go
func divide(a, b float64) (float64, error) {
    if b == 0 {
        return 0, fmt.Errorf("cannot divide by zero")
    }
    return a / b, nil
}

func main() {
    result, err := divide(10, 2)
    if err != nil {
        fmt.Println("error:", err)
        return
    }
    fmt.Println(result) // 5
}
```

The pattern `if err != nil` is everywhere in Go. You'll type it thousands of times. It forces you to think about failure at the exact point where it can happen, not in a distant catch block.

**Wrapping errors with %w**

When an error travels up through multiple function calls, you want to add context at each layer without losing the original error. `fmt.Errorf` with `%w` does this:

```go
func readConfig(path string) ([]byte, error) {
    data, err := os.ReadFile(path)
    if err != nil {
        return nil, fmt.Errorf("readConfig: %w", err)
    }
    return data, nil
}
```

Now if `readConfig` fails, the error message will say `readConfig: open config.json: no such file or directory`. You know exactly where things went wrong and why.

The `%w` verb also lets callers unwrap the error later using `errors.Is` and `errors.As`, which I cover more in the intermediate series. For now, just know that `%w` preserves the original error inside the new one.

**Creating custom errors**

Sometimes a string isn't enough. You want a structured error that callers can inspect. The simplest approach is a struct with an `Error() string` method:

```go
type ValidationError struct {
    Field   string
    Message string
}

func (e *ValidationError) Error() string {
    return fmt.Sprintf("validation failed on %s: %s", e.Field, e.Message)
}

func validateAge(age int) error {
    if age < 0 {
        return &ValidationError{Field: "age", Message: "must be non-negative"}
    }
    return nil
}

func main() {
    err := validateAge(-5)
    if err != nil {
        fmt.Println(err) // validation failed on age: must be non-negative
    }
}
```

A caller who needs the field name can do a type assertion: `if ve, ok := err.(*ValidationError); ok { ... }`. A caller who just needs the message can treat it like any other error. Both are satisfied.

**Sentinel errors**

For errors you want to check for by identity, you can create package-level error variables:

```go
var ErrNotFound = fmt.Errorf("not found")

func findUser(id int) (string, error) {
    if id != 1 {
        return "", ErrNotFound
    }
    return "Atharva", nil
}

func main() {
    _, err := findUser(99)
    if err == ErrNotFound {
        fmt.Println("user does not exist")
    }
}
```

These are called sentinel errors. The standard library uses them too — `io.EOF` is a sentinel error that signals the end of a stream.

## Try It Yourself

Write a small file-reading function that wraps errors at each layer:

```go
package main

import (
    "errors"
    "fmt"
    "os"
)

var ErrEmpty = errors.New("file is empty")

func readFile(path string) (string, error) {
    data, err := os.ReadFile(path)
    if err != nil {
        return "", fmt.Errorf("readFile %s: %w", path, err)
    }
    if len(data) == 0 {
        return "", fmt.Errorf("readFile %s: %w", path, ErrEmpty)
    }
    return string(data), nil
}

func main() {
    content, err := readFile("notes.txt")
    if err != nil {
        if errors.Is(err, ErrEmpty) {
            fmt.Println("file exists but has no content")
        } else {
            fmt.Println("error:", err)
        }
        return
    }
    fmt.Println(content)
}
```

Create `notes.txt`, leave it empty, run the program. Then delete it and run again. Watch how the error messages differ.

## Common Mistakes

**Ignoring errors with `_`.** This is the most dangerous habit in Go:

```go
// WRONG — if Open fails, f is nil and the next call panics
f, _ := os.Open("data.txt")
data, _ := io.ReadAll(f)
```

Every error deserves a check. Yes, every single one.

**Using `panic` for normal errors.** `panic` is for truly unexpected situations — bugs in your own code, impossible states. It's not a replacement for `return err`. If you find yourself writing `panic(err)` for things like file-not-found, stop. Return the error instead.

**Not adding context when wrapping.** `return fmt.Errorf("%w", err)` is no better than `return err`. Always add a message that describes what you were trying to do:

```go
// USELESS wrapping
return fmt.Errorf("%w", err)

// GOOD wrapping
return fmt.Errorf("loading user profile for id %d: %w", id, err)
```

**Returning both a result and an error when only one makes sense.** If your function returns an error, the caller should not use the first return value. Make this clear in your code:

```go
// When there's an error, return the zero value for the first return
return 0, fmt.Errorf("something went wrong")
// Not:
return somePartialResult, fmt.Errorf("something went wrong")
```

## Key Takeaway

Go doesn't have exceptions because exceptions hide control flow. With `if err != nil`, you see exactly where your program can fail and what it does about it. It's more typing, but it produces more honest code. When you add context with `fmt.Errorf("%w", ...)`, error messages become a trail of breadcrumbs that lead you straight to the source of any problem. Embrace the pattern — it pays off.

---

**Series: Go from Scratch**

- Previous: [Lesson 11: Interfaces](/post/go/go-scratch-interfaces)
- Next: [Lesson 13: Goroutines](/post/go/go-scratch-goroutines)
