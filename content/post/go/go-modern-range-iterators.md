---
title: "Lesson 1: Range Over Integers and Iterators — for i := range 10 and the iterator protocol"
author: Atharva Pandey
series: "Modern Go: 1.21 to 1.26"
lesson: 1
keywords:
  - Go 1.22
  - range over integers
  - iterator protocol
  - iter package
  - golang iterators
  - for range
tags:
  - Go tutorial
  - golang
  - modern Go
date: "2024-06-04T00:00:00.000Z"
---

I have written `for i := 0; i < n; i++` so many times that my fingers type it on autopilot. The three-clause for loop is fine — it works, everyone understands it, and Go has always had it. But when Go 1.22 shipped `for i := range 10`, I stopped and stared at it for a moment. Not because it was complicated. Because it was so obviously the right thing that I wondered why it had taken this long.

Then I read the iterator proposal and realized the range-over-integers change was the small, friendly face of something much larger: a new push-based iterator protocol that lets you range over any data structure, lazily, without materializing everything into a slice first. That combination — clean integer ranges plus a composable iterator protocol — changed how I write data-processing pipelines in Go.

## The Problem

Before Go 1.22, ranging over a number required a loop like this:

```go
for i := 0; i < 10; i++ {
    fmt.Println(i)
}
```

Nothing wrong with it. But it is verbose for what is conceptually "iterate ten times." Python has `range(10)`. Rust has `0..10`. Go had nothing, and it was a small but constant friction point.

The deeper problem was iterators. If you built a custom collection — a tree, a ring buffer, a paginated API client — and wanted callers to range over it, you had two bad options. You could return a slice and materialize everything upfront, wasting memory for large or infinite sequences. Or you could expose a `Next() (T, bool)` style iterator and ask callers to write manual loops. Neither option was idiomatic. Neither composed well.

The standard library had the same problem. `bufio.Scanner`, `sql.Rows`, `filepath.Walk` — each with its own iteration API, each requiring different boilerplate. There was no unified pattern.

## How It Works

**Range over integers** is the simple part. In Go 1.22+, you can write:

```go
for i := range 10 {
    fmt.Println(i) // 0, 1, 2, ..., 9
}
```

That is it. `range n` gives you indices `0` through `n-1`. No off-by-one temptation. You can still use `_` if you do not need the index:

```go
for range 10 {
    doSomething()
}
```

**The iterator protocol** is the more interesting part, introduced alongside this in Go 1.23 under `iter.Seq` and `iter.Seq2`. An iterator in the new protocol is simply a function that accepts a `yield` function:

```go
// iter.Seq[V] is defined as:
type Seq[V any] func(yield func(V) bool)

// iter.Seq2[K, V] is defined as:
type Seq2[K, V any] func(yield func(K, V) bool)
```

You write a function that, when called, calls `yield` for each element. If `yield` returns `false`, the caller broke out of the loop — you stop producing values. If it returns `true`, keep going.

Here is a concrete example — an iterator over lines in a string, producing them lazily:

```go
func Lines(s string) iter.Seq[string] {
    return func(yield func(string) bool) {
        for len(s) > 0 {
            line, rest, _ := strings.Cut(s, "\n")
            s = rest
            if !yield(line) {
                return // caller broke
            }
        }
    }
}
```

And you use it with an ordinary `for range`:

```go
for line := range Lines(input) {
    fmt.Println(line)
}
```

The runtime wires `yield` to the loop body. The compiler handles the break-detection. You get lazy evaluation, early exit, and clean syntax — all without channels, goroutines, or materializing a slice.

## In Practice

The places where I have found this most useful:

**Paginated API clients.** Instead of loading all pages into memory before processing, I write an iterator that fetches one page at a time and yields each item:

```go
func FetchUsers(ctx context.Context, client *APIClient) iter.Seq2[User, error] {
    return func(yield func(User, error) bool) {
        cursor := ""
        for {
            page, next, err := client.ListUsers(ctx, cursor)
            if err != nil {
                yield(User{}, err)
                return
            }
            for _, u := range page {
                if !yield(u, nil) {
                    return
                }
            }
            if next == "" {
                return
            }
            cursor = next
        }
    }
}
```

Callers get clean range syntax and automatic backpressure — they control when to stop.

**Tree traversal.** An in-order traversal of a binary tree as an iterator yields nodes one at a time without allocating a slice of all nodes:

```go
func InOrder[T any](root *Node[T]) iter.Seq[T] {
    return func(yield func(T) bool) {
        var walk func(*Node[T]) bool
        walk = func(n *Node[T]) bool {
            if n == nil {
                return true
            }
            return walk(n.Left) && yield(n.Value) && walk(n.Right)
        }
        walk(root)
    }
}
```

**The `slices` and `maps` packages now accept iterators.** Functions like `slices.Collect` turn an `iter.Seq[T]` into a `[]T` when you actually need a slice. This gives you a clean boundary: process lazily, materialize only when necessary.

## The Gotchas

**Early termination responsibility.** When you write an iterator and the caller breaks out of the loop, `yield` returns false. You must check that return value and stop immediately. If you ignore it and keep calling `yield`, you will get a panic — the runtime detects this and panics with a clear message. This is intentional: the protocol requires that iterators honour early exit.

**Not goroutine-based.** Unlike channel-based iterators you may have used before, `iter.Seq` functions run synchronously in the caller's goroutine. There is no goroutine spawned, no channel buffer to tune, no risk of goroutine leaks. The `yield` function literally calls back into the loop body inline. This is both faster and simpler — but it means you cannot do concurrent work inside an iterator without managing that yourself.

**`iter.Seq2` for errors.** When your iterator can fail — network calls, file reads — use `iter.Seq2[T, error]` and yield the error alongside the value. Callers check each iteration. Do not panic inside an iterator for recoverable errors.

**Go version gating.** Range over integers needs Go 1.22. The full iterator protocol with `for range` over functions needs Go 1.23. If your module's `go.mod` is older, you will get compile errors. Update the `go` directive in `go.mod` first.

**Capture bugs in range-over-integers are gone.** One pleasant side effect: `for i := range 10` creates a new `i` each iteration (due to the loop variable fix in 1.22 which we cover in Lesson 3). No more accidental closure captures of a shared loop variable.

## Key Takeaway

Range over integers is a small quality-of-life improvement that removes a consistent friction from everyday Go code. The iterator protocol underneath it is the bigger deal — it gives Go a composable, lazy, allocation-efficient way to sequence values across any data source, without channels or manual cursor loops. Write iterators for anything you want to range over. Use `iter.Seq2` when errors are possible. Respect early exits. The standard library is already adopting this pattern, and your code should too.

---

**Next:** [Lesson 2: Enhanced HTTP Routing — Method patterns in net/http, no more gorilla/mux](/post/go/go-modern-http-routing/)
