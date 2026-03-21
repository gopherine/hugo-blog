---
title: "Lesson 5: Arrays and Slices — Slices are what you actually use"
author: Atharva Pandey
series: "Go from Scratch"
lesson: 5
keywords: [Go, golang, beginner, arrays, slices, make, append, len, cap, range]
tags: [Go tutorial, golang, beginner]
date: '2024-01-22T00:00:00.000Z'
---

Go has two sequence types: arrays and slices. I'll tell you upfront — arrays are rarely used directly. They exist, they matter for understanding how things work under the hood, but in day-to-day Go programming you'll almost always reach for a slice instead.

Slices are Go's workhorse collection type. They're flexible, they grow when you need them to, and they're used everywhere: in standard library APIs, in function parameters, in HTTP handlers. Understanding slices well will make you a much more effective Go programmer than memorizing syntax ever will.

Let's start with arrays so you understand what's underneath, then spend most of our time on slices.

---

## The Basics

### Arrays — fixed size, rarely used directly

An array in Go holds a fixed number of elements, all of the same type. The size is part of the type itself:

```go
package main

import "fmt"

func main() {
    var scores [5]int           // array of 5 ints, all zero
    scores[0] = 90
    scores[1] = 85
    scores[2] = 92

    fmt.Println(scores)          // [90 85 92 0 0]
    fmt.Println(len(scores))     // 5

    // Declare and initialize in one step
    primes := [5]int{2, 3, 5, 7, 11}
    fmt.Println(primes)
}
```

The critical thing to understand: `[5]int` and `[10]int` are *different types* in Go. You can't assign one to the other or pass a `[5]int` to a function that expects `[10]int`. This rigidity is why arrays are rarely used directly in practice.

You can let Go count the elements for you with `[...]`:

```go
letters := [...]string{"a", "b", "c"}  // Go figures out it's [3]string
```

### Slices — dynamic, flexible, and backed by an array

A slice is a *window into an array*. It has three components:
- A pointer to some position in an underlying array
- A length (`len`) — how many elements are currently visible
- A capacity (`cap`) — how many elements the underlying array has from that pointer position

You almost never think about this directly. What you need to know is: slices can grow, slices are what you pass around, and slices are what most Go APIs expect.

The simplest way to create a slice is a *slice literal*:

```go
package main

import "fmt"

func main() {
    fruits := []string{"apple", "banana", "cherry"}

    fmt.Println(fruits)         // [apple banana cherry]
    fmt.Println(len(fruits))    // 3
    fmt.Println(fruits[0])      // apple
    fmt.Println(fruits[1:3])    // [banana cherry] — a sub-slice
}
```

Notice `[]string` vs `[3]string` — no number inside the brackets means slice. A number means array.

### `make` — creating slices with a known size

When you know how many elements you'll have upfront, use `make`:

```go
package main

import "fmt"

func main() {
    // make([]type, length, capacity)
    scores := make([]int, 5)         // length 5, cap 5, all zeros
    scores[0] = 100
    scores[1] = 95

    fmt.Println(scores)              // [100 95 0 0 0]
    fmt.Println(len(scores), cap(scores)) // 5 5

    // Pre-allocate with extra capacity for performance
    buffer := make([]int, 0, 10)     // length 0, capacity 10
    fmt.Println(len(buffer), cap(buffer)) // 0 10
}
```

The difference between length and capacity matters for performance. When you `append` to a slice and it runs out of capacity, Go allocates a new, larger underlying array and copies everything over. If you know you'll add 1000 items, pre-allocating with `make([]T, 0, 1000)` avoids repeated re-allocations.

### `append` — adding elements to a slice

`append` is how you grow a slice. It returns a new slice — you must use the return value:

```go
package main

import "fmt"

func main() {
    nums := []int{1, 2, 3}
    fmt.Println(nums, len(nums)) // [1 2 3] 3

    nums = append(nums, 4)
    nums = append(nums, 5, 6, 7) // append multiple at once
    fmt.Println(nums, len(nums)) // [1 2 3 4 5 6 7] 7

    // Append one slice onto another using ...
    more := []int{8, 9, 10}
    nums = append(nums, more...)
    fmt.Println(nums) // [1 2 3 4 5 6 7 8 9 10]
}
```

