---
title: 'Lesson 9: range Gotchas — The loop variable that bit every Go team'
author: Atharva Pandey
keywords:
  - Go
  - golang
  - range
  - loop variable capture
  - goroutines
  - closures
  - Go 1.22
  - map iteration
tags:
  - Go tutorial
  - golang
series: "Idiomatic Go"
lesson: 9
date: '2025-12-01T00:00:00.000Z'
---

`range` looks harmless. Index and value, loop over a slice — what could go wrong? Quite a bit, it turns out. Some of the most insidious bugs I've seen in Go codebases come from assumptions about `range` that seem obvious but are wrong. One was painful enough that Go 1.22 changed the fundamental loop semantics to fix it. Let's walk through each gotcha and the correct pattern to use.

## The Problem

The classic range bug — the one that famously broke production code at teams large and small for years — is the loop variable capture problem. In Go versions before 1.22, every iteration of a `range` loop reused the same loop variable. Taking its address multiple times gave you the same address every time.

```go
// WRONG (pre-Go 1.22): all pointers point to the same variable
nums := []int{1, 2, 3, 4, 5}
ptrs := make([]*int, len(nums))
for i, v := range nums {
    ptrs[i] = &v // BUG: &v is the same address every iteration
}

for _, p := range ptrs {
    fmt.Println(*p) // prints 5 five times
}
```

After the loop, `v` holds `5` — the last element. Every pointer in `ptrs` points to that same variable. You've got five pointers that all dereference to `5`.

It gets worse with goroutines:

```go
// WRONG (pre-Go 1.22): all goroutines see the final value of item
items := []string{"a", "b", "c", "d"}
for _, item := range items {
    go func() {
        fmt.Println(item) // captures item by reference
    }()
}
// likely prints "d" four times
```

By the time the goroutines actually run, the loop has finished and `item` holds `"d"`. All four goroutines close over the same variable. This is the bug that Go 1.22 was explicitly designed to fix.

## The Idiomatic Way

**If you're on Go 1.22+**, the loop variable issue is fixed at the language level. Each iteration now creates a distinct variable, so taking addresses and spawning goroutines both work correctly without any workaround. Check your `go.mod` — if your toolchain is 1.22 or later, you're good.

**If you're on an older toolchain**, the fix is variable shadowing. It looks weird the first time you see it but it's idiomatic Go:

```go
// RIGHT (pre-Go 1.22): shadow the loop variable to create a new one
for i, v := range nums {
    v := v // new variable allocated each iteration
    ptrs[i] = &v
}
```

Or for goroutines, passing the value as a function argument is arguably cleaner:

```go
// RIGHT (pre-Go 1.22): pass as argument, evaluated at call site
for _, item := range items {
    go func(s string) {
        fmt.Println(s)
    }(item)
}
```

Passing `item` as a function argument evaluates it at the moment the `go` statement runs, creating a separate copy for each goroutine.

The other common gotcha: the range value is always a copy, not a reference. Mutating the range variable doesn't modify the original slice:

```go
type Point struct{ X, Y int }

// WRONG: modifying v does not modify the slice
points := []Point{{1, 2}, {3, 4}, {5, 6}}
for _, v := range points {
    v.X *= 10 // modifies the copy, not the slice element
}
fmt.Println(points) // [{1 2} {3 4} {5 6}] — unchanged
```

If you need in-place mutation, use the index:

```go
// RIGHT: modify through the index
for i := range points {
    points[i].X *= 10
}
fmt.Println(points) // [{10 2} {30 4} {50 6}]
```

## In The Wild

Map iteration order is another one that catches people out. Go deliberately randomizes map iteration order on every run — this isn't an implementation quirk, it's a language guarantee, added specifically to prevent code from accidentally depending on what used to be implementation-defined behavior.

```go
scores := map[string]int{"alice": 95, "bob": 87, "charlie": 91}
for name, score := range scores {
    fmt.Printf("%s: %d\n", name, score) // order changes every run
}
```

Any test that asserts on the order of map iteration will be flaky. The fix — sort the keys first — is worth committing to muscle memory:

```go
// RIGHT: collect keys, sort them, then iterate
keys := make([]string, 0, len(scores))
for k := range scores {
    keys = append(keys, k)
}
sort.Strings(keys)
for _, k := range keys {
    fmt.Printf("%s: %d\n", k, scores[k])
}
```

Ranging over channels is also worth mentioning: it's a clean way to consume all values until a channel is closed, but if the channel is never closed, the range loop blocks forever. Always close from the producer, ideally with `defer`:

```go
// RIGHT: close the channel to signal completion
func producer() <-chan int {
    ch := make(chan int)
    go func() {
        defer close(ch) // guaranteed to close even if something panics
        for i := 0; i < 5; i++ {
            ch <- i
        }
    }()
    return ch
}
```

And string ranging iterates runes (Unicode code points), not bytes. The index is the byte offset of the rune, not a sequential counter:

```go
s := "héllo"
for i, r := range s {
    fmt.Printf("index=%d rune=%c\n", i, r)
}
// index=0 rune=h
// index=1 rune=é  (é is 2 bytes in UTF-8)
// index=3 rune=l
// ...
```

If you need sequential indices, convert to `[]rune` first.

## The Gotchas

**Know your Go version.** The loop variable fix in 1.22 is gated by the `go` directive in your `go.mod`. If you're on 1.21 or earlier, you need the shadowing workaround. If your team uses a mix of toolchain versions, document which behavior you're relying on.

**The value copy cuts both ways.** The fact that range gives you a copy is usually an advantage — you can modify the copy without affecting the original. But if you're iterating over a slice of large structs specifically to modify them in place, using the index is the right move from the start, not an afterthought.

**Closed channel vs nil channel in select.** If you're using the "set channel to nil when closed" pattern in a `select`, ranging over the channel is not a direct substitute — a nil channel in a `select` case is silently ignored, which is a deliberate tool, but it's different from the completion signal that a range-over-channel provides. Don't confuse them.

## Key Takeaway

The core insight behind almost every `range` gotcha is the same: understand what `range` actually hands you in each context. For slices it gives you a copied value and an index into the original. For maps it gives you keys in random order. For channels it gives you values until the channel closes. For strings it gives you runes at byte offsets. The loop variable capture issue (fixed in 1.22) was a single design decision that compounded badly when closures and goroutines were involved. Every other quirk follows directly from understanding what range is doing. Once you have that mental model, none of this is surprising — it's just the language behaving exactly as specified.

---

← [Lesson 8: Capacity Matters](/post/go/go-idioms-capacity-matters/) | [Course Index](#) | [Lesson 10: Zero Values Are Useful →](/post/go/go-idioms-zero-values/)
