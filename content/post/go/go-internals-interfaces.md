---
title: "Lesson 1: Interface Representation — Two words that carry your abstractions"
author: Atharva Pandey
series: "Go Memory Model & Internals"
lesson: 1
keywords: [Go, golang, internals, memory]
tags: [Go tutorial, golang, internals]
date: '2024-06-10T00:00:00.000Z'
---

I remember the first time I hit a bug where `err != nil` returned `true` even though the function clearly returned `nil`. I spent forty minutes staring at perfectly reasonable-looking code before I realized I had no idea what an interface actually *is* at the memory level. Once I understood the two-word representation, that class of bug never confused me again.

## The Problem

Go interfaces are the backbone of polymorphism in the language. You use them constantly — `io.Reader`, `error`, `fmt.Stringer`. But most developers treat them as magic. You assign a concrete value, you call methods, and somehow Go figures out which implementation to dispatch to at runtime.

The problem is that this "magic" has real memory layout consequences. When you don't understand what an interface value looks like in memory, you will:

- Get confused by nil interface comparisons that behave unexpectedly
- Misunderstand the cost of storing values in interfaces
- Miss subtle bugs when passing interfaces across package boundaries
- Struggle to reason about what the garbage collector sees

The fix is straightforward: understand that every interface value is exactly two machine words. Once that's in your head, the rest follows.

## The Idiomatic Way

At runtime, every non-empty interface value in Go is a pair of pointers:

1. **The type pointer** (sometimes called `itab`) — points to a structure that describes the concrete type stored in the interface and the method dispatch table for that type against this interface.
2. **The data pointer** — points to (or directly contains, for small values) the actual data.

You can inspect this directly by looking at how the `reflect` package exposes it:

```go
package main

import (
    "fmt"
    "reflect"
    "unsafe"
)

// iface mirrors the internal layout of a non-empty interface
type iface struct {
    tab  unsafe.Pointer // *itab
    data unsafe.Pointer // pointer to data
}

func main() {
    var r io.Reader = os.Stdin

    // reflect gives us clean access to the two words
    v := reflect.ValueOf(r)
    fmt.Println("Kind:", v.Kind())       // ptr
    fmt.Println("Type:", v.Type())       // *os.File
    fmt.Println("Elem kind:", v.Elem().Kind()) // struct

    // Size of an interface value is always two words
    fmt.Println("Size of io.Reader:", unsafe.Sizeof(r)) // 16 on 64-bit
}
```

The key insight: `unsafe.Sizeof` on *any* interface is always 16 bytes on a 64-bit system (two 8-byte pointers), regardless of how large the underlying concrete type is.

Now let's see what happens when you store different types in an interface:

```go
package main

import (
    "fmt"
    "unsafe"
)

type Small struct{ x int }
type Large struct{ data [1024]byte }

func sizeOf(i interface{}) uintptr {
    return unsafe.Sizeof(i)
}

func main() {
    var s Small
    var l Large

    var i1 interface{} = s
    var i2 interface{} = l
    var i3 interface{} = 42
    var i4 interface{} = "hello"

    // All the same size — interface is always two words
    fmt.Println(unsafe.Sizeof(i1)) // 16
    fmt.Println(unsafe.Sizeof(i2)) // 16
    fmt.Println(unsafe.Sizeof(i3)) // 16
    fmt.Println(unsafe.Sizeof(i4)) // 16

    // But the data pointer behavior differs:
    // Small values may be stored inline or on heap
    // Large values always go to heap; interface holds pointer
    _ = i1
    _ = i2
    _ = i3
    _ = i4
}
```

The interface value is always the same size, but what the data pointer *points to* differs. For small scalars, Go may inline the value directly in the pointer word itself (an optimization). For anything larger than a pointer, Go allocates the value on the heap and the data pointer references that allocation.

The type pointer points to an `itab` structure. This structure contains:

- A pointer to the concrete type's type descriptor (`*_type`)
- A pointer to the interface type's type descriptor
- A cached hash for fast type assertions
- An array of function pointers for the methods this concrete type implements

