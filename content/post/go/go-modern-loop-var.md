---
title: "Lesson 3: Loop Variable Fix — The Go 1.22 change that fixed a decade of bugs"
author: Atharva Pandey
series: "Modern Go: 1.21 to 1.26"
lesson: 3
keywords:
  - Go 1.22
  - loop variable capture
  - goroutine closure
  - range loop bug
  - golang loop fix
  - per-iteration variable
tags:
  - Go tutorial
  - golang
  - modern Go
date: "2024-10-13T00:00:00.000Z"
---

If you have written Go for more than a few weeks, you have hit this bug. Or you have reviewed code that had it and caught it. Or — if you were unlucky — you shipped it to production and spent an hour staring at a data race report wondering what on earth was happening. The loop variable capture bug was so common that it was essentially a Go rite of passage. Every Go tutorial mentioned it. Every linter had a rule for it. And for a decade, the answer was always "just copy the variable inside the loop."

Go 1.22 fixed it. Not with a warning. Not with a lint rule. By changing the language itself.

## The Problem

Here is the classic version of the bug:

```go
funcs := make([]func(), 5)
for i, v := range []int{1, 2, 3, 4, 5} {
    funcs[i] = func() {
        fmt.Println(v) // captures v
    }
}
for _, f := range funcs {
    f()
}
```

Before Go 1.22, this prints `5 5 5 5 5`. Every closure captures the same variable `v`, and by the time the closures run, `v` has the value from the last iteration.

The goroutine version was even more dangerous:

```go
for _, item := range items {
    go func() {
        process(item) // BUG: all goroutines may see the same item
    }()
}
```

You intended to process each item concurrently. Instead, all goroutines captured the same `item` variable. Depending on scheduling, they might all process the last item, or they might process a random subset, or the behaviour might change between runs. The race detector would catch data races if `item` was being written concurrently, but in this case it was only being read — just read from the wrong value. Silent, wrong output.

The fix everyone used was to shadow the variable:

```go
for _, item := range items {
    item := item // create a new variable scoped to this iteration
    go func() {
        process(item) // now captures the per-iteration copy
    }()
}
```

This works, but it is boilerplate. Worse, it is easy to forget, especially for people newer to Go who have not been burned yet. And forgetting it once in a codebase that has thousands of loops is all it takes.

## How It Works

Go 1.22 changed the semantics of `for` loop variables: each iteration now gets its own variable. Instead of one variable that is updated each loop iteration, the compiler creates a fresh variable per iteration.

Conceptually, the compiler transforms:

```go
for i, v := range slice {
    // ...
}
```

into something equivalent to:

```go
for _i, _v := range slice {
    i := _i
    v := _v
    // ...
}
```

where `i` and `v` are freshly allocated each time. Closures that capture them now capture a distinct value per iteration, not a shared location.

This applies to all three forms of `for` loops:

```go
// for range with index and value
for i, v := range slice { ... }

// for range with just value
for _, v := range slice { ... }

// three-clause for
for i := 0; i < n; i++ { ... }
```

All three now give you per-iteration variables in Go 1.22+.

The change is controlled by the `go` directive in your `go.mod` file. If your module declares `go 1.21` or earlier, you get the old behaviour. This is intentional — the Go team could not change the semantics globally without breaking existing code that relied on the old behaviour (yes, some code did). Bump your `go.mod` to `go 1.22` and the new semantics activate.

## In Practice

The goroutine example just works now:

```go
for _, item := range items {
    go func() {
        process(item) // correct: captures per-iteration copy of item
    }()
}
```

The closure example works:

```go
funcs := make([]func(), 5)
for i, v := range []int{1, 2, 3, 4, 5} {
    funcs[i] = func() {
        fmt.Println(v) // prints 1, 2, 3, 4, 5 — correct
    }
}
```

The `item := item` shadow pattern is now unnecessary in almost all cases. You can remove it from your codebase when you move to Go 1.22+.

I went through a mid-sized service after upgrading and deleted maybe forty of these shadow lines. The code became meaningfully cleaner — the idiom had been a hint that something subtle was happening, and now it is gone because nothing subtle is happening.

**The `go vet` tool** was updated to catch cases where the old shadow pattern is now redundant, and will eventually warn about them. Static analysis tools like `staticcheck` also updated their rules.

**Test coverage for the fix.** If you have table-driven tests with goroutines that you wrote before 1.22, and they used the shadow pattern, the tests will still pass after upgrading — the shadow pattern just becomes harmless redundancy.

## The Gotchas

**Performance cost is negligible.** The compiler is smart about this. For loops where the variable's address is never taken and no closures capture it, the compiler can still use a single stack slot. The extra allocation only matters when a closure actually captures the variable. Benchmarks show no measurable difference for tight loops without closures.

**go.mod controls the behaviour.** This is the most important thing to understand. If you upgrade your Go toolchain to 1.22 but your `go.mod` still says `go 1.21`, your code runs with old semantics. You must update the `go` directive:

```
go 1.22
```

Running `go mod tidy` after upgrading the toolchain will often suggest updating this line.

**Existing shadow patterns are safe.** If you have `item := item` in your code, it is now a no-op rather than a fix. It does not cause bugs. You can remove it for clarity, but there is no urgency.

**The change is not retroactive for imported packages.** If you import a package whose `go.mod` declares `go 1.21`, that package's loops run with the old semantics. The fix only applies when a package is compiled with Go 1.22 module semantics. This rarely causes issues in practice, but it is worth knowing.

**Some rare code depended on the old behaviour.** If you had code that intentionally used a single shared loop variable — sharing state across goroutines via a loop variable pointer, for example — upgrading to Go 1.22 semantics will break it. This pattern was always wrong from a correctness standpoint, but it is worth auditing your code before upgrading.

## Key Takeaway

Go 1.22's loop variable fix eliminates the most common class of goroutine closure bugs in Go. The fix is controlled by the `go` directive in `go.mod`, so upgrading your toolchain is not enough — you need to update the directive too. Once you do, the `item := item` shadow pattern is no longer needed, closures over loop variables behave as you expect, and one whole category of subtle production bugs disappears. If you are on Go 1.21 or earlier, this single change is worth the upgrade.

---

**Previous:** [Lesson 2: Enhanced HTTP Routing](/post/go/go-modern-http-routing/)

**Next:** [Lesson 4: Structured Logging with slog](/post/go/go-modern-slog/)
