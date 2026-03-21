---
title: "Lesson 6: Maps — Key-value pairs that power everything"
author: "Atharva Pandey"
series: "Go from Scratch"
lesson: 6
keywords:
  - Go
  - golang
  - maps
  - Go tutorial
  - beginner Go
tags:
  - Go tutorial
  - golang
  - beginner
date: "2024-01-27T00:00:00.000Z"
---

Every language has some version of the dictionary or hash map. Python calls it a `dict`, JavaScript calls it an `Object` or `Map`, Ruby calls it a `Hash`. In Go, it's just called a map. Once you understand how Go maps work, you'll reach for them constantly — they're one of the most useful data structures in the language.

## The Basics

### Creating a map

The zero value of a map is `nil`. A nil map is readable (it returns zero values) but not writable. If you try to write to a nil map, your program panics. The safe way to create a map is with `make`:

```go
package main

import "fmt"

func main() {
    // Create a map from string keys to int values
    scores := make(map[string]int)

    scores["Alice"] = 95
    scores["Bob"] = 82
    scores["Charlie"] = 91

    fmt.Println(scores["Alice"]) // 95
}
```

You can also use a map literal when you already know the initial values:

```go
scores := map[string]int{
    "Alice":   95,
    "Bob":     82,
    "Charlie": 91,
}
```

The syntax is `map[KeyType]ValueType`. The key can be any comparable type — strings, ints, booleans, even structs (as long as they don't contain slices or maps). The value can be anything at all.

### Reading and writing

Reading a key that doesn't exist doesn't panic — it returns the zero value for the value type:

```go
scores := map[string]int{
    "Alice": 95,
}

fmt.Println(scores["Alice"])  // 95
fmt.Println(scores["Dave"])   // 0 — Dave doesn't exist, zero value returned
```

Writing is straightforward assignment:

```go
scores["Dave"] = 78   // adds a new key
scores["Alice"] = 99  // updates an existing key
```

### The comma-ok pattern

The problem with reading a missing key is that you can't tell the difference between "key doesn't exist" and "key exists with the zero value." What if Bob genuinely scored 0? You'd get 0 either way.

Go solves this with the comma-ok idiom:

```go
scores := map[string]int{
    "Alice": 95,
    "Bob":   0,
}

// Two-variable form: value and a boolean
aliceScore, ok := scores["Alice"]
fmt.Println(aliceScore, ok) // 95 true

bobScore, ok := scores["Bob"]
fmt.Println(bobScore, ok) // 0 true — Bob exists, his score really is 0

daveScore, ok := scores["Dave"]
fmt.Println(daveScore, ok) // 0 false — Dave doesn't exist
```

The second variable (`ok`) is `true` if the key was found, `false` if it wasn't. This is one of Go's most common patterns — you'll see it everywhere.

### Deleting entries

Use the built-in `delete` function:

```go
scores := map[string]int{
    "Alice": 95,
    "Bob":   82,
}

delete(scores, "Bob")
fmt.Println(len(scores)) // 1 — only Alice remains
```

Deleting a key that doesn't exist is a no-op — it doesn't panic.

### Iterating with range

```go
scores := map[string]int{
    "Alice":   95,
    "Bob":     82,
    "Charlie": 91,
}

for name, score := range scores {
    fmt.Printf("%s scored %d\n", name, score)
}
```

One important thing: **map iteration order is randomized in Go**. Every time you run this loop, you might get the keys in a different order. This is intentional — Go deliberately randomizes it to prevent developers from accidentally relying on a specific order. If you need sorted output, collect the keys into a slice and sort it first.

### The nil map panic

This is the most common map mistake for beginners:

```go
var scores map[string]int // scores is nil

scores["Alice"] = 95 // PANIC: assignment to entry in nil map
```

Declare with `make` or a literal, and you'll never hit this.

## Try It Yourself

Build a word counter. Read a slice of words and count how many times each word appears:

```go
package main

import (
    "fmt"
    "strings"
)

func wordCount(text string) map[string]int {
    counts := make(map[string]int)
    words := strings.Fields(text)

    for _, word := range words {
        counts[word]++ // if key doesn't exist, zero value (0) is used, then incremented
    }

    return counts
}

func main() {
    text := "the quick brown fox jumps over the lazy dog the fox"
    counts := wordCount(text)

    fmt.Println("the:", counts["the"])   // 3
    fmt.Println("fox:", counts["fox"])   // 2
    fmt.Println("cat:", counts["cat"])   // 0 — not in text
}
```

Notice that `counts[word]++` works even when `word` isn't in the map yet. Go fetches the zero value (0), increments it to 1, and stores it back. This is a very common Go pattern.

## Common Mistakes

**Forgetting comma-ok when checking existence:**

```go
// WRONG — can't tell if the key exists or just has a zero value
if scores["Dave"] == 0 {
    fmt.Println("Dave not found") // might be wrong!
}

// RIGHT
if _, ok := scores["Dave"]; !ok {
    fmt.Println("Dave not found")
}
```

**Assuming iteration order is stable:**

```go
// WRONG — don't rely on this order being consistent
for k, v := range myMap {
    process(k, v) // order changes each run
}
```

**Declaring a map without initializing it:**

```go
// WRONG
var m map[string]int
m["key"] = 1 // panic

// RIGHT
m := make(map[string]int)
m["key"] = 1
```

**Maps are reference types.** When you pass a map to a function, the function gets a reference to the same underlying data. Changes inside the function affect the original:

```go
func addBonus(scores map[string]int) {
    for k := range scores {
        scores[k] += 10 // modifies the original map
    }
}
```

This is different from slices, which have a header that's copied. With maps, there's no copy at all.

## Key Takeaway

Maps give you fast key-value lookups. Use `make(map[K]V)` to create one — never write to a nil map. Use the comma-ok pattern (`value, ok := m[key]`) when you need to distinguish "key not found" from "key has a zero value." Remember that map iteration order is random by design, and maps are always passed by reference.

---

**Previous lesson:** [Lesson 5: Arrays and Slices](/post/go/go-scratch-arrays-slices)

**Next lesson:** [Lesson 7: Strings, Runes, and Bytes](/post/go/go-scratch-strings)
