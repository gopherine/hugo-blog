---
title: "Lesson 8: String Internals — Immutable, backed by bytes, cheaper than you think"
author: Atharva Pandey
series: "Go Memory Model & Internals"
lesson: 8
keywords: [Go, golang, internals, memory]
tags: [Go tutorial, golang, internals]
date: '2025-04-15T00:00:00.000Z'
---

I used to reach for `[]byte` over `string` in performance-sensitive code, assuming strings were somehow more expensive because "immutability must cost something." That intuition was wrong. Understanding what a string actually is — a read-only slice header — corrected my assumptions and simplified a lot of code I had unnecessarily complicated.

## The Problem

Strings in Go are everywhere, and most developers use them without thinking about what they are at the memory level. This leads to a few common mistakes:

- Unnecessary conversions between `string` and `[]byte` that cause allocations
- Confusion about why modifying a `[]byte` derived from a string doesn't affect the string
- Suboptimal string concatenation patterns in loops
- Misunderstanding why comparing strings is cheap even for long strings (sometimes)

The root cause of all these is not knowing that a Go string is literally just two words: a pointer to an immutable byte array and a length. Once you see that, the behavior follows naturally.

## The Idiomatic Way

A Go string is represented identically to a slice header, minus the capacity field:

```go
// Conceptually, a string looks like this:
type StringHeader struct {
    Data uintptr // pointer to underlying bytes
    Len  int     // number of bytes
}
```

You can inspect this directly:

```go
package main

import (
    "fmt"
    "reflect"
    "unsafe"
)

func main() {
    s := "hello, world"

    // A string is always 16 bytes on 64-bit (pointer + length)
    fmt.Println("size of string:", unsafe.Sizeof(s)) // 16

    // Access the string header via reflect
    hdr := (*reflect.StringHeader)(unsafe.Pointer(&s))
    fmt.Printf("data ptr: 0x%x\n", hdr.Data)
    fmt.Printf("length:   %d\n", hdr.Len)

    // Substrings share the same backing array — no allocation!
    sub := s[7:] // "world"
    subHdr := (*reflect.StringHeader)(unsafe.Pointer(&sub))
    fmt.Printf("sub data ptr:  0x%x\n", subHdr.Data)
    fmt.Printf("original data: 0x%x\n", hdr.Data)
    fmt.Printf("offset:        %d\n", subHdr.Data-hdr.Data) // 7
    // sub.Data == s.Data + 7 — they share the same bytes
}
```

The critical implication: **taking a substring creates a new string header (two words on the stack) but does not copy any bytes**. The substring's data pointer is just the original pointer advanced by an offset. This makes substring operations essentially free.

```go
package main

import (
    "fmt"
    "strings"
    "unicode"
)

// Demonstrating zero-copy substring operations
func trimAndSplit(input string) []string {
    // strings.TrimSpace, strings.Split — all return substrings
    // sharing the original backing bytes
    trimmed := strings.TrimSpace(input)
    parts := strings.FieldsFunc(trimmed, unicode.IsSpace)

    // Each element of parts is a string header pointing into `input`
    // No byte copying occurred
    return parts
}

// String comparison: compares lengths first, then bytes
// Short-circuits on length mismatch — O(1) for different-length strings
func compareStrings() {
    s1 := strings.Repeat("a", 1_000_000)
    s2 := strings.Repeat("a", 999_999) // one byte shorter

    // This is O(1) — length check short-circuits immediately
    fmt.Println(s1 == s2) // false — instant

    s3 := s1 + "x"
    s4 := s1 + "y"
    // These have the same length — comparison must scan all bytes: O(n)
    // In practice the runtime uses optimized SIMD comparison routines
    fmt.Println(s3 == s4) // false — scanned to last byte
}

func main() {
    parts := trimAndSplit("  hello   world   go  ")
    fmt.Println(parts)
    compareStrings()
}
```

Now, string-to-`[]byte` conversion. This is where allocation happens — and where many developers are more cautious than they need to be:

```go
package main

import (
    "fmt"
    "unsafe"
)

func main() {
    s := "immutable string"

    // Standard conversion: allocates a new byte slice, copies the data
    // Required because []byte is mutable and string is not
    b := []byte(s)
    b[0] = 'I' // modifying the byte slice
    fmt.Println(string(b)) // "Immutable string"
    fmt.Println(s)         // "immutable string" — unchanged

    // The Go compiler is smart: in certain contexts, it avoids the copy
    // For example, passing string to a function expecting []byte in a
    // range over bytes — the compiler may optimize away the allocation

    // Safe zero-copy read-only access (don't do this in production
    // unless you're absolutely sure about lifetime and immutability):
    bSlice := unsafe.Slice(unsafe.StringData(s), len(s))
    fmt.Println(bSlice[0]) // 105 ('i') — reading is fine
    // bSlice[0] = 'x'     // DO NOT DO: undefined behavior, string is immutable
}
```

## In The Wild

The most common string performance issue I see is naive concatenation in loops:

```go
package main

import (
    "fmt"
    "strings"
)

// BAD: O(n²) allocations — each iteration creates a new string
func buildStringBad(parts []string) string {
    result := ""
    for _, p := range parts {
        result += p + ", " // allocates a new string each time
    }
    return result
}

// GOOD: strings.Builder — single allocation, amortized growth
func buildStringGood(parts []string) string {
    var sb strings.Builder
    // Optional but helpful: pre-size if you know the total length
    total := 0
    for _, p := range parts {
        total += len(p) + 2
    }
    sb.Grow(total)

    for i, p := range parts {
        sb.WriteString(p)
        if i < len(parts)-1 {
            sb.WriteString(", ")
        }
    }
    return sb.String()
}

// ALSO GOOD: strings.Join for simple cases
func buildStringJoin(parts []string) string {
    return strings.Join(parts, ", ")
}

func main() {
    data := make([]string, 1000)
    for i := range data {
        data[i] = fmt.Sprintf("item%d", i)
    }

    // Don't run buildStringBad with a large slice in production
    r1 := buildStringGood(data)
    r2 := buildStringJoin(data)
    fmt.Println(len(r1) == len(r2)) // true — same result
}
```