```go
package main

import (
    "fmt"
    "io"
    "os"
)

type MyWriter struct{}

func (m *MyWriter) Write(p []byte) (n int, err error) {
    return len(p), nil
}

func demonstrate(w io.Writer) {
    // At this point, w contains:
    //   word 1: pointer to itab{*MyWriter implements io.Writer}
    //   word 2: pointer to the MyWriter value
    fmt.Printf("type: %T\n", w)

    // Type assertion checks the itab pointer — O(1) operation
    if _, ok := w.(*MyWriter); ok {
        fmt.Println("it's a *MyWriter")
    }

    // Type switch also uses the itab
    switch v := w.(type) {
    case *MyWriter:
        fmt.Printf("concrete: %v\n", v)
    case *os.File:
        fmt.Println("it's a file")
    }
}

func main() {
    mw := &MyWriter{}
    demonstrate(mw)
}
```

## In The Wild

This two-word layout shows up in real code all the time. One place it matters enormously is the `error` interface. Because `error` is just an interface with a single `Error() string` method, every error value you return is two words.

Consider a hot path that returns errors frequently:

```go
package main

import (
    "errors"
    "fmt"
)

// Sentinel errors are allocated once; the interface copies are cheap
var ErrNotFound = errors.New("not found")
var ErrPermission = errors.New("permission denied")

type PathError struct {
    Op   string
    Path string
    Err  error
}

func (e *PathError) Error() string {
    return e.Op + " " + e.Path + ": " + e.Err.Error()
}

func lookup(key string) error {
    if key == "" {
        return ErrNotFound // interface = {itab for *errors.errorString, ptr to ErrNotFound}
    }
    if key == "restricted" {
        // New allocation each call — the *PathError goes to heap
        return &PathError{Op: "lookup", Path: key, Err: ErrPermission}
    }
    return nil // interface = {nil, nil}
}

func main() {
    err := lookup("")
    if err == ErrNotFound {
        fmt.Println("not found") // pointer comparison on the data word — fast
    }

    err2 := lookup("restricted")
    var pe *PathError
    if errors.As(err2, &pe) {
        fmt.Println("path error:", pe.Op)
    }
}
```

Sentinel error comparison works efficiently because it compares the data pointer word directly. Both the stored pointer and `ErrNotFound` point to the same `errorString` allocation — same address, instant match.

## The Gotchas

The most dangerous consequence of the two-word layout is the nil interface trap, which I'll cover in depth in Lesson 2. But there's another subtle gotcha worth noting here: **copying interfaces copies only the two words, not the underlying data**.

```go
package main

import "fmt"

type Counter struct{ n int }
func (c *Counter) Inc() { c.n++ }

func main() {
    c := &Counter{}
    var i1 interface{} = c
    i2 := i1 // copies the two words: same itab, same data pointer

    // Both i1 and i2 point to the SAME Counter
    if ptr1, ok := i1.(*Counter); ok {
        ptr1.Inc()
    }
    if ptr2, ok := i2.(*Counter); ok {
        fmt.Println(ptr2.n) // 1 — shared underlying data
    }

    // Now with a value type (not pointer)
    n := 42
    var j1 interface{} = n
    j2 := j1

    // j1 and j2 have independent copies — Go copied the int
    // (the data word holds the value directly for small scalars)
    fmt.Println(j1, j2) // 42 42 — independent
}
```

When you store a pointer in an interface, copying the interface gives you two interface values sharing the same underlying object. When you store a value type, Go copies the value (possibly allocating it on the heap), and the two interface values are independent.

Another gotcha: **method dispatch through an interface costs more than a direct call**. The runtime must dereference the itab, find the function pointer, then call through it. This is one indirect call plus one memory fetch. In tight loops, this can be measurable. Use concrete types in hot paths, interfaces at API boundaries.

## Key Takeaway

Every interface value is exactly two machine words: a type pointer (itab) and a data pointer. The type pointer identifies what concrete type is stored and provides the method dispatch table. The data pointer holds the actual value — either directly for small scalars or as a pointer to a heap allocation for anything larger.

This layout explains why:
- All interface values are the same size (16 bytes on 64-bit)
- Nil interface comparison requires *both* words to be nil
- Storing a value in an interface may cause a heap allocation
- Method calls through interfaces involve an indirect function pointer lookup
- Type assertions are O(1) — they just compare the type pointer

Understanding this makes you a better Go programmer. You stop being surprised by the edge cases and start being able to predict them.

---

**Series: Go Memory Model & Internals**

[Lesson 2: Nil Interface Gotchas →](../go-internals-nil-interface)
