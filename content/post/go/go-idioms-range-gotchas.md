---
title: 'Go Idioms: range Gotchas'
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
date: '2025-12-01T00:00:00.000Z'
---
s most-used constructs, and it looks completely harmless. Loop over a slice, get the index and value — straightforward. But `range` carries a handful of behaviors that have caused some of the most insidious bugs in Go codebases. Some have been around since the beginning of the language. One was so painful that Go 1.22 changed the fundamental loop semantics to fix it. This article walks through each gotcha, shows exactly why it happens, and gives you the correct pattern to use.

## Gotcha 1: The Loop Variable Is a Copy

When you range over a slice of structs, the value variable is a copy of each element, not a reference to it. Mutations through the value variable do not affect the original slice.

```go
type Point struct{ X, Y int }

// WRONG: modifying v does not modify the slice
points := []Point{{1, 2}, {3, 4}, {5, 6}}
for _, v := range points {
    v.X *= 10 // modifies the copy, not the slice element
}
fmt.Println(points) // [{1 2} {3 4} {5 6}] — unchanged
```

If you need to mutate slice elements in place, use the index:

```go
// RIGHT: modify through the index
for i := range points {
    points[i].X *= 10
}
fmt.Println(points) // [{10 2} {30 4} {50 6}]
```

This is not a bug — it is the documented behavior. But it surprises people often enough to be worth treating as a gotcha, especially when the elements are large structs and the intent is clearly to modify them.

## Gotcha 2: Pointer to Loop Variable (Pre-Go 1.22)

This is the most famous Go gotcha, and it was the subject of countless debugging sessions for years. In Go versions before 1.22, the loop variable in a `range` loop was a *single variable reused across all iterations*. Taking its address gave you the same address every time.

```go
// WRONG (pre-Go 1.22): all pointers point to the same variable
nums := []int{1, 2, 3, 4, 5}
ptrs := make([]*int, len(nums))
for i, v := range nums {
    ptrs[i] = &v // BUG: &v is the same address every iteration
}

for _, p := range ptrs {
    fmt.Println(*p) // prints 5 five times — the final value of v
}
```

After the loop, `v` holds the value `5` (the last element). Every pointer in `ptrs` points to that same variable, so dereferencing any of them gives `5`.

The classic fix before Go 1.22 was to shadow the loop variable inside the loop body:

```go
// RIGHT (pre-Go 1.22 fix): capture a new variable each iteration
for i, v := range nums {
    v := v // shadow: new variable allocated each iteration
    ptrs[i] = &v
}
```

The inner `v := v` creates a new local variable that is distinct for each iteration. Each `&v` now points to a different address.

## Gotcha 3: Goroutine + Closure Capture (The Notorious One)

The loop variable bug becomes especially nasty when combined with goroutines. This pattern broke production code at companies large and small for years:

```go
// WRONG (pre-Go 1.22): all goroutines see the final value of item
items := []string{"a", "b", "c", "d"}
for _, item := range items {
    go func() {
        fmt.Println(item) // captures item by reference
    }()
}
// likely prints "d" four times (or some mix depending on scheduling)
```

By the time the goroutines run, the loop has finished and `item` holds `"d"`. All four goroutines close over the same `item` variable.

The pre-1.22 fix:

```go
// RIGHT (pre-Go 1.22): pass value as argument
for _, item := range items {
    item := item // or: go func(item string) { ... }(item)
    go func() {
        fmt.Println(item)
    }()
}
```

Or equivalently:

```go
for _, item := range items {
    go func(s string) {
        fmt.Println(s)
    }(item)
}
```

Passing `item` as a function argument evaluates it at the time of the `go` statement, creating a separate copy for each goroutine.

## Gotcha 4: The Go 1.22 Fix and What Changed

Go 1.22 (released February 2024) changed the semantics of `for` loop variables. In Go 1.22 and later, each iteration of a `for` loop creates a new variable, rather than reusing the same one. This means the pointer and goroutine examples above work correctly *without* any shadowing workaround.

