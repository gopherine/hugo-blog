---
title: "Lesson 9: Methods — Functions that belong to a type"
author: "Atharva Pandey"
series: "Go from Scratch"
lesson: 9
keywords:
  - Go
  - golang
  - methods
  - value receivers
  - pointer receivers
  - Go tutorial
  - beginner Go
tags:
  - Go tutorial
  - golang
  - beginner
date: "2024-02-11T00:00:00.000Z"
---

In the last lesson we defined a `User` struct. We could pass it to functions — `birthday(u)`, `sendEmail(u)`, `formatName(u)`. That works, but there's a better way: we can attach functions directly to the `User` type. Then instead of `birthday(u)` we write `u.Birthday()`. Those are called methods.

Methods make code more readable, more organized, and closer to how we naturally think about objects — "a user does birthday-related things" rather than "a birthday function takes a user."

## The Basics

### Defining a method

A method is just a function with an extra receiver argument that appears before the function name:

```go
package main

import "fmt"

type User struct {
    Name string
    Age  int
}

// This is a method on User
// "u User" is the receiver
func (u User) Greet() string {
    return fmt.Sprintf("Hi, I'm %s and I'm %d years old.", u.Name, u.Age)
}

func main() {
    u := User{Name: "Alice", Age: 30}
    fmt.Println(u.Greet()) // Hi, I'm Alice and I'm 30 years old.
}
```

The `(u User)` part is the receiver. `u` is the name you use to refer to the value inside the method, and `User` is the type this method belongs to. By convention, the receiver name is a short abbreviation of the type — `u` for `User`, `s` for `Server`, and so on.

### Value receivers vs pointer receivers

This is the single most important thing to understand about methods in Go.

A **value receiver** gets a copy of the value. Changes inside the method don't affect the original:

```go
func (u User) IncrementAge() {
    u.Age++ // modifies the copy, original unchanged
}

u := User{Name: "Alice", Age: 30}
u.IncrementAge()
fmt.Println(u.Age) // still 30
```

A **pointer receiver** gets a pointer to the original. Changes inside the method affect the original:

```go
func (u *User) IncrementAge() {
    u.Age++ // modifies the original
}

u := User{Name: "Alice", Age: 30}
u.IncrementAge() // Go automatically takes the address: (&u).IncrementAge()
fmt.Println(u.Age) // 31 — changed!
```

Notice the `*` before `User` in the receiver. That's what makes it a pointer receiver.

### When to use which

Use a **pointer receiver** when:
- The method needs to modify the receiver
- The struct is large and you don't want to copy it on every method call
- The type has any methods that use pointer receivers (for consistency — mix only causes confusion)

Use a **value receiver** when:
- The method only reads the receiver, never modifies it
- The type is small (an int, a small struct)
- The type should behave like a value (like `time.Time`)

In practice, most non-trivial structs end up using pointer receivers for almost everything. When in doubt, use a pointer receiver.

Here's a realistic example showing both kinds on the same type:

```go
type BankAccount struct {
    Owner   string
    Balance float64
}

// Value receiver — only reads, doesn't modify
func (a BankAccount) String() string {
    return fmt.Sprintf("%s: $%.2f", a.Owner, a.Balance)
}

// Pointer receiver — modifies the balance
func (a *BankAccount) Deposit(amount float64) {
    if amount > 0 {
        a.Balance += amount
    }
}

// Pointer receiver — modifies the balance
func (a *BankAccount) Withdraw(amount float64) bool {
    if amount > a.Balance {
        return false
    }
    a.Balance -= amount
    return true
}

func main() {
    account := BankAccount{Owner: "Alice", Balance: 100.0}

    account.Deposit(50.0)
    fmt.Println(account.String()) // Alice: $150.00

    ok := account.Withdraw(200.0)
    fmt.Println(ok)               // false — not enough funds
    fmt.Println(account.String()) // Alice: $150.00 — unchanged
}
```

### Methods on any named type

You don't have to define methods only on structs. You can define a method on any named type that you define yourself — including types based on primitives:

```go
type Celsius float64
type Fahrenheit float64

func (c Celsius) ToFahrenheit() Fahrenheit {
    return Fahrenheit(c*9/5 + 32)
}

func (f Fahrenheit) ToCelsius() Celsius {
    return Celsius((f - 32) * 5 / 9)
}

func main() {
    boiling := Celsius(100)
    fmt.Printf("%.1f°C = %.1f°F\n", boiling, boiling.ToFahrenheit()) // 100.0°C = 212.0°F

    body := Fahrenheit(98.6)
    fmt.Printf("%.1f°F = %.1f°C\n", body, body.ToCelsius()) // 98.6°F = 37.0°C
}
```

This is a powerful idea. By creating named types for units, you prevent accidentally mixing them — the compiler will complain if you try to pass a `Fahrenheit` where a `Celsius` is expected.

## Try It Yourself

Let's build a simple `Stack` data structure using methods:

```go
package main

import "fmt"

type Stack struct {
    items []int
}

func (s *Stack) Push(item int) {
    s.items = append(s.items, item)
}

func (s *Stack) Pop() (int, bool) {
    if len(s.items) == 0 {
        return 0, false
    }
    last := s.items[len(s.items)-1]
    s.items = s.items[:len(s.items)-1]
    return last, true
}

func (s Stack) Len() int {
    return len(s.items)
}

func (s Stack) IsEmpty() bool {
    return len(s.items) == 0
}

func main() {
    var s Stack

    s.Push(1)
    s.Push(2)
    s.Push(3)
    fmt.Println("Size:", s.Len()) // 3

    for !s.IsEmpty() {
        val, _ := s.Pop()
        fmt.Println("Popped:", val)
    }
    // Popped: 3
    // Popped: 2
    // Popped: 1
}
```

`Push` and `Pop` use pointer receivers because they modify `items`. `Len` and `IsEmpty` use value receivers because they only read.

## Common Mistakes

**Using a value receiver when you need to modify the receiver:**

```go
// WRONG — modifies a copy, original unchanged
func (u User) SetName(name string) {
    u.Name = name
}

// RIGHT — modifies the original
func (u *User) SetName(name string) {
    u.Name = name
}
```

**Mixing pointer and value receivers on the same type.** Go lets you do it, but it leads to confusion about method sets (which methods are available on a value vs a pointer). Stick to one style per type — usually pointer receivers.

**Calling a pointer receiver method on a non-addressable value:**

```go
// This won't work
User{Name: "Alice"}.SetName("Bob") // can't take address of a temporary value
```

You need to assign the struct to a variable first, then call the method.

## Key Takeaway

Methods are functions with a receiver — they attach behavior directly to a type. Value receivers get a copy; pointer receivers get access to the original. Use pointer receivers when you need to modify the receiver or when the struct is large. You can define methods on any named type you create, not just structs. Consistency matters: pick pointer or value receivers and stick to one style per type.

---

**Previous lesson:** [Lesson 8: Structs](/post/go/go-scratch-structs)

**Next lesson:** [Lesson 10: Pointers](/post/go/go-scratch-pointers)
