---
title: "Lesson 3: Method Sets and Addressability — Why your value can't satisfy that interface"
author: Atharva Pandey
series: "Go Memory Model & Internals"
lesson: 3
keywords: [Go, golang, internals, memory]
tags: [Go tutorial, golang, internals]
date: '2024-08-25T00:00:00.000Z'
---

I spent a solid twenty minutes staring at a compilation error that read "does not implement interface (Write method has pointer receiver)" and thinking: I *can see* the Write method right there. What is Go complaining about? Once I understood method sets and why addressability matters, the rule became obvious — and I have never needed to look it up since.

## The Problem

Go lets you define methods with either a value receiver or a pointer receiver:

```go
func (s MyStruct) ValueMethod()   {}
func (s *MyStruct) PointerMethod() {}
```

The question is: which combination of receiver types means a given type satisfies a given interface? The answer involves **method sets** — the formal set of methods available on a type — and **addressability** — whether the Go compiler can take the address of a value automatically.

Most developers encounter this in the form of a compile error when trying to store a value type in an interface where the method is defined on the pointer type. The fix is often just to use `&myVar` instead of `myVar`, but knowing *why* saves you from guessing.

## The Idiomatic Way

The Go spec defines method sets precisely:

| Type | Method set includes |
|------|---------------------|
| `T` | Methods with receiver `T` |
| `*T` | Methods with receiver `T` *and* `*T` |
| Interface | All methods listed in the interface |

A type satisfies an interface when its method set is a superset of the interface's method set. This means:

- A value of type `T` can only satisfy interfaces whose methods all have value receivers
- A value of type `*T` can satisfy interfaces whose methods use either pointer *or* value receivers

```go
package main

import "fmt"

type Stringer interface {
    String() string
}

type Counter struct {
    n int
}

// Value receiver — available on both Counter and *Counter
func (c Counter) String() string {
    return fmt.Sprintf("counter(%d)", c.n)
}

// Pointer receiver — available only on *Counter
func (c *Counter) Inc() {
    c.n++
}

type Writer interface {
    Write(p []byte) (int, error)
}

type Logger struct{}

// Pointer receiver only
func (l *Logger) Write(p []byte) (int, error) {
    fmt.Print(string(p))
    return len(p), nil
}

func main() {
    c := Counter{n: 0}
    pc := &Counter{n: 0}

    // OK: Counter has String() with value receiver
    var s1 Stringer = c
    fmt.Println(s1.String())

    // Also OK: *Counter inherits all value receiver methods
    var s2 Stringer = pc
    fmt.Println(s2.String())

    l := Logger{}
    // COMPILE ERROR if uncommented:
    // var w Writer = l      // Logger's Write has pointer receiver
    var w Writer = &l        // OK: *Logger satisfies Writer
    _, _ = w.Write([]byte("hello\n"))
}
```

Now, why can't Go just automatically take the address and let `Counter` satisfy `Writer`? The answer is addressability. Consider what happens at the call site:

```go
package main

import "fmt"

type Incrementer interface {
    Inc()
}

type Counter struct{ n int }

func (c *Counter) Inc() { c.n++ }

func doIncrement(i Incrementer) {
    i.Inc()
}

func main() {
    c := Counter{n: 0}

    // If this were allowed, what would Go do?
    // doIncrement(c)  // hypothetical

    // It would have to take c's address to get *Counter
    // But the interface stores a copy — incrementing it would have no effect on c
    // Go's designers decided this confusing behavior is worse than a compile error

    // The correct way:
    doIncrement(&c)
    fmt.Println(c.n) // 1 — actual c was modified
}
```

The rule exists to prevent silent correctness bugs. If Go automatically took the address of a value to satisfy a pointer-receiver interface, the method would operate on a copy, and mutations would be lost. The compile error is Go telling you "this would be confusing, be explicit."

