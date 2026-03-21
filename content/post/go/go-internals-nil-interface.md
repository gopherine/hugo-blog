---
title: "Lesson 2: Nil Interface Gotchas — nil is not nil when it has a type"
author: Atharva Pandey
series: "Go Memory Model & Internals"
lesson: 2
keywords: [Go, golang, internals, memory]
tags: [Go tutorial, golang, internals]
date: '2024-07-18T00:00:00.000Z'
---

A few months into my first production Go service, I had a bug that cost me two hours. A function returned an `error`, I checked `if err != nil` and it passed — but then further down the stack the program panicked trying to call `.Error()` on a nil pointer. The function had returned a nil `*MyError`, not a nil `error`. These are not the same thing, and until you internalize why, you will hit this bug.

## The Problem

From Lesson 1, we know an interface value is two words: a type pointer and a data pointer. An interface is only `nil` when **both** words are nil. The moment you assign a typed nil pointer to an interface variable, the type word becomes non-nil — and suddenly your nil-looking value is not nil.

This is one of Go's most notorious beginner traps, but it also catches experienced developers. It's not a language bug; it's a consequence of the interface representation being two separate pieces of information. Understanding this deeply means you'll never be confused by it again.

Here's the minimal reproduction:

```go
package main

import "fmt"

type MyError struct {
    Code    int
    Message string
}

func (e *MyError) Error() string {
    return fmt.Sprintf("error %d: %s", e.Code, e.Message)
}

// BUG: this function has a subtle problem
func doSomething(fail bool) error {
    var err *MyError // typed nil pointer — *MyError, value is nil
    if fail {
        err = &MyError{Code: 500, Message: "internal error"}
    }
    return err // returning a typed nil!
}

func main() {
    err := doSomething(false)

    // This check FAILS — err is not nil!
    if err != nil {
        fmt.Println("got error:", err) // prints: "got error: error 0: "
    } else {
        fmt.Println("no error")
    }
}
```

The function returns `nil` conceptually — no error occurred. But what it actually returns to the caller is an interface value where the type word is `*MyError` and the data word is `nil`. The `!= nil` check looks at whether either word is non-nil. The type word is non-nil, so the check returns `true`.

## The Idiomatic Way

The fix is simple: **return the interface type directly, not a concrete typed nil**.

```go
package main

import "fmt"

type MyError struct {
    Code    int
    Message string
}

func (e *MyError) Error() string {
    return fmt.Sprintf("error %d: %s", e.Code, e.Message)
}

// CORRECT: return error interface, not *MyError
func doSomething(fail bool) error {
    if fail {
        return &MyError{Code: 500, Message: "internal error"}
    }
    return nil // this is a true interface nil — both words are zero
}

// ALSO CORRECT: if you must use a local variable, declare it as the interface
func doSomethingV2(fail bool) error {
    var err error // declared as interface type, not *MyError
    if fail {
        err = &MyError{Code: 500, Message: "internal error"}
    }
    return err // when fail==false, err is a true nil interface
}

func main() {
    err := doSomething(false)
    if err == nil {
        fmt.Println("no error") // correct
    }

    err2 := doSomethingV2(false)
    if err2 == nil {
        fmt.Println("no error v2") // also correct
    }
}
```

The rule: **never return a concrete type variable from a function that returns an interface**. Always return the nil literal directly, or use a variable declared as the interface type.

You can also visualize the difference using unsafe pointer inspection:

```go
package main

import (
    "fmt"
    "unsafe"
)

// interfaceWords extracts the two pointer words from any interface
func interfaceWords(i interface{}) (typePtr, dataPtr uintptr) {
    type iface struct {
        typ  uintptr
        data uintptr
    }
    f := (*iface)(unsafe.Pointer(&i))
    return f.typ, f.data
}

type MyError struct{ msg string }
func (e *MyError) Error() string { return e.msg }

func main() {
    // True nil interface
    var nilInterface error
    t1, d1 := interfaceWords(nilInterface)
    fmt.Printf("nil interface:      type=0x%x data=0x%x nil=%v\n", t1, d1, nilInterface == nil)

    // Typed nil pointer stored in interface
    var typedNilPtr *MyError
    var typedNilIface error = typedNilPtr
    t2, d2 := interfaceWords(typedNilIface)
    fmt.Printf("typed nil in iface: type=0x%x data=0x%x nil=%v\n", t2, d2, typedNilIface == nil)
    // type word is non-zero even though the pointer value is nil!
}
```

Running this makes the problem concrete. The typed nil interface has a non-zero type word, which is why the `== nil` comparison fails.

## In The Wild