`strings.Builder` is backed by a `[]byte` that grows as needed. Its `String()` method performs a single conversion at the end. Since Go 1.20, the compiler can sometimes convert `[]byte` to `string` in `sb.String()` without copying (using the unsafe string trick internally), making it even more efficient.

Another real pattern: using `strings.Reader` for zero-copy streaming:

```go
package main

import (
    "fmt"
    "io"
    "strings"
)

func processFromString(data string) (int, error) {
    // strings.NewReader wraps a string as an io.Reader
    // No copy: the reader holds the string header directly
    r := strings.NewReader(data)

    buf := make([]byte, 256)
    total := 0
    for {
        n, err := r.Read(buf)
        total += n
        if err == io.EOF {
            break
        }
        if err != nil {
            return total, err
        }
    }
    return total, nil
}

func main() {
    payload := strings.Repeat("Go is great! ", 100)
    n, err := processFromString(payload)
    fmt.Printf("processed %d bytes, err=%v\n", n, err)
    // The original string was never copied — just read through a Reader interface
}
```

## The Gotchas

**Unicode and byte indexing**: Go strings are sequences of bytes, not characters. A single Unicode code point (rune) may be 1–4 bytes in UTF-8. Indexing a string with `s[i]` gives you a byte, not a character:

```go
package main

import "fmt"

func main() {
    s := "Hello, 世界"

    fmt.Println(len(s))    // 13 — bytes, not characters
    fmt.Println(s[7])      // 228 — first byte of '世' (0xE4), not a character

    // To iterate by character (rune), use range:
    for i, r := range s {
        if i < 10 {
            fmt.Printf("s[%d] = %c (U+%04X)\n", i, r, r)
        }
    }
    // Note: i jumps by the rune's byte width, not by 1
}
```

**The compiler's string([]byte) optimization**: the Go compiler avoids allocation in specific narrow cases when converting `[]byte` to `string` — notably in map lookups and string comparisons. You don't need to cache these conversions manually:

```go
package main

import "fmt"

func main() {
    m := map[string]int{"hello": 1, "world": 2}
    key := []byte("hello")

    // This does NOT allocate a new string in modern Go:
    // the compiler uses the byte slice bytes directly for the map lookup
    val := m[string(key)]
    fmt.Println(val) // 1

    // Similarly for switch statements with string(b)
    switch string(key) {
    case "hello":
        fmt.Println("matched")
    }
}
```

**String interning**: Go interns compile-time string constants — two string literals with identical content share the same backing bytes. This means pointer comparison (via `reflect.StringHeader`) can sometimes tell you if two strings are "the same allocation," but you should never rely on this for correctness. Use `==` for equality, which compares bytes.

**Large string substrings and GC**: because a substring keeps the entire original backing array alive (it holds a pointer into it), a small substring of a very large string can prevent the large string from being collected. If you extract a small piece of a large string and need to hold it long-term, convert it through a `strings.Builder` or `string([]byte(...))` to get an independent copy:

```go
package main

import "fmt"

func extractSmallPiece(huge string) string {
    // This keeps the entire huge string alive in memory!
    // return huge[1000:1010]

    // This creates an independent copy — the huge string can be GC'd
    piece := huge[1000:1010]
    independent := string([]byte(piece)) // force copy
    return independent
}

func main() {
    large := make([]byte, 1_000_000)
    for i := range large {
        large[i] = byte('a' + i%26)
    }
    s := string(large)
    piece := extractSmallPiece(s)
    fmt.Println(piece) // 10 bytes, independently allocated
    // s and large are now eligible for GC
}
```

## Key Takeaway

A Go string is a read-only two-word value: a pointer to bytes and a length. This design means:
- Substrings are free — they're just adjusted headers pointing into the same bytes
- String copying is cheap — only 16 bytes, regardless of content
- `string` to `[]byte` conversion allocates and copies (required for mutability)
- `[]byte` to `string` conversion also normally allocates, but the compiler optimizes specific patterns (map lookups, comparisons) to avoid it
- Long-lived substrings of large strings prevent GC of the backing bytes — copy them if needed

The practical rules:
- Use `strings.Builder` for concatenation in loops, not `+=`
- Use `strings.Reader` to wrap strings as `io.Reader` without copying
- Use range loops (not index access) when you care about Unicode characters
- Be aware that a small substring can pin a large backing array — copy when holding long-term

---

**Series: Go Memory Model & Internals**

[← Lesson 7: Values Copy vs Share](../go-internals-copy-share)

---

🎓 **Course Complete!** You've finished the Go Memory Model & Internals series. From interface two-word representation to string headers, you now have a mental model of how Go manages memory at the runtime level. These foundations will inform every performance-sensitive design decision you make going forward.

**Full series:**
1. [Interface Representation](../go-internals-interfaces)
2. [Nil Interface Gotchas](../go-internals-nil-interface)
3. [Method Sets and Addressability](../go-internals-method-sets)
4. [Escape Analysis Deep Dive](../go-internals-escape-analysis)
5. [GC Behavior and Tuning](../go-internals-gc)
6. [Memory Alignment and Struct Padding](../go-internals-alignment)
7. [Values Copy vs Share](../go-internals-copy-share)
8. **String Internals** ← you are here