```go
package main

import "fmt"

type Resetter interface {
    Reset()
}

type Buffer struct {
    data []byte
    pos  int
}

// Pointer receiver — modifies the receiver
func (b *Buffer) Reset() {
    b.data = b.data[:0]
    b.pos = 0
}

// Value receiver — reads but doesn't modify
func (b Buffer) Len() int {
    return len(b.data) - b.pos
}

func resetAll(rs []Resetter) {
    for _, r := range rs {
        r.Reset()
    }
}

func main() {
    b1 := &Buffer{data: []byte("hello")}
    b2 := &Buffer{data: []byte("world")}

    // Both are *Buffer, which satisfies Resetter
    resetAll([]Resetter{b1, b2})
    fmt.Println(b1.Len(), b2.Len()) // 0 0

    // Value type in map — not addressable!
    m := map[string]Buffer{
        "a": {data: []byte("test")},
    }
    // m["a"].Reset() — COMPILE ERROR: cannot take the address of m["a"]
    // Map values are not addressable. Use a pointer:
    m2 := map[string]*Buffer{
        "a": {data: []byte("test")},
    }
    m2["a"].Reset()
    fmt.Println(m2["a"].Len()) // 0
}
```

## In The Wild

This comes up often with the standard library. `sort.Interface` requires three methods, all typically implemented on pointer receivers when the sort modifies state. `io.Writer` and `io.Reader` are almost always satisfied by pointer types. The pattern is consistent: if a method mutates state, use a pointer receiver, and store pointers in interfaces.

```go
package main

import (
    "fmt"
    "sort"
)

// Implementing sort.Interface — classic pointer receiver pattern
type SortableRecords struct {
    records []struct {
        name  string
        score int
    }
}

func (s *SortableRecords) Len() int { return len(s.records) }
func (s *SortableRecords) Less(i, j int) bool {
    return s.records[i].score > s.records[j].score // descending
}
func (s *SortableRecords) Swap(i, j int) {
    s.records[i], s.records[j] = s.records[j], s.records[i]
}

func main() {
    sr := &SortableRecords{
        records: []struct {
            name  string
            score int
        }{
            {"Alice", 85},
            {"Bob", 92},
            {"Carol", 78},
        },
    }

    sort.Sort(sr)

    for _, r := range sr.records {
        fmt.Printf("%s: %d\n", r.name, r.score)
    }
    // Output: Bob: 92, Alice: 85, Carol: 78
}
```

## The Gotchas

**Embedding and method set promotion**: when you embed a type, the outer type's method set includes the embedded type's method set. But there's a subtlety:

```go
package main

import "fmt"

type Inner struct{ val int }
func (i *Inner) SetVal(v int) { i.val = v }
func (i Inner) GetVal() int   { return i.val }

type Outer struct {
    Inner // embedded by value
}

type Setter interface {
    SetVal(int)
}

func main() {
    o := Outer{Inner: Inner{val: 0}}

    // *Outer promotes *Inner's methods — SetVal is available
    var s Setter = &o
    s.SetVal(42)
    fmt.Println(o.GetVal()) // 42

    // Outer (value) does NOT satisfy Setter
    // because Inner is embedded by value, not pointer
    // and SetVal requires *Inner receiver
    // Uncommenting causes compile error:
    // var s2 Setter = o
    _ = s
}
```

If you embed by pointer (`*Inner`), the outer value type *does* gain access to pointer receiver methods — because the embedded field is already a pointer, and its address can always be taken.

**Interface satisfaction at compile time vs. runtime**: use a blank identifier assignment to check interface satisfaction at compile time rather than discovering it at runtime:

```go
// Compile-time assertion: *MyType must satisfy io.Writer
var _ io.Writer = (*MyType)(nil)

// Or with a concrete value:
var _ fmt.Stringer = MyType{}
```

This pattern is common in well-maintained Go codebases and costs nothing at runtime.

## Key Takeaway

Method sets determine interface satisfaction. A value type `T` only includes value-receiver methods in its method set. A pointer type `*T` includes *both* pointer-receiver and value-receiver methods. Go deliberately does not auto-address values to satisfy pointer-receiver interfaces because doing so silently on copies would cause mutation to be lost.

The practical rules:
- Use pointer receivers for methods that mutate state; store those types as pointers in interfaces
- Use value receivers for methods that only read; those can be called on both values and pointers
- Map values and function return values are not addressable — you cannot call pointer-receiver methods on them directly
- Use `var _ Interface = (*ConcreteType)(nil)` for compile-time interface satisfaction checks

---

**Series: Go Memory Model & Internals**

[← Lesson 2: Nil Interface Gotchas](../go-internals-nil-interface) | [Lesson 4: Escape Analysis Deep Dive →](../go-internals-escape-analysis)