The `...` after `more` spreads the slice into individual arguments, just like we saw with variadic functions in Lesson 4.

### `range` — iterating over a slice

The `range` keyword gives you both the index and the value when looping over a slice:

```go
package main

import "fmt"

func main() {
    languages := []string{"Go", "Python", "Rust", "JavaScript"}

    // Both index and value
    for i, lang := range languages {
        fmt.Printf("%d: %s\n", i, lang)
    }

    // Just the value (discard index with _)
    for _, lang := range languages {
        fmt.Println(lang)
    }

    // Just the index
    for i := range languages {
        fmt.Println(i)
    }
}
```

`range` is the idiomatic way to iterate in Go. You'll use it constantly — over slices, over maps (which we'll cover soon), and over strings.

### Slicing a slice

You can take a portion of a slice using the `[low:high]` syntax. `low` is inclusive, `high` is exclusive:

```go
package main

import "fmt"

func main() {
    nums := []int{10, 20, 30, 40, 50}

    fmt.Println(nums[1:4])  // [20 30 40] — indices 1, 2, 3
    fmt.Println(nums[:3])   // [10 20 30] — from start to index 2
    fmt.Println(nums[2:])   // [30 40 50] — from index 2 to end
    fmt.Println(nums[:])    // [10 20 30 40 50] — the whole slice
}
```

**Important**: sub-slices share the same underlying array as the original. Modifying an element in a sub-slice modifies it in the original too. When you need an independent copy, use the built-in `copy`:

```go
original := []int{1, 2, 3, 4, 5}
clone := make([]int, len(original))
copy(clone, original)
```

---

## Try It Yourself

Write a function that takes a slice of integers and returns a new slice containing only the even numbers:

```go
package main

import "fmt"

func evens(nums []int) []int {
    result := []int{}
    for _, n := range nums {
        if n%2 == 0 {
            result = append(result, n)
        }
    }
    return result
}

func main() {
    numbers := []int{1, 2, 3, 4, 5, 6, 7, 8, 9, 10}
    fmt.Println(evens(numbers)) // [2 4 6 8 10]
}
```

Once that's working, modify it to take a second argument — a function `func(int) bool` — and use that function to decide which elements to include. This is a general-purpose `filter` function. Here's the signature to aim for:

```go
func filter(nums []int, keep func(int) bool) []int
```

---

## Common Mistakes

**Forgetting that `append` returns a new slice**

This is the most common slice mistake in Go. `append` does not modify the slice in place — it returns a new slice. If you write `append(nums, 4)` without capturing the result, the original `nums` is unchanged. Always write `nums = append(nums, 4)`.

**Modifying elements through a sub-slice accidentally**

Because sub-slices share the same backing array, changes to one affect the other:

```go
a := []int{1, 2, 3, 4, 5}
b := a[1:3]  // b is [2, 3], shares memory with a
b[0] = 99
fmt.Println(a) // [1 99 3 4 5] — a was also changed!
```

If you need an independent copy, use `copy`.

**Indexing out of bounds**

Accessing an index that doesn't exist causes a panic (a runtime crash) in Go:

```go
nums := []int{1, 2, 3}
fmt.Println(nums[5]) // panic: index out of range [5] with length 3
```

Always check `len(slice)` before accessing a specific index, or use `range` which handles bounds automatically.

**Confusing `len` and `cap`**

`len` is how many elements are in the slice right now. `cap` is how many elements the underlying array can hold from the start of this slice. You access elements up to `len`, not `cap`. Accessing `slice[len(slice)]` panics even if there's capacity left.

---

## Key Takeaway

Arrays in Go are fixed-size and rarely used directly — their size is baked into the type. Slices are Go's real workhorse: dynamic, flexible, and backed by an array. You create them with literals, `make`, or by slicing existing slices. `append` grows a slice but always returns a new one — you must capture the return value. `range` gives you clean, idiomatic iteration with both index and value. Slices share their underlying array with sub-slices, so be careful about unexpected mutations, and use `copy` when you need an independent snapshot.

---

*[← Previous: Lesson 4 — Functions](/post/go/go-scratch-functions) | [Course Index: Go from Scratch](/series/go-from-scratch) | Next: Lesson 6 — Maps →*