This pattern shows up most often in constructors and factory functions that conditionally return errors:

```go
package main

import (
    "database/sql"
    "errors"
    "fmt"
)

type ValidationError struct {
    Field   string
    Message string
}

func (v *ValidationError) Error() string {
    return fmt.Sprintf("validation failed on %s: %s", v.Field, v.Message)
}

type UserService struct {
    db *sql.DB
}

// Common pattern: build an error list and return it
func (s *UserService) validateUser(name, email string) error {
    // BAD pattern: collecting errors as concrete type, then returning
    // var errs *ValidationError
    // if name == "" { errs = &ValidationError{Field: "name", ...} }
    // return errs  // TRAP: always non-nil even when no errors!

    // GOOD pattern: use the interface type or return nil directly
    if name == "" {
        return &ValidationError{Field: "name", Message: "cannot be empty"}
    }
    if email == "" {
        return &ValidationError{Field: "email", Message: "cannot be empty"}
    }
    return nil
}

// Another common trap: wrapping errors
func wrap(err error) error {
    // BAD: if err is a typed nil, this creates a non-nil wrapper of a nil
    // var wrapped *ValidationError = err.(*ValidationError) // panic if wrong type

    // GOOD: check before wrapping
    if err == nil {
        return nil
    }
    return fmt.Errorf("wrapped: %w", err)
}

func main() {
    svc := &UserService{}
    err := svc.validateUser("Alice", "alice@example.com")
    if err != nil {
        fmt.Println("error:", err)
    } else {
        fmt.Println("valid")
    }

    // Demonstrate errors.As with typed nil — also safe when done right
    err2 := svc.validateUser("", "")
    var ve *ValidationError
    if errors.As(err2, &ve) {
        fmt.Println("validation error on field:", ve.Field)
    }
}
```

Another place this bites people is when returning from functions that use named return values:

```go
package main

import "fmt"

type DBError struct{ query string }
func (e *DBError) Error() string { return "db error: " + e.query }

// Named returns: the zero value of error is a true nil interface — this is safe
func queryGood(q string) (result string, err error) {
    if q == "" {
        err = &DBError{query: q}
        return
    }
    result = "data"
    return // err is zero value of error interface — true nil
}

// But this is still broken with named returns if you use a concrete type
func queryBad(q string) (result string, err *DBError) {
    // err is *DBError here — a concrete type
    // If you then convert to error interface elsewhere, same trap applies
    if q == "" {
        err = &DBError{query: q}
    }
    return
}

func main() {
    _, err := queryGood("")
    fmt.Println("queryGood err == nil:", err == nil) // depends on path

    _, dbErr := queryBad("ok")
    var iface error = dbErr
    fmt.Println("queryBad nil check:", iface == nil) // false! typed nil trap
}
```

## The Gotchas

Beyond the basic trap, there are a few related edge cases worth knowing.

**Comparing two interface values**: two interfaces are equal only if they have both the same dynamic type *and* the same dynamic value. If the underlying type is not comparable (e.g., a slice), the comparison panics at runtime.

```go
package main

import "fmt"

func main() {
    var a, b interface{}

    a = []int{1, 2, 3}
    b = []int{1, 2, 3}

    defer func() {
        if r := recover(); r != nil {
            fmt.Println("panic:", r) // runtime error: comparing uncomparable type []int
        }
    }()

    fmt.Println(a == b) // panics!
}
```

**The reflect.DeepEqual escape hatch**: when you need to compare interface values containing slices or maps, use `reflect.DeepEqual`. It handles nil correctly too — `reflect.DeepEqual(nil, (*MyError)(nil))` returns `false`, correctly distinguishing the two.

**Type assertions on nil interfaces panic**:

```go
var err error // true nil
_ = err.(*MyError) // PANIC: interface conversion: interface is nil, not *MyError
```

Always use the comma-ok form: `v, ok := err.(*MyError)` — this never panics.

## Key Takeaway

An interface value is nil only when *both* the type word and the data word are zero. Assigning a typed nil pointer (`var p *MyError`) to an interface variable populates the type word with a non-zero value, making the interface non-nil even though the underlying pointer is nil.

The practical rules:
- Return `nil` directly from functions returning interfaces, never return a typed nil variable
- Declare error variables as `error` (the interface), not `*MyError` (the concrete type)
- Use the comma-ok form for type assertions to avoid panics on nil interfaces
- Use `errors.As` and `errors.Is` for error inspection — they handle the type word correctly

---

**Series: Go Memory Model & Internals**

[← Lesson 1: Interface Representation](../go-internals-interfaces) | [Lesson 3: Method Sets and Addressability →](../go-internals-method-sets)
