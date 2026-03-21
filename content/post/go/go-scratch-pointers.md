---
title: "Lesson 10: Pointers — Addresses, not magic"
author: "Atharva Pandey"
series: "Go from Scratch"
lesson: 10
keywords:
  - Go
  - golang
  - pointers
  - memory
  - Go tutorial
  - beginner Go
tags:
  - Go tutorial
  - golang
  - beginner
date: "2024-02-17T00:00:00.000Z"
---

Pointers have a reputation for being scary. In C, they're the source of buffer overflows, dangling references, and cryptic crashes. In Go, pointers are much tamer. There's no pointer arithmetic, the garbage collector handles memory for you, and the rules are simpler. But pointers are still important — without them, you can't write Go programs that mutate data through function calls, and you can't use pointer receivers (which we covered in the last lesson).

Let me demystify them completely.

## The Basics

### What is a pointer?

A pointer is a variable that holds the **memory address** of another variable. Instead of storing a value directly, it stores where that value lives in memory.

Think of memory like a giant hotel. Every room (memory location) has an address (like room 203). A regular variable is like saying "the guest in this room is Alice." A pointer is like saying "the address of Alice's room is 203."

### & and *

Two operators are the core of pointer work in Go:

- `&` (address-of): gives you the address of a variable
- `*` (dereference): gives you the value at an address

```go
package main

import "fmt"

func main() {
    x := 42

    p := &x // p is a pointer to x; p holds x's address
    fmt.Println(x)  // 42  — the value
    fmt.Println(p)  // 0xc000018070 — some memory address
    fmt.Println(*p) // 42  — dereference: the value at that address

    *p = 100        // change the value at the address
    fmt.Println(x)  // 100 — x changed because *p and x are the same location
}
```

`p` and `x` point to the same memory. When you write `*p = 100`, you're reaching into that memory location and changing what's stored there. `x` reflects that change because it's the same location.

The type of `p` is `*int` — "pointer to int." For a `string`, the pointer type would be `*string`. For a `User` struct, it would be `*User`.

### Why do pointers exist?

There are two main reasons to use a pointer in Go.

**Reason 1: Mutation through function calls.**

Go passes everything by value. When you pass a variable to a function, the function gets a copy. The original is untouched. If you want the function to modify the original, you pass a pointer:

```go
func doubleValue(n int) {
    n *= 2 // modifies the copy, not the original
}

func doublePointer(n *int) {
    *n *= 2 // modifies the value at the address
}

func main() {
    x := 10

    doubleValue(x)
    fmt.Println(x) // 10 — unchanged

    doublePointer(&x)
    fmt.Println(x) // 20 — changed!
}
```

**Reason 2: Avoiding large copies.**

When you pass a large struct to a function, Go copies every field. For small structs this is fine. For structs with many fields or large embedded data, copying is wasteful. Passing a pointer passes just the address — 8 bytes on a 64-bit system, regardless of how big the struct is:

```go
type BigConfig struct {
    Settings [1000]string
    Options  map[string]interface{}
    // ... many more fields
}

// Each call copies the entire struct — expensive
func processConfig(cfg BigConfig) { ... }

// Each call passes just an address — cheap
func processConfig(cfg *BigConfig) { ... }
```

### Creating pointers with new

Besides `&`, you can create a pointer with the built-in `new` function:

```go
p := new(int)   // allocates an int, returns *int pointing to it
*p = 42
fmt.Println(*p) // 42
```

`new(T)` allocates memory for a value of type T, initializes it to its zero value, and returns a pointer to it. In practice, `new` is used less often than `&` with a struct literal.

For structs, the most common pattern is:

```go
type User struct {
    Name string
    Age  int
}

// Create a *User directly
u := &User{Name: "Alice", Age: 30}
fmt.Println(u.Name) // Alice — Go automatically dereferences for field access
```

Go lets you access fields directly through a pointer — you don't need to write `(*u).Name`. The dot operator handles dereferencing automatically.

### Nil pointers

The zero value for any pointer type is `nil`. A nil pointer doesn't point to anything. Trying to dereference a nil pointer panics:

```go
var p *int // p is nil

fmt.Println(p)  // <nil>
fmt.Println(*p) // PANIC: runtime error: invalid memory address or nil pointer dereference
```

Always check if a pointer might be nil before dereferencing it:

```go
func printValue(p *int) {
    if p == nil {
        fmt.Println("no value")
        return
    }
    fmt.Println(*p)
}
```

### No pointer arithmetic

In C, you can do things like `p++` to advance a pointer to the next memory location. Go does not allow this. You cannot add or subtract from a pointer. This removes a huge class of bugs. If you need to iterate over a collection, use slices and `range` — that's what they're for.

## Try It Yourself

Let's write a function that swaps two values — something that's impossible without pointers (or returning multiple values):

```go
package main

import "fmt"

func swap(a, b *int) {
    *a, *b = *b, *a
}

func main() {
    x, y := 10, 20
    fmt.Println("before:", x, y) // 10 20

    swap(&x, &y)
    fmt.Println("after:", x, y)  // 20 10
}
```

And here's a more realistic example — updating a user's email through a function:

```go
package main

import "fmt"

type User struct {
    Name  string
    Email string
}

func updateEmail(u *User, newEmail string) {
    u.Email = newEmail // modifies the original through the pointer
}

func main() {
    user := User{Name: "Alice", Email: "alice@old.com"}

    updateEmail(&user, "alice@new.com")
    fmt.Println(user.Email) // alice@new.com
}
```

Notice that inside `updateEmail`, we write `u.Email` not `(*u).Email`. Go handles the dereferencing automatically when you use the dot operator on a pointer.

## Common Mistakes

**Dereferencing a nil pointer:**

```go
var u *User
fmt.Println(u.Name) // PANIC — u is nil
```

Always initialize the pointer before using it: `u := &User{Name: "Alice"}` or check for nil first.

**Returning a pointer to a local variable — and worrying about it:**

```go
func newUser(name string) *User {
    u := User{Name: name}
    return &u // is this safe?
}
```

Yes, this is completely safe in Go. The garbage collector detects that `u` escapes to the heap and keeps it alive as long as the pointer exists. Unlike C, you don't need to worry about stack variables being cleaned up. This is called escape analysis.

**Overusing pointers.** Not every value needs to be a pointer. Small values (ints, booleans, small structs) are fine to pass by value. Only reach for a pointer when you genuinely need mutation or are avoiding an expensive copy.

**Confusing `*T` (pointer type) with `*` (dereference operator):**

```go
var p *int   // *int is the type — "pointer to int"
x := 42
p = &x
fmt.Println(*p) // * here is the operator — "value at this address"
```

The `*` symbol does double duty: in a type declaration it means "pointer to", and as an operator it means "dereference."

## Key Takeaway

A pointer holds a memory address. Use `&` to get the address of a variable, and `*` to access the value at an address. Pointers exist so functions can mutate their arguments and so large values can be passed cheaply. The zero value of any pointer is `nil` — always check for nil before dereferencing. Go has no pointer arithmetic, which keeps things safe. When you use the dot operator on a pointer to a struct, Go dereferences automatically.

---

**Previous lesson:** [Lesson 9: Methods](/post/go/go-scratch-methods)

**Next lesson:** [Lesson 11: Interfaces](/post/go/go-scratch-interfaces)