```go
// In Go 1.22+: this now works correctly
nums := []int{1, 2, 3}
ptrs := make([]*int, len(nums))
for i, v := range nums {
    ptrs[i] = &v // each iteration's v is a distinct variable
}
for _, p := range ptrs {
    fmt.Println(*p) // prints 1, 2, 3 — correct
}
```

This is a semantic change that was carefully designed to be backward-compatible in most cases. The old behavior was almost never intentionally relied upon — it was almost always a bug. The `go.mod` toolchain version gates the new behavior, so existing code compiled with older toolchains is unaffected.

**Why this matters:** If your codebase has a `go.mod` declaring `go 1.22` or later, the loop variable shadowing workarounds are no longer necessary. If your team is still on 1.21 or earlier, they are still essential. Know your toolchain version.

## Gotcha 5: Map Iteration Order Is Not Defined

Go deliberately randomizes map iteration order on every run. This is not an implementation detail — it is a language guarantee. Code that depends on map iteration order is incorrect.

```go
// WRONG assumption: map range order is predictable
scores := map[string]int{"alice": 95, "bob": 87, "charlie": 91}
for name, score := range scores {
    fmt.Printf("%s: %d\n", name, score)
}
// Order changes every run — do not rely on it
```

The randomization was added intentionally to prevent programmers from accidentally depending on what was previously an implementation-defined order. If you need sorted output, sort the keys:

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

This pattern is common enough that it is worth committing to muscle memory. Any time you find yourself needing deterministic map output — for tests, for logging, for report generation — sort the keys first.

## Gotcha 6: Ranging Over Channels

You can range over a channel, and it is a clean way to consume all values until the channel is closed. The gotcha is that if the channel is never closed, the range loop never terminates — it blocks forever.

```go
// WRONG: producer never closes the channel — range loops forever
func producer() <-chan int {
    ch := make(chan int)
    go func() {
        for i := 0; i < 5; i++ {
            ch <- i
        }
        // BUG: forgot to close(ch)
    }()
    return ch
}

func main() {
    for v := range producer() { // blocks after receiving 5 values
        fmt.Println(v)
    }
    fmt.Println("done") // never reached
}
```

The fix is to always close the channel when the producer is done:

```go
// RIGHT: close the channel to signal completion
func producer() <-chan int {
    ch := make(chan int)
    go func() {
        defer close(ch) // guaranteed to close even if loop panics
        for i := 0; i < 5; i++ {
            ch <- i
        }
    }()
    return ch
}
```

`defer close(ch)` is the idiomatic pattern. It guarantees the channel is closed even if something panics in the goroutine, and it clearly communicates intent: this goroutine owns the channel and is responsible for closing it.

## Gotcha 7: range on Strings Is Rune-Based, Not Byte-Based

When you range over a string, Go iterates over *runes* (Unicode code points), not bytes. The index is the byte offset of the rune, not a sequential counter.

```go
s := "héllo"
for i, r := range s {
    fmt.Printf("index=%d rune=%c\n", i, r)
}
// index=0 rune=h
// index=1 rune=é  (é is 2 bytes in UTF-8)
// index=3 rune=l
// index=4 rune=l
// index=5 rune=o
```

The index jumps from 1 to 3 because `é` takes 2 bytes in UTF-8. If you need byte-level access, convert to `[]byte` first, or use indexing directly. If you need sequential numeric indices for runes, keep a separate counter.

```go
// For sequential rune indices:
for i, r := range []rune(s) {
    fmt.Printf("rune index=%d char=%c\n", i, r)
}
// rune index=0 char=h
// rune index=1 char=é
// rune index=2 char=l
// ...
```

## Putting It Together

`range` is powerful and usually the right tool. The key behaviors to keep in your mental model:

- The value variable is always a copy — use index access for in-place mutation
- Pre-Go 1.22: loop variable is shared across iterations — shadow it before taking addresses or spawning goroutines
- Go 1.22+: each iteration gets its own variable — the old workarounds are no longer needed
- Maps iterate in random order by design — sort keys when order matters
- Channel ranges block until the channel is closed — always close from the producer, ideally with `defer`
- String ranges iterate runes with byte-offset indices — not sequential integers

Most of these gotchas come down to one underlying principle: understand what `range` gives you in each context, and be explicit when the default behavior is not what you need.
