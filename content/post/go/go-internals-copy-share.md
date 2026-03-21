---
title: "Lesson 7: Values Copy vs Share — When Go copies and when it doesn't"
author: Atharva Pandey
series: "Go Memory Model & Internals"
lesson: 7
keywords: [Go, golang, internals, memory]
tags: [Go tutorial, golang, internals]
date: '2025-03-01T00:00:00.000Z'
---

Early in my Go career I had a bug where I modified a slice inside a function expecting the caller's slice to remain unchanged — and it did. Then I had a different bug where I expected the opposite and the caller's slice *was* modified. I had no mental model for predicting which would happen. Building that model is what this lesson is about.

## The Problem

Go passes everything by value. That's the official line, and it's true. But "by value" means different things for different types:

- For an `int`, passing by value copies the integer — completely independent.
- For a `slice`, passing by value copies the slice header (pointer + length + capacity) — but the underlying array is shared.
- For a `map`, passing by value copies the map pointer — both the original and the copy point to the same hash table.
- For a `pointer`, passing by value copies the pointer — both point to the same underlying data.

The confusion arises because Go has both **value types** (copy semantics) and **reference types** (shared backing storage), and they look similar at the call site. Understanding which is which — and why — gives you a reliable mental model.

## The Idiomatic Way

Let's walk through each category explicitly:

```go
package main

import "fmt"

// Integers, floats, bools: pure value types
// Assignment and function calls create independent copies
func incrementInt(n int) int {
    n++
    return n
}

// Arrays (not slices): value types — fully copied
func zeroFirstElement(arr [5]int) {
    arr[0] = 0 // modifies the local copy only
}

// Structs without reference fields: value types — fully copied
type Point struct{ X, Y float64 }

func movePoint(p Point, dx, dy float64) Point {
    p.X += dx
    p.Y += dy
    return p // returning modified copy
}

func main() {
    n := 10
    incremented := incrementInt(n)
    fmt.Println(n, incremented) // 10 11 — n unchanged

    arr := [5]int{1, 2, 3, 4, 5}
    zeroFirstElement(arr)
    fmt.Println(arr[0]) // 1 — arr unchanged

    p := Point{X: 1.0, Y: 2.0}
    moved := movePoint(p, 1.0, 1.0)
    fmt.Println(p, moved) // {1 2} {2 3} — p unchanged
}
```

Now the reference-like types:

```go
package main

import "fmt"

// Slices: header is copied, backing array is shared
func appendToSlice(s []int, v int) []int {
    // This does NOT modify the caller's length/capacity
    // but it DOES modify the backing array if len < cap
    s = append(s, v)
    return s
}

func modifyElement(s []int, idx, val int) {
    s[idx] = val // THIS modifies the caller's backing array!
}

func main() {
    original := make([]int, 3, 6) // len=3, cap=6
    original[0], original[1], original[2] = 10, 20, 30

    // modifyElement shares the backing array — caller sees the change
    modifyElement(original, 0, 999)
    fmt.Println(original[0]) // 999 — modified!

    // appendToSlice: if within capacity, modifies backing array
    // but returns a slice with different length
    original[0] = 10 // reset
    result := appendToSlice(original, 40)
    fmt.Println(original) // [10 20 30] — caller's LENGTH unchanged
    fmt.Println(result)   // [10 20 30 40] — new view of same array

    // If backing array is accessed via original past index 3:
    // original[:4] would show [10 20 30 40] — the element was written!
    fmt.Println(original[:4]) // [10 20 30 40] — sharing confirmed
}
```

The slice rule is the most nuanced. A slice has three parts: a pointer to the backing array, a length, and a capacity. When you pass a slice to a function, the function gets its own copy of these three values. Modifications to *elements within the length* affect the shared backing array. But changes to length or capacity (via `append`) don't affect the caller's slice header.

```go
package main

import "fmt"

// Maps: always share the underlying hash table
func addToMap(m map[string]int, key string, val int) {
    m[key] = val // modifies caller's map — there's no "copy" here
}

func replaceMap(m map[string]int) {
    // This only replaces the local copy of the map pointer
    // does NOT affect the caller's map variable
    m = make(map[string]int)
    m["new"] = 1
}

func main() {
    myMap := map[string]int{"a": 1, "b": 2}

    addToMap(myMap, "c", 3)
    fmt.Println(myMap) // map[a:1 b:2 c:3] — modified!

    replaceMap(myMap)
    fmt.Println(myMap) // map[a:1 b:2 c:3] — unchanged!
    // replaceMap created a new map and pointed its local variable at it
    // the caller's variable still points to the original map
}
```

## In The Wild

This copy-vs-share distinction appears constantly in idiomatic Go patterns. One of the most common is the functional options pattern, where you want to build a config struct by value:

```go
package main

import (
    "fmt"
    "time"
)

type ServerConfig struct {
    Host        string
    Port        int
    Timeout     time.Duration
    MaxConns    int
    TLSEnabled  bool
}

type Option func(*ServerConfig)

func WithTimeout(d time.Duration) Option {
    return func(c *ServerConfig) {
        c.Timeout = d // modifies via pointer — shares the config being built
    }
}

func WithMaxConns(n int) Option {
    return func(c *ServerConfig) {
        c.MaxConns = n
    }
}

func NewServer(opts ...Option) ServerConfig {
    cfg := ServerConfig{
        Host:     "localhost",
        Port:     8080,
        Timeout:  30 * time.Second,
        MaxConns: 100,
    }
    for _, opt := range opts {
        opt(&cfg) // pass pointer so options can modify cfg
    }
    return cfg // return by value — caller gets their own copy
}

func main() {
    cfg := NewServer(
        WithTimeout(10*time.Second),
        WithMaxConns(500),
    )
    fmt.Printf("%+v\n", cfg)
    // Mutations inside opts affected cfg via pointer
    // but the returned cfg is an independent value copy
}
```

Another real case: copying slices when you need true independence:

```go
package main

import "fmt"

// When you NEED a true copy of a slice — use copy()
func safeProcess(data []int) []int {
    // If we might modify elements and don't want to affect the caller:
    local := make([]int, len(data))
    copy(local, data)

    for i := range local {
        local[i] *= 2
    }
    return local
}

// Contrast with in-place modification (intentional sharing):
func doubleInPlace(data []int) {
    for i := range data {
        data[i] *= 2
    }
}

func main() {
    original := []int{1, 2, 3, 4, 5}

    doubled := safeProcess(original)
    fmt.Println(original) // [1 2 3 4 5] — unchanged
    fmt.Println(doubled)  // [2 4 6 8 10]

    doubleInPlace(original)
    fmt.Println(original) // [2 4 6 8 10] — modified in place
}
```

## The Gotchas

**Struct with a slice field**: a struct is a value type — but if it contains a slice field, copying the struct copies the slice header, and both the original and the copy share the backing array:

```go
package main

import "fmt"

type Buffer struct {
    data []byte
    pos  int
}

func main() {
    b1 := Buffer{data: []byte("hello"), pos: 0}
    b2 := b1 // struct copy — SHALLOW copy

    b2.pos = 3           // independent: modifies only b2's pos
    b2.data[0] = 'H'     // shared: modifies the backing array both see

    fmt.Println(string(b1.data)) // "Hello" — b1's array was modified!
    fmt.Println(b1.pos)          // 0 — b1's pos unchanged
    fmt.Println(b2.pos)          // 3 — b2's pos is independent
}
```

To get a truly independent copy, you must deep-copy the slice explicitly. This is why types containing slices often implement a `Clone()` method.

**Range loop copies elements**: the range loop gives you a copy of each element. Modifying the range variable does not modify the slice:

```go
package main

import "fmt"

type Item struct{ value int }

func main() {
    items := []Item{{1}, {2}, {3}}

    // WRONG: modifies a copy of each Item
    for _, item := range items {
        item.value *= 10 // no effect on items
    }
    fmt.Println(items) // [{1} {2} {3}]

    // CORRECT: modify via index
    for i := range items {
        items[i].value *= 10
    }
    fmt.Println(items) // [{10} {20} {30}]

    // Also correct: slice of pointers
    ptrs := []*Item{{1}, {2}, {3}}
    for _, item := range ptrs {
        item.value *= 10 // modifies via pointer — works
    }
    fmt.Println(*ptrs[0]) // {10}
}
```

**Channel sends copy the value**: when you send a value on a channel, Go copies it. This is intentional — it prevents races. But it means large structs are expensive to send; send pointers instead for large values (while being mindful of ownership and the GC implications).

## Key Takeaway

Go is always pass-by-value, but "value" means different things for different types. Integers, floats, bools, arrays, and structs without reference fields are true value types — assignments and function calls create fully independent copies. Slices, maps, channels, pointers, and functions contain or are pointers to shared backing storage — copying these gives you a new header that still refers to the same underlying data.

The practical rules:
- Modifying slice elements inside a function affects the caller; modifying length/capacity (via append) does not
- Map operations always affect the shared hash table; reassigning the map variable does not
- Use `copy()` when you need a truly independent slice
- Structs containing slices or maps are shallow-copied — implement `Clone()` when deep copying is needed
- In range loops, modify via index (`items[i]`), not via the range variable, for slice element modification

---

**Series: Go Memory Model & Internals**

[← Lesson 6: Memory Alignment and Struct Padding](../go-internals-alignment) | [Lesson 8: String Internals →](../go-internals-strings)
