---
title: "Lesson 7: Strings, Runes, and Bytes — Strings aren't what you think"
author: "Atharva Pandey"
series: "Go from Scratch"
lesson: 7
keywords:
  - Go
  - golang
  - strings
  - runes
  - bytes
  - Go tutorial
  - beginner Go
tags:
  - Go tutorial
  - golang
  - beginner
date: "2024-02-02T00:00:00.000Z"
---

Strings look simple. You write `"hello"`, you print it, done. But when I first started working with non-ASCII text in Go — names with accents, emoji, Chinese characters — things started behaving oddly. `len("café")` returned 5, not 4. Iterating with a regular index loop gave me garbled output. It took me a while to understand why, and once I did, a lot of things clicked.

The short version: Go strings are sequences of bytes, not characters. Once you internalize that, the rest makes sense.

## The Basics

### Strings are immutable byte slices

A Go string is a read-only slice of bytes. When you write `"hello"`, you get 5 bytes — one per character, because ASCII characters each fit in a single byte. But many characters in the world require more than one byte to represent.

Go uses UTF-8 encoding for source files and strings. UTF-8 is a variable-width encoding: ASCII characters use 1 byte, but characters like `é`, `中`, or `🎉` use 2, 3, or 4 bytes respectively.

```go
package main

import "fmt"

func main() {
    s := "hello"
    fmt.Println(len(s)) // 5 — five bytes, five characters (ASCII)

    s2 := "café"
    fmt.Println(len(s2)) // 5 — four characters, but five bytes!
    // 'é' takes two bytes in UTF-8
}
```

`len()` always counts bytes, not characters. This surprises almost everyone the first time.

### Bytes vs runes

A **byte** is a `uint8` — a number from 0 to 255. Each element of a string is a byte.

A **rune** is an `int32` — it represents a Unicode code point. Think of a rune as "one character in the human sense." The word "rune" is Go's name for a Unicode code point.

```go
s := "café"

// Index access gives you a byte
b := s[0]
fmt.Println(b)         // 99 (the byte value of 'c')
fmt.Printf("%c\n", b) // c

// To get runes, use []rune conversion
runes := []rune(s)
fmt.Println(len(runes)) // 4 — four characters
fmt.Printf("%c\n", runes[3]) // é
```

When you need to work with characters (not bytes), convert to `[]rune` first, or use `range` (more on that below).

### range gives you runes

Here's where Go does something smart. When you use `range` on a string, you get runes, not bytes:

```go
s := "café"

// range decodes UTF-8 and gives you (byte index, rune)
for i, r := range s {
    fmt.Printf("index %d: %c (rune value %d)\n", i, r, r)
}
// index 0: c (rune value 99)
// index 1: a (rune value 97)
// index 2: f (rune value 102)
// index 3: é (rune value 233)
// Note: index jumps from 3 to 5 because 'é' is two bytes
```

The index is still the byte position, but the value you get is a decoded rune. This means `range` handles multi-byte characters correctly and is almost always what you want when iterating over a string.

### Strings are immutable

You cannot change a character in a string in place:

```go
s := "hello"
s[0] = 'H' // compile error: cannot assign to s[0]
```

To build modified strings, you need to convert to a byte slice, modify it, then convert back — or use `strings.Builder`.

### strings.Builder for building strings

Concatenating strings in a loop with `+` is inefficient. Each `+` creates a new string. For building strings incrementally, use `strings.Builder`:

```go
package main

import (
    "fmt"
    "strings"
)

func main() {
    var b strings.Builder

    for i := 0; i < 5; i++ {
        fmt.Fprintf(&b, "item %d, ", i)
    }

    result := b.String()
    fmt.Println(result)
    // item 0, item 1, item 2, item 3, item 4,
}
```

`strings.Builder` grows a buffer internally and only allocates the final string once when you call `.String()`. Use it any time you're building a string in a loop.

### The strings package

The `strings` package has everything you need for common string operations:

```go
import "strings"

s := "Hello, World!"

fmt.Println(strings.ToUpper(s))         // HELLO, WORLD!
fmt.Println(strings.Contains(s, "World")) // true
fmt.Println(strings.HasPrefix(s, "Hello")) // true
fmt.Println(strings.Replace(s, "World", "Go", 1)) // Hello, Go!
fmt.Println(strings.Split(s, ", "))     // [Hello World!]
fmt.Println(strings.TrimSpace("  hi  ")) // hi
```

## Try It Yourself

Let's write a function that counts the number of characters (runes) in a string and reverses it properly — handling multi-byte characters correctly:

```go
package main

import "fmt"

func runeCount(s string) int {
    count := 0
    for range s {
        count++
    }
    return count
}

func reverseString(s string) string {
    runes := []rune(s)
    // swap from both ends
    for i, j := 0, len(runes)-1; i < j; i, j = i+1, j-1 {
        runes[i], runes[j] = runes[j], runes[i]
    }
    return string(runes)
}

func main() {
    s := "café"

    fmt.Println("bytes:", len(s))           // 5
    fmt.Println("characters:", runeCount(s)) // 4

    fmt.Println(reverseString("hello"))  // olleh
    fmt.Println(reverseString("café"))   // éfac — correct!
}
```

Notice how converting to `[]rune` before reversing handles `é` as a single character, not two bytes. If you reversed the raw bytes, `é` would get corrupted.

## Common Mistakes

**Using `len()` when you want character count:**

```go
s := "日本語" // three Japanese characters

fmt.Println(len(s))           // 9 — nine bytes
fmt.Println(len([]rune(s)))   // 3 — three characters
```

**Indexing a string to get a character:**

```go
s := "café"
fmt.Printf("%c\n", s[3]) // NOT 'é' — you get the first byte of 'é', which is garbage
```

**Concatenating in a loop:**

```go
// SLOW — creates a new string on every iteration
result := ""
for _, word := range words {
    result += word + " "
}

// FAST — use strings.Builder
var b strings.Builder
for _, word := range words {
    b.WriteString(word)
    b.WriteString(" ")
}
result := b.String()
```

**Forgetting strings are immutable.** You'll need to work with `[]byte` or `[]rune` if you want to modify individual characters.

## Key Takeaway

Go strings are byte sequences, not character sequences. `len()` counts bytes. `range` iterates by rune (character). When you need to work with individual characters — especially non-ASCII ones — convert to `[]rune`. Use `strings.Builder` to build strings efficiently in loops. The `strings` package covers almost every string operation you'll need.

---

**Previous lesson:** [Lesson 6: Maps](/post/go/go-scratch-maps)

**Next lesson:** [Lesson 8: Structs](/post/go/go-scratch-structs)
