---
title: "Lesson 8: Structs — Your first custom type"
author: "Atharva Pandey"
series: "Go from Scratch"
lesson: 8
keywords:
  - Go
  - golang
  - structs
  - custom types
  - Go tutorial
  - beginner Go
tags:
  - Go tutorial
  - golang
  - beginner
date: "2024-02-07T00:00:00.000Z"
---

So far we've been working with Go's built-in types: strings, ints, booleans, slices, maps. Those are great, but real programs deal with real-world things — users, orders, products, messages. You need a way to group related data together and give it a meaningful name. In Go, that's a struct.

If you come from Python, think of a struct as a lightweight class that holds data (but with no inheritance, and methods are defined separately). If you come from JavaScript, think of it as a typed object. If you come from C, structs work almost exactly the same way.

## The Basics

### Defining a struct

```go
type User struct {
    Name  string
    Email string
    Age   int
}
```

`type` is the keyword. `User` is the name we're giving this type. Inside the curly braces are the fields — each field has a name and a type.

That's it. We've defined a new type called `User` that bundles three pieces of data together.

### Creating a struct (struct literals)

```go
package main

import "fmt"

type User struct {
    Name  string
    Email string
    Age   int
}

func main() {
    // Named field literal — preferred style
    u := User{
        Name:  "Alice",
        Email: "alice@example.com",
        Age:   30,
    }

    fmt.Println(u.Name)  // Alice
    fmt.Println(u.Email) // alice@example.com
    fmt.Println(u.Age)   // 30
}
```

The named field style (`Name: "Alice"`) is almost always the right choice. It's clear, self-documenting, and safe if you add fields to the struct later.

There's also a positional form — `User{"Alice", "alice@example.com", 30}` — but I'd avoid it. If you ever add or reorder fields in the struct definition, positional literals silently break.

### Accessing and modifying fields

Use dot notation to read or write any field:

```go
u := User{Name: "Alice", Email: "alice@example.com", Age: 30}

// Read
fmt.Println(u.Name) // Alice

// Write
u.Age = 31
fmt.Println(u.Age) // 31
```

### Zero values in structs

Every field in a struct starts at its zero value if you don't set it. Strings default to `""`, ints to `0`, booleans to `false`, pointers to `nil`:

```go
var u User
fmt.Println(u.Name)  // "" — empty string
fmt.Println(u.Age)   // 0
```

This is useful when you want to partially initialize a struct:

```go
u := User{Name: "Bob"}
// Email is "" and Age is 0 — zero values
```

### Nested structs

Structs can contain other structs. This is how you model more complex data:

```go
type Address struct {
    Street string
    City   string
    Zip    string
}

type User struct {
    Name    string
    Email   string
    Age     int
    Address Address
}

func main() {
    u := User{
        Name:  "Alice",
        Email: "alice@example.com",
        Age:   30,
        Address: Address{
            Street: "123 Main St",
            City:   "Springfield",
            Zip:    "12345",
        },
    }

    fmt.Println(u.Address.City) // Springfield
}
```

Dot notation chains naturally — `u.Address.City` reads as "user's address's city."

### Anonymous structs

Sometimes you need a one-off struct just for a quick grouping — maybe for a test or to decode some JSON. You don't have to declare a named type:

```go
point := struct {
    X int
    Y int
}{X: 10, Y: 20}

fmt.Println(point.X, point.Y) // 10 20
```

Anonymous structs are handy but should stay local. If you find yourself reusing the same anonymous struct shape in multiple places, it's time to give it a proper name.

### Comparing structs

Two struct values are equal if all their fields are equal. This works as long as all fields are comparable types (no slices, maps, or functions):

```go
type Point struct {
    X int
    Y int
}

p1 := Point{X: 1, Y: 2}
p2 := Point{X: 1, Y: 2}
p3 := Point{X: 3, Y: 4}

fmt.Println(p1 == p2) // true
fmt.Println(p1 == p3) // false
```

This is one place where Go is nicer than many languages — you get value equality for free, without implementing any comparison interface.

## Try It Yourself

Let's model a simple shopping cart item and write a function that calculates the total:

```go
package main

import "fmt"

type CartItem struct {
    Name     string
    Price    float64
    Quantity int
}

func itemTotal(item CartItem) float64 {
    return item.Price * float64(item.Quantity)
}

func cartTotal(items []CartItem) float64 {
    total := 0.0
    for _, item := range items {
        total += itemTotal(item)
    }
    return total
}

func main() {
    cart := []CartItem{
        {Name: "Go book", Price: 39.99, Quantity: 1},
        {Name: "Keyboard", Price: 129.00, Quantity: 2},
        {Name: "Coffee", Price: 12.50, Quantity: 3},
    }

    for _, item := range cart {
        fmt.Printf("%s: $%.2f\n", item.Name, itemTotal(item))
    }
    fmt.Printf("Total: $%.2f\n", cartTotal(cart))
}
```

Notice how passing a `CartItem` to `itemTotal` makes the code self-documenting. Compare this to passing three separate parameters — the struct version is cleaner and easier to extend.

## Common Mistakes

**Using positional struct literals:**

```go
// RISKY — breaks silently if fields are added or reordered
u := User{"Alice", "alice@example.com", 30}

// SAFE
u := User{Name: "Alice", Email: "alice@example.com", Age: 30}
```

**Forgetting that structs are copied when passed to functions.** In Go, everything is pass-by-value. When you pass a struct to a function, the function gets a copy:

```go
func birthday(u User) {
    u.Age++ // modifies the copy, not the original
}

u := User{Name: "Alice", Age: 30}
birthday(u)
fmt.Println(u.Age) // still 30
```

If you want the function to modify the original, use a pointer (we'll cover that in Lesson 10). For now, just be aware of this.

**Trying to compare structs that contain slices or maps:**

```go
type Team struct {
    Name    string
    Members []string // slice — not comparable
}

t1 := Team{Name: "A", Members: []string{"Alice"}}
t2 := Team{Name: "A", Members: []string{"Alice"}}

// t1 == t2 // compile error: invalid operation
```

Use `reflect.DeepEqual` or compare fields manually when your struct contains slices or maps.

## Key Takeaway

Structs let you create your own types by grouping related fields together. Use named field literals when initializing structs — they're safer and more readable. All fields default to their zero values. Structs are copied when passed to functions, which is safe but means mutations inside the function won't affect the original. In the next lesson we'll add methods to structs to make them even more powerful.

---

**Previous lesson:** [Lesson 7: Strings, Runes, and Bytes](/post/go/go-scratch-strings)

**Next lesson:** [Lesson 9: Methods](/post/go/go-scratch-methods)
